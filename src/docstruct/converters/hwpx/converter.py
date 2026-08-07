"""HWPX → markdown/HTML/XML/텍스트.

역할:
    python-hwpx 로 문서를 열어 목표 형식으로 변환한다.
호출부:
    converters.registry, docstruct.extractors.hwpx
출력:
    변환된 문자열
"""
from __future__ import annotations

from docstruct.converters.base import BaseConverter
from docstruct.converters.deps import HWPX_AVAILABLE


class HwpxConverter(BaseConverter):
    """HWPX → text / markdown / html / xml (python-hwpx)."""

    def __init__(self, path: str):
        super().__init__(path)
        self._document = None

    @property
    def source_format(self) -> str:
        """소스 포맷 식별자.

        입력: 없음
        출력: 'hwpx'
        """
        return "hwpx"

    def _ensure_hwpx(self) -> None:
        """python-hwpx 를 쓸 수 있는지 확인한다.

        입력: 없음
        출력: 없음 (미설치면 ImportError)
        """
        if not HWPX_AVAILABLE:
            raise ImportError(
                "python-hwpx 패키지가 필요합니다: pip install python-hwpx"
            )

    def _get_document(self):
        """HwpxDocument 를 연다 (한 번만 열고 캐시).

        입력: 없음
        출력: hwpx.HwpxDocument
        """
        if self._document is not None:
            return self._document

        self._ensure_hwpx()
        from hwpx import HwpxDocument

        self._document = HwpxDocument.open(self.path)
        return self._document

    def to_text(self) -> str:
        """평문으로 변환한다.

        입력: 없음
        출력: 텍스트 문자열
        """
        return self._get_document().export_text()

    def to_markdown(self) -> str:
        """markdown 으로 변환한다.

        입력: 없음
        출력: markdown 문자열 (rich_markdown — 신·구 API 겸용)
        """
        return rich_markdown(self._get_document())

    def to_html(self) -> str:
        """HTML 로 변환한다.

        입력: 없음
        출력: HTML 문자열
        """
        return self._get_document().export_html()

    def to_xml(self) -> str:
        """XML 로 변환한다.

        입력: 없음
        출력: XML 문자열 (HTML 을 거쳐 재구성)
        """
        from docstruct.converters.html import html_to_xml

        return html_to_xml(self.to_html())


def rich_markdown(doc) -> str:
    """HwpxDocument 에서 rich markdown 을 얻는다 (신·구 API 겸용).

    입력: doc — hwpx.HwpxDocument 인스턴스
    출력: markdown 문자열
    동작:
        python-hwpx 6.0 부터 `export_rich_markdown()` 이
        `doc.text.markdown(rich=True)` 로 옮겨졌고 7.0 에서 제거된다.
        신 API 를 먼저 시도하고, 5.x 이하에서는 구 API 로 내려간다.
        상한 핀(`<7`) 대신 이 분기를 두는 이유: 사내 여러 환경에 설치된
        버전이 제각각이라, 코드가 양쪽을 다 받아주는 편이 운영이 쉽다.
    """
    text_api = getattr(doc, "text", None)
    markdown = getattr(text_api, "markdown", None) if text_api is not None else None
    if callable(markdown):
        try:
            return markdown(rich=True)
        except TypeError:
            pass                         # 시그니처가 다른 별개 속성이면 구 API 로
    return doc.export_rich_markdown()
