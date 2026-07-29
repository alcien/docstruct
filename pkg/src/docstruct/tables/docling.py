"""Docling TableItem → GFM markdown.

역할:
    Docling 이 복원한 표 격자(행·열·병합·헤더 정보)를 GFM 표 문자열로 바꾼다.
    GFM 은 병합셀과 다단 헤더를 표현할 수 없으므로, 헤더는 열 단위로 병합해
    한 줄로 만들고 데이터 셀은 좌상단에만 값을 둔다.
호출부:
    docstruct.extractors.pdf.extract_pdf_pages
출력:
    GFM 표 문자열. 셀이 없거나 크기 정보가 없으면 빈 문자열
"""
from __future__ import annotations

from docstruct.converters.common.table import render_md_table
from docstruct.converters.html.tables import flatten_header_rows

#: 헤더로 병합할 최대 행 수 (그 이상은 데이터로 간주)
MAX_HEADER_ROWS = 3


def _cell_span(cell, axis: str) -> tuple[int, int]:
    """셀이 차지하는 (시작, 끝) 인덱스.

    입력: cell — TableCell, axis — 'row' | 'col'
    출력: (start, end) 튜플. end 가 없거나 잘못되면 span 값으로 보정
    """
    start = getattr(cell, f"start_{axis}_offset_idx", 0) or 0
    end = getattr(cell, f"end_{axis}_offset_idx", None)
    if not isinstance(end, int) or end <= start:
        span = getattr(cell, f"{axis}_span", 1) or 1
        end = start + max(int(span), 1)
    return int(start), int(end)


def _header_row_count(cells, num_rows: int) -> int:
    """상단 헤더 행 수를 센다.

    입력: cells — TableCell 목록, num_rows — 전체 행 수
    출력: column_header 가 덮는 상단 연속 행 수 (최대 MAX_HEADER_ROWS)
    """
    header_rows: set[int] = set()
    for cell in cells:
        if not getattr(cell, "column_header", False):
            continue
        r0, r1 = _cell_span(cell, "row")
        header_rows.update(range(r0, min(r1, num_rows)))

    count = 0
    while count in header_rows and count < min(num_rows, MAX_HEADER_ROWS):
        count += 1
    return count


def docling_table_to_markdown(item) -> str:
    """TableItem 을 GFM 표로 변환한다.

    입력: item — Docling TableItem (data.table_cells, num_rows, num_cols 사용)
    출력: GFM 표 문자열. 변환 불가 시 빈 문자열
    동작: 헤더 셀은 span 전체에 값을 전파하고, 다단 헤더는 열별로 이어 붙인다.
          데이터 셀은 좌상단 칸에만 두어 값이 중복 집계되지 않게 한다.
    """
    data = getattr(item, "data", None)
    if not data or not data.table_cells:
        return ""

    num_rows = int(getattr(data, "num_rows", 0) or 0)
    num_cols = int(getattr(data, "num_cols", 0) or 0)
    if num_rows <= 0 or num_cols <= 0:
        return ""

    cells = list(data.table_cells)
    header_count = _header_row_count(cells, num_rows)

    grid: list[list[str]] = [[""] * num_cols for _ in range(num_rows)]

    for cell in cells:
        text = (getattr(cell, "text", "") or "").strip()
        if not text:
            continue
        r0, r1 = _cell_span(cell, "row")
        c0, c1 = _cell_span(cell, "col")
        if not (0 <= r0 < num_rows and 0 <= c0 < num_cols):
            continue

        is_header = bool(getattr(cell, "column_header", False)) or r0 < header_count
        if is_header:
            # 헤더는 span 전체에 전파 — 열별 병합이 가능해야 합니다.
            for r in range(r0, min(r1, num_rows)):
                for c in range(c0, min(c1, num_cols)):
                    if not grid[r][c]:
                        grid[r][c] = text
        else:
            # 데이터는 좌상단에만 — 값 복제는 집계를 왜곡합니다.
            grid[r0][c0] = text

    while grid and not any(cell.strip() for cell in grid[-1]):
        grid.pop()
    if not grid:
        return ""

    if header_count > 1:
        grid = flatten_header_rows(grid, min(header_count, len(grid)))

    return render_md_table(grid)
