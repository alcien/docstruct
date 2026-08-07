"""HTML `<table>` → markdown.

역할:
    rowspan/colspan 을 격자로 펼치고, 다단 헤더는 열별로 이어 붙여
    GFM 표 한 줄 헤더로 만든다.
호출부:
    converters.html.blocks, docstruct.tables.docling (헤더 병합만 재사용)
출력:
    GFM 표 문자열 또는 격자(list[list[str]])
"""
from __future__ import annotations

from docstruct.converters.common.table import display_width, render_md_table
from docstruct.converters.deps import Tag
from docstruct.converters.html.utils import cell_text, int_attr

__all__ = [
    "display_width",
    "render_md_table",
    "expand_table_grid",
    "table_rows",
    "looks_like_subheader_row",
    "count_header_rows",
    "flatten_header_rows",
    "prepare_md_table_rows",
]


def expand_table_grid(table: "Tag") -> list[list[str]]:
    """rowspan/colspan 을 반영해 표를 직사각형 격자로 확장한다.

    입력: table — `<table>` Tag
    출력: list[list[str]] — 모든 행의 열 수가 같은 격자
    동작: HTML 표는 병합 셀 아래 행에 `<td>` 가 생략되어 행마다 열 수가
          다르다. 브라우저 렌더링과 같은 규칙으로 빈 칸을 채워 복원한다.
          병합 값은 왼쪽 위 칸에만 두고 나머지는 빈 문자열로 둔다.
    """
    trs = [
        tr for tr in table.find_all("tr")
        if tr.find_parent("table") is table
    ]
    if not trs:
        return []

    pending: dict[tuple[int, int], str] = {}
    grid: list[list[str]] = []

    for r, tr in enumerate(trs):
        row: list[str] = []
        col = 0

        for cell in tr.find_all(["th", "td"], recursive=False):
            while (r, col) in pending:
                row.append(pending.pop((r, col)))
                col += 1

            text = cell_text(cell)
            rs = int_attr(cell, "rowspan")
            cs = int_attr(cell, "colspan")

            for dc in range(cs):
                row.append(text if dc == 0 else "")
                for dr in range(1, rs):
                    pending[(r + dr, col)] = ""
                col += 1

        while (r, col) in pending:
            row.append(pending.pop((r, col)))
            col += 1

        if any(c.strip() for c in row):
            grid.append(row)

    if not grid:
        return []

    width = max(len(row) for row in grid)
    for row in grid:
        row.extend([""] * (width - len(row)))
    return grid


def table_rows(table: "Tag") -> list[list[str]]:
    """`<table>` 요소를 격자로 펼친다.

    입력: table — BeautifulSoup Tag
    출력: list[list[str]] — rowspan/colspan 이 반영된 격자
    """
    return expand_table_grid(table)


def looks_like_subheader_row(prev_row: list[str], row: list[str]) -> bool:
    """이전 행의 병합 헤더 아래 오는 보조 헤더 행인지 판별한다.

    입력: prev_row — 직전 행, row — 판별할 행 (둘 다 셀 문자열 목록)
    출력: 보조 헤더로 보이면 True
    동작: 월 번호처럼 짧은 셀이 다수(60% 이상)이고 이전 행 헤더 아래가
          비어 있으면 보조 헤더로 본다. ○·◦ 불릿이나 긴 셀(20자 초과)이
          있으면 본문 데이터 행으로 간주해 제외한다.
    """
    if not any(c.strip() for c in row):
        return False

    filled_cells = [c.strip() for c in row if c.strip()]
    row_text = " ".join(filled_cells)
    if "○" in row_text or "◦" in row_text:
        return False
    if any(len(c) > 20 for c in filled_cells):
        return False

    width = max(len(prev_row), len(row))
    empty_leading = 0
    for c in range(width):
        prev_t = prev_row[c].strip() if c < len(prev_row) else ""
        row_t = row[c].strip() if c < len(row) else ""
        if prev_t and not row_t:
            empty_leading += 1

    filled = len(filled_cells)
    if empty_leading < 1 or filled < 2:
        return False

    short = sum(1 for c in filled_cells if len(c) <= 6)
    return short >= filled * 0.6


def count_header_rows(rows: list[list[str]], max_header: int = 3) -> int:
    """GFM 단일 헤더로 병합할 행 수를 센다.

    입력: rows — 격자, max_header — 병합 상한 (기본 3)
    출력: 1 이상의 헤더 행 수
    """
    n = 1
    while n < len(rows) and n < max_header:
        if looks_like_subheader_row(rows[n - 1], rows[n]):
            n += 1
        else:
            break
    return n


def flatten_header_rows(rows: list[list[str]], header_count: int) -> list[list[str]]:
    """여러 줄 헤더를 한 줄로 합친다.

    입력: rows — 격자, header_count — 헤더 행 수
    출력: 헤더가 한 줄로 합쳐진 격자 (열별로 상위 헤더를 이어 붙임)
    """
    if header_count <= 1:
        return rows
    width = max(len(r) for r in rows[:header_count])
    merged: list[str] = []
    for c in range(width):
        parts: list[str] = []
        for r in range(header_count):
            if c < len(rows[r]):
                t = rows[r][c].strip()
                if t and t not in parts:
                    parts.append(t)
        merged.append(" ".join(parts))
    return [merged] + rows[header_count:]


def prepare_md_table_rows(rows: list[list[str]]) -> list[list[str]]:
    """격자를 markdown 표로 만들 수 있게 다듬는다.

    입력: rows — 격자
    출력: 헤더 병합·빈 행 제거가 끝난 격자
    """
    if not rows:
        return rows
    return flatten_header_rows(rows, count_header_rows(rows))
