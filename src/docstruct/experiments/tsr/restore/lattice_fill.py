"""실험 lattice_fill — 격자가 서는데 셀에 구멍이 남은 표를 다시 세운다.

입력:
    ★ 격자 결함이 있는 표

역할:
    ⑮(lattice_restore)와 **같은 기계**를 쓰되, 발화 조건을 열 수 차이가
    아니라 **격자 결함**(0.4.69)으로 잡는다.
호출부:
    experiments.registry (`DOCSTRUCT_EXP_LATTICE_FILL=1`)
출력:
    고친 표 수 (TableInfo.cells/markdown/source 갱신)

왜 있는가 — 정보는 있는데 쓰지 않고 있었다
----------------------------------------
0.4.70 에서 격자 결함의 뿌리를 찾았다. `source` 별 결함률이 갈렸다.

    grid (⑮·⑦ 복원)   113표   결함 **0%**
    parser (손 안 댐)   364표   결함  60%

⑮·⑦이 손댄 표는 하나도 깨지지 않는다. 괘선으로 격자를 **먼저 세우고**
셀을 배정하므로 완전성이 보장되기 때문이다.

그러면 왜 나머지에는 안 붙었나. `col_gate` 진단(문체부 609쪽)이 답했다.

    cols_match     259표   결함률 **79%**   ← 격자가 서고 열 수도 맞는다
    too_few_cols    53표   결함률  17%
    no_lattice      33표   결함률  27%      ← 괘선이 없는 표는 **9%뿐**
    lattice_fewer   15표   결함률 100%

**괘선이 없어서가 아니었다.** 격자는 90% 이상에서 정상적으로 선다.
⑮의 게이트가 `cols <= detected` 라 **열 수가 맞으면 물러나기** 때문에,
"격자는 서는데 셀에 구멍이 있는 표" 259개를 그냥 지나쳤다. 결함이 남은
220표 중 **205표가 격자 열 수 = 인식 열 수**다.

즉 표를 바로 세울 재료가 이미 손에 있는데 쓰지 않고 있었다.

무엇이 다른가 (⑮과의 관계)
------------------------
    ⑮ lattice_restore   격자 열이 **더 많을 때** — 열 밀림 교정
    이 실험             열 수는 같은데 **셀에 구멍이 있을 때** — 배정 실패 교정

기계는 같고 조건만 다르다. 그래서 ⑮이 이미 손댄 표는 건드리지 않는다.

안전 장치 — 좋아질 때만 받는다
----------------------------
⑮의 기존 가드(글자 수 유지·격자 경계 일치)를 그대로 쓰고, **격자 결함이
실제로 줄어들 때만 채택한다.** 0.4.70 에서 VLM 재구성이 격자를 나쁘게
만든 것을 막았듯, 이쪽도 같은 잣대를 스스로에게 건다 — 고치겠다고 나선
기법이 더 망가뜨리면 안 된다.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from docstruct.experiments.registry import Experiment, register

if TYPE_CHECKING:
    from docstruct.models import PageContent

_log = logging.getLogger(__name__)

#: 열이 이보다 적으면 재지 않는다.
#:
#: **⑮에서 물려받은 값(4)을 3으로 낮췄다** (0.4.73). ⑮은 "열 밀림 교정"
#: 이라 작은 표를 뺄 이유가 있었지만, 이 실험은 **구멍 메우기**라 3열
#: 표도 격자만 서면 메울 수 있다.
#:
#: 실측(두 부처 남은 결함 50표): `too_few_cols` 19표 중 **3열이 11개**,
#: 2열이 8개였다. 2열은 격자 판정이 불안정할 수 있어 남겨 둔다.
DEFAULT_MIN_COLS = 3


def _min_cols() -> int:
    """열 수 문턱 (DOCSTRUCT_EXP_FILL_MIN_COLS).

    입력: 없음
    출력: 문턱 (기본 DEFAULT_MIN_COLS)
    비고:
        손잡이로 뺀 이유는 이 값이 **이 실험에서 검증된 것이 아니라**
        ⑮에서 물려받은 것이기 때문이다. 되돌리기 쉬워야 다시 잴 수 있다.
    """
    import os

    raw = os.getenv("DOCSTRUCT_EXP_FILL_MIN_COLS", "").strip()
    try:
        value = int(raw) if raw else DEFAULT_MIN_COLS
    except ValueError:
        return DEFAULT_MIN_COLS
    return value if value >= 2 else DEFAULT_MIN_COLS


def restore_filled(pdf_path, page_no: int, bbox: dict,
                   detected_cells: list[dict] | None) -> dict | None:
    """열 수가 같아도 격자로 다시 세운다.

    입력: pdf_path — 원본, page_no — 쪽, bbox — 표 영역,
          detected_cells — 인식 셀
    출력: ⑮ `restore_table` 과 같은 꼴에 `gate` 를 더한 것.
          성공하면 `gate` 는 없고, 물러나면 `{"gate": 사유, ...}` 만 있다
    비고:
        ⑮의 `restore_table` 은 `cols <= detected` 에서 물러난다. 여기서는
        그 조건만 빼고 나머지 가드(격자 경계 일치·글자 수 유지)는 그대로
        쓴다. **코드를 베끼지 않고 같은 조각을 부른다** — 두 벌이 되면
        한쪽만 고쳐지는 일이 생긴다.

        **물러난 자리를 반드시 남긴다** (0.4.72). 처음에는 `None` 만
        돌려줬는데, 그러면 실측에서 남은 표가 왜 안 걸렸는지 알 수 없다 —
        `col_gate`(0.4.56)가 ⑬ 문제를 닫은 결정적 재료였는데 같은 계측을
        새 실험에 넣지 않은 것이다. 물러나는 자리가 여섯이고 다음 수가
        각각 다르다.
    """
    from docstruct.experiments.tsr.restore.grid_restore import _cell_texts, cells_to_markdown
    from docstruct.experiments.tsr.restore.lattice_restore import (MIN_TEXT_KEEP,
                                                       _detected_cols,
                                                       _text_len)
    from docstruct.experiments.tsr.measure.line_grid import (_clip, fold_decor_bounds,
                                                 lattice_cells, page_segments)
    from docstruct.experiments.tsr.measure.vector_grid import _bands

    detected = _detected_cols(detected_cells)
    floor = _min_cols()
    if detected < floor:
        return {"gate": "too_few_cols", "detected": detected, "floor": floor}

    horizontal, vertical = page_segments(pdf_path, page_no)
    h = _clip(horizontal, bbox["t"], bbox["b"], bbox["l"], bbox["r"])
    v = _clip(vertical, bbox["l"], bbox["r"], bbox["t"], bbox["b"])
    if not h or not v:
        # 표 자리에 괘선이 없다 — 다른 기법이 필요한 표다.
        return {"gate": "no_segments", "detected": detected,
                "h": len(h), "v": len(v)}
    v = fold_decor_bounds(pdf_path, page_no, bbox, v)

    got = lattice_cells(h, v)
    if got is None:
        return {"gate": "no_lattice", "detected": detected}
    positions, _rows, cols = got
    # ⑮과 갈리는 자리 — 열 수가 같아도 물러나지 않는다. 대신 격자가
    # 인식보다 **적으면** 손대지 않는다(격자를 덜 찾은 쪽일 수 있다).
    if cols < detected:
        return {"gate": "lattice_fewer", "detected": detected, "lattice": cols}

    xs = _bands([x for x, _, _ in v])
    ys = _bands([y for y, _, _ in h])
    if len(xs) - 1 != cols:
        # 격자 열 수와 세로선 밴딩이 어긋난다 — `_bands` 쪽 문제다.
        return {"gate": "bounds_mismatch", "detected": detected,
                "lattice": cols, "bands": len(xs) - 1}

    rects = [(xs[c], ys[r], xs[c + span_c], ys[r + span_r])
             for r, c, span_r, span_c in positions]
    texts = _cell_texts(pdf_path, page_no, rects)
    if texts is None or len(texts) != len(positions):
        return {"gate": "text_read_failed", "detected": detected,
                "lattice": cols}

    cells = [{"row": r, "col": c, "rowspan": span_r, "colspan": span_c,
              "text": text}
             for (r, c, span_r, span_c), text in zip(positions, texts)]
    cells.sort(key=lambda cell: (cell["row"], cell["col"]))

    before, after = _text_len(detected_cells), _text_len(cells)
    if before and after < before * MIN_TEXT_KEEP:
        # 격자가 표를 일부만 덮어 바깥 글자가 빠졌다 — bbox 문제일 수 있다.
        return {"gate": "text_loss", "detected": detected, "lattice": cols,
                "chars": [before, after]}

    return {"cells": cells, "markdown": cells_to_markdown(cells),
            "before": detected, "after": cols}


def _faults(cells: list[dict] | None) -> int:
    """격자 결함 수 (구멍 + 겹침). 못 재면 -1."""
    from docstruct.structuring.checks import grid_check

    got = grid_check(cells)
    return -1 if got is None else got["holes"] + got["overlaps"]


def run(pages: list[PageContent], **kwargs) -> int:
    """격자는 서는데 셀에 구멍이 남은 표를 다시 세운다.

    입력: pages — 페이지 목록 (제자리 갱신), pdf_path — 원본 경로
    출력: 고친 표 수
    """
    source = kwargs.get("pdf_path")
    if not source:
        return 0

    fixed = 0
    for page in pages:
        if not page.tables or not isinstance(page.page_no, int):
            continue
        for table in page.tables:
            if not table.bbox or not table.cells:
                continue
            before_faults = _faults(table.cells)
            if before_faults <= 0:
                continue                         # 온전한 표는 건드리지 않는다
            if table.source == "grid" or getattr(table, "head_grid", None):
                # ⑦·⑫·⑮가 이긴 자리는 지킨다. 다만 **그 표에 결함이
                # 남아 있다면** 그것도 알아야 하므로 사유를 적는다.
                table.fill_gate = {"gate": "earlier_restorer",
                                   "faults": before_faults}
                continue
            if getattr(table, "agreed_grid", None):
                table.fill_gate = {"gate": "earlier_restorer",
                                   "faults": before_faults}
                continue

            result = restore_filled(source, page.page_no, table.bbox,
                                    table.cells)
            if result is None or result.get("gate"):
                # **물러난 자리를 남긴다.** 이것이 없으면 남은 결함 표가
                # 왜 안 걸렸는지 결과만 보고 가릴 수 없다 (0.4.72).
                table.fill_gate = dict(result or {"gate": "unknown"})
                table.fill_gate["faults"] = before_faults
                continue
            after_faults = _faults(result["cells"])
            if after_faults < 0 or after_faults >= before_faults:
                table.fill_gate = {"gate": "made_worse",
                                   "faults": before_faults,
                                   "faults_after": after_faults}
                # **좋아질 때만 받는다.** 고치겠다고 나선 기법이 더
                # 망가뜨리면 안 된다 (0.4.70 의 교훈을 스스로에게 건다).
                page.trace.add(
                    "docstruct.experiments.tsr.restore.lattice_fill", "격자 채움 폐기",
                    f"{table.id} · 결함 {before_faults} → {after_faults}칸 — "
                    "원본을 유지합니다", status="warn")
                continue

            table.cells = result["cells"]
            table.markdown = result["markdown"]
            table.source = "grid"                # VLM 이 다시 쓰지 않게 한다
            table.lattice_fill = {
                "cols": result["after"],
                "faults_before": before_faults,
                "faults_after": after_faults,
            }
            fixed += 1
            page.trace.add(
                "docstruct.experiments.tsr.restore.lattice_fill", "격자로 셀 채움",
                f"{table.id} · 결함 {before_faults} → {after_faults}칸 "
                f"({result['after']}열)", status="warn")
    return fixed


register(Experiment(
    key="lattice_fill",
    title="격자는 서는데 구멍이 남은 표 다시 세우기",
    purpose="⑮과 같은 기계를 열 수 차이가 아니라 **격자 결함**으로 발화 — "
            "재료가 이미 있는데 쓰지 않던 259표가 대상이다",
    origin="0.4.70 진단 — `cols_match` 259표의 결함률이 79% 인데 "
           "`no_lattice` 는 33표(9%)뿐이었다. 괘선이 없어서가 아니었다",
    formats=("pdf",),
    needs=("cells", "vector"),
    status="verified",
    note="⑮이 이미 손댄 표는 건드리지 않는다. **격자 결함이 실제로 줄 때만** "
         "채택한다 — 고치겠다고 나선 기법이 더 망가뜨리면 안 된다"
         "(0.4.70 의 교훈).\n"
         "0.4.79 에 셀 오염(앞 칸 끝 글자가 다음 칸에 딸려 옴)으로 강등됐다가 "
         "**0.4.80 복원** — 판독 후처리(`repair_leaks`)가 그 중복 글자를 "
         "지운다. 원본 대조: 오염 표 셀의 원본 일치 79~81% → 96~98%.\n"
         "실측(0.4.73 승격 당시): 격자 결함 문체부 50%→7% · 행안부 53%→6% · "
         "복원 365표 전부 결함 0 · 폐기 0건. 행안부 HWPX 원본 대조에서 "
         "**내용도 개선** — 숫자 93개 회복, 지어낸 숫자 0 증가, 닮음이 "
         "나빠진 표 2/133.\n"
         "남은 결함은 100% 사유가 기록된다(`fill_gate`): lattice_fewer 21 · "
         "too_few_cols 19 · no_lattice 9 · text_loss 1. **괘선 자체가 없어 "
         "못 고친 표는 0건**이다.",
    run=run,
    knobs={"DOCSTRUCT_EXP_FILL_MIN_COLS": "열 수 문턱 (기본 3 — ⑮에서 "
                                          "물려받은 4를 낮췄다)"},
))
