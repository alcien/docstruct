"""본문 placeholder 를 실제 내용으로 되돌리기.

역할:
    `<table N>` 블록과 이미지 주석을 실제 표 markdown·설명 텍스트로
    바꾼 완전한 본문을 만든다. 목차 추출처럼 전체 텍스트가 필요한 곳에서 쓴다.
호출부:
    docstruct.outline.builder
출력:
    placeholder 가 모두 치환된 본문 문자열
"""
from __future__ import annotations

from typing import Iterable

from docstruct.models import ImageInfo, TableInfo
from docstruct.tables.tags import replace_block_with_markdown


def expand_tables_and_images(
    content: str,
    tables: Iterable[TableInfo],
    images: Iterable[ImageInfo],
) -> str:
    """본문의 표·이미지 placeholder 를 실제 내용으로 치환한다.

    입력:
        content  본문 markdown
        tables   TableInfo 목록
        images   ImageInfo 목록
    출력: 표는 GFM 으로, 이미지는 설명 텍스트로 바뀐 본문
    """
    expanded = content or ""
    for table in tables:
        md = table.markdown.strip() if table.markdown else ""
        if md:
            expanded = replace_block_with_markdown(expanded, table.table_num, md)
    for image in images:
        replacement = image.description or image.placeholder
        expanded = expanded.replace(image.placeholder, replacement)
    return expanded.strip()
