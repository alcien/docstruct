"""표 셀과 OCR 조각을 좌표로 맞춘다.

역할:
    TableFormer 가 만든 셀(행·열·병합·bbox)은 그대로 두고, 그 안의 **텍스트만**
    한국어 OCR 결과로 갈아끼운다.
호출부:
    docstruct.converters.pdf.rapidocr_ko (표 텍스트 교체 시)
입력: 셀 목록(bbox 보유)과 OCR 조각 목록(bbox 보유)
출력: 셀 인덱스 → 이어 붙인 텍스트

왜 구조를 건드리지 않는가
----------------------
TableFormer 는 레이아웃·표 구조 인식이라 OCR 과 계층이 다르다. 실제 문서에서
셀 10개가 모두 bbox 를 갖고 `row_span`·`col_span` 도 온전했으나, `text` 만
중국어였다(`品品品`, `昆品`). 인식 언어가 틀린 것이지 구조가 틀린 것이
아니므로 텍스트만 바꾸면 된다.

좌표 기준
--------
셀 bbox 는 PDF 포인트, 원점은 TOPLEFT 다. OCR 조각은 렌더된 이미지의 픽셀
좌표다. 같은 원점을 쓰므로 배율만 나누면 맞는다.

    포인트 = 픽셀 / render_scale

실측(595.0 x 841.9 포인트 문서, scale=2.0)에서 렌더 결과가 1190 x 1684
픽셀이었다 — 정확히 2배다.

왜 IoU 최대값만으로는 부족한가
---------------------------
조각 하나가 셀 경계에 걸치면 여러 셀과 겹친다. 각 조각을 겹침이 가장 큰
셀에 붙이면 **한 셀이 조각을 독점하고 옆 셀이 비는** 배정이 나온다. 표는
셀마다 내용이 따로 있는 구조이므로 전역 최적으로 푸는 편이 맞다.

다만 **조각 수와 셀 수가 다르고**(한 셀에 여러 줄이 들어간다) 조각이 어느
셀에도 안 속할 수 있어, 일대일 할당이 아니라 **겹침 비율 기준의 다대일
배정**을 쓴다. 조각 쪽에서 본 겹침 비율이 임계 이상인 셀 중 가장 큰 곳에
붙인다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

_log = logging.getLogger(__name__)

#: 조각이 셀에 속한다고 볼 최소 겹침 비율 (조각 넓이 기준).
#: 표 밖 본문이나 괘선 조각이 셀에 살짝 걸치는 일이 있어 절반은 넘겨야 한다.
#:
#: **OCR 신뢰도 임계와 다르다.** 이 값을 낮춰도 잡음이 늘지 않는다 —
#: 이미 신뢰도 검사를 통과한 조각들 중 **어느 셀에 넣을지**만 정하기
#: 때문이다. 셀 경계에 걸친 조각이 더 많이 배정될 뿐이다.
OVERLAP_ENV = "DOCSTRUCT_CELL_MIN_OVERLAP"
#: 셀 기준 배정으로 바꾸면서 낮췄다. 예전에는 조각마다 셀 하나만 골라
#: 0.5 가 필요했지만, 이제는 여러 셀이 나눠 가질 수 있어 경계에 걸친
#: 조각을 회수하려면 더 낮아야 한다. 실문서에서 표 하나에 58개가 배정
#: 실패했고, 그 대부분이 칸을 넘은 조각이었다.
MIN_OVERLAP = 0.3

#: 이 비율 이상 한 셀에 들어간 조각은 **그 셀에만** 준다.
#: 없으면 경계를 살짝 스친 셀까지 같은 텍스트를 받아 표가 같은 말로
#: 뒤덮인다.
DOMINANT_OVERLAP = 0.7


def min_overlap_setting() -> float:
    """셀 배정에 쓸 최소 겹침 비율.

    입력: 없음 (`DOCSTRUCT_CELL_MIN_OVERLAP`)
    출력: 0~1 실수. 잘못된 값이면 기본값
    비고:
        셀 bbox 가 실제 글자 영역보다 좁게 잡히는 표가 있어, 임계를 낮춰
        보면 원인이 TableFormer 쪽인지 임계 쪽인지 가려진다.
    """
    import os

    raw = os.environ.get(OVERLAP_ENV, "").strip()
    if not raw:
        return MIN_OVERLAP
    try:
        value = float(raw)
    except ValueError:
        _log.warning("%s 값이 숫자가 아닙니다: %r", OVERLAP_ENV, raw)
        return MIN_OVERLAP
    return value if 0.0 < value <= 1.0 else MIN_OVERLAP

#: 같은 줄로 볼 세로 오차 (포인트). 글자 높이의 절반쯤.
LINE_TOLERANCE = 6.0


@dataclass(frozen=True)
class Box:
    """좌표 상자 (PDF 포인트, 원점 TOPLEFT).

    입력(필드): left, top, right, bottom
    """

    left: float
    top: float
    right: float
    bottom: float

    @property
    def area(self) -> float:
        """넓이.

        입력: 없음
        출력: 0 이상 실수. 뒤집힌 상자는 0
        """
        return max(0.0, self.right - self.left) * max(0.0, self.bottom - self.top)

    def overlap_area(self, other: "Box") -> float:
        """다른 상자와 겹치는 넓이.

        입력: other — 비교할 상자
        출력: 겹침 넓이. 겹치지 않으면 0
        """
        width = min(self.right, other.right) - max(self.left, other.left)
        height = min(self.bottom, other.bottom) - max(self.top, other.top)
        return max(0.0, width) * max(0.0, height)

    def iou(self, other: "Box") -> float:
        """Intersection over Union.

        입력: other — 비교할 상자
        출력: 0~1 실수
        비고:
            진단·기록용이다. 배정은 `overlap_ratio` 로 한다 — 조각이 셀보다
            훨씬 작을 때 IoU 는 낮게 나와 임계를 잡기 어렵다.
        """
        intersection = self.overlap_area(other)
        union = self.area + other.area - intersection
        return intersection / union if union > 0 else 0.0

    def overlap_ratio(self, other: "Box") -> float:
        """**내 넓이** 중 다른 상자와 겹치는 비율.

        입력: other — 비교할 상자
        출력: 0~1 실수
        비고:
            조각이 셀 안에 온전히 들어가면 1.0 이다. 조각과 셀의 크기 차가
            커도 값이 안정적이라 임계를 정하기 쉽다.
        """
        return self.overlap_area(other) / self.area if self.area > 0 else 0.0


def from_pixels(box: Box, scale: float) -> Box:
    """렌더 이미지 픽셀 좌표를 PDF 포인트로 바꾼다.

    입력: box — 픽셀 좌표 상자, scale — 렌더 배율
    출력: 포인트 좌표 상자
    비고: 두 좌표계 모두 원점이 TOPLEFT 라 배율만 나누면 된다.
    """
    if scale <= 0:
        return box
    return Box(box.left / scale, box.top / scale,
               box.right / scale, box.bottom / scale)


def box_of(points: list[tuple[float, float]]) -> Box:
    """꼭짓점 목록을 감싸는 상자.

    입력: points — [(x, y), ...]
    출력: Box. 점이 없으면 넓이 0 인 상자
    비고: OCR 은 기울어진 사각형을 줄 수 있어 외접 상자로 바꾼다.
    """
    if not points:
        return Box(0.0, 0.0, 0.0, 0.0)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return Box(min(xs), min(ys), max(xs), max(ys))


def assign(
    cells: list[Box],
    fragments: list[tuple[Box, str]],
    *,
    min_overlap: float | None = None,
) -> dict[int, list[tuple[Box, str]]]:
    """조각을 셀에 배정한다.

    입력:
        cells        셀 상자 목록 (포인트 좌표)
        fragments    (조각 상자, 텍스트) 목록 (포인트 좌표)
        min_overlap  이 비율 미만이면 어느 셀에도 넣지 않는다
    출력: 셀 인덱스 → 그 셀에 배정된 조각 목록
    비고:
        **셀 기준으로 본다.** 조각마다 겹침이 가장 큰 셀 하나에만 넣던
        방식은, 두 셀에 걸친 조각이 한쪽만 채우고 다른 쪽을 비워 두었다.
        실문서에서 표 안 미배정이 표 하나에 58개까지 나왔고, 왼쪽 열이
        통째로 빈 표도 있었다(`지방세법`·`종합부동산세법` 이 OCR 에는
        읽혔는데 셀에는 없었다).

        이제 셀마다 자기 영역과 겹치는 조각을 모은다. 한 조각이 두 셀에
        걸치면 양쪽이 모두 가져간다 — 표 괘선이 얇아 조각이 칸을 넘는 일이
        흔하고, 한쪽을 비우는 것보다 양쪽에 넣는 편이 내용을 덜 잃는다.

        **다만 무제한 복제는 막는다.** 조각이 어느 셀에 대해서도 임계를
        넘지 못하면 버리고, `dominant_overlap` 이상 겹치는 셀이 하나라도
        있으면 그 셀들만 가져간다 — 살짝 스친 셀까지 같은 텍스트를 받으면
        표 전체가 같은 말로 뒤덮인다.
    """
    threshold = min_overlap if min_overlap is not None else min_overlap_setting()
    result: dict[int, list[tuple[Box, str]]] = {}

    for fragment_box, text in fragments:
        ratios = [
            (index, fragment_box.overlap_ratio(cell))
            for index, cell in enumerate(cells)
        ]
        hits = [(index, ratio) for index, ratio in ratios if ratio >= threshold]
        if not hits:
            continue
        # 압도적으로 한 셀에 들어간 조각은 그 셀에만 준다. 나머지 셀은
        # 경계에 스친 것뿐이라 같은 텍스트를 받을 이유가 없다.
        best = max(ratio for _, ratio in hits)
        if best >= DOMINANT_OVERLAP:
            hits = [(index, ratio) for index, ratio in hits
                    if ratio >= DOMINANT_OVERLAP]
        for index, _ in hits:
            result.setdefault(index, []).append((fragment_box, text))
    return result


def join_fragments(fragments: list[tuple[Box, str]]) -> str:
    """한 셀에 배정된 조각들을 읽기 순서로 잇는다.

    입력: fragments — (상자, 텍스트) 목록
    출력: 이어 붙인 텍스트
    비고:
        위에서 아래로, 같은 높이면 왼쪽에서 오른쪽으로 잇는다. 셀 안에서
        줄이 나뉜 경우가 많아 줄바꿈이 아니라 공백으로 잇는다 — 셀 텍스트에
        줄바꿈이 들어가면 markdown 표가 깨진다.
    """
    if not fragments:
        return ""
    ordered = sorted(
        fragments,
        key=lambda item: (round(item[0].top / LINE_TOLERANCE), item[0].left),
    )
    return " ".join(text for _, text in ordered if text)


def fill_cells(
    cell_boxes: list[Box],
    fragments: list[tuple[Box, str]],
    *,
    min_overlap: float | None = None,
) -> tuple[dict[int, str], int]:
    """셀별 텍스트를 만든다.

    입력:
        cell_boxes   셀 상자 목록
        fragments    (조각 상자, 텍스트) 목록
        min_overlap  배정 임계
    출력: (셀 인덱스 → 텍스트, 어느 셀에도 안 들어간 조각 수)
    """
    assigned = assign(cell_boxes, fragments, min_overlap=min_overlap)
    used = sum(len(items) for items in assigned.values())
    texts = {index: join_fragments(items) for index, items in assigned.items()}
    return texts, len(fragments) - used
