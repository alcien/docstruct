"""깨진 ToUnicode 매핑 탐지 — 글머리표가 엉뚱한 글자로 나올 때.

역할:
    HWP 에서 내보낸 PDF 는 글머리표(□ ○ ※ ▪)를 심볼 폰트의 글리프로 담는데,
    그 폰트의 ToUnicode CMap 이 엉뚱한 코드포인트를 가리키는 경우가 많다.
    그러면 텍스트 레이어에서 `□` 대신 `숿` 같은 **정상 한글 음절**이 나온다.
    PUA(U+F020~) 가 아니므로 converters.korean_text.map_pua 가 잡지 못한다.

    이 모듈은 고치지 않는다. **어떤 코드포인트가 의심스러운지 증거를 모아**
    보여 줄 뿐이다. 매핑표는 문서마다 다를 수 있어 추측으로 넣으면 멀쩡한
    글자를 망친다 — 실제 파일에서 확인한 뒤 넣어야 한다.
호출부:
    docstruct.converters.pdf.glyph_probe.report (CLI: python -m 으로 직접)
출력:
    의심 코드포인트 목록과 그 근거 (빈도·줄머리 비율·주변 문맥)

판별 근거
--------
글머리표가 깨진 자리는 다음을 **동시에** 만족한다.

    · 홀글자   앞뒤가 공백인 한 글자
    · 줄머리   줄 맨 앞(들여쓰기 제외)에 온다
    · 반복     문서 전체에서 여러 번 되풀이된다
    · 무의미   그 음절이 실제 단어로 쓰이는 자리가 없다

한 조건만으로는 오탐이 난다. `가 나 다` 처럼 열거하는 본문도 홀글자이고,
`시 도 군` 같은 행정단위도 줄머리에 온다. 네 가지가 겹칠 때만 의심한다.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

#: 이미 아는 글머리표. 이것들이 나오면 그 페이지의 텍스트 레이어는 멀쩡하다.
KNOWN_BULLETS = "□■◆○●◎※▪▫◦·∙-–—*▸▷➔→"

#: 의심으로 올릴 최소 등장 횟수. 한두 번은 우연일 수 있다.
MIN_OCCURRENCES = 3

#: 의심으로 올릴 최소 줄머리 비율. 글머리표는 거의 항상 줄 맨 앞에 온다.
MIN_LINE_HEAD_RATIO = 0.6

#: 한글 음절 영역.
_HANGUL = re.compile(r"[가-힣]")


@dataclass
class Suspect:
    """깨진 글머리표로 의심되는 문자 하나.

    입력(필드):
        char        해당 문자
        codepoint   유니코드 코드포인트
        total       문서 전체 등장 횟수
        line_head   줄머리에서 등장한 횟수
        samples     주변 문맥 예시
    출력(파생):
        line_head_ratio  줄머리 비율
    """

    char: str
    codepoint: int
    total: int = 0
    line_head: int = 0
    samples: list[str] = field(default_factory=list)

    @property
    def line_head_ratio(self) -> float:
        """줄머리에서 나온 비율.

        입력: 없음
        출력: 0~1. 등장이 없으면 0.0
        """
        return self.line_head / self.total if self.total else 0.0

    def describe(self) -> str:
        """사람이 읽는 한 줄 요약.

        입력: 없음
        출력: `숿 U+C23F  47회 · 줄머리 96% · HANGUL SYLLABLE …` 형태
        """
        name = unicodedata.name(self.char, "(이름 없음)")
        return (
            f"{self.char} U+{self.codepoint:04X}  {self.total}회 · "
            f"줄머리 {self.line_head_ratio:.0%} · {name}"
        )


def find_suspects(text: str) -> list[Suspect]:
    """텍스트에서 깨진 글머리표 후보를 찾는다.

    입력: text — PDF 텍스트 레이어에서 뽑은 문자열 (여러 줄)
    출력: 의심도가 높은 순으로 정렬된 Suspect 목록
    동작:
        홀글자 한글 음절을 세되, 줄머리 비율과 반복 횟수가 기준을 넘는
        것만 남긴다. 실제 단어 안에서도 쓰이는 음절은 제외한다 —
        `그 는 말했다` 의 `그` 를 글머리표로 오인하지 않기 위함이다.
    """
    lone: dict[str, Suspect] = {}
    in_word: Counter[str] = Counter()

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        tokens = stripped.split()
        for index, token in enumerate(tokens):
            if len(token) == 1 and _HANGUL.fullmatch(token):
                s = lone.setdefault(token, Suspect(token, ord(token)))
                s.total += 1
                if index == 0:
                    s.line_head += 1
                if len(s.samples) < 3:
                    s.samples.append(stripped[:60])
            else:
                # 두 글자 이상 토큰 안에 쓰인 음절은 실제 글자다.
                for ch in token:
                    if _HANGUL.fullmatch(ch):
                        in_word[ch] += 1

    suspects = [
        s for s in lone.values()
        if s.total >= MIN_OCCURRENCES
        and s.line_head_ratio >= MIN_LINE_HEAD_RATIO
        # 본문 단어 안에서 자주 쓰이는 음절이면 글머리표가 아니다.
        and in_word[s.char] <= s.total
    ]
    suspects.sort(key=lambda s: (-s.line_head_ratio, -s.total))
    return suspects


def has_known_bullets(text: str) -> bool:
    """이미 정상적인 글머리표가 들어 있는지.

    입력: text — 문자열
    출력: KNOWN_BULLETS 중 하나라도 있으면 True
    비고: True 면 텍스트 레이어가 멀쩡하다는 뜻이므로 의심 결과는 오탐이다.
    """
    return any(b in text for b in KNOWN_BULLETS)


def probe_pdf(pdf_path: str | Path, *, pages: int = 5) -> tuple[str, list[Suspect]]:
    """PDF 텍스트 레이어를 직접 읽어 의심 문자를 찾는다.

    입력: pdf_path — PDF 경로, pages — 앞에서 몇 쪽까지 볼지
    출력: (읽은 텍스트, Suspect 목록)
    예외: pypdfium2 미설치 시 ImportError
    비고:
        docling 을 거치지 않고 원본 텍스트 레이어를 그대로 본다. 중간
        가공이 끼면 원인이 폰트인지 우리 코드인지 가려지지 않기 때문이다.
    """
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        chunks: list[str] = []
        for index in range(min(pages, len(doc))):
            page = doc[index]
            textpage = page.get_textpage()
            chunks.append(textpage.get_text_range())
        text = "\n".join(chunks)
    finally:
        doc.close()
    return text, find_suspects(text)


def report(pdf_path: str | Path, *, pages: int = 5) -> None:
    """진단 결과를 사람이 읽게 출력한다.

    입력: pdf_path — PDF 경로, pages — 앞에서 몇 쪽까지 볼지
    출력: 없음 (stdout)
    """
    text, suspects = probe_pdf(pdf_path, pages=pages)

    print(f"검사: {pdf_path}  (앞 {pages}쪽 · {len(text):,}자)")

    if has_known_bullets(text):
        found = sorted({b for b in KNOWN_BULLETS if b in text})
        print(f"  정상 글머리표 발견: {' '.join(found)}")
        print("  → 텍스트 레이어는 멀쩡합니다. 아래 결과는 오탐일 수 있습니다.")

    pua = sorted({ch for ch in text if 0xE000 <= ord(ch) <= 0xF8FF})
    if pua:
        codes = ", ".join(f"U+{ord(c):04X}" for c in pua[:10])
        print(f"  PUA 문자 {len(pua)}종: {codes}")
        print("  → 이쪽은 converters.korean_text.map_pua 가 처리합니다.")

    if not suspects:
        print("  의심 문자 없음.")
        return

    print(f"\n  깨진 글머리표 의심 {len(suspects)}종:")
    for s in suspects:
        print(f"    {s.describe()}")
        for sample in s.samples:
            print(f"        | {sample}")

    print(
        "\n  판단은 사람이 하세요. 위 문자가 원문에서 □ ○ ※ 자리에 해당하면\n"
        "  폰트 ToUnicode 매핑이 깨진 것입니다. 대응은 두 가지입니다.\n"
        "    ① force_full_page_ocr=True  — 텍스트 레이어를 버리고 전면 OCR\n"
        "    ② 확인된 매핑을 코드에 등록 — 같은 양식 문서를 반복 처리할 때\n"
        "  ②는 이 진단으로 코드포인트를 확정한 뒤에만 하세요. 추측으로 넣으면\n"
        "  멀쩡한 한글이 기호로 바뀝니다."
    )


def main() -> None:
    """CLI 진입점.

    입력: 없음 (argv: <pdf경로> [쪽수=5])
    출력: 없음 (stdout)
    """
    import sys

    if len(sys.argv) < 2:
        print("사용법: python -m docstruct.converters.pdf.glyph_probe <pdf경로> [쪽수]")
        raise SystemExit(1)
    pages = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    report(sys.argv[1], pages=pages)


if __name__ == "__main__":
    main()
