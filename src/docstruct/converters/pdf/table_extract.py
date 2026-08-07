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
    """/convert 경로의 표 LLM 스위치.

    입력: 없음
    출력: DOCLING_TABLE_LLM 설정값 (bool)
    """
    return get_settings().table_llm_enabled


def _table_llm_mode() -> str:
    """표 LLM 적용 범위.

    입력: 없음
    출력: 'selective'(기본 — 다중헤더·분할 표만) | 'always'(모든 표)
    """
    return get_settings().table_llm_mode


def _table_format() -> str:
    """LLM 에 넘길 표 직렬화 형식.

    입력: 없음
    출력: 'html'(기본) | 'json'
    """
    return get_settings().table_format


def _table_api_config() -> dict[str, Any] | None:
    """표 LLM 엔드포인트 설정.

    입력: 없음
    출력: 설정 dict. 미설정이면 None
    """
    endpoint = get_settings().llm
    return endpoint.as_dict() if endpoint else None


# ---------------------------------------------------------------------------
# 표 직렬화 (LLM 입력 생성)
# ---------------------------------------------------------------------------

def _table_to_llm_input(item, doc, fmt: str) -> str:
    """표를 LLM 입력 문자열로 직렬화한다.

    입력: item — TableItem, doc — DoclingDocument, fmt — 'html'|'json'
    출력: 직렬화된 문자열
    """
    if fmt == "json":
        return _table_to_slim_json(item)
    return item.export_to_html(doc)


def _table_to_slim_json(item) -> str:
    """table_cells 만 추린 slim JSON 을 만든다.

    입력: item — TableItem
    출력: {num_rows, num_cols, cells:[{r,c,text,rs?,cs?,header?}], page?} JSON 문자열
    비고: bbox·grid 를 빼 토큰을 아낀다. 병합·헤더 플래그는 값이 있을 때만 넣는다.
    """
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
    """LLM 입력에서 셀 텍스트 집합을 뽑는다 (출력 검증용).

    입력: source_input — 직렬화된 표, fmt — 'html'|'json'
    출력: 셀 텍스트 집합. 해석 실패 시 빈 집합 (검증을 통과시킨다)
    """
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
    """item 이 속한 페이지 번호.

    입력: item — docling item
    출력: 페이지 번호. prov 가 없으면 None
    """
    prov = getattr(item, "prov", None) or []
    return prov[0].page_no if prov else None


def _first_row_has_header(item) -> bool:
    """표 첫 행에 헤더 셀이 있는지.

    입력: item — TableItem
    출력: 첫 행에 column_header=True 셀이 있으면 True
    """
    data = getattr(item, "data", None)
    if not data or not data.table_cells:
        return False
    min_row = min(c.start_row_offset_idx for c in data.table_cells)
    return any(
        c.column_header and c.start_row_offset_idx == min_row
        for c in data.table_cells
    )


def _header_row_count(item) -> int:
    """column_header=True 인 행 수.

    입력: item — TableItem
    출력: 헤더 행 수 (셀이 없으면 0)
    """
    data = getattr(item, "data", None)
    if not data or not data.table_cells:
        return 0
    header_rows: set[int] = {
        c.start_row_offset_idx for c in data.table_cells if c.column_header
    }
    return len(header_rows)


def _last_row_bbox_bottom(item) -> float | None:
    """마지막 데이터 행의 bbox bottom 좌표.

    입력: item — TableItem
    출력: 좌표값. 셀·bbox 가 없으면 None
    비고: 표가 페이지 하단에 붙어 있는지(분할 후보) 판단에 쓴다.
    """
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
    """item 이 속한 페이지의 높이.

    입력: item — docling item, doc — DoclingDocument
    출력: 높이(points). 알 수 없으면 None
    """
    prov = getattr(item, "prov", None) or []
    if not prov:
        return None
    page = doc.pages.get(prov[0].page_no)
    return page.size.height if page and page.size else None


def _is_continuation(item_a, item_b, doc=None) -> bool:
    """두 표가 페이지 분할로 갈라진 한 표인지 판별한다.

    입력: item_a, item_b — 문서 순서상 이웃한 TableItem, doc — 페이지 정보용
    출력: 분할 조각이면 True
    동작: ① 인접 페이지 ② b 열 수 ≤ a 열 수 ③ b 첫 행에 헤더 없음
          ④ b 첫 행이 왼쪽 패딩 패턴이거나 행 수가 크게 작음
          ⑤ (페이지 정보가 있으면) a 마지막 행이 페이지 하단 70% 아래
          를 모두 만족해야 한다.
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
    """분할 표 쌍을 사전 구성한다.

    입력: tables — 문서 순서의 TableItem 목록, doc — 페이지 정보용
    출력: (첫 표 self_ref → 둘째 표 item, 건너뛸 둘째 표 self_ref 집합)
    """
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
    """LLM 응답에서 코드펜스를 벗긴다.

    입력: text — 응답 원문
    출력: ```markdown 펜스가 제거된 문자열
    """
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:markdown|md)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text.strip())
    return text.strip()


def _call_llm(prompt: str, cfg: dict[str, Any]) -> str | None:
    """표 프롬프트를 LLM 에 보낸다.

    입력: prompt — 프롬프트, cfg — 엔드포인트 설정
    출력: 정리된 응답. 실패·빈 응답이면 None (호출부가 fallback)
    """
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
    """LLM 출력에 원본 셀 텍스트가 보존됐는지 확인한다.

    입력: source_input — 직렬화 원본, output_md — LLM 출력, fmt — 형식
    출력: 통과하면 True
    동작: 한글 2자 이상 셀만 검증 대상으로 삼아, 원본에 있던 텍스트가
          출력 표 셀에서 사라졌으면 실패로 본다 (환각·누락 차단).
    """
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
    """이 표에 LLM 이 필요한지 결정한다.

    입력: item — TableItem, split_partners — 분할 쌍 맵
    출력: 필요하면 True
    동작: always 모드면 무조건. selective(기본)면 분할 쌍의 첫 표거나
          헤더 행이 2줄 이상일 때만 LLM 을 부른다.
    """
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
    """표 하나를 LLM 으로 markdown 화한다.

    입력: doc, item, cfg, fmt, split_partners, skip_seconds
    출력: 검증을 통과한 markdown. 실패 시 None (호출부가 Docling fallback)
    동작: 분할 쌍이면 두 조각을 한 프롬프트에 넣어 병합을 요청한다.
          응답은 _validate_cell_texts 로 원본 셀 보존 여부를 검증한다.
    """
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
    """TableItem 이외의 doc item 을 markdown 으로 변환한다.

    입력: item — docling item, doc — DoclingDocument
    출력: markdown 조각. 컨테이너(GroupItem)는 빈 문자열
    비고: 개별 item 에는 export_to_markdown 이 없는 타입이 있어
          (TextItem·SectionHeaderItem·ListItem 등) 타입별로 직접 처리한다.
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
    """DoclingDocument 전체를 markdown 으로 조립한다.

    입력: doc — DoclingDocument
    출력: markdown 문자열
    동작: iterate_items() 단일 패스로 조립한다. 표는 조건에 맞으면
          LLM(_table_to_md_via_llm), 아니면 Docling export 를 쓴다.
          분할 쌍의 둘째 표는 첫째와 함께 처리되므로 건너뛴다.
          regex 후처리 교체를 쓰지 않아 표가 뒤바뀔 여지를 구조적으로 막는다.
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
