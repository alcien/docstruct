"""HTML 파싱 보조 함수.

역할:
    태그 텍스트 추출, 속성 읽기, 공백 정리 등 작은 유틸.
호출부:
    converters.html.blocks, converters.html.tables
출력:
    문자열 또는 정수
"""
from __future__ import annotations

import re

from docstruct.converters.deps import NavigableString, Tag

def normalize_line(text: str) -> str:
    """연속 공백·개행을 한 줄 텍스트로 정리한다.

    입력: text — 원본 문자열
    출력: 공백 하나로 이어진 한 줄 문자열
    """
    return " ".join(text.split())


def tag_text(tag: "Tag") -> str:
    """hwp5html span 경계의 공백을 보존하며 텍스트를 추출한다.

    입력: tag — Tag
    출력: 정리된 한 줄 텍스트 (`<br>` 은 개행으로)
    비고: get_text(separator=' ') 는 한글 단어 중간에 공백을 넣고,
          get_text() 는 span 경계 공백을 지우므로, 텍스트 노드를 직접
          이어 붙인다.
    """
    parts: list[str] = []
    for node in tag.descendants:
        if type(node) is NavigableString:
            parts.append(str(node).replace("\r", "").replace("\xa0", " "))
        elif isinstance(node, Tag) and node.name == "br":
            parts.append("\n")
    return normalize_line("".join(parts))


def cell_text(tag: "Tag") -> str:
    """셀 태그의 내부 텍스트를 한 줄로 정리한다.

    입력: tag — `<td>/<th>` Tag
    출력: 한 줄 문자열 (tag_text 와 동일 규칙)
    """
    return tag_text(tag)


# hwp5html은 ul/li 대신 <p> 안에 ◦, - 등으로 목록을 표현
_RE_BULLET_L2 = re.compile(r"^\s*[-–—]\s+")
_RE_BULLET_L1 = re.compile(r"^\s*[◦•●○▪■□◆◇▷▶]\s*")


def bullet_to_md(text: str) -> str:
    """HWP 식 불릿 단락을 GFM 목록 항목으로 바꾼다.

    입력: text — 단락 텍스트
    출력: `- ` / `  - ` 접두의 목록 줄. 불릿이 아니면 원문 그대로
    비고: hwp5html 은 ul/li 대신 `<p>` 안에 ◦·- 기호로 목록을 표현한다.
    """
    if _RE_BULLET_L2.match(text):
        return "  - " + _RE_BULLET_L2.sub("", text).strip()
    if _RE_BULLET_L1.match(text):
        return "- " + _RE_BULLET_L1.sub("", text).strip()
    return text


def int_attr(tag: "Tag", name: str, default: int = 1) -> int:
    """태그 속성을 정수로 읽는다.

    입력: tag — Tag, name — 속성명, default — 기본값
    출력: 정수. 없거나 숫자가 아니면 default
    """
    try:
        return max(1, int(tag.get(name, default)))
    except (TypeError, ValueError):
        return default

