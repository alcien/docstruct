"""텍스트 레이어가 없는 그림을 VLM 으로 읽는다.

입력:
    그림 + 지시문

역할:
    캡처 이미지로 붙인 표·조직도·흐름도는 PDF 안에 글자 좌표가 없다.
    좌표 기반 판정(`converters.pdf.region_kind`)이 IMAGE 로 가르는데,
    그렇다고 버릴 수는 없다 — 내용이 통째로 사라진다.

    저장해 둔 **그림 파일 자체**를 근거로 VLM 에 내용을 옮겨 달라고 한다.
    페이지 전체가 아니라 해당 그림만 보내므로 판독률이 높다.
호출부:
    docstruct.pipeline.build_document (표 판정 다음 단계)
출력:
    없음 (PageContent.content 와 ImageInfo.vlm_markdown 을 제자리 갱신)

그림 설명(picture description)과 다른 점
--------------------------------------
docling 의 그림 설명은 **한 문장 캡션**을 만든다("조직도를 나타낸 그림").
여기서는 **내용을 옮긴다** — 표면 GFM 표로, 도표면 계층 목록으로. 목적이
검색·인용이라 캡션으로는 쓸모가 없다.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from docstruct.core.config import get_settings
from docstruct.infrastructure.llm.client import invoke_llm, llm_api_config, llm_available
from docstruct.images.encode import encode_image_file
from docstruct.models import PageContent
from docstruct.core.progress import ProgressBar

_log = logging.getLogger(__name__)

#: 이 크기(페이지 면적 대비) 이상인 그림만 읽는다. 로고·아이콘까지 보내면
#: 호출 수가 급증하는데 얻는 것이 없다.
MIN_AREA_RATIO = 0.03

#: 응답이 이보다 짧으면 실패로 본다 ("표를 읽을 수 없습니다" 류).
MIN_RESULT_CHARS = 20
#: **이보다 작은 그림에는 길이 문턱을 걸지 않는다** (0.5.29).
#:
#: 20자는 큰 그림에서 모델이 얼버무린 답을 거르려고 둔 것이다. 그런데 절
#: 표지 배지는 `별첨2` **세 글자가 정답**이다 — 0.5.28 이 배지 넷을 VLM
#: 까지 보냈지만 전부 여기서 버려져 `빈 응답` 으로 기록됐다. 모델이 못
#: 읽은 것이 아니라 우리가 버렸다.
SHORT_OK_AREA_RATIO = 0.01


def _too_short(text: str, info) -> bool:
    """이 응답을 길이 때문에 버릴 것인가.

    입력: text — 다듬은 응답, info — 대상 그림
    출력: 버릴 것이면 True
    비고:
        작은 그림은 원래 글자가 몇 자 없으므로 길이로 거르지 않는다.
    """
    return (len(text) < MIN_RESULT_CHARS
            and _area_ratio(info) >= SHORT_OK_AREA_RATIO)

_PROMPT = """\
첨부한 그림의 **내용을 텍스트로 옮기세요.** 그림에 대한 설명이 아니라 안에 적힌 내용 자체가 필요합니다.

규칙:
- 표라면 GFM(GitHub Flavored Markdown) 표로 옮기세요. 병합된 칸은 왼쪽 위에만 값을 넣고 나머지는 비웁니다.
- 조직도·흐름도라면 계층을 들여쓴 목록으로 옮기고, 연결 관계는 `→` 로 나타내세요.
- 그 밖의 그림이면 안에 보이는 글자를 위에서 아래, 왼쪽에서 오른쪽 순서로 옮기세요.
- 숫자와 단위는 보이는 그대로 옮기세요. 추측하지 마세요.
- 읽을 수 있는 글자가 없으면 정확히 `내용 없음` 이라고만 답하세요.
- 옮긴 내용 외에 다른 말은 쓰지 마세요.

문서 맥락(참고용): {context}
"""

#: 읽을 내용이 없을 때 모델이 돌려주도록 지시한 문구.
_EMPTY_ANSWER = "내용 없음"


def read_picture_regions(
    pages: list[PageContent],
    *,
    progress: bool = False,
) -> int:
    """VLM 으로 읽어야 할 그림들의 내용을 복원한다.

    입력:
        pages     대상 페이지 목록 (제자리 갱신)
        progress  진행 표시 여부
    출력: 내용을 복원한 그림 수
    비고:
        `ImageInfo.region_kind == "image"` 이면서 면적이 충분한 것만 고른다.
        표·도표로 이미 판정된 것은 각자의 경로(승격·본문 삽입)가 처리한다.
    """
    mode = get_settings().picture_mode
    if mode not in ("read", "both"):
        _log.debug("그림 내용 읽기 생략 — picture_mode=%s", mode)
        return 0
    if not llm_available():
        _log.info("LLM 미설정 — 그림 내용 읽기를 건너뜁니다")
        return 0

    # **그림마다 사유를 적는다** (0.5.18). 일부만 읽은 경우에도 빠진
    # 그림이 왜 빠졌는지 결과물에 남아야 한다 — 로그는 흘러가고, 쪽 단위
    # 요약만으로는 **어느 그림이** 왜 빠졌는지 알 수 없다.
    jobs = []
    for page in pages:
        for info in (page.images or []):
            reason = _skip_reason(info, page)
            if reason:
                info.read_skipped = reason
            else:
                info.read_skipped = None
                jobs.append((page, info))
    if not jobs:
        # **왜 한 장도 안 읽었는지 남긴다** (0.5.17). 예전에는 조용히 0 을
        # 돌려줘, 그림이 있는데도 판독 기록이 아예 없었다 — "VLM 은
        # 연결됐는데 적용이 안 된다" 를 로그로도 결과물로도 가릴 수 없었다.
        #
        # 실측(개인정보보호위원회 PDF): 그림 9장 중 5장이 `image` 인데 면적이
        # 0.29~1.3% 라 문턱(3%)에 걸렸다 — 전부 `별첨N` 번호 배지·머리 띠였다.
        # 나머지 4장은 text·chart 로 분류돼 다른 경로 몫이다.
        _report_no_jobs(pages)
        return 0

    cfg = llm_api_config()
    workers = max(1, get_settings().llm_concurrency)
    done = 0
    bar = ProgressBar(len(jobs), "그림 내용 읽기", unit="개", enabled=progress)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_read_one, page, info, cfg): (page, info)
            for page, info in jobs
        }
        for future in as_completed(futures):
            page, info = futures[future]
            failure = ""
            try:
                markdown = future.result()
            except Exception as exc:             # noqa: BLE001 - 한 건 실패가 전체를 막지 않는다
                _log.warning("%s 내용 읽기 실패: %s", info.id, exc)
                markdown = None
                failure = f"읽기 실패 — {exc}"
            if markdown:
                info.vlm_markdown = markdown
                info.vlm_model = str((cfg or {}).get("model") or "vlm")
                _insert_after_placeholder(page, info.placeholder, markdown,
                                          getattr(info, "image_num", None))
                done += 1
                _log.info("%s 의 내용을 VLM 으로 읽었습니다 (%d자)",
                          info.id, len(markdown))
            else:
                # **시도했다 못 읽은 것도 남긴다** (0.5.18). 이것이 없으면
                # `vlm_markdown` 이 없는 그림 앞에서 "걸러진 것" 과
                # "불렀는데 실패한 것" 이 똑같아 보인다 — 연결 문제인지
                # 문턱 문제인지 결과물로 가릴 수 없다.
                info.read_skipped = failure or "모델이 읽을 내용이 없다고 답했습니다"
            bar.update()
    bar.close()
    return done


def _skip_reason(info, page=None) -> str:
    """이 그림을 왜 안 읽는지 한 마디.

    입력: info — ImageInfo
    출력: 사유 문자열 (읽을 대상이면 빈 문자열)
    """
    if getattr(info, "vlm_markdown", None):
        return "이미 읽음"
    kind = getattr(info, "region_kind", None)
    if kind not in (None, "image"):
        return f"{kind} 경로 담당"
    if not getattr(info, "image_path", None):
        return "그림 파일 없음"
    ratio = _area_ratio(info)
    if ratio < MIN_AREA_RATIO and not (page is not None and _beside_heading(info, page)):
        return f"면적 {ratio:.1%} < 문턱 {MIN_AREA_RATIO:.0%}"
    return ""


def _report_no_jobs(pages) -> None:
    """읽을 그림이 하나도 없을 때 사유를 모아 남긴다.

    입력: pages — 대상 페이지 목록
    출력: 없음 (로그 + trace)
    비고:
        **그림이 아예 없으면 말하지 않는다** — 그때는 건너뛴 것이 아니라
        할 일이 없던 것이다.
    """
    from collections import Counter

    reasons = Counter()
    for page in pages:
        for info in (page.images or []):
            reasons[_skip_reason(info, page) or "대상"] += 1
    if not reasons:
        return
    summary = " · ".join(f"{why} {n}장" for why, n in reasons.most_common())
    _log.info("VLM 으로 읽을 그림이 없습니다 — %s", summary)
    for page in pages:
        if page.images:
            page.trace.add("docstruct.images.vlm_read", "그림 판독 없음",
                           summary, status="skip")
            break


#: 그림 블록 앞뒤로 볼 줄 수. 붙어 있어야 그 제목의 표지다 —
#: 멀면 남의 제목이다.
HEADING_NEAR_LINES = 1


def _beside_heading(info, page) -> bool:
    """이 그림이 **제목 바로 옆**에 있는가 (0.5.28).

    입력: info — ImageInfo, page — 그 그림이 놓인 PageContent
    출력: 제목에 붙어 있으면 True
    비고:
        `별첨2` 처럼 절 표지를 이루는 작은 배지를 살리려는 것이다. 면적
        문턱(3%)은 로고·아이콘을 거르려고 둔 것인데, 이 배지들도 함께
        걸렀다 — 45×32 · 면적 0.3%.

        실측(개인정보보호위원회):

            <image 6> </image 6> # 성과목표체계별 예산현황 (단위: 백만원)
            # 복합 프로그램 성과지표 관리 별첨8 <image 9> </image 9> # <복합…

        배지는 **제목 바로 옆**에 붙는다. 로고는 그렇지 않다. 크기나
        가로세로비보다 이 자리 관계가 확실하다 — 배지는 거의 정사각
        (1.4)이라 "가로로 긴 띠" 로는 가릴 수 없었다.

        긴 제목 띠(`성과목표체계별 예산현황`)는 그림이 아니라 **텍스트로
        이미 읽힌다.** 잃는 것은 `별첨N` 세 글자뿐이므로, 문턱을 통째로
        낮추는 대신 이 자리만 연다.
    """
    import re

    num = getattr(info, "image_num", None)
    content = getattr(page, "content", "") or ""
    if num is None or not content:
        return False
    match = re.search(rf"<image {num}>.*?</image {num}>", content, re.DOTALL)
    if match is None:
        return False

    def _is_heading(lines: list[str]) -> bool:
        """빈 줄을 건너뛴 **첫 줄**이 제목인가."""
        seen = 0
        for line in lines:
            text = line.strip()
            if not text:
                continue
            seen += 1
            if seen > HEADING_NEAR_LINES:
                return False
            if re.match(r"#{1,6} ", text):
                return True
        return False

    before = content[:match.start()].split("\n")[::-1]
    after = content[match.end():].split("\n")
    return _is_heading(after) or _is_heading(before)


def _should_read(info, page=None) -> bool:
    """이 그림을 VLM 으로 읽어야 하는지.

    입력: info — ImageInfo, page — 그 그림이 놓인 PageContent (없어도 된다)
    출력: 대상이면 True
    비고:
        · 좌표 판정이 IMAGE 여야 한다 (표·도표는 다른 경로가 맡는다)
        · 저장된 그림 파일이 있어야 한다 (근거가 없으면 물어볼 수 없다)
        · 이미 읽었으면 다시 하지 않는다
        · 면적이 작으면 로고·아이콘이므로 건너뛴다 —
          **단 제목 바로 옆이면 절 표지이므로 읽는다** (0.5.28)

        면적 판정은 `bbox` 가 있어야 하므로 사실상 **PDF 경로에만** 걸린다.
        HWPX·HWP 는 지면 좌표가 없어 `_area_ratio` 가 1.0 을 내고 언제나
        통과한다 — 이 예외도 그쪽 동작을 바꾸지 않는다.
    """
    if getattr(info, "vlm_markdown", None):
        return False
    if getattr(info, "region_kind", None) not in (None, "image"):
        return False
    if not getattr(info, "image_path", None):
        return False
    if _area_ratio(info) >= MIN_AREA_RATIO:
        return True
    return page is not None and _beside_heading(info, page)


def _area_ratio(info) -> float:
    """그림이 페이지에서 차지하는 면적 비율.

    입력: info — ImageInfo (bbox 사용)
    출력: 0.0~1.0. bbox 가 없으면 1.0 (판단 못 하면 읽어 본다)
    """
    bbox = getattr(info, "bbox", None)
    if not bbox:
        return 1.0
    try:
        width = float(bbox["r"]) - float(bbox["l"])
        height = float(bbox["b"]) - float(bbox["t"])
    except (KeyError, TypeError, ValueError):
        return 1.0
    if width <= 0 or height <= 0:
        return 1.0
    # A4 기준. 정확한 페이지 크기를 몰라도 대소 판단에는 충분하다.
    return min(1.0, abs(width * height) / (595.0 * 842.0))


#: 같은 줄이 이 비율 이상이면 지어낸 것으로 본다.
MAX_REPEAT_RATIO = 0.30
#: 반복을 따지기 시작할 최소 줄 수 (짧은 답은 우연히 겹칠 수 있다).
MIN_LINES_FOR_REPEAT = 6

#: 이 비율 이상이면 지면을 통째로 채운 이미지로 본다 — 스캔본을 문서에
#: 붙인 경우다. **형식과 무관하다**: 한글 문서에도 스캔 이미지가 들어간다.
PAGE_LIKE_RATIO = 0.55


def is_page_like(info, legible: dict | None = None) -> bool:
    """옮길 글이 줄지어 있는 그림인가 (= 전사 대상).

    입력: info — ImageInfo, legible — legibility.measure 결과
    출력: 전사 대상이면 True
    비고:
        **지시문이 달라야 한다.** 도표는 "무엇을 나타내는 그림인지" 를
        설명하게 하고, 스캔 지면은 "보이는 글을 그대로 옮기게" 해야 한다.
        스캔본을 붙인 한글 문서를 그림 지시문으로 읽으면 **본문이 통째로
        설명 한 문단으로 요약돼 사라진다.**

        **면적으로 가르지 않는다** (0.4.34). 실측: `ratio >= 0.55` 로
        잡힌 셋이 전부 스캔본이 아니라 큰 도표였다 — 해상 지도·논리모형·
        꺾은선그래프. 지도에 "보이는 글을 그대로 옮기라" 고 해 봐야
        섬 이름 나열이 나올 뿐이다.

        글자가 여러 줄로 빽빽한지를 본다. 조직도도 여기 걸리는데
        해롭지 않다 — 전사하면 계층째로 옮겨 온다.
    """
    kind = (legible or {}).get("kind")
    if kind:
        return kind == "page"
    return _area_ratio(info) >= PAGE_LIKE_RATIO


#: 도해로 볼 최소 글자 줄 수와 줄당 조각 상한.
CHART_MIN_ROWS = 20
CHART_MAX_PER_ROW = 16.0

#: 도해(조직도·논리모형)용 지시문 — **표가 아니라 계층**으로 받는다.
_CHART_PROMPT = """이 그림은 조직도·흐름도 같은 **도해**입니다. 표로
옮기지 마세요 — 칸이 격자로 놓여 있어도 그것은 표가 아니라 상자와 선의
배치입니다.

들여쓴 목록으로 계층을 옮겨 주세요:

- 위 상자
  - 그 아래 상자
    - 더 아래 상자

규칙:
- **읽히는 글자만** 옮깁니다. 흐려서 확실하지 않으면 그 자리를 비우거나
  "(읽을 수 없음)" 이라고 적으세요
- **같은 이름을 되풀이하지 마세요.** 앞의 것을 복사해 칸을 채우는 것은
  지어내는 것입니다
- 상자가 너무 많아 다 읽을 수 없으면, 큰 갈래만 적고 "이하 하위 부서
  다수" 라고 쓰세요

앞뒤 맥락(참고용):
{context}
"""

#: 판독이 안 되는 그림용 지시문 — **전사가 아니라 설명**을 받는다.
_DESCRIBE_PROMPT = """이 그림은 해상도가 낮아 글자를 정확히 읽을 수
없습니다. 글자를 옮기려 하지 말고, **무엇을 나타내는 그림인지** 설명해
주세요.

담을 것:
- 그림의 종류 (원그래프·막대그래프·꺾은선그래프·조직도·흐름도·지도 등)
- 무엇에 관한 것인지 (제목이 읽히면 그대로, 아니면 앞뒤 맥락으로)
- 축·범례·항목이 읽히는 범위에서 무엇을 견주고 있는지
- 값의 흐름 (늘어남·줄어듦·한 항목이 큼 등). **숫자는 확실히 읽히는
  것만** 적고, 흐려서 확실하지 않으면 적지 마세요

지어내지 마세요. 확실하지 않으면 "읽을 수 없음" 이라고 쓰세요.
두세 문장이면 충분합니다.

앞뒤 맥락(참고용):
{context}
"""


def _read_one(page: PageContent, info, cfg: dict[str, Any]) -> str | None:
    """그림 하나를 VLM 에 보내 내용을 받는다.

    입력: page(맥락용), info(대상 그림), cfg(LLM 설정)
    출력: markdown 문자열. 읽을 내용이 없거나 실패하면 None
    비고:
        지면을 채운 이미지는 **스캔 쪽 지시문**으로 읽는다 — 같은 VLM
        호출이지만 무엇을 시키느냐가 결과를 가른다.
    """
    # **판독 가능성을 먼저 잰다.** 이 값이 보정 여부와 지시문을 정하고,
    # 판독이 부실할 때 원인이 된다.
    from docstruct.images.image_prep import prepare
    from docstruct.images.legibility import measure

    legible = measure(info.image_path)
    info.legibility = legible
    if legible.get("verdict") == "decoration":
        # 도형 장식이다 — VLM 을 부를 이유가 없다. 실측(국방부):
        # 14×91 픽셀짜리 삼각형이 판독 대상으로 잡혀 있었다.
        page.trace.add("docstruct.images.vlm_read", "그림 판독 생략",
                       f"{info.id} · 장식으로 판정 (글자가 없다)")
        return None

    send_path, applied = prepare(info.image_path,
                                 getattr(info, "dpi", None), legible)
    if applied:
        info.image_prep = applied                # 어느 설정이었는지 남긴다

    # **무엇을 하고 있는지 남긴다.**
    verdict = legible.get("verdict")
    glyph = legible.get("glyph_px")
    detail = f"{info.id} · 글자 {glyph}px({verdict})"
    if applied.get("upscale"):
        detail += f" · 초고해상도 보정 후 VLM 수행 [{applied['upscale']}]"
    elif applied.get("skipped"):
        detail += f" · 보정 취소({applied['skipped']}) — 원본으로 수행"
    elif verdict == "poor":
        detail += (" · 원본에 정보가 없어 보정하지 않음 — "
                   "**전사 대신 그림 설명**으로 자리를 채움")
    page.trace.add("docstruct.images.vlm_read", "그림 판독", detail,
                   status="warn" if verdict == "poor" else "ok")
    encoded = encode_image_file(send_path)
    if not encoded:
        return None
    mime, b64 = encoded

    context = (page.content or "")[:400]
    if is_page_like(info, legible):
        from docstruct.text.scan_vlm import _PROMPT as PAGE_PROMPT

        prompt = PAGE_PROMPT.format(context=context or "(없음)")
        span = "page_image_read"
    else:
        prompt = _PROMPT.format(context=context or "(없음)")
        span = "picture_read"
    rows = legible.get("text_rows") or 0
    per_row = legible.get("per_row") or 0
    if (legible.get("verdict") != "poor" and rows >= CHART_MIN_ROWS
            and per_row < CHART_MAX_PER_ROW):
        # **상자가 많고 줄당 글자가 성기면 도해다.** 조직도·논리모형이
        # 여기 든다. 표로 옮기라고 하면 모델이 없는 표를 만든다 —
        # 실측(행안부 조직도): 같은 줄이 10번 반복된 표가 나왔고 지면에는
        # 그런 표가 없다. **계층 목록**으로 받는다.
        prompt = _CHART_PROMPT.format(context=context or "(없음)")
        span = "chart_read"
    if legible.get("verdict") == "poor":
        # **글자를 옮기라고 하지 않는다 — 설명하라고 한다.**
        # 원본 픽셀에 이미 정보가 없어(실측: `100%` 가 `IDD%` 로 박혀
        # 있다) 전사를 시키면 모델이 그럴듯하게 지어낸다. 그러나 그림이
        # 무엇인지·무엇을 말하려는지는 흐려도 알 수 있다. 본문에서
        # 그림이 사라지지 않게 **그 설명이 자리를 대신한다.**
        prompt = _DESCRIBE_PROMPT.format(context=context or "(없음)")
        span = "picture_describe"
    raw = invoke_llm(
        prompt,
        span_name=span,
        image_urls=[f"data:{mime};base64,{b64}"],
        cfg=cfg,
    )
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = _strip_fence(text)
    if not text or text.replace(" ", "") == _EMPTY_ANSWER.replace(" ", ""):
        return None
    if _too_short(text, info):
        return None

    repeats = _repetition_ratio(text)
    if repeats >= MAX_REPEAT_RATIO:
        # **같은 줄을 되풀이하면 읽은 것이 아니다.** 흐린 그림에서 모델이
        # 앞 패턴을 복사하며 채우는 전형적인 실패다 — 실측(행안부 조직도
        # 122dpi·획 6px): `□ 안전정책담당관 | □ 예방정책담당관 | …` 이
        # **똑같이 10줄** 반복됐고, 실제 지면에는 그런 표가 없다.
        #
        # 그럴듯한 표가 들어가는 것이 비어 있는 것보다 나쁘다 — 사람도
        # 하류도 사실로 읽는다.
        page.trace.add(
            "docstruct.images.vlm_read", "그림 판독 버림",
            f"{info.id} · 같은 줄이 {repeats:.0%} 반복 — 지어낸 것으로 봅니다",
            status="warn")
        return None
    if span == "page_image_read":
        # **전사했다는 사실을 남긴다** (0.4.58). 여기서 쓴 지시문은 스캔 쪽
        # 판독(scan_vlm)의 것과 같은 것이다 — 이 그림은 PDF 스캔 쪽과
        # **물리적으로 같은 행위**로 읽혔다. 그런데 그 사실이 결과물에
        # 남지 않아, 수치 오독 위험이 같은데도 검증(verify_ocr)과 이중
        # 판독(scan_ab) 대상에서 통째로 빠져 있었다. 실측(조달청 HWPX
        # image_1): `kind="page"` 로 갈려 조직도 전문이 전사돼 본문에
        # 들어갔는데 아무 검증도 받지 않았다.
        #
        # **결과가 확정된 뒤에 세운다.** 지시문을 고른 자리에서 세우면
        # 뒤 분기(도해·설명)가 덮어쓴 경우와 반복 검출로 버린 경우까지
        # "전사했다" 고 적게 된다.
        info.transcribed = True
        page.trace.add(
            "docstruct.images.vlm_read", "지면 전사",
            f"{info.id} · 스캔 쪽과 같은 전사 지시문 — 검증 대상에 넣습니다")
    return text


def _repetition_ratio(text: str) -> float:
    """같은 줄이 얼마나 되풀이되는가.

    입력: text — VLM 이 낸 글
    출력: 0.0~1.0 (가장 많이 나온 줄의 비율)
    비고:
        표 구분선(`| --- |`)과 빈 줄은 세지 않는다 — 정상적으로 반복된다.
    """
    lines = [line.strip() for line in (text or "").splitlines()]
    lines = [line for line in lines
             if line and not set(line.replace("|", "").replace(" ", "")) <= set("-:")]
    if len(lines) < MIN_LINES_FOR_REPEAT:
        return 0.0
    from collections import Counter

    most = Counter(lines).most_common(1)[0][1]
    return most / len(lines)


def _strip_fence(text: str) -> str:
    """```markdown 울타리를 벗긴다.

    입력: text — 모델 응답
    출력: 울타리를 제거한 본문
    """
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _insert_after_placeholder(page: PageContent, placeholder: str, markdown: str,
                              image_num: int | None = None) -> None:
    """읽은 내용을 그 그림의 `<image-read>` 칸에 넣는다.

    입력: page, placeholder — 여는 태그, markdown — 읽은 내용, image_num — 그림 번호
    출력: 없음 (page.content 갱신)
    비고:
        **칸을 지정해 넣는다** (0.4.89). 예전에는 여는 표식 **뒤에 이어
        붙이기만** 했다. 닫는 태그가 없으니 그 글이 그림의 몫인지 본문인지
        구분이 없었고, 두 번 읽으면 두 번 붙었다. 이제 그 자리에 덮어쓴다 —
        다시 읽어도 늘지 않는다.

        파서가 준 설명(`<image-desc>`)은 건드리지 않는다. 출처가 다르므로
        "원본에 있던 글" 과 "모델이 읽은 글" 이 갈려 있어야 한다.

        블록을 못 찾으면(옛 산출물·짝이 깨진 본문) 예전처럼 뒤에 붙인다 —
        읽은 것을 버리지는 않는다.
    """
    from docstruct.images.tags import sync_image_block

    content = page.content or ""
    if image_num is not None and f"<image {image_num}>" in content:
        page.content = sync_image_block(content, image_num, read=markdown)
        return
    if placeholder and placeholder in content:
        page.content = content.replace(placeholder, f"{placeholder}\n\n{markdown}", 1)
    else:
        page.content = f"{content}\n\n{markdown}" if content else markdown


def read_new_pictures(pages: list[PageContent], *, progress: bool = False) -> int:
    """**판독 단계 뒤에 생긴 그림**만 다시 읽는다 (0.5.30).

    입력: pages — 대상 페이지 목록 (제자리 갱신), progress — 진행 표시
    출력: 읽은 그림 수
    비고:
        구간 9 에서 표가 그림으로 판정되면 그 자리에 `ImageInfo` 가 새로
        생긴다(`tables.fill._group_to_image`). 그런데 그림 판독은 구간 7 —
        이미 지나갔다. 그래서 이 그림들은 **내용도 사유도 없이** 남았다:

            img_from_table_5  0×0  read_skipped=None  vlm_markdown=None

        0.5.18 이 "모든 그림은 내용이 있거나 이유가 있다" 고 못 박았는데
        이 자리만 그 약속 밖이었다. 게다가 표가 그림으로 판정됐다는 것은
        **그 자리에 읽을 내용이 있다**는 신호다 — 버릴 이유가 없다.

        **새로 생긴 것만 본다.** 앞 단계에서 읽혔거나 사유가 붙은 그림은
        건드리지 않는다 — 실패한 그림을 다시 부르면 엔드포인트가 죽어
        있을 때 호출이 두 배가 된다.
    """
    fresh = [page for page in pages
             if any(getattr(i, "vlm_markdown", None) is None
                    and getattr(i, "read_skipped", None) is None
                    for i in (page.images or []))]
    if not fresh:
        return 0
    _log.info("판독 뒤에 생긴 그림을 읽습니다 (쪽 %d개)", len(fresh))
    return read_picture_regions(fresh, progress=progress)
