"""실험 page_chrome — 쪽마다 되풀이되는 머리말·꼬리말 검출 (측정 전용).

입력:
    쪽 목록 content
출력:
    없음 — PageContent.page_chrome 기록

역할:
    문서의 거의 모든 쪽에 똑같이 나타나는 줄을 찾아 **지면 껍데기**로
    지목한다. 본문은 바꾸지 않는다.
호출부:
    pipeline (실험이 켜졌을 때) — 사다리 밖, 검증 계열
설정:
    DOCSTRUCT_EXP_PAGE_CHROME=1 (기본 꺼짐)
    DOCSTRUCT_EXP_CHROME_RATIO   이 비율 이상 쪽에 나오면 후보 (기본 0.05)
    DOCSTRUCT_EXP_CHROME_EDGE    지면 위·아래 몇 줄을 볼지 (기본 2)

왜 있는가 — 판독은 맞는데 내용이 아닌 글
--------------------------------------
실측(주택과세금 377쪽)에서 나온 문제다. 이 PDF 는 웹 이북을 브라우저로
인쇄한 것이라 **모든 쪽에 인쇄 껍데기가 박혀 있다**:

    26. 5. 11. 오후 5:44          ← 인쇄한 시각
    2025 주택과세금                ← 브라우저 제목
    https://www.nts.go.kr/...     ← 주소
    4/380                         ← 브라우저 쪽 번호 (문서 쪽 번호가 아니다)

판독기가 이것을 읽는 것은 **틀린 것이 아니다** — 지면에 실제로 있다.
그러나 하류(RAG)에는 순수한 잡음이고, 글이 적은 쪽에서는 이것이 본문의
대부분을 차지한다. 실측: 표지 24쪽의 본문이 껍데기 112자뿐이었다.

VLM 은 대체로 이것을 알아서 무시했고(353쪽 중 3쪽만 새어듦), rapidocr 는
그대로 옮겼다 — **판독기에 따라 결과가 갈리는 자리**이기도 하다.

어떻게 가려내는가 — 횟수가 아니라 **자리**
----------------------------------------
처음에는 "거의 모든 쪽에 나오면 껍데기" 로 잡았는데 **틀렸다.** 주택과세금
실측으로 두 번 어긋났다.

    ① 문턱이 높으면 못 잡는다
       VLM 이 껍데기를 알아서 무시해(353쪽 중 3쪽만 새어듦) 실제로는
       24쪽(6.4%)에만 남았다. 60% 문턱으로는 0건이었다 — **판독기가
       좋을수록 검출이 안 되는** 뒤집힌 detector 였다.

    ② 문턱을 낮추면 본문이 걸린다
       횟수로만 보면 `사례`(48쪽)·`Q&A`(30쪽)가 인쇄 껍데기(24쪽)보다
       더 자주 나온다. 둘 다 본문의 절 이름이다.

가르는 것은 **자리**다. 껍데기는 정의상 지면의 위나 아래에 있고, 절
이름은 글 가운데에 있다. 가장자리 두 줄만 보면 `사례` 는 48→18쪽,
`Q&A` 는 30→12쪽으로 떨어지고 껍데기는 그대로 남는다.

왜 지우지 않고 지목만 하나
------------------------
가장자리로 좁혀도 `사례`(18쪽)는 남는다 — 그 낱말로 시작하는 쪽이 실제로
있기 때문이다. 지우는 판단은 오탐이 곧 본문 삭제라 되돌릴 수 없다. ④ 가
예산 표에서 61건 오탐을 낸 전례가 있다.

대신 쪽마다 **본문 대비 비중**(`share`)을 함께 남긴다. `사례` 로 시작하는
쪽은 비중이 몇 %지만, 껍데기뿐인 쪽은 100% 다 — 사람이 그 수치로 가른다.

무엇을 하고 무엇을 안 하나
------------------------
    한다     되풀이되는 줄을 찾아 쪽마다 `page_chrome` 에 기록
    안 한다  본문에서 제거 (측정 전용)
             표 안 텍스트 (표는 별도 문제)
             한 쪽에만 있는 줄 (그것은 본문이다)
"""
from __future__ import annotations

import logging
import os
import re
from collections import Counter
from typing import TYPE_CHECKING

from docstruct.experiments.registry import Experiment, register

if TYPE_CHECKING:
    from docstruct.models import PageContent

_log = logging.getLogger(__name__)

#: 이 비율 이상의 쪽 가장자리에 나오면 후보로 본다. **낮게 잡는다** —
#: 좋은 판독기가 껍데기를 걸러내면 남는 쪽이 적어지기 때문이다(실측:
#: 6.4%). 가려내는 일은 자리(가장자리)와 비중(share)이 맡는다.
DEFAULT_RATIO = 0.05

#: 지면 위·아래 몇 줄까지를 "가장자리" 로 볼 것인가.
DEFAULT_EDGE = 2

#: 이보다 쪽이 적으면 재지 않는다 — 반복이 우연일 수 있다.
MIN_PAGES = 5

#: 표 자리표시자는 세지 않는다.
_TABLE_TAG = re.compile(r"<table \d+>")

#: 쪽 번호만 다른 줄을 한 줄로 묶기 위한 자리 (`4/380` → `#/#`).
_DIGITS = re.compile(r"\d+")

#: markdown 표 구분선 — 모든 표에 나오므로 세면 안 된다.
_SEPARATOR = re.compile(r"^[\s|:\-]+$")


def normalize_line(line: str) -> str:
    """쪽마다 숫자만 다른 줄을 같은 것으로 보이게 만든다.

    입력: line — 한 줄
    출력: 숫자를 `#` 로 바꾼 문자열
    비고:
        `4/380` 과 `5/380` 은 다른 문자열이지만 같은 껍데기다. 숫자를
        지우지 않고 `#` 로 **바꾼다** — 지우면 `2025 주택과 세금` 같은
        본문 제목까지 뭉개져 서로 다른 줄이 같아진다.
    """
    return _DIGITS.sub("#", line.strip())


def _ratio() -> float:
    """후보 판정 문턱 (DOCSTRUCT_EXP_CHROME_RATIO)."""
    raw = os.getenv("DOCSTRUCT_EXP_CHROME_RATIO", "").strip()
    try:
        value = float(raw) if raw else DEFAULT_RATIO
    except ValueError:
        return DEFAULT_RATIO
    return value if 0.0 < value <= 1.0 else DEFAULT_RATIO


def _edge() -> int:
    """가장자리로 볼 줄 수 (DOCSTRUCT_EXP_CHROME_EDGE)."""
    raw = os.getenv("DOCSTRUCT_EXP_CHROME_EDGE", "").strip()
    try:
        value = int(raw) if raw else DEFAULT_EDGE
    except ValueError:
        return DEFAULT_EDGE
    return value if value > 0 else DEFAULT_EDGE


def body_lines(page) -> list[str]:
    """세어 볼 줄만 남긴다.

    입력: page — PageContent
    출력: 빈 줄과 표 구분선을 뺀 줄 목록
    비고:
        markdown 표 구분선(`|---|---|`)은 모든 표에 나오므로 세면 안 된다 —
        실측에서 39쪽·37쪽으로 상위에 올라왔다.
    """
    body = _TABLE_TAG.sub("", page.content or "")
    return [line for line in body.splitlines()
            if line.strip() and not _SEPARATOR.match(line)]


def edge_lines(lines: list[str], edge: int) -> set[str]:
    """지면 위·아래 가장자리 줄을 정규화해 모은다.

    입력: lines — body_lines 결과, edge — 가장자리 줄 수
    출력: 정규화된 줄 집합
    비고:
        **한 쪽 안의 중복은 세지 않는다**(집합) — 한 쪽에 같은 줄이 열 번
        나오는 것은 판독 실패이지 껍데기가 아니고, 그것은 반복 가드가
        따로 본다. 여기서 세는 것은 **몇 쪽에 걸쳐** 나오는가다.
    """
    return ({normalize_line(line) for line in lines[:edge]}
            | {normalize_line(line) for line in lines[-edge:]})


def find_chrome(pages: list[PageContent], ratio: float,
                edge: int = DEFAULT_EDGE) -> dict[str, int]:
    """가장자리에서 되풀이되는 줄을 찾는다.

    입력: pages — 페이지 목록, ratio — 문턱 (0~1), edge — 가장자리 줄 수
    출력: {정규화된 줄: 나온 쪽 수}
    """
    counts: Counter[str] = Counter()
    for page in pages:
        counts.update(edge_lines(body_lines(page), edge))
    floor = max(MIN_PAGES, int(len(pages) * ratio))
    return {line: n for line, n in counts.items() if n >= floor}


def run(pages: list[PageContent], **kwargs) -> int:
    """되풀이되는 머리말·꼬리말을 찾아 쪽마다 기록한다.

    입력: pages — 페이지 목록 (page_chrome 필드만 채운다)
    출력: 껍데기가 발견된 쪽 수
    """
    if len(pages) < MIN_PAGES:
        return 0
    edge = _edge()
    chrome = find_chrome(pages, _ratio(), edge)
    if not chrome:
        return 0

    _log.info("가장자리에서 되풀이되는 줄 %d종 — 지면 껍데기 후보", len(chrome))
    hits = 0
    for page in pages:
        lines = body_lines(page)
        if not lines:
            continue
        # 세는 것은 가장자리에서 잡힌 줄이지만, **비중은 그 줄이 이 쪽에서
        # 차지하는 몫**으로 잰다 — 같은 줄이 가운데에도 있으면 그것은
        # 본문이므로 세지 않는다.
        # **가장자리를 자리로 집는다.** 줄 수가 적은 쪽에서는 위·아래
        # 가장자리가 겹치는데, 줄을 합치면 같은 줄이 두 번 세어져 비중이
        # 100% 를 넘는다 (실측: 3쪽에서 122%).
        edges = sorted(set(range(min(edge, len(lines))))
                       | set(range(max(0, len(lines) - edge), len(lines))))
        matched = [lines[i] for i in edges
                   if normalize_line(lines[i]) in chrome]
        if not matched:
            continue
        chrome_chars = sum(len(line) for line in matched)
        total_chars = sum(len(line) for line in lines)
        share = round(chrome_chars / total_chars, 3) if total_chars else 0.0
        page.page_chrome = {
            "lines": matched[:10],
            "line_count": len(matched),
            "chars": chrome_chars,
            # **본문 대비 비중이 가르는 수치다.** 몇 %면 절 이름이 우연히
            # 가장자리에 온 것이고, 100% 면 그 쪽에는 내용이 없다는 뜻이다 —
            # 실측(주택과세금): 표지 24쪽이 그랬다.
            "share": share,
            "engine": getattr(page, "ocr_engine", None),
        }
        hits += 1
        page.trace.add(
            "docstruct.experiments.text.page_chrome", "되풀이되는 줄",
            f"{len(matched)}줄 · 본문의 {share:.0%}"
            + (" — 이 쪽은 껍데기뿐입니다" if share >= 0.9 else ""),
            status="warn" if share >= 0.9 else "ok")
    return hits


register(Experiment(
    key="page_chrome",
    title="쪽마다 되풀이되는 머리말·꼬리말 검출",
    purpose="판독은 맞는데 내용이 아닌 글(인쇄 껍데기)을 지목 — 웹 이북을 "
            "인쇄한 PDF 는 모든 쪽에 시각·주소·브라우저 쪽번호가 박힌다",
    origin="주택과세금 377쪽 실측 — 표지 24쪽의 본문이 껍데기 112자뿐이었다",
    formats=("pdf", "hwpx", "hwp"),
    needs=("page",),
    status="testing",
    note="측정 전용 · 본문 불변. 지우지 않는 이유는 쪽마다 반복되는 "
         "**본문**(표 이어짐 머리행·장 제목 띠)과 구별해야 하기 때문이다 — "
         "④ 가 예산 표에서 61건 오탐을 낸 전례가 있다. 0.4.60 신설.",
    run=run,
    knobs={
        "DOCSTRUCT_EXP_CHROME_RATIO": "후보 판정 문턱 (기본 0.05)",
        "DOCSTRUCT_EXP_CHROME_EDGE": "지면 위·아래로 볼 줄 수 (기본 2)",
    },
))
