"""계층 추출 — 병합 포함 관계를 부모>자식 삼항으로 (구조화 §5-2, H9(b) 1단계).

입력:
    cells
출력:
    부모>자식 삼항 (계층, §5-2)

역할:
    rowspan 닻(부모)이 덮는 행들의 오른쪽 이름 열 값(자식)을 삼항으로
    뽑는다. "성실납세 및 민생지원 > 납세자 권익보호" 같은 것이다.
호출부:
    structuring.structure_document
설정:
    없음 — 순수 함수다.

근거 위계 (§19-3)
---------------
    rowspan   지면에 그려진 소속 표시 — 1순위 증거
    코드 열    같은 값 연속 구간 — 병합이 없는 표의 2순위 증거 (전개
              레코드에서 왼쪽 열 묶음으로 같은 꼴이 된다 — count_check
              의 scope 와 동일 규칙이라 서로 검산한다)
"""
from __future__ import annotations

from docstruct.structuring.expand import _anchor_grid


def _name_columns(names: list[str]) -> list[int]:
    """이름 열 후보 — '…명' 으로 끝나는 열 번호.

    입력: names — 열 이름 목록
    출력: 열 번호 목록 (없으면 빈 목록)
    """
    return [i for i, n in enumerate(names) if n.endswith("명")]


def extract_hierarchy(cells: list[dict], names: list[str]) -> list[dict]:
    """rowspan 포함 관계 → 부모>자식 삼항.

    입력: cells — 셀 목록, names — column_names 산출
    출력: [{"parent","parent_col","child","child_col","evidence"}]
    비고:
        부모는 이름 열의 rowspan>1 닻만 — 숫자 열의 병합(합계 칸 등)은
        소속이 아니라 값의 공유라 삼항으로 만들지 않는다. 자식 열은
        부모 열 **오른쪽에서 가장 가까운 이름 열**이다 (프로그램명 →
        단위사업명 → 세부사업명 사슬이 그 꼴이다).
    """
    name_cols = _name_columns(names)
    if not name_cols:
        return []
    grid, rows, _ = _anchor_grid(cells)

    triples: list[dict] = []
    for (row, col), cell in sorted(grid.items()):
        if row == 0 or col not in name_cols:
            continue
        rowspan = int(cell.get("rowspan", 1) or 1)
        if rowspan <= 1:
            continue
        parent = (cell.get("text") or "").strip()
        if not parent:
            continue
        child_cols = [c for c in name_cols if c > col]
        if not child_cols:
            continue
        child_col = child_cols[0]
        seen: set[str] = set()
        for covered_row in range(row, row + rowspan):
            child_cell = grid.get((covered_row, child_col))
            child = (child_cell.get("text") or "").strip() if child_cell else ""
            if child and child not in seen:
                seen.add(child)
                triples.append({
                    "parent": parent, "parent_col": names[col],
                    "child": child, "child_col": names[child_col],
                    "evidence": "rowspan",
                })
    return triples
