"""실험 ⑦ — 물리 격자 결정 복원 (grid_restore).

입력:
    ★ 배경 사각형 격자
출력:
    표 재작성 (덮개≥1.0 일 때만)

역할:
    셀 배경 사각형이 표 전체를 덮은 표(T1)를 TableFormer 출력 대신
    **지면 도형 + 지면 텍스트만으로** 다시 세운다. 추측이 없다.
호출부:
    pipeline (실험이 켜졌을 때) — vector_grid 뒤에 돈다
설정:
    DOCSTRUCT_EXP_GRID_RESTORE=false 로 끈다 — 0.4.3 승격 뒤 **기본 켬** (registry.DEFAULT_ON)

왜 있는가
--------
가설 H1 (가설재검토_복원기준.md). 실측이 방향을 정했다:

    table_10 (208셀)   자리·span·텍스트까지 208/208 완전 일치 (HWPX 정답)
    같은 표 TableFormer  171셀 · 병합 재현율 31%

배경 사각형은 셀 그 자체라 격자를 **추측할 필요가 없다.** 덮개(물리 셀 ÷
인식 셀)가 1.0 이상일 때만 복원한다 — 그 구간에서 '놓침' 정밀도 100%
(24/24), 미만이면 격자가 표 일부만 덮어 행·열 번호가 어긋날 수 있다
(83%). 낮은 덮개는 vector_grid 의 표시(`confidence: low`)로 남는다.

무엇을 바꾸나
-----------
    table.cells     물리 격자 셀 (row·col·rowspan·colspan·text)
    table.markdown  hwpxtree 와 같은 규칙으로 재작성 (`〃` 병합 표식 포함)
    table.source    "grid" — 결과만 보고 출처를 알 수 있게
    table.grid_restore  전후 기록 {"cells_detected", "cells_restored",
                        "merged_detected", "merged_restored", "coverage"}

원본 markdown 은 `original_markdown` 에 남긴다 (비어 있을 때만 — fill 이
먼저 채웠으면 그것이 더 원본이다).

세 파일 유형에서의 자리
--------------------
    HWPX     불필요 — 구조가 이미 원본이다 (이 실험의 **정답 소스**로 쓴다)
    텍스트 PDF  주 대상 (T1)
    스캔 PDF   벡터가 없어 불가 — 같은 자리는 H8(모폴로지 선 검출)이 맡는다.
             한 유형에서 못 쓴다고 버리지 않는다: 축은 같고 근거만 다르다.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from docstruct.experiments.registry import Experiment, register
from docstruct.experiments.tsr.measure.vector_grid import (
    HIGH_COVERAGE,
    _detected_cells,
    _inside,
    _page_rects,
    physical_cells,
)

if TYPE_CHECKING:
    from docstruct.models import PageContent

_log = logging.getLogger(__name__)

#: hwpxtree 와 같은 병합 표식 — 세로 병합이 이어지는 칸.
MERGE_UP = "〃"

#: 텍스트를 뽑을 때 사각형 안쪽으로 들일 여백(pt). 경계선 위 글자가 이웃
#: 칸으로 새는 것을 막는다. 실측(행안부 208셀 표)에서 1.0 으로 전 셀 일치.
TEXT_PAD = 1.0


def _cell_texts(
    pdf_path,
    page_no: int,
    rects: list[tuple[float, float, float, float]],
) -> list[str] | None:
    """사각형마다 그 안의 지면 텍스트를 뽑는다.

    입력: pdf_path — 원본 PDF, page_no — 1-기준 쪽, rects — top-left 좌표계
    출력: rects 와 같은 차례의 텍스트 목록. 실패하면 None
    비고:
        pdfium 텍스트 좌표는 bottom-left 기준이라 쪽 높이로 뒤집는다.
        여기서 뽑는 것은 **원문 그대로**다 — OCR 도 LLM 도 아니다.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return None

    document = pdfium.PdfDocument(str(pdf_path))
    try:
        page = document[page_no - 1]
        height = page.get_size()[1]
        textpage = page.get_textpage()
        out = []
        for left, top, right, bottom in rects:
            text = textpage.get_text_bounded(
                left=left + TEXT_PAD, bottom=height - bottom + TEXT_PAD,
                right=right - TEXT_PAD, top=height - top - TEXT_PAD,
            )
            out.append(" ".join((text or "").split()))
        return out
    except Exception as exc:                     # noqa: BLE001 - 실험이 본체를 막지 않는다
        _log.warning("지면 텍스트 추출 실패 (%s쪽): %s", page_no, exc)
        return None
    finally:
        document.close()


def _escape(text: str) -> str:
    """markdown 표 안에 넣을 수 있게 한다.

    입력: text — 셀 텍스트
    출력: `|` 를 이스케이프한 문자열
    """
    return (text or "").replace("|", "\\|")


def cells_to_markdown(cells: list[dict]) -> str:
    """셀 목록을 markdown 표로 — hwpxtree._render_table 과 같은 규칙.

    입력: cells — {"row","col","rowspan","colspan","text"} 목록
    출력: markdown 표 문자열
    비고:
        같은 규칙을 쓰는 이유: 파이프라인의 나머지(구조화·평가·검산)가
        HWPX 표와 PDF 표를 **같은 모양**으로 받아야 한다. 앞쪽 빈 행은 지운다.

        **세로 병합을 무엇으로 채울지는 여기서 정하지 않는다** (0.5.15).
        `converters.common.table.merge_continuation` 한 곳이 정한다 — 0.5.6
        에서 렌더러 셋을 그리로 모았는데 **이 함수를 빠뜨렸다.** 그래서
        PDF 에서만 `〃` 가 남았다: 실측(개인정보보호위원회 0.5.13) 50표 중
        14표에 `〃` 가 있었고 **전부 `source="grid"`** — 이 함수가 다시 쓴
        표였다(lattice_fill 8 · lattice_restore 3 · grid_restore 3).

        `lattice_fill`·`lattice_restore` 도 이 함수를 쓰므로 한 곳만 고치면
        셋이 함께 따라온다.
    """
    from docstruct.converters.common.table import merge_continuation
    if not cells:
        return ""
    rows = max(c["row"] + c.get("rowspan", 1) for c in cells)
    cols = max(c["col"] + c.get("colspan", 1) for c in cells)
    grid = [["" for _ in range(cols)] for _ in range(rows)]
    for cell in cells:
        row, col = cell["row"], cell["col"]
        if not (0 <= row < rows and 0 <= col < cols):
            continue
        anchor = _escape(cell.get("text", ""))
        grid[row][col] = anchor
        filler = merge_continuation(anchor)
        for r in range(row + 1, min(row + cell.get("rowspan", 1), rows)):
            grid[r][col] = filler

    while len(grid) > 1 and not any(c.strip() for c in grid[0]):
        grid.pop(0)

    lines = ["| " + " | ".join(grid[0]) + " |",
             "| " + " | ".join(["---"] * cols) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in grid[1:])
    return "\n".join(lines)


def restore_table(pdf_path, page_no: int, bbox: dict,
                  detected_cells: list[dict] | None) -> dict | None:
    """표 하나를 물리 격자로 다시 세운다.

    입력: pdf_path — 원본, page_no — 쪽, bbox — 표 영역,
          detected_cells — TableFormer 셀 (덮개 계산용)
    출력: {"cells": [...], "markdown": str, "coverage": float,
           "rows": r, "cols": c}. 복원 조건이 안 되면 None
    비고:
        **덮개 < 1.0 이면 복원하지 않는다.** 격자가 표 일부만 덮은 것이라
        행·열 번호가 어긋날 수 있다 — 그 구간은 표시(vector_grid)로만
        남기고 표는 건드리지 않는다. 회귀 없음이 완전 복원보다 앞선다.
    """
    rects = [r for r in _page_rects(pdf_path, page_no) if _inside(r, bbox)]
    got = physical_cells(rects)
    if got is None:
        return None
    positions, rows, cols = got

    detected = _detected_cells(detected_cells)
    coverage = (len(positions) / len(detected)) if detected else 0.0
    if coverage < HIGH_COVERAGE:
        return None

    texts = _cell_texts(pdf_path, page_no, rects)
    if texts is None or len(texts) != len(rects):
        return None

    # 텍스트와 자리를 짝지으려면 사각형 차례대로 자리 계산이 필요하다 —
    # physical_cells 는 요약만 주므로 밴드 계산을 여기서 한 번 더 한다.
    from docstruct.experiments.tsr.measure.vector_grid import _band_index, _bands

    cells: list[dict] = []
    xs = _bands([r[0] for r in rects] + [r[2] for r in rects])
    ys = _bands([r[1] for r in rects] + [r[3] for r in rects])
    for (left, top, right, bottom), text in zip(rects, texts):
        c0, c1 = _band_index(xs, left), _band_index(xs, right)
        r0, r1 = _band_index(ys, top), _band_index(ys, bottom)
        if None in (c0, c1, r0, r1):
            continue
        cells.append({"row": r0, "col": c0,
                      "rowspan": r1 - r0, "colspan": c1 - c0,
                      "text": text})
    cells.sort(key=lambda c: (c["row"], c["col"]))
    return {"cells": cells, "markdown": cells_to_markdown(cells),
            "coverage": round(coverage, 2), "rows": rows, "cols": cols}


def run(pages: list[PageContent], **kwargs) -> int:
    """덮개가 충분한 표를 물리 격자로 다시 세운다.

    입력: pages — 페이지 목록 (제자리 갱신), pdf_path — 원본 경로
    출력: 복원한 표 수
    """
    source = kwargs.get("pdf_path")
    if not source:
        return 0

    restored = 0
    for page in pages:
        if not page.tables or not isinstance(page.page_no, int):
            continue
        for table in page.tables:
            if not table.bbox:
                continue
            result = restore_table(source, page.page_no, table.bbox, table.cells)
            if result is None:
                continue

            merged = lambda cs: sum(  # noqa: E731
                1 for c in (cs or [])
                if int(c.get("rowspan", 1) or 1) > 1
                or int(c.get("colspan", 1) or 1) > 1)
            table.grid_restore = {
                "cells_detected": len(table.cells or []),
                "cells_restored": len(result["cells"]),
                "merged_detected": merged(table.cells),
                "merged_restored": merged(result["cells"]),
                "coverage": result["coverage"],
            }
            if not table.original_markdown:
                table.original_markdown = table.markdown
            table.cells = result["cells"]
            table.markdown = result["markdown"]
            table.source = "grid"
            restored += 1
            page.trace.add(
                "experiments.tsr.restore.grid_restore", "결정 복원",
                f"{table.id} · 물리 격자로 재작성 (셀 "
                f"{table.grid_restore['cells_detected']} → "
                f"{table.grid_restore['cells_restored']} · 병합 "
                f"{table.grid_restore['merged_detected']} → "
                f"{table.grid_restore['merged_restored']} · 덮개 "
                f"{result['coverage']})",
                status="info")
    return restored


register(Experiment(
    key="grid_restore",
    title="물리 격자 결정 복원",
    purpose="배경 사각형이 표 전체를 덮은 표(T1)를 지면 도형+지면 텍스트로 재작성",
    origin="가설 H1 — SPARTAN·Camelot lattice 계보의 국내 공문서 변형 "
           "(선이 아니라 한글 내보내기의 셀 배경 사각형을 근거로 씀)",
    run=run,
    formats=("pdf",),
    needs=("vector", "geometry"),
    status="verified",
    note="**표를 바꾼다** (vector_grid 는 표시만). 덮개≥1.0 에서만 — 그 "
         "구간 실측: 셀 208개까지 자리·span·텍스트 완전 일치, TableFormer "
         "는 같은 표에서 171셀·병합 재현율 31%. 덮개<1.0 은 건드리지 않고 "
         "vector_grid 의 confidence:low 표시로만 남는다. 전후는 "
         "grid_restore 필드와 original_markdown 으로 되짚는다. 스캔 PDF "
         "에는 벡터가 없어 못 쓴다 — 같은 자리는 H8(선 검출)이 맡는다.",
    knobs={"DOCSTRUCT_EXP_GRID_RESTORE": "false 면 끔 (기본 켬 — 승격)"},
))
