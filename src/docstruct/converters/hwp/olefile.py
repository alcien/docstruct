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

#: 뒤에 12바이트 부속 데이터가 따라오는 제어 문자.
#: HWP 5.0 규격의 확장·인라인 제어 문자 범위. 그대로 읽으면 글자로
#: 오해해 깨진 문자가 섞인다 (예: 捤獥汤捯).
_INLINE_CONTROLS = frozenset(
    {1, 2, 3, 4, 5, 6, 7, 8, 11, 12, 14, 15, 16, 17, 18, 21, 22, 23}
)


def extract_raw_text(hwp_path: str) -> str:
    """
    OLE 레코드를 직접 읽어 텍스트만 추출
    표·그림 구조는 복원되지 않음 (pyhwp 권장).
    """
    if not OLEFILE_AVAILABLE:
        raise ImportError("olefile 패키지를 설치하세요: pip install olefile")

    import struct
    import zlib

    CHAR_TAG = 67  # HWPTAG_CHAR

    def records(data: bytes):
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
                        # 확장/인라인 제어 문자는 뒤에 12바이트 부속 데이터가
                        # 붙는다. 그대로 읽으면 글자로 오해해 깨진 문자가 섞인다
                        # (예: 捤獥汤捯). 통째로 건너뛴다.
                        off += 12
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
            paragraphs.append("".join(buf))
        sec += 1

    ole.close()
    return "\n".join(p for p in paragraphs if p.strip())


def clean_text(text: str) -> str:
    """olefile 추출 텍스트에서 HWP 필드 제어 문자 등을 정리합니다."""
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
