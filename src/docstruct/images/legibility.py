"""그림이 읽을 만한지 잰다 — dpi 가 아니라 **글자 획 높이**로.

입력:
    그림 파일

역할:
    이미지 안 글자가 몇 픽셀인지 재서 판독 가능성을 판정한다. 그 결과가
    전처리·확대를 걸지, 아예 건너뛸지, VLM 지시문을 바꿀지를 정한다.
호출부:
    images/vlm_read · images/image_prep · 시험 꾸러미(image_survey)
출력:
    measure(path) → {"glyph_px", "verdict", "width", "height", "sharpness"}

왜 dpi 가 아닌가
--------------
dpi 는 "지면에 얼마나 크게 놓였나" 에 좌우된다. 실제로 판독을 가르는
것은 **글자 한 자가 몇 픽셀인가**다. 실측으로 순서가 뒤집힌다.

    국방부 막대그래프   80dpi · 획  8px → 축 값·데이터 라벨까지 다 읽힘
    조달청 조직도      167dpi · 획 11px → VLM 이 계층·정원표까지 복원
    행안부 조직도      122dpi · 획  8px → 읽힘
    원그래프(3문서 공통) 102dpi · 획  6px → **글자가 이미 깨져 있다**
                                          (`100%` → `IDD%`, `략` → `곽`)

80dpi 가 102dpi 보다 잘 읽힌다. 지면에 작게 놓여 dpi 는 낮지만 글자는
충분한 것이다. 그래서 dpi 로 고르면 멀쩡한 것을 손보고 못 읽는 것을
놓친다.

**6px 가 한글의 한계선**으로 보인다. 획이 많아 6px 에서 무너진다 —
라틴 문자는 같은 크기에서 버틴다(원그래프의 `%` 는 살아 있다).
"""
from __future__ import annotations

import logging
from pathlib import Path
from pathlib import Path as pathlib_Path

_log = logging.getLogger(__name__)

#: cv2 미설치 경고를 한 번만 낸다 (그림마다 찍으면 로그가 묻힌다).
_WARNED: list[bool] = []

#: 이 값 **이하**면 한글이 무너진다 — 원본에 이미 정보가 없다.
#:
#: 처음에 6px 로 잡았다가 7px 로 올렸다(0.4.32). 눈으로 확인한 조달청
#: 원그래프(`100%` 가 `IDD%` 로 박힌 그것)가 **7px 로 측정돼 `fair`**
#: 판정을 받았기 때문이다 — 명백히 깨진 그림이 "보정하면 된다" 로
#: 분류됐다.
#:
#: 실측으로 확인한 것:
#:     6px  깨짐 (원그래프)
#:     7px  **깨짐** (같은 원그래프 · 다른 문서)
#:     8~9px 읽힘 (국방부 막대그래프 · 행안부 조직도)
#:     11px  잘 읽힘 (조달청 조직도)
POOR_GLYPH_PX = 7
#: 이 이상이면 손댈 것이 없다.
GOOD_GLYPH_PX = 10
#: 장식으로 볼 크기. **한 변이 아니라 넓이로 본다** — 막대그래프처럼
#: 납작한 그림(458×166)이 한 변 기준에 걸려 장식으로 오판됐다. 실측
#: (국방부 image4): 80dpi·획 8px 로 축 값·데이터 라벨까지 다 읽히는데도
#: 높이 166 때문에 걸렸다. 도형 장식은 넓이가 훨씬 작다(14×91 = 1,274).
MIN_AREA = 20_000
#: 글자로 셀 연결요소의 크기 범위(px).
_MIN_BOX, _MAX_BOX = 3, 60
#: 가로세로 비가 이보다 크면 글자가 아니라 선분이다.
_MAX_ASPECT = 3.0
#: 외접 사각형을 이 비율 미만으로 채우면 속 빈 도형(상자 테두리)이다.
_MIN_FILL = 0.25

#: 스캔 지면으로 볼 최소 글자 줄 수·줄당 조각 수.
MIN_PAGE_ROWS = 12
MIN_PER_ROW = 15.0


def imread_unicode(path, flags=None):
    """한글 경로에서도 이미지를 읽는다.

    입력: path — 이미지 경로, flags — cv2 읽기 플래그
    출력: ndarray. 못 읽으면 None
    비고:
        **`cv2.imread` 는 윈도우에서 한글 경로를 못 연다.** 내부적으로
        ANSI 로 바꾸면서 파일명이 깨진다 — 실측:

            can't open/read file: '洹몃┝\\fair\\...怨쇳븰湲곗닠...png'

        파일은 멀쩡한데 열지를 못한다. 바이트로 읽어 메모리에서 디코딩
        하면 경로 인코딩을 거치지 않는다.
    """
    import cv2
    import numpy as np

    try:
        data = np.fromfile(str(path), dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_GRAYSCALE if flags is None else flags)


def imwrite_unicode(path, image) -> bool:
    """한글 경로에도 이미지를 쓴다.

    입력: path — 저장 경로, image — ndarray
    출력: 성공 여부
    비고: `cv2.imwrite` 도 같은 이유로 한글 경로에서 실패한다.
    """
    import cv2

    suffix = pathlib_Path(path).suffix or ".png"
    ok, buffer = cv2.imencode(suffix, image)
    if not ok:
        return False
    try:
        buffer.tofile(str(path))
    except OSError:
        return False
    return True


def measure(image_path: str | Path) -> dict:
    """이미지의 판독 가능성을 잰다.

    입력: image_path — 이미지 경로
    출력: {"glyph_px", "verdict", "width", "height", "sharpness"}
          못 재면 {"verdict": "unknown"}
    비고:
        판정은 셋이다.

            decoration  장식 — 글자가 없다. **VLM 에 보내지 않는다**
            poor        글자가 6px 이하 — 원본에 정보가 없다
            fair        7~9px — 읽히지만 여유가 없다
            good        10px 이상 — 그대로 보낸다

        `decoration` 은 크기로 먼저 거른다. 실측(국방부): 14×91 픽셀짜리
        가느다란 삼각형이 dpi 23 으로 잡혀 "가장 급한 문서" 2위로
        올라왔다 — 판독 대상이 아니다.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        # **조용히 넘어가지 않는다.** cv2 가 없으면 모든 그림이
        # `unknown` 이 되고, 그것을 "손댈 것 없음" 으로 읽으면 **문제가
        # 없는 것처럼 보인다** — 실측: 61건 조사에서 전부 "그대로" 로
        # 나와 판정이 통째로 무의미했다.
        if not _WARNED:
            _WARNED.append(True)
            _log.warning(
                "opencv 가 없어 판독 가능성을 잴 수 없습니다 — 모든 그림이 "
                "'unknown' 이 됩니다. `pip install opencv-python-headless` "
                "로 설치하세요")
        return {"verdict": "unknown"}

    array = imread_unicode(image_path, cv2.IMREAD_GRAYSCALE)
    if array is None:
        return {"verdict": "unknown"}
    height, width = array.shape[:2]

    if width * height < MIN_AREA:
        return {"verdict": "decoration", "width": width, "height": height,
                "glyph_px": None, "sharpness": None}

    sharpness = float(cv2.Laplacian(array, cv2.CV_64F).var())
    _, binary = cv2.threshold(array, 0, 255,
                              cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    _n, _labels, stats, _cent = cv2.connectedComponentsWithStats(binary)
    # **글자만 골라 잰다.** 도표는 상자 테두리·연결선이 작은 조각으로
    # 잔뜩 잡혀 중앙값을 끌어내린다 — 실측(과기부 논리모형): 상자 안
    # 글자가 다 읽히는데 5px `poor` 로 판정됐다. 글자는 세로로 길거나
    # 정사각에 가깝고(가로선은 아니다), 상자를 채우는 비율이 높다.
    heights = []
    for stat in stats[1:]:
        left, top, w, h, area = (int(v) for v in stat)
        if not (_MIN_BOX <= h <= _MAX_BOX and _MIN_BOX <= w <= _MAX_BOX):
            continue
        if w > h * _MAX_ASPECT or h > w * _MAX_ASPECT:
            continue                             # 선분·막대
        if area < w * h * _MIN_FILL:
            continue                             # 속 빈 사각형(상자 테두리)
        heights.append(h)
    if not heights:
        return {"verdict": "decoration", "width": width, "height": height,
                "glyph_px": None, "sharpness": sharpness}

    glyph = int(np.median(heights))
    lines, per_line = _text_rows(stats, height)
    if glyph <= POOR_GLYPH_PX:
        verdict = "poor"
    elif glyph < GOOD_GLYPH_PX:
        verdict = "fair"
    else:
        verdict = "good"
    return {"glyph_px": glyph, "verdict": verdict,
            "width": width, "height": height, "sharpness": sharpness,
            "text_rows": lines, "per_row": per_line,
            "kind": "page" if _looks_like_page(lines, per_line) else "figure"}


def _text_rows(stats, height: int) -> tuple[int, float]:
    """글자 조각이 몇 줄로 늘어서 있나.

    입력: stats — 연결요소 통계, height — 이미지 높이
    출력: (줄 수, 줄당 조각 수 중앙값)
    비고:
        **스캔된 지면과 도표를 가르는 신호다.** 지면은 글자가 여러 줄로
        규칙적으로 늘어서고, 도표·그래프·지도는 산재한다.
    """
    import numpy as np

    centers = [float(s[1] + s[3] / 2) for s in stats[1:]
               if _MIN_BOX <= s[3] <= _MAX_BOX]
    if not centers:
        return 0, 0.0
    band = max(4.0, height / 120.0)              # 같은 줄로 볼 세로 폭
    buckets: dict[int, int] = {}
    for value in centers:
        key = int(value / band)
        buckets[key] = buckets.get(key, 0) + 1
    rows = [n for n in buckets.values() if n >= 3]
    return len(rows), float(np.median(rows)) if rows else 0.0


def _looks_like_page(rows: int, per_row: float) -> bool:
    """스캔된 지면인가 (전사해야 하는가).

    입력: rows — 글자 줄 수, per_row — 줄당 조각 수 중앙값
    출력: 지면이면 True
    비고:
        **면적 비율로 가르면 안 된다.** 실측: `ratio >= 0.55` 로 잡힌
        셋이 전부 스캔본이 아니라 큰 도표였다 — 해양경찰청 해상 지도,
        과기부 논리모형, 대법원 꺾은선그래프. 전사 지시문은 그런 그림에
        맞지 않는다.

        지면은 글자가 **여러 줄로 빽빽하게** 늘어선다. 도표는 줄 수가
        적거나 줄당 글자가 성기다.

        **조직도는 여기 걸린다** — 상자마다 글자가 줄지어 있어 지면과
        신호가 비슷하다(실측: 조달청 조직도 줄 52·줄당 19). 그래도
        전사 지시문이 해롭지 않다: 조직도에서 "보이는 글을 그대로
        옮기라" 고 하면 계층째로 옮겨 오고, 실제로 그렇게 복원됐다.
        가려야 할 것은 **지도·그래프처럼 옮길 글이 없는 그림**이고,
        그것들은 줄당 조각이 성겨 걸리지 않는다.
    """
    return rows >= MIN_PAGE_ROWS and per_row >= MIN_PER_ROW
