"""Docling PictureItem → 이미지 파일 + 본문 placeholder.

입력:
    Docling PictureItem

역할:
    그림을 PNG 로 저장하고, 본문에는 참조 주석을 남기며, LLM 설명이
    있으면 메타에 함께 담는다.
호출부:
    docstruct.extractors.pdf.extract_pdf_pages
출력:
    (본문에 넣을 문자열, ImageInfo)
"""
from __future__ import annotations

import base64
import io
import logging
import re
from pathlib import Path

from docstruct.converters.pdf.picture_inspect import get_picture_description_text
from docstruct.models import ImageInfo

_log = logging.getLogger(__name__)

_DATA_URI_RE = re.compile(
    r"!\[[^\]]*\]\(data:(image/[^;]+);base64,([^)]+)\)",
    re.DOTALL,
)

_EXT_BY_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


def _parse_data_uri(md: str) -> tuple[bytes | None, str | None]:
    """markdown 안의 data URI 에서 이미지를 꺼낸다.

    입력: md — `![](data:image/png;base64,...)` 형태 문자열
    출력: (바이트, 확장자). 형식이 아니면 (None, None)
    """
    match = _DATA_URI_RE.search(md)
    if not match:
        return None, None
    try:
        return base64.b64decode(match.group(2)), match.group(1)
    except Exception:
        return None, None


def _image_bytes(item, doc) -> tuple[bytes | None, str | None]:
    """PictureItem 에서 이미지 바이트를 얻는다.

    입력: item — PictureItem, doc — DoclingDocument
    출력: (바이트, 확장자). 얻지 못하면 (None, None)
    """
    image_ref = getattr(item, "image", None)
    if image_ref is not None:
        uri = getattr(image_ref, "uri", None)
        if isinstance(uri, str) and uri.startswith("data:"):
            data, mime = _parse_data_uri(f"![]({uri})")
            if data:
                return data, mime

        pil = getattr(image_ref, "pil_image", None)
        if pil is not None:
            buf = io.BytesIO()
            pil.save(buf, format="PNG")
            return buf.getvalue(), "image/png"

    # fallback: markdown export 안의 data URI
    try:
        raw_md = item.export_to_markdown(doc) or ""
    except Exception:
        raw_md = ""
    return _parse_data_uri(raw_md)


def picture_to_block(
    item,
    doc,
    *,
    image_id: str,
    image_dir: str | Path | None = None,
    source_path: str | Path | None = None,
    page_no: int | None = None,
    bbox: dict | None = None,
) -> tuple[str, ImageInfo]:
    """그림을 저장하고 본문 블록과 메타를 만든다.

    입력:
        item       Docling PictureItem
        doc        DoclingDocument
        image_dir  저장 위치. None 이면 파일로 저장하지 않음
        index      그림 순번 (파일명에 사용)
    출력:
        block  본문에 넣을 placeholder 문자열
        info   ImageInfo (경로·설명·페이지). 이미지를 못 얻으면 None
    """
    # **여닫는 블록으로 감싼다** (0.4.89). placeholder 는 여는 태그이며
    # `<image N>` 이다 — 표의 `<table N>` 과 같은 계약이다. 옛 모양
    # (`<!-- image_1 -->`)은 그림의 몫이 어디서 끝나는지 말하지 못했다.
    from docstruct.images.tags import make_image_block, open_tag

    image_num = int(image_id.rsplit("_", 1)[-1]) if "_" in image_id else 1
    placeholder = open_tag(image_num)
    description = get_picture_description_text(item)

    image_path: str | None = None
    mime: str | None = None

    if image_dir is not None:
        data, mime = _image_bytes(item, doc)
        # 원본 화소 우선 — Docling 내보내기는 images_scale 로 **다시 렌더**한
        # 것이라 원본에 심긴 비트맵보다 작을 수 있다 (실측: 조달청 59쪽
        # 그래프 3139×947 → 377×113, 8배 손실). 초해상으로 되살릴 것이
        # 아니라 애초에 버리지 않는다.
        if source_path and page_no and bbox:
            from docstruct.images.native_image import extract_region_png

            got = extract_region_png(source_path, page_no, bbox)
            if got:
                better, origin = got
                if not data or len(better) > len(data):
                    data, mime = better, "image/png"
                    _log.debug("%s 이미지를 %s 경로로 받았습니다", image_id, origin)
        if data:
            out_dir = Path(image_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            ext = _EXT_BY_MIME.get(mime or "", ".png")
            path = out_dir / f"{image_id}{ext}"
            try:
                path.write_bytes(data)
                image_path = str(path.resolve())
            except OSError as exc:
                _log.warning("이미지 저장 실패 %s: %s", path, exc)

    block = make_image_block(image_num, description)

    return block, ImageInfo(
        id=image_id,
        image_num=image_num,
        placeholder=placeholder,
        description=description or None,
        image_path=image_path,
        mime_type=mime,
    )
