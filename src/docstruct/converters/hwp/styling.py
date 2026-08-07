"""HWP 서식 정보 → markdown 서식.

역할:
    pyhwp 트리에는 문단·표 말고도 서식이 다 들어 있다. 지금까지 ``Text`` 만
    읽어 제목도 굵은 글씨도 평문이 됐다. DocInfo 의 스타일·글자모양을
    문단에 연결해 markdown 으로 옮긴다.
호출부:
    docstruct.converters.hwp.hwp5tree
출력:
    제목(`#`), 강조(`**`, `*`), 목록(`- `) 이 반영된 문단 문자열

무엇을 읽는가
-------------
정부 문서 626KB 기준 실측:

    DocInfo   Style 46개 · CharShape 1,168개 · ParaShape 639개 · Numbering 23개
    본문      Paragraph.style_id / charshape_id 로 참조

한글 기본 스타일에 `개요 1` ~ `개요 7` 이 있어 제목 계층이 그대로 나온다.
글자 크기(`basesize`)와 굵기(`bold`)는 CharShape 에 있다.

의도적으로 안 하는 것
--------------------
글자 크기를 제목 수준으로 **추정하지 않는다.** 표 안 강조나 표지 큰 글씨가
전부 제목이 되어 문서 구조가 망가진다. 스타일 이름이 분명할 때만 제목으로
올린다.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

_log = logging.getLogger(__name__)

#: 스타일 이름 → markdown 제목 수준. 한글 기본 스타일 이름을 그대로 쓴다.
#: 사용자가 만든 스타일은 여기 없으므로 제목이 되지 않는다 — 추정보다
#: 놓치는 편이 안전하다.
_HEADING_STYLES: dict[str, int] = {
    "개요 1": 1, "개요 2": 2, "개요 3": 3, "개요 4": 4,
    "개요 5": 5, "개요 6": 6, "개요 7": 6,
    "제목": 1, "부제목": 2,
    "Heading 1": 1, "Heading 2": 2, "Heading 3": 3,
    "Outline 1": 1, "Outline 2": 2, "Outline 3": 3,
}

#: 이 스타일들은 본문이 아니므로 제목으로 올리지 않는다.
_NON_BODY_STYLES = frozenset({
    "쪽 번호", "머리말", "꼬리말", "각주", "미주", "메모", "캡션",
})

#: 목록 글머리로 자주 쓰이는 문자. 한글 공문서 관례를 따른다.
#: `□ ○ - ∙ ▪ ➊` 같은 것들로, 계층이 이 순서로 내려간다.
_BULLET_LEVELS: tuple[tuple[str, ...], ...] = (
    ("□", "■", "◆"),
    ("○", "●", "◎"),
    ("-", "–", "∙", "▪", "·"),
    ("*", "▸", "▷"),
)

#: 글머리 뒤에 공백이 오는 형태만 목록으로 본다.
_BULLET_RE = re.compile(r"^([□■◆○●◎\-–∙▪·*▸▷])\s+(.*)$")

#: 번호 체계로 나타낸 제목. 실제 공문서는 `개요 N` 스타일 대신 이 표기를
#: 쓰는 경우가 훨씬 많다 — 626KB 정부 문서에서 스타일 기반 제목은 0건,
#: `제N장` 은 3건이었다.
_NUMBERED_HEADINGS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"^제\s*\d+\s*편(\s|$)"), 1),
    (re.compile(r"^제\s*\d+\s*장(\s|$)"), 1),
    (re.compile(r"^제\s*\d+\s*절(\s|$)"), 2),
    (re.compile(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]\s*[.．]\s*\S"), 2),
)

#: 제목으로 볼 최대 길이. 이보다 길면 번호로 시작하는 본문 문장이다.
_MAX_HEADING_CHARS = 60


@dataclass
class DocStyles:
    """DocInfo 에서 뽑은 서식 표.

    입력(필드):
        style_names  스타일 id → 이름
        charshapes   글자모양 id → (굵기, 기울임, 크기)
    """

    style_names: dict[int, str] = field(default_factory=dict)
    charshapes: dict[int, tuple[bool, bool, int]] = field(default_factory=dict)

    def heading_level(self, style_id: int | None) -> int | None:
        """이 문단이 몇 수준 제목인지.

        입력: style_id — Paragraph.style_id
        출력: 1~6. 제목이 아니면 None
        """
        if style_id is None:
            return None
        name = (self.style_names.get(style_id) or "").strip()
        if not name or name in _NON_BODY_STYLES:
            return None
        level = _HEADING_STYLES.get(name)
        if level:
            return level
        # `개요 6 사본1` 처럼 파생 스타일이 흔하다. 접두가 같으면 같은 수준.
        for base, base_level in _HEADING_STYLES.items():
            if name.startswith(base):
                return base_level
        return None

    def is_bold(self, charshape_id: int | None) -> bool:
        """이 글자모양이 굵은지.

        입력: charshape_id
        출력: bool
        """
        shape = self.charshapes.get(charshape_id) if charshape_id is not None else None
        return bool(shape and shape[0])

    def is_italic(self, charshape_id: int | None) -> bool:
        """이 글자모양이 기울임인지.

        입력: charshape_id
        출력: bool
        """
        shape = self.charshapes.get(charshape_id) if charshape_id is not None else None
        return bool(shape and shape[1])


def read_styles(hwp, end_event) -> DocStyles:
    """DocInfo 를 훑어 서식 표를 만든다.

    입력: hwp — Hwp5File, end_event — hwp5.treeop.ENDEVENT
    출력: DocStyles (실패하면 빈 표)
    비고: 서식은 부가 정보라, 못 읽어도 본문 추출은 계속돼야 한다.
    """
    styles = DocStyles()
    try:
        events = list(hwp.docinfo.events())
    except Exception as exc:                     # noqa: BLE001 - 서식 없이도 진행
        _log.debug("DocInfo 를 읽지 못했습니다: %s", exc)
        return styles

    style_index = char_index = 0
    for event, item in events:
        if event is end_event:
            continue
        name, attrs = item[0].__name__, item[1]
        if name == "Style":
            label = attrs.get("local_name") or attrs.get("name") or ""
            styles.style_names[style_index] = str(label)
            style_index += 1
        elif name == "CharShape":
            flags = attrs.get("charshapeflags")
            styles.charshapes[char_index] = (
                bool(getattr(flags, "bold", 0)),
                bool(getattr(flags, "italic", 0)),
                int(attrs.get("basesize") or 0),
            )
            char_index += 1
    return styles


def format_paragraph(
    text: str,
    *,
    styles: DocStyles,
    style_id: int | None = None,
    charshape_id: int | None = None,
    in_cell: bool = False,
) -> str:
    """문단 하나를 markdown 서식으로 옮긴다.

    입력:
        text          문단 텍스트 (이미 정규화된 것)
        styles        DocStyles
        style_id      Paragraph.style_id
        charshape_id  문단 첫 글자의 charshape_id
        in_cell       표 셀 안인지 — 제목·목록 기호를 쓰지 않는다
    출력: markdown 문자열
    비고:
        셀 안에서는 `#` 나 `- ` 를 붙이지 않는다. GFM 표 셀에 넣으면 깨진다.
    """
    body = text.strip()
    if not body:
        return ""

    if in_cell:
        return _apply_emphasis(body, styles, charshape_id)

    level = styles.heading_level(style_id) or _numbered_heading_level(body)
    if level:
        return "#" * level + " " + _strip_bullet(body)[1]

    depth, rest = _bullet_depth(body)
    if depth is not None:
        emphasized = _apply_emphasis(rest, styles, charshape_id)
        return "  " * depth + "- " + emphasized

    return _apply_emphasis(body, styles, charshape_id)


def _numbered_heading_level(text: str) -> int | None:
    """번호 표기로 제목 수준을 판단한다.

    입력: text — 문단 텍스트
    출력: 1~2. 제목이 아니면 None
    비고:
        길이 제한을 둔다. `제1장 총칙` 은 제목이지만 `제1조에 따라 …` 로
        이어지는 긴 문장은 본문이다.
    """
    if len(text) > _MAX_HEADING_CHARS:
        return None
    for pattern, level in _NUMBERED_HEADINGS:
        if pattern.match(text):
            return level
    return None


def _bullet_depth(text: str) -> tuple[int | None, str]:
    """글머리 기호로 목록 수준을 판단한다.

    입력: text — 문단 텍스트
    출력: (수준 0~3, 기호를 뗀 본문). 목록이 아니면 (None, 원문)
    비고:
        한글 공문서는 `□ → ○ → - → *` 순으로 계층을 내린다. 기호가 곧
        수준이라 들여쓰기 정보 없이도 계층이 복원된다.
    """
    match = _BULLET_RE.match(text)
    if not match:
        return None, text
    marker, rest = match.group(1), match.group(2)
    for depth, markers in enumerate(_BULLET_LEVELS):
        if marker in markers:
            return depth, rest.strip()
    return None, text


def _strip_bullet(text: str) -> tuple[str, str]:
    """제목 앞의 글머리 기호를 뗀다.

    입력: text
    출력: (뗀 기호, 남은 본문). 기호가 없으면 ("", 원문)
    비고: `□ 성과목표관리` 가 제목 스타일이면 `# 성과목표관리` 가 자연스럽다.
    """
    match = _BULLET_RE.match(text)
    if match:
        return match.group(1), match.group(2).strip()
    return "", text


def _apply_emphasis(text: str, styles: DocStyles, charshape_id: int | None) -> str:
    """굵기·기울임을 markdown 으로 감싼다.

    입력: text, styles, charshape_id
    출력: `**...**` / `*...*` 이 적용된 문자열
    비고:
        문단 **전체**가 같은 글자모양일 때만 적용한다. 조각마다 다르게
        감싸면 표식이 겹쳐 읽기 어려워지고, 검색에도 방해가 된다.
    """
    if not text or charshape_id is None:
        return text
    bold = styles.is_bold(charshape_id)
    italic = styles.is_italic(charshape_id)
    if not bold and not italic:
        return text
    # 이미 표식이 있으면 덧씌우지 않는다.
    if text.startswith(("*", "_")) or text.endswith(("*", "_")):
        return text
    if bold and italic:
        return f"***{text}***"
    return f"**{text}**" if bold else f"*{text}*"
