"""HWP 경로가 쓰는 본문 표식 (백엔드와 무관).

입력:
    (없음 — 상수)
역할:
    쪽 나눔 같은 **표식 문자열**을 둔다. 이 값들은 pyhwp 와 아무 상관이
    없는데도 `pyhwp_backend/hwp5tree.py` 안에 있었다 — 그래서 백엔드를
    떼어내면 쪽 나누기(`extractors.hwp`)까지 함께 죽었다(0.5.0).
호출부:
    docstruct.extractors.hwp · docstruct.converters.hwp.pyhwp_backend.hwp5tree
출력:
    표식 상수
"""
from __future__ import annotations

#: 쪽 나눔 자리. 추출기가 이것을 기준으로 쪽을 가른다.
#: 본문에 우연히 나올 수 없는 모양이어야 한다.
PAGE_BREAK = "\x0c"
