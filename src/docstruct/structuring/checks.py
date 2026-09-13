"""구조화 후 불변량 재검산 — ⑧ 재실행 + 개수 검산 (구조화 §5-5, §19-4).

입력:
    cells
출력:
    grid_check(결함) · leak_check(오염) · repair_leaks(복원)

역할:
    전개·이어붙임이 끝난 표를 다시 잰다. 구조화 **후** 다시 재는 이유:
    전개가 새 오류를 만들 수 있고, 이어붙임은 쪽 단위 검산으로는 못
    보던 전체 합을 드러낸다.
호출부:
    structuring.structure_document
설정:
    없음 — 순수 함수다.

증가율 불변량 (0.3.79)
------------------
"실적" 과 "전년 대비 증가율" 이 함께 적힌 표는 스스로를 검산한다:

    증가율(t) = (실적(t) - 실적(t-1)) / 실적(t-1) × 100

조달청 성과계획서 54쪽 실측: 적힌 77.8/31.9/7.1% ↔ 계산 77.87/31.93/7.09%
— 0.07%p 안에서 맞물린다. 남의 그래프를 표로 옮겨 적다 생긴 오타는 이
관계를 깨뜨린다 (9,755 → 7,955 자리바꿈 시 두 해가 동시에 어긋난다).

개수 불변량 (§19-4)
-----------------
국세청류 표의 "X수" 열은 X 의 개수를 적는다:

    단위사업수 8  ↔  같은 묶음 안의 서로 다른 "단위사업" 값 8개

묶음(scope)은 **X수 열 바로 왼쪽 열의 같은 값 연속 구간**이다 — 지면에서
프로그램명 병합이 덮던 구간이 전개 후 "왼쪽 열 값이 같은 연속 행" 으로
남기 때문이다. 쪽 넘김 조각(continues_from 사슬)은 개수가 잘려 보이므로
**이어붙임 후에만** 이 검산을 돌린다 — 호출부 계약이다.
"""
from __future__ import annotations

import re

from docstruct.experiments.tsr.measure.sum_check import check_table, parse_number


def recheck_sums(cells: list[dict]) -> dict | None:
    """⑧을 구조화 산출물에서 재실행한다.

    입력: cells — (이어붙임까지 끝난) 셀 목록
    출력: check_table 과 같음 ({"checked","failed","failures"}) 또는 None
    """
    return check_table(cells)


def count_check(records: list[dict], names: list[str]) -> dict | None:
    """"X수" 열의 값 = 묶음 안 서로 다른 X 값 수 — 개수 불변량.

    입력: records — expand_merges 산출, names — column_names 산출
    출력: {"checked": n, "failed": n, "failures": [...]}. 대상 없으면 None
    비고:
        짝(X, X수)이 **둘 다 실재하는 열**만 검사한다 — 이름 규칙만으로
        묶음을 지어내지 않기 위한 오탐 0 방향이다.
    """
    pairs = [(name[:-1], name) for name in names
             if name.endswith("수") and len(name) > 1 and name[:-1] in names]
    if not pairs or not records:
        return None

    checked = failed = 0
    failures: list[str] = []
    for base, counter in pairs:
        scope_col = names[names.index(counter) - 1] if names.index(counter) else None
        if scope_col is None:
            continue
        # 왼쪽 열 같은 값 연속 구간으로 묶는다
        start = 0
        while start < len(records):
            end = start
            scope_value = records[start].get(scope_col, "")
            while (end + 1 < len(records)
                   and records[end + 1].get(scope_col, "") == scope_value):
                end += 1
            group = records[start:end + 1]
            start = end + 1
            declared = {parse_number(r.get(counter, "")) for r in group}
            declared.discard(None)
            if len(declared) != 1 or not scope_value:
                continue                          # 선언이 없거나 흔들리면 대상 아님
            (number,) = declared
            distinct = {r.get(base, "") for r in group if r.get(base, "")}
            checked += 1
            if len(distinct) != int(number):
                failed += 1
                failures.append(
                    f"{scope_col}={scope_value}: {counter} {int(number)} 인데 "
                    f"{base} 값 {len(distinct)}종")
    if not checked:
        return None
    return {"checked": checked, "failed": failed, "failures": failures}


#: 증가율 표기 자체의 반올림 여유(%p). 소수 한 자리 표기면 ±0.05 이다.
RATE_ROUNDING_PP = 0.05
#: 문턱의 하한(%p). 표기 정밀도에서 유도한 값이 이보다 작아도 여기서 멈춘다.
RATE_MIN_TOLERANCE_PP = 0.15


def _half_unit(text_value: float) -> float:
    """적힌 값의 반올림 반폭 — 정수면 0.5, 소수 한 자리면 0.05.

    입력: text_value — 값
    출력: 반폭
    비고:
        문턱을 **표기 정밀도에서 유도**하는 이유: 고정 문턱은 정수로 적힌
        표(오차가 거의 없다)에서 너무 헐겁고, 조 단위 소수 한 자리 표
        (반올림이 크다)에서는 너무 빡빡하다. 같은 규칙이 두 표를 다 받는다.
    """
    text = f"{text_value:.10f}".rstrip("0")
    if "." not in text:
        return 0.5
    decimals = len(text.split(".")[1])
    return 0.5 * (10 ** -decimals) if decimals else 0.5


def growth_check(values: list[float], rates: list[float | None]) -> dict | None:
    """실적 계열과 적힌 증가율이 맞물리는지 본다.

    입력: values — 연도순 실적, rates — 같은 순서의 증가율(%). 첫 항목은
          전년 값이 없으므로 None 이어도 된다
    출력: {"checked","failed","failures":[…]}. 잴 짝이 없으면 None
    비고:
        표 **안에서** 닫히는 검산이라 그래프도 원본도 필요 없다. 끝자리
        오타처럼 기하 대조(media.chart_verify)가 못 잡는 작은 오류를
        여기서 잡는다 — 두 층이 서로의 사각을 메운다.
    """
    if not values or len(values) != len(rates):
        return None
    checked = failed = 0
    failures: list[str] = []
    for index in range(1, len(values)):
        said = rates[index]
        previous, current = values[index - 1], values[index]
        if said is None or not previous:
            continue
        calculated = (current - previous) / previous * 100
        # 두 값의 반올림이 증가율에 얼마나 번지는지 계산해 문턱을 정한다.
        spread = (_half_unit(current) / abs(previous)
                  + abs(current) * _half_unit(previous) / previous ** 2) * 100
        tolerance = max(RATE_MIN_TOLERANCE_PP, spread + RATE_ROUNDING_PP)
        checked += 1
        if abs(calculated - said) > tolerance:
            failed += 1
            failures.append(
                f"{index}번째: 적힌 증가율 {said}% vs 계산 {calculated:.2f}% "
                f"(허용 {tolerance:.2f}%p)")
    if not checked:
        return None
    return {"checked": checked, "failed": failed, "failures": failures}


#: 격자 결함을 셀 수 대비 이 비율 넘게 가진 표는 "많이 깨졌다" 로 본다.
#: 실측(0.4.69): HWPX 653표는 결함이 **0개**이고, PDF 는 40~53% 의 표에
#: 결함이 있다. 비율은 그 안에서 정도를 가르기 위한 것이다.
HEAVY_FAULT_RATIO = 0.1


def grid_check(cells: list[dict] | None) -> dict | None:
    """셀이 격자를 빈틈없이 덮는지 — 표 자신의 내적 모순을 본다.

    입력: cells — 표의 셀 목록 (row·col·rowspan·colspan·text)
    출력: {"width","height","holes","overlaps","hole_ratio","ok"} · 못 재면 None
    비고:
        **정답이 없어도 확실한 신호다.** 표는 직사각 격자이므로 모든
        (행, 열) 자리가 정확히 한 셀에 덮여야 한다. 구멍이 있으면 셀을
        놓친 것이고, 겹침이 있으면 경계를 잘못 그은 것이다 — 어느 쪽이든
        **원본을 몰라도 틀렸다고 말할 수 있다.**

        실측(0.4.69)이 그것을 보여 준다.

            문체부 HWPX  653표  결함 **0개**       ← 음성 대조군
            조달청  PDF   58표  결함 23표 (40%)
            행안부  PDF  309표  결함 164표 (53%)
            문체부  PDF  471표  결함 217표 (46%)

        HWPX 는 셀 주소가 XML 에 적혀 있어 격자가 깨질 수 없다. 그 0% 가
        **이 검사가 멀쩡한 표에서 울지 않는다는 증거**다. PDF 쪽 40~53% 는
        인식이 열 구조를 놓친 비율이다.

        **고치지 않고 표시만 한다.** 구멍을 어느 셀로 메울지는 지면을
        봐야 알 수 있고(판독의 몫), 여기서 지어내면 없는 값을 만든다.
        하류(RAG)가 이 표의 열 이름-값 짝을 믿을지 정하는 데 쓰라고 남긴다.
    """
    if not cells:
        return None
    try:
        width = max(c.get("col", 0) + c.get("colspan", 1) for c in cells)
        height = max(c.get("row", 0) + c.get("rowspan", 1) for c in cells)
    except (TypeError, ValueError):
        return None
    if width < 2 or height < 1:
        # 1열짜리는 격자라 할 것이 없다 — 제목 띠다.
        return None

    seen: dict[tuple[int, int], int] = {}
    for cell in cells:
        row0 = cell.get("row", 0)
        col0 = cell.get("col", 0)
        for row in range(row0, row0 + cell.get("rowspan", 1)):
            for col in range(col0, col0 + cell.get("colspan", 1)):
                seen[(row, col)] = seen.get((row, col), 0) + 1

    total = width * height
    holes = sum(1 for row in range(height) for col in range(width)
                if (row, col) not in seen)
    overlaps = sum(1 for count in seen.values() if count > 1)
    return {
        "width": width,
        "height": height,
        # 덮이지 않은 자리 — 셀을 놓쳤다
        "holes": holes,
        # 두 번 덮인 자리 — 경계를 잘못 그었다
        "overlaps": overlaps,
        "hole_ratio": round(holes / total, 3) if total else 0.0,
        "ok": holes == 0 and overlaps == 0,
        # 조금 어긋난 것과 많이 깨진 것을 가른다 — 하류가 쓸 잣대다.
        "heavy": (holes + overlaps) / total > HEAVY_FAULT_RATIO if total else False,
    }


#: 이웃 칸으로 새어 든 글자를 셀 때, 다음 칸이 이 꼴이면 오염으로 본다.
#: `<한 글자><공백><글자>` — 앞 칸의 끝 글자가 딸려 온 모양이다.
_LEAK_HEAD = re.compile(r"^(\S)\s+(\S)")


#: 한 표에서 이만큼 이상 같은 모양이 나와야 "경계가 밀렸다" 고 본다.
#: 한 번은 우연일 수 있다 — 앞 칸 끝 글자와 다음 칸 첫 글자가 같은 것은
#: 드물지만 있을 수 있는 일이고, 그때 지우면 멀쩡한 글을 깎는다.
MIN_LEAK_RUNS = 2


def repair_leaks(cells: list[dict] | None) -> int:
    """이웃 칸으로 새어 든 글자를 **지운다**.

    입력: cells — 표의 셀 목록 (제자리 갱신)
    출력: 고친 칸 수
    비고:
        **은폐가 아니라 복원이다.** 새어 든 글자는 앞 칸에 **이미 있다** —
        `63,618` 은 그 자체로 온전하고, 다음 칸의 `8 66,578` 에서 앞의
        `8` 은 같은 글자가 두 번 적힌 것이다. 지우면 `66,578` 이 되어
        원래 값으로 돌아간다.

            앞 칸 `63,618`  다음 칸 `8 66,578`  →  `66,578`
            앞 칸 `(비중)`  다음 칸 `) 36.2`    →  `36.2`

        **한 표에서 두 번 이상 같은 모양일 때만 고친다.** 한 번은 우연일
        수 있고(앞 칸 끝 글자와 다음 칸 첫 글자가 같은 경우), 그때 지우면
        멀쩡한 글을 깎는다. 경계가 밀린 표는 행마다 같은 모양이 반복된다.
    """
    got = leak_check(cells)
    if got is None or got["leaks"] < MIN_LEAK_RUNS:
        return 0

    by_row: dict[int, list[dict]] = {}
    for cell in cells or []:
        by_row.setdefault(cell.get("row", 0), []).append(cell)

    fixed = 0
    for row in by_row.values():
        row = sorted(row, key=lambda c: c.get("col", 0))
        for left, right in zip(row, row[1:]):
            head = (left.get("text") or "").strip()
            tail = (right.get("text") or "").strip()
            if not head or len(tail) < 3:
                continue
            match = _LEAK_HEAD.match(tail)
            if match and match.group(1) == head[-1]:
                right["text"] = tail[match.end(1):].lstrip()
                fixed += 1
    return fixed


def cell_text_diff(before: dict[tuple, str],
                   cells: list[dict] | None) -> list[tuple[str, str]]:
    """`repair_leaks` 전후의 셀 텍스트 차이를 (옛값, 새값) 쌍으로 낸다.

    입력: before — {(row, col): 고치기 전 텍스트}, cells — 고친 뒤 셀 목록
    출력: 바뀐 셀의 (옛값, 새값) — 행·열 순서
    비고:
        `repair_leaks` 는 셀만 고친다. 표에는 **markdown 도 있다** — 그것을
        함께 고치려면 무엇이 무엇으로 바뀌었는지 알아야 하는데, 반환값이
        개수라 이 함수가 그 차이를 다시 잰다. 호출부가 고치기 전에
        `{(c["row"], c["col"]): c["text"]}` 를 찍어 두면 된다.
    """
    pairs: list[tuple[str, str]] = []
    for cell in sorted(cells or [], key=lambda c: (c.get("row", 0), c.get("col", 0))):
        key = (cell.get("row", 0), cell.get("col", 0))
        old = before.get(key)
        new = (cell.get("text") or "").strip()
        if old is not None and old != new:
            pairs.append((old, new))
    return pairs


def patch_markdown_cells(markdown: str | None,
                         pairs: list[tuple[str, str]]) -> tuple[str, int]:
    """셀 텍스트 교체를 markdown 표에도 반영한다.

    입력: markdown — 표 GFM, pairs — (옛 셀 텍스트, 새 셀 텍스트) 행·열 순
    출력: (고친 markdown, 반영한 칸 수)
    비고:
        **왜 필요한가 (0.4.83).** 0.4.80 의 `repair_leaks` 는 `cells` 만
        고쳤다. `TableInfo.markdown` 과 본문의 `<table N>` 블록은 오염된
        채 남아, trace 는 "셀 오염 복원" 이라 적고 `cells` 는 깨끗한데
        **document.md 와 JSON 의 markdown 에는 `8 66,578` 이 그대로
        나갔다.** 결과물의 두 필드가 다른 말을 하는 상태였다.

        통째로 다시 그리지 않는다 — 파서 표의 markdown 은 docling 렌더러
        (머리 전파·다단 머리 접기)가 만든 것이라 `cells_to_markdown` 으로
        새로 그리면 모양이 바뀐다. **칸 단위로 그 글자만 바꾼다.**

        칸은 `| ` 와 ` |` 사이에 있고 렌더러가 열 폭을 맞추느라 뒤에 공백을
        붙이므로, 옛 텍스트를 `| ` 뒤에서 찾고 뒤는 공백·`|` 이면 받는다.
        앞 칸부터 차례로 찾아 **한 번씩만** 바꾼다 — 같은 값이 여러 행에
        있어도 자리가 어긋나지 않는다. 열 폭은 옛 폭에 맞춰 공백을 채워
        정렬을 유지한다(GFM 파싱에는 영향 없다).

        머리 칸은 렌더러가 여러 행을 이어 붙여 그대로 찍히지 않을 수 있다.
        그런 칸은 못 찾아도 그냥 넘어가고 **반영한 칸 수를 돌려준다** —
        호출부가 `cells` 고침 수와 견줘 어긋남을 기록할 수 있게.
    """
    if not markdown or not pairs:
        return markdown or "", 0

    from docstruct.converters.common.table import display_width

    text = markdown
    cursor = 0
    patched = 0
    for old, new in pairs:
        if not old:
            continue
        old_md = old.replace("|", "\\|").replace("\n", " ")
        new_md = new.replace("|", "\\|").replace("\n", " ")
        pattern = re.compile(r"(?<=\| )" + re.escape(old_md) + r"(?=\s*\|)")
        match = pattern.search(text, cursor)
        if match is None:
            continue
        pad = max(0, display_width(old_md) - display_width(new_md))
        replacement = new_md + " " * pad
        text = text[:match.start()] + replacement + text[match.end():]
        cursor = match.start() + len(replacement)
        patched += 1
    return text, patched


def leak_check(cells: list[dict] | None) -> dict | None:
    """앞 칸의 끝 글자가 다음 칸 앞에 새어 들었는지 본다.

    입력: cells — 표의 셀 목록
    출력: {"leaks","samples"} · 없으면 None
    비고:
        **정답 없이 확인되는 신호다.** 지면을 몰라도, 다음 칸이
        `<한 글자><공백><…>` 꼴이고 그 글자가 **앞 칸의 마지막 글자와
        같으면** 경계가 밀린 것이다. 우연히 그럴 확률은 낮고, 표 하나에서
        여러 행이 같은 모양이면 확실하다.

        실측(0.4.79)이 이 검사를 요구했다. 격자로 다시 세운 표에서:

            해경  parser 60표 오염 0%   ·  lattice_fill 36표 오염 **33%**
            문체  parser 149표 오염 1%  ·  lattice_fill 215표 오염 **21%**

        실물:

            r1c2 '63,618'   r1c3 '8 66,578'   r1c4 '8 2,960'
            r2c2 ') 36.2'   r2c3 '2 40.4'     r2c4 '4 4.2'

        `63,618` 의 끝 `8` 이 다음 칸 앞에 붙었다. **격자 온전성으로는
        못 잡는다** — 자리는 다 덮였으므로 `ok: True` 다. 토큰 닮음으로도
        못 잡는다 — `8 66,578` 을 토큰화하면 `66,578` 이 그대로 나온다.

        보는 사람은 `63,618` 과 `8 66,578` 중 무엇이 값인지 알 수 없고,
        하류가 그것을 뽑으면 그냥 틀린 답이 나간다.
    """
    if not cells:
        return None
    by_row: dict[int, list[dict]] = {}
    for cell in cells:
        by_row.setdefault(cell.get("row", 0), []).append(cell)

    samples: list[str] = []
    leaks = 0
    for row in by_row.values():
        row = sorted(row, key=lambda c: c.get("col", 0))
        for left, right in zip(row, row[1:]):
            head = (left.get("text") or "").strip()
            tail = (right.get("text") or "").strip()
            if not head or len(tail) < 3:
                continue
            got = _LEAK_HEAD.match(tail)
            if got and got.group(1) == head[-1]:
                leaks += 1
                if len(samples) < 5:
                    samples.append(f"{head} → {tail}")
    if not leaks:
        return None
    return {"leaks": leaks, "samples": samples}
