"""OCR 언어 오판 탐지 — 한글 지면이 한자로 나온 자리를 찾는다.

입력:
    쪽 본문
출력:
    한자 오판 자리

역할:
    추출된 텍스트에서 **한자 비율**을 재어, 중국어 모델 OCR 산물인지
    가른다. 탐지는 결정론이고, 복구는 기존 경로(한국어 재판독 · VLM
    재구성)가 맡는다.
호출부:
    pipeline (표 신호 · 재판독 대상 선정)
설정:
    없음.

왜 필요한가 (조달청 실측)
----------------------
텍스트 PDF 안에 **래스터로 붙인 쪽**이 있으면(조직도·정원표 등) 그
영역은 docling 내장 OCR 이 읽는다. 그 기본 모델은 중국어라 한글이
한자로 나온다:

    9쪽 table_4   | 7是 | 77 | 歪 |   ← "구 분 / 기 구 / 기준정원"
                  한자 10자 / 낱말 12자 = 0.83

그런데 그 쪽의 **원본 텍스트 레이어에는 한자가 없다**(제목 21자뿐).
그래서 쪽 단위 텍스트 레이어 판정은 "쓸 만함" 으로 통과했고, 우리 한국어
재판독은 건너뛰었다 — 지면 대부분이 래스터인 쪽을 절대 글자 수만으로는
가릴 수 없다는 뜻이다. **결과물**을 보는 이 검사가 그 사각을 메운다.

문턱의 근거
---------
    조달청 59표   한자 0인 표 57 · 정상 한자 사용 비율 0.004~0.035
                  (軍·前中後·單價 같은 병기)
    문제 표         0.83
    HWPX 580표    최대 0.035

정상 사용과 오판 사이가 20배 이상 벌어져 있어, 0.15 는 넉넉히 안전하다.
"""
from __future__ import annotations

import re

#: 한자(CJK 통합 한자) 한 글자.
_HAN = re.compile(r"[\u4e00-\u9fff]")
#: 낱말 글자 — 한글·라틴·한자. 기호·숫자는 언어 판정에 쓰지 않는다.
_WORD = re.compile(r"[가-힣A-Za-z\u4e00-\u9fff]")

#: 이 비율을 넘으면 언어 오판으로 본다. 실측 정상 최대 0.035, 오판 0.83.
MAX_HAN_RATIO = 0.15
#: 한자가 이보다 적으면 재지 않는다 — 병기 한두 자로 표를 흔들지 않는다.
MIN_HAN_CHARS = 5


def han_ratio(text: str | None) -> float:
    """낱말 글자 중 한자 비율.

    입력: text — 검사할 텍스트
    출력: 0.0~1.0 (낱말 글자가 없으면 0.0)
    """
    if not text:
        return 0.0
    words = _WORD.findall(text)
    if not words:
        return 0.0
    return len(_HAN.findall(text)) / len(words)


def wrong_language(text: str | None) -> dict | None:
    """중국어 모델 OCR 산물로 보이는가.

    입력: text — 검사할 텍스트
    출력: {"han": n, "words": n, "ratio": r} 이면 의심. 아니면 None
    비고:
        한자 **수**와 **비율**을 함께 본다. 비율만 보면 짧은 칸("軍")이
        걸리고, 수만 보면 한자를 많이 병기한 긴 문서가 걸린다.
    """
    if not text:
        return None
    words = _WORD.findall(text)
    han = _HAN.findall(text)
    if len(han) < MIN_HAN_CHARS or not words:
        return None
    ratio = len(han) / len(words)
    if ratio < MAX_HAN_RATIO:
        return None
    return {"han": len(han), "words": len(words), "ratio": round(ratio, 2)}
