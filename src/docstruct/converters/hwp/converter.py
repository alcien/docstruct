"""HWP → markdown/HTML/XML/텍스트.

역할:
    HWP 파일을 세 경로 중 하나로 읽는다.
      hwpml-xml     내용이 실제로는 XML 인 경우 직접 파싱 (표 구조 보존)
      hwp5-tree     pyhwp 파서 트리를 직접 읽음 (표·중첩표·병합 보존, 기본)
      pyhwp-html    hwp5html 로 HTML 변환 후 파싱 (폴백)
      olefile-text  위가 모두 불충분할 때 텍스트만 추출 (표·그림 구조 손실)
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
from docstruct.converters.hwp import hwp5tree
from docstruct.converters.hwp.diagnose import diagnose
from docstruct.converters.hwp.olefile import clean_text, extract_raw_text, text_to_html, text_to_markdown, text_to_xml
import logging

from docstruct.converters.hwp.pyhwp import (
    HwpTimeout, hwp_to_html_str, pyhwp_html_verdict, real_error_lines,
)


_log = logging.getLogger(__name__)


def _first_real_error(exc: Exception) -> str:
    """예외 메시지에서 상시 경고를 뺀 첫 줄을 뽑는다.

    입력: exc — hwp_to_html_str 이 낸 RuntimeError
    출력: 로그 한 줄에 넣을 짧은 사유
    """
    lines = real_error_lines(str(exc), limit=3)
    return lines[-1].strip() if lines else "원인 미상 (경고 외 메시지 없음)"

#: 파서 트리 결과가 이보다 적으면 실패로 보고 기존 경로로 넘어간다.
_MIN_TREE_CHARS = 200


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
        self._tree_cache: str | None = None
        self._tree_tried = False
        #: 기본 경로(hwp5-tree)가 실패한 사유. 폴백까지 실패했을 때
        #: 사람에게 보여야 할 **첫 실패**다.
        self._tree_failure: str | None = None

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
        출력: 'hwpml-xml' | 'hwp5-tree' | 'pyhwp-html' | 'olefile-text'
        """
        if is_hwpml(self.path):
            return "hwpml-xml"
        if self._get_tree_markdown() is not None:
            return "hwp5-tree"
        return "olefile-text" if self._uses_ole_fallback() else "pyhwp-html"

    def _get_tree_markdown(self) -> str | None:
        """pyhwp 파서 트리 경로 결과 (한 번만 시도하고 캐시).

        입력: 없음
        출력: markdown. 쓸 수 없으면 None
        비고:
            같은 pyhwp 안에서 파서 층(hwp5.xmlmodel)을 직접 읽는다. HTML
            생성기(hwp5html)는 XSLT 단계에서 내용이 크게 깎이고 문서에 따라
            통째로 실패한다 — 626KB 문서에서 표 48개/글자 7천 vs
            표 111개/글자 3만7천, 26초 vs 1.6초 차이였다.

            결과가 비면 None 을 돌려주고 기존 경로가 이어받는다.
        """
        if self._tree_tried:
            return self._tree_cache
        self._tree_tried = True
        if not hwp5tree.is_available():
            self._tree_failure = "pyhwp 파서 모듈(hwp5.xmlmodel)을 불러올 수 없음"
            return None
        try:
            md = hwp5tree.to_markdown(str(self.path))
        except Exception as exc:                 # noqa: BLE001 - 폴백이 있으므로 삼킨다
            # **기본 경로가 죽은 것**이므로 INFO 로 묻으면 안 된다. 예전에는
            # INFO 였고, 기본 로깅(WARNING)에서 보이지 않았다. 그래서 뒤이어
            # hwp5html 까지 실패했을 때 사람이 두 번째 실패만 보고 그것을
            # 원인으로 오해했다. 첫 실패가 진짜 원인이다.
            self._tree_failure = f"{type(exc).__name__}: {exc}"
            _log.warning(
                "hwp5-tree(기본 경로) 실패 — 폴백으로 내려갑니다: %s",
                self._tree_failure,
            )
            return None
        if not md or len(md.strip()) < _MIN_TREE_CHARS:
            chars = len(md.strip()) if md else 0
            self._tree_failure = (
                f"본문이 {chars}자뿐 (기준 {_MIN_TREE_CHARS}자) — 파싱은 됐으나 내용이 없음"
            )
            _log.warning(
                "hwp5-tree(기본 경로) 결과가 %d자뿐 — 폴백으로 내려갑니다", chars,
            )
            return None
        self._tree_cache = md
        return md

    @property
    def tree_failure(self) -> str | None:
        """기본 경로(hwp5-tree)가 실패한 사유.

        입력: 없음
        출력: 실패 사유 문자열. 기본 경로가 성공했으면 None
        비고:
            폴백 경로까지 실패했을 때, 사람에게 보여야 할 것은 **첫 실패**다.
            두 경로는 같은 pyhwp 파서를 공유하므로 대개 원인이 같다.
        """
        return getattr(self, "_tree_failure", None)

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
        except RuntimeError as exc:
            # hwp5html 이 0 이 아닌 코드로 끝난 경우. 이건 olefile 로 내려갈
            # **가장 강한 근거**다 — 그런데 예전에는 예외가 그대로 위로 튀어
            # 문서가 통째로 실패했다. olefile 로 본문은 건질 수 있는데도
            # 아무것도 못 건지던 자리다.
            # 두 경로는 같은 pyhwp 파서를 공유한다. 둘 다 실패했다면 원인이
            # 같을 가능성이 높으므로, 먼저 죽은 쪽(hwp5-tree)의 사유를 함께
            # 싣는다 — 그쪽이 진짜 원인에 가깝다.
            first = self.tree_failure
            self._fallback_reason = (
                f"hwp5html 실행 실패 — {_first_real_error(exc)}"
                + (f" / 앞서 hwp5-tree 도 실패: {first}" if first else "")
            )
            _log.warning(
                "hwp5html 이 실패해 olefile 폴백으로 내려갑니다 "
                "(표·그림 구조 손실): %s", self._fallback_reason,
            )
            self._ole_fallback = True
            return True
        self._html_cache = html
        self._html_stderr = stderr
        file_size = os.path.getsize(self.path)
        insufficient, reason = pyhwp_html_verdict(html, stderr, file_size)
        self._fallback_reason = reason
        if insufficient:
            if OLEFILE_AVAILABLE:
                _log.warning(
                    "pyhwp HTML 불충분 — olefile 폴백 사용 (표/그림 구조 손실): %s",
                    reason,
                )
                self._ole_fallback = True
            else:
                _log.warning(
                    "pyhwp HTML 불충분이나 olefile 미설치 — HTML 결과 사용: %s",
                    reason,
                )
                self._ole_fallback = False
        else:
            _log.debug("pyhwp HTML 사용: %s", reason)
            self._ole_fallback = False
        return self._ole_fallback

    @property
    def fallback_reason(self) -> str | None:
        """폴백 판정 사유.

        입력: 없음
        출력: 판정 문구. 아직 판정하지 않았으면 None
        비고: 트레이스·리포트에 남겨 왜 그 경로를 골랐는지 드러낸다.
        """
        return getattr(self, "_fallback_reason", None)

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
        report = diagnose(self.path)
        if not report.readable:
            # 조용히 빈 결과를 내면 배치에서 "성공했는데 내용 없음" 이 된다.
            raise ValueError(f"{Path(self.path).name}: {report.reason}")
        tree = self._get_tree_markdown()
        if tree is not None:
            return tree
        if self._uses_ole_fallback():
            return text_to_markdown(self._get_ole_text())
        return html_to_markdown(self._get_html())

    def table_html_fragments(self) -> list[str]:
        """원본 `<table>` HTML 조각을 문서 순서로 얻는다.

        입력: 없음
        출력: HTML 문자열 목록. HWPML·olefile 경로이거나 bs4 미설치면 빈 목록
        비고: 페이지 이미지가 없는 HWP 에서 표 재추출의 근거로 쓴다
        """
        if is_hwpml(self.path) or self._get_tree_markdown() is not None:
            return []
        if self._uses_ole_fallback():
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
        """변환 결과를 파일로 저장하고 완료를 알린다.

        입력: output_path — 저장 경로, fmt — 형식 (None 이면 확장자로 추정)
        출력: 없음 (파일 기록 + stdout 안내)
        """
        super().save(output_path, fmt)
        print(f"저장 완료: {output_path}  ({fmt or 'auto'})")
