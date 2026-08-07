"""그림으로 잡힌 영역을 표 / 텍스트 / 이미지로 가른다 (LLM 호출 없음).

역할:
    레이아웃 모델이 ``PictureItem`` 으로 분류한 영역은 셋 중 하나다.

      · **표**       격자 구조. 표로 복원해야 한다
      · **도표·텍스트** 조직도·흐름도처럼 글자는 많지만 격자가 아닌 것.
                     격자로 만들면 의미가 망가지므로 **텍스트로** 뽑아야 한다
      · **사진·로고**  글자가 없다. 그대로 그림으로 둔다

    예전에는 표/이미지 두 갈래뿐이라 조직도가 갈 곳이 없었다. 그림으로
    두면 533자가 통째로 사라지고, 표로 승격하면 흐름도가 격자로 뭉개졌다.
호출부:
    docstruct.extractors.pdf (0단계 후보 선별 직후)
출력:
    RegionKind — TABLE | TEXT | IMAGE, 판정 근거 포함

판정 근거
---------
글자 좌표만 본다. 정부 문서 2종으로 실측한 값:

    조직도+흐름도 : 최빈 열수 2 (52%) · 열 시작 x 편차 최대 112.0pt → 표 아님
    예산 대비표    : 최빈 열수 5 (73%) · 열 시작 x 편차 최대   9.4pt → 표

표는 **같은 열이 여러 줄에서 같은 x 에서 시작한다.** 도표는 상자가 제각기
놓여 있어 편차가 크다. 열 개수만 보면 갈리지 않고(둘 다 다중 셀 줄이 있다)
정렬 일관성을 함께 봐야 한다.
"""
from __future__ import annotations

import logging
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

_log = logging.getLogger(__name__)

#: 같은 줄로 묶을 세로 허용 오차(pt). 글자 중심의 y 를 이 단위로 양자화한다.
LINE_TOLERANCE = 6.0

#: 셀 경계로 볼 가로 공백(pt). 이보다 벌어지면 다른 칸으로 본다.
CELL_GAP = 8.0

#: 표로 인정할 최소 조건.
MIN_TABLE_ROWS = 3          # 최빈 열수를 가진 줄이 이만큼은 있어야 한다
MIN_TABLE_COLS = 2
MIN_MODE_SHARE = 0.5        # 다중 셀 줄 중 최빈 열수가 차지하는 비율
MAX_COLUMN_DRIFT = 12.0     # 열 시작 x 의 표준편차 상한(pt)

#: 텍스트를 뽑을 최소 글자 수.
#:
#: 표 후보 문턱(text_probe.MIN_CHARS = 80)과 **분리했다**. 파이프라인
#: 도표처럼 상자마다 짧은 레이블만 있는 경우 67자 정도가 나오는데, 표
#: 문턱을 그대로 쓰면 그 글자가 통째로 사라진다. 표로 만들 만큼은 아니어도
#: 본문으로 뽑을 값어치는 있다.
MIN_TEXT_CHARS = 30

#: 텍스트로 뽑을 최소 줄 수. 한 줄짜리는 캡션·라벨이므로 그림으로 둔다.
MIN_TEXT_LINES = 2


class RegionKind(str, Enum):
    """그림 영역의 실제 성격."""

    TABLE = "table"
    TEXT = "text"
    IMAGE = "image"


@dataclass
class RegionVerdict:
    """판정 결과와 근거.

    입력(필드):
        kind        판정
        reason      사람이 읽을 근거 (로그·트레이스용)
        rows        다중 셀 줄 수
        mode_cols   최빈 열 수
        mode_share  최빈 열 수가 차지하는 비율
        drift       열 시작 x 의 최대 표준편차(pt)
    """

    kind: RegionKind
    reason: str
    rows: int = 0
    mode_cols: int = 0
    mode_share: float = 0.0
    drift: float = 0.0


def classify_region(
    pdf_path: str | Path,
    page_no: int,
    bbox: dict[str, float],
    *,
    char_count: int | None = None,
) -> RegionVerdict:
    """그림 영역 하나를 표/텍스트/이미지로 가른다.

    입력:
        pdf_path    원본 PDF 경로
        page_no     페이지 번호(1-based)
        bbox        TOPLEFT 좌표 {l, t, r, b}
        char_count  이미 잰 글자 수가 있으면 전달 (없으면 여기서 센다)
    출력: RegionVerdict
    비고: 좌표를 읽지 못하면 IMAGE 로 본다 — 판단 근거가 없으면 그대로 두는
          편이 안전하다.
    """
    scanned = _text_rows(pdf_path, page_no, bbox)
    if scanned is None:
        return RegionVerdict(RegionKind.IMAGE, "글자 좌표를 읽지 못함")
    rows, scanned_chars = scanned

    chars = char_count if char_count is not None else scanned_chars
    if not rows or chars < MIN_TEXT_CHARS or len(rows) < MIN_TEXT_LINES:
        return RegionVerdict(
            RegionKind.IMAGE,
            f"글자 {chars}자 · {len(rows)}줄 — 사진·로고로 둡니다",
        )

    multi = [r for r in rows if len(r) >= MIN_TABLE_COLS]
    if not multi:
        return RegionVerdict(
            RegionKind.TEXT, "여러 칸으로 나뉜 줄이 없음 — 격자 아님", rows=0
        )

    mode_cols, mode_count = Counter(len(r) for r in multi).most_common(1)[0]
    share = mode_count / len(multi)
    same = [r for r in multi if len(r) == mode_cols]
    drift = _column_drift(same)

    if mode_count < MIN_TABLE_ROWS:
        kind, why = RegionKind.TEXT, f"같은 열수({mode_cols})인 줄이 {mode_count}개뿐"
    elif share < MIN_MODE_SHARE:
        kind, why = RegionKind.TEXT, f"열 수가 들쭉날쭉 (최빈 {mode_cols}열이 {share:.0%})"
    elif drift > MAX_COLUMN_DRIFT:
        kind, why = RegionKind.TEXT, (
            f"열 시작 위치가 줄마다 어긋남 (편차 {drift:.0f}pt) — 도표로 보입니다"
        )
    else:
        kind, why = RegionKind.TABLE, (
            f"{mode_cols}열이 {mode_count}줄 반복 · 열 정렬 편차 {drift:.0f}pt"
        )

    return RegionVerdict(kind, why, rows=len(multi), mode_cols=mode_cols,
                         mode_share=share, drift=drift)


def _text_rows(
    pdf_path: str | Path, page_no: int, bbox: dict[str, float]
) -> tuple[list[list[float]], int] | None:
    """영역 안의 글자를 줄→칸으로 묶는다.

    입력: pdf_path, page_no(1-based), bbox(TOPLEFT)
    출력: (줄마다 칸 시작 x 목록, 영역 안 글자 수). 읽지 못하면 None
    """
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return None
    try:
        pdf = pdfium.PdfDocument(str(pdf_path))
    except Exception as exc:                     # noqa: BLE001
        _log.debug("PDF 열기 실패: %s", exc)
        return None

    try:
        index = page_no - 1
        if index < 0 or index >= len(pdf):
            return None
        page = pdf[index]
        height = page.get_size()[1]
        textpage = page.get_textpage()
        # TOPLEFT → PDF 좌표(BOTTOMLEFT)
        left, right = float(bbox["l"]), float(bbox["r"])
        top, bottom = height - float(bbox["t"]), height - float(bbox["b"])

        char_total = 0
        lines: dict[int, list[tuple[float, float]]] = defaultdict(list)
        for i in range(textpage.count_chars()):
            box = textpage.get_charbox(i)
            if not box:
                continue
            cl, cb, cr, ct = box
            if cr <= cl or ct <= cb:
                continue                          # 공백 등 폭이 없는 글자
            if cl < left or cr > right or cb < bottom or ct > top:
                continue                          # 영역 밖
            lines[round((ct + cb) / 2 / LINE_TOLERANCE)].append((cl, cr))
            char_total += 1
    except (KeyError, TypeError, ValueError, Exception) as exc:  # noqa: BLE001
        _log.debug("글자 좌표 수집 실패: %s", exc)
        return None
    finally:
        try:
            pdf.close()
        except Exception:                        # noqa: BLE001
            pass

    rows: list[list[float]] = []
    for key in sorted(lines, reverse=True):       # 위에서 아래로
        spans = sorted(lines[key])
        starts, cursor = [], list(spans[0])
        for cl, cr in spans[1:]:
            if cl - cursor[1] > CELL_GAP:
                starts.append(cursor[0])
                cursor = [cl, cr]
            else:
                cursor[1] = max(cursor[1], cr)
        starts.append(cursor[0])
        rows.append(starts)
    return rows, char_total


def _column_drift(rows: list[list[float]]) -> float:
    """같은 열 수를 가진 줄들에서 열 시작 x 가 얼마나 흔들리는지.

    입력: rows — 열 수가 같은 줄들의 칸 시작 x 목록
    출력: 열별 표준편차 중 최대값(pt). 줄이 2개 미만이면 큰 값
    비고: 표는 같은 열이 매 줄 같은 x 에서 시작한다. 도표는 상자가 제각기
          놓여 있어 이 값이 크게 나온다 — 실측에서 9.4pt 대 112.0pt 였다.
    """
    if len(rows) < 2:
        return float("inf")
    cols = len(rows[0])
    return max(
        statistics.pstdev([row[i] for row in rows]) for i in range(cols)
    )
