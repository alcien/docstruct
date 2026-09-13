"""스캔 쪽을 VLM 으로 읽는다 — rapidocr 대신.

입력:
    쪽 이미지

역할:
    텍스트 레이어가 없는 쪽의 지면 이미지를 VLM 에 보내 본문을 받는다.
호출부:
    docstruct.pipeline (`DOCSTRUCT_SCAN_BACKEND=vlm` 일 때)
출력:
    바꾼 쪽 번호 집합 (PageContent.content 갱신). 파이프라인이 이 집합을
    빼고 남은 쪽만 rapidocr 폴백으로 넘긴다 — 실패·무응답·반복 검출로
    물러난 쪽이 폴백을 실제로 받게 하기 위함이다 (0.4.56)

왜 필요한가
--------
docling 이 쓰는 rapidocr 기본 모델(PP-OCRv6 small)에는 **한국어가 없다.**
한국어 모델을 붙여도 46~70% 이고 한자·가나가 섞인다. 실측(조달청 p5):

    - 고위공무원단  →  공금운농-
    - 관리운영      →  융공군-
    - 8·9급         →  6·8-

VLM 은 문맥을 보므로 이런 어긋남이 나지 않는다. 같은 문서의 조직도를
VLM 이 읽었을 때 청장→차장→각 과의 계층과 정원 표까지 정확히 복원했다.

**표적이 작아져서 비로소 값이 맞는다.** 0.4.11 에서 OCR 오판을 고치기
전에는 조달청 76쪽 전부가 재판독 대상이었다. 지금은 2쪽이다 — 그 정도면
VLM 이 rapidocr 보다 모든 면에서 낫다.

원본을 남긴다
-----------
`page.ocr_original` 에 바꾸기 전 본문을 담는다. 이것이 없으면 **OCR 과
VLM 중 무엇이 나았는지 비교할 근거가 아예 없다** — 지금까지 그랬다.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from docstruct.models import PageContent

_log = logging.getLogger(__name__)

#: 이보다 짧으면 **글이 적은 지면**으로 보고 기록에 남긴다. 버리지는
#: 않는다 (0.4.60) — 표지·간지에서는 짧은 것이 정답이고, 버리면 그
#: 자리를 폴백(rapidocr)의 잡음이 차지한다.
MIN_RESULT_CHARS = 30

#: 지시문. 표는 GFM 으로, 없는 것은 지어내지 말 것.
_PROMPT = """이 페이지 이미지를 그대로 텍스트로 옮겨 주세요.

규칙:
- 지면에 보이는 내용만 옮깁니다. 없는 것을 지어내지 마세요.
- 표는 GFM 마크다운 표로 옮깁니다. 병합된 칸은 같은 값을 반복하지 말고
  비워 두세요.
- 글머리 기호(□ ○ ㅇ -)와 들여쓰기는 원문 그대로 둡니다.
- 쪽 번호·머리말·꼬리말은 옮기지 않습니다.
- 설명이나 감상을 덧붙이지 말고 본문만 출력합니다.

앞뒤 맥락(참고용):
{context}
"""


def read_scanned_pages(pages: list[PageContent],
                       targets: set[int] | None = None) -> set[int]:
    """스캔 쪽을 VLM 으로 다시 읽는다.

    입력: pages — page_image_path 가 채워진 PageContent 목록,
          targets — 대상 쪽 번호 (None 이면 전부)
    출력: 바꾼 쪽 번호 집합. 물러난 쪽(실패·무응답·반복 검출·빈 지면)은
          들어가지 않으므로, 호출부가 그 쪽만 rapidocr 로 넘길 수 있다.
    비고:
        표 자리표시자(`<table N>`)는 살려 둔다 — 뒤 단계(표 판정·재추출)가
        그것을 앵커로 쓴다. 읽은 결과가 비면 그 쪽은 그대로 둔다: OCR 이
        실패한 지면에서 있던 내용까지 지우면 안 된다.

        **반복 검출을 여기서도 건다** (0.4.56). 그림 판독(vlm_read)에는
        0.4.49 부터 있었는데, 정작 VLM 이 루프에 빠지기 가장 쉬운 전면
        쪽 전사에는 없었다. 같은 줄이 30% 이상이면 지어낸 것으로 보고
        그 쪽을 rapidocr 폴백에 남긴다 — 환각으로 본문을 덮는 것보다
        낮은 정확도의 실측이 낫다.
    """
    from docstruct.text.korean_text import normalize_pdf_text
    from docstruct.infrastructure.llm.client import invoke_llm, llm_api_config
    from docstruct.images.encode import encode_image_file
    from docstruct.images.vlm_read import (MAX_REPEAT_RATIO, _repetition_ratio,
                                          _strip_fence)
    from docstruct.pipeline import _TABLE_TAG_RE as TABLE_TAG_RE

    cfg = llm_api_config()
    if cfg is None:
        # **조용히 넘어가지 않는다.** 로그만 찍으면 결과물에 남지 않아
        # "VLM 이 안 돌았다" 는 사실조차 알 수 없다 — 실측(행안부 p76·
        # p243)에서 실제로 그 때문에 원인을 잘못 짚었다.
        for page in pages:
            if targets is None or page.page_no in targets:
                page.trace.add("docstruct.text.scan_vlm", "VLM 쪽 판독 생략",
                               "LLM 미설정 — rapidocr 로 넘어갑니다",
                               status="warn")
        _log.info("LLM 미설정 — VLM 쪽 판독을 건너뜁니다")
        return set()

    read: set[int] = set()
    for page in pages:
        if targets is not None and page.page_no not in targets:
            continue
        image = getattr(page, "page_image_path", None)
        if not image or not Path(image).is_file():
            page.trace.add("docstruct.text.scan_vlm", "VLM 쪽 판독 생략",
                           "지면 이미지가 없습니다", status="warn")
            continue

        encoded = encode_image_file(image)
        if not encoded:
            continue
        mime, b64 = encoded
        try:
            raw = invoke_llm(
                _PROMPT.format(context=(page.content or "")[:300] or "(없음)"),
                span_name="scan_page_read",
                image_urls=[f"data:{mime};base64,{b64}"],
                cfg=cfg,
            )
        except Exception as exc:                 # noqa: BLE001 - 한 쪽 실패로 멈추지 않는다
            _log.warning("%s쪽 VLM 판독 실패: %s", page.page_no, exc)
            page.trace.add("docstruct.text.scan_vlm", "VLM 쪽 판독 실패",
                           str(exc)[:120], status="warn")
            continue
        if not raw:
            page.trace.add("docstruct.text.scan_vlm", "VLM 쪽 판독 무응답",
                           "rapidocr 로 넘어갑니다", status="warn")
            continue
        text = raw.strip()
        if text.startswith("```"):
            text = _strip_fence(text)
        short = len(text.strip()) < MIN_RESULT_CHARS
        if short:
            # **짧다고 버리지 않는다** (0.4.60). 예전에는 여기서 물러나
            # rapidocr 에 넘겼는데, 표지·간지처럼 원래 글이 적은 쪽에서는
            # **짧은 것이 정답**이다.
            #
            # 실측(주택과세금 377쪽): VLM 이 11자를 읽은 쪽 17개가 전부
            # `2025 주택과 세금` 한 줄짜리 표지였다 — 정확히 맞는 판독이다.
            # 그것을 버리고 rapidocr 로 넘기자 브라우저 인쇄 껍데기
            # (`26.5.11. 오후5:44` · URL · `4/380`)가 112자로 들어와
            # **본문 자리를 차지했다.** 맞는 11자를 틀린 112자로 바꾼 셈이다.
            #
            # 짧은 판독과 실패한 판독은 다르다. 무응답·예외는 위에서 이미
            # 걸러졌으니, 여기 온 것은 **읽고서 짧게 답한 것**이다.
            page.trace.add(
                "docstruct.text.scan_vlm", "VLM 쪽 판독 (짧음)",
                f"읽은 글이 {len(text.strip())}자뿐 — 글이 적은 지면으로 "
                "보고 그대로 씁니다 (지어내지 않은 것이므로 정상)")
        repeats = _repetition_ratio(text)
        if repeats >= MAX_REPEAT_RATIO:
            # **반복 검출 — 지어낸 것으로 본다** (그림 판독 0.4.49 와 같은
            # 가드). VLM 이 루프에 빠지면 같은 줄이 수십 번 나온다. 본문을
            # 그것으로 덮지 않고 rapidocr 폴백에 남긴다.
            page.trace.add(
                "docstruct.text.scan_vlm", "VLM 쪽 판독 기각",
                f"같은 줄이 {repeats:.0%} 반복 — 지어낸 것으로 보고 "
                "rapidocr 로 넘어갑니다", status="warn")
            continue

        # **원본을 남긴다.** 이것이 없으면 OCR 과 VLM 중 무엇이 나았는지
        # 비교할 근거가 없다.
        page.ocr_original = page.content
        page.ocr_engine = str(cfg.get("model") or "vlm")
        placeholders = TABLE_TAG_RE.findall(page.content or "")
        body = normalize_pdf_text(text)
        page.content = ("\n\n".join([body, *placeholders])
                        if placeholders else body)
        page.trace.add("docstruct.text.scan_vlm", "VLM 쪽 판독",
                       f"본문 {len(body)}자 · 모델 {page.ocr_engine} "
                       "(rapidocr 대신)"
                       + (" · 글이 적은 지면" if short else ""))
        if isinstance(page.page_no, int):
            read.add(page.page_no)
    return read
