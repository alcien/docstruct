"""한국 문서 텍스트 정규화 — 균등배분 복원, 한컴 PUA 매핑.

입력:
    문자열

역할:
    한국 공문서에서 반복해서 나타나는 두 가지 텍스트 훼손을 되돌린다.

    · **균등배분**: 제목을 칸에 맞추려고 글자를 벌려 쓴 것.
      `대 한 민 국 정 부` → `대한민국정부`

    · **세로쓰기**: 표 셀에 제목을 세로로 배치해 낱글자 줄이 이어진 것.
      `프
로
그
램` → `프로그램`

    · **한컴 PUA**: 글머리표·기호를 사용자 정의 영역(U+F020~U+F0FF,
      U+F0000대)에 저장한 것. 매핑 없이 내보내면 빈 네모로 깨진다.

호출부:
    docstruct.converters.hwp.olefile, docstruct.extractors.hwp,
    docstruct.converters.pdf (텍스트 후처리)
출력:
    정규화된 문자열

출처
----
알고리즘과 PUA 매핑표는 kordoc(MIT, chrisryugj)을 참조했다. PUA 표의
원 출처는 rhwp(MIT, edwardkim)의 한컴 PDF 시각 검증 테이블이다.
"""
from __future__ import annotations

import re

#: BMP Symbol 영역(U+F020~U+F0FF) 매핑. 키는 (코드 - 0xF000).
#: 한컴이 Symbol/Wingdings 계열 기호를 이 영역에 저장한다.
_BMP_SYMBOL_MAP: dict[int, str] = {
    # 도형
    0x6C: "●", 0x6D: "●", 0x6E: "■", 0x6F: "□",
    0x70: "□", 0x71: "□", 0x72: "□",
    0x73: "⬧", 0x74: "⧫", 0x75: "◆", 0x76: "❖", 0x77: "⬥",
    # 점·별
    0x9E: "·", 0x9F: "•", 0xA0: "·",
    0xA1: "⚪", 0xA2: "○", 0xA3: "○", 0xA4: "◉", 0xA5: "◎",
    0xA7: "▪", 0xA8: "◻",
    0xAA: "✦", 0xAB: "★", 0xAC: "✶", 0xAD: "✴", 0xAE: "✹",
    # 손 모양
    0x45: "☜", 0x46: "☞", 0x47: "☝", 0x48: "☟",
    # 체크
    0xFB: "✗", 0xFC: "✔", 0xFD: "☒", 0xFE: "☑",
    # 화살표
    0xE8: "➔", 0xEF: "⇦", 0xF0: "⇨", 0xF1: "⇧", 0xF2: "⇩",
    # 기타
    0x20: " ",   # PUA 공백 → 일반 공백 (기호 런 폴백에서도 안전)
    0x22: "✂", 0x36: "⌛", 0x4A: "☺", 0x4E: "☠",
    0x52: "☼", 0x54: "❄", 0x58: "✠", 0x59: "✡",
}

#: Supplementary PUA-A (U+F0000대) — 한컴 자체 영역.
_SUPPLEMENTARY_MAP: dict[int, str] = {
    0xF003B: "↓", 0xF02EF: "·", 0xF0854: "《", 0xF0855: "》",
    0xF00DA: "▸", 0xF080F: "━", 0xF0827: "■", 0xF03C5: "□",
}

#: 부분 균등배분 — 1글자가 3개 이상 연속으로 벌어진 구간.
#: 앞뒤가 글자면 매칭하지 않아 `중동 사태 대응` 같은 2자 단어를 건드리지 않는다.
_PARTIAL_EVEN = re.compile(r"(?<![가-힣\d])[가-힣\d](?: [가-힣\d]){2,}(?![가-힣\d])")

#: 전체 균등배분으로 볼 최소 토큰 수.
_MIN_TOKENS = 3

#: 세로쓰기로 볼 최소 연속 줄 수. 표 셀에 글자를 세로로 한 자씩 배치한
#: 경우가 많아, PDF·HWP 어디서든 낱글자 줄이 이어져 나온다.
_MIN_VERTICAL_LINES = 3

#: 세로쓰기 한 줄로 인정할 문자 — 한글·숫자만. 기호나 라틴 문자는
#: 목록 기호·머리글자일 수 있어 제외한다.
_VERTICAL_CHAR = re.compile(r"^[가-힣0-9]$")


#: ASCII 규약 PUA 런을 복원할 때 허용하는 문자 (코드 − 0xF000 결과).
#: 실측(과기부 성과보고서 583쪽)에서 인쇄 쪽번호 바닥글 `- 16 -` 이
#: `U+F02D U+F020 U+F031 U+F036 U+F020 U+F02D` 로 들어 있었다 — 일부 PDF
#: 폰트는 Symbol 기호가 아니라 **ASCII 를 그대로 0xF000 대에** 싣는다.
#: 숫자·구두점만 허용해 좁게 잡는다. 넓히면(영문 허용) `●●`(F06C F06C)이
#: `ll` 로 둔갑한다.
_ASCII_PUA_SAFE = frozenset("-0123456789 .,()/%:")


def map_pua(text: str) -> str:
    """한컴 PUA 문자를 표준 유니코드로 바꾼다.

    입력: text — 원본 문자열
    출력: 매핑된 문자열
    비고:
        매핑에 없는 BMP PUA 는 **그대로 둔다.** 옛한글(한양 PUA)일 수
        있어 지우면 글자가 사라진다. 지금까지 PUA 를 통째로 제거하면서
        글머리표가 함께 없어지고 있었다.

        같은 코드포인트를 문서마다 다르게 쓴다 — 한컴 Symbol 규약은
        F036 이 ⌛ 인데, ASCII 규약 PDF 는 숫자 `6` 이다. **연속한 PUA
        런을 통째로 보고** 가른다: 런 전체가 숫자·구두점으로 복원되면
        ASCII 규약이고, 아니면 기호 규약이다. 낱개로 온 F036 은 ⌛ 를
        유지한다 (숫자라면 바닥글처럼 이웃 PUA 와 함께 온다).
    """
    if not text:
        return text
    out: list[str] = []
    i, length = 0, len(text)
    while i < length:
        code = ord(text[i])
        if 0xF020 <= code <= 0xF0FF:
            j = i
            while j < length and 0xF020 <= ord(text[j]) <= 0xF0FF:
                j += 1
            run = text[i:j]
            decoded = "".join(chr(ord(c) - 0xF000) for c in run)
            ascii_run = all(c in _ASCII_PUA_SAFE for c in decoded)
            # 낱개는 기호 매핑과 겹치지 않을 때만 ASCII 로 본다 —
            # F02D(-)·F030~F039 는 기호 맵에 없어 안전하고, F036 낱개는
            # 기호(⌛) 로 남는다.
            if ascii_run and (len(run) >= 2
                              or (ord(run[0]) - 0xF000) not in _BMP_SYMBOL_MAP):
                out.append(decoded)
            else:
                out.extend(_BMP_SYMBOL_MAP.get(ord(c) - 0xF000, c) for c in run)
            i = j
            continue
        if 0xF0000 <= code <= 0xF09FF:
            out.append(_SUPPLEMENTARY_MAP.get(code, text[i]))
        else:
            out.append(text[i])
        i += 1
    return "".join(out)


def collapse_even_spacing(text: str) -> str:
    """균등배분으로 벌어진 글자를 되붙인다.

    입력: text — 한 줄
    출력: 되붙인 문자열
    비고:
        두 단계로 본다.

        1. **전체 균등배분** — 모든 토큰이 1글자면 줄 전체를 붙인다.
           `대 한 민 국 정 부` → `대한민국정부`
        2. **부분 균등배분** — 1글자가 3개 이상 연속인 구간만 붙인다.
           `홍 보 담 당 관 회의 자료` → `홍보담당관 회의 자료`

        2자 단어는 건드리지 않는다. `중동 사태 대응` 을 균등배분으로 보면
        `중동사태대응` 이 되어 정상 문장이 망가진다 — kordoc 이 실제로
        겪은 실패라 같은 기준을 쓴다.
    """
    if not text or " " not in text:
        return text

    tokens = [t for t in text.split(" ") if t]
    # 전체 균등배분: **모든** 토큰이 1글자일 때만. 비율 기준(예: 70%)을 쓰면
    # `홍 보 담 당 관 회의 자료` 처럼 뒤에 정상 단어가 붙은 줄까지 통째로
    # 붙여 버린다. 그런 줄은 아래 부분 규칙이 앞부분만 정확히 처리한다.
    if len(tokens) >= _MIN_TOKENS and all(len(t) == 1 for t in tokens):
        return "".join(tokens)

    return _PARTIAL_EVEN.sub(lambda m: m.group(0).replace(" ", ""), text)


def collapse_vertical_text(text: str) -> str:
    """세로쓰기로 한 글자씩 나뉜 줄을 한 줄로 되붙인다.

    입력: text — 여러 줄 문자열
    출력: 되붙인 문자열
    비고:
        표 셀에 제목을 세로로 배치한 경우 PDF 텍스트 레이어와 HWP 문단이
        모두 낱글자 줄로 나온다.

            프                프로그램논리모형
            로       →
            그
            램 …

        3줄 이상 연속일 때만 붙인다. 표 안의 한 글자 값(`○`, `1`)이 우연히
        이어지는 것과 구분하기 위해 한글·숫자만 대상으로 한다.
    """
    if not text:
        return text
    lines = text.split("\n")
    out: list[str] = []
    run: list[str] = []

    def flush() -> None:
        """모아둔 낱글자 줄을 처리한다.

        입력: 없음 (둘러싼 run·out 사용)
        출력: 없음 — 연속 낱글자가 기준 이상이면 한 줄로 합치고, 아니면 원래대로
        """
        if len(run) >= _MIN_VERTICAL_LINES:
            out.append("".join(run))
        else:
            out.extend(run)
        run.clear()

    for line in lines:
        if _VERTICAL_CHAR.match(line.strip()) and line.strip() == line.strip():
            run.append(line.strip())
        else:
            flush()
            out.append(line)
    flush()
    return "\n".join(out)


#: 앞에 공백이 오면 안 되는 문자 — 닫는 괄호·따옴표와 종결 부호.
#: `입법 , 예` → `입법, 예`
_NO_SPACE_BEFORE = r",.;:!?)\]}»〉》」』】〕｣）］｝"

#: 뒤에 공백이 오면 안 되는 문자 — 여는 괄호·따옴표.
#: `｢ 헌법 ｣` → `｢헌법｣`
_NO_SPACE_AFTER = r"([{«〈《「『【〔｢（［｛"

#: 양옆 공백을 없앨 가운뎃점류. `예 · 결산` → `예·결산`
_TIGHT_MIDDOT = r"·‧・"

_SPACE_BEFORE_RE = re.compile(rf"[ \t]+(?=[{_NO_SPACE_BEFORE}])")

#: 소괄호 **앞** 의 공백. 한국어 표기에서 `1급 (2명)` 은 `1급(2명)` 이다.
#: 0.1.95 는 괄호 뒤만 다뤄 이쪽이 남았다 — 실측 396건.
#:
#: **소괄호만** 다룬다. 인용부호(`｢ ｣`)까지 넣었더니 `및 ｢국회법｣` 의
#: 낱말 사이 공백을 지웠다 — 그 자리는 원문에 공백이 있는 것이 맞다.
_SPACE_BEFORE_OPEN_RE = re.compile(r"(?<=[\w가-힣])[ \t]+(?=[(（])")

#: 숫자와 뒤따르는 한글 사이 공백. `1 급`·`2 명`·`169 명` 처럼 벌어진다.
#: 좌표로 단어를 재조립하는 쪽이 숫자와 글자의 자간을 공백으로 읽는다.
#: **한 글자 단위·조사만** 붙인다. `2027 년도` 는 맞지만 `5 개년 계획` 의
#: `개년` 까지 붙이면 다른 말이 될 수 있어 보수적으로 둔다.
_NUM_UNIT_RE = re.compile(r"(?<=\d)[ \t]+(?=[급명개년월일차호원건배쪽점])")
_SPACE_AFTER_RE = re.compile(rf"(?<=[{_NO_SPACE_AFTER}])[ \t]+")
#: 가운뎃점 양옆 공백. 양옆 글자를 소비하면 `가 · 나 · 다` 처럼 연달아
#: 나올 때 겹쳐서 하나를 건너뛴다. lookahead 로 소비하지 않고 확인만 한다.
_MIDDOT_RE = re.compile(rf"[ \t]*([{_TIGHT_MIDDOT}])[ \t]*(?=\S)")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")


def tighten_punctuation(text: str) -> str:
    """구두점·괄호 주위에 잘못 끼어든 공백을 없앤다.

    입력: text — 한 줄 문자열
    출력: 공백이 정리된 문자열
    비고:
        PDF 텍스트 레이어에는 글자마다 좌표만 있고 단어 경계가 없다.
        한국어 조판은 구두점 앞뒤 자간이 넓어, 좌표로 단어를 재조립하는
        쪽(docling 등)이 그 틈을 공백으로 읽는다. 같은 문서에서

            PDF  국민의 대의기관으로 입법 , 예 · 결산 심사 , 국정감 · 조사 등 의
            HWP  국민의 대의기관으로 입법, 예·결산 심사, 국정감·조사 등의

        처럼 갈렸고, 이런 자리가 527군데였다.

        **원문에 있던 공백은 건드리지 않는다.** 구두점 바로 앞, 여는 괄호
        바로 뒤, 가운뎃점 양옆 — 한국어 표기에서 공백이 올 수 없는 자리만
        좁힌다. `등 의` 처럼 일반 글자 사이 공백은 판단 근거가 없어 그대로
        둔다 — 붙여야 할지 아닌지는 문맥을 봐야 알 수 있다.
    """
    if not text:
        return text
    # 줄 맨 앞의 가운뎃점은 글머리표다(`· 항목`). 뒤 공백을 지우면
    # 본문에 붙어 버리므로 그 부분만 떼어 두고 나중에 되돌린다.
    lead = ""
    body = text
    marker = re.match(rf"\s*[{_TIGHT_MIDDOT}][ \t]+", text)
    if marker:
        lead = marker.group(0)
        body = text[marker.end():]

    out = _SPACE_BEFORE_RE.sub("", body)
    out = _SPACE_BEFORE_OPEN_RE.sub("", out)
    out = _NUM_UNIT_RE.sub("", out)
    out = _SPACE_AFTER_RE.sub("", out)
    out = _MIDDOT_RE.sub(r"\1", out)
    return lead + _MULTI_SPACE_RE.sub(" ", out)


#: 같은 낱말이 몇 번 반복돼야 중복으로 볼지.
_MIN_REPEAT = 3

#: 값으로 보는 토큰 — **숫자로 시작**하는 것 (앞의 부호·괄호는 허용).
#: `60` 뿐 아니라 `9개`·`10.5%이상` 처럼 단위·접미가 붙은 값도 포함한다.
#: 실측(과기부 성과보고서)에서 `입학(명) 20 60 60 60 60 60 60 380`,
#: `선정 신규 10개 - 9개 9개 9개`, `목표치 … 10.5%이상 ×3` 처럼
#: **연차별 동일 목표치가 정당하게 반복**되는 줄이 24쪽 있었고, 반복
#: 정리가 이를 지워 숫자가 사라졌다. 그림자 효과 반복은 제목(한글로
#: 시작)에서 나오므로, 숫자로 시작하는 토큰의 반복은 손대지 않는다.
_VALUE_TOKEN = re.compile(r"^[△▲()\[\]\-–~+]*\d")


def collapse_repeated_words(text: str) -> str:
    """같은 낱말이나 구절이 연달아 반복되면 하나로 줄인다.

    입력: text — 한 줄 문자열
    출력: 반복이 정리된 문자열
    비고:
        제목에 그림자·테두리 효과를 준 지면에서 나온다. 같은 글자가 조금씩
        어긋난 위치에 여러 번 그려져 있고, 텍스트 레이어에는 그것이 전부
        들어 있다. 실제로 이런 줄들이 나왔다.

            별첨3 별첨3 별첨3
            성과계획 목표체계 성과계획 목표체계 성과계획 목표체계 제1장 제1장 제1장

        낱말 하나뿐 아니라 **여러 낱말로 된 구절**도 반복되므로, 길이 1부터
        차례로 늘려 가며 본다.

        **세 번 이상**부터 줄인다. 두 번은 `국가 국가` 처럼 실제로 그렇게
        쓰인 경우가 있어 구분할 수 없다.
    """
    if not text:
        return text
    tokens = text.split()
    if len(tokens) < _MIN_REPEAT:
        return text

    out: list[str] = []
    index = 0
    while index < len(tokens):
        best = 1                             # 이 자리에서 건너뛸 토큰 수
        # 긴 구절을 먼저 잡아야 `A B A B A B` 가 `A B` 로 줄어든다.
        # 낱말 단위로 먼저 보면 반복을 못 알아본다.
        for size in range((len(tokens) - index) // _MIN_REPEAT, 0, -1):
            unit = tokens[index:index + size]
            # 값 반복은 데이터다 — 지우면 표의 수치가 사라진다.
            if all(_VALUE_TOKEN.match(t) for t in unit):
                continue
            repeat = 1
            while (tokens[index + repeat * size: index + (repeat + 1) * size] == unit):
                repeat += 1
            if repeat >= _MIN_REPEAT:
                out.extend(unit)
                best = repeat * size
                break
        if best == 1:
            out.append(tokens[index])
        index += best
    return " ".join(out)


def normalize_pdf_text(text: str) -> str:
    """PDF 텍스트 레이어 특유의 손상까지 함께 정리한다.

    입력: text — 페이지에서 뽑은 문자열
    출력: 정규화된 문자열
    비고:
        `normalize_korean_text` 에 구두점 공백·낱말 반복 정리를 더한 것이다.
        **PDF 경로에서만** 쓴다. HWP·HWPX 는 바이너리에서 글자를 직접 읽어
        이런 손상이 없고(같은 문서에서 527건 대 0건), 정상 텍스트에 규칙을
        더 걸면 고칠 것 없이 위험만 는다.
    """
    normalized = normalize_korean_text(text)
    lines = (collapse_repeated_words(tighten_punctuation(line))
             for line in normalized.split("\n"))
    return "\n".join(lines)


#: 한글 누름틀(필드) 상태가 직렬화돼 지면까지 새어 나온 잔재.
#: 예: `{"fields": {},"simplefields": {}}` — 화면에는 안 보이지만 PDF
#: 텍스트 레이어에는 남아, 표 머리 칸에 딸려 들어간다.
#: 실측(조달청 p75 별첨7): 머리 첫 칸이
#: `{"fields": {},"simplefields": {}} 프로 그램 (코드 번호)` 로 나왔다.
#: HWPX 경로는 `hwpxtree._is_field_payload` 로 걸렀지만 PDF 경로는
#: 거르는 곳이 없었다 — 여기서 함께 뗀다(모든 형식이 지나는 자리다).
_FIELD_PAYLOAD = re.compile(r'\{\s*"fields"\s*:.*?"simplefields"\s*:.*?\}\s*\}')


def strip_field_payload(text: str) -> str:
    """누름틀 잔재를 뗀다.

    입력: text — 원본 문자열
    출력: 잔재를 뺀 문자열 (앞뒤 공백 정리)
    """
    if not text or '"simplefields"' not in text:
        return text
    return _FIELD_PAYLOAD.sub("", text).strip()


def normalize_korean_text(text: str, *, collapse: bool = True) -> str:
    """PUA 매핑과 균등배분 복원을 함께 적용한다.

    입력:
        text      원본 문자열 (여러 줄 가능)
        collapse  균등배분 복원 여부. 표 셀처럼 짧은 조각에는 끄는 편이 안전
    출력: 정규화된 문자열
    비고: 균등배분은 **줄 단위**로 판단한다. 전체를 한 덩어리로 보면
          긴 문서에서 1글자 비율이 희석돼 감지되지 않는다.
    """
    mapped = strip_field_payload(map_pua(text))
    if not collapse:
        return mapped
    joined = collapse_vertical_text(mapped)
    return "\n".join(collapse_even_spacing(line) for line in joined.split("\n"))
