"""HWP OLE 스트림 텍스트 추출.

역할:
    pyhwp 변환이 불충분할 때 쓰는 최후 경로. 본문 텍스트만 얻으며
    표·그림 구조는 보존되지 않는다.
호출부:
    converters.hwp.converter
출력:
    텍스트 및 그것을 감싼 markdown/HTML/XML
"""
from __future__ import annotations

from docstruct.converters.korean_text import normalize_korean_text

import re
import xml.etree.ElementTree as ET

from docstruct.converters.deps import OLEFILE_AVAILABLE, olefile
from docstruct.converters.html.utils import normalize_line

_RE_OLE_GARBAGE = re.compile(r"^[捤獥汤捯氠瑢]+$")
_RE_OLE_FIELD_MARK = re.compile(r"汫[╣ॣ]")


#: 사설 사용 영역(Private Use Area). 한글이 글머리표·기호를 여기에 담는데,
#: 다른 프로그램에서는 뜻이 없고 네모로 보인다. 본문에서는 지운다.
_RE_PRIVATE_USE = re.compile(
    "[\uE000-\uF8FF\U000F0000-\U000FFFFD\U00100000-\U0010FFFD]"
)

#: 뒤에 12바이트 부속 데이터가 따라오는 제어 문자 (HWP 5.0 규격).
#:
#: 규격상 문자 제어는 세 종류다.
#:   · 단독 1글자 : 0, 10(줄바꿈), 13(문단끝), 24~31
#:   · 인라인 8글자: 4~9, 19, 20            ← 9 = 탭
#:   · 확장 8글자  : 1~3, 11, 12, 14~18, 21~23
#: 뒤 둘은 "제어코드 + 부속 12바이트 + 제어코드 반복" 구조라 12바이트를
#: 건너뛰어야 한다. 빠뜨리면 부속 데이터가 글자로 읽혀 깨진다.
#:
#: 예) 탭(9)을 빠뜨렸을 때 실제로 나온 것:
#:     0009 5B88 0000 0303 0000 0000 0000 0009  →  "守̃"
#: 확장 제어는 부속 데이터에 4글자 컨트롤 ID 가 들어 있어 이렇게 보인다:
#:     0002 6364 7365 ...  →  "捤獥" (= "secd", 구역 정의)
_INLINE_CONTROLS = frozenset(
    {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}
)

#: 부속 데이터 길이(바이트). 제어코드 6글자분.
_INLINE_CONTROL_DATA_BYTES = 12

#: 탭. 건너뛰되 자리는 남긴다 — 목차의 "항목 ⟶ 쪽번호" 처럼 탭이 열을
#: 가르는 경우가 많아, 지우면 글자가 통째로 붙어 버린다.
_TAB = 9


def extract_raw_text(hwp_path: str) -> str:
    """OLE 레코드를 직접 읽어 텍스트만 추출한다 (최후 폴백).

    입력: hwp_path — HWP 파일 경로
    출력: 문단별 개행으로 이어진 텍스트
    예외: olefile 미설치 시 ImportError
    동작: FileHeader 의 압축 플래그를 읽고 BodyText/SectionN 스트림을
          HWPTAG_CHAR(67) 레코드 단위로 해석한다. 인라인 제어 문자의
          부속 12바이트를 건너뛰고, UTF-16 대리쌍을 합쳐 한 글자로 만든다.
          표·그림 구조는 복원되지 않는다 (pyhwp 경로 권장).
    """
    if not OLEFILE_AVAILABLE:
        raise ImportError("olefile 패키지를 설치하세요: pip install olefile")

    import struct
    import zlib

    CHAR_TAG = 67  # HWPTAG_CHAR

    def records(data: bytes):
        """스트림 바이트를 (태그, 페이로드) 레코드로 자른다.

        입력: data — 섹션 스트림 바이트
        출력: (tag, bytes) 제너레이터
        비고: 길이 필드가 0xFFF 이면 뒤따르는 4바이트가 실제 길이다 (HWP 5.0 규격).
        """
        i = 0
        while i + 4 <= len(data):
            hdr = struct.unpack_from("<I", data, i)[0]
            i += 4
            tag = hdr & 0x3FF
            dlen = (hdr >> 20) & 0xFFF
            if dlen == 0xFFF:
                dlen = struct.unpack_from("<I", data, i)[0]
                i += 4
            yield tag, data[i : i + dlen]
            i += dlen

    def decompress(raw: bytes, compressed: bool) -> bytes:
        """압축 플래그에 따라 스트림을 푼다.

        입력: raw — 원본 바이트, compressed — FileHeader 압축 플래그
        출력: 푼 바이트. 실패하면 원본 그대로 (문서 일부라도 살린다)
        """
        if not compressed:
            return raw
        try:
            return zlib.decompress(raw, -15)
        except Exception:
            return raw

    ole = olefile.OleFileIO(hwp_path)
    compressed = False
    try:
        fh = ole.openstream("FileHeader").read()
        flags = struct.unpack_from("<I", fh, 36)[0]
        compressed = bool(flags & 1)
    except Exception:
        pass

    paragraphs: list[str] = []
    sec = 0
    while ole.exists(f"BodyText/Section{sec}"):
        raw = ole.openstream(f"BodyText/Section{sec}").read()
        data = decompress(raw, compressed)
        buf: list[str] = []
        for tag, payload in records(data):
            if tag == CHAR_TAG:
                off = 0
                end = len(payload) - 1
                while off < end:
                    code = struct.unpack_from("<H", payload, off)[0]
                    off += 2
                    if code == 13:                    # 문단 끝
                        paragraphs.append("".join(buf))
                        buf = []
                    elif code in _INLINE_CONTROLS:
                        # 부속 12바이트를 통째로 건너뛴다. 뒤따르는 제어코드
                        # 반복은 code < 32 가지에서 걸러진다.
                        if code == _TAB:
                            buf.append("\t")
                        off += _INLINE_CONTROL_DATA_BYTES
                    elif code < 32:
                        continue                      # 그 밖의 제어 문자는 버린다
                    elif 0xD800 <= code <= 0xDBFF:
                        # UTF-16 상위 대리(surrogate). 다음 16비트와 짝을 이뤄
                        # 한 글자가 된다. 낱개로 chr() 하면 JSON·파일 저장에서
                        # "surrogates not allowed" 로 실패한다.
                        if off < end:
                            low = struct.unpack_from("<H", payload, off)[0]
                            if 0xDC00 <= low <= 0xDFFF:
                                off += 2
                                buf.append(
                                    chr(0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00))
                                )
                                continue
                        continue                      # 짝이 없으면 버린다
                    elif 0xDC00 <= code <= 0xDFFF:
                        continue                      # 짝 없는 하위 대리는 버린다
                    else:
                        try:
                            buf.append(chr(code))
                        except (ValueError, OverflowError):
                            pass
        if buf:
            paragraphs.append(normalize_korean_text("".join(buf)))
        sec += 1

    ole.close()
    return "\n".join(p for p in paragraphs if p.strip())


def clean_text(text: str) -> str:
    """olefile 추출 텍스트에서 HWP 필드 제어 문자 등을 정리한다.

    입력: text — extract_raw_text 결과
    출력: PUA·필드 표식·깨진 줄이 제거된 텍스트
    """
    text = _RE_PRIVATE_USE.sub("", text)
    text = _RE_OLE_FIELD_MARK.sub("\n", text)
    lines: list[str] = []
    for line in text.splitlines():
        line = normalize_line(line)
        if not line or _RE_OLE_GARBAGE.fullmatch(line):
            continue
        lines.append(line)
    return "\n".join(lines)


def text_to_html(text: str) -> str:
    """평문을 최소한의 HTML 로 감싼다 (olefile 폴백 표시용).

    입력: text — 평문
    출력: 줄마다 `<p>` 로 감싼 HTML (`---` 줄은 `<hr/>`)
    """
    import html as html_module

    parts = [
        "<!DOCTYPE html>",
        '<html><head><meta charset="utf-8"></head><body>',
        "<!-- olefile fallback: pyhwp HTML 불충분 -->",
    ]
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if set(line) <= {"-"}:
            parts.append("<hr/>")
        else:
            parts.append(f"<p>{html_module.escape(line)}</p>")
    parts.append("</body></html>")
    return "\n".join(parts)


def text_to_markdown(text: str) -> str:
    """평문을 markdown 으로 감싼다.

    입력: text — 평문
    출력: 문단 사이 빈 줄이 정리된 markdown (`---` 줄은 구분선)
    """
    parts: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if set(line) <= {"-"}:
            parts.append("\n---\n")
        else:
            parts.append(f"\n{line}\n")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(parts)).strip()


def text_to_xml(text: str) -> str:
    """평문을 구조화 XML 로 감싼다.

    입력: text — 평문
    출력: `<document source="olefile-fallback">` 루트 XML
    """
    doc = ET.Element("document", source="olefile-fallback")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if set(line) <= {"-"}:
            ET.SubElement(doc, "divider")
        else:
            ET.SubElement(doc, "paragraph").text = line
    ET.indent(doc, space="  ")
    return ET.tostring(doc, encoding="unicode", xml_declaration=False)
