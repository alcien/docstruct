"""OCR 조각 좌표로 표 격자를 다시 세운다.

역할:
    표 구조 인식이 행이나 열을 놓친 표를, 그 안의 OCR 조각 좌표만으로
    격자를 복원해 markdown 표로 만든다.
호출부:
    docstruct.pipeline (`rebuild_grid` 가 켜졌을 때)
입력: 표 영역과 그 안의 (상자, 텍스트) 조각
출력: markdown 표

왜 필요한가
----------
실제 문서에서 13행 2열짜리 표가 **7행**으로 인식됐다. 가로 구분선이 연한
회색이라 행 경계를 놓친 것이다. 왼쪽 열은 아예 셀로 생성되지 않았고,
OCR 은 `지방세법`·`종합부동산세법` 을 제대로 읽었는데 넣을 칸이 없었다.

    표 구조 인식 결과   7행 2열 · 왼쪽 열 전부 빈 칸
    원본 지면          13행 2열

셀이 없으면 좌표 매칭도 소용없다. 없는 칸에 값을 넣을 수는 없다.

왜 좌표만으로 되는가
-----------------
표는 격자다. 같은 행의 글자는 세로 위치가 겹치고, 행 사이에는 빈 구간이
있다. 조각들의 y 좌표를 투영해 골짜기를 찾으면 행 경계가 나온다. 열도
같은 방식으로 x 축에서 찾는다.

이는 Split-Merge 계열(SEMv2·SEMv3)이 학습으로 하는 "분리선 예측" 을,
우리가 이미 가진 OCR 좌표로 직접 하는 것이다. 학습도 모델도 필요 없고
결과가 결정적이라 왜 그렇게 나뉘었는지 좌표로 설명된다.

한계
----
- **병합 셀을 복원하지 못한다.** 격자만 세우므로 `rowspan`·`colspan` 정보가
  없다. 병합이 많은 표는 원본 구조가 더 나을 수 있다.
- 조각이 없는 칸은 빈 칸이 된다 — 원본에 값이 있었어도 OCR 이 못 읽었으면
  복원할 수 없다.
- 그래서 **결함이 표시된 표에만** 쓰고, 결과가 원본보다 나쁘면 되돌린다.
"""
from __future__ import annotations

import logging
import statistics

from docstruct.converters.common.table import render_md_table
from docstruct.converters.pdf.cell_match import Box

_log = logging.getLogger(__name__)

#: 분리선으로 볼 최소 빈 구간. 간격 분포의 중앙값에 이 비율을 곱해 쓴다.
#: 고정값을 쓰면 촘촘한 표에서 모든 행이 하나로 뭉친다 — 실측에서 간격
#: 0.5pt 짜리 표가 13행이 아니라 1행이 됐다.
GAP_FACTOR = 0.5

#: 간격 임계의 하한 (포인트). 0 에 가까우면 같은 행 조각까지 갈라진다.
MIN_GAP = 0.1

#: 복원된 격자가 이 크기 미만이면 표로 보지 않는다.
MIN_ROWS = 2
MIN_COLS = 2

#: 폭이 중앙값의 이 배를 넘으면 가로 병합 조각으로 보고 열 경계 계산에서
#: 제외한다. 병합 헤더가 그 아래 열 경계를 덮어 버리기 때문이다.
WIDE_FACTOR = 1.8


def _bands(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """1차원 구간들을 겹침 기준으로 묶어 띠를 만든다.

    입력: intervals — (시작, 끝) 목록
    출력: 병합된 띠 목록 (시작 오름차순)
    비고:
        간격 분포에서 임계를 스스로 정한다. 문서마다 글자 크기와 행 간격이
        달라 고정값을 쓸 수 없다 — 실측에서 간격 3.5pt·0.5pt·0.3pt 짜리
        표가 모두 13행으로 복원됐다.
    """
    if not intervals:
        return []
    ordered = sorted(intervals)
    if len(ordered) == 1:
        return [ordered[0]]

    gaps: list[float] = []
    edge = ordered[0][1]
    for start, end in ordered[1:]:
        if start > edge:
            gaps.append(start - edge)
        edge = max(edge, end)
    threshold = max(statistics.median(gaps) * GAP_FACTOR, MIN_GAP) if gaps else MIN_GAP

    result: list[tuple[float, float]] = []
    current_start, current_end = ordered[0]
    for start, end in ordered[1:]:
        if start - current_end > threshold:
            result.append((current_start, current_end))
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    result.append((current_start, current_end))
    return result


def _index_of(bands: list[tuple[float, float]], low: float, high: float) -> int:
    """조각이 어느 띠에 속하는지.

    입력: bands — 띠 목록, low·high — 조각의 구간
    출력: 띠 인덱스. 어디에도 안 맞으면 가장 많이 겹치는 곳
    """
    best, best_overlap = 0, -1.0
    for index, (start, end) in enumerate(bands):
        overlap = min(high, end) - max(low, start)
        if overlap > best_overlap:
            best, best_overlap = index, overlap
    return best


def _column_bands(fragments: list[tuple[Box, str]]) -> list[tuple[float, float]]:
    """열 경계를 찾는다 (넓은 조각 제외).

    입력: fragments — (상자, 텍스트) 목록
    출력: 열 띠 목록
    비고:
        **가로로 병합된 조각을 빼고 센다.** 병합 헤더는 여러 열을 덮으므로
        그대로 투영하면 그 아래 열 경계가 사라진다. 실측에서
        `예산 (A+B)` 한 칸이 `'26년`·`'27년` 두 열을 삼켜 4열 표가 2열이
        됐다.

        폭이 중앙값의 1.8배를 넘는 조각을 병합 후보로 보고 경계 계산에서
        제외한다. 배정 자체에서 빼지는 않는다 — 값은 그대로 들어가야 한다.
    """
    widths = [box.right - box.left for box, _ in fragments]
    if not widths:
        return []
    limit = statistics.median(widths) * WIDE_FACTOR
    narrow = [(box.left, box.right) for box, _ in fragments
              if box.right - box.left <= limit]
    # 좁은 조각이 너무 적으면 판단 근거가 없다. 전체로 되돌린다.
    if len(narrow) < len(fragments) * 0.5:
        narrow = [(box.left, box.right) for box, _ in fragments]
    return _bands(narrow)


def rebuild(fragments: list[tuple[Box, str]]) -> str:
    """조각 좌표로 격자를 세워 markdown 표를 만든다.

    입력: fragments — (상자, 텍스트) 목록 (표 영역 안, 포인트 좌표)
    출력: GFM markdown 표. 격자를 세우지 못하면 빈 문자열
    비고:
        같은 칸에 여러 조각이 오면 읽기 순서로 이어 붙인다. 셀 안에서
        줄이 나뉜 경우가 흔한데, 줄바꿈을 넣으면 markdown 표가 깨지므로
        공백으로 잇는다.
    """
    usable = [(box, text.strip()) for box, text in fragments if text.strip()]
    if len(usable) < MIN_ROWS * MIN_COLS:
        return ""

    rows = _bands([(box.top, box.bottom) for box, _ in usable])
    cols = _column_bands(usable)
    if len(rows) < MIN_ROWS or len(cols) < MIN_COLS:
        return ""

    grid: list[list[list[tuple[float, str]]]] = [
        [[] for _ in cols] for _ in rows
    ]
    for box, text in usable:
        row = _index_of(rows, box.top, box.bottom)
        col = _index_of(cols, box.left, box.right)
        grid[row][col].append((box.left, text))

    table = [
        [" ".join(text for _, text in sorted(cell)) for cell in line]
        for line in grid
    ]
    # 내용이 하나도 없는 행·열은 버린다. 괘선이나 여백이 띠로 잡히면
    # 빈 줄이 생기는데, 그대로 두면 표가 성겨 보인다.
    table = [line for line in table if any(cell for cell in line)]
    if len(table) < MIN_ROWS:
        return ""
    keep = [index for index in range(len(table[0]))
            if any(line[index] for line in table)]
    if len(keep) < MIN_COLS:
        return ""
    table = [[line[index] for index in keep] for line in table]

    return render_md_table(table) if len(table) > 1 else ""
