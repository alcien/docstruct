"""원본 문서 변환 패키지.

역할:
    HWP/HWPX/PDF 를 markdown·HTML·XML·텍스트로 바꾸는 컨버터를 모은다.
    docstruct 파이프라인은 이 위에서 구조화를 수행한다.
호출부:
    docstruct.extractors.*, converters.cli
출력:
    BaseConverter 구현체와 get_converter/supported_extensions
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
