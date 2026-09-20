#!/usr/bin/env python3
"""쪽 맞춤 잣대 — **모호하지 않은 자리만** 재서 정확도를 낸다.

쓰기:
    python scripts/align_yardstick.py 문서.hwpx 문서_pdf.json [...]
    python scripts/align_yardstick.py --pairs 폴더

역할:
    `align_documents` 가 HWPX 본문을 PDF 쪽으로 제대로 잘랐는지 잰다.
    개선을 재려면 잣대가 먼저 있어야 한다 — 없이 고치다 두 번 헛발질했다
    (0.5.11 에서 되돌림).

왜 "모호하지 않은 자리만" 인가
--------------------------
처음 만든 잣대는 **PDF 쪽의 첫 줄이 어느 쪽에 들어갔나**를 "가장 가까운
쪽" 으로 골라 셌다. 그 결과 84% · -1 밀림 22건이 나왔고, 그것을 align 의
오류율로 읽었다. **틀린 읽기였다.**

밀린 23건을 뜯어 보니 **전부 반복 제목**이었다:

    2. 프로그램 분석 및 성과관리 계획      프로그램마다 되풀이
    3. 프로그램 목표별 성과지표            〃

이 문서들은 프로그램마다 같은 표제를 쓴다. 같은 글이 문서에 여러 번 있으면
"어느 쪽에 들어갔나" 를 **텍스트 포함만으로는 판정할 수 없다** — 목차에도
있고 머리말에도 있다. `min(abs(쪽 차이))` 는 그때 이웃 쪽을 집는다.

후보가 HWPX 본문에 **정확히 한 번** 나올 때만 재면 답이 하나로 정해진다.
실측(세 부처):

    유일한 후보로만          70건 · 정확 70 (100%)
    표 내용으로              13건 · 정확 13 (100%)
    ── 반복 제목 87건은 이 방법으로 판정 불가 ──

즉 **모호하지 않은 자리에서 align 은 틀리지 않는다.** 84% 는 잣대의
한계였지 align 의 오류율이 아니었다.

남은 숙제
--------
반복 제목 구간에도 **실제 오류가 있다** — `별첨3` 이 PDF 54쪽인데 align 이
53쪽에 넣은 것을 사람이 확인했다. 그 구간은 이 잣대로 잴 수 없으므로,
확인된 사례를 모아 회귀 시험으로 박는 편이 현실적이다.

후보를 이어 붙여 유일하게 만드는 방법도 재 봤지만 커버리지가 줄었다
(70 → 20 → 14). PDF 에서 잇닿은 줄이 HWPX 에서는 떨어져 있기 때문이다.
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path


def measure(hwpx_doc: dict, pdf_doc: dict) -> dict:
    """한 쌍을 재서 결과를 낸다.

    입력: hwpx_doc — HWPX 판독 결과 dict, pdf_doc — PDF 판독 결과 dict
    출력: {checked, exact, skipped, offsets}
    비고:
        `skipped` 는 실패가 아니라 **판정 불가**다. 분모에 넣으면 수치가
        거짓으로 나빠진다 — 0.4.75 에서 표 성적에 같은 정정을 했다.
    """
    from docstruct.align import page_map as pm
    from docstruct.align.documents import _drop_line_breaks, align_documents

    aligned = align_documents(hwpx_doc, pdf_doc)
    # **align 과 같은 재료를 본다** (0.5.52). align 은 `<br>` 을 없앤 뒤
    # 비교하는데(0.5.50) 잣대는 그대로 봤다 — 둘이 다른 글을 보면 멀쩡한
    # 쪽이 "후보가 HWPX 에 없다" 로 판정된다.
    pdf_bodies = [pm._flatten(_drop_line_breaks(page.get("content") or ""))
                  for page in pdf_doc["pages"]]
    flat = pm._flatten(_drop_line_breaks(
        "\n".join(page.get("content") or "" for page in hwpx_doc["pages"])))
    text = {
        page["page_no"]: pm._flatten(_drop_line_breaks(
            (page.get("content") or "")
            + "".join((t.get("markdown") or "") for t in (page.get("tables") or []))))
        for page in aligned["pages"]
    }

    offsets: collections.Counter = collections.Counter()
    unjudged: list[dict] = []
    wrong: list[dict] = []
    for page in pdf_doc["pages"]:
        no = page["page_no"]
        keys = pm._anchor_keys(
            {**page, "content": _drop_line_breaks(page.get("content") or "")})
        if not keys:
            unjudged.append({"page_no": no, "why": "눈금 후보가 없다 (표·표지뿐)"})
            continue

        # **후보를 차례로 본다** (0.5.47). 예전에는 `keys[0]` 하나만 보고
        # 그것이 HWPX 에 없으면 판정을 포기했다. 그런데 쪽 맞춤 자신은
        # (`_find_unique`) 후보를 차례로 훑는다 — **잣대가 맞춤보다 눈이
        # 어두웠다.**
        #
        # 실측(병무청 61·74·82쪽): 첫 후보는 0회인데 둘째·셋째가 1회로
        # 멀쩡히 있었다. PDF 가 낱말 사이에 공백을 넣어 조각낸 줄이 첫
        # 후보로 잡힌 탓이다(`은 행연합회`). 그런 줄은 건너뛰면 된다.
        chosen = None
        reasons: list[str] = []
        for key in keys:
            seen = flat.count(key)
            if seen != 1:
                reasons.append("없음" if seen == 0 else f"{seen}회")
                continue
            # **PDF 쪽에서도 유일해야 한다** (0.5.51). HWPX 에 한 번뿐이어도
            # PDF 여러 쪽에 같은 글이 있으면 어느 쪽 몫인지 잣대가 못 가린다.
            #
            # 실측(문체부 쪽370): `실적치 집계 완료 시점 : '28. 2월 예정` 이
            # PDF 313·370 두 쪽에 있었다. align 은 313 에 넣었고 그것이
            # 맞는데, 잣대가 370 을 기대해 **-57 밀림으로 오판**했다.
            if sum(1 for body in pdf_bodies if key in body) != 1:
                reasons.append("PDF 여러 쪽")
                continue
            chosen = key
            break
        if chosen is None:
            first = reasons[0] if reasons else "없음"
            why = ("후보가 HWPX 에 없다" if first == "없음"
                   else f"후보가 HWPX 에 {first} 나온다")
            unjudged.append({"page_no": no, "why": why, "key": keys[0][:40],
                             "tried": len(keys)})
            continue
        key = chosen
        hits = [n for n, body in text.items() if key in body]
        if not hits:
            unjudged.append({"page_no": no, "why": "맞춘 결과에서 후보를 못 찾음",
                             "key": key[:40]})
            continue
        landed = min(hits, key=lambda n: abs(n - no))
        offsets[landed - no] += 1
        if landed != no:
            wrong.append({"page_no": no, "landed": landed, "offset": landed - no,
                          "key": key[:40]})

    checked = sum(offsets.values())
    return {"checked": checked, "exact": offsets[0], "skipped": len(unjudged),
            "offsets": dict(sorted(offsets.items())),
            "unjudged": unjudged, "wrong": wrong}


#: 목차 항목의 핵심어가 이보다 짧으면 재지 않는다. `전략목표Ⅱ` 처럼
#: 짧은 표제는 목차·간지·본문 여기저기 나와 어느 것인지 가릴 수 없다.
MIN_TOC_CORE = 8


def _title_core(title: str) -> str:
    """목차 제목에서 **번호를 뗀 알맹이** (0.5.47).

    입력: title — 목차에 적힌 제목
    출력: 앞뒤의 번호·기호를 뗀 문자열
    비고:
        PDF 는 장 번호를 제목 **뒤**로 돌려 내놓는다(`성과계획 목표체계
        제1장`). 통째로 대조하면 본문에 있는 제목도 못 찾는다 — 실측
        (병무청): 16항목 중 13개가 그랬다.

        앞의 `3. ` · `제3장 ` 과 뒤의 `……… 84` 같은 목차 점선·쪽번호를
        떼고 남은 것으로 찾는다.
    """
    import re as _re

    text = _re.sub(r"[.·]{2,}\s*\d*\s*$", "", title or "").strip()
    text = _re.sub(r"^제\s*\d+\s*[장편절]\s*", "", text)
    text = _re.sub(r"^\d+\s*[.)]\s*", "", text)
    text = _re.sub(r"^【[^】]*】\s*", "", text)
    return text.strip()


def measure_toc(hwpx_doc: dict, pdf_doc: dict) -> dict:
    """**목차를 정답으로** 쪽 맞춤을 잰다 (0.5.35).

    입력: hwpx_doc — HWPX 판독 결과, pdf_doc — PDF 판독 결과
    출력: {checked, exact, skipped, offsets}
    비고:
        `measure` 는 같은 글이 문서에 여러 번 나오면 판정을 포기한다.
        실측(병무청·개인정보보호위): 판정 불가 89건 중 **54건이 그것**이다 —
        `4. 조직 및 성과관리 추진체계 현황` 처럼 목차에도 본문에도 있는
        제목이다. 잣대가 못 보는 가장 큰 덩어리다.

        목차는 그 자리를 연다. 문서가 스스로 `4. 조직 … 5` 라 적어 두었고,
        인쇄 쪽에 오프셋을 더하면 **물리 쪽이 정해진다** — 어느 등장인지
        고를 필요가 없다. 기대한 쪽이 그 제목을 담고 있는지만 보면 된다.

        **순환이 없다.** `align_documents` 는 목차를 쓰지 않는다(본문 눈금과
        표 짝짓기만 쓴다). 0.5.24 가 `toc_offset` 을 바로잡아 근거도 섰다.

        목차 쪽 자체는 뺀다 — 거기엔 모든 제목이 있어 언제나 맞는다.
    """
    import re

    from docstruct.align.documents import _drop_line_breaks, align_documents

    toc = pdf_doc.get("toc") or []
    offset = pdf_doc.get("toc_offset")
    if not toc or offset is None:
        return {"checked": 0, "exact": 0, "skipped": len(toc), "offsets": {}}

    aligned = align_documents(hwpx_doc, pdf_doc)
    squeeze = lambda text: re.sub(r"\s+", "", text or "")   # noqa: E731
    body = {
        page["page_no"]: squeeze(
            (page.get("content") or "")
            + "".join((t.get("markdown") or "") for t in (page.get("tables") or [])))
        for page in aligned["pages"]
    }
    toc_pages = {item.get("source_page") for item in toc}

    offsets: collections.Counter = collections.Counter()
    skipped = 0
    unjudged: list[dict] = []
    for item in toc:
        raw_title = item.get("title") or ""
        printed = item.get("page")
        if not raw_title or not printed or printed <= 0:
            skipped += 1
            continue
        want = printed + offset
        if want not in body:
            skipped += 1
            continue

        # **제목을 통째로 찾지 않는다** (0.5.47). PDF 는 장 번호를 뒤로
        # 돌려 내놓는다:
        #
        #     목차   제1장 성과계획 목표체계
        #     PDF    성과계획 목표체계 제1장     ← 번호가 뒤로
        #
        # 그래서 통째 대조는 **본문에 있는 제목도 없다고** 판정했다 —
        # 병무청 16항목 중 13개가 그랬다. 번호를 뗀 **핵심어**로 찾는다.
        core = squeeze(_title_core(raw_title))
        if len(core) < MIN_TOC_CORE:
            unjudged.append({"title": raw_title, "why": "핵심어가 너무 짧다"})
            skipped += 1
            continue

        hits = [no for no, text in body.items()
                if no not in toc_pages and core in text]
        if not hits:
            unjudged.append({"title": raw_title, "why": "본문에서 못 찾음"})
            skipped += 1
            continue
        if len(hits) > 1 and want not in hits:
            # 여러 곳에 있고 기대한 쪽에는 없다 — 어느 것이 그 항목인지
            # 가릴 수 없다. `전략목표Ⅱ` 처럼 짧고 되풀이되는 표제다.
            unjudged.append({"title": raw_title, "why": f"{len(hits)}곳에 나온다"})
            skipped += 1
            continue
        landed = want if want in hits else hits[0]
        offsets[landed - want] += 1

    return {"checked": sum(offsets.values()), "exact": offsets[0],
            "skipped": skipped, "offsets": dict(sorted(offsets.items())),
            "unjudged": unjudged}


def _load(path: Path) -> dict:
    """판독 결과를 얻는다 — `.json` 은 읽고 `.hwpx`·`.pdf` 는 돌린다.

    입력: path — 파일 경로
    출력: 판독 결과 dict
    """
    if path.suffix.lower() == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    import docstruct

    return docstruct.structure(str(path), assess_tables=False, fill_tables=False,
                               read_pictures=False, steps="silent")


def main(argv: list[str]) -> int:
    """명령행 진입점.

    입력: argv — (hwpx, pdf) 쌍들
    출력: 종료 코드
    """
    if len(argv) < 2 or len(argv) % 2:
        print(__doc__.strip().splitlines()[2].strip())
        print("  python scripts/align_yardstick.py <hwpx|json> <pdf json> [...]")
        return 2

    total: collections.Counter = collections.Counter()
    for i in range(0, len(argv), 2):
        left, right = Path(argv[i]), Path(argv[i + 1])
        hwpx_doc, pdf_doc = _load(left), _load(right)
        got = measure(hwpx_doc, pdf_doc)
        by_toc = measure_toc(hwpx_doc, pdf_doc)
        rate = got["exact"] / got["checked"] if got["checked"] else 0.0
        print(f"{left.name[:34]:36} 본문 {got['exact']:>3}/{got['checked']:<3} ({rate:.0%}) "
              f"· 목차 {by_toc['exact']:>3}/{by_toc['checked']:<3} "
              f"· 판정 불가 {got['skipped']:>3}")
        for item in got.get("wrong") or ():
            print(f"{'':36} ✗ 쪽{item['page_no']} → {item['landed']} "
                  f"({item['offset']:+d}) · {item['key']!r}")
        if by_toc["offsets"] != {0: by_toc["exact"]}:
            print(f"{'':36} 목차 밀림 {by_toc['offsets']}")
        counts: collections.Counter = collections.Counter(
            item["why"] for item in (got.get("unjudged") or ()))
        for why, many in counts.most_common():
            pages = [item["page_no"] for item in got["unjudged"]
                     if item["why"] == why][:12]
            more = " …" if many > len(pages) else ""
            print(f"{'':36} ? {why} — {many}쪽: {pages}{more}")
        total["checked"] += got["checked"]
        total["exact"] += got["exact"]
        total["skipped"] += got["skipped"]
        total["toc_checked"] += by_toc["checked"]
        total["toc_exact"] += by_toc["exact"]

    if total["checked"]:
        rate = total["exact"] / total["checked"]
        toc_rate = (total["toc_exact"] / total["toc_checked"]
                    if total["toc_checked"] else 0.0)
        print(f"\n합계 본문: {total['exact']}/{total['checked']} ({rate:.0%}) "
              f"· 판정 불가 {total['skipped']}")
        print(f"합계 목차: {total['toc_exact']}/{total['toc_checked']} ({toc_rate:.0%})")
        print("판정 불가는 실패가 아니다 — 같은 글이 문서에 여러 번 나와")
        print("어느 쪽에 들어갔는지 텍스트만으로는 가릴 수 없는 자리다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
