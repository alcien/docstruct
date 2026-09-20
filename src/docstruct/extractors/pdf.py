"""Docling 변환 결과 → PageContent 목록.

입력:
    Docling 변환 결과

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
from docstruct.output.layout import LayoutItem, item_bbox, label_name, preview_text
from docstruct.images.picture import picture_to_block
from docstruct.text.korean_text import normalize_pdf_text
from docstruct.models import ImageInfo, PageContent, PageTrace, TableInfo
from docstruct.tables.docling import docling_table_to_markdown
from docstruct.tables.tags import make_table_block, make_table_id, open_tag

_log = logging.getLogger(__name__)


def _table_bbox_top_left(item, doc) -> dict[str, float] | None:
    """TableItem prov.bbox 를 TOPLEFT 페이지 좌표로 바꾼다.

    입력: item — TableItem, doc — DoclingDocument
    출력: {l, t, r, b} (points). 정보가 없으면 None
    """
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
    """TableItem 을 병합셀이 반영된 GFM markdown 으로 만든다.

    입력: doc — DoclingDocument, item — TableItem
    출력: markdown 표. 자체 렌더 실패 시 docling export 로 폴백, 그마저
          실패하면 빈 문자열
    """
    md = docling_table_to_markdown(item)
    if md:
        return md
    try:
        chunk = item.export_to_markdown(doc)
        return chunk.strip() if chunk else ""
    except Exception as exc:
        _log.debug("표 export_to_markdown fallback 실패: %s", exc)
        return ""


def _all_page_numbers(doc, *buckets: dict[int, list]) -> list[int]:
    """문서가 가진 **모든 쪽 번호**를 차례대로 (0.5.42).

    입력: doc — DoclingDocument, buckets — 쪽별로 모은 것들
    출력: 쪽 번호 오름차순
    비고:
        내용이 들어온 쪽만 세면 **빈 쪽이 번호째 사라진다.** 문서가 아는
        쪽 수를 먼저 보고, 그것을 모르면 들어온 번호의 범위를 메운다.

        번호를 지어내지는 않는다 — 최대 번호까지만 채운다. 마지막 쪽들이
        통째로 비면 그건 알 길이 없고, 그때는 있는 것만 낸다.
    """
    seen: set[int] = set()
    for bucket in buckets:
        seen |= set(bucket)

    total = getattr(doc, "num_pages", None)
    if callable(total):
        try:
            total = total()
        except Exception:                        # noqa: BLE001 - 못 물으면 아래로
            total = None
    if not isinstance(total, int) or total <= 0:
        pages = getattr(doc, "pages", None)
        if pages is not None:
            try:
                total = len(pages)
            except TypeError:
                total = None
    if not isinstance(total, int) or total <= 0:
        total = max(seen) if seen else 0
    if seen:
        total = max(total, max(seen))
    return list(range(1, total + 1)) if total else sorted(seen)


def extract_pdf_pages(
    doc,
    *,
    image_dir: str | Path | None = None,
    page_stats: dict[int, dict] | None = None,
    source_path: str | Path | None = None,
) -> list[PageContent]:
    """DoclingDocument 를 페이지 단위로 구조화한다.

    입력:
        doc          DoclingDocument
        image_dir    그림 저장 위치. None 이면 저장하지 않음
        page_stats   페이지별 텍스트 출처 통계 (converters.pdf.converter 제공)
        source_path  원본 PDF 경로. 주면 그림 영역의 텍스트 밀도를 재어
                     표 오분류 후보를 표시한다 (LLM 호출 없음)
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
                    # 셀 텍스트를 나중에 갈아끼우려면 원본 객체가 필요하다.
                    # markdown 문자열만 남기면 행·열 구조를 되살릴 수 없다.
                    source_item=item,
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
                source_path=source_path,
                page_no=page,
                bbox=record.bbox,
            )
            info.bbox = record.bbox
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
                page_parts[page].append(normalize_pdf_text(chunk.strip()))
                record.outcome = "text"
                record.text = preview_text(chunk)
                record.char_count = len(chunk.strip())
            else:
                record.outcome = "dropped"

        page_layout[page].append(record)

    if source_path is not None:
        _mark_table_candidates(source_path, page_images)
        _inject_region_text(page_parts, page_images)

    from docstruct.core.config import get_settings

    settings = get_settings()
    pages: list[PageContent] = []

    # **빈 쪽도 자리를 지킨다** (0.5.42). 예전에는 `page_parts` 에 들어온
    # 쪽만 돌아, 내용이 하나도 없는 쪽이 **번호째 사라졌다.**
    #
    # 실측(병무청 PDF): 쪽 31·54·68 이 없어 `page_count` 가 94 인데 번호는
    # 1~97 이었다. `failed_pages` 는 비어 있었으므로 **조용히 사라진** 것이다.
    # 그 빈자리 때문에 쪽 맞춤이 PDF 에 없는 쪽을 만들어 냈고(align 96쪽),
    # 표 블록이 두 쪽을 건너뛴 것처럼 보였다.
    #
    # 쪽은 지면의 사실이다 — 내용이 없다고 없어지지 않는다. 있는 그대로
    # 비운 채 낸다.
    for page_no in _all_page_numbers(doc, page_parts, page_tables, page_images):
        tables = page_tables.get(page_no, [])
        images = page_images.get(page_no, [])
        stat = (page_stats or {}).get(page_no, {})
        source = stat.get("text_source", "unmeasured")
        ratio = stat.get("ocr_ratio")
        cells = stat.get("cell_count")

        body = "\n\n".join(page_parts.get(page_no) or [])

        # 셀 계측은 신뢰할 수 없을 때가 많습니다(Docling 이 셀을 버림).
        # 반면 "본문이 비었는가" 는 확실한 신호이므로, 측정이 안 된 상태에서
        # 본문까지 비어 있으면 그때만 실제 실패(empty)로 봅니다.
        if source == "unmeasured" and not body.strip():
            source = "empty"

        # **빈 쪽에는 그렇게 적는다** (0.5.42). 내용이 없는 것과 판독이
        # 실패한 것은 다르다 — 지면이 정말 비었을 수도 있고(간지), 우리가
        # 못 읽었을 수도 있다. 어느 쪽인지 결과물이 말해야 한다.
        blank = not body.strip() and not tables and not images

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
        text_blocks = len(page_parts.get(page_no) or []) - len(tables) - len(images)
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
                "docstruct.images.picture",
                "그림 추출",
                f"{len(images)}개"
                + (
                    f" · 설명 {sum(1 for i in images if i.description)}건"
                    if any(i.description for i in images)
                    else " · 설명 없음"
                ),
            )

        if blank:
            trace.add("docstruct.extractors.pdf", "빈 쪽",
                      "본문·표·그림이 모두 없습니다 — 간지이거나 판독하지 "
                      "못한 지면입니다", status="warn")

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


def _inject_region_text(
    page_parts: dict[int, list[str]],
    page_images: dict[int, list[ImageInfo]],
) -> None:
    """도표로 판정된 그림의 텍스트를 본문에 넣는다.

    입력:
        page_parts   페이지별 본문 조각 (제자리 갱신)
        page_images  페이지별 그림 메타
    출력: 없음
    비고:
        조직도·흐름도는 글자가 많지만 격자가 아니다. 표로 만들면 의미가
        망가지고, 그림으로 두면 글자가 통째로 사라진다. 그림 placeholder
        바로 뒤에 원문을 넣어 **둘 다** 남긴다.

        예) 성과목표관리 추진체계 조직도 — 533자가 그림 안에 갇혀 있었다.
    """
    for page_no, images in page_images.items():
        parts = page_parts.get(page_no)
        if not parts:
            continue
        for info in images:
            # 산식(formula)도 본문으로 흘려보낸다 — 그림으로 두면 내용이
            # 사라지고, 표로 두면 없는 격자가 생긴다 (H12-a).
            if info.region_kind not in ("text", "formula") or not info.region_text:
                continue
            text = normalize_pdf_text(info.region_text.strip())
            if not text:
                continue
            placeholder = info.placeholder
            for index, chunk in enumerate(parts):
                if placeholder and placeholder in chunk:
                    parts.insert(index + 1, text)
                    break
            else:
                parts.append(text)
            _log.info("%s 의 텍스트 %d자를 본문에 넣었습니다", info.id, len(text))


def _mark_table_candidates(
    source_path: str | Path,
    page_images: dict[int, list[ImageInfo]],
) -> None:
    """그림 중 표일 가능성이 있는 것을 표시한다.

    입력:
        source_path  원본 PDF 경로
        page_images  페이지 번호 → ImageInfo 목록 (제자리에서 갱신)
    출력: 없음
    비고:
        레이아웃 모델이 표를 PICTURE 로 분류하면 TableFormer 가 돌지 않아
        내용이 텍스트화되지 않는다. 여기서 후보만 골라 두면 표 평가 LLM 이
        **이미 나가는 호출에** 얹어 판정할 수 있다 — 호출이 늘지 않는다.

        사진·로고는 영역 안 글자 수가 0 에 가까워 여기서 걸러진다.
    """
    from docstruct.converters.pdf.region_kind import RegionKind, classify_region
    from docstruct.converters.pdf.text_probe import probe_regions

    regions: dict[str, tuple[int, dict[str, float]]] = {}
    lookup: dict[str, ImageInfo] = {}
    for page_no, images in page_images.items():
        for info in images:
            if info.bbox:
                regions[info.id] = (page_no, info.bbox)
                lookup[info.id] = info
    if not regions:
        return

    for image_id, density in probe_regions(source_path, regions).items():
        info = lookup[image_id]
        info.text_chars = density.chars
        info.text_lines = density.lines
        # 표 후보가 아니어도 판정은 돌린다. 도표는 표 문턱(80자·3줄)에
        # 못 미쳐도 본문으로 뽑을 값어치가 있다.
        info.region_text = density.text
        page_no, bbox = regions[image_id]
        verdict = classify_region(
            source_path, page_no, bbox, char_count=density.chars
        )
        info.region_kind = verdict.kind.value
        info.region_kind_reason = verdict.reason

        if verdict.kind is RegionKind.TABLE:
            info.table_candidate = True
            _log.info("%s 는 표로 보입니다 (%s) — 평가 대상에 올립니다",
                      image_id, verdict.reason)
        elif verdict.kind is RegionKind.TEXT:
            _log.info("%s 는 도표·텍스트로 보입니다 (%s) — 본문으로 뽑습니다",
                      image_id, verdict.reason)
        elif verdict.kind is RegionKind.CHART:
            # 값이 그림 안에 있어 텍스트로 옮겨지지 않는다. 여기서 표시하지
            # 않으면 무엇을 놓쳤는지도 남지 않는다.
            _log.info("%s 는 그래프로 보입니다 (%s) — 값은 그림 안에 있습니다",
                      image_id, verdict.reason)
        elif verdict.kind is RegionKind.FORMULA:
            _log.info("%s 는 산식 배열로 보입니다 (%s) — 본문으로 뽑고 "
                      "캡처 표 읽기에서 뺍니다", image_id, verdict.reason)
        else:
            _log.debug("%s 는 그림으로 둡니다 (%s)", image_id, verdict.reason)
