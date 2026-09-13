"""실험 ⑩ — 스캔 지면 선 검출 격자 (scan_grid, 가설 H8).

입력:
    스캔 지면 선 검출
출력:
    격자 (H8)

역할:
    벡터가 없는 스캔 쪽에서 **렌더 이미지의 가로/세로 선**을 찾아 격자를
    세운다. 격자 세우기와 병합 결정식은 ⑨(line_grid)와 같은 코드를 쓴다 —
    근거(벡터 → 화소)만 다르다.
호출부:
    pipeline (실험이 켜졌을 때)
설정:
    DOCSTRUCT_EXP_SCAN_GRID=1 (기본 꺼짐)

왜 있는가
--------
"한 유형에서 못 쓴다고 축을 버리지 않는다" — ⑦(결정 복원)은 스캔 PDF 에서
벡터가 없어 못 돈다. 같은 축(물리 격자)을 화소에서 잇는 것이 H8 이다.
SPARTAN(Sci. Reports 2026)이 이 경로의 실증이다: 학습·GPU 없이 선 검출
휴리스틱만으로 산업 배치를 겨냥했다.

SIFT·HOG 같은 특징점은 여기 과하다 — 표의 선은 **방향성 긴 어두운 띠**라
행/열 방향 어두운 연속 구간(run) 탐지가 정확하고 설명 가능하다.

전제와 한계
---------
    · 기울기(skew)가 크면 선이 여러 행/열에 걸쳐 끊긴다 — 심하면 격자가
      안 서고, 그 경우 조용히 물러난다 (현행 OCR·VLM 경로 유지)
    · 점선·저해상도에서 덮음 비율(SEPARATOR_COVER)이 안 차면 병합으로
      오인할 수 있다 — run 이음(GAP_PX)으로 일부를 흡수한다
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from docstruct.experiments.tsr.measure.line_grid import lattice_cells
from docstruct.experiments.registry import Experiment, register
from docstruct.experiments.tsr.measure.vector_grid import (
    HIGH_COVERAGE,
    MIN_GAP,
    _detected_cells,
    compare_grids,
)

if TYPE_CHECKING:
    from docstruct.models import PageContent

_log = logging.getLogger(__name__)

#: 렌더 배율. 2.0 이면 A4 가 약 1190×1684px — 0.5pt 선도 1px 이상이 된다.
RENDER_SCALE = 2.0
#: 이보다 어두우면(0~255) 잉크로 본다. 스캔 배경 얼룩(200±)을 피한 값.
INK_THRESHOLD = 160
#: 선으로 볼 최소 길이 — 쪽 너비 대비 비율. 글자 획(짧다)을 거른다.
MIN_LINE_RATIO = 0.06
#: 어두운 run 사이 이만큼(px)의 끊김은 이어 붙인다 — 점선·스캔 결손 흡수.
GAP_PX = 3
#: 같은 선의 이웃 행/열 묶음 허용(px).
MERGE_PX = 3


def _runs(mask_line, min_len: int) -> list[tuple[int, int]]:
    """한 줄에서 어두운 연속 구간을 찾는다 (GAP_PX 이하 끊김은 잇는다).

    입력: mask_line — bool 배열 한 줄, min_len — 최소 길이(px)
    출력: [(시작, 끝)] — 끝은 미포함
    """
    import numpy as np

    indices = np.flatnonzero(mask_line)
    if indices.size == 0:
        return []
    out: list[tuple[int, int]] = []
    start = previous = int(indices[0])
    for index in indices[1:]:
        index = int(index)
        if index - previous > GAP_PX + 1:
            if previous - start + 1 >= min_len:
                out.append((start, previous + 1))
            start = index
        previous = index
    if previous - start + 1 >= min_len:
        out.append((start, previous + 1))
    return out


def render_segments(
    pdf_path, page_no: int, *, scale: float = RENDER_SCALE,
) -> tuple[list, list, tuple[float, float]] | None:
    """쪽을 렌더해 가로/세로 선분을 pt(TOPLEFT) 좌표로 낸다.

    입력: pdf_path — PDF, page_no — 1부터, scale — 렌더 배율
    출력: (가로선 [(y,x0,x1)], 세로선 [(x,y0,y1)], (쪽 너비, 높이) pt).
          렌더 실패 시 None
    비고:
        방향성 run 탐지다: 행마다 긴 어두운 구간 → 가로선 후보, 이웃 행의
        겹치는 후보를 한 선으로 묶는다(두께 흡수). 세로는 전치와 같다.
    """
    try:
        import numpy as np
        import pypdfium2 as pdfium
    except ImportError:
        return None

    try:
        document = pdfium.PdfDocument(str(pdf_path))
    except Exception as exc:                     # noqa: BLE001
        _log.debug("PDF 를 열지 못했습니다: %s", exc)
        return None
    try:
        index = page_no - 1
        if not 0 <= index < len(document):
            return None
        page = document[index]
        size_pt = page.get_size()
        bitmap = page.render(scale=scale, grayscale=True)
        image = np.asarray(bitmap.to_pil(), dtype=np.uint8)
        if image.ndim == 3:
            image = image[..., 0]
        mask = image < INK_THRESHOLD
        height_px, width_px = mask.shape
        to_pt = 1.0 / scale

        def sweep(matrix, min_len):
            """행 방향으로 훑어 (줄 번호, 시작, 끝) run 을 모은다."""
            found = []
            for line_no in range(matrix.shape[0]):
                for start, end in _runs(matrix[line_no], min_len):
                    found.append((line_no, start, end))
            return found

        def merge(found):
            """이웃 줄의 겹치는 run 을 한 선으로 (두께·조각 흡수)."""
            found.sort()
            lines = []                            # [줄들, 시작, 끝]
            for line_no, start, end in found:
                for item in lines:
                    if (line_no - item[0][-1] <= MERGE_PX
                            and start < item[2] + GAP_PX
                            and end > item[1] - GAP_PX):
                        item[0].append(line_no)
                        item[1] = min(item[1], start)
                        item[2] = max(item[2], end)
                        break
                else:
                    lines.append([[line_no], start, end])
            return [(sum(ls) / len(ls), s, e) for ls, s, e in lines]

        min_h = int(width_px * MIN_LINE_RATIO)
        min_v = int(height_px * MIN_LINE_RATIO)
        horizontal = [(y * to_pt, x0 * to_pt, x1 * to_pt)
                      for y, x0, x1 in merge(sweep(mask, min_h))]
        vertical = [(x * to_pt, y0 * to_pt, y1 * to_pt)
                    for x, y0, y1 in merge(sweep(mask.T, min_v))]
        return horizontal, vertical, size_pt
    except Exception as exc:                     # noqa: BLE001
        _log.debug("%s쪽 렌더 선 검출 실패: %s", page_no, exc)
        return None
    finally:
        document.close()


def run(pages: list[PageContent], **kwargs) -> int:
    """스캔 쪽의 선 검출 격자와 인식 결과를 견준다.

    입력: pages — 페이지 목록 (제자리 갱신), pdf_path — 원본 경로
    출력: 표시한 표 수
    비고:
        표시만 한다 (⑥·⑨와 같은 계약). **벡터 선분이 있는 쪽은 렌더
        없이 건너뛴다** — 그 쪽은 ⑨의 영역이고, 렌더 선 검출은 같은
        근거의 열화판이다. 스캔 쪽에는 벡터가 없어 자연히 통과하므로
        합본(텍스트+스캔 섞임)에서도 쪽 단위로 맞게 갈린다.
    """
    source = kwargs.get("pdf_path")
    if not source:
        return 0

    from docstruct.experiments.tsr.measure.line_grid import _clip, page_segments

    marked = 0
    segments_cache: dict[int, tuple | None] = {}
    vector_cache: dict[int, bool] = {}
    for page in pages:
        if not page.tables or not isinstance(page.page_no, int):
            continue
        for table in page.tables:
            bbox = table.bbox
            if not bbox:
                continue
            # **벡터 게이트** — 이 쪽에 벡터 선분(괘선·사각형 모서리)이
            # 있으면 ⑨의 영역이다: 렌더 선 검출은 같은 근거의 열화판이라
            # 이득이 없고, 쪽마다 렌더하는 비용만 든다. 스캔 쪽에는
            # 벡터가 없어 자연히 통과한다 — 합본(텍스트+스캔 섞임)에서도
            # 쪽 단위로 맞게 갈린다. 문서 단위 판정(looks_scanned)과
            # 별개로, 실험은 **쪽 단위 자기선택**을 지킨다.
            if page.page_no not in vector_cache:
                h_vec, v_vec = page_segments(source, page.page_no)
                vector_cache[page.page_no] = bool(h_vec or v_vec)
            if vector_cache[page.page_no]:
                continue
            if page.page_no not in segments_cache:
                segments_cache[page.page_no] = render_segments(source, page.page_no)
            got = segments_cache[page.page_no]
            if got is None:
                continue
            horizontal, vertical, _ = got

            h = _clip(horizontal, bbox["t"], bbox["b"], bbox["l"], bbox["r"])
            v = _clip(vertical, bbox["l"], bbox["r"], bbox["t"], bbox["b"])
            lattice = lattice_cells(h, v) if (h and v) else None
            if lattice is None:
                continue
            cells, rows, cols = lattice
            report = compare_grids(cells, _detected_cells(table.cells))
            if len(report["missing"]) < MIN_GAP:
                continue
            confidence = ("high" if report["coverage"] >= HIGH_COVERAGE
                          else "low")
            table.scan_grid = {
                "physical": report["physical"],
                "detected": report["detected"],
                "missing": report["missing"],
                "extra": report["extra"],
                "coverage": report["coverage"],
                "confidence": confidence,
                "cells": len(cells), "rows": rows, "cols": cols,
            }
            marked += 1
            page.trace.add(
                "experiments.tsr.measure.scan_grid", "선 검출 격자 차이",
                f"{table.id} · 렌더 선 격자에 있으나 인식에 없는 병합 "
                f"{len(report['missing'])}개 (덮개 {report['coverage']} · "
                f"신뢰 {confidence})",
                status="warn" if confidence == "high" else "info")
    return marked


register(Experiment(
    key="scan_grid",
    title="스캔 선 검출 격자",
    purpose="벡터가 없는 스캔 쪽에서 렌더 이미지의 선으로 격자를 세움 (T5)",
    origin="가설 H8 — SPARTAN(2026) 선 검출 휴리스틱 계보. SIFT·HOG 는 이 "
           "문제에 과함(방향성 run 이 정확·설명 가능)",
    run=run,
    formats=("pdf",),
    needs=("geometry",),
    status="testing",
    note="⑦·⑨의 축(물리 격자)을 스캔으로 잇는다 — 근거만 벡터에서 화소로 "
         "바뀌고 격자 세우기·병합 결정식(⑨)은 같은 코드다. 기울기·점선· "
         "저해상도가 한계이며 격자가 안 서면 조용히 물러난다(현행 OCR·VLM "
         "경로 유지). 표시만 한다.",
    knobs={"DOCSTRUCT_EXP_SCAN_GRID": "1 이면 켬 (기본 꺼짐)"},
))
