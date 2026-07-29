"""포맷 공통 유틸.

역할:
    여러 컨버터가 함께 쓰는 표 렌더링 등을 담는다.
호출부:
    converters.html, docstruct.tables.docling
출력:
    render_md_table, display_width
"""
from docstruct.converters.common.table import display_width, render_md_table

__all__ = ["display_width", "render_md_table"]
