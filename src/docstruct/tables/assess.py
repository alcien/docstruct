"""표 품질 판정 (LLM).

역할:
    페이지 본문과 (있으면) 페이지 이미지를 LLM 에 보내, 각 `<table N>` 이
    실제로 표인지(content_type) 와 파싱 품질이 쓸 만한지(quality)를 판정한다.
    판정만 하고 표 내용은 바꾸지 않는다.
호출부:
    docstruct.pipeline.build_document
출력:
    없음 (TableInfo 의 content_type, quality, llm_title, reason,
    group_image_ids 를 제자리에서 갱신)
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from docstruct.core.config import get_settings

from docstruct.media.images import encode_image_file
from docstruct.progress import ProgressBar
from docstruct.models import (
    IMAGE,
    INSUFFICIENT,
    SUFFICIENT,
    TABLE,
    TEXT,
    WRONG,
    PageContent,
    TableInfo,
)
from docstruct.infrastructure.llm.client import invoke_llm, llm_api_config, llm_available
from docstruct.infrastructure.llm.json_parse import parse_json_list_or_object_map

_log = logging.getLogger(__name__)

_ASSESS_PROMPT = """\
아래는 문서 한 페이지(또는 섹션)의 markdown입니다. `<table N> ... </table N>` 블록 안에 해당 표의 파싱 결과가 인라인으로 있습니다.

{content}

## 문제가 있는 표만 JSON 배열로 응답하세요. 충분한 표는 JSON에 포함하지 마세요.

### content_type 판단 (문제 있는 항목만):
- "table"  : 실제 표 — quality도 함께 기록
- "text"   : 표가 아니라 본문 텍스트
- "image"  : 표가 아니라 이미지/도표

### quality (content_type=table일 때만):
- "wrong"        : 표 구조·내용이 명백히 잘못 파싱됨
- "insufficient" : 불완전·빈 표·데이터 손실·페이지 분할 조각 등

### 각 항목 필드:
- "id"              : table ID (예: "table_1")
- "content_type"    : table | text | image
- "title"           : 표/도표 제목 (필수)
- "quality"         : wrong | insufficient (content_type=table일 때만)
- "group_image_ids" : image일 때 묶이는 table ID 목록, 없으면 null
- "reason"          : 판단 이유 (디버그용)

응답 형식 (JSON 배열만, 다른 텍스트 없음):
[
  {{"id": "table_2", "content_type": "table", "title": "...", "quality": "insufficient", "group_image_ids": null, "reason": "..."}},
  {{"id": "table_4", "content_type": "image", "title": "...", "group_image_ids": ["table_4", "table_5"], "reason": "..."}},
  {{"id": "table_7", "content_type": "text", "reason": "표 구조가 아닌 단락 텍스트"}}
]
"""

_VALID_CONTENT_TYPES = frozenset({TABLE, TEXT, IMAGE})
_VALID_QUALITIES = frozenset({WRONG, INSUFFICIENT})

#: assess 프롬프트에 넣을 페이지 본문 최대 길이 (컨텍스트 초과 방지)
MAX_ASSESS_CHARS = 20_000


def _mark_default(table: TableInfo) -> None:
    """문제없는 표로 표시한다.

    입력: table — TableInfo
    출력: 없음 (content_type=table, quality=sufficient)
    """
    table.content_type = TABLE
    table.quality = SUFFICIENT


def _apply_assessment(
    tables: list[TableInfo],
    assessment: list[dict[str, Any]],
) -> None:
    """LLM 판정 결과를 TableInfo 에 반영한다.

    입력:
        tables      페이지의 표 목록
        assessment  LLM 이 반환한 판정 목록 (id, content_type, quality, ...)
    출력: 없음 (제자리 갱신)
    동작: 판정에 없는 표는 문제없음으로 간주. 알 수 없는 content_type 도 마찬가지.
          content_type 이 table 인데 quality 가 없으면 insufficient 로 둔다.
    """
    assessment_map: dict[str, dict[str, Any]] = {
        item["id"]: item for item in assessment if item.get("id")
    }
    known_ids = {t.id for t in tables}

    for table in tables:
        info = assessment_map.get(table.id)
        if not info:
            _mark_default(table)
            continue

        content_type = (info.get("content_type") or "").strip().lower()
        if content_type not in _VALID_CONTENT_TYPES:
            _log.debug("알 수 없는 content_type=%r — 기본값 적용: %s", content_type, table.id)
            _mark_default(table)
            continue

        quality_raw = (info.get("quality") or "").strip().lower()
        quality = quality_raw if quality_raw in _VALID_QUALITIES else None

        group_raw = info.get("group_image_ids")
        group_ids: list[str] | None = None
        if isinstance(group_raw, list) and group_raw:
            group_ids = [str(g) for g in group_raw if g]

        table.llm_title = (info.get("title") or "").strip() or None
        table.content_type = content_type
        table.group_image_ids = group_ids
        table.reason = (info.get("reason") or "").strip() or None

        if content_type == TABLE:
            # 문제 있다고 지목했는데 quality를 안 준 경우 → 보수적으로 insufficient
            table.quality = quality or INSUFFICIENT
        else:
            table.quality = None

    for tid in assessment_map:
        if tid not in known_ids:
            _log.debug("LLM이 문서에 없는 table_id 반환: %s", tid)


def assess_page_tables(
    page: PageContent,
    *,
    cfg: dict[str, Any] | None = None,
) -> None:
    """페이지 하나의 표를 판정한다.

    입력:
        page  PageContent (content, tables, page_image_path 사용)
        cfg   LLM 설정. None 이면 전역 설정에서 가져옴
    출력: 없음 (page.tables 의 각 TableInfo 갱신)
    동작: LLM 미설정이거나 호출 실패 시 모든 표를 sufficient 로 표시한다.
    """
    if not page.tables:
        return
    if cfg is None:
        cfg = llm_api_config()
    if cfg is None and not llm_available():
        # 엔드포인트도 로컬 VLM 도 없으면 판정을 건너뛴다.
        _log.debug("LLM 미설정 — 표 평가 스킵")
        for table in page.tables:
            _mark_default(table)
        return

    content = page.content or ""
    if len(content) > MAX_ASSESS_CHARS:
        _log.warning(
            "%s페이지 본문이 %d자 — %d자로 잘라 평가합니다.",
            page.page_no,
            len(content),
            MAX_ASSESS_CHARS,
        )
        content = content[:MAX_ASSESS_CHARS]

    image_urls: list[str] | None = None
    if page.page_image_path:
        encoded = encode_image_file(page.page_image_path)
        if encoded:
            mime, b64 = encoded
            image_urls = [f"data:{mime};base64,{b64}"]

    try:
        raw = invoke_llm(
            _ASSESS_PROMPT.format(content=content),
            span_name="table_assess",
            image_urls=image_urls,
            cfg=cfg,
        )
        assessment = parse_json_list_or_object_map(raw)
    except Exception as exc:
        _log_page_failure(page.page_no, exc)
        assessment = []

    _apply_assessment(page.tables, assessment)


def _log_page_failure(page_no: object, exc: Exception) -> None:
    """페이지 평가 실패를 로그에 남긴다.

    입력: page_no — 페이지 번호, exc — 발생한 예외
    출력: 없음
    비고:
        연결 불가는 이미 클라이언트가 한 번 경고했으므로 여기서는
        짧게만 남긴다. 페이지마다 같은 스택을 반복 출력하지 않는다.
    """
    from docstruct.infrastructure.llm.client import LLMUnreachableError

    if isinstance(exc, LLMUnreachableError):
        _log.debug("%s페이지 평가 생략 (LLM 연결 불가)", page_no)
    else:
        _log.warning("%s페이지 평가 실패: %s", page_no, exc)


def assess_document(pages: list[PageContent], *, progress: bool = False) -> None:
    """문서 전체의 표를 판정한다.

    입력: pages — PageContent 목록, progress — 진행 막대 표시 여부
    출력: 없음 (각 TableInfo 갱신)
    동작: 표가 있는 페이지마다 LLM 1회 호출. 설정된 동시 실행 수만큼 병렬 처리하며,
          한 페이지가 실패해도 나머지는 계속 진행한다.
    """
    cfg = llm_api_config()
    targets = [page for page in pages if page.tables]
    if not targets:
        return

    workers = min(get_settings().llm_concurrency, len(targets))
    bar = ProgressBar(len(targets), "표 평가", unit="p", enabled=progress)

    try:
        if workers <= 1:
            for page in targets:
                assess_page_tables(page, cfg=cfg)
                bar.update(1, f"p.{page.page_no}")
            return

        _log.info("표 평가 %d페이지 · 동시 %d개", len(targets), workers)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(assess_page_tables, page, cfg=cfg): page for page in targets}
            for future in as_completed(futures):
                page = futures[future]
                try:
                    future.result()
                except Exception as exc:   # 한 페이지 실패가 전체를 막지 않도록
                    _log_page_failure(page.page_no, exc)
                bar.update(1, f"p.{page.page_no}")
    finally:
        bar.close()
