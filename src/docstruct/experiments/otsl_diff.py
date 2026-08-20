"""실험 ⑤ — 표 구조를 OTSL 토큰으로 바꿔 견준다.

무엇을 보완하는가
--------------
두 방법의 결과를 견줄 기준이 없다. 지금은 "칸 수가 늘었나" 로만 보는데,
그것은 거칠다 — 칸 수가 같아도 병합 구조가 다를 수 있다.

어디서 빌린 발상인가
-----------------
OTSL(IBM, ICDAR 2023) 은 표 구조를 다섯 토큰으로 표현한다. HTML 이 28개
이상을 쓰는 것을 줄여 시퀀스를 절반으로 만들었다.

    C  셀 시작        L  왼쪽 셀에 이어짐 (가로 병합)
    U  위 셀에 이어짐 (세로 병합)     X  양쪽에 이어짐
    NL 행 바꿈

우리는 생성에 쓰지 않고 **비교**에 쓴다. 구조를 문자열로 만들면 어디가
다른지 정량화된다.

한계
----
- **진단용이다.** 구조를 고치지 않는다.
- 다만 좌표가 필요 없어 **HWP·HWPX 를 포함한 전 형식**에 통한다.
"""
from __future__ import annotations

import logging

from docstruct.experiments.registry import Experiment, register
from docstruct.models import PageContent

_log = logging.getLogger(__name__)


def to_otsl(cells: list[dict], rows: int, cols: int) -> str:
    """셀 격자를 OTSL 토큰 문자열로 만든다.

    입력: cells — {row, col, rowspan, colspan} 목록, rows·cols — 격자 크기
    출력: 공백으로 이은 토큰 문자열
    비고:
        `TableInfo.cells` 형식을 그대로 받는다 — PDF·HWPX 가 같은 형태라
        형식에 무관하게 쓸 수 있다.
    """
    if rows <= 0 or cols <= 0:
        return ""
    grid = [["" for _ in range(cols)] for _ in range(rows)]
    for cell in cells:
        row = int(cell.get("row", 0))
        col = int(cell.get("col", 0))
        for r in range(row, min(row + int(cell.get("rowspan", 1) or 1), rows)):
            for c in range(col, min(col + int(cell.get("colspan", 1) or 1), cols)):
                if r == row and c == col:
                    grid[r][c] = "C"
                elif r == row:
                    grid[r][c] = "L"
                elif c == col:
                    grid[r][c] = "U"
                else:
                    grid[r][c] = "X"
    return " ".join(
        " ".join(token or "C" for token in line) + " NL" for line in grid
    )


def token_diff(left: str, right: str) -> int:
    """두 OTSL 문자열이 다른 토큰 수.

    입력: left, right — OTSL 문자열
    출력: 다른 토큰 개수. 길이가 다르면 그 차이도 더한다
    """
    a, b = left.split(), right.split()
    common = sum(1 for x, y in zip(a, b) if x != y)
    return common + abs(len(a) - len(b))


def run(pages: list[PageContent], **_kwargs) -> int:
    """표 구조를 OTSL 로 기록한다.

    입력: pages — 페이지 목록
    출력: 기록한 표 수
    비고:
        비교 대상이 있을 때(재추출 전후 등) 차이를 낼 수 있도록 구조를
        남겨 둔다. 지금은 기록만 한다.
    """
    marked = 0
    for page in pages:
        for table in page.tables:
            cells = getattr(table, "cells", None)
            if not cells:
                continue
            rows = max(int(c.get("row", 0)) + int(c.get("rowspan", 1) or 1)
                       for c in cells)
            cols = max(int(c.get("col", 0)) + int(c.get("colspan", 1) or 1)
                       for c in cells)
            table.otsl = to_otsl(cells, rows, cols)
            marked += 1
    return marked


register(Experiment(
    key="otsl_diff",
    title="표 구조를 OTSL 토큰으로 표현",
    purpose="두 방법의 결과를 정량 비교할 기준",
    origin="OTSL (IBM, ICDAR 2023) — 다섯 토큰 구조 표현",
    formats=("pdf", "hwp", "hwpx"),
    status="proposed",
    note="진단용 — 검출이 아니라 기록이다. 지금은 쓰이지 않으나, "
         "VLM 재작성 전후 구조를 견줄 때 쓸 예정이라 남겨 둔다. "
         "좌표가 필요 없어 전 형식에 통한다.",
    run=run,
))
