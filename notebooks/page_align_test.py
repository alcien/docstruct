"""HWPX 쪽 맞춤을 로컬에서 시험한다.

역할:
    HWPX·PDF 판독 결과 두 개를 받아 쪽으로 나누고, **얼마나 믿을 만한지**
    를 함께 낸다. 서비스(`/align/pages`)와 같은 계산을 쓴다.
호출부:
    시험 꾸러미.

        python page_align_test.py out_hwpx/*/document.json out_pdf/*/document.json
        python page_align_test.py hwpx.json pdf.json -o 나뉜쪽.md
        python page_align_test.py hwpx.json pdf.json --json 나뉜쪽.json

출력:
    ① 눈금 요약 (실측·추정·차례)
    ② 쪽별 분량
    ③ 표 배정
    (-o / --json 을 주면 나뉜 결과를 파일로도 남긴다)

왜 이런 방식인가
--------------
**HWPX 에는 쪽 정보가 없다.** 세 갈래를 다 확인했다 — `hp:pageNum` 은
"여기에 찍어라" 는 지시일 뿐이고, `hp:startNum page` 는 전부 0 이며,
인쇄된 쪽번호는 꼬리말이라 본문에 들어오지 않는다. 쪽은 한글이 그릴 때
생기는 것이지 저장되는 것이 아니다.

그래서 같은 문서의 PDF 결과와 맞춘다. **본문 첫·마지막 문단을 눈금으로**
쓴다 — 본문 글은 순서가 바뀌지 않으므로(실측: 눈금 266개가 예외 없이
차례대로 증가) 표 유사도보다 든든하다.

눈금이 없는 쪽은 **비례로 채운다.** 실측(행안부): 놓친 163쪽 중 112쪽이
표만 있어 본문 글로는 잡을 수 없다. 채운 쪽은 `추정` 으로 구분한다 —
그러지 않으면 100% 라는 수치가 거짓이 된다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# ── CONFIG ────────────────────────────────────────────────────────────
#: docstruct 소스 경로. 설치본(pip)이면 비워 둔다.
DOCSTRUCT_SRC = ""
#: 미리보기로 보여 줄 쪽 수
PREVIEW_PAGES = 8
# ──────────────────────────────────────────────────────────────────────

if DOCSTRUCT_SRC:
    sys.path.insert(0, DOCSTRUCT_SRC)


def _import(name: str, *symbols):
    """pkg 배치와 배포 배치를 모두 받는다.

    입력: name — `docstruct.` 를 뺀 모듈 경로, symbols — 가져올 이름
    출력: 심볼 튜플
    비고:
        배포 트리는 `converters`·`core`·`align` 등을 최상위로 승격한다
        (`docstruct.align` 이 없을 수 있다). 설치본은 `docstruct.` 아래에
        둔다. 도구가 양쪽에서 도는 것이 맞다.
    """
    import importlib

    for prefix in ("docstruct.", ""):
        try:
            module = importlib.import_module(prefix + name)
        except ImportError:
            continue
        return tuple(getattr(module, s) for s in symbols)
    raise ImportError(f"{name} 을(를) 찾지 못했습니다 — DOCSTRUCT_SRC 를 보세요")


def load(path: str) -> dict:
    """document.json 을 읽는다."""
    target = Path(path).expanduser()
    if not target.is_file():
        raise SystemExit(f"파일이 없습니다: {target}")
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SystemExit(f"JSON 을 읽지 못했습니다 ({target}): {exc}") from exc


def body_of(document: dict) -> str:
    """문서의 본문을 이어 붙인다."""
    return "\n\n".join((page.get("content") or "")
                       for page in (document.get("pages") or []))


def run(hwpx: dict, pdf: dict) -> dict:
    """쪽을 맞추고 근거를 함께 낸다.

    입력: hwpx·pdf — document.json 을 읽은 dict
    출력: {"anchors", "measured", "filled", "parts", "tables", ...}
    """
    (text_anchors, interpolate, split_text_by_page, flatten) = _import(
        "align.page_map",
        "text_anchors", "interpolate", "split_text_by_page", "_flatten")
    (align,) = _import("align.page_map", "align")

    body = body_of(hwpx)
    pdf_pages = pdf.get("pages") or []
    if not body.strip():
        raise SystemExit("HWPX 결과에 본문이 없습니다 — 맞출 근거가 없습니다")
    if not pdf_pages:
        raise SystemExit("PDF 결과에 쪽이 없습니다")

    anchors = text_anchors(pdf_pages, body)
    measured = {page for _pos, page in anchors}
    last = max((p.get("page_no") or 0) for p in pdf_pages)
    filled = interpolate(anchors, last, len(flatten(body)))
    parts = split_text_by_page(body, filled, measured) if filled else []

    # 표 배정 (본문 눈금과 별개로 얼마나 붙는지 본다)
    hwpx_tables = [(p.get("page_no"), t)
                   for p in (hwpx.get("pages") or [])
                   for t in (p.get("tables") or [])]
    pdf_tables = [(p.get("page_no"), t)
                  for p in pdf_pages for t in (p.get("tables") or [])]
    table_pairs = (align(pdf_tables, [(t.get("cells") or [])
                                      for _p, t in hwpx_tables])
                   if hwpx_tables and pdf_tables else [])
    matched = sum(1 for pair in table_pairs
                  if pair.gt_index is not None and pair.page_no is not None)

    return {
        "anchors": anchors, "measured": measured, "filled": filled,
        "parts": parts, "last_page": last,
        "hwpx_tables": len(hwpx_tables), "pdf_tables": len(pdf_tables),
        "matched_tables": matched, "body_len": len(body),
    }


def report(result: dict) -> None:
    """사람이 읽을 요약을 낸다."""
    anchors, filled, parts = result["anchors"], result["filled"], result["parts"]
    last = result["last_page"] or 1

    print(f"\n① 눈금")
    print(f"   실측  {len(anchors):4} / {last}쪽 ({len(anchors) / last:.0%})")
    print(f"   보간  {len(filled) - len(anchors):4}  → 합계 {len(filled)}")
    disorder = sum(1 for a, b in zip(filled, filled[1:])
                   if b[0] < a[0] or b[1] <= a[1])
    print(f"   차례가 어긋난 것 {disorder}  "
          f"{'(없어야 정상입니다)' if disorder else '✓'}")

    kept = sum(len(p["content"]) for p in parts)
    print(f"\n② 쪽 나누기")
    print(f"   덩어리 {len(parts)} · 글자 {kept:,} / 원본 {result['body_len']:,}"
          f" (손실 {result['body_len'] - kept:,})")
    estimated = sum(1 for p in parts if p.get("estimated"))
    print(f"   실측 쪽 {len(parts) - estimated} · **추정 쪽 {estimated}**"
          f" — 추정은 자리를 가늠한 것이지 찾은 것이 아닙니다")

    print(f"\n③ 표 배정 (참고)")
    print(f"   HWPX {result['hwpx_tables']} · PDF {result['pdf_tables']}"
          f" · 짝지어짐 {result['matched_tables']}")

    print(f"\n④ 미리보기 (앞 {PREVIEW_PAGES}쪽)")
    for part in parts[:PREVIEW_PAGES]:
        mark = "추정" if part.get("estimated") else "실측"
        head = (part["content"] or "").strip().replace("\n", " ")[:60]
        print(f"   p{str(part['page_no'] or '-'):>4} [{mark}] "
              f"{len(part['content']):6,}자  {head}")


def to_markdown(parts: list[dict], name: str | None) -> str:
    """나뉜 쪽을 markdown 으로."""
    lines = [f"# {name or '문서'} — 쪽 맞춤 결과", ""]
    for part in parts:
        mark = " *(추정)*" if part.get("estimated") else ""
        lines += ["---", "", f"## 페이지 {part['page_no'] or '머리말'}{mark}",
                  "", (part["content"] or "").strip(), ""]
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    """두 결과를 맞춰 보고 결과를 낸다."""
    args = list(sys.argv[1:] if argv is None else argv)
    md_out = json_out = None
    for flag in ("-o", "--json"):
        if flag in args:
            index = args.index(flag)
            if index + 1 >= len(args):
                print("사용: python page_align_test.py HWPX.json PDF.json "
                      "[-o 결과.md] [--json 결과.json]")
                return 1
            value = args[index + 1]
            del args[index:index + 2]
            if flag == "-o":
                md_out = value
            else:
                json_out = value

    if len(args) != 2:
        print("사용: python page_align_test.py HWPX의_document.json "
              "PDF의_document.json [-o 결과.md] [--json 결과.json]")
        print("\n  두 파일은 **같은 문서**의 판독 결과여야 합니다.")
        return 1

    hwpx, pdf = load(args[0]), load(args[1])
    print(f"HWPX : {hwpx.get('filename')} ({hwpx.get('source_format')})")
    print(f"PDF  : {pdf.get('filename')} ({len(pdf.get('pages') or [])}쪽)")

    result = run(hwpx, pdf)
    report(result)

    if md_out:
        Path(md_out).write_text(
            to_markdown(result["parts"], hwpx.get("filename")),
            encoding="utf-8")
        print(f"\n저장: {md_out}")
    if json_out:
        Path(json_out).write_text(
            json.dumps({"filename": hwpx.get("filename"),
                        "page_count": len(result["parts"]),
                        "pages": result["parts"]},
                       ensure_ascii=False, indent=2),
            encoding="utf-8")
        print(f"저장: {json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
