"""OCR 이 잘못 읽은 곳을 문맥으로 짚는다.

역할:
    읽어 낸 글을 LLM 에게 보여 **이상한 곳만 지목**하게 한다.
호출부:
    docstruct.pipeline (`verify_ocr` 가 켜졌을 때, 스캔본만)
입력: PageContent 목록
출력: 없음 (page.ocr_doubts 채움)

왜 필요한가
--------
CTC 기반 OCR(rapidocr)은 **글자 모양만** 본다. 문맥을 모르므로 이런 일이
난다.

    원본: "기획재정부령으로 정하는 이자율"이란 연 1천분의 29를 말한다
    OCR:  "기획재정부령으로 정하는 이자율" 이란 연 2.9를 말한다

`1천분의` 를 놓치고 남은 숫자를 `2.9` 로 합쳤다. 값이 열 배 틀렸는데
**신뢰도로는 못 잡는다** — 각 획이 또렷해 점수가 높게 나온다.

이런 것은 문장으로 읽어야 안다. 법령체에서 이자율은 `1천분의 N` 꼴이라는
것을 아는 쪽이 판단해야 한다.

왜 고치지 않는가
-------------
**LLM 에게 고치라고 하면 지어낸다.** `연 2.9` 가 이상하다는 판단은 옳지만,
무엇이 맞는지는 지면을 봐야 안다 — `29` 일 수도 `2.9` 일 수도 있다.

그래서 여기서는 **어디가 이상한지만** 짚는다. 값을 정하는 것은 지면을 보는
쪽(VLM)의 몫이다. 좌표를 함께 남기므로 그 조각만 다시 읽으면 된다.

CTC + 언어모델 디코딩과 무엇이 다른가
--------------------------------
정석은 인식 모델의 **후보 분포**를 언어모델과 결합해 고르는 것이다. 그러면
원본에 없는 값이 나오지 않는다.

    rapidocr 은 후보 분포를 내지 않는다 — 최종 문자열과 평균 점수뿐이다.

그 정보가 없으니 LLM 은 처음부터 다시 추론하게 되고, 그것이 곧 환각 위험
이다. **지목만 시키는 이유가 여기에 있다.**
"""
from __future__ import annotations

import logging
import re

from docstruct.infrastructure.llm.client import invoke_llm, llm_api_config
from docstruct.infrastructure.llm.json_parse import parse_json_array
from docstruct.models import PageContent

_log = logging.getLogger(__name__)

#: 한 번에 보낼 쪽 수. 많이 묶을수록 호출이 준다.
PAGES_PER_CALL = 20

#: 한 번에 보낼 최대 글자 수. 넘으면 쪽 수를 줄인다.
MAX_CHARS = 18_000

#: 한 조각으로 볼 최소 글자 수. 너무 짧으면 판단할 근거가 없다.
#:
#: 8자로 두었더니 제목이 빠졌다 — 지면은 `주택취득자금에 / 대한 확인` 두
#: 줄인데 첫 줄만 읽혀 `주택취득자금에`(7자)가 남았다. 그것이 잘렸다는 것을
#: 알려면 조각으로 들어가야 한다.
MIN_FRAGMENT_CHARS = 5

#: 머리말·바닥글은 보내지 않는다. 쪽마다 같은 것이 반복돼 조각만 늘린다.
_BOILERPLATE_RE = re.compile(
    r"https?://"
    r"|^\d{1,2}\s*[./]\s*\d{1,2}\s*[./]\s*\d{1,2}"      # 26.5.11.
    r"|^\d+\s*/\s*\d+$"                                  # 63/380
    r"|^(?:오전|오후)"
)

_PROMPT = """\
아래는 스캔 문서를 OCR 로 읽은 결과입니다. **이상한 곳만 짚으세요.**

## 고치지 마세요

무엇이 맞는지는 지면을 봐야 압니다. 추측해서 고치면 없던 값을 만들게 됩니다.
여기서는 **어디가 이상한지**만 알려 주세요. 그 자리는 나중에 지면을 보고
다시 읽습니다.

## 무엇이 이상한가

- **수치 표기가 문서 유형에 안 맞음**
  법령·행정 문서는 `1천분의 29`·`100분의 5` 꼴을 씁니다. `연 2.9` 처럼
  나오면 `1천분의` 가 빠졌을 수 있습니다.
- **문장이 문법에 안 맞음**
  조사가 어긋나거나, 서술어가 없거나, 앞뒤가 이어지지 않습니다.
- **단위가 어긋남**
  금액에 `%`, 비율에 `원` 처럼 맞지 않는 단위가 붙습니다.
- **낱말이 깨짐**
  `농어촌특별세는국세이지만` 처럼 붙거나, `대 한 확 인` 처럼 벌어집니다.

## 무엇이 이상하지 않은가

- **띄어쓰기가 없는 것만으로는** 짚지 마세요. 스캔 문서에서 흔합니다.
- 표 안의 짧은 값(`신규`·`-`·숫자만)은 원래 그렇습니다.
- 한자·영문이 섞인 것은 원문일 수 있습니다.
- **법령 인용은 이 꼴이 정상입니다.** 기호와 숫자가 제대로 있으면 짚지
  마세요 — 원문이 그렇게 씁니다.

      지방법§11①8      지특법§36의3     지방법§111의2
      소득령§154①      농특세법§5①6     종부법§16③

  `§` 가 **영문 `S` 나 다른 글자로 바뀐 것**만 짚습니다.

      지방법S11        농특법S5①6       종부법S16③   ← 이것이 이상합니다

## 응답 (JSON 배열만, 다른 텍스트 없음)

이상한 곳이 없으면 빈 배열 `[]` 을 내세요.

[
  {{"page": 147, "index": 12, "text": "이란 연 2.9를 말한다",
    "reason": "법령체에서 이자율은 `1천분의 N` 꼴이며 `연 2.9` 는 어긋남"}}
]

- "page"  : 쪽 번호
- "index" : 아래 목록의 조각 번호
- "text"  : 이상한 부분 (짧게)
- "reason": 왜 이상한지

## OCR 결과

{content}
"""


def _fragments(pages: list[PageContent]) -> list[tuple[int, int, str]]:
    """쪽별로 조각을 번호와 함께 낸다.

    입력: pages — 페이지 목록
    출력: (쪽 번호, 조각 번호, 글) 목록
    비고:
        본문은 줄 단위, **표는 칸 단위**로 자른다. 표 한 줄을 통째로 보내면
        LLM 이 어느 칸이 이상한지 짚기 어렵다.

        좌표를 함께 남기려면 OCR 조각 단위가 나은데, 지금 구조에서는 본문이
        이미 합쳐진 문자열이라 줄이 최소 단위다.
    """
    out: list[tuple[int, int, str]] = []
    for page in pages:
        if not isinstance(page.page_no, int):
            continue
        index = 0
        for line in (page.content or "").splitlines():
            text = line.strip()
            if len(text) < MIN_FRAGMENT_CHARS:
                continue
            if _BOILERPLATE_RE.search(text):
                continue
            out.append((page.page_no, index, text))
            index += 1

        # **표 안도 봐야 한다.** 본문에는 `<table N>` 자리표시자만 남으므로,
        # 표를 빼면 표 안의 오독을 통째로 놓친다 — 실측에서 `연 1천분의 29`
        # 가 `연 2.9` 로 읽힌 자리가 표 안이었다.
        #
        # 다만 **재추출된 표는 뺀다.** 지면을 보고 다시 쓴 것이라 텍스트만
        # 보는 검증이 더 나을 수 없고, 조각만 늘려 비용이 든다.
        for table in page.tables:
            if getattr(table, "source", "parser") != "parser":
                continue
            for line in (table.markdown or "").splitlines():
                text = line.strip()
                if not text.startswith("|"):
                    continue
                if not (set(text) - set("|-: ")):
                    continue                  # 구분선
                # 칸을 나눠 보낸다. 한 줄이 길면 LLM 이 어느 칸인지 못 짚는다.
                for cell in text.strip("|").split("|"):
                    value = cell.strip().strip("*")
                    if len(value) < MIN_FRAGMENT_CHARS:
                        continue
                    out.append((page.page_no, index, value))
                    index += 1
    return out


def _batches(
    fragments: list[tuple[int, int, str]],
) -> list[list[tuple[int, int, str]]]:
    """보낼 묶음으로 나눈다.

    입력: fragments — 조각 목록
    출력: 묶음 목록
    비고:
        쪽 수와 글자 수 **둘 다** 본다. 표가 많은 쪽은 20쪽만 모아도
        한도를 넘는다.
    """
    out: list[list[tuple[int, int, str]]] = []
    current: list[tuple[int, int, str]] = []
    chars = 0
    pages: set[int] = set()
    for item in fragments:
        page_no, _, text = item
        if current and (chars + len(text) > MAX_CHARS
                        or (page_no not in pages
                            and len(pages) >= PAGES_PER_CALL)):
            out.append(current)
            current, chars, pages = [], 0, set()
        current.append(item)
        chars += len(text)
        pages.add(page_no)
    if current:
        out.append(current)
    return out


def _render(batch: list[tuple[int, int, str]]) -> str:
    """묶음을 프롬프트에 넣을 꼴로.

    입력: batch — 조각 목록
    출력: 여러 줄 문자열
    """
    return "\n".join(f"[{page}쪽] {index}: {text}"
                     for page, index, text in batch)


def find_doubts(pages: list[PageContent]) -> int:
    """이상한 곳을 찾아 표시한다.

    입력: pages — 페이지 목록 (제자리 갱신)
    출력: 짚어 낸 곳의 수
    비고:
        LLM 이 없으면 아무것도 하지 않는다. 실패해도 본문은 그대로 둔다 —
        **읽어 낸 글을 건드리지 않는 것이 이 단계의 원칙이다.**
    """
    if llm_api_config() is None:
        _log.info("LLM 이 없어 OCR 검증을 건너뜁니다")
        return 0

    fragments = _fragments(pages)
    if not fragments:
        return 0

    by_page = {page.page_no: page for page in pages
               if isinstance(page.page_no, int)}
    batches = _batches(fragments)
    _log.info("OCR 검증 %d묶음 (조각 %d개)", len(batches), len(fragments))

    lookup = {(p, i): t for p, i, t in fragments}
    found = 0
    for batch in batches:
        try:
            reply = invoke_llm(
                _PROMPT.format(content=_render(batch)),
                span_name="ocr_verify",
            )
        except Exception as exc:                 # noqa: BLE001 - 검증 실패는 치명적이지 않다
            _log.warning("OCR 검증 실패: %s", exc)
            continue
        for item in parse_json_array(reply) or []:
            page_no = item.get("page")
            index = item.get("index")
            page = by_page.get(page_no)
            if page is None:
                continue
            doubt = {
                "index": index,
                "text": (item.get("text") or "").strip()[:120],
                "reason": (item.get("reason") or "").strip()[:200],
                "source_text": lookup.get((page_no, index), "")[:200],
            }
            page.ocr_doubts = (page.ocr_doubts or []) + [doubt]
            found += 1
    if found:
        _log.info("OCR 이 의심스러운 곳 %d군데", found)
    return found


def doubt_pages(pages: list[PageContent]) -> list[int]:
    """의심스러운 곳이 있는 쪽 번호.

    입력: pages — 페이지 목록
    출력: 쪽 번호 목록
    비고:
        VLM 으로 다시 읽을 대상을 고를 때 쓴다. 전면 재판독은 비싸므로
        이 목록만 태운다.
    """
    return [page.page_no for page in pages
            if page.ocr_doubts and isinstance(page.page_no, int)]
