"""실험 ⑬ — 열 격자 복원 (col_grid).

입력:
    ★ 격자 열 수
출력:
    마지막 열 colspan 조정 (강등 · 기본 꺼짐)

역할:
    인식이 **열을 잃은** 표에서, ⑨(line_grid)가 세운 격자의 열 경계로
    셀을 다시 배치한다. ⑫(head_grid)가 열 수 불일치로 물러난 자리를 연다.
호출부:
    pipeline (실험이 켜졌을 때) — 사다리에서 ⑦ 다음, ⑫ 앞
설정:
    DOCSTRUCT_EXP_COL_GRID=1 (기본 꺼짐)

왜 있는가 (조달청 정답 대조)
-------------------------
⑫를 켠 뒤에도 남은 놓침 49개 중 **35개(71%)가 "열 수 불일치"** 로 ⑫가
물러난 자리였다. ⑫는 격자와 인식의 열 수가 다르면 손대지 않는다 —
자리가 어긋난 채로 머리를 갈아 끼우면 없는 결함을 만들기 때문이다.

즉 **열이 먼저 맞아야 ⑫가 일할 수 있다.**

안전 조건 — 실측이 정해 줬다
--------------------------
격자 열 수가 늘 옳은 것은 아니다. 조달청 실측:

    쪽   정답  인식  격자   격자가 옳은가
    42   13    13    14    아니오   ← 인식과 정답이 이미 같다
    58   13    13    14    아니오
    68    6     6     7    아니오
    74    8     8     9    아니오
    65    8     7     8    **예**   ← 인식이 열을 잃었다
    73   13    10    13    **예**

규칙이 보인다: **인식이 열을 잃은 표에서만 격자가 옳다.** 인식과 격자가
비슷한데 격자가 하나 더 많으면, 그 하나는 격자가 지어낸 경계다(여백을
경계로 오인하는 경우).

그래서 문턱을 둔다 — 격자가 인식보다 **뚜렷하게** 많을 때만 받는다.
실측에서 옳았던 두 표는 차이가 1/7·3/10 이고, 틀린 넷은 모두 1/13·1/6·
1/8 이다. 비율로 가르면 갈린다.

무엇을 하고 무엇을 안 하나
------------------------
    한다     격자의 열 경계로 셀의 col·colspan 을 다시 매긴다
    안 한다  행은 건드리지 않는다 (⑫의 몫)
             격자가 서지 않는 표는 받지 않는다 (세로선만으로는 부족)
             글자는 인식이 읽은 것을 그대로 옮긴다
             격자가 인식보다 적으면 물러난다 (열을 지우지 않는다)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from docstruct.experiments.registry import Experiment, register

if TYPE_CHECKING:
    from docstruct.models import PageContent

_log = logging.getLogger(__name__)

#: 격자 열 수가 인식보다 이 비율 이상 많아야 "인식이 열을 잃었다" 로 본다.
#: 실측 근거: 옳았던 표는 8/7=1.14 · 13/10=1.30, 틀린 표는 14/13=1.08 ·
#: 7/6=1.17 · 9/8=1.13. 1.17 이 경계에 걸리므로 **1.2** 로 잡는다 —
#: 애매한 것은 받지 않는 방향이다.
MIN_GAIN = 1.2
#: 이보다 열이 적은 표는 다루지 않는다 (작은 표는 오판 여지가 크다).
MIN_COLS = 4


def _detected_cols(cells: list[dict] | None) -> int:
    """인식 결과의 열 수."""
    if not cells:
        return 0
    return max(int(c.get("col", 0)) + int(c.get("colspan", 1) or 1) for c in cells)


def remap_columns(cells: list[dict], old_cols: int, new_cols: int) -> list[dict] | None:
    """셀의 열 번호를 새 열 수에 맞춰 늘린다.

    입력: cells — 인식 셀, old_cols — 지금 열 수, new_cols — 격자 열 수
    출력: 다시 매긴 셀 목록. 못 하면 None
    비고:
        **어느 열이 쪼개졌는지는 모른다.** 아는 것은 "열이 몇 개 더
        있었다" 뿐이다. 그래서 지어내지 않는다 — 마지막 열을 늘려 남은
        자리를 채우고, 나머지 셀은 자리를 그대로 지킨다. 이것으로 열
        **수**가 맞아 ⑫가 일할 수 있게 되고, 세부 배치는 ⑫가 격자에서
        직접 가져온다.
    """
    if new_cols <= old_cols:
        return None
    out = []
    for cell in cells:
        col = int(cell.get("col", 0))
        span = int(cell.get("colspan", 1) or 1)
        new = dict(cell)
        if col + span >= old_cols:               # 마지막 열까지 닿는 셀
            new["colspan"] = span + (new_cols - old_cols)
        out.append(new)
    return out


def run(pages: list[PageContent], **kwargs) -> int:
    """인식이 잃은 열을 격자 열 수로 되살린다.

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
            if table.source == "grid":
                continue                         # ⑦이 통째로 복원했다
            # **전체 격자가 서야 한다.** 세로선만 세면 여백을 경계로
            # 오인한 것과 구분되지 않는다 — 실측(조달청 37쪽 table_22):
            # 세로 경계 9개를 찾았지만 가로선이 부족해 격자가 안 섰고,
            # 그 열 수로 늘리자 없는 병합 2개가 생겼다. 가로·세로가 모두
            # 있어 직사각형 격자가 서는 표만 받는다.
            from docstruct.experiments.tsr.measure.line_grid import table_lattice

            # **장식 경계를 접고 센다.** 표 가장자리 겹선이 만든 폭 몇 pt
            # 짜리 빈 띠를 격자가 열로 세면, 인식이 아무것도 잃지 않은
            # 표에서도 열 수가 1 커져 없는 열을 되살리게 된다 — 실측
            # (행안부 p388 table_277): 2.8pt 빈 띠 때문에 8열을 9열로
            # 만들었고 정답은 8이었다.
            lattice = table_lattice(source, page.page_no, table.bbox,
                                    fold_decor=True)
            # **물러난 사유를 남긴다** (0.4.56 게이트 계측). ⑬ 강등의
            # 근거가 행안부 한 문서(⑮이 앞서 0표 발화)뿐인데, 조달청
            # 열 밀림 7표 중 6표는 어느 실험에도 안 걸린 채 남아 있다 —
            # ⑮이 흡수한 것인지 이 게이트가 좁은 것인지는 기각 지점이
            # 기록돼야 가릴 수 있다. 발화 수(hits)에는 세지 않는다.
            detected = _detected_cols(table.cells)
            if lattice is None:
                table.col_gate = {"reason": "no_lattice",
                                  "detected": detected}
                continue
            lattice_cols = lattice[2]
            # **사유를 뭉치지 않는다** (0.4.62). 처음에는 이 셋을 모두
            # `cols_match` 로 적었는데, 그 라벨이 사실과 달랐다 — 실측
            # (행안부 249표 + 조달청 51표)에서 `cols_match` 218표를 손으로
            # 다시 갈라야 209/8/1 이 나왔다. 계측이 읽히지 않으면 계측이
            # 아니다.
            if detected < MIN_COLS:
                # 열이 너무 적어 판단을 보류한 것이지, 맞아떨어진 것이 아니다.
                table.col_gate = {"reason": "too_few_cols",
                                  "detected": detected,
                                  "lattice": lattice_cols}
                continue
            if lattice_cols < detected:
                # **반대 방향 신호다.** 파서가 괘선이 뒷받침하는 것보다
                # 열을 더 쪼갰다는 뜻으로, ⑬이 고칠 대상이 아니다.
                # 실측에서 9표 나왔다 — `over_split` 실험이 이것을 본다.
                table.col_gate = {"reason": "lattice_fewer",
                                  "detected": detected,
                                  "lattice": lattice_cols}
                continue
            if lattice_cols == detected:
                table.col_gate = {"reason": "cols_match",
                                  "detected": detected,
                                  "lattice": lattice_cols}
                continue
            if lattice_cols / detected < MIN_GAIN:
                # 격자가 경계를 지어낸 쪽이다
                table.col_gate = {"reason": "gain_below",
                                  "detected": detected,
                                  "lattice": lattice_cols}
                continue
            remapped = remap_columns(table.cells, detected, lattice_cols)
            if remapped is None:
                table.col_gate = {"reason": "remap_failed",
                                  "detected": detected,
                                  "lattice": lattice_cols}
                continue
            table.cells = remapped
            table.col_grid = {"before": detected, "after": lattice_cols}
            fixed += 1
            page.trace.add(
                "experiments.tsr.restore.col_grid", "열 격자 복원",
                f"{table.id} · 열 {detected} → {lattice_cols} "
                f"(지면 격자 기준)",
                status="warn")
    return fixed


register(Experiment(
    key="col_grid",
    title="열 격자 복원",
    purpose="인식이 잃은 열을 지면 격자의 열 수로 되살림 — ⑫가 물러난 자리를 연다",
    origin="조달청 정답 대조 — ⑫를 켠 뒤 남은 놓침 49개 중 35개(71%)가 "
           "'열 수 불일치' 로 ⑫가 물러난 자리였다",
    run=run,
    formats=("pdf",),
    needs=("cells", "vector"),
    status="testing",
    note="**열이 먼저 맞아야 ⑫가 일한다.** 다만 격자 열 수가 늘 옳지는 "
         "않다 — 실측에서 인식과 정답이 이미 같은 표(42·58·68·74쪽)는 "
         "격자가 하나 더 많았고 그 하나는 지어낸 경계였다. 그래서 격자가 "
         "인식보다 **뚜렷하게**(1.2배 이상) 많을 때만 받는다. 어느 열이 "
         "쪼개졌는지는 모르므로 지어내지 않고 마지막 열을 늘려 열 수만 "
         "맞춘다 — 세부 배치는 뒤이어 ⑫가 격자에서 직접 가져온다.",
    knobs={"DOCSTRUCT_EXP_COL_GRID": "1 이면 켬 (기본 꺼짐)"},
))
