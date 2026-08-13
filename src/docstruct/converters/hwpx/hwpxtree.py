"""HWPX(.hwpx/.hwtx) XML 직접 파싱 — pyhwp(AGPL) 대체 후보.

**상태: 검증 완료, 기본 경로 전환은 보류.** 같은 문서에서 pyhwp 와 같은
품질(셀 100%, 표 212/212)을 9배 빠르게 냈다. 다만 HWP → HWPX 변환 수단이
서버에 아직 없어 기본 경로로 올리지 않았다.

역할:
    HWPX 는 OOXML 계열 zip 이라 표준 XML 파서만으로 읽을 수 있다. 표 좌표·
    병합·글자모양이 XML 에 그대로 들어 있어, pyhwp(AGPL) 없이도 같은 품질을
    낼 수 있는지 확인하기 위한 시제품이다.
호출부:
    docstruct.converters.hwpx.converter (검증 후 전환 예정)
입력: .hwpx / .hwtx 파일 경로
출력: markdown 문자열 — hwp5tree.to_markdown 과 같은 형식

왜 python-hwpx 의 markdown 을 쓰지 않는가
--------------------------------------
같은 문서로 재어 보면 그쪽은 표 94개(원본 212), 셀 보존 93.8% 다. 게다가
모든 텍스트에 `~~`(취소선)가 4,456회 씌워진다 — 밑줄 스타일 값이 라이브러리
표에 없어 생기는 문제로, pyhwp 의 `UnderlineStyle 15` 와 같은 뿌리다.
**변환 파일 자체에는 표 212개·셀 5,391개가 온전히 들어 있다.** 손실은 변환이
아니라 내보내기 단계에서 생기므로, XML 을 직접 읽으면 사라진다.

hwp5tree 와 맞춘 규칙
--------------------
지금까지 hwp5tree 에서 잡은 개선을 그대로 옮겼다. 옮기지 않으면 경로를
바꾸는 순간 이미 고친 문제들이 되살아난다.

    · 세로 병합이 이어지는 칸에 `〃`      (0.1.75)
    · 셀 안 끊긴 굵게를 하나로 병합        (0.1.73)
    · 맨 앞의 완전히 빈 행은 헤더로 안 씀   (0.1.72)
    · 필드 상태 직렬화 값 제거             (0.1.78)
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

#: HWPX 문단 네임스페이스.
HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
#: HWPX 머리말(스타일) 네임스페이스.
HH = "http://www.hancom.co.kr/hwpml/2011/head"

#: 쪽 나눔 표식 — hwp5tree 와 같은 값을 쓴다.
PAGE_BREAK = "\n\n---\n\n"
#: 세로 병합이 이어지는 칸의 표식.
MERGE_UP = "〃"
#: 굵게 표기.
BOLD = "**"
#: 열 수 상한 (깨진 문서 방어).
MAX_COLS = 64


def _tag(ns: str, name: str) -> str:
    """네임스페이스가 붙은 태그 이름.

    입력: ns — 네임스페이스 URI, name — 태그 이름
    출력: `{uri}name` 형태 문자열
    """
    return f"{{{ns}}}{name}"


def _is_field_payload(text: str) -> bool:
    """필드 상태 직렬화 값인지 판별한다.

    입력: text — 텍스트 런의 내용
    출력: `{"fields": …,"simplefields": …}` 형태면 True
    비고: hwp5tree 와 같은 규칙. 화면에 보이지 않는 누름틀 잔재다.
    """
    stripped = text.strip()
    return (
        '"simplefields"' in stripped
        and stripped.startswith("{")
        and stripped.endswith("}")
    )


@dataclass
class _Cell:
    """표의 셀 하나."""

    row: int
    col: int
    rowspan: int = 1
    colspan: int = 1
    blocks: list[str] = field(default_factory=list)


@dataclass
class _Table:
    """표 하나."""

    rows: int = 0
    cols: int = 0
    cells: list[_Cell] = field(default_factory=list)


def _bold_char_ids(archive: zipfile.ZipFile) -> set[str]:
    """굵게로 정의된 글자모양 ID 집합.

    입력: archive — 열린 HWPX zip
    출력: charPr id 문자열 집합
    비고:
        header.xml 의 `<hh:charPr>` 중 `<hh:bold/>` 를 가진 것만 모은다.
        문단마다 charPrIDRef 로 이 표를 가리키므로, 이 집합만 있으면
        본문을 훑으며 굵게 여부를 판정할 수 있다.
    """
    try:
        root = ET.fromstring(archive.read("Contents/header.xml"))
    except KeyError:
        return set()
    bold: set[str] = set()
    for char_pr in root.iter(_tag(HH, "charPr")):
        if char_pr.find(_tag(HH, "bold")) is not None:
            ident = char_pr.get("id")
            if ident:
                bold.add(ident)
    return bold


def _run_text(run: ET.Element) -> str:
    """런 하나의 텍스트를 모은다.

    입력: run — `<hp:run>` 요소
    출력: 텍스트 문자열 (탭·줄바꿈 제어문자는 공백으로)
    비고: 표(`<hp:tbl>`) 안쪽은 상위에서 따로 처리하므로 건너뛴다.
    """
    parts: list[str] = []
    for node in run:
        if node.tag == _tag(HP, "t"):
            parts.append("".join(node.itertext()))
        elif node.tag == _tag(HP, "ctrl"):
            continue                             # 필드·컨트롤은 본문이 아니다
        elif node.tag in (_tag(HP, "tab"), _tag(HP, "lineBreak")):
            parts.append(" ")
    return "".join(parts)


def _paragraph_text(para: ET.Element, bold_ids: set[str]) -> str:
    """문단 하나를 markdown 텍스트로 만든다.

    입력: para — `<hp:p>` 요소, bold_ids — 굵게 charPr ID 집합
    출력: 굵게가 반영된 문단 텍스트. 내용이 없으면 빈 문자열
    비고:
        문단 안 런들의 charPr 가 **모두** 굵게일 때만 굵게를 두른다.
        일부만 굵은 문단을 통째로 굵게 하면 hwp5tree 에서 겪은 것과
        같은 왜곡이 생긴다.

        `para.iter()` 는 문단 안에 놓인 표(`<hp:tbl>`)의 런까지 훑는다.
        그러면 표 내용이 본문에도 한 번 더 실려, 같은 문서에서 표 밖
        본문 글자가 29,713 대 72,288 로 부풀었다. 표에 속한 런은 표가
        가져가므로 여기서 제외한다.
    """
    inside_table = {id(run) for tbl in para.iter(_tag(HP, "tbl"))
                    for run in tbl.iter(_tag(HP, "run"))}

    texts: list[str] = []
    all_bold = True
    saw_run = False
    for run in para.iter(_tag(HP, "run")):
        if id(run) in inside_table:
            continue
        text = _run_text(run)
        if not text:
            continue
        if _is_field_payload(text):
            continue
        saw_run = True
        if run.get("charPrIDRef") not in bold_ids:
            all_bold = False
        texts.append(text)

    body = "".join(texts).strip()
    if not body:
        return ""
    return f"{BOLD}{body}{BOLD}" if (saw_run and all_bold) else body


def _join_cell_blocks(blocks: list[str]) -> str:
    """셀 안 문단들을 이어 붙이되 끊긴 굵게를 합친다.

    입력: blocks — 셀 안 문단 목록
    출력: 이어 붙인 셀 텍스트
    비고: hwp5tree._join_cell_blocks 와 같은 규칙 (0.1.73).
    """
    parts = [b.strip() for b in blocks if b.strip()]
    if not parts:
        return ""
    out: list[str] = []
    run: list[str] = []

    def flush() -> None:
        """모아둔 굵게 블록을 한 덩어리로 낸다."""
        if run:
            out.append(BOLD + " ".join(run) + BOLD)
            run.clear()

    for part in parts:
        if len(part) > 2 * len(BOLD) and part.startswith(BOLD) and part.endswith(BOLD):
            run.append(part[len(BOLD):-len(BOLD)])
        else:
            flush()
            out.append(part)
    flush()
    return " ".join(out)


def _escape_cell(text: str) -> str:
    """셀 안의 `|` 를 이스케이프한다.

    입력: text — 셀 텍스트
    출력: 표 구조를 깨지 않는 문자열
    """
    return text.replace("|", "\\|").replace("\n", " ")


def _render_table(table: _Table) -> str:
    """표를 GFM markdown 으로 만든다.

    입력: table — 셀 목록
    출력: markdown 표 문자열
    비고: hwp5tree._render_table 과 같은 규칙 — 병합 표식, 앞쪽 빈 행 제거.
    """
    if not table.cells:
        return ""
    measured_cols = max(c.col + c.colspan for c in table.cells)
    cols = max(1, min(max(table.cols, measured_cols), MAX_COLS))
    rows = max(c.row + c.rowspan for c in table.cells)
    grid = [["" for _ in range(cols)] for _ in range(rows)]

    for cell in table.cells:
        if not (0 <= cell.row < rows and 0 <= cell.col < cols):
            continue
        grid[cell.row][cell.col] = _escape_cell(_join_cell_blocks(cell.blocks))
        if cell.rowspan > 1:
            for r in range(cell.row + 1, min(cell.row + cell.rowspan, rows)):
                grid[r][cell.col] = MERGE_UP

    while len(grid) > 1 and not any(c.strip() for c in grid[0]):
        grid.pop(0)

    lines = ["| " + " | ".join(grid[0]) + " |",
             "| " + " | ".join(["---"] * cols) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in grid[1:])
    return "\n".join(lines)


def _read_table(element: ET.Element, bold_ids: set[str]) -> _Table:
    """`<hp:tbl>` 을 _Table 로 읽는다.

    입력: element — `<hp:tbl>`, bold_ids — 굵게 charPr ID
    출력: _Table
    비고:
        중첩 표는 바깥 표의 셀 텍스트로 접어 넣는다. hwp5tree 는 별도
        블록으로 빼는데, 그 차이는 검증 후 맞춘다.
    """
    table = _Table(
        rows=int(element.get("rowCnt") or 0),
        cols=int(element.get("colCnt") or 0),
    )
    for tc in element.iter(_tag(HP, "tc")):
        addr = tc.find(_tag(HP, "cellAddr"))
        span = tc.find(_tag(HP, "cellSpan"))
        cell = _Cell(
            row=int(addr.get("rowAddr", 0)) if addr is not None else 0,
            col=int(addr.get("colAddr", 0)) if addr is not None else 0,
            rowspan=int(span.get("rowSpan", 1)) if span is not None else 1,
            colspan=int(span.get("colSpan", 1)) if span is not None else 1,
        )
        for para in tc.iter(_tag(HP, "p")):
            text = _paragraph_text(para, bold_ids)
            if text:
                cell.blocks.append(text)
        table.cells.append(cell)
    return table


def to_markdown(path: str | Path) -> str:
    """HWPX 를 markdown 으로 변환한다.

    입력: path — .hwpx / .hwtx 경로
    출력: markdown 문자열
    동작:
        섹션 XML 을 순서대로 훑으며 문단과 표를 문서 순서로 낸다.
        표 안쪽 문단은 표가 가져가므로 본문에서 제외한다.
    """
    archive = zipfile.ZipFile(str(path))
    try:
        bold_ids = _bold_char_ids(archive)
        section_names = sorted(
            (n for n in archive.namelist()
             if re.fullmatch(r"Contents/section\d+\.xml", n)),
            key=lambda n: int(re.search(r"\d+", n.split("/")[1]).group()),
        )

        blocks: list[str] = []
        for index, name in enumerate(section_names):
            if index:
                blocks.append(PAGE_BREAK.strip())
            root = ET.fromstring(archive.read(name))
            blocks.extend(_walk(root, bold_ids))
        return "\n\n".join(blocks)
    finally:
        archive.close()


def _walk(node: ET.Element, bold_ids: set[str]) -> list[str]:
    """요소를 문서 순서로 훑어 블록 목록을 만든다.

    입력: node — 순회할 요소, bold_ids — 굵게 charPr ID
    출력: markdown 블록 목록
    비고:
        `root.iter()` 로 평평하게 훑으면 표 안쪽 문단이 표와 **따로 한 번
        더** 나온다. 실제로 그렇게 짰다가 글자 수가 121,389 대 70,951 로
        부풀었다. 표를 만나면 그 서브트리는 표가 통째로 가져가고 재귀를
        멈춰야 한다.
    """
    out: list[str] = []
    for child in node:
        if child.tag == _tag(HP, "tbl"):
            rendered = _render_table(_read_table(child, bold_ids))
            if rendered:
                out.append(rendered)
            continue                             # 서브트리는 표가 가져갔다
        if child.tag == _tag(HP, "p"):
            text = _paragraph_text(child, bold_ids)
            if text:
                out.append(text)
            # 문단 안에 표가 놓일 수 있다 (hwp5html 과 같은 구조).
            for tbl in child.iter(_tag(HP, "tbl")):
                rendered = _render_table(_read_table(tbl, bold_ids))
                if rendered:
                    out.append(rendered)
            continue
        out.extend(_walk(child, bold_ids))
    return out
