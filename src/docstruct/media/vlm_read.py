"""텍스트 레이어가 없는 그림을 VLM 으로 읽는다.

역할:
    캡처 이미지로 붙인 표·조직도·흐름도는 PDF 안에 글자 좌표가 없다.
    좌표 기반 판정(`converters.pdf.region_kind`)이 IMAGE 로 가르는데,
    그렇다고 버릴 수는 없다 — 내용이 통째로 사라진다.

    저장해 둔 **그림 파일 자체**를 근거로 VLM 에 내용을 옮겨 달라고 한다.
    페이지 전체가 아니라 해당 그림만 보내므로 판독률이 높다.
호출부:
    docstruct.pipeline.build_document (표 판정 다음 단계)
출력:
    없음 (PageContent.content 와 ImageInfo.vlm_markdown 을 제자리 갱신)

그림 설명(picture description)과 다른 점
--------------------------------------
docling 의 그림 설명은 **한 문장 캡션**을 만든다("조직도를 나타낸 그림").
여기서는 **내용을 옮긴다** — 표면 GFM 표로, 도표면 계층 목록으로. 목적이
검색·인용이라 캡션으로는 쓸모가 없다.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from docstruct.core.config import get_settings
from docstruct.infrastructure.llm.client import invoke_llm, llm_api_config, llm_available
from docstruct.media.images import encode_image_file
from docstruct.models import PageContent
from docstruct.progress import ProgressBar

_log = logging.getLogger(__name__)

#: 이 크기(페이지 면적 대비) 이상인 그림만 읽는다. 로고·아이콘까지 보내면
#: 호출 수가 급증하는데 얻는 것이 없다.
MIN_AREA_RATIO = 0.03

#: 응답이 이보다 짧으면 실패로 본다 ("표를 읽을 수 없습니다" 류).
MIN_RESULT_CHARS = 20

_PROMPT = """\
첨부한 그림의 **내용을 텍스트로 옮기세요.** 그림에 대한 설명이 아니라 안에 적힌 내용 자체가 필요합니다.

규칙:
- 표라면 GFM(GitHub Flavored Markdown) 표로 옮기세요. 병합된 칸은 왼쪽 위에만 값을 넣고 나머지는 비웁니다.
- 조직도·흐름도라면 계층을 들여쓴 목록으로 옮기고, 연결 관계는 `→` 로 나타내세요.
- 그 밖의 그림이면 안에 보이는 글자를 위에서 아래, 왼쪽에서 오른쪽 순서로 옮기세요.
- 숫자와 단위는 보이는 그대로 옮기세요. 추측하지 마세요.
- 읽을 수 있는 글자가 없으면 정확히 `내용 없음` 이라고만 답하세요.
- 옮긴 내용 외에 다른 말은 쓰지 마세요.

문서 맥락(참고용): {context}
"""

#: 읽을 내용이 없을 때 모델이 돌려주도록 지시한 문구.
_EMPTY_ANSWER = "내용 없음"


def read_picture_regions(
    pages: list[PageContent],
    *,
    progress: bool = False,
) -> int:
    """VLM 으로 읽어야 할 그림들의 내용을 복원한다.

    입력:
        pages     대상 페이지 목록 (제자리 갱신)
        progress  진행 표시 여부
    출력: 내용을 복원한 그림 수
    비고:
        `ImageInfo.region_kind == "image"` 이면서 면적이 충분한 것만 고른다.
        표·도표로 이미 판정된 것은 각자의 경로(승격·본문 삽입)가 처리한다.
    """
    mode = get_settings().picture_mode
    if mode not in ("read", "both"):
        _log.debug("그림 내용 읽기 생략 — picture_mode=%s", mode)
        return 0
    if not llm_available():
        _log.info("LLM 미설정 — 그림 내용 읽기를 건너뜁니다")
        return 0

    jobs = [
        (page, info)
        for page in pages
        for info in (page.images or [])
        if _should_read(info)
    ]
    if not jobs:
        return 0

    cfg = llm_api_config()
    workers = max(1, get_settings().llm_concurrency)
    done = 0
    bar = ProgressBar(len(jobs), "그림 내용 읽기", unit="개", enabled=progress)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_read_one, page, info, cfg): (page, info)
            for page, info in jobs
        }
        for future in as_completed(futures):
            page, info = futures[future]
            try:
                markdown = future.result()
            except Exception as exc:             # noqa: BLE001 - 한 건 실패가 전체를 막지 않는다
                _log.warning("%s 내용 읽기 실패: %s", info.id, exc)
                markdown = None
            if markdown:
                info.vlm_markdown = markdown
                _insert_after_placeholder(page, info.placeholder, markdown)
                done += 1
                _log.info("%s 의 내용을 VLM 으로 읽었습니다 (%d자)",
                          info.id, len(markdown))
            bar.update()
    bar.close()
    return done


def _should_read(info) -> bool:
    """이 그림을 VLM 으로 읽어야 하는지.

    입력: info — ImageInfo
    출력: 대상이면 True
    비고:
        · 좌표 판정이 IMAGE 여야 한다 (표·도표는 다른 경로가 맡는다)
        · 저장된 그림 파일이 있어야 한다 (근거가 없으면 물어볼 수 없다)
        · 이미 읽었으면 다시 하지 않는다
        · 면적이 작으면 로고·아이콘이므로 건너뛴다
    """
    if getattr(info, "vlm_markdown", None):
        return False
    if getattr(info, "region_kind", None) not in (None, "image"):
        return False
    if not getattr(info, "image_path", None):
        return False
    return _area_ratio(info) >= MIN_AREA_RATIO


def _area_ratio(info) -> float:
    """그림이 페이지에서 차지하는 면적 비율.

    입력: info — ImageInfo (bbox 사용)
    출력: 0.0~1.0. bbox 가 없으면 1.0 (판단 못 하면 읽어 본다)
    """
    bbox = getattr(info, "bbox", None)
    if not bbox:
        return 1.0
    try:
        width = float(bbox["r"]) - float(bbox["l"])
        height = float(bbox["b"]) - float(bbox["t"])
    except (KeyError, TypeError, ValueError):
        return 1.0
    if width <= 0 or height <= 0:
        return 1.0
    # A4 기준. 정확한 페이지 크기를 몰라도 대소 판단에는 충분하다.
    return min(1.0, abs(width * height) / (595.0 * 842.0))


def _read_one(page: PageContent, info, cfg: dict[str, Any]) -> str | None:
    """그림 하나를 VLM 에 보내 내용을 받는다.

    입력: page(맥락용), info(대상 그림), cfg(LLM 설정)
    출력: markdown 문자열. 읽을 내용이 없거나 실패하면 None
    """
    encoded = encode_image_file(info.image_path)
    if not encoded:
        return None
    mime, b64 = encoded

    context = (page.content or "")[:400]
    raw = invoke_llm(
        _PROMPT.format(context=context or "(없음)"),
        span_name="picture_read",
        image_urls=[f"data:{mime};base64,{b64}"],
        cfg=cfg,
    )
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = _strip_fence(text)
    if not text or text.replace(" ", "") == _EMPTY_ANSWER.replace(" ", ""):
        return None
    if len(text) < MIN_RESULT_CHARS:
        return None
    return text


def _strip_fence(text: str) -> str:
    """```markdown 울타리를 벗긴다.

    입력: text — 모델 응답
    출력: 울타리를 제거한 본문
    """
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _insert_after_placeholder(page: PageContent, placeholder: str, markdown: str) -> None:
    """복원한 내용을 그림 placeholder 바로 뒤에 넣는다.

    입력: page, placeholder, markdown
    출력: 없음 (page.content 갱신)
    비고: 그림은 그대로 둔다 — 원본 확인 수단이 필요하다.
    """
    content = page.content or ""
    if placeholder and placeholder in content:
        page.content = content.replace(placeholder, f"{placeholder}\n\n{markdown}", 1)
    else:
        page.content = f"{content}\n\n{markdown}" if content else markdown
