"""converters.pdf — PDF 읽기 (text PDF · scan PDF) [형식 축].

축: 형식. 안에서 다시 인식 축이 갈린다 — 표(table_extract·cell_match),
    텍스트(text_runs·glyph_probe·ocr_language·rapidocr_ko), 그림(region_kind·
    text_probe·picture_inspect).
역할:
    Docling 으로 PDF 를 읽고, Docling 이 놓치는 한국 공문서 특성을 보정한다.
    텍스트 레이어가 있으면 그대로(text PDF), 없으면 OCR/VLM 으로(scan PDF).
    두 경로의 분기는 scanned.py 와 pipeline 구간 2 가 정한다.
호출부:
    docstruct.extractors.pdf · docstruct.pipeline (스캔 판별·OCR 재판독)

모듈 (입력 → 출력 · 역할):
  경로·구성
    converter.py          PDF 경로 → DoclingDocument (+ 표 HTML·그림·레이아웃)
                          Docling 변환 실행과 결과 수집. 쪽 단위 실패는
                          failed_pages 로 남긴다.
    docling_backend.py    Settings → DocumentConverter (캐시)
                          장치·스레드·OCR 엔진·PDF 백엔드를 반영한 파이프라인
                          구성. 설정이 바뀌면 invalidate_caches 로 새로 만든다.
    scanned.py            PDF 경로 → 스캔본 여부
                          변환 전에 원본을 미리 가려 전면 OCR 을 결정한다.
  텍스트
    text_runs.py          PDF 쪽 → 글자·단어 좌표 목록
                          텍스트 레이어의 기하. 격자 복원·셀 매칭의 재료.
    glyph_probe.py        본문 → 의심 코드포인트 + 근거
                          깨진 ToUnicode 매핑(글머리표가 엉뚱한 글자로).
    ocr_language.py       쪽 본문 → 한자 오판 자리
                          한글 지면이 한자로 나온 자리를 찾는다 (LLM 없음).
    rapidocr_ko.py        쪽 이미지 → OcrLine 목록 (텍스트·신뢰도·좌표)
                          rapidocr 를 직접 불러 한국어 모델을 강제한다.
                          Docling 내장 OCR 은 한국어 모델을 고를 수 없다.
  표
    table_extract.py      Docling 표·본문 요소 → markdown (레거시 경로)
                          extractors.pdf 이전 경로. 표 → GFM 규칙의 원형.
    cell_match.py         표 셀 bbox + OCR 조각 좌표 → 셀 → 텍스트
                          스캔 표의 셀에 OCR 글자를 좌표로 귀속.
  그림
    region_kind.py        그림 영역 + 텍스트 밀도 → TABLE | TEXT | IMAGE
                          그림으로 잡힌 영역의 정체를 LLM 없이 가른다.
    text_probe.py         그림 영역 → TextDensity (글자 수·줄 수·표 후보)
                          region_kind 의 재료.
    picture_inspect.py    DoclingDocument → 그림별 VLM 설명·점검 결과
                          Docling 의 그림 설명(enrichment) 조회.

읽는 순서: scanned → converter → (text) text_runs → (표) table_extract →
           (그림) text_probe → region_kind.
"""
from docstruct.converters.pdf.converter import PdfConverter

__all__ = ["PdfConverter"]
