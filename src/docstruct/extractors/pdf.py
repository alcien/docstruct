"""Docling 변환 결과 → PageContent 목록.

역할:
    DoclingDocument 의 요소를 페이지별로 모아 본문 markdown 을 만들고,
    표는 `<table N>` 블록으로 치환하며, 그림은 파일로 저장해 메타를 남긴다.
    페이지마다 처리 경로(PageTrace)를 기록한다.
호출부:
    docstruct.extractors.registry._extract_pdf
출력:
    list[PageContent] — content, tables, images, trace 가 채워진 상태
"""
from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

from docstruct.converters.pdf.table_extract import (
    non_table_item_to_markdown,
    page_no as docling_page_no,
)
from docstruct.layout import LayoutItem, item_bbox, label_name, preview_text
from docstruct.media.picture import picture_to_block
from docstruct.models import ImageInfo, PageContent, PageTrace, TableInfo
from docstruct.tables.docling import docling_table_to_markdown
from docstruct.tables.tags import make_table_block, make_table_id, open_tag

_log = logging.getLogger(__name__)


def _table_bbox_top_left(item, doc) -> dict[str, float] | None:
    """Docling TableItem prov.bbox → TOPLEFT 페이지 좌표(points)."""
    prov_list = getattr(item, "prov", None) or []
    if not prov_list:
        return None
    bbox = getattr(prov_list[0], "bbox", None)
    if bbox is None:
        return None
    page = doc.pages.get(prov_list[0].page_no)
    if page is None or page.size is None:
        return None
    tl = bbox.to_top_left_origin(page_height=page.size.height)
    return {"l": tl.l, "t": tl.t, "r": tl.r, "b": tl.b}


def _table_markdown(doc, item) -> str:
    """TableItem → 병합셀 grid GFM markdown (실패 시 docling export)."""
    md = docling_table_to_markdown(item)
    if md:
        return md
    try:
        chunk = item.export_to_markdown(doc)
        return chunk.strip() if chunk else ""
    except Exception as exc:
        _log.debug("표 export_to_markdown fallback 실패: %s", exc)
        return ""


def extract_pdf_pages(
    doc,
    *,
    image_dir: str | Path | None = None,
    page_stats: dict[int, dict] | None = None,
) -> list[PageContent]:
    """DoclingDocument 를 페이지 단위로 구조화한다.

    입력:
        doc         DoclingDocument
        image_dir   그림 저장 위치. None 이면 저장하지 않음
        page_stats  페이지별 텍스트 출처 통계 (converters.pdf.converter 제공)
    출력:
        list[PageContent] — 페이지 번호 오름차순
    부수효과:
        image_dir 에 그림 PNG 저장
    """
    from docling_core.types.doc.labels import DocItemLabel

    page_parts: dict[int, list[str]] = defaultdict(list)
    page_tables: dict[int, list[TableInfo]] = defaultdict(list)
    page_images: dict[int, list[ImageInfo]] = defaultdict(list)
    page_layout: dict[int, list[LayoutItem]] = defaultdict(list)
    table_counter = 0
    image_counter = 0

    for order, (item, _level) in enumerate(doc.iterate_items()):
        page = docling_page_no(item)
        if page is None:
            page = 0

        label = getattr(item, "label", None)
        record = LayoutItem(
            order=order,
            page_no=page,
            label=label_name(label),
            bbox=item_bbox(item, doc),
        )

        if label == DocItemLabel.TABLE:
            table_counter += 1
            md = _table_markdown(doc, item)
            page_parts[page].append(make_table_block(table_counter, md))
            page_tables[page].append(
                TableInfo(
                    id=make_table_id(table_counter),
                    table_num=table_counter,
                    placeholder=open_tag(table_counter),
                    markdown=md,
                    bbox=record.bbox,
                )
            )
            record.outcome = "table"
            record.ref = make_table_id(table_counter)
            record.text = preview_text(md)
            record.char_count = len(md)

        elif label == DocItemLabel.PICTURE:
            image_counter += 1
            block, info = picture_to_block(
                item,
                doc,
                image_id=f"image_{image_counter}",
                image_dir=image_dir,
            )
            # 설명이 없어도 이미지 메타는 남긴다 (본문 placeholder 와 짝을 맞추기 위함).
            page_parts[page].append(block)
            page_images[page].append(info)
            record.outcome = "image"
            record.ref = f"image_{image_counter}"
            record.text = preview_text(getattr(info, "description", "") or "")
            record.char_count = len(getattr(info, "description", "") or "")

        else:
            chunk = non_table_item_to_markdown(item, doc)
            if chunk and chunk.strip():
                page_parts[page].append(chunk.strip())
                record.outcome = "text"
                record.text = preview_text(chunk)
                record.char_count = len(chunk.strip())
            else:
                record.outcome = "dropped"

        page_layout[page].append(record)

    from docstruct.core.config import get_settings

    settings = get_settings()
    pages: list[PageContent] = []

    for page_no in sorted(page_parts):
        tables = page_tables.get(page_no, [])
        images = page_images.get(page_no, [])
        stat = (page_stats or {}).get(page_no, {})
        source = stat.get("text_source", "unmeasured")
        ratio = stat.get("ocr_ratio")
        cells = stat.get("cell_count")

        body = "\n\n".join(page_parts[page_no])

        # 셀 계측은 신뢰할 수 없을 때가 많습니다(Docling 이 셀을 버림).
        # 반면 "본문이 비었는가" 는 확실한 신호이므로, 측정이 안 된 상태에서
        # 본문까지 비어 있으면 그때만 실제 실패(empty)로 봅니다.
        if source == "unmeasured" and not body.strip():
            source = "empty"

        trace = PageTrace(
            extractor="docling",
            text_source=source,
            ocr_ratio=ratio,
            cell_count=cells,
            table_count=len(tables),
            picture_count=len(images),
        )

        # ① PDF 페이지 로드
        trace.add(
            "converters.pdf.converter",
            "PDF 페이지 로드",
            f"backend={settings.pdf_backend}",
        )

        # ② 텍스트 획득 경로 — from_ocr 플래그로 실제 관측된 사실만 기록
        if source == "text_layer":
            trace.add(
                "docling.parse",
                "내장 텍스트 레이어 파싱",
                f"{cells}셀 · OCR 미수행",
            )
        elif source == "ocr":
            trace.add(
                "docling.ocr",
                "OCR 수행 (스캔 페이지)",
                f"{settings.ocr_backend} · {cells}셀 전부 OCR",
            )
        elif source == "mixed":
            ocr_cells = int(round((ratio or 0) * (cells or 0)))
            trace.add(
                "docling.parse+ocr",
                "텍스트 레이어 + 부분 OCR",
                f"{cells}셀 중 {ocr_cells}셀 OCR ({settings.ocr_backend})",
            )
        elif source == "empty":
            # 본문까지 비었을 때만 실제 문제로 봅니다.
            # 원인은 둘 중 하나 — ① OCR 이 이 영역에 안 걸림,
            # ② OCR 은 돌았지만 인식 실패(로그의 "OCR ... empty result").
            trace.add(
                "docling.parse",
                "본문 추출 실패",
                "텍스트가 하나도 나오지 않았습니다 — "
                "DOCLING_FORCE_FULL_PAGE_OCR=true 또는 다른 OCR 백엔드를 시도하세요",
                status="warn",
            )
        else:
            # 측정이 안 됐을 뿐 추출은 정상입니다. 경고가 아닙니다.
            trace.add(
                "docling.parse",
                "텍스트 추출",
                f"{len(body):,}자 · 출처(레이어/OCR) 구분은 미측정 "
                "— 필요하면 DOCLING_GENERATE_PARSED_PAGES=true",
            )

        # ③ 레이아웃 요소 분류 결과
        text_blocks = len(page_parts[page_no]) - len(tables) - len(images)
        trace.add(
            "docstruct.extractors.pdf",
            "요소 분류",
            f"텍스트블록 {max(text_blocks, 0)} · 표 {len(tables)} · 그림 {len(images)}",
        )

        # ④ 표 구조 → GFM 변환
        if tables:
            trace.add(
                "docstruct.tables.docling",
                "TableItem → GFM markdown",
                f"{len(tables)}개 (병합셀 grid 복원)",
            )
        if images:
            trace.add(
                "docstruct.media.picture",
                "그림 추출",
                f"{len(images)}개"
                + (
                    f" · 설명 {sum(1 for i in images if i.description)}건"
                    if any(i.description for i in images)
                    else " · 설명 없음"
                ),
            )

        pages.append(
            PageContent(
                page_no=page_no,
                page_no_kind="exact",
                content=body,
                tables=tables,
                images=images,
                trace=trace,
                layout=page_layout.get(page_no, []),
            )
        )
    return pages
