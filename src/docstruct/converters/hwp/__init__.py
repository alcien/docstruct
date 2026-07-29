"""HWP 변환 모음.

역할:
    HWPML(XML)·pyhwp(HTML)·olefile(텍스트) 세 경로를 담는다.
호출부:
    docstruct.extractors.hwp, converters.registry
출력:
    HwpConverter
"""
from docstruct.converters.hwp.converter import HwpConverter

__all__ = ["HwpConverter"]
