"""컨버터 공통 인터페이스.

역할:
    포맷별 컨버터가 지켜야 할 형태(to_markdown/to_html/to_text/to_xml)와
    출력 형식 상수를 정의한다.
호출부:
    converters.hwp / converters.hwpx / converters.pdf
출력:
    BaseConverter, OutputFormat, OUTPUT_MEDIA_TYPES
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path


class OutputFormat(str, Enum):
    """출력 형식 상수 (markdown | html | text | xml)."""
    text = "text"
    markdown = "markdown"
    html = "html"
    xml = "xml"


OUTPUT_MEDIA_TYPES = {
    OutputFormat.text: "text/plain; charset=utf-8",
    OutputFormat.markdown: "text/markdown; charset=utf-8",
    OutputFormat.html: "text/html; charset=utf-8",
    OutputFormat.xml: "application/xml; charset=utf-8",
}

_FMT_EXT = {
    "txt": OutputFormat.text,
    "text": OutputFormat.text,
    "md": OutputFormat.markdown,
    "markdown": OutputFormat.markdown,
    "html": OutputFormat.html,
    "htm": OutputFormat.html,
    "xml": OutputFormat.xml,
}


class BaseConverter(ABC):
    """포맷별 컨버터의 공통 인터페이스.

    입력(생성자): path — 원본 파일 경로
    출력: to_markdown/to_html/to_text/to_xml 문자열, save() 로 파일 저장
    """

    def __init__(self, path: str | Path):
        self.path = str(Path(path).resolve())
        if not os.path.exists(self.path):
            raise FileNotFoundError(f"파일을 찾을 수 없습니다: {self.path}")

    @property
    @abstractmethod
    def source_format(self) -> str:
        """소스 포맷 식별자.

        입력: 없음
        출력: 'hwp' | 'hwpx' | 'pdf' 등 소문자 문자열
        """

    @abstractmethod
    def to_html(self) -> str:
        """HTML 로 변환한다.

        입력: 없음
        출력: HTML 문자열
        """

    @abstractmethod
    def to_text(self) -> str:
        """평문으로 변환한다.

        입력: 없음
        출력: 텍스트 문자열
        """

    @abstractmethod
    def to_markdown(self) -> str:
        """markdown 으로 변환한다.

        입력: 없음
        출력: markdown 문자열
        """

    @abstractmethod
    def to_xml(self) -> str:
        """XML 로 변환한다.

        입력: 없음
        출력: XML 문자열
        """

    def convert(self, fmt: str | OutputFormat) -> str:
        """지정한 형식으로 변환한다.

        입력: fmt — OutputFormat 또는 형식 이름
        출력: 변환된 문자열
        """
        if isinstance(fmt, OutputFormat):
            fmt = fmt.value
        dispatch = {
            OutputFormat.text.value: self.to_text,
            OutputFormat.markdown.value: self.to_markdown,
            OutputFormat.html.value: self.to_html,
            OutputFormat.xml.value: self.to_xml,
        }
        if fmt not in dispatch:
            raise ValueError(f"지원하지 않는 포맷: {fmt!r}. 선택: {list(dispatch)}")
        return dispatch[fmt]()

    def save(self, output_path: str | Path, fmt: str | OutputFormat | None = None) -> None:
        """변환 결과를 파일로 저장한다.

        입력: path — 저장 경로, fmt — 출력 형식
        출력: 저장된 Path
        """
        output_path = Path(output_path)
        if fmt is None:
            ext = output_path.suffix.lower().lstrip(".")
            fmt = _FMT_EXT.get(ext, OutputFormat.text)
        content = self.convert(fmt)
        output_path.write_text(content, encoding="utf-8")
