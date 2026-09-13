"""후보 채점기 — VLM 이 낸 표 후보를 결정론으로 채점한다.

입력:
    VLM 후보 markdown
출력:
    결정론 점수

역할:
    VLM 재구성 후보(markdown)를 받아 **결정론 검증기들의 점수**로 바꾼다.
    H12-b(후보-검증-선택 루프)와 H11(선택형 중재)이 같은 채점기를 쓴다.
호출부:
    tables/vlm_rebuild (best-of-N) · 후속 H11
설정:
    없음 — 순수 함수 묶음이다.

설계 원칙 (VLM_OCR_활용설계.md §4~5)
--------------------------------
RL 에서 가져온 것은 구조뿐이다: 탐색(VLM 생성) - 보상(이 채점기) - 선택.
보상이 결정론이므로 정책 갱신(학습)이 필요 없다.

TSR 실험 코드는 **읽기만** 한다 — sum_check.check_table 과
otsl_diff.to_otsl 을 순수 함수로 부를 뿐, 실험의 실행·필드에 쓰지 않는다.

채점표 (설계 문서 §4 그대로)
--------------------------
    문턱 (통과 못 하면 후보 탈락 — 점수 없음):
        · 표 형태다 (구분선 + 자료 행, 열 수 일정)
        · ⑧ 검산이 돌았다면 불일치 0 (합이 깨진 후보는 구조 손상이다)
    점수:
        · 열 수 일정(OTSL 구성 가능)          +2
        · ⑧ 검산 대상이고 통과                +2
        · ⑥⑨ missing(세로 병합) 해소          +1/개

한계를 그대로 적는다: GFM markdown 은 가로 병합을 표현하지 못하므로
missing 중 세로 병합(rowspan>1 · colspan=1)만 `〃` 표식으로 확인한다.
가로 병합 해소는 여기서 못 재고, 셀 단위 재구성(OTSL 출력 강제)이
들어오면 그때 잰다.
"""
from __future__ import annotations

from typing import Any

from docstruct.experiments.tsr.measure.otsl_diff import to_otsl
from docstruct.experiments.tsr.measure.sum_check import check_table

#: hwpxtree · grid_restore 와 같은 세로 병합 이어짐 표식.
MERGE_UP = "〃"

#: 형태 문턱 — 구분선 포함 최소 줄 수.
MIN_TABLE_LINES = 3


def cells_from_markdown(markdown: str) -> list[dict] | None:
    """GFM markdown 표를 셀 목록으로 되읽는다.

    입력: markdown — 표 문자열
    출력: {"row","col","rowspan","colspan","text"} 목록 (닻 셀만).
          표 형태가 아니면 None
    비고:
        `〃` 는 값이 아니라 "위 칸이 이어진다" 는 표식이므로, 위쪽 닻
        셀의 rowspan 을 늘리고 자기 자리는 만들지 않는다 — grid_restore
        가 쓰는 규칙의 역방향이다. 열 수가 줄마다 다르면 None (그런
        후보는 채점 이전에 표가 아니다).
    """
    rows = [line.strip() for line in (markdown or "").splitlines()
            if line.strip().startswith("|")]
    if len(rows) < MIN_TABLE_LINES:
        return None
    body: list[list[str]] = []
    for line in rows:
        parts = [p.strip() for p in line.strip("|").split("|")]
        if all(set(p) <= set("-: ") for p in parts):
            continue                             # 구분선
        body.append(parts)
    if len(body) < 2:
        return None
    cols = len(body[0])
    if any(len(r) != cols for r in body):
        return None                              # 열 수가 들쭉날쭉

    cells: list[dict] = []
    anchors: dict[int, dict] = {}                # 열 → 살아 있는 닻 셀
    for row_no, parts in enumerate(body):
        for col_no, text in enumerate(parts):
            if text == MERGE_UP:
                anchor = anchors.get(col_no)
                if anchor is not None:
                    anchor["rowspan"] += 1
                continue                         # 자기 자리는 없다
            cell = {"row": row_no, "col": col_no,
                    "rowspan": 1, "colspan": 1,
                    "text": text.replace("\\|", "|")}
            cells.append(cell)
            anchors[col_no] = cell
    return cells


def structure_otsl(markdown: str) -> str | None:
    """후보의 구조를 OTSL 로 적는다 (② 와 같은 표기).

    입력: markdown — 표 문자열
    출력: OTSL 문자열. 표 형태가 아니면 None
    """
    cells = cells_from_markdown(markdown)
    if cells is None:
        return None
    rows = max(c["row"] + c["rowspan"] for c in cells)
    cols = max(c["col"] + c["colspan"] for c in cells)
    return to_otsl(cells, rows, cols)


def _resolved_missing(cells: list[dict], missing: list) -> int:
    """후보가 해소한 세로 병합 missing 수.

    입력: cells — 후보 셀, missing — (row, col, rowspan, colspan) 목록
    출력: 해소 수
    비고:
        markdown 후보의 행 번호는 머리행 제거 등으로 원본과 밀릴 수
        있다 — 자리 정확일치를 고집하면 맞는 후보를 떨어뜨린다. 그래서
        **열이 같고 rowspan 이 같은 세로 병합이 존재하는가** 로 잰다.
        느슨하지만 방향이 안전하다: 없는 병합을 만든 후보는 ⑧·형태
        문턱이 따로 거른다.
    """
    resolved = 0
    for row, col, rowspan, colspan in missing:
        if colspan > 1 or rowspan <= 1:
            continue                             # 가로 병합은 GFM 이 못 적는다
        if any(c["col"] == col and c["rowspan"] == rowspan for c in cells):
            resolved += 1
    return resolved


def _high_confidence_missing(table: Any) -> list:
    """⑥·⑨가 남긴 missing 중 confidence:high 인 것.

    입력: table — TableInfo
    출력: missing 목록 (없으면 빈 목록)
    """
    for source in (getattr(table, "grid_merge_gap", None),
                   getattr(table, "synth_grid", None)):
        if source and source.get("confidence") == "high":
            return list(source.get("missing") or [])
    return []


def _sum_view(cells: list[dict]) -> list[dict]:
    """⑧ 검산용 뷰 — 세로 병합의 덮인 자리를 `〃` 표식 셀로 되살린다.

    입력: cells — 닻 셀 목록
    출력: 닻 셀 + 표식 셀
    비고:
        닻 표현 그대로 검산기에 주면, `〃` 로 덮인 행의 딱지 열이 비어
        검산기의 층 가드("행마다 딱지 열이 다르다")가 잘못 발동한다 —
        실측으로 잡은 상호작용이다. 검산기는 `〃` 를 건너뛰게 돼 있으므로
        (sum_check 의 값 수집 규칙) 표식을 복원해 주면 맞물린다.
    """
    view = [dict(c) for c in cells]
    for cell in cells:
        for offset in range(1, cell.get("rowspan", 1)):
            view.append({"row": cell["row"] + offset, "col": cell["col"],
                         "text": MERGE_UP})
    return view


def score_candidate(table: Any, markdown: str) -> dict:
    """후보 하나를 채점한다.

    입력: table — 원본 TableInfo (실험 필드는 읽기만), markdown — 후보
    출력: {"gate": bool, "score": int, "detail": {...}}
    비고:
        gate 탈락이면 score 는 보지 않는다. **모든 후보가 탈락하면
        호출부는 원본을 유지해야 한다** — 폴백의 폴백은 무행동이다.
    """
    detail: dict[str, Any] = {}
    cells = cells_from_markdown(markdown)
    if cells is None:
        return {"gate": False, "score": 0, "detail": {"form": "표 형태 아님"}}
    detail["form"] = "통과"
    score = 2                                    # 열 수 일정 (구성 가능)

    report = check_table(_sum_view(cells))
    if report is not None:
        detail["sum_check"] = report
        if report["failed"]:
            return {"gate": False, "score": 0, "detail": detail}
        score += 2                               # 검산 대상이고 통과
    else:
        detail["sum_check"] = "대상 아님"

    missing = _high_confidence_missing(table)
    if missing:
        resolved = _resolved_missing(cells, missing)
        detail["missing_resolved"] = f"{resolved}/{len(missing)}"
        score += resolved
    return {"gate": True, "score": score, "detail": detail}


def pick_best(table: Any, candidates: dict[str, str]) -> tuple[str, dict] | None:
    """후보들 중 최고점을 고른다.

    입력: table — 원본 TableInfo, candidates — {이름: markdown}
    출력: (이름, 채점 결과). 문턱 통과가 하나도 없으면 None
    비고:
        동점이면 **먼저 온 것**을 고른다 — 호출부가 후보를 보수적인
        차례(현행 프롬프트 먼저)로 주면 동점에서 현행이 이긴다.
    """
    best: tuple[str, dict] | None = None
    for name, markdown in candidates.items():
        graded = score_candidate(table, markdown)
        if not graded["gate"]:
            continue
        if best is None or graded["score"] > best[1]["score"]:
            best = (name, graded)
    return best
