"""본문 markdown 의 표를 `<table N>` 블록으로 치환.

역할:
    HWP/HWPX 경로에서 markdown 안에 그대로 들어 있는 GFM 표를 찾아
    번호를 매기고 태그 블록으로 감싼다. PDF 경로는 추출 단계에서 이미
    블록으로 만들어지므로 이 모듈을 쓰지 않는다.
호출부:
    docstruct.extractors.hwp / docstruct.extractors.hwpx
출력:
    (본문 markdown, TableInfo 목록, 표 개수)
"""
from __future__ import annotations

import re

from docstruct.models import TableInfo
from docstruct.tables.tags import make_table_block, make_table_id, open_tag

_MD_TABLE_BLOCK = re.compile(
    # 주의: 구분선 문자클래스에 \s 를 쓰면 개행까지 소비해서
#      표가 3행째에서 잘립니다. 공백/탭만 허용해야 합니다.
    r"(\|[^\n]+\|\n\|[-:| \t]+\|(?:\n\|[^\n]+\|)*)",
    re.MULTILINE,
)


def inject_table_placeholders(
    content: str,
    start_id: int = 0,
) -> tuple[str, list[TableInfo], int]:
    """markdown 안의 GFM 표를 태그 블록으로 감싼다.

    입력: md — 표가 포함된 markdown
    출력:
        content  표가 `<table N>` 블록으로 감싸인 본문
        tables   TableInfo 목록 (표 번호는 1부터)
        count    찾은 표 개수
    """
    tables: list[TableInfo] = []
    counter = start_id

    def repl(match: re.Match[str]) -> str:
        nonlocal counter
        counter += 1
        table_id = make_table_id(counter)
        placeholder = open_tag(counter)
        md = match.group(1).strip()
        tables.append(
            TableInfo(
                id=table_id,
                table_num=counter,
                placeholder=placeholder,
                markdown=md,
            )
        )
        return make_table_block(counter, md)

    new_content = _MD_TABLE_BLOCK.sub(repl, content)
    return new_content, tables, counter
