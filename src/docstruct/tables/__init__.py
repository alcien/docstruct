"""표 처리 모음.

역할:
    표를 markdown 으로 만들고(docling, markdown), 본문에 블록으로
    심고(tags), 품질을 판정하고(assess), 필요하면 다시 뽑는다(fill).
호출부:
    docstruct.pipeline, docstruct.extractors.*
출력:
    표 markdown 문자열 및 TableInfo 상태 갱신
"""
