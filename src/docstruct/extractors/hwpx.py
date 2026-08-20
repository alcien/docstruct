"""HWPX → PageContent.

역할:
    HWPX(OOXML) 를 읽어 markdown 을 만들고 표를 블록으로 치환한다.
호출부:
    docstruct.extractors.registry._extract_hwpx
출력:
    list[PageContent] — 문서 전체가 1개 (HWPX 도 페이지 경계 정보 없음)

왜 XML 을 직접 읽는가
------------------
python-hwpx 의 markdown 내보내기는 손실이 크다. 같은 문서(성과계획서
국회, 표 212개)로 재어 보면:

    python-hwpx markdown   표  94개 · 셀 93.8% · 모든 텍스트에 취소선 4,456회
    XML 직접 파싱          표 212개 · 셀 100%  · 취소선 없음

**변환 파일 자체에는 표 212개·셀 5,391개가 온전히 들어 있다.** 손실은
파일이 아니라 내보내기 단계에서 생긴다. 취소선은 밑줄 스타일 값이
라이브러리 표에 없어 생기는 것으로, pyhwp 의 `UnderlineStyle 15` 와 같은
뿌리다.

XML 직접 파싱은 pyhwp(AGPL) 경로와 같은 품질을 9배 빠르게 낸다
(2.62초 → 0.28초).

python-hwpx 는 폴백으로 남긴다. 새 파서가 예외를 내면 그쪽으로 물러나
문서를 통째로 잃지 않는다.
"""
from __future__ import annotations

import logging

from docstruct.converters.korean_text import normalize_korean_text
from docstruct.models import PageContent, PageTrace
from docstruct.tables.markdown import inject_table_placeholders

_log = logging.getLogger(__name__)


def _fallback_markdown(hwpx_path: str) -> tuple[str, str]:
    """python-hwpx 로 markdown 을 만든다 (폴백).

    입력: hwpx_path — HWPX 파일 경로
    출력: (markdown, 경로 이름)
    예외: python-hwpx 미설치 시 ImportError
    """
    try:
        from hwpx import HwpxDocument
    except ImportError as exc:
        import sys

        raise ImportError(
            "python-hwpx 를 불러올 수 없습니다.\n"
            f"  실행 중인 파이썬 : {sys.executable}\n"
            f"  import 시도 결과 : {type(exc).__name__}: {exc}\n"
            f'  설치            : "{sys.executable}" -m pip install python-hwpx'
        ) from exc

    from docstruct.converters.hwpx.converter import rich_markdown

    return rich_markdown(HwpxDocument.open(hwpx_path)), "python-hwpx"


def extract_hwpx_pages(hwpx_path: str) -> list[PageContent]:
    """HWPX 파일을 구조화한다.

    입력: hwpx_path — HWPX 파일 경로
    출력: PageContent 1개를 담은 리스트
    예외: 두 경로 모두 실패하면 마지막 예외를 올린다
    동작:
        XML 직접 파싱을 먼저 시도하고, 실패하면 python-hwpx 로 물러난다.
    """
    from docstruct.converters.hwpx import hwpxtree

    try:
        markdown = hwpxtree.to_markdown(hwpx_path)
        source = "hwpx-tree"
        detail = "zip + XML 직접 파싱 — 표 구조·병합 보존"
    except Exception as exc:                     # noqa: BLE001 - 폴백이 있다
        _log.warning(
            "HWPX XML 직접 파싱 실패 — python-hwpx 로 물러납니다: %s", exc
        )
        markdown, source = _fallback_markdown(hwpx_path)
        detail = f"python-hwpx 내보내기 (XML 파싱 실패: {str(exc)[:60]})"

    markdown = normalize_korean_text(markdown)
    content, tables, _ = inject_table_placeholders(markdown)

    if source == "hwpx-tree":
        # 병합 정보를 함께 낸다. markdown 은 `colSpan="3"` 을 표현하지 못해
        # 한 칸에만 값이 들어가는데, 그 자리에서 span 이 사라진다.
        try:
            grids = hwpxtree.table_grids(hwpx_path)
        except Exception as exc:             # noqa: BLE001 - 본문은 이미 나왔다
            _log.warning("표 셀 격자를 읽지 못했습니다: %s", exc)
            grids = []
        for table, grid in zip(tables, grids):
            table.cells = grid

    trace = PageTrace(extractor=source, text_source="n/a", table_count=len(tables))
    trace.add("converters.hwpx.hwpxtree", "HWPX(OOXML) 파싱", detail)
    trace.add("docstruct.tables.markdown", "표 블록 placeholder 삽입",
              f"<table N> {len(tables)}개" if tables else "표 없음")

    return [
        PageContent(page_no=1, page_no_kind="document", content=content,
                    tables=tables, trace=trace)
    ]
