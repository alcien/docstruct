"""실험 scan_scale_ab — 스캔 렌더 배율 A/B (측정 전용).

입력:
    스캔 쪽 이미지 (두 배율)
출력:
    없음 — page.scan_scale_ab 기록

역할:
    VLM 이 읽은 스캔 쪽을 **다른 배율로 다시 그려** 한 번 더 읽히고,
    두 판의 차이(글자 수·숫자 집합·반복 비율)를 기록한다. 본문은 바꾸지
    않는다.
호출부:
    pipeline (실험이 켜졌을 때) — 사다리 밖, 검증 계열
설정:
    DOCSTRUCT_EXP_SCAN_SCALE_AB=1 (기본 꺼짐)
    DOCSTRUCT_EXP_SCAN_SCALE_ALT   대조 배율. 수(예 2.0) 또는 native
                                   (기본 — 원본 dpi 그대로, 키우지 않음)
    DOCSTRUCT_EXP_SCAN_SCALE_PAGES 최대 쪽 수 (기본 8 — VLM 호출 비용 상한)

왜 있는가 — 300dpi 기본값은 잰 적이 없다
--------------------------------------
`SCAN_RENDER_SCALE=4.17`(≈300dpi)은 "OCR 권장값이라는 일반론으로 넣었고
이 문서군에서 재 본 적이 없다" (pipeline 의 자기 기술). 그런데 지금
기본 판독기는 OCR 이 아니라 **VLM** 이고, VLM 확대는 그림 경로에서
-11% 실측이 있다 (image_prep 0.4.41 — lanczos 확대가 흐림을 토큰에
퍼뜨려 주의를 분산). 판독_연구_동향 §3 의 분야 흐름도 "통째로 키우지
않는다" 쪽이다.

지면 렌더는 lanczos 확대와 물리적으로 다르다(래스터라이즈 해상도를
올리는 것이지 화소 보간이 아니다) — 그래서 **그림 실측을 그대로 옮겨
결론 내릴 수 없고, 지면에서 따로 재야 한다.** 145dpi 스캔 원본이 박힌
쪽을 300dpi 로 그리면 결국 원본 화소를 두 배로 늘린 것이라 같은 문제가
있을 수 있고, 없을 수도 있다. 이 실험이 그 대조를 만든다.

판정에 쓸 것
----------
정답 없이도 방향은 갈린다:

    반복 비율   대조 판만 루프에 빠지면 그 배율이 위험하다
    숫자 집합   두 판의 숫자 불일치 = 최소 한쪽이 틀렸다 (표적 목록)
    글자 수     대조 판이 크게 짧으면 누락이 늘었다는 신호

정답(주택과세금 60쪽 원본 대조 표본)이 있는 문서에서는 두 판을 그것과
대조해 정확도로 판정한다 — 그 대조는 docs/ 채점 킷 몫이다.

비용: 쪽당 렌더 1회 + VLM 호출 1회. 기본 상한 8쪽.
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from docstruct.experiments.registry import Experiment, register

if TYPE_CHECKING:
    from docstruct.models import PageContent

_log = logging.getLogger(__name__)

#: 기본 최대 쪽 수. VLM 호출이 쪽당 한 번이라 상한이 없으면 전면 스캔본
#: 에서 수백 회가 된다.
DEFAULT_MAX_PAGES = 8

#: 배율 차이가 이보다 작으면 대조가 무의미하다.
MIN_SCALE_DELTA = 0.05


def _max_pages() -> int:
    """대조할 최대 쪽 수 (DOCSTRUCT_EXP_SCAN_SCALE_PAGES)."""
    raw = os.getenv("DOCSTRUCT_EXP_SCAN_SCALE_PAGES", "").strip()
    try:
        value = int(raw) if raw else DEFAULT_MAX_PAGES
    except ValueError:
        return DEFAULT_MAX_PAGES
    return value if value > 0 else DEFAULT_MAX_PAGES


def _alt_scale(pdf_path, page_no: int, main_scale: float) -> float | None:
    """대조 배율을 정한다.

    입력: pdf_path — 원본, page_no — 쪽, main_scale — 기본 판이 쓴 배율
    출력: 대조 배율. 기본과 사실상 같으면 None
    비고:
        기본은 `native` — 원본 이미지 dpi 그대로, **키우지 않은 판**이다.
        기본 판(300dpi 목표)과의 대조가 곧 "확대가 VLM 판독에 이득인가"
        라는 물음 그 자체다. 원본 dpi 를 못 재면 2.0(144dpi)으로 물러난다.
    """
    raw = os.getenv("DOCSTRUCT_EXP_SCAN_SCALE_ALT", "").strip().lower()
    if raw and raw != "native":
        try:
            value = float(raw)
        except ValueError:
            _log.warning("DOCSTRUCT_EXP_SCAN_SCALE_ALT=%r 를 읽지 못했습니다", raw)
            return None
        if not 0.5 <= value <= 12.0:
            return None
        return None if abs(value - main_scale) < MIN_SCALE_DELTA else value

    from docstruct.pipeline import MAX_SCAN_SCALE, native_dpi

    native = native_dpi(pdf_path, page_no)
    value = (min(max(native / 72.0, 1.0), MAX_SCAN_SCALE)
             if native else 2.0)
    return None if abs(value - main_scale) < MIN_SCALE_DELTA else value


def run(pages: list[PageContent], **kwargs) -> int:
    """VLM 이 읽은 스캔 쪽을 다른 배율로 다시 읽혀 대조 기록을 남긴다.

    입력: pages — 페이지 목록 (scan_scale_ab 필드만 채운다),
          pdf_path — 원본 경로
    출력: 대조를 기록한 쪽 수
    비고:
        기본 판독이 rapidocr 인 쪽은 건너뛴다 — 대조 판은 VLM 으로 읽으므로
        엔진과 배율 두 변수가 섞여 무엇을 잰 것인지 알 수 없게 된다.
        (OCR 쪽 배율 실측은 image_prep 0.4.50 에 이미 있다 — 확대가 이득.)
    """
    source = kwargs.get("pdf_path")
    if not source:
        return 0

    from docstruct.infrastructure.llm.client import invoke_llm, llm_api_config

    cfg = llm_api_config()
    if cfg is None:
        _log.info("LLM 미설정 — scan_scale_ab 를 건너뜁니다")
        return 0

    from docstruct.experiments.text.scan_ab import (_TABLE_TAG, _hangul,
                                               digit_jaccard, number_tokens)
    from docstruct.images.encode import encode_image_file
    from docstruct.images.page_render import (render_pages_with_tables,
                                             safe_file_stem)
    from docstruct.text.scan_vlm import _PROMPT
    from docstruct.images.vlm_read import _repetition_ratio, _strip_fence
    from docstruct.pipeline import page_scan_scale

    limit = _max_pages()
    done = 0
    tmp = Path(tempfile.mkdtemp(prefix="docstruct-scaleab-"))
    for page in pages:
        if done >= limit:
            break
        engine = getattr(page, "ocr_engine", None)
        if not engine or engine == "rapidocr":
            continue                             # VLM 이 읽은 쪽만 (비고 참조)
        if not isinstance(page.page_no, int):
            continue

        main_scale = page_scan_scale(source, page.page_no)
        alt = _alt_scale(source, page.page_no, main_scale)
        if alt is None:
            page.trace.add("docstruct.experiments.text.scan_scale_ab", "배율 대조 생략",
                           f"대조 배율이 기본({main_scale:.2f})과 같습니다")
            continue

        try:
            rendered = render_pages_with_tables(
                source, [page.page_no], tmp,
                file_stem=f"{safe_file_stem(Path(source).name)}_x{alt:.2f}",
                scale=alt)
            image = rendered.get(page.page_no)
            encoded = encode_image_file(image) if image else None
            if not encoded:
                continue
            mime, b64 = encoded
            raw = invoke_llm(
                _PROMPT.format(context="(없음)"),
                span_name="scan_scale_ab_read",
                image_urls=[f"data:{mime};base64,{b64}"],
                cfg=cfg,
            )
        except Exception as exc:                 # noqa: BLE001 - 측정 실패는 조용히 물러난다
            page.trace.add("docstruct.experiments.text.scan_scale_ab", "배율 대조 실패",
                           str(exc)[:120], status="warn")
            continue
        alt_text = (raw or "").strip()
        if alt_text.startswith("```"):
            alt_text = _strip_fence(alt_text)

        main_text = _TABLE_TAG.sub("", page.content or "")
        main_digits = number_tokens(main_text)
        alt_digits = number_tokens(alt_text)
        jaccard = digit_jaccard(main_digits, alt_digits)
        page.scan_scale_ab = {
            "scale_main": round(main_scale, 2),
            "scale_alt": round(alt, 2),
            "engine": engine,
            "digit_jaccard": round(jaccard, 3),
            "digits_only_main": sorted(main_digits - alt_digits)[:20],
            "digits_only_alt": sorted(alt_digits - main_digits)[:20],
            "hangul": [_hangul(main_text), _hangul(alt_text)],
            "len": [len(main_text.strip()), len(alt_text.strip())],
            "repeat": [round(_repetition_ratio(main_text), 2),
                       round(_repetition_ratio(alt_text), 2)],
        }
        done += 1
        page.trace.add(
            "docstruct.experiments.text.scan_scale_ab", "배율 대조",
            f"x{main_scale:.2f} 대 x{alt:.2f} · 숫자 일치도 {jaccard:.0%} · "
            f"글자 {len(main_text.strip())} 대 {len(alt_text.strip())}",
            status="warn" if jaccard < 1.0 else "ok")
    return done


register(Experiment(
    key="scan_scale_ab",
    title="스캔 렌더 배율 A/B (VLM 판독)",
    purpose="300dpi 기본값이 VLM 판독에 이득인지 원본 배율 판과 대조 — "
            "일반론으로 들어온 기본값의 실측",
    origin="image_prep 0.4.41 (-11%) · 판독_연구_동향 §3 (통째로 키우지 "
           "않는다) — 다만 렌더는 보간이 아니라 지면에서 따로 재야 한다",
    formats=("pdf",),
    needs=("raster",),
    status="testing",
    note="측정 전용 · 본문 불변 · VLM 이 읽은 쪽만. 쪽당 렌더+VLM 1회, "
         "기본 상한 8쪽(DOCSTRUCT_EXP_SCAN_SCALE_PAGES). 0.4.56 신설.",
    run=run,
    knobs={
        "DOCSTRUCT_EXP_SCAN_SCALE_ALT": "대조 배율 — 수 또는 native(기본)",
        "DOCSTRUCT_EXP_SCAN_SCALE_PAGES": "최대 쪽 수 (기본 8)",
    },
))
