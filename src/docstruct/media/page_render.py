"""PDF 페이지 → PNG 렌더.

역할:
    표 평가·재추출의 시각 근거로 쓸 페이지 이미지를 만든다.
    표가 있는 페이지만 렌더해 불필요한 파일 생성을 피한다.
호출부:
    docstruct.pipeline.build_document (PDF 이고 render_pages=True 일 때)
    docstruct.cli (safe_file_stem 만)
출력:
    {페이지 번호: PNG 경로} 및 실제 파일
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

_log = logging.getLogger(__name__)

DEFAULT_RENDER_SCALE = 2.0


def safe_file_stem(name: str) -> str:
    """파일명으로 쓸 수 있게 문자열을 정리한다.

    입력: name — 원본 파일명
    출력: 경로 구분자·특수문자를 제거한 stem
    """
    stem = Path(name).stem if name else "document"
    safe = re.sub(r"[^\w\-.]", "_", stem).strip("._")
    return safe or "document"


def render_page(
    pdf_path: str | Path,
    page_no: int,
    out_dir: str | Path,
    *,
    file_stem: str | None = None,
    scale: float = 2.0,
) -> str:
    """페이지 하나를 PNG 로 렌더한다.

    입력: pdf_path, page_no(1-based), out_path, scale
    출력: 저장된 경로. pypdfium2 미설치나 렌더 실패 시 None
    """
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise ImportError(
            "pypdfium2 패키지가 필요합니다: pip install pypdfium2"
        ) from exc

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        if page_no < 1 or page_no > len(pdf):
            raise ValueError(f"페이지 범위 초과: {page_no} (총 {len(pdf)}페이지)")

        page = pdf[page_no - 1]
        bitmap = page.render(scale=scale)
        img = bitmap.to_pil()
        stem = file_stem or safe_file_stem(Path(pdf_path).name)
        path = out / f"{stem}_page_{page_no}.png"
        img.save(path)
        return str(path.resolve())
    finally:
        pdf.close()


def render_pages_with_tables(
    pdf_path: str | Path,
    page_nos: list[int],
    out_dir: str | Path,
    *,
    file_stem: str | None = None,
    scale: float = 2.0,
) -> dict[int, str]:
    """표가 있는 페이지를 PNG 로 렌더한다.

    입력:
        pdf_path  원본 PDF 경로
        pages     PageContent 목록 (표 유무 판단에 사용)
        out_dir   저장 위치
        scale     렌더 배율 (1.0 = 72dpi)
    출력: {페이지 번호: PNG 경로}. 렌더 실패한 페이지는 포함되지 않음
    """
    stem = file_stem or safe_file_stem(Path(pdf_path).name)
    result: dict[int, str] = {}
    for page_no in sorted(set(page_nos)):
        try:
            result[page_no] = render_page(
                pdf_path, page_no, out_dir, file_stem=stem, scale=scale
            )
        except Exception as exc:
            _log.warning("페이지 %s 렌더링 실패: %s", page_no, exc)
    return result
