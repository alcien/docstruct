"""HWPX 변환 모음.

역할:
    python-hwpx 로 OOXML 기반 HWPX 를 읽는다.
호출부:
    docstruct.extractors.hwpx, converters.registry
출력:
    HwpxConverter
"""
from docstruct.converters.hwpx.converter import HwpxConverter

__all__ = ["HwpxConverter"]
