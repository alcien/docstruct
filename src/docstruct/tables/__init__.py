"""tables — 표 구조 인식 [인식 축 ①].

축: 인식 — 형식을 모른다. 입력은 TableInfo(cells·markdown·bbox) 와 쪽 이미지.
역할:
    파서가 낸 표를 **표시 → 재구성 → LLM 판정·재추출** 순서로 다룬다.
    바꾸는 단계는 반드시 original_markdown 을 남기고, 바꾼 뒤가 더 나쁘면
    되돌린다. 정답 없이 재는 상시 검사는 structuring/checks 에 있다.
호출부:
    docstruct.pipeline 구간 4(표시) · 6(재구성) · 9(판정·재추출)

모듈 (입력 → 출력 · 역할):
  표현·블록
    markdown.py           본문 markdown → (본문, TableInfo 목록, 표 수)
                          표를 `<table N>` 블록으로 치환해 자리표시자를 만든다.
    tags.py               본문 ↔ `<table N>…</table N>` 블록 생성·파싱·동기화
                          markdown 이 바뀌면 sync_table_block 으로 본문도 맞춘다.
    docling.py            Docling TableItem → GFM 표 · cell_grid
                          머리 전파·다단 머리 접기 규칙. 파서 표 markdown 의 원형.
  표시 (바꾸지 않는다)
    continued.py          쪽 목록 → 표시한 표 수 (continues_from · header_ref)
                          쪽을 넘어 이어지는 표를 잇는 관계만 기록.
    odd_tables.py         쪽 목록 → 표시한 표 수 (odd_table)
                          같은 머리를 가진 표끼리 견줘 열 수가 다른 표.
  재구성 (바꾼다)
    grid_rebuild.py       OCR 조각 좌표 → markdown 표
                          행·열을 놓친 스캔 표를 좌표만으로 다시 세운다.
    vlm_rebuild.py        표 + 쪽 이미지 → 다시 만든 표 수
                          격자에 셀이 빠진 표를 VLM 후보 여러 개로 다시
                          만들고 grade 로 고른다. vlm_hint(기본 켬)·vlm_steps.
    grade.py              VLM 후보 markdown → 결정론 점수
                          검산·격자 일치·글자 보존으로 채점. 후보 선택의 심판.
  LLM 판정·재추출
    assess.py             표 + 쪽 이미지 → TableInfo.content_type/quality/reason
                          sufficient | wrong | insufficient. LLM 없으면 판정 안 함.
    fill.py               판정 결과 → page.content·tables·images 제자리 갱신
                          미달 표를 쪽 이미지에서 다시 뽑고, 그림으로 판정된
                          표는 ImageInfo 로 옮긴다.

읽는 순서: markdown/tags → docling → continued → grid_rebuild → vlm_rebuild(grade) → assess → fill.
"""
