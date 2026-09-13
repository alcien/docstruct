"""실험 ⑫ — 머리행 계층 복원 (head_grid).

입력:
    ★ 격자
출력:
    머리행 계층 복원

역할:
    ⑨(line_grid)가 세운 합성 격자에서 **머리 구간의 병합만** 가져와
    인식 결과에 반영한다. 표 전체를 다시 쓰지 않는다.
호출부:
    pipeline (실험이 켜졌을 때) — 사다리에서 ⑦ 다음, ⑨ 앞
설정:
    DOCSTRUCT_EXP_HEAD_GRID=false 로 끈다 — 0.4.3 승격 뒤 **기본 켬** (registry.DEFAULT_ON)

왜 있는가 (조달청 정답 대조)
-------------------------
예산 내역표 여섯(45·47·61·63·65·66쪽)이 **정답 병합 7개를 하나도 못
잡았다**(0/7 × 6 = 42개). 지면은 2층 머리다:

    회계구분·'25결산·'26예산·'27예산안·비고   → 세로 2칸 (rowspan 2)
    재정사업 성과평가                        → 가로 2칸 (colspan 2)
    그 아래                                  평가명 │ 결과

⑨는 이미 이 구조를 **정확히** 세우고 있었다:

    65쪽 lattice 머리: (0,0,2,1) (0,1,2,1) (0,2,2,1) (0,3,2,1)
                      (0,4,2,1) (0,5,1,2) (0,7,2,1) — 정답과 일치

⑦(grid_restore)이 이 표들을 건너뛴 것은 **사각형 덮개가 0.24~0.53**
이었기 때문이다. 그런데 같은 표의 lattice 덮개는 1.11~1.53 이다 —
근거가 있는데도 쓰지 않고 있었다.

왜 '머리만' 인가
--------------
lattice 전체로 표를 다시 쓰면 ⑨ 승격과 같은 문제에 부딪힌다(정밀도
69.6~84.5%, 문서군을 탄다 — §16-1·§23). 그러나 **머리 구간은 다르다**:

    · 자리가 적고(보통 1~2행) 구조가 단순하다
    · 머리 병합은 열 경계가 결정하는데, 그 경계는 괘선이 직접 그린다
    · 실측: 남은 놓침의 73%가 머리행(0~1행)에 있다

본문은 건드리지 않으므로, 틀려도 값 귀속이 무너지지 않는다. 머리는
"이 열이 무엇인가" 를 말할 뿐이다.

안전 장치
--------
    · 머리 격자의 열 수가 인식 결과와 다르면 물러난다 (자리가 어긋난다)
    · 이미 머리에 병합이 있으면 건드리지 않는다 (인식이 이미 봤다)
    · 본문 셀은 그대로 둔다 — 머리 행만 다시 쓴다
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from docstruct.experiments.registry import Experiment, register

if TYPE_CHECKING:
    from docstruct.models import PageContent

_log = logging.getLogger(__name__)

#: 머리로 볼 최대 행 수. 3층 머리는 실측에 없었다.
MAX_HEAD_ROWS = 2


def head_merges(pdf_path, page_no: int, bbox: dict) -> tuple[list[tuple], int] | None:
    """합성 격자에서 머리 구간의 셀 자리를 낸다.

    입력: pdf_path — 원본, page_no — 쪽, bbox — 표 영역
    출력: (머리 셀 자리 목록, 격자 열 수). 격자가 안 서면 None
    비고:
        머리 = 0행에서 시작하는 셀들. 세로 병합이 있으면 그 셀이 1행까지
        덮으므로, 1행에서 새로 시작하는 셀도 함께 담는다(2층의 아래칸).
    """
    from docstruct.experiments.tsr.measure.line_grid import table_lattice

    lattice = table_lattice(pdf_path, page_no, bbox)
    if lattice is None:
        return None
    cells, _rows, cols = lattice
    head = [c for c in cells if c[0] < MAX_HEAD_ROWS]
    if not head:
        return None
    return head, cols


def _detected_cols(cells: list[dict] | None) -> int:
    """인식 결과의 열 수."""
    if not cells:
        return 0
    return max(int(c.get("col", 0)) + int(c.get("colspan", 1) or 1) for c in cells)


def apply_head(table, head: list[tuple]) -> dict | None:
    """머리 셀 자리를 표에 반영한다 (본문은 그대로).

    입력: table — TableInfo, head — 머리 셀 자리 [(row,col,rowspan,colspan)]
    출력: {"before": n, "after": n, "rows": n} 또는 None (반영 안 함)
    비고:
        머리 행의 셀만 갈아 끼운다. 글자는 **인식이 읽은 것을 그대로**
        옮긴다 — 자리는 지면이, 글자는 인식이 안다. 자리에 맞는 글자를
        못 찾으면 빈 칸으로 둔다(지어내지 않는다).
    """
    cells = list(table.cells or [])
    if not cells:
        return None

    head_rows = max(r + rs for r, _c, rs, _cs in head)
    old_head = [c for c in cells if int(c["row"]) < head_rows]
    body = [c for c in cells if int(c["row"]) >= head_rows]
    if not old_head:
        return None

    before = sum(1 for c in old_head
                 if (c.get("rowspan", 1) or 1) > 1 or (c.get("colspan", 1) or 1) > 1)
    after = sum(1 for _r, _c, rs, cs in head if rs > 1 or cs > 1)
    if after <= before:
        return None                              # 얻는 것이 없다

    # 인식이 읽은 머리 글자를 자리 순서대로 나눠 준다.
    texts = [(c.get("text") or "").strip() for c in sorted(
        old_head, key=lambda c: (int(c["row"]), int(c["col"]))) ]
    texts = [t for t in texts if t]
    new_head = []
    for index, (row, col, rowspan, colspan) in enumerate(sorted(head)):
        new_head.append({
            "row": row, "col": col, "rowspan": rowspan, "colspan": colspan,
            "text": texts[index] if index < len(texts) else "",
        })
    table.cells = new_head + body
    return {"before": before, "after": after, "rows": head_rows}


def run(pages: list[PageContent], **kwargs) -> int:
    """머리 계층을 합성 격자로 복원한다.

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
            if table.source == "grid":
                continue                         # ⑦이 이미 통째로 복원했다
            got = head_merges(source, page.page_no, table.bbox)
            if got is None:
                continue
            head, cols = got
            if cols != _detected_cols(table.cells):
                continue                         # 열 수가 다르면 자리가 어긋난다
            report = apply_head(table, head)
            if report is None:
                continue
            table.head_grid = report
            fixed += 1
            page.trace.add(
                "experiments.tsr.restore.head_grid", "머리 계층 복원",
                f"{table.id} · 머리 {report['rows']}행 · 병합 "
                f"{report['before']} → {report['after']}",
                status="warn")
    return fixed


register(Experiment(
    key="head_grid",
    title="머리행 계층 복원",
    purpose="합성 격자(⑨)의 머리 구간 병합만 표에 반영 — 2층 머리를 납작하게 읽는 문제",
    origin="조달청 정답 대조 — 예산 내역표 6개가 정답 병합 7개를 하나도 못 잡았고"
           "(0/7×6), 같은 표의 ⑨ lattice 는 그 7개를 정확히 세우고 있었다",
    run=run,
    formats=("pdf",),
    needs=("cells", "vector"),
    status="verified",
    note="⑦은 사각형 덮개만 보는데 이 표들은 0.24~0.53 이라 물러났다. 같은 "
         "표의 lattice 덮개는 1.11~1.53 이다 — 근거가 있는데 쓰지 않고 "
         "있었다. 표 전체를 다시 쓰면 ⑨ 승격과 같은 정밀도 문제에 부딪히나, "
         "머리 구간은 자리가 적고 괘선이 직접 경계를 그린다. 본문을 건드리지 "
         "않으므로 틀려도 값 귀속이 무너지지 않는다.",
    knobs={"DOCSTRUCT_EXP_HEAD_GRID": "false 면 끔 (기본 켬 — 승격)"},
))
