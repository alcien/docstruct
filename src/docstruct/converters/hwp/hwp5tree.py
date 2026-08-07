"""pyhwp 파서 트리(hwp5.xmlmodel) → markdown.

역할:
    HWP 바이너리를 pyhwp 가 해석한 **의미 모델 트리**에서 직접 읽는다.
    문단·표·셀·병합 정보가 그대로 들어 있어 구조 손실이 거의 없다.
호출부:
    docstruct.converters.hwp.converter.HwpConverter
출력:
    markdown 문자열 (표는 GFM 표, 중첩 표는 부모 셀 안에 인라인)

왜 이 경로인가
--------------
같은 pyhwp 안에 층이 둘이다.

    hwp5.binmodel / xmlmodel   ← 파서 (여기)
            ↓ XSLT 변환
    hwp5html                   ← HTML 생성기

그동안 아래쪽(`hwp5html`)을 썼는데, XSLT 단계에서 내용이 크게 깎이고
문서에 따라 통째로 실패한다. 626KB 정부 성과계획서로 실측한 차이:

    hwp5html      표  48개 · 순수 글자  7,170 · 26.3초
    xmlmodel      표 119개 · 순수 글자 47,644 ·  1.0초

파서는 멀쩡했고 우리가 잘못된 층을 쓰고 있었다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from docstruct.converters.hwp.styling import DocStyles, format_paragraph, read_styles
from docstruct.converters.korean_text import normalize_korean_text

_log = logging.getLogger(__name__)

#: 셀 안에서 줄바꿈 대신 쓸 구분자. GFM 표는 셀 안 줄바꿈을 표현하지 못한다.
CELL_LINE_JOIN = " "

#: 중첩 표를 가리키는 표식. 부모 셀에는 이 표식만 남기고 표 본체는 부모 표
#: 바로 뒤에 붙인다. 번호는 **문서 전체 통번호** 다 — 부모마다 1부터 다시
#: 세면 같은 `[중첩표 1]` 이 문서에 열두 번 나와 어느 표를 가리키는지
#: 알 수 없다.
#:
#: GFM 은 셀 안에 표를 담지 못한다. 그대로 넣으면 한 줄로 눕고 `|` 가
#: 이스케이프되어 사람도 LLM 도 읽을 수 없다. 표식으로 관계를 남기고
#: 본체는 읽을 수 있는 형태로 두는 편이 양쪽 모두에 낫다.
NESTED_MARKER = "[중첩표 {n}]"

#: 쪽 나눔 자리에 넣는 표식. 호출부가 이걸로 페이지를 가른다.
#:
#: HWP 는 렌더링 시점에 쪽이 정해져서 파일에 페이지 경계가 없다. 다만
#: **명시적 쪽나눔**(Ctrl+Enter)과 **구역 구분**은 저장돼 있어, 그것만으로도
#: 통짜 문서를 의미 있는 단위로 자를 수 있다. 자동 줄바꿈으로 생긴 쪽은
#: 복원할 수 없다.
PAGE_BREAK = "\x0c"

#: Paragraph.split 의 쪽나눔 비트.
_SPLIT_NEW_PAGE = 0x04
_SPLIT_NEW_SECTION = 0x01

#: 표가 이보다 많은 열을 가지면 병합 복원을 포기하고 좌표대로만 채운다.
#: (비정상적으로 큰 값이 들어오면 메모리를 크게 먹는다)
MAX_COLS = 200


@dataclass
class _Cell:
    """표의 셀 하나."""

    col: int
    row: int
    colspan: int = 1
    rowspan: int = 1
    blocks: list[str] = field(default_factory=list)   # 문단 텍스트와 중첩 표


@dataclass
class _Table:
    """표 하나."""

    cols: int = 0
    cells: list[_Cell] = field(default_factory=list)
    #: 이 표 안에 있던 중첩 표들의 markdown (부모 표 뒤에 이어 붙인다)
    nested: list[str] = field(default_factory=list)


def is_available() -> bool:
    """이 경로를 쓸 수 있는지.

    입력: 없음
    출력: pyhwp 의 파서 모듈을 불러올 수 있으면 True
    """
    try:
        import hwp5.xmlmodel  # noqa: F401
    except Exception:                            # noqa: BLE001 - 어떤 이유든 사용 불가
        return False
    return True


@dataclass
class _Counter:
    """문서 전체에 걸친 중첩 표 번호."""

    value: int = 0

    def next(self) -> int:
        """다음 번호.

        입력: 없음
        출력: 1부터 증가하는 정수
        """
        self.value += 1
        return self.value


def to_markdown(hwp_path: str) -> str:
    """HWP 를 파서 트리로 읽어 markdown 으로 만든다.

    입력: hwp_path — HWP 파일 경로
    출력: markdown 문자열
    예외: pyhwp 가 파일을 열지 못하면 그대로 전파한다 (호출부가 폴백 판단)
    """
    from hwp5.treeop import ENDEVENT
    from hwp5.xmlmodel import Hwp5File

    _quiet_pyhwp()

    hwp = Hwp5File(hwp_path)
    try:
        styles = read_styles(hwp, ENDEVENT)
        _log.debug("서식: 스타일 %d개 · 글자모양 %d개",
                   len(styles.style_names), len(styles.charshapes))
        parts: list[str] = []
        nested_no = _Counter()
        for index in range(_section_count(hwp)):
            try:
                section = hwp.bodytext.section(index)
            except Exception as exc:             # noqa: BLE001 - 섹션 하나가 깨져도 나머지는 살린다
                _log.warning("%s번 섹션을 읽지 못했습니다: %s", index, exc)
                continue
            if parts:
                parts.append(PAGE_BREAK)         # 구역이 바뀌면 새 쪽으로 본다
            parts.extend(_read_section(section, ENDEVENT, styles, nested_no))
    finally:
        # 열린 핸들을 남기면 Windows 에서 원본 삭제·이동이 막히고,
        # 오래 사는 서버 프로세스에서는 핸들이 누적된다.
        _close_quietly(hwp)
    # 쪽 표식은 공백 판정에 걸리므로 따로 남긴다.
    kept = [p for p in parts if p == PAGE_BREAK or p.strip()]
    return "\n\n".join(
        p if p == PAGE_BREAK else normalize_korean_text(p) for p in kept
    )


def _close_quietly(hwp) -> None:
    """Hwp5File 을 예외 없이 닫는다.

    입력: hwp — Hwp5File
    출력: 없음
    동작: pyhwp 버전에 따라 close() 유무가 달라 getattr 로 찾아 부른다.
          닫기 실패는 본문 추출 성패와 무관하므로 삼킨다.
    """
    close = getattr(hwp, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception:                            # noqa: BLE001 - 닫기 실패는 치명적이지 않다
        _log.debug("Hwp5File 닫기 실패 — 무시합니다", exc_info=True)


def _quiet_pyhwp() -> None:
    """pyhwp 의 INFO 로그를 줄인다.

    입력: 없음
    출력: 없음
    비고:
        `hwp5.bintype` 이 모델 타입을 컴파일할 때마다 INFO 를 남긴다.
        문서 하나에 수십 줄이 쏟아져 우리 로그를 덮는다. 경고 이상만 남긴다.
    """
    for name in ("hwp5.bintype", "hwp5.binmodel", "hwp5.filestructure"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _section_count(hwp) -> int:
    """본문 섹션 수.

    입력: hwp — Hwp5File
    출력: 섹션 개수 (알 수 없으면 열리는 데까지 세어 본다)
    """
    count = getattr(hwp.bodytext, "section_count", None)
    if isinstance(count, int) and count > 0:
        return count
    found = 0
    while found < 1000:
        try:
            hwp.bodytext.section(found)
        except Exception:                        # noqa: BLE001 - 더 없으면 끝
            break
        found += 1
    return found


def _read_section(
    section, end_event, styles: DocStyles, nested_no: "_Counter"
) -> list[str]:
    """섹션 하나를 블록 목록으로 읽는다.

    입력: section — 섹션 객체, end_event — hwp5.treeop.ENDEVENT
    출력: 문단·표 markdown 블록 목록
    비고:
        표는 중첩될 수 있어 스택으로 다룬다. 중첩 표가 닫히면 **부모 셀의
        블록으로 넣어** 원문 위치를 지킨다. 별도 표로 빼면 읽는 순서가
        어긋나고 어느 셀에 속했는지 알 수 없게 된다.
    """
    blocks: list[str] = []
    tables: list[_Table] = []          # 열려 있는 표 (바깥 → 안쪽)
    cells: list[_Cell] = []            # 열려 있는 셀
    para: list[str] = []               # 현재 문단의 텍스트 조각
    #: 현재 문단의 서식 참조.
    #:   style — Paragraph.style_id
    #:   char  — 첫 Text 런의 charshape_id
    #:   mixed — 문단 안 런들의 charshape 가 서로 다른지
    style_ref: dict[str, int | bool | None] = {
        "style": None, "char": None, "mixed": False,
    }

    def flush_paragraph() -> None:
        """모아둔 문단 텍스트를 서식과 함께 현재 위치에 넣는다.

        입력: 없음 (둘러싼 para·style_ref·cells·blocks 사용)
        출력: 없음 (cells 안이면 현재 셀에, 아니면 blocks 에 추가)
        동작:
            문단의 charshape 가 **균일할 때만** 강조를 적용한다. 첫 런만
            굵은 문단에 첫 런의 모양을 통째로 입히면 문단 전체가 굵어진다
            — styling._apply_emphasis 의 전제(문단 전체 동일 모양)를
            여기서 보장한다.
        """
        raw = "".join(para).strip()
        para.clear()
        style_id = style_ref["style"]
        char_id = None if style_ref["mixed"] else style_ref["char"]
        style_ref["style"] = style_ref["char"] = None
        style_ref["mixed"] = False
        if not raw:
            return
        text = format_paragraph(
            raw, styles=styles, style_id=style_id,
            charshape_id=char_id, in_cell=bool(cells),
        )
        if not text:
            return
        if cells:
            cells[-1].blocks.append(text)
        else:
            blocks.append(text)

    for event, item in section.events():
        model, attrs = item[0], item[1]
        name = model.__name__

        if event is end_event:
            if name == "Paragraph":
                flush_paragraph()
            elif name == "TableCell" and cells:
                flush_paragraph()
                cells.pop()
            elif name == "TableControl" and tables:
                table = tables.pop()
                rendered = _render_table(table)
                if not rendered:
                    continue
                pieces = [rendered, *table.nested]
                if tables:
                    # 중첩 표: 부모 셀에는 표식만, 본체는 부모 표 뒤로 보낸다.
                    marker = NESTED_MARKER.format(n=nested_no.next())
                    if cells:
                        cells[-1].blocks.append(marker)
                    tables[-1].nested.append(f"{marker}\n\n" + "\n\n".join(pieces))
                else:
                    blocks.extend(pieces)
            continue

        if name == "TableControl":
            flush_paragraph()
            tables.append(_Table())
        elif name == "TableBody" and tables:
            tables[-1].cols = int(attrs.get("cols") or 0)
        elif name == "TableCell" and tables:
            flush_paragraph()
            cell = _Cell(
                col=int(attrs.get("col") or 0),
                row=int(attrs.get("row") or 0),
                colspan=int(attrs.get("colspan") or 1),
                rowspan=int(attrs.get("rowspan") or 1),
            )
            tables[-1].cells.append(cell)
            cells.append(cell)
        elif name == "Paragraph":
            style_ref["style"] = attrs.get("style_id")
            split = int(attrs.get("split") or 0)
            if split & (_SPLIT_NEW_PAGE | _SPLIT_NEW_SECTION) and not tables:
                # 표 안의 쪽나눔은 표를 쪼개므로 무시한다.
                flush_paragraph()
                if blocks and blocks[-1] != PAGE_BREAK:
                    blocks.append(PAGE_BREAK)
        elif name == "Text":
            para.append(attrs.get("text") or "")
            cid = attrs.get("charshape_id")
            if cid is not None:
                if style_ref["char"] is None:
                    style_ref["char"] = cid
                elif cid != style_ref["char"]:
                    # 런마다 모양이 다르면 문단 단위 강조를 포기한다.
                    style_ref["mixed"] = True
        elif name == "ControlChar":
            # 탭·줄바꿈 등. 셀 안에서는 공백으로 눕힌다.
            para.append(" ")

    flush_paragraph()
    return blocks


def _render_table(table: _Table) -> str:
    """표를 GFM markdown 으로 만든다.

    입력: table — 셀 목록과 열 수
    출력: markdown 표 문자열. 셀이 없으면 빈 문자열
    비고:
        병합 셀은 왼쪽 위 칸에만 내용을 넣고 나머지는 비운다. GFM 이 병합을
        표현하지 못하므로, 값을 복제하면 같은 내용이 여러 번 검색에 걸린다.
    """
    if not table.cells:
        return ""
    # TableBody 가 선언한 열 수와 셀 좌표로 실측한 열 수 중 **큰 쪽**을 쓴다.
    # 선언값이 실제보다 작은 문서가 있는데, 선언값만 믿으면 범위 밖 셀이
    # 아래 경계 검사에서 조용히 버려진다.
    measured = max(c.col + c.colspan for c in table.cells)
    cols = max(table.cols, measured) if table.cols else measured
    cols = max(1, min(cols, MAX_COLS))
    rows = max(c.row for c in table.cells) + 1
    grid = [["" for _ in range(cols)] for _ in range(rows)]

    for cell in table.cells:
        if not (0 <= cell.row < rows and 0 <= cell.col < cols):
            continue
        text = CELL_LINE_JOIN.join(b.strip() for b in cell.blocks if b.strip())
        grid[cell.row][cell.col] = _escape_cell(text)

    lines = ["| " + " | ".join(grid[0]) + " |",
             "| " + " | ".join(["---"] * cols) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in grid[1:])
    return "\n".join(lines)


def _escape_cell(text: str) -> str:
    """셀 안의 markdown 표 구분자를 무해하게 만든다.

    입력: text — 셀 내용
    출력: `|` 와 줄바꿈이 정리된 문자열
    """
    return text.replace("|", "\\|").replace("\n", CELL_LINE_JOIN)
