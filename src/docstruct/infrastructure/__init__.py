"""infrastructure — 외부 서비스 통신 (공통 층).

축: 공통.
역할:
    LLM·VLM 을 부르는 방법을 한곳에 둔다. 표 판정·재추출·그림 판독·OCR
    재판독은 전부 `infrastructure.llm.client` 를 통해 나간다 — 엔드포인트
    전환(사내 → 대비책), 재시도, 동시 호출 제한이 여기서 한 번만 처리된다.
호출부:
    docstruct.tables.* · docstruct.images.* · docstruct.text.* · docstruct.outline.toc
하위:
    llm/                  HTTP 클라이언트 · 로컬 VLM · 응답 JSON 파서
"""
