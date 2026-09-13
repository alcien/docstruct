"""markdown 표 렌더링 공통 유틸.

입력:
    격자(list[list[str]])

역할:
    문자열 격자를 열 폭이 맞춰진 GFM 표로 만든다.
    한글·한자처럼 폭이 2인 문자를 고려해 정렬한다.
호출부:
    converters.html.tables, docstruct.tables.docling
출력:
    GFM 표 문자열
"""
from __future__ import annotations

import unicodedata


def display_width(s: str) -> int:
    """문자열의 표시 폭을 센다.

    입력: text — 문자열
    출력: 폭 (한글·한자 등 전각 문자는 2로 계산)
    """
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def render_md_table(rows: list[list[str]]) -> str:
    """문자열 격자를 GFM 표로 만든다.

    입력: rows — 행 목록 (각 행은 셀 문자열 목록)
    출력: 열 폭이 맞춰진 GFM 표 문자열
    """
    if not rows:
        return ""

    escaped = [[c.replace("|", "\\|").replace("\n", " ") for c in row] for row in rows]
    n_cols = max(len(r) for r in escaped)
    norm = [r + [""] * (n_cols - len(r)) for r in escaped]

    col_w = [
        max(3, *(display_width(norm[r][c]) for r in range(len(norm))))
        for c in range(n_cols)
    ]

    def pad(s: str, w: int) -> str:
        """셀을 지정 폭에 맞춰 채운다.

        입력: text — 셀 문자열, width — 목표 폭
        출력: 공백이 채워진 문자열
        """
        return s + " " * (w - display_width(s))

    def row_str(cells: list[str]) -> str:
        """셀 목록 하나를 GFM 표의 한 행 문자열로 만든다.

        입력: cells — 폭이 정규화된 셀 문자열 목록
        출력: `| a | b |` 형태 문자열 (열 폭에 맞춰 패딩)
        """
        return "| " + " | ".join(pad(cells[j], col_w[j]) for j in range(n_cols)) + " |"

    sep = "|-" + "-|-".join("-" * w for w in col_w) + "-|"
    lines = [row_str(norm[0]), sep] + [row_str(r) for r in norm[1:]]
    return "\n".join(lines)


#: 세로 병합이 이어지는 칸의 옛 표식. 값이 아니라 "위 칸이 이어진다" 는 뜻.
MERGE_UP = "〃"

#: 병합 채우기 방식을 고르는 환경변수.
MERGE_FILL_ENV = "DOCSTRUCT_MERGE_FILL"


def merge_fill_mode() -> str:
    """세로 병합으로 덮인 칸을 무엇으로 채울지.

    입력: 없음 (`DOCSTRUCT_MERGE_FILL`)
    출력: "repeat" | "ditto" | "blank"
    비고:
        **기본은 `repeat`** (0.5.6). 병합된 값을 덮인 행마다 되풀이한다.

        왜 바꿨나 — `〃` 는 사람이 표를 **통째로 볼 때**만 뜻이 통한다.
        이 결과물의 주된 쓰임은 RAG 이고, 거기서는 행 하나가 잘려 나가
        조각이 된다. `| 11 | 3000 | 〃 | 3031 | … |` 만 남으면 무엇이
        이어졌는지 알 길이 없다 — 값이 통째로 사라진 것과 같다.
        되풀이해 두면 조각 하나로도 뜻이 선다.

        `ditto` 로 두면 예전 모양이다. 표 구조 자체(`cells` 의
        rowspan)는 어느 쪽이든 그대로이므로, 병합이 있었다는 사실은
        잃지 않는다 — 되풀이는 **보기 방식**이지 구조가 아니다.
    """
    import os

    value = os.environ.get(MERGE_FILL_ENV, "").strip().lower()
    if value in ("ditto", "mark", "〃"):
        return "ditto"
    if value in ("blank", "empty", ""):
        return "blank" if value else "repeat"
    return "repeat"


def merge_continuation(anchor: str) -> str:
    """병합으로 덮인 칸에 넣을 글.

    입력: anchor — 병합의 맨 위(왼쪽 위) 칸의 글
    출력: 그 자리에 넣을 문자열
    비고:
        `repeat` 이면 닻의 값을 그대로 되풀이한다. 닻이 비어 있으면
        되풀이할 것이 없으므로 빈 칸으로 둔다 — 빈 값을 `〃` 로 적으면
        "위에 무언가 있다" 는 거짓말이 된다.
    """
    mode = merge_fill_mode()
    if mode == "blank":
        return ""
    if mode == "ditto":
        return MERGE_UP
    return (anchor or "").strip()
