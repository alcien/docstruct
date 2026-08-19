"""쪽을 넘어 이어지는 표를 찾아 관계를 표시한다.

역할:
    "이 표는 앞 표의 이어짐이고 헤더는 저기 있다" 를 기록한다.
    **markdown 은 건드리지 않는다.**
호출부:
    docstruct.pipeline (`mark_table_continuation` 이 켜졌을 때)
입력: 표가 담긴 PageContent 목록
출력: 표시한 표 수

무엇이 문제인가
------------
docling 은 페이지 단위로 처리하므로 쪽을 넘는 표를 별개로 본다. 실제 문서
(행안부 성과계획서 별첨3)에서 한 표가 **21쪽에 걸쳐** 있었는데, 첫 쪽에만
헤더가 있고 나머지는 데이터로 시작했다.

    6쪽  ['회 계', '계 정', '분 야', ...]        ← 헤더
    7쪽  ['11', '0', '010', '013', '1100', ...]  ← 데이터
    ...
    26쪽 ['11', '0', '020', '025', ...]          ← 데이터

7쪽 이후 값이 어느 열인지 알 수 없다. `537` 이 `'26예산` 인지 `'27예산안`
인지 모른다.

왜 고치지 않고 표시만 하는가
------------------------
헤더를 markdown 에 끼워 넣으면 원본이 변형된다. 그런데 열 수가 쪽마다
13~17 로 달라(빈 열이 잘림) 앞에서부터 억지로 맞추게 되고, 그 정렬이
틀리면 되돌릴 수 없다.

정보는 이미 다 있다 — 쪽 번호, 표 id, 앞 표에 헤더가 있다는 사실.
**관계만 기록하면** 구조화 단계가 실제 값을 보고 맞출 수 있고, 조각으로
쓸지 이어 볼지도 그쪽에서 정한다.

    {"id": "table_7", "continues_from": "table_6",
     "inherited_header": ["회 계", "계 정", ...]}

헤더 내용도 함께 담아 앞 표를 되짚지 않아도 되게 한다.

헤더가 매 쪽 반복되는 표는 건드리지 않는다
------------------------------------
같은 문서의 다른 표(별첨2)는 쪽마다 헤더가 다시 인쇄돼 있었다. 그런 표는
이미 쓸 수 있으므로 손대지 않는다.
"""
from __future__ import annotations

import logging
import re

from docstruct.models import PageContent

_log = logging.getLogger(__name__)

#: 첫 행이 데이터로 보일 최소 숫자 비율.
#: 실측에서 헤더 행은 0, 데이터 행은 0.4~0.6 이었다.
DATA_NUMERIC_RATIO = 0.3

#: 헤더를 물려줄 최대 연속 쪽 수. 이보다 길면 사람이 확인하는 편이 낫다.
MAX_CHAIN = 60

#: 열 수 차이 허용치. 빈 열이 잘려 쪽마다 달라진다(실측 13~17).
#: 너무 넓히면 다른 표까지 이어지므로 비율로 본다.
COLUMN_TOLERANCE = 0.35

_NUMERIC_RE = re.compile(r"^[\d,.\-–~%()]+$")


def _rows(markdown: str) -> list[list[str]]:
    """markdown 표를 셀 격자로 바꾼다.

    입력: markdown — GFM 표
    출력: 행별 셀 목록. 구분선은 뺀다
    """
    out: list[list[str]] = []
    for line in (markdown or "").splitlines():
        if not line.startswith("|"):
            continue
        if not set(line.strip()) - set("|-: "):      # 구분선
            continue
        out.append([cell.strip() for cell in line.strip("|").split("|")])
    return out


def looks_like_data(cells: list[str]) -> bool:
    """이 행이 헤더가 아니라 데이터인지.

    입력: cells — 한 행의 셀 목록
    출력: 데이터로 보이면 True
    비고:
        docling 의 `column_header` 플래그는 쓸 수 없다 — 헤더가 없는 표에도
        **항상 참**으로 표시된다(실측에서 세 표 모두 그랬다). 그래서 내용을
        본다.

        숫자·코드 비율이 높으면 데이터로 본다. 실측에서 헤더 행은 0,
        데이터 행은 0.4~0.6 이었다.
    """
    values = [cell for cell in cells if cell]
    if not values:
        return False
    numeric = sum(1 for cell in values if _NUMERIC_RE.match(cell))
    return numeric / len(values) >= DATA_NUMERIC_RATIO


def _similar_width(a: int, b: int) -> bool:
    """열 수가 같은 표로 볼 만큼 비슷한지.

    입력: a, b — 열 수
    출력: 비슷하면 True
    비고:
        같은 표인데도 쪽마다 열 수가 13~17 로 달랐다. 빈 열이 잘리기
        때문이다. 절대값이 아니라 비율로 본다.
    """
    if a <= 0 or b <= 0:
        return False
    return abs(a - b) / max(a, b) <= COLUMN_TOLERANCE


def mark_continuations(pages: list[PageContent]) -> int:
    """쪽을 넘는 표에 이어짐 관계를 표시한다.

    입력: pages — 표가 담긴 페이지 목록 (제자리 갱신)
    출력: 표시한 표 수
    비고:
        연속한 쪽에서, 앞 표에 헤더가 있고 뒤 표가 데이터로 시작하며 열
        수가 비슷하면 이어지는 것으로 본다. 세 조건이 **모두** 맞아야 한다.

        **markdown 은 건드리지 않는다.** 헤더를 끼워 넣으면 원본이 변형되고,
        열 수가 달라(실측 13~17) 앞에서부터 억지로 맞추게 된다. 그 정렬이
        틀리면 되돌릴 수 없다.

        관계만 기록하면 구조화 단계가 실제 값을 보고 맞출 수 있고, 조각으로
        쓸지 이어 볼지도 그쪽에서 정한다.

        잘못 표시하는 실수는 관계가 틀릴 뿐 원본은 남는다. 그래도 보수적으로
        간다 — 표시하지 않는 실수는 지금과 같을 뿐이다.
    """
    ordered = [
        (page, table)
        for page in sorted(pages, key=lambda p: p.page_no if isinstance(p.page_no, int) else 0)
        for table in page.tables
    ]

    marked = 0
    header: list[str] | None = None
    header_width = 0
    header_id: str | None = None
    header_page: int | None = None
    chain = 0

    for page, table in ordered:
        rows = _rows(table.markdown)
        if not rows:
            header = None
            continue
        width = len(rows[0])

        if not looks_like_data(rows[0]):
            # 헤더가 있는 표다. 다음 표들이 이어받을 후보가 된다.
            header, header_width = rows[0], width
            header_id, header_page, chain = table.id, page.page_no, 0
            continue

        if header is None or header_page is None:
            continue
        # 연속한 쪽이어야 한다. 사이에 다른 내용이 끼면 다른 표다.
        if not isinstance(page.page_no, int) or page.page_no - header_page > chain + 1:
            header = None
            continue
        if not _similar_width(header_width, width) or chain >= MAX_CHAIN:
            header = None
            continue

        table.continues_from = header_id
        # 헤더 내용을 함께 담아 둔다. 구조화 단계가 앞 표를 되짚지 않아도 된다.
        table.inherited_header = list(header)
        marked += 1
        chain += 1
        header_page = page.page_no
        page.trace.add(
            "docstruct.tables.continued", "표 이어짐",
            f"{table.id} · {header_id} 에서 이어집니다 (헤더 {len(header)}칸)")
    return marked


#: 옛 이름. 0.3.9 에서는 헤더를 markdown 에 끼워 넣었다.
inherit_headers = mark_continuations
