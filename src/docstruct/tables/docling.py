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

from pathlib import Path

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


def replace_cell_texts(item, page_image: str | Path, *, scale: float) -> dict:
    """표 셀의 텍스트를 한국어 OCR 결과로 갈아끼운다.

    입력:
        item        Docling TableItem (data.table_cells 를 제자리에서 수정)
        page_image  그 표가 있는 페이지의 렌더 이미지
        scale       렌더 배율 (픽셀 → 포인트 환산에 쓴다)
    출력: 진단 dict
        changed      바뀐 셀 수
        empty_cells  조각을 못 받아 원래 값이 남은 셀 수
        near_miss    **표 영역과 겹치는데** 어느 셀에도 못 들어간 조각 수
        outside      표 밖 조각 수 (표 밖 본문 — 정상)
    비고:
        **구조는 건드리지 않는다.** 행·열·병합(`row_span`/`col_span`)은
        TableFormer 가 만든 것을 그대로 두고 `text` 만 바꾼다. 실제 문서에서
        셀 10개가 모두 bbox·span 을 온전히 갖고 `text` 만 중국어였다
        (`品品品`, `昆品`) — 인식 언어가 틀린 것이지 구조가 틀린 것이 아니다.

        새 텍스트가 비면 원래 값을 남긴다. OCR 이 못 읽은 칸까지 지우면
        있던 내용을 잃는다.

        `near_miss` 와 `empty_cells` 를 나눠 세는 이유: 처음에는 미배정
        조각을 통째로 셌는데, 페이지 전체를 OCR 하므로 **표 밖 본문이 전부
        거기 잡혔다**(표 하나에 81개). 동작은 정상인데 지표가 원인을 가렸다.

            near_miss > 0   조각이 표 안에 있는데 셀에 못 들어감
                            → 겹침 임계가 빡빡하거나 셀 bbox 가 좁다
            near_miss = 0 인데 empty_cells > 0
                            → 셀 bbox 가 조각을 아예 안 덮는다 (TableFormer)
    """
    from docstruct.converters.pdf.cell_match import (
        Box, assign, box_of, fill_cells, from_pixels,
    )
    from docstruct.converters.pdf.rapidocr_ko import read_image

    blank = {"changed": 0, "empty_cells": 0, "near_miss": 0, "outside": 0}

    data = getattr(item, "data", None)
    cells = list(getattr(data, "table_cells", None) or [])
    boxed = [(index, cell) for index, cell in enumerate(cells)
             if getattr(cell, "bbox", None) is not None]
    if not boxed:
        return blank

    cell_boxes = [
        Box(float(cell.bbox.l), float(cell.bbox.t),
            float(cell.bbox.r), float(cell.bbox.b))
        for _, cell in boxed
    ]
    # 표 전체를 감싸는 상자. 조각이 표 안인지 밖인지 가르는 데 쓴다.
    area = Box(min(b.left for b in cell_boxes), min(b.top for b in cell_boxes),
               max(b.right for b in cell_boxes), max(b.bottom for b in cell_boxes))

    fragments = [
        (from_pixels(box_of(line.box), scale), line.text)
        for line in read_image(page_image) if line.box
    ]
    if not fragments:
        return blank

    texts, _ = fill_cells(cell_boxes, fragments)

    changed = 0
    for position, (_, cell) in enumerate(boxed):
        new_text = texts.get(position, "").strip()
        if new_text and new_text != (getattr(cell, "text", "") or ""):
            cell.text = new_text
            changed += 1

    # 실제로 배정된 조각을 그대로 센다. 임계를 다시 계산하면 설정을 바꿨을 때
    # 지표와 동작이 어긋난다 — 진단이 틀리면 원인을 잘못 짚게 된다.
    assigned = assign(cell_boxes, fragments)
    used = {id(box) for items in assigned.values() for box, _ in items}
    inside = [box for box, _ in fragments if box.overlap_ratio(area) >= 0.5]
    return {
        "changed": changed,
        "empty_cells": len(boxed) - len(texts),
        "near_miss": sum(1 for box in inside if id(box) not in used),
        "outside": len(fragments) - len(inside),
    }


def structure_gap(item) -> dict:
    """표 격자에서 셀이 빠진 정도를 잰다.

    입력: item — Docling TableItem
    출력: 진단 dict
        declared   선언된 격자 칸 수 (num_rows × num_cols)
        covered    셀이 실제로 덮는 칸 수 (병합 span 반영)
        missing    덮이지 않은 칸 수
        ratio      빠진 비율 0~1
    비고:
        표 구조 인식이 열을 통째로 놓치는 일이 있다. 실제 문서에서
        7행 2열(14칸)로 렌더되는 표의 셀이 **7개뿐**이었고, 왼쪽 열이
        아예 셀로 존재하지 않았다. 그 자리는 OCR 이 글자를 읽었어도
        넣을 곳이 없어 비어 나온다.

        병합을 반영해 센다 — `row_span`·`col_span` 이 큰 셀 하나가 여러
        칸을 덮으므로, 셀 개수만 세면 정상 표도 부족해 보인다.
    """
    data = getattr(item, "data", None)
    cells = list(getattr(data, "table_cells", None) or [])
    rows = int(getattr(data, "num_rows", 0) or 0)
    cols = int(getattr(data, "num_cols", 0) or 0)
    if rows <= 0 or cols <= 0 or not cells:
        return {"declared": 0, "covered": 0, "missing": 0, "ratio": 0.0}

    filled = [[False] * cols for _ in range(rows)]
    for cell in cells:
        row0, row1 = _cell_span(cell, "row")
        col0, col1 = _cell_span(cell, "col")
        for row in range(max(row0, 0), min(row1, rows)):
            for col in range(max(col0, 0), min(col1, cols)):
                filled[row][col] = True

    declared = rows * cols
    covered = sum(1 for line in filled for value in line if value)
    missing = declared - covered
    return {
        "declared": declared,
        "covered": covered,
        "missing": missing,
        "ratio": missing / declared if declared else 0.0,
    }


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
