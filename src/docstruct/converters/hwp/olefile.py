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
                for off in range(0, len(payload) - 1, 2):
                    code = struct.unpack_from("<H", payload, off)[0]
                    if code == 13:
                        paragraphs.append("".join(buf))
                        buf = []
                    elif code >= 32:
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
