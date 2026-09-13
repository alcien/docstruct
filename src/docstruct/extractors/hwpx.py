"""HWPX → PageContent.

입력:
    .hwpx 경로

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
import re
from pathlib import Path

from docstruct.text.korean_text import normalize_korean_text
from docstruct.models import ImageInfo, PageContent, PageTrace
from docstruct.images.tags import make_image_block, open_tag
from docstruct.tables.markdown import inject_table_placeholders

_log = logging.getLogger(__name__)

#: 숨은 글 사유를 사람 말로.
_HIDDEN_LABELS = {
    "tiny": "1pt 글자",
    "white": "흰 글자",
    "tiny+white": "1pt+흰 글자",
    "field": "누름틀 잔재",
}


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


_IMAGE_MARK_RE = re.compile(r"<!-- hwpx-image:([^>]+?) -->")

#: 이보다 낮으면 판독이 부실할 수 있다고 본다. OCR 권장이 300dpi 이고
#: 스캔 원본이 보통 150 안팎이다.
LOW_DPI = 150.0


def _save_images(
    hwpx_path: str,
    content: str,
    image_dir: str | Path | None,
    start_id: int = 0,
) -> tuple[str, list[ImageInfo]]:
    """본문의 그림 표식을 `<image N> … </image N>` 블록으로 바꾸고 파일로 꺼낸다.

    입력:
        hwpx_path  HWPX 경로
        content    `<!-- hwpx-image:ref -->` 표식이 든 본문
        image_dir  그림을 저장할 위치 (None 이면 저장하지 않는다)
        start_id   번호 시작값
    출력: (표식이 placeholder 로 바뀐 본문, ImageInfo 목록)
    비고:
        **본문에는 경로도 바이너리도 넣지 않는다.** placeholder 만 남기고,
        나중에 `expand_tables_and_images` 가 VLM 이 읽은 글(description /
        vlm_markdown)로 펼친다. 사람이 읽는 산출에 남아야 하는 것은
        복원된 글귀뿐이다.

        저장에 실패하거나 image_dir 이 없으면 ImageInfo 는 만들되
        `image_path` 를 비운다 — 그러면 VLM 읽기가 그 그림을 건너뛴다
        (`_should_read` 가 파일을 요구한다). 본문에서 표식만 사라져
        누락처럼 보이는 것을 막으려는 것이다.
    """
    refs = _IMAGE_MARK_RE.findall(content or "")
    if not refs:
        return content, []

    parts: dict[str, tuple[bytes, str, str]] = {}
    sizes: dict[str, tuple[float, float]] = {}
    try:
        from docstruct.converters.hwpx.hwpxtree import (
            image_display_sizes, image_parts,
        )

        parts = image_parts(hwpx_path)
        sizes = image_display_sizes(hwpx_path)
    except Exception as exc:                     # noqa: BLE001 - 본문은 이미 나왔다
        _log.warning("HWPX 그림을 꺼내지 못했습니다: %s", exc)

    target = Path(image_dir) if image_dir else None
    if target is not None:
        target.mkdir(parents=True, exist_ok=True)

    images: list[ImageInfo] = []
    counter = start_id
    for ref in refs:
        counter += 1
        # 0.4.89 — PDF 경로와 **같은 모양**을 쓴다. 예전에는 PDF 가
        # `<!-- image_1 -->`, HWPX 가 `<!-- image 1 -->` 로 갈려 있어
        # 같은 것을 두 벌로 찾아야 했다.
        placeholder = open_tag(counter)
        data, mime, suffix = parts.get(ref, (None, None, ".png"))
        saved: str | None = None
        if data and target is not None:
            out = target / f"{Path(hwpx_path).stem}_image_{counter}{suffix}"
            try:
                out.write_bytes(data)
                saved = str(out)
            except OSError as exc:
                _log.warning("그림 저장 실패 (%s): %s", ref, exc)
        info = ImageInfo(id=f"image_{counter}", image_num=counter,
                         placeholder=placeholder,
                         image_path=saved, mime_type=mime,
                         region_kind="image")
        info.dpi = _image_dpi(data, sizes.get(ref))
        images.append(info)
        content = content.replace(f"<!-- hwpx-image:{ref} -->",
                                  make_image_block(counter), 1)
    return content, images


def _image_dpi(data: bytes | None,
               display: tuple[float, float] | None) -> float | None:
    """그림의 실제 해상도(dpi).

    입력: data — 이미지 바이트, display — 지면에 놓이는 (폭pt, 높이pt)
    출력: dpi. 못 재면 None
    비고:
        **형식과 무관하게 재야 한다.** 한글 문서에도 스캔 이미지가
        들어가고, 원본이 흐리면 VLM 이 무엇으로 읽든 한계가 있다.
        PDF 는 다시 렌더해 배율을 올릴 수 있지만 **HWPX 는 그 이미지가
        원본 자체**라 손쓸 방법이 없다 — 그래서 최소한 기록은 남긴다.

        실측(조달청·행안부 HWPX): 그림 다섯 개가 102~167dpi 였다.
        조직도(조달청 167 · 행안부 122)도 그 안에 있다.
    """
    if not data or not display or display[0] <= 0:
        return None
    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(data)) as image:
            pixels = image.width
    except Exception:                            # noqa: BLE001 - 못 재면 비운다
        return None
    return pixels / (display[0] / 72.0)


def extract_hwpx_pages(
    hwpx_path: str,
    *,
    image_dir: str | Path | None = None,
) -> list[PageContent]:
    """HWPX 파일을 구조화한다.

    입력: hwpx_path — HWPX 파일 경로, image_dir — 그림 저장 위치
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
    content, images = _save_images(hwpx_path, content, image_dir)

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

    # **숨은 글을 결과물에 밝힌다** (0.4.74). 한글 문서는 지면에 보이지
    # 않는 글을 담는다 — 1pt 글자, 흰 글자, 누름틀 잔재. 본문에 실으면
    # 없는 글이 생기므로 빼는 것이 맞지만, **뺐다는 사실이 남지 않으면**
    # HWPX 와 PDF 의 열 수가 왜 다른지 짚을 수 없다.
    #
    # 실측(조달청 70쪽): 프로그램 코드 `53405` 가 **1pt 이면서 흰 글씨**로
    # 표의 좁은 열 하나를 차지하고 있었다. PDF 로 뽑으면 그 열이 보이지
    # 않으니 쪽 맞춤이 어긋나는데, 원인을 결과물에서 알 길이 없었다.
    hidden = {}
    anchors = {}
    if source == "hwpx-tree":
        try:
            anchors = hwpxtree.anchor_notes()
        except Exception as exc:             # noqa: BLE001
            _log.debug("앵커 요약을 읽지 못했습니다: %s", exc)
        try:
            hidden = hwpxtree.hidden_notes()
        except Exception as exc:             # noqa: BLE001 - 본문은 이미 나왔다
            _log.debug("숨은 글 요약을 읽지 못했습니다: %s", exc)
    if hidden:
        total = sum(v["count"] for v in hidden.values())
        detail_parts = []
        for why, info in sorted(hidden.items()):
            shown = ", ".join(s[:14] for s in info["samples"][:3])
            detail_parts.append(f"{_HIDDEN_LABELS.get(why, why)} {info['count']}개"
                                + (f"({shown})" if shown else ""))
        trace.add(
            "converters.hwpx.hwpxtree", "숨은 글 제외",
            f"{total}개 — " + " · ".join(detail_parts)
            + ". 지면에 보이지 않아 본문에서 뺐습니다. 표에서는 열을 "
              "차지하므로 PDF 와 열 수가 다를 수 있습니다",
            status="warn")
    trace.add("docstruct.tables.markdown", "표 블록 placeholder 삽입",
              f"<table N> {len(tables)}개" if tables else "표 없음")
    if images:
        # **해상도가 낮으면 남긴다.** HWPX 는 그 이미지가 원본 자체라
        # 다시 렌더해 배율을 올릴 수 없다 — 판독이 부실할 때 원인을
        # 결과물에서 짚을 수 있어야 한다. 실측: 조달청·행안부 그림이
        # 102~167dpi 였고 조직도도 그 안에 있다.
        low = [i for i in images if i.dpi is not None and i.dpi < LOW_DPI]
        if low:
            trace.add("docstruct.extractors.hwpx", "그림 해상도 낮음",
                      " · ".join(f"{i.id} {i.dpi:.0f}dpi" for i in low)
                      + f" (기준 {LOW_DPI}dpi) — 판독이 부실하면 이것이 원인일 수 있습니다",
                      status="warn")
        saved = sum(1 for i in images if i.image_path)
        trace.add("docstruct.extractors.hwpx", "그림 추출",
                  f"{len(images)}개 (파일로 저장 {saved}개) — "
                  "내용은 VLM 이 읽어 본문에 채운다",
                  status="ok" if saved == len(images) else "warn")
    trace.picture_count = len(images)

    if anchors.get("reordered"):
        # **표가 제 캡션보다 먼저 나오던 것을 바로잡았다** (0.4.81).
        # `treatAsChar=0` 인 표는 문단에 매달린 객체라 지면에서는 문단 글
        # 아래에 그려지는데 XML 로는 문단 앞쪽에 앉아 있다.
        trace.add(
            "converters.hwpx.hwpxtree", "표 차례 바로잡음",
            f"문단에 매달린 표 {anchors.get('anchored', 0)}개 중 "
            f"{anchors['reordered']}개를 문단 글 뒤로 옮겼습니다 "
            f"(글자 취급 {anchors.get('inline', 0)}개는 그대로)")

    page = PageContent(page_no=1, page_no_kind="document", content=content,
                       tables=tables, images=images, trace=trace)
    page.table_anchors = anchors or None
    page.hidden_text = hidden or None
    return [page]
