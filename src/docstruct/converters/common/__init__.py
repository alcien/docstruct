"""converters.common — 형식 공통 유틸.

축: 공통.
모듈 (입력 → 출력 · 역할):
    table.py              격자(list[list[str]]) → GFM 표 문자열
                          `render_md_table` · `display_width`(한글 2칸).
                          hwpxtree·html.tables·structuring.checks 가 같은
                          렌더 규칙을 쓰도록 한곳에 둔다.
"""
from docstruct.converters.common.table import display_width, render_md_table

__all__ = ["display_width", "render_md_table"]
