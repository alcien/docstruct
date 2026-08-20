"""PDF 텍스트 레이어에서 글자 좌표를 읽는다.

역할:
    렌더·OCR 없이 글자와 그 위치를 낸다.
호출부:
    docstruct.experiments (grid_refine, two_way_match)

왜 OCR 이 아니라 이것인가
---------------------
텍스트 PDF 는 글자가 좌표로 들어 있다. 그것을 이미지로 렌더한 뒤 OCR 로
다시 읽으면

    · 79쪽 문서에서 전 페이지 렌더가 필요하고
    · OCR 오차가 더해지며
    · 원본보다 정확할 수 없다

스캔본은 글자가 이미지이므로 여전히 OCR 이 필요하다. 이 모듈은 **텍스트
레이어가 있을 때만** 쓴다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)

#: 한 낱말로 묶을 가로 간격 (포인트). 이보다 벌어지면 다른 낱말이다.
WORD_GAP = 3.0

#: 같은 줄로 볼 세로 오차 (포인트).
LINE_TOLERANCE = 2.0


@dataclass
class TextRun:
    """같은 줄에 이어진 글자 덩어리 (TOPLEFT 좌표)."""

    text: str
    left: float
    top: float
    right: float
    bottom: float


def read_text_runs(pdf_path: str | Path, page_no: int) -> list[TextRun]:
    """이 쪽의 글자 덩어리를 낸다.

    입력: pdf_path — PDF 경로, page_no — 1부터
    출력: TextRun 목록. 텍스트 레이어가 없으면 빈 목록
    비고:
        글자마다 좌표를 읽어 **가까운 것끼리 묶는다.** 낱말 단위가 되므로
        OCR 조각과 비슷한 알갱이가 된다.

        좌표는 TOPLEFT 로 맞춘다 — 표 bbox 와 같은 기준이라 바로 견줄 수
        있다.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return []
    try:
        document = pdfium.PdfDocument(str(pdf_path))
    except Exception as exc:                     # noqa: BLE001 - 진단 보조다
        _log.warning("PDF 를 열지 못했습니다: %s", exc)
        return []
    try:
        index = page_no - 1
        if not 0 <= index < len(document):
            return []
        page = document[index]
        height = page.get_size()[1]
        textpage = page.get_textpage()

        runs: list[TextRun] = []
        current: TextRun | None = None
        for position in range(textpage.count_chars()):
            try:
                left, bottom, right, top = textpage.get_charbox(position)
            except Exception:                    # noqa: BLE001
                continue
            char = textpage.get_text_range(position, 1)
            if not char or not char.strip():
                current = None                   # 공백에서 끊는다
                continue
            # BOTTOMLEFT → TOPLEFT
            box_top, box_bottom = height - top, height - bottom
            if (current is not None
                    and abs(box_top - current.top) <= LINE_TOLERANCE
                    and left - current.right <= WORD_GAP):
                current.text += char
                current.right = max(current.right, right)
                current.bottom = max(current.bottom, box_bottom)
                continue
            current = TextRun(text=char, left=left, top=box_top,
                              right=right, bottom=box_bottom)
            runs.append(current)
        return runs
    except Exception as exc:                     # noqa: BLE001
        _log.warning("%s쪽 글자 좌표를 읽지 못했습니다: %s", page_no, exc)
        return []
    finally:
        document.close()


def has_text_layer(pdf_path: str | Path, page_no: int) -> bool:
    """이 쪽에 쓸 만한 텍스트 레이어가 있는가.

    입력: pdf_path — PDF 경로, page_no — 1부터
    출력: 글자가 있으면 True
    비고:
        스캔본은 False 가 나온다. 그때는 OCR(렌더 필요)로 가야 한다.
    """
    return len(read_text_runs(pdf_path, page_no)) >= 3
