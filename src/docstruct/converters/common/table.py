"""markdown 표 렌더링 공통 유틸.

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
        return "| " + " | ".join(pad(cells[j], col_w[j]) for j in range(n_cols)) + " |"

    sep = "|-" + "-|-".join("-" * w for w in col_w) + "-|"
    lines = [row_str(norm[0]), sep] + [row_str(r) for r in norm[1:]]
    return "\n".join(lines)
