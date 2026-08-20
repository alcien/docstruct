"""테스트용 표 객체를 **실제 docling 스키마로** 만든다.

역할:
    `TableCell` / `TableData` 를 docling 이 설치돼 있으면 그 클래스로,
    없으면 같은 필드를 갖춘 대역으로 만든다.
호출부:
    tests/test_regressions.py

왜 필요한가
---------
가짜 객체(`SimpleNamespace`)로 시험하면 **실제와 다른 것을 시험하게 된다.**
실제로 그 실수가 두 번 있었다.

    structure_gap  가짜가 빈 셀도 만들어 두어, 실제 docling 이 만들지
                   않는다는 것을 놓쳤다 → 정상 표를 82% 오판
    fill_diff      시험 데이터가 실제 문단 ID 형태와 달라 두 번 고쳤다

docling 이 있으면 **진짜 클래스**를 쓴다. 없는 환경에서는 대역을 쓰되,
필드 이름을 실제 스키마에서 가져와 어긋나면 즉시 드러나게 한다.
"""
from __future__ import annotations

from dataclasses import dataclass


def _docling_classes():
    """docling 의 표 클래스를 가져온다.

    입력: 없음
    출력: (TableCell, TableData, BoundingBox) 또는 None
    """
    try:
        from docling_core.types.doc.document import TableCell, TableData
        from docling_core.types.doc.base import BoundingBox
    except ImportError:
        return None
    return TableCell, TableData, BoundingBox


#: 실제 `TableCell` 이 가진 필드. 대역이 이 이름을 벗어나면 안 된다.
CELL_FIELDS = (
    "start_row_offset_idx", "end_row_offset_idx",
    "start_col_offset_idx", "end_col_offset_idx",
    "row_span", "col_span", "column_header", "text", "bbox",
)


@dataclass
class _Box:
    """BoundingBox 대역 (TOPLEFT 기준)."""

    l: float  # noqa: E741 - docling 필드명을 그대로 쓴다
    t: float
    r: float
    b: float


@dataclass
class _Cell:
    """TableCell 대역.

    필드 이름은 `CELL_FIELDS` 와 같아야 한다 — 실제 코드가 `getattr` 로
    이 이름들을 읽는다.
    """

    start_row_offset_idx: int
    end_row_offset_idx: int
    start_col_offset_idx: int
    end_col_offset_idx: int
    row_span: int
    col_span: int
    column_header: bool
    text: str
    bbox: _Box | None


@dataclass
class _Data:
    """TableData 대역."""

    num_rows: int
    num_cols: int
    table_cells: list


@dataclass
class _Item:
    """TableItem 대역."""

    data: _Data


def make_cell(
    row: int,
    col: int,
    text: str = "값",
    *,
    row_span: int = 1,
    col_span: int = 1,
    header: bool = False,
    box: tuple[float, float, float, float] | None = None,
):
    """표 셀 하나를 만든다.

    입력:
        row, col      격자 위치 (0부터)
        text          셀 내용
        row_span      세로 병합 칸 수
        col_span      가로 병합 칸 수
        header        헤더 셀인지
        box           (left, top, right, bottom). 없으면 bbox 는 None
    출력: TableCell (docling 이 있으면 진짜, 없으면 대역)
    비고:
        **docling 은 값이 없는 칸에 셀을 만들지 않는다.** 빈 셀을 시험에
        넣으면 실제와 달라지므로, 필요한 칸만 만든다.
    """
    classes = _docling_classes()
    if classes is not None:
        TableCell, _, BoundingBox = classes
        bbox = None
        if box is not None:
            left, top, right, bottom = box
            bbox = BoundingBox(l=left, t=top, r=right, b=bottom)
        return TableCell(
            start_row_offset_idx=row, end_row_offset_idx=row + row_span,
            start_col_offset_idx=col, end_col_offset_idx=col + col_span,
            row_span=row_span, col_span=col_span,
            column_header=header, text=text, bbox=bbox,
        )
    return _Cell(
        start_row_offset_idx=row, end_row_offset_idx=row + row_span,
        start_col_offset_idx=col, end_col_offset_idx=col + col_span,
        row_span=row_span, col_span=col_span,
        column_header=header, text=text,
        bbox=_Box(*box) if box else None,
    )


def make_table(rows: int, cols: int, cells: list):
    """표 하나를 만든다.

    입력: rows, cols — 격자 크기, cells — make_cell 로 만든 셀 목록
    출력: TableItem (docling 이 있으면 진짜 data, 없으면 대역)
    """
    classes = _docling_classes()
    if classes is not None:
        _, TableData, _ = classes
        return _Item(data=TableData(
            num_rows=rows, num_cols=cols, table_cells=cells))
    return _Item(data=_Data(num_rows=rows, num_cols=cols, table_cells=cells))


def uses_real_docling() -> bool:
    """진짜 docling 클래스를 쓰고 있는지.

    입력: 없음
    출력: 진짜면 True
    비고:
        테스트가 어느 쪽으로 돌았는지 알리기 위함이다. 대역으로만 돌면
        실제와 어긋날 여지가 남는다.
    """
    return _docling_classes() is not None


@dataclass
class _OcrLine:
    """rapidocr 결과 한 줄의 대역.

    실제 `OcrLine` 과 같은 속성을 갖는다 — 코드가 `.text`·`.score`·`.box`
    를 읽는다.
    """

    text: str
    score: float
    box: list


def make_ocr_line(
    text: str,
    left: float,
    top: float,
    right: float,
    bottom: float,
    *,
    score: float = 0.9,
):
    """OCR 조각 하나를 만든다.

    입력: text — 인식된 글자, left~bottom — 픽셀 좌표, score — 신뢰도
    출력: OcrLine (docstruct 가 설치돼 있으면 진짜)
    비고:
        네 꼭짓점을 시계 방향으로 넣는다. 실제 rapidocr 이 그렇게 준다.
    """
    corners = [(left, top), (right, top), (right, bottom), (left, bottom)]
    try:
        from docstruct.converters.pdf.rapidocr_ko import OcrLine
    except ImportError:
        return _OcrLine(text=text, score=score, box=corners)
    return OcrLine(text=text, score=score, box=corners)
