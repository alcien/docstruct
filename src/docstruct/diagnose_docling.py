"""Docling 파싱 진단 스크립트.

역할:
    특정 PDF 에서 Docling 이 무엇을 뽑았는지 요소 단위로 덤프해
    파싱 문제의 원인을 좁힌다.
호출부:
    `python -m docstruct.diagnose_docling <PDF>`
출력:
    표준출력에 요소 목록과 페이지별 통계
"""
from __future__ import annotations

import sys


def _describe(obj, name: str, max_items: int = 3) -> None:
    print(f"\n--- {name}: {type(obj)!r} ---")
    attrs = [a for a in dir(obj) if not a.startswith("_")]
    print(f"공개 속성/메서드 ({len(attrs)}개): {attrs}")

    for cand in ("cells", "parsed_page", "predictions", "size", "page_no"):
        if hasattr(obj, cand):
            val = getattr(obj, cand)
            print(f"  .{cand} = {type(val)!r}", end="")
            if isinstance(val, (list, tuple)):
                print(f"  len={len(val)}")
                if val:
                    _describe(val[0], f"{name}.{cand}[0]", max_items=1)
            else:
                print(f"  값={val!r}"[:200])


def main() -> None:
    if len(sys.argv) < 2:
        print("사용법: python -m docstruct.diagnose_docling <pdf경로> [페이지번호=1]")
        raise SystemExit(1)

    pdf_path = sys.argv[1]
    target_page = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    from docstruct.converters.pdf.docling_backend import get_document_converter

    print(f"변환 중: {pdf_path} ...")
    result = get_document_converter().convert(pdf_path)

    print(f"\n=== result: {type(result)!r} ===")
    print(f"공개 속성: {[a for a in dir(result) if not a.startswith('_')]}")

    pages = getattr(result, "pages", None)
    print(f"\n=== result.pages: {type(pages)!r} ===")
    if isinstance(pages, dict):
        print(f"키 목록 (앞 5개): {list(pages.keys())[:5]}")
        page = pages.get(target_page) or next(iter(pages.values()), None)
    elif isinstance(pages, (list, tuple)):
        print(f"길이: {len(pages)}")
        page = pages[target_page - 1] if len(pages) >= target_page else (pages[0] if pages else None)
    else:
        page = None

    if page is None:
        print("페이지 객체를 못 찾았습니다 — 위 결과를 그대로 알려주세요.")
        return

    _describe(page, "page")

    # 문서 자체에서 이 페이지의 텍스트 아이템 개수 (비교용 — 실제 본문은 이 경로로 나옵니다)
    doc = result.document
    from docling_core.types.doc.labels import DocItemLabel

    def _page_no_of(item):
        prov = getattr(item, "prov", None) or []
        return prov[0].page_no if prov else None

    count_by_type: dict[str, int] = {}
    for item, _ in doc.iterate_items():
        if _page_no_of(item) != target_page:
            continue
        label = str(getattr(item, "label", type(item).__name__))
        count_by_type[label] = count_by_type.get(label, 0) + 1

    print(f"\n=== doc.iterate_items() 로 본 {target_page}페이지 아이템 (실제 본문 출처) ===")
    print(count_by_type or "(해당 페이지 아이템 없음 — page_no 매칭 확인 필요)")


if __name__ == "__main__":
    main()
