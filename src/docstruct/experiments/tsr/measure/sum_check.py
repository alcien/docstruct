"""실험 ⑧ — 예산표 산술 검산 (sum_check).

입력:
    예산표
출력:
    산술 검산 결과 (sum_check 필드)

역할:
    합계 열이 있는 표에서 행마다 `연도열 합 = 합계` 를 확인한다.
    불일치는 **구조가 깨졌다는 결정적 신호**다 — 병합이 손실되면 값이
    옆 칸으로 밀려 합이 어긋난다.
호출부:
    pipeline (실험이 켜졌을 때) — 복원(⑦)·재작성(fill/vlm) 뒤에 돈다
설정:
    DOCSTRUCT_EXP_SUM_CHECK=false 로 끈다 — 0.4.3 승격 뒤 **기본 켬** (registry.DEFAULT_ON)

왜 있는가
--------
가설 H5 (가설재검토_복원기준.md). 판정이 아니라 **검증** 실험이다 —
어느 경로(파서·⑦ 복원·LLM 재작성·VLM)로 왔든 뒤에 붙어 결과를 잰다.
LLM 없이, 어느 파일 유형(HWPX·텍스트 PDF·스캔 PDF)에든 같은 코드로 돈다.

TabStruct-Net V2 (2025) 가 정렬·연속·비중첩 같은 인지 제약을 **학습
목적함수**에 넣어 효과를 보였다. 우리는 학습을 지양하므로 같은 제약을
**검산식**으로 옮긴다 — 산술 불변량은 그중 가장 강한 것이다.

왜 이렇게 좁게 잡았나
------------------
오탐 하나가 신뢰를 깎는다 (grid_consensus 의 교훈 — 61건 전부 오탐으로
폐기). 그래서 다음이 **모두** 성립할 때만 검사한다.

    · 머리행에 `합계`/`계` 열이 있다
    · 그 행에서 연도꼴 머리(`'24`·`2024`·`2024년`…) 열이 2개 이상이다
    · 그 행의 연도값·합계값이 전부 수로 읽힌다

이 틀은 성과계획서 예산표(연도별 + 합계)의 표준 꼴이다. 행 방향
합계(`합계` 행)는 소계 층이 섞여 오탐 위험이 커 1판에서는 뺐다 —
HWPX 실측으로 오탐 0 을 확인한 뒤에 넓힌다.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from docstruct.experiments.registry import Experiment, register

if TYPE_CHECKING:
    from docstruct.models import PageContent

_log = logging.getLogger(__name__)

#: 합계 열 머리. `총계` 는 층이 섞인 표(소계+합계+총계)에서만 나와 뺐다.
_TOTAL_HEADER = re.compile(r"^(합\s*계|계)$")

#: 연도꼴 머리. '24 · 24년 · 2024 · 2024년 (+ 뒤에 계획/실적 등 한 낱말 허용).
_YEAR_HEADER = re.compile(r"^['’]?(\d{2}|(19|20)\d{2})\s*년?(\s*(?!\d)\S{1,3})?$")

#: 수 표기. 쉼표·공백 제거 뒤 판정. △·▲·괄호는 음수.
_NUMBER = re.compile(r"^[-+]?\d+(\.\d+)?$")

#: 부동소수 비교 여유. 예산은 소수 한 자리(억원)까지라 0.05 면 넉넉하다.
_TOLERANCE = 0.05

#: 총계 행 딱지. 글머리(`ㅇ`·`-`·`□`)와 회계 이름(`[일반회계]`)이 앞에
#: 붙는 것이 실측 꼴이다: `ㅇ 총계` `-총계` `[일반회계] 합계` `□ 재정사업 합계`.
_TOTAL_ROW = re.compile(r"(총\s*계|합\s*계)\s*$")

#: 비율 열 머리 — 합계 행의 값이 **합이 아니다** (전체 대비 비율이거나
#: 전체 증감률이다). 실측 오탐 세 건이 전부 이 꼴이었다 (조달청 39~40쪽):
#:
#:     증감률(%)  합계 -4.7  vs 각 기관 증감률의 합 35.1
#:     활용률(%)  합계 89.9  vs 각 기관 활용률의 합 766.5
#:
#: 열 머리로 가른다 — 값만 보고는 "합이 아닌 열" 과 "합이 틀린 열" 을
#: 구분할 수 없기 때문이다 (그것이 바로 검산기가 답해야 할 물음이다).
_RATIO_HEADER = __import__("re").compile(
    r"(%|％|비율|비중|율\s*$|률\s*$|율\s*\(|률\s*\(|증감률|증가율|달성률|점유율)")

#: 소계 행 딱지 — 이것이 하나라도 있으면 층이 섞인 표라 검사하지 않는다.
_SUBTOTAL_ROW = re.compile(r"소\s*계\s*$")


def parse_number(text: str) -> float | None:
    """셀 텍스트를 수로 읽는다.

    입력: text — 셀 텍스트
    출력: 수. 수가 아니면 None
    비고:
        `△1,234` `(56.7)` `▲8` 은 음수다 — 정부 문서 표기 관행.
        글자가 섞이면(단위·주석) 수로 보지 않는다. 억지로 읽으면 오탐이다.
    """
    if text is None:
        return None
    # HWPX 셀에는 굵게 표식(`**`)이 그대로 들어온다 — 수 판정 전에 벗긴다.
    stripped = str(text).replace("*", "").strip()
    negative = False
    if stripped.startswith(("△", "▲")):
        negative, stripped = True, stripped[1:]
    elif stripped.startswith("(") and stripped.endswith(")"):
        negative, stripped = True, stripped[1:-1]
    stripped = stripped.replace(",", "").replace(" ", "")
    if not _NUMBER.match(stripped):
        return None
    value = float(stripped)
    return -value if negative else value


def _anchor_grid(cells: list[dict]) -> dict[tuple[int, int], str]:
    """셀 목록을 (row, col) → text 로.

    입력: cells — {"row","col","text"} 목록
    출력: 자리 → 텍스트
    """
    out: dict[tuple[int, int], str] = {}
    for cell in cells or []:
        try:
            out[(int(cell["row"]), int(cell["col"]))] = str(cell.get("text", ""))
        except (KeyError, TypeError, ValueError):  # noqa: PERF203
            continue
    return out


def _row_texts(grid: dict[tuple[int, int], str], row: int,
               max_col: int) -> list[str]:
    """한 행의 텍스트를 열 차례로.

    입력: grid — 자리→텍스트, row — 행, max_col — 마지막 열
    출력: 텍스트 목록 (빈 자리는 "")
    """
    return [(grid.get((row, col)) or "") for col in range(max_col + 1)]


def _label_of(texts: list[str]) -> str:
    """행의 딱지 — 앞쪽에서 처음 나오는 비지 않은 텍스트.

    입력: texts — 행 텍스트
    출력: 딱지 문자열 (굵게 표식 제거)
    """
    for text in texts:
        stripped = text.replace("*", "").strip()
        if stripped:
            return stripped
    return ""


def _ratio_columns(grid: dict[tuple[int, int], str],
                   max_row: int, max_col: int) -> set[int]:
    """비율 열 번호 — 머리행(앞 두 행)에서 찾는다.

    입력: grid — 자리→텍스트, max_row/max_col — 격자 크기
    출력: 검사에서 뺄 열 번호 집합
    """
    out: set[int] = set()
    for row in range(min(2, max_row + 1)):
        for col in range(max_col + 1):
            head = (grid.get((row, col)) or "").replace("*", "").strip()
            if head and _RATIO_HEADER.search(head):
                out.add(col)
    return out


def _looks_layered(grid: dict[tuple[int, int], str],
                   data_rows: list[int], max_col: int) -> bool:
    """자료 행 안에 **중간 집계 행**이 있는가 (계층표 판정).

    입력: grid — 자리→텍스트, data_rows — 자료 행 번호, max_col — 마지막 열
    출력: 계층으로 보이면 True
    비고:
        딱지(`소계`)가 없어도 계층인 표가 있다. 실측(문체부 12쪽 정원표):

            총계 3,049 = 본부 774 + 소속기관 2,275
            소속기관 2,275 = 한예종 276 + 국악고 94 + …

        `소속기관` 은 딱지가 없어 층 가드를 통과했고, 모든 자료 행을 더해
        이중 계산이 났다 — **정답(HWPX)에서도 같은 실패가 났다**는 것이
        구조 손상이 아니라 불변량 오적용이라는 증거다.

        판정: 어떤 자료 행의 값이 **그 아래 연속 행들의 합**과 같으면
        그 행은 중간 집계다. 수치가 스스로 계층을 증언한다 — 딱지 이름에
        기대지 않는다.
    """
    for index, row in enumerate(data_rows):
        below = data_rows[index + 1:]
        if len(below) < 2:
            continue
        for col in range(max_col + 1):
            value = parse_number(grid.get((row, col)))
            if value is None or value == 0:
                continue
            parts = [parse_number(grid.get((r, col))) for r in below]
            parts = [v for v in parts if v is not None]
            if len(parts) < 2:
                continue
            if abs(sum(parts) - value) <= max(1.0, abs(value) * 0.005):
                return True
    return False


def _check_total_row(grid: dict[tuple[int, int], str],
                     max_row: int, max_col: int) -> dict | None:
    """행 방향 총계 검산: `총계` 행의 각 열 = 나머지 행의 열별 합.

    입력: grid — 자리→텍스트, max_row/max_col — 격자 크기
    출력: {"checked", "failed", "failures"}. 대상 아니면 None
    비고:
        실측 꼴(행안부 성과계획서): 합계가 열이 아니라 **행**으로 있다 —
        `ㅇ 총계` `[일반회계] 합계`. 오탐을 막는 조건:

        · 총계꼴 행이 **정확히 하나** (여럿이면 층이 섞인 표다)
        · `소계` 행이 없다 (있으면 총계가 소계의 합일 수 있다)
        · 열마다: 총계 칸이 수 + 자료 행의 수 칸이 2개 이상 +
          수 아닌 비지 않은 칸이 없다 (글 열은 검사 밖)
    """
    total_rows = []
    for row in range(max_row + 1):
        label = _label_of(_row_texts(grid, row, max_col))
        if _SUBTOTAL_ROW.search(label):
            return None                          # 층이 섞였다
        if _TOTAL_ROW.search(label):
            total_rows.append(row)
    if len(total_rows) != 1:
        return None
    total_row = total_rows[0]

    def _has_number(row: int) -> bool:
        """행에 수 칸이 하나라도 있는가 — 없으면 머리행이다."""
        return any(parse_number(grid.get((row, col))) is not None
                   for col in range(max_col + 1))

    # 머리행(수가 하나도 없는 행)은 자료가 아니다. 총계가 맨 위에 오는
    # 표(`총계` 행 아래 내역)와 맨 아래 오는 표를 같은 규칙으로 다룬다.
    data_rows = [r for r in range(max_row + 1)
                 if r != total_row and _has_number(r)]
    # 비율 **행**도 뺀다. 0.3.80 에서 비율 열을 뺐는데 행 쪽이 남아
    # 있었다 — 실측(문체부 20쪽): `(전년대비증가율, %)` 행이 합에 섞여
    # 102,500 + (-38.7) + (-29.1) = 102,432.2 를 기대값으로 냈다.
    data_rows = [r for r in data_rows
                 if not _RATIO_HEADER.search(_label_of(_row_texts(grid, r, max_col)))]
    if len(data_rows) < 2:
        return None

    if _looks_layered(grid, data_rows, max_col):
        return None                              # 계층표 — 합이 겹친다

    def _label_col(row: int) -> int | None:
        """행 딱지가 있는 열 — 앞쪽 첫 비지 않은 칸."""
        for col in range(max_col + 1):
            if (grid.get((row, col)) or "").replace("*", "").strip():
                return col
        return None

    # **층이 섞인 표는 걸러낸다.** 실측(행안부 별첨1)에서 전략목표·
    # 프로그램목표·단위사업이 한 표에 있고 금액이 층마다 있어, 전부
    # 더하면 총계의 세 배가 나왔다 — 검산기 오탐이다. 층이 섞이면 행마다
    # 딱지 열이 달라진다(위층 0열 · 아래층 3열). 평평한 표만 검사한다.
    label_cols = {_label_col(r) for r in data_rows}
    if len(label_cols) != 1 or _label_col(total_row) not in label_cols:
        return None

    ratio_cols = _ratio_columns(grid, max_row, max_col)

    checked = failed = 0
    failures: list[dict] = []
    for col in range(max_col + 1):
        if col in ratio_cols:
            continue                             # 비율 열은 합의 대상이 아니다
        total = parse_number(grid.get((total_row, col)))
        if total is None:
            continue
        values = []
        clean = True
        for row in data_rows:
            text = (grid.get((row, col)) or "").strip()
            if not text or text == "〃":
                continue
            value = parse_number(text)
            if value is None:
                clean = False                    # 글이 섞인 열 — 검사 밖
                break
            values.append(value)
        if not clean or len(values) < 2:
            continue
        checked += 1
        expected = sum(values)
        if abs(expected - total) > _TOLERANCE:
            failed += 1
            if len(failures) < 3:
                failures.append({"col": col, "expected": round(expected, 2),
                                 "got": total})
    if checked == 0:
        return None
    return {"checked": checked, "failed": failed, "failures": failures}


def check_table(cells: list[dict]) -> dict | None:
    """표 하나를 검산한다.

    입력: cells — 셀 목록 (어느 파일 유형이든 같은 꼴)
    출력: {"checked": n, "failed": k, "failures": [...]}. 검사 대상이
          아니면 None
    비고:
        머리행은 0행으로 본다. 0행에서 못 찾으면 1행도 본다 — 제목이
        표 안 첫 행에 들어간 표가 실재한다.
    """
    grid = _anchor_grid(cells)
    if not grid:
        return None
    max_col = max(col for _, col in grid)
    max_row = max(row for row, _ in grid)

    for header_row in (0, 1):
        total_col = None
        year_cols: list[int] = []
        for col in range(max_col + 1):
            head = (grid.get((header_row, col)) or "").replace("*", "").strip()
            if _TOTAL_HEADER.match(head):
                total_col = col
            elif _YEAR_HEADER.match(head):
                year_cols.append(col)
        if total_col is not None and len(year_cols) >= 2:
            break
    else:
        # 열 방향 틀(연도별+합계)이 없으면 행 방향 총계를 본다 —
        # 성과계획서는 이쪽이 실측 꼴이다.
        return _check_total_row(grid, max_row, max_col)

    checked = failed = 0
    failures: list[dict] = []
    for row in range(header_row + 1, max_row + 1):
        total = parse_number(grid.get((row, total_col)))
        values = [parse_number(grid.get((row, col))) for col in year_cols]
        if total is None or any(v is None for v in values):
            continue                             # 수가 아닌 행은 검사 밖
        checked += 1
        expected = sum(values)                   # type: ignore[arg-type]
        if abs(expected - total) > _TOLERANCE:
            failed += 1
            if len(failures) < 3:
                failures.append({"row": row, "expected": round(expected, 2),
                                 "got": total})
    if checked == 0:
        return None
    return {"checked": checked, "failed": failed, "failures": failures}


def run(pages: list[PageContent], **kwargs) -> int:
    """합계 열이 있는 표를 검산한다.

    입력: pages — 페이지 목록 (제자리 갱신)
    출력: 불일치가 나온 표 수
    비고:
        파일 유형을 가리지 않는다 — 셀만 있으면 된다. `pdf_path` 도
        필요 없다 (지면이 아니라 **결과**를 재는 실험이다).
    """
    flagged = 0
    for page in pages:
        for table in (page.tables or []):
            report = check_table(table.cells or [])
            if report is None:
                continue
            table.sum_check = report
            if report["failed"]:
                flagged += 1
                page.trace.add(
                    "experiments.tsr.measure.sum_check", "합계 불일치",
                    f"{table.id} · 검사 {report['checked']}행 중 "
                    f"{report['failed']}행 어긋남 — 구조 손상(병합 손실로 "
                    f"값 밀림) 신호",
                    status="warn")
    return flagged


register(Experiment(
    key="sum_check",
    title="예산표 산술 검산",
    purpose="합계 열이 있는 표에서 행마다 연도열 합 = 합계 를 확인 (구조 손상 검증기)",
    origin="가설 H5 — TabStruct-Net V2 의 구조 제약을 학습 대신 검산식으로 옮김",
    run=run,
    formats=("hwpx", "pdf"),
    needs=("cells",),
    status="verified",
    note="판정이 아니라 **검증** 실험 — 파서·복원(⑦)·LLM·VLM 어느 경로 "
         "뒤에도 붙는다. 합이 어긋나면 병합 손실로 값이 밀렸다는 결정적 "
         "신호다. 오탐을 막으려고 조건을 좁게 잡았다(합계 열 + 연도열 "
         "2개↑ + 전부 수). HWPX(구조 원본)에서 오탐 0 을 확인한 뒤 행 "
         "방향 합계로 넓힌다.",
    knobs={"DOCSTRUCT_EXP_SUM_CHECK": "false 면 끔 (기본 켬 — 승격)"},
))
