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
    """hwp5html 출력 HTML 을 markdown 으로 변환한다.

    입력: html — HTML 문자열
    출력: markdown 문자열
    예외: beautifulsoup4 미설치 시 ImportError
    동작: `<p>` 내부 중첩 표, ◦/- 불릿, GFM 표 등 hwp5html 특유 구조를
          collect_html_blocks 로 블록화한 뒤 렌더링한다.
    """
    if not BS4_AVAILABLE:
        raise ImportError("beautifulsoup4가 필요합니다: pip install beautifulsoup4")

    soup = BeautifulSoup(html, "html.parser")
    return blocks_to_markdown(collect_html_blocks(soup))


def html_to_text(html: str) -> str:
    """HTML 에서 순수 텍스트를 추출한다.

    입력: html — HTML 문자열
    출력: 텍스트 문자열 (표는 탭 구분 행, 그림은 [그림] 표식)
    동작: bs4 가 있으면 블록 파이프라인을 쓰고, 없으면 표준 HTMLParser 로
          태그만 벗겨 최소한의 결과라도 낸다.
    """
    if not BS4_AVAILABLE:
        class _Strip(HTMLParser):
            def __init__(self):
                super().__init__()
                self.parts: list[str] = []

            def handle_data(self, data: str):
                """태그 사이 텍스트 조각을 모은다.

                입력: data — 파서가 넘겨준 텍스트 노드
                출력: 없음 (self.parts 에 축적)
                """
                self.parts.append(data)

        p = _Strip()
        p.feed(html)
        return " ".join(p.parts)

    soup = BeautifulSoup(html, "html.parser")
    return blocks_to_text(collect_html_blocks(soup))


def html_to_xml(html: str) -> str:
    """HTML 구조를 간결한 XML 로 재구성한다.

    입력: html — HTML 문자열
    출력: XML 문자열
    예외: beautifulsoup4 미설치 시 ImportError
    비고: text/markdown 과 동일한 collect_html_blocks 파이프라인을 쓴다.
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
