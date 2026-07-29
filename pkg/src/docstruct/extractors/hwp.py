"""HWP → PageContent.

역할:
    HWPML(XML) / pyhwp(HTML) / olefile(텍스트) 중 실제로 사용된 경로를
    판별해 본문 markdown 을 만들고, 표를 `<table N>` 블록으로 치환한다.
    페이지 이미지가 없는 형식이므로 표 재추출 근거로 쓸 원본 HTML 을 함께 반환한다.
호출부:
    docstruct.extractors.registry._extract_hwp
출력:
    (list[PageContent], list[str]) — 페이지 목록과 원본 `<table>` HTML 조각
    HWP 는 페이지 경계 정보가 없어 문서 전체가 1개 PageContent 가 된다.
"""
from __future__ import annotations

from docstruct.converters.hwp.converter import HwpConverter
from docstruct.converters.hwp.hwpml import is_hwpml, to_markdown as hwpml_to_markdown
from docstruct.models import PageContent, PageTrace
from docstruct.tables.markdown import inject_table_placeholders


def extract_hwp_pages(hwp_path: str) -> tuple[list[PageContent], list[str]]:
    """HWP 파일을 구조화한다.

    입력: hwp_path — HWP 파일 경로
    출력:
        pages       PageContent 1개 (page_no=1, page_no_kind='document')
        table_html  원본 `<table>` HTML 조각. HWPML·olefile 경로에서는 빈 목록
    """
    table_html: list[str] = []
    if is_hwpml(hwp_path):
        md = hwpml_to_markdown(hwp_path)
        path_name = "hwpml-xml"
    else:
        converter = HwpConverter(hwp_path)
        md = converter.to_markdown()
        path_name = converter.extraction_path()
        # 페이지 이미지가 없는 경로라 표 재추출 근거로 원본 HTML 을 챙깁니다.
        table_html = converter.table_html_fragments()

    content, tables, _ = inject_table_placeholders(md)

    notes: list[str] = []
    trace = PageTrace(extractor=path_name, text_source="n/a", table_count=len(tables))

    if path_name == "hwpml-xml":
        trace.add("converters.hwp.hwpml", "HWPML(XML) 직접 파싱",
                  "바이너리 HWP 가 아니라 XML — ElementTree 로 표 구조 보존")
    elif path_name == "pyhwp-html":
        trace.add("converters.hwp.pyhwp", "hwp5html 실행", "HWP 바이너리 → HTML")
        trace.add("converters.html.blocks", "HTML → markdown", "BeautifulSoup · 표 구조 보존")
    else:
        trace.add("converters.hwp.pyhwp", "hwp5html 결과 불충분", "폴백 판정",
                  status="warn")
        trace.add("converters.hwp.olefile", "OLE 스트림 텍스트 추출",
                  "표·그림 구조 손실", status="warn")
        notes.append(
            "pyhwp HTML 이 불충분해 olefile 텍스트 폴백으로 처리 — 표·그림 구조가 손실됩니다"
        )

    trace.add("docstruct.tables.markdown", "표 블록 placeholder 삽입",
              f"<table N> {len(tables)}개" if tables else "표 없음")
    trace.notes = notes

    pages = [
        PageContent(page_no=1, page_no_kind="document", content=content,
                    tables=tables, trace=trace)
    ]
    return pages, table_html
