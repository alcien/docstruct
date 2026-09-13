"""HWPX(.hwpx/.hwtx) XML 직접 파싱 — pyhwp(AGPL) 대체 후보.

**상태: 검증 완료, 기본 경로 전환은 보류.** 같은 문서에서 pyhwp 와 같은
품질(셀 100%, 표 212/212)을 9배 빠르게 냈다. 다만 HWP → HWPX 변환 수단이
서버에 아직 없어 기본 경로로 올리지 않았다.

역할:
    HWPX 는 OOXML 계열 zip 이라 표준 XML 파서만으로 읽을 수 있다. 표 좌표·
    병합·글자모양이 XML 에 그대로 들어 있어, pyhwp(AGPL) 없이도 같은 품질을
    낼 수 있는지 확인하기 위한 시제품이다.
호출부:
    converters.hwpx.converter (검증 후 전환 예정)
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
import threading
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

from docstruct.converters.common.table import merge_continuation
from docstruct.converters.hwp.styling import format_body_text

#: HWPX 문단 네임스페이스.
# ═══ 구간 1 — 네임스페이스·상수 ════════════════════════════════════════════════════
# HWPX XML 네임스페이스와 렌더 상수(MERGE_UP·MAX_COLS·IMAGE_MARK).
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
#: HWPX 그림(hc:img) 네임스페이스.
HC = "http://www.hancom.co.kr/hwpml/2011/core"
#: 본문에 남기는 그림 표식. 추출기가 이것을 `<image N>` 로 바꾸고
#: 나중에 VLM 이 읽은 글로 펼친다 — **경로도 바이너리도 본문에 넣지
#: 않는다.** 사람이 읽는 산출에는 복원된 글귀만 남아야 한다.
IMAGE_MARK = "<!-- hwpx-image:{ref} -->"


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
    #: 이 칸에서 걸러낸 숨은 글 — [{"why","text"}] (0.4.75).
    hidden: list[dict] = field(default_factory=list)


@dataclass
class _Table:
    """표 하나."""

    rows: int = 0
    cols: int = 0
    cells: list[_Cell] = field(default_factory=list)


# ═══ 구간 2 — 글자 모양 판정 (header.xml) ════════════════════════════════════════
# 굵은 글자 id · 숨은 글자 id(_hidden_char_ids: 1pt·흰색·누름틀) — 0.4.74/0.4.82.
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


#: 이 크기(1/100pt) 이하면 눈에 보이라고 넣은 글자가 아니다.
#: 실측(조달청): `height` 가 **100(=1pt)** 인 글자모양이 17개 있었고,
#: 목차 전문과 프로그램 코드(`53405`)가 거기 실려 있었다. 본문 글자는
#: 900~3100(9~31pt)이라 300 이면 넉넉한 경계다.
HIDDEN_HEIGHT = 300

#: 바탕이 흰 문서에서 흰 글자는 보이지 않는다 — **다만 작을 때만 그렇다.**
HIDDEN_COLORS = frozenset({"#FFFFFF", "#FFFFFE", "#FEFEFE"})

#: 흰 글자라도 이 크기(1/100pt) 이상이면 **숨긴 것이 아니다.**
#:
#: 실측(해양경찰청 0.4.82): `별첨1`~`별첨8` 이 흰 글자 16pt 로 되어 있는데
#: **진한 바탕 위에 얹힌 제목 번호**다. 지면에서는 또렷이 보인다. 0.4.74
#: 에서 색만 보고 지웠더니 여덟 개가 통째로 사라졌다.
#:
#:     charPr 978  height=1600  textColor=#FFFFFF   ← 별첨8 · 보이는 글
#:     charPr  …   height= 100  textColor=#FFFFFF   ← 목차 · 숨긴 글
#:
#: 진짜 숨긴 흰 글자는 1pt 로도 함께 작다. 크기를 함께 보면 갈린다.
#: 바탕색은 `shadeColor` 가 `none` 이라 글자만으로는 알 수 없어, **크기를
#: 대리 신호로 쓴다.**
VISIBLE_WHITE_HEIGHT = 900


def _hidden_char_ids(archive: zipfile.ZipFile) -> dict[str, str]:
    """**눈에 보이지 않게 설정된** 글자모양 ID → 사유.

    입력: archive — 열린 HWPX zip
    출력: {charPr id: "tiny" | "white" | "tiny+white"}
    비고:
        한글 문서는 지면에 보이지 않는 글자를 자주 담는다. 실측(조달청
        성과계획서)에서 두 가지가 나왔다.

            1pt 글자   목차 전문과 프로그램 코드가 `height=100` 으로 숨어
                       있었다. 표 안에서는 **좁은 열 하나**를 차지한다
            흰 글자    `textColor="#FFFFFF"` — `53405` 는 **둘 다**였다

        이것을 그냥 본문으로 실으면 없는 글이 생기고, 그냥 버리면 **왜
        HWPX 와 PDF 의 열 수가 다른지** 알 수 없게 된다. 실제로 `53405`
        같은 코드가 HWPX 에서는 제 칸을 차지하는데 PDF 에서는 보이지
        않으니, 쪽 맞춤이 어긋나도 원인을 짚을 수 없었다.

        그래서 **가려내되 기록한다** — 본문에서는 빼고, 무엇이 어디에
        숨어 있었는지 표에 남긴다.
    """
    try:
        root = ET.fromstring(archive.read("Contents/header.xml"))
    except KeyError:
        return {}
    hidden: dict[str, str] = {}
    for char_pr in root.iter(_tag(HH, "charPr")):
        ident = char_pr.get("id")
        if not ident:
            continue
        marks = []
        try:
            height = int(char_pr.get("height") or 0)
        except ValueError:
            height = 0
        if 0 < height <= HIDDEN_HEIGHT:
            marks.append("tiny")
        if (char_pr.get("textColor") or "").upper() in HIDDEN_COLORS:
            # **큰 흰 글자는 숨긴 것이 아니다.** 진한 바탕 위에 얹힌
            # 제목이 그렇다 (실측: `별첨8` 이 흰 글자 16pt).
            if height >= VISIBLE_WHITE_HEIGHT:
                continue
            marks.append("white")
        if marks:
            hidden[ident] = "+".join(marks)
    return hidden


#: **변환 한 번의 수집기** (0.4.87 — 스레드마다 따로 둔다).
#:
#: 문단·표를 걷는 함수들은 값을 돌려줄 자리가 없어 여기에 쌓는다. 예전에는
#: 이것이 **모듈 전역**이라 서버에서 HWPX 두 건이 스레드로 동시에 돌면
#: 요약이 섞였다. 실측(조달청 성과계획서, 스레드 4개 동시):
#:
#:     단독   anchored  90 · inline  29 · tiny+white 13
#:     동시   anchored 329 · inline 106 · tiny+white 52   ← 남의 문서까지 센 값
#:
#: 이것이 `DOCSTRUCT_CONVERT_CONCURRENCY` 를 1 로 묶어 둔 진짜 이유였다.
#: `threading.local` 로 옮겨 스레드마다 자기 것만 보게 했다.
@dataclass
class _Collectors:
    """변환 한 번이 쌓는 것 — 표 앵커 · 숨은 글 · 지금 담고 있는 셀."""

    #: 본 표 앵커 — `{"inline": n, "anchored": n, "reordered": n}`.
    anchors: dict[str, int] = field(default_factory=dict)
    #: 걸러낸 숨은 글 — `{사유: [글]}`.
    hidden: dict[str, list[str]] = field(default_factory=dict)
    #: **지금 글을 모으고 있는 셀** (0.4.75). 표를 읽는 동안만 채워진다.
    cell: list | None = None


_state = threading.local()


def _collectors() -> _Collectors:
    """이 스레드의 수집기를 얻는다 (없으면 만든다).

    입력: 없음
    출력: _Collectors
    """
    got = getattr(_state, "box", None)
    if got is None:
        got = _Collectors()
        _state.box = got
    return got


# ═══ 구간 3 — 표 앵커·수집기 ═════════════════════════════════════════════════════
# 표가 매달린 것인지 글자 취급인지(_table_anchor). hidden_notes/anchor_notes 는 모듈 전역 수집기 — 동시 변환 미지원.
def _table_anchor(tbl: ET.Element) -> str:
    """이 표가 **글자처럼 놓였는지** 본다.

    입력: tbl — `<hp:tbl>` 요소
    출력: "inline"(글자 취급) · "anchored"(문단에 매달림) · "unknown"
    비고:
        `<hp:pos treatAsChar>` 가 1 이면 표가 글 흐름 안에 있어 XML 순서가
        곧 지면 순서다. 0 이면 **문단에 매달린 객체**라 지면에서는
        `vertOffset` 만큼 떨어져 그려지는데, XML 로는 그 문단의 앞쪽에
        앉아 있다.

        그래서 문서 순서로 훑으면 **표가 제 캡션보다 먼저 나온다.**
        실측(해양경찰청 223표): `treatAsChar=0` 이 **159개(71%)** 이고,
        표가 문단의 첫 run 인 경우가 142개다. 결과물에서
        `</table 166>` 다음에 `프로그램 내 사업 우선순위 조정 관련 주요
        내용` 이 오는 것이 이것이다.

        `treatAsChar=1` 인 64개는 진짜 글자 취급이라 지금 순서가 맞다 —
        **섞어서 뒤집으면 안 된다.**
    """
    pos = next((c for c in tbl if c.tag == _tag(HP, "pos")), None)
    if pos is None:
        return "unknown"
    return "inline" if pos.get("treatAsChar") == "1" else "anchored"


def anchor_notes() -> dict[str, int]:
    """직전 변환에서 본 표 앵커 분포.

    입력: 없음
    출력: {"inline","anchored","unknown","reordered"} · 없으면 빈 dict
    비고:
        `reordered` 는 **순서를 바로잡은 표 수**다. 매달린 표가 문단의
        첫 자리에 있으면 그 문단의 글이 지면에서는 표보다 위에 오므로,
        글을 먼저 내보낸다.
    """
    return {k: v for k, v in _collectors().anchors.items() if v}


def hidden_notes() -> dict[str, dict]:
    """직전 변환에서 걸러낸 숨은 글의 요약.

    입력: 없음 (모듈 안의 수집기를 읽는다)
    출력: {사유: {"count": 개수, "samples": 앞 10개}} · 없으면 빈 dict
    비고:
        `to_markdown` 이 시작할 때 수집기를 비우므로 **직전 한 번의
        변환**만 담는다. 판독 계층이 이것을 읽어 결과물에 싣는다.

        왜 내는가 — 실측(조달청 성과계획서)에서 프로그램 코드 `53405` 가
        **1pt 이면서 흰 글씨**로 숨어 있었다. 표에서는 좁은 열 하나를
        차지하는데 PDF 로 뽑으면 보이지 않으니, HWPX 와 PDF 의 열 수가
        달라져 쪽 맞춤이 어긋난다. 그 사실이 결과물에 없으면 원인을
        짚을 길이 없다.
    """
    return {why: {"count": len(items), "samples": items[:10]}
            for why, items in _collectors().hidden.items() if items}


# ═══ 구간 4 — 문단 텍스트 ═══════════════════════════════════════════════════════
# run → 문단 텍스트. 숨은 글은 빼고 기록(_note_hidden), 균등배분·PUA 는 text/korean_text.
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


#: 왜 셀 단위까지 남기나 — 문서 단위 요약만으로는 **어느 칸이 비었는지** 알 수
#: 없다. 쪽 맞춤이 "이 표의 이 열은 HWPX 에만 있다" 를 판단하려면 자리가
#: 필요하다. 실측(조달청 70쪽): `53405` 는 지표명 왼쪽 좁은 열에 있었다.
#: (수집기 자체는 위 `_Collectors` — 스레드마다 따로다.)


def _note_hidden(why: str, text: str) -> None:
    """숨은 글 하나를 기록한다 — 문서 단위와 **셀 단위** 양쪽에.

    입력: why — 사유, text — 걸러낸 글
    출력: 없음
    비고:
        셀 안에서 걸린 것은 그 셀에도 담는다(`_Collectors.cell`). 표 밖(본문·목차)
        에서 걸린 것은 문서 단위에만 남는다.
    """
    if not text:
        return
    box = _collectors()
    box.hidden.setdefault(why, []).append(text)
    if box.cell is not None:
        box.cell.append({"why": why, "text": text})


def _paragraph_text(para: ET.Element, bold_ids: set[str],
                    hidden_ids: dict[str, str] | None = None) -> str:
    """문단 하나를 markdown 텍스트로 만든다.

    입력: para — `<hp:p>` 요소, bold_ids — 굵게 charPr ID 집합,
          hidden_ids — 보이지 않는 charPr ID → 사유
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

    # 글상자(`<hp:drawText>`)마다 제 런을 갖는다. 서로 다른 상자의 글은
    # 지면에서 떨어져 있는데(간지의 큰 제목 상자와 `제N장` 상자),
    # `para.iter()` 가 이들을 한 줄로 이어 붙여 `성과계획 목표체계제1장`
    # 이 됐다 — 실측(행안부 성과계획서)에서 간지 3곳 전부. 상자 경계에서
    # 줄을 바꾼다. 같은 상자 안, 그리고 문단 자신의 런들은 지금처럼
    # 그대로 붙인다 (글자모양이 갈리면 한 문장이 여러 런으로 쪼개진다).
    box_of: dict[int, int] = {}
    for box_index, draw in enumerate(para.iter(_tag(HP, "drawText"))):
        for run in draw.iter(_tag(HP, "run")):
            box_of.setdefault(id(run), box_index)

    texts: list[str] = []
    all_bold = True
    saw_run = False
    current_box: int | None = None
    for run in para.iter(_tag(HP, "run")):
        if id(run) in inside_table:
            continue
        text = _run_text(run)
        if not text:
            continue
        if _is_field_payload(text):
            # 누름틀 잔재 — 화면에 없던 글이다. 기록만 남긴다.
            _note_hidden("field", text.strip())
            continue
        why = (hidden_ids or {}).get(run.get("charPrIDRef"))
        if why:
            # **1pt·흰 글자는 본문에서 뺀다.** 지면에 보이지 않던 글을
            # 본문으로 실으면 없는 글이 생긴다. 다만 표의 열을 차지하므로
            # 사라졌다는 사실은 남겨야 한다 (0.4.74).
            _note_hidden(why, text.strip())
            continue
        box = box_of.get(id(run), -1)
        if texts and box != current_box:
            texts.append("\n")
        current_box = box
        saw_run = True
        if run.get("charPrIDRef") not in bold_ids:
            all_bold = False
        texts.append(text)

    body = "\n".join(part.strip() for part in "".join(texts).split("\n"))
    body = body.strip()
    if not body:
        return ""
    if saw_run and all_bold:
        # 줄이 갈린 굵게는 줄마다 두른다 — 개행을 가로지르는 `**` 는
        # markdown 에서 굵게로 렌더되지 않는다.
        return "\n".join(f"{BOLD}{line}{BOLD}" if line else line
                         for line in body.split("\n"))
    return body


# ═══ 구간 5 — 표 셀·세로 병합 접기 ═════════════════════════════════════════════════
# 셀 안 블록 잇기 · 세로로 한 글자씩 놓인 칸 접기(collapse_vertical_cells) · 셀 이스케이프.
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


#: 세로 배치로 볼 최소 연속 행 수. 둘은 우연일 수 있다.
MIN_VERTICAL_RUN = 3
#: 한 글자로 셀 문자 (한글·숫자·라틴). 기호는 세지 않는다 — `○`·`〃` 가
#: 세로로 이어지는 것은 세로쓰기가 아니라 값이다.
_ONE_CHAR_RE = re.compile(r"^[가-힣0-9A-Za-z]$")


def _bare(text: str | None) -> str:
    """굵게 표기를 벗긴 셀 글자."""
    body = (text or "").strip()
    if len(body) > 2 * len(BOLD) and body.startswith(BOLD) and body.endswith(BOLD):
        return body[len(BOLD):-len(BOLD)].strip()
    return body


def collapse_vertical_cells(cells: list[dict]) -> int:
    """세로로 한 글자씩 나뉜 칸을 한 칸으로 합친다.

    입력: cells — 표 셀 목록 (제자리 갱신)
    출력: 합친 무리 수
    비고:
        원본이 "2027" 을 세로로 배치하려고 **표 칸마다 한 글자씩** 넣는
        일이 있다. 그대로 두면 markdown 이 이렇게 나온다:

            |  | **2** |  |
            |  | **0** |  |
            |  | **2** |  |
            |  | **7** |  |

        `korean_text.collapse_vertical_text` 가 같은 일을 하지만 **평문
        줄**을 본다. 여기서는 글자가 이미 표 셀 안에 들어가 있어 그 규칙이
        닿지 않는다 — 표를 만들기 전, 셀 단계에서 합쳐야 한다.

        **판별은 이웃 열이 정한다.** "한 글자가 세로로 3칸 이상" 만으로는
        정상 데이터를 부순다 — 실측(조달청): 그 조건에 걸리는 무리가
        10개인데 전부 `0000`·`1111` 같은 값 열이었다. 세로 배치는 그
        열만 쓰므로 **같은 행의 다른 열이 모두 비어 있다.** 이 조건을
        더하면 조달청에서 발화 1건(표지 "2027"), 오탐 0 이다.
    """
    if not cells:
        return 0
    rows = max(c["row"] + c["rowspan"] for c in cells)
    cols = max(c["col"] + c["colspan"] for c in cells)
    at = {(c["row"], c["col"]): c for c in cells}

    def _single(row: int, col: int) -> dict | None:
        """그 자리가 세로 배치 후보인지 — 아니면 None."""
        cell = at.get((row, col))
        if not cell or cell["rowspan"] != 1 or cell["colspan"] != 1:
            return None
        if not _ONE_CHAR_RE.match(_bare(cell.get("text"))):
            return None
        for other in range(cols):
            if other == col:
                continue
            neighbour = at.get((row, other))
            if neighbour and (neighbour.get("text") or "").strip():
                return None                      # 옆에 값이 있다 — 값 열이다
        return cell

    merged = 0
    drop: list[dict] = []
    for col in range(cols):
        run: list[dict] = []

        def flush() -> None:
            """모은 칸을 첫 칸에 합친다."""
            nonlocal merged
            if len(run) < MIN_VERTICAL_RUN:
                run.clear()
                return
            bold = all(( c.get("text") or "").strip().startswith(BOLD) for c in run)
            joined = "".join(_bare(c.get("text")) for c in run)
            run[0]["text"] = f"{BOLD}{joined}{BOLD}" if bold else joined
            run[0]["rowspan"] = len(run)
            drop.extend(run[1:])
            merged += 1
            run.clear()

        for row in range(rows):
            cell = _single(row, col)
            if cell is None:
                flush()
            else:
                run.append(cell)
        flush()

    if drop:
        keep = {id(c) for c in drop}
        cells[:] = [c for c in cells if id(c) not in keep]
    return merged


def _collapse_vertical_table(table: _Table) -> int:
    """_Table 에 세로 배치 합치기를 적용한다 (제자리).

    입력: table — _Table
    출력: 합친 무리 수
    비고:
        `collapse_vertical_cells` 는 dict 목록을 다루므로, 셀 객체를 dict
        로 옮겨 판정하고 결과를 다시 셀에 반영한다. 두 산출(markdown 과
        table_grids)이 **같은 규칙**을 쓰게 하려는 것이다 — 어긋나면 정답
        대조에서 셀 수가 맞지 않는다.
    """
    cells = [
        {"row": c.row, "col": c.col, "rowspan": c.rowspan,
         "colspan": c.colspan, "text": " ".join(c.blocks).strip(), "_src": c}
        for c in sorted(table.cells, key=lambda c: (c.row, c.col))
    ]
    merged = collapse_vertical_cells(cells)
    if not merged:
        return 0
    kept = []
    for item in cells:
        src = item["_src"]
        src.rowspan = item["rowspan"]
        src.blocks = [item["text"]] if item["text"] else []
        kept.append(src)
    table.cells = kept
    return merged


# ═══ 구간 6 — 표 읽기·렌더 ══════════════════════════════════════════════════════
# _read_table 이 격자(_Table)를 만들고 _render_table 이 GFM 으로. 중첩 표는 별도 블록.
def _table_cells(table: _Table) -> list[dict]:
    """_Table 을 셀 dict 목록으로 (세로 배치 합침 포함)."""
    cells = [
        {"row": c.row, "col": c.col, "rowspan": c.rowspan,
         "colspan": c.colspan, "text": " ".join(c.blocks).strip()}
        for c in sorted(table.cells, key=lambda c: (c.row, c.col))
    ]
    collapse_vertical_cells(cells)
    return cells


def _render_table(table: _Table) -> str:
    """표를 GFM markdown 으로 만든다.

    입력: table — 셀 목록
    출력: markdown 표 문자열
    비고: hwp5tree._render_table 과 같은 규칙 — 병합 표식, 앞쪽 빈 행 제거.
    """
    if not table.cells:
        return ""
    # 세로로 한 글자씩 나뉜 칸을 먼저 합친다 — 표를 만든 뒤에는 markdown
    # 행이 되어 버려 `collapse_vertical_text` 도 닿지 않는다.
    _collapse_vertical_table(table)
    measured_cols = max(c.col + c.colspan for c in table.cells)
    cols = max(1, min(max(table.cols, measured_cols), MAX_COLS))
    rows = max(c.row + c.rowspan for c in table.cells)
    grid = [["" for _ in range(cols)] for _ in range(rows)]

    for cell in table.cells:
        if not (0 <= cell.row < rows and 0 <= cell.col < cols):
            continue
        anchor = _escape_cell(_join_cell_blocks(cell.blocks))
        grid[cell.row][cell.col] = anchor
        if cell.rowspan > 1:
            # 덮인 칸을 무엇으로 채울지는 한 곳이 정한다 (0.5.6).
            filler = merge_continuation(anchor)
            for r in range(cell.row + 1, min(cell.row + cell.rowspan, rows)):
                grid[r][cell.col] = filler

    while len(grid) > 1 and not any(c.strip() for c in grid[0]):
        grid.pop(0)

    lines = ["| " + " | ".join(grid[0]) + " |",
             "| " + " | ".join(["---"] * cols) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in grid[1:])
    return "\n".join(lines)


def _read_table(element: ET.Element, bold_ids: set[str],
                hidden_ids: dict[str, str] | None = None) -> _Table:
    """`<hp:tbl>` 을 _Table 로 읽는다.

    입력: element — `<hp:tbl>`, bold_ids — 굵게 charPr ID,
          hidden_ids — 보이지 않는 charPr ID → 사유
    출력: _Table
    비고:
        중첩 표는 바깥 표의 셀 텍스트로 접어 넣는다. hwp5tree 는 별도
        블록으로 빼는데, 그 차이는 검증 후 맞춘다.
    """
    table = _Table(
        rows=int(element.get("rowCnt") or 0),
        cols=int(element.get("colCnt") or 0),
    )
    # **이 표가 직접 가진 셀만** 읽는다. `iter()` 로 훑으면 중첩 표의 셀까지
    # 잡혀, 좌표가 겹쳐 서로 덮어쓴다.
    #
    # 실측(행정안전부 성과계획서): 3행 3열 표의 셀이 6개여야 하는데 21개로
    # 잡혔고, 참고1·참고2 두 표가 한 표로 뒤섞였다. 그 과정에서 사이의
    # 서술문이 통째로 사라졌다 — 4,283줄 중 211줄(4.9%), 9쪽은 절반 이상.
    #
    # 직계 `<hp:tr>` 아래의 `<hp:tc>` 만 자기 셀이다. 구조가 다른 문서를
    # 대비해, 하나도 못 찾으면 옛 방식으로 물러선다.
    own = [tc for tr in element.findall(_tag(HP, "tr"))
           for tc in tr.findall(_tag(HP, "tc"))]
    for tc in own or list(element.iter(_tag(HP, "tc"))):
        addr = tc.find(_tag(HP, "cellAddr"))
        span = tc.find(_tag(HP, "cellSpan"))
        cell = _Cell(
            row=int(addr.get("rowAddr", 0)) if addr is not None else 0,
            col=int(addr.get("colAddr", 0)) if addr is not None else 0,
            rowspan=int(span.get("rowSpan", 1)) if span is not None else 1,
            colspan=int(span.get("colSpan", 1)) if span is not None else 1,
        )
        # 중첩 표 안쪽 문단은 뺀다 — 그 표가 별도 블록으로 따로 나오므로
        # 여기서 또 담으면 같은 내용이 두 번 나온다.
        nested = {id(para) for inner in tc.iter(_tag(HP, "tbl"))
                  for para in inner.iter(_tag(HP, "p"))}
        # **이 칸에서 걸린 숨은 글을 이 칸에 남긴다** (0.4.75). 문서 단위
        # 요약만으로는 어느 칸이 비었는지 알 수 없어, 쪽 맞춤이 "이 열은
        # HWPX 에만 있다" 를 판단할 수 없었다.
        box = _collectors()
        outer, box.cell = box.cell, cell.hidden
        try:
            for para in tc.iter(_tag(HP, "p")):
                if id(para) in nested:
                    continue
                text = _paragraph_text(para, bold_ids, hidden_ids)
                if text:
                    cell.blocks.append(text)
        finally:
            box.cell = outer
        table.cells.append(cell)
    return table


# ═══ 구간 7 — 문서 걷기 (진입점) ══════════════════════════════════════════════════
# to_markdown → _walk: 문단·표·그림·도형을 지면 순서로. inline 표 제자리 놓기는 향후 과제(0.4.83).
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
        hidden_ids = _hidden_char_ids(archive)
        box = _collectors()
        box.hidden.clear()
        box.anchors.clear()
        box.cell = None
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
            blocks.extend(_walk(root, bold_ids, hidden_ids))
        return "\n\n".join(blocks)
    finally:
        archive.close()


def _shape_text(node: ET.Element, bold_ids: set[str],
                hidden_ids: dict[str, str] | None = None) -> str:
    """도형 묶음(container) 안의 글자를 한 줄로 모은다.

    입력: node — hp:container 등 도형 요소, bold_ids — 굵게 charPr ID,
          hidden_ids — 보이지 않는 charPr ID → 사유
    출력: 글자를 이은 문자열 (없으면 빈 문자열)
    비고:
        간지 제목이 도형으로 그려져 있다 — "제2장 / 2027 / 년도 /
        재정운용 방향" 처럼 조각나 `hp:drawText` 안에 들어간다. 그냥
        건너뛰면 **장 제목이 통째로 빠진다.** 세 문서 공통이다
        (행안부 35 · 문체부 117 · 조달청 32개 drawText).

        조각 차례가 지면 배치 순서와 달라 그대로 이으면 어색하지만,
        빠지는 것보다는 낫다 — 좌표로 다시 세우는 것은 별개 일이다.
    """
    parts = [_paragraph_text(p, bold_ids, hidden_ids)
             for p in node.iter(_tag(HP, "p"))]
    return " ".join(x for x in parts if x).strip()


def _image_refs(node: ET.Element) -> list[str]:
    """이 요소 안 그림들의 BinData 참조 id.

    입력: node — hp:pic 을 품을 수 있는 요소
    출력: binaryItemIDRef 목록 (예: ["image1"])
    """
    return [img.get("binaryItemIDRef") or ""
            for img in node.iter(_tag(HC, "img"))
            if img.get("binaryItemIDRef")]


def image_display_sizes(path: str | Path) -> dict[str, tuple[float, float]]:
    """그림이 지면에 놓이는 크기 (참조 id → (폭pt, 높이pt)).

    입력: path — .hwpx / .hwtx 경로
    출력: {참조 id: (폭, 높이)} — HWPUNIT(1/7200인치)을 pt 로 바꾼 값
    비고:
        **해상도를 재려면 지면 크기가 있어야 한다.** 화소 수만으로는
        흐린지 알 수 없다 — 같은 1000×1000 이라도 6cm 에 놓이면 선명하고
        A4 전면이면 흐리다. `hp:curSz` 가 그 크기다.

        한글 문서에도 스캔 이미지가 들어간다. 형식이 HWPX 라고 해서
        해상도 문제가 없는 것이 아니다.
    """
    archive = zipfile.ZipFile(str(path))
    try:
        out: dict[str, tuple[float, float]] = {}
        for name in sorted(archive.namelist()):
            if not re.match(r"Contents/section\d+\.xml", name):
                continue
            try:
                root = ET.fromstring(archive.read(name))
            except ET.ParseError:
                continue
            for pic in root.iter(_tag(HP, "pic")):
                size = pic.find(_tag(HP, "curSz"))
                img = pic.find(f".//{_tag(HC, 'img')}")
                if size is None or img is None:
                    continue
                ref = img.get("binaryItemIDRef")
                if not ref:
                    continue
                try:
                    # HWPUNIT = 1/7200 inch · 1pt = 1/72 inch
                    width = int(size.get("width", "0")) / 100.0
                    height = int(size.get("height", "0")) / 100.0
                except ValueError:
                    continue
                if width > 0 and height > 0:
                    out.setdefault(ref, (width, height))
        return out
    finally:
        archive.close()


def _walk(node: ET.Element, bold_ids: set[str],
          hidden_ids: dict[str, str] | None = None) -> list[str]:
    """요소를 문서 순서로 훑어 블록 목록을 만든다.

    입력: node — 순회할 요소, bold_ids — 굵게 charPr ID,
          hidden_ids — 보이지 않는 charPr ID → 사유
    출력: markdown 블록 목록
    비고:
        `root.iter()` 로 평평하게 훑으면 표 안쪽 문단이 표와 **따로 한 번
        더** 나온다. 실제로 그렇게 짰다가 글자 수가 121,389 대 70,951 로
        부풀었다. 표를 만나면 그 서브트리는 표가 통째로 가져가고 재귀를
        멈춰야 한다.

        그림(hp:pic)과 도형 글자(hp:container)도 같은 자리에서 다룬다 —
        둘 다 `hp:p` 안에 앉아 있어(조상 사슬 pic < run < p < sec) 문단
        차례를 그대로 따르면 원본 위치에 놓인다.
    """
    out: list[str] = []
    for child in node:
        if child.tag == _tag(HP, "tbl"):
            rendered = _render_table(_read_table(child, bold_ids, hidden_ids))
            if rendered:
                out.append(rendered)
            continue                             # 서브트리는 표가 가져갔다
        if child.tag == _tag(HP, "p"):
            text = _paragraph_text(child, bold_ids, hidden_ids)
            if text:
                # 글머리 기호로 계층을 복원한다 — HWP 경로와 같은 규칙을
                # 쓴다(□ → ○ → - → *). 이것이 없어 HWPX 산출에는
                # 들여쓰기가 하나도 없었다 (실측: 같은 조달청 문서에서
                # HWP 317줄 대 HWPX 0줄). **표 셀에는 적용하지 않는다** —
                # `- ` 나 `#` 를 GFM 셀에 넣으면 표가 깨진다.
                out.append(format_body_text(text))
            # 그림 — 표식만 남긴다. 실제 내용은 VLM 이 읽어 채운다.
            for ref in _image_refs(child):
                out.append(IMAGE_MARK.format(ref=ref))
            # 도형 글자 (간지 제목 등)
            for shape in child.iter(_tag(HP, "container")):
                shape_text = _shape_text(shape, bold_ids, hidden_ids)
                if shape_text:
                    out.append(shape_text)
            # 문단 안에 표가 놓일 수 있다 (hwp5html 과 같은 구조).
            #
            # **매달린 표는 글 뒤에 놓는다** (0.4.81). `treatAsChar=0` 인
            # 표는 지면에서 문단 글보다 아래에 그려지는데 XML 로는 문단
            # 앞쪽에 앉아 있다 — 문서 순서를 그대로 따르면 표가 제 캡션보다
            # 먼저 나온다. 위에서 문단 글을 이미 내보냈으므로, 여기서
            # 표를 붙이면 지면 순서와 맞는다.
            #
            # 글자 취급(`treatAsChar=1`)인 표는 XML 순서가 곧 지면 순서라
            # **제자리에 두는 것이 맞다** — 실측(해경): 매달림 159 · 글자
            # 취급 64. 다만 **아직 그렇게 하지 않는다** (0.4.83 명시): 지금
            # 코드는 종류와 무관하게 문단 글을 전부 먼저 내고 표를 뒤에
            # 붙인다. inline 표 앞뒤로 글이 갈린 문단이면 뒤쪽 글이 표 위로
            # 끌려온다. 제대로 하려면 `_paragraph_text` 를 run 단위로 쪼개
            # inline 표는 그 자리에, anchored 만 문단 끝으로 보내야 한다 —
            # 네 부처 inline 표 118~141개에 걸리는 변경이라 실측과 함께
            # 별도 판에서 한다. `kind` 는 지금은 **기록(anchor_notes)에만**
            # 쓰인다.
            for tbl in child.iter(_tag(HP, "tbl")):
                kind = _table_anchor(tbl)
                anchors = _collectors().anchors
                anchors[kind] = anchors.get(kind, 0) + 1
                if kind == "anchored" and text:
                    # 글을 먼저 냈고 표를 뒤에 붙인다 — 순서를 바로잡았다.
                    anchors["reordered"] = anchors.get("reordered", 0) + 1
                rendered = _render_table(_read_table(tbl, bold_ids, hidden_ids))
                if rendered:
                    out.append(rendered)
            continue
        out.extend(_walk(child, bold_ids, hidden_ids))
    return out


# ═══ 구간 8 — 그림 바이트·표 격자 노출 ═══════════════════════════════════════════════
# image_parts(zip 안 그림) · table_grids(cells 재료) — extractors.hwpx 가 쓴다.
def image_parts(path: str | Path) -> dict[str, tuple[bytes, str, str]]:
    """HWPX 안의 그림을 참조 id 로 꺼낸다.

    입력: path — .hwpx / .hwtx 경로
    출력: {참조 id: (바이트, media-type, 확장자)}
    비고:
        `Contents/content.hpf` 의 opf:item 이 id → BinData 경로를 명시한다
        — 추측하지 않는다. **확장자를 PNG 로 가정하면 안 된다**: 행안부는
        image1.jpg 다 (조달청·문체부는 png).

        확장자는 media-type 이 아니라 **href 에서 가져온다.** 행안부의
        media-type 은 비표준 `image/jpg` 라(표준은 image/jpeg)
        `mimetypes.guess_extension` 이 None 을 내고, 그러면 기본값 .png 로
        저장돼 **JPEG 를 .png 이름으로 쓰게 된다.** href 는 실제 파일
        이름이라 그런 문제가 없다.
    """
    archive = zipfile.ZipFile(str(path))
    try:
        try:
            manifest = archive.read("Contents/content.hpf").decode(
                "utf-8", errors="replace")
        except KeyError:
            return {}
        out: dict[str, tuple[bytes, str]] = {}
        for match in re.finditer(r"<opf:item\b[^>]*/>", manifest):
            item = match.group(0)
            ref = re.search(r'id="([^"]+)"', item)
            href = re.search(r'href="([^"]+)"', item)
            mime = re.search(r'media-type="([^"]+)"', item)
            if not (ref and href and mime and mime.group(1).startswith("image/")):
                continue
            try:
                data = archive.read(href.group(1))
            except KeyError:
                continue                         # 참조는 있는데 파일이 없다
            suffix = PurePosixPath(href.group(1)).suffix or ".png"
            out[ref.group(1)] = (data, mime.group(1), suffix)
        return out
    finally:
        archive.close()


def table_grids(path: str | Path) -> list[list[dict]]:
    """표별 셀 격자를 문서 순서로 낸다.

    입력: path — .hwpx / .hwtx 경로
    출력: 표마다 셀 dict 목록. 셀은 row, col, rowspan, colspan, text
    비고:
        markdown 은 병합을 표현하지 못한다. `colSpan="3"` 인 셀도 한 칸에만
        값이 들어가고 나머지는 빈 칸이 된다 — 실측에서 병합 표기(`〃`)가
        1,165회 나왔는데, 그 자리의 원래 span 값은 markdown 에서 사라진다.

        HWPX 는 XML 에 병합이 명시돼 있어(`cellSpan`, `cellAddr`) 추측할
        필요가 없다. 그 값을 그대로 내보내면 구조화 단계가 **병합 셀 값을
        하위 행에 전파**할 수 있다 — 표 조각을 RAG 청크로 잘라도 레이블이
        붙어 있게 하는 표준 대응이다.

        markdown 과 순서가 같으므로 `<table N>` 의 N 번째가 이 목록의
        N-1 번째 항목이다.
    """
    archive = zipfile.ZipFile(str(path))
    try:
        bold_ids = _bold_char_ids(archive)
        hidden_ids = _hidden_char_ids(archive)
        # **여기서는 비우지 않는다** (0.4.74). 호출부는 `to_markdown` 을
        # 먼저 부른 뒤 이 함수를 부르는데, 여기서 비우면 본문에서 걸러낸
        # 숨은 글이 지워진다 — 실측에서 흰 글자 22개(목차 전문)가 그렇게
        # 사라져 0으로 찍혔다. 수명은 `to_markdown` 이 소유한다.
        section_names = sorted(
            (n for n in archive.namelist()
             if re.fullmatch(r"Contents/section\d+\.xml", n)),
            key=lambda n: int(re.search(r"\d+", n.split("/")[1]).group()),
        )

        grids: list[list[dict]] = []
        for name in section_names:
            root = ET.fromstring(archive.read(name))
            for element in root.iter(_tag(HP, "tbl")):
                table = _read_table(element, bold_ids, hidden_ids)
                cells = [
                    {
                        "row": cell.row,
                        "col": cell.col,
                        "rowspan": cell.rowspan,
                        "colspan": cell.colspan,
                        "text": " ".join(cell.blocks).strip(),
                        # 이 칸에서 뺀 숨은 글. 없으면 키 자체를 넣지 않는다
                        # — 대부분의 칸에 없으므로 결과물이 부풀지 않게.
                        **({"hidden": cell.hidden} if cell.hidden else {}),
                    }
                    for cell in sorted(table.cells, key=lambda c: (c.row, c.col))
                ]
                collapse_vertical_cells(cells)
                grids.append(cells)
        return grids
    finally:
        archive.close()
