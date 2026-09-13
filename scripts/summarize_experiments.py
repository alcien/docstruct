"""여러 문서의 실험 기록을 한 표로 모은다 — 문서군 대조용.

역할:
    부처별로 돌린 `document.json`·`aligned.json` 을 읽어 실험 기록을
    한자리에 모은다. 실험 하나를 여러 문서에서 재야 승격·폐기를 정할 수
    있는데, 지금까지는 그 대조를 손으로 했다.
호출부:
    수동 실행 (윈도우 가능).

        python scripts/summarize_experiments.py out_a/문서/document.json ...
        python scripts/summarize_experiments.py "out_*/**/document.json"

왜 있는가
--------
**한 문서로 정한 것은 다음 문서에서 무너진다.** ⑬은 행안부 한 건으로
강등됐다가 두 부처 300표를 다시 재고서야 닫혔고, `chart_gate` 는 지금
지면형 그림 **세 개**뿐이라 아직 아무것도 정할 수 없다(셋 다 문턱 ±3
안이라는 것만 안다).

문서마다 결과를 열어 손으로 세는 대신, 같은 잣대로 모아 놓고 본다.

무엇을 모으나
-----------
    chart_gate   경로 분포 · 경계 비율 · 두 축의 문턱까지 거리
    over_split   인식 대 격자 열 차이 · 믿을 만한 것 수
    col_gate     ⑬ 게이트 기각 사유 분포
    page_chrome  껍데기 비중이 높은 쪽 수
    scan_ab      숫자 불일치가 있는 대상 수
    grid         격자 결함 비율(상시 검사) · lattice_fill/hole_fill 발화 ·
                 fill_gate 기각 사유 · 셀 오염 복원/잔여 (0.4.69~0.4.83)
    hidden       HWPX 숨은 글·표 앵커 분포 (0.4.74 · 0.4.81)
    align        쪽 배정·못 맞춘 표(단서별)

없는 실험은 조용히 건너뛴다 — 문서마다 켠 실험이 다르기 때문이다.
"""
from __future__ import annotations

import glob
import json
import sys
from collections import Counter
from pathlib import Path


def _load(path: Path) -> dict | None:
    """JSON 을 읽는다. 못 읽으면 알리고 None."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"  ! {path} 를 읽지 못했습니다: {exc}", file=sys.stderr)
        return None


def _tables(doc: dict) -> list[dict]:
    return [t for p in doc.get("pages") or [] for t in (p.get("tables") or [])]


def _images(doc: dict) -> list[dict]:
    return [i for p in doc.get("pages") or [] for i in (p.get("images") or [])]


def chart_gate_rows(doc: dict) -> list[str]:
    """그림 판독 경로 계측 요약."""
    got = [i["chart_gate"] for i in _images(doc) if i.get("chart_gate")]
    if not got:
        return []
    near = [g for g in got if g.get("borderline")]
    routes = Counter(g.get("route") for g in got)
    lines = [f"  chart_gate  : 잰 그림 {len(got)} · 경계 {len(near)}"
             f" ({len(near) / len(got):.0%}) · "
             + " ".join(f"{k}={v}" for k, v in routes.most_common())]
    for g in near:
        # **경계에 놓인 것은 낱낱이 적는다.** 비율만으로는 어느 축이
        # 갈랐는지 알 수 없고, 축이 다르면 손볼 곳도 다르다.
        lines.append(
            f"      경계 {g.get('route')}: 줄 {g.get('text_rows')}"
            f"(차 {g.get('rows_margin')}) · 줄당 {g.get('per_row')}"
            f"(차 {g.get('per_row_margin')})")
    return lines


def over_split_rows(doc: dict) -> list[str]:
    """괘선보다 열을 더 쪼갠 표 요약."""
    got = [t["over_split"] for t in _tables(doc) if t.get("over_split")]
    if not got:
        return []
    trusted = [g for g in got if g.get("trusted")]
    gaps = Counter(g.get("gap") for g in trusted)
    return [f"  over_split  : {len(got)}표 (믿을 만한 것 {len(trusted)}) · "
            "차이 " + " ".join(f"{k}열={v}" for k, v in sorted(gaps.items()))]


def col_gate_rows(doc: dict) -> list[str]:
    """⑬ 게이트 기각 사유 분포."""
    got = [t["col_gate"] for t in _tables(doc) if t.get("col_gate")]
    if not got:
        return []
    fired = sum(1 for t in _tables(doc) if t.get("col_grid"))
    reasons = Counter(g.get("reason") for g in got)
    return [f"  col_gate    : {len(got)}표 · 발화 {fired} · "
            + " ".join(f"{k}={v}" for k, v in reasons.most_common())]


def page_chrome_rows(doc: dict) -> list[str]:
    """되풀이되는 머리말·꼬리말 요약."""
    got = [p["page_chrome"] for p in doc.get("pages") or []
           if p.get("page_chrome")]
    if not got:
        return []
    bare = [g for g in got if (g.get("share") or 0) >= 0.9]
    return [f"  page_chrome : {len(got)}쪽 · 본문의 90%↑ 가 껍데기인 쪽 "
            f"{len(bare)}"]


def scan_ab_rows(doc: dict) -> list[str]:
    """이중 판독 숫자 불일치 요약 (쪽·그림 양쪽)."""
    got = [p["scan_ab"] for p in doc.get("pages") or [] if p.get("scan_ab")]
    got += [i["scan_ab"] for i in _images(doc) if i.get("scan_ab")]
    if not got:
        return []
    bad = [g for g in got if (g.get("digit_jaccard") or 1.0) < 1.0]
    return [f"  scan_ab     : 대조 {len(got)} · 숫자 불일치 {len(bad)}"]


def align_rows(doc: dict) -> list[str]:
    """쪽 맞춤 결과 요약 (aligned.json)."""
    if doc.get("source") != "hwpx+pdf 쪽 맞춤":
        return []
    un = doc.get("unmatched") or []
    def _why(table: dict) -> str:
        """단서 이름. 옛 결과(0.4.65 이전)는 `why` 가 없으므로 보정한다."""
        note = table.get("align_note") or {}
        if note.get("why"):
            return note["why"]
        return "layout(옛 기록)" if note.get("layout_like") else "데이터 표로 보임"

    why = Counter(_why(t) for t in un)
    lines = [f"  align       : 쪽 {doc.get('page_count')} "
             f"(추정 {doc.get('estimated_pages')}) · 표 "
             f"{doc.get('matched_tables')}/{doc.get('total_tables')} 배정 · "
             f"머리말 {doc.get('head_chars', 0)}자"]
    if un:
        lines.append("      못 맞춘 " + str(len(un)) + ": "
                     + " ".join(f"{k}={v}" for k, v in why.most_common()))
    return lines


def grid_rows(doc: dict) -> list[str]:
    """격자 온전성·격자 복원·셀 오염 요약 (0.4.83 추가).

    비고:
        0.4.69 이후 승격·강등의 근거가 이 신호들인데(격자 결함 50%→7%,
        hole_fill 14%→4%, 오염 21~33%), 문서군 대조 도구에는 없었다 —
        부처마다 손으로 세고 있었다. **수치가 부처를 탄다**(0.4.77: 6~7%
        가 아니라 14%)는 것이 드러난 뒤라 더더욱 한 잣대로 모아야 한다.
    """
    tables = _tables(doc)
    checked = [t for t in tables if t.get("grid_faults")]
    if not checked and not any(t.get("cell_leaks") for t in tables):
        return []
    faulty = [t for t in checked if not t["grid_faults"].get("ok")]
    heavy = [t for t in faulty if t["grid_faults"].get("heavy")]
    lines = []
    if checked:
        share = len(faulty) / len(checked) if checked else 0.0
        lines.append(f"  grid_check  : {len(checked)}표 잼 · 결함 {len(faulty)}"
                     f" ({share:.0%}) · 많이 깨짐 {len(heavy)}")
    sources = Counter(t.get("source") or "parser" for t in tables)
    fill = sum(1 for t in tables if t.get("lattice_fill"))
    hole = sum(1 for t in tables if t.get("hole_fill"))
    restore = sum(1 for t in tables if t.get("lattice_restore"))
    if fill or hole or restore or sources.get("grid"):
        lines.append(f"  restore     : lattice_restore {restore} · lattice_fill "
                     f"{fill} · hole_fill {hole} · source=grid {sources.get('grid', 0)}"
                     f"/{len(tables)}")
    gates = Counter((t.get("fill_gate") or {}).get("gate")
                    for t in tables if t.get("fill_gate"))
    if gates:
        lines.append("  fill_gate   : "
                     + " ".join(f"{k}={v}" for k, v in gates.most_common()))
    leaks = [t["cell_leaks"] for t in tables if t.get("cell_leaks")]
    if leaks:
        repaired = sum(l.get("repaired", 0) for l in leaks)
        left = sum(1 for l in leaks if l.get("leaks"))
        unpatched = sum(l.get("markdown_unpatched", 0) for l in leaks)
        line = (f"  cell_leaks  : 복원 {repaired}칸 · 남은 오염 표 {left}"
                f" (한 번뿐이라 손대지 않음)")
        if unpatched:
            line += f" · markdown 미반영 {unpatched}칸"
        lines.append(line)
    return lines


def hidden_rows(doc: dict) -> list[str]:
    """HWPX 숨은 글·표 앵커 요약 (0.4.83 추가)."""
    pages = doc.get("pages") or []
    hidden = Counter()
    for page in pages:
        for why, info in (page.get("hidden_text") or {}).items():
            hidden[why] += (info or {}).get("count", 0)
    anchors = Counter()
    for page in pages:
        for kind, count in (page.get("table_anchors") or {}).items():
            anchors[kind] += count
    lines = []
    if hidden:
        lines.append("  hidden_text : "
                     + " ".join(f"{k}={v}" for k, v in hidden.most_common()))
    if anchors:
        lines.append("  anchors     : "
                     + " ".join(f"{k}={v}" for k, v in anchors.most_common()))
    return lines


#: 이름 → 요약 함수. 없는 실험은 빈 목록을 돌려주므로 조용히 빠진다.
SECTIONS = (chart_gate_rows, over_split_rows, col_gate_rows,
            page_chrome_rows, scan_ab_rows, grid_rows, hidden_rows,
            align_rows)


def summarize(path: Path) -> list[str]:
    """문서 하나를 요약한다.

    입력: path — document.json 또는 aligned.json
    출력: 화면에 찍을 줄 목록 (기록이 없으면 그 사실만)
    """
    doc = _load(path)
    if doc is None:
        return []
    name = doc.get("filename") or path.parent.name
    head = [f"── {name}  ({path})"]
    body: list[str] = []
    for section in SECTIONS:
        body += section(doc)
    if not body:
        body = ["  (실험 기록 없음 — `--exp` 로 켜고 다시 돌리세요)"]
    return head + body + [""]


def main(argv: list[str]) -> int:
    """진입점. 사용법은 모듈 머리를 보라."""
    if not argv:
        print(f"사용법: python {Path(__file__).name} <document.json ...>",
              file=sys.stderr)
        print("예시  : python scripts/summarize_experiments.py "
              "out_*/*/document.json", file=sys.stderr)
        return 1

    paths: list[Path] = []
    for arg in argv:
        # 윈도우 셸은 별표를 펴 주지 않으므로 여기서 편다.
        hits = [Path(p) for p in glob.glob(arg, recursive=True)]
        paths += hits or [Path(arg)]

    found = 0
    for path in paths:
        if not path.is_file():
            print(f"  ! 파일이 없습니다: {path}", file=sys.stderr)
            continue
        found += 1
        for line in summarize(path):
            print(line)
    if not found:
        print("읽을 파일이 없습니다.", file=sys.stderr)
        return 1
    print(f"문서 {found}건을 모았습니다.")
    print("한 문서로 정하지 않습니다 — 부처가 다르면 표의 생김새도 다릅니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
