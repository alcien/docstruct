"""converters.hwpx — HWPX 읽기 [형식 축].

축: 형식.
역할:
    HWPX(OOXML zip) 를 XML 로 직접 파싱해 markdown 을 만든다. pyhwp(AGPL)
    없이 돌고, 표 구조·병합·중첩·숨은 글(0.4.74)·표 앵커(0.4.81)를 보존한다.
    HWP 는 여기로 변환(convert.py)해서 들어오기도 한다.
호출부:
    docstruct.extractors.hwpx · docstruct.extractors.hwp (HWP→HWPX 경로)

모듈 (입력 → 출력 · 역할):
    converter.py          .hwpx 경로 → markdown / HTML / XML / 텍스트
                          BaseConverter 구현. 실제 일은 hwpxtree 가 한다.
    hwpxtree.py           zip 안 section XML → markdown + 그림 바이트 + 요약
                          문단·표·그림·도형을 지면 순서로 걷는다(_walk).
                          숨은 글은 본문에서 빼고 hidden_notes 에, 표 앵커
                          종류는 anchor_notes 에 남긴다. 세 수집기는 모듈
                          전역 — 동시 변환은 아직 지원하지 않는다.
                          inline 표 제자리 놓기는 향후 과제(0.4.83 주석).
    convert.py            .hwp 경로 → 변환된 .hwpx 임시 파일 경로
                          한컴 변환기·LibreOffice 가 있을 때 HWP 를 HWPX 로.

읽는 순서: converter → hwpxtree(_walk → _read_table → _paragraph_text).
"""
from docstruct.converters.hwpx.converter import HwpxConverter

__all__ = ["HwpxConverter"]
