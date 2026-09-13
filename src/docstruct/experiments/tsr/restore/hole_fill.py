"""실험 hole_fill — 격자의 구멍을 **빈 칸**으로 메운다.

입력:
    ★ 격자 구멍

역할:
    표가 직사각 격자를 다 덮지 못한 자리에 빈 셀을 넣어 격자를 닫는다.
    글은 한 자도 만들지 않는다.
호출부:
    experiments.registry (`DOCSTRUCT_EXP_HOLE_FILL=1`)
출력:
    메운 표 수 (TableInfo.cells 갱신)

왜 이것이 안전한가
----------------
표는 직사각 격자이므로 **모든 (행, 열) 자리가 정확히 한 셀에 덮여야
한다**(0.4.69). 덮이지 않은 자리는 인식이 셀을 내놓지 않은 것이고, 그
자리에 원래 무엇이 있었든 **빈 셀을 넣는 것은 글을 지어내지 않는다.**

    괘선 복원(⑮·lattice_fill)   지면을 다시 읽어 **글을 가져온다**
    이 실험                     자리만 채운다 — **글은 건드리지 않는다**

그래서 괘선이 없어도 쓸 수 있다. `lattice_fill` 이 `no_lattice` 로 물러난
표가 대상이 된다.

무엇을 얻나 (실측 · 네 부처 남은 결함 65표)
------------------------------------------
    구멍만 있는 표   **59표 (91%)**   메우면 격자가 닫힌다 · 메울 칸 555
    겹침이 있는 표     6표            경계를 잘못 그은 것이라 이 방법으로는
                                     못 고친다 — 손대지 않는다

구멍은 **가장자리에 몰린다.** 실측(해경 `table_2` · 3열 11행): 구멍 7칸
중 6칸이 첫 열이었다. 표 왼쪽의 빈 칸을 인식이 내놓지 않은 것이다.

무엇을 하고 무엇을 안 하나
------------------------
    한다     덮이지 않은 자리에 빈 셀을 넣는다
    안 한다  겹친 표 (경계 오류라 메워도 낫지 않는다)
             글 채우기 (그것은 판독의 몫 — 지면을 봐야 한다)
             markdown 재생성 (셀만 고친다 — 본문은 그대로다)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from docstruct.experiments.registry import Experiment, register

if TYPE_CHECKING:
    from docstruct.models import PageContent

_log = logging.getLogger(__name__)

#: 이보다 많이 비어 있으면 메우지 않는다. 절반이 빈 표는 격자를 놓친
#: 것이 아니라 **애초에 표가 아닐** 수 있다 — 그런 것을 빈 칸으로 채우면
#: 없던 격자를 만들어 주는 셈이다.
MAX_HOLE_RATIO = 0.5


def fill_holes(cells: list[dict]) -> list[dict] | None:
    """덮이지 않은 자리에 빈 셀을 넣는다.

    입력: cells — 표의 셀 목록
    출력: 메운 셀 목록. 메울 것이 없거나 손대면 안 되면 None
    비고:
        **겹친 표는 손대지 않는다.** 두 번 덮인 자리는 경계를 잘못 그은
        것이고, 빈 칸을 더해도 낫지 않는다(오히려 셀 수만 는다).
    """
    from docstruct.structuring.checks import grid_check

    got = grid_check(cells)
    if got is None or got["ok"] or got["overlaps"]:
        return None
    if got["hole_ratio"] > MAX_HOLE_RATIO:
        return None

    seen = set()
    for cell in cells:
        for row in range(cell.get("row", 0),
                         cell.get("row", 0) + cell.get("rowspan", 1)):
            for col in range(cell.get("col", 0),
                             cell.get("col", 0) + cell.get("colspan", 1)):
                seen.add((row, col))

    filled = list(cells)
    for row in range(got["height"]):
        for col in range(got["width"]):
            if (row, col) in seen:
                continue
            # **빈 셀이다.** 글을 지어내지 않는다 — 자리만 닫는다.
            filled.append({"row": row, "col": col, "rowspan": 1,
                           "colspan": 1, "text": ""})
    filled.sort(key=lambda c: (c["row"], c["col"]))
    return filled


def run(pages: list[PageContent], **kwargs) -> int:
    """격자의 구멍을 빈 칸으로 메운다.

    입력: pages — 페이지 목록 (제자리 갱신)
    출력: 메운 표 수
    """
    from docstruct.structuring.checks import grid_check

    fixed = 0
    for page in pages:
        for table in page.tables:
            before = grid_check(table.cells)
            if before is None or before["ok"]:
                continue
            filled = fill_holes(table.cells)
            if filled is None:
                continue
            after = grid_check(filled)
            if after is None or not after["ok"]:
                # 메웠는데도 안 닫히면 받지 않는다 — 있을 수 없는 일이지만
                # 스스로에게 잣대를 건다 (0.4.70 의 교훈).
                continue
            table.cells = filled
            table.hole_fill = {
                "holes": before["holes"],
                "width": before["width"],
                "height": before["height"],
            }
            fixed += 1
            page.trace.add(
                "docstruct.experiments.tsr.restore.hole_fill", "격자 구멍 메움",
                f"{table.id} · {before['width']}×{before['height']} 에 빈 칸 "
                f"{before['holes']}개 — 글은 더하지 않았습니다")
    return fixed


register(Experiment(
    key="hole_fill",
    title="격자의 구멍을 빈 칸으로 메우기",
    purpose="괘선이 없어 `lattice_fill` 이 물러난 표를 닫는다 — 자리만 "
            "채우고 글은 만들지 않으므로 괘선이 필요 없다",
    origin="해양경찰청 실측 — `no_lattice` 가 34%(문체부는 10%)여서 괘선 "
           "복원이 닿지 않는 문서가 있다. 남은 결함 65표 중 **59표(91%)가 "
           "구멍만**이고 메우면 격자가 닫힌다",
    formats=("pdf", "hwpx", "hwp"),
    needs=("cells",),
    status="verified",
    note="겹친 표는 손대지 않는다(경계 오류라 메워도 낫지 않는다). 절반 "
         "넘게 빈 표도 제외한다 — 격자를 놓친 것이 아니라 애초에 표가 "
         "아닐 수 있다.\n"
         "실측(0.4.79 승격): 격자 결함 해경 14%→4% · 문체부 7%→1%, "
         "메운 표 41개 전부 markdown 정상. 남은 7표는 겹침 5 · "
         "lattice_fewer 2 로 설계상 대상이 아니다.",
    run=run,
))
