"""목차를 규칙으로 찾는다.

역할:
    `목차`·`차례` 머리글이 있는 쪽에서 `제목 … 쪽번호` 항목을 뽑는다.
호출부:
    docstruct.pipeline (`detect_toc` 가 켜졌을 때)
입력: PageContent 목록
출력: 목차 항목 목록

왜 규칙으로 하는가
---------------
LLM 목차 추출(`docstruct.outline`)이 이미 있으나 CLI 전용이고 호출 비용이
든다. 목차 줄은 **형태가 뚜렷하다** — 왼쪽에 제목, 오른쪽 끝에 쪽번호.
그 사이를 점선이 잇는다.

    3. 종합소득세 신고·납부 ······················· 172

두 가지 모양
----------
텍스트 PDF·HWPX 는 한 줄에 다 들어간다. **스캔본은 줄이 나뉜다** —
OCR 이 제목과 쪽번호를 다른 줄로 읽는다.

    '5.취득세에 부가되는세금'
    '57'

그래서 한 줄 안에서 찾고, 없으면 다음 줄이 숫자만인지 본다.

머리글로 범위를 좁힌다
------------------
문서 전체에서 `제목 … 숫자` 를 찾으면 본문의 참조도 걸린다. `목차`·`차례`
가 있는 쪽과 그 **앞뒤 2쪽**만 본다.
"""
from __future__ import annotations

import logging
import re

from docstruct.models import PageContent

_log = logging.getLogger(__name__)

#: 목차 머리글. 이 글자가 있는 쪽을 기준으로 삼는다.
#: 실물에서 본 유형: `목차` `차례` `차 례` `순 서` `CONTENTS`.
#: 자간을 벌려 쓰는 문서가 많아 글자 사이 공백을 허용한다.
_HEADING_RE = re.compile(
    r"목\s*차|차\s*례|순\s*서|CONTENTS|CONTENT", re.IGNORECASE)

#: 목차를 찾을 앞쪽 범위. 0 이면 전체를 본다.
#: 실측: 스캔본 7~15쪽 · 행안부 1쪽 · 25쪽 이후 0건. 논문도 표지·초록
#: 뒤에 오므로 앞쪽에 든다.
#:
#: **뒤쪽 목차가 있는 문서**(일본·중국 서적, 합본 자료집, 부록 목차)는
#: `DOCSTRUCT_TOC_HEAD_PAGES=0` 으로 전체를 본다.
HEAD_PAGES_ENV = "DOCSTRUCT_TOC_HEAD_PAGES"
DEFAULT_HEAD_PAGES = 30

#: 머리글 쪽에서 앞뒤로 몇 쪽까지 볼지.
#: 목차가 여러 쪽에 이어지는 문서가 흔하다 — 실측(주택과세금)에서 7·9·11·
#: 13·15쪽에 머리글이 반복됐다.
TOC_SPAN = 2

#: 한 줄 안에 `제목 … 쪽번호` 가 다 있는 모양.
#: 점선은 `·` `․` `‧` `∙` `…` `.` 이 섞여 쓰인다.
_INLINE_RE = re.compile(r"^(.{2,}?)[\s·․‧∙….]{3,}(\d{1,4})$")

#: 쪽번호만 있는 줄 (스캔본에서 줄이 나뉜 경우).
_PAGE_ONLY_RE = re.compile(r"^\s*(\d{1,4})\s*$")

#: 제목으로 볼 최소 길이 (공백 제외).
MIN_TITLE_CHARS = 2

#: 제목 앞에 붙는 번호·기호. 줄이 나뉜 경우(스캔본) 잡음을 거르는 데 쓴다.
#:
#: 실물에서 본 유형:
#:     제1부 · 제1장 · 제2편          편·장·부
#:     1. · 가. · 01 · 10            번호 (두 자리 앞자리 0 포함)
#:     I. · Ⅱ.                       로마자 (라틴·전각 둘 다)
#:     Q1. · ①                       질문·원문자
#:     ◆ · ▶ · ◦ · * · ㅇ           글머리표
#:
#: 넓게 잡되 **글머리표만 있고 번호가 없는 줄**도 받는다 — `◆ 사업 시작
#: 단계` 처럼 목차 소제목으로 쓰인다.
_NUMBERING_RE = re.compile(
    r"^\s*(?:"
    r"제?\s*\d+\s*[장절편부]"          # 제1장 · 제1부 · 2편
    r"|\d{1,3}\s*[.)]"                # 1. · 01) · 003.
    r"|\d{1,3}\s+\S"                  # 01 제목 · 10 제목 (점 없이)
    r"|[가-힣]\s*[.)]"                # 가. · 나)
    r"|[①-⑳]"                        # 원문자
    r"|[IVXivx]{1,4}\s*[.)]"          # I. · iv)
    r"|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]"                  # 전각 로마자
    r"|Q\s*\d+\s*[.)]"                # Q1.
    r"|[◆◇▶▷●○◦∙·※*＊■□]"             # 글머리표
    r")"
)


#: 쪽번호로 볼 최대값. 이보다 크면 금액·연도·건수다.
#: 실측 문서가 377~652쪽이라 넉넉히 잡되, `2025` 같은 연도는 걸러진다.
MAX_PAGE_NO = 1500

#: 쪽번호는 문서를 따라 **거의 단조 증가**한다. 실측(주택과세금 7쪽):
#:
#:     57 · 57 · 58 · 60 · 62 · 62 · 64 · 66 · 67 · 79 …
#:
#: 같은 값이 이어지기는 해도 되돌아가지는 않는다. 편이 바뀌며 작아지는
#: 경우를 위해 조금만 봐준다.
BACKWARD_TOLERANCE = 3

#: 앞 항목보다 이만큼 넘게 뛰면 쪽번호가 아니라 금액·건수로 본다.
#: 목차는 한 항목씩 조금씩 나아간다 — 실측에서 최대 도약이 12쪽이었다.
MAX_FORWARD_JUMP = 80


def _looks_like_page(value: int, previous: int | None) -> bool:
    """이 숫자를 쪽번호로 볼 수 있는가.

    입력: value — 후보 숫자, previous — 직전에 잡은 쪽번호
    출력: 쪽번호로 보이면 True
    비고:
        스캔본은 제목과 숫자가 다른 줄로 나뉘어, **본문의 금액도 같은 모양**
        이 된다.

            가. 취득세액      ← 제목처럼 보임
            537              ← 쪽번호처럼 보임

        쪽번호는 **문서를 따라 커진다**는 성질을 쓴다. 앞 항목보다 크게
        작아지면 본문 숫자다. 편이 바뀌며 되돌아가는 경우가 있어 약간의
        뒷걸음질은 봐준다.
    """
    if not 1 <= value <= MAX_PAGE_NO:
        return False
    if previous is None:
        return True
    if value < previous - BACKWARD_TOLERANCE:
        return False
    # 갑자기 크게 뛰면 금액·건수다 — `가. 취득세액 / 537`
    return value - previous <= MAX_FORWARD_JUMP


def _clean(text: str) -> str:
    """제목에서 점선과 군더더기를 뗀다.

    입력: text — 목차 줄의 제목 부분
    출력: 다듬은 제목
    """
    text = re.sub(r"[·․‧∙…]{2,}", "", text)
    return re.sub(r"\s+", " ", text).strip(" .·-")


#: 머리글 없이도 목차로 볼 최소 항목 수.
#: 실물에서 `CONTENTS` 같은 머리글 없이 바로 항목이 이어지는 목차가 있었다
#: (`1세대 1주택 비과세 ❶` 같은 제목만 있는 쪽).
#:
#: 목차 쪽은 **`제목 … 쪽번호` 가 여러 줄 이어진다.** 본문에는 그런 줄이
#: 드물게 하나둘 나오므로, 여럿이 모여 있으면 목차로 본다.
MIN_ENTRIES_WITHOUT_HEADING = 5

#: 그 줄들이 쪽 전체에서 차지하는 비율. 본문에 참조가 몇 줄 섞인 것과
#: 구분한다.
MIN_ENTRY_RATIO = 0.4


def _entry_like_lines(page: PageContent) -> tuple[int, int]:
    """이 쪽에서 목차 항목처럼 보이는 줄 수.

    입력: page — PageContent
    출력: (항목처럼 보이는 줄, 전체 줄)
    비고:
        머리글이 없는 목차를 알아보려면 쪽 자체의 모양을 봐야 한다.
        `제목 … 쪽번호` 가 여러 줄 이어지는 것이 목차의 특징이다.
    """
    lines = [ln.strip() for ln in (page.content or "").splitlines() if ln.strip()]
    body = [ln for ln in lines if not ln.startswith(("|", "<"))]
    if not body:
        return 0, 0
    hits = sum(1 for ln in body if _INLINE_RE.match(ln))
    return hits, len(body)


def _looks_like_toc_page(page: PageContent) -> bool:
    """머리글 없이도 목차 쪽으로 볼 수 있는가.

    입력: page — PageContent
    출력: 목차 쪽으로 보이면 True
    """
    hits, total = _entry_like_lines(page)
    if hits < MIN_ENTRIES_WITHOUT_HEADING:
        return False
    return hits / total >= MIN_ENTRY_RATIO


def _head_limit() -> int:
    """목차를 찾을 앞쪽 범위.

    입력: 없음 (`DOCSTRUCT_TOC_HEAD_PAGES`)
    출력: 쪽 수. 0 이면 제한 없음
    """
    import os

    raw = os.environ.get(HEAD_PAGES_ENV, "").strip()
    if not raw:
        return DEFAULT_HEAD_PAGES
    try:
        value = int(raw)
    except ValueError:
        _log.warning("%s 값이 숫자가 아닙니다: %r", HEAD_PAGES_ENV, raw)
        return DEFAULT_HEAD_PAGES
    return max(value, 0)


def _toc_pages(pages: list[PageContent]) -> set[int]:
    """목차가 있을 쪽 번호.

    입력: pages — 페이지 목록
    출력: 쪽 번호 집합
    비고:
        머리글이 있는 쪽과 앞뒤 `TOC_SPAN` 쪽을 담는다. 문서 전체를 뒤지면
        본문의 참조(`자세한 내용은 57쪽`)까지 걸린다.

        **앞쪽 `DEFAULT_HEAD_PAGES` 쪽만 본다.** 실측에서 목차 머리글이
        7~15쪽(스캔본)·1쪽(행안부)에 있었고 25쪽 이후에는 없었다.
    """
    limit = _head_limit()
    found: set[int] = set()
    for page in pages:
        if not isinstance(page.page_no, int):
            continue
        # 목차는 앞쪽에 있다. 뒤까지 뒤지면 본문의 `차례` 언급이 걸리고
        # 581쪽 문서에서 헛돈다.
        if limit and page.page_no > limit:
            break
        head = "\n".join((page.content or "").splitlines()[:12])
        # 머리글이 있으면 앞뒤로 넓게 본다 — 목차가 여러 쪽에 이어진다.
        if _HEADING_RE.search(head):
            found.update(range(page.page_no - TOC_SPAN, page.page_no + TOC_SPAN + 1))
        # 머리글이 없어도 `제목 … 쪽번호` 가 여러 줄 이어지면 목차다.
        # 실물에서 머리글 없이 바로 항목이 시작되는 목차가 있었다.
        elif _looks_like_toc_page(page):
            found.add(page.page_no)
    return found


def find_toc(pages: list[PageContent]) -> list[dict]:
    """목차 항목을 찾는다.

    입력: pages — 페이지 목록
    출력: [{title, page, source_page}] 목록
    비고:
        `page` 는 **문서에 인쇄된 쪽번호**다. PDF 쪽 번호와 다를 수 있다 —
        실측에서 5쪽 차이가 났다. 어느 쪽에서 찾았는지는 `source_page` 에
        남긴다.
    """
    targets = _toc_pages(pages)
    if not targets:
        return []

    items: list[dict] = []
    last_page: int | None = None
    for page in pages:
        if not isinstance(page.page_no, int) or page.page_no not in targets:
            continue
        lines = [ln.strip() for ln in (page.content or "").splitlines()]
        index = 0
        while index < len(lines):
            line = lines[index]
            index += 1
            if not line or line.startswith("|") or line.startswith("<"):
                continue

            inline = _INLINE_RE.match(line)
            if inline:
                title = _clean(inline.group(1))
                number = int(inline.group(2))
                # 점선이 있으면 목차가 거의 확실하므로 순서 검사는 느슨하게
                if (len(title.replace(" ", "")) >= MIN_TITLE_CHARS
                        and 1 <= number <= MAX_PAGE_NO):
                    items.append({"title": title, "page": number,
                                  "source_page": page.page_no})
                    last_page = number
                continue

            # 스캔본은 제목과 쪽번호가 다른 줄로 나뉜다.
            match = (_PAGE_ONLY_RE.match(lines[index] or "")
                     if index < len(lines) else None)
            if match:
                title = _clean(line)
                number = int(match.group(1))
                # 줄이 나뉜 모양은 본문 금액과 구분되지 않는다. 번호 매김이
                # 있고 쪽번호답게 커지는 것만 받는다.
                if (len(title.replace(" ", "")) >= MIN_TITLE_CHARS
                        and _NUMBERING_RE.match(title)
                        and _looks_like_page(number, last_page)):
                    items.append({"title": title, "page": number,
                                  "source_page": page.page_no})
                    last_page = number
                    index += 1
    return items


def page_offset(items: list[dict]) -> int | None:
    """인쇄 쪽번호와 PDF 쪽번호의 차이.

    입력: items — find_toc 결과
    출력: 차이(PDF − 인쇄). 알 수 없으면 None
    비고:
        목차가 가리키는 쪽과 실제 PDF 쪽이 어긋난다 — 표지·간지 때문이다.
        실측(행안부)에서 5쪽 차이였다. 이 값을 알면 목차로 본문을 찾아갈
        수 있다.

        목차 자체가 실린 쪽에서는 잴 수 없으므로, 항목이 가리키는 쪽과
        목차가 있던 쪽의 차이 중 **가장 작은 양수**를 쓴다.

        **목차가 앞쪽에 있고 항목이 뒤를 가리키면 잴 수 없다** — 실측
        (주택과세금)에서 목차는 6쪽인데 첫 항목이 25쪽이라 None 이 나왔다.
        본문 쪽을 함께 봐야 알 수 있는데, 그것은 다운스트림 몫이다.
    """
    gaps = [
        item["source_page"] - item["page"]
        for item in items
        if item["page"] > 0 and item["source_page"] > item["page"]
    ]
    if not gaps:
        return None
    # 목차 앞쪽 항목일수록 차이가 실제 오프셋에 가깝다
    return min(gaps)


#: 오프셋을 믿을 최소 근거 쪽 수.
#: 실측: 스캔본 135쪽(전부 일치) · 과기부 6쪽(쪽번호가 본문에 안 남음).
MIN_OFFSET_SAMPLES = 20

#: 다수결이 이 비율을 넘어야 믿는다. 값이 흩어지면 잘못 잡은 것이다.
MIN_OFFSET_RATIO = 0.7

#: 쪽번호를 찾을 위치 — 지면 아래쪽 몇 줄까지 볼지.
#: 인쇄 쪽번호는 바닥글에 있다. 본문 숫자와 섞이지 않도록 좁게 본다.
FOOTER_LINES = 4

#: 머리글에서도 찾는다. 위쪽에 쪽번호를 두는 문서가 있다.
HEADER_LINES = 3

#: 브라우저 인쇄 표시(`31/380`). 쪽번호가 아니라 PDF 쪽 위치다.
_BROWSER_RE = re.compile(r"^(\d+)\s*/\s*\d+$")

#: 바닥글의 쪽번호. `- 152 -` 처럼 장식이 붙기도 한다.
_FOOTER_PAGE_RE = re.compile(r"^[\s\-–—·]*(\d{1,4})[\s\-–—·]*$")


def _printed_page(page: PageContent) -> int | None:
    """이 쪽에 인쇄된 쪽번호.

    입력: page — PageContent
    출력: 쪽번호. 못 찾으면 None
    비고:
        **바닥글과 머리글만 본다.** 본문 전체를 보면 금액·연도가 걸린다.

        브라우저 인쇄 표시(`31/380`)는 건너뛴다 — 그것은 PDF 쪽 위치이지
        문서에 인쇄된 번호가 아니다. 스캔본에 흔하다.
    """
    lines = [ln.strip() for ln in (page.content or "").splitlines() if ln.strip()]
    if not lines:
        return None
    edges = lines[:HEADER_LINES] + lines[-FOOTER_LINES:]
    for line in edges:
        if _BROWSER_RE.match(line):
            continue
        found = _FOOTER_PAGE_RE.match(line)
        if found:
            value = int(found.group(1))
            if 1 <= value <= MAX_PAGE_NO:
                return value
    return None


def printed_page_offset(pages: list[PageContent]) -> tuple[int | None, int]:
    """인쇄 쪽번호와 PDF 쪽번호의 차이를 잰다.

    입력: pages — 페이지 목록
    출력: (차이, 근거가 된 쪽 수). 못 재면 (None, 0)
    비고:
        표지·간지 때문에 둘이 어긋난다. **다수결로 정한다** — 목차 쪽에서는
        본문의 다른 숫자를 잡을 수 있으나, 대부분의 쪽은 바닥글에 제 번호를
        달고 있다.

        실측(주택과세금 377쪽): 133쪽에서 차이가 잡혔고 **전부 2** 였다.

        이 값을 알면 목차의 `25쪽` 이 PDF 몇 쪽인지 계산된다.
    """
    from collections import Counter

    gaps: list[int] = []
    for page in pages:
        if not isinstance(page.page_no, int):
            continue
        printed = _printed_page(page)
        # 인쇄 번호가 PDF 쪽보다 클 수는 없다 (앞에 표지가 붙으므로)
        if printed is not None and 0 < printed <= page.page_no:
            gaps.append(page.page_no - printed)
    if not gaps:
        return None, 0
    value, count = Counter(gaps).most_common(1)[0]
    # 근거가 적거나 흩어져 있으면 믿지 않는다. 쪽번호가 본문에 남지 않는
    # 문서가 있다 — 실측(과기부)에서 581쪽 중 6쪽만 잡혀 오프셋이
    # 흔들렸다.
    if count < MIN_OFFSET_SAMPLES or count < len(gaps) * MIN_OFFSET_RATIO:
        return None, count
    return value, count
