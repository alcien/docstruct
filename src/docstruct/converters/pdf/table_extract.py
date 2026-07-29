"""Docling 표·본문 요소 → markdown (레거시 경로).

역할:
    converters.cli 의 PDF 변환에서 쓰는 표 처리. 페이지 경계로 나뉜 표를
    잇고, 필요하면 LLM 으로 다시 뽑은 뒤 원본 셀 텍스트가 남아 있는지 검증한다.
    (docstruct 파이프라인은 docstruct.tables 쪽을 쓴다.)
호출부:
    converters.pdf.converter, docstruct.extractors.pdf (요소 변환 함수만)
출력:
    markdown 문자열
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from docstruct.core.config import get_settings
from docstruct.infrastructure.llm.client import invoke_llm

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 프롬프트
# ---------------------------------------------------------------------------

_SINGLE_HTML_PROMPT = """\
아래 HTML 표를 GFM(GitHub Flavored Markdown) 표로 변환하세요.
- <th> 태그가 있는 행은 헤더 행. 구분선(|---|)은 마지막 헤더 행 아래 한 번만
- rowspan/colspan 병합 셀은 시작 위치에만 내용, 나머지는 빈 칸
- 원본 셀 텍스트를 추가·삭제·변경 없이 그대로 유지
- 표 외 다른 텍스트 출력 금지

HTML:
{input}
"""

_SPLIT_HTML_PROMPT = """\
아래 두 HTML 표는 PDF 페이지 경계에서 잘린 하나의 표입니다.
두 번째 표를 첫 번째 표에 이어 붙여 하나의 GFM markdown 표로 변환하세요.
- 헤더는 첫 번째 표의 것만 사용. 헤더 중복 금지
- 구분선(|---|)은 마지막 헤더 행 아래 한 번만
- rowspan/colspan 병합 셀은 시작 위치에만 내용, 나머지는 빈 칸
- 원본 셀 텍스트를 추가·삭제·변경 없이 그대로 유지
- 표 외 다른 텍스트 출력 금지

첫 번째 HTML:
{input_a}

두 번째 HTML (이어지는 행):
{input_b}
"""

_SINGLE_JSON_PROMPT = """\
아래 JSON은 PDF 표의 셀 목록입니다. GFM(GitHub Flavored Markdown) 표로 변환하세요.
- header=true인 셀들의 행이 헤더 행. 구분선(|---|)은 마지막 헤더 행 아래 한 번만
- rs/cs(rowspan/colspan)로 병합된 셀은 시작 위치에만 내용, 나머지는 빈 칸
- 원본 셀 텍스트를 추가·삭제·변경 없이 그대로 유지
- 표 외 다른 텍스트 출력 금지

JSON:
{input}
"""

_SPLIT_JSON_PROMPT = """\
아래 두 JSON은 PDF 페이지 경계에서 잘린 하나의 표입니다.
두 번째 JSON의 행을 첫 번째 JSON에 이어 붙여 하나의 GFM markdown 표로 변환하세요.
- 헤더는 첫 번째 JSON의 것만 사용. 헤더 중복 금지
- 구분선(|---|)은 마지막 헤더 행 아래 한 번만
- rs/cs(rowspan/colspan) 병합 셀은 시작 위치에만 내용, 나머지는 빈 칸
- 원본 셀 텍스트를 추가·삭제·변경 없이 그대로 유지
- 표 외 다른 텍스트 출력 금지

첫 번째 JSON:
{input_a}

두 번째 JSON (이어지는 행):
{input_b}
"""


# ---------------------------------------------------------------------------
# 설정 헬퍼 — 실제 값은 core.config 에서 가져옵니다 (단일 진입점).
# ---------------------------------------------------------------------------

def table_llm_enabled() -> bool:
    return get_settings().table_llm_enabled


def _table_llm_mode() -> str:
    """selective(기본): 다중헤더·분할 표만 LLM. always: 모든 표."""
    return get_settings().table_llm_mode


def _table_format() -> str:
    """html(기본) 또는 json."""
    return get_settings().table_format


def _table_api_config() -> dict[str, Any] | None:
    endpoint = get_settings().llm
    return endpoint.as_dict() if endpoint else None


# ---------------------------------------------------------------------------
# 표 직렬화 (LLM 입력 생성)
# ---------------------------------------------------------------------------

def _table_to_llm_input(item, doc, fmt: str) -> str:
    """표를 LLM 입력 문자열로 직렬화합니다. fmt='html'|'json'."""
    if fmt == "json":
        return _table_to_slim_json(item)
    return item.export_to_html(doc)


def _table_to_slim_json(item) -> str:
    """table_cells만 추린 slim JSON (bbox/grid 제외)."""
    page = None
    prov = getattr(item, "prov", None) or []
    if prov:
        page = prov[0].page_no

    cells = []
    for c in item.data.table_cells:
        cell: dict[str, Any] = {
            "r": c.start_row_offset_idx,
            "c": c.start_col_offset_idx,
            "text": c.text,
        }
        if c.row_span > 1:
            cell["rs"] = c.row_span
        if c.col_span > 1:
            cell["cs"] = c.col_span
        if c.column_header:
            cell["header"] = True
        if c.row_header:
            cell["row_header"] = True
        cells.append(cell)

    payload: dict[str, Any] = {
        "num_rows": item.data.num_rows,
        "num_cols": item.data.num_cols,
        "cells": cells,
    }
    if page is not None:
        payload["page"] = page

    return json.dumps(payload, ensure_ascii=False)


def _extract_source_texts(source_input: str, fmt: str) -> set[str]:
    """LLM 입력에서 셀 텍스트 집합 추출 (검증용)."""
    if fmt == "json":
        try:
            data = json.loads(source_input)
            return {c["text"] for c in data.get("cells", []) if c.get("text")}
        except Exception:
            return set()
    # HTML: 태그 제거
    texts: set[str] = set()
    for m in re.finditer(r">([^<]+)<", source_input):
        t = m.group(1).strip()
        if t:
            texts.add(t)
    return texts


# ---------------------------------------------------------------------------
# 페이지 분할 감지 (강화)
# ---------------------------------------------------------------------------

def page_no(item) -> int | None:
    prov = getattr(item, "prov", None) or []
    return prov[0].page_no if prov else None


def _first_row_has_header(item) -> bool:
    data = getattr(item, "data", None)
    if not data or not data.table_cells:
        return False
    min_row = min(c.start_row_offset_idx for c in data.table_cells)
    return any(
        c.column_header and c.start_row_offset_idx == min_row
        for c in data.table_cells
    )


def _header_row_count(item) -> int:
    """column_header=True 행이 몇 개인지 반환."""
    data = getattr(item, "data", None)
    if not data or not data.table_cells:
        return 0
    header_rows: set[int] = {
        c.start_row_offset_idx for c in data.table_cells if c.column_header
    }
    return len(header_rows)


def _last_row_bbox_bottom(item) -> float | None:
    """마지막 데이터 행의 bbox bottom (페이지 하단 근접 여부 판단용)."""
    data = getattr(item, "data", None)
    if not data or not data.table_cells:
        return None
    last_row = max(c.start_row_offset_idx for c in data.table_cells)
    bottoms = [
        c.bbox.b
        for c in data.table_cells
        if c.start_row_offset_idx == last_row and c.bbox is not None
    ]
    return max(bottoms) if bottoms else None


def _page_height(item, doc) -> float | None:
    prov = getattr(item, "prov", None) or []
    if not prov:
        return None
    page = doc.pages.get(prov[0].page_no)
    return page.size.height if page and page.size else None


def _is_continuation(item_a, item_b, doc=None) -> bool:
    """페이지 분할 표 조각인지 판별합니다.

    조건:
    1. 인접 페이지 (b = a + 1)
    2. item_b 열 수 < item_a 열 수
    3. item_b 첫 행에 헤더 셀 없음
    4. (강화) item_b 첫 행에 비어 있지 않은 셀이 1~2개 이하 (왼쪽 패딩 패턴)
       또는 item_b 행 수가 item_a 행 수보다 매우 작음
    5. (선택) item_a 마지막 행이 페이지 하단 80% 이상 위치
    """
    p1, p2 = page_no(item_a), page_no(item_b)
    if p1 is None or p2 is None or p2 != p1 + 1:
        return False

    data_a = getattr(item_a, "data", None)
    data_b = getattr(item_b, "data", None)
    if not data_a or not data_b:
        return False

    # 조건 2: 열 수 감소
    if data_b.num_cols >= data_a.num_cols:
        return False

    # 조건 3: item_b 첫 행에 헤더 없음
    if _first_row_has_header(item_b):
        return False

    # 조건 4 (강화): item_b가 의미 있는 독립 표가 아닐 것
    # item_b의 행 수가 item_a의 절반 이하일 것
    if data_b.num_rows > data_a.num_rows // 2 + 1:
        return False

    # 조건 5 (선택): item_a 마지막 행이 페이지 하단 근처
    if doc is not None:
        h = _page_height(item_a, doc)
        bottom = _last_row_bbox_bottom(item_a)
        if h is not None and bottom is not None:
            # 페이지 높이의 70% 이하에 마지막 행이 있으면 분할이 아닐 가능성 높음
            if bottom < h * 0.7:
                return False

    return True


def _build_split_map(tables: list, doc=None) -> tuple[dict[str, Any], set[str]]:
    """첫 번째 표 self_ref → 두 번째 표 item, 스킵할 self_ref 집합."""
    partners: dict[str, Any] = {}
    skip_seconds: set[str] = set()

    for i in range(len(tables) - 1):
        first, second = tables[i], tables[i + 1]
        second_ref = getattr(second, "self_ref", None)
        if second_ref and second_ref in skip_seconds:
            continue
        if not _is_continuation(first, second, doc):
            continue
        first_ref = getattr(first, "self_ref", None)
        if not first_ref:
            continue
        partners[first_ref] = second
        if second_ref:
            skip_seconds.add(second_ref)

    return partners, skip_seconds


# ---------------------------------------------------------------------------
# LLM 호출
# ---------------------------------------------------------------------------

def _normalize_table_markdown(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:markdown|md)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text.strip())
    return text.strip()


def _call_llm(prompt: str, cfg: dict[str, Any]) -> str | None:
    try:
        content = invoke_llm(prompt, span_name="table_extract", cfg=cfg)
        return _normalize_table_markdown(content) if content else None
    except Exception as exc:
        _log.warning("표 LLM API 호출 실패: %s", exc)
        return None


# ---------------------------------------------------------------------------
# 검증
# ---------------------------------------------------------------------------

def _validate_cell_texts(source_input: str, output_md: str, fmt: str) -> bool:
    """LLM 출력에 원본 셀 텍스트가 포함되어 있는지 확인합니다."""
    source_texts = _extract_source_texts(source_input, fmt)
    if not source_texts:
        return True

    # 한글 2자 이상 비어있지 않은 텍스트만 검증 대상
    ko_re = re.compile(r"[가-힣]{2,}")
    important = {t for t in source_texts if ko_re.search(t)}
    if not important:
        return True

    # output markdown 셀 텍스트 집합
    output_cells: set[str] = set()
    for line in output_md.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        if re.match(r"^\|[-:\s|]+\|$", line):
            continue
        for cell in line.strip("|").split("|"):
            cell = cell.strip()
            if cell:
                output_cells.add(cell)

    # 중요 텍스트의 80% 이상이 output에 있어야 통과
    missing = important - output_cells
    pass_ratio = 1 - len(missing) / len(important)
    if pass_ratio < 0.8:
        _log.warning(
            "표 LLM 검증 실패: source=%d important=%d missing=%d (%.0f%% pass)",
            len(source_texts),
            len(important),
            len(missing),
            pass_ratio * 100,
        )
        return False
    return True


# ---------------------------------------------------------------------------
# LLM 필요 여부 판단 (selective 모드)
# ---------------------------------------------------------------------------

def _needs_llm_table(item, split_partners: dict) -> bool:
    """이 표에 LLM이 필요한지 결정합니다."""
    mode = _table_llm_mode()
    if mode == "always":
        return True

    # selective: 다중 헤더 행 또는 페이지 분할 쌍
    ref = getattr(item, "self_ref", None)
    if ref and ref in split_partners:
        return True
    if _header_row_count(item) >= 2:
        return True
    return False


# ---------------------------------------------------------------------------
# 단일 표 LLM 변환
# ---------------------------------------------------------------------------

def _table_to_md_via_llm(
    doc, item, cfg: dict[str, Any], fmt: str,
    split_partners: dict, skip_seconds: set,
) -> str | None:
    """LLM으로 표 markdown 생성. 실패 시 None 반환."""
    ref = getattr(item, "self_ref", None)

    if ref and ref in split_partners:
        second = split_partners[ref]
        inp_a = _table_to_llm_input(item, doc, fmt)
        inp_b = _table_to_llm_input(second, doc, fmt)
        if fmt == "json":
            prompt = _SPLIT_JSON_PROMPT.format(input_a=inp_a, input_b=inp_b)
        else:
            prompt = _SPLIT_HTML_PROMPT.format(input_a=inp_a, input_b=inp_b)
        source_for_validate = inp_a + inp_b
    else:
        inp = _table_to_llm_input(item, doc, fmt)
        if fmt == "json":
            prompt = _SINGLE_JSON_PROMPT.format(input=inp)
        else:
            prompt = _SINGLE_HTML_PROMPT.format(input=inp)
        source_for_validate = inp

    md = _call_llm(prompt, cfg)
    if not md:
        return None

    if not _validate_cell_texts(source_for_validate, md, fmt):
        _log.warning(
            "표 LLM 검증 실패 — fallback: ref=%s", ref or "?"
        )
        return None

    return md


# ---------------------------------------------------------------------------
# 비표 item → markdown 변환 헬퍼
# ---------------------------------------------------------------------------

def non_table_item_to_markdown(item, doc) -> str:
    """TableItem 이외의 doc item을 markdown 문자열로 변환합니다.

    Docling 개별 item에는 export_to_markdown이 없는 타입이 있으므로
    (TextItem, SectionHeaderItem, ListItem 등) 타입별로 직접 처리합니다.
    """
    from docling_core.types.doc import (
        GroupItem,
        ListItem,
        PictureItem,
        SectionHeaderItem,
    )

    # 컨테이너 — 직접 출력 없음 (자식이 별도로 yield 됨)
    if isinstance(item, GroupItem):
        return ""

    # 그림 — 자체 export_to_markdown 사용
    if isinstance(item, PictureItem):
        try:
            return item.export_to_markdown(doc) or ""
        except Exception:
            return ""

    # 섹션 헤더 — level 기반 # 접두사
    if isinstance(item, SectionHeaderItem):
        text = (getattr(item, "text", "") or "").strip()
        level = max(1, min(int(getattr(item, "level", 1)), 6))
        return f"{'#' * level} {text}" if text else ""

    # 리스트 아이템
    if isinstance(item, ListItem):
        text = (getattr(item, "text", "") or "").strip()
        marker = (getattr(item, "marker", "") or "").strip()
        if not text:
            return ""
        return f"{marker} {text}" if marker else f"- {text}"

    # 나머지 (TextItem, CodeItem, FormulaItem 등) — text 속성 그대로
    text = (getattr(item, "text", "") or "").strip()
    return text


# ---------------------------------------------------------------------------
# export_markdown — A 방향 단일 패스 조립
# ---------------------------------------------------------------------------

def export_markdown(doc) -> str:
    """PDF DoclingDocument → markdown.

    iterate_items() 단일 패스로 조립:
    - TableItem: selective LLM (조건 해당) 또는 Docling fallback
    - 비표 item: 타입별 직접 변환 (non_table_item_to_markdown)

    _postprocess_markdown / regex 교체 방식 사용 안 함 → 표 swap 구조적 차단.
    """
    from docling_core.types.doc.labels import DocItemLabel

    raw_needed = not table_llm_enabled()
    cfg = None if raw_needed else _table_api_config()

    if raw_needed or cfg is None:
        return doc.export_to_markdown()

    fmt = _table_format()

    # 페이지 분할 쌍 사전 구성
    tables = [
        item
        for item, _ in doc.iterate_items()
        if getattr(item, "label", None) == DocItemLabel.TABLE
    ]
    split_partners, skip_seconds = _build_split_map(tables, doc)

    parts: list[str] = []

    for item, _level in doc.iterate_items():
        label = getattr(item, "label", None)

        if label == DocItemLabel.TABLE:
            ref = getattr(item, "self_ref", None)
            if ref and ref in skip_seconds:
                # 분할 쌍의 두 번째 표: 첫 번째와 함께 이미 처리됨
                continue

            if _needs_llm_table(item, split_partners):
                md = _table_to_md_via_llm(
                    doc, item, cfg, fmt, split_partners, skip_seconds
                )
                if md:
                    parts.append(md)
                    continue
                # LLM 실패 → fallback
                _log.warning("표 LLM 실패 — Docling fallback: ref=%s", ref or "?")
                # 분할 쌍이었으면 두 표 모두 fallback
                if ref and ref in split_partners:
                    second = split_partners[ref]
                    fb_a = item.export_to_markdown(doc)
                    fb_b = second.export_to_markdown(doc)
                    for fb in (fb_a, fb_b):
                        if fb and fb.strip():
                            parts.append(fb.strip())
                    continue

            try:
                chunk = item.export_to_markdown(doc)
            except Exception as exc:
                _log.debug("표 export_to_markdown 실패: %s", exc)
                continue
            if chunk and chunk.strip():
                parts.append(chunk.strip())

        else:
            chunk = non_table_item_to_markdown(item, doc)
            if chunk and chunk.strip():
                parts.append(chunk.strip())

    return "\n\n".join(parts)
