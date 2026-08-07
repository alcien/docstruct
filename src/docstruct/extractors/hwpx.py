"""HWPX → PageContent.

역할:
    python-hwpx 로 OOXML 을 읽어 markdown 을 만들고 표를 블록으로 치환한다.
호출부:
    docstruct.extractors.registry._extract_hwpx
출력:
    list[PageContent] — 문서 전체가 1개 (HWPX 도 페이지 경계 정보 없음)
"""
from __future__ import annotations

from docstruct.converters.hwpx.converter import rich_markdown as _rich_markdown
from docstruct.converters.korean_text import normalize_korean_text
from docstruct.models import PageContent, PageTrace
from docstruct.tables.markdown import inject_table_placeholders


def extract_hwpx_pages(hwpx_path: str) -> list[PageContent]:
    """HWPX 파일을 구조화한다.

    입력: hwpx_path — HWPX 파일 경로
    출력: PageContent 1개를 담은 리스트
    예외: python-hwpx 미설치 시 RuntimeError
    """
    # 탐색 결과보다 실제 import 를 우선한다. 설치 직후 커널을 재시작하지
    # 않으면 탐색만 실패하고 import 는 되는 상태가 생긴다.
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

    doc = HwpxDocument.open(hwpx_path)
    md = _rich_markdown(doc)
    md = normalize_korean_text(md)
    content, tables, _ = inject_table_placeholders(md)

    trace = PageTrace(extractor="python-hwpx", text_source="n/a", table_count=len(tables))
    trace.add("converters.deps.hwpx", "HWPX(OOXML) 파싱",
              "zip + XML — rich markdown 내보내기")
    trace.add("docstruct.tables.markdown", "표 블록 placeholder 삽입",
              f"<table N> {len(tables)}개" if tables else "표 없음")

    return [
        PageContent(page_no=1, page_no_kind="document", content=content,
                    tables=tables, trace=trace)
    ]
