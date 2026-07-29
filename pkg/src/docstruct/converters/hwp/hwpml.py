"""HWPML(XML) 파싱.

역할:
    확장자만 .hwp 이고 내용은 XML 인 문서를 직접 파싱한다.
    표 구조가 XML 에 남아 있어 손실 없이 변환된다.
호출부:
    converters.hwp.converter, docstruct.extractors.hwp
출력:
    markdown / HTML / XML / 텍스트 문자열
"""
from __future__ import annotations

import html as html_module
import re
import xml.etree.ElementTree as ET

from docstruct.converters.common.table import render_md_table

RE_JANG = re.compile(r"^\s*제\s*\d+\s*장\b")
RE_JO   = re.compile(r"^\s*제\s*\d+\s*조")
RE_BLANK = re.compile(r"\s+")


def is_hwpml(hwp_path: str) -> bool:
    """파일 앞부분을 읽어 HWPML XML 형식인지 판별합니다."""
    try:
        with open(hwp_path, "rb") as f:
            head = f.read(512)
        text = head.decode("utf-8", errors="ignore")
        return "<HWPML" in text or (text.startswith("<?xml") and "HWPML" in text)
    except Exception:
        return False


def hwpml_normalize(text: str) -> str:
    """HWPML 텍스트를 정리한다.

    입력: text — 원문
    출력: 공백·제어문자가 정리된 문자열
    """
    return RE_BLANK.sub(" ", text.replace("\xa0", " ")).strip()


def hwpml_char_texts(elem) -> str:
    """문자 노드의 텍스트를 모은다.

    입력: node — XML Element
    출력: 텍스트 목록
    """
    parts = []
    for child in elem.iter():
        if child.tag == "CHAR":
            parts.append(child.text or "")
        elif child.tag == "PICTURE":
            parts.append("[그림]")
    return "".join(parts)


def hwpml_cell_text(cell) -> str:
    """HWPML 셀의 텍스트를 뽑는다.

    입력: cell — XML Element
    출력: 셀 텍스트
    """
    return hwpml_normalize(hwpml_char_texts(cell))


def hwpml_render_table(rows: list) -> str:
    """HWPML 표 요소를 markdown 표로 만든다.

    입력: table — XML Element
    출력: GFM 표 문자열
    """
    return render_md_table(rows)


HWPML_SKIP = {"HEADER","FOOTER","SECDEF","FOOTNOTE","ENDNOTE",
               "FIELDBEGIN","FIELDEND","HIDE","PAGEDEF",
               "FOOTNOTESHAPE","ENDNOTESHAPE","PAGEBORDERFILL","COLDEF"}


def hwpml_walk_body(root):
    """BODY 아래 <P>/<TABLE> 요소를 ('p'|'table', elem) 형태로 yield."""
    body = root.find(".//BODY")
    if body is None:
        return
    def walk(elem):
        tag = elem.tag
        if tag in HWPML_SKIP:
            return
        if tag == "TABLE":
            yield ("table", elem)
            return
        if tag == "P":
            yield ("p", elem)
            for child in elem:
                yield from walk(child)
            return
        for child in elem:
            yield from walk(child)
    yield from walk(body)


def hwpml_p_text(p_elem) -> str:
    """<P>의 본문 텍스트. 표 내부 TEXT는 제외합니다.

    `.//TEXT`는 CELL 안쪽 TEXT까지 전부 찾아오므로, 표 셀 텍스트가 본문
    단락으로 한 번 더 복제됩니다. TABLE 서브트리에 속한 노드를 미리 걸러냅니다.
    """
    inside_table = {
        id(node)
        for tbl in p_elem.findall(".//TABLE")
        for node in tbl.iter()
    }
    parts = []
    for text_elem in p_elem.findall(".//TEXT"):
        if id(text_elem) in inside_table:
            continue
        if text_elem.find("TABLE") is not None:
            continue
        for char in text_elem.findall("CHAR"):
            parts.append(char.text or "")
        for _ in text_elem.findall("PICTURE"):
            parts.append("[그림]")
    return hwpml_normalize("".join(parts))


def hwpml_table_to_md(tbl) -> str:
    rows = []
    for row in tbl.findall(".//ROW"):
        cells = [hwpml_cell_text(c) for c in row.findall("CELL")]
        if cells:
            rows.append(cells)
    return hwpml_render_table(rows)


def to_markdown(hwp_path: str) -> str:
    root = ET.parse(hwp_path).getroot()
    parts = []
    seen = set()
    for kind, elem in hwpml_walk_body(root):
        if kind == "p":
            for tbl in elem.findall(".//TABLE"):
                tid = id(tbl)
                if tid not in seen:
                    seen.add(tid)
                    md = hwpml_table_to_md(tbl)
                    if md:
                        parts.append(f"\n{md}\n")
            text = hwpml_p_text(elem)
            if not text:
                continue
            if RE_JANG.match(text):
                parts.append(f"\n## {text.strip()}\n")
            elif RE_JO.match(text):
                m = re.match(r"(제\s*\d+조(?:의\d+)?\s*\([^)]*\))(.*)", text, re.S)
                if m:
                    line = f"**{m.group(1).strip()}**" + (f" {m.group(2).strip()}" if m.group(2).strip() else "")
                else:
                    line = text
                parts.append(f"\n{line}\n")
            else:
                parts.append(f"\n{text}\n")
        elif kind == "table":
            tid = id(elem)
            if tid not in seen:
                seen.add(tid)
                md = hwpml_table_to_md(elem)
                if md:
                    parts.append(f"\n{md}\n")
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(parts))
    return result.strip()


def to_text(hwp_path: str) -> str:
    root = ET.parse(hwp_path).getroot()
    lines = []
    seen = set()
    for kind, elem in hwpml_walk_body(root):
        if kind == "p":
            for tbl in elem.findall(".//TABLE"):
                tid = id(tbl)
                if tid not in seen:
                    seen.add(tid)
                    for row in tbl.findall(".//ROW"):
                        lines.append("\t".join(hwpml_cell_text(c) for c in row.findall("CELL")))
            text = hwpml_p_text(elem)
            if text:
                lines.append(text)
        elif kind == "table":
            tid = id(elem)
            if tid not in seen:
                seen.add(tid)
                for row in elem.findall(".//ROW"):
                    lines.append("\t".join(hwpml_cell_text(c) for c in row.findall("CELL")))
    return "\n".join(lines)


def to_xml(hwp_path: str) -> str:
    root_src = ET.parse(hwp_path).getroot()
    doc = ET.Element("document")
    seen = set()
    for kind, elem in hwpml_walk_body(root_src):
        if kind == "p":
            for tbl in elem.findall(".//TABLE"):
                tid = id(tbl)
                if tid not in seen:
                    seen.add(tid)
                    tbl_el = ET.SubElement(doc, "table")
                    for row in tbl.findall(".//ROW"):
                        row_el = ET.SubElement(tbl_el, "row")
                        for cell in row.findall("CELL"):
                            ET.SubElement(row_el, "cell").text = hwpml_cell_text(cell)
            text = hwpml_p_text(elem)
            if not text:
                continue
            if RE_JANG.match(text):
                ET.SubElement(doc, "heading", level="2").text = text
            elif RE_JO.match(text):
                ET.SubElement(doc, "heading", level="3").text = text
            else:
                ET.SubElement(doc, "paragraph").text = text
        elif kind == "table":
            tid = id(elem)
            if tid not in seen:
                seen.add(tid)
                tbl_el = ET.SubElement(doc, "table")
                for row in elem.findall(".//ROW"):
                    row_el = ET.SubElement(tbl_el, "row")
                    for cell in row.findall("CELL"):
                        ET.SubElement(row_el, "cell").text = hwpml_cell_text(cell)
    ET.indent(doc, space="  ")
    return ET.tostring(doc, encoding="unicode", xml_declaration=False)


def to_html(hwp_path: str) -> str:
    root_src = ET.parse(hwp_path).getroot()
    parts = ['<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>']
    seen = set()
    for kind, elem in hwpml_walk_body(root_src):
        if kind == "p":
            for tbl in elem.findall(".//TABLE"):
                tid = id(tbl)
                if tid not in seen:
                    seen.add(tid)
                    parts.append("<table border='1'>")
                    for row in tbl.findall(".//ROW"):
                        parts.append("<tr>" + "".join(f"<td>{hwpml_cell_text(c)}</td>" for c in row.findall("CELL")) + "</tr>")
                    parts.append("</table>")
            text = hwpml_p_text(elem)
            if not text:
                continue
            esc = html_module.escape(text)
            if RE_JANG.match(text):
                parts.append(f"<h2>{esc}</h2>")
            elif RE_JO.match(text):
                m = re.match(r"(제\s*\d+조(?:의\d+)?\s*\([^)]*\))(.*)", text, re.S)
                if m:
                    parts.append(f"<p><strong>{html_module.escape(m.group(1).strip())}</strong> {html_module.escape(m.group(2).strip())}</p>")
                else:
                    parts.append(f"<p>{esc}</p>")
            else:
                parts.append(f"<p>{esc}</p>")
        elif kind == "table":
            tid = id(elem)
            if tid not in seen:
                seen.add(tid)
                parts.append("<table border='1'>")
                for row in elem.findall(".//ROW"):
                    parts.append("<tr>" + "".join(f"<td>{hwpml_cell_text(c)}</td>" for c in row.findall("CELL")) + "</tr>")
                parts.append("</table>")
    parts.append("</body></html>")
    return "\n".join(parts)
