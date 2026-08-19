"""같은 서식 표끼리 견주어 이상한 표를 찾는다.

역할:
    문서 안에서 헤더가 같은 표들을 묶고, 그중 열 수가 다른 표를 표시한다.
호출부:
    docstruct.pipeline (`flag_odd_tables` 가 켜졌을 때)
입력: 표가 담긴 PageContent 목록
출력: 표시한 표 수

왜 이 방법인가
------------
표 하나만 보고 "구조가 깨졌다" 를 판정할 방법이 마땅치 않다. 빈 칸 비율을
써 봤으나 **정상 표를 82% 나 잡아** 쓸 수 없었다 — docling 은 값이 없는
칸에 셀을 만들지 않으므로 빈 칸이 결함처럼 보인다.

그런데 정부 문서는 같은 서식 표를 여러 쪽에 걸쳐 반복한다. 실제 문서에서
헤더가 같은 표 12개 중 **11개가 8열, 하나만 7열**이었다. 그 하나가 헤더 두
칸을 하나로 뭉친 표였다.

    정상    ... | 재정사업 평가명 | 성과평가 결과 | 비고 |   (8열)
    이상    ... | 재정사업 성과평가 평가명 결과 | 비고 |     (7열)

**다수결이 근거다.** 같은 서식인데 혼자만 다르면 그 하나가 틀렸을 가능성이
높다. 문서 밖의 기준이 필요 없고, 왜 표시됐는지 설명도 된다.

한계
----
- 같은 서식 표가 **셋 이상** 있어야 다수를 판단할 수 있다. 한 번만 나오는
  표는 비교 대상이 없어 검사하지 못한다.
- 다수가 틀렸으면 소수를 이상으로 본다. 서식이 실제로 바뀐 경우(중간에
  열이 추가된 표)도 표시될 수 있다.
- 열 수만 본다. 열 수는 같은데 내용이 밀린 표는 잡지 못한다.
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict

from docstruct.models import PageContent

_log = logging.getLogger(__name__)

#: 다수를 판단할 최소 표 수. 둘뿐이면 어느 쪽이 옳은지 알 수 없다.
MIN_GROUP = 3

#: 서식이 같다고 볼 때 견주는 헤더 칸 수.
#: 전부 견주면 표마다 조금씩 달라 묶이지 않고, 하나만 보면 다른 서식이
#: 섞인다. 실측에서 셋이 적당했다.
HEADER_KEY_CELLS = 3


def _header_cells(markdown: str) -> list[str]:
    """표의 첫 행 셀 목록.

    입력: markdown — GFM 표
    출력: 셀 문자열 목록. 표가 아니면 빈 목록
    """
    lines = [
        line for line in (markdown or "").splitlines()
        if line.startswith("|") and set(line.strip()) - set("|-: ")
    ]
    if not lines:
        return []
    return [cell.strip() for cell in lines[0].strip("|").split("|")]


def find_odd_tables(pages: list[PageContent]) -> list[tuple[PageContent, object, int, int]]:
    """서식이 같은 표 중 열 수가 다른 것을 찾는다.

    입력: pages — 표가 담긴 페이지 목록
    출력: (페이지, 표, 이 표의 열 수, 다수의 열 수) 목록
    비고:
        헤더 앞 세 칸이 같으면 같은 서식으로 본다. 그 안에서 열 수의
        최빈값을 구하고, 그와 다른 표를 돌려준다.
    """
    groups: dict[tuple, list[tuple[PageContent, object, int]]] = defaultdict(list)
    for page in pages:
        for table in page.tables:
            header = _header_cells(table.markdown)
            if not header:
                continue
            key = tuple(header[:HEADER_KEY_CELLS])
            groups[key].append((page, table, len(header)))

    odd: list[tuple[PageContent, object, int, int]] = []
    for members in groups.values():
        if len(members) < MIN_GROUP:
            continue
        counts = Counter(width for _, _, width in members)
        majority, majority_count = counts.most_common(1)[0]
        # 다수가 과반이어야 기준으로 삼는다. 반반이면 판단하지 않는다.
        if majority_count <= len(members) / 2:
            continue
        odd.extend(
            (page, table, width, majority)
            for page, table, width in members if width != majority
        )
    return odd
