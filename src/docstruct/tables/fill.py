"""판정 결과에 따른 표 후처리.

역할:
    assess 가 매긴 content_type / quality 를 소비해 표를 처리한다.
      text   `<table N>` 래퍼를 벗겨 본문으로 되돌림
      image  표를 이미지 placeholder 로 바꾸고 메타를 남김
      table  품질이 나쁘면 LLM 으로 재추출
    재추출 근거는 페이지 이미지(PDF) 를 우선하고, 없으면 원본 표 HTML(HWP) 을 쓴다.
호출부:
    docstruct.pipeline.build_document (assess_document 이후)
출력:
    없음 (page.content, page.tables, page.images, TableInfo.markdown 을 제자리 갱신)
"""
from __future__ import annotations

import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, NamedTuple

from docstruct.core.config import get_settings

from docstruct.media.images import encode_image_file
from docstruct.progress import ProgressBar
from docstruct.models import IMAGE, TABLE, TEXT, ImageInfo, PageContent, TableInfo
from docstruct.tables.tags import (
    block_span,
    extract_block_markdown,
    normalize_table_blocks,
    page_context_slice,
    replace_block_with_markdown,
    sync_table_block,
)
from docstruct.infrastructure.llm.client import invoke_llm, llm_api_config, llm_available

_log = logging.getLogger(__name__)

_FILL_PROMPT = """\
목표:
하단의 마크다운 파싱결과와 첨부 페이지 이미지를 토대로 <table {table_num}> ({title})를 GFM(GitHub Flavored Markdown) 표로 파싱하세요.

[마크다운 파싱]
{context_markdown}
{region_text}
규칙:
- <table {table_num}>에 해당하는 표만 GFM 표로 출력하세요. 같은 페이지의 다른 표는 무시하세요.
- [중요] 표가 페이지 경계에서 잘려 있으면 해당 방향에 `<-- continue-->` 한 줄을 추가하세요.
- 병합셀이 있다면 빈칸으로 남겨주세요.
- 표 외 다른 텍스트 출력 금지.
"""

#: 그림에서 승격된 표에만 붙는다. PDF 텍스트 레이어의 원문이라 글자가 정확하다.
#: 다만 좌우 열이 뒤섞여 나오므로 구조는 이미지로 판단해야 한다.
_REGION_TEXT_BLOCK = """
[영역 원문 — PDF 텍스트 레이어에서 그대로 추출]
이 표는 레이아웃 인식에서 그림으로 잘못 분류되어 파싱 결과가 비어 있습니다.
아래는 해당 영역의 원문입니다. **글자는 이 원문을 그대로 쓰고**(OCR 오독 방지),
행·열 구조는 첨부 이미지를 보고 판단하세요. 원문은 좌우 열이 뒤섞여 있을 수 있습니다.

{text}
"""


_FILL_FROM_HTML_PROMPT = """\
목표:
아래 원본 HTML 표와 현재 markdown 변환 결과를 비교해, <table {table_num}> ({title})를
올바른 GFM(GitHub Flavored Markdown) 표로 다시 작성하세요.

[원본 HTML] — rowspan/colspan 이 구조의 정답입니다
{table_html}

[현재 markdown 변환 결과] — 이것이 잘못되었습니다
{current_markdown}

[판정 사유]
{reason}

규칙:
- 원본 HTML 의 rowspan/colspan 을 근거로 행·열을 정확히 복원하세요.
- 다단 헤더는 상위 헤더를 하위에 병합해 한 줄로 만드세요 (예: `2023년 예산`).
- 병합셀의 반복 값은 좌상단 셀에만 두고 나머지는 빈칸으로 두세요.
- 표 외 다른 텍스트 출력 금지.
"""


def _strip_fences(text: str) -> str:
    """LLM 응답의 코드펜스를 제거한다.

    입력: text — LLM 원문
    출력: ```markdown 등의 펜스를 벗긴 문자열
    """
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:markdown|gfm|md)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text.strip())
    return text.strip()


def _unwrap_text_table(page: PageContent, table: TableInfo) -> None:
    """표가 아닌 것으로 판정된 블록의 래퍼를 벗긴다.

    입력: page, table
    출력: 없음 (page.content 에서 `<table N>` 태그를 제거하고 내용만 남김)
    """
    inner = extract_block_markdown(page.content, table.table_num)
    if not inner:
        inner = (table.markdown or "").strip()
    page.content = replace_block_with_markdown(page.content, table.table_num, inner)


def _convert_group_to_image(
    page: PageContent,
    group_ids: list[str],
) -> tuple[ImageInfo | None, set[str]]:
    """이미지로 판정된 표 그룹을 이미지 placeholder 로 바꾼다.

    입력: page, group_ids — 한 이미지로 묶일 표 id 목록
    출력:
        ImageInfo  생성된 이미지 메타 (호출자가 page.images 에 추가). 실패 시 None
        set[str]   본문에서 제거된 표 id (page.tables 에서도 빼야 함)
    """
    if not group_ids:
        return None, set()

    table_map = {t.id: t for t in page.tables}
    first = table_map.get(group_ids[0])
    if first is None:
        return None, set()

    placeholder = f"<!-- img_from_{first.id} -->"

    span = block_span(page.content, first.table_num)
    if span:
        page.content = page.content[: span[0]] + placeholder + page.content[span[1]:]
    else:
        page.content = page.content.replace(first.placeholder, placeholder)

    for tid in group_ids[1:]:
        tbl = table_map.get(tid)
        if tbl is None:
            continue
        span = block_span(page.content, tbl.table_num)
        if span:
            page.content = page.content[: span[0]] + page.content[span[1]:]

    page.content = re.sub(r"\n{3,}", "\n\n", page.content).strip()

    _log.debug("image 그룹 변환: %s → %s (흡수: %s)", first.id, placeholder, group_ids[1:])

    absorbed = {tid for tid in group_ids if tid in table_map}

    return ImageInfo(
        id=f"img_from_{first.id}",
        placeholder=placeholder,
        description=first.llm_title or first.reason,
        image_path=page.page_image_path,
        mime_type="image/png" if page.page_image_path else None,
    ), absorbed


def _request_fill_from_html(
    table: TableInfo,
    cfg: dict[str, Any],
    table_html: str,
) -> str | None:
    """원본 HTML 을 근거로 표 markdown 을 요청한다.

    입력:
        table       대상 표
        cfg         LLM 설정
        table_html  원본 `<table>` HTML 조각
    출력: GFM markdown 문자열, 실패하거나 응답이 비면 None
    비고: 페이지 이미지가 없는 HWP 경로에서 쓴다. rowspan/colspan 이
          구조의 근거가 되며 OCR 오인식 위험이 없다.
    """
    prompt = _FILL_FROM_HTML_PROMPT.format(
        table_num=table.table_num,
        title=table.llm_title or "알 수 없음",
        table_html=table_html[:12000],
        current_markdown=table.markdown,
        reason=table.reason or "(사유 없음)",
    )

    try:
        raw = invoke_llm(prompt, span_name="table_fill_html", cfg=cfg)
    except Exception as exc:
        _log_fill_failure(table.id, exc, "HTML")
        return None

    md = _strip_fences(raw) if raw else ""
    if not md:
        _log.warning("표 재추출(HTML) 결과 비어 있음: id=%s", table.id)
        return None
    return md


def _region_text_block(
    table: TableInfo,
    images: list[ImageInfo] | None,
) -> str:
    """승격된 표라면 원본 영역의 PDF 원문을 프롬프트 블록으로 만든다.

    입력:
        table   대상 표 (source_image_id 로 원본 그림을 찾는다)
        images  같은 페이지의 그림 목록
    출력: 프롬프트에 끼울 문자열. 해당 없으면 빈 문자열
    비고:
        그림에서 승격된 표는 markdown 파싱 결과가 비어 있어 이미지만으로
        재추출해야 한다. PDF 텍스트 레이어 원문을 함께 주면 글자 오독을
        막을 수 있다 — 구조는 이미지, 글자는 원문이라는 역할 분담이다.
    """
    if not table.source_image_id or not images:
        return ""
    for info in images:
        if info.id == table.source_image_id and info.region_text:
            return _REGION_TEXT_BLOCK.format(text=info.region_text)
    return ""


def _request_fill_with_image(
    page_content: str,
    table: TableInfo,
    cfg: dict[str, Any],
    page_image_b64: str,
    images: list[ImageInfo] | None = None,
) -> str | None:
    """페이지 이미지를 근거로 표 markdown 을 요청한다.

    입력:
        page_content    본문 스냅샷 (표 주변 컨텍스트 추출용)
        table           대상 표
        cfg             LLM 설정
        page_image_b64  페이지 PNG 의 base64
    출력: GFM markdown 문자열, 실패하거나 응답이 비면 None
    비고: 아무것도 수정하지 않는다. 반영은 _apply_fill 이 한다.
    """
    context = page_context_slice(page_content, table.table_num)
    prompt = _FILL_PROMPT.format(
        table_num=table.table_num,
        title=table.llm_title or "알 수 없음",
        context_markdown=context,
        region_text=_region_text_block(table, images),
    )

    try:
        raw = invoke_llm(
            prompt,
            span_name="table_fill",
            image_urls=[f"data:image/png;base64,{page_image_b64}"],
            cfg=cfg,
        )
    except Exception as exc:
        _log_fill_failure(table.id, exc, "이미지")
        return None

    md = _strip_fences(raw) if raw else ""
    if not md:
        _log.warning("표 재추출 결과 비어 있음: id=%s", table.id)
        return None
    return md


def _log_fill_failure(table_id: str, exc: Exception, basis: str) -> None:
    """표 재추출 실패를 로그에 남긴다.

    입력: table_id, exc — 발생한 예외, basis — 근거 종류(이미지/HTML)
    출력: 없음
    비고: 연결 불가는 클라이언트가 이미 한 번 알렸으므로 짧게만 남긴다.
    """
    from docstruct.infrastructure.llm.client import LLMUnreachableError

    if isinstance(exc, LLMUnreachableError):
        _log.debug("표 재추출 생략 (LLM 연결 불가): id=%s", table_id)
    else:
        _log.warning("표 재추출(%s) 실패: id=%s err=%s", basis, table_id, exc)


class FillJob(NamedTuple):
    """재추출 작업 하나.

    입력(필드):
        page     대상 페이지
        table    대상 표
        kind     'image' | 'html' — 재추출 근거의 종류
        payload  kind='image' 면 본문 스냅샷(컨텍스트용),
                 kind='html' 면 원본 `<table>` HTML 조각
    출력:
        _run() 이 이 값을 받아 LLM 을 호출한다.
    """

    page: PageContent
    table: TableInfo
    kind: str          # "image" | "html"
    payload: str


class _ImageCache:
    """페이지 이미지 base64 캐시.

    입력: get(path) — 페이지 PNG 경로
    출력: base64 문자열, 인코딩 실패 시 None
    동작: 같은 경로는 한 번만 인코딩한다. 여러 스레드가 동시에 요청해도
          중복 인코딩이 일어나지 않는다.
    """

    def __init__(self) -> None:
        self._store: dict[str, str | None] = {}
        self._lock = threading.Lock()

    def get(self, path: str) -> str | None:
        # 인코딩을 락 안에서 합니다. 락 밖에서 하면 같은 키로 동시에 miss 한
        # 스레드들이 전부 인코딩해 중복이 그대로 남습니다. 인코딩은 짧고
        # (밀리초), 병렬화의 목적은 LLM I/O 이므로 직렬화해도 손해가 없습니다.
        """경로의 base64 를 얻는다 (없으면 인코딩 후 보관).

        입력: path — 이미지 파일 경로
        출력: base64 문자열 또는 None
        """
        with self._lock:
            if path not in self._store:
                encoded = encode_image_file(path)
                self._store[path] = encoded[1] if encoded else None
            return self._store[path]


def _apply_fill(page: PageContent, table: TableInfo, md: str) -> None:
    """재추출 결과를 표와 본문에 반영한다.

    입력: page, table, md(새 markdown)
    출력: 없음 (original_markdown 에 원본 보존, markdown 교체, 본문 블록 동기화)
    비고: 단일 스레드에서만 호출한다.
    """
    table.original_markdown = table.markdown   # 비교용으로 원본 보존
    table.markdown = md
    page.content = sync_table_block(page.content, table.table_num, md)
    _log.debug("표 재추출 완료: id=%s (%d자)", table.id, len(md))


def process_tables(
    pages: list[PageContent],
    *,
    fill_tables: bool = True,
    fill_all: bool = False,
    table_html: list[str] | None = None,
    progress: bool = False,
) -> None:
    """판정 결과에 따라 표를 후처리한다.

    입력:
        pages        PageContent 목록 (assess 완료 상태)
        fill_tables  False 면 LLM 재추출 없이 분류·정리만 수행
        fill_all     True 면 quality 와 무관하게 모든 표를 재추출
        table_html   원본 `<table>` HTML 조각, 문서 순서. HWP 재추출 근거
        progress     진행 막대 표시 여부
    출력: 없음 (제자리 갱신)
    동작: 분류·수집 → LLM 요청 병렬 실행 → 결과 반영 순으로 처리한다.
          요청과 반영을 분리해 같은 페이지의 표가 서로의 결과를 덮어쓰지 않게 한다.
    """
    # 엔드포인트가 없어도 로컬 VLM 이 있으면 재추출할 수 있다.
    can_call = fill_tables and llm_available()
    cfg = llm_api_config() if can_call else None

    # ── 1단계: text/image 분류 처리 + 재추출 대상 수집 (순차, LLM 없음) ──
    jobs: list[FillJob] = []

    for page in pages:
        if not page.tables:
            continue

        # 여기서는 **존재 여부만** 확인합니다. 인코딩까지 해버리면 2단계에서
        # 다시 인코딩하므로 페이지당 이미지 인코딩이 두 번 일어납니다.
        has_image = bool(
            fill_tables
            and page.page_image_path
            and Path(page.page_image_path).is_file()
        )

        done_groups: set[tuple[str, ...]] = set()
        absorbed: set[str] = set()   # 이미지 그룹에 흡수되어 본문에서 사라진 표
        remaining: list[TableInfo] = []
        pending: list[TableInfo] = []

        for table in page.tables:
            ctype = table.content_type or TABLE

            if ctype == TEXT:
                _unwrap_text_table(page, table)
                continue

            if ctype == IMAGE:
                ids = list(table.group_image_ids or [table.id])
                key = tuple(sorted(ids))
                if key not in done_groups:
                    done_groups.add(key)
                    info, taken = _convert_group_to_image(page, ids)
                    absorbed |= taken
                    if info is not None:
                        page.images.append(info)
                continue

            if fill_tables and (fill_all or table.needs_fill):
                pending.append(table)

            remaining.append(table)

        page.content = normalize_table_blocks(page.content)
        # 본문 블록이 사라진 표는 메타에서도 제거해 orphan을 남기지 않습니다.
        page.tables = [t for t in remaining if t.id not in absorbed]

        # 컨텍스트는 이 시점 본문으로 고정합니다 — 병렬 요청들이 서로의
        # 중간 수정 상태를 보지 않게 하기 위함입니다.
        snapshot = page.content
        for table in pending:
            if not can_call:
                _log.debug("LLM 미설정 — 재추출 스킵: id=%s", table.id)
                continue
            if has_image:
                jobs.append(FillJob(page, table, "image", snapshot))   # 우선순위 ①: PDF
            else:
                html_frag = None
                if table_html and 1 <= table.table_num <= len(table_html):
                    html_frag = table_html[table.table_num - 1]
                if html_frag:
                    jobs.append(FillJob(page, table, "html", html_frag))  # 우선순위 ②: HWP
                else:
                    # 사유를 함께 남긴다. 예전에는 id 만 찍혀서, 왜 이 표가
                    # 재추출 대상이 됐는지 결과 JSON 을 따로 열어봐야 알 수
                    # 있었다. 사유가 보이면 "정말 고쳐야 할 표인가" 를 그
                    # 자리에서 판단할 수 있다 — 실제로 병합 셀을 빈 칸으로
                    # 오해한 오탐이 섞여 있었다.
                    _log.warning(
                        "재추출 근거 없음 (페이지 이미지·원본 HTML 모두 부재): "
                        "id=%s · 품질=%s · 사유=%s",
                        table.id,
                        table.quality or "?",
                        table.reason or "(사유 없음)",
                    )

    if not jobs or not can_call:
        return

    # ── 2단계: LLM 요청만 병렬 실행 (부수효과 없음) ──────────────────
    image_cache = _ImageCache()

    def _run(job: FillJob) -> tuple[FillJob, str | None]:
        """재추출 요청 하나를 실행한다 (병렬 워커).

        입력: job — FillJob
        출력: (job, markdown 또는 None) — 반영은 3단계에서 한꺼번에 한다
        비고: 부수효과 없이 요청만 수행해, 병렬 실행이 페이지 상태를 서로
              덮어쓰지 않게 한다.
        """
        if job.kind == "html":
            return job, _request_fill_from_html(job.table, cfg, job.payload)
        b64 = image_cache.get(job.page.page_image_path or "")
        if b64 is None:
            return job, None
        return job, _request_fill_with_image(
            job.payload, job.table, cfg, b64, job.page.images
        )

    workers = min(get_settings().llm_concurrency, len(jobs))
    results: list[tuple[FillJob, str | None]] = []
    bar = ProgressBar(len(jobs), "표 재추출", unit="개", enabled=progress)

    try:
        if workers <= 1:
            for job in jobs:
                results.append(_run(job))
                bar.update(1, job.table.id)
        else:
            _log.info("표 재추출 %d건 · 동시 %d개", len(jobs), workers)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(_run, job) for job in jobs]
                for future in as_completed(futures):
                    try:
                        job, md = future.result()
                        results.append((job, md))
                        bar.update(1, job.table.id)
                    except Exception as exc:
                        _log.warning("표 재추출 작업 실패: %s", exc)
                        bar.update(1)
    finally:
        bar.close()

    # ── 3단계: 반영은 순차 (page.content 경합 방지) ──────────────────
    for job, md in results:
        if md:
            _apply_fill(job.page, job.table, md)

    for page in pages:
        if page.tables:
            page.content = normalize_table_blocks(page.content)
