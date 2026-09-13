"""실험 scan_ab — 스캔 쪽 VLM↔OCR 이중 판독 대조 (측정 전용).

입력:
    스캔 쪽 이미지 + VLM 판독·OCR 판독
출력:
    없음 — page.scan_ab / ImageInfo.scan_ab 대조 기록

역할:
    스캔으로 다시 읽은 **쪽**과, 지면으로 보고 전사한 **그림**을 다른
    판독기로 한 번 더 읽어, 두 판독의 숫자 집합 불일치를 지목한다.
    본문은 바꾸지 않는다.
호출부:
    pipeline (실험이 켜졌을 때) — 사다리 밖, 검증 계열
설정:
    DOCSTRUCT_EXP_SCAN_AB=1 (기본 꺼짐)

왜 HWPX 도 대상인가 (0.4.58)
--------------------------
처음에는 PDF 전용으로 냈는데 **좁게 잡은 것이었다.** 한글 문서에도 스캔
지면이 그림으로 들어가고(0.4.25), 그런 그림은 `is_page_like` 로 갈려
**스캔 쪽 판독과 똑같은 전사 지시문**으로 읽힌다. 실측(조달청 HWPX
image_1): `kind="page"` 로 잡혀 조직도 전문이 전사돼 본문에 들어갔다.

읽는 행위가 같으면 위험도 같다 — 그런데 그 결과만 검증(verify_ocr)과
이중 판독 대상 밖에 있었다. 대상을 쪽에서 "전사된 것" 으로 넓힌다.

왜 있는가 — VLM-OCR 효용성을 잴 도구가 없었다
------------------------------------------
0.4.17 에서 스캔 판독 기본이 VLM 이 됐는데, 그 승격 근거는 rapidocr 의
질적 실패(`- 고위공무원단` → `공금운농-`)이지 **같은 쪽을 나란히 읽힌
계측이 아니다.** `page.ocr_original` 도 VLM 경로에서는 rapidocr 결과가
아니라 docling 의 (대개 빈) 본문이라, 한 번의 실행에서 둘을 견줄 근거가
없다 — A/B 를 하려면 문서 전체를 두 번 돌려야 했다.

이 실험은 한 실행 안에서 그 대조를 만든다. 기본 판독(파이프라인이 채택한
것)은 그대로 두고, 반대쪽 판독을 추가로 얻어 **숫자만** 대조한다.

왜 숫자인가
---------
가장 위험한 오류가 수치 오독이다 (STATUS.md ①: `연 1천분의 29` →
`연 2.9` — 값이 열 배 틀렸는데 텍스트만으로는 모른다). 문장은 두 판독기가
표현을 달리해도 무해하지만, **숫자는 표기가 하나뿐**이라 집합 대조가
결정론 지목이 된다. verify_ocr(LLM 지목)과 상보적이다 — 이쪽은 LLM 없이
돌고, 두 판독기가 **서로 다른 숫자를 읽은 자리**라는 물적 근거를 남긴다.

무엇을 하고 무엇을 안 하나
------------------------
    한다     전사된 쪽·그림마다 반대 판독을 얻어 숫자 집합·글자 수·
             반복 비율 기록
    안 한다  본문 교체 (측정 전용 — 판정은 verify→reread 나 사람 몫)
             둘 중 누가 옳은지 판정 (지면을 봐야 안다 — ocr_reread 원칙)
             표 안 텍스트 (표는 좌표 매칭 별도 단계)
             설명·도해로 읽은 그림 (전사가 아니라 요약이므로 숫자 대조가
             성립하지 않는다 — `transcribed` 가 참인 것만 본다)

비용: 기본이 VLM 인 실행에서는 쪽당 rapidocr 한 번(로컬·무료).
기본이 OCR 인 실행에서는 쪽당 VLM 호출 한 번 — LLM 이 없으면 물러난다.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from docstruct.experiments.registry import Experiment, register

if TYPE_CHECKING:
    from docstruct.models import PageContent

_log = logging.getLogger(__name__)

#: 값으로 볼 숫자 토큰 — 쉼표 붙은 수(1,150,000), 소수(2.9), 두 자리 이상.
#: 한 자리 정수는 목록 번호·글머리라 잡음이 커서 뺀다. `2.9` 대 `29` 를
#: 가르는 것이 목적이므로 소수는 한 자리여도 잡는다.
_NUMBER = re.compile(r"\d[\d,]*\.\d+|\d[\d,]*\d")

#: 표 자리표시자 — 대조에서 뺀다 (표는 좌표 매칭 별도 단계).
_TABLE_TAG = re.compile(r"<table \d+>")

#: 이보다 짧은 반대 판독은 실패로 본다 (scan_vlm 과 같은 문턱).
MIN_ALT_CHARS = 30


def number_tokens(text: str) -> set[str]:
    """본문에서 값 꼴 숫자를 뽑아 정규화한 집합으로.

    입력: text — 본문
    출력: 쉼표를 뗀 숫자 문자열 집합
    비고:
        쉼표만 정규화한다 — `1,150,000` 과 `1150000` 은 같은 값이다.
        소수점은 남긴다 — `2.9` 와 `29` 를 가르는 것이 이 실험의 목적이다.
    """
    return {m.group().replace(",", "")
            for m in _NUMBER.finditer(_TABLE_TAG.sub("", text or ""))}


def digit_jaccard(a: set[str], b: set[str]) -> float:
    """두 숫자 집합의 Jaccard 일치도.

    입력: a, b — number_tokens 결과
    출력: 0.0~1.0. 둘 다 비면 1.0 (숫자가 없으니 불일치도 없다)
    """
    if not a and not b:
        return 1.0
    union = a | b
    return len(a & b) / len(union) if union else 1.0


def _hangul(text: str) -> int:
    """한글 글자 수."""
    return sum(1 for ch in text or "" if "가" <= ch <= "힣")


def _alt_read(page, image_path: str, engine: str, *, label: str):
    """기본 판독의 반대쪽으로 같은 그림을 읽는다.

    입력: page — PageContent (trace 기록용), image_path — 지면·그림 파일,
          engine — 기본 판독 주체, label — 기록에 쓸 대상 이름
    출력: (반대 판독 본문, 반대 판독기 이름). 못 읽으면 None
    비고:
        기본이 VLM 이면 rapidocr 로, 기본이 rapidocr 면 VLM 으로 읽는다.
        어느 쪽이든 실패는 trace 에 남기고 물러난다 — 측정 실험이 본체를
        멈추게 하면 안 된다.
    """
    if not image_path:
        return None
    if engine != "rapidocr":
        try:
            from docstruct.converters.pdf.rapidocr_ko import read_page_text

            text = read_page_text(image_path)
        except Exception as exc:                 # noqa: BLE001 - 측정 실패는 조용히 물러난다
            page.trace.add("docstruct.experiments.text.scan_ab", "이중 판독 생략",
                           f"{label} · rapidocr 실패: {str(exc)[:70]}",
                           status="warn")
            return None
        return (text or "", "rapidocr")

    from docstruct.infrastructure.llm.client import invoke_llm, llm_api_config

    cfg = llm_api_config()
    if cfg is None:
        page.trace.add("docstruct.experiments.text.scan_ab", "이중 판독 생략",
                       f"{label} · 기본이 rapidocr 인데 LLM 미설정 — 반대 판독 불가",
                       status="warn")
        return None
    try:
        from docstruct.images.encode import encode_image_file
        from docstruct.text.scan_vlm import _PROMPT
        from docstruct.images.vlm_read import _strip_fence

        encoded = encode_image_file(image_path)
        if not encoded:
            return None
        mime, b64 = encoded
        raw = invoke_llm(
            _PROMPT.format(context="(없음)"),
            span_name="scan_ab_read",
            image_urls=[f"data:{mime};base64,{b64}"],
            cfg=cfg,
        )
    except Exception as exc:                     # noqa: BLE001
        page.trace.add("docstruct.experiments.text.scan_ab", "이중 판독 생략",
                       f"{label} · VLM 실패: {str(exc)[:70]}", status="warn")
        return None
    text = (raw or "").strip()
    if text.startswith("```"):
        text = _strip_fence(text)
    return (text, str(cfg.get("model") or "vlm"))


def _targets(pages):
    """대조할 것을 모은다 — 전사된 쪽과 전사된 그림.

    입력: pages — 페이지 목록
    출력: [(page, holder, 기본 본문, 기본 판독기, 그림 파일, 이름)]
    비고:
        `holder` 가 기록을 받을 객체다 — 쪽 전체를 다시 읽었으면
        PageContent, 그림 하나를 전사했으면 ImageInfo. **어느 것을 잰
        것인지가 결과물에서 갈려야** 나중에 수치를 섞지 않는다.

        그림은 `transcribed` 가 참인 것만 본다. 설명·도해로 읽은 그림은
        전사가 아니라 요약이므로 숫자 집합 대조가 성립하지 않는다.
    """
    out = []
    for page in pages:
        engine = getattr(page, "ocr_engine", None)
        if engine:
            out.append((page, page, page.content or "", engine,
                        getattr(page, "page_image_path", None),
                        f"p{page.page_no}"))
        for info in getattr(page, "images", None) or []:
            if not getattr(info, "transcribed", False):
                continue
            out.append((page, info, info.vlm_markdown or "",
                        info.vlm_model or "vlm", info.image_path, info.id))
    return out


def run(pages: list[PageContent], **kwargs) -> int:
    """전사된 쪽·그림을 반대 판독기로 다시 읽어 숫자 불일치를 기록한다.

    입력: pages — 페이지 목록 (scan_ab 필드만 채운다)
    출력: 숫자 불일치가 있는 대상 수
    """
    from docstruct.images.vlm_read import _repetition_ratio

    hits = 0
    for page, holder, main_raw, engine, image_path, label in _targets(pages):
        got = _alt_read(page, image_path, engine, label=label)
        if got is None:
            continue
        alt_text, alt_engine = got
        if len(alt_text.strip()) < MIN_ALT_CHARS:
            page.trace.add("docstruct.experiments.text.scan_ab", "이중 판독 물러남",
                           f"{label} · 반대 판독({alt_engine})이 "
                           f"{len(alt_text.strip())}자뿐")
            continue

        main_text = _TABLE_TAG.sub("", main_raw or "")
        main_digits = number_tokens(main_text)
        alt_digits = number_tokens(alt_text)
        jaccard = digit_jaccard(main_digits, alt_digits)
        holder.scan_ab = {
            "target": label,
            "engine": engine,
            "alt_engine": alt_engine,
            "digit_jaccard": round(jaccard, 3),
            # 한쪽에만 있는 숫자 — 이것이 지목이다. 길면 앞만 남긴다.
            "digits_only_main": sorted(main_digits - alt_digits)[:20],
            "digits_only_alt": sorted(alt_digits - main_digits)[:20],
            "hangul": [_hangul(main_text), _hangul(alt_text)],
            "len": [len(main_text.strip()), len(alt_text.strip())],
            "repeat": [round(_repetition_ratio(main_text), 2),
                       round(_repetition_ratio(alt_text), 2)],
        }
        if jaccard < 1.0:
            hits += 1
            page.trace.add(
                "docstruct.experiments.text.scan_ab", "이중 판독 숫자 불일치",
                f"{label} · {engine}↔{alt_engine} · 일치도 {jaccard:.0%} · "
                f"기본에만 {len(main_digits - alt_digits)}개 · "
                f"반대에만 {len(alt_digits - main_digits)}개",
                status="warn")
        else:
            page.trace.add(
                "docstruct.experiments.text.scan_ab", "이중 판독 일치",
                f"{label} · {engine}↔{alt_engine} · "
                f"숫자 {len(main_digits)}개 전부 일치")
    return hits


register(Experiment(
    key="scan_ab",
    title="전사된 쪽·그림 VLM↔OCR 이중 판독 대조",
    purpose="수치 오독(연 1천분의 29 → 2.9)을 두 판독기의 숫자 집합 "
            "불일치로 지목 — 텍스트만 보는 검증이 못 잡는 자리",
    origin="verify→reread 2단 구조의 결정론 판 — 지목만 하고 고치지 않는다",
    formats=("pdf", "hwpx", "hwp"),
    needs=("raster",),
    status="testing",
    stage="images",
    note="측정 전용 · 본문 불변. 대상은 스캔으로 다시 읽은 쪽과 "
         "`transcribed` 인 그림이다 — HWPX 에도 스캔 지면이 그림으로 "
         "들어가고 같은 전사 지시문으로 읽힌다(0.4.58 에서 대상 확대). "
         "대상당 rapidocr 1회(기본이 VLM 일 때) 또는 VLM 1회. "
         "0.4.56 신설 — 실측 없음.",
    run=run,
))
