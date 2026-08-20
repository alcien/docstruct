"""실험 ② — 병합을 놓친 셀을 찾는다.

무엇을 보완하는가
--------------
표 구조 인식이 세로 병합을 좌우로 가르는 일이 있다. 실측(성과계획서)에서
`구분` 이 두 칸으로 쪼개져 양쪽 열에 붙었다.

    정상  | 구분 | 총 계 (A+B) | 부처 소관 (C) |
    이상  | 구 총 계 (A+B) | 분 총 계 (A+B) | 부처 소관 (C) 820,542 |

어디서 빌린 발상인가
-----------------
GridFormer 는 표를 격자의 **꼭짓점과 변**으로 본다. 병합을 위상으로 다루므로
`rowspan`·`colspan` 이 복잡한 표에 강하다. 모델은 못 쓰지만 **"격자 위상을
보고 이상을 찾는다"** 는 발상은 좌표만으로 흉내 낼 수 있다.

어떻게 검출하는가
--------------
같은 행의 이웃한 두 셀이

- 병합으로 표시돼 있지 않고 (`col_span == 1`)
- 세로 위치가 거의 같으며 (같은 줄에 있고)
- 가로로 맞닿아 있고 (사이 간격이 글자 폭보다 좁다)
- **양쪽 텍스트가 한 낱말을 가른 것처럼 보이면**

병합을 놓쳤을 후보로 본다. `구 총 계` / `분 총 계` 처럼 한 글자씩 갈린 것이
전형이다.

한계
----
- **검출만 한다.** 고치지 않는다 — 어느 쪽이 옳은지 좌표만으로는 모른다.
- 원래 한 글자짜리 셀이 나란한 표(`갑`/`을` 같은)에서 오탐이 날 수 있다.
- HWP·HWPX 는 병합이 파일에 명시돼 있어 이 문제가 없다. PDF 전용이다.
"""
from __future__ import annotations

import logging
import os

from docstruct.experiments.registry import Experiment, register
from docstruct.models import PageContent

_log = logging.getLogger(__name__)

#: 같은 줄로 볼 세로 오차 (포인트).
ROW_TOLERANCE = 3.0

#: 맞닿았다고 볼 가로 간격 (포인트). 글자 폭보다 좁아야 한다.
GAP_LIMIT_ENV = "DOCSTRUCT_EXP_SPLIT_GAP"
DEFAULT_GAP_LIMIT = 6.0

#: 한 낱말이 갈린 것으로 볼 최대 글자 수. `구` / `분` 처럼 짧아야 한다.
SHORT_ENV = "DOCSTRUCT_EXP_SPLIT_MAXLEN"
DEFAULT_MAX_LEN = 3


def _limit(name: str, default: float) -> float:
    """환경변수에서 수치를 읽는다.

    입력: name — 환경변수 이름, default — 기본값
    출력: 실수. 잘못된 값이면 기본값
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        _log.warning("%s 값이 숫자가 아닙니다: %r", name, raw)
        return default
    return value if value > 0 else default


def find_split_merges(item) -> list[dict]:
    """병합을 놓쳤을 셀 쌍을 찾는다.

    입력: item — Docling TableItem
    출력: 후보 목록 [{row, cols, texts}]
    비고:
        좌표가 없으면 판단할 수 없어 빈 목록을 낸다. HWP 계열이 그렇다.
    """
    data = getattr(item, "data", None)
    cells = [
        cell for cell in (getattr(data, "table_cells", None) or [])
        if getattr(cell, "bbox", None) is not None
    ]
    if len(cells) < 2:
        return []

    gap_limit = _limit(GAP_LIMIT_ENV, DEFAULT_GAP_LIMIT)
    max_len = int(_limit(SHORT_ENV, DEFAULT_MAX_LEN))

    by_row: dict[int, list] = {}
    for cell in cells:
        by_row.setdefault(int(getattr(cell, "start_row_offset_idx", 0) or 0), []).append(cell)

    found: list[dict] = []
    for row, line in by_row.items():
        line.sort(key=lambda c: float(c.bbox.l))
        for left, right in zip(line, line[1:]):
            if int(getattr(left, "col_span", 1) or 1) > 1:
                continue
            if int(getattr(right, "col_span", 1) or 1) > 1:
                continue
            # 세로로 같은 줄이어야 한다
            if abs(float(left.bbox.t) - float(right.bbox.t)) > ROW_TOLERANCE:
                continue
            # 가로로 맞닿아야 한다
            if float(right.bbox.l) - float(left.bbox.r) > gap_limit:
                continue
            a = (getattr(left, "text", "") or "").strip()
            b = (getattr(right, "text", "") or "").strip()
            if not a or not b:
                continue
            # 한 낱말이 갈린 모습 — 양쪽 다 아주 짧다
            if len(a) <= max_len and len(b) <= max_len:
                found.append({
                    "row": row,
                    "cols": [int(getattr(left, "start_col_offset_idx", 0) or 0),
                             int(getattr(right, "start_col_offset_idx", 0) or 0)],
                    "texts": [a, b],
                })
    return found


def run(pages: list[PageContent], **_kwargs) -> int:
    """병합 놓침 후보를 표시한다.

    입력: pages — 표가 담긴 페이지 목록 (제자리 갱신)
    출력: 표시한 표 수
    """
    marked = 0
    for page in pages:
        for table in page.tables:
            item = getattr(table, "source_item", None)
            if item is None:
                continue
            found = find_split_merges(item)
            if not found:
                continue
            table.split_merge_hints = found
            marked += 1
            sample = " / ".join("".join(f["texts"]) for f in found[:2])
            page.trace.add(
                "experiments.split_merge", "병합 놓침 의심",
                f"{table.id} · {len(found)}곳 (예: {sample})", status="warn")
    return marked


register(Experiment(
    key="split_merge",
    title="병합을 놓친 셀 쌍 검출",
    purpose="세로 병합 헤더가 좌우로 갈리는 오류 (`구분` → `구` / `분`)",
    origin="GridFormer — 격자 위상으로 병합을 본다는 발상",
    formats=("pdf",),
    status="proposed",
    note="검출만 한다. 한 글자짜리 셀이 나란한 표에서 오탐 가능. "
         "HWP·HWPX 는 병합이 파일에 있어 해당 없음.",
    run=run,
    knobs={
        GAP_LIMIT_ENV: f"맞닿았다고 볼 가로 간격 (기본 {DEFAULT_GAP_LIMIT}pt)",
        SHORT_ENV: f"갈린 낱말로 볼 최대 글자 수 (기본 {DEFAULT_MAX_LEN})",
    },
))
