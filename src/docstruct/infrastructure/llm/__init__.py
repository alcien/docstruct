"""infrastructure.llm — LLM/VLM 호출 계층.

축: 공통.
역할:
    OpenAI 호환 chat/completions 엔드포인트와 로컬 HuggingFace VLM 을
    같은 함수 모양으로 감싼다. 호출부는 "메시지를 주고 문자열을 받는다"
    만 알면 된다.
호출부:
    docstruct.tables.assess / fill / vlm_rebuild · docstruct.images.vlm_read /
    chart_read · docstruct.text.scan_vlm / ocr_reread · docstruct.outline.toc

모듈 (입력 → 출력 · 역할):
    client.py             메시지(텍스트+이미지 data URI) → 응답 문자열
                          엔드포인트·모델·키·타임아웃을 Settings 에서 읽는다.
                          연결 불가면 대비 엔드포인트로 넘어가고(키가 있을
                          때만), 401·잘못된 응답은 넘어가지 않는다.
                          `llm_api_config()` 가 None 이면 LLM 미설정 —
                          호출부는 "판정 안 함" 으로 기록하고 지나간다.
    local_vlm.py          메시지 → 응답 문자열 (DOCSTRUCT_VLM_MODEL 일 때)
                          transformers 로 이 장비에서 직접 실행. 무거워서
                          한 번 올리면 프로세스 안에서 재사용.
    json_parse.py         응답 문자열 → dict 목록
                          코드 펜스·앞뒤 설명이 붙어도 JSON 만 뽑는다.
                          실패하면 빈 목록 — 예외를 올리지 않는다.
"""
