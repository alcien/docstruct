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
from collections.abc import Iterator
from contextlib import contextmanager
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

    # 계수기는 파일 열기가 실패해도 반드시 떨어져야 한다. Hwp5File() 자체가
    # 예외를 던지는 경우(파일 없음·형식 오류)가 흔하므로 그것까지 감싼다.
    with _quiet_warnings() as counter:
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
                except Exception as exc:         # noqa: BLE001 - 섹션 하나가 깨져도 나머지는 살린다
                    _log.warning("%s번 섹션을 읽지 못했습니다: %s", index, exc)
                    continue
                if parts:
                    parts.append(PAGE_BREAK)     # 구역이 바뀌면 새 쪽으로 본다
                parts.extend(_read_section(section, ENDEVENT, styles, nested_no))
        finally:
            # 열린 핸들을 남기면 Windows 에서 원본 삭제·이동이 막히고,
            # 오래 사는 서버 프로세스에서는 핸들이 누적된다.
            _close_quietly(hwp)
        summary = counter.summary()
        if summary:
            _log.info(
                "pyhwp 반복 경고 요약 — %s "
                "(본문·표에는 영향 없음. 원문을 보려면 %s=true)",
                summary, _VERBOSE_ENV,
            )
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


#: 문서마다 대량으로 반복되는 pyhwp 경고들. 여기 걸리는 것만 모아서 세고,
#: 나머지 경고는 **그대로 통과시킨다** — 모르는 경고를 삼키면 진짜 문제가
#: 조용히 묻힌다.
#:
#:   unmatched field end     hwp5.xmlmodel — 필드(누름틀·쪽번호) 짝이 안 맞음.
#:                           짝 없는 종료 이벤트를 버리고 진행하며 본문 손실은
#:                           없다 (xmlmodel.mfse_field_end).
#:   undefined … value       hwp5.dataio  — 비트필드 값이 pyhwp 의 Enum 표에
#:                           없음. `int.__new__` 로 원시 정수를 그대로 돌려주고
#:                           예외를 내지 않는다 (dataio.py:319).
#:
#: 후자의 대표 사례가 `UnderlineStyle value: 15` 다. CharShape 비트필드에서
#: underline_style 은 **4~7비트(4비트, 0~15)** 인데 pyhwp 의 표는 0~10 만
#: 정의한다. 즉 문서가 깨진 게 아니라 **pyhwp 표가 비어 있는 것**이다.
#: 우리가 읽는 bold(1비트)·italic(0비트)은 별개 비트라 영향을 받지 않는다.
_NOISY_PATTERNS = ("unmatched field end", "undefined ", "defined name/values")

#: 계수기를 붙일 로거들.
_NOISY_LOGGERS = ("hwp5.xmlmodel", "hwp5.dataio")

#: 원문 경고를 그대로 보고 싶을 때 켠다 (`DOCSTRUCT_PYHWP_VERBOSE=true`).
#: pyhwp 가 특정 문서에서 무엇을 못 읽는지 직접 확인해야 할 때 쓴다.
_VERBOSE_ENV = "DOCSTRUCT_PYHWP_VERBOSE"


def _verbose_pyhwp() -> bool:
    """pyhwp 원문 경고를 그대로 흘릴지.

    입력: 없음 (`DOCSTRUCT_PYHWP_VERBOSE`)
    출력: 켜져 있으면 True (계수기를 달지 않는다)
    """
    import os

    return os.environ.get(_VERBOSE_ENV, "").strip().lower() in ("1", "true", "on", "yes")


class _NoiseCounter(logging.Filter):
    """되풀이되는 pyhwp 경고를 종류별로 세고 출력은 막는다.

    입력(필드): counts — 요약 문구 → 횟수
    출력: filter() 가 False 를 돌려 해당 레코드를 버린다
    비고:
        통째로 가리지 않는다. `_NOISY_PATTERNS` 에 걸리는 것만 삼키고
        나머지는 통과시킨다. 삼킨 것도 버리지 않고 종류별로 세어,
        호출부가 문서당 한 줄로 요약한다.

        `defined name/values:` 로 시작하는 줄은 앞선 `undefined …` 경고에
        딸린 Enum 표 덤프다. 한 번에 열 줄이 넘어가므로 세지 않고 버린다
        — 같은 정보가 앞 줄에 이미 요약돼 있다.
    """

    def __init__(self) -> None:
        super().__init__()
        self.counts: dict[str, int] = {}

    @property
    def total(self) -> int:
        """삼킨 경고의 총 횟수.

        입력: 없음
        출력: counts 값의 합
        """
        return sum(self.counts.values())

    def filter(self, record: logging.LogRecord) -> bool:
        """레코드를 통과시킬지 정한다.

        입력: record — 로그 레코드
        출력: 되풀이 경고면 False (버림), 그 밖에는 True
        """
        message = str(record.getMessage())
        lowered = message.lower()
        if not any(pattern in lowered for pattern in _NOISY_PATTERNS):
            return True                      # 모르는 경고는 그대로 보여 준다
        if lowered.startswith("defined name/values"):
            return False                     # 앞 줄에 딸린 표 덤프 — 세지 않는다
        key = message[:80]
        self.counts[key] = self.counts.get(key, 0) + 1
        return False

    def summary(self) -> str:
        """문서당 한 줄 요약.

        입력: 없음
        출력: `필드 짝 경고 47건 · UnderlineStyle 15 1,168건` 형태.
              삼킨 것이 없으면 빈 문자열
        """
        if not self.counts:
            return ""
        parts = [
            f"{key} {count:,}건"
            for key, count in sorted(self.counts.items(), key=lambda kv: -kv[1])
        ]
        return " · ".join(parts)


@contextmanager
def _quiet_warnings() -> "Iterator[_NoiseCounter]":
    """되풀이 경고 계수기를 붙였다가 반드시 뗀다.

    입력: 없음
    출력: _NoiseCounter (with 블록 안에서 counts 를 읽는다)
    비고:
        떼는 일을 호출부의 finally 에 맡겼더니, 파일 열기가 먼저 실패하는
        경로에서 계수기가 로거에 남았다. 배치로 손상 파일을 계속 만나면
        필터가 무한히 쌓인다. 붙이고 떼는 짝을 여기서 묶는다.

        `DOCSTRUCT_PYHWP_VERBOSE=true` 면 아무것도 달지 않아 pyhwp 경고가
        원문 그대로 나온다.
    """
    for name in ("hwp5.bintype", "hwp5.binmodel", "hwp5.filestructure"):
        logging.getLogger(name).setLevel(logging.WARNING)

    counter = _NoiseCounter()
    if _verbose_pyhwp():
        yield counter                        # 계수기를 달지 않는다 — 원문 그대로
        return

    loggers = [logging.getLogger(name) for name in _NOISY_LOGGERS]
    for logger in loggers:
        logger.addFilter(counter)
    try:
        yield counter
    finally:
        for logger in loggers:
            logger.removeFilter(counter)


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
            text = attrs.get("text") or ""
            if _is_field_payload(text):
                continue                     # 필드 상태 직렬화 — 본문이 아니다
            para.append(text)
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


#: 한글이 누름틀(FieldClickHere) 상태를 직렬화해 Text 런에 넣어 두는 값.
#: 화면·인쇄물에는 보이지 않지만 텍스트 레이어에는 남아, HWP 로 읽든
#: PDF 로 내보내든 본문에 섞여 나온다. 실제 성과계획서에서 문서당 2회쯤
#: 발견됐고 값은 늘 같았다.
#:
#: **필드 자체를 버리면 안 된다.** 이 문서에서 FieldClickHere 는 5,306회
#: 쓰였고 그 안에 `기획예산처`·`전략목표`·`83` 같은 진짜 본문이 들어 있다.
#: 걸러낼 것은 필드가 아니라 이 직렬화 값 하나뿐이다.
_FIELD_PAYLOAD_MARK = '"simplefields"'


def _is_field_payload(text: str) -> bool:
    """필드 상태 직렬화 값인지 판별한다.

    입력: text — Text 런의 내용
    출력: 필드 상태 JSON 이면 True
    비고:
        `{"fields": {},"simplefields": {}}` 형태만 노린다. 넓게 잡으면
        본문에 나오는 정상적인 JSON 예시까지 지운다 — 정부 문서에도
        코드 조각이 실릴 수 있다. 양끝이 중괄호이고 이 표식을 가진
        경우로 한정한다.
    """
    stripped = text.strip()
    return (
        _FIELD_PAYLOAD_MARK in stripped
        and stripped.startswith("{")
        and stripped.endswith("}")
    )


#: 세로 병합이 아래로 이어지는 칸에 넣는 표식.
#: 빈 칸으로 두면 "그 행에는 값이 없다" 로 읽혀, 위 행 하나에만 값이
#: 귀속된다. 원본에서 두 행이 공유하던 값이라면 사실과 달라진다.
MERGE_UP = "〃"

#: 병합 표식을 넣을지. 끄면 예전처럼 빈 칸으로 둔다.
MERGE_MARK_ENV = "DOCSTRUCT_TABLE_MERGE_MARK"


def _merge_marks_enabled() -> bool:
    """세로 병합 표식을 넣을지.

    입력: 없음 (`DOCSTRUCT_TABLE_MERGE_MARK`, 기본 켜짐)
    출력: 켜져 있으면 True
    비고:
        기존 결과와 비교해야 할 때 끌 수 있게 해 둔다. 표식이 들어가면
        markdown 문자열이 달라지므로, 예전 산출물과 대조하는 검증에는
        꺼야 할 수 있다.
    """
    import os

    return os.environ.get(MERGE_MARK_ENV, "").strip().lower() not in ("0", "false", "off", "no")


def _render_table(table: _Table) -> str:
    """표를 GFM markdown 으로 만든다.

    입력: table — 셀 목록과 열 수
    출력: markdown 표 문자열. 셀이 없으면 빈 문자열
    비고:
        GFM 은 병합을 표현하지 못한다. 값을 복제하면 같은 내용이 검색에
        여러 번 걸리고, 빈 칸으로 두면 **값이 맨 윗행만의 것으로 읽힌다** —
        실제 문서에서 `페이스북+인스타그램 합계 15.7만` 이 `페이스북 단독
        15.7만` 으로 잘못 읽혔다. 그래서 세로 병합이 이어지는 칸에는
        `〃` 표식을 남겨 "위 값과 같은 칸" 임을 드러낸다.

        가로 병합은 왼쪽 칸에만 값을 넣고 나머지를 비운다. 제목 행처럼
        한 값이 여러 열을 덮는 경우가 대부분이라 오해의 소지가 적다.
    """
    if not table.cells:
        return ""
    # TableBody 가 선언한 열 수와 셀 좌표로 실측한 열 수 중 **큰 쪽**을 쓴다.
    # 선언값이 실제보다 작은 문서가 있는데, 선언값만 믿으면 범위 밖 셀이
    # 아래 경계 검사에서 조용히 버려진다.
    measured = max(c.col + c.colspan for c in table.cells)
    cols = max(table.cols, measured) if table.cols else measured
    cols = max(1, min(cols, MAX_COLS))
    rows = max(c.row + c.rowspan for c in table.cells)
    grid = [["" for _ in range(cols)] for _ in range(rows)]

    mark_merges = _merge_marks_enabled()
    for cell in table.cells:
        if not (0 <= cell.row < rows and 0 <= cell.col < cols):
            continue
        grid[cell.row][cell.col] = _escape_cell(_join_cell_blocks(cell.blocks))
        if not mark_merges or cell.rowspan <= 1:
            continue
        # 세로로 이어지는 칸에 표식을 남긴다 (맨 윗행은 값 그대로).
        for row in range(cell.row + 1, min(cell.row + cell.rowspan, rows)):
            grid[row][cell.col] = MERGE_UP

    # 맨 앞의 **완전히 빈 행**은 헤더로 쓰지 않는다. 정부 HWP 문서는 표
    # 위쪽에 여백용 빈 행을 두는 일이 흔한데, 그것이 GFM 헤더가 되면
    # `|||||||||` 같은 빈 머리행이 나와 표의 의미가 사라진다. 값이 없는
    # 행이므로 버려도 잃는 정보가 없다 — 값이 하나라도 있으면 남긴다.
    while len(grid) > 1 and not any(c.strip() for c in grid[0]):
        grid.pop(0)

    lines = ["| " + " | ".join(grid[0]) + " |",
             "| " + " | ".join(["---"] * cols) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in grid[1:])
    return "\n".join(lines)


#: 굵게 표시. styling.format_paragraph 가 문단 전체에 두르는 기호다.
_BOLD = "**"


def _is_bold_block(block: str) -> bool:
    """이 블록이 **우리가** 두른 굵게인지 판별한다.

    입력: block — 셀 안의 문단 하나
    출력: 양끝이 `**` 로 감싸였고 안쪽에 내용이 있으면 True
    비고:
        원문에 들어 있던 별표와 구분하기 위한 검사다. 정부 문서에는
        `96.25*0.4` 같은 계산식이나 `* 주:` 각주가 흔하다. 문자열 전체를
        정규식으로 훑으면 그런 별표까지 건드려, 셀이 통째로 사라지는
        일이 생긴다 — 실제로 `*****` 만 담긴 셀이 빈 칸이 됐다.
        블록 단위로 양끝만 보면 원문 별표는 손대지 않는다.
    """
    return (
        len(block) > 2 * len(_BOLD)
        and block.startswith(_BOLD)
        and block.endswith(_BOLD)
    )


def _join_cell_blocks(blocks: list[str]) -> str:
    """셀 안의 문단들을 이어 붙이되, 끊긴 굵게를 하나로 합친다.

    입력: blocks — 셀 안의 문단 목록
    출력: 이어 붙인 셀 텍스트
    비고:
        좁은 칸에서 작성자가 Enter 로 줄을 나누면 문단마다 굵게가 걸려
        `**프로그램목표Ⅰ-1** **의정활동의 …**` 가 된다. 보기 나쁜 데서
        그치지 않는다 — 셀 중간에 낀 `**` 가 문자열 매칭을 깨뜨려,
        원본 대조 검증에서 멀쩡한 셀 75개가 유실로 오판됐다. RAG 색인이나
        LLM 판정도 같은 이유로 이 셀들을 잘못 읽는다.

        **글자와 공백은 건드리지 않는다.** `년 도` 를 `년도` 로 붙일지는
        문서마다 답이 달라(`성과관리대상 사업` 은 띄어야 맞다) 추측하면
        안 되고, 여기서 고치려는 것은 기호뿐이다.
    """
    parts = [b.strip() for b in blocks if b.strip()]
    if not parts:
        return ""

    out: list[str] = []
    run: list[str] = []                          # 연속된 굵게 블록의 알맹이

    def flush() -> None:
        """모아둔 굵게 블록을 한 덩어리로 내보낸다."""
        if run:
            out.append(_BOLD + CELL_LINE_JOIN.join(run) + _BOLD)
            run.clear()

    for part in parts:
        if _is_bold_block(part):
            run.append(part[len(_BOLD):-len(_BOLD)])
        else:
            flush()
            out.append(part)
    flush()
    return CELL_LINE_JOIN.join(out)


def _escape_cell(text: str) -> str:
    """셀 안의 markdown 표 구분자를 무해하게 만든다.

    입력: text — 셀 내용
    출력: `|` 와 줄바꿈이 정리된 문자열
    """
    return text.replace("|", "\\|").replace("\n", CELL_LINE_JOIN)
