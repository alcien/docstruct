"""한국 문서 텍스트 정규화 — 균등배분 복원, 한컴 PUA 매핑.

역할:
    한국 공문서에서 반복해서 나타나는 두 가지 텍스트 훼손을 되돌린다.

    · **균등배분**: 제목을 칸에 맞추려고 글자를 벌려 쓴 것.
      `대 한 민 국 정 부` → `대한민국정부`

    · **세로쓰기**: 표 셀에 제목을 세로로 배치해 낱글자 줄이 이어진 것.
      `프\n로\n그\n램` → `프로그램`

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


def map_pua(text: str) -> str:
    """한컴 PUA 문자를 표준 유니코드로 바꾼다.

    입력: text — 원본 문자열
    출력: 매핑된 문자열
    비고:
        매핑에 없는 BMP PUA 는 **그대로 둔다.** 옛한글(한양 PUA)일 수
        있어 지우면 글자가 사라진다. 지금까지 PUA 를 통째로 제거하면서
        글머리표가 함께 없어지고 있었다.
    """
    if not text:
        return text
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if 0xF020 <= code <= 0xF0FF:
            out.append(_BMP_SYMBOL_MAP.get(code - 0xF000, ch))
        elif 0xF0000 <= code <= 0xF09FF:
            out.append(_SUPPLEMENTARY_MAP.get(code, ch))
        else:
            out.append(ch)
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


def normalize_korean_text(text: str, *, collapse: bool = True) -> str:
    """PUA 매핑과 균등배분 복원을 함께 적용한다.

    입력:
        text      원본 문자열 (여러 줄 가능)
        collapse  균등배분 복원 여부. 표 셀처럼 짧은 조각에는 끄는 편이 안전
    출력: 정규화된 문자열
    비고: 균등배분은 **줄 단위**로 판단한다. 전체를 한 덩어리로 보면
          긴 문서에서 1글자 비율이 희석돼 감지되지 않는다.
    """
    mapped = map_pua(text)
    if not collapse:
        return mapped
    joined = collapse_vertical_text(mapped)
    return "\n".join(collapse_even_spacing(line) for line in joined.split("\n"))
