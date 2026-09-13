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
    hwpx_body = "\n\n".join(
        (page.get("content") or "") for page in (hwpx.get("pages") or []))
    pdf_pages = pdf.get("pages") or []
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
    filled = interpolate(anchors, last, len(_flatten(hwpx_body)))
    text_pages = (split_text_by_page(hwpx_body, filled, measured)
                  if filled else [])

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
        slot["estimated"] = bool(chunk.get("estimated"))

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
        "page_count": len(pages),
        "text_anchors": len(anchors),
        "estimated_pages": sum(1 for c in text_pages if c.get("estimated")),
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
    lines.append(f"쪽 {result['page_count']} · 본문 눈금 "
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
        mark = " *(추정)*" if page.get("estimated") else ""
        lines += ["---", "", f"## 페이지 {page['page_no']}{mark}", ""]
        body = (page.get("content") or "").strip()
        if body:
            lines += [body, ""]
        for table in page["tables"]:
            markdown = (table.get("markdown") or "").strip()
            if markdown and markdown not in body:
                lines += [markdown, ""]

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
        f"보간 추정 {estimated}쪽)",
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
