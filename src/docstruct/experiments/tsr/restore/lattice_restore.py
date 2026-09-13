"""실험 ⑮ — 괘선 격자로 표 전체 복원 (lattice_restore).

입력:
    ★ 괘선 격자

역할:
    ⑦(grid_restore)과 같은 일을 **다른 근거**로 한다. ⑦은 배경 사각형이
    표 전체를 덮을 때(T1)만 서지만, ⑮는 접은 괘선 격자로 선다 — 사각형이
    없는 표(T2·T3)가 대상이다. 셀 자리는 격자에서, 글자는 그 칸의 지면
    텍스트에서 직접 가져온다.
호출부:
    experiments.registry (`DOCSTRUCT_EXP_LATTICE_RESTORE=1`)
출력:
    고친 표 수 (TableInfo.cells/markdown/source 갱신)

왜 필요한가 — ⑬으로는 열 밀림이 안 고쳐진다
------------------------------------------
⑬(col_grid)은 열 **수**만 맞춘다. `remap_columns` 가 하는 일은 마지막
열의 colspan 을 늘리는 것뿐이다 — 어느 열이 쪼개졌는지 모르기 때문에
지어내지 않는다는 판단이었다.

그런데 실제로 잃은 열은 **중간**에 있다. 조달청 p65 의 "재정사업 평가명/
결과" 열, 행안부 p63·p299 가 그렇다. 중간이 빈 것을 끝에서 메우니 열
수만 맞고 **본문 값은 밀린 채 남는다.** 원래 설계는 "세부 배치는 ⑫가
격자에서 가져온다" 였는데 ⑫는 머리 2행만 손대므로, 본문은 아무도 고치지
않는다.

그래서 열 수를 맞추는 대신 **표를 다시 세운다.** 0.4.3 의 장식 경계
접기로 격자 열 수가 지면과 맞게 됐고(가짜 띠 제거), ⑦이 쓰던 재작성
기계(칸마다 `get_text_bounded`)는 3부처 병합 623개 오탐 0 으로 검증돼
있다. 둘을 붙이면 중간 열이 살아나면서 값 귀속이 함께 맞는다.

안전 장치
--------
    · ⑦·⑫·⑭가 이미 손댄 표는 건너뛴다 (결정론이 이긴 자리는 지킨다)
    · 격자가 인식보다 **열이 많을 때만** 선다 — 열 밀림이 대상이고,
      멀쩡한 표를 다시 쓰는 위험을 지지 않는다
    · 글자 수가 줄면 되돌린다. 격자가 표를 일부만 덮으면 바깥 글자가
      통째로 빠지는데, 그것이 가장 큰 손해다
    · 세로 병합이 과반이면 물러난다 (도해가 격자 흉내를 낸 것)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from docstruct.experiments.registry import Experiment, register

if TYPE_CHECKING:
    from docstruct.models import PageContent

_log = logging.getLogger(__name__)

#: 복원 뒤 글자 수가 이 비율 밑으로 떨어지면 되돌린다.
MIN_TEXT_KEEP = 0.95
#: 이보다 열이 적은 표는 다루지 않는다 (작은 표는 오판 여지가 크다).
MIN_COLS = 4


def _detected_cols(cells: list[dict] | None) -> int:
    """인식 결과의 열 수."""
    if not cells:
        return 0
    return max(int(c.get("col", 0)) + int(c.get("colspan", 1) or 1) for c in cells)


def _text_len(cells: list[dict] | None) -> int:
    """셀 글자 수 합 (공백 제외)."""
    if not cells:
        return 0
    return sum(len("".join((c.get("text") or "").split())) for c in cells)


def restore_table(pdf_path, page_no: int, bbox: dict,
                  detected_cells: list[dict] | None) -> dict | None:
    """표 하나를 접은 괘선 격자로 다시 세운다.

    입력: pdf_path — 원본, page_no — 쪽, bbox — 표 영역,
          detected_cells — 인식 셀 (열 수 비교용)
    출력: {"cells", "markdown", "before", "after"}. 조건이 안 되면 None
    """
    from docstruct.experiments.tsr.measure.line_grid import (
        _clip, fold_decor_bounds, lattice_cells, page_segments,
    )
    from docstruct.experiments.tsr.measure.vector_grid import _bands

    detected = _detected_cols(detected_cells)
    if detected < MIN_COLS:
        return None

    horizontal, vertical = page_segments(pdf_path, page_no)
    h = _clip(horizontal, bbox["t"], bbox["b"], bbox["l"], bbox["r"])
    v = _clip(vertical, bbox["l"], bbox["r"], bbox["t"], bbox["b"])
    if not h or not v:
        return None
    v = fold_decor_bounds(pdf_path, page_no, bbox, v)

    got = lattice_cells(h, v)
    if got is None:
        return None
    positions, _rows, cols = got
    if cols <= detected:
        return None                              # 열 밀림이 아니다 — 손대지 않는다

    xs = _bands([x for x, _, _ in v])
    ys = _bands([y for y, _, _ in h])
    if len(xs) - 1 != cols:
        return None                              # 격자와 경계가 어긋난다

    rects = [(xs[c], ys[r], xs[c + span_c], ys[r + span_r])
             for r, c, span_r, span_c in positions]
    from docstruct.experiments.tsr.restore.grid_restore import _cell_texts, cells_to_markdown

    texts = _cell_texts(pdf_path, page_no, rects)
    if texts is None or len(texts) != len(positions):
        return None

    cells = [{"row": r, "col": c, "rowspan": span_r, "colspan": span_c,
              "text": text}
             for (r, c, span_r, span_c), text in zip(positions, texts)]
    cells.sort(key=lambda cell: (cell["row"], cell["col"]))

    # **글자가 줄면 되돌린다.** 격자가 표를 일부만 덮으면 바깥 칸의 글자가
    # 통째로 빠진다 — 열을 바로잡자고 내용을 잃는 것은 손해다.
    before, after = _text_len(detected_cells), _text_len(cells)
    if before and after < before * MIN_TEXT_KEEP:
        _log.debug("%s쪽 복원 취소 — 글자 %d → %d", page_no, before, after)
        return None

    return {"cells": cells, "markdown": cells_to_markdown(cells),
            "before": detected, "after": cols}


def run(pages: list[PageContent], **kwargs) -> int:
    """열이 밀린 표를 괘선 격자로 다시 세운다.

    입력: pages — 페이지 목록 (제자리 갱신), pdf_path — 원본 경로
    출력: 고친 표 수
    """
    source = kwargs.get("pdf_path")
    if not source:
        return 0

    fixed = 0
    for page in pages:
        if not page.tables or not isinstance(page.page_no, int):
            continue
        for table in page.tables:
            if not table.bbox or not table.cells:
                continue
            if table.source == "grid" or getattr(table, "head_grid", None):
                continue                         # ⑦·⑫가 이긴 자리는 지킨다
            if getattr(table, "agreed_grid", None):
                continue                         # ⑭도 마찬가지
            result = restore_table(source, page.page_no, table.bbox, table.cells)
            if result is None:
                continue
            table.cells = result["cells"]
            table.markdown = result["markdown"]
            table.source = "grid"                # VLM 이 다시 쓰지 않게 한다
            table.lattice_restore = {"before": result["before"],
                                     "after": result["after"]}
            fixed += 1
            page.trace.add(
                "experiments.tsr.restore.lattice_restore", "괘선 격자로 표 복원",
                f"{table.id} · 열 {result['before']} → {result['after']} "
                f"(값 귀속까지 다시 세움)",
                status="warn")
    return fixed


register(Experiment(
    key="lattice_restore",
    title="괘선 격자로 표 전체 복원 (열 밀림 교정)",
    purpose="인식이 중간 열을 잃어 본문 값이 밀린 표를 지면 격자로 다시 세운다",
    origin="⑦ grid_restore 의 재작성 기계 + 0.4.3 장식 경계 접기",
    formats=("pdf",),
    needs=("vector", "geometry"),
    status="testing",
    note="⑬(col_grid)은 열 **수**만 맞춘다 — 마지막 열의 colspan 을 늘릴 "
         "뿐이라 중간 열이 빈 자리는 그대로 밀린다(조달청 p65 · 행안부 "
         "p63·p299). ⑫는 머리 2행만 손대므로 본문은 아무도 고치지 않았다. "
         "⑮는 접은 격자에서 셀 자리를, 그 칸의 지면 텍스트에서 글자를 "
         "가져와 값 귀속까지 바로잡는다. 글자 수가 5% 넘게 줄면 되돌린다.",
    run=run,
    knobs={"DOCSTRUCT_EXP_LATTICE_RESTORE": "false 면 끔 (기본 켬 — 승격)"},
))
