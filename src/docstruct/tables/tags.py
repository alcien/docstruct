"""`<table N> ... </table N>` 블록 생성·파싱·동기화.

역할:
    본문 markdown 안에서 표를 감싸는 태그 블록을 다루는 문자열 유틸.
    LLM 이 표 위치를 지목할 수 있게 하고, 재추출 결과를 본문에 되돌린다.
호출부:
    docstruct.tables.markdown  블록 삽입
    docstruct.tables.fill      블록 교체·제거·컨텍스트 추출
    docstruct.pipeline         정규화
    docstruct.preview          표시용 파싱
출력:
    문자열 (본문 markdown) 또는 위치 정보
"""
from __future__ import annotations

import re

TABLE_BLOCK_RE = re.compile(
    r"<table (\d+)>\s*(.*?)\s*</table \1>",
    re.DOTALL,
)
TABLE_OPEN_RE = re.compile(r"<table (\d+)>")

_HEADING = re.compile(r"^#{1,6}\s+.+$", re.MULTILINE)


def make_table_id(num: int) -> str:
    """표 id 문자열.

    입력: num — 표 번호
    출력: 'table_3' 형태
    """
    return f"table_{num}"


def open_tag(num: int) -> str:
    """여는 태그.

    입력: num — 표 번호
    출력: '<table 3>'
    """
    return f"<table {num}>"


def close_tag(num: int) -> str:
    """닫는 태그.

    입력: num — 표 번호
    출력: '</table 3>'
    """
    return f"</table {num}>"


def make_table_block(num: int, markdown: str) -> str:
    """표 블록 문자열을 만든다.

    입력: num — 표 번호, markdown — 표 내용
    출력: 태그와 GFM 사이에 빈 줄을 둔 블록. 내용이 없으면 빈 블록
    """
    md = (markdown or "").strip()
    if md:
        return f"{open_tag(num)}\n\n{md}\n\n{close_tag(num)}"
    return f"{open_tag(num)}\n{close_tag(num)}"


def normalize_table_blocks(content: str) -> str:
    """본문의 모든 표 블록을 표준 형식으로 정규화한다.

    입력: content — 본문 markdown
    출력: 태그와 내용 사이 간격이 일정해진 본문
    """

    def _repl(match: re.Match[str]) -> str:
        num = int(match.group(1))
        inner = match.group(2).strip()
        return make_table_block(num, inner)

    return TABLE_BLOCK_RE.sub(_repl, content)


def block_span(content: str, num: int) -> tuple[int, int] | None:
    """표 블록의 본문 내 위치.

    입력: content — 본문, num — 표 번호
    출력: (시작, 끝) 인덱스. 블록이 없으면 None
    """
    pattern = re.compile(
        rf"<table {num}>\s*.*?\s*</table {num}>",
        re.DOTALL,
    )
    m = pattern.search(content)
    if not m:
        return None
    return m.start(), m.end()


def sync_table_block(content: str, num: int, markdown: str) -> str:
    """본문의 표 블록을 새 markdown 으로 교체한다.

    입력: content — 본문, num — 표 번호, markdown — 새 내용
    출력: 교체된 본문. 블록이 없으면 원본 그대로
    """
    span = block_span(content, num)
    block = make_table_block(num, markdown)
    if span is None:
        return content
    start, end = span
    return content[:start] + block + content[end:]


def extract_block_markdown(content: str, num: int) -> str:
    """표 블록 안의 내용만 꺼낸다.

    입력: content — 본문, num — 표 번호
    출력: 태그를 뺀 표 markdown. 블록이 없으면 빈 문자열
    """
    pattern = re.compile(
        rf"<table {num}>\s*(.*?)\s*</table {num}>",
        re.DOTALL,
    )
    m = pattern.search(content)
    return m.group(1).strip() if m else ""


def page_context_slice(content: str, num: int) -> str:
    """표 주변 본문을 잘라낸다 (LLM 재추출 컨텍스트용).

    입력: content — 본문, num — 표 번호
    출력: 대상 표와 앞뒤 문맥을 포함한 문자열
    """
    opens = list(TABLE_OPEN_RE.finditer(content))
    target_idx = next(
        (i for i, m in enumerate(opens) if int(m.group(1)) == num),
        None,
    )
    span = block_span(content, num)
    if span is None:
        return content.strip()

    if target_idx is not None and target_idx > 0:
        start = opens[target_idx - 1].start()
    else:
        headings = list(_HEADING.finditer(content[: span[0]]))
        start = headings[-1].start() if headings else 0

    if target_idx is not None and target_idx + 1 < len(opens):
        end = opens[target_idx + 1].start()
    else:
        end = len(content)

    return content[start:end].strip()


def replace_block_with_markdown(content: str, num: int, markdown: str) -> str:
    """표 블록을 태그 없는 내용으로 바꾼다.

    입력: content — 본문, num — 표 번호, markdown — 넣을 내용
    출력: 태그가 사라진 본문
    """
    span = block_span(content, num)
    if span is None:
        return content
    start, end = span
    md = (markdown or "").strip()
    return content[:start] + md + content[end:]
