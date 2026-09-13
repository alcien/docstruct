"""실험 over_split — 괘선보다 열을 더 쪼갠 표 지목 (측정 전용).

입력:
    인식 열 수 vs 괘선 열 수
출력:
    더 쪼갠 표 지목 (측정 전용)

역할:
    인식이 잡은 열 수가 **괘선 격자보다 많은** 표를 찾아 남긴다. 표는
    바꾸지 않는다.
호출부:
    pipeline (실험이 켜졌을 때) — 사다리 밖, 검증 계열
설정:
    DOCSTRUCT_EXP_OVER_SPLIT=1 (기본 꺼짐)
    DOCSTRUCT_EXP_OVER_SPLIT_MAX   믿을 만한 최대 차이 (기본 3)

왜 있는가 — ⑬을 닫으면서 나온 반대 방향 신호
------------------------------------------
⑬(col_grid)은 "인식이 열을 **잃으면** 괘선 격자로 되살린다" 는 기법이다.
0.4.56 에서 게이트 기각 사유를 계측해 두 부처에서 재 보니:

    행안부 249표 · 조달청 51표 = 300표
    격자 > 인식 (⑬이 일할 자리)  ……  **0건**

⑬의 전제가 이 문서군에서 성립하지 않는다(강등 확정). 그런데 같은 계측에
**반대 방향**이 9표 있었다.

    table_81   인식  9열 · 격자  8열   (차 1)
    table_92   인식 14열 · 격자 13열   (차 1)
    table_152  인식 15열 · 격자 13열   (차 2)
    …
    table_141  인식 11열 · 격자  2열   (차 9)   ← 격자 검출 실패로 보인다

열을 하나 더 쪼개면 그 뒤 값이 통째로 한 칸씩 밀린다 — 오래 쫓던
**열 밀림(값 귀속)** 과 모양이 같다. ⑬은 열 수를 늘리는 도구라 이것을
구조적으로 건드릴 수 없었다. 방향이 반대다.

무엇을 믿고 무엇을 안 믿나
------------------------
격자가 인식보다 적다고 해서 **격자가 옳다는 뜻은 아니다.** 괘선을 덜
찾았을 수도 있다. 차이가 클수록 그 가능성이 커진다 — `table_141` 의
11 대 2 는 표가 2열이라기보다 격자가 무너진 것으로 보는 편이 옳다.

    믿는다     차이 1~3 — 열 하나·둘을 더 쪼갠 모양
    안 믿는다  차이 4 이상 — 격자 검출 실패로 보고 사유만 남긴다

문턱은 손잡이로 열어 둔다. **정답 대조 전에는 문턱을 고정할 근거가 없다.**

무엇을 하고 무엇을 안 하나
------------------------
    한다     의심 표에 `over_split` 기록 (인식·격자 열 수, 차이, 신뢰)
    안 한다  표 수정 (측정 전용 — 고치려면 값을 어느 열로 옮길지 알아야
             하고, 그것은 격자만으로 정해지지 않는다)
             ⑦이 통째로 복원한 표(source=="grid") — 이미 격자 기준이다
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from docstruct.experiments.registry import Experiment, register

if TYPE_CHECKING:
    from docstruct.models import PageContent

_log = logging.getLogger(__name__)

#: 이 차이까지는 "더 쪼갬" 으로 본다. 넘으면 격자 쪽을 의심한다.
DEFAULT_MAX_GAP = 3

#: 열이 이보다 적으면 재지 않는다 — ⑬과 같은 문턱을 쓴다.
MIN_COLS = 4


def _max_gap() -> int:
    """믿을 만한 최대 차이 (DOCSTRUCT_EXP_OVER_SPLIT_MAX)."""
    raw = os.getenv("DOCSTRUCT_EXP_OVER_SPLIT_MAX", "").strip()
    try:
        value = int(raw) if raw else DEFAULT_MAX_GAP
    except ValueError:
        return DEFAULT_MAX_GAP
    return value if value > 0 else DEFAULT_MAX_GAP


def _detected_cols(cells: list[dict] | None) -> int:
    """셀에서 열 수를 센다 (colspan 을 편 뒤의 폭).

    입력: cells — 셀 목록
    출력: 열 수. 셀이 없으면 0
    """
    if not cells:
        return 0
    return max((c.get("col", 0) + c.get("colspan", 1)) for c in cells)


def run(pages: list[PageContent], **kwargs) -> int:
    """괘선보다 열을 더 쪼갠 표를 찾아 기록한다.

    입력: pages — 페이지 목록 (over_split 필드만 채운다), pdf_path — 원본
    출력: 믿을 만한 의심 표 수 (격자 검출 실패로 본 것은 세지 않는다)
    """
    source = kwargs.get("pdf_path")
    if not source:
        return 0

    from docstruct.experiments.tsr.measure.line_grid import table_lattice

    max_gap = _max_gap()
    hits = 0
    for page in pages:
        if not page.tables or not isinstance(page.page_no, int):
            continue
        for table in page.tables:
            if not table.bbox or not table.cells:
                continue
            if table.source == "grid":
                continue                         # 이미 격자 기준으로 세운 표다
            detected = _detected_cols(table.cells)
            if detected < MIN_COLS:
                continue
            lattice = table_lattice(source, page.page_no, table.bbox,
                                    fold_decor=True)
            if lattice is None:
                continue
            lattice_cols = lattice[2]
            if lattice_cols >= detected:
                continue

            gap = detected - lattice_cols
            trusted = gap <= max_gap
            table.over_split = {
                "detected": detected,
                "lattice": lattice_cols,
                "gap": gap,
                # **격자를 믿을 수 있는가**를 함께 남긴다. 차이가 크면
                # 표가 좁은 것이 아니라 괘선을 덜 찾은 것으로 본다.
                "trusted": trusted,
            }
            if trusted:
                hits += 1
                page.trace.add(
                    "docstruct.experiments.tsr.measure.over_split", "열을 더 쪼갬",
                    f"{table.id} · 인식 {detected}열 · 괘선 격자 "
                    f"{lattice_cols}열 (차 {gap}) — 값이 옆 칸으로 밀렸을 수 "
                    "있습니다", status="warn")
            else:
                page.trace.add(
                    "docstruct.experiments.tsr.measure.over_split", "열 차이 큼",
                    f"{table.id} · 인식 {detected}열 · 괘선 격자 "
                    f"{lattice_cols}열 (차 {gap}) — 격자를 덜 찾은 것으로 "
                    "보고 세지 않습니다")
    return hits


register(Experiment(
    key="over_split",
    title="괘선보다 열을 더 쪼갠 표 지목",
    purpose="열 밀림(값 귀속)의 후보를 찾는다 — 열을 하나 더 쪼개면 그 뒤 "
            "값이 통째로 한 칸씩 밀린다",
    origin="⑬ 게이트 계측(0.4.56)에서 나온 반대 방향 신호 — 300표에서 "
           "`격자 > 인식` 은 0건인데 `격자 < 인식` 이 9건이었다",
    formats=("pdf",),
    needs=("cells", "vector"),
    status="testing",
    note="측정 전용 · 표 불변. 차이가 큰 것(기본 4 이상)은 격자 검출 실패로 "
         "보고 세지 않는다 — 격자가 적다고 격자가 옳은 것은 아니다. "
         "0.4.62 신설 — 정답 대조 전.",
    run=run,
    knobs={"DOCSTRUCT_EXP_OVER_SPLIT_MAX": "믿을 만한 최대 차이 (기본 3)"},
))
