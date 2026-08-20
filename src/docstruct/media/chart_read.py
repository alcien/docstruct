"""그래프를 VLM 으로 읽고 본문과 대조한다.

역할:
    `region_kind == "chart"` 인 영역을 VLM 에 보여 값을 받고, 같은 쪽 본문에
    그 값이 있는지 확인해 신뢰도를 표시한다.
호출부:
    docstruct.pipeline (`read_charts` 가 켜졌을 때)
입력: 그래프로 표시된 ImageInfo 를 가진 PageContent 목록
출력: 읽어낸 그래프 수

왜 검증이 어려운가
---------------
표는 원본 markdown 과 견줄 수 있지만 **그래프는 대조할 원본이 없다.** 값이
그림 안에 있기 때문이다. 그리고 VLM 은 사람도 못 읽을 만큼 손상된 그래프
에도 답을 시도한다.

그래서 값을 내되 **검증 여부를 함께** 표시한다. 자동으로 쓸지 사람이 볼지는
읽는 쪽이 정한다.

본문 대조
--------
공공문서는 그래프의 근거를 표나 문장으로 함께 싣는다. 다만 **같은 쪽에 있지
않다.** 실측에서 43쪽 원그래프의 값이 같은 쪽에는 0/11, 앞뒤 1쪽까지 보면
9/11 이 있었다 — 그래프는 전략목표별 합계이고 같은 쪽 표는 프로그램별이라
층위가 달랐다.

    verified   앞뒤 쪽 본문에서 확인된 숫자 비율 0~1

넓힐수록 우연 일치가 는다. ±2 이상으로 늘려도 확인 수가 그대로였으므로
±1 로 둔다.

다만 **본문이 정확하다는 전제**가 필요하다. 스캔본처럼 본문 자체가 OCR
결과라면 근거가 약하므로 `DOCSTRUCT_CHART_VERIFY_SOURCE=off` 로 끈다.

한계
----
- 그래프가 본문에 없는 값을 보여 주면 검증되지 않는다. 층위가 다른 경우가
  그렇다 — 실측 문서에서 그래프는 전략목표별 합계이고 표는 프로그램별이라
  숫자가 겹치지 않았다.
- 검증률이 낮다고 값이 틀린 것은 아니다. **확인하지 못했다**는 뜻이다.
"""
from __future__ import annotations

import logging
import os
import re

from docstruct.infrastructure.llm.client import invoke_llm, llm_api_config
from docstruct.media.vlm_read import encode_image_file
from docstruct.models import PageContent

_log = logging.getLogger(__name__)

#: 무엇과 견줄지. `page` 는 이웃 쪽 본문, `document` 는 문서 전체,
#: `off` 는 대조하지 않음.
VERIFY_SOURCE_ENV = "DOCSTRUCT_CHART_VERIFY_SOURCE"

#: `page` 일 때 앞뒤로 몇 쪽까지 볼지.
#: **같은 쪽만 보면 안 된다.** 실측에서 43쪽 그래프의 값이 41~42쪽 표에
#: 있어 같은 쪽 대조로는 **0/9** 였다. 범위를 넓히면 8/9 가 된다.
#: 공공문서는 설명과 그림이 쪽을 걸쳐 흩어진다.
#:
#:     같은 쪽만    0/9   =   0%
#:     ±2쪽        8/9   =  89%
#:     문서 전체    9/9   = 100%
#:
#: 넓힐수록 우연히 맞을 확률도 커진다. 문서 전체를 보면 관계없는 쪽의
#: 숫자와도 맞아 근거가 약해진다.
VERIFY_SPAN_ENV = "DOCSTRUCT_CHART_VERIFY_SPAN"
DEFAULT_VERIFY_SPAN = 2

#: 읽어낸 결과가 이보다 짧으면 실패로 본다.
MIN_RESULT_CHARS = 15

#: 대조에 쓸 숫자 — 두 자리 이상. 한 자리는 우연히 맞을 확률이 높다.
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?%?")

_PROMPT = """이 이미지의 그래프를 읽어 값을 표로 옮기세요.

규칙:
- 보이는 값만 적습니다. 읽을 수 없으면 그 항목을 빼세요.
- 추정하거나 계산하지 마세요.
- 항목과 값을 GFM markdown 표로 냅니다.
- 그래프가 없거나 읽을 수 없으면 정확히 `없음` 이라고만 답하세요.
- 설명 없이 표만 출력하세요.

참고 (이 그래프 주변 본문):
{context}"""


def _verify_source() -> str:
    """무엇과 대조할지.

    입력: 없음 (`DOCSTRUCT_CHART_VERIFY_SOURCE`)
    출력: "page" | "document" | "off"
    """
    value = os.environ.get(VERIFY_SOURCE_ENV, "page").strip().lower()
    return value if value in ("page", "document", "off") else "page"


def _verify_span() -> int:
    """앞뒤로 몇 쪽까지 볼지.

    입력: 없음 (`DOCSTRUCT_CHART_VERIFY_SPAN`)
    출력: 0 이상 정수
    """
    raw = os.environ.get(VERIFY_SPAN_ENV, "").strip()
    if not raw:
        return DEFAULT_VERIFY_SPAN
    try:
        value = int(raw)
    except ValueError:
        _log.warning("%s 값이 숫자가 아닙니다: %r", VERIFY_SPAN_ENV, raw)
        return DEFAULT_VERIFY_SPAN
    return value if value >= 0 else DEFAULT_VERIFY_SPAN


def _reference_text(pages: list[PageContent], page: PageContent, mode: str) -> str:
    """대조에 쓸 본문.

    입력: pages — 전체 페이지, page — 그래프가 있는 쪽, mode — 대조 방식
    출력: 이어 붙인 본문
    비고:
        **같은 쪽만 보면 안 된다.** 이 문서는 설명과 그림이 쪽을 걸쳐
        흩어진다 — 43쪽 그래프의 값이 41~42쪽 표에 있었다.
    """
    if mode == "document":
        return "\n".join(p.content or "" for p in pages)

    span = _verify_span()
    if not isinstance(page.page_no, int) or span <= 0:
        return page.content or ""
    low, high = page.page_no - span, page.page_no + span
    return "\n".join(
        p.content or "" for p in pages
        if isinstance(p.page_no, int) and low <= p.page_no <= high
    )


def verified_ratio(values: str, reference: str) -> tuple[int, int]:
    """읽어낸 숫자 중 몇 개가 참고 텍스트에 있는지.

    입력: values — VLM 이 낸 표, reference — 대조할 본문
    출력: (확인된 개수, 전체 개수)
    비고:
        두 자리 이상 숫자만 본다. 한 자리는 우연히 맞을 확률이 높아 근거가
        되지 못한다. 백분율 기호는 떼고 견준다 — 본문에서는 `20.7%` 가
        `20.7` 로 적히는 일이 흔하다.
    """
    def _clean(text: str) -> set[str]:
        found = _NUMBER_RE.findall(text or "")
        return {n.rstrip("%") for n in found if len(n.rstrip("%").replace(".", "")) >= 2}

    read = _clean(values)
    if not read:
        return 0, 0
    pool = _clean(reference)
    return len(read & pool), len(read)


def _reference(
    pages: list[PageContent], target: PageContent, span: int
) -> str:
    """대조에 쓸 본문.

    입력: pages — 전체 페이지, target — 그래프가 있는 쪽, span — 앞뒤 쪽 수
    출력: 이어 붙인 본문
    비고:
        그래프와 그 근거가 같은 쪽에 있지 않다. 앞뒤로 조금 넓혀야 잡힌다.
    """
    if not isinstance(target.page_no, int) or span <= 0:
        return target.content or ""
    low, high = target.page_no - span, target.page_no + span
    return "\n".join(
        page.content or "" for page in pages
        if isinstance(page.page_no, int) and low <= page.page_no <= high
    )


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


def read_charts(pages: list[PageContent], *, progress: bool = False) -> int:
    """그래프로 표시된 영역을 VLM 으로 읽는다.

    입력: pages — 대상 페이지 목록 (제자리 갱신), progress — 진행 표시 여부
    출력: 값을 읽어낸 그래프 수
    비고:
        읽어낸 값은 `ImageInfo.description` 에 담고, 본문 대조 결과를
        `chart_verified` 에 남긴다. **원본은 건드리지 않는다.**
    """
    targets = [
        (page, info)
        for page in pages
        for info in page.images
        if info.region_kind == "chart" and info.image_path
    ]
    if not targets:
        return 0

    cfg = llm_api_config()
    if not cfg:
        _log.warning("LLM 이 설정되지 않아 그래프 읽기를 건너뜁니다 (%d개)", len(targets))
        for page, info in targets:
            page.trace.add(
                "docstruct.media.chart_read", "그래프 읽기 생략",
                f"{info.id} · LLM 미설정 — 값은 그림 안에 남습니다", status="warn")
        return 0

    mode = _verify_source()
    read = 0
    for page, info in targets:
        encoded = encode_image_file(info.image_path)
        if not encoded:
            continue
        mime, b64 = encoded
        context = re.sub(r"<[^>]+>", "", page.content or "")[:600]

        try:
            raw = invoke_llm(
                _PROMPT.format(context=context.strip() or "(없음)"),
                span_name="chart_read",
                image_urls=[f"data:{mime};base64,{b64}"],
                cfg=cfg,
            )
        except Exception as exc:                 # noqa: BLE001 - 하나 실패로 멈추지 않는다
            _log.warning("%s 그래프 읽기 실패: %s", info.id, exc)
            continue

        text = (raw or "").strip()
        if text.startswith("```"):
            text = _strip_fence(text)
        if not text or text.replace(" ", "") == "없음" or len(text) < MIN_RESULT_CHARS:
            continue

        info.description = text
        info.source = "vlm"
        read += 1

        if mode == "off":
            info.chart_verified = None
            page.trace.add("docstruct.media.chart_read", "그래프 읽음",
                           f"{info.id} · 대조하지 않음")
            continue

        hit, total = verified_ratio(text, _reference_text(pages, page, mode))
        info.chart_verified = round(hit / total, 2) if total else 0.0
        page.trace.add(
            "docstruct.media.chart_read", "그래프 읽음",
            f"{info.id} · 숫자 {total}개 중 {hit}개가 본문에서 확인됨",
            status="warn" if total and hit / total < 0.5 else "ok")
    return read
