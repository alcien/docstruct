"""레이아웃 모델 판정 결과 수집.

역할:
    Docling 의 레이아웃 모델(RT-DETR)이 페이지의 각 영역에 붙인 라벨과 좌표를
    원본 그대로 기록하고, 그 영역을 파이프라인이 무엇으로 바꿨는지 함께 남긴다.
    표가 깨졌을 때 원인이 레이아웃 오인식인지 이후 변환인지 구분하는 데 쓴다.
호출부:
    docstruct.extractors.pdf.extract_pdf_pages
    docstruct.report.write_layout_report / docstruct.preview.show_layout
출력:
    LayoutItem 목록 — 읽기 순서, 라벨, 좌표, 텍스트 미리보기, 파이프라인 처리 결과
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field  # noqa: F401
from typing import Any

#: DocLayNet 라벨 → 한국어 표기
LABEL_KO = {
    "caption": "캡션",
    "checkbox_selected": "체크박스(선택)",
    "checkbox_unselected": "체크박스(해제)",
    "code": "코드",
    "document_index": "목차",
    "footnote": "각주",
    "form": "양식",
    "formula": "수식",
    "key_value_region": "키-값 영역",
    "list_item": "목록 항목",
    "page_footer": "바닥글",
    "page_header": "머리글",
    "picture": "그림",
    "section_header": "절 제목",
    "table": "표",
    "text": "본문",
    "title": "제목",
}

#: 파이프라인 처리 결과 코드 → 설명
OUTCOME_KO = {
    "table": "표로 변환",
    "image": "이미지로 저장",
    "text": "본문에 포함",
    "dropped": "버려짐 (빈 내용)",
}


@dataclass
class LayoutItem:
    """레이아웃 모델이 인식한 영역 하나.

    입력(필드):
        order      읽기 순서 (문서 전체 기준, 0-based)
        page_no    페이지 번호
        label      레이아웃 모델이 붙인 원본 라벨 (DocLayNet 17종)
        bbox       페이지 좌표 {l, t, r, b}. 좌상단 원점, 단위 point
        text       내용 미리보기
        char_count 내용 길이
        outcome    파이프라인 처리 결과 (table | image | text | dropped)
        ref        산출물 식별자 (표 id, 이미지 경로 등)
    출력:
        to_dict()  document.json 의 page.layout 원소
    """

    order: int
    page_no: int
    label: str
    bbox: dict[str, float] | None = None
    text: str = ""
    char_count: int = 0
    outcome: str = ""
    ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON 직렬화용 dict.

        입력: 없음
        출력: 모든 필드를 담은 dict
        """
        return asdict(self)

    @property
    def label_ko(self) -> str:
        """라벨의 한국어 표기.

        입력: 없음
        출력: 매핑에 있으면 한국어, 없으면 원본 라벨
        """
        return LABEL_KO.get(self.label, self.label)

    @property
    def area(self) -> float | None:
        """영역 넓이 (point²).

        입력: 없음
        출력: bbox 가 있으면 넓이, 없으면 None
        """
        if not self.bbox:
            return None
        return abs(self.bbox["r"] - self.bbox["l"]) * abs(self.bbox["b"] - self.bbox["t"])


def label_name(label: Any) -> str:
    """DocItemLabel 이나 문자열에서 라벨 이름을 얻는다.

    입력: label — DocItemLabel enum 또는 문자열
    출력: 소문자 라벨 이름. 판별 불가 시 'unknown'
    """
    if label is None:
        return "unknown"
    value = getattr(label, "value", label)
    return str(value).lower()


def item_bbox(item: Any, doc: Any) -> dict[str, float] | None:
    """항목의 페이지 좌표를 좌상단 원점으로 얻는다.

    입력: item — Docling 항목, doc — DoclingDocument
    출력: {l, t, r, b} 또는 좌표를 얻지 못하면 None
    """
    prov_list = getattr(item, "prov", None) or []
    if not prov_list:
        return None
    bbox = getattr(prov_list[0], "bbox", None)
    if bbox is None:
        return None
    try:
        page = doc.pages.get(prov_list[0].page_no)
        if page is not None and getattr(page, "size", None) is not None:
            tl = bbox.to_top_left_origin(page_height=page.size.height)
            return {
                "l": round(tl.l, 1),
                "t": round(tl.t, 1),
                "r": round(tl.r, 1),
                "b": round(tl.b, 1),
            }
        return {
            "l": round(bbox.l, 1),
            "t": round(bbox.t, 1),
            "r": round(bbox.r, 1),
            "b": round(bbox.b, 1),
        }
    except Exception:
        return None


def preview_text(text: str, limit: int = 60) -> str:
    """내용 미리보기를 만든다.

    입력: text — 원본 문자열, limit — 최대 길이
    출력: 개행을 공백으로 바꾸고 limit 을 넘으면 잘라낸 문자열
    """
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def label_counts(items: list[LayoutItem]) -> dict[str, int]:
    """라벨별 개수를 센다.

    입력: items — LayoutItem 목록
    출력: {라벨: 개수}, 개수 내림차순
    """
    counts: dict[str, int] = {}
    for item in items:
        counts[item.label] = counts.get(item.label, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def overlapping_pairs(
    items: list[LayoutItem], min_ratio: float = 0.5
) -> list[tuple[LayoutItem, LayoutItem, float]]:
    """서로 겹치는 영역 쌍을 찾는다.

    입력:
        items      같은 페이지의 LayoutItem 목록
        min_ratio  작은 쪽 넓이 대비 겹침 비율 기준
    출력:
        (항목 A, 항목 B, 겹침 비율) 목록
    비고:
        레이아웃 모델이 같은 영역을 두 번 잡았거나 라벨이 갈린 경우를 찾는 데 쓴다.
    """
    result = []
    boxed = [i for i in items if i.bbox]
    for idx, a in enumerate(boxed):
        for b in boxed[idx + 1 :]:
            ax0, ay0 = a.bbox["l"], a.bbox["t"]
            ax1, ay1 = a.bbox["r"], a.bbox["b"]
            bx0, by0 = b.bbox["l"], b.bbox["t"]
            bx1, by1 = b.bbox["r"], b.bbox["b"]
            ix = max(0.0, min(ax1, bx1) - max(ax0, bx0))
            iy = max(0.0, min(ay1, by1) - max(ay0, by0))
            inter = ix * iy
            if inter <= 0:
                continue
            smaller = min(a.area or 0, b.area or 0)
            if smaller <= 0:
                continue
            ratio = inter / smaller
            if ratio >= min_ratio:
                result.append((a, b, ratio))
    return result
