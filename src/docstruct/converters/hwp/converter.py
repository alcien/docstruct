"""HWP → markdown/HTML/XML/텍스트.

역할:
    HWP 파일을 세 경로 중 하나로 읽는다.
      hwpml-xml     내용이 실제로는 XML 인 경우 직접 파싱 (표 구조 보존)
      pyhwp-html    HWP 바이너리를 HTML 로 변환 후 파싱 (표 구조 보존)
      olefile-text  위가 불충분할 때 텍스트만 추출 (표·그림 구조 손실)
호출부:
    docstruct.extractors.hwp
    converters.registry (BaseConverter 인터페이스)
출력:
    markdown / HTML / XML / 텍스트 문자열, 원본 `<table>` HTML 조각,
    실제 사용된 경로 이름
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from docstruct.converters.base import BaseConverter
from docstruct.converters.deps import BS4_AVAILABLE, BeautifulSoup, OLEFILE_AVAILABLE, PYHWP_AVAILABLE
from docstruct.converters.html import html_to_markdown, html_to_text, html_to_xml
from docstruct.converters.hwp.hwpml import is_hwpml, to_html as hwpml_to_html
from docstruct.converters.hwp.hwpml import to_markdown as hwpml_to_markdown
from docstruct.converters.hwp.hwpml import to_text as hwpml_to_text
from docstruct.converters.hwp.hwpml import to_xml as hwpml_to_xml
from docstruct.converters.hwp.olefile import clean_text, extract_raw_text, text_to_html, text_to_markdown, text_to_xml
import logging

from docstruct.converters.hwp.pyhwp import HwpTimeout, assess_pyhwp_html, hwp_to_html_str


_log = logging.getLogger(__name__)


class HwpConverter(BaseConverter):
    """
    HWP 파일을 text / markdown / html / xml 로 변환

    핵심 전략
    ----------
    1. hwp5html로 HTML을 얻고 HTML 파싱으로 각 포맷 변환 (우선)
    2. HTML이 불충분하면 olefile 직접 파싱으로 폴백 (필드 문서 등)
    3. pyhwp 미설치 시 olefile 폴백
    """

    def __init__(self, hwp_path: str | Path):
        super().__init__(hwp_path)
        self._html_cache: str | None = None
        self._html_stderr: str = ""
        self._ole_fallback: bool | None = None
        self._ole_text_cache: str | None = None

    @property
    def source_format(self) -> str:
        """원본 형식 이름.

        입력: 없음
        출력: 'hwp'
        """
        return "hwp"

    def extraction_path(self) -> str:
        """실제로 사용된 추출 경로.

        입력: 없음
        출력: 'hwpml-xml' | 'pyhwp-html' | 'olefile-text'
        """
        if is_hwpml(self.path):
            return "hwpml-xml"
        return "olefile-text" if self._uses_ole_fallback() else "pyhwp-html"

    def _uses_ole_fallback(self) -> bool:
        """pyhwp 결과가 불충분해 텍스트 폴백이 필요한지 판단한다.

        입력: 없음
        출력: 폴백이 필요하면 True
        """
        if self._ole_fallback is not None:
            return self._ole_fallback
        if is_hwpml(self.path):
            self._ole_fallback = False
            return False
        if not PYHWP_AVAILABLE:
            self._ole_fallback = True
            return True
        try:
            html, stderr = hwp_to_html_str(self.path)
        except HwpTimeout as exc:
            # 시간 안에 못 끝내면 표 구조를 포기하고 텍스트만이라도 뽑는다.
            _log.warning("%s", exc)
            self._ole_fallback = True
            return True
        self._html_cache = html
        self._html_stderr = stderr
        file_size = os.path.getsize(self.path)
        if assess_pyhwp_html(html, stderr, file_size):
            if OLEFILE_AVAILABLE:
                print(
                    "[경고] pyhwp HTML 불충분 — olefile 폴백 사용 "
                    "(표/그림 구조 손실)",
                    file=sys.stderr,
                )
                self._ole_fallback = True
            else:
                print(
                    "[경고] pyhwp HTML 불충분 — olefile 미설치, HTML 결과 사용",
                    file=sys.stderr,
                )
                self._ole_fallback = False
        else:
            self._ole_fallback = False
        return self._ole_fallback

    def _get_html(self) -> str:
        """pyhwp 로 변환한 HTML 을 얻는다 (최초 1회 변환 후 재사용).

        입력: 없음
        출력: HTML 문자열
        """
        if self._uses_ole_fallback():
            return text_to_html(self._get_ole_text())
        if self._html_cache is None:
            self._html_cache, self._html_stderr = hwp_to_html_str(self.path)
        return self._html_cache

    def _get_ole_text(self) -> str:
        """olefile 로 원문 텍스트를 추출한다 (최초 1회 후 재사용).

        입력: 없음
        출력: 텍스트 문자열
        """
        if self._ole_text_cache is None:
            if not OLEFILE_AVAILABLE:
                raise ImportError("olefile 패키지를 설치하세요: pip install olefile")
            self._ole_text_cache = clean_text(extract_raw_text(self.path))
        return self._ole_text_cache

    def to_html(self) -> str:
        """본문을 HTML 로 변환한다.

        입력: 없음
        출력: HTML 문자열
        """
        if is_hwpml(self.path):
            return hwpml_to_html(self.path)
        return self._get_html()

    def to_text(self) -> str:
        """본문을 평문으로 변환한다.

        입력: 없음
        출력: 텍스트 문자열
        """
        if is_hwpml(self.path):
            return hwpml_to_text(self.path)
        if self._uses_ole_fallback():
            return self._get_ole_text()
        if PYHWP_AVAILABLE:
            return html_to_text(self._get_html())
        print("[경고] pyhwp 없음 — olefile 폴백 (표/그림 구조 손실)", file=sys.stderr)
        return self._get_ole_text()

    def to_markdown(self) -> str:
        """본문을 markdown 으로 변환한다.

        입력: 없음
        출력: markdown 문자열
        """
        if is_hwpml(self.path):
            return hwpml_to_markdown(self.path)
        if self._uses_ole_fallback():
            return text_to_markdown(self._get_ole_text())
        return html_to_markdown(self._get_html())

    def table_html_fragments(self) -> list[str]:
        """원본 `<table>` HTML 조각을 문서 순서로 얻는다.

        입력: 없음
        출력: HTML 문자열 목록. HWPML·olefile 경로이거나 bs4 미설치면 빈 목록
        비고: 페이지 이미지가 없는 HWP 에서 표 재추출의 근거로 쓴다
        """
        if is_hwpml(self.path) or self._uses_ole_fallback():
            return []
        if not BS4_AVAILABLE:
            return []
        soup = BeautifulSoup(self._get_html(), "html.parser")
        return [str(table) for table in soup.find_all("table")]

    def to_xml(self) -> str:
        """본문을 XML 로 변환한다.

        입력: 없음
        출력: XML 문자열
        """
        if is_hwpml(self.path):
            return hwpml_to_xml(self.path)
        if self._uses_ole_fallback():
            return text_to_xml(self._get_ole_text())
        return html_to_xml(self._get_html())

    def save(self, output_path: str | Path, fmt: str | None = None) -> None:
        super().save(output_path, fmt)
        print(f"저장 완료: {output_path}  ({fmt or 'auto'})")
