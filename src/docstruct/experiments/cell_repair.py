"""실험 ⑦ — 한 칸에 뭉친 값을 되돌린다.

무엇을 보완하는가
--------------
셀 경계가 무너져 **두 값이 한 칸에** 들어가는 일이 있다. 실측(행안부
성과계획서 320표)에서 16건이었다.

    | 자원봉사활성화지원 | 50771 ①자원봉사 만족도(점) | ...
                          ↑ 사업코드    ↑ 지표명이 한 칸에

    | 1,150,0 00 |     ← 금액이 갈림
    | 14, 63 |         ← 두 값인지 한 값인지 애매

어떻게 되돌리는가
--------------
처음에는 열 수 다수결로 잡으려 했다. **틀렸다** — 실측에서 표 전체가
일관되게 뭉쳐 있었다.

    | 자원봉사활성화지원 | 50771 ①자원봉사 만족도(점) | ...   12열
    | 비영리민간단체지원 | 42364 ①비영리민간단체 공익활동…  | ...   12열
    | 맞춤형 새마을운동   | 51024 ②개도국 새마을교육…      | ...   12열

한 행만 어긋났다면 다수결이 통하지만, 모든 행이 같은 모양이면 무엇이
정상인지 알 수 없다.

**그래서 칸 안의 모양으로 판단한다.** 사업코드 뒤에 지표명이 붙은 것은
그 자체로 근거가 된다 — 다섯 자리 숫자와 `①` 로 시작하는 이름은 원래
다른 칸의 값이다.

    50771 ①자원봉사 만족도(점)
    ↑ 코드  ↑ 지표명

**같은 열의 다른 칸이 같은 모양이면** 확신이 커진다. 열 전체가 그렇다면
그 열은 원래 둘이었다.

무엇을 되돌리지 않는가
------------------
`적 및 목표치 구분` 처럼 **글자 사이가 벌어진 것은 손대지 않는다.** 원본이
좁은 칸에 맞추려 자간을 벌린 것이지 손상이 아니다 — 실측 35건 중 18건이
그 경우였고, 고치려 들면 멀쩡한 표를 망친다.
"""
from __future__ import annotations

import logging
import re
from collections import Counter

from docstruct.experiments.registry import Experiment, register
from docstruct.models import PageContent

_log = logging.getLogger(__name__)

#: 열을 가를 때 필요한 최소 행 수. 이보다 적으면 무엇이 정상인지 알 수 없다.
MIN_ROWS = 4

#: 갈린 숫자를 붙일 때 필요한 최소 행 수.
#: **열을 늘리지 않으므로 위험이 작다.** 자릿수로 검증되니 근거도 칸 안에
#: 있다 — 실측(행안부)에서 3행짜리 예산표가 다수였다.
MIN_NUMBER_ROWS = 2

#: 다수가 이 비율을 넘어야 기준으로 삼는다.
MIN_MAJORITY = 0.6

#: 사업코드처럼 보이는 숫자 — 네 자리 이상.
_CODE_RE = re.compile(r"^(\d{4,})\s+(\S.*)$")

#: 세 자리씩 끊긴 금액. `1,150,000` 은 맞고 `1,150,0` 은 아니다.
_AMOUNT_RE = re.compile(r"^\d{1,3}(?:,\d{3})*$")

#: 공백으로 갈린 숫자. `1,150,0 00` · `4199 0` 처럼.
_SPLIT_NUMBER_RE = re.compile(r"^([\d,]+)\s+([\d,]+)$")

#: 표식이 든 칸의 최대 글자 수. 서술문을 걸러낸다.
MAX_MARKER_CHARS = 20

#: 두 행이 한 행으로 뭉친 표식. 성과지표 표에서 `목표` 와 `실적` 이 각각
#: 한 행인데 파서가 한 행으로 합쳐 놓는 일이 있다.
#:
#:     | 1.0 목표 실적 | 100 100 | 100 100 |
#:            ↑ 두 행이 뭉침
#:
#: 이 표식이 한 칸에 함께 있으면 그 행은 원래 둘이었다.
_ROW_MARKERS = (("목표", "실적"), ("계획", "실적"))


def split_candidates(cell: str) -> list[tuple[str, str]]:
    """이 칸을 어디서 가를 수 있는가.

    입력: cell — 셀 내용
    출력: (앞, 뒤) 후보 목록. 없으면 빈 목록
    비고:
        **자를 근거가 뚜렷한 것만 낸다.** 아무 공백에서나 자르면 정상 문구를
        망친다.

        근거는 둘이다.
          · 네 자리 이상 숫자 뒤에 글자가 오면 사업코드 + 이름
          · 갈린 숫자를 붙였을 때 금액 형식이 되면 원래 한 값
    """
    text = cell.strip()
    if not text:
        return []

    out: list[tuple[str, str]] = []

    # 사업코드 + 이름
    code = _CODE_RE.match(text)
    if code:
        out.append((code.group(1), code.group(2)))

    # 갈린 금액 — 붙였을 때 세 자리 규칙에 맞으면 한 값이었다
    parts = text.split()
    if len(parts) == 2 and all(re.fullmatch(r"[\d,]+", p) for p in parts):
        joined = "".join(parts)
        if _AMOUNT_RE.fullmatch(joined):
            out.append((joined, ""))       # 뒤가 비면 "합친다" 는 뜻
    return out


def join_split_number(cell: str) -> str | None:
    """갈린 숫자를 붙인다.

    입력: cell — 셀 내용
    출력: 붙인 값. 붙일 근거가 없으면 None
    비고:
        **자릿수로 검증한다.** 붙였을 때 세 자리 규칙에 맞거나, 쉼표 없는
        정수가 되면 원래 한 값이었다고 본다.

            '1,150,0 00'  →  '1,150,000'   세 자리 규칙 ✓
            '4199 0'      →  '41990'       쉼표 없는 정수 ✓
            '14, 63'      →  None          어느 쪽도 아님

        마지막 경우는 두 값이 붙은 것인지 한 값이 갈린 것인지 **여기서
        판단하지 않는다.** 구조화 단계가 열 의미를 알고 정할 문제다.
    """
    found = _SPLIT_NUMBER_RE.match(cell.strip())
    if not found:
        return None
    left, right = found.group(1), found.group(2)

    # **양쪽이 다 온전한 수면 두 값이다.** `100 100` 을 `100100` 으로 붙이면
    # 값을 망친다 — 성과지표 표에서 목표값과 실적값이 나란히 있는 모양이다.
    if _AMOUNT_RE.fullmatch(left) and _AMOUNT_RE.fullmatch(right):
        return None

    joined = left + right
    if _AMOUNT_RE.fullmatch(joined):
        return joined                      # 1,150,0 + 00 → 1,150,000
    if joined.isdigit() and len(joined) >= 3:
        return joined                      # 4199 + 0 → 41990
    return None


def _number_column(rows: list[str]) -> list[int]:
    """갈린 숫자가 반복되는 열.

    입력: rows — 표 본문 행 목록
    출력: 열 번호 목록
    비고:
        **한 칸만 그렇다면 손대지 않는다.** 같은 열의 다른 칸이 온전한
        숫자여야 그 열이 수치 열이라는 근거가 된다.
    """
    grids = [[c.strip() for c in r.strip("|").split("|")] for r in rows]
    if not grids:
        return []
    width = Counter(len(g) for g in grids).most_common(1)[0][0]
    same = [g for g in grids if len(g) == width]

    out: list[int] = []
    for column in range(width):
        values = [g[column] for g in same if g[column]]
        if not values:
            continue
        broken = sum(1 for v in values if join_split_number(v))
        whole = sum(1 for v in values if _AMOUNT_RE.fullmatch(v) or v.isdigit())
        # 갈린 것이 있고, 그 열이 수치 열이어야 한다
        if broken and broken + whole >= len(values) * MIN_MAJORITY:
            out.append(column)
    return out


def find_merged_rows(cells: list[str]) -> int | None:
    """두 행이 뭉친 자리를 찾는다.

    입력: cells — 한 행의 셀 목록
    출력: 표식이 든 열 번호. 없으면 None
    비고:
        성과지표 표는 `목표` 와 `실적` 이 각각 한 행이다. 그 둘이 한 칸에
        있으면 파서가 두 행을 합친 것이다 — 그러면 옆 칸의 값들도 두 개씩
        붙어 있다.

            | 1.0 목표 실적 | 100 100 | 100 100 |
                              ↑ 목표값 실적값

        **표식이 근거다.** 값만 보고는 두 개인지 한 개가 갈린 것인지 알 수
        없다.
    """
    for index, cell in enumerate(cells):
        text = cell.strip()
        # **칸이 짧아야 한다.** 서술문에 `목표`·`실적` 이 함께 나오는 일이
        # 흔하다 — 실측에서 `목표달성률=(시도 목표달성 지표 수…` 같은 설명이
        # 26표나 걸렸다. 표식 칸은 `1.0 목표 실적` 처럼 짧다.
        if len(text) > MAX_MARKER_CHARS:
            continue
        for first, second in _ROW_MARKERS:
            if first in text and second in text:
                return index
    return None


def split_merged_row(row: str) -> tuple[str, str] | None:
    """뭉친 행을 두 행으로 되돌린다.

    입력: row — markdown 행
    출력: (앞 행, 뒤 행). 되돌리지 못하면 None
    비고:
        표식이 든 칸을 앞뒤로 가르고, 값이 둘씩 붙은 칸도 함께 가른다.
        값이 하나뿐인 칸은 **앞 행에 두고 뒤 행은 비운다** — 병합 셀이라
        원래 위쪽에만 있었을 수 있다.
    """
    cells = [c.strip() for c in row.strip("|").split("|")]
    marker = find_merged_rows(cells)
    if marker is None:
        return None

    head: list[str] = []
    tail: list[str] = []
    for index, cell in enumerate(cells):
        if index == marker:
            for first, second in _ROW_MARKERS:
                if first in cell and second in cell:
                    before, _, after = cell.partition(second)
                    head.append(before.strip())
                    tail.append((second + after).strip())
                    break
            continue
        parts = cell.split()
        if len(parts) == 2 and all(re.fullmatch(r"[\d,.\-]+", p) for p in parts):
            head.append(parts[0])
            tail.append(parts[1])
        else:
            head.append(cell)
            tail.append("")
    if not any(tail):
        return None
    return ("| " + " | ".join(head) + " |",
            "| " + " | ".join(tail) + " |")


def _rows_of(markdown: str) -> list[str]:
    """표 본문 행만.

    입력: markdown — 표 문자열
    출력: 구분선을 뺀 행 목록
    """
    return [
        line for line in (markdown or "").splitlines()
        if line.startswith("|") and set(line.strip()) - set("|-: ")
    ]


def _split_column(rows: list[str]) -> int | None:
    """어느 열이 통째로 뭉쳤는가.

    입력: rows — 표 본문 행 목록
    출력: 열 번호. 없으면 None
    비고:
        **같은 열의 여러 칸이 같은 모양이어야** 근거가 된다. 한 칸만
        그렇다면 원래 그런 값일 수 있다.
    """
    if len(rows) < MIN_ROWS:
        return None
    grids = [[c.strip() for c in r.strip("|").split("|")] for r in rows]
    width = Counter(len(g) for g in grids).most_common(1)[0][0]
    same = [g for g in grids if len(g) == width]
    if len(same) < MIN_ROWS:
        return None

    for column in range(width):
        values = [g[column] for g in same if g[column]]
        if len(values) < MIN_ROWS:
            continue
        hits = sum(1 for v in values if _CODE_RE.match(v))
        if hits >= len(values) * MIN_MAJORITY:
            return column
    return None


def repair_table(markdown: str, *, row_merge: bool = False) -> tuple[str, int]:
    """표에서 뭉친 값을 되돌린다.

    입력:
        markdown   표 문자열
        row_merge  두 행이 뭉친 것도 되돌릴지 (③ 이 짚은 표에서만 켠다)
    출력: (고친 markdown, 고친 행 수)
    비고:
        **열 하나가 통째로 뭉친 경우만** 다룬다. 그 열을 둘로 가른다.
        구분선(`|---|`)도 함께 늘려야 markdown 이 깨지지 않는다.
    """
    rows = _rows_of(markdown)
    if len(rows) < MIN_NUMBER_ROWS:
        return markdown, 0

    # ① 갈린 숫자를 붙인다 — 열을 늘리지 않으므로 먼저 한다.
    number_columns = _number_column(rows)
    lines = (markdown or "").splitlines()
    joined_count = 0
    if number_columns:
        for position, line in enumerate(lines):
            if not line.startswith("|") or not (set(line.strip()) - set("|-: ")):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            changed = False
            for column in number_columns:
                if column >= len(cells):
                    continue
                joined = join_split_number(cells[column])
                if joined:
                    cells[column] = joined
                    changed = True
            if changed:
                lines[position] = "| " + " | ".join(cells) + " |"
                joined_count += 1
        markdown = "\n".join(lines)
        rows = _rows_of(markdown)

    # ② 두 행이 뭉친 것을 되돌린다 — 행이 늘어난다.
    #
    # ③(two_way_match)이 짚은 표에서 자주 나온다. `목표`/`실적` 표식이
    # 근거이며, 값만 보고는 두 개인지 한 개가 갈린 것인지 알 수 없다.
    lines = (markdown or "").splitlines()
    rebuilt: list[str] = []
    row_splits = 0
    for line in lines if row_merge else []:
        if line.startswith("|") and (set(line.strip()) - set("|-: ")):
            pair = split_merged_row(line)
            if pair:
                rebuilt.extend(pair)
                row_splits += 1
                continue
        rebuilt.append(line)
    if row_splits:
        markdown = "\n".join(rebuilt + lines[len(rebuilt) - row_splits:][row_splits:])
        markdown = "\n".join(rebuilt)
        rows = _rows_of(markdown)
        joined_count += row_splits

    # ③ 코드+이름이 뭉친 열을 가른다 — 열이 하나 늘어난다.
    column = _split_column(rows)
    if column is None:
        return (markdown, joined_count) if joined_count else (markdown, 0)

    lines = (markdown or "").splitlines()
    fixed_count = 0
    for position, line in enumerate(lines):
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if column >= len(cells):
            continue
        # 구분선(`|---|---|`)은 칸만 하나 늘린다.
        # **빈 행(`| | | |`)과 구분해야 한다** — 둘 다 글자가 없지만
        # 빈 행은 데이터이고 구분선은 서식이다.
        if all(c in ("", "---", ":---", "---:", ":---:") for c in cells):
            if any(c for c in cells):        # 대시가 있으면 구분선
                cells.insert(column, "---")
                lines[position] = "|" + "|".join(cells) + "|"
            else:                            # 빈 행이면 칸만 늘린다
                cells.insert(column, "")
                lines[position] = "| " + " | ".join(cells) + " |"
            continue
        found = _CODE_RE.match(cells[column])
        if found:
            cells[column:column + 1] = [found.group(1), found.group(2)]
            fixed_count += 1
        else:
            cells.insert(column + 1, "")     # 열 수를 맞춘다
        lines[position] = "| " + " | ".join(cells) + " |"
    total = fixed_count + joined_count
    return ("\n".join(lines), total) if fixed_count else (markdown, joined_count)


def run(pages: list[PageContent], **_kwargs) -> int:
    """뭉친 값을 되돌린다.

    입력: pages — 페이지 목록 (제자리 갱신)
    출력: 고친 표 수
    비고:
        **원본을 `original_markdown` 에 남긴다.** 되돌릴 수 있어야 한다.
    """
    fixed = 0
    for page in pages:
        for table in page.tables:
            before = table.markdown or ""
            # **행 분리는 ③ 이 짚은 표에서만 한다.** 표식(`목표`/`실적`)만
            # 으로는 근거가 약해 서술문까지 걸린다 — ③ 이 "이 표의 배정이
            # 어긋났다" 를 알려줄 때만 손댄다.
            after, count = repair_table(
                before, row_merge=bool(table.match_disagreements))
            if not count:
                continue
            if table.original_markdown is None:
                table.original_markdown = before
            table.markdown = after
            table.cell_repairs = count
            fixed += 1
            page.trace.add(
                "experiments.cell_repair", "뭉친 값 분리",
                f"{table.id} · {count}행", status="warn")
    return fixed


register(Experiment(
    key="cell_repair",
    title="한 칸에 뭉친 값을 되돌림",
    purpose="셀 경계가 무너져 두 값이 한 칸에 들어간 것",
    origin="열 수 다수결 — 계보 밖",
    formats=("pdf", "hwp", "hwpx"),
    status="proposed",
    note="**표 내용을 바꾸는 유일한 실험이다.** 두 가지를 되돌린다 — "
         "갈린 금액(`1,150,0 00` → `1,150,000`)과 코드+이름 뭉침"
         "(`50771 ①지표` → 두 칸). 원본은 `original_markdown` 에 남는다. "
         "글자 사이가 벌어진 것(`적 및 목표치`)은 손대지 않는다 — 원본 조판이다. "
         "두 행이 뭉친 것(`1.0 목표 실적`)은 ③ 이 짚은 표에서만 되돌린다. "
         "실측(행안부 320표): 22표 · 48행 · 숫자 손실 0.",
    run=run,
))
