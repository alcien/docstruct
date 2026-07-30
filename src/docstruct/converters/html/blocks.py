"""HTML → 블록 목록 → markdown/텍스트.

역할:
    문단·목록·표·제목을 블록으로 파싱한 뒤 목표 형식으로 렌더한다.
호출부:
    converters.html
출력:
    markdown 또는 텍스트 문자열
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from docstruct.converters.deps import BS4_AVAILABLE, BeautifulSoup, NavigableString, Tag
from docstruct.converters.html.tables import prepare_md_table_rows, render_md_table, table_rows
from docstruct.converters.html.utils import bullet_to_md, cell_text, normalize_line, tag_text

def collect_html_blocks(soup: "BeautifulSoup") -> list[dict]:
    """
    hwp5html DOM을 문서 순서대로 블록 단위로 수집합니다.

    hwp5html은 표를 <p><span class="TableControl"><table>…</table></span></p>
    형태로 감싸므로, <p> 내부 표를 순서대로 분리해 수집합니다.
    """
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()

    blocks: list[dict] = []
    seen_tables: set[int] = set()

    def add_table(tbl: "Tag") -> None:
        tid = id(tbl)
        if tid in seen_tables:
            return
        seen_tables.add(tid)
        rows = table_rows(tbl)
        if rows:
            caption = tbl.find("caption")
            caption_text = cell_text(caption) if caption else ""
            blocks.append({"type": "table", "rows": rows, "caption": caption_text})

    def add_paragraph(text: str) -> None:
        text = normalize_line(text)
        if text:
            blocks.append({"type": "paragraph", "text": text})

    def walk_mixed(node) -> None:
        """표·텍스트가 섞인 노드의 자식을 문서 순서대로 처리합니다."""
        if type(node) is NavigableString:
            add_paragraph(str(node))
            return
        if not isinstance(node, Tag):
            return
        if node.name in ("script", "style", "head"):
            return
        if node.name == "table":
            add_table(node)
            return
        if node.name == "img":
            add_paragraph("[그림]")
            return
        if node.name == "br":
            return
        for child in node.children:
            walk_mixed(child)

    for el in soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol"]):
        if el.find_parent(["td", "th"]):
            continue
        if el.find_parent("p"):
            continue

        if el.name == "table":
            if el.find_parent("table") or el.find_parent("p"):
                continue
            add_table(el)
            continue

        if el.name == "p":
            if el.find("table"):
                for child in el.children:
                    walk_mixed(child)
            else:
                text = tag_text(el)
                img_count = len(el.find_all("img"))
                if img_count:
                    ph = " ".join(["[그림]"] * img_count)
                    text = f"{text} {ph}".strip() if text else ph
                add_paragraph(text)
            continue

        if el.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            text = tag_text(el)
            if text:
                blocks.append({"type": "heading", "level": int(el.name[1]), "text": text})
            continue

        if el.name in ("ul", "ol"):
            blocks.append({"type": "list", "ordered": el.name == "ol", "element": el})

    return blocks


def blocks_to_text(blocks: list[dict]) -> str:
    """HTML 블록 목록을 평문으로 만든다.

    입력: blocks — 파싱된 블록 목록
    출력: 텍스트 문자열
    """
    lines: list[str] = []
    for block in blocks:
        kind = block["type"]
        if kind == "paragraph":
            lines.append(block["text"])
        elif kind == "heading":
            lines.append(block["text"])
        elif kind == "table":
            if block.get("caption"):
                lines.append(block["caption"])
            for row in block["rows"]:
                lines.append("\t".join(row))
        elif kind == "list":
            el = block["element"]
            ordered = block["ordered"]
            for i, li in enumerate(el.find_all("li", recursive=False), 1):
                text = tag_text(li)
                if text:
                    prefix = f"{i}." if ordered else "-"
                    lines.append(f"{prefix} {text}")
    return "\n".join(lines)


def md_inline_text(tag: "Tag") -> str:
    """인라인 요소를 마크다운 텍스트로 변환합니다."""
    result = []
    for node in tag.children:
        if isinstance(node, NavigableString):
            result.append(str(node))
        elif node.name in ("strong", "b"):
            inner = md_inline_text(node).strip()
            result.append(f"**{inner}**" if inner else "")
        elif node.name in ("em", "i"):
            inner = md_inline_text(node).strip()
            result.append(f"*{inner}*" if inner else "")
        elif node.name == "br":
            result.append("\n")
        elif node.name == "img":
            src = node.get("src", "")
            alt = node.get("alt", "이미지")
            if src.startswith("data:"):
                result.append("[그림]")
            else:
                result.append(f"![{alt}]({src})")
        elif node.name is not None:
            result.append(md_inline_text(node))
    return "".join(result)


def md_process_list(tag: "Tag", depth: int = 0, ordered: bool = False) -> list[str]:
    lines: list[str] = []
    idx = 1
    for child in tag.children:
        if not isinstance(child, Tag) or child.name != "li":
            continue
        indent = "  " * depth
        prefix = f"{idx}." if ordered else "-"
        direct_text = "".join(
            md_inline_text(c) if isinstance(c, Tag) and c.name not in ("ul", "ol") else
            (str(c) if isinstance(c, NavigableString) else "")
            for c in child.children
        ).strip()
        if direct_text:
            lines.append(f"{indent}{prefix} {direct_text}")
        for sub in child.find_all(["ul", "ol"], recursive=False):
            lines.extend(md_process_list(sub, depth + 1, sub.name == "ol"))
        idx += 1
    return lines


def blocks_to_markdown(blocks: list[dict]) -> str:
    """HTML 블록 목록을 markdown 으로 만든다.

    입력: blocks — 파싱된 블록 목록
    출력: markdown 문자열
    """
    parts: list[str] = []
    for block in blocks:
        kind = block["type"]
        if kind == "paragraph":
            parts.append(f"\n{bullet_to_md(block['text'])}\n")
        elif kind == "heading":
            level = block["level"]
            parts.append(f"\n{'#' * level} {block['text']}\n")
        elif kind == "table":
            if block.get("caption"):
                parts.append(f"\n**{block['caption']}**\n")
            md = render_md_table(prepare_md_table_rows(block["rows"]))
            if md:
                parts.append(f"\n{md}\n")
        elif kind == "list":
            lines = md_process_list(block["element"], ordered=block["ordered"])
            if lines:
                parts.append("\n" + "\n".join(lines) + "\n")

    result = "\n".join(parts)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()

def blocks_to_xml(blocks: list[dict]) -> str:
    """블록 목록을 구조화 XML로 렌더링합니다."""
    doc = ET.Element("document")
    for block in blocks:
        kind = block["type"]
        if kind == "paragraph":
            ET.SubElement(doc, "paragraph").text = block["text"]
        elif kind == "heading":
            ET.SubElement(doc, "heading", level=str(block["level"])).text = block["text"]
        elif kind == "table":
            tbl = ET.SubElement(doc, "table")
            if block.get("caption"):
                ET.SubElement(tbl, "caption").text = block["caption"]
            for row in block["rows"]:
                row_el = ET.SubElement(tbl, "row")
                for cell in row:
                    cell_el = ET.SubElement(row_el, "cell")
                    if cell:
                        cell_el.text = cell
        elif kind == "list":
            list_el = ET.SubElement(doc, "list", type="ol" if block["ordered"] else "ul")
            el = block["element"]
            for li in el.find_all("li", recursive=False):
                text = tag_text(li)
                if text:
                    ET.SubElement(list_el, "item").text = text
    ET.indent(doc, space="  ")
    return ET.tostring(doc, encoding="unicode", xml_declaration=False)
