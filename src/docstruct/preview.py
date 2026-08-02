"""PageDocument → 노트북 표시.

역할:
    구조화 결과를 Jupyter 에서 HTML 로 보여준다. 표는 markdown 렌더러가
    그리도록 GFM 그대로 두고, 판정·처리 경로는 표로 정리해 보여준다.
호출부:
    notebooks/preview.ipynb, notebooks/preview_colab.ipynb
출력:
    없음 (IPython.display 로 화면에 출력)
"""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

from docstruct.models import (
    IMAGE, TABLE, TEXT, PageContent, PageDocument, TableInfo, source_label,
)
from docstruct.tables.tags import TABLE_BLOCK_RE

# ── 판정별 색상 -------------------------------------------------------------

_TYPE_COLOR = {
    TABLE: "#2563eb",
    TEXT: "#64748b",
    IMAGE: "#7c3aed",
}
_QUALITY_COLOR = {
    "sufficient": "#16a34a",
    "insufficient": "#d97706",
    "wrong": "#dc2626",
}


def _badge(text: str, color: str) -> str:
    return (
        f'<span style="background:{color};color:#fff;border-radius:4px;'
        f'padding:1px 7px;font-size:11px;font-weight:600;">{html.escape(text)}</span>'
    )


def _pre(text: str, *, bg: str = "#f8fafc") -> str:
    return (
        f'<pre style="background:{bg};border:1px solid #e2e8f0;border-radius:6px;'
        f'padding:10px;font-size:12px;line-height:1.45;overflow-x:auto;'
        f'white-space:pre;margin:4px 0;">{html.escape(text or "(비어 있음)")}</pre>'
    )


# ── 본문 markdown 가공 ------------------------------------------------------

_IMG_PLACEHOLDER_RE = re.compile(r"<!--\s*([\w.]+)\s*-->")


def content_for_markdown(page: PageContent) -> str:
    """본문을 노트북 표시용으로 다듬는다.

    입력: page — PageContent
    출력: `<table N>` 태그를 라벨로 바꾼 markdown (표 자체는 렌더되도록 유지)
    """
    content = page.content or ""

    titles = {t.table_num: (t.llm_title or "") for t in page.tables}

    def _table_repl(m: re.Match[str]) -> str:
        num = int(m.group(1))
        inner = m.group(2).strip()
        title = titles.get(num, "")
        label = f"▦ table {num}" + (f" — {title}" if title else "")
        return f"\n**{label}**\n\n{inner}\n"

    content = TABLE_BLOCK_RE.sub(_table_repl, content)

    descriptions = {i.placeholder: i for i in page.images}

    def _img_repl(m: re.Match[str]) -> str:
        info = descriptions.get(m.group(0))
        if info is None:
            return f"**🖼 {m.group(1)}**"
        desc = f" — {info.description}" if info.description else ""
        return f"**🖼 {info.id}**{desc}"

    return _IMG_PLACEHOLDER_RE.sub(_img_repl, content)


# ── 요약 --------------------------------------------------------------------

def summary_html(doc: PageDocument) -> str:
    """문서 요약 표를 HTML 로 만든다.

    입력: doc — PageDocument
    출력: HTML 문자열
    """
    tables = [t for _, t in doc.all_tables()]
    images = doc.all_images()

    by_type: dict[str, int] = {}
    for t in tables:
        by_type[t.content_type or "미평가"] = by_type.get(t.content_type or "미평가", 0) + 1
    filled = sum(1 for t in tables if t.was_filled)
    empty = [str(p.page_no) for p in doc.pages if not (p.content or "").strip()]

    rows = [
        ("파일", f"{html.escape(doc.filename)} <code>{doc.source_format}</code>"),
        ("페이지", str(len(doc.pages))),
        ("본문 길이", f"{doc.char_count():,}자"),
        (
            "표",
            f"{len(tables)}개 "
            + " ".join(
                _badge(f"{k} {v}", _TYPE_COLOR.get(k, "#94a3b8"))
                for k, v in sorted(by_type.items())
            ),
        ),
        ("표 재추출", f"{filled}개"),
        ("이미지", f"{len(images)}개"),
    ]
    if empty:
        rows.append(("⚠ 빈 페이지", ", ".join(empty)))
    if doc.failed_pages:
        rows.append((
            "⚠ 파싱 실패",
            f'<span style="color:#dc2626;">{", ".join(map(str, doc.failed_pages))}</span>'
            ' <span style="color:#64748b;font-size:11px;">— 결과에서 빠짐. '
            'DOCLING_PDF_BACKEND=pypdfium2 또는 전면 OCR 시도</span>',
        ))

    body = "".join(
        f'<tr><td style="padding:4px 14px 4px 0;color:#64748b;white-space:nowrap;">{k}</td>'
        f'<td style="padding:4px 0;">{v}</td></tr>'
        for k, v in rows
    )
    return f'<table style="border-collapse:collapse;font-size:13px;">{body}</table>'


# ── 표 리포트 ---------------------------------------------------------------

def table_overview_html(doc: PageDocument) -> str:
    rows = doc.all_tables()
    if not rows:
        return '<p style="color:#64748b;">표가 없습니다.</p>'

    head = (
        '<tr style="background:#f1f5f9;">'
        + "".join(
            f'<th style="padding:6px 10px;text-align:left;font-size:12px;">{h}</th>'
            for h in ("ID", "페이지", "판정", "품질", "재추출", "제목", "근거")
        )
        + "</tr>"
    )

    body = []
    for page, t in rows:
        ctype = t.content_type or "-"
        quality = t.quality or "-"
        cells = [
            f"<code>{t.id}</code>",
            str(page.page_no),
            _badge(ctype, _TYPE_COLOR.get(ctype, "#94a3b8")) if ctype != "-" else "-",
            _badge(quality, _QUALITY_COLOR.get(quality, "#94a3b8")) if quality != "-" else "-",
            "✅" if t.was_filled else "—",
            html.escape(t.llm_title or "-"),
            html.escape((t.reason or "-")[:60]),
        ]
        body.append(
            '<tr style="border-bottom:1px solid #e2e8f0;">'
            + "".join(
                f'<td style="padding:5px 10px;font-size:12px;vertical-align:top;">{c}</td>'
                for c in cells
            )
            + "</tr>"
        )

    return (
        '<table style="border-collapse:collapse;width:100%;">'
        + head
        + "".join(body)
        + "</table>"
    )


def table_detail_html(page: PageContent, table: TableInfo) -> str:
    """표 하나의 재추출 전/후 비교."""
    header = (
        f'<div style="margin-top:14px;font-weight:600;font-size:13px;">'
        f"<code>{table.id}</code> · 페이지 {page.page_no}"
        + (f" · {html.escape(table.llm_title)}" if table.llm_title else "")
        + "</div>"
    )
    if table.reason:
        header += (
            f'<div style="color:#64748b;font-size:12px;margin:2px 0 4px;">'
            f"판단 근거: {html.escape(table.reason)}</div>"
        )

    if not table.was_filled:
        return header + _pre(table.markdown)

    return header + (
        '<div style="display:flex;gap:10px;align-items:flex-start;">'
        f'<div style="flex:1;min-width:0;">'
        f'<div style="font-size:11px;color:#dc2626;font-weight:600;">재추출 전</div>'
        f"{_pre(table.original_markdown or '', bg='#fef2f2')}</div>"
        f'<div style="flex:1;min-width:0;">'
        f'<div style="font-size:11px;color:#16a34a;font-weight:600;">재추출 후</div>'
        f"{_pre(table.markdown, bg='#f0fdf4')}</div>"
        "</div>"
    )


# ── display 진입점 ----------------------------------------------------------

def _notebook_hint() -> None:
    """IPython 없이 show_* 를 호출했을 때 안내한다.

    입력: 없음
    출력: 없음 (한 번만 출력)
    """
    global _HINTED
    if _HINTED:
        return
    _HINTED = True
    print(
        "[docstruct] 노트북 표시 기능에는 IPython 이 필요합니다.\n"
        "  설치: pip install \"docstruct[notebook]\"\n"
        "  또는 *_html() 함수로 문자열만 얻어 쓰세요 "
        "(summary_html, pipeline_html, trace_log_html, layout_html).",
    )


def _display(obj: Any) -> None:
    try:
        from IPython.display import display
    except ImportError:
        _notebook_hint()
        return
    display(obj)


def _html(markup: str) -> None:
    try:
        from IPython.display import HTML
    except ImportError:
        _notebook_hint()
        return
    _display(HTML(markup))


def _markdown(text: str) -> None:
    try:
        from IPython.display import Markdown
    except ImportError:
        _notebook_hint()
        return
    _display(Markdown(text))


def show_summary(doc: PageDocument) -> None:
    """문서 요약을 표시한다.

    입력: doc — PageDocument
    출력: 없음 (파일 정보·페이지 수·표/이미지 개수 표)
    """
    _html(summary_html(doc))


_STATUS_STYLE = {
    "ok":   ("#16a34a", "✓"),
    "skip": ("#94a3b8", "–"),
    "warn": ("#d97706", "!"),
    "fail": ("#dc2626", "✕"),
}


def trace_log_html(page: PageContent) -> str:
    """페이지의 순차 실행 로그를 HTML 로 만든다.

    입력: page — PageContent
    출력: 접힌 형태의 HTML 문자열
    """
    steps = page.trace.steps
    if not steps:
        return '<div style="color:#94a3b8;font-size:11.5px;">기록된 단계 없음</div>'

    rows = []
    for i, step in enumerate(steps, 1):
        color, mark = _STATUS_STYLE.get(step.status, ("#64748b", "·"))
        timing = (
            f'<span style="color:#94a3b8;">{step.duration_ms / 1000:.1f}s</span>'
            if step.duration_ms is not None
            else ""
        )
        rows.append(
            '<tr>'
            f'<td style="padding:2px 6px 2px 0;color:{color};font-weight:700;'
            f'width:14px;">{mark}</td>'
            f'<td style="padding:2px 8px 2px 0;color:#94a3b8;width:16px;">{i}</td>'
            f'<td style="padding:2px 12px 2px 0;white-space:nowrap;">'
            f'<code style="font-size:11px;color:#475569;">{html.escape(step.module)}</code></td>'
            f'<td style="padding:2px 10px 2px 0;white-space:nowrap;font-weight:600;'
            f'color:{color};">{html.escape(step.action)}</td>'
            f'<td style="padding:2px 8px 2px 0;color:#64748b;">'
            f'{html.escape(step.detail)}</td>'
            f'<td style="padding:2px 0;text-align:right;">{timing}</td>'
            '</tr>'
        )

    return (
        '<details style="margin:6px 0 10px;">'
        '<summary style="cursor:pointer;font-size:12px;color:#475569;">'
        f'처리 경로 — <code>{html.escape(page.trace.summary())}</code>'
        '</summary>'
        '<table style="border-collapse:collapse;font-size:11.5px;margin:8px 0 0 4px;'
        'width:100%;">' + "".join(rows) + "</table>"
        "</details>"
    )


def show_trace(page: PageContent) -> None:
    """페이지 하나의 실행 로그만 표시한다.

    입력: page — PageContent
    출력: 없음
    """
    _html(trace_log_html(page))


#: 안내를 한 번만 내보내기 위한 표시
_HINTED = False

_LABEL_COLOR = {
    "table": "#0891b2",
    "picture": "#7c3aed",
    "section_header": "#16a34a",
    "title": "#16a34a",
    "text": "#64748b",
    "list_item": "#64748b",
    "formula": "#d97706",
    "code": "#d97706",
}
_OUTCOME_COLOR = {"table": "#0891b2", "image": "#7c3aed",
                  "text": "#16a34a", "dropped": "#dc2626"}


def layout_html(page: PageContent) -> str:
    """페이지의 레이아웃 인식 결과를 HTML 표로 만든다.

    입력: page — PageContent
    출력: HTML 문자열. 레이아웃 정보가 없으면 안내 문구
    """
    from docstruct.layout import OUTCOME_KO, overlapping_pairs

    if not page.layout:
        return ('<div style="color:#94a3b8;font-size:11.5px;">'
                "레이아웃 정보 없음 (PDF 가 아닙니다)</div>")

    head = (
        '<tr style="background:#f1f5f9;">'
        + "".join(
            f'<th style="padding:4px 8px;text-align:left;font-size:11px;">{h}</th>'
            for h in ("#", "라벨", "좌표", "글자", "처리", "산출물", "내용")
        )
        + "</tr>"
    )

    rows = []
    for item in page.layout:
        lcolor = _LABEL_COLOR.get(item.label, "#64748b")
        ocolor = _OUTCOME_COLOR.get(item.outcome, "#64748b")
        bbox = (
            f"{item.bbox['l']:.0f},{item.bbox['t']:.0f},"
            f"{item.bbox['r']:.0f},{item.bbox['b']:.0f}"
            if item.bbox else "-"
        )
        style = "background:#fef2f2;" if item.outcome == "dropped" else ""
        cells = [
            str(item.order),
            f'<span style="color:{lcolor};font-weight:600;">{html.escape(item.label_ko)}</span>',
            f'<code style="font-size:10.5px;color:#94a3b8;">{bbox}</code>',
            str(item.char_count),
            f'<span style="color:{ocolor};">{OUTCOME_KO.get(item.outcome, item.outcome)}</span>',
            f'<code style="font-size:10.5px;">{html.escape(item.ref or "-")}</code>',
            f'<span style="color:#475569;">{html.escape(item.text)}</span>',
        ]
        rows.append(
            f'<tr style="border-bottom:1px solid #e2e8f0;{style}">'
            + "".join(f'<td style="padding:3px 8px;font-size:11px;">{c}</td>' for c in cells)
            + "</tr>"
        )

    warn = ""
    overlaps = overlapping_pairs(page.layout)
    if overlaps:
        items = "<br>".join(
            f"#{a.order} {html.escape(a.label_ko)} ↔ #{b.order} "
            f"{html.escape(b.label_ko)} (겹침 {r:.0%})"
            for a, b, r in overlaps
        )
        warn = (
            '<div style="margin-top:6px;font-size:11px;color:#b45309;">'
            f"겹치는 영역 — 같은 자리를 두 번 인식했을 수 있습니다<br>{items}</div>"
        )

    return f'<table style="border-collapse:collapse;width:100%;">{head}{"".join(rows)}</table>{warn}'


def show_layout(doc: PageDocument) -> None:
    """문서 전체의 레이아웃 인식 결과를 표시한다.

    입력: doc — PageDocument
    출력: 없음 (페이지별 인식 영역 표)
    """
    shown = False
    for page in doc.pages:
        if not page.layout:
            continue
        shown = True
        _html(f'<div style="margin:12px 0 4px;font-weight:600;">페이지 {page.page_no}</div>')
        _html(layout_html(page))
    if not shown:
        _html('<div style="color:#94a3b8;font-size:12px;">'
              "레이아웃 정보 없음 (PDF 가 아닙니다).</div>")


def show_page(
    page: PageContent,
    *,
    show_image: bool = True,
    image_width: int = 620,
    show_trace_log: bool = True,
) -> None:
    """페이지 하나를 표시한다.

    입력:
        page            PageContent
        show_image      페이지 PNG 표시 여부
        image_width     이미지 표시 폭(px)
        show_trace_log  처리 경로 로그 표시 여부
    출력: 없음 (처리 경로 · 페이지 이미지 · 본문 순으로 출력)
    """
    label = (
        f"페이지 {page.page_no}"
        if page.page_no_kind == "exact"
        else "문서 전체 (페이지 구분 없음)"
    )
    meta = f"표 {len(page.tables)} · 이미지 {len(page.images)} · {len(page.content or ''):,}자"
    _html(
        f'<div style="border-left:3px solid #2563eb;padding-left:10px;margin:16px 0 6px;">'
        f'<span style="font-weight:700;font-size:14px;">{label}</span> '
        f'<span style="color:#64748b;font-size:12px;">{meta}</span></div>'
    )

    if show_trace_log:
        _html(trace_log_html(page))
        if page.layout:
            _html(
                '<details style="margin:0 0 10px;">'
                '<summary style="cursor:pointer;font-size:12px;color:#475569;">'
                f"레이아웃 인식 — 영역 {len(page.layout)}개</summary>"
                f'<div style="margin:8px 0 0 4px;">{layout_html(page)}</div>'
                "</details>"
            )

    if show_image and page.page_image_path and Path(page.page_image_path).is_file():
        try:
            from IPython.display import Image
        except ImportError:
            _notebook_hint()
        else:
            _display(Image(filename=page.page_image_path, width=image_width))

    if not (page.content or "").strip():
        _html('<p style="color:#dc2626;">⚠ 본문이 비어 있습니다.</p>')
        return

    _markdown(content_for_markdown(page))


def show_pages(doc: PageDocument, *, limit: int | None = None, **kwargs) -> None:
    for page in doc.pages[:limit]:
        show_page(page, **kwargs)


def show_tables(doc: PageDocument, *, details: bool = True) -> None:
    """표 판정 결과와 재추출 전/후를 표시한다.

    입력: doc — PageDocument
    출력: 없음 (판정 표 + 변경된 표의 대조 표시)
    """
    _html(table_overview_html(doc))
    if not details:
        return
    for page, table in doc.all_tables():
        _html(table_detail_html(page, table))


def show_images(doc: PageDocument, *, width: int = 320) -> None:
    """추출된 이미지 목록을 표시한다.

    입력: doc — PageDocument
    출력: 없음 (썸네일과 LLM 설명)
    비고: IPython 이 없으면 안내만 출력하고 넘어간다.
    """
    try:
        from IPython.display import Image
    except ImportError:
        _notebook_hint()
        return

    items = doc.all_images()
    if not items:
        _html('<p style="color:#64748b;">추출된 이미지가 없습니다.</p>')
        return

    for page, info in items:
        _html(
            f'<div style="margin-top:12px;font-size:13px;">'
            f"<code>{info.id}</code> · 페이지 {page.page_no}"
            + (
                f'<div style="color:#64748b;font-size:12px;">{html.escape(info.description)}</div>'
                if info.description
                else '<div style="color:#dc2626;font-size:12px;">설명 없음 '
                     "(Vision LLM 미설정이거나 area threshold 미만)</div>"
            )
            + "</div>"
        )
        if info.image_path and Path(info.image_path).is_file():
            _display(Image(filename=info.image_path, width=width))
        else:
            _html('<p style="color:#94a3b8;font-size:12px;">(이미지 파일 없음)</p>')


_SRC_COLOR = {
    "text_layer": "#16a34a",
    "ocr": "#d97706",
    "mixed": "#0891b2",
    "empty": "#dc2626",       # 실제 문제 — 빨강
    "unmeasured": "#cbd5e1",  # 정보 없음 — 흐리게
    "n/a": "#cbd5e1",
    "unknown": "#cbd5e1",
}


def pipeline_html(doc: PageDocument) -> str:
    """페이지별 처리 경로 표를 HTML 로 만든다.

    입력: doc — PageDocument
    출력: HTML 문자열
    """
    cfg = "".join(
        f'<tr><td style="padding:2px 12px 2px 0;color:#64748b;">{html.escape(str(k))}</td>'
        f'<td style="padding:2px 0;"><code>{html.escape(str(v))}</code></td></tr>'
        for k, v in doc.pipeline.items()
    )

    head = (
        '<tr style="background:#f1f5f9;">'
        + "".join(
            f'<th style="padding:5px 9px;text-align:left;font-size:11.5px;">{h}</th>'
            for h in ("페이지", "추출기", "텍스트 출처", "셀", "표", "그림", "렌더", "평가", "재추출")
        )
        + "</tr>"
    )

    rows = []
    for page in doc.pages:
        t = page.trace
        cells = [
            str(page.page_no),
            f"<code>{html.escape(t.extractor)}</code>",
            _badge(source_label(t.text_source, t.ocr_ratio),
                   _SRC_COLOR.get(t.text_source, "#cbd5e1")),
            str(t.cell_count) if t.cell_count else "-",
            str(t.table_count),
            str(t.picture_count),
            "●" if t.rendered else "·",
            "●" if t.assessed else "·",
            str(len(t.refilled)) if t.refilled else "·",
        ]
        style = "background:#fef2f2;" if t.failed else ""
        rows.append(
            f'<tr style="border-bottom:1px solid #e2e8f0;{style}">'
            + "".join(
                f'<td style="padding:4px 9px;font-size:11.5px;">{c}</td>' for c in cells
            )
            + "</tr>"
        )

    notes = [(p.page_no, n) for p in doc.pages for n in p.trace.notes]
    note_html = ""
    if notes:
        note_html = (
            '<div style="margin-top:8px;font-size:11.5px;color:#b45309;">'
            + "<br>".join(f"p.{n}: {html.escape(m)}" for n, m in notes)
            + "</div>"
        )

    return (
        '<div style="font-size:12px;color:#64748b;margin-bottom:4px;">적용된 설정</div>'
        f'<table style="border-collapse:collapse;font-size:11.5px;margin-bottom:12px;">{cfg}</table>'
        f'<table style="border-collapse:collapse;width:100%;">{head}{"".join(rows)}</table>'
        + note_html
    )


def show_pipeline(doc: PageDocument) -> None:
    """페이지별 처리 경로를 표시한다.

    입력: doc — PageDocument
    출력: 없음 (적용 설정 + 페이지별 추출기·텍스트 출처·단계 수행 여부 표)
    """
    _html(pipeline_html(doc))


def show_document(doc: PageDocument, *, page_limit: int | None = None) -> None:
    """문서 전체를 표시한다 (요약 → 처리 경로 → 표 판정 → 본문).

    입력: doc — PageDocument
    출력: 없음
    """
    show_summary(doc)
    _html('<h3 style="margin:20px 0 6px;">처리 경로</h3>')
    show_pipeline(doc)
    _html('<h3 style="margin:20px 0 6px;">표 판정</h3>')
    show_tables(doc)
    _html('<h3 style="margin:20px 0 6px;">본문</h3>')
    show_pages(doc, limit=page_limit)
