"""포맷별 추출기 모음.

역할:
    PDF/HWP/HWPX 를 PageContent 로 바꾸는 함수들을 담는다.
    포맷 선택은 registry 가 담당한다.
호출부:
    docstruct.pipeline (registry 경유)
출력:
    각 추출기의 반환은 registry.ExtractionResult 로 통일된다.
"""
