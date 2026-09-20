"""Docling TableItem → GFM markdown.

입력:
    Docling TableItem

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

from docstruct.converters.common.table import merge_continuation, render_md_table
from docstruct.converters.html.tables import flatten_header_rows

#: 헤더로 병합할 최대 행 수 (그 이상은 데이터로 간주)
MAX_HEADER_ROWS = 3


#: 세로 병합이 이어지는 칸에 남기는 표식. HWP·HWPX 경로와 같은 값을 쓴다 —
#: 형식마다 다르면 읽는 쪽이 분기해야 한다.
#: 옛 표식 — 호환을 위해 이름은 남긴다. 실제 채움은
#: `converters.common.table.merge_continuation` 이 정한다.
MERGE_UP = "〃"

#: 이 표식을 끄는 환경변수. 값을 복제하지도 비우지도 않는 절충이라,
#: 다운스트림이 원치 않으면 끌 수 있어야 한다.
MERGE_MARK_ENV = "DOCSTRUCT_TABLE_MERGE_MARK"


def _merge_mark_enabled() -> bool:
    """세로 병합 표식을 남길지.

    입력: 없음 (`DOCSTRUCT_TABLE_MERGE_MARK`)
    출력: 남기면 True (기본)
    """
    import os

    raw = os.environ.get(MERGE_MARK_ENV, "").strip().lower()
    return raw not in ("0", "false", "off", "no")


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


def cell_grid(item) -> list[dict]:
    """Docling TableItem 의 셀 격자를 낸다.

    입력: item — Docling TableItem
    출력: 셀 dict 목록 (row, col, rowspan, colspan, text)
    비고:
        markdown 은 병합을 표현하지 못한다. 이 값을 함께 내보내면 구조화
        단계가 병합 셀 값을 하위 행에 전파할 수 있다.

        HWPX 경로(`hwpxtree.table_grids`)와 같은 형태를 쓴다 — 형식마다
        다르면 쓰는 쪽이 분기해야 한다.
    """
    data = getattr(item, "data", None)
    cells = list(getattr(data, "table_cells", None) or [])
    grid: list[dict] = []
    for cell in cells:
        row0, row1 = _cell_span(cell, "row")
        col0, col1 = _cell_span(cell, "col")
        grid.append({
            "row": row0,
            "col": col0,
            "rowspan": max(row1 - row0, 1),
            "colspan": max(col1 - col0, 1),
            "text": (getattr(cell, "text", "") or "").strip(),
        })
    return sorted(grid, key=lambda c: (c["row"], c["col"]))


def empty_cell_ratio(item) -> dict:
    """격자에서 셀 객체가 없는 칸의 비율을 잰다.

    입력: item — Docling TableItem
    출력: 진단 dict
        declared   선언된 격자 칸 수 (num_rows × num_cols)
        covered    셀이 실제로 덮는 칸 수 (병합 span 반영)
        empty      셀 객체가 없는 칸 수
        ratio      그 비율 0~1
    비고:
        **이것은 "구조 결함" 이 아니라 "빈 칸" 이다.** 처음에 결함으로
        읽었다가 정정했다. docling 은 값이 없는 칸에 TableCell 객체를 만들지
        않으므로, 덮이지 않은 칸은 원본에서 비어 있던 자리다.

        실측으로 확인했다 — 표 세 개에서 `text` 가 빈 셀이 **0개**였다.
        즉 셀이 있으면 반드시 값이 있고, 값이 없으면 셀이 없다.

            선언 29행 × 5열 = 145칸 · table_cells 143개 · 빈 text 0개

        그 2칸은 렌더 결과에서도 빈칸으로 나온다. 정상이다.

        **격자 크기 자체가 원본과 다른 경우는 이 값으로 잡히지 않는다.**
        `num_rows` 안에서만 세기 때문이다. 스캔본에서 13행 표가 7행으로
        인식된 사례가 그렇다 — 그때는 이 비율이 낮게 나온다.
    """
    data = getattr(item, "data", None)
    cells = list(getattr(data, "table_cells", None) or [])
    rows = int(getattr(data, "num_rows", 0) or 0)
    cols = int(getattr(data, "num_cols", 0) or 0)
    if rows <= 0 or cols <= 0 or not cells:
        return {"declared": 0, "covered": 0, "empty": 0, "ratio": 0.0}

    filled = [[False] * cols for _ in range(rows)]
    for cell in cells:
        row0, row1 = _cell_span(cell, "row")
        col0, col1 = _cell_span(cell, "col")
        for row in range(max(row0, 0), min(row1, rows)):
            for col in range(max(col0, 0), min(col1, cols)):
                filled[row][col] = True

    declared = rows * cols
    covered = sum(1 for line in filled for value in line if value)
    empty = declared - covered
    return {
        "declared": declared,
        "covered": covered,
        "empty": empty,
        "ratio": empty / declared if declared else 0.0,
    }


#: 옛 이름. 0.3.1~0.3.6 에서 "구조 결함" 으로 잘못 부르던 것이다.
structure_gap = empty_cell_ratio


def grid_from_cells(cells: list[dict], header_count: int = 0) -> list[list[str]]:
    """정규화된 셀 목록으로 글자 격자를 만든다 (0.5.10).

    입력: cells — `cell_grid` 형태 (row·col·rowspan·colspan·text), header_count — 머리행 수
    출력: 글자 격자 (list[list[str]])
    비고:
        **markdown 과 `cells` 가 같은 재료를 쓰게 하는 함수다.** 예전에는
        `docling_table_to_markdown` 이 Docling 객체에서 **따로** 격자를
        만들고 `cell_grid` 가 또 따로 만들었다. 둘이 어긋나면 결과물의 두
        필드가 다른 말을 한다 — 0.4.83 의 `repair_leaks` 와 같은 병이다.

        실측(개인정보보호위원회 PDF): 51표 중 8표에서 markdown 이 `cells`
        보다 값이 적었고, 한 표는 `신규` 하나를 잃고 `30` 이 '27 에서
        '26 으로 **한 칸 밀렸다.** HWPX 경로는 같은 문서에서 0표였다 —
        거기서는 렌더러가 셀 목록을 그대로 읽기 때문이다.

        격자 크기는 `num_rows`·`num_cols` 가 아니라 **셀이 실제로 덮는
        범위**로 잡는다. 선언값이 작으면 바깥 셀이 조용히 잘렸다.
    """
    if not cells:
        return []
    rows = max(c["row"] + max(c.get("rowspan", 1), 1) for c in cells)
    cols = max(c["col"] + max(c.get("colspan", 1), 1) for c in cells)
    grid: list[list[str]] = [[""] * cols for _ in range(rows)]

    for cell in sorted(cells, key=lambda c: (c["row"], c["col"])):
        text = (cell.get("text") or "").strip()
        if not text:
            continue
        r0, c0 = cell["row"], cell["col"]
        r1 = r0 + max(cell.get("rowspan", 1), 1)
        c1 = c0 + max(cell.get("colspan", 1), 1)
        grid[r0][c0] = text

        if r0 < header_count:
            # 머리는 span 전체에 퍼뜨린다 — 열별로 접어야 하기 때문이다.
            for r in range(r0, min(r1, rows)):
                for c in range(c0, min(c1, cols)):
                    if not grid[r][c]:
                        grid[r][c] = text
            continue

        # 세로 병합으로 덮인 칸을 채운다. 무엇으로 채울지는
        # `converters.common.table` 한 곳이 정한다 (0.5.6).
        if _merge_mark_enabled() and r1 - r0 > 1:
            filler = merge_continuation(text)
            for r in range(r0 + 1, min(r1, rows)):
                if not grid[r][c0]:
                    grid[r][c0] = filler
    return grid


def docling_table_to_markdown(item) -> str:
    """TableItem 을 GFM 표로 변환한다.

    입력: item — Docling TableItem
    출력: GFM 표 문자열. 변환 불가 시 빈 문자열
    동작:
        `cell_grid(item)` 이 낸 **같은 셀 목록**으로 격자를 세운다 —
        markdown 과 `cells` 가 어긋날 수 없다 (0.5.10). 머리 셀은 span
        전체에 값을 전파하고, 다단 머리는 열별로 이어 붙인다.
    """
    data = getattr(item, "data", None)
    if not data or not data.table_cells:
        return ""

    cells = cell_grid(item)
    if not cells:
        return ""
    rows = max(c["row"] + max(c.get("rowspan", 1), 1) for c in cells)
    header_count = _header_row_count(list(data.table_cells), rows)

    grid = grid_from_cells(cells, header_count)
    while grid and not any(cell.strip() for cell in grid[-1]):
        grid.pop()
    if not grid:
        return ""

    if header_count > 1:
        grid = flatten_header_rows(grid, min(header_count, len(grid)))

    return render_md_table(grid)
