"""실험 ③ — 셀↔조각 매칭을 양방향으로 견준다.

무엇을 보완하는가
--------------
셀은 맞는데 텍스트가 옆 칸으로 들어가는 문제. 지금 `cell_match` 는 **한
방향**(조각 → 어느 셀)으로만 배정한다. 반대 방향(셀 → 어느 조각)으로도
풀어 답이 다른 자리를 찾으면, 그곳이 애매한 지점이다.

어디서 빌린 발상인가
-----------------
TFLOP 은 텍스트 위치를 모델 입력으로 넣어 정렬 문제를 완화한다. 우리는
모델 안에 넣을 수 없지만, **사후에 양방향으로 견주는** 것은 좌표만으로 된다.

한계
----
- 불일치를 **표시만** 한다. 어느 쪽이 옳은지는 판단하지 않는다.
- 좌표가 있어야 한다 — HWP 계열은 대상이 아니다.
"""
from __future__ import annotations

import logging

from docstruct.experiments.registry import Experiment, register
from docstruct.models import PageContent

_log = logging.getLogger(__name__)


def disagreements(cell_boxes, fragments) -> list[int]:
    """두 방향 배정이 어긋나는 조각을 찾는다.

    입력: cell_boxes — 셀 상자 목록, fragments — (상자, 텍스트) 목록
    출력: 어긋난 조각 인덱스 목록
    비고:
        정방향은 조각마다 가장 많이 겹치는 셀을 고르고, 역방향은 셀마다
        가장 많이 겹치는 조각을 고른다. 서로 가리키지 않으면 애매한 자리다.
    """
    if not cell_boxes or not fragments:
        return []

    forward: dict[int, int] = {}
    for index, (box, _) in enumerate(fragments):
        best, ratio = -1, 0.0
        for position, cell in enumerate(cell_boxes):
            value = box.overlap_ratio(cell)
            if value > ratio:
                best, ratio = position, value
        if best >= 0:
            forward[index] = best

    backward: dict[int, int] = {}
    for position, cell in enumerate(cell_boxes):
        best, ratio = -1, 0.0
        for index, (box, _) in enumerate(fragments):
            value = cell.overlap_ratio(box)
            if value > ratio:
                best, ratio = index, value
        if best >= 0:
            backward[position] = best

    return [
        index for index, position in forward.items()
        if backward.get(position) not in (index, None)
    ]


def run(pages: list[PageContent], *, scale: float = 2.0, **_kwargs) -> int:
    """양방향이 어긋나는 표를 표시한다.

    입력: pages — 페이지 목록, scale — 렌더 배율
    출력: 표시한 표 수
    """
    from pathlib import Path

    from docstruct.converters.pdf.cell_match import Box, box_of, from_pixels
    from docstruct.converters.pdf.rapidocr_ko import read_image

    marked = 0
    for page in pages:
        image = page.page_image_path
        if not image or not page.tables or not Path(image).is_file():
            continue
        try:
            lines = read_image(image)
        except Exception as exc:                 # noqa: BLE001
            _log.warning("%s쪽 조각을 읽지 못했습니다: %s", page.page_no, exc)
            continue
        fragments = [(from_pixels(box_of(ln.box), scale), ln.text)
                     for ln in lines if ln.box]

        for table in page.tables:
            item = getattr(table, "source_item", None)
            cells = [c for c in (getattr(getattr(item, "data", None),
                                         "table_cells", None) or [])
                     if getattr(c, "bbox", None) is not None]
            if not cells or not fragments:
                continue
            boxes = [Box(float(c.bbox.l), float(c.bbox.t),
                         float(c.bbox.r), float(c.bbox.b)) for c in cells]
            inside = [(b, t) for b, t in fragments
                      if any(b.overlap_ratio(x) > 0 for x in boxes)]
            odd = disagreements(boxes, inside)
            if not odd:
                continue
            table.match_disagreements = len(odd)
            marked += 1
            page.trace.add(
                "experiments.two_way_match", "매칭 불일치",
                f"{table.id} · 조각 {len(odd)}개가 양방향에서 다르게 배정됨",
                status="warn")
    return marked


register(Experiment(
    key="two_way_match",
    title="셀↔조각 매칭 양방향 대조",
    purpose="셀은 맞는데 텍스트가 옆 칸으로 들어가는 문제",
    origin="TFLOP — 텍스트 위치를 구조 판단에 쓴다는 발상",
    formats=("pdf",),
    status="proposed",
    note="표시만 한다. 좌표가 필요해 HWP 계열은 대상 아님. "
         "스캔본은 좌표가 OCR 결과라 정확도가 다르다.",
    run=run,
))
