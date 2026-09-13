"""그림을 VLM 에 보내기 전에 다듬는다 — 손잡이로 켠다.

입력:
    그림 파일

역할:
    흐리거나 기울어진 그림을 보정하고, 필요하면 키운다. 기본은 **꺼짐**
    이다 — 효과를 재기 전에는 켜지 않는다.
호출부:
    images/vlm_read (그림 판독 직전)
출력:
    prepare(path) → (보낼 이미지 경로, 적용 기록)

왜 필요한가
--------
**배율은 원본에 없는 정보를 만들지 못한다.** PDF 는 지면을 다시 렌더해
해상도를 올릴 수 있지만(0.4.24), **HWPX·HWP 는 그 이미지가 원본
자체**라 손쓸 방법이 없다. 실측(조달청·행안부 HWPX): 그림 다섯 개가
102~167dpi 이고 조직도도 그 안에 있다(행안부 122dpi).

흐린 원본을 그대로 보내면 VLM 이 무엇으로 읽든 한계가 있다. 남은 수단이
전처리와 확대뿐이다.

손잡이
-----
    DOCSTRUCT_IMAGE_PREPROCESS = off | basic | full
        off    그대로 보낸다 (기본)
        basic  대비 정규화 · 노이즈 제거 · 기울기 보정
        full   basic + 선명화(unsharp mask)

    DOCSTRUCT_IMAGE_UPSCALE = off | lanczos | model
        off      그대로 (기본)
        lanczos  고전 보간으로 목표 dpi 까지. **획을 지어내지 않는다**
        model    초해상도 모델 (`images/super_resolution` · 0.4.30 —
                 **GPU 가 있을 때만** 돌고 없으면 lanczos 로 물러난다)

`lanczos` 를 중간 단계로 둔 것은 의도다. 모델 없이 가볍고, 없는 획을
만들지 않으므로 **위험이 없다.** 122dpi 를 300dpi 상당으로 올리는 것
만으로 나아지는지부터 보고, 그래도 부족할 때 모델을 검토한다.

초해상도 모델을 기본으로 켜지 않는 이유
----------------------------------
문서용 초해상도 모델은 학습 데이터가 사진 위주라 글자에서 **없는 획을
지어낸다.** 한글은 획 하나로 다른 글자가 되므로, 모델이 지어낸 획을
VLM 이 다시 해석하면 **오류가 두 번 겹친다** — `공금운농` 같은 오독이
더 그럴듯해질 수 있다. 컨테이너 무게(수백 MB)와 GPU 배분도 걸린다.

(이 머리글은 0.4.30 이전 "미구현" 으로 남아 있었다 — 0.4.83 정정.
 코드는 `upscale_mode() == "model"` 에서 실제로 모델을 부른다.)
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

_log = logging.getLogger(__name__)

#: 확대 목표 해상도(dpi). 획 높이를 모를 때만 쓴다.
TARGET_DPI = 300.0

#: 확대 목표 글자 획 높이(px). `good` 판정선(10px)에 여유를 둔다.
TARGET_GLYPH_PX = 12.0
#: 한 번에 키울 수 있는 최대 배수. 4배를 넘으면 보간이 뭉갠다.
MAX_UPSCALE = 4.0
#: 확대 뒤 최대 변 길이(화소). VLM 토큰이 터지는 것을 막는다.
MAX_SIDE = 4000


def preprocess_mode() -> str:
    """전처리 단계 — auto(기본) · off · basic · full.

    비고:
        `auto` 는 지금 **off 와 같다** (0.4.41 — `_auto_plan` 이 어느
        판정에도 걸지 않는다). 값이 auto 인 것은 판정 규칙을 되살릴 자리를
        남긴 것뿐이다. 켜려면 basic·full 을 명시한다 — VLM 판독에는 -11%
        실측이 있으니 OCR 로 읽을 때(`DOCSTRUCT_SCAN_BACKEND=ocr`)만.
    """
    value = os.getenv("DOCSTRUCT_IMAGE_PREPROCESS", "").strip().lower()
    return value if value in ("off", "basic", "full") else "auto"


def upscale_mode() -> str:
    """확대 방식 — auto(기본) · off · lanczos · model.

    비고:
        `auto` 는 지금 **off 와 같다** (0.4.41 — preprocess_mode 와 같은
        사정). 켜려면 lanczos·model 을 명시한다 — OCR 판독에는 이득
        (숫자 34→48개 실측), VLM 판독에는 손해다.
    """
    value = os.getenv("DOCSTRUCT_IMAGE_UPSCALE", "").strip().lower()
    return value if value in ("off", "lanczos", "model") else "auto"


def _deskew(array):
    """기울기를 바로잡는다 (cv2).

    입력: array — 회색조 ndarray
    출력: 회전된 ndarray. 각도가 작으면 원본 그대로
    비고:
        스캔은 대개 1~3도 기울어 있다. 그 정도로도 OCR 은 눈에 띄게
        나빠진다. **5도를 넘으면 손대지 않는다** — 그만큼 기울었다면
        글자가 아니라 도형일 가능성이 크고, 잘못 돌리면 더 나빠진다.
    """
    import cv2
    import numpy as np

    edges = cv2.Canny(array, 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=100,
                            minLineLength=array.shape[1] // 3, maxLineGap=20)
    if lines is None:
        return array
    # **반환 모양에 기대지 않는다.** OpenCV 판에 따라 `[[x1,y1,x2,y2]]`
    # 로도, 평평하게도 온다 — 실측(윈도우): `line[0]` 이 numpy.int32 라
    # 언패킹이 터졌다(`cannot unpack non-iterable numpy.int32`).
    # 평평하게 편 뒤 넷씩 끊는다.
    flat = np.asarray(lines).reshape(-1)
    angles = []
    for index in range(0, min(flat.size, 800) - 3, 4):
        x1, y1, x2, y2 = (float(v) for v in flat[index:index + 4])
        if x2 == x1:
            continue
        angle = float(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
        if abs(angle) < 15:                      # 가로선만 본다
            angles.append(angle)
    if not angles:
        return array
    angle = float(np.median(angles))
    if abs(angle) < 0.3 or abs(angle) > 5.0:
        return array
    h, w = array.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(array, matrix, (w, h),
                          flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_REPLICATE)


def _stage(array, image_path) -> str | None:
    """지금까지 손본 결과를 임시 파일로 낸다 (모델 입력용)."""
    import cv2

    out = Path(tempfile.gettempdir()) / "docstruct-imageprep"
    out.mkdir(parents=True, exist_ok=True)
    staged = out / f"{Path(image_path).stem}_stage.png"
    from docstruct.images.legibility import imwrite_unicode

    return str(staged) if imwrite_unicode(staged, array) else None


def _auto_plan(legible: dict | None) -> tuple[str, str]:
    """판독 가능성으로 무엇을 걸지 정한다 (규칙 기반).

    입력: legible — legibility.measure 의 결과
    출력: (전처리 단계, 확대 방식)
    비고:
        **어느 판정에도 보정을 걸지 않는다** (0.4.41). 손잡이는 남겨
        두되 자동으로는 쓰지 않는다.

        처음에는 `fair`(획 7~9px)에 `basic + lanczos` 를 걸었다. 기계적
        지표가 좋았기 때문이다 — fair 45장 전수에서 획이 41장 올라가고
        내려간 것이 0, 변화 중앙값 +4px 로 `good` 구간에 들었다.

        **그러나 실제 판독은 반대였다.** 같은 그림을 보정 없이 한 번,
        보정하고 한 번 VLM 에 읽혀 견주니(`notebooks/image_ab.py`, 10장):

            개선 1 · 비슷 5 · **악화 4**
            글자 합계 6,434 → 5,753 (-11%)
            국가보훈부 image1: 읽어낸 **숫자가 10개 → 3개**

        획 높이는 픽셀 수로 재므로 확대하면 오른다. 그러나 lanczos 는
        정보를 늘리지 않고 **흐리게 퍼뜨린다** — 경계가 뭉개져 모델이
        구분하지 못한다.

        **다만 그 A/B 는 `basic + lanczos` 를 한꺼번에 걸었다.** 셋 중
        무엇이 해로웠는지는 가르지 못했다 — 확대일 수도, 대비 정규화가
        얇은 획을 끊은 것일 수도(국방부 막대그래프에서 9px → 4px 로
        떨어졌다), 노이즈 제거가 작은 글자를 뭉갠 것일 수도 있다.
        결론(끄기)은 같으나 **"lanczos 때문" 은 근거를 넘어선 말이다.**
        `notebooks/image_ab.py` 가 이제 다섯 조합을 하나씩 떼어 잰다. 조달청 원그래프에서 확대 뒤 선명도가
        6477 → 187 로 떨어진 것이 같은 현상이었다.

        대통령경호처 조직도(8px)는 보정 유무와 **글자 하나까지 같았다**
        — 이 구간에서 모델은 이미 충분히 읽고 있고 보정이 더 줄 것이
        없다.

        **기계적 지표가 좋아 보여도 실제로 읽혀 봐야 안다.**

        읽는 주체가 OCR 이면 반대다
        ---------------------------
        같은 확대가 **OCR 에는 도움이 된다.** 실측(국방부 막대그래프를
        tesseract 로 읽어 견줌):

            원본        획  9px · 숫자 34개 · 글자  86
            lanczos x2  획  5px · 숫자 43개 · 글자 171
            lanczos x3  획  8px · 숫자 **48개** · 글자 192
            lanczos x4  획 10px · 숫자 49개 · 글자 186

        원본에서는 연도가 `2029`·`2035`·`2088` 로 깨졌는데(실제
        2032·2038·2040) x3 에서는 `45.7` 같은 **데이터 라벨이 새로
        잡혔다.**

        갈리는 이유는 이미지를 쓰는 방식이 다르기 때문이다.

            OCR  글자 하나하나를 잘라 분류한다. 획이 5~10px 면 분류기
                 입력에 못 미쳐 뭉개지므로, 키우면 경계가 살아난다 —
                 흐려도 **크면** 낫다
            VLM  이미지를 패치로 잘라 토큰화한다. 이미 충분하면 키워도
                 정보가 늘지 않고, 같은 내용이 더 많은 토큰에 흐리게
                 퍼져 주의가 분산된다

        STISR 연구가 성능을 **OCR 인식률**로 재는 것이 이 때문이다
        (TPGSR 41.4% → 49.8%). 그 파이프라인에서는 확대가 정상 작동한다.

        지금 구조는 VLM 중심이라 기본을 끈다. **스캔 쪽을 OCR 로 읽을
        때는 켜는 편이 낫다** — `DOCSTRUCT_SCAN_BACKEND=ocr` 과 함께
        `DOCSTRUCT_IMAGE_UPSCALE=lanczos` 를 쓴다.
    """
    return "off", "off"


def prepare(image_path: str | Path,
            dpi: float | None = None,
            legible: dict | None = None) -> tuple[str, dict]:
    """VLM 에 보낼 이미지를 준비한다.

    입력: image_path — 원본 경로, dpi — 실제 해상도,
          legible — legibility.measure 결과 (auto 판정에 쓴다)
    출력: (보낼 경로, 적용 기록). 아무것도 안 하면 (원본 경로, {})
    비고:
        **원본을 덮어쓰지 않는다.** 임시 파일로 만들어 그 경로를 준다 —
        되돌릴 수 없는 변형은 만들지 않는다.

        어떤 손잡이가 걸렸는지 기록으로 돌려준다. 그것이 없으면 A/B 때
        어느 결과가 어느 설정이었는지 결과물에서 가릴 수 없다.
    """
    pre, up = preprocess_mode(), upscale_mode()
    if pre == "auto" or up == "auto":
        auto_pre, auto_up = _auto_plan(legible)
        pre = auto_pre if pre == "auto" else pre
        up = auto_up if up == "auto" else up
    if pre == "off" and up == "off":
        return str(image_path), {}

    try:
        import cv2
        import numpy as np
    except ImportError:
        _log.info("cv2 가 없어 이미지 보정을 건너뜁니다")
        return str(image_path), {}

    from docstruct.images.legibility import imread_unicode, imwrite_unicode

    array = imread_unicode(image_path, cv2.IMREAD_GRAYSCALE)
    if array is None:
        return str(image_path), {}

    applied: dict = {}

    if pre in ("basic", "full"):
        # 대비 정규화 — 배경 얼룩과 낮은 대비를 함께 잡는다.
        array = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(array)
        # 노이즈 제거. 획을 지우지 않도록 약하게 건다.
        array = cv2.fastNlMeansDenoising(array, None, h=7,
                                         templateWindowSize=7,
                                         searchWindowSize=21)
        try:
            array = _deskew(array)
        except Exception as exc:                 # noqa: BLE001
            # **기울기 보정 실패가 전체를 막지 않는다.** 나머지 보정
            # (대비·노이즈)은 이미 적용됐고 그것만으로도 이득이 있다.
            _log.debug("기울기 보정 실패 — 건너뜁니다: %s", exc)
        applied["preprocess"] = pre

    if pre == "full":
        # 선명화 — 원본에서 흐려진 획의 경계를 세운다. **없는 획을
        # 만들지는 않는다**(모델과 다른 점이다).
        blurred = cv2.GaussianBlur(array, (0, 0), 3)
        array = cv2.addWeighted(array, 1.5, blurred, -0.5, 0)

    if up == "lanczos":
        # **배율은 글자 획 높이로 정한다.** dpi 는 지면 배치에 좌우돼
        # 판독과 어긋난다(80dpi 가 102dpi 보다 잘 읽힌다). 목표는 획
        # 12px — good 판정선(10px)에 여유를 둔 값이다. dpi 밖에 모르면
        # 그것으로 물러난다.
        glyph = (legible or {}).get("glyph_px")
        if glyph:
            factor = min(TARGET_GLYPH_PX / glyph, MAX_UPSCALE)
        elif dpi and dpi < TARGET_DPI:
            factor = min(TARGET_DPI / dpi, MAX_UPSCALE)
        else:
            factor = 1.0
        height, width = array.shape[:2]
        if max(width, height) * factor > MAX_SIDE:
            factor = MAX_SIDE / max(width, height)
        if factor > 1.05:
            array = cv2.resize(array, None, fx=factor, fy=factor,
                               interpolation=cv2.INTER_LANCZOS4)
            applied["upscale"] = f"lanczos x{factor:.2f}"
    elif up == "model":
        # **GPU 가 있을 때만 돈다.** 없으면 lanczos 로 물러난다 — CPU 로는
        # 한 장에 수십 초가 걸려 대량 처리에 맞지 않는다.
        from docstruct.images.super_resolution import upscale as sr_upscale

        # 전처리를 먼저 저장해야 모델이 그것을 받는다.
        staged = _stage(array, image_path)
        result, note = sr_upscale(staged or image_path)
        if result:
            from docstruct.images.legibility import imread_unicode

            loaded = imread_unicode(result, cv2.IMREAD_GRAYSCALE)
            if loaded is not None:
                array = loaded
                applied["upscale"] = f"model {note}"
        else:
            glyph = (legible or {}).get("glyph_px")
            factor = min(TARGET_GLYPH_PX / glyph, MAX_UPSCALE) if glyph else 1.0
            if factor > 1.05:
                array = cv2.resize(array, None, fx=factor, fy=factor,
                                   interpolation=cv2.INTER_LANCZOS4)
                applied["upscale"] = f"lanczos x{factor:.2f} (모델 불가: {note})"
            else:
                applied["upscale"] = f"건너뜀 (모델 불가: {note})"

    if not applied:
        return str(image_path), {}

    out = Path(tempfile.gettempdir()) / "docstruct-imageprep"
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{Path(image_path).stem}_prep.png"
    if not imwrite_unicode(target, array):
        return str(image_path), {}

    # **나빠졌으면 되돌린다.** 보정이 늘 이득은 아니다 — 실측(국방부
    # 막대그래프 458×166): 대비 정규화가 얇은 축선을 끊어 획 판정이
    # 9px → 4px 로 떨어졌다. 손대서 나빠질 바에는 원본을 보낸다.
    if legible and legible.get("glyph_px"):
        from docstruct.images.legibility import measure

        after = measure(target)
        if (after.get("glyph_px") or 0) < legible["glyph_px"]:
            _log.debug("보정이 판독을 낮춰 원본을 씁니다: %s", image_path)
            return str(image_path), {"skipped": "보정 뒤 판독이 낮아짐"}
    return str(target), applied
