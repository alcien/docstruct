"""그림 영역 안의 텍스트 밀도 측정 (LLM 호출 없음).

역할:
    레이아웃 모델이 표를 ``PictureItem`` 으로 잘못 분류하면 TableFormer 가
    돌지 않아 내용이 통째로 텍스트화되지 않는다. 그런 영역을 찾아내려면
    먼저 후보를 좁혀야 하는데, 사진·로고까지 전부 LLM 에 물어보면 호출 수가
    그림 개수만큼 늘어난다.

    디지털 PDF 는 텍스트 레이어가 있으므로 **영역 안의 글자 수를 공짜로**
    셀 수 있다. 사진·로고는 0자에 가깝고 오분류된 표는 수백 자다. 이 한
    가지 신호로 대부분을 걸러낸다.
호출부:
    docstruct.extractors.pdf.extract_pdf_pages
출력:
    영역 id → TextDensity (글자 수, 줄 수, 표 후보 여부)

좌표계 주의
-----------
Docling 의 ``item_bbox`` 는 **좌상단 원점**(TOPLEFT)이고, PDF 와 pypdfium2 는
**좌하단 원점**이다. 여기서 페이지 높이를 이용해 변환한다. 그냥 넘기면
페이지 반대쪽을 읽어 항상 0자가 나온다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)

#: 표 후보로 볼 최소 글자 수. 캡션 한 줄("<그림 1> 조직도")에 걸리지 않도록
#: 넉넉히 잡는다.
MIN_CHARS = 80

#: 표 후보로 볼 최소 줄 수. 한 줄짜리는 표가 아니라 캡션·라벨이다.
MIN_LINES = 3


@dataclass
class TextDensity:
    """그림 영역 안의 텍스트 양.

    입력(필드):
        chars       공백을 제외한 글자 수
        lines       빈 줄을 뺀 줄 수
        text        영역 안 텍스트 전문 (재추출 근거로 넘김)
        sample      앞부분 미리보기 (디버그·로그용)
    출력(파생):
        table_candidate  표로 승격할 후보인지
    """

    chars: int
    lines: int
    text: str = ""
    sample: str = ""

    @property
    def table_candidate(self) -> bool:
        """표 후보 여부.

        입력: chars, lines
        출력: 둘 다 하한을 넘으면 True
        """
        return self.chars >= MIN_CHARS and self.lines >= MIN_LINES


def probe_regions(
    pdf_path: str | Path,
    regions: dict[str, tuple[int, dict[str, float]]],
) -> dict[str, TextDensity]:
    """그림 영역들의 텍스트 밀도를 한 번에 잰다.

    입력:
        pdf_path  원본 PDF 경로
        regions   영역 id → (페이지 번호(1-based), TOPLEFT bbox {l,t,r,b})
    출력:
        영역 id → TextDensity. 측정할 수 없으면 그 id 는 결과에서 빠진다
    비고:
        페이지를 한 번씩만 열도록 페이지별로 묶어 처리한다. pypdfium2 가
        없거나 스캔 PDF 라 텍스트 레이어가 없으면 빈 결과를 돌려주고,
        호출부는 승격 후보 없음으로 처리한다.
    """
    if not regions:
        return {}
    try:
        import pypdfium2 as pdfium
    except ImportError:
        _log.debug("pypdfium2 없음 — 그림 텍스트 밀도 측정 생략")
        return {}

    by_page: dict[int, list[tuple[str, dict[str, float]]]] = {}
    for region_id, (page_no, bbox) in regions.items():
        if bbox:
            by_page.setdefault(page_no, []).append((region_id, bbox))

    out: dict[str, TextDensity] = {}
    try:
        pdf = pdfium.PdfDocument(str(pdf_path))
    except Exception as exc:
        _log.debug("PDF 열기 실패 — 텍스트 밀도 측정 생략: %s", exc)
        return {}

    try:
        for page_no, items in sorted(by_page.items()):
            index = page_no - 1                  # docling 은 1-based
            if index < 0 or index >= len(pdf):
                continue
            try:
                page = pdf[index]
                height = page.get_size()[1]
                textpage = page.get_textpage()
            except Exception as exc:
                _log.debug("%s페이지 텍스트 레이어 접근 실패: %s", page_no, exc)
                continue
            for region_id, bbox in items:
                density = _measure(textpage, bbox, height)
                if density is not None:
                    out[region_id] = density
    finally:
        try:
            pdf.close()
        except Exception:                        # noqa: BLE001 - 정리 실패는 무시
            pass
    return out


def _measure(textpage, bbox: dict[str, float], page_height: float) -> TextDensity | None:
    """영역 하나의 텍스트를 읽어 밀도를 만든다.

    입력:
        textpage     pypdfium2 PdfTextPage
        bbox         TOPLEFT 좌표 {l, t, r, b}
        page_height  페이지 높이(points) — 원점 변환에 사용
    출력: TextDensity. 읽지 못하면 None
    """
    try:
        left = float(bbox["l"])
        right = float(bbox["r"])
        # TOPLEFT → BOTTOMLEFT. t 는 위쪽 변, b 는 아래쪽 변이므로 뒤집힌다.
        top = page_height - float(bbox["t"])
        bottom = page_height - float(bbox["b"])
    except (KeyError, TypeError, ValueError):
        return None
    if right <= left or top <= bottom:
        return None

    try:
        text = textpage.get_text_bounded(
            left=left, bottom=bottom, right=right, top=top
        )
    except Exception as exc:
        _log.debug("영역 텍스트 추출 실패: %s", exc)
        return None

    stripped = text.strip()
    lines = [line for line in stripped.splitlines() if line.strip()]
    chars = len("".join(stripped.split()))
    return TextDensity(
        chars=chars, lines=len(lines), text=stripped, sample=stripped[:120]
    )
