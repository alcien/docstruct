"""병합 전개 — cells 를 행 단위 레코드로 편다 (구조화 §5-1).

입력:
    cells
출력:
    행 단위 레코드 (병합 전개, §5-1)

역할:
    rowspan 이 덮은 행에 닻 값을 복제해, 표를 "행마다 완결된 레코드"
    목록으로 바꾼다. §19-2 갭(〃 로 끊긴 소속)의 해소 본체다.
호출부:
    structuring.structure_document · overlay structure 단계 · 시험 CLI
설정:
    없음 — 순수 함수다.

원칙 (설계 문서 §3)
-----------------
값을 만들지 않는다 — 레코드의 모든 값은 cells 에서 왔고, 전개로 온 값은
`_expanded` 에 열 이름을 남긴다. 판독이 병합을 놓쳤다면 여기서도 끊긴
채로 나온다 (그건 판독의 문제고, 지어내지 않는 것이 이 층의 계약이다).
"""
from __future__ import annotations

#: hwpxtree·grid_restore 와 같은 세로 이어짐 표식.
MERGE_UP = "〃"


def _anchor_grid(cells: list[dict]) -> tuple[dict, int, int]:
    """(row, col) → 닻 셀 지도와 격자 크기.

    입력: cells — {"row","col","rowspan","colspan","text"} 목록
    출력: (지도, 행 수, 열 수)
    """
    grid: dict[tuple[int, int], dict] = {}
    rows = cols = 0
    for cell in cells:
        row = int(cell.get("row", 0) or 0)
        col = int(cell.get("col", 0) or 0)
        grid[(row, col)] = cell
        rows = max(rows, row + int(cell.get("rowspan", 1) or 1))
        cols = max(cols, col + int(cell.get("colspan", 1) or 1))
    return grid, rows, cols


def column_names(cells: list[dict]) -> list[str]:
    """0행(머리행)에서 열 이름을 뽑는다.

    입력: cells — 셀 목록
    출력: 열 이름 목록 (colspan 머리는 덮는 열마다 같은 이름)
    비고:
        여러 줄 머리(계층 머리행)는 v1 에서 0행만 쓴다 — 아래 행 머리가
        필요한 표가 실측되면 그때 합성 규칙을 들인다 (지어내지 않는다).
        이름이 빈 열은 "col{n}" 으로 채워 레코드 열쇠 충돌을 막는다.
    """
    grid, _, cols = _anchor_grid(cells)
    names = [""] * cols
    for (row, col), cell in grid.items():
        if row != 0:
            continue
        span = int(cell.get("colspan", 1) or 1)
        for offset in range(span):
            if col + offset < cols:
                names[col + offset] = (cell.get("text") or "").strip()
    out = []
    seen: dict[str, int] = {}
    for index, name in enumerate(names):
        if not name:
            name = f"col{index}"
        if name in seen:                          # 같은 이름 열 구분
            seen[name] += 1
            name = f"{name}#{seen[name]}"
        else:
            seen[name] = 0
        out.append(name)
    return out


def expand_merges(cells: list[dict]) -> list[dict]:
    """cells → 행 단위 레코드 (병합 전개).

    입력: cells — 셀 목록 (닻 표현 — 덮인 자리는 셀이 없다)
    출력: [{열이름: 값, "_row": n, "_expanded": [열이름…]}] — 머리행 제외
    비고:
        전개 규칙 둘, 모두 결정론이다:
          · rowspan>1 닻 → 덮인 행에 값 복제 (지면의 병합 = 저자의 소속 표시)
          · 값이 `〃` → 위 행 값 복제 (판독기가 이미 병합을 표식으로 편 경우)
        빈칸 상속은 **하지 않는다** — 빈칸이 "위와 같음" 인 표와 "정말
        빈" 표를 지면만으로 못 가르므로, 그 규칙은 kind 별 정규화(후속)
        에서 근거를 갖고 켠다.
    """
    if not cells:
        return []
    grid, rows, cols = _anchor_grid(cells)
    names = column_names(cells)

    # 덮은 자리 → 닻 값 지도 (rowspan·colspan 전개)
    value_at: dict[tuple[int, int], tuple[str, bool]] = {}
    for (row, col), cell in grid.items():
        text = (cell.get("text") or "").strip()
        for dr in range(int(cell.get("rowspan", 1) or 1)):
            for dc in range(int(cell.get("colspan", 1) or 1)):
                covered = (row + dr, col + dc)
                value_at[covered] = (text, dr > 0 or dc > 0)

    records: list[dict] = []
    previous: dict[str, str] = {}
    for row in range(1, rows):                    # 0행은 머리
        record: dict = {"_row": row, "_expanded": []}
        for col in range(cols):
            name = names[col]
            text, expanded = value_at.get((row, col), ("", False))
            if text == MERGE_UP:                  # 표식 → 위 행 값 복제
                text = previous.get(name, "")
                expanded = True
            record[name] = text
            if expanded and text:
                record["_expanded"].append(name)
        records.append(record)
        previous = {n: record[n] for n in names}
    return records
