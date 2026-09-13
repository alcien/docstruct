"""extractors — 형식별 추출 진입 [형식 축의 윗단].

축: 형식 — 여기가 형식을 아는 마지막 층이다.
역할:
    converters 가 만든 원문(markdown·Docling 문서)을 파이프라인의 공통
    모델(PageContent · TableInfo · ImageInfo · PageTrace)로 옮긴다.
    모든 추출기는 같은 반환 타입(ExtractionResult)을 쓴다 — 새 형식은
    registry 에 함수 하나를 더하면 되고 pipeline 은 손대지 않는다.
호출부:
    docstruct.pipeline._extract (구간 1)

모듈 (입력 → 출력 · 역할):
    registry.py           확장자 → 추출기 함수 · ExtractionResult(pages·failed_pages·table_html)
                          등록표. SUPPORTED_SUFFIXES 와 어긋나면 import 시점에 터진다.
    pdf.py                Docling 변환 결과 → list[PageContent]
                          쪽마다 content(표는 <table N> 블록)·tables(cells 포함)·
                          images·layout·trace 를 채운다. 스캔 쪽 표시도 여기서.
    hwpx.py               .hwpx 경로 → list[PageContent] (문서 전체가 1쪽)
                          HWPX 는 쪽 경계가 없다. 숨은 글·표 앵커 요약을
                          page.hidden_text · page.table_anchors 로 올린다.
    hwp.py                .hwp 경로 → (list[PageContent], 원본 <table> HTML 조각)
                          사다리 결과와 fallback_reason 을 trace 에 남긴다.

읽는 순서: registry → 형식별 파일. 쪽 번호 규칙(page_no_kind) 은 models.py.
"""
