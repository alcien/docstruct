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

import logging
import re
from pathlib import Path

from docstruct.converters.hwp import preview
from docstruct.converters.hwp.converter import HwpConverter
from docstruct.converters.hwp.hwpml import is_hwpml, to_markdown as hwpml_to_markdown
from docstruct.models import PageContent, PageTrace
from docstruct.tables.markdown import inject_table_placeholders
from docstruct.tables.tags import make_table_id

_log = logging.getLogger(__name__)


def extract_hwp_pages(
    hwp_path: str,
    *,
    image_dir: str | Path | None = None,
) -> tuple[list[PageContent], list[str]]:
    """HWP 파일을 구조화한다.

    입력:
        hwp_path   HWP 파일 경로
        image_dir  미리보기 이미지를 저장할 위치 (olefile 폴백에서만 사용)
    출력:
        pages       PageContent 1개 (page_no=1, page_no_kind='document')
        table_html  원본 `<table>` HTML 조각. HWPML·olefile 경로에서는 빈 목록
    """
    table_html: list[str] = []
    page_image_path: str | None = None
    if is_hwpml(hwp_path):
        md = hwpml_to_markdown(hwp_path)
        path_name = "hwpml-xml"
        converter = None
    else:
        converter = HwpConverter(hwp_path)
        md = converter.to_markdown()
        path_name = converter.extraction_path()
        # 페이지 이미지가 없는 경로라 표 재추출 근거로 원본 HTML 을 챙깁니다.
        table_html = converter.table_html_fragments()

    notes: list[str] = []
    trace = PageTrace(extractor=path_name, text_source="n/a", table_count=0)

    if path_name == "hwpml-xml":
        trace.add("converters.hwp.hwpml", "HWPML(XML) 직접 파싱",
                  "바이너리 HWP 가 아니라 XML — ElementTree 로 표 구조 보존")
    elif path_name == "hwp5-tree":
        trace.add("converters.hwp.hwp5tree", "pyhwp 파서 트리 직접 읽기",
                  "표·중첩표·병합 구조 보존")
    elif path_name == "pyhwp-html":
        trace.add("converters.hwp.pyhwp", "hwp5html 실행", "HWP 바이너리 → HTML")
        trace.add("converters.html.blocks", "HTML → markdown", "BeautifulSoup · 표 구조 보존")
    else:
        reason = getattr(converter, "fallback_reason", None) if converter else None
        trace.add("converters.hwp.pyhwp", "hwp5html 결과 불충분",
                  reason or "폴백 판정", status="warn")
        trace.add("converters.hwp.olefile", "OLE 스트림 텍스트 추출",
                  "표·그림 구조 손실", status="warn")
        notes.append(
            "pyhwp HTML 이 불충분해 olefile 텍스트 폴백으로 처리 — "
            "표·그림 구조가 손실됩니다"
            + (f" (사유: {reason})" if reason else "")
        )
        md, page_image_path = _apply_preview(
            hwp_path, md, image_dir, trace, notes
        )

    # 미리보기 보강이 끝난 뒤에 표를 뽑아야 복원된 표가 잡힙니다.
    # 표 번호는 문서 전체 통번호라, 페이지로 가르기 **전에** 붙입니다.
    content, tables, _ = inject_table_placeholders(md)
    trace.table_count = len(tables)

    trace.add("docstruct.tables.markdown", "표 블록 placeholder 삽입",
              f"<table N> {len(tables)}개" if tables else "표 없음")
    trace.notes = notes

    pages = _split_by_page_break(content, tables, trace, page_image_path)
    return pages, table_html


def _split_by_page_break(
    content: str,
    tables: list,
    trace: PageTrace,
    page_image_path: str | None,
) -> list[PageContent]:
    """쪽 표식으로 본문을 페이지로 가른다.

    입력:
        content          표 placeholder 가 들어간 본문
        tables           문서 전체 표 목록
        trace            추출 경로 기록 (페이지마다 공유)
        page_image_path  미리보기 이미지 (첫 쪽에만 붙인다)
    출력: PageContent 목록
    비고:
        HWP 는 렌더링 시점에 쪽이 정해져 파일에 페이지 경계가 없습니다.
        여기서 쓰는 것은 **명시적 쪽나눔(Ctrl+Enter)과 구역 구분** 뿐이라
        인쇄된 쪽 번호와 일치하지 않습니다. 그래서 page_no_kind 를
        'document' 로 둬서 물리 쪽이 아님을 드러냅니다.

        표는 통번호를 유지한 채 해당 쪽으로 나눠 담습니다 — 번호를 다시
        매기면 본문의 `<table N>` 과 어긋납니다.
    """
    from docstruct.converters.hwp.hwp5tree import PAGE_BREAK

    chunks = [c.strip() for c in content.split(PAGE_BREAK)]
    chunks = [c for c in chunks if c]
    if len(chunks) <= 1:
        return [
            PageContent(page_no=1, page_no_kind="document", content=content,
                        tables=tables, trace=trace,
                        page_image_path=page_image_path)
        ]

    by_id = {t.id: t for t in tables}
    pages: list[PageContent] = []
    for index, chunk in enumerate(chunks, start=1):
        nums = [int(n) for n in re.findall(r"<table (\d+)>", chunk)]
        page_tables = [by_id[make_table_id(n)] for n in nums if make_table_id(n) in by_id]
        pages.append(
            PageContent(
                page_no=index,
                page_no_kind="document",
                content=chunk,
                tables=page_tables,
                # 페이지마다 **독립된** trace 를 준다. 같은 객체를 공유하면
                # 이후 단계가 페이지별로 남기는 기록이 한 리스트에 쌓이고,
                # 그 리스트가 페이지 수만큼 직렬화된다 — 72쪽 문서에서
                # 203단계가 72번 복제돼 JSON 의 85%(2.5MB)가 중복이었다.
                # 게다가 1쪽 기록과 72쪽 기록을 구분할 수 없었다.
                trace=_clone_trace(trace, table_count=len(page_tables)),
                page_image_path=page_image_path if index == 1 else None,
            )
        )
    _log.info("쪽 나눔 표식으로 %d쪽으로 나눴습니다", len(pages))
    return pages


def _clone_trace(trace: PageTrace, *, table_count: int) -> PageTrace:
    """분할 전까지의 기록을 복사한 새 PageTrace 를 만든다.

    입력: trace — 분할 전 trace, table_count — 이 쪽의 표 개수
    출력: steps 를 복사한 독립 PageTrace
    비고:
        추출까지의 기록(어느 경로로 파싱했는지)은 모든 쪽에 공통이라
        복사해 남기고, 이후 단계는 쪽마다 따로 쌓이게 한다.
    """
    clone = PageTrace(
        extractor=trace.extractor,
        text_source=trace.text_source,
        ocr_ratio=trace.ocr_ratio,
        table_count=table_count,
    )
    clone.steps = list(trace.steps)
    return clone


def _apply_preview(
    hwp_path: str,
    body_markdown: str,
    image_dir: str | Path | None,
    trace: PageTrace,
    notes: list[str],
) -> tuple[str, str | None]:
    """미리보기 스트림으로 폴백 결과를 보강한다.

    입력:
        hwp_path       HWP 경로
        body_markdown  olefile 로 뽑은 평문 markdown
        image_dir      미리보기 이미지 저장 위치
        trace, notes   기록 대상 (제자리 갱신)
    출력: (사용할 markdown, 페이지 이미지 경로 또는 None)
    비고:
        PrvText 는 1,023자에서 잘리고 PrvImage 는 첫 페이지뿐이다. 긴 문서에
        쓰면 앞부분만 살고 나머지가 없는 것이 되므로, 커버리지가 충분할 때만
        적용한다. 부족하면 아무것도 바꾸지 않는다.
    """
    prv = preview.read_prv_text(hwp_path)
    if not prv:
        return body_markdown, None

    ratio = preview.coverage(prv, body_markdown)
    if ratio < preview.MIN_COVERAGE:
        trace.add("converters.hwp.preview", "미리보기 커버리지 부족",
                  f"{ratio:.0%} — 본문 앞부분만 담겨 사용하지 않음",
                  status="warn")
        return body_markdown, None

    md = preview.to_markdown(prv)
    if not md:
        return body_markdown, None

    trace.add("converters.hwp.preview", "PrvText 로 표 구조 복원",
              f"커버리지 {ratio:.0%}")
    notes.append(
        f"미리보기(PrvText)로 표 셀 경계를 복원했습니다 — 커버리지 {ratio:.0%}"
    )

    image_path = None
    if image_dir is not None:
        image_path = preview.save_preview_image(
            hwp_path, image_dir, Path(hwp_path).stem
        )
        if image_path:
            trace.add("converters.hwp.preview", "PrvImage 저장",
                      "표 재추출 근거로 사용 (첫 페이지)")
    return md, image_path
