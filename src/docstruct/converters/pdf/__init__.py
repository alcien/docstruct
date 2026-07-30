"""PDF 변환 모음.

역할:
    Docling 기반 PDF 파싱과 그림·표 부가 처리를 담는다.
호출부:
    docstruct.extractors.registry, converters.registry
출력:
    PdfConverter
"""
from docstruct.converters.pdf.converter import PdfConverter

__all__ = ["PdfConverter"]
