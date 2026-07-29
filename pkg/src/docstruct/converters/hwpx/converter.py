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
        return "hwpx"

    def _ensure_hwpx(self) -> None:
        if not HWPX_AVAILABLE:
            raise ImportError(
                "python-hwpx 패키지가 필요합니다: pip install python-hwpx"
            )

    def _get_document(self):
        if self._document is not None:
            return self._document

        self._ensure_hwpx()
        from hwpx import HwpxDocument

        self._document = HwpxDocument.open(self.path)
        return self._document

    def to_text(self) -> str:
        return self._get_document().export_text()

    def to_markdown(self) -> str:
        return self._get_document().export_rich_markdown()

    def to_html(self) -> str:
        return self._get_document().export_html()

    def to_xml(self) -> str:
        from docstruct.converters.html import html_to_xml

        return html_to_xml(self.to_html())
