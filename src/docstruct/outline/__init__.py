"""outline — 문서 구조(목차·의미 경로) [인식 축 ② 텍스트의 윗단].

축: 인식.
역할:
    본문 제목 계층에서 목차를 만들고, 인쇄 쪽번호와 PDF 쪽번호의 차이를
    찾는다. 표·그림 단위가 아니라 **문서 단위**의 구조다.
호출부:
    docstruct.pipeline 구간 11 · docstruct.output.report

모듈 (입력 → 출력 · 역할):
    toc.py                쪽 목록 → 목차 항목 목록 [{title, page, source_page}] · toc_offset
                          규칙으로 찾고(점선·쪽수 패턴), 인쇄 쪽 오프셋을 잰다.
    builder.py            쪽 목록 → OutlineNode 트리 / markdown 목차
                          제목 수준으로 의미 경로(1 > 1.2 > 가)를 세운다.
"""
