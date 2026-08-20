"""PDF 가 스캔본인지 원본에서 미리 가린다.

역할:
    docling 을 부르기 **전에** 텍스트 레이어 유무를 본다.
호출부:
    docstruct.converters.pdf.docling_backend (OCR 을 켤지 정할 때)

왜 미리 봐야 하는가
----------------
스캔본은 지면을 두 번 읽는다.

    docling 내장 OCR   쪽당 2.9초  → 중국어 모델이라 한자로 나옴 (버림)
    한국어 재판독      쪽당 1.7초  → 실제로 쓰는 결과

실측(주택과세금 377쪽): 추출 1,096초 + 재판독 627초 = **29분**. 앞의 것이
버려지는 결과에 쓰인 시간이다.

docling 을 부른 뒤에는 늦다 — 이미 OCR 이 끝나 있다. 그래서 `pypdfium2` 로
먼저 몇 쪽만 훑어본다.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

_log = logging.getLogger(__name__)

#: 표본으로 볼 쪽 수. 앞쪽은 표지·목차라 건너뛴다.
SAMPLE_PAGES = 12

#: 표본에서 건너뛸 앞쪽 수.
SKIP_FRONT = 3

#: 한 쪽에 글자가 이보다 적으면 텍스트 레이어가 없다고 본다.
#:
#: 실측(주택과세금 380쪽): 본문은 이미지인데 **머리말·바닥글만 텍스트**로
#: 있어 쪽당 97자가 나왔다.
#:
#:     '26. 5. 11. 오후 5:44 2025 주택과세금
#:     https://www.nts.go.kr/upload/...index.html  6/380
#:
#: 이런 장식 텍스트를 빼고 세야 한다. 본문이 있는 쪽은 수백 자가 나온다.
MIN_CHARS_PER_PAGE = 300

#: 머리말·바닥글로 보고 세지 않을 것 — URL·날짜·쪽표시.
_BOILERPLATE_RE = re.compile(
    r"https?://\S+"                      # URL
    r"|\d{1,2}\s*[./]\s*\d{1,2}\s*[./]\s*\d{1,2}"   # 날짜
    r"|오전|오후"
    r"|\d+\s*/\s*\d+"                   # 브라우저 쪽표시 6/380
)

#: 표본 중 이 비율 이상이 비어야 스캔본으로 본다.
#: 일부만 스캔인 문서(합본)를 스캔본으로 몰면 텍스트 쪽까지 OCR 로 읽는다.
MIN_EMPTY_RATIO = 0.8

_WHITESPACE_RE = re.compile(r"\s")


def looks_scanned(pdf_path: str | Path) -> bool:
    """이 PDF 가 스캔본인가.

    입력: pdf_path — PDF 경로
    출력: 스캔본이면 True. 판단하지 못하면 False
    비고:
        **판단하지 못하면 False 를 낸다.** 스캔본이 아닌데 스캔본으로 보면
        docling OCR 을 꺼서 표 내용을 잃는다. 반대 실수는 시간만 더 쓴다.

        앞쪽 몇 쪽은 표지·목차라 글자가 적을 수 있어 건너뛴다.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return False
    try:
        document = pdfium.PdfDocument(str(pdf_path))
    except Exception as exc:                     # noqa: BLE001 - 판정 실패는 치명적이지 않다
        _log.debug("텍스트 레이어를 확인하지 못했습니다: %s", exc)
        return False

    try:
        total = len(document)
        if total <= SKIP_FRONT:
            return False
        # 문서 전체에 고루 퍼지도록 건너뛰며 뽑는다
        step = max((total - SKIP_FRONT) // SAMPLE_PAGES, 1)
        indexes = list(range(SKIP_FRONT, total, step))[:SAMPLE_PAGES]
        if not indexes:
            return False

        empty = 0
        for index in indexes:
            try:
                text = document[index].get_textpage().get_text_range()
            except Exception:                    # noqa: BLE001
                continue
            body = _BOILERPLATE_RE.sub("", text or "")
            if len(_WHITESPACE_RE.sub("", body)) < MIN_CHARS_PER_PAGE:
                empty += 1
        ratio = empty / len(indexes)
        if ratio >= MIN_EMPTY_RATIO:
            _log.info("스캔본으로 봅니다 (표본 %d쪽 중 %d쪽이 비어 있음)",
                      len(indexes), empty)
            return True
        return False
    except Exception as exc:                     # noqa: BLE001
        _log.debug("스캔본 판정 실패: %s", exc)
        return False
    finally:
        document.close()
