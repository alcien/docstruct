"""시험 공통 설정 — 환경변수를 시험 사이에 새지 않게 한다.

역할:
    docstruct 설정은 환경변수에서 온다. 어떤 시험이 `os.environ` 을 직접
    건드리면 그 값이 뒤 시험까지 따라가, **혼자 돌리면 통과하고 전체를
    돌리면 실패하는** 시험이 생긴다.
호출부:
    pytest 가 자동으로 읽는다.

왜 필요했나 (0.4.61)
------------------
`docstruct.set_api_key()` 는 `os.environ` 을 직접 세운다(그것이 그 함수의
일이다). 그것을 시험하는 쪽은 `monkeypatch.delenv(..., raising=False)` 로
앞을 치웠는데, **원래 없던 변수는 monkeypatch 가 되돌릴 것이 없다** —
시험이 세운 값이 그대로 남는다.

그 결과 `DOCLING_TABLE_API_KEY=sk-test-abcdefgh` 가 뒤 시험까지 살아
있었고, 키 검증 시험이 자기가 넣지도 않은 키를 보고 실패했다. 혼자
돌리면 통과했으므로 원인을 찾기 어려운 종류의 실패다.
"""
from __future__ import annotations

import os

import pytest

#: 판독 설정이 읽는 환경변수 앞가지. 이것으로 시작하는 것은 시험이 끝나면
#: 원래대로 되돌린다.
_PREFIXES = ("DOCSTRUCT_", "DOCLING_", "OPENAI_")


@pytest.fixture(autouse=True)
def _isolate_docstruct_env():
    """시험 하나가 끝나면 판독 관련 환경변수를 원래대로 되돌린다.

    입력: 없음
    출력: 없음 (fixture)
    비고:
        앞뒤를 통째로 견줘 **더해진 것은 지우고 바뀐 것은 되돌린다.**
        `monkeypatch` 를 쓰지 않고 `os.environ` 을 직접 세우는 코드
        (`set_api_key` · `_apply_key`)까지 덮기 위함이다.
    """
    before = {k: v for k, v in os.environ.items()
              if k.startswith(_PREFIXES)}
    try:
        yield
    finally:
        after = {k for k in os.environ if k.startswith(_PREFIXES)}
        for name in after - set(before):
            os.environ.pop(name, None)
        for name, value in before.items():
            os.environ[name] = value
        # 설정은 캐시되므로 다음 시험이 낡은 값을 보지 않게 비운다.
        try:
            from docstruct.core.config import rebuild_settings
        except ImportError:                      # 로컬 트리 배치
            try:
                from core.config import rebuild_settings
            except ImportError:
                return
        rebuild_settings()
