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
from docstruct.tables.tags import make_table_block, make_table_id, open_tag

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

### 빈 칸의 원인을 구분해서 기술하세요
markdown 표는 병합 셀(rowspan/colspan)을 표현하지 못합니다. 원본에서 여러 행에 걸친 셀은 **맨 윗행에만 값이 들어가고 아래 행은 빈 칸으로** 옮겨집니다. 그래서 빈 칸은 두 가지 원인이 있습니다.

1. **원본이 정말 비어 있음** — 아직 값이 없는 항목
2. **병합 셀이 풀리면서 생김** — 값은 바로 위 행에 있고, 실제로는 두 행이 그 값을 공유

2번은 단순한 표시 문제가 아닙니다. 위 행 하나에만 값이 붙으면 **그 값이 위 행만의 것으로 잘못 읽힙니다.** 원본에서 두 행의 합계였다면 사실과 달라집니다. 그러므로 2번도 그냥 넘기지 말고 quality=insufficient 로 표시하되, reason 에 **"병합 셀이 풀려 값이 윗행에만 귀속됨"** 처럼 원인을 밝혀 주세요. 값이 누락됐다고만 쓰면 원인을 오해하게 됩니다.

빈 칸이 아래로 이어지는 모양(윗행에 값이 있고 아랫행 같은 열이 빔)이면 2번일 가능성이 높습니다.

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

#: 그림으로 잘못 분류된 표를 되찾는 부분. 후보가 있을 때만 프롬프트에 붙인다.
#:
#: 레이아웃 모델이 표를 그림으로 잡으면 TableFormer 가 돌지 않아 내용이
#: 통째로 사라진다. 페이지 이미지는 이미 함께 보내고 있으므로, 같은 호출에서
#: 판정만 더 받으면 추가 비용이 없다.
_PROMOTE_SECTION = """\

## 그림으로 잘못 분류된 표 찾기

아래 그림들은 영역 안에 글자가 많아 표일 가능성이 있습니다. 페이지 이미지를 보고 **실제로 표(또는 표 형태의 비교/대조 박스)인 것만** 골라 위 JSON 배열에 함께 넣으세요.

{candidates}

- "id"           : 위 목록의 image ID 를 그대로
- "content_type" : "table" (표가 맞을 때만. 사진·도형·수식이면 목록에서 빼세요)
- "title"        : 표 제목
- "reason"       : 판단 이유

예: {{"id": "image_3", "content_type": "table", "title": "종전·개정안 대비표", "reason": "2열 대비표"}}
"""

_VALID_CONTENT_TYPES = frozenset({TABLE, TEXT, IMAGE})
_VALID_QUALITIES = frozenset({WRONG, INSUFFICIENT})

#: assess 프롬프트에 넣을 페이지 본문 최대 길이 (컨텍스트 초과 방지)
MAX_ASSESS_CHARS = 20_000


#: LLM 없이 기본값으로 표시했을 때 reason 에 남기는 문구.
#: 결과 JSON 만 보고 "판정을 거쳐 sufficient 가 나왔다" 고 오해하지 않도록,
#: 판정한 적이 없다는 사실을 데이터에 남긴다.
UNASSESSED_REASON = "미판정 — LLM 없이 기본값으로 표시 (품질을 확인한 것이 아님)"


def _mark_default(table: TableInfo, *, unassessed: bool = False) -> None:
    """문제없는 표로 표시한다.

    입력:
        table       TableInfo
        unassessed  LLM 자체가 없어 판정을 못 한 경우 True
    출력: 없음 (content_type=table, quality=sufficient)
    비고:
        `unassessed=True` 면 reason 에 그 사실을 남긴다. 이것이 없으면
        결과 JSON 에서 "LLM 이 sufficient 로 판정한 표" 와 "판정조차 못 한
        표" 가 똑같이 보인다 — 212개 표가 전부 sufficient 인데 실은 LLM 이
        한 번도 호출되지 않은 상황을 구분할 수 없다.
    """
    table.content_type = TABLE
    table.quality = SUFFICIENT
    if unassessed:
        table.reason = UNASSESSED_REASON


def _apply_assessment(
    tables: list[TableInfo],
    assessment: list[dict[str, Any]],
    *,
    unassessed: bool = False,
) -> None:
    """LLM 판정 결과를 TableInfo 에 반영한다.

    입력:
        tables      페이지의 표 목록
        assessment  LLM 이 반환한 판정 목록 (id, content_type, quality, ...)
        unassessed  LLM 을 아예 못 불렀으면 True (미설정·연결 불가·응답 실패)
    출력: 없음 (제자리 갱신)
    동작: 판정에 없는 표는 문제없음으로 간주. 알 수 없는 content_type 도 마찬가지.
          content_type 이 table 인데 quality 가 없으면 insufficient 로 둔다.
    비고:
        `unassessed` 를 구분하는 이유: LLM 이 "괜찮다" 고 답한 표와, LLM 을
        부르지도 못해 기본값이 된 표가 결과 JSON 에서 똑같이 보이면 안 된다.
        사내망 밖에서 돌리면 엔드포인트가 설정돼 있어도 연결이 안 되는데,
        그때 212개 표가 전부 sufficient 로 남아 검수를 통과한 것처럼 보였다.
    """
    assessment_map: dict[str, dict[str, Any]] = {
        item["id"]: item for item in assessment if item.get("id")
    }
    known_ids = {t.id for t in tables}

    for table in tables:
        info = assessment_map.get(table.id)
        if not info:
            _mark_default(table, unassessed=unassessed)
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


def promote_images_to_tables(
    page: PageContent,
    assessment: list[dict[str, Any]],
) -> None:
    """표로 판정된 그림을 TableInfo 로 승격한다.

    입력:
        page        페이지 (tables, images, content 를 제자리에서 갱신)
        assessment  LLM 판정 목록
    출력: 없음
    비고:
        **그림은 지우지 않는다.** 표 텍스트는 검색용, 원본 그림은 출처 확인용
        으로 둘 다 쓸모가 있다. 같은 영역이 tables 와 images 양쪽에 남으므로
        TableInfo.source_image_id 와 ImageInfo.promoted_table_id 로 짝을 건다.

        승격된 표는 markdown 이 비어 있다. quality=insufficient 로 표시해
        기존 재추출(fill) 경로가 페이지 이미지를 근거로 내용을 채우게 한다.
    """
    by_id = {img.id: img for img in (page.images or [])}
    next_num = max((t.table_num for t in page.tables), default=0)

    for item in assessment:
        image_id = str(item.get("id") or "")
        info = by_id.get(image_id)
        if info is None or info.promoted_table_id:
            continue                     # 표 판정이거나 이미 승격됨
        if (item.get("content_type") or "").strip().lower() != TABLE:
            continue
        if not info.table_candidate:
            # 후보로 올리지 않은 그림을 LLM 이 임의로 지목한 경우는 무시한다.
            _log.debug("후보가 아닌 그림을 표로 지목 — 무시: %s", image_id)
            continue

        next_num += 1
        table = TableInfo(
            id=make_table_id(next_num),
            table_num=next_num,
            placeholder=open_tag(next_num),
            markdown="",                 # fill 이 채운다
            bbox=info.bbox,
            llm_title=(item.get("title") or "").strip() or None,
            content_type=TABLE,
            quality=INSUFFICIENT,
            reason=(item.get("reason") or "").strip() or None,
            source_image_id=info.id,
        )
        page.tables.append(table)
        info.promoted_table_id = table.id
        page.content = _insert_table_block(
            page.content or "", info.placeholder, next_num
        )
        _log.info(
            "%s → %s 로 승격했습니다 (그림도 그대로 남깁니다)", info.id, table.id
        )


def _insert_table_block(content: str, placeholder: str, table_num: int) -> str:
    """그림 placeholder 바로 뒤에 빈 표 블록을 끼워 넣는다.

    입력:
        content      페이지 본문
        placeholder  `<!-- image N -->` 문자열
        table_num    새 표 번호
    출력: 표 블록이 삽입된 본문 (placeholder 를 못 찾으면 끝에 덧붙임)
    비고: 읽기 순서를 지키려고 그림 자리 바로 뒤에 넣는다.
    """
    block = make_table_block(table_num, "")
    if placeholder and placeholder in content:
        return content.replace(placeholder, f"{placeholder}\n\n{block}", 1)
    return f"{content}\n\n{block}" if content else block


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
            _mark_default(table, unassessed=True)
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

    prompt = _ASSESS_PROMPT.format(content=content)
    candidates = [img for img in (page.images or []) if img.table_candidate]
    if candidates and image_urls:
        # 페이지 이미지를 못 보내면 그림을 볼 수 없으므로 판정도 무의미하다.
        listing = "\n".join(
            f"- {img.id} (글자 {img.text_chars}자, {img.text_lines}줄)"
            for img in candidates
        )
        prompt += _PROMOTE_SECTION.format(candidates=listing)

    try:
        raw = invoke_llm(
            prompt,
            span_name="table_assess",
            image_urls=image_urls,
            cfg=cfg,
        )
        assessment = parse_json_list_or_object_map(raw)
        unassessed = False
    except Exception as exc:
        _log_page_failure(page.page_no, exc)
        assessment = []
        unassessed = True                # 연결 불가·응답 실패 — 판정한 적 없음

    _apply_assessment(page.tables, assessment, unassessed=unassessed)
    if candidates:
        promote_images_to_tables(page, assessment)


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
