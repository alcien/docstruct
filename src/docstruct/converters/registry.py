"""확장자 → 컨버터 매핑.

역할:
    파일 확장자에 맞는 BaseConverter 구현을 찾아준다.
    (docstruct 파이프라인은 별도의 extractors.registry 를 쓴다.
    이쪽은 converters.cli 등 원본 변환 경로용이다.)
호출부:
    converters.cli, converters/__init__
출력:
    BaseConverter 인스턴스
"""
from __future__ import annotations

from pathlib import Path

from docstruct.converters.base import BaseConverter
from docstruct.converters.hwp.converter import HwpConverter
from docstruct.converters.hwp.hwpml import is_hwpml
from docstruct.converters.hwpx.converter import HwpxConverter
from docstruct.converters.pdf.converter import PdfConverter

_CONVERTERS: dict[str, type[BaseConverter]] = {
    ".hwp": HwpConverter,
    ".hwpx": HwpxConverter,
    ".pdf": PdfConverter,
}


def supported_extensions() -> list[str]:
    return sorted(_CONVERTERS.keys())


def get_converter(path: str | Path) -> BaseConverter:
    """
    파일 확장자에 맞는 변환기 인스턴스를 반환합니다.

    HWPML 형식(.hwp 확장자지만 XML 내용)도 HwpConverter가 처리합니다.
    """
    resolved = Path(path).resolve()
    ext = resolved.suffix.lower()

    if ext == ".hwp" or (ext == "" and is_hwpml(str(resolved))):
        return HwpConverter(resolved)

    cls = _CONVERTERS.get(ext)
    if cls is None:
        supported = ", ".join(supported_extensions())
        raise ValueError(
            f"지원하지 않는 파일 형식: {ext!r}. "
            f"지원 확장자: {supported}"
        )
    return cls(resolved)
