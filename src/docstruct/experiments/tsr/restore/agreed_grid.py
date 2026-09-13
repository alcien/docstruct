"""실험 ⑭ — 합의 병합 반영 (agreed_grid).

입력:
    표(TableInfo cells) + 두 격자 근거(vector·line)
출력:
    합의한 병합만 반영한 표 수 (agreed_grid 필드)

역할:
    두 기하 근거(배경 사각형·괘선 격자)가 **둘 다 인정한** 병합만 표에
    반영한다. ⑫가 머리 계층을 맡았다면 ⑭은 **본문을 포함한 표 전체**를
    맡되, 합의된 자리만 손댄다.
호출부:
    pipeline (실험이 켜졌을 때) — 사다리에서 ⑫ 다음, ⑨ 앞
설정:
    DOCSTRUCT_EXP_AGREED_GRID=1 (기본 꺼짐)

왜 있는가 (행안부 정답 대조)
-------------------------
⑫를 켜고도 `source="parser"`(아무도 손대지 않은) 표에 병합 73개가
남았다. 그 근거를 갈라 보니 **90% 가 지면에 근거가 있었다**:

    합의(사각형∩괘선) 있음   30개
    lattice 만 있음          20개
    사각형만 있음            16개
    기하 근거 없음            7개   ← 여기만 VLM 몫

⑫가 손대지 못한 까닭은 그 병합들이 **머리 2행 밖**에 있었기 때문이다
(⑫는 MAX_HEAD_ROWS=2). 예: 299쪽 table_196 은 정답 병합 5개가 모두
세로 2칸인데 본문 행에 걸쳐 있다.

왜 '합의' 만 쓰는가
-----------------
단일 근거는 문서군을 탄다 — lattice 69.6~84.5%, 사각형 81.7~91.9%
(§21·§22). 그러나 **둘이 합의한 병합**은 다르다:

    행안부 실측: agreed_missing 810개 중 정답 일치 **810개 = 100.0%**
    (합의 병합이 있는 표 129개 · 정답 병합 1,015개 기준)

⑦(덮개≥1.0)과 같은 오탐 0 이다. 두 독립 근거가 같은 자리를 짚었다는
것은 지면이 그렇게 그려져 있다는 뜻이고, 인식만 놓친 것이다.

⑪(grid_score)이 이미 두 후보를 계산해 `agreed_missing` 에 담아 두므로
추가 비용도 없다.

안전 장치
--------
    · 합의가 없으면 아무것도 하지 않는다 (단일 근거는 쓰지 않는다)
    · **좌표계 검사** — agreed 의 (row, col) 은 기하 격자의 번호다. 두
      기하 격자(rect·lattice)와 인식의 열 수가 **모두 같을 때만** 반영한다.
      인식이 열을 잃은 표(⑬의 표적)에서는 같은 번호가 지면의 다른 자리를
      가리키므로, 하나라도 다르면 물러난다 (⑪이 남긴 agreed_cols 로 잰다)
    · ⑦·⑫가 이미 손댄 표는 건너뛴다 (결정론이 이긴 자리는 지킨다)
    · 반영 결과에 겹치는 셀이 생기면 **통째로 되돌린다** — 병합별 검사가
      막지 못한 경우의 마지막 그물이다 (없는 결함을 만들지 않는다)

이 넷 중 좌표계 검사와 겹침 되돌림은 0.4.1 문서에는 약속돼 있었으나
구현이 없었다 (0.4.2 에서 넣음). 발화 0(조달청 최종 조합)이라 드러나지
않았을 뿐, 행안부 parser 표에서 발화하는 순간 필요해지는 장치다.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from docstruct.experiments.registry import Experiment, register

if TYPE_CHECKING:
    from docstruct.models import PageContent

_log = logging.getLogger(__name__)


def _detected_cols(cells: list[dict] | None) -> int:
    """인식 결과의 열 수."""
    if not cells:
        return 0
    return max(int(c.get("col", 0)) + int(c.get("colspan", 1) or 1) for c in cells)


def _overlapped(cells: list[dict]) -> bool:
    """두 셀이 같은 자리를 덮는 곳이 있는가.

    입력: cells — 셀 목록
    출력: 겹침이 있으면 True
    비고:
        `_cover` 는 겹침을 조용히 덮어써 못 본다 — 여기서는 자리마다
        셀 수를 세어 겹침 자체를 잡는다. 반영 **전에** 이미 겹쳐 있던
        표도 True 가 되는데, 그런 표는 좌표가 깨진 것이므로 손대지 않는
        쪽이 맞다 (보수 방향).
    """
    seen: set[tuple[int, int]] = set()
    for cell in cells:
        row, col = int(cell.get("row", 0)), int(cell.get("col", 0))
        rowspan = int(cell.get("rowspan", 1) or 1)
        colspan = int(cell.get("colspan", 1) or 1)
        for r in range(row, row + rowspan):
            for c in range(col, col + colspan):
                if (r, c) in seen:
                    return True
                seen.add((r, c))
    return False


def _cover(cells: list[dict]) -> tuple[dict, int, int]:
    """자리 → 셀 지도와 격자 크기.

    입력: cells — 셀 목록
    출력: ({(row, col): 셀}, 행 수, 열 수)
    """
    cover: dict[tuple[int, int], dict] = {}
    rows = cols = 0
    for cell in cells:
        row, col = int(cell.get("row", 0)), int(cell.get("col", 0))
        rowspan = int(cell.get("rowspan", 1) or 1)
        colspan = int(cell.get("colspan", 1) or 1)
        for r in range(row, row + rowspan):
            for c in range(col, col + colspan):
                cover[(r, c)] = cell
        rows = max(rows, row + rowspan)
        cols = max(cols, col + colspan)
    return cover, rows, cols


def apply_agreed(table, agreed: list) -> dict | None:
    """합의 병합을 표에 반영한다.

    입력: table — TableInfo, agreed — [(row, col, rowspan, colspan)]
    출력: {"applied": n, "before": n, "after": n} 또는 None (반영 안 함)
    비고:
        병합이 덮는 자리의 셀들을 하나로 합친다. 글자는 **인식이 읽은
        것을 이어 붙인다** — 자리는 지면이, 글자는 인식이 안다. 덮인
        자리가 이미 다른 병합에 속해 있으면 그 병합은 건너뛴다(겹침 방지).
    """
    cells = [dict(c) for c in (table.cells or [])]
    if not cells:
        return None
    cover, rows, cols = _cover(cells)
    before = sum(1 for c in cells
                 if (c.get("rowspan", 1) or 1) > 1 or (c.get("colspan", 1) or 1) > 1)

    applied = 0
    for row, col, rowspan, colspan in sorted(agreed):
        if row + rowspan > rows or col + colspan > cols:
            continue                             # 격자 밖
        covered = [cover.get((r, c))
                   for r in range(row, row + rowspan)
                   for c in range(col, col + colspan)]
        if any(x is None for x in covered):
            continue
        # 이미 병합된 셀이 섞여 있으면 건드리지 않는다
        if any((x.get("rowspan", 1) or 1) > 1 or (x.get("colspan", 1) or 1) > 1
               for x in covered):
            continue
        seen: list[dict] = []
        for x in covered:
            if x not in seen:
                seen.append(x)
        texts = [(x.get("text") or "").strip() for x in seen]
        texts = [t for t in texts if t]
        anchor = seen[0]
        anchor["row"], anchor["col"] = row, col
        anchor["rowspan"], anchor["colspan"] = rowspan, colspan
        anchor["text"] = " ".join(texts)
        for x in seen[1:]:
            if x in cells:
                cells.remove(x)
        cover, rows, cols = _cover(cells)
        applied += 1

    if not applied:
        return None
    if _overlapped(cells):
        return None                              # 겹침이 생겼다 — 원본 유지 (되돌림)
    table.cells = cells
    after = sum(1 for c in cells
                if (c.get("rowspan", 1) or 1) > 1 or (c.get("colspan", 1) or 1) > 1)
    return {"applied": applied, "before": before, "after": after}


def run(pages: list[PageContent], **kwargs) -> int:
    """합의 병합을 반영한다.

    입력: pages — 페이지 목록 (제자리 갱신)
    출력: 고친 표 수
    비고:
        ⑪(grid_score)이 남긴 `agreed_missing` 을 읽기만 한다 — 추가
        계산이 없다. ⑪이 꺼져 있으면 아무것도 하지 않는다.
    """
    fixed = 0
    for page in pages:
        for table in (page.tables or []):
            if table.source == "grid" or getattr(table, "head_grid", None):
                continue                         # ⑦·⑫가 이미 손댔다
            score = getattr(table, "grid_score", None)
            agreed = (score or {}).get("agreed_missing") or []
            if not agreed:
                continue
            # **좌표계 검사.** agreed 의 (row, col) 은 기하 격자의 번호다.
            # 두 기하 격자와 인식의 열 수가 모두 같아야 같은 자리를
            # 가리킨다 — 인식이 열을 잃은 표(⑬의 표적)에서는 한 칸씩
            # 밀린 자리에 병합을 심게 된다. agreed_cols 가 없으면(옛 ⑪
            # 기록) 판정할 수 없으므로 물러난다 — 오탐 0 이 먼저다.
            cols = (score or {}).get("agreed_cols") or {}
            detected = _detected_cols(table.cells)
            if (not detected or cols.get("rect") != detected
                    or cols.get("lattice") != detected):
                continue
            report = apply_agreed(table, [tuple(x) for x in agreed])
            if report is None:
                continue
            table.agreed_grid = report
            fixed += 1
            page.trace.add(
                "experiments.tsr.restore.agreed_grid", "합의 병합 반영",
                f"{table.id} · 두 기하 근거가 합의한 병합 {report['applied']}개 "
                f"(병합 {report['before']} → {report['after']})",
                status="warn")
    return fixed


register(Experiment(
    key="agreed_grid",
    title="합의 병합 반영",
    purpose="배경 사각형과 괘선 격자가 둘 다 인정한 병합만 표에 반영 (본문 포함)",
    origin="행안부 정답 대조 — ⑫를 켜고도 남은 병합의 90%가 지면에 근거가 "
           "있었고, 합의 병합 810개는 정답과 100% 일치했다",
    run=run,
    formats=("pdf",),
    needs=("cells", "vector"),
    status="testing",
    note="⑫는 머리 2행만 본다(MAX_HEAD_ROWS=2). 본문에 걸친 세로 병합은 "
         "그 밖이라 손대지 못했다. 단일 근거는 문서군을 타지만(lattice "
         "69.6~84.5% · 사각형 81.7~91.9%) **둘이 합의한 병합은 실측 "
         "100.0%** 다 — ⑦과 같은 오탐 0 수준이다. ⑪이 이미 계산해 둔 "
         "agreed_missing 을 읽기만 하므로 추가 비용이 없다. 0.4.2: "
         "좌표계 검사(기하·인식 열 수 일치 요구)와 겹침 되돌림을 구현. "
         "**0.4.20 판정: 켜지 않는다.** 행안부 실측 6표 발화 · 개선 0 · "
         "악화 4 · 동일 2 — 근거(합의 병합 810/810)는 최상급인데 "
         "`_cover` 가 덮어쓰면서 그 자리의 기존 정답 병합을 함께 지운다"
         "(table_165: 2개 더하고 4개 없앰). 겹침 되돌림은 '겹치는가' 만 "
         "보므로 '없앴는가' 를 놓친다. 다시 켜려면 반영 전후의 병합 수를 "
         "견주어 줄면 버리도록 고쳐야 한다.",
    knobs={"DOCSTRUCT_EXP_AGREED_GRID": "1 이면 켬 (기본 꺼짐)"},
))
