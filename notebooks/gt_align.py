"""(옮겨졌습니다) 쪽 맞춤은 이제 라이브러리 기능입니다.

    from docstruct.align import align, pages, split_by_page

0.4.44 에서 `docstruct/align/page_map.py` 로 옮겼습니다 — 주요 기능이라
노트북에 두지 않습니다. 서비스에서는 `/align/pages` 로 쓸 수 있습니다
(HWPX·PDF 의 document.json 두 개를 올리면 쪽으로 나뉜 JSON·MD 를 받습니다).

이 파일은 옛 노트북이 깨지지 않게 이름만 이어 줍니다.
"""
from docstruct.align.page_map import (  # noqa: F401
    MIN_SIMILARITY, Pair, align, merges_of, pages, score_merges, size_of,
    split_by_page, toc_anchors,
)
