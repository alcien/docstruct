"""긴 문서를 여러 조각으로 나눈다.

역할:
    HWP 처럼 페이지 경계가 없는 문서는 본문 전체가 한 덩어리로 나온다.
    그대로 두면 후속 처리(청킹·색인·LLM 입력)가 곤란하므로,
    **구조 경계를 지키면서** 목표 크기까지 모아 나눈다.
호출부:
    docstruct.pipeline.build_document (split_chars 를 준 경우)
    docstruct.api.DocStruct.set(split_chars=...)
출력:
    list[PageContent] — 나뉜 조각. 표·이미지는 속한 조각으로 따라간다

비고:
    구조 경계에서만 자르므로 문단 중간이 끊기지 않는다. 경계가 없으면
    줄 단위로 자른다 (그래도 줄 중간은 끊지 않는다).
"""
from __future__ import annotations

import logging
import re
from typing import Iterable

from docstruct.models import ImageInfo, PageContent, PageTrace, TableInfo

_log = logging.getLogger(__name__)

#: 조각 경계로 삼을 줄. 위에 있을수록 큰 단위다.
#: 한국 공문서에서 흔한 형태를 순서대로 본다.
_BOUNDARIES: tuple[tuple[str, str], ...] = (
    ("제N장", r"^\s*제\s*\d+\s*장"),
    ("로마숫자", r"^\s*[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]\s*[.．]"),
    ("markdown 제목", r"^\s*#{1,3}\s+\S"),
    ("□", r"^\s*□"),
    ("◇", r"^\s*◇"),
    ("○", r"^\s*○"),
    ("번호 항목", r"^\s*\d+\s*[.．]\s*\S"),
)

#: 표·이미지 placeholder 를 찾는 식
_TABLE_MARK = re.compile(r"<table\s+(\d+)>")
_IMAGE_MARK = re.compile(r"<image\s+(\d+)>")


def boundary_lines(lines: list[str], want: int) -> tuple[str, list[int]]:
    """목표 조각 수에 맞는 경계 종류와 줄 번호를 찾는다.

    입력:
        lines  본문 줄 목록
        want   원하는 조각 수 (전체 길이 ÷ 목표 크기)
    출력: (경계 이름, 줄 번호 목록). 마땅한 것이 없으면 ("없음", [])
    비고:
        큰 단위(제N장)부터 보되, **개수가 목표에 못 미치면 다음 단위**를
        본다. 목차에만 3번 나오는 "제N장" 으로 40만 자를 나눌 수는 없다.
        경계가 목표보다 많은 것은 문제없다 — 모아서 쓰면 되기 때문이다.
    """
    best: tuple[str, list[int]] = ("없음", [])
    for name, pattern in _BOUNDARIES:
        rx = re.compile(pattern)
        idx = [i for i, line in enumerate(lines) if rx.match(line)]
        if len(idx) > len(best[1]):
            best = (name, idx)
        if len(idx) >= want and _spread_enough(idx, len(lines), want):
            return name, idx           # 충분하고 고르게 퍼져 있다
    # 어느 단위도 못 미치면 가장 많이 잡히는 것을 쓴다.
    return best if len(best[1]) >= 2 else ("없음", [])


def _spread_enough(idx: list[int], total: int, want: int) -> bool:
    """경계가 문서 전체에 고르게 퍼져 있는지.

    입력: idx — 경계 줄 번호, total — 전체 줄 수, want — 원하는 조각 수
    출력: 뒷부분까지 경계가 있으면 True
    비고:
        목차에만 몰려 있는 표시(예: 앞쪽 50줄 안의 "제1장~제3장")로는
        본문을 나눌 수 없다. 마지막 경계가 문서 뒤쪽에 있는지 본다.
    """
    if not idx or total <= 0:
        return False
    return idx[-1] > total * 0.5 and len(idx) >= want


def _collect_marks(text: str, tables: Iterable[TableInfo],
                   images: Iterable[ImageInfo]) -> tuple[list[TableInfo], list[ImageInfo]]:
    """조각 본문에 등장하는 표·이미지만 골라낸다.

    입력: text — 조각 본문, tables/images — 원본 페이지의 전체 목록
    출력: (그 조각에 속한 표, 이미지)
    """
    table_nums = {m.group(1) for m in _TABLE_MARK.finditer(text)}
    image_nums = {m.group(1) for m in _IMAGE_MARK.finditer(text)}
    kept_tables = [
        t for t in tables
        if _TABLE_MARK.search(t.placeholder or "")
        and _TABLE_MARK.search(t.placeholder).group(1) in table_nums
    ]
    kept_images = [
        i for i in images
        if _IMAGE_MARK.search(i.placeholder or "")
        and _IMAGE_MARK.search(i.placeholder).group(1) in image_nums
    ]
    return kept_tables, kept_images


def split_page(page: PageContent, max_chars: int) -> list[PageContent]:
    """페이지 하나를 목표 크기 이하 조각들로 나눈다.

    입력:
        page       나눌 페이지
        max_chars  조각 하나의 목표 상한 (글자 수)
    출력:
        list[PageContent] — 나뉜 조각. 나눌 필요가 없으면 [page] 그대로
    비고:
        경계를 넘어가면서까지 자르지 않으므로, 한 조각이 목표보다 클 수 있다
        (예: 경계 없이 이어지는 긴 표). 그 편이 문맥을 지키는 데 낫다.
    """
    content = page.content or ""
    if len(content) <= max_chars:
        return [page]

    lines = content.splitlines()
    want = max(2, len(content) // max_chars)
    kind, marks = boundary_lines(lines, want)
    if not marks:
        kind, marks = "줄", list(range(0, len(lines), 200))

    # 경계에서 시작해 목표 크기를 넘길 때까지 모은다.
    starts = [m for m in marks if m > 0]
    chunks: list[tuple[int, int]] = []
    begin = 0
    size = sum(len(lines[i]) + 1 for i in range(0, starts[0] if starts else len(lines)))
    for pos in starts:
        if size >= max_chars:
            chunks.append((begin, pos))
            begin, size = pos, 0
        nxt = starts[starts.index(pos) + 1] if pos != starts[-1] else len(lines)
        size += sum(len(lines[i]) + 1 for i in range(pos, nxt))
    chunks.append((begin, len(lines)))

    # 마지막 조각이 목표를 크게 넘기면 남은 경계로 한 번 더 나눈다.
    # (경계를 모으다 보면 끝부분이 통째로 남는 경우가 있다)
    a, b = chunks[-1]
    if sum(len(lines[i]) + 1 for i in range(a, b)) > max_chars * 1.5:
        tail = [m for m in starts if a < m < b]
        if tail:
            chunks.pop()
            begin, size = a, 0
            for pos in tail:
                nxt = tail[tail.index(pos) + 1] if pos != tail[-1] else b
                if size >= max_chars:
                    chunks.append((begin, pos))
                    begin, size = pos, 0
                size += sum(len(lines[i]) + 1 for i in range(pos, nxt))
            chunks.append((begin, b))

    if len(chunks) <= 1:
        return [page]

    out: list[PageContent] = []
    for n, (a, b) in enumerate(chunks, start=1):
        text = "\n".join(lines[a:b]).strip()
        if not text:
            continue
        tables, images = _collect_marks(text, page.tables, page.images)
        trace = PageTrace(
            extractor=page.trace.extractor,
            text_source=page.trace.text_source,
            ocr_ratio=page.trace.ocr_ratio,
            table_count=len(tables),
            picture_count=len(images),
        )
        trace.steps = list(page.trace.steps)
        trace.add(
            "docstruct.split",
            "긴 문서 분할",
            f"{kind} 경계 기준 {n}/{len(chunks)} 조각 — {len(text):,}자",
        )
        out.append(
            PageContent(
                page_no=n,
                page_no_kind="chunk",
                content=text,
                tables=tables,
                images=images,
                page_image_path=page.page_image_path,
                trace=trace,
                layout=page.layout,
            )
        )

    _log.info(
        "긴 문서를 %d조각으로 나눴습니다 (%s 경계, 목표 %s자)",
        len(out), kind, f"{max_chars:,}",
    )
    return out


def split_document(pages: list[PageContent], max_chars: int) -> list[PageContent]:
    """문서 전체를 조각으로 나눈다.

    입력: pages — 원본 페이지 목록, max_chars — 조각 상한
    출력: 나뉜 페이지 목록 (번호가 1부터 다시 매겨짐)
    비고: 이미 여러 페이지로 나뉜 문서(PDF 등)에는 영향이 적다.
    """
    if max_chars <= 0:
        return pages

    out: list[PageContent] = []
    for page in pages:
        out.extend(split_page(page, max_chars))

    for n, page in enumerate(out, start=1):
        page.page_no = n
    return out
