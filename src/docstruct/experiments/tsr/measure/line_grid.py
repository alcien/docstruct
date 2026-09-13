"""실험 ⑨ — 괘선·모서리 합성 격자 (line_grid, 가설 H2+H3).

입력:
    괘선·모서리
출력:
    합성 격자 (H2+H3)

역할:
    배경 사각형의 **모서리**와 얇은 괘선(가로/세로 선)을 한 경계 목록으로
    합쳐 격자를 세운다. 병합은 SPARTAN(2026) 결정식으로 정한다 —
    **이웃 칸 사이를 가르는 선이 없으면 병합이다** (span = 관통선 + 1).
호출부:
    pipeline (실험이 켜졌을 때)
설정:
    DOCSTRUCT_EXP_LINE_GRID=false 로 끈다 — 0.4.3 승격 뒤 **기본 켬** (registry.DEFAULT_ON)

왜 있는가
--------
⑥·⑦의 근거(배경 사각형)는 표 전체를 덮을 때만 믿을 수 있다(덮개≥1.0
에서 정밀도 100%, 미만 83%). 덮개가 낮은 표(T2)와 사각형이 아예 없는
표(T3)에는 **괘선**이 남아 있다 — 사각형과 괘선을 합치면 덮개를 끌어올릴
수 있다는 것이 H2, 괘선만으로 격자가 선다는 것이 H3 다.

한 알고리즘이 세 유형을 다 받는 이유: 사각형의 네 모서리도 결국 선분이다.
T1 은 모서리만으로도 완전한 선 집합이 되고, T2 는 모서리+괘선, T3 는
괘선만 — **입력 선분의 출처만 다르고 격자 세우기는 같다.**
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from docstruct.experiments.registry import Experiment, register
from docstruct.experiments.tsr.measure.vector_grid import (
    BAND_TOLERANCE,
    HIGH_COVERAGE,
    MIN_GAP,
    _bands,
    _detected_cells,
    compare_grids,
)

if TYPE_CHECKING:
    from docstruct.models import PageContent

_log = logging.getLogger(__name__)

#: 선으로 볼 최대 두께(pt). 실측: 한글 내보내기 괘선은 0.5~1.2pt.
LINE_MAX_THICK = 1.8
#: 선으로 볼 최소 길이(pt). 짧은 장식·글자 획을 거른다.
LINE_MIN_LEN = 8.0
#: 경계 선분이 칸 구간을 이만큼 덮어야 "가르는 선" 으로 본다.
#: 1.0 이 아니라 0.6 인 이유: 괘선이 칸 안쪽 여백만큼 짧게 그려지는
#: 문서가 있다 — 그래도 가르는 선이다.
SEPARATOR_COVER = 0.6
#: 격자로 인정할 최소 경계 수 (행·열 각각). 2×2 미만은 표가 아니다.
MIN_BOUNDS = 3
#: 병합 비율이 이보다 크면 격자가 아니라 도해다. 실측 두 건이 근거:
#: 과기부 335쪽(도해) 1,967/1,967 · 주택과세금 179쪽(쪽 전체) 136/145 —
#: 정상 표는 행안부 최대치도 0.4 를 넘지 않았다 (table_1: 7/25).
MAX_MERGE_RATIO = 0.6


def page_segments(
    pdf_path, page_no: int,
) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    """이 쪽의 가로/세로 선분을 TOPLEFT 좌표로 낸다.

    입력: pdf_path — PDF 경로, page_no — 1부터
    출력: (가로선 [(y, x0, x1)], 세로선 [(x, y0, y1)])
    비고:
        두 출처를 합친다 — 얇은 도형(괘선)은 선 그대로, 큰 도형(셀 배경
        사각형)은 **네 모서리를 선분으로** 바꿔 넣는다. 한 목록이 되면
        아래 격자 세우기는 출처를 구분할 필요가 없다 (H2 합성의 구현).
    """
    try:
        import ctypes

        import pypdfium2 as pdfium
        import pypdfium2.raw as raw
    except ImportError:
        return [], []

    horizontal: list[tuple[float, float, float]] = []
    vertical: list[tuple[float, float, float]] = []
    try:
        document = pdfium.PdfDocument(str(pdf_path))
    except Exception as exc:                     # noqa: BLE001 - 진단 보조다
        _log.debug("PDF 를 열지 못했습니다: %s", exc)
        return [], []
    try:
        index = page_no - 1
        if not 0 <= index < len(document):
            return [], []
        page = document[index]
        height = page.get_size()[1]
        for obj in page.get_objects():
            if obj.type != raw.FPDF_PAGEOBJ_PATH:
                continue
            left = ctypes.c_float(); bottom = ctypes.c_float()
            right = ctypes.c_float(); top = ctypes.c_float()
            if not raw.FPDFPageObj_GetBounds(
                    obj.raw, ctypes.byref(left), ctypes.byref(bottom),
                    ctypes.byref(right), ctypes.byref(top)):
                continue
            l, b = left.value, height - top.value          # TOPLEFT 변환
            r, t = right.value, height - bottom.value
            width, tall = r - l, t - b
            if tall <= LINE_MAX_THICK and width >= LINE_MIN_LEN:
                horizontal.append(((b + t) / 2.0, l, r))   # 가로 괘선
            elif width <= LINE_MAX_THICK and tall >= LINE_MIN_LEN:
                vertical.append(((l + r) / 2.0, b, t))     # 세로 괘선
            elif width >= LINE_MIN_LEN and tall >= LINE_MIN_LEN:
                # 셀 배경 사각형 — 네 모서리를 선분으로 (H2 합성)
                horizontal.append((b, l, r))
                horizontal.append((t, l, r))
                vertical.append((l, b, t))
                vertical.append((r, b, t))
        return horizontal, vertical
    except Exception as exc:                     # noqa: BLE001
        _log.debug("%s쪽 선분을 읽지 못했습니다: %s", page_no, exc)
        return [], []
    finally:
        document.close()


def _clip(segments, lo, hi, lo2, hi2):
    """bbox 안의 선분만 (자리 lo~hi · 뻗음 lo2~hi2).

    입력: segments — (자리, 시작, 끝), lo/hi — 자리 허용 구간,
          lo2/hi2 — 뻗는 방향 허용 구간
    출력: 잘라낸 선분 목록
    """
    pad = BAND_TOLERANCE
    out = []
    for position, start, end in segments:
        if not (lo - pad <= position <= hi + pad):
            continue
        s, e = max(start, lo2), min(end, hi2)
        if e - s >= LINE_MIN_LEN * 0.5:
            out.append((position, s, e))
    return out


def _separated(segments, boundary: float, span_lo: float, span_hi: float) -> bool:
    """이 경계 자리의 선이 칸 구간을 가르는가.

    입력: segments — 그 방향 선분, boundary — 경계 좌표,
          span_lo/span_hi — 가를 칸 구간
    출력: 구간의 SEPARATOR_COVER 이상을 선이 덮으면 True
    비고:
        SPARTAN 의 결정식을 구간 덮음으로 구현했다 — "관통하는 선이
        있으면 갈라진 칸" 이다. 선이 조각나 있어도 덮은 길이를 합친다.
    """
    need = (span_hi - span_lo) * SEPARATOR_COVER
    if need <= 0:
        return True
    covered: list[tuple[float, float]] = []
    for position, start, end in segments:
        if abs(position - boundary) > BAND_TOLERANCE:
            continue
        s, e = max(start, span_lo), min(end, span_hi)
        if e > s:
            covered.append((s, e))
    covered.sort()
    total = 0.0
    cursor = None
    for s, e in covered:
        if cursor is None or s > cursor:
            total += e - s
            cursor = e
        elif e > cursor:
            total += e - cursor
            cursor = e
    return total >= need


def lattice_cells(
    horizontal: list[tuple[float, float, float]],
    vertical: list[tuple[float, float, float]],
) -> tuple[list[tuple[int, int, int, int]], int, int] | None:
    """선분에서 격자를 세우고 병합을 결정식으로 정한다.

    입력: horizontal — (y, x0, x1), vertical — (x, y0, y1)
    출력: ([(row, col, rowspan, colspan)], 행 수, 열 수). 격자가 아니면 None
    비고:
        칸 사이에 가르는 선이 없으면 한 셀이다 (union-find). 병합 결과가
        직사각형이 아니면 — L자 등 — 격자를 믿을 수 없으므로 버린다.
        없는 결함을 만드는 것보다 안 내는 편이 낫다.
    """
    ys = _bands([y for y, _, _ in horizontal])
    xs = _bands([x for x, _, _ in vertical])
    if len(xs) < MIN_BOUNDS or len(ys) < MIN_BOUNDS:
        return None
    rows, cols = len(ys) - 1, len(xs) - 1

    parent = list(range(rows * cols))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for r in range(rows):
        for c in range(cols):
            slot = r * cols + c
            if c + 1 < cols and not _separated(
                    vertical, xs[c + 1], ys[r], ys[r + 1]):
                union(slot, slot + 1)
            if r + 1 < rows and not _separated(
                    horizontal, ys[r + 1], xs[c], xs[c + 1]):
                union(slot, slot + cols)

    groups: dict[int, list[tuple[int, int]]] = {}
    for r in range(rows):
        for c in range(cols):
            groups.setdefault(find(r * cols + c), []).append((r, c))

    cells: list[tuple[int, int, int, int]] = []
    for slots in groups.values():
        r0 = min(r for r, _ in slots); r1 = max(r for r, _ in slots)
        c0 = min(c for _, c in slots); c1 = max(c for _, c in slots)
        if (r1 - r0 + 1) * (c1 - c0 + 1) != len(slots):
            return None                          # 직사각형이 아니다 — 격자 불신
        cells.append((r0, c0, r1 - r0 + 1, c1 - c0 + 1))
    merged = sum(1 for c in cells if c[2] > 1 or c[3] > 1)
    if cells and merged / len(cells) > MAX_MERGE_RATIO:
        return None                              # 도해가 격자 흉내를 낸 것
    cells.sort()
    return cells, rows, cols


#: 장식 경계로 볼 빈 띠의 최대 폭(pt). 이보다 넓은 빈 띠는 **이 쪽에서만
#: 값이 없는 진짜 열**이므로 접지 않는다.
#:
#: 실측 근거 — 두 종류가 섞여 있었다.
#:     가짜 (표 가장자리 겹선):  5.6 · 8.4 · 2.8pt
#:     진짜 (값이 빈 열):        41.9 · 42.0 · 47.6 · 88.3pt
#: 조달청 p65 의 "재정사업 평가명/결과" 열이 행안부 p63·p299 에서는 값이
#: 없어 비어 있을 뿐 지면에 실재한다 — 폭을 보지 않고 접으면 정답 8열을
#: 6열로 만든다. 두 무리 사이가 20pt 이상 벌어져 있어 10 은 안전한 자리다.
DECOR_MAX_WIDTH = 10.0

def _band_char_counts(pdf_path, page_no: int, xs: list[float],
                      top: float, bottom: float) -> list[int] | None:
    """세로 경계로 나뉜 띠마다 그 안의 글자 수를 센다.

    입력: pdf_path — 원본, page_no — 1-기준 쪽, xs — 경계 x좌표,
          top/bottom — 표의 위/아래 (top-left 좌표계)
    출력: 띠 수(len(xs)-1)만큼의 글자 수. 실패하면 None
    비고:
        글자의 **가로 중심**으로 띠를 정한다. 영역으로 텍스트를 뽑으면
        (`get_text_bounded`) 띠에 **걸치기만 한** 이웃 열의 글자까지 딸려
        와, 폭 8.4pt 짜리 빈 띠가 비어 있지 않은 것으로 나온다 — 실측
        (행안부 p334 table_221): 그 때문에 접기가 일어나지 않았다.
        pdfium 좌표는 bottom-left 기준이라 쪽 높이로 뒤집는다.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return None

    try:
        document = pdfium.PdfDocument(str(pdf_path))
    except Exception as exc:                     # noqa: BLE001 - 실험이 본체를 막지 않는다
        _log.debug("PDF 를 열지 못했습니다: %s", exc)
        return None
    try:
        page = document[page_no - 1]
        height = page.get_size()[1]
        textpage = page.get_textpage()
        low, high = height - bottom, height - top     # BOTTOMLEFT 로 뒤집는다
        counts = [0] * (len(xs) - 1)
        for i in range(textpage.count_chars()):
            box = textpage.get_charbox(i)
            if not box:
                continue
            cl, cb, cr, ct = box
            if cr <= cl or ct <= cb:
                continue                         # 공백 등 폭이 없는 글자
            if ct < low or cb > high:
                continue                         # 표의 위아래 밖
            center = (cl + cr) / 2
            for b in range(len(xs) - 1):
                if xs[b] <= center < xs[b + 1]:
                    counts[b] += 1
                    break
        return counts
    except Exception as exc:                     # noqa: BLE001 - 실험이 본체를 막지 않는다
        _log.debug("띠 글자 세기 실패 (%s쪽): %s", page_no, exc)
        return None
    finally:
        document.close()


def fold_decor_bounds(pdf_path, page_no: int, bbox: dict,
                      vertical: list[tuple[float, float, float]],
                      ) -> list[tuple[float, float, float]]:
    """장식 세로 경계를 접는다 — 열 수를 세기 전에.

    입력: pdf_path — 원본, page_no — 쪽, bbox — 표 영역,
          vertical — 표 안으로 자른 세로 선분 [(x, y0, y1)]
    출력: 장식 경계를 뺀 세로 선분 목록 (접을 것이 없으면 그대로)
    비고:
        표 가장자리의 겹선이 폭 몇 pt 짜리 빈 띠를 만들고, 격자는 그것을
        열로 센다. 그 한 칸 때문에 **기하 열 수가 인식보다 늘 1 커져**
        ⑬은 없는 열을 되살리려 하고 ⑭의 좌표계 검사는 통과하지 못한다.
        실측(행안부 429쪽): ⑬ 발화 11표 중 오탐 1표가 이것 때문이었고,
        ⑭ 차단 19표 중 16표가 이 한 칸 차이였다.

        접을 것은 **비어 있고 좁은** 띠뿐이다 — 폭 조건이 없으면 값이 빈
        진짜 열까지 접는다(DECOR_MAX_WIDTH 주석의 실측을 보라).
        글자를 못 읽으면 아무것도 접지 않는다 — 근거 없이 격자를 고치지
        않는다.
    """
    xs = _bands([x for x, _, _ in vertical])
    if len(xs) < 3:                              # 띠가 하나뿐이면 접을 것이 없다
        return vertical
    counts = _band_char_counts(pdf_path, page_no, xs, bbox["t"], bbox["b"])
    if counts is None:
        return vertical

    drop: list[float] = []
    for i, count in enumerate(counts):
        if count or xs[i + 1] - xs[i] > DECOR_MAX_WIDTH:
            continue
        # 띠를 이웃과 합친다 — 첫 띠는 오른쪽 경계를, 그 밖은 왼쪽 경계를
        # 지우면 바깥 테두리는 그대로 남는다.
        drop.append(xs[i + 1] if i == 0 else xs[i])
    if not drop:
        return vertical
    kept = [seg for seg in vertical
            if not any(abs(seg[0] - x) <= BAND_TOLERANCE for x in drop)]
    # 접고 나서 격자가 서지 않으면 접지 않은 쪽을 쓴다 (없는 결함을 만들지
    # 않는다).
    return kept if len(_bands([x for x, _, _ in kept])) >= MIN_BOUNDS else vertical


def table_lattice(pdf_path, page_no: int, bbox: dict, *, fold_decor: bool = False):
    """표 bbox 안에서 합성 격자를 세운다.

    입력: pdf_path — 원본, page_no — 쪽, bbox — 표 영역 (l/t/r/b),
          fold_decor — 장식 경계를 접고 셀지 (기본 False)
    출력: lattice_cells 와 같음
    비고:
        `fold_decor` 는 **열 수를 재는 쪽**(⑬·⑪)만 켠다. ⑨ 자신의 표시는
        지면을 있는 그대로 보고해야 하므로 접지 않는다 — 접은 격자를
        표시했다가는 지면에 있는 선을 없다고 말하게 된다.
    """
    horizontal, vertical = page_segments(pdf_path, page_no)
    h = _clip(horizontal, bbox["t"], bbox["b"], bbox["l"], bbox["r"])
    v = _clip(vertical, bbox["l"], bbox["r"], bbox["t"], bbox["b"])
    if not h or not v:
        return None
    if fold_decor:
        v = fold_decor_bounds(pdf_path, page_no, bbox, v)
    return lattice_cells(h, v)


def run(pages: list[PageContent], **kwargs) -> int:
    """합성 격자와 TableFormer 결과를 셀 자리로 견준다.

    입력: pages — 페이지 목록 (제자리 갱신), pdf_path — 원본 경로
    출력: 표시한 표 수
    비고:
        **표시만 한다** — ⑥과 같은 계약이다. ⑦(복원)로의 승격은 이
        표시의 정밀도를 다부처에서 잰 뒤 정한다.
    """
    source = kwargs.get("pdf_path")
    if not source:
        return 0

    marked = 0
    for page in pages:
        if not page.tables or not isinstance(page.page_no, int):
            continue
        for table in page.tables:
            if not table.bbox:
                continue
            got = table_lattice(source, page.page_no, table.bbox)
            if got is None:
                continue
            cells, rows, cols = got
            report = compare_grids(cells, _detected_cells(table.cells))
            if len(report["missing"]) < MIN_GAP:
                continue
            confidence = ("high" if report["coverage"] >= HIGH_COVERAGE
                          else "low")
            table.synth_grid = {
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
                "experiments.tsr.measure.line_grid", "합성 격자 차이",
                f"{table.id} · 괘선+모서리 격자에 있으나 인식에 없는 병합 "
                f"{len(report['missing'])}개 (덮개 {report['coverage']} · "
                f"신뢰 {confidence})",
                status="warn" if confidence == "high" else "info")
    return marked


register(Experiment(
    key="line_grid",
    title="괘선·모서리 합성 격자",
    purpose="배경 사각형 모서리 + 얇은 괘선을 합쳐 격자를 세움 (T2·T3 — 사각형이 부족한 표)",
    origin="가설 H2·H3 — SPARTAN(2026)의 span=관통선+1 결정식, Camelot lattice 계보, "
           "NCGM 의 다중 근거 협력 발상",
    run=run,
    formats=("pdf",),
    needs=("vector",),
    status="verified",
    note="⑥(사각형만)의 덮개<1.0 구간을 겨냥한다 — 사각형이 표 일부만 "
         "덮어도 괘선이 나머지를 채우면 격자가 선다. 병합은 학습 없이 "
         "결정식(이웃 칸을 가르는 선 부재)으로 정하고, 병합 결과가 "
         "직사각형이 아니면 격자를 통째로 버린다(없는 결함을 만들지 "
         "않는다). 표시만 하며 ⑦ 승격은 다부처 실측 후.",
    knobs={"DOCSTRUCT_EXP_LINE_GRID": "false 면 끔 (기본 켬 — 승격)"},
))
