"""그림 설명(VLM) 결과 조회.

역할:
    Docling 이 그림에 붙인 설명 텍스트를 꺼내고, 설명 생성 현황을
    점검할 수 있게 한다.
호출부:
    docstruct.media.picture, converters.pdf.converter
출력:
    설명 문자열 및 그림별 점검 결과
"""
from __future__ import annotations

from typing import Any


def picture_area_fraction(item, doc) -> float | None:
    """PictureItem bbox 가 페이지에서 차지하는 면적 비율.

    입력: item — PictureItem, doc — DoclingDocument
    출력: 0~1 비율. prov·페이지 정보가 없으면 None
    """
    prov_list = getattr(item, "prov", None) or []
    if not prov_list:
        return None
    prov = prov_list[0]
    page = doc.pages.get(prov.page_no)
    if page is None:
        return None
    page_area = page.size.width * page.size.height
    if page_area <= 0:
        return None
    return prov.bbox.area() / page_area


def get_picture_description_text(item) -> str:
    """그림의 LLM 설명 텍스트를 찾는다.

    입력: item — PictureItem
    출력: 설명 문자열. 없으면 빈 문자열
    동작: 신형 meta.description → meta.classification → legacy annotations
          순으로 찾는다. 분류만 있으면 "(classification only: …)" 로 표시한다.
    """
    meta = getattr(item, "meta", None)
    if meta is not None:
        desc = getattr(meta, "description", None)
        if desc is not None:
            text = (getattr(desc, "text", None) or "").strip()
            if text:
                return text
        classification = getattr(meta, "classification", None)
        if classification is not None:
            pred = getattr(classification, "predictions", None)
            if pred:
                main = pred[0]
                return f"(classification only: {getattr(main, 'class_name', main)})"

    for ann in getattr(item, "annotations", []) or []:
        text = getattr(ann, "text", None) or getattr(ann, "description", None)
        if text:
            return str(text).strip()
    return ""


def collect_picture_reports(doc, *, area_threshold: float = 0.05) -> list[dict[str, Any]]:
    """문서 내 모든 PictureItem 의 진단 정보를 모은다.

    입력: doc — DoclingDocument, area_threshold — 면적 임계값 (기본 0.05)
    출력: [{ref, page, area_fraction, skipped_by_area, description,
           has_llm_description}] 목록
    """
    from docling_core.types.doc.labels import DocItemLabel

    reports: list[dict[str, Any]] = []
    for item, _level in doc.iterate_items():
        if getattr(item, "label", None) != DocItemLabel.PICTURE:
            continue

        area = picture_area_fraction(item, doc)
        desc = get_picture_description_text(item)
        skipped_by_area = area is not None and area < area_threshold

        reports.append(
            {
                "ref": getattr(item, "self_ref", "?"),
                "page": item.prov[0].page_no if item.prov else None,
                "area_fraction": area,
                "area_threshold": area_threshold,
                "skipped_by_area": skipped_by_area,
                "description": desc,
                "has_llm_description": bool(
                    desc and not desc.startswith("(classification only:")
                ),
            }
        )
    return reports


def print_picture_reports(
    reports: list[dict[str, Any]],
    *,
    markdown: str = "",
    placeholder: str = "<!-- image -->",
) -> None:
    """PictureItem 진단 결과를 stdout 에 출력한다.

    입력:
        reports      collect_picture_reports 결과
        markdown     함께 세어볼 markdown (placeholder 개수 비교용)
        placeholder  그림 자리 표식 문자열
    출력: 없음 (stdout)
    """
    ph_count = markdown.count(placeholder) if markdown else 0
    if markdown:
        print(f"  markdown '{placeholder}' 개수: {ph_count}")

    print(f"  PictureItem 수: {len(reports)}")
    for i, r in enumerate(reports, 1):
        area = r["area_fraction"]
        area_str = f"{area:.4f}" if area is not None else "?"
        print(f"  [{i}] ref={r['ref']} page={r['page']} area={area_str} (threshold={r['area_threshold']})")

        if r["skipped_by_area"]:
            print("      → area threshold 미만 — LLM 설명 스킵됨")
        elif r["has_llm_description"]:
            print(f"      → LLM 설명 ({len(r['description'])}자): {r['description'][:200]}")
        elif r["description"]:
            print(f"      → {r['description']}")
        else:
            print("      → LLM 설명 없음 (API 미호출/실패 또는 enrichment 미실행)")

    if ph_count and not any(r["has_llm_description"] for r in reports):
        print()
        print("  참고: markdown은 기본적으로 <!-- image --> placeholder를 쓰고,")
        print("        LLM 설명은 그 아래 별도 문단으로 붙습니다 (meta.description).")
        print("        설명이 비어 있으면 placeholder만 보입니다.")
