"""문서 → PageDocument 변환 파이프라인.

역할:
    파일 하나를 받아 포맷별 추출 → 페이지 렌더 → 표 평가 → 표 재추출 →
    정규화 순으로 처리하고, 단계별 소요 시간과 처리 경로를 기록한다.
    포맷 분기는 하지 않으며 extractors.registry 에 위임한다.
호출부:
    docstruct.cli        CLI 실행
    docstruct/__init__   build_document 로 재노출 (노트북에서 사용)
출력:
    PageDocument — pages[].trace 에 처리 경로, timings 에 단계별 초,
    pipeline 에 적용 설정이 채워진 상태
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from docstruct.core.config import get_settings, resolve_device
from docstruct.media.page_render import render_pages_with_tables, safe_file_stem
from docstruct.models import PageContent, PageDocument
from docstruct.tables.assess import assess_document
from docstruct.tables.fill import process_tables
from docstruct.tables.tags import normalize_table_blocks

_log = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = (".hwp", ".hwpx", ".pdf")
# ↑ registry.supported_suffixes() 와 동일해야 합니다 (import 순서상 상수로 유지,
#   docstruct/__init__ 의 자가 검증이 불일치를 잡습니다).

from docstruct.models import (  # noqa: F401  (하위호환 재노출)
    GPU_ACCELERATED, STAGE_ASSESS, STAGE_EXTRACT, STAGE_FILL, STAGE_RENDER,
)


def source_format(path: Path) -> str:
    """확장자로 문서 형식을 판별한다.

    입력: path — 문서 경로
    출력: 'pdf' | 'hwp' | 'hwpx'
    예외: 미지원 확장자면 ValueError
    """
    ext = path.suffix.lower()
    if ext in SUPPORTED_SUFFIXES:
        return ext.lstrip(".")
    raise ValueError(
        f"지원하지 않는 형식: {ext!r} (지원: {', '.join(SUPPORTED_SUFFIXES)})"
    )


def _extract(path: Path, fmt: str, image_dir: Path | None):
    """포맷에 맞는 추출기를 찾아 실행한다.

    입력: path(문서 경로), fmt(pdf|hwp|hwpx), image_dir(이미지 저장 위치)
    출력: ExtractionResult (pages, failed_pages, table_html)
    """
    from docstruct.extractors.registry import get_extractor

    return get_extractor(f".{fmt}")(path, image_dir=image_dir)


def _render_page_images(
    pdf_path: Path,
    pages: list[PageContent],
    out_dir: Path,
    *,
    scale: float = 2.0,
) -> None:
    """표가 있는 페이지를 PNG 로 렌더하고 경로를 기록한다.

    입력:
        pdf_path  원본 PDF 경로
        pages     PageContent 목록 (표가 있는 페이지만 렌더)
        out_dir   저장 위치
        scale     렌더 배율
    출력: 없음 (page.page_image_path 설정, trace 에 단계 기록)
    비고:
        렌더 결과는 표 평가·재추출의 시각 근거로 쓰인다. pypdfium2 가 없거나
        렌더에 실패하면 경고만 남기고 텍스트 기반으로 진행한다.
    """
    from docstruct.media.page_render import render_pages_with_tables, safe_file_stem

    targets = [p.page_no for p in pages if p.tables and isinstance(p.page_no, int)]
    if not targets:
        return

    try:
        rendered = render_pages_with_tables(
            pdf_path,
            targets,
            out_dir,
            file_stem=safe_file_stem(pdf_path.name),
            scale=scale,
        )
    except Exception as exc:
        _log.warning("페이지 렌더 실패 — 텍스트만으로 평가합니다: %s", exc)
        rendered = {}

    for page in pages:
        img = rendered.get(page.page_no)
        if img:
            page.page_image_path = img
            page.trace.rendered = True
            page.trace.add(
                "docstruct.media.page_render",
                "페이지 PNG 렌더",
                f"pypdfium2 · {scale}x (표 평가·재추출의 시각 근거)",
            )
        elif page.tables:
            page.trace.add(
                "docstruct.media.page_render",
                "페이지 렌더 실패",
                "이미지 없이 텍스트만으로 평가 — 정확도 하락",
                status="warn",
            )


def build_document(
    path: str | Path,
    *,
    assess_tables: bool = True,
    fill_tables: bool = True,
    fill_all: bool = False,
    render_pages: bool = True,
    out_dir: str | Path | None = None,
    render_scale: float = 2.0,
    source_filename: str | None = None,
    progress: bool = False,
) -> PageDocument:
    """문서 파일 하나를 구조화한다.

    입력:
        src           문서 경로 (.pdf | .hwp | .hwpx)
        out_dir       산출물 디렉터리. None 이면 렌더·이미지 저장 생략
        assess_tables LLM 표 판정 수행 여부
        fill_tables   판정 결과에 따른 표 재추출 수행 여부
        fill_all      quality 와 무관하게 모든 표 재추출
        render_pages  페이지 PNG 렌더 여부 (PDF 만 해당)
        render_scale  렌더 배율
        progress      단계별 진행 막대 표시 여부
    출력:
        PageDocument
    부수효과:
        out_dir/pages/*.png, out_dir/images/*.png 생성
    """
    get_settings()   # .env 로드 + 설정 확정 (최초 1회, 이후 캐시)

    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        if resolved.is_dir():
            # 폴더를 주는 실수가 잦다. 어디로 가야 하는지 알려준다.
            raise IsADirectoryError(
                f"폴더가 주어졌습니다: {resolved}\n"
                "  build_document / DocStruct 는 문서 하나만 처리합니다.\n"
                "  폴더는 DocStructBatch 를 쓰세요.\n"
                "\n"
                "    from docstruct import DocStructBatch\n"
                f"    DocStructBatch({str(resolved)!r}, pattern='*.pdf').run()\n"
                "\n"
                "  CLI 라면 그대로 폴더를 주면 됩니다.\n"
                f"    docstruct {resolved.name}/ --glob '*.pdf'"
            )
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {resolved}")

    fmt = source_format(resolved)
    display_name = (source_filename or resolved.name).strip() or resolved.name

    out_path = Path(out_dir).resolve() if out_dir is not None else None
    image_dir = out_path / "images" if out_path else None

    timings: dict[str, float] = {}   # 라벨은 STAGE_* 상수를 씁니다

    _log.info("추출 시작: %s (%s)", display_name, fmt)
    _t = time.perf_counter()
    extraction = _extract(resolved, fmt, image_dir)
    pages, failed_pages, table_html = (
        extraction.pages, extraction.failed_pages, extraction.table_html
    )
    timings[STAGE_EXTRACT] = time.perf_counter() - _t
    _log.info("추출 완료: %d페이지, 표 %d개", len(pages), sum(len(p.tables) for p in pages))

    doc = PageDocument(
        filename=display_name,
        source_format=fmt,
        pages=pages,
        failed_pages=failed_pages,
    )

    if fmt == "pdf" and render_pages and out_path is not None:
        _t = time.perf_counter()
        _render_page_images(
            resolved, pages, out_path / "pages", scale=render_scale
        )
        timings[STAGE_RENDER] = time.perf_counter() - _t

    if assess_tables and any(p.tables for p in pages):
        _log.info("표 품질 평가 중...")
        t0 = time.perf_counter()
        assess_document(pages, progress=progress)
        timings[STAGE_ASSESS] = time.perf_counter() - t0
        elapsed = (time.perf_counter() - t0) * 1000 / max(
            sum(1 for p in pages if p.tables), 1
        )
        for page in pages:
            if not page.tables:
                continue
            page.trace.assessed = True
            verdicts = ", ".join(
                f"{t.id}:{t.content_type or '?'}"
                + (f"/{t.quality}" if t.quality else "")
                for t in page.tables
            )
            page.trace.add(
                "docstruct.tables.assess", "LLM 표 판정", verdicts,
                duration_ms=elapsed,
            )

        before = {id(t): t.markdown for p in pages for t in p.tables}
        targets = [t.id for p in pages for t in p.tables if t.needs_fill]

        t0 = time.perf_counter()
        process_tables(
            pages, fill_tables=fill_tables, fill_all=fill_all,
            table_html=table_html, progress=progress,
        )
        timings[STAGE_FILL] = time.perf_counter() - t0
        fill_elapsed = (time.perf_counter() - t0) * 1000

        for page in pages:
            page.trace.refilled = [
                t.id for t in page.tables
                if t.was_filled and before.get(id(t)) != t.markdown
            ]
            if page.trace.refilled:
                basis = "페이지 이미지" if page.page_image_path else "원본 표 HTML"
                page.trace.add(
                    "docstruct.tables.fill", "LLM 표 재추출",
                    f"{', '.join(page.trace.refilled)} 교체 ({basis} 근거)",
                    duration_ms=fill_elapsed / max(len(pages), 1),
                )
            elif fill_tables and any(t.id in targets for t in page.tables):
                # 재추출은 페이지 이미지를 근거로 삼습니다. 이미지가 없으면
                # 애초에 호출조차 하지 않으므로 사유를 구분해 남깁니다.
                if not page.page_image_path:
                    page.trace.add(
                        "docstruct.tables.fill", "재추출 불가",
                        "재추출 근거 없음 — 페이지 이미지(PDF)도 원본 표 HTML(HWP)도 "
                        "확보되지 않음",
                        status="warn",
                    )
                else:
                    page.trace.add(
                        "docstruct.tables.fill", "재추출 시도했으나 미교체",
                        "LLM 응답이 비었거나 요청 실패", status="warn",
                    )
            elif fill_tables and page.tables:
                page.trace.add(
                    "docstruct.tables.fill", "재추출 생략",
                    "품질 sufficient — LLM 호출 없음", status="skip",
                )
    else:
        if not assess_tables:
            _log.info("표 평가 생략 (--no-llm 또는 --no-assess)")
            for page in pages:
                if page.tables:
                    page.trace.add(
                        "docstruct.tables.assess", "표 판정 생략",
                        "LLM 미사용 — 원본 파싱 결과 그대로", status="skip",
                    )

    for page in pages:
        page.content = normalize_table_blocks(page.content or "")
        page.trace.add("docstruct.tables.tags", "표 블록 정규화", "<table N> 태그 정리")
        page.trace.table_count = len(page.tables)
        page.trace.picture_count = len(page.images)
        if not (page.content or "").strip():
            page.trace.failed = True
            page.trace.notes.append("본문이 비어 있음")

    doc.pipeline = _pipeline_settings(fmt, assess_tables, fill_tables, fill_all)
    doc.timings = {k: round(v, 2) for k, v in timings.items()}
    _log_timings(doc.timings)
    return doc


def _log_timings(timings: dict[str, float]) -> None:
    """단계별 소요 시간을 비중과 함께 로그로 남긴다.

    입력: timings — 단계명 → 초
    출력: 없음 (INFO 로그)
    """
    total = sum(timings.values())
    if total <= 0:
        return
    _log.info("── 단계별 소요 시간 (총 %.1f초) ──", total)
    for label, seconds in sorted(timings.items(), key=lambda kv: -kv[1]):
        _log.info("   %-32s %6.1f초  %4.0f%%", label, seconds, seconds / total * 100)


def _pipeline_settings(
    fmt: str, assess_tables: bool, fill_tables: bool, fill_all: bool
) -> dict:
    """이 실행에 적용된 설정 스냅샷을 만든다.

    입력: fmt, assess_tables, fill_tables, fill_all 과 전역 설정
    출력: dict — pdf_backend, ocr_backend, llm_model 등 (document.json 의 pipeline)
    """
    settings = get_settings()
    info: dict = {"source_format": fmt}

    if fmt == "pdf":
        info.update(
            pdf_backend=settings.pdf_backend,
            ocr_backend=settings.ocr_backend,
            force_full_page_ocr=settings.force_full_page_ocr,
            code_formula_enrichment=settings.code_formula_enrichment,
            picture_description=(
                settings.docling_picture.model if settings.docling_picture else None
            ),
        )

    info.update(
        assess_tables=assess_tables,
        fill_tables=fill_tables,
        fill_all=fill_all,
        llm_model=settings.llm.model if settings.llm else None,
        llm_url=settings.llm.url if settings.llm else None,
        # 성능 관련 값도 남긴다. 나중에 "왜 느렸나" 를 볼 때 필요하다.
        llm_concurrency=settings.llm_concurrency,
        llm_fallback_model=(
            settings.llm_fallback.model if settings.llm_fallback else None
        ),
        device=resolve_device()[0],
        num_threads=settings.num_threads or None,
    )
    return info
