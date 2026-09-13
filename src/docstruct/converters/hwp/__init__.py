"""converters.hwp — HWP(OLE 바이너리) 읽기 [형식 축].

축: 형식.
역할:
    HWP 는 한 가지 방법으로 다 읽히지 않는다. 읽기 경로를 **사다리**로
    두고 위에서부터 시도하며, 어느 단에서 왜 물러났는지를 결과물(trace ·
    fallback_reason)에 남긴다.

        1  HWP → HWPX 변환 (hwpx/convert)  **cells 까지 살아난다 — 최선**
                                           hwp2hwpx(jar)가 있을 때. 0.5.1 배선
        2  pyhwp 트리 (pyhwp_backend)      표 구조·서식 보존 (AGPL · 떼어낼 수 있음)
        3  pyhwp HTML (pyhwp → html/)   표는 되나 서식 일부 손실
        4  HWPML (hwpml)                XML 로 저장된 변종
        5  OLE 텍스트 (olefile)         본문만
        6  미리보기 스트림 (preview)     PrvText — 마지막 수단

    HWP 는 `cells` 를 만들지 못하므로 격자 검사·오염 검사·hole_fill 이
    비켜 간다 — 실험_총정리 §6 의 가장 큰 공백.
호출부:
    docstruct.extractors.hwp

모듈 (입력 → 출력 · 역할):
    converter.py          .hwp 경로 → markdown / HTML / XML / 텍스트 + 원본 <table> 조각
                          사다리를 타는 본체. 판정 결과는 diagnose 에서 받는다.
    diagnose.py           .hwp 경로 → HwpDiagnosis (읽을 수 있는지·이유)
                          암호화·배포용·손상 파일을 미리 가린다.
    pyhwp_backend/        **떼어낼 수 있는 폴더** — pyhwp(AGPL)를 쓰는 코드 전부
                          hwp5tree.py(파서 트리 → markdown) · html_export.py(hwp5html)
                          없으면 사다리 1단·3단만 빠지고 나머지는 그대로 돈다
                          표는 GFM, 중첩 표는 부모 셀 안에 인라인.
    marks.py              쪽 나눔 표식 (백엔드와 무관 — 떼어내도 쪽 나누기는 돈다)
                          hwp5html 실행과 결과 신뢰도 판단.
    hwpml.py              HWPML XML → markdown / HTML / XML / 텍스트
    olefile.py            OLE 스트림 → 텍스트 (+ 감싼 markdown/HTML/XML)
    preview.py            PrvText·PrvImage 스트림 → 표 행 복원 markdown + 첫 쪽 이미지
    styling.py            HWP 서식 정보 → 제목(#)·강조(**)·목록(- ) 반영 문단
                          hwp5tree 가 문단마다 부른다.

읽는 순서: diagnose → converter → hwp5tree → (물러날 때) pyhwp → olefile → preview.
"""
from docstruct.converters.hwp.converter import HwpConverter

__all__ = ["HwpConverter"]
