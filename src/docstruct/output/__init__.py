"""output — 산출물·표시 (파이프라인의 바깥쪽).

축: 공통 — 결과(PageDocument)를 받아 사람·기계에게 내보내는 층.
역할:
    구조화 결과를 파일·화면으로 만든다. 여기서는 **판독 결과를 바꾸지
    않는다** — 표시하고 내보낼 뿐이다. 노트북·Colab 전용 보조도 여기.
호출부:
    docstruct.cli · docstruct.api.save · 노트북

모듈 (입력 → 출력 · 역할):
    report.py             PageDocument → document.md · document.json · tables.md ·
                          pipeline.md · layout.md (파일)
                          표는 table.markdown(권위)으로 펼친다.
    content.py            PageContent → placeholder 가 치환된 본문 문자열
                          `<table N>` 블록·그림 주석을 실제 내용으로 되돌린다.
    layout.py             Docling 레이아웃 판정 → LayoutItem 목록 (layout.md 재료)
                          영역 라벨·좌표와 파이프라인이 그것을 무엇으로 바꿨는지.
    preview.py            PageDocument → 노트북 HTML 표시
    nbui.py               (위젯) → 선택된 파일 Path
    colab.py              (Colab) → 설치·설정·반출 보조
"""
