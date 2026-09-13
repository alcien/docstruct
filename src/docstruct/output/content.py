"""본문 placeholder 를 실제 내용으로 되돌리기.

입력:
    PageContent

역할:
    `<table N>` 블록과 `<image N>` 블록을 실제 내용으로 바꾼 완전한 본문을
    만든다. 목차 추출처럼 전체 텍스트가 필요한 곳에서 쓴다.

    **표는 표식을 지우고 그림은 남긴다** (0.5.9). 복원한 표는 원문에 있던
    값을 다시 세운 것이라 본문에 녹아도 되지만, 그림 설명은 VLM 이 스스로
    쓴 글이라 원문 문장과 섞이면 구별할 수 없다.
호출부:
    docstruct.outline.builder
출력:
    placeholder 가 모두 치환된 본문 문자열
"""
from __future__ import annotations

from typing import Iterable

from docstruct.models import ImageInfo, TableInfo
from docstruct.images.tags import replace_image_block
from docstruct.tables.tags import replace_block_with_markdown


def expand_tables_and_images(
    content: str,
    tables: Iterable[TableInfo],
    images: Iterable[ImageInfo],
) -> str:
    """본문의 표·이미지 placeholder 를 실제 내용으로 치환한다.

    입력:
        content  본문 markdown
        tables   TableInfo 목록
        images   ImageInfo 목록
    출력: 표는 GFM 으로, 이미지는 설명 텍스트로 바뀐 본문
    """
    expanded = content or ""
    for table in tables:
        md = table.markdown.strip() if table.markdown else ""
        if md:
            expanded = replace_block_with_markdown(expanded, table.table_num, md)
    for image in images:
        num = getattr(image, "image_num", None)
        if num is not None and f"<image {num}>" in expanded:
            expanded = replace_image_block(expanded, num,
                                           _image_block_text(image, num))
            continue
        # 옛 산출물(0.4.88 이전)에는 여는 표식만 있다.
        expanded = expanded.replace(
            image.placeholder, _image_text(image, expanded))
    return expanded.strip()


def keep_image_marks() -> bool:
    """산출물에서 그림 구간 표식을 남길지 (0.5.9).

    입력: 없음 (`DOCSTRUCT_IMAGE_MARKS`)
    출력: 남기면 True (기본)
    비고:
        **표와 그림은 다르다.** 표를 복원한 결과는 원문에 있던 값을 다시
        세운 것이라 본문에 그대로 녹아도 된다. 그림 설명은 아니다 —
        VLM 이 `조직도로 보입니다` 처럼 **스스로 쓴 글**이고, 그것이 본문에
        표식 없이 섞이면 원문에 있던 문장과 구별할 수 없다.

        읽는 사람도, 나중에 이 문서를 근거로 답을 만드는 쪽도 "이 문장이
        원본에 있었나" 를 물을 수 있어야 한다. 그래서 그림만 구간을 남긴다.

        `DOCSTRUCT_IMAGE_MARKS=false` 로 끄면 예전처럼 펼쳐진다.
    """
    import os

    return os.environ.get("DOCSTRUCT_IMAGE_MARKS", "").strip().lower() not in (
        "0", "false", "no", "off")


def _image_block_text(image: ImageInfo, num: int) -> str:
    """산출물에 넣을 그림 구간 전체.

    입력: image — ImageInfo, num — 블록 번호
    출력: 표식을 포함한 문자열 (끄면 본문만)
    비고:
        표식을 남길 때는 JSON(`pages[].content`)과 **같은 모양**을 쓴다.
        두 산출물이 다른 표기를 쓰면 하나를 기준으로 짠 도구가 다른
        하나에서 깨진다.
    """
    body = _image_body(image)
    if not keep_image_marks():
        return body
    return f"<image {num}>\n{body}\n</image {num}>"


def _image_body(image: ImageInfo) -> str:
    """그림 블록 자리에 넣을 글 (0.4.89).

    입력: image — ImageInfo
    출력: 설명·읽은 내용을 합친 글, 둘 다 없으면 왜 비었는지 한 줄
    비고:
        블록에는 이미 설명과 읽은 내용이 갈려 담겨 있다. 펼칠 때는 사람이
        읽을 한 덩어리로 합치되, **없으면 없다고 적는다** — 그림이 있었다는
        사실이 사라지면 조직도가 통째로 빠진 것처럼 읽힌다.
    """
    read = (getattr(image, "vlm_markdown", None) or "").strip()
    desc = (getattr(image, "description", None) or "").strip()
    parts = []
    if desc:
        parts.append(desc)
    if read:
        # **누가 쓴 글인지 밝힌다** (0.5.9). 파서가 준 설명과 VLM 이 읽은
        # 내용은 출처가 다른데, 본문에 나란히 놓이면 구별이 안 된다.
        parts.append(f"> _(그림 `{image.id}` 판독 — 모델이 읽은 내용)_\n\n{read}")
    if parts:
        return "\n\n".join(parts)
    reason = ("VLM 미실행 — LLM 을 설정하면 내용을 읽어 채웁니다"
              if getattr(image, "image_path", None)
              else "그림 파일이 저장되지 않아 읽지 못했습니다")
    return f"> _(그림 `{image.id}` — {reason})_"


def _image_text(image: ImageInfo, content: str = "") -> str:
    """그림 자리에 넣을 글.

    입력: image — ImageInfo, content — 본문 (이미 실려 있는지 확인용)
    출력: VLM 이 읽은 글, 없으면 **눈에 보이는** 안내 한 줄
    비고:
        예전에는 읽은 것이 없으면 placeholder(`<!-- image N -->`)를 그대로
        두었다. 그것은 HTML 주석이라 markdown 으로 보면 **아무것도 보이지
        않는다** — 그림이 있었다는 사실조차 사라져, 조직도가 통째로 빠진
        것처럼 읽혔다.

        읽은 것이 있으면 그 글을, 없으면 무엇이 왜 비었는지를 남긴다.
        원본 확인용으로 placeholder 도 함께 둔다(주석이라 렌더에는 영향이
        없고, 재추출 결과를 되돌릴 때 앵커로 쓰인다).
    """
    body = (getattr(image, "vlm_markdown", None)
            or getattr(image, "description", None) or "").strip()
    if body:
        # **두 번 넣지 않는다.** 파이프라인의 `vlm_read` 가 읽은 글을
        # 이미 `page.content` 의 placeholder 뒤에 넣어 둔다. 여기서 또
        # 넣으면 같은 조직도가 두 번 나온다.
        if body in (content or ""):
            return image.placeholder
        return f"{image.placeholder}\n\n{body}"
    reason = ("VLM 미실행 — LLM 을 설정하면 내용을 읽어 채웁니다"
              if getattr(image, "image_path", None)
              else "그림 파일이 저장되지 않아 읽지 못했습니다")
    return f"{image.placeholder}\n\n> _(그림 `{image.id}` — {reason})_"
