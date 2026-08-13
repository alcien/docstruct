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
import tempfile
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
    GPU_ACCELERATED, STAGE_ASSESS, STAGE_EXTRACT, STAGE_EXTRACT_MARKUP,
    STAGE_PICTURE_READ,
    STAGE_FILL, STAGE_RENDER, stage_extract,
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
    """포맷에 맞는 추출기를 찾아 실행한다 (실패 시 실제 형식으로 재시도).

    입력: path(문서 경로), fmt(pdf|hwp|hwpx), image_dir(이미지 저장 위치)
    출력: ExtractionResult (pages, failed_pages, table_html)
    예외: 재시도까지 실패하면 **처음 예외**를 그대로 올린다
    동작:
        ① 확장자가 가리키는 추출기로 시도한다.
        ② 실패하면 파일 내용(시그니처)으로 실제 형식을 알아보고, 확장자와
           다를 때만 그 추출기로 한 번 더 시도한다.
        ③ 그래도 실패하면 ①의 예외를 낸다.

        ②를 두는 이유: 실제 문서에서 이름만 `.hwpx` 이고 내용은 HWP
        바이너리인 파일이 있었다(한글에서 형식을 `한글 문서(*.hwp)` 로 둔
        채 파일명에 `.hwpx` 를 타이핑한 경우). 확장자만 믿으면 python-hwpx
        가 `BadZipFile` 을 내며 멈춘다.

        재시도가 실패하면 **처음 예외를 올린다.** 사용자가 넣은 형식 기준의
        오류가 원인에 가깝고, 재시도는 어디까지나 구제 시도이기 때문이다.
    """
    from docstruct.converters.signature import detect_format
    from docstruct.extractors.registry import get_extractor

    try:
        return get_extractor(f".{fmt}")(path, image_dir=image_dir)
    except Exception as first_error:
        actual = detect_format(path)
        if not actual or actual == fmt:
            raise
        _log.warning(
            "%s: %s 로 읽지 못했습니다 — 내용이 %s 형식이라 다시 시도합니다 (%s)",
            path.name, fmt.upper(), actual.upper(), first_error,
        )
        try:
            result = get_extractor(f".{actual}")(path, image_dir=image_dir)
        except Exception:
            raise first_error from None
        _log.warning(
            "%s: %s 형식으로 처리했습니다. 확장자(.%s)와 내용이 다릅니다 — "
            "한글에서 '다른 이름으로 저장' 시 파일 형식을 확인하세요.",
            path.name, actual.upper(), fmt,
        )
        return result


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
    split_chars: int = 0,
    assess_tables: bool = True,
    fill_tables: bool = True,
    fill_all: bool = False,
    read_pictures: bool = True,
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
        read_pictures 텍스트 레이어가 없는 그림을 VLM 으로 읽을지
                      (캡처 이미지로 붙인 표·조직도 복원)
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
    # out_dir 을 주지 않아도 그림·페이지 PNG 는 저장한다. 저장하지 않으면
    # preview 와 document.md 가 아무것도 보여주지 못하고(파일 경로를 참조),
    # 표 재추출은 근거 이미지가 없어 통째로 무력화된다.
    # 임시 폴더에 두고 경로를 알려준다 — save()/to_json() 때 함께 옮겨진다.
    #
    # 임시 폴더는 PageDocument 가 사라질 때 같이 지운다 (아래 _bind_scratch).
    # 프로세스 종료까지 남겨두면 배치 N 건이 N 개의 폴더를 남긴다.
    scratch: Path | None = None
    if out_path:
        image_dir = out_path / "images"
    else:
        scratch = Path(tempfile.mkdtemp(prefix="docstruct-"))
        image_dir = scratch / "images"

    timings: dict[str, float] = {}   # 라벨은 STAGE_* 상수를 씁니다

    _log.info("추출 시작: %s (%s)", display_name, fmt)
    _t = time.perf_counter()
    extraction = _extract(resolved, fmt, image_dir)
    pages, failed_pages, table_html = (
        extraction.pages, extraction.failed_pages, extraction.table_html
    )
    timings[stage_extract(fmt)] = time.perf_counter() - _t

    # 페이지 경계가 없는 문서(HWP 등)를 구조 경계에서 나눈다.
    if split_chars > 0 and pages:
        from docstruct.split import split_document

        pages = split_document(pages, split_chars)
    _log.info("추출 완료: %d페이지, 표 %d개", len(pages), sum(len(p.tables) for p in pages))
    _warn_if_empty(display_name, pages)

    doc = PageDocument(
        filename=display_name,
        source_format=fmt,
        pages=pages,
        failed_pages=failed_pages,
    )

    if fmt == "pdf" and render_pages:
        # out_dir 이 없으면 임시 작업 폴더에 렌더한다. 여기서 건너뛰면
        # 재추출이 근거 이미지를 못 찾아 조용히 무력화된다.
        pages_dir = (out_path / "pages") if out_path else (scratch / "pages")  # type: ignore[operator]
        _t = time.perf_counter()
        _render_page_images(resolved, pages, pages_dir, scale=render_scale)
        timings[STAGE_RENDER] = time.perf_counter() - _t

    if read_pictures and any(p.images for p in pages):
        from docstruct.media.vlm_read import read_picture_regions

        t0 = time.perf_counter()
        count = read_picture_regions(pages, progress=progress)
        if count:
            timings[STAGE_PICTURE_READ] = time.perf_counter() - t0
            _log.info("그림 %d개의 내용을 VLM 으로 읽었습니다", count)

    if assess_tables and any(p.tables for p in pages):
        # LLM 이 없으면 assess_document 는 모든 표를 table/sufficient 로
        # 기본 표시만 하고 끝난다. 그것을 "LLM 판정 완료" 로 기록하면 결과
        # JSON 이 거짓말을 한다 — 판정한 적 없는 212개 표가 전부 sufficient
        # 로 남아 사람이 품질을 확인했다고 오해한다. 여기서 미리 갈라 둔다.
        from docstruct.infrastructure.llm.client import llm_available
        from docstruct.tables.assess import UNASSESSED_REASON

        llm_on = llm_available()
        _log.info("표 품질 평가 중..." if llm_on
                  else "LLM 미설정 — 표 판정을 건너뜁니다 (모두 기본값 처리)")
        t0 = time.perf_counter()
        assess_document(pages, progress=progress)
        timings[STAGE_ASSESS] = time.perf_counter() - t0
        elapsed = (time.perf_counter() - t0) * 1000 / max(
            sum(1 for p in pages if p.tables), 1
        )
        for page in pages:
            if not page.tables:
                continue
            # `llm_available()` 만 보면 부족하다 — 엔드포인트가 설정돼 있어도
            # 사내망 밖이라 연결이 안 되면 역시 판정이 안 된다. 실제 결과를
            # 보고 판단한다.
            unassessed = [t for t in page.tables if t.reason == UNASSESSED_REASON]
            if unassessed:
                page.trace.add(
                    "docstruct.tables.assess", "표 판정 생략",
                    f"LLM 응답 없음(미설정 또는 연결 불가) — {len(unassessed)}개를 "
                    "기본값(table/sufficient)으로 표시했을 뿐, 품질을 확인한 것이 "
                    "아닙니다",
                    status="skip",
                )
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
                    pending = [t for t in page.tables if t.needs_fill]
                    detail = "; ".join(
                        f"{t.id}({t.quality}): {t.reason or '사유 없음'}"
                        for t in pending[:5]
                    )
                    if len(pending) > 5:
                        detail += f" … 외 {len(pending) - 5}건"
                    page.trace.add(
                        "docstruct.tables.fill", "재추출 불가",
                        "근거 부재(페이지 이미지·원본 표 HTML 모두 없음) — "
                        f"{detail}",
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
                    "품질 sufficient — LLM 호출 없음"
                    if not any(t.reason == UNASSESSED_REASON for t in page.tables)
                    else "LLM 응답 없음 — 판정 자체를 못 해 재추출 대상이 없음",
                    status="skip",
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
    if scratch is not None:
        _bind_scratch(doc, scratch)
    return doc


def _bind_scratch(doc: PageDocument, scratch: Path) -> None:
    """임시 작업 폴더를 문서 수명에 묶는다.

    입력: doc — 결과 문서, scratch — 지울 임시 폴더
    출력: 없음 (문서가 회수될 때 폴더 삭제)
    비고:
        out_dir 없이 실행하면 그림·페이지 PNG 가 이 폴더에 남는다. 문서가
        살아 있는 동안은 preview 와 save() 가 그 경로를 읽으므로 지우면
        안 되고, 문서가 사라진 뒤에는 아무도 안 쓰므로 남기면 안 된다.
        weakref.finalize 는 그 두 시점을 정확히 맞춰 준다.
    """
    import shutil
    import weakref

    # 저장 시 "이 폴더 안의 파일만" 이관하도록 위치를 남긴다.
    # out_dir 을 준 실행에서는 이 속성이 없으므로 이관도 일어나지 않는다.
    doc.scratch_dir = str(scratch)
    weakref.finalize(doc, shutil.rmtree, scratch, True)


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


#: 본문이 이보다 적으면 추출이 사실상 실패한 것으로 본다.
_EMPTY_THRESHOLD = 50


def _warn_if_empty(name: str, pages: list[PageContent]) -> None:
    """내용이 사실상 비었으면 눈에 띄게 알린다.

    입력: name — 파일 이름, pages — 추출 결과
    출력: 없음 (경고 로그)
    비고:
        예외가 없으면 배치는 "성공" 으로 셉니다. 그런데 본문이 비어 있으면
        실패 목록에도 안 뜨고 JSON 만 텅 빈 채로 남습니다. 배포용 문서나
        파서가 조용히 실패한 경우가 그렇습니다 — 여기서 잡아 둡니다.
    """
    chars = sum(len(p.content or "") for p in pages)
    tables = sum(len(p.tables) for p in pages)
    if chars >= _EMPTY_THRESHOLD or tables:
        return
    _log.warning(
        "%s: 본문이 %d자뿐입니다 — 추출에 실패했을 수 있습니다. "
        "배포용(DRM) 문서이거나 파서가 내용을 읽지 못한 경우입니다",
        name, chars,
    )


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
