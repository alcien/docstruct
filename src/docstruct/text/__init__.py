"""text — 텍스트 구조 인식 [인식 축 ②].

축: 인식 — 형식을 모른다. 입력은 PageContent.content 와 (스캔이면) 쪽 이미지.
역할:
    본문 글자를 **바르게** 만드는 일. 스캔 쪽을 읽고, 읽은 것을 문맥으로
    검증하고, 의심 자리만 다시 읽고, 한국 문서 특유의 훼손(균등배분·PUA)을
    되돌리고, 긴 문서를 구조 경계에서 나눈다. 문서 구조(목차·계층)는
    outline/ 과 structuring/ 이 맡는다.
호출부:
    docstruct.pipeline 구간 3(판독) · 10(검증·재판독) · 12(누름틀 정리) ·
    docstruct.extractors.* (korean_text)

모듈 (입력 → 출력 · 역할):
    scan_vlm.py           쪽 이미지 → 바꾼 쪽 번호 집합 (content 갱신 · ocr_engine 기록)
                          텍스트 레이어가 없는 쪽을 VLM 으로 읽는다
                          (DOCSTRUCT_SCAN_BACKEND=vlm). 짧게 읽혀도 버리지
                          않고, 아예 못 읽으면 rapidocr 로 물러난다.
    ocr_verify.py         쪽 본문 → page.ocr_doubts [{index,text,reason}]
                          OCR/전사 결과를 문맥으로 검증해 의심 자리를 짚는다.
                          바꾸지 않는다 — 짚기만 한다.
    ocr_reread.py         의심 자리 + 쪽 이미지 → 고친 곳 수 (ocr_original 보존)
                          그 자리만 잘라 VLM 에 다시 읽힌다. 표 셀도 대상.
    korean_text.py        문자열 → 정규화된 문자열
                          균등배분 복원(`대 한 민 국` → `대한민국`), 한컴 PUA
                          매핑, 누름틀 잔재 제거(strip_field_payload).
    split.py              list[PageContent] → 나뉜 조각 목록
                          쪽 경계가 없는 HWP/HWPX 를 목표 크기로, 표·그림은
                          속한 조각으로 따라간다.

읽는 순서: scan_vlm → ocr_verify → ocr_reread. korean_text 는 추출기가 먼저 부른다.
"""
