"""core — 환경·설정·진단 (공통 층).

축: 공통 — 형식·인식 어느 축에도 속하지 않는 바탕.
역할:
    파이프라인이 도는 조건을 만든다. 환경변수를 설정 객체로 바꾸고,
    윈도우 로케일을 우회하고, 무엇이 준비됐는지 점검하고, 진행률을 보여
    준다. 문서를 읽지 않는다 — 문서를 읽는 코드가 기대는 바닥이다.
호출부:
    docstruct.pipeline · docstruct.api · docstruct.cli · 노트북

모듈 (입력 → 출력 · 역할):
    config.py             환경변수·.env·site_defaults → Settings
                          단일 설정 객체. LLM 엔드포인트·OCR/PDF 백엔드·
                          연산 장치·동시 실행 수·실험 손잡이가 전부 여기로
                          들어온다. `get_settings()` 는 캐시, `rebuild_settings()`
                          로 갱신.
    site_defaults.py      (코드 상수) → DEFAULTS {환경변수: 값}
                          사내 기본값. 배포마다 다르므로 pkg 에만 예시
                          (`site_defaults.example.py`)를 두고 사본은 배치마다.
    checks.py             현재 설정 → 점검 항목 목록 / (성공, 메시지)
                          `docstruct --check`. LLM 엔드포인트에 실제로 닿는지
                          호출해 본다. 설정값 유무만 보지 않는다.
    progress.py           (작업 수) → ProgressBar
                          tqdm 이 있으면 막대, 없으면 로그. 하위 모듈은 tqdm
                          을 직접 부르지 않는다.
    winfix.py             (없음) → 환경변수·함수 패치
                          Windows cp949 로케일에서 docling·subprocess 가
                          깨지는 것을 우회. `from docstruct import winfix`.
    diagnose_docling.py   PDF 경로 → 표준출력(요소 목록·쪽별 통계)
                          Docling 이 무엇을 뽑았는지 요소 단위로 덤프하는
                          진단 스크립트.

읽는 순서: config → checks → progress. winfix 는 윈도우에서만.
"""
