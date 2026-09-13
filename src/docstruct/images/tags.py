"""`<image N> ... </image N>` 블록 생성·파싱·동기화.

입력:
    본문 markdown · 그림 번호 · 설명 · 읽은 내용

역할:
    본문 안에서 그림이 차지하는 구간을 **여는 태그와 닫는 태그로 감싼다.**
    안쪽은 다시 두 칸으로 나뉜다 — 파서가 준 **설명**과 판독이 읽은
    **내용**. 표의 `<table N> ... </table N>`(tables.tags)과 같은 계약이다.
호출부:
    docstruct.images.picture      PDF 그림 블록 생성
    docstruct.extractors.hwpx     HWPX 그림 블록 생성
    docstruct.images.vlm_read     읽은 내용 되돌리기
    docstruct.output.content      본문 펼치기
    docstruct.output.preview      표시용 파싱
출력:
    문자열 (본문 markdown) 또는 구간 정보

왜 닫는 태그가 필요한가 (0.4.89)
-------------------------------
예전에는 여는 표식 하나뿐이었다 — `<!-- image_1 -->`. 그래서 **그림의 몫이
어디서 끝나는지 아무도 몰랐다.**

    <!-- image_1 -->

    조직도: 청장 아래 차장, 그 아래 3국 9과      ← 그림에서 읽은 것
    위 조직은 2027년부터 적용된다                ← 원래 본문

    ↑ 이 둘을 가르는 규칙이 없다. 본문만 뽑으려 해도, 그림만 다시 읽히려
      해도, 어디까지가 그림인지 셀 수가 없었다.

게다가 표식 모양이 경로마다 달랐다 — PDF 는 `<!-- image_1 -->`(밑줄),
HWPX 는 `<!-- image 1 -->`(공백). 같은 것을 두 벌로 찾아야 했다.

이제 구간이 닫힌다:

    <image 1>
    <image-desc 1>조직도</image-desc 1>
    <image-read 1>
    | 부서 | 인원 |
    | --- | --- |
    </image-read 1>
    </image 1>

    위 조직은 2027년부터 적용된다                ← 밖이므로 본문이다

**설명과 내용을 나눈 이유.** 둘은 출처가 다르다. 설명은 파서(Docling
picture description·HWPX alt)가 준 것이고, 내용은 VLM 이 그림을 보고 읽은
것이다. 섞어 두면 "이 글자가 원본에 있던 것인가, 모델이 지어낸 것인가" 를
나중에 가릴 수 없다. 그림 판독의 신뢰도를 따지려면 이 구분이 필요하다.

**옛 표식도 읽는다.** `normalize_image_blocks` 가 두 옛 모양을 새 블록으로
올린다. 이미 나간 산출물(document.json)을 다시 태워도 깨지지 않는다.
"""
from __future__ import annotations

import re

#: 그림 블록 전체 — `<image 3> … </image 3>`.
IMAGE_BLOCK_RE = re.compile(r"<image (\d+)>\s*(.*?)\s*</image \1>", re.DOTALL)
#: 여는 태그만 (짝이 깨진 본문에서 자리를 찾을 때).
IMAGE_OPEN_RE = re.compile(r"<image (\d+)>")
#: 안쪽 두 칸.
IMAGE_DESC_RE = re.compile(r"<image-desc (\d+)>\s*(.*?)\s*</image-desc \1>", re.DOTALL)
IMAGE_READ_RE = re.compile(r"<image-read (\d+)>\s*(.*?)\s*</image-read \1>", re.DOTALL)

#: 0.4.88 이전 표식 — PDF 는 `<!-- image_1 -->`, HWPX 는 `<!-- image 1 -->`.
LEGACY_MARK_RE = re.compile(r"<!--\s*image[ _](\d+)\s*-->")


def make_image_id(num: int) -> str:
    """그림 id 문자열.

    입력: num — 그림 번호
    출력: 'image_3' 형태
    """
    return f"image_{num}"


def open_tag(num: int) -> str:
    """여는 태그.

    입력: num — 그림 번호
    출력: '<image 3>'
    """
    return f"<image {num}>"


def close_tag(num: int) -> str:
    """닫는 태그.

    입력: num — 그림 번호
    출력: '</image 3>'
    """
    return f"</image {num}>"


def make_image_block(num: int, description: str | None = None,
                     read: str | None = None) -> str:
    """그림 블록 문자열을 만든다.

    입력: num — 그림 번호, description — 파서가 준 설명, read — 판독이 읽은 내용
    출력: `<image N> … </image N>` 문자열
    비고:
        빈 칸은 넣지 않는다 — 설명도 내용도 없으면 여닫는 태그만 남는다.
        그래도 **자리는 남는다**: 그림이 거기 있었다는 사실과 번호는
        지워지지 않는다.
    """
    inner: list[str] = []
    text = (description or "").strip()
    if text:
        inner.append(f"<image-desc {num}>{text}</image-desc {num}>")
    body = (read or "").strip()
    if body:
        inner.append(f"<image-read {num}>\n{body}\n</image-read {num}>")
    middle = ("\n" + "\n".join(inner) + "\n") if inner else "\n"
    return f"{open_tag(num)}{middle}{close_tag(num)}"


def parse_image_block(content: str, num: int) -> dict[str, str] | None:
    """본문에서 그림 블록을 찾아 설명과 내용을 갈라 낸다.

    입력: content — 본문, num — 그림 번호
    출력: {'description': …, 'read': …, 'raw': …} 또는 없으면 None
    비고:
        `raw` 는 태그 안쪽 전체다. 옛 산출물처럼 두 칸으로 나뉘지 않은
        글이 들어 있을 수 있어, 그때는 `description`·`read` 가 비고
        `raw` 만 찬다 — 무엇도 버리지 않는다.
    """
    match = re.search(rf"<image {num}>\s*(.*?)\s*</image {num}>", content or "",
                      re.DOTALL)
    if match is None:
        return None
    raw = match.group(1)
    desc = IMAGE_DESC_RE.search(raw)
    read = IMAGE_READ_RE.search(raw)
    return {
        "description": desc.group(2).strip() if desc else "",
        "read": read.group(2).strip() if read else "",
        "raw": raw.strip(),
    }


def block_span(content: str, num: int) -> tuple[int, int] | None:
    """그림 블록이 본문에서 차지하는 구간.

    입력: content — 본문, num — 그림 번호
    출력: (시작, 끝) 또는 없으면 None
    """
    match = re.search(rf"<image {num}>.*?</image {num}>", content or "", re.DOTALL)
    return (match.start(), match.end()) if match else None


def sync_image_block(content: str, num: int, description: str | None = None,
                     read: str | None = None) -> str:
    """본문의 그림 블록을 새 값으로 맞춘다.

    입력: content — 본문, num — 그림 번호, description·read — 새 값
    출력: 바뀐 본문 (블록이 없으면 그대로)
    비고:
        주지 않은 칸(None)은 **건드리지 않는다.** VLM 이 내용만 채울 때
        파서가 준 설명이 지워지면 안 된다. 빈 문자열을 주면 지운다는 뜻이다.
    """
    got = parse_image_block(content, num)
    if got is None:
        return content or ""
    new_desc = got["description"] if description is None else description
    new_read = got["read"] if read is None else read
    block = make_image_block(num, new_desc, new_read)
    return re.sub(rf"<image {num}>.*?</image {num}>",
                  lambda _m: block, content or "", count=1, flags=re.DOTALL)


def strip_image_blocks(content: str) -> str:
    """그림 블록을 통째로 걷어낸 본문.

    입력: content — 본문
    출력: 그림 구간이 빠진 본문
    비고:
        "원래 본문만" 을 원하는 곳(쪽 맞춤의 본문 눈금 등)이 쓴다. 닫는
        태그가 생기기 전에는 할 수 없던 일이다 — 그림이 어디서 끝나는지
        몰랐으므로 읽은 내용이 본문에 섞여 눈금을 흔들었다.
    """
    return re.sub(r"<image \d+>.*?</image \d+>\s*", "", content or "",
                  flags=re.DOTALL).strip()


def normalize_image_blocks(content: str) -> str:
    """옛 표식을 새 블록으로 올린다.

    입력: content — 본문 (옛 `<!-- image_1 -->` 이 있을 수 있다)
    출력: 모든 그림이 여닫는 블록으로 감싸인 본문
    비고:
        0.4.88 이전 산출물을 다시 태울 때를 위한 것이다. 옛 표식 **바로
        뒤에 붙어 있던 글**은 그 그림에서 읽은 것이므로 `<image-read>` 로
        옮긴다 — 다음 빈 줄까지가 아니라 **다음 표식·표 블록·제목까지**를
        그림의 몫으로 본다. 판독 결과가 여러 문단일 수 있기 때문이다.
    """
    text = content or ""
    if not LEGACY_MARK_RE.search(text):
        return text

    marks = list(LEGACY_MARK_RE.finditer(text))
    out: list[str] = []
    cursor = 0
    stop = re.compile(r"<!--\s*image[ _]\d+\s*-->|<table \d+>|^#{1,6}\s+",
                      re.MULTILINE)
    for mark in marks:
        num = int(mark.group(1))
        out.append(text[cursor:mark.start()])
        tail_from = mark.end()
        nxt = stop.search(text, tail_from)
        tail_to = nxt.start() if nxt else len(text)
        out.append(make_image_block(num, None, text[tail_from:tail_to].strip()))
        out.append("\n\n")
        cursor = tail_to
    out.append(text[cursor:])
    return "".join(out).strip()


def replace_image_block(content: str, num: int, body: str) -> str:
    """그림 블록 전체를 펼친 글로 바꾼다.

    입력: content — 본문, num — 그림 번호, body — 자리에 넣을 글
    출력: 블록이 글로 바뀐 본문
    비고:
        산출물(document.md)을 만들 때 쓴다. 태그는 **파이프라인 안에서만**
        의미가 있고, 사람이 읽는 문서에는 남기지 않는다. 표의
        `replace_block_with_markdown` 과 같은 역할이다.
    """
    return re.sub(rf"<image {num}>.*?</image {num}>",
                  lambda _m: body, content or "", count=1, flags=re.DOTALL)
