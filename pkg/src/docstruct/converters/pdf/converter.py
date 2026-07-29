"""Docling 변환 실행과 결과 수집.

역할:
    PDF 를 DocumentConverter 로 변환하고, 변환 과정에서 로그로만 남는
    정보(페이지 단위 실패, 페이지별 텍스트 출처)를 호출부가 쓸 수 있게 모은다.
호출부:
    docstruct.extractors.registry._extract_pdf
    converters.registry (BaseConverter 인터페이스)
출력:
    DoclingDocument, 그리고 인스턴스 속성으로
    failed_pages(파싱 실패 페이지 번호), page_stats(페이지별 텍스트 출처)
"""
from __future__ import annotations

import logging

from docstruct.converters.base import BaseConverter
from docstruct.converters.deps import DOCLING_AVAILABLE
from docstruct.converters.pdf.picture_inspect import collect_picture_reports

_log = logging.getLogger(__name__)


def _log_picture_items(result) -> None:
    """그림 항목과 LLM 설명 결과를 로그로 남긴다.

    입력: result — 변환 결과
    출력: 없음
    """
    from docstruct.core.config import get_settings

    # 주의: build_pdf_pipeline_options()가 반환하는 객체는 그림 설명 옵션을
    # 설정하지 않으므로(그건 get_document_converter() 안에서만 적용됨),
    # 거기서 threshold를 읽으면 Docling 기본값이 나옵니다 — 실제 설정은
    # core.config 에서 직접 가져옵니다.
    threshold = get_settings().picture_area_threshold
    reports = collect_picture_reports(
        result.document,
        area_threshold=threshold,
    )
    if not reports:
        return
    for r in reports:
        if r["has_llm_description"]:
            _log.info(
                "Picture %s page=%s: LLM 설명 %d자",
                r["ref"],
                r["page"],
                len(r["description"]),
            )
        elif r["skipped_by_area"]:
            _log.warning(
                "Picture %s page=%s: area=%.4f < threshold=%.4f — LLM 스킵",
                r["ref"],
                r["page"],
                r["area_fraction"] or 0,
                threshold,
            )
        else:
            _log.warning(
                "Picture %s page=%s: LLM 설명 없음",
                r["ref"],
                r["page"],
            )


def _failed_page_numbers(result) -> list[int]:
    """파싱에 실패한 페이지 번호를 모은다.

    입력: result — Docling 변환 결과
    출력: 정렬된 페이지 번호 목록
    """
    pages: set[int] = set()
    for err in getattr(result, "errors", None) or []:
        page_no = getattr(err, "page_no", None)
        if isinstance(page_no, int) and page_no >= 0:
            pages.add(page_no)
    return sorted(pages)


def collect_page_stats(result) -> dict[int, dict]:
    """페이지별 텍스트 출처 통계를 모은다.

    입력: result — Docling 변환 결과
    출력: {페이지 번호: {cell_count, text_source, ocr_ratio}}
    동작: 각 텍스트 셀의 OCR 여부로 text_layer/ocr/mixed 를 구분한다.
          셀 데이터에 접근할 수 없으면 unmeasured 로 둔다 (파싱 실패와 구분).
    """
    stats: dict[int, dict] = {}

    raw_pages = getattr(result, "pages", None)
    if isinstance(raw_pages, dict):
        items = list(raw_pages.items())
    else:
        items = list(enumerate(raw_pages or [], start=1))

    for page_no, page in items:
        # 페이지 객체가 page_no 를 직접 들고 있으면 그것을 신뢰합니다.
        page_no = getattr(page, "page_no", page_no)

        # 중요: "셀 컨테이너에 접근 자체를 못 한 것"과 "셀이 실제로 0개인 것"은
        # 전혀 다릅니다. Docling 은 generate_parsed_pages=True 가 아니면 파싱된
        # 셀을 보관하지 않으므로, 이를 구분하지 않으면 정상 추출된 페이지를
        # "텍스트 없음"으로 오판하게 됩니다.
        cells = getattr(page, "cells", None)
        if cells is None:
            parsed = getattr(page, "parsed_page", None)
            cells = getattr(parsed, "textline_cells", None) or getattr(parsed, "cells", None)

        if cells is None:
            stats[int(page_no)] = {
                "cell_count": None,
                "text_source": "unmeasured",
                "ocr_ratio": None,
            }
            continue

        cells = list(cells)
        total = len(cells)
        ocr = 0
        known = 0
        for cell in cells:
            flag = getattr(cell, "from_ocr", None)
            if flag is None:
                continue
            known += 1
            if flag:
                ocr += 1

        if total == 0:
            # 셀이 0개라고 해서 텍스트가 없다는 뜻은 아닙니다. Docling 은
            # generate_parsed_pages 가 꺼져 있으면 파싱 후 셀을 버리는데,
            # 그때 컨테이너가 None 이 아니라 빈 리스트로 남기도 합니다.
            # 실제 본문 유무는 추출기가 교차 확인합니다.
            source, ratio = "unmeasured", None
        elif known == 0:
            source, ratio = "unmeasured", None
        elif ocr == 0:
            source, ratio = "text_layer", 0.0
        elif ocr == known:
            source, ratio = "ocr", 1.0
        else:
            source, ratio = "mixed", ocr / known

        stats[int(page_no)] = {
            "cell_count": total,
            "text_source": source,
            "ocr_ratio": ratio,
        }

    return stats


def _log_conversion_result(path: str, result) -> None:
    """변환 결과 요약을 로그로 남긴다.

    입력: path — 원본 경로, result — 변환 결과
    출력: 없음
    """
    status = getattr(result, "status", None)
    status_str = status.value if status is not None and hasattr(status, "value") else str(status)

    if result.has_errors():
        _log.warning(
            "Docling 변환 중 오류 (%d건): path=%s status=%s",
            len(result.errors),
            path,
            status_str,
        )
        for i, err in enumerate(result.errors, 1):
            _log.warning(
                "  [%d] category=%s module=%s page=%s msg=%s",
                i,
                err.category,
                err.module_name,
                err.page_no,
                err.error_message,
            )
    else:
        page_count = len(getattr(result, "pages", []) or [])
        _log.info(
            "Docling 변환 완료: path=%s status=%s pages=%d",
            path,
            status_str,
            page_count,
        )


def _docling_install_hint(executable: str) -> str:
    """docling 을 못 쓸 때의 원인별 안내 문구를 만든다.

    입력: executable — 실행 중인 파이썬 경로
    출력: 여러 줄 안내 문자열
    비고:
        docling 배포물에는 코드가 없다 (파일 7개짜리 메타패키지). 실제
        ``docling/`` 모듈은 의존성인 ``docling-slim`` 이 제공한다. 그래서
        ``pip show docling`` 은 성공하는데 ``import docling`` 은 실패하는
        상태가 생긴다 — 가장 흔한 원인이다.
    """
    from importlib.metadata import PackageNotFoundError, version

    lines = []
    try:
        core = version("docling-slim")
        lines.append(f"  docling-slim     : {core} (설치됨)")
    except PackageNotFoundError:
        lines.append("  docling-slim     : 없음  ← 실제 코드는 이 패키지에 들어 있습니다")
        lines.append(f'  해결             : "{executable}" -m pip install docling-slim')
        return "\n".join(lines)

    lines.append(f'  설치 확인        : "{executable}" -m pip show docling docling-slim')
    lines.append(f'  재설치           : "{executable}" -m pip install --force-reinstall docling')
    lines.append("  (다른 파이썬·가상환경에 설치된 경우도 흔합니다)")
    return "\n".join(lines)


class PdfConverter(BaseConverter):
    """PDF → text / markdown / html / xml (Docling Standard Pipeline)."""

    def __init__(self, path: str):
        super().__init__(path)
        self._document = None

    @property
    def source_format(self) -> str:
        return "pdf"

    def _ensure_docling(self) -> None:
        """docling 을 쓸 수 있는지 확인한다.

        입력: 없음
        출력: 없음 (쓸 수 없으면 ImportError)
        비고:
            "설치했는데 없다고 한다" 는 대부분 **다른 파이썬에 설치**한 경우다.
            어느 인터프리터로 실행 중인지와 실제 import 오류를 함께 알려준다.
        """
        if DOCLING_AVAILABLE:
            return

        import sys

        # 탐색(find_spec)은 실패해도 실제 import 가 되면 쓸 수 있다.
        # 노트북에서 설치 직후 커널을 재시작하지 않으면 이 상태가 된다.
        try:
            import docling  # noqa: F401
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
        else:
            _log.info(
                "docling 탐색은 실패했으나 import 는 성공 — 그대로 진행합니다 "
                "(설치 직후 커널을 재시작하지 않은 경우 흔합니다)"
            )
            return

        hint = _docling_install_hint(sys.executable)
        raise ImportError(
            "docling 을 불러올 수 없습니다.\n"
            f"  실행 중인 파이썬 : {sys.executable}\n"
            f"  import 시도 결과 : {reason}\n"
            f"{hint}"
        )

    def _get_document(self):
        if self._document is not None:
            return self._document

        self._ensure_docling()
        from docstruct.converters.pdf.docling_backend import get_document_converter

        converter = get_document_converter()
        result = converter.convert(self.path)
        _log_conversion_result(self.path, result)
        _log_picture_items(result)

        # 페이지 단위 실패는 예외가 아니라 로그로만 남아서, 결과에서 해당
        # 페이지가 조용히 빠집니다. 호출자가 알 수 있게 보관합니다.
        self.failed_pages = _failed_page_numbers(result)
        if self.failed_pages:
            _log.warning(
                "%d개 페이지가 파싱에 실패해 결과에서 빠집니다: %s "
                "(DOCLING_PDF_BACKEND=pypdfium2 또는 "
                "DOCLING_FORCE_FULL_PAGE_OCR=true 를 시도해 보세요)",
                len(self.failed_pages),
                self.failed_pages,
            )

        try:
            self.page_stats = collect_page_stats(result)
        except Exception as exc:   # 진단 정보 수집이 파싱을 막으면 안 됩니다
            _log.debug("페이지 통계 수집 실패 (무시): %s", exc)
            self.page_stats = {}

        self._document = result.document
        return self._document

    def to_html(self) -> str:
        return self._get_document().export_to_html()

    def to_text(self) -> str:
        return self._get_document().export_to_text()

    def to_markdown(self) -> str:
        from docstruct.converters.pdf.table_extract import export_markdown

        return export_markdown(self._get_document())

    def to_xml(self) -> str:
        return self._get_document().export_to_element_tree()
