"""실험 ④ — 같은 서식 표들의 열 위치로 표준 격자를 세운다.

무엇을 보완하는가
--------------
표 하나만 보고는 격자가 옳은지 알 수 없다. 그런데 정부 문서는 같은 서식
표를 여러 쪽에 반복한다. **다수가 쓰는 열 위치**가 정답에 가깝다.

이미 `flag_odd_tables` 가 **열 개수**로 이상을 찾고 있다(오탐 0). 여기서는
**열 좌표**까지 견줘, 개수는 같은데 위치가 어긋난 표를 찾는다.

어디서 빌린 발상인가
-----------------
연구 계보 밖이다. 모델은 이미지 한 장만 보므로 이런 판단을 할 수 없다.
문서 전체를 볼 수 있는 후처리만의 이점이다.

한계
----
- 같은 서식 표가 **셋 이상** 있어야 한다. 스캔본에서는 표 4개가 서식이
  제각각이라 그룹이 만들어지지 않았다.
- 다수가 틀렸으면 소수를 이상으로 본다.
"""
from __future__ import annotations

import logging
import os
import statistics
from collections import defaultdict

from docstruct.experiments.registry import Experiment, register
from docstruct.models import PageContent

_log = logging.getLogger(__name__)

#: 같은 열로 볼 좌표 오차 (포인트).
TOLERANCE_ENV = "DOCSTRUCT_EXP_CONSENSUS_TOL"
DEFAULT_TOLERANCE = 5.0

#: 다수를 판단할 최소 표 수.
MIN_GROUP = 3

#: 열 폭 대비 이 비율을 넘어야 어긋남으로 본다.
#: 실측(행안부): 정상 예산표들이 열 폭의 10~50% 안에서 흔들렸다. 열 하나를
#: 통째로 밀어낼 정도(100%)라야 구조가 어긋난 것이다.
MIN_DRIFT_RATIO = 1.0


def _tolerance() -> float:
    """같은 열로 볼 오차.

    입력: 없음 (`DOCSTRUCT_EXP_CONSENSUS_TOL`)
    출력: 포인트 단위 실수
    """
    raw = os.environ.get(TOLERANCE_ENV, "").strip()
    if not raw:
        return DEFAULT_TOLERANCE
    try:
        value = float(raw)
    except ValueError:
        _log.warning("%s 값이 숫자가 아닙니다: %r", TOLERANCE_ENV, raw)
        return DEFAULT_TOLERANCE
    return value if value > 0 else DEFAULT_TOLERANCE


def _column_edges(item) -> list[float] | None:
    """표의 열 시작 좌표.

    입력: item — Docling TableItem
    출력: 좌표 목록. 좌표가 없으면 None
    """
    cells = [c for c in (getattr(getattr(item, "data", None), "table_cells", None) or [])
             if getattr(c, "bbox", None) is not None]
    if not cells:
        return None
    return sorted({round(float(c.bbox.l), 1) for c in cells})


def consensus_edges(groups: list[list[float]]) -> list[float]:
    """여러 표의 열 좌표에서 표준 격자를 만든다.

    입력: groups — 표별 열 좌표 목록
    출력: 중앙값으로 모은 표준 좌표
    비고:
        열 수가 같은 표끼리만 견준다. 개수가 다르면 대응을 알 수 없다.
    """
    if not groups:
        return []
    width = statistics.mode(len(g) for g in groups)
    same = [g for g in groups if len(g) == width]
    if len(same) < MIN_GROUP:
        return []
    return [statistics.median(col) for col in zip(*same)]


def _header_key(table) -> tuple:
    """이 표의 서식을 나타내는 열쇠.

    입력: table — TableInfo
    출력: 헤더 첫 낱말들로 만든 튜플
    비고:
        **열 개수만으로 묶으면 안 된다.** 서식이 달라도 열 수가 같으면 한
        그룹이 되어, 서로 다른 표의 열 위치를 견주게 된다 — 실측(국세청
        성과보고서)에서 `연도/목표/실적` 표와 `프로그램명/목표` 표가 함께
        묶여 61개 중 24개(39%)가 어긋났다고 나왔다.

        헤더 내용이 같아야 같은 서식이다.
    """
    rows = [
        line for line in (getattr(table, "markdown", "") or "").splitlines()
        if line.startswith("|") and set(line.strip()) - set("|-: ")
    ]
    if not rows:
        return ()
    cells = [c.strip().strip("*") for c in rows[0].strip("|").split("|")]
    filled = [c for c in cells if c]
    return tuple(filled[:3])


def run(pages: list[PageContent], **_kwargs) -> int:
    """표준 격자와 어긋난 표를 표시한다.

    입력: pages — 페이지 목록
    출력: 표시한 표 수
    """
    buckets: dict[tuple, list] = defaultdict(list)
    for page in pages:
        for table in page.tables:
            item = getattr(table, "source_item", None)
            edges = _column_edges(item) if item is not None else None
            if not edges:
                continue
            # 열 개수 **와 헤더 내용**으로 묶는다. 개수만 보면 서로 다른
            # 표가 한 그룹이 된다.
            key = _header_key(table)
            if not key:
                continue
            buckets[(len(edges), key)].append((page, table, edges))

    tolerance = _tolerance()
    marked = 0
    for members in buckets.values():
        if len(members) < MIN_GROUP:
            continue
        standard = consensus_edges([e for _, _, e in members])
        if not standard:
            continue
        for page, table, edges in members:
            drift = [abs(a - b) for a, b in zip(edges, standard)]
            worst = max(drift) if drift else 0.0
            # **표 폭에 견준다.** 고정 pt 로 재면 넓은 표가 불리하다 —
            # 실측(행안부 성과계획서)에서 예산표 97개 중 87개가 걸렸는데,
            # 열 폭이 60pt 인 표에서 20pt 는 흔한 편차였다.
            #
            # 열 하나를 통째로 밀어낼 만큼 어긋난 것만 본다.
            width = max(standard[-1] - standard[0], 1.0)
            column = width / max(len(standard) - 1, 1)
            if worst <= tolerance or worst < column * MIN_DRIFT_RATIO:
                continue
            table.consensus_drift = round(worst, 1)
            marked += 1
            page.trace.add(
                "experiments.grid_consensus", "표준 격자와 어긋남",
                f"{table.id} · 최대 {worst:.1f}pt (같은 서식 {len(members)}개 기준)",
                status="warn")
    return marked


register(Experiment(
    key="grid_consensus",
    title="같은 서식 표들의 열 좌표로 표준 격자",
    purpose="열 개수는 같은데 위치가 어긋난 표",
    origin="연구 계보 밖 — 문서 전체를 보는 후처리만의 이점",
    formats=("pdf",),
    status="proposed",
    note="같은 서식 표가 셋 이상 필요. 스캔본은 표 서식이 제각각이라 "
         "그룹이 만들어지지 않는다(실측 4개 표 → 대상 0).",
    run=run,
    knobs={TOLERANCE_ENV: f"같은 열로 볼 오차 (기본 {DEFAULT_TOLERANCE}pt)"},
))
