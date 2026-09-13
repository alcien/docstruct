"""의심스러운 자리를 지면 보고 다시 읽는다.

역할:
    `verify_ocr` 이 짚은 쪽만 VLM 에 지면 이미지를 보내 바로잡는다.
호출부:
    docstruct.pipeline (`reread_doubts` 가 켜졌을 때)
입력: PageContent 목록 (`ocr_doubts` 가 채워진 상태)
출력: 고친 곳 수

왜 필요한가
--------
`verify_ocr` 은 **어디가 이상한지만** 짚는다. 텍스트만 보므로 무엇이 맞는지
정할 수 없다.

    "이란 연 2.9를 말한다"  →  이상하다 (법령체 아님)
                             그런데 `29` 인지 `2.9` 인지는 모른다

지면을 보면 안다. 그래서 짚은 쪽만 골라 VLM 에 태운다.

왜 짚은 쪽만인가
-------------
전면 재판독은 비싸다. 실측(주택과세금 377쪽)에서 rapidocr 재판독만 627초
였고, VLM 은 그보다 훨씬 오래 걸린다.

`verify_ocr` 이 13쪽 중 8쪽을 짚었으니, 377쪽이면 200쪽쯤 된다. 그래도
전면보다는 낫고 — 무엇보다 **고칠 것이 있는 쪽만** 본다.

무엇을 고치는가
------------
**짚은 조각만** 바꾼다. 지면 전체를 다시 쓰면 멀쩡한 곳까지 흔들린다.

    ocr_doubts 의 source_text 를 찾아 → VLM 이 읽은 것으로 교체

본문(`page.content`)에 없으면 표 안이다 — 검증(⑦)이 표 칸도 짚으므로
파서 표(`source == parser`)의 markdown 에서도 찾는다.

원본은 `page.ocr_original`(본문) · `table.original_markdown`(표)에 남겨
되돌릴 수 있게 한다.
"""
from __future__ import annotations

import logging
from typing import Any

from docstruct.infrastructure.llm.client import invoke_llm, llm_api_config
from docstruct.images.encode import encode_image_file
from docstruct.models import PageContent

_log = logging.getLogger(__name__)

#: 한 쪽에서 다시 읽을 최대 조각 수. 많으면 프롬프트가 길어져 정확도가 떨어진다.
MAX_DOUBTS_PER_PAGE = 8

#: 고친 글이 원본보다 이 비율 넘게 길어지면 받지 않는다.
#: 지면에 없는 말을 덧붙인 것으로 본다.
MAX_GROWTH = 2.5

_PROMPT = """\
아래 이미지는 문서 한 쪽입니다. OCR 로 읽은 결과에 **의심스러운 자리**가
있어 확인을 부탁합니다.

## 무엇을 해 주세요

지면에서 그 자리를 찾아 **실제로 무엇이라 쓰여 있는지** 알려 주세요.

- 지면에 보이는 대로만 적으세요.
- **추측하지 마세요.** 흐릿해서 못 읽겠으면 `모름` 이라고 하세요.
- 앞뒤 문맥을 덧붙이지 마세요. 그 자리의 글자만 냅니다.
- OCR 이 맞게 읽었으면 그대로 다시 적으세요.

## 의심스러운 자리

{doubts}

## 응답 (JSON 배열만, 다른 텍스트 없음)

[
  {{"index": 12, "text": "이란 연 1천분의 29를 말한다"}},
  {{"index": 15, "text": "모름"}}
]

- "index": 위 목록의 번호
- "text" : 지면에 실제로 쓰인 글. 못 읽으면 `모름`
"""


def _render_doubts(doubts: list[dict]) -> str:
    """의심 목록을 프롬프트에 넣을 꼴로.

    입력: doubts — page.ocr_doubts
    출력: 여러 줄 문자열
    """
    lines = []
    for doubt in doubts[:MAX_DOUBTS_PER_PAGE]:
        source = (doubt.get("source_text") or doubt.get("text") or "").strip()
        reason = (doubt.get("reason") or "").strip()
        lines.append(f"{doubt.get('index')}: {source}")
        if reason:
            lines.append(f"    (의심 이유: {reason})")
    return "\n".join(lines)


def _acceptable(before: str, after: str) -> bool:
    """고친 글을 받아들일지.

    입력: before — 원래 글, after — VLM 이 읽은 글
    출력: 받아들이면 True
    비고:
        **지어낸 것을 거른다.** 원본보다 크게 길어지면 지면에 없는 말을
        덧붙인 것이다. `모름` 은 못 읽었다는 뜻이므로 손대지 않는다.
    """
    text = after.strip()
    if not text or text == "모름":
        return False
    if text == before.strip():
        return False                     # 바뀐 것이 없다
    if len(text) > max(len(before), 1) * MAX_GROWTH:
        return False
    return True


def _reread_page(page: PageContent, cfg: dict[str, Any]) -> int:
    """한 쪽의 의심 자리를 다시 읽는다.

    입력: page — PageContent, cfg — LLM 설정
    출력: 고친 조각 수
    """
    encoded = encode_image_file(page.page_image_path)
    if not encoded:
        return 0
    mime, b64 = encoded

    try:
        raw = invoke_llm(
            _PROMPT.format(doubts=_render_doubts(page.ocr_doubts)),
            span_name="ocr_reread",
            image_urls=[f"data:{mime};base64,{b64}"],
            cfg=cfg,
        )
    except Exception as exc:                     # noqa: BLE001 - 재판독 실패는 치명적이지 않다
        _log.warning("%s쪽 재판독 실패: %s", page.page_no, exc)
        return 0

    from docstruct.infrastructure.llm.json_parse import parse_json_array

    answers = {item.get("index"): (item.get("text") or "").strip()
               for item in (parse_json_array(raw) or [])}
    if not answers:
        return 0

    content = page.content or ""
    fixed = 0
    for doubt in page.ocr_doubts:
        answer = answers.get(doubt.get("index"))
        if answer is None:
            continue
        before = (doubt.get("source_text") or "").strip()
        if not before:
            continue
        if not _acceptable(before, answer):
            continue

        if before in content:
            if page.ocr_original is None:
                page.ocr_original = content
            content = content.replace(before, answer, 1)
            doubt["fixed_text"] = answer
            fixed += 1
            continue

        # 본문에 없으면 **표 안**이다 — 검증(⑦)은 표 칸도 짚는다 (0.3.55).
        # 본문만 바꾸면 표 칸 의심은 조용히 버려져, VLM 이 읽어 와도
        # 반영되지 않았다. 검증과 같은 기준으로 파서 표만 본다 —
        # 재추출된 표(source≠parser)는 애초에 검증 대상이 아니다.
        for table in page.tables:
            if getattr(table, "source", "parser") != "parser":
                continue
            markdown = table.markdown or ""
            if before not in markdown:
                continue
            if table.original_markdown is None:
                table.original_markdown = markdown   # 되돌릴 수 있게 남긴다
            table.markdown = markdown.replace(before, answer, 1)
            doubt["fixed_text"] = answer
            fixed += 1
            break

    if fixed:
        page.content = content
        page.trace.add("docstruct.text.ocr_reread", "의심 자리 재판독",
                       f"{fixed}군데", status="warn")
    return fixed


def reread_doubts(pages: list[PageContent]) -> int:
    """의심스러운 자리를 지면 보고 다시 읽는다.

    입력: pages — 페이지 목록 (제자리 갱신)
    출력: 고친 곳 수
    비고:
        `ocr_doubts` 가 있는 쪽만 본다. 렌더 이미지가 없으면 건너뛴다 —
        지면을 봐야 하는 일이라 이미지가 없으면 할 수 없다.
    """
    cfg = llm_api_config()
    if cfg is None:
        _log.info("LLM 이 없어 재판독을 건너뜁니다")
        return 0

    targets = [p for p in pages if p.ocr_doubts and p.page_image_path]
    if not targets:
        return 0

    _log.info("의심 자리 재판독: %d쪽", len(targets))
    total = 0
    for page in targets:
        total += _reread_page(page, cfg)
    if total:
        _log.info("재판독으로 %d군데 고침 — 원본은 `ocr_original` 에 남습니다",
                  total)
    return total
