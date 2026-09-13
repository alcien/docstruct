"""초해상도 모델로 키운다 — **GPU 가 있을 때만.**

입력:
    그림 파일

역할:
    Swin2SR 로 이미지를 2배 키운다. 모델·GPU 가 없으면 조용히 물러나
    호출부가 lanczos 로 처리하게 한다.
호출부:
    images/image_prep (`DOCSTRUCT_IMAGE_UPSCALE=model` 일 때)
출력:
    upscale(path) → (결과 경로, 기록) 또는 (None, 사유)

왜 Swin2SR 인가
-------------
후보를 견주면:

    Swin2SR (caidas/swin2SR-*)   transformers 에 들어 있어 추가 설치가
                                 없다. **회귀 모델**이라 없는 획을 만드는
                                 성향이 확산 모델보다 낮다. ~50MB
    Real-ESRGAN                  별도 패키지·가중치. 사진 위주 학습
    LDM / SD Upscale             **확산 모델** — 그럴듯한 글자를 지어낸다.
                                 한글은 획 하나로 다른 글자가 되므로
                                 위험이 크다. 수 GB
    MSRN (eugenesiow/*)          super_image 라이브러리가 따로 필요

문서 글자에는 **지어내지 않는 것**이 가장 중요하다. 확산 모델은 흐린
`100%` 를 그럴듯한 `IOO%` 로 만들 수 있고, 그것을 VLM 이 다시 읽으면
오류가 두 번 겹친다. 그래서 회귀 모델인 Swin2SR 을 쓴다.

왜 GPU 가 있을 때만인가
--------------------
CPU 로 돌리면 한 장에 수십 초가 걸린다. 61개 문서에 그림이 283개이므로
현실적이지 않다. GPU 가 없으면 **lanczos 로 물러나는 편이 낫다** —
느린 것보다 못 하는 편이 낫다는 뜻이 아니라, 그 시간에 얻는 것이
lanczos 와 크게 다르지 않기 때문이다(회귀 모델의 이득은 확산 모델만큼
극적이지 않다).

`poor` 에는 쓰지 않는다
--------------------
원본 픽셀에 정보가 없는 그림(실측: `100%` 가 `IDD%` 로 박혀 있다)은
어떤 모델로도 복구되지 않는다. 모델을 쓰면 **그럴듯하게 메울 뿐**이다.
`image_prep._auto_plan` 이 `poor` 를 걸러 내므로 여기까지 오지 않는다.
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

_log = logging.getLogger(__name__)

#: 계열별 기본 모델. 문서 종류에 따라 갈아 끼운다.
#:
#:     image  자연 이미지 초해상도(Swin2SR). 사진·지도·도해처럼 **글자가
#:            주가 아닌** 그림에 맞는다. transformers 에 들어 있어 추가
#:            설치가 없다
#:     text   텍스트 특화 초해상도(STISR). 연구 목적이 정확히 "인식 불가한
#:            저해상도를 알아보게" 다 — TextZoom 계열은 인식률을
#:            41.4% → 49.8% 로 올린다(TPGSR)
#:
#: **텍스트 계열에는 큰 단서가 있다.** 학습·평가가 전부 `16×64` 크롭이다
#: — 장면 텍스트(간판)의 **단어 한 조각**을 32×128 로 키우는 과제다.
#: 우리 대상은 680×321 안에 라벨이 산재한 도표라 도메인이 다르다.
#: 그대로 넣으면 맞지 않고, 글자 영역을 찾아 잘라 각각 키운 뒤 되붙이는
#: 단계가 필요하다 — **아직 만들지 않았다.**
MODEL_PROFILES = {
    "image": "caidas/swin2SR-classical-sr-x2-64",
}

#: **텍스트 특화(STISR) 계열은 넣지 않는다** (0.4.43 판정).
#:
#: 연구 목적은 우리 문제와 정확히 맞는다 — TextZoom 계열은 "인식 불가한
#: 저해상도에서 복원" 을 목표로 하고 인식률을 41.4% → 49.8% 로 올린다.
#: 그런데 **한글 특화 모델을 찾지 못했다.** 라틴은 26자라 모델이 후보를
#: 좁히기 쉬운데 한글은 조합으로 1만 자가 넘고 획 하나로 `략` 이 `곽` 이
#: 된다 — 복원과 오독의 경계가 좁다. 중국어 획 복원 연구는 있으나 한글에
#: 그대로 쓸 수 없다.
#:
#: 모델이 확신을 갖고 틀린 글자를 만들면 VLM 이 그대로 읽는다. **틀린
#: 값이 그럴듯하게 들어가는 것이 비어 있는 것보다 나쁘다** — 지금
#: `poor` 는 설명으로 자리를 채우므로(0.4.33) 값을 잃지 않는다.
#:
#: 한글 특화 모델이 나오면 `MODEL_PROFILES` 에 더하고 `image_ab.py` 로
#: 재면 된다. 채점은 **본문 표의 값과 맞는가**로 한다.
_EXCLUDED_PROFILES = ("text",)

#: 기본 계열. x2 이면 획 6px → 12px 이라 목표에 맞는다.
DEFAULT_PROFILE = "image"
DEFAULT_MODEL = MODEL_PROFILES["image"]
#: 이보다 큰 이미지는 모델에 넣지 않는다 (메모리·시간).
MAX_INPUT_PIXELS = 4_000_000

#: 한 번 만든 파이프라인을 다시 쓴다 (모델 적재가 비싸다).
_CACHE: dict = {}


def profile() -> str:
    """쓸 계열 — `image`(기본) 또는 `text`.

    입력: 없음 (`DOCSTRUCT_SR_PROFILE`)
    출력: 계열 이름
    비고:
        지금은 `image` 하나뿐이다 — 자연 이미지 SR(Swin2SR). **지도·
        사진·도해처럼 글자가 주가 아닌 그림**이 대상이다(과기부·해양
        경찰청에 그런 것이 있다).

        텍스트 특화 계열은 뺐다 — 위 `_EXCLUDED_PROFILES` 의 이유를 보라.
        `DOCSTRUCT_SR_MODEL` 로 직접 지정하면 여전히 쓸 수 있으나,
        한글에서는 재고 쓰는 편이 좋다.
    """
    value = os.getenv("DOCSTRUCT_SR_PROFILE", "").strip().lower()
    return value if value in MODEL_PROFILES else DEFAULT_PROFILE


def model_name() -> str:
    """쓸 모델 이름 — 직접 지정이 계열보다 우선한다.

    입력: 없음 (`DOCSTRUCT_SR_MODEL` · `DOCSTRUCT_SR_PROFILE`)
    출력: 모델 이름. 계열에 기본값이 없고 지정도 없으면 빈 문자열
    비고:
        **계열에 기본값이 없으면 다른 계열로 흘러가지 않는다.** `text`
        를 골랐는데 조용히 자연 이미지 모델이 도는 것이 가장 나쁘다 —
        그 계열을 고른 이유가 사라진다. 빈 문자열을 돌려 `available()`
        이 사유와 함께 막는다.
    """
    explicit = os.getenv("DOCSTRUCT_SR_MODEL", "").strip()
    return explicit or MODEL_PROFILES.get(profile(), "")


def available() -> tuple[bool, str]:
    """모델 확대를 쓸 수 있는가.

    입력: 없음
    출력: (가능 여부, 사유)
    비고:
        **GPU 를 요구한다.** `DOCSTRUCT_SR_ALLOW_CPU=1` 로 풀 수 있으나
        한 장에 수십 초가 걸린다 — 대량 처리에는 맞지 않는다.
    """
    # **계열 판정을 먼저 한다.** torch 유무보다 앞이어야 "이 계열은 쓰지
    # 않는다" 는 사유가 드러난다 — 뒤에 두면 환경 문제로만 보인다.
    requested = os.getenv("DOCSTRUCT_SR_PROFILE", "").strip().lower()
    if requested in _EXCLUDED_PROFILES and not os.getenv("DOCSTRUCT_SR_MODEL"):
        return False, ("텍스트 특화(STISR) 계열은 쓰지 않습니다 — 한글 특화 "
                       "모델이 없어 오독 위험이 큽니다. DOCSTRUCT_SR_MODEL 로 "
                       "직접 지정하면 쓸 수 있습니다")

    try:
        import torch                             # noqa: F401
    except ImportError:
        return False, "torch 가 없습니다"
    try:
        import transformers                      # noqa: F401
    except ImportError:
        return False, "transformers 가 없습니다"

    import torch

    if torch.cuda.is_available():
        return True, f"cuda:{torch.cuda.get_device_name(0)}"
    if os.getenv("DOCSTRUCT_SR_ALLOW_CPU", "").strip() == "1":
        return True, "cpu(허용됨 — 느립니다)"
    return False, "GPU 가 없습니다 (DOCSTRUCT_SR_ALLOW_CPU=1 로 강제 가능)"


def _pipeline():
    """모델과 전처리기를 얻는다 (한 번만 적재)."""
    key = model_name()
    if key in _CACHE:
        return _CACHE[key]
    import torch
    from transformers import AutoImageProcessor, Swin2SRForImageSuperResolution

    processor = AutoImageProcessor.from_pretrained(key)
    model = Swin2SRForImageSuperResolution.from_pretrained(key)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = model.to(device).eval()
    _CACHE[key] = (processor, model, device)
    return _CACHE[key]


def upscale(image_path: str | Path) -> tuple[str | None, str]:
    """모델로 2배 키운다.

    입력: image_path — 원본 경로
    출력: (결과 경로, 기록 문자열). 못 하면 (None, 사유)
    비고:
        실패는 사유를 돌려주고 끝난다 — 호출부가 lanczos 로 물러난다.
        모델 적재는 처음 한 번만 걸린다(수 초~수십 초).
    """
    ok, reason = available()
    if not ok:
        return None, reason

    try:
        import numpy as np
        import torch
        from PIL import Image
    except ImportError as exc:
        return None, f"의존성 없음: {exc}"

    try:
        with Image.open(image_path) as handle:
            image = handle.convert("RGB")
            if image.width * image.height > MAX_INPUT_PIXELS:
                return None, f"이미지가 큽니다 ({image.width}x{image.height})"
            processor, model, device = _pipeline()
            inputs = processor(image, return_tensors="pt").to(device)
            with torch.no_grad():
                output = model(**inputs)
        array = output.reconstruction.data.squeeze().float().cpu().clamp_(0, 1)
        array = (np.moveaxis(array.numpy(), 0, -1) * 255.0).round().astype(np.uint8)
        result = Image.fromarray(array)
    except Exception as exc:                     # noqa: BLE001 - 실패하면 물러난다
        _log.warning("초해상도 모델 실패: %s", exc)
        return None, f"모델 실패: {exc}"

    out_dir = Path(tempfile.gettempdir()) / "docstruct-sr"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{Path(image_path).stem}_sr.png"
    result.save(target)
    return str(target), f"{profile()}:{model_name()} ({reason})"
