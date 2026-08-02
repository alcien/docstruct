"""로컬 HuggingFace VLM 실행.

역할:
    HTTP 엔드포인트 대신 이 장비에 내려받은 VLM 을 직접 돌린다.
    표 판정·재추출·그림 설명이 쓰는 `invoke_llm` 과 같은 입력(프롬프트 +
    data URI 이미지)을 받아 같은 형태의 문자열을 돌려주므로, 호출부는
    무엇이 실행되는지 알 필요가 없다.
호출부:
    docstruct.infrastructure.llm.client.invoke_llm (로컬 모델이 설정된 경우)
출력:
    응답 본문 문자열

사용:
    import docstruct
    docstruct.set_model("Qwen/Qwen3-VL-4B-Instruct")     # 또는 로컬 경로
    docstruct.DocStruct("문서.pdf").run()

비고:
    모델은 처음 쓸 때 한 번만 로드하고 이후 재사용한다. 생성은 스레드
    안전하지 않으므로 락으로 직렬화한다 — `llm_concurrency` 를 올려도
    로컬 모델에서는 병렬로 돌지 않는다 (GPU 메모리 때문에도 그 편이 낫다).
"""
from __future__ import annotations

import base64
import binascii
import logging
import re
import threading
from typing import Any

_log = logging.getLogger(__name__)

#: 로드한 모델을 재사용하기 위한 캐시. (모델 이름, 장치) → (model, processor)
_CACHE: dict[tuple[str, str], tuple[Any, Any]] = {}
_CACHE_LOCK = threading.Lock()

#: 생성 직렬화용. VLM 은 동시 호출에 안전하지 않다.
_GENERATE_LOCK = threading.Lock()

_DATA_URI = re.compile(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.+)$", re.S)


def available() -> bool:
    """로컬 VLM 을 돌릴 수 있는 환경인지.

    입력: 없음
    출력: transformers 와 torch 가 있으면 True
    """
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401

        return True
    except ImportError:
        return False


def _decode_images(image_urls: list[str] | None) -> list[Any]:
    """data URI 목록을 PIL 이미지로 바꾼다.

    입력: image_urls — `data:image/png;base64,...` 형태 목록
    출력: PIL.Image 목록. 해독할 수 없는 항목은 건너뛴다
    비고:
        호출부(assess/fill)는 HTTP 전송을 전제로 data URI 를 만든다.
        로컬 실행에서는 다시 이미지로 되돌려야 한다.
    """
    import io

    from PIL import Image

    images = []
    for url in image_urls or []:
        m = _DATA_URI.match(url.strip())
        if not m:
            _log.warning("data URI 가 아니어서 건너뜁니다: %s…", url[:40])
            continue
        try:
            raw = base64.b64decode(m.group(2))
            images.append(Image.open(io.BytesIO(raw)).convert("RGB"))
        except (binascii.Error, OSError, ValueError) as exc:
            _log.warning("이미지 해독 실패 — 건너뜁니다: %s", exc)
    return images


def _load(model_id: str, device: str, dtype: str) -> tuple[Any, Any]:
    """모델과 프로세서를 로드한다 (최초 1회, 이후 캐시).

    입력:
        model_id  HuggingFace 이름 또는 로컬 경로
        device    auto | cpu | cuda | cuda:0 …
        dtype     auto | float16 | bfloat16 | float32
    출력: (model, processor)
    예외: transformers 미설치 시 RuntimeError
    """
    key = (model_id, device)
    with _CACHE_LOCK:
        if key in _CACHE:
            return _CACHE[key]

    try:
        import torch
        from transformers import AutoModelForVision2Seq, AutoProcessor
    except ImportError as exc:
        raise RuntimeError(
            "로컬 VLM 을 쓰려면 transformers 와 torch 가 필요합니다.\n"
            "  pip install transformers torch"
        ) from exc

    torch_dtype = {
        "float16": torch.float16,
        "bfloat16": torch.bfloat16,
        "float32": torch.float32,
    }.get(dtype, "auto")

    _log.info("로컬 VLM 로드: %s (device=%s, dtype=%s)", model_id, device, dtype)
    processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
    model = AutoModelForVision2Seq.from_pretrained(
        model_id,
        dtype=torch_dtype,
        device_map=device if device != "auto" else "auto",
        trust_remote_code=True,
    )
    model.eval()

    with _CACHE_LOCK:
        _CACHE[key] = (model, processor)
    return model, processor


def clear_cache() -> None:
    """로드한 모델을 버린다 (설정 변경 시).

    입력: 없음
    출력: 없음
    """
    with _CACHE_LOCK:
        had = bool(_CACHE)
        _CACHE.clear()
    if had:
        try:
            import gc

            import torch

            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass


def invoke(
    prompt: str,
    *,
    image_urls: list[str] | None = None,
    model_id: str,
    device: str = "auto",
    dtype: str = "auto",
    max_new_tokens: int = 2048,
) -> str:
    """로컬 VLM 으로 한 번 호출한다.

    입력:
        prompt          보낼 텍스트
        image_urls      data URI 이미지 목록 (없어도 됨)
        model_id        HuggingFace 이름 또는 로컬 경로
        device          실행 장치
        dtype           가중치 정밀도
        max_new_tokens  생성 상한
    출력: 응답 본문 문자열
    비고: 생성은 락으로 직렬화한다 (VLM 은 동시 호출에 안전하지 않다).
    """
    import torch

    model, processor = _load(model_id, device, dtype)
    images = _decode_images(image_urls)

    content: list[dict[str, Any]] = [{"type": "image"} for _ in images]
    content.append({"type": "text", "text": prompt})
    messages = [{"role": "user", "content": content}]

    text = processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    inputs = processor(
        text=[text], images=images or None, return_tensors="pt"
    ).to(model.device)

    with _GENERATE_LOCK, torch.inference_mode():
        out = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)

    # 입력 토큰을 잘라내고 생성분만 디코딩한다.
    trimmed = out[:, inputs["input_ids"].shape[1] :]
    answer = processor.batch_decode(trimmed, skip_special_tokens=True)[0]
    return answer.strip()
