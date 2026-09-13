"""converters — 원본 문서 읽기 [형식 축].

축: 형식 — 이 층만 파일 형식을 안다. 위(extractors)는 형식별 진입,
    그 위(pipeline)는 형식을 모른다.
역할:
    HWP/HWPX/PDF 를 markdown·HTML·XML·텍스트로 바꾸는 컨버터를 모은다.
    docstruct 파이프라인은 이 위에서 구조화를 수행한다. `converters.cli`
    는 구조화 없이 원본 변환만 하는 별도 입구다.
호출부:
    docstruct.extractors.* · converters.cli

하위 패키지 (형식별):
    pdf/                  text PDF · scan PDF — Docling 기반. 텍스트 레이어
                          유무(scanned.py)로 두 경로가 갈리고, 스캔은
                          rapidocr_ko 또는 VLM(text/scan_vlm)이 읽는다.
    hwpx/                 HWPX(OOXML) — zip+XML 직접 파싱(hwpxtree). 표
                          구조·병합·숨은 글·표 앵커를 보존한다.
    hwp/                  HWP(OLE 바이너리) — pyhwp(hwp5tree) 우선, 안 되면
                          HWP→HWPX 변환(hwpx/convert) → OLE 텍스트 → 미리보기
                          스트림 순으로 물러난다. 사유는 trace 에 남는다.
    html/                 pyhwp 가 낸 HTML → 블록·표 → markdown. hwp 경로의
                          중간 표현.
    common/               markdown 표 렌더러 등 형식 공통 유틸.

모듈 (입력 → 출력 · 역할):
    base.py               (없음) → BaseConverter · OutputFormat
                          모든 컨버터가 지키는 to_markdown/to_html/to_text/to_xml.
    registry.py           확장자 → BaseConverter 인스턴스
                          converters.cli 용. 파이프라인은 extractors.registry 를 쓴다.
    signature.py          파일 바이트 → 실제 형식 ('hwp'|'hwpx'|'pdf'|None)
                          이름은 .hwpx 인데 내용은 HWP 인 파일을 알아본다.
    deps.py               (없음) → *_AVAILABLE 불리언 + 심볼
                          docling·bs4·olefile·pyhwp·hwpx 가 없어도 import 가
                          실패하지 않게 한다 — 그 기능만 꺼진다.
    cli.py                인자 → 변환 파일 + 종료 코드
                          `python -m converters.cli` 원본 변환.

읽는 순서: signature → registry → 형식별 폴더의 converter.py.
"""
from docstruct.converters.base import BaseConverter, OutputFormat, OUTPUT_MEDIA_TYPES
from docstruct.converters.hwp import HwpConverter
from docstruct.converters.hwpx import HwpxConverter
from docstruct.converters.pdf import PdfConverter
from docstruct.converters.registry import get_converter, supported_extensions

__all__ = [
    "BaseConverter",
    "HwpConverter",
    "HwpxConverter",
    "PdfConverter",
    "OutputFormat",
    "OUTPUT_MEDIA_TYPES",
    "get_converter",
    "supported_extensions",
]
