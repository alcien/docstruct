"""시험 CLI — document.json → structured.json (설계 문서 §4 local).

쓰는 법:
    python -m docstruct.structuring out/document.json -o out/structured.json
    python -m docstruct.structuring out/document.json --pdf 원본.pdf   # 열 검산 켬
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from docstruct.structuring import structure_document


def main() -> int:
    """CLI 진입점."""
    parser = argparse.ArgumentParser(description="구조화: document.json → structured.json")
    parser.add_argument("document", type=Path)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--pdf", type=Path, help="원본 PDF — 이어붙임 열 배치 검산을 켠다")
    args = parser.parse_args()
    if not args.document.is_file():
        print(f"파일이 없습니다: {args.document}", file=sys.stderr)
        return 1
    with args.document.open(encoding="utf-8") as handle:
        document = json.load(handle)
    structured = structure_document(document, pdf_path=args.pdf)
    output = args.output or args.document.with_name("structured.json")
    with output.open("w", encoding="utf-8") as handle:
        json.dump(structured, handle, ensure_ascii=False, indent=1)
    tables = structured["tables"]
    print(f"표 {len(tables)} · 레코드 {sum(len(t['records']) for t in tables)} · "
          f"계층 {sum(len(t['hierarchy']) for t in tables)} · "
          f"계약 위반 {len(structured['problems'])} → {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
