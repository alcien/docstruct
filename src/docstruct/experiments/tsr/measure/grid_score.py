"""실험 ⑪ — 격자 일치도 채점 (grid_score, GriTS 간이판).

입력:
    TableFormer·사각형·lattice 격자
출력:
    일치도 점수 (GriTS 간이)

역할:
    한 표에 대해 얻을 수 있는 격자 후보들(TableFormer 인식 · 사각형 격자 ·
    합성 lattice)의 **서로 일치하는 정도**를 기록한다. 정답 없이도
    "어느 양상에서 어떤 근거가 유리한가" 를 문서 전체에서 잴 수 있게 한다.
호출부:
    pipeline (실험이 켜졌을 때) — 다른 격자 실험과 무관하게 돈다
설정:
    DOCSTRUCT_EXP_GRID_SCORE=false 로 끈다 — 0.4.3 승격 뒤 **기본 켬** (registry.DEFAULT_ON)

지표에 대하여 (정직한 이름)
------------------------
GriTS(Smock 2023)는 두 격자의 **가장 비슷한 부분구조**를 2차원 DP 로 찾아
점수를 낸다. 여기 구현은 그 간이판이다: 행·열 번호가 같은 좌표계라고 보고
셀 자리(row·col·rowspan·colspan) 집합의 Dice 계수를 낸다.

    dice = 2·|A ∩ B| / (|A| + |B|)

좌표계가 어긋난 두 격자(행이 하나 밀린 경우 등)에는 GriTS 보다 박하게
나온다 — **낮은 점수가 "다르다" 를 과장할 수는 있어도 "같다" 를 지어내지는
않는다.** 완전한 2차원 DP 는 부분 점수가 필요해지면 그때 들인다.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from docstruct.experiments.registry import Experiment, register
from docstruct.experiments.tsr.measure.vector_grid import (
    _detected_cells,
    _inside,
    _page_rects,
    physical_cells,
)

if TYPE_CHECKING:
    from docstruct.models import PageContent

_log = logging.getLogger(__name__)


def grid_dice(cells_a, cells_b) -> float:
    """두 셀 자리 집합의 Dice 계수.

    입력: cells_a/cells_b — (row, col, rowspan, colspan) 목록
    출력: 0.0~1.0
    """
    a, b = set(cells_a or []), set(cells_b or [])
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return 2.0 * len(a & b) / (len(a) + len(b))


def merged_dice(cells_a, cells_b) -> float | None:
    """병합 셀만의 Dice. 양쪽 다 병합이 없으면 None (재지 않음).

    입력: cells_a/cells_b — 셀 자리 목록
    출력: 0.0~1.0 또는 None
    """
    a = {c for c in (cells_a or []) if c[2] > 1 or c[3] > 1}
    b = {c for c in (cells_b or []) if c[2] > 1 or c[3] > 1}
    if not a and not b:
        return None
    if not a or not b:
        return 0.0
    return round(2.0 * len(a & b) / (len(a) + len(b)), 2)


def _cols_of(cells) -> int:
    """셀 자리 목록의 열 수 (col + colspan 의 최댓값).

    입력: cells — (row, col, rowspan, colspan) 목록
    출력: 열 수 (비면 0)
    """
    return max((c[1] + c[3] for c in cells), default=0)


def score_table(pdf_path, page_no: int, bbox: dict,
                detected_cells: list[dict] | None) -> dict | None:
    """표 하나에 대해 격자 후보들의 일치도를 낸다.

    입력: pdf_path — 원본, page_no — 쪽, bbox — 표 영역,
          detected_cells — TableFormer 셀
    출력: {"sizes", "dice", "merged_dice", (두 기하 근거가 다 서면)
          "agreed", "agreed_missing"}.
          비교할 후보가 하나뿐이면 None
    """
    from docstruct.experiments.tsr.measure.line_grid import table_lattice

    candidates: dict[str, list] = {}
    detected = _detected_cells(detected_cells)
    if detected:
        candidates["tf"] = detected
    rect = physical_cells(
        [r for r in _page_rects(pdf_path, page_no) if _inside(r, bbox)])
    if rect is not None:
        candidates["rect"] = rect[0]
    # **장식 경계를 접고 센다.** 합의 병합의 (row, col) 은 이 격자의
    # 번호이므로, 가장자리 겹선이 만든 빈 띠가 열로 세어지면 좌표가 통째로
    # 한 칸 밀린다 — ⑭의 좌표계 검사가 그것을 막느라 행안부에서 19표 중
    # 16표를 물렸다. 접고 세면 그 16표가 인식과 같은 좌표계가 된다.
    lattice = table_lattice(pdf_path, page_no, bbox, fold_decor=True)
    if lattice is not None:
        candidates["lattice"] = lattice[0]

    if len(candidates) < 2:
        return None
    names = sorted(candidates)
    dice = {}
    merged = {}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            key = f"{a}~{b}"
            dice[key] = round(grid_dice(candidates[a], candidates[b]), 2)
            m = merged_dice(candidates[a], candidates[b])
            if m is not None:
                merged[key] = m
    report = {"sizes": {n: len(candidates[n]) for n in names},
              "dice": dice, "merged_dice": merged}

    # **합의 병합** — 사각형과 괘선이 둘 다 인정한 병합. 정답 대조 실측:
    #     조달청 94.9% (75/79) · 문체부 98.0% (1152/1175)
    # 복원 승격 기준(오탐 0)에는 못 미치지만, 어느 단일 근거보다 정확해
    # **VLM 힌트의 근거**로 쓴다 (lat 단독 69.6~84.5%, rect 81.7~91.9%).
    merged_of = lambda cells: {c for c in cells if c[2] > 1 or c[3] > 1}
    if "rect" in candidates and "lattice" in candidates:
        agreed = merged_of(candidates["rect"]) & merged_of(candidates["lattice"])
        detected_merges = merged_of(detected) if detected else set()
        report["agreed"] = len(agreed)
        report["agreed_missing"] = sorted(agreed - detected_merges)
        # ⑭(agreed_grid)의 **좌표계 검사** 근거 — agreed_missing 의
        # (row, col) 은 기하 격자의 번호다. 인식의 열 수와 다르면 같은
        # 번호가 지면의 다른 자리를 가리킨다 (인식이 열을 잃은 표 —
        # ⑬의 표적 — 가 바로 그 경우다). ⑭은 이 열 수가 인식과 일치할
        # 때만 반영한다.
        report["agreed_cols"] = {"rect": _cols_of(candidates["rect"]),
                                 "lattice": _cols_of(candidates["lattice"])}
    return report


def run(pages: list[PageContent], **kwargs) -> int:
    """모든 표에 격자 일치도를 기록한다.

    입력: pages — 페이지 목록 (제자리 갱신), pdf_path — 원본 경로
    출력: 기록한 표 수
    비고:
        **아무것도 바꾸지 않는다** — 순수 계측이다. 전 문서를 돌린 뒤
        `grid_score` 를 모아 보면 근거별 유불리의 양상이 나온다:
        rect~lattice 가 높고 tf 가 낮은 표 = 기하가 옳고 인식이 틀린 표.
    """
    source = kwargs.get("pdf_path")
    if not source:
        return 0

    scored = 0
    for page in pages:
        if not page.tables or not isinstance(page.page_no, int):
            continue
        for table in page.tables:
            if not table.bbox:
                continue
            report = score_table(source, page.page_no, table.bbox, table.cells)
            if report is None:
                continue
            table.grid_score = report
            scored += 1
    return scored


register(Experiment(
    key="grid_score",
    title="격자 일치도 채점",
    purpose="TableFormer·사각형 격자·합성 lattice 의 서로 일치도를 기록 (정답 없는 양상 계측)",
    origin="GriTS(Smock 2023)의 간이판 — 좌표 정렬 가정의 Dice. 낮은 점수가 "
           "차이를 과장할 수는 있어도 일치를 지어내지는 않는다",
    run=run,
    formats=("pdf",),
    needs=("cells", "geometry"),
    status="verified",
    note="아무것도 바꾸지 않는 순수 계측. 전 문서에서 모으면 근거별 "
         "유불리가 양상으로 나온다 — rect~lattice 높고 tf 낮음 = 기하가 "
         "옳고 인식이 틀린 표(⑦·⑨ 표적), tf~rect 높음 = 인식이 이미 맞는 "
         "표. 완전한 GriTS 2차원 DP 는 부분 점수가 필요해지면 도입.",
    knobs={"DOCSTRUCT_EXP_GRID_SCORE": "false 면 끔 (기본 켬 — 승격)"},
))
