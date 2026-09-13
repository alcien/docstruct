"""막대그래프와 표 값의 교차 검산 — 화소를 재서 옮겨적기 오류를 잡는다.

입력:
    막대그래프 화소 + 표 값
출력:
    교차 검산 결과

역할:
    그래프 이미지에서 막대 높이를 **결정론으로 재고**, 같은 문서에 적힌
    값들과 비율이 맞는지 본다. 축 눈금을 읽지 않는다 — 눈금 대신 값들
    자체로 배율을 맞추므로 OCR·VLM 없이 돈다.
호출부:
    후속 배선 (지금은 순수 함수 + 시험 CLI)
설정:
    없음.

왜 필요한가
----------
표를 그래프로 그린 경우는 둘이 같은 원천이라 어긋날 일이 없다. 문제는
**반대 방향**이다 — 남의 그래프 이미지를 가져와 표로 옮겨 적을 때 오타나
오독이 들어가면, 표와 그래프가 서로 다른 말을 한다. 지면에는 둘 다
있으므로 **비교할 근거가 이미 있다.**

무엇을 잡고 무엇을 못 잡나 (조달청 59쪽 실측)
------------------------------------------
막대 높이 측정 자체의 오차가 약 0.8% 였다 (표 4,157/7,394/9,755/10,447 ↔
화소 환산 4,157/7,337/9,691/10,392). 그래서 문턱을 3% 로 두면:

    자리바꿈 9,755 → 7,955      21.8% 어긋남   **검출**
    자리바꿈 10,447 → 14,047    26.0%          **검출**
    자릿수 누락 10,447 → 1,447  618%           **검출**
    복사 실수 7,394 → 4,157     76.5%          **검출**
    끝자리 오타 10,447 → 10,477  0.8%          검출 못 함 (측정 오차 안)

즉 **자릿수·자리바꿈·복사 실수는 잡고, 끝자리 오타는 못 잡는다.** 못 잡는
쪽을 감추지 않는 것이 이 검산의 계약이다 — 끝자리는 표 내부 불변량
(structuring.checks 의 증가율 대조)이 맡는다.

배율은 **중앙값**으로 맞춘다. 첫 값으로 맞추면 그 값이 오타일 때 나머지가
전부 어긋난 것처럼 보인다 (실측: 첫 값 오타 시 first 는 3건 오탐, median 은
오타 1건만 지목).
"""
from __future__ import annotations

import logging
import statistics

_log = logging.getLogger(__name__)

#: 이보다 크게 어긋나면 불일치로 본다(%). 측정 오차 실측 0.8% 의 여유 배수.
TOLERANCE_PCT = 3.0
#: 막대로 볼 최소 화소 높이 — 범례 표식(실측 25px)을 거른다.
MIN_BAR_PX = 50
#: 막대로 볼 최소 화소 너비.
MIN_BAR_WIDTH = 30
#: 한 열이 막대에 속한다고 볼 최소 색 화소 수.
MIN_COLUMN_HITS = 20


def bar_heights(image_path) -> list[int] | None:
    """그래프 이미지에서 막대 높이(화소)를 왼쪽부터 낸다.

    입력: image_path — 그래프 PNG (원본 화소 권장 — media.native_image)
    출력: 높이 목록. 막대를 못 찾으면 None
    비고:
        색으로 막대를 가른다: 파랑이 우세한 화소 덩어리. 색 계열이 다른
        그래프는 여기서 물러난다(None) — 색 규칙을 늘리는 것은 표본이
        쌓인 뒤에 한다. 범례 표식은 높이·너비 문턱으로 걸러진다.
    """
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        return None
    try:
        image = np.asarray(Image.open(image_path).convert("RGB"), dtype=np.int16)
    except Exception as exc:                     # noqa: BLE001 - 보조 검산이다
        _log.debug("그래프 이미지를 열지 못했습니다: %s", exc)
        return None

    red, green, blue = image[..., 0], image[..., 1], image[..., 2]
    mask = (blue > red + 30) & (blue > green + 20) & (blue < 230)
    if not mask.any():
        return None

    columns = mask.sum(axis=0)
    spans: list[tuple[int, int]] = []
    start = None
    for x in range(mask.shape[1]):
        if columns[x] > MIN_COLUMN_HITS and start is None:
            start = x
        elif columns[x] <= MIN_COLUMN_HITS and start is not None:
            if x - start >= MIN_BAR_WIDTH:
                spans.append((start, x))
            start = None
    if start is not None and mask.shape[1] - start >= MIN_BAR_WIDTH:
        spans.append((start, mask.shape[1]))

    heights: list[int] = []
    for x0, x1 in spans:
        rows = mask[:, x0:x1].any(axis=1).nonzero()[0]
        if rows.size == 0:
            continue
        height = int(rows.max() - rows.min() + 1)
        if height >= MIN_BAR_PX:
            heights.append(height)
    return heights or None


def compare_series(heights: list[int], values: list[float]) -> dict | None:
    """막대 높이와 적힌 값들이 같은 비율인지 본다.

    입력: heights — 막대 화소 높이, values — 표에 적힌 값 (같은 순서)
    출력: {"checked","failed","tolerance_pct","failures":[…]}.
          개수가 다르거나 값이 모자라면 None (재지 않는다)
    비고:
        배율은 (화소÷값)의 **중앙값**이다. 하나가 틀려도 나머지가 눈금을
        지킨다. 0 이나 음수 값은 비율 검산의 대상이 아니라 건너뛴다.
    """
    if not heights or not values or len(heights) != len(values):
        return None
    pairs = [(h, float(v)) for h, v in zip(heights, values) if float(v) > 0]
    if len(pairs) < 2:
        return None

    scale = statistics.median(h / v for h, v in pairs)
    if scale <= 0:
        return None

    failures = []
    for index, (height, value) in enumerate(pairs):
        estimated = height / scale
        error = abs(estimated - value) / value * 100
        if error > TOLERANCE_PCT:
            failures.append({
                "index": index, "written": value,
                "measured": round(estimated), "error_pct": round(error, 1),
            })
    return {"checked": len(pairs), "failed": len(failures),
            "tolerance_pct": TOLERANCE_PCT, "failures": failures}


def verify_bar_chart(image_path, values: list[float]) -> dict | None:
    """그래프 이미지와 적힌 값들을 대조한다 (bar_heights + compare_series).

    입력: image_path — 그래프 이미지, values — 표에서 읽은 값
    출력: compare_series 와 같음. 막대를 못 찾으면 None
    """
    heights = bar_heights(image_path)
    if heights is None:
        return None
    return compare_series(heights, values)
