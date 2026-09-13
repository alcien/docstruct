"""실험 ⑥ — PDF 도형에서 격자를 복원해 병합 인식을 견준다.

무엇을 보완하는가
--------------
텍스트 PDF 의 남은 결함은 **문자가 아니라 구조**다. 같은 문서를 두 형식으로
읽어 재면 이렇게 갈린다.

    HWPX(정답)  셀 18,442 · 병합 3,429 (18.6%)
    PDF         셀 14,391 · 병합 1,344 (9.3%)      ← 절반

TableFormer 는 이미지에서 병합을 **추측**한다. 세로 병합을 놓치면 그 아래
칸이 빈 칸이 되고, 값이 맨 윗행만의 것으로 읽힌다 — 실측(행안부)에서 세로
병합이 덮는 하위 칸이 4,356개였다. 예산·지표 표에서 값 귀속이 무너지는
자리가 그만큼이라는 뜻이다.

왜 지면 도형인가
-------------
한글에서 만든 표는 **셀마다 배경 사각형**이 도형으로 들어 있다. 병합된
셀은 사각형 하나가 여러 행·열에 걸친다 — 추측할 것이 없는 물리적 근거다.

실측(표본 60쪽):

    행안부 성과계획서   격자 복원 30쪽 · 사각형 1,006 · 다중밴드 21.1%
    과기부 성과보고서   격자 복원 21쪽 · 사각형 1,866 · 다중밴드 10.8%

행안부의 21.1% 는 HWPX 정답(18.6%)에 가깝고 TableFormer(9.3%)의 두 배다.
**놓친 병합이 지면에는 남아 있다.**

무엇을 하는가
-----------
고치지 않는다. 물리 격자와 TableFormer 결과의 **병합 수 차이만 표시**한다.

    table.grid_merge_gap = {"physical": 12, "detected": 5,
                            "missing": [(3, 0, 2, 1), …],   ← 자리까지
                            "extra": [], "coverage": 1.0, "cells": 40, …}

이 표시가 붙은 표가 VLM 재구성(`vlm_fix_tables`)의 대상이 되어야 한다는
것이 이 실험의 가설이다. 지금 대상 선정(`odd_columns`)은 열 수가 어긋난
표만 고르는데, **병합 손실은 열 수를 바꾸지 않아** 걸리지 않는다.

한계
----
- 배경 사각형이 없는 표(선만 그린 표·이미지 표)에는 근거가 없다. 표본에서
  절반(30/60·21/60)이 그랬다 — 그런 표는 표시하지 않는다.
- 사각형이 셀이 아니라 강조 상자·도형인 경우가 있다. 격자로 정렬되지
  않으면 버린다.
- 좌표는 표 bbox 안으로 한정한다. 쪽 전체를 보면 머리글 상자까지 든다.

역할:
    실험 ⑥ — PDF 도형에서 격자를 복원해 병합 인식을 견준다.
입력:
    PDF 도형
출력:
    격자 (confidence) — 병합 인식 대조"""
from __future__ import annotations

import logging

from docstruct.experiments.registry import Experiment, register
from docstruct.models import PageContent

_log = logging.getLogger(__name__)

#: 셀 후보로 볼 최소 크기 (points). 이보다 작으면 선·점이다.
MIN_CELL_PT = 6.0

#: 같은 경계로 볼 좌표 오차 (points).
BAND_TOLERANCE = 2.0

#: 격자로 인정할 최소 사각형 수. 적으면 강조 상자일 뿐이다.
MIN_RECTS = 6

#: 격자로 인정할 최소 경계 수 (행·열 각각).
MIN_BANDS = 3

#: 표시할 최소 병합 차이. 하나둘 차이는 경계 판정 오차일 수 있다.
MIN_GAP = 2

#: 이 덮개(물리 셀 ÷ 인식 셀) 이상이면 격자가 표 전체를 덮은 것으로 본다.
#: 실측(행안부 10쪽·표 12개, 셀 자리 정확일치 기준):
#:
#:     덮개 ≥ 1.0   '놓침' 표시 24개 중 24개가 정답에 실재 (100%)
#:     덮개 < 1.0   12개 중 10개 (83%)
#:
#: 덮개가 낮다는 것은 배경 사각형이 표의 **일부만** 덮었다는 뜻이고, 그러면
#: 물리 격자의 행·열 번호가 표 전체 기준과 어긋나 없는 병합이 만들어진다.
HIGH_COVERAGE = 1.0


def _page_rects(pdf_path, page_no: int) -> list[tuple[float, float, float, float]]:
    """이 쪽의 사각형 도형을 TOPLEFT 좌표로 낸다.

    입력: pdf_path — PDF 경로, page_no — 1부터
    출력: (left, top, right, bottom) 목록. 못 읽으면 빈 목록
    비고:
        PDF 좌표는 BOTTOMLEFT 이므로 표 bbox(TOPLEFT)와 맞추어 뒤집는다 —
        `text_runs` 와 같은 규칙이다.
    """
    try:
        import ctypes

        import pypdfium2 as pdfium
        import pypdfium2.raw as raw
    except ImportError:
        return []

    try:
        document = pdfium.PdfDocument(str(pdf_path))
    except Exception as exc:                     # noqa: BLE001 - 진단 보조다
        _log.debug("PDF 를 열지 못했습니다: %s", exc)
        return []
    try:
        index = page_no - 1
        if not 0 <= index < len(document):
            return []
        page = document[index]
        height = page.get_size()[1]
        out: list[tuple[float, float, float, float]] = []
        for obj in page.get_objects():
            if obj.type != raw.FPDF_PAGEOBJ_PATH:
                continue
            left = ctypes.c_float()
            bottom = ctypes.c_float()
            right = ctypes.c_float()
            top = ctypes.c_float()
            ok = raw.FPDFPageObj_GetBounds(
                obj.raw, ctypes.byref(left), ctypes.byref(bottom),
                ctypes.byref(right), ctypes.byref(top),
            )
            if not ok:
                continue
            width = right.value - left.value
            tall = top.value - bottom.value
            if width < MIN_CELL_PT or tall < MIN_CELL_PT:
                continue                         # 선·점은 셀이 아니다
            out.append((left.value, height - top.value,
                        right.value, height - bottom.value))
        return out
    except Exception as exc:                     # noqa: BLE001
        _log.debug("%s쪽 도형을 읽지 못했습니다: %s", page_no, exc)
        return []
    finally:
        document.close()


def _bands(values: list[float]) -> list[float]:
    """가까운 좌표를 하나의 경계로 묶는다.

    입력: values — 좌표 목록
    출력: 오름차순 경계 목록
    """
    out: list[float] = []
    for value in sorted(values):
        if not out or value - out[-1] > BAND_TOLERANCE:
            out.append(value)
    return out


def _band_index(bands: list[float], value: float) -> int | None:
    """이 좌표가 몇 번째 경계인가.

    입력: bands — 경계 목록, value — 좌표
    출력: 경계 번호. 어느 경계와도 맞지 않으면 None
    """
    for index, edge in enumerate(bands):
        if abs(edge - value) <= BAND_TOLERANCE:
            return index
    return None


def physical_cells(
    rects: list[tuple[float, float, float, float]],
) -> tuple[list[tuple[int, int, int, int]], int, int] | None:
    """사각형 목록에서 격자를 세우고 **셀 하나하나**를 낸다.

    입력: rects — (left, top, right, bottom) 목록
    출력: ([(row, col, rowspan, colspan), …], 행 수, 열 수). 격자가 아니면 None
    비고:
        개수가 아니라 자리를 낸다. **개수만 견주면 자리가 어긋난 것을
        놓친다** — 실측(행안부 121쪽)에서 병합 수는 7 로 같은데 격자 크기가
        (2,8) 대 (4,8) 로 달랐다.

        격자로 정렬되지 않는 도형은 버린다. 강조 상자·도해가 섞이면 경계가
        맞지 않는데, 그것을 병합으로 세면 없는 결함을 만든다.
    """
    if len(rects) < MIN_RECTS:
        return None
    xs = _bands([r[0] for r in rects] + [r[2] for r in rects])
    ys = _bands([r[1] for r in rects] + [r[3] for r in rects])
    if len(xs) < MIN_BANDS or len(ys) < MIN_BANDS:
        return None

    cells: list[tuple[int, int, int, int]] = []
    for left, top, right, bottom in rects:
        c0, c1 = _band_index(xs, left), _band_index(xs, right)
        r0, r1 = _band_index(ys, top), _band_index(ys, bottom)
        if None in (c0, c1, r0, r1):
            continue                             # 격자에 안 맞는 도형
        cells.append((r0, c0, r1 - r0, c1 - c0))  # type: ignore[operator]
    if len(cells) < MIN_RECTS:
        return None
    return cells, len(ys) - 1, len(xs) - 1


def physical_merges(rects: list[tuple[float, float, float, float]]) -> dict | None:
    """사각형 목록에서 격자를 세우고 병합 셀을 센다.

    입력: rects — (left, top, right, bottom) 목록
    출력: {"cells": n, "merged": m, "rows": r, "cols": c}. 격자가 아니면 None
    비고:
        `physical_cells` 의 요약이다. 진단 도구가 쓴다.
    """
    got = physical_cells(rects)
    if got is None:
        return None
    cells, rows, cols = got
    merged = sum(1 for c in cells if c[2] > 1 or c[3] > 1)
    return {"cells": len(cells), "merged": merged, "rows": rows, "cols": cols}


def cluster_rects(
    rects: list[tuple[float, float, float, float]],
    *, gap: float = 12.0,
) -> list[list[tuple[float, float, float, float]]]:
    """세로로 떨어진 사각형 무리를 표 단위로 가른다.

    입력: rects — (left, top, right, bottom) 목록, gap — 무리를 가를 세로 간격
    출력: 무리 목록
    비고:
        **한 쪽에 표가 둘 이상이면 경계를 함께 세면 안 된다.** 실측(행안부
        155쪽)에서 위쪽 표의 열 경계 4개와 아래쪽 표의 13개가 한 목록으로
        묶여, 위쪽 표의 정상 셀이 `col 1~5` 를 걸치는 것으로 보였다 —
        정답 0인 쪽에서 병합 13이 나온 원인이다.

        파이프라인에서는 표 bbox 로 이미 한정되므로 필요 없다. 쪽 전체를
        보는 진단 도구(`scripts/check_vector_grid.py`)를 위한 것이다.
    """
    if not rects:
        return []
    ordered = sorted(rects, key=lambda r: r[1])
    groups: list[list[tuple[float, float, float, float]]] = [[ordered[0]]]
    bottom = ordered[0][3]
    for rect in ordered[1:]:
        if rect[1] - bottom > gap:
            groups.append([rect])
            bottom = rect[3]
            continue
        groups[-1].append(rect)
        bottom = max(bottom, rect[3])
    return groups


def _detected_cells(cells: list[dict] | None) -> list[tuple[int, int, int, int]]:
    """TableFormer 결과를 (row, col, rowspan, colspan) 목록으로.

    입력: cells — TableInfo.cells
    출력: 셀 자리 목록
    """
    if not cells:
        return []
    out = []
    for cell in cells:
        try:
            out.append((
                int(cell.get("row", 0)), int(cell.get("col", 0)),
                int(cell.get("rowspan", 1) or 1), int(cell.get("colspan", 1) or 1),
            ))
        except (TypeError, ValueError):          # noqa: PERF203 - 이상한 셀은 건너뛴다
            continue
    return out


def _merged_only(cells: list[tuple[int, int, int, int]]) -> set[tuple[int, int, int, int]]:
    """병합 셀만 고른다.

    입력: cells — 셀 자리 목록
    출력: 병합 셀 집합
    """
    return {c for c in cells if c[2] > 1 or c[3] > 1}


def compare_grids(physical: list[tuple[int, int, int, int]],
                  detected: list[tuple[int, int, int, int]]) -> dict:
    """물리 격자와 TableFormer 결과를 **셀 자리로** 견준다.

    입력: physical — 지면 도형에서 세운 셀, detected — TableFormer 셀
    출력: {"missing": [...], "extra": [...], "physical": n, "detected": m,
           "coverage": 0.0~}
    비고:
        개수가 아니라 자리를 견준다. 개수만 보면 **같은 수의 다른 병합**을
        일치로 착각한다.

        `missing` 은 지면에는 있는데 TableFormer 가 놓친 병합, `extra` 는
        그 반대다. `coverage` 는 물리 셀 수 ÷ TableFormer 셀 수 — 배경
        사각형이 표의 일부만 덮은 경우(실측 121쪽: 9 대 18)를 알아보기
        위한 값이다. 낮으면 `missing` 이 적게 나올 수 있다.
    """
    physical_merged = _merged_only(physical)
    detected_merged = _merged_only(detected)
    coverage = (len(physical) / len(detected)) if detected else 0.0
    return {
        "missing": sorted(physical_merged - set(detected)),
        "extra": sorted(detected_merged - set(physical)),
        "physical": len(physical_merged),
        "detected": len(detected_merged),
        "coverage": round(coverage, 2),
    }


def column_widths(rects: list[tuple[float, float, float, float]]) -> list[float] | None:
    """사각형 목록에서 열 너비 목록을 낸다 (경계 차분).

    입력: rects — (left, top, right, bottom) 목록
    출력: 열 너비 목록. 경계가 안 서면 None
    비고:
        절대 x좌표가 아니라 **너비**를 내는 이유: 쪽을 넘는 표에서 홀수·
        짝수 쪽의 안팎 여백이 달라 절대좌표는 통째로 밀린다 — 실측(별첨3
        339→340쪽)에서 14.26pt 상수 이동, 퍼짐 0.001pt 였다. 너비는
        평행이동에 불변이다.
    """
    xs = _bands([r[0] for r in rects] + [r[2] for r in rects])
    if len(xs) < MIN_BANDS:
        return None
    return [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]


def same_column_layout(
    rects_a: list[tuple[float, float, float, float]],
    rects_b: list[tuple[float, float, float, float]],
) -> bool | None:
    """두 표 조각이 같은 열 배치인가 — 쪽 넘김 이어붙임(T6)의 검산식.

    입력: rects_a/rects_b — 앞 쪽·뒷 쪽 표의 사각형 목록
    출력: 열 수가 같고 너비가 전부 허용오차 안이면 True. 판정 불가면 None
    비고:
        ST-GCN 계보의 "연속 프레임에서 같은 관절" 을 결정론으로 옮긴 것.
        실측: 진짜 연속 쌍(별첨3 340→342쪽) 18경계 완전 일치(0.00pt) ·
        홀짝 여백 쌍(339→340쪽)도 너비 기준 17/17 일치 · 다른 표 대조군은
        열 수부터 거부. 어긋나면 이어붙임을 표시로 강등하는 데 쓴다.
    """
    wa, wb = column_widths(rects_a), column_widths(rects_b)
    if wa is None or wb is None:
        return None
    if len(wa) != len(wb):
        return False
    return all(abs(p - q) <= BAND_TOLERANCE for p, q in zip(wa, wb))


def _inside(rect: tuple[float, float, float, float], bbox: dict) -> bool:
    """이 사각형이 표 영역 안에 있는가.

    입력: rect — (left, top, right, bottom), bbox — 표 bbox(TOPLEFT)
    출력: 중심이 표 안이면 True
    """
    cx = (rect[0] + rect[2]) / 2
    cy = (rect[1] + rect[3]) / 2
    return (bbox["l"] <= cx <= bbox["r"]) and (bbox["t"] <= cy <= bbox["b"])


def run(pages: list[PageContent], **kwargs) -> int:
    """물리 격자와 TableFormer 결과를 셀 자리로 견준다.

    입력: pages — 페이지 목록 (제자리 갱신), pdf_path — 원본 경로
    출력: 표시한 표 수
    비고:
        **표시만 한다.** 어느 쪽이 옳은지 정하지 않는다 — 지면에 있는데
        인식에 없는 병합(`missing`)을 남기고, 고치는 것은 지면을 보는
        VLM 의 몫이다.
    """
    source = kwargs.get("pdf_path")
    if not source:
        return 0

    marked = 0
    for page in pages:
        if not page.tables or not isinstance(page.page_no, int):
            continue
        rects = _page_rects(source, page.page_no)
        if not rects:
            continue
        for table in page.tables:
            bbox = table.bbox
            if not bbox:
                continue
            got = physical_cells([r for r in rects if _inside(r, bbox)])
            if got is None:
                continue
            cells, rows, cols = got
            report = compare_grids(cells, _detected_cells(table.cells))
            if len(report["missing"]) < MIN_GAP:
                continue
            # 덮개가 낮으면 격자가 표 일부만 덮은 것이라 행·열 번호가
            # 어긋날 수 있다. 버리지 않고 **등급을 매긴다** — 실측에서
            # 낮은 쪽도 83% 는 실재했다.
            confidence = "high" if report["coverage"] >= HIGH_COVERAGE else "low"
            table.grid_merge_gap = {
                "physical": report["physical"],
                "detected": report["detected"],
                # 자리까지 남긴다 — VLM 에 "여기를 보라" 고 줄 수 있다.
                "missing": report["missing"],
                "extra": report["extra"],
                "coverage": report["coverage"],
                "confidence": confidence,
                "cells": len(cells),
                "rows": rows,
                "cols": cols,
            }
            marked += 1
            page.trace.add(
                "experiments.tsr.measure.vector_grid", "병합 인식 차이",
                f"{table.id} · 지면에 있으나 인식에 없는 병합 "
                f"{len(report['missing'])}개 (지면 {report['physical']} vs "
                f"인식 {report['detected']} · 덮개 {report['coverage']} · "
                f"신뢰 {confidence})",
                status="warn" if confidence == "high" else "info")
    return marked


register(Experiment(
    key="vector_grid",
    title="지면 도형으로 병합 인식을 견줌",
    purpose="TableFormer 가 세로 병합을 절반만 잡는 것 (18.6% → 9.3%)",
    origin="벡터 괘선·셀 배경 — 물리적 근거 (계보 밖)",
    formats=("pdf",),
    needs=("vector",),
    status="verified",
    note="**표시만 한다.** 한글이 만든 표는 셀마다 배경 사각형이 있어 "
         "병합이 도형에 남는다. 개수가 아니라 **셀 자리**를 견준다. "
         "실측(행안부 10쪽·표 12개·HWPX 정답 대조, 자리 정확일치): "
         "병합 셀 정밀도 물리 88% vs TableFormer 60%, 재현율 48% vs 24%. "
         "덮개 1.0 이상인 표만 보면 물리 격자가 **정밀도·재현율 100%** "
         "(4표 전부 셀 208~222개까지 완전 일치)이고 '놓침' 표시 24개가 "
         "모두 정답에 실재했다. 덮개가 낮으면(표 일부만 도형) 83% 로 "
         "떨어져 `confidence: low` 로 표시한다. "
         "`vlm_fix_tables` 대상 선정(`odd_columns`)이 병합 손실을 "
         "못 잡으므로, 이 표시를 대상 신호로 쓸지 검토 중.",
    knobs={"DOCSTRUCT_EXP_VECTOR_GRID": "false 면 끔 (기본 켬 — 승격)"},
    run=run,
))
