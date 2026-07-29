"""사이트 전용 기본값 예시.

역할:
    이 파일을 ``site_defaults.py`` 로 복사하고 값을 채우면, 설치 직후
    설정 없이 해당 엔드포인트로 동작한다. 환경변수·.env 가 언제나 우선한다.
호출부:
    docstruct.core.config._load_site_defaults
출력:
    DEFAULTS — {환경변수명: 값}

사용:
    cp site_defaults.example.py site_defaults.py   # 그리고 값 수정

주의:
    ``site_defaults.py`` 는 .gitignore 대상이다. 사내 주소가 공개 저장소에
    올라가지 않도록 분리한 것이므로 커밋하지 말 것.
"""

DEFAULTS = {
    # 표 평가·재추출·목차 추출
    "DOCLING_TABLE_API_URL": "http://내부주소:포트/v1/chat/completions",
    "DOCLING_TABLE_API_MODEL": "모델명",
    # 그림 설명 VLM (생략하면 그림 캡션 없이 동작)
    "DOCLING_PICTURE_API_URL": "http://내부주소:포트/v1/chat/completions",
    "DOCLING_PICTURE_API_MODEL": "모델명",
}
