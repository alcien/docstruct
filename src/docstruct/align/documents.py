"""두 판독 결과(document.json)를 맞춰 쪽으로 나눈다 — 순수 dict→dict.

입력:
    (hwpx dict, pdf dict)

역할:
    쪽이 없는 HWPX·HWP 산출에 같은 문서 PDF 산출의 쪽 번호를 물려준다.
    `page_map` 의 눈금·정렬 도구를 **서비스 자료형(JSON)** 에 맞춰 엮은
    한 겹이다.
호출부:
    docstruct.cli (`--align`) · rag.adapters.page_align (FastAPI) ·
    notebooks/page_review.ipynb
출력:
    align_documents(hwpx, pdf) → dict · to_markdown(result) → str

왜 라이브러리에 두는가 (0.4.57)
-----------------------------
이 로직은 원래 overlay 의 `rag/adapters/page_align.py` 에만 있었다.
그래서 **같은 기능이 서버에서만 되고 로컬에서는 안 됐다** — 로컬
`split_by_page()` 는 표 정렬만 쓰는 옛 판이라 본문 눈금·보간이 없었고,
같은 문서를 CLI 로 돌리면 서버와 다른 답이 나왔다.

세 트리를 기계적으로 동기화하는 규율(`sync_trees.py`)의 취지는 **코드가
한 벌**이라는 것인데, overlay 전용 폴더에 로직이 들어가면 그 규율 밖으로
샌다. 그래서 판정 로직은 여기(pkg)에 두고, overlay 어댑터는 이것을
부르기만 한다.

무엇을 근거로 쪽을 정하는가
-------------------------
    본문 눈금  PDF 각 쪽의 첫·끝 문구를 HWPX 본문에서 찾는다. 주력이다 —
               실측(행안부): 눈금 231개가 예외 없이 차례대로 증가(230/230)
    표 눈금    본문이 없는 쪽(표만 있는 쪽)을 표 정렬로 메운다.
               실측: 본문만 78%/67% → 표를 더하면 **83%/73%**
    보간       눈금 사이는 글자 수 비례로 채운다. 실측(행안부): 163쪽 중
               112쪽이 표만 있어 본문 눈금으로는 잡히지 않는다

보간으로 채운 쪽은 `estimated: true` 로 표시한다 — **실측과 추정을 섞어
내면 수치가 거짓이 된다.** 표 배정 역시 못 맞춘 수를 함께 낸다.
"""
from __future__ import annotations

import os
import re
from typing import Any


def tables_of(document: dict) -> list[tuple[int, dict]]:
    """문서에서 (쪽, 표) 목록을 문서 차례대로 뽑는다.

    입력: document — document.json 을 읽은 dict
    출력: [(쪽 번호, 표 dict)] — 쪽이 정수가 아니면 0
    """
    out: list[tuple[int, dict]] = []
    for page in document.get("pages") or []:
        page_no = page.get("page_no")
        for table in page.get("tables") or []:
            out.append((page_no if isinstance(page_no, int) else 0, table))
    return out


#: 매처가 쓸 토큰이 이보다 적으면 **짝짓기 자체가 성립하지 않는다.**
#: `page_map` 이 Jaccard 닮음으로 짝을 짓는데, 토큰이 한둘이면 닮음이
#: 문턱(MIN_SIMILARITY=0.20)을 넘든 못 넘든 우연이다.
#: 실측(문체부): 짝을 지은 338표의 토큰 중앙값은 31 이고 3개 미만은
#: **하나도 없다.** 못 맞춘 505표 중 148개(29%)가 3개 미만이다.
MIN_MATCH_TOKENS = 3


#: 포함률을 잴 때 뺄 머리 행 수. **이 문서군 관찰이지 검증된 값이 아니다**
#: — 손잡이로 두어 다시 잴 수 있게 한다.
HEAD_ROWS = 2

#: 이 비율 이상이 원본 표 안에 들어가면 "그 표의 조각" 으로 본다.
MIN_CONTAINMENT = 0.7


def _body_tokens(cells: list[dict] | None, head_rows: int = 0) -> set[str]:
    """머리 행을 뺀 토큰 집합.

    입력: cells — 셀 목록, head_rows — 뺄 머리 행 수
    출력: 토큰 집합
    비고:
        **앞부분만 보면 안 된다.** 성과계획서의 표는 머리 어휘가 서로
        비슷해(`구분`·`예산`·`비고`·`'23`…) 머리만으로도 포함률이 높게
        나온다. 실측(문체부 `table_838`): 머리를 포함하면 조각이 115개,
        머리 2행을 빼면 101개였다 — 14개가 어휘로 붙은 가짜였다.

        본문 기준을 판단에 쓰고 전체 기준은 대조용으로 함께 낸다. 둘
        차이가 크면 그 후보는 머리로 붙은 것이라 **지표가 스스로를
        검증한다.**
    """
    from docstruct.align.page_map import _tokens

    if not cells:
        return set()
    return _tokens([c for c in cells
                    if c.get("row", 0) >= head_rows])


def fragment_note(table: dict, pdf_tables: list[tuple[int, dict]],
                  head_rows: int = HEAD_ROWS) -> dict | None:
    """이 표가 PDF 에서 **여러 조각으로 쪼개졌는지** 잰다.

    입력: table — 짝을 못 지은 HWPX 표, pdf_tables — [(쪽, PDF 표)],
          head_rows — 포함률에서 뺄 머리 행 수
    출력: {fragments, pages, covered, covered_all, tokens} · 없으면 None
    비고:
        **Jaccard 는 크기 차이를 벌준다.** 짝짓기가 쓰는
        `|A∩B| / |A∪B|` 는 큰 표와 그 조각을 견줄 때 분모가 큰 표에
        끌려가, 조각이 완전한 부분집합이어도 값이 바닥에 깔린다.

        실측(문체부 `table_838` · 678행 · 토큰 3,243):

            포함률 70%↑ 인 PDF 표      101개 (머리 2행 제외)
            그 조각들이 덮는 비율       86%
            개별 조각의 Jaccard 최고    **0.041** (문턱 0.20)

        조각들은 제자리에 있었고 원본의 86% 를 덮고 있었는데, 지표가
        전부 버린 것이다. **못 찾은 것이 아니라 찾고도 버렸다.**

        여기서는 고치지 않고 잰다 — 짝짓기를 바꾸는 것은 결과를 바꾸는
        일이고, 얼마나 많은 표가 이 유형인지 먼저 알아야 한다.
    """
    body = _body_tokens(table.get("cells"), head_rows)
    if len(body) < 20:
        return None                              # 작은 표는 이 문제가 아니다

    whole = _body_tokens(table.get("cells"))
    hits: list[tuple[int, set[str]]] = []
    hits_all = 0
    for page_no, other in pdf_tables:
        part = _body_tokens(other.get("cells"), head_rows)
        if part and len(part & body) / len(part) >= MIN_CONTAINMENT:
            hits.append((page_no, part))
        part_all = _body_tokens(other.get("cells"))
        if part_all and len(part_all & whole) / len(part_all) >= MIN_CONTAINMENT:
            hits_all += 1
    if not hits:
        return None

    covered = set().union(*[p for _n, p in hits])
    pages = sorted(n for n, _p in hits)
    return {
        "tokens": len(body),
        # 본문 기준(판단용)
        "fragments": len(hits),
        "covered": round(len(covered & body) / len(body), 3),
        "pages": [pages[0], pages[-1]],
        # 전체 기준(대조용) — 본문 기준과 크게 다르면 머리로 붙은 후보다
        "fragments_all": hits_all,
        "head_rows": head_rows,
    }


#: 한 칸에 이만큼 이상 들어 있으면 표가 아니라 서술이다.
NARRATIVE_CHARS = 100

#: 도해를 표로 그릴 때 쓰는 화살표.
ARROW_CHARS = "⇒⇨→⟹➡"

#: 채움이 이보다 낮으면 얼개만 잡은 표로 본다.
SPARSE_FILL = 0.5


#: 본문 대조로 쪽을 줄 때 필요한 최소 포함률.
#: 실측(세 부처 못 맞춘 216표): 85%면 108개(50%), **70%면 147개(68%)**,
#: 60%면 160개(74%)에 쪽이 붙는다. 70%를 기본으로 둔다.
TEXT_PAGE_MIN = 0.70

#: 1위 쪽과 2위 쪽의 차이가 이보다 작으면 쪽을 주지 않는다.
#: **유일성이 없으면 주지 않는다** — 성과계획서는 같은 서식 표가 쪽마다
#: 되풀이되므로, 이 조건이 없으면 아무 쪽에나 붙는다.
TEXT_PAGE_MARGIN = 0.10


def _text_page_floor() -> float:
    """본문 대조 문턱 (DOCSTRUCT_ALIGN_TEXT_MIN · 기본 0.70).

    입력: 없음
    출력: 0~1
    비고:
        실측으로 고른 값이지만 문서군이 바뀌면 다시 재야 한다 — 손잡이로
        빼 두어야 되돌릴 수 있다.
    """
    raw = os.getenv("DOCSTRUCT_ALIGN_TEXT_MIN", "").strip()
    try:
        value = float(raw) if raw else TEXT_PAGE_MIN
    except ValueError:
        return TEXT_PAGE_MIN
    return value if 0.0 < value <= 1.0 else TEXT_PAGE_MIN


def _page_grams(pdf: dict) -> list[tuple[int, set[str]]]:
    """PDF 쪽마다 3-gram 집합 — 본문과 표를 함께 본다.

    입력: pdf — PDF 판독 결과
    출력: [(쪽 번호, 3-gram 집합)]
    """
    out = []
    for page in pdf.get("pages") or []:
        page_no = page.get("page_no")
        if not isinstance(page_no, int):
            continue
        text = (page.get("content") or "") + "\n" + "\n".join(
            t.get("markdown") or "" for t in (page.get("tables") or []))
        out.append((page_no, _grams(_norm_for_match(text))))
    return out


def _norm_for_match(text: str) -> str:
    """대조용 정규화 — 공백·강조·표 기호를 지운다."""
    return re.sub(r"\s+", "",
                  re.sub(r"\*+|\||<[^>]+>|〃", "", text or ""))


def _grams(text: str, size: int = 3) -> set[str]:
    """문자 n-gram 집합."""
    return {text[i:i + size] for i in range(len(text) - size + 1)}


def page_from_text(table: dict, page_grams: list[tuple[int, set[str]]],
                   floor: float = TEXT_PAGE_MIN,
                   margin: float = TEXT_PAGE_MARGIN) -> dict | None:
    """표 짝짓기가 실패한 표에 **본문 대조로** 쪽을 물려준다.

    입력: table — HWPX 표, page_grams — `_page_grams` 결과,
          floor — 최소 포함률, margin — 2위 쪽과의 최소 차이
    출력: {"page_no","containment","margin"} · 못 정하면 None
    비고:
        **분모를 깎는 대신 쪽을 준다** (0.4.76). 짝을 못 지은 표의 상당수는
        HWPX 가 서술 상자로 그린 것을 PDF 가 본문으로 풀어낸 것이다 —
        내용은 그대로 있고 표라는 껍데기만 사라졌다.

        실측(세 부처 141/142): `narrative` 표의 내용이 PDF **본문**에
        85% 이상 들어 있었고, PDF **표**에는 9표뿐이었다. 표 대 표로는
        맞출 수 없지만 **쪽은 줄 수 있다.**

        쪽을 줄 수 있는 비율(세 부처 못 맞춘 216표):

            문턱 85%   108개 (50%)
            문턱 70%   **147개 (68%)**
            문턱 60%   160개 (74%)

        `margin` 이 핵심이다. 성과계획서는 같은 서식 표가 쪽마다 되풀이
        되므로, 1위와 2위가 비슷하면 **어느 쪽인지 모르는 것**이고 그때
        찍으면 틀린 쪽을 준다. 모르면 주지 않는다.
    """
    grams = _grams(_norm_for_match(table.get("markdown") or ""))
    if not grams or not page_grams:
        return None
    scored = sorted(((len(grams & seen) / len(grams), page_no)
                     for page_no, seen in page_grams), reverse=True)
    best, page_no = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    if best < floor or (best - second) < margin:
        return None
    return {"page_no": page_no,
            "containment": round(best, 3),
            "margin": round(best - second, 3)}


def table_kind(table: dict) -> str:
    """짝을 못 지은 표가 **어떤 생김새인지** 이름 붙인다 (측정 전용).

    입력: table — HWPX 표 dict
    출력: narrative | diagram | sparse_grid | "" (그 밖)
    비고:
        **분모를 건드리지 않는다.** 판정을 좁히면 성적이 올라가는데 그것이
        진짜 개선인지 분모를 깎은 것인지 구분되지 않는다. 이름만 붙여
        유형 분포를 보고, 승격 여부는 그 수치를 보고 정한다.

        실물을 보고 고른 세 갈래다(문체부 못 맞춘 110표).

            narrative    한 칸에 문단이 통째로 들어간 표. `측정산식 :
                         일반국민을 대상으로 …` 같은 서술 상자다
            diagram      `⇒` 로 흐름을 그린 표. 프로그램 논리모형이
                         대표적이고 PDF 에서는 그림으로 잡히기도 한다
            sparse_grid  채움이 절반 아래. SWOT·`기존/개편` 2×2 상자가
                         그렇다

        **부처를 타는 신호는 넣지 않았다.** `〃` 병합 표시가 그렇다 —
        문체부 못 맞춘 표 110개 중 18개에 있는데 조달청·행안부는 **0건**
        이다. 한 부처의 작성 습관을 규칙으로 삼으면 다른 문서에서 무너진다.

        긴 문단과 화살표는 세 부처에 모두 나오지만 비중은 다르다
        (긴 문단: 조달청 28% · 행안부 64% · 문체부 31%).
    """
    cells = table.get("cells") or []
    if not cells:
        return ""
    texts = [(c.get("text") or "") for c in cells]
    if any(len(x) >= NARRATIVE_CHARS for x in texts):
        return "narrative"
    if any(ch in x for x in texts for ch in ARROW_CHARS):
        return "diagram"
    filled = sum(1 for x in texts if x.strip())
    if filled and filled / len(cells) < SPARSE_FILL:
        return "sparse_grid"
    return ""


def has_ditto(table: dict) -> bool:
    """`〃` 병합 표시가 두 칸 이상인가 (기록 전용).

    입력: table — HWPX 표 dict
    출력: 두 칸 이상이면 True
    비고:
        **판정에 쓰지 않는다.** 문체부에만 나오는 작성 습관이라
        (조달청·행안부 0건) 규칙으로 삼을 수 없다. 부처를 타는 신호라는
        사실 자체를 남기려고 센다.
    """
    cells = table.get("cells") or []
    return sum(1 for c in cells
               if (c.get("text") or "").strip() == "〃") >= 2


def is_matchable(table: dict) -> bool:
    """이 표를 **짝 성적에 넣을 것인가**.

    입력: table — HWPX 표 dict
    출력: 데이터 표면 True, 지면 장식이면 False
    비고:
        HWPX 는 제목·표지·간지를 **표로 그린다.** 그런 표는 PDF 에 대응
        표가 아예 없으므로, 짝을 못 지은 것이 아니라 **맞출 것이 없다.**
        분모에 넣으면 성적이 실제보다 나쁘게 보인다.

        실측(문체부 840표):

            띠(1행 또는 1열)        405개   `2027년도` · `1. 임무와 비전`
            내용이 완전히 빈 표      52개   자리만 잡은 상자
            데이터 표               433개

        **버리지 않는다.** `unmatched` 에는 그대로 실리고(0.4.64) 사유도
        붙는다 — 다만 성적을 재는 분모에서만 뺀다.
    """
    note = layout_hint(table)
    if note["layout_like"]:
        return False
    cells = table.get("cells") or []
    return any((c.get("text") or "").strip() for c in cells)


def layout_hint(table: dict) -> dict:
    """이 표가 왜 짝이 없는지 **설명할 수 있는 것만** 적는다.

    입력: table — 표 dict (cells 필요)
    출력: {rows, cols, cells, filled, fill_ratio, tokens,
           layout_like, too_few_tokens, why}
    비고:
        **판정이 아니라 단서다.** 표를 바꾸지 않고 사람이 읽을 기록으로만
        쓴다. 근거 수치를 **언제나 함께** 내므로 나중에 문턱을 다시 볼 수
        있다.

        두 가지만 말한다. 둘 다 정답 없이 확인되는 것이다.

            band            1행 또는 1열이다. 행과 열의 관계가 없으므로
                            구조적으로 데이터 표가 아니다 (실측:
                            `(단위 : 백만원)` 이 1행 15열 표였다)
            too_few_tokens  매처가 쓸 토큰이 3개 미만이다. `page_map` 은
                            Jaccard 닮음으로 짝을 짓는데 토큰이 한둘이면
                            짝짓기 자체가 성립하지 않는다 — **이 표가
                            무엇인지가 아니라 매처가 왜 못 했는지**를
                            말한다

        **채움 비율로 "레이아웃 표" 를 판정하던 것을 걷어냈다** (0.4.67).
        그것은 순환이었다: 성긴 표는 토큰이 적어 못 맞춰지는데, 그
        "못 맞춰짐" 을 근거로 다시 레이아웃 표라고 불렀다. 매처의 실패를
        매처의 출력으로 설명한 것이다.

        실측이 그 오류를 드러냈다(문체부): 못 맞춘 505표 중 **153개가
        토큰 8개 이상**이고, `table_838` 은 토큰이 **3,243개**다. 성기지도
        않고 띠도 아닌 큰 데이터 표가 통째로 못 맞춰지고 있었는데,
        채움 비율 기준은 그것을 `sparse` 로 덮어 보이지 않게 했다.

        `fill_ratio` 는 그대로 낸다 — 수치는 쓸모가 있고, 다만 그것으로
        표의 성격을 단정하지 않을 뿐이다.
    """
    from docstruct.align.page_map import _tokens

    cells = table.get("cells") or []
    rows = max((c.get("row", 0) + c.get("rowspan", 1) for c in cells),
               default=0)
    cols = max((c.get("col", 0) + c.get("colspan", 1) for c in cells),
               default=0)
    filled = sum(1 for c in cells if (c.get("text") or "").strip())
    ratio = round(filled / len(cells), 2) if cells else 0.0
    tokens = len(_tokens(cells))

    band = rows <= 1 or cols <= 1
    too_few = tokens < MIN_MATCH_TOKENS
    why = "band" if band else ("too_few_tokens" if too_few else "")
    return {
        "rows": rows,
        "cols": cols,
        "cells": len(cells),
        "filled": filled,
        "fill_ratio": ratio,
        "tokens": tokens,
        # **1행/1열만 레이아웃으로 부른다** — 구조가 곧 근거다.
        "layout_like": band,
        # 매처가 못 한 이유. 표의 성격에 대한 주장이 아니다.
        "too_few_tokens": too_few,
        "why": why,
    }


#: 눈금 사이가 이보다 벌어지면 그 사이 쪽들을 "먼 구간" 으로 표시한다.
#: 실측(네 문서): 8쪽 이내는 거의 100%, 9쪽 넘으면 57%.
WIDE_GAP_PAGES = 9


def _mark_wide_gaps(by_page: dict, measured: set[int] | None) -> list[int]:
    """**눈금이 먼 구간**의 쪽에 표시를 남긴다 (0.5.51).

    입력: by_page — 쪽별 슬롯 (제자리 갱신), measured — 눈금을 잡은 쪽
    출력: 표시한 쪽 번호 목록
    비고:
        보간은 눈금 사이가 벌어질수록 오차가 커진다. 실측(네 문서):

            2쪽 구간   100%      5~8쪽    100%
            3~4쪽       95%      9쪽 이상  **57%**

        `page_no_kind: approximate` 만으로는 "조금 추정" 과 "많이 추정" 이
        구별되지 않는다. 근거를 인용하는 쪽이 쪽 번호를 얼마나 믿을지
        정하려면 그 차이가 보여야 한다.

        `gap_pages` 로 그 구간이 몇 쪽인지도 함께 적는다.
    """
    anchored = sorted(measured or set())
    if len(anchored) < 2:
        return []
    marked: list[int] = []
    for start, end in zip(anchored, anchored[1:]):
        if end - start < WIDE_GAP_PAGES:
            continue
        for page_no in range(start + 1, end):
            slot = by_page.get(page_no)
            if slot is None:
                continue
            slot["wide_gap"] = True
            slot["gap_pages"] = end - start
            marked.append(page_no)
    return marked


#: 표가 지면 **아래끝**에 닿았다고 볼 좌표(pt). A4 높이는 842pt.
SPAN_BOTTOM = 760.0
#: 표가 지면 **위끝**에서 시작했다고 볼 좌표(pt).
SPAN_TOP = 110.0


def continued_tables(pdf_pages: list[dict]) -> dict[int, list[int]]:
    """PDF **좌표로** 쪽을 넘는 표를 짚는다 (0.5.53).

    입력: pdf_pages — PDF 판독 결과의 쪽 목록
    출력: {쪽 번호: 그 표가 걸친 쪽 범위}
    비고:
        표가 지면 아래끝에서 끝나고 다음 쪽 위끝에서 시작하면 **한 표가
        두 쪽에 인쇄된 것**이다. 실측(문체부 613쪽): 49건 · 걸친 쪽 87개.

        **왜 좌표인가** — 이 자리는 쪽 번호 하나로 답할 수 없다. 표 안
        문장을 인용하면서 "423쪽" 이라고 하면 틀린 것이 아니라 **부정확한
        질문에 억지로 답한 것**이다. 우리 오류가 아니라 문서의 구조다.

        텍스트로 찾으려 해도 그 문장은 HWPX 에서 표 블록 안에 있고, 블록은
        통째로 앞 쪽에 배정된다 — 뒤쪽 조각이 갈 데가 없다(0.5.38 에서 본
        구조적 한계). 좌표는 그 사실을 **추정 없이** 말해 준다.

        PDF 표는 `bbox` 를 모두 갖는다(실측: 475/475).
    """
    by_page = {page.get("page_no"): (page.get("tables") or [])
               for page in pdf_pages}
    spans: dict[int, list[int]] = {}
    for page_no in sorted(n for n in by_page if isinstance(n, int)):
        here, after = by_page.get(page_no) or [], by_page.get(page_no + 1) or []
        if not here or not after:
            continue
        lowest = max(((t.get("bbox") or {}).get("b") or 0) for t in here)
        highest = min(((t.get("bbox") or {}).get("t") or 9999) for t in after)
        if lowest >= SPAN_BOTTOM and highest <= SPAN_TOP:
            for member in (page_no, page_no + 1):
                spans.setdefault(member, [page_no, page_no + 1])
                spans[member] = [min(spans[member][0], page_no),
                                 max(spans[member][1], page_no + 1)]
    return spans


#: 쪽 안에서 블록을 옮길 때 표가 이만큼은 닮아야 한다.
MIN_REORDER_MATCH = 0.5


def _reorder_by_pdf(by_page: dict, pdf_pages: list[dict],
                    hwpx_tables: list) -> int:
    """쪽 **안에서** 블록 순서를 PDF 에 맞춘다 (0.5.57).

    입력: by_page — 쪽별 슬롯 (제자리 갱신), pdf_pages — PDF 쪽 목록,
          hwpx_tables — (쪽, 표) 목록
    출력: 자리를 바꾼 표 수
    비고:
        HWP 는 매달린 표(`treatAsChar=0`)를 `vertOffset` 만큼 아래에
        그린다. 그 값이 문단을 넘으면(실측: 1,006개 중 **35개**) 표가
        뒤따르는 문단 글보다 아래에 인쇄되는데, 판독은 표를 문단 끝에
        붙이므로 순서가 꼬인다:

            지면   □추진체계 · 조직도 · □자체평가위원회 · ㅇ구성 · 명단
            판독   □추진체계 · 조직도 · 명단 · □자체평가위원회 · ㅇ구성

        고치려면 한글의 조판(글꼴·줄간격으로 문단 높이 계산)을 재현해야
        한다. 그런데 **PDF 가 답을 안다** — 같은 쪽을 인쇄해 두었다.

        **쪽 배정은 건드리지 않는다.** 그 쪽에 이미 배정된 블록들끼리만
        자리를 바꾸므로, 잘못돼도 쪽 번호는 그대로다.

        PDF 에서 그 표를 못 찾으면 움직이지 않는다 — 근거 없이 옮기지
        않는다.
    """
    by_id = {}
    for _page_no, table in hwpx_tables:
        tid = table.get("id") if isinstance(table, dict) else None
        if tid:
            by_id[tid] = table

    pdf_order = {}
    for page in pdf_pages:
        text = page.get("content") or ""
        order = []
        for match in re.finditer(r"<(?:table|image) \d+>", text):
            order.append((match.start(), match.group(0)))
        pdf_order[page.get("page_no")] = (text, order)

    moved = 0
    for page_no, slot in by_page.items():
        content = slot.get("content") or ""
        blocks = list(re.finditer(r"<table (\d+)>.*?</table \1>", content, re.DOTALL))
        if len(blocks) < 2:
            continue
        pdf_text = (pdf_order.get(page_no) or ("", []))[0]
        if not pdf_text:
            continue
        rebuilt, changed = _reorder_page(content, blocks, pdf_text, by_id)
        if changed:
            slot["content"] = rebuilt
            slot["reordered_by_pdf"] = True
            moved += changed
    return moved


def _rank_blocks(blocks: list, pdf_text: str, by_id: dict) -> list[int] | None:
    """PDF 본문에서 각 표가 **몇 번째로 나오는지** (0.5.57).

    입력: blocks — 쪽 안의 표 블록들, pdf_text — 그 PDF 쪽 내용,
          by_id — 표 id → 표
    출력: 블록마다의 자리. 하나라도 못 찾으면 None
    비고:
        표의 첫 행 글자를 PDF 쪽에서 찾는다. 하나라도 못 찾으면 **아무것도
        옮기지 않는다** — 일부만 아는 채로 섞으면 더 나빠진다.
    """
    from docstruct.align.page_map import _flatten

    flat_pdf = _flatten(pdf_text)
    spots: list[int] = []
    for block in blocks:
        table = by_id.get(f"table_{block.group(1)}")
        markdown = (table or {}).get("markdown") or block.group(0)
        first = next((line for line in markdown.splitlines()
                      if line.strip() and "---" not in line), "")
        key = _flatten(first)[:40]
        if len(key) < 8:
            return None
        at = flat_pdf.find(key)
        if at < 0:
            # 앞부분이 안 맞으면 조금 짧게 한 번 더 — PDF 가 글자를 쪼갠다
            at = flat_pdf.find(key[:16]) if len(key) >= 16 else -1
        if at < 0:
            return None
        spots.append(at)
    return spots


def _reorder_page(content: str, blocks: list, pdf_text: str,
                  by_id: dict) -> tuple[str, int]:
    """쪽 안의 **표와 글을 함께** PDF 순서로 (0.5.57).

    입력: content — 쪽 내용, blocks — 표 블록들, pdf_text — PDF 쪽 내용,
          by_id — 표 id → 표
    출력: (바뀐 내용, 옮긴 조각 수)
    비고:
        표끼리만 맞추면 부족하다 — 실측(병무청 10쪽): 표 둘은 이미 순서가
        맞았고, 문제는 **표가 뒤따르는 글보다 앞에 있는 것**이었다.

            지면   □추진체계 · 조직도 · □자체평가위원회 · ㅇ구성 · 명단
            판독   □추진체계 · 조직도 · 명단 · □자체평가위원회 · ㅇ구성

        그래서 표 블록과 그 사이 글을 **모두 조각으로 보고** PDF 안에서
        각자 몇 번째로 나오는지 재어 다시 늘어놓는다.

        조각 하나라도 PDF 에서 못 찾으면 **아무것도 옮기지 않는다** — 일부만
        아는 채로 섞으면 더 나빠진다.
    """
    from docstruct.align.page_map import _flatten

    flat_pdf = _flatten(pdf_text)
    pieces: list[tuple[str, int]] = []
    last = 0
    for block in blocks:
        gap = content[last:block.start()]
        if gap.strip():
            spot = _spot_of(_flatten(gap), flat_pdf)
            if spot is None:
                return content, 0
            pieces.append((gap, spot))
        table = by_id.get(f"table_{block.group(1)}")
        markdown = (table or {}).get("markdown") or block.group(0)
        first = next((line for line in markdown.splitlines()
                      if line.strip() and "---" not in line), "")
        spot = _spot_of(_flatten(first), flat_pdf)
        if spot is None:
            return content, 0
        pieces.append((block.group(0), spot))
        last = block.end()

    # **마지막 표 뒤의 글도 조각이다** — 빼 두면 늘 맨 뒤로 가서, 표가 그
    # 글보다 앞에 있는 바로 그 경우를 고칠 수 없다(실측: 병무청 10쪽의
    # `□ 자체평가위원회 운영…`).
    tail = content[last:]
    if tail.strip():
        spot = _spot_of(_flatten(tail), flat_pdf)
        if spot is None:
            return content, 0
        pieces.append((tail, spot))

    order = sorted(range(len(pieces)), key=lambda i: pieces[i][1])
    if order == list(range(len(pieces))):
        return content, 0
    joined = "\n\n".join(pieces[i][0].strip() for i in order if pieces[i][0].strip())
    return joined, sum(1 for i, j in enumerate(order) if i != j)


def _spot_of(flat_piece: str, flat_pdf: str) -> int | None:
    """이 조각이 PDF 쪽에서 **몇 번째 글자에** 나오는지.

    입력: flat_piece — 납작하게 편 조각, flat_pdf — 납작하게 편 PDF 쪽
    출력: 자리. 못 찾으면 None
    비고:
        앞부분 40자로 찾고, 안 되면 16자로 한 번 더 본다 — PDF 가 낱말
        사이에 공백을 넣어 조각내는 일이 잦다(0.5.47).
    """
    key = flat_piece[:40]
    if len(key) < 8:
        return None
    at = flat_pdf.find(key)
    if at < 0 and len(key) >= 16:
        at = flat_pdf.find(key[:16])
    return at if at >= 0 else None


def _rebuild(content: str, blocks: list, spots: list[int]) -> tuple[str, int]:
    """블록을 PDF 순서로 바꿔 끼운다 (0.5.57).

    입력: content — 쪽 내용, blocks — 표 블록들, spots — PDF 안 자리
    출력: (바뀐 내용, 옮긴 수)
    비고:
        **블록이 있던 자리는 그대로 두고 알맹이만 바꾼다.** 사이의 글은
        건드리지 않으므로 본문 흐름이 깨지지 않는다.
    """
    order = sorted(range(len(blocks)), key=lambda i: spots[i])
    if order == list(range(len(blocks))):
        return content, 0
    texts = [block.group(0) for block in blocks]
    out, last = [], 0
    for slot, source in enumerate(order):
        out.append(content[last:blocks[slot].start()])
        out.append(texts[source])
        last = blocks[slot].end()
    out.append(content[last:])
    return "".join(out), sum(1 for i, j in enumerate(order) if i != j)


def _pdf_parts(markdown: str, pdf_tables: list) -> int:
    """이 표가 PDF 에 **몇 조각으로** 실렸는지 (0.5.59).

    입력: markdown — HWPX 표의 markdown, pdf_tables — (쪽, 표) 목록
    출력: 조각 수
    비고:
        쪽을 넘는 표는 새 쪽에서 **머리행을 다시 찍는다** — 인쇄의 사실이지
        중복이 아니다. 그래서 머리행이 PDF 표 여럿에 나타나면 그 표는
        걸친 것이다.
    """
    from docstruct.align.page_map import _flatten

    first = next((line for line in (markdown or "").splitlines()
                  if line.strip() and "---" not in line), "")
    head = _flatten(first)
    if len(head) < 8:
        return 0
    # **PDF 는 글자를 더하거나 뺀다.** 실측(병무청 10쪽): 머리행이
    # `직위(직급))주요경력` 으로 닫는 괄호가 하나 더 붙어 있었다. 그 한 자
    # 때문에 걸친 표를 못 알아보면 안 되므로, 앞부분 여러 길이로 본다.
    count = 0
    for _page_no, table in pdf_tables:
        flat = _flatten(table.get("markdown") or "")
        if any(head[:size] in flat for size in (16, 12, 8) if len(head) >= size):
            count += 1
    return count


def _mark_disputed_tables(by_page: dict, pdf_tables: list,
                          hwpx_tables: list | None = None) -> list[str]:
    """**두 방법이 다른 쪽을 가리킨 표**에 범위를 적는다 (0.5.58).

    입력: by_page — 쪽별 슬롯 (제자리 갱신)
    출력: 범위가 붙은 표 id 목록
    비고:
        표의 쪽은 두 갈래로 정해진다:

            본문 자르기   눈금으로 HWPX 본문을 쪽 경계에서 자른다
            표 짝짓기     PDF 표와 셀 내용을 대조한다

        둘이 **1쪽 차이로 다른 답**을 내는 일이 있다 — 실측(병무청 79표
        중 11건 · 조달청 63표 중 4건). 짝이 부실해서가 아니다: 닮음이
        1.0 인 것도 갈렸다(`table_63` · `table_110`).

        그 표들은 대개 **쪽 경계에 걸쳐 있다.** 표 짝짓기는 PDF 조각이
        시작된 쪽을, 본문 자르기는 HWPX 블록이 눈금 사이 어디에 떨어지는지를
        말한다 — 걸친 표에서는 둘 다 맞고 둘 다 부족하다.

        그래서 **어느 하나를 고르지 않는다.** 두 답을 범위로 적어 두면
        인용하는 쪽이 "10~11쪽의 표" 라고 답할 수 있다(0.5.53 의
        `page_span` 과 같은 뜻이다).
    """
    body_at: dict[str, int] = {}
    field_at: dict[str, int] = {}
    for page_no, slot in by_page.items():
        for num in re.findall(r"<table (\d+)>", slot.get("content") or ""):
            body_at[num] = page_no
        for table in (slot.get("tables") or []):
            num = str(table.get("table_num") or "")
            if num:
                field_at[num] = page_no

    marked: list[str] = []

    # **본문에만 있는 표도 본다** (0.5.59). 쪽 맞춤이 두 답을 내지 않아도
    # PDF 가 두 조각으로 갖고 있으면 그 표는 걸친 것이다 — 실측(병무청
    # `table_23` 명단): 본문 10쪽 하나뿐인데 PDF 는 10·11쪽에 나눠 인쇄했다.
    by_num: dict[str, dict] = {}
    for slot in by_page.values():
        for table in (slot.get("tables") or []):
            num = str(table.get("table_num") or "")
            if num:
                by_num[num] = table
    # **짝을 못 지은 표도 본다.** 쪽을 넘는 표는 조각 하나만 짝지어지므로
    # 남은 것이 `unmatched` 로 간다 — 실측: `table_23`(명단)이 그랬다.
    for _page_no, table in (hwpx_tables or []):
        num = str(table.get("table_num") or "")
        if num and num not in by_num:
            by_num[num] = table
    for num, at_body in body_at.items():
        if num in field_at or num not in by_num:
            continue
        table = by_num[num]
        if _pdf_parts(table.get("markdown") or "", pdf_tables) >= 2:
            table["page_span"] = [at_body, at_body + 1]
            table["span_reason"] = "표가 두 쪽에 걸쳐 인쇄됨"
            marked.append(f"table_{num}")

    for num, at_field in field_at.items():
        at_body = body_at.get(num)
        if at_body is None or at_body == at_field:
            continue
        span = [min(at_body, at_field), max(at_body, at_field)]
        for slot in by_page.values():
            for table in (slot.get("tables") or []):
                if str(table.get("table_num") or "") != num:
                    continue
                # **왜 갈렸는지 가린다** (0.5.59). 셋은 성격이 다르다 —
                # 실측(병무청 11건): 진짜 걸침 3 · 짝 오인 6 · 경계 차이 2.
                parts = _pdf_parts(table.get("markdown") or "", pdf_tables)
                if parts >= 2:
                    table["page_span"] = span
                    table["span_reason"] = "표가 두 쪽에 걸쳐 인쇄됨"
                elif parts == 0:
                    # PDF 에 이 표가 없다 — 짝이 엉뚱한 것을 집었다.
                    # 범위를 적으면 **걸치지도 않은 표에 걸렸다고 말하는 것**
                    # 이 된다. 실측: 조직도(table_22)가 명단 조각과 0.57 로
                    # 짝지어져 11쪽에 실렸다.
                    table["pairing_doubt"] = True
                    table["doubt_reason"] = "PDF 에 대응 표가 없는데 짝이 잡힘"
                else:
                    table["page_span"] = span
                    table["span_reason"] = "본문 위치와 표 짝이 한 쪽 다름"
        marked.append(f"table_{num}")
    return sorted(marked)


def _mark_page_spans(by_page: dict, pdf_pages: list[dict]) -> list[int]:
    """쪽을 넘는 표가 있는 쪽에 **범위**를 적는다 (0.5.53).

    입력: by_page — 쪽별 슬롯 (제자리 갱신), pdf_pages — PDF 쪽 목록
    출력: 범위가 붙은 쪽 번호 목록
    비고:
        `page_span` 은 "이 쪽의 표가 이 범위에 걸쳐 인쇄됐다" 는 뜻이다.
        근거를 인용하는 쪽이 표 안 문장을 만나면 쪽 하나가 아니라 이
        범위를 대면 된다 — **±1 이 틀림이 아니라 범위가 된다.**

        쪽을 넘는 표가 없는 쪽에는 붙이지 않는다. 모든 쪽에 달면 읽는 눈이
        흐려지고, 있다는 것 자체가 신호가 되지 못한다.
    """
    spans = continued_tables(pdf_pages)
    marked: list[int] = []
    for page_no, span in spans.items():
        slot = by_page.get(page_no)
        if slot is None:
            continue
        slot["page_span"] = span
        slot["span_reason"] = "표가 두 쪽에 걸쳐 인쇄됨"
        marked.append(page_no)
    return sorted(marked)


def _mark_split_blocks(by_page: dict) -> None:
    """쪽 경계에 걸려 **반쪽만 든 표 블록**을 쪽마다 적는다 (0.5.39).

    입력: by_page — 쪽별 슬롯 (제자리 갱신)
    출력: 없음 (`split_blocks` 필드 추가)
    비고:
        `<table N> … </table N>` 이 쪽을 넘으면 한쪽에는 여는 태그만,
        다른 쪽에는 닫는 태그만 남는다. 그 쪽만 떼어 읽으면 표가 반쪽이다.

        예전에는 이 사실이 **어디에도 없었다** — 세어 보려면 결과물을
        직접 뒤져야 했다. 실측(세 부처): 190쪽 중 112쪽이 여기 해당했고
        (0.5.37·0.5.38 로 줄었다), 그 규모를 사람이 알 길이 없었다.

        쪽마다 `split_blocks` 로 적는다:

            {"table_num": "13", "side": "open"}    여는 태그만 있다
            {"table_num": "13", "side": "close"}   닫는 태그만 있다

        걸린 표가 없는 쪽에는 필드를 두지 않는다 — 빈 목록이 모든 쪽에
        붙으면 읽는 눈이 흐려진다.
    """
    import re

    for slot in by_page.values():
        content = slot.get("content") or ""
        opened = set(re.findall(r"<table (\d+)>", content))
        closed = set(re.findall(r"</table (\d+)>", content))
        marks = ([{"table_num": num, "side": "open"} for num in sorted(opened - closed)]
                 + [{"table_num": num, "side": "close"} for num in sorted(closed - opened)])
        if marks:
            slot["split_blocks"] = marks


def _restore_line_breaks(chunks: list[dict], raw: str, masked: str) -> None:
    """가린 본문으로 자른 조각을 **원본 글로 바꿔 놓는다** (0.5.56).

    입력: chunks — 쪽별 조각 (제자리 갱신), raw — 원본 본문,
          masked — `<br>` 을 가린 본문 (길이가 같다)
    출력: 없음
    비고:
        `_mask_line_breaks` 가 길이를 지키므로 자리가 1:1 로 대응한다.
        조각의 글을 가린 본문에서 찾아 같은 자리의 원본으로 바꾼다.

        못 찾으면 그대로 둔다 — 조각은 `strip()` 된 것이라 자리가 조금
        어긋날 수 있고, 그때는 가린 글이라도 있는 편이 낫다.
    """
    if len(raw) != len(masked):
        return
    cursor = 0
    for chunk in chunks:
        body = chunk.get("content") or ""
        if not body:
            continue
        at = masked.find(body, cursor)
        if at < 0:
            at = masked.find(body)
        if at < 0:
            continue
        chunk["content"] = raw[at:at + len(body)]
        cursor = at + len(body)


def _mask_line_breaks(text: str) -> str:
    """`<br>` 을 **같은 길이의 빈칸**으로 가린다 (0.5.56).

    입력: text — markdown
    출력: `<br>` 이 공백으로 바뀐 글 (길이 그대로)
    비고:
        비교에서는 `<br>` 이 걸리적거리지만(0.5.50) **자리는 지켜야 한다** —
        눈금은 글자 수로 재므로 길이가 달라지면 자르는 자리가 어긋난다.

        그리고 산출에는 `<br>` 이 그대로 남아야 한다. 진짜 개행으로 바꾸면
        GFM 표가 깨지고, 표 markdown 과 달라져 중복 검사가 빗나가 같은 표가
        두 번 실린다 — 실측(병무청 6쪽): `<table 13>` 이 두 번 나왔다.
    """
    import re as _re

    return _re.sub(r"<br\s*/?>", lambda m: " " * len(m.group(0)), text or "",
                   flags=_re.IGNORECASE)


def _drop_line_breaks(text: str) -> str:
    """`<br>` 을 진짜 줄바꿈으로 되돌린다 (0.5.50).

    입력: text — markdown
    출력: `<br>` 이 개행으로 바뀐 글
    비고:
        셀 안 문단 경계를 살리려고 0.5.21 이 넣은 표시다. 저장할 때는
        맞지만 **비교할 때는 글자가 아니다** — 같은 표가 서로 다른 글로
        보인다.

        공백이 아니라 개행으로 바꾼다: `<br>` 뒤의 `- ` 는 줄머리 기호이고,
        공백으로 두면 줄머리로 보이지 않아 그대로 남는다.
    """
    import re as _re

    return _re.sub(r"<br\s*/?>", "\n", text or "", flags=_re.IGNORECASE)


#: 그림을 옮길 때 볼 이웃 쪽 범위. 캡션과 그림이 갈리는 것은 한 쪽 차이다.
IMAGE_MOVE_SPAN = 1
#: 이만큼은 되어야 "읽을 만한 그림" 으로 센다(pt). 아이콘·글머리 기호는
#: 어느 쪽에나 있어 근거가 못 된다.
MIN_MOVE_IMAGE_SIDE = 100.0


#: 검산에 쓸 수치의 모양 — 소수점이 있거나 다섯 자리 이상.
#: 연도(2026·2027)와 한두 자리 숫자는 어디에나 있어 근거가 못 된다.
_CHECK_NUMBER = re.compile(r"\d[\d,]*\.\d+%?|\d[\d,]{4,}")


def _check_image_numbers(by_page: dict, pdf_pages: list[dict]) -> None:
    """그림 판독의 **수치를 그 쪽 PDF 와 대조**한다 (0.5.63).

    입력: by_page — 쪽별 슬롯 (제자리 갱신), pdf_pages — PDF 쪽 목록
    출력: 없음 (`number_check` 필드 추가)
    비고:
        VLM 이 그래프의 숫자를 잘못 읽는 일이 있다 — 실측(병무청):
        `89.7% → 90.2%` 를 **`69.7% → 80.2%`** 로 읽었다. 판독 자체는
        모델의 한계이지만, **틀렸을 수 있다는 사실은 알릴 수 있다.**

        문서 전체에서 찾으면 안 된다. 실측: 잘못 읽은 `69.7%` 가 90,000자
        어딘가에 우연히 있어 **전부 확인으로 통과했다.** 그림이 실린 쪽과
        그 이웃으로 좁혀야 뜻이 있다.

        연도(2026)나 한두 자리 숫자는 세지 않는다 — 어느 쪽에나 있다.

            verified   판독한 수치가 모두 그 쪽에 있다
            partial    일부만 있다 — 잘못 읽었을 수 있다
            unseen     하나도 없다
            none       견줄 수치가 판독문에 없다
    """
    text_at: dict[int, str] = {}
    for page in pdf_pages:
        page_no = page.get("page_no")
        body = (page.get("content") or "") + "".join(
            (t.get("markdown") or "") for t in (page.get("tables") or []))
        text_at[page_no] = re.sub(r"[\s,]+", "", body)

    for page_no, slot in by_page.items():
        near = "".join(text_at.get(no, "")
                       for no in (page_no - 1, page_no, page_no + 1))
        for image in (slot.get("images") or []):
            read = image.get("vlm_markdown") or image.get("description") or ""
            numbers = _CHECK_NUMBER.findall(read)
            if not numbers:
                image["number_check"] = "none"
                continue
            hits = sum(1 for number in numbers
                       if number.replace(",", "") in near)
            image["number_check"] = ("verified" if hits == len(numbers)
                                     else "partial" if hits else "unseen")
            image["numbers_checked"] = len(numbers)
            image["numbers_found"] = hits


def _move_image_block(source: dict, target: dict, marker: str) -> bool:
    """그림 블록을 **본문에서** 옮긴다 (0.5.61).

    입력: source — 지금 쪽, target — 갈 쪽, marker — `<image N>`
    출력: 옮겼으면 True
    비고:
        `<image N> … </image N>` 과 그 안의 판독(`<image-read N>`)을 한
        덩어리로 떼어 다음 쪽 **맨 앞**에 놓는다. 캡션은 그대로 둔다 —
        캡션은 그 쪽의 글이고, 옮겨야 할 것은 그림이다.
    """
    num = marker.strip("<>").split()[-1]
    body = source.get("content") or ""
    pattern = re.compile(
        rf"<image {num}>.*?(?:</image {num}>|(?=\n\n)|$)", re.DOTALL)
    match = pattern.search(body)
    if not match:
        return False
    block = match.group(0).strip()
    if not block:
        return False
    source["content"] = (body[:match.start()] + body[match.end():]).strip()
    after = (target.get("content") or "").strip()
    target["content"] = f"{block}\n\n{after}" if after else block
    return True


def _image_page_from_pdf(image: dict, at: int, pdf_images: dict) -> int | None:
    """이 그림이 **PDF 에서 실린 쪽** (0.5.61).

    입력: image — HWPX 그림, at — 본문이 가리키는 쪽, pdf_images — 쪽 → 그림들
    출력: 쪽 번호. 가릴 수 없으면 None
    비고:
        캡션과 그림이 다른 쪽에 인쇄되는 일이 있다. 자리표시자는 본문을
        따라가므로 캡션 쪽에 붙는다 — 그림이 실제로 어디 있는지는 PDF 가
        안다. 실측(병무청): `<전년도 대비 …>` 캡션은 PDF 27쪽(인쇄 22)에,
        그래프는 28쪽(인쇄 23)에 있었다.

        **HWPX 그림에는 크기가 없다**(bbox·width 모두 None). 그래서 크기로
        견주지 못하고, 이웃 한 쪽까지 훑어 **읽을 만한 그림이 딱 하나**일
        때만 옮긴다. 둘 이상이면 어느 것인지 가릴 수 없으므로 그대로 둔다.

        작은 조각(아이콘·글머리 기호)은 세지 않는다 — 어느 쪽에나 있다.
    """
    hits: list[int] = []
    for page_no in range(at - IMAGE_MOVE_SPAN, at + IMAGE_MOVE_SPAN + 1):
        for other in pdf_images.get(page_no) or []:
            if (_bbox_width(other) >= MIN_MOVE_IMAGE_SIDE
                    and _bbox_height(other) >= MIN_MOVE_IMAGE_SIDE):
                hits.append(page_no)
    if len(hits) != 1:
        return None
    return hits[0]


def _bbox_width(image: dict) -> float:
    box = image.get("bbox") or {}
    return max(0.0, (box.get("r") or 0) - (box.get("l") or 0))


def _bbox_height(image: dict) -> float:
    box = image.get("bbox") or {}
    return max(0.0, (box.get("b") or 0) - (box.get("t") or 0))


def _place_images(hwpx: dict, by_page: dict,
                  pdf_images: dict | None = None) -> list[dict]:
    """HWPX 그림을 본문 블록 자리로 쪽에 배정한다 (0.5.19).

    입력: hwpx — HWPX 판독 결과, by_page — 쪽별 슬롯 (제자리 갱신)
    출력: 쪽을 못 찾은 그림 목록
    비고:
        `<image N>` 블록이 어느 쪽 본문에 들어갔는지로 정한다. 본문이
        이미 쪽으로 잘려 있으므로 추가 추정이 필요 없다 — **자리를
        지어내지 않는다.**
    """
    import re

    images = [img for page in (hwpx.get("pages") or [])
              for img in (page.get("images") or [])]
    if not images:
        return []

    pdf_images = pdf_images or {}
    unplaced: list[dict] = []
    for image in images:
        num = image.get("image_num")
        marker = f"<image {num}>" if num is not None else (image.get("placeholder") or "")
        found = None
        if marker:
            for page_no, slot in by_page.items():
                if marker in (slot.get("content") or ""):
                    found = page_no
                    break
        if found is None:
            unplaced.append(image)
            continue

        # **그림이 실린 쪽은 PDF 가 안다** (0.5.61). 자리표시자는 본문에
        # 붙어 있어 본문 경계를 따라가는데, 그림은 캡션보다 **다음 쪽**에
        # 인쇄되는 일이 있다 — 실측(병무청): `<전년도 대비 …>` 캡션은 PDF
        # 27쪽(인쇄 22)에, 그래프는 28쪽(인쇄 23)에 있었다.
        #
        # PDF 에 크기가 비슷한 그림이 **그 쪽 언저리에 딱 하나** 있으면
        # 그쪽을 쓴다. 여럿이거나 없으면 움직이지 않는다 — 근거 없이 옮기지
        # 않는다.
        moved = _image_page_from_pdf(image, found, pdf_images)
        if moved is not None and moved != found and moved in by_page:
            # **본문 블록도 함께 옮긴다.** `images` 필드만 바꾸면 반쪽이다 —
            # markdown 은 본문을 따라가므로 그림이 여전히 캡션 쪽에 찍힌다.
            if _move_image_block(by_page[found], by_page[moved], marker):
                image = {**image, "page_moved_from": found,
                         "move_reason": "PDF 에서 그림이 실린 쪽"}
                found = moved
        by_page[found].setdefault("images", []).append(image)
    return unplaced


def align_documents(hwpx: dict, pdf: dict, *,
                    as_markdown: bool = False) -> Any:
    """HWPX 결과를 PDF 쪽에 맞춰 나눈다.

    입력: hwpx·pdf — document.json 을 읽은 dict, as_markdown — 형식
    출력: dict(쪽으로 나뉜 문서) 또는 markdown 문자열
    예외: 표가 없으면 ValueError — 맞출 근거가 없다
    비고:
        **표를 앵커로 쓴다.** 본문 글은 쪽 경계가 모호하지만 표는 덩어리라
        어느 쪽에 있었는지가 분명하다. 짝을 짓지 못한 표는 쪽 없이 남기고
        그 수를 함께 낸다 — 조용히 버리면 수치가 거짓이 된다.
    """
    from docstruct.align.page_map import (
        _flatten, align, interpolate, merge_anchors, split_text_by_page,
        table_anchors, text_anchors,
    )

    # **본문 눈금이 주력이다.** 표 정렬은 서식이 같은 표가 많은 문서에서
    # 흔들리지만(행안부 317표 중 230표), 본문 글은 순서가 바뀌지 않는다 —
    # 실측: 눈금 231개가 예외 없이 차례대로 증가했다(230/230).
    # **`<br>` 은 줄바꿈 표시이지 글자가 아니다** (0.5.50). 셀 안 문단
    # 경계를 살리려고 0.5.21 이 넣은 것인데, 비교하기 전에 실제 줄바꿈으로
    # 되돌리지 않으면 같은 표가 서로 다른 글이 된다:
    #
    #     HWPX  |투입<br>(input)|⇒|활동<br>(activities)|…
    #     PDF   |투입(input)|활동(activities)|…
    #
    # 실측(문체부): `프로그램 논리` 표가 HWPX 에 33개 다 있는데 본문
    # 대조에서는 **1회로 보였다** — `<br>` 없이 이어진 한 곳만 걸린 것이다.
    # 그 한 자리로 쪽 144·180·365 가 몰려 ±100쪽씩 튀었다.
    #
    # **`_flatten` 이 아니라 여기서 없앤다.** `_raw_positions` 가 납작한
    # 자리를 원문 자리로 되돌릴 때 `_flatten` 의 규칙을 손으로 다시
    # 밟으므로, 한쪽만 고치면 두 계산이 어긋나 자리가 통째로 밀린다
    # (실측: 잣대가 98% → 15%). 재료를 먼저 다듬는 편이 안전하다.
    # **비교용과 산출용을 가른다** (0.5.56). `<br>` 은 비교할 때만 거치적
    # 거리고(0.5.50), 산출에는 **그대로 있어야 한다** — 셀 안 줄바꿈을
    # 진짜 개행으로 바꾸면 GFM 표가 깨지고, 표 markdown 과도 달라져
    # `to_markdown` 의 중복 검사(`markdown not in body`)가 빗나가
    # **같은 표가 두 번** 실린다.
    #
    # 길이가 달라지면 눈금 자리가 어긋나므로, `<br>` 을 **같은 길이의
    # 빈칸**으로 바꾼다. 자리는 그대로 두고 글자만 지우는 셈이다.
    raw_body = "\n\n".join(
        (page.get("content") or "") for page in (hwpx.get("pages") or []))
    hwpx_body = _mask_line_breaks(raw_body)
    pdf_pages = [{**page,
                  "content": _mask_line_breaks(page.get("content") or ""),
                  "tables": [{**t, "markdown": _mask_line_breaks(t.get("markdown") or "")}
                             for t in (page.get("tables") or [])]}
                 for page in (pdf.get("pages") or [])]
    # **본문 눈금과 표 눈금을 함께 쓴다.** 본문이 있는 쪽은 본문이,
    # 표만 있는 쪽은 표가 맡아 서로를 보완한다 — 실측(행안부): 본문만
    # 쓰면 닮음 중앙 78%·0.6↑ 67%, 표를 더하면 **83%·73%** 다.
    anchors = merge_anchors(
        text_anchors(pdf_pages, hwpx_body),
        table_anchors(hwpx_body, tables_of(hwpx), tables_of(pdf)),
    )
    measured = {page for _pos, page in anchors}
    # **눈금 사이를 비례로 채운다.** 놓친 쪽의 대부분은 표만 있어 본문
    # 글로는 잡을 수 없다 — 실측(행안부): 163쪽 중 112쪽이 그렇다.
    last = max((p.get("page_no") or 0) for p in pdf_pages) if pdf_pages else 0
    # PDF 가 빈 지면이라고 말하는 쪽 — 거기엔 HWPX 대응 내용이 없다 (0.5.44).
    blank_pages = {
        page.get("page_no") for page in pdf_pages
        if not (page.get("content") or "").strip()
        and not (page.get("tables") or [])
        and not (page.get("images") or [])
    }
    filled = interpolate(anchors, last, len(_flatten(hwpx_body)), blank_pages)
    # 경계가 표 블록 안일 때 PDF 에게 물을 재료를 넘긴다 (0.5.38).
    hwpx_blocks = {str(t.get("table_num")): (t.get("cells") or [])
                   for _page_no, t in tables_of(hwpx)}
    pdf_page_text = {
        page.get("page_no"): _flatten(
            (page.get("content") or "")
            + "".join((t.get("markdown") or "") for t in (page.get("tables") or [])))
        for page in pdf_pages
    }
    text_pages = (split_text_by_page(hwpx_body, filled, measured,
                                     blocks=hwpx_blocks, page_text=pdf_page_text,
                                     blank_pages=blank_pages)
                  if filled else [])
    # **자른 뒤 원본으로 되돌린다** (0.5.56). 가린 본문은 자리를 재기 위한
    # 것이지 산출물이 아니다 — `<br>` 이 공백으로 남으면 GFM 표가 깨지고,
    # 표 markdown 과 달라져 같은 표가 두 번 실린다. 길이를 지켰으므로
    # 잘린 조각의 자리도 그대로다.
    _restore_line_breaks(text_pages, raw_body, hwpx_body)

    hwpx_tables = tables_of(hwpx)
    pdf_tables = tables_of(pdf)
    if not hwpx_tables:
        raise ValueError("HWPX 결과에 표가 없습니다 — 맞출 근거가 없습니다")
    if not pdf_tables:
        raise ValueError("PDF 결과에 표가 없습니다 — 맞출 근거가 없습니다")

    gt_cells = [(t.get("cells") or []) for _p, t in hwpx_tables]
    pairs = align(pdf_tables, gt_cells)

    by_page: dict[int, dict] = {}
    matched = 0
    placed: set[int] = set()
    for pair in pairs:
        if pair.gt_index is None or pair.page_no is None:
            continue
        matched += 1
        placed.add(pair.gt_index)
        table = hwpx_tables[pair.gt_index][1]
        slot = by_page.setdefault(
            pair.page_no,
            {"page_no": pair.page_no, "tables": [], "similarity": []})
        slot["tables"].append(table)
        slot["similarity"].append(round(pair.similarity, 2))

    # 본문 눈금으로 나눈 쪽에 표를 얹는다.
    #
    # **첫 눈금 앞의 머리말을 버리지 않는다** (0.4.57). `split_text_by_page`
    # 는 그것을 `page_no: None` 조각으로 일부러 남겨 두는데("첫 눈금 앞의
    # 머리말도 잃지 않는다"), 예전 어댑터는 `continue` 로 **조용히
    # 지웠다.** 표지·간지·발간사처럼 첫 눈금 앞에 오는 글이 통째로
    # 사라지는 자리다 — 쪽을 모르는 것과 없는 것은 다르다.
    head = ""
    for chunk in text_pages:
        page_no = chunk.get("page_no")
        if page_no is None:
            head = (chunk.get("content") or "").strip()
            continue
        slot = by_page.setdefault(
            page_no, {"page_no": page_no, "tables": [], "similarity": []})
        slot["content"] = chunk.get("content", "")
        if chunk.get("blank"):
            slot["blank"] = True
        estimated = bool(chunk.get("estimated"))
        slot["estimated"] = estimated
        # **쪽 번호를 믿어도 되는지 결과물에 적는다** (0.5.19). 예전에는
        # `estimated` 만 있고 `page_no_kind` 는 비어 있었다 — 판독 결과와
        # 같은 이름의 필드가 align 결과에서만 None 이라, 쓰는 쪽이 분기해야
        # 했고 브릿지(`_PAGE_KIND`)도 값을 못 찾았다.
        #
        #   exact        본문·표 눈금으로 **잡은** 쪽
        #   approximate  앞뒤 눈금 사이를 비례로 **채운** 쪽
        slot["page_no_kind"] = "approximate" if estimated else "exact"

    # **그림도 쪽에 배정한다** (0.5.19). 예전에는 align 결과에 그림이
    # 하나도 없었다 — HWPX 가 가진 그림이 통째로 사라졌고, 조직도·별첨
    # 상자처럼 **글자가 그림 안에만 있는 것**까지 함께 없어졌다.
    #
    # 자리는 본문이 말해 준다: 그림은 `<image N>` 블록으로 본문에 박혀
    # 있으므로, 그 블록을 담은 쪽이 그 그림의 쪽이다. 쪽을 못 찾은 그림은
    # 버리지 않고 `unplaced_images` 로 낸다 — 표와 같은 원칙이다.
    unplaced_images = _place_images(
        hwpx, by_page,
        pdf_images={page.get("page_no"): (page.get("images") or [])
                    for page in pdf_pages})


    # **표 짝짓기가 실패한 표에 본문으로 쪽을 준다** (0.4.76 · 2차 경로).
    # HWPX 가 서술 상자로 그린 것을 PDF 가 본문으로 풀어낸 경우가 많아,
    # 표 대 표로는 못 맞추지만 쪽은 줄 수 있다. 실측: 세 부처 못 맞춘
    # 216표 중 147개(68%)에 쪽이 붙는다.
    page_grams = _page_grams(pdf)
    floor = _text_page_floor()
    by_text = 0
    for index, (_page_no, table) in enumerate(hwpx_tables):
        if index in placed or not is_matchable(table):
            continue
        got = page_from_text(table, page_grams, floor)
        if got is None:
            continue
        placed.add(index)
        by_text += 1
        slot = by_page.setdefault(
            got["page_no"],
            {"page_no": got["page_no"], "tables": [], "similarity": []})
        # **어떻게 쪽을 얻었는지 남긴다.** 표 짝으로 얻은 것과 본문으로
        # 얻은 것은 근거가 다르므로 하류가 구분할 수 있어야 한다.
        slot["tables"].append({**table, "page_source": "text",
                               "page_evidence": got})

    # **짝을 못 지은 표도 버리지 않는다** (0.4.64). 머리말은 `head` 로
    # 살려 두면서 표는 그냥 지우고 있었다 — 같은 원칙("쪽을 모르는 것과
    # 없는 것은 다르다")이 표에는 적용되지 않은 것이다.
    #
    # 실측(조달청): 표 110개 중 55개가 결과물에서 사라졌고, 본문에는
    # `<table 46>` 같은 자리표시자가 남아 있었다. 자리표시자 100종 중
    # 46종은 가리킬 표가 없다 — 하류가 그것을 따라가면 빈손이다.
    unmatched = []
    for index, (_page_no, table) in enumerate(hwpx_tables):
        if index in placed:
            continue
        # **왜 못 맞췄는지 짐작할 단서**를 함께 낸다. HWPX 는 제목 상자를
        # 표로 그리므로(실측: 조달청 119표 중 1행1열이 31개) PDF 에 대응
        # 표가 아예 없는 경우가 많다 — "맞추기 실패" 와 "맞출 것이 없음"
        # 은 다르고, 결과물이 둘을 구분하지 못하면 수치가 나쁘게만 보인다.
        note = layout_hint(table)
        # **쪽을 넘는 표인지 여기서 적어 둔다** (0.5.59). `to_markdown` 은
        # PDF 원본을 받지 않으므로 나중에는 셀 수 없다. 조각이 둘 이상이면
        # 그 표는 쪽을 넘어 인쇄된 것이다 — 짝은 하나만 지어지고 나머지가
        # 여기로 온다(실측: 병무청 `table_23` 명단).
        parts = _pdf_parts(table.get("markdown") or "", pdf_tables)
        if parts >= 2:
            note["pdf_parts"] = parts
        # **쪼개진 표인지 함께 잰다** (0.4.75). 큰 표가 PDF 에서 여러 조각이
        # 되면 Jaccard 가 크기 차이에 끌려 전부 버린다 — 그 유형이 얼마나
        # 되는지 알아야 짝짓기를 고칠지 정할 수 있다.
        if not note["layout_like"]:
            got = fragment_note(table, pdf_tables)
            if got:
                note["split"] = got
            # **유형만 이름 붙인다** (0.4.76 · 측정 전용). 분모는
            # 그대로다 — 판정을 좁혀 성적을 올리는 것과 진짜 개선을
            # 구분해야 한다.
            kind = table_kind(table)
            if kind:
                note["kind"] = kind
            if has_ditto(table):
                # 부처를 타는 신호 — 판정에 쓰지 않고 세기만 한다.
                note["ditto"] = True
        unmatched.append({**table, "align_note": note})

    # **모든 쪽에 종류를 적는다** (0.5.19). 본문 없이 표 짝짓기만으로
    # 생긴 쪽은 쪽 번호는 얻었지만 본문 경계가 없으므로 `exact` 라 할 수
    # 없다 — 비어 있는 것보다 "확실하지 않다" 고 적는 편이 정직하다.
    # **슬롯은 여러 단계에서 생기므로 마지막에 한 번 채운다.**
    for slot in by_page.values():
        slot.setdefault("page_no_kind", "approximate")
        slot.setdefault("estimated", True)

    # **인쇄 쪽번호도 함께 간다** (0.5.25). 쪽 맞춤 결과를 인용하는 쪽은
    # 사람이 읽는 번호를 필요로 한다 — "PDF 59번째 장" 이 아니라 "54쪽".
    # PDF 판독이 이미 쪽마다 계산해 두었으므로 그대로 옮긴다.
    printed = {page.get("page_no"): page.get("printed_page_no")
               for page in (pdf.get("pages") or [])}
    for page_no, slot in by_page.items():
        if printed.get(page_no) is not None:
            slot["printed_page_no"] = printed[page_no]

    _check_image_numbers(by_page, pdf_pages)
    _reorder_by_pdf(by_page, pdf_pages, hwpx_tables=tables_of(hwpx))
    disputed = _mark_disputed_tables(by_page, pdf_tables=tables_of(pdf),
                                     hwpx_tables=tables_of(hwpx))
    _mark_split_blocks(by_page)
    wide_gap_pages = _mark_wide_gaps(by_page, measured)
    span_pages = _mark_page_spans(by_page, pdf_pages)

    pages = [by_page[key] for key in sorted(by_page)]
    layout_like = sum(1 for t in unmatched
                      if t["align_note"]["layout_like"])
    # **성적은 맞출 수 있는 표로만 잰다** (0.4.75). 제목 상자·빈 상자는
    # PDF 에 대응 표가 없으므로 분모에 넣으면 수치가 거짓으로 나빠진다.
    matchable = [t for _page_no, t in hwpx_tables if is_matchable(t)]
    matchable_matched = sum(
        1 for slot in by_page.values() for t in slot["tables"]
        if is_matchable(t))
    result = {
        "filename": hwpx.get("filename"),
        "source": "hwpx+pdf 쪽 맞춤",
        # **문서의 쪽 수는 PDF 가 정한다** (0.5.46). `len(pages)` 는 *우리가
        # 낸 쪽* 수여서, 표지처럼 HWPX 대응이 없어 자리를 못 만든 쪽이
        # 있으면 실제보다 적게 보인다 — 실측(병무청): 97쪽 문서인데 96.
        #
        # 보는 사람은 "쪽 수가 안 맞네" 로 읽는다. 쪽 번호는 PDF 것을 그대로
        # 쓰고 `printed_page_no` 도 PDF 와 한 칸도 다르지 않은데, 합계만
        # 어긋나 틀린 것처럼 보인다.
        #
        # 몇 쪽을 못 채웠는지는 따로 적는다 — 숨기는 것이 아니라 나누는 것이다.
        "page_count": len(pdf_pages) or len(pages),
        #: 쪽 맞춤이 내용을 채운 쪽 수 (표지 등은 빠질 수 있다).
        "aligned_pages": len(pages),
        #: PDF 에는 있으나 대응 내용을 못 찾은 쪽 — 대개 표지·간지다.
        "unaligned_pages": sorted(
            {page.get("page_no") for page in pdf_pages}
            - {page.get("page_no") for page in pages}),
        "text_anchors": len(anchors),
        "estimated_pages": sum(1 for c in text_pages if c.get("estimated")),
        "unplaced_images": unplaced_images,
        #: **눈금 사이가 먼 쪽들** (0.5.51). 보간은 눈금 사이가 벌어질수록
        #: 오차가 커진다 — 실측(네 문서): 8쪽 이내 구간은 거의 100%,
        #: 9쪽 넘는 구간은 57% 였다. 어느 쪽이 그런 자리인지 적어 두면
        #: 인용하는 쪽이 "±1 여지가 있다" 를 알 수 있다.
        "wide_gap_pages": wide_gap_pages,
        #: **표가 두 쪽에 걸쳐 인쇄된 쪽들** (0.5.53). 그 표 안의 글을
        #: 인용할 때는 쪽 하나가 아니라 `page_span` 을 대야 한다.
        "page_span_pages": span_pages,
        #: **쪽이 갈린 표** (0.5.58, 0.5.59 에서 갈래를 나눔). 본문 위치와
        #: 표 짝이 다른 쪽을 가리킨 표들이다. 까닭이 셋으로 갈린다:
        #:   `page_span` + "표가 두 쪽에 걸쳐 인쇄됨"  — 진짜 걸침
        #:   `page_span` + "…한 쪽 다름"              — 경계 차이
        #:   `pairing_doubt`                          — 짝이 엉뚱한 것을 집음
        "disputed_tables": disputed,
        #: 쪽 경계에 걸린 표 블록 수 — 자세한 자리는 각 쪽의 `split_blocks`.
        "split_block_pages": sum(1 for page in pages if page.get("split_blocks")),
        "matched_tables": matched,
        "total_tables": len(hwpx_tables),
        "unmatched_tables": len(unmatched),
        # 맞출 수 있는 표만으로 잰 성적 — 지면 장식을 뺀 것이다.
        "matchable_tables": len(matchable),
        "matchable_matched": matchable_matched,
        # 그중 본문 대조로 쪽을 얻은 표 — 근거가 다르므로 따로 센다.
        "matched_by_text": by_text,
        # 그중 1행1열 제목 상자 — PDF 에 대응 표가 없는 것이 정상이다.
        "unmatched_layout_like": layout_like,
        # 첫 눈금 앞의 글. 쪽을 모르므로 pages 에 넣지 않고 따로 낸다 —
        # 없으면 빈 문자열이라 쓰는 쪽이 분기하기 쉽다.
        "head": head,
        "head_chars": len(head),
        "pages": pages,
        # 쪽을 **모르는** 표. 없는 것이 아니므로 `pages` 에 넣지 않고
        # 따로 낸다 — `head` 와 같은 처리다.
        "unmatched": unmatched,
    }
    # **본문으로 준 쪽을 독립 신호로 검증한다** (0.4.78). 자기 점수
    # (`containment`)로 자기를 검증하면 순환이므로, 표 짝이 정한 순서와
    # 어긋나지 않는지 본다. 실측(네 부처): 145/147 = 99%.
    got = order_check(result)
    if got:
        result["text_order_check"] = got

    return to_markdown(result) if as_markdown else result


def _pdf_tables_of(result: dict) -> list:
    """맞춤 결과에 실린 표들을 `(쪽, 표)` 목록으로 (0.5.59).

    입력: result — 맞춤 결과
    출력: `_pdf_parts` 가 받는 모양
    비고:
        `to_markdown` 은 PDF 원본을 받지 않으므로, 결과에 실린 표로 대신
        센다. 쪽을 넘는 표는 조각 하나가 쪽에 실리고 나머지가 `unmatched`
        에 있으니 둘을 합쳐야 조각 수가 맞는다.
    """
    out = [(page.get("page_no"), table)
           for page in result.get("pages") or []
           for table in (page.get("tables") or [])]
    out += [(None, table) for table in (result.get("unmatched") or [])]
    return out


#: 검산 결과를 사람 말로.
_CHECK_WORDS = {
    "verified": "판독 수치가 그 쪽에서 모두 확인됨",
    "partial": "판독 수치 일부만 확인됨 — 잘못 읽었을 수 있음",
    "unseen": "판독 수치를 그 쪽에서 찾지 못함",
}


def _image_note(image: dict) -> str:
    """그림에 붙일 **검산 한 줄** (0.5.63).

    입력: image — 그림 dict
    출력: `" · 판독 수치 일부만 확인됨 (2/6)"` 꼴. 붙일 것이 없으면 ""
    비고:
        `verified` 에는 붙이지 않는다 — 확인된 것이 보통이고, 표시가 있다는
        것 자체가 신호여야 한다. 옮긴 그림은 그 사실도 함께 적는다.
    """
    parts: list[str] = []
    check = image.get("number_check")
    if check in ("partial", "unseen"):
        found = image.get("numbers_found")
        total = image.get("numbers_checked")
        count = f" ({found}/{total})" if total else ""
        parts.append(f"{_CHECK_WORDS[check]}{count}")
    if image.get("page_moved_from"):
        parts.append(f"{image['page_moved_from']}쪽에서 옮김 "
                     f"({image.get('move_reason') or ''})")
    return (" · " + " · ".join(parts)) if parts else ""


def _table_note(table: dict) -> str:
    """표에 붙일 **한 줄 주석** (0.5.59).

    입력: table — 표 dict
    출력: `" · 10~11쪽에 걸친 표"` 꼴. 붙일 것이 없으면 ""
    """
    span = table.get("page_span")
    if span:
        return (f" · {span[0]}~{span[1]}쪽에 걸친 표"
                f" ({table.get('span_reason') or ''})")
    if table.get("pairing_doubt"):
        return f" · 이 쪽에 실린 근거가 약함 ({table.get('doubt_reason') or ''})"
    return ""


def _collect_notes(result: dict, pdf_tables: list | None = None) -> dict[str, str]:
    """표마다의 주석을 **문서 전체에서** 모은다 (0.5.59).

    입력: result — 맞춤 결과
    출력: {표 번호: 주석}
    비고:
        쪽을 넘는 표는 조각 하나만 짝지어지므로 나머지가 `unmatched` 로
        간다. 본문에는 그 블록이 있는데 그 쪽 `tables` 에는 없으므로,
        쪽별로 모으면 놓친다.
    """
    notes: dict[str, str] = {}
    for page in result["pages"]:
        for table in (page.get("tables") or []):
            note = _table_note(table)
            if note:
                notes[str(table.get("table_num") or "")] = note
    # **짝을 못 지은 표는 여기서 잰다** (0.5.59). `unmatched` 항목은 쪽
    # 슬롯과 다른 dict 이므로 `_mark_disputed_tables` 가 적어 둔 표시가
    # 실리지 않는다. 그 대신 `fragment_note` 가 이미 재어 둔 **쪼개진
    # 조각 수**를 쓴다 — 쪽을 넘는 표는 조각 하나만 짝지어지고 나머지가
    # 여기로 온다(실측: 병무청 `table_23` 명단).
    for table in (result.get("unmatched") or []):
        num = str(table.get("table_num") or "")
        if not num or num in notes:
            continue
        parts = (table.get("align_note") or {}).get("pdf_parts") or 0
        if parts >= 2:
            notes[num] = f" · {parts}조각으로 나뉘어 인쇄됨 (쪽을 넘는 표)"
    return notes


def _annotate_blocks(body: str, page: dict,
                     notes: dict[str, str] | None = None) -> str:
    """본문의 `<table N>` 태그 옆에 주석을 붙인다 (0.5.59).

    입력: body — 쪽 본문, page — 그 쪽
    출력: 주석이 붙은 본문
    비고:
        주석을 **본문 문장으로 끼워 넣으면 읽는 글이 되어 버린다.** 표에
        딸린 것임이 보이도록 블록 태그 줄에 붙인다.
    """
    if notes is None:
        notes = {}
        for table in (page.get("tables") or []):
            note = _table_note(table)
            if note:
                notes[str(table.get("table_num") or "")] = note
    if not notes:
        return body

    def mark(match):
        note = notes.get(match.group(1))
        return f"{match.group(0)}<!--{note} -->" if note else match.group(0)

    body = re.sub(r"<table (\d+)>", mark, body)

    # **그림 판독의 검산 결과도 붙인다** (0.5.63). 판독문은 본문에 그대로
    # 실리므로, 그것이 확인된 것인지 아닌지가 보여야 한다.
    shots = {}
    for image in (page.get("images") or []):
        note = _image_note(image)
        if note:
            shots[str(image.get("image_num") or "")] = note

    def mark_image(match):
        note = shots.get(match.group(1))
        return f"{match.group(0)}<!--{note} -->" if note else match.group(0)

    return re.sub(r"<image (\d+)>", mark_image, body)


def _page_note(page: dict) -> str:
    """쪽 제목에 붙일 **믿을 만한 정도** (0.5.54).

    입력: page — 맞춘 결과의 쪽 하나
    출력: `" (인쇄 49) *(추정 · 표가 433~434쪽에 걸침)*"` 꼴. 붙일 것이 없으면 ""
    비고:
        json 에는 `printed_page_no` · `wide_gap` · `page_span` · `blank` 가
        다 있는데 markdown 에는 `*(추정)*` 하나뿐이었다. 근거를 인용하는
        사람이 md 를 읽는다면 **거기서도 쪽 번호를 얼마나 믿을지** 보여야
        한다.

        인쇄 쪽은 괄호로 먼저 — 사람이 "54쪽" 이라 말할 때 가리키는 번호다.
        나머지는 기울임 한 덩어리로 묶는다. 붙일 것이 없으면 아무것도
        붙이지 않는다 — 모든 제목에 꼬리가 달리면 읽는 눈이 흐려진다.
    """
    printed = page.get("printed_page_no")
    head = f" (인쇄 {printed})" if printed else ""

    notes: list[str] = []
    if page.get("blank"):
        notes.append("빈 지면")
    if page.get("estimated"):
        notes.append("추정")
    if page.get("wide_gap"):
        gap = page.get("gap_pages")
        notes.append(f"눈금이 {gap}쪽 떨어짐" if gap else "눈금이 멂")
    span = page.get("page_span")
    if span:
        notes.append(f"표가 {span[0]}~{span[1]}쪽에 걸침")
    return head + (f" *({' · '.join(notes)})*" if notes else "")


def to_markdown(result: dict) -> str:
    """쪽으로 나뉜 결과를 markdown 으로.

    입력: result — align_documents 의 dict
    출력: markdown 문자열
    비고:
        추정 쪽은 제목에 표시한다 — 눈금으로 잰 쪽과 보간한 쪽이 화면에서
        같아 보이면 안 된다.
    """
    lines = [f"# {result.get('filename') or '문서'}", ""]
    layout_like = result.get("unmatched_layout_like", 0)
    notes = _collect_notes(result)
    unaligned = result.get("unaligned_pages") or []
    lines.append(f"쪽 {result['page_count']}"
                 + (f" (대응 {result.get('aligned_pages', 0)}쪽 · "
                    f"못 찾음 {len(unaligned)}쪽)" if unaligned else "")
                 + " · 본문 눈금 "
                 f"{result.get('text_anchors', 0)}"
                 f"(추정 {result.get('estimated_pages', 0)}) · 표 "
                 f"{result['matched_tables']}/{result['total_tables']} 배정"
                 f" (못 맞춘 표 {result['unmatched_tables']}"
                 + (f", 그중 제목 상자로 보이는 것 {layout_like}"
                    if layout_like else "") + ")")
    lines.append("")
    head = (result.get("head") or "").strip()
    if head:
        # 쪽을 모른다는 사실을 제목에 적는다 — 1쪽인 것처럼 보이면 안 된다.
        lines += ["---", "", "## (쪽 미상 — 첫 눈금 앞 머리말)", "", head, ""]
    for page in result["pages"]:
        lines += ["---", "", f"## 페이지 {page['page_no']}{_page_note(page)}", ""]
        body = (page.get("content") or "").strip()
        if body:
            # **표 블록 태그 옆에 적는다** (0.5.59). 본문 한복판에 문장으로
            # 끼워 넣으면 읽는 글이 되어 버린다 — 표에 붙은 주석이어야 한다.
            lines += [_annotate_blocks(body, page, notes), ""]
        for table in page["tables"]:
            markdown = (table.get("markdown") or "").strip()
            if not markdown or markdown in body:
                continue
            # **쪽이 갈린 표에는 범위를 적는다** (0.5.58). 본문 위치와 표
            # 짝이 다른 쪽을 가리킨 표는 대개 쪽 경계에 걸쳐 있다 — 표만
            # 덩그러니 실으면 읽는 사람이 앞 쪽에서 본 것과 같은 표인지
            # 알 수 없다.
            lines += [f"<!-- {table.get('id') or 'table'}{_table_note(table)} -->",
                      markdown, ""]

    # **짝을 못 지은 표도 싣는다** (0.4.64). 본문에 `<table 46>` 자리표시자가
    # 남아 있는데 그 표가 어디에도 없으면 하류가 빈손이 된다.
    unmatched = result.get("unmatched") or []
    if unmatched:
        lines += ["---", "",
                  f"## (쪽 미상 — 짝을 못 지은 표 {len(unmatched)}개)", "",
                  "PDF 쪽과 짝을 짓지 못한 표입니다. 쪽을 **모르는** 것이지 "
                  "없는 것이 아닙니다. HWPX 는 제목 상자도 표로 그리므로 "
                  "PDF 에 대응 표가 아예 없는 경우가 많습니다.", ""]
        for table in unmatched:
            note = table.get("align_note") or {}
            tag = " *(제목 상자로 보임)*" if note.get("layout_like") else ""
            lines += [f"### {table.get('placeholder') or table.get('id')}"
                      f"{tag}", ""]
            markdown = (table.get("markdown") or "").strip()
            if markdown:
                lines += [markdown, ""]
    return "\n".join(lines).rstrip() + "\n"


def order_check(result: dict) -> dict | None:
    """본문으로 준 쪽이 **표 짝이 정한 순서와 어긋나지 않는가**.

    입력: result — align_documents 의 dict
    출력: {"checked","inside","outside","hit_rate"} · 잴 것이 없으면 None
    비고:
        **자기 점수로 자기를 검증할 수 없다.** `containment` 와 `margin` 은
        본문 대조가 스스로 매긴 점수이므로, 그것으로 "잘 맞췄다" 고 하면
        순환이다.

        표 짝짓기는 **다른 신호**(셀 토큰의 Jaccard)를 쓴다. 그래서 본문
        으로 준 표가 앞뒤의 *표 짝* 이웃이 정한 쪽 구간 안에 들어가는지
        보면 독립 검증이 된다. 문서 순서는 뒤집히지 않으므로, 구간을
        벗어나면 둘 중 하나가 틀린 것이다.

        실측(네 부처 · 본문 배정 175건 중 검증 가능 147건):

            조달청        6/6    100%
            행안부       48/49    98%
            문체부       72/73    99%
            해양경찰청   19/19   100%
            ─────────────────────────
            합계        145/147  **99%**

        벗어난 2건은 이웃 구간 바로 밖(1~4쪽 차이)이었다.
    """
    import bisect

    rows = []
    for page in result.get("pages") or []:
        for table in page.get("tables") or []:
            num = table.get("table_num")
            if num is None:
                continue
            rows.append((num, page["page_no"],
                         table.get("page_source") or "pair"))
    rows.sort()
    pairs = [(num, page_no) for num, page_no, src in rows if src == "pair"]
    if not pairs:
        return None
    nums = [num for num, _p in pairs]

    inside = outside = 0
    for num, page_no, src in rows:
        if src != "text":
            continue
        index = bisect.bisect_left(nums, num)
        if index == 0 or index >= len(pairs):
            continue                             # 앞뒤 중 하나가 없다
        low, high = pairs[index - 1][1], pairs[index][1]
        if low <= page_no <= high:
            inside += 1
        else:
            outside += 1
    checked = inside + outside
    if not checked:
        return None
    return {"checked": checked, "inside": inside, "outside": outside,
            "hit_rate": round(inside / checked, 3)}


def summary_lines(result: dict) -> list[str]:
    """쪽 맞춤 결과 요약 — CLI 화면용.

    입력: result — align_documents 의 dict
    출력: 화면에 찍을 줄 목록
    비고:
        **실측과 추정을 갈라 적는다.** 한 수치로 뭉치면 80%가 눈금에서
        온 것인지 보간에서 온 것인지 알 수 없다.
    """
    total = result.get("matchable_tables") or result["total_tables"]
    matched = result.get("matchable_matched", result["matched_tables"])
    ratio = (matched / total * 100) if total else 0.0
    pages = result["page_count"]
    estimated = result.get("estimated_pages", 0)
    lines = [
        f"쪽: {pages}개 (본문 눈금 {result.get('text_anchors', 0)}개 · "
        f"보간 추정 {estimated}쪽)"
        + (f" · 대응 못 찾은 쪽 {len(result['unaligned_pages'])}개 "
           f"{result['unaligned_pages']} — 대개 표지·간지입니다"
           if result.get("unaligned_pages") else ""),
        f"표 배정: {matched}/{total} ({ratio:.0f}%) — 맞출 수 있는 표 기준"
        f" (전체 {result['total_tables']}표 중 지면 장식 "
        f"{result['total_tables'] - total}개 제외)",
    ]
    if result.get("head_chars"):
        lines.append(f"쪽 미상 머리말: {result['head_chars']}자 "
                     "(첫 눈금 앞 — head 필드)")
    if result.get("matched_by_text"):
        line = (f"그중 본문 대조로 쪽을 얻은 표: {result['matched_by_text']}개 "
                "(표 짝이 아니라 PDF 본문에서 찾음 — `page_source: text`)")
        check = result.get("text_order_check")
        if check:
            # **독립 검증 결과를 함께 낸다.** 자기 점수만 보이면 믿을
            # 근거가 없다.
            line += (f" · 순서 검증 {check['inside']}/{check['checked']} "
                     f"({check['hit_rate']:.0%})")
        lines.append(line)
    if result.get("unmatched_tables"):
        layout_like = result.get("unmatched_layout_like", 0)
        # **버린 것이 아니라 따로 실었다**는 사실을 적는다. 수치만 보면
        # 사라진 줄 안다.
        lines.append(
            f"쪽 미상 표: {result['unmatched_tables']}개 (unmatched 필드)"
            + (f" · 그중 {layout_like}개는 제목 상자로 보입니다 — PDF 에 "
               "대응 표가 없는 것이 정상입니다" if layout_like else ""))
    return lines
