"""HWPX → markdown/HTML/XML/텍스트.

입력:
    .hwpx 경로

역할:
    python-hwpx 로 문서를 열어 목표 형식으로 변환한다.
호출부:
    converters.registry, docstruct.extractors.hwpx
출력:
    변환된 문자열
"""
from __future__ import annotations

import logging

from docstruct.converters.base import BaseConverter
from docstruct.converters.deps import HWPX_AVAILABLE


_log = logging.getLogger(__name__)

def _pipeline_markdown(path) -> str:
    """파이프라인을 돌려 사람이 볼 수 있는 markdown 을 만든다.

    입력: path — 원본 문서 경로
    출력: markdown 문자열
    예외: 실패하면 그대로 올린다 (호출부가 옛 경로로 폴백한다)
    비고:
        컨버터 자체 경로는 **원재료**다. 그것을 그대로 내보내면 뒤가 전부
        빠진다 — 한글 정규화·누름틀 잔재 제거·표 placeholder·그림 추출·
        VLM 판독·펼치기. PDF 는 여기에 더해 0.4.11 의 OCR 게이트 수정과
        0.4.13 의 텍스트 레이어 메우기도 못 받는다.

        서비스의 `/convert/markdown` 이 이 자리를 탄다. 실측(조달청 HWPX):
        document.json 에는 VLM 이 조직도를 계층·정원표까지 복원해 담겼는데
        같은 실행의 `.md` 에는 `<!-- hwpx-image:image1 -->` 원형 표식만
        남았다 — 컨버터가 파이프라인을 건너뛰었기 때문이다.
    """
    from docstruct.pipeline import build_document
    from docstruct.output.report import document_markdown

    doc = build_document(path, assess_tables=False, fill_tables=False)
    return document_markdown(doc)


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
        출력: markdown 문자열
        비고:
            XML 을 직접 읽는다. python-hwpx 의 내보내기는 같은 문서에서
            표 94개(원본 212), 셀 93.8% 였고 모든 텍스트에 취소선이
            4,456회 씌워졌다. 파일 자체에는 표 212개가 온전히 들어 있어
            손실은 내보내기 단계에서 생긴다.

            실패하면 python-hwpx 로 물러난다 — 아무것도 못 내는 것보다 낫다.
        """
        try:
            return _pipeline_markdown(self.path)
        except Exception as exc:                 # noqa: BLE001 - 폴백이 둘 있다
            _log.warning("파이프라인 markdown 실패 — 원재료로 물러납니다: %s", exc)

        from docstruct.converters.hwpx import hwpxtree

        try:
            return hwpxtree.to_markdown(self.path)
        except Exception as exc:                 # noqa: BLE001 - 폴백이 있다
            _log.warning(
                "HWPX XML 직접 파싱 실패 — python-hwpx 로 물러납니다: %s", exc
            )
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
