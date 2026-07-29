"""포맷별 추출기 레지스트리.

역할:
    확장자를 추출기 함수에 매핑하고, 모든 추출기가 같은 반환 타입
    (ExtractionResult)을 쓰도록 강제한다. 새 포맷 지원은 이 파일에
    함수 하나를 추가하는 것으로 끝나며 파이프라인은 수정하지 않는다.
호출부:
    docstruct.pipeline._extract   get_extractor 로 조회 후 실행
    docstruct/__init__            supported_suffixes 로 상수 검증
출력:
    ExtractionResult — pages, failed_pages, table_html
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from docstruct.models import PageContent


@dataclass
class ExtractionResult:
    """추출기의 공통 반환 타입.

    입력(필드):
        pages         페이지 목록
        failed_pages  파싱 실패로 빠진 페이지 번호 (PDF 전용)
        table_html    원본 `<table>` HTML 조각, 문서 순서 (HWP 전용)
    출력:
        호출부는 포맷과 무관하게 같은 필드를 읽는다. 해당 없는 필드는 빈 값.
    """

    pages: list[PageContent]
    #: 파싱 실패로 결과에서 빠진 페이지 번호 (PDF 전용 — Docling 부분 실패)
    failed_pages: list[int] = field(default_factory=list)
    #: 원본 ``<table>`` HTML 조각, 문서 순서 (HWP 전용 — 재추출 근거)
    table_html: list[str] = field(default_factory=list)


class Extractor(Protocol):
    def __call__(self, path: Path, *, image_dir: Path | None) -> ExtractionResult: ...


_REGISTRY: dict[str, Extractor] = {}


def register_extractor(*suffixes: str) -> Callable[[Extractor], Extractor]:
    """확장자를 추출기 함수에 등록하는 데코레이터.

    입력: suffixes — ".pdf" 같은 확장자 (여러 개 가능)
    출력: 원본 함수를 그대로 반환 (_REGISTRY 에 등록)
    """

    def deco(fn: Extractor) -> Extractor:
        for suffix in suffixes:
            _REGISTRY[suffix.lower()] = fn
        return fn

    return deco


def get_extractor(suffix: str) -> Extractor:
    """확장자에 등록된 추출기를 찾는다.

    입력: suffix — ".pdf" 등 (대소문자 무관)
    출력: Extractor 함수
    예외: 미등록 확장자면 ValueError (지원 목록 포함)
    """
    try:
        return _REGISTRY[suffix.lower()]
    except KeyError:
        supported = ", ".join(sorted(_REGISTRY))
        raise ValueError(f"지원하지 않는 형식: {suffix} (지원: {supported})") from None


def supported_suffixes() -> tuple[str, ...]:
    """등록된 확장자 전체.

    입력: 없음
    출력: 정렬된 확장자 튜플 (예: ('.hwp', '.hwpx', '.pdf'))
    """
    return tuple(sorted(_REGISTRY))


# ── 등록 ---------------------------------------------------------------------


@register_extractor(".pdf")
def _extract_pdf(path: Path, *, image_dir: Path | None) -> ExtractionResult:
    """PDF 를 Docling 으로 추출한다.

    입력: path(PDF 경로), image_dir(그림 저장 위치)
    출력: ExtractionResult — pages, failed_pages
    """
    from docstruct.converters.pdf.converter import PdfConverter
    from docstruct.extractors.pdf import extract_pdf_pages

    converter = PdfConverter(str(path))
    doc = converter._get_document()
    return ExtractionResult(
        pages=extract_pdf_pages(
            doc,
            image_dir=image_dir,
            page_stats=getattr(converter, "page_stats", None),
        ),
        failed_pages=list(getattr(converter, "failed_pages", []) or []),
    )


@register_extractor(".hwp")
def _extract_hwp(path: Path, *, image_dir: Path | None) -> ExtractionResult:
    """HWP 를 추출한다 (HWPML XML / pyhwp HTML / olefile 텍스트 중 자동 선택).

    입력: path(HWP 경로), image_dir(사용하지 않음)
    출력: ExtractionResult — pages, table_html
    """
    from docstruct.extractors.hwp import extract_hwp_pages

    pages, table_html = extract_hwp_pages(str(path))
    return ExtractionResult(pages=pages, table_html=table_html)


@register_extractor(".hwpx")
def _extract_hwpx(path: Path, *, image_dir: Path | None) -> ExtractionResult:
    """HWPX 를 python-hwpx 로 추출한다.

    입력: path(HWPX 경로), image_dir(사용하지 않음)
    출력: ExtractionResult — pages
    """
    from docstruct.extractors.hwpx import extract_hwpx_pages

    return ExtractionResult(pages=extract_hwpx_pages(str(path)))
