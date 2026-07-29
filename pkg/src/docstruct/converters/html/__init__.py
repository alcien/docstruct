"""HTML → markdown/텍스트/XML 변환.

역할:
    pyhwp 가 만든 HTML 을 구조를 보존하며 변환한다.
호출부:
    converters.hwp.converter
출력:
    html_to_markdown / html_to_text / html_to_xml
"""
from __future__ import annotations

from html.parser import HTMLParser

from docstruct.converters.deps import BS4_AVAILABLE, BeautifulSoup
from docstruct.converters.html.blocks import (
    blocks_to_markdown,
    blocks_to_text,
    blocks_to_xml,
    collect_html_blocks,
)


def html_to_markdown(html: str) -> str:
    """
    hwp5html 출력 HTML을 마크다운으로 변환합니다.

    <p> 내부 중첩 표, ◦/- 불릿, GFM 표 등 hwp5html 특수 구조를 처리합니다.
    """
    if not BS4_AVAILABLE:
        raise ImportError("beautifulsoup4가 필요합니다: pip install beautifulsoup4")

    soup = BeautifulSoup(html, "html.parser")
    return blocks_to_markdown(collect_html_blocks(soup))


def html_to_text(html: str) -> str:
    """
    HTML에서 순수 텍스트를 추출합니다.

    표는 셀 텍스트를 탭으로 구분해 행별로 출력
    그림은 [그림] 플레이스홀더로 대체
    """
    if not BS4_AVAILABLE:
        class _Strip(HTMLParser):
            def __init__(self):
                super().__init__()
                self.parts: list[str] = []

            def handle_data(self, data: str):
                self.parts.append(data)

        p = _Strip()
        p.feed(html)
        return " ".join(p.parts)

    soup = BeautifulSoup(html, "html.parser")
    return blocks_to_text(collect_html_blocks(soup))


def html_to_xml(html: str) -> str:
    """
    HTML 구조를 간결한 XML로 재구성합니다.

    text/markdown과 동일한 `collect_html_blocks` 파이프라인을 사용합니다.
    """
    if not BS4_AVAILABLE:
        raise ImportError("beautifulsoup4가 필요합니다: pip install beautifulsoup4")

    soup = BeautifulSoup(html, "html.parser")
    return blocks_to_xml(collect_html_blocks(soup))


__all__ = [
    "html_to_text",
    "html_to_markdown",
    "html_to_xml",
    "collect_html_blocks",
]
