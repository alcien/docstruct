"""align — 두 판독 결과 맞춤 (HWPX ↔ PDF 쪽 번호).

축: 공통 — 형식을 둘 다 알아야 하는 유일한 자리.
역할:
    쪽 경계가 없는 HWPX 산출에 같은 문서의 PDF 산출에서 쪽 번호를 물려준다.
    본문 눈금(text_anchors)이 주력, 표 짝짓기(table_anchors)가 보완, 사이는
    비례로 채운다(interpolate). 못 맞춘 표는 버리지 않고 `unmatched` 로
    사유와 함께 낸다 — 쪽을 모르는 것과 없는 것은 다르다.
호출부:
    docstruct.cli (`--align`) · overlay `/align/pages` (rag/adapters/page_align)

모듈 (입력 → 출력 · 역할):
    documents.py          (hwpx dict, pdf dict) → 쪽으로 나뉜 dict / markdown
                          진입점 align_documents. 성적은 맞출 수 있는 표만으로
                          잰다(matchable). 본문으로 준 쪽은 표 순서로 검증(order_check).
    page_map.py           본문·표·목차 눈금 → [Pair] · 쪽별 조각
                          눈금 찾기(text/toc/table_anchors) · 병합 · 보간 ·
                          쪽 나누기(split_text_by_page)의 기계.
"""
from docstruct.align.documents import align_documents, to_markdown
from docstruct.align.page_map import Pair, align, pages, split_by_page

__all__ = ["Pair", "align", "align_documents", "pages", "split_by_page",
           "to_markdown"]
