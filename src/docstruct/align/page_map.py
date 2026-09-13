"""쪽이 없는 산출에 PDF 의 쪽 번호를 물려준다.

입력:
    본문·표·목차 눈금

역할:
    HWPX 표 목록과 PDF 표 목록을 순서 정렬(DP)로 짝지어, HWPX 쪽이
    **PDF 쪽 번호를 얻게** 한다. 목차가 있으면 그것으로 큰 눈금을 먼저
    잡는다.
호출부:
    docstruct.api · rag(FastAPI) · notebooks/page_review.ipynb
출력:
    align(...) → [Pair] · pages(...) → {쪽: [Pair]} ·
    split_by_page(...) → 쪽으로 나뉜 문서

HWPX 에 쪽 정보가 없다는 것을 먼저 확인했다
-----------------------------------------
세 갈래를 다 뒤졌고 모두 없었다.

    hp:pageNum       쪽번호가 아니라 **"여기에 찍어라" 는 지시**다
                     (`formatType="DIGIT" sideChar="-"`). 실제 숫자는
                     한글이 화면에 그릴 때 계산한다
    hp:startNum page 섹션이 몇 쪽에서 시작하는지 담을 자리인데 **전부 0**
                     이다(조달청 23·행안부 160 섹션) — "앞에서 이어서"
    본문의 `- 71 -`  **0건.** 꼬리말 영역에 있어 본문 추출에 들어오지 않는다
                     (PDF 에서는 나온다)

쪽은 한글이 글꼴·용지·여백을 놓고 **그릴 때 생기는 것**이지 저장되는
것이 아니다. `lineseg vertpos` 되감김을 세는 방법도 재 봤으나 쪽을 넘는
표에서 되감기지 않아 어긋난다(조달청 68/76 · 행안부 326/429).

그래서 PDF 와 맞추는 수밖에 없다.

얼마나 맞는가 (행안부 433쪽 실측)
------------------------------
**목적에 따라 지표가 다르다.** 무엇을 쓰려는지 정하고 읽어야 한다.

    표가 몇 쪽에 있나        데이터 표 **186/232 (80%)** 배정
                            짝의 닮음 중앙 **96%** · 90%↑ 82%
    본문을 쪽으로 나누기      닮음 중앙 83% · 0.6↑ 73%
                            **실측 쪽만: 중앙 91% · 0.6↑ 88%**

표 쪽 배정이 더 정확하다 — 표는 덩어리라 "어느 쪽에 있었나" 가 분명한
반면, 본문은 쪽 경계가 모호하고 눈금이 없는 쪽은 보간이기 때문이다.

HWPX 표 580개 중 **348개는 레이아웃 표**다(`| 대한민국정부 |` ·
`| 2027년도 |` 같은 표지·간지 장식, 글자 40자 미만이 66%). 그것을 빼면
실질 커버리지가 40% 가 아니라 **80%** 다.

못 붙은 데이터 표 46개는 목차(`| 제1장 … | 1 |`)·간지 제목·머리글
성격이다 — PDF 에서는 표로 잡히지 않아 짝지을 상대가 없다. 쪽 번호를
알아도 쓸 데가 적으므로 병목으로 보지 않는다.

왜 순서 정렬인가
--------------
표마다 가장 닮은 정답을 **따로** 고르면 성과계획서에서 무너진다. 서식이
같은 표가 수십 개라(성과지표표·예산표) 토큰이 겹치고, 실측(행안부 580
정답 대 317 인식)에서 독립 최적 매칭은 Jaccard 0.11 짜리 엉뚱한 짝을
골랐다 — 1열짜리 표에 12열 표가 붙었다.

두 목록은 **같은 문서의 같은 차례**다. 그 제약을 쓰면 닮은 표가 많아도
자리로 갈린다. Needleman-Wunsch 로 전역 정렬하고, 짝지어지지 않은 것은
빈자리(gap)로 남긴다 — 인식이 놓친 표와 정답에만 있는 표(표지·간지)가
그 자리에 온다.

쪽 유도를 왜 HWPX 안에서 하지 않는가
---------------------------------
HWPX 에는 쪽 개념이 없다. `lineseg vertpos` 가 쪽 안 세로 위치라 되감김을
세면 쪽을 짐작할 수 있으나, **쪽을 넘는 표**에서는 되감기지 않아 과소
계수된다 — 실측: 조달청 68쪽(실제 76) · 행안부 326쪽(실제 429). 행안부
별첨3 처럼 한 표가 21쪽에 걸치는 문서에서 특히 어긋난다. 그래서 쪽은
PDF 에서 가져오고 정답을 거기에 붙인다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: 짝으로 인정할 최소 닮음. 이보다 낮으면 빈자리로 둔다.
MIN_SIMILARITY = 0.20
#: 빈자리 벌점. 닮음(0~1)과 같은 자에서 비교된다. 낮으면 건너뛰기가 쉬워져
#: 엉뚱한 짝 대신 빈자리를 고른다.
GAP_PENALTY = 0.10
#: 토큰으로 셀 것 — 두 글자 이상 한글, 세 자리 이상 숫자.
_TOKEN = re.compile(r"[0-9][0-9,\.]{2,}|[가-힣]{2,}")


@dataclass
class Pair:
    """정답 표 하나와 인식 표 하나의 짝 (한쪽은 None 일 수 있다)."""

    page_no: int | None = None
    detected_id: str | None = None
    gt_index: int | None = None
    similarity: float = 0.0
    detected: dict | None = field(default=None, repr=False)
    gt: list[dict] | None = field(default=None, repr=False)

    @property
    def kind(self) -> str:
        """짝의 종류 — 화면에 그대로 쓴다."""
        if self.detected_id is not None and self.gt_index is not None:
            return "짝"
        if self.detected_id is not None:
            return "정답 없음"
        return "인식 못함"


def _cells_of(table) -> list[dict]:
    """표에서 셀 목록을 꺼낸다 — dict 든 TableInfo 든.

    입력: table — dict 또는 TableInfo
    출력: 셀 dict 목록
    비고:
        같은 함수가 JSON 에서 읽은 dict 와 파이프라인의 객체를 모두 받아야
        한다 — 서비스는 파일을 받고 라이브러리는 객체를 넘긴다.
    """
    if isinstance(table, dict):
        return table.get("cells") or []
    return getattr(table, "cells", None) or []


def _table_id(table) -> str | None:
    """표 식별자 — dict 든 객체든."""
    if isinstance(table, dict):
        return table.get("id")
    return getattr(table, "id", None)


def _tokens(cells) -> set[str]:
    """셀 목록에서 비교용 토큰을 뽑는다."""
    if not cells:
        return set()
    text = " ".join((c.get("text") or "") for c in cells)
    return set(_TOKEN.findall(text))


def _similar(a: set[str], b: set[str]) -> float:
    """두 토큰 집합의 Jaccard 닮음."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def size_of(cells) -> tuple[int, int]:
    """셀 목록의 (행 수, 열 수)."""
    if not cells:
        return (0, 0)
    return (max(c.get("row", 0) + (c.get("rowspan", 1) or 1) for c in cells),
            max(c.get("col", 0) + (c.get("colspan", 1) or 1) for c in cells))


def merges_of(cells) -> set[tuple[int, int, int, int]]:
    """병합 자리 집합 — 채점의 단위."""
    if not cells:
        return set()
    out = set()
    for c in cells:
        rs, cs = c.get("rowspan", 1) or 1, c.get("colspan", 1) or 1
        if rs > 1 or cs > 1:
            out.add((c.get("row", 0), c.get("col", 0), rs, cs))
    return out


#: 눈금으로 쓸 최소 글자 수(공백 제외). 짧으면 여러 곳에 나온다.
MIN_ANCHOR_CHARS = 12
#: 한 쪽에서 눈금 후보로 시도할 줄 수.
ANCHOR_TRIES = 6
#: 비교에 쓸 글자 수. 길수록 유일해지지만 판독 차이에 약해진다.
ANCHOR_KEY_LEN = 35

#: 눈금으로 쓰면 안 되는 줄 — placeholder·표 조각·쪽번호.
_SKIP_LINE = re.compile(r"^\s*(?:<!--|<--|\||-{3,}|\d+\s*$)")


def _flatten(text: str) -> str:
    """비교용으로 납작하게 — 마크다운 표기와 공백을 뺀다.

    입력: text — markdown 본문
    출력: 공백·`#`·`*`·`>`·`|` 를 뺀 문자열
    비고:
        **접두사를 안 빼면 거의 안 붙는다.** PDF 는 docling 이 제목으로
        분류해 `# ` 를 붙이고 HWPX 는 `- ` 를 붙이거나 원문 그대로 둔다 —
        실측(행안부): 그대로 비교하면 6%, 빼고 비교하면 **37%** 가 붙었다.
    """
    # **줄머리의 목록·제목 기호만 뗀다.** 본문 안의 하이픈(`4·5급`,
    # `'26 → '27`)은 내용이므로 남긴다 — 통째로 빼면 다른 글이 같아진다.
    stripped = re.sub(r"(?m)^[ \t]*[#>*\-–•·]+[ \t]*", "", text or "")
    return re.sub(r"\s+", "", re.sub(r"[#*>|]", "", stripped))


def _anchor_keys(page, tail: bool = False) -> list[str]:
    """이 쪽에서 눈금으로 쓸 후보들.

    입력: page — PageContent 또는 dict, tail — 뒤에서부터 고를지
    출력: 납작하게 편 후보 문자열 목록
    비고:
        **첫 줄만 보면 놓친다** — 실측(행안부): 첫 줄로 실패한 115쪽 중
        28쪽이 둘째·셋째 줄로는 찾아졌다. placeholder(`<!-- image N -->`)
        와 표 조각은 후보에서 뺀다 — 그것을 골라 실패한 쪽이 있었다.
    """
    content = (page.get("content") if isinstance(page, dict)
               else getattr(page, "content", "")) or ""
    out: list[str] = []
    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped or _SKIP_LINE.match(stripped):
            continue
        flat = _flatten(stripped)
        if len(flat) < MIN_ANCHOR_CHARS or flat.isdigit():
            continue
        out.append(flat[:ANCHOR_KEY_LEN])
    if tail:
        return out[::-1][:ANCHOR_TRIES]
    return out[:ANCHOR_TRIES]


def text_anchors(pdf_pages, hwpx_text: str) -> list[tuple[int, int]]:
    """PDF 쪽의 첫 문단을 HWPX 본문에서 찾아 눈금을 만든다.

    입력: pdf_pages — PDF PageContent 목록, hwpx_text — HWPX 본문 markdown
    출력: [(HWPX 본문 안 위치, PDF 쪽 번호)] — 위치 오름차순
    비고:
        **이것이 쪽 맞춤의 주력이다.** 목차(`toc_anchors`)는 눈금이 22개
        뿐이라 429쪽을 나누기에 성기고, 표 정렬은 서식이 같은 표가 많은
        문서에서 흔들린다(행안부 317표 중 230표).

        본문 글은 순서가 바뀌지 않는다 — 실측(행안부): 찾은 160개가
        **예외 없이 차례대로 증가**했다(159/159). 쪽을 넘는 표도 텍스트
        경계는 표 바깥에 있어 영향이 적다.

        **앞 눈금 이후에서만 찾는다.** `ㅇ 현원(정원)` 처럼 여러 곳에
        나오는 문구도 구간을 좁히면 유일해진다 — 그대로 찾으면 71쪽이
        "여러 곳" 으로 버려졌다.
    """
    flat = _flatten(hwpx_text)
    anchors: list[tuple[int, int]] = []
    cursor = 0
    for page in pdf_pages:
        page_no = (page.get("page_no") if isinstance(page, dict)
                   else getattr(page, "page_no", None))
        if not isinstance(page_no, int):
            continue

        # **머리와 꼬리를 모두 쓴다.** 머리는 그 쪽이 시작하는 자리,
        # 꼬리는 끝나는 자리를 가리킨다 — 실측(행안부): 머리만 쓰면
        # 257쪽이 잡히는데 꼬리를 더하면 놓친 쪽을 메울 수 있다.
        # 꼬리로 잡은 것은 **다음 쪽의 시작**으로 삼는다.
        head = _find_unique(flat, _anchor_keys(page), cursor)
        if head is not None:
            anchors.append((head, page_no))
            cursor = head + 1
            continue

        tail = _find_unique(flat, _anchor_keys(page, tail=True), cursor)
        if tail is not None:
            # **꼬리는 그 쪽이 *끝나는* 자리다.** 그것을 시작으로 삼으면
            # 잘린 덩어리가 앞쪽 내용을 담는다 — 실측(행안부): 덩어리의
            # 78%가 1~3쪽 앞의 PDF 쪽과 더 많이 겹쳤다.
            #
            # 그래서 **다음 쪽의 시작**으로 기록한다. 이 쪽 자체의
            # 시작은 앞 눈금이 정한다.
            anchors.append((tail, page_no + 1))
            cursor = tail + 1
    return anchors


def _find_unique(flat: str, keys: list[str], cursor: int) -> int | None:
    """앞 눈금 이후에서 **유일하게** 나오는 첫 후보의 위치.

    입력: flat — 납작하게 편 본문, keys — 후보 목록, cursor — 탐색 시작
    출력: 위치. 없으면 None
    비고:
        구간을 좁혀도 세 번 넘게 나오면 믿지 않는다 — `ㅇ 현원(정원)`
        처럼 문서 전체에 흩어진 문구다.
    """
    for key in keys:
        found = flat.find(key, cursor)
        if found < 0:
            continue
        if flat.find(key, found + 1) >= 0 and flat.count(key) > 2:
            continue
        return found
    return None


def toc_anchors(toc: list[dict], hwpx_text: str) -> list[tuple[int, int]]:
    """목차로 큰 눈금을 잡는다.

    입력: toc — find_toc 결과 [{title, page}], hwpx_text — HWPX 본문
    출력: [(본문 안 위치, 쪽 번호)] — 위치 오름차순
    비고:
        **목차는 문서 자신이 밝힌 쪽 정보다.** 표 유사도보다 확실하다 —
        조달청 목차에 `제1장 성과계획 목표체계 … 1` 처럼 22개 항목이
        쪽번호와 함께 들어 있다.

        이것으로 구간을 좁히면 서식이 같은 표가 많은 문서에서 오정렬이
        줄어든다(행안부는 317표 중 230표만 붙었다).

        제목이 본문에 **한 번만** 나오는 것만 쓴다 — 여러 번 나오면
        어느 것이 그 장인지 알 수 없다. 목차 자신에도 나오므로 앞쪽
        일부는 건너뛴다.
    """
    anchors: list[tuple[int, int]] = []
    body = hwpx_text or ""
    for entry in toc or []:
        title = (entry.get("title") or "").strip()
        page = entry.get("page")
        if not title or not isinstance(page, int) or len(title) < 4:
            continue
        found = [m.start() for m in re.finditer(re.escape(title), body)]
        # 첫 것은 목차 자신일 가능성이 크다 — 둘일 때는 뒤엣것을 쓴다.
        if len(found) == 2:
            anchors.append((found[1], page))
        elif len(found) == 1:
            anchors.append((found[0], page))
    anchors.sort()
    # 쪽 번호가 뒤로만 가야 한다 — 거꾸로 가는 것은 잘못 잡은 것이다.
    kept: list[tuple[int, int]] = []
    for position, page in anchors:
        if not kept or page >= kept[-1][1]:
            kept.append((position, page))
    return kept


def align(detected: list[tuple[int, dict]], gt: list[list[dict]]) -> list[Pair]:
    """두 표 목록을 차례대로 맞춘다.

    입력: detected — [(쪽 번호, 표 dict)] 문서 차례,
          gt — [정답 셀 목록] 문서 차례
    출력: Pair 목록 (문서 차례). 짝짓지 못한 쪽은 한쪽이 None
    비고:
        Needleman-Wunsch. 점수는 닮음 − 빈자리 벌점이고, MIN_SIMILARITY
        미만은 짝으로 세지 않는다 — 억지로 붙이느니 비워 두는 편이 낫다.
    """
    det_tokens = [_tokens(_cells_of(t)) for _p, t in detected]
    gt_tokens = [_tokens(g) for g in gt]
    n, m = len(detected), len(gt)

    # 닮음을 미리 다 재 둔다 — 역추적에서 다시 재면 부동소수 오차로 앞의
    # 판단과 어긋난다.
    sim = [[_similar(a, b) for b in gt_tokens] for a in det_tokens]

    #: 앞 표에서 이어지는가 — 파이프라인의 `continues_from` 을 그대로 쓴다.
    #
    # **한 정답 표가 인식에서는 여럿으로 쪼개진다.** 쪽을 넘는 표를
    # TableFormer 는 쪽마다 하나씩 내기 때문이다 (행안부 32표가 그렇다).
    # 1:1 정렬로는 이것을 표현하지 못해, 앞 조각이 정답을 가져가면 뒤
    # 조각이 통째로 빈자리가 된다 — 실측: table_29 가 gt[74] 를 가져가고
    # table_30(⑬ 발화 표)이 짝 없음으로 빠졌다.
    continues = [bool(t.get("continues_from") if isinstance(t, dict)
                      else getattr(t, "continues_from", None))
                 for _p, t in detected]

    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    #: 역추적 포인터. 0=대각(짝) · 1=위(인식만) · 2=왼쪽(정답만) · 3=이어짐
    #
    # **점수 비교로 역추적하지 않는다.** `score[i][j] == hit` 처럼 실수를
    # 같음으로 견주면 누적 오차 때문에 성립해야 할 자리가 어긋나고, 짝이
    # 조용히 빈자리로 바뀐다 — 실측(행안부): 닮음 0.42 로 붙어야 할
    # table_30 이 0.00 짝없음으로 나왔다. 앞으로 갈 때 정한 길을 그대로
    # 적어 두고 그것만 따라간다.
    back = [[2] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0] = score[i - 1][0] - GAP_PENALTY
        back[i][0] = 1
    for j in range(1, m + 1):
        score[0][j] = score[0][j - 1] - GAP_PENALTY
    for i in range(1, n + 1):
        row, prev = score[i], score[i - 1]
        for j in range(1, m + 1):
            s = sim[i - 1][j - 1]
            hit = prev[j - 1] + (s if s >= MIN_SIMILARITY else -GAP_PENALTY)
            up = prev[j] - GAP_PENALTY
            left = row[j - 1] - GAP_PENALTY
            best, move = hit, 0
            if up > best:
                best, move = up, 1
            if left > best:
                best, move = left, 2
            # 이어지는 조각은 **정답을 소비하지 않고** 같은 정답에 붙는다.
            if continues[i - 1] and s >= MIN_SIMILARITY:
                carry = prev[j] + s
                if carry > best:
                    best, move = carry, 3
            row[j], back[i][j] = best, move

    pairs: list[Pair] = []
    i, j = n, m
    while i > 0 or j > 0:
        move = back[i][j] if i > 0 and j > 0 else (1 if j == 0 else 2)
        if move == 0:
            page, table = detected[i - 1]
            s = sim[i - 1][j - 1]
            if s >= MIN_SIMILARITY:
                pairs.append(Pair(page, _table_id(table), j - 1, s,
                                  table, gt[j - 1]))
            else:                                # 자리는 맞아도 내용이 다르다
                pairs.append(Pair(None, None, j - 1, s, None, gt[j - 1]))
                pairs.append(Pair(page, _table_id(table), None, s, table, None))
            i, j = i - 1, j - 1
        elif move == 3:                          # 이어지는 조각
            page, table = detected[i - 1]
            pairs.append(Pair(page, _table_id(table), j - 1, sim[i - 1][j - 1],
                              table, gt[j - 1]))
            i -= 1
        elif move == 1:
            page, table = detected[i - 1]
            pairs.append(Pair(page, _table_id(table), None, 0.0, table, None))
            i -= 1
        else:
            pairs.append(Pair(None, None, j - 1, 0.0, None, gt[j - 1]))
            j -= 1
    pairs.reverse()
    return pairs


def table_anchors(hwpx_text: str, hwpx_tables, pdf_tables) -> list[tuple[int, int]]:
    """표를 눈금으로 쓴다 — 본문 글이 없는 쪽을 메운다.

    입력: hwpx_text — HWPX 본문, hwpx_tables — [(쪽, 표)],
          pdf_tables — [(쪽, 표)]
    출력: [(HWPX 본문 안 위치, PDF 쪽 번호)]
    비고:
        **본문 눈금이 못 잡는 쪽이 표만 있는 쪽이다** — 실측(행안부):
        놓친 163쪽 중 112쪽이 그렇다. 별첨 계열이 30쪽 넘게 이어진다.

        표 정렬(`align`)로 짝을 지은 뒤, 그 표의 markdown 첫 줄을 본문
        에서 찾아 자리를 얻는다. 표 자체가 본문에 펼쳐져 있으므로
        찾을 수 있다.

        표 유사도는 서식이 같은 표가 많으면 흔들리지만(행안부 580표 중
        230표), **본문 눈금과 함께 쓰면 서로를 보완한다** — 본문이 있는
        쪽은 본문이, 표만 있는 쪽은 표가 맡는다.
    """
    if not hwpx_tables or not pdf_tables:
        return []
    pairs = align(pdf_tables, [_cells_of(t) for _p, t in hwpx_tables])
    flat = _flatten(hwpx_text)
    out: list[tuple[int, int]] = []
    for pair in pairs:
        if pair.gt_index is None or pair.page_no is None:
            continue
        table = hwpx_tables[pair.gt_index][1]
        markdown = (table.get("markdown") if isinstance(table, dict)
                    else getattr(table, "markdown", "")) or ""
        first = next((line for line in markdown.splitlines()
                      if line.strip() and "---" not in line), "")
        key = _flatten(first)[:ANCHOR_KEY_LEN]
        if len(key) < MIN_ANCHOR_CHARS:
            continue
        found = flat.find(key)
        if found >= 0 and flat.find(key, found + 1) < 0:   # 유일할 때만
            out.append((found, pair.page_no))
    out.sort()
    return out


def merge_anchors(*groups: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """여러 눈금을 합쳐 **차례가 맞는 것만** 남긴다.

    입력: groups — 눈금 목록들 (앞에 오는 것이 우선)
    출력: 위치·쪽이 함께 증가하는 눈금 목록
    비고:
        서로 다른 근거로 얻은 눈금은 어긋날 수 있다. 위치 순으로 훑으며
        **쪽 번호가 뒤로만 가는 것**만 받는다 — 거꾸로 가는 것은 둘 중
        하나가 틀린 것이므로 버린다.

        앞 그룹을 우선한다(본문 눈금이 표 눈금보다 든든하다). 같은 쪽에
        둘이 있으면 앞 그룹 것을 쓴다.
    """
    seen: dict[int, int] = {}                    # 쪽 → 위치
    for group in groups:
        for position, page in group:
            seen.setdefault(page, position)
    merged = sorted((position, page) for page, position in seen.items())
    kept: list[tuple[int, int]] = []
    for position, page in merged:
        if kept and page <= kept[-1][1]:
            continue                             # 쪽이 되돌아간다 — 버린다
        kept.append((position, page))
    return kept


def interpolate(anchors: list[tuple[int, int]], last_page: int,
                total_chars: int) -> list[tuple[int, int]]:
    """눈금 사이의 빈 쪽을 **비례로 채운다.**

    입력: anchors — text_anchors 결과, last_page — 마지막 쪽 번호,
          total_chars — 납작하게 편 본문 길이
    출력: 채워진 눈금 목록 (위치 오름차순)
    비고:
        **놓친 쪽의 대부분은 텍스트로 잡을 수 없다.** 실측(행안부):
        163쪽을 놓쳤는데 그중 **112쪽이 후보 자체가 없다** — 표만 있는
        쪽이다. 그런 쪽은 아무리 규칙을 고쳐도 본문 글로는 못 잡는다.

        대신 앞뒤 눈금 사이를 **쪽 수로 나눠** 자리를 매긴다. 성과계획서
        처럼 쪽마다 분량이 비슷한 문서에서는 이 근사가 쓸 만하다 —
        표만 있는 쪽이 연속으로 30쪽 넘게 이어지는 구간(별첨3 계열)도
        고르게 나뉜다.

        **정확한 자리는 아니다.** 그래서 채운 눈금은 `estimated` 로
        구분할 수 있게 위치만 준다 — 쓰는 쪽이 정확도를 오해하지 않도록
        `split_text_by_page` 가 결과에 표시한다.
    """
    if not anchors:
        return []
    filled: list[tuple[int, int]] = []
    for index, (position, page) in enumerate(anchors):
        filled.append((position, page))
        next_position, next_page = (anchors[index + 1]
                                    if index + 1 < len(anchors)
                                    else (total_chars, last_page + 1))
        gap_pages = next_page - page
        if gap_pages <= 1 or next_position <= position:
            continue
        step = (next_position - position) / gap_pages
        for offset in range(1, gap_pages):
            filled.append((int(position + step * offset), page + offset))
    filled.sort()
    return filled


#: 줄 경계로 옮길 때 허용할 최대 거리(글자). **넓으면 눈금이 뭉개진다** —
#: 400 으로 두었더니 p6 이 48자 앞으로 밀렸고, 그런 밀림이 쌓여 잘린
#: 덩어리가 1~3쪽 앞 내용을 담았다(실측: 78%가 마이너스 방향).
#: 표 한복판을 피할 만큼만 좁게 잡는다.
SNAP_WINDOW = 80


def _raw_positions(text: str, wanted: set[int]) -> dict[int, int]:
    """납작한 위치 → 원문 위치.

    입력: text — 원문, wanted — 알고 싶은 납작 위치들
    출력: {납작 위치: 원문 위치}
    비고:
        `_flatten` 을 줄 단위로 다시 밟아 대응을 만든다. 그 함수가
        **줄머리 기호를 통째로 지우므로** 문자 하나씩 세는 방식으로는
        맞출 수 없다.
    """
    import re as _re

    lead = _re.compile(r"^[ \t]*[#>*\-–•·]+[ \t]*")
    out: dict[int, int] = {}
    flat_index = 0
    offset = 0
    for line in (text or "").splitlines(keepends=True):
        body = line.rstrip("\n")
        match = lead.match(body)
        start = match.end() if match else 0
        for local, char in enumerate(body[start:], start=start):
            if char.isspace() or char in "#*>|":
                continue
            if flat_index in wanted:
                out.setdefault(flat_index, offset + local)
            flat_index += 1
        offset += len(line)
    return out


def _snap_to_line(text: str, position: int, window: int = SNAP_WINDOW) -> int:
    """가장 가까운 줄 시작으로 옮긴다.

    입력: text — 원문, position — 자를 자리, window — 찾을 범위(글자)
    출력: 옮긴 자리
    비고:
        표 한복판에서 자르면 `<table 15>` 가 `able 15>` 가 된다. 빈 줄
        (문단 경계)을 먼저 찾고, 없으면 아무 줄바꿈이나 쓴다.
    """
    if position <= 0 or position >= len(text):
        return max(0, min(position, len(text)))
    low = max(0, position - window)
    high = min(len(text), position + window)

    # **앞뒤 중 가까운 쪽으로 옮긴다.** 뒤만 보거나 앞만 보면 엉뚱하게
    # 멀리 간다 — `rfind` 로 뒤쪽 빈 줄을 잡아 다음 문단까지 건너뛴 적이
    # 있다.
    candidates = []
    before = text.rfind("\n\n", low, position)
    after = text.find("\n\n", position, high)
    if before >= 0:
        candidates.append(before + 2)
    if after >= 0:
        candidates.append(after + 2)
    if not candidates:                           # 빈 줄이 없으면 아무 줄바꿈
        before = text.rfind("\n", low, position)
        after = text.find("\n", position, high)
        if before >= 0:
            candidates.append(before + 1)
        if after >= 0:
            candidates.append(after + 1)
    if not candidates:
        return position
    return min(candidates, key=lambda value: abs(value - position))


def split_text_by_page(hwpx_text: str, anchors: list[tuple[int, int]],
                       measured: set[int] | None = None) -> list[dict]:
    """눈금으로 HWPX 본문을 쪽 단위로 자른다.

    입력: hwpx_text — HWPX 본문 markdown, anchors — 눈금 목록,
          measured — 실제로 찾은 쪽 번호 집합 (나머지는 추정으로 표시)
    출력: [{page_no, content, estimated}] — 쪽 번호 오름차순
    비고:
        눈금이 없는 쪽은 앞 눈금의 쪽에 딸린다. 실측(행안부): 429쪽 중
        눈금이 잡힌 것이 231개이므로, 나머지는 앞 쪽에 붙어 **덩어리가
        조금 커진다.** 쪽이 아예 없는 것보다 낫고, 잘못된 자리에 쪼개는
        것보다도 낫다.

        **납작하게 편 좌표를 원문 좌표로 되돌린다** — 눈금은 공백·마크다운
        표기를 뺀 문자열에서 잰 위치이므로 그대로 자르면 어긋난다.
    """
    if not anchors:
        return [{"page_no": None, "content": hwpx_text}]

    # 납작한 위치 → 원문 위치.
    #
    # **`_flatten` 과 똑같은 규칙으로 세야 한다.** 그것은 줄머리의
    # 목록·제목 기호(`- `, `# `)를 통째로 지우는데, 여기서 문자 하나씩
    # 세면 그 기호를 세게 되어 **글머리 하나마다 좌표가 밀린다.**
    # 행안부는 글머리가 수천 개라 잘린 덩어리가 눈금과 전혀 다른 곳이
    # 됐다 — 눈금은 198/200 정확한데 결과가 어긋난 원인이었다.
    mapping = _raw_positions(hwpx_text, {p for p, _page in anchors})
    cuts = sorted((mapping.get(position, 0), page)
                  for position, page in anchors)

    # **줄 경계로 물린다.** 보간은 글자 수로 자리를 잡으므로 표 중간이나
    # 낱말 가운데가 될 수 있다 — 실측: `able 15>` 처럼 `<table 15>` 가
    # 반토막 난 쪽이 나왔다. 가장 가까운 줄바꿈으로 옮긴다.
    # **첫 눈금은 물리지 않는다.** 앞에 아무것도 없는데 물리면 그
    # 조각이 머리말로 떨어져 나가 첫 쪽이 사라진다.
    cuts = [(position if index == 0 else _snap_to_line(hwpx_text, position), page)
            for index, (position, page) in enumerate(cuts)]
    cuts.sort()

    out: list[dict] = []
    for index, (start, page_no) in enumerate(cuts):
        end = cuts[index + 1][0] if index + 1 < len(cuts) else len(hwpx_text)
        body = (hwpx_text[start:end] or "").strip()
        if body:
            out.append({
                "page_no": page_no,
                "content": body,
                # **추정을 사실처럼 보이게 하지 않는다.** 보간으로 채운
                # 쪽은 자리가 정확하지 않다 — 쓰는 쪽이 알아야 한다.
                "estimated": bool(measured is not None
                                  and page_no not in measured),
            })
    # 첫 눈금 앞의 머리말도 잃지 않는다. **첫 눈금이 0 이면 머리말이
    # 없다** — 그런데 물림이 줄머리 기호 앞으로 옮기면 0 보다 작아질 수
    # 없으므로 빈 조각이 생기지 않아야 한다.
    head = (hwpx_text[:cuts[0][0]] or "").strip()
    if head:
        out.insert(0, {"page_no": None, "content": head, "estimated": False})
    return out


def split_by_page(hwpx_pages, pdf_pages, toc=None) -> dict:
    """HWPX 판독 결과를 PDF 쪽에 맞춰 나눈다.

    입력:
        hwpx_pages  HWPX PageContent 목록 (보통 한 쪽짜리)
        pdf_pages   같은 문서의 PDF PageContent 목록
        toc         PDF 쪽에서 찾은 목차 (있으면 눈금으로 쓴다)
    출력:
        {"pages": [{page_no, tables, images, content}],
         "matched": n, "total": n, "anchors": n}
    비고:
        **표를 앵커로 쓴다.** 본문 글은 쪽 경계가 모호하지만 표는 덩어리
        라 어느 쪽에 있었는지가 분명하다. 표에 딸린 본문은 그 표의 쪽에
        붙인다 — 완벽하지 않지만 쪽 없는 것보다 낫다.
    """
    hwpx_tables = [t for page in hwpx_pages for t in (page.tables or [])]
    detected = [(p.page_no, t) for p in pdf_pages
                for t in (p.tables or []) if isinstance(p.page_no, int)]
    gt_cells = [(t.cells or []) for t in hwpx_tables]

    pairs = align(detected, gt_cells)
    by_page: dict[int, dict] = {}
    matched = 0
    for pair in pairs:
        if pair.gt_index is None or pair.page_no is None:
            continue
        matched += 1
        slot = by_page.setdefault(pair.page_no,
                                  {"page_no": pair.page_no, "tables": [],
                                   "images": [], "content": ""})
        slot["tables"].append(hwpx_tables[pair.gt_index])

    # 그림은 본문 차례상 가장 가까운 표의 쪽에 붙인다.
    known = sorted(by_page)
    for page in hwpx_pages:
        for image in page.images or []:
            if known:
                by_page[known[0]]["images"].append(image)

    return {"pages": [by_page[k] for k in sorted(by_page)],
            "matched": matched, "total": len(hwpx_tables),
            "anchors": len(toc or [])}


def pages(pairs: list[Pair]) -> dict[int, list[Pair]]:
    """쪽별로 모은다 — 정답에만 있는 표는 앞 짝의 쪽에 딸린다.

    입력: pairs — align 의 결과
    출력: {쪽 번호: Pair 목록}
    비고:
        정답에만 있는 표(인식이 통째로 놓친 표)는 쪽을 모른다. 문서
        차례상 **바로 앞 짝의 쪽**에 붙여 둔다 — 그 쪽 화면에서 "여기
        놓친 표가 있다"를 보게 하려는 것이다.
    """
    out: dict[int, list[Pair]] = {}
    last = None
    for pair in pairs:
        page = pair.page_no if pair.page_no is not None else last
        if page is None:
            continue
        out.setdefault(page, []).append(pair)
        if pair.page_no is not None:
            last = pair.page_no
    return out


def score_merges(pair: Pair) -> tuple[int, int, int]:
    """이 짝의 병합 채점 — (맞힘, 정답 수, 낸 수).

    입력: pair — 짝
    출력: (맞힘, 정답 병합 수, 인식 병합 수)
    비고:
        자리 그대로 비교한다. 행 오프셋 보정(정답에 캡션 행이 더 있는
        경우)은 `kit/truth_align.py` 의 몫이고 여기서는 하지 않는다 —
        노트북은 **눈으로 보는 화면**이지 최종 채점이 아니다.
    """
    if pair.detected is None or pair.gt is None:
        return (0, len(merges_of(pair.gt)) if pair.gt else 0,
                len(merges_of(_cells_of(pair.detected) if pair.detected else None)))
    got = merges_of(_cells_of(pair.detected))
    want = merges_of(pair.gt)
    return len(got & want), len(want), len(got)
