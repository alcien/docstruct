"""문서 → PageDocument 변환 파이프라인.

입력:
    문서 경로(pdf·hwpx·hwp) + 실행 옵션(assess_tables·fill_tables·render_pages·out_dir…)

역할:
    파일 하나를 받아 포맷별 추출 → 페이지 렌더 → 표 평가 → 표 재추출 →
    정규화 순으로 처리하고, 단계별 소요 시간과 처리 경로를 기록한다.
    포맷 분기는 하지 않으며 extractors.registry 에 위임한다.
호출부:
    docstruct.cli        CLI 실행
    docstruct/__init__   build_document 로 재노출 (노트북에서 사용)
출력:
    PageDocument — pages[].trace 에 처리 경로, timings 에 단계별 초,
    pipeline 에 적용 설정이 채워진 상태

구간 목차 (build_document 안의 배너 번호와 같다)::

    0  입력 확인·작업 폴더           공통
    1  추출                          형식 축  extractors/ · converters/
    2  스캔 쪽 판별·지면 렌더        텍스트   images/page_render
    3  스캔 쪽 본문 판독             텍스트   text/scan_vlm · converters/pdf/rapidocr_ko
    4  표 표시(바꾸지 않음)          표       tables/continued·odd_tables · images/chart_read
    5  실험 사다리                   표       experiments/
    6  표 재구성(바꿈)               표       tables/grid_rebuild · vlm_rebuild
    7  그림 판독                     그림     images/picture · vlm_read · chart_read
    8  상시 검사                     표       structuring/checks (grid_check · repair_leaks)
    9  표 LLM 판정·재추출            표       tables/assess · fill
    10 OCR 검증·재판독               텍스트   text/ocr_verify · ocr_reread
    11 문서 구조(목차)               텍스트   outline/toc
    12 마무리                        공통
"""
from __future__ import annotations

import logging
import re
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from docstruct.core.config import get_settings, resolve_device
from docstruct.core.steps import (report, report_progress,
                                  report_skip, steps_for)
from docstruct.images.page_render import render_pages_with_tables, safe_file_stem
from docstruct.models import PageContent, PageDocument
from docstruct.tables.assess import assess_document
from docstruct.tables.fill import process_tables
from docstruct.tables.tags import normalize_table_blocks

_log = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = (".hwp", ".hwpx", ".pdf")
# ↑ registry.supported_suffixes() 와 동일해야 합니다 (import 순서상 상수로 유지,
#   docstruct/__init__ 의 자가 검증이 불일치를 잡습니다).

from docstruct.models import (  # noqa: F401  (하위호환 재노출)
    GPU_ACCELERATED, STAGE_ASSESS, STAGE_EXTRACT, STAGE_EXTRACT_MARKUP,
    STAGE_PICTURE_READ,
    STAGE_FILL, STAGE_GRID_REBUILD, STAGE_KOREAN_OCR,
    STAGE_REREAD_OCR, STAGE_SCAN_VLM,
    STAGE_VERIFY_OCR, STAGE_RENDER,
    STAGE_TABLE_REBUILD,
    stage_extract,
)


def source_format(path: Path) -> str:
    """확장자로 문서 형식을 판별한다.

    입력: path — 문서 경로
    출력: 'pdf' | 'hwp' | 'hwpx'
    예외: 미지원 확장자면 ValueError
    """
    ext = path.suffix.lower()
    if ext in SUPPORTED_SUFFIXES:
        return ext.lstrip(".")
    raise ValueError(
        f"지원하지 않는 형식: {ext!r} (지원: {', '.join(SUPPORTED_SUFFIXES)})"
    )


def _extract(path: Path, fmt: str, image_dir: Path | None):
    """포맷에 맞는 추출기를 찾아 실행한다 (실패 시 실제 형식으로 재시도).

    입력: path(문서 경로), fmt(pdf|hwp|hwpx), image_dir(이미지 저장 위치)
    출력: ExtractionResult (pages, failed_pages, table_html)
    예외: 재시도까지 실패하면 **처음 예외**를 그대로 올린다
    동작:
        ① 확장자가 가리키는 추출기로 시도한다.
        ② 실패하면 파일 내용(시그니처)으로 실제 형식을 알아보고, 확장자와
           다를 때만 그 추출기로 한 번 더 시도한다.
        ③ 그래도 실패하면 ①의 예외를 낸다.

        ②를 두는 이유: 실제 문서에서 이름만 `.hwpx` 이고 내용은 HWP
        바이너리인 파일이 있었다(한글에서 형식을 `한글 문서(*.hwp)` 로 둔
        채 파일명에 `.hwpx` 를 타이핑한 경우). 확장자만 믿으면 python-hwpx
        가 `BadZipFile` 을 내며 멈춘다.

        재시도가 실패하면 **처음 예외를 올린다.** 사용자가 넣은 형식 기준의
        오류가 원인에 가깝고, 재시도는 어디까지나 구제 시도이기 때문이다.
    """
    from docstruct.converters.signature import detect_format
    from docstruct.extractors.registry import get_extractor

    try:
        return get_extractor(f".{fmt}")(path, image_dir=image_dir)
    except Exception as first_error:
        actual = detect_format(path)
        if not actual or actual == fmt:
            raise
        _log.warning(
            "%s: %s 로 읽지 못했습니다 — 내용이 %s 형식이라 다시 시도합니다 (%s)",
            path.name, fmt.upper(), actual.upper(), first_error,
        )
        try:
            result = get_extractor(f".{actual}")(path, image_dir=image_dir)
        except Exception:
            raise first_error from None
        _log.warning(
            "%s: %s 형식으로 처리했습니다. 확장자(.%s)와 내용이 다릅니다 — "
            "한글에서 '다른 이름으로 저장' 시 파일 형식을 확인하세요.",
            path.name, actual.upper(), fmt,
        )
        return result


def _render_page_images(
    pdf_path: Path,
    pages: list[PageContent],
    out_dir: Path,
    *,
    scale: float = 2.0,
    all_pages: bool = False,
    only: set[int] | None = None,
) -> None:
    """페이지를 PNG 로 렌더하고 경로를 기록한다.

    입력:
        pdf_path   원본 PDF 경로
        pages      PageContent 목록
        out_dir    저장 위치
        scale      렌더 배율
        all_pages  True 면 표 유무와 무관하게 렌더
        only       주면 이 쪽 번호만 렌더 (표가 있는 쪽은 항상 포함)
    출력: 없음 (page.page_image_path 설정, trace 에 단계 기록)
    비고:
        원래 용도는 **표 재추출의 시각 근거**라 표가 있는 페이지만 렌더했다.
        한국어 OCR 로 본문을 다시 읽으려면 표 없는 페이지도 이미지가 있어야
        하므로 `all_pages=True` 를 쓴다.

        `only` 는 그중에서도 **다시 읽어야 할 쪽만** 렌더하기 위한 것이다.
        텍스트 레이어가 온전한 쪽까지 렌더하면 155쪽 문서에서 50초쯤
        헛돈다. `only` 를 주면 **그 쪽만** 그린다 — 표가 있는 쪽을 함께
        그리던 예전 동작은 스캔 대상별 재렌더 호출과 만나 O(N²) 렌더와
        근거 이미지 덮어쓰기를 만들었다(0.4.56).

        pypdfium2 가 없거나 렌더에 실패하면 경고만 남기고 텍스트 기반으로
        진행한다.
    """
    if only is not None:
        # **`only` 는 엄격한 필터다.** 예전에는 표가 있는 쪽을 `only` 와
        # 무관하게 항상 포함했는데, 스캔 대상마다 이 함수를 다시 부르는
        # 호출부(고해상도 재렌더)와 만나 **스캔 대상 수 × 표 쪽 수**만큼
        # 300dpi 렌더가 반복됐다 — 전면 스캔본이면 O(N²)이고, 텍스트
        # PDF 에서도 표 근거 이미지가 144dpi 에서 마지막 스캔 배율로
        # 조용히 덮어써졌다(같은 표가 문서 사정에 따라 다른 근거를 받는
        # 결함의 재발). 표 쪽은 첫 호출(only=None)이 이미 그렸다.
        targets = [
            p.page_no for p in pages
            if isinstance(p.page_no, int) and p.page_no in only
        ]
    else:
        targets = [
            p.page_no for p in pages
            if isinstance(p.page_no, int) and (p.tables or all_pages)
        ]
    if not targets:
        return

    try:
        rendered = render_pages_with_tables(
            pdf_path,
            targets,
            out_dir,
            file_stem=safe_file_stem(pdf_path.name),
            scale=scale,
        )
    except Exception as exc:
        _log.warning("페이지 렌더 실패 — 텍스트만으로 평가합니다: %s", exc)
        rendered = {}

    for page in pages:
        img = rendered.get(page.page_no)
        if img:
            page.page_image_path = img
            page.trace.rendered = True
            page.trace.add(
                "docstruct.images.page_render",
                "페이지 PNG 렌더",
                f"pypdfium2 · {scale}x (표 평가·재추출의 시각 근거)",
            )
        elif page.tables:
            page.trace.add(
                "docstruct.images.page_render",
                "페이지 렌더 실패",
                "이미지 없이 텍스트만으로 평가 — 정확도 하락",
                status="warn",
            )


#: 텍스트 레이어가 쓸 만하다고 볼 최소 글자 수 (한글 + 라틴).
#: 스캔본에도 브라우저 인쇄 머리말·URL 이 텍스트로 들어 있어 전체 글자 수만
#: 으로는 갈리지 않는다. URL 을 걷어낸 뒤 실제 낱말 글자만 세면 나뉜다.
#:
#:     스캔 PDF    쪽당 7자
#:     텍스트 PDF  25% 지점 33자 · 중앙값 86자
MIN_LAYER_HANGUL = 15

#: 텍스트 레이어를 신뢰할 최소 낱말 비율.
#: 실측: 텍스트 PDF 중앙값 0.61, 스캔 PDF 0.17.
MIN_LAYER_HANGUL_RATIO = 0.3

#: 비율을 보지 않고 통과시키는 낱말 글자 수. 이만큼 있으면 그 쪽은
#: 스캔본이 아니다 — 다시 읽어야 할 만큼 빈약한 지면일 수 없다.
#:
#: **왜 필요한가.** 비율만 보면 **숫자 표가 스캔본으로 오판된다.**
#: 실측(조달청 p5 직급별 인원 현황): 텍스트 레이어 1,571자가 멀쩡한데
#: `592.3(576) 527.0(541) 1,119.3(1,117)` 같은 숫자·괄호가 대부분이라
#: 낱말 글자 비율이 0.15 였다. 그 때문에 **76쪽 전부가** 한국어 OCR
#: 재판독을 탔고, 정확한 텍스트를 46~70% 정확도의 인식 결과로 덮었다
#: (`- 고위공무원단` → `공금운농-`).
#:
#: 원래 걸러내려던 것(브라우저로 인쇄한 스캔본의 머리말·URL)은 쪽당
#: 낱말 글자가 7자였으므로, 이 문턱에 한참 못 미쳐 여전히 걸러진다.
MIN_LAYER_WORDS_ABSOLUTE = 60

#: 비율을 잴 때 분모에서 빼는 글자. 숫자·구두점이 많은 것은 표일 뿐
#: 스캔본의 근거가 아니다.
_FILLER_CHAR_RE = re.compile(r"[0-9.,()\[\]{}%△▲▽▼·・:;/\\|~\-＋+*'\"　]")

#: 재판독 대상 판정을 끄는 스위치. 켜면 모든 쪽을 다시 읽는다.
FORCE_REREAD_ENV = "DOCSTRUCT_KOREAN_OCR_FORCE"

#: 낱말을 이루는 글자 — 한글과 라틴 문자.
#: **한글만 세면 영어 문서가 스캔본으로 오판된다.** NASA 약력(3,080자,
#: 라틴 2,885자, 한글 0자)이 재판독 대상으로 잡혔다. 온전한 텍스트를
#: 인식 결과로 바꾸게 되므로 라틴도 함께 센다.
_WORD_CHAR_RE = re.compile(r"[가-힣A-Za-z]")


def _has_usable_text_layer(text: str | None) -> bool:
    """이 페이지의 텍스트 레이어를 그대로 써도 되는지.

    입력: text — 그 페이지에서 이미 뽑아 둔 본문
    출력: 쓸 만하면 True
    비고:
        **텍스트 PDF 를 OCR 로 덮으면 정확한 텍스트를 인식 결과로 바꾼다.**
        스캔본에서 OCR 이 46~70% 였으니, 텍스트 레이어가 있는 쪽에 그것을
        적용하면 손해가 크다. 실무에서는 스캔본과 텍스트 PDF 가 섞여
        들어오므로 페이지마다 판단해야 한다.

        글자 수만으로는 갈리지 않는다. 브라우저로 인쇄한 스캔본에는
        머리말·URL 이 텍스트로 들어 있어 쪽당 97자가 잡혔다(낱말 글자는
        7자). URL 과 태그를 걷어낸 뒤 한글·라틴 글자만 세고 비율까지 본다.
    """
    if not text:
        return False
    flat = re.sub(r"\s", "", re.sub(r"<[^>]+>|https?://\S+", "", text))
    if not flat:
        return False
    words = len(_WORD_CHAR_RE.findall(flat))
    if words >= MIN_LAYER_WORDS_ABSOLUTE:
        return True                              # 숫자 표여도 본문은 충분하다
    meaningful = len(_FILLER_CHAR_RE.sub("", flat)) or 1
    return (words >= MIN_LAYER_HANGUL
            and words / meaningful >= MIN_LAYER_HANGUL_RATIO)


#: 스캔 쪽 재판독 배율. OCR·VLM 모두 지면 이미지를 입력으로 쓰므로
#: 해상도가 곧 정확도다. 1.0 = 72dpi 이므로 4.17 ≈ **300dpi** —
#: OCR 권장 해상도다. 기본 렌더(2.0 = 144dpi)는 스캔 원본(145~148dpi)과
#: 겹쳐 여유가 없었다. 대상이 문서당 두어 쪽뿐이라 비용이 문제가 되지
#: 않는다(0.4.11 게이트 수정 뒤 조달청 76쪽 → 2쪽).
SCAN_RENDER_SCALE = 4.17

#: 원본 해상도를 잴 때 무시할 최소 크기(pt). 이보다 작으면 장식이다 —
#: 실측(행안부 p38): 17×19pt 짜리 글머리 아이콘이 609dpi 였다.
MIN_IMAGE_PT = 72.0

#: 배율 상한(≈864dpi). 아주 높은 dpi 원본에서 화소가 터지는 것을 막는다.
#: **600dpi 스캔은 흔하므로 그 위에 둔다** — 8.0(576dpi)으로 두면 600dpi
#: 원본을 깎아 있는 정보를 버리게 된다.
MAX_SCAN_SCALE = 12.0


def scan_render_scale() -> float:
    """스캔 쪽 렌더 배율 — 손잡이로 뺀다.

    입력: 없음 (`DOCSTRUCT_SCAN_RENDER_SCALE`)
    출력: 배율 (1.0 = 72dpi). 잘못된 값이면 기본값
    비고:
        300dpi 는 **OCR 권장값이라는 일반론으로 넣었고 이 문서군에서
        재 본 적이 없다.** A/B 로 확인하려면 손잡이가 있어야 한다 —
        관통 규칙(0.4.7)처럼 물리 근거가 그럴듯해도 실측에서 뒤집힌
        전례가 있다.

        VLM 은 이미지를 그대로 보내므로 해상도가 토큰·지연·비용에
        직결된다. 스캔본 수백 쪽을 다룰 때는 값을 낮출 여지가 필요하다.
    """
    import os

    raw = os.getenv("DOCSTRUCT_SCAN_RENDER_SCALE", "").strip()
    if not raw:
        return SCAN_RENDER_SCALE
    try:
        value = float(raw)
    except ValueError:
        _log.warning("DOCSTRUCT_SCAN_RENDER_SCALE=%r 를 읽지 못했습니다", raw)
        return SCAN_RENDER_SCALE
    # 상한은 MAX_SCAN_SCALE 과 같다 — 손잡이 상한(예전 10.0)이 원본 유지
    # 상한(12.0)보다 낮으면 600dpi 이상을 손으로 지정할 길이 없었다.
    return value if 0.5 <= value <= MAX_SCAN_SCALE else SCAN_RENDER_SCALE


def native_dpi(pdf_path, page_no: int) -> float | None:
    """그 쪽에 박힌 이미지의 실제 해상도(dpi).

    입력: pdf_path — 원본 PDF, page_no — 1-기준 쪽 번호
    출력: 가장 큰 이미지의 dpi. 이미지가 없거나 못 재면 None
    비고:
        스캔 쪽은 지면이 통째로 이미지 하나다. 그 이미지가 **원래 몇
        dpi 로 들어와 있는지**를 재면, 헛되이 키우는 일을 막을 수 있다.

        배율은 원본에 없는 정보를 만들지 못한다. 145dpi 로 들어온 스캔을
        300dpi 로 렌더해 봐야 **같은 정보를 두 배로 늘린 것**뿐이고,
        보간 때문에 오히려 흐려질 수 있다. 반대로 이미 600dpi 로 들어온
        쪽을 300 으로 낮추면 있던 정보를 버린다.

        작은 장식은 세지 않는다 — 실측(행안부 p38): 17×19pt 짜리 글머리
        아이콘이 609dpi 였다. 그것을 기준으로 삼으면 배율이 엉뚱해진다.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return None
    try:
        document = pdfium.PdfDocument(str(pdf_path))
    except Exception:                            # noqa: BLE001 - 못 재면 기본값을 쓴다
        return None
    try:
        page = document[page_no - 1]
        best = None
        for obj in page.get_objects():
            if getattr(obj, "type", None) != 3:  # 3 = 이미지
                continue
            try:
                left, bottom, right, top = obj.get_bounds()
                meta = obj.get_metadata()
            except Exception:                    # noqa: BLE001 - 그 객체만 건너뛴다
                continue
            width_pt = right - left
            if width_pt < MIN_IMAGE_PT or (top - bottom) < MIN_IMAGE_PT:
                continue                         # 글머리 아이콘 등 장식
            dpi = meta.width / (width_pt / 72)
            best = dpi if best is None else max(best, dpi)
        return best
    except Exception:                            # noqa: BLE001
        return None
    finally:
        document.close()


def page_scan_scale(pdf_path, page_no: int) -> float:
    """이 쪽을 몇 배로 그릴지 — 원본 해상도를 보고 정한다.

    입력: pdf_path — 원본 PDF, page_no — 쪽 번호
    출력: 렌더 배율 (1.0 = 72dpi)
    비고:
        요구사항은 "필요시 300dpi" 다. 그래서 **목표는 300 으로 두되**,
        원본이 이미 그 이상이면 그 해상도에 맞춘다(있는 정보를 버리지
        않는다). 원본을 못 재면 목표 배율을 그대로 쓴다.

            원본 150dpi → 목표 300 (2배로 키운다)
            원본 600dpi → 600 (낮추지 않는다)
            못 잼        → 4.17 (기본)

        상한(MAX_SCAN_SCALE)을 둔다 — 아주 높은 dpi 원본에서 이미지가
        수천만 화소가 되면 VLM 토큰과 렌더 시간이 함께 터진다.
    """
    target = scan_render_scale()
    native = native_dpi(pdf_path, page_no)
    if native is None:
        return target
    return max(target, min(native / 72.0, MAX_SCAN_SCALE))


def scan_backend() -> str:
    """스캔 쪽을 무엇으로 읽을지 — `vlm`(기본) 또는 `ocr`.

    입력: 없음 (`DOCSTRUCT_SCAN_BACKEND`)
    출력: "vlm" 또는 "ocr"
    비고:
        rapidocr 는 한국어 모델을 붙여도 46~70% 이고 한자가 섞인다
        (기본 PP-OCRv6 small 에 한국어가 없다) — 실측: `- 고위공무원단`
        이 `공금운농-` 이 됐다. VLM 은 문맥을 보므로 그런 어긋남이 없다.
        A/B 를 위해 `ocr` 로 되돌릴 수 있게 둔다.
    """
    import os

    value = os.getenv("DOCSTRUCT_SCAN_BACKEND", "").strip().lower()
    return "ocr" if value == "ocr" else "vlm"


def _force_reread() -> bool:
    """텍스트 레이어와 무관하게 모든 쪽을 다시 읽을지.

    입력: 없음 (`DOCSTRUCT_KOREAN_OCR_FORCE`)
    출력: 강제면 True
    """
    import os

    return os.environ.get(FORCE_REREAD_ENV, "").strip().lower() in (
        "1", "true", "on", "yes")



#: 이보다 본문이 짧으면 텍스트 레이어로 메울 후보로 본다.
MIN_PAGE_TEXT = 40


def _rescue_thin_pages(pdf_path: Path, pages: list[PageContent]) -> int:
    """docling 이 놓친 쪽을 원본 텍스트 레이어로 메운다.

    입력: pdf_path — 원본 PDF, pages — 추출된 페이지 목록 (제자리 갱신)
    출력: 메운 쪽 수
    비고:
        **docling 이 어떤 쪽에서 텍스트를 거의 못 뽑고 쪽 전체를 그림
        하나로 분류하는 일이 있다.** 실측(조달청 p5 직급별 인원 현황):
        `docling.parse 16자 · 요소 분류 텍스트블록 0 · 표 0 · 그림 1`
        인데 pdfium 으로 같은 쪽을 읽으면 **1,571자**가 멀쩡히 나온다.

        예전에는 이런 쪽이 한국어 OCR 재판독으로 흘러가 그럭저럭 글자가
        채워졌다(다만 46~70% 정확도라 `- 고위공무원단` 이 `공금운농-` 이
        됐다). 0.4.11 에서 OCR 오판을 고치자 **그 쪽이 통째로 비어**
        드러났다 — 조달청 76쪽 중 3쪽(1·4·5).

        뽑을 수 있는 글자가 있는데 인식으로 메우는 것은 손해다. 원본
        텍스트 레이어가 쓸 만하면 그것을 쓴다. **덮어쓰지 않고 비어 있는
        쪽만** 메운다 — docling 이 제대로 뽑은 쪽을 건드리지 않는다.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return 0

    thin = [p for p in pages
            if isinstance(p.page_no, int)
            and len((p.content or "").strip()) < MIN_PAGE_TEXT]
    if not thin:
        return 0

    try:
        document = pdfium.PdfDocument(str(pdf_path))
    except Exception as exc:                     # noqa: BLE001 - 메우기 실패는 치명적이지 않다
        _log.warning("텍스트 레이어로 메우지 못했습니다: %s", exc)
        return 0

    filled = 0
    try:
        for page in thin:
            index = page.page_no - 1
            if not 0 <= index < len(document):
                continue
            try:
                layer = document[index].get_textpage().get_text_range()
            except Exception:                    # noqa: BLE001 - 그 쪽만 실패
                continue
            if not _has_usable_text_layer(layer):
                continue                         # 진짜 그림 쪽이다 — 그대로 둔다
            from docstruct.text.korean_text import normalize_pdf_text

            text = normalize_pdf_text(layer).strip()
            if len(text) < MIN_PAGE_TEXT:
                continue
            # 그림 placeholder 는 지우지 않는다 — 그 쪽에 그림이 있었다는
            # 사실과 VLM 이 채울 자리를 잃으면 안 된다.
            page.content = f"{(page.content or '').strip()}\n\n{text}".strip()
            page.trace.add(
                "docstruct.pipeline", "텍스트 레이어로 메움",
                f"docling 이 {len((page.content or '')) - len(text)}자만 뽑아 "
                f"원본에서 {len(text)}자를 가져왔습니다",
                status="warn")
            filled += 1
    finally:
        document.close()
    if filled:
        _log.info("텍스트 레이어로 메운 쪽: %d개", filled)
    return filled


#: 이 비율 이상을 덮는 이미지가 있으면 "지면이 그림으로 들어온 쪽" 으로 본다.
#: 실측(주택과세금 전면 스캔본): 쪽마다 지면의 **80.6%** 를 덮는 이미지가
#: 하나씩 있었다. 텍스트 PDF 의 삽화는 이만큼 덮지 않는다.
MIN_SCAN_IMAGE_COVER = 0.5


def _has_page_image(document, index: int) -> bool:
    """이 쪽에 **읽을 그림이 있는가** — 지면을 덮는 이미지 객체를 찾는다.

    입력: document — 열린 pdfium 문서, index — 0부터 세는 쪽 번호
    출력: 지면의 MIN_SCAN_IMAGE_COVER 이상을 덮는 이미지가 있으면 True
    비고:
        **글자가 적다는 것만으로 스캔이라고 하면 안 된다** (0.4.68).
        실측(문체부 609쪽): 스캔으로 판정된 7쪽이 모두 **그림이 없는
        텍스트 쪽**이었다 — 표가 쪽을 넘어와 꼬리 조각만 남았거나
        (`| 고도화(정보화)(500) | 계 |`), 본문이 `ㅇ 해당사항 없음` 한 줄인
        쪽이었다. 텍스트 레이어에 11~79자가 멀쩡히 있었다.

        그것을 VLM 으로 다시 읽자 **있던 표 구조가 망가졌다**:

            원래(docling)  | 고도화(정보화)(500) | 계 |
            VLM 재판독      | 고도화(정보화)(500) | 계 | | |     ← 빈 열을 지어냈다
            원래(docling)  | 및 확산(정보화 | 화정보원 |
            VLM 재판독      | | 및<br>확산(정보화<br>) | ... |    ← 13열로 부풀었다

        읽을 그림이 없으면 재판독으로 얻을 것이 없다. 지면에 그려진 것이
        벡터 글자뿐이라면 텍스트 레이어가 이미 그 답이고, VLM 은 같은 것을
        다시 옮겨 적으며 구조를 새로 지어낼 뿐이다.

        대조군(행안부 p76·p243): 텍스트 레이어가 **0자**인 진짜 빈/스캔
        쪽이다. 이런 쪽은 이 검사와 무관하게 아래에서 따로 걸러진다.
    """
    try:
        import pypdfium2.raw as pdfium_c

        page = document[index]
        width, height = page.get_size()
        area = float(width) * float(height)
        if area <= 0:
            return True                          # 크기를 모르면 막지 않는다
        for obj in page.get_objects():
            if obj.type != pdfium_c.FPDF_PAGEOBJ_IMAGE:
                continue
            try:
                left, bottom, right, top = obj.get_bounds()
            except Exception:                    # noqa: BLE001 - 그 객체만 건너뛴다
                continue
            if (right - left) * (top - bottom) / area >= MIN_SCAN_IMAGE_COVER:
                return True
        return False
    except Exception as exc:                     # noqa: BLE001
        # **판정을 못 하면 막지 않는다.** 스캔본을 놓치면 본문을 잃지만
        # 반대 방향의 실수는 시간만 더 쓴다 (이 함수 밖의 원칙과 같다).
        _log.debug("지면 이미지 확인 실패 (%s쪽): %s", index + 1, exc)
        return True


def _pages_needing_ocr(pdf_path: Path, pages: list[PageContent]) -> set[int]:
    """원본 PDF 에서 다시 읽어야 할 쪽 번호를 가려낸다.

    입력: pdf_path — 원본 PDF, pages — 추출된 페이지 목록
    출력: 재판독이 필요한 쪽 번호 집합
    비고:
        판정을 **렌더보다 먼저** 한다. 렌더한 뒤에 판정하면 텍스트 PDF 도
        전 페이지를 렌더하고 나서 전부 건너뛰게 된다 — 155쪽 문서에서 50초쯤
        헛돈다. 판정에 필요한 것은 원본의 텍스트 레이어이지 렌더 이미지가
        아니다.

        원본을 읽지 못하면 **모든 쪽을 대상으로 본다.** 판정을 못 했다는
        이유로 스캔본을 건너뛰면 본문을 통째로 잃는다. 반대 방향의 실수는
        시간만 더 쓴다.
    """
    numbers = {p.page_no for p in pages if isinstance(p.page_no, int)}
    if _force_reread():
        return numbers

    try:
        import pypdfium2 as pdfium
    except ImportError:
        return numbers

    try:
        document = pdfium.PdfDocument(str(pdf_path))
    except Exception as exc:                     # noqa: BLE001 - 판정 실패는 치명적이지 않다
        _log.warning("텍스트 레이어를 확인하지 못했습니다 (모든 쪽 재판독): %s", exc)
        return numbers

    needed: set[int] = set()
    try:
        for page_no in sorted(numbers):
            index = page_no - 1
            if not 0 <= index < len(document):
                needed.add(page_no)
                continue
            try:
                layer = document[index].get_textpage().get_text_range()
            except Exception:                    # noqa: BLE001 - 그 쪽만 판정 실패
                needed.add(page_no)
                continue
            if _has_usable_text_layer(layer):
                continue
            # **글자가 없는 쪽**은 그대로 대상이다 — 진짜 스캔이거나 빈
            # 쪽이고, 어느 쪽이든 재판독이 손해를 만들지 않는다
            # (대조군: 행안부 p76·p243 이 0자였다).
            flat = re.sub(r"\s", "", re.sub(r"<[^>]+>|https?://\S+", "",
                                            layer or ""))
            if not flat:
                needed.add(page_no)
                continue
            # 글자가 조금이라도 있으면 **읽을 그림이 있을 때만** 다시 읽는다.
            if _has_page_image(document, index):
                needed.add(page_no)
            else:
                _log.debug("%s쪽: 글자가 적지만 지면 그림이 없어 그대로 씁니다",
                           page_no)
    finally:
        document.close()
    return needed


def _reread_with_korean_ocr(
    pages: list[PageContent], targets: set[int] | None = None
) -> int:
    """페이지 이미지를 한국어 OCR 로 다시 읽어 본문을 바꾼다.

    입력: pages — page_image_path 가 채워진 PageContent 목록
    출력: 바뀐 페이지 수
    비고:
        docling 이 쓰는 rapidocr 기본 모델(PP-OCRv6 small)에는 한국어가 없어
        한글 지면이 한자·가나로 나온다. 실제 문서에서 **한글 0%** 였다.
        한국어 모델로 다시 읽으면 46~70% 가 된다.

        **표 안 텍스트는 건드리지 않는다.** 표는 `<table N>` 자리표시자로
        본문과 분리되어 있고, 셀 텍스트 교체는 좌표 매칭이 필요해 별도
        단계에서 다룬다. 여기서는 표 밖 본문만 바꾼다.

        원본을 잃지 않도록, 새로 읽은 결과가 비어 있으면 그 페이지는
        그대로 둔다 — OCR 이 실패한 지면에서 있던 내용까지 지우면 안 된다.
    """
    from docstruct.text.korean_text import normalize_pdf_text
    from docstruct.converters.pdf.rapidocr_ko import read_page_text

    changed = 0
    for page in pages:
        image = page.page_image_path
        # 텍스트 레이어가 쓸 만한 쪽은 targets 에 없다. 스캔본과 텍스트 PDF 가
        # 섞여 들어오므로 페이지마다 판단해야 한다.
        if targets is not None and page.page_no not in targets:
            page.trace.add("converters.pdf.rapidocr_ko", "재판독 생략",
                           "텍스트 레이어를 그대로 씁니다")
            continue
        if not image or not Path(image).is_file():
            continue
        try:
            text = read_page_text(image)
        except Exception as exc:                 # noqa: BLE001 - 한 쪽 실패로 멈추지 않는다
            _log.warning("%s쪽 한국어 OCR 실패: %s", page.page_no, exc)
            page.trace.add("converters.pdf.rapidocr_ko", "한국어 OCR 실패",
                           str(exc)[:120], status="warn")
            continue
        if not text.strip():
            continue
        if page.ocr_engine and page.ocr_engine != "rapidocr":
            # **이미 읽힌 쪽은 덮지 않는다** (0.4.60). 0.4.56 부터 폴백이
            # 쪽 단위라 두 판독이 같은 실행에서 함께 도는데, 그때 뒤엣것이
            # 앞엣것을 밀어내면 **더 나은 판독이 더 나쁜 것으로 바뀐다.**
            # 대상 집합(`ocr_targets`)이 이미 차집합이라 여기 올 일이
            # 없어야 하지만, 그 계산이 어긋나도 결과가 나빠지지 않게
            # 마지막 자리에서 한 번 더 막는다.
            page.trace.add("converters.pdf.rapidocr_ko", "재판독 생략",
                           f"이미 {page.ocr_engine} 로 읽은 쪽입니다")
            continue

        placeholders = _TABLE_TAG_RE.findall(page.content or "")
        body = normalize_pdf_text(text)
        # 표 자리표시자는 그대로 살려 뒤 단계(표 판정·재추출)가 찾을 수 있게 한다.
        page.content = ("\n\n".join([body, *placeholders]) if placeholders else body)
        page.ocr_engine = "rapidocr"
        page.trace.add("converters.pdf.rapidocr_ko", "한국어 OCR 재판독",
                       f"본문 {len(body)}자 · 엔진 rapidocr")
        changed += 1
    return changed


#: 본문에 박힌 표 자리표시자 (`<table 3>` 등).
_TABLE_TAG_RE = re.compile(r"<table \d+>")


def _reread_tables_with_korean_ocr(
    pages: list[PageContent], *, scale: float, targets: set[int] | None = None
) -> int:
    """표 셀 텍스트를 한국어 OCR 결과로 바꾼다.

    입력: pages — page_image_path 가 채워진 PageContent 목록, scale — 렌더 배율
    출력: 바뀐 표 수
    비고:
        본문은 `_reread_with_korean_ocr` 가 바꾸지만 표 안은 docling 이 넣은
        값(중국어)이 남는다. 셀 bbox 와 OCR 조각 bbox 를 겹쳐 어느 셀에
        속하는지 정하고, **행·열·병합은 그대로 둔 채 텍스트만** 바꾼다.

        새 텍스트가 비면 원래 표를 남긴다 — OCR 이 못 읽은 표까지 지우면
        있던 내용을 잃는다.
    """
    from docstruct.tables.docling import docling_table_to_markdown, replace_cell_texts

    changed = 0
    for page in pages:
        image = page.page_image_path
        if targets is not None and page.page_no not in targets:
            continue
        if not image or not page.tables or not Path(image).is_file():
            continue
        for table in page.tables:
            item = table.source_item
            if item is None:
                continue
            try:
                stat = replace_cell_texts(item, image, scale=scale)
            except Exception as exc:             # noqa: BLE001 - 한 표 실패로 멈추지 않는다
                _log.warning("%s 셀 교체 실패: %s", table.id, exc)
                continue
            if not stat["changed"]:
                continue
            rebuilt = docling_table_to_markdown(item)
            if not rebuilt.strip():
                continue
            table.markdown = rebuilt
            changed += 1

            # near_miss 와 empty_cells 를 나눠 기록한다. 표 밖 본문 조각까지
            # 세면(outside) 수치가 커져 원인을 가린다 — 실제로 표 하나에
            # 81개가 잡혀 매칭이 실패한 것처럼 보였다.
            detail = f"{table.id} · 셀 {stat['changed']}개 교체"
            if stat["empty_cells"]:
                detail += f", 빈 셀 {stat['empty_cells']}"
            if stat["near_miss"]:
                detail += f", 표 안 미배정 {stat['near_miss']}"
            page.trace.add("docstruct.tables.docling", "표 셀 한국어 재판독", detail)
    return changed


def _flag_broken_tables(pages: list[PageContent]) -> int:
    """빈 칸이 많은 표를 표시한다.

    입력: pages — TableInfo.source_item 이 채워진 페이지 목록
    출력: 표시한 표 수
    비고:
        **처음에는 이것을 "격자 결함" 으로 읽었으나 오판이었다.** docling 은
        값이 없는 칸에 TableCell 객체를 만들지 않으므로, 덮이지 않은 칸은
        원본에서 비어 있던 자리다. 표 세 개를 확인하니 `text` 가 빈 셀이
        **0개**였다 — 셀이 있으면 반드시 값이 있다.

        텍스트 PDF 에서 표 17개 중 14개(82%)가 이 표시를 받았는데, 렌더
        결과를 보면 모두 정상이었다. 값이 없는 칸이 있을 뿐이다.

        그래서 기본으로 끈다. 빈 칸 비율 자체는 표를 훑을 때 참고가 되므로
        기능은 남긴다.

        **격자 크기가 원본과 다른 경우는 이것으로 못 잡는다.** `num_rows`
        안에서만 세기 때문이다. 스캔본에서 13행 표가 7행으로 인식된 사례가
        그렇고, 그때는 오히려 비율이 낮게 나온다.
    """
    from docstruct.tables.docling import empty_cell_ratio

    flagged = 0
    for page in pages:
        for table in page.tables:
            item = table.source_item
            if item is None:
                continue
            stat = empty_cell_ratio(item)
            if not stat["empty"]:
                continue
            table.structure_ratio = round(stat["ratio"], 3)
            flagged += 1
            page.trace.add(
                "docstruct.tables.docling", "표 빈 칸",
                f"{table.id} · {stat['declared']}칸 중 {stat['empty']}칸이 "
                f"비어 있습니다 ({stat['ratio']:.0%})")
    return flagged


def _flag_ocr_language(pages: list[PageContent]) -> int:
    """한글 지면이 한자로 나온 표를 표시한다 (OCR 언어 오판).

    입력: pages — 페이지 목록 (제자리 갱신)
    출력: 표시한 표 수
    비고:
        텍스트 PDF 안에 래스터로 붙은 쪽(조직도 등)은 docling 내장 OCR 이
        읽고, 그 기본 모델은 중국어다 — 실측(조달청 9쪽): 한자 비율 0.83.
        그 쪽의 **원본 텍스트 레이어에는 한자가 없어** 쪽 단위 판정이
        통과해 버리므로, 결과물을 보는 이 검사가 사각을 메운다.
        표시만 하고 고치지 않는다 — 복구는 VLM 재구성이 맡는다.
    """
    from docstruct.converters.pdf.ocr_language import wrong_language

    flagged = 0
    for page in pages:
        for table in (page.tables or []):
            verdict = wrong_language(table.markdown)
            if not verdict:
                continue
            table.ocr_language_doubt = verdict
            flagged += 1
            page.trace.add(
                "converters.pdf.ocr_language", "한자로 나온 표",
                f"{table.id} · 한자 {verdict['han']}/{verdict['words']}자 "
                f"(비율 {verdict['ratio']}) — 중국어 모델 OCR 로 보입니다",
                status="warn")
    return flagged


def _flag_odd_tables(pages: list[PageContent]) -> int:
    """같은 서식 표 중 열 수가 다른 것을 표시한다.

    입력: pages — 표가 담긴 페이지 목록
    출력: 표시한 표 수
    비고:
        표 하나만 보고는 구조가 깨졌는지 알기 어렵다. 빈 칸 비율을 써 봤으나
        **정상 표를 82% 나 잡아** 쓸 수 없었다.

        정부 문서는 같은 서식 표를 여러 쪽에 반복한다. 실제 문서에서 헤더가
        같은 표 12개 중 11개가 8열이고 하나만 7열이었는데, 그 하나가 헤더 두
        칸을 뭉친 표였다. 다수와 다르면 그것이 틀렸을 가능성이 높다.
    """
    from docstruct.tables.odd_tables import find_odd_tables

    odd = find_odd_tables(pages)
    for page, table, width, majority in odd:
        table.structure_ratio = None       # 빈 칸 비율과 다른 지표다
        table.odd_columns = (width, majority)
        page.trace.add(
            "docstruct.tables.odd_tables", "표 서식 불일치",
            f"{table.id} · {width}열 — 같은 서식 표 다수는 {majority}열입니다",
            status="warn")
    return len(odd)


def _rebuild_broken_grids(pages: list[PageContent], *, scale: float) -> int:
    """격자 결함이 표시된 표를 OCR 좌표로 다시 세운다.

    입력: pages — 결함이 표시된 PageContent 목록, scale — 렌더 배율
    출력: 다시 세운 표 수
    비고:
        표 구조 인식이 행·열을 놓친 표는 좌표 매칭으로 고칠 수 없다 —
        없는 칸에 값을 넣을 수는 없다. 조각 좌표를 y·x 축에 투영해 빈
        구간으로 격자를 다시 만든다.

        **원본보다 작아지면 되돌린다.** 격자 재구성은 병합을 복원하지
        못하므로, 병합이 많은 표는 원본 구조가 더 나을 수 있다.
    """
    from docstruct.converters.pdf.cell_match import Box, box_of, from_pixels
    from docstruct.converters.pdf.rapidocr_ko import read_image
    from docstruct.tables.grid_rebuild import rebuild

    def _cell_count(markdown: str) -> int:
        """markdown 표의 내용 있는 칸 수."""
        lines = [ln for ln in (markdown or "").splitlines()
                 if ln.startswith("|") and set(ln.strip()) - set("|-: ")]
        return sum(1 for ln in lines for c in ln.strip("|").split("|") if c.strip())

    def _has_merges(table) -> bool:
        """원본 표에 병합 셀이 있는지.

        입력: table — TableInfo
        출력: `row_span`·`col_span` 이 1보다 큰 셀이 있으면 True
        비고:
            **병합이 있으면 재구성하지 않는다.** 좌표 격자는 병합을
            표현하지 못해 값의 귀속이 바뀔 수 있다 — 두 행이 공유하던
            값이 한 행만의 것으로 읽힌다. 그 문제를 0.1.75 에서 `〃`
            표기로 고쳤는데, 재구성이 그것을 되돌리게 된다.

            성과계획서처럼 병합이 많은 문서가 주 대상이므로 보수적으로
            간다. 병합 없는 표(괘선이 연해 행을 놓친 경우)만 고친다.
        """
        item = getattr(table, "source_item", None)
        cells = getattr(getattr(item, "data", None), "table_cells", None) or []
        return any(int(getattr(c, "row_span", 1) or 1) > 1
                   or int(getattr(c, "col_span", 1) or 1) > 1 for c in cells)

    changed = 0
    for page in pages:
        image = page.page_image_path
        targets = [t for t in page.tables if t.structure_ratio and t.bbox]
        if not image or not targets or not Path(image).is_file():
            continue
        try:
            lines = read_image(image)
        except Exception as exc:                 # noqa: BLE001 - 한 쪽 실패로 멈추지 않는다
            _log.warning("%s쪽 격자 재구성용 OCR 실패: %s", page.page_no, exc)
            continue
        fragments = [(from_pixels(box_of(ln.box), scale), ln.text)
                     for ln in lines if ln.box]

        for table in targets:
            if _has_merges(table):
                page.trace.add(
                    "docstruct.tables.grid_rebuild", "격자 재구성 건너뜀",
                    f"{table.id} · 병합 셀이 있어 원본 구조를 지킵니다")
                continue
            area = Box(float(table.bbox["l"]), float(table.bbox["t"]),
                       float(table.bbox["r"]), float(table.bbox["b"]))
            inside = [(box, text) for box, text in fragments
                      if box.overlap_ratio(area) >= 0.5]
            rebuilt = rebuild(inside)
            if not rebuilt:
                continue
            before, after = _cell_count(table.markdown), _cell_count(rebuilt)
            if after <= before:
                page.trace.add(
                    "docstruct.tables.grid_rebuild", "격자 재구성 폐기",
                    f"{table.id} · 칸 {before} → {after} 로 늘지 않아 원본을 유지합니다",
                    status="warn")
                continue
            table.original_markdown = table.original_markdown or table.markdown
            table.markdown = rebuilt
            table.structure_ratio = None         # 결함이 해소됐다
            changed += 1
            page.trace.add(
                "docstruct.tables.grid_rebuild", "격자 재구성",
                f"{table.id} · 좌표로 다시 세움 (칸 {before} → {after})")
    return changed


def build_document(
    path: str | Path,
    *,
    split_chars: int = 0,
    assess_tables: bool = True,
    fill_tables: bool = True,
    fill_all: bool = False,
    read_pictures: bool = True,
    render_pages: bool = True,
    render_all: bool = False,
    out_dir: str | Path | None = None,
    render_scale: float = 2.0,
    source_filename: str | None = None,
    progress: bool = False,
) -> PageDocument:
    """문서 파일 하나를 구조화한다.

    입력:
        path          문서 경로 (.pdf | .hwp | .hwpx)
        split_chars   쪽 경계가 없는 문서를 이 글자 수로 조각낸다 (0 이면 나누지 않음)
        out_dir       산출물 디렉터리. None 이면 렌더·이미지 저장 생략
        assess_tables LLM 표 판정 수행 여부
        fill_tables   판정 결과에 따른 표 재추출 수행 여부
        fill_all      quality 와 무관하게 모든 표 재추출
        read_pictures 텍스트 레이어가 없는 그림을 VLM 으로 읽을지
                      (캡처 이미지로 붙인 표·조직도 복원)
        render_pages  페이지 PNG 렌더 여부 (PDF 만 해당)
        render_all    표가 없는 쪽까지 전부 렌더할지
        render_scale  렌더 배율
        source_filename  결과에 적을 원본 파일 이름 — 임시 경로로 받은 파일을
                      원래 이름으로 남길 때 (없으면 경로의 이름)
        progress      단계별 진행 막대 표시 여부
    출력:
        PageDocument
    부수효과:
        out_dir/pages/*.png, out_dir/images/*.png 생성
    """
    get_settings()   # .env 로드 + 설정 확정 (최초 1회, 이후 캐시)

    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        if resolved.is_dir():
            # 폴더를 주는 실수가 잦다. 어디로 가야 하는지 알려준다.
            raise IsADirectoryError(
                f"폴더가 주어졌습니다: {resolved}\n"
                "  build_document / DocStruct 는 문서 하나만 처리합니다.\n"
                "  폴더는 DocStructBatch 를 쓰세요.\n"
                "\n"
                "    from docstruct import DocStructBatch\n"
                f"    DocStructBatch({str(resolved)!r}, pattern='*.pdf').run()\n"
                "\n"
                "  CLI 라면 그대로 폴더를 주면 됩니다.\n"
                f"    docstruct {resolved.name}/ --glob '*.pdf'"
            )
        raise FileNotFoundError(f"파일을 찾을 수 없습니다: {resolved}")

    fmt = source_format(resolved)
    display_name = (source_filename or resolved.name).strip() or resolved.name

    # ═══ 구간 0 — 입력 확인·작업 폴더 ═══════════════════════════════════════
    # 경로 해석, 형식 판별(source_format · 시그니처), 산출 폴더·scratch 준비.
    report("open")
    out_path = Path(out_dir).resolve() if out_dir is not None else None
    # out_dir 을 주지 않아도 그림·페이지 PNG 는 저장한다. 저장하지 않으면
    # preview 와 document.md 가 아무것도 보여주지 못하고(파일 경로를 참조),
    # 표 재추출은 근거 이미지가 없어 통째로 무력화된다.
    # 임시 폴더에 두고 경로를 알려준다 — save()/to_json() 때 함께 옮겨진다.
    #
    # 임시 폴더는 PageDocument 가 사라질 때 같이 지운다 (아래 _bind_scratch).
    # 프로세스 종료까지 남겨두면 배치 N 건이 N 개의 폴더를 남긴다.
    scratch: Path | None = None
    if out_path:
        image_dir = out_path / "images"
    else:
        scratch = Path(tempfile.mkdtemp(prefix="docstruct-"))
        image_dir = scratch / "images"

    timings: dict[str, float] = {}   # 라벨은 STAGE_* 상수를 씁니다
    _counter = _counted(display_name, fmt)
    concurrency_peak = _counter.__enter__()

    # ═══ 구간 1 — 추출 [형식 축] ═══════════════════════════════════════════
    # extractors/<fmt> 가 ExtractionResult(pages·images·layout) 를 낸다.
    # 여기까지가 "형식별" 코드이고, 아래는 형식과 무관한 인식 단계다.
    report("extract")
    # **이 형식이 지나가지 않는 단계를 먼저 알린다** (0.4.94). 목록에서
    # 지우지 않는다 — HWP 에서 표 단계가 왜 없는지가 진행 화면의 가장
    # 쓸모 있는 정보다. 사라지면 "안 본 것" 과 "볼 것이 없었던 것" 을
    # 가릴 수 없다.
    _report_unused_steps(fmt)
    _t = time.perf_counter()
    extraction = _extract(resolved, fmt, image_dir)
    pages, failed_pages, table_html = (
        extraction.pages, extraction.failed_pages, extraction.table_html
    )
    timings[stage_extract(fmt)] = time.perf_counter() - _t

    # docling 이 놓친 쪽을 원본 텍스트 레이어로 메운다 (OCR 보다 먼저).
    if fmt == "pdf":
        _rescue_thin_pages(resolved, pages)

    # 페이지 경계가 없는 문서(HWP 등)를 구조 경계에서 나눈다.
    if split_chars > 0 and pages:
        from docstruct.text.split import split_document

        pages = split_document(pages, split_chars)
    _log.info("추출 완료: %d페이지, 표 %d개", len(pages), sum(len(p.tables) for p in pages))

    # **게이트는 확장자가 아니라 손에 든 재료로 정한다** (0.5.2).
    # HWP 를 HWPX 로 바꿔 읽으면 `cells` 가 생기는데, `fmt` 는 여전히
    # "hwp" 라 격자 실험이 전부 막혔다 — 변환한 이유가 §6 의 공백을 닫는
    # 것이었는데 게이트가 그대로 닫고 있었다. 결과물의 `source_format` 은
    # 입력 그대로 "hwp" 다(그것이 사실이므로). 게이트만 바꾼다.
    gate_fmt = _gate_format(fmt, pages)
    if gate_fmt != fmt:
        _log.info("%s 를 %s 경로로 읽어 %s 재료를 얻었습니다 — 실험 게이트를 "
                  "%s 기준으로 봅니다", fmt, "hwp2hwpx→hwpx", "cells", gate_fmt)
    _warn_if_empty(display_name, pages)

    doc = PageDocument(
        filename=display_name,
        source_format=fmt,
        pages=pages,
        failed_pages=failed_pages,
        failure_reasons=list(getattr(extraction, "failure_reasons", []) or []),
    )

    # ═══ 구간 2 — 스캔 쪽 판별·지면 렌더 [텍스트 축 · PDF] ══════════════════
    # 텍스트 레이어가 없는 쪽(_pages_needing_ocr)을 고르고, 표 판정·VLM 판독의
    # 시각 근거로 쪽 PNG 를 목적별 배율로 그린다(_render_page_images).
    report("scan_detect", total=len(pages))
    korean_ocr = bool(get_settings().korean_ocr)
    ocr_targets: set[int] | None = None
    if fmt == "pdf" and korean_ocr:
        # 렌더보다 먼저 판정한다. 나중에 하면 텍스트 PDF 도 전 페이지를
        # 렌더하고 나서 전부 건너뛴다.
        ocr_targets = _pages_needing_ocr(resolved, pages)
        if not ocr_targets:
            _log.info("텍스트 레이어가 온전해 한국어 OCR 을 건너뜁니다")

    if fmt == "pdf" and (render_pages or ocr_targets):
        # out_dir 이 없으면 임시 작업 폴더에 렌더한다. 여기서 건너뛰면
        # 재추출이 근거 이미지를 못 찾아 조용히 무력화된다.
        #
        # 한국어 OCR 로 다시 읽을 때는 표 없는 페이지도 필요하므로 전
        # 페이지를 렌더한다 — 기본은 표가 있는 페이지만이다.
        pages_dir = (out_path / "pages") if out_path else (scratch / "pages")  # type: ignore[operator]
        _t = time.perf_counter()
        # **목적별로 나눠 그린다.** 예전에는 스캔 쪽이 하나라도 있으면
        # `all_pages=True` 로 **문서 전체**가 300dpi 로 그려졌다. 그 쪽에
        # 그럴 이유가 있어서가 아니라 같은 문서에 스캔 쪽이 섞여 있어서다
        # — 스캔 쪽이 없는 문서를 돌리면 같은 표가 144dpi 근거로 떨어진다.
        # **같은 코드가 문서 사정에 따라 다르게 동작하는 것**이라, A/B
        # 이전에 그 자체로 결함이다.
        #
        #   표 평가·재추출 근거   render_scale        (기본 2.0 = 144dpi)
        #   스캔 쪽 판독          scan_render_scale() (기본 4.17 ≈ 300dpi)
        _render_page_images(resolved, pages, pages_dir, scale=render_scale,
                            all_pages=render_all)
        if ocr_targets:
            # 스캔 쪽만 고해상도로 덮어 그린다. OCR·VLM 모두 지면 이미지를
            # 입력으로 쓰므로 해상도가 곧 정확도이고, 기본 144dpi 는 스캔
            # 원본(145~148dpi)과 겹쳐 여유가 없다.
            #
            # **배율은 쪽마다 정한다.** 원본이 이미 300dpi 이상이면 그
            # 해상도에 맞추고(있는 정보를 버리지 않는다), 낮으면 300 까지
            # 올린다 — 배율은 원본에 없는 정보를 만들지 못하므로 무턱대고
            # 키우는 것은 흐림만 키운다.
            #
            # **같은 배율끼리 묶어 한 번에 그린다.** 쪽마다 따로 부르면
            # pdfium 문서 열기가 대상 수만큼 반복된다 — 전면 스캔본에서
            # 수백 번이다. 배율이 같은 쪽은 한 호출로 충분하다.
            by_scale: dict[float, set[int]] = {}
            for target in sorted(ocr_targets):
                key = round(page_scan_scale(resolved, target), 2)
                by_scale.setdefault(key, set()).add(target)
            for key, group in sorted(by_scale.items()):
                _render_page_images(resolved, pages, pages_dir,
                                    scale=key, all_pages=True, only=group)
        timings[STAGE_RENDER] = time.perf_counter() - _t

    #: 이 실행에서 스캔으로 판정돼 다시 읽은(읽으려 한) 쪽 — VLM·OCR 공통.
    #: `ocr_targets` 는 아래에서 소비되며 줄어들므로, OCR 검증(⑦⑧)의
    #: 게이트는 이 원본 집합을 본다. 예전에는 VLM 이 읽고 나면
    #: `ocr_targets=None` 이 돼 **검증이 켜져 있어도 절대 돌지 않았다** —
    #: 수치 오독(연 1천분의 29 → 2.9) 위험은 VLM 판독도 마찬가지인데,
    #: 기본 경로(0.4.17 VLM)에서 verify→reread 2단이 통째로 죽어 있었다.
    scanned_pages: set[int] = set(ocr_targets or ())

    # ═══ 구간 3 — 스캔 쪽 본문 판독 [텍스트 축 · PDF] ═════════════════════
    # text/scan_vlm(VLM) 또는 converters/pdf/rapidocr_ko(한국어 OCR) 로 본문을
    # 다시 읽는다. 어느 쪽이 읽었는지 page.ocr_engine 에 남는다.
    report("scan_read")
    if fmt == "pdf" and ocr_targets and scan_backend() == "vlm":
        # **VLM 으로 읽는다.** rapidocr 는 한국어 모델을 붙여도 46~70% 이고
        # 한자가 섞인다 — `- 고위공무원단` 이 `공금운농-` 이 됐다.
        from docstruct.text.scan_vlm import read_scanned_pages

        _t = time.perf_counter()
        read_pages = read_scanned_pages(pages, ocr_targets)
        if read_pages:
            _log.info("VLM 으로 다시 읽은 쪽: %d개", len(read_pages))
            # **읽은 쪽만 뺀다.** 예전에는 한 쪽이라도 읽히면 전체를
            # 비웠는데, 그러면 VLM 이 실패·무응답으로 물러난 쪽이
            # rapidocr 폴백을 영영 못 받는다 — 쪽별 trace 는 "rapidocr 로
            # 넘어갑니다" 라고 적으면서 실제로는 넘어가지 않았다.
            ocr_targets = ocr_targets - read_pages
            if ocr_targets:
                _log.info("VLM 이 물러난 %d쪽은 rapidocr 로 넘어갑니다",
                          len(ocr_targets))
        else:
            _log.info("VLM 판독 결과가 없어 rapidocr 로 넘어갑니다")
        # **두 판독의 시간을 따로 적는다** (0.4.60). 예전에는 둘 다 같은
        # 칸에 썼는데, 0.4.56 에서 쪽별 폴백이 되면서 **두 분기가 함께
        # 도는 일이 생겼고 뒤엣것이 앞엣것을 덮었다.** 실측(주택과세금
        # 377쪽): `한국어 OCR 재판독 18.97초` 로 찍혔는데 그것은 rapidocr
        # 24쪽 시간이고, VLM 353쪽 판독 시간은 어디에도 남지 않았다 —
        # 가장 오래 걸리는 단계가 계측에서 사라진 셈이다.
        timings[STAGE_SCAN_VLM] = time.perf_counter() - _t

    if fmt == "pdf" and ocr_targets:
        _t = time.perf_counter()
        changed = _reread_with_korean_ocr(pages, ocr_targets)
        tables_changed = _reread_tables_with_korean_ocr(
            pages, scale=render_scale, targets=ocr_targets)
        timings[STAGE_KOREAN_OCR] = time.perf_counter() - _t
        _log.info("한국어 OCR 재판독: 본문 %d쪽 · 표 %d개", changed, tables_changed)

    # ═══ 구간 4 — 표 표시 단계 [표 축 · 바꾸지 않는다] ═══════════════════════
    # 깨진 표·OCR 언어·쪽 넘김 이어짐·그래프 읽기를 **표시만** 한다.
    # markdown 은 손대지 않는다 — 바꾸는 단계는 구간 5·6 이다.
    report("table_mark")
    if fmt == "pdf" and get_settings().flag_broken_tables:
        # 좌표 매칭이 끝난 뒤에 잰다. 셀이 있는데도 안 채워진 것과 셀 자체가
        # 없는 것은 다른 문제이고, 뒤엣것만 VLM 으로 고칠 수 있다.
        broken = _flag_broken_tables(pages)
        if broken:
            _log.info("격자 결함이 있는 표 %d개", broken)

    if fmt == "pdf":
        # 병합 정보를 JSON 에 담는다. markdown 은 span 을 표현하지 못해
        # 병합 셀 값이 한 칸에만 남는다.
        from docstruct.tables.docling import cell_grid

        for page in pages:
            for table in page.tables:
                if table.source_item is not None and table.cells is None:
                    table.cells = cell_grid(table.source_item)

    if fmt == "pdf" and get_settings().mark_table_continuation:
        from docstruct.tables.continued import mark_continuations

        got = mark_continuations(pages)
        if got:
            _log.info("이어짐으로 표시한 표 %d개", got)

    if fmt == "pdf" and get_settings().read_charts:
        from docstruct.images.chart_read import read_charts

        got = read_charts(pages, progress=progress)
        if got:
            _log.info("VLM 으로 읽은 그래프 %d개", got)

    # 실험 기법은 등록된 것 중 켜진 것만 돌린다. 하나가 깨져도 본체는
    # 계속 간다 — 검증 전 코드가 파이프라인을 멈추게 하면 안 된다.
    #
    # ═══ 구간 5 — 실험 사다리 [표 축] ══════════════════════════════════════
    # experiments/registry 의 기본 켬·--exp 로 켠 기법을 등록 순서대로 돈다.
    # 실험 하나가 실패해도 다음으로 넘어가고 결과물(trace)에 남긴다.
    # **표를 바꾸는 단계(rebuild_grid·vlm_fix_tables)보다 먼저** 둔다.
    # 실험은 원본 구조를 진단하는 것이므로, 다시 만든 표를 보면 무엇을
    # 재는지 알 수 없게 된다.
    report("experiments")
    from docstruct.experiments import enabled_experiments
    def _run_experiments(stage: str) -> None:
        """이 자리에서 돌 실험을 돌린다.

        입력: stage — "tables" | "images"
        출력: 없음 (표·쪽·그림에 기록만 남긴다)
        비고:
            **형식이 맞지 않으면 알린다** (0.4.58). 예전에는 조용히
            건너뛰어, `--exp col_grid` 를 HWPX 에 주고 실험이 돈 줄 알고
            결과를 비교하는 일이 실제로 있었다.
        """
        skipped: list[str] = []
        barren: list[tuple[str, tuple[str, ...]]] = []
        ran = 0
        for experiment in enabled_experiments(stage):
            if gate_fmt not in experiment.formats:
                reason = (f"{experiment.key} 는 {gate_fmt} 형식에 적용되지 "
                          f"않습니다 (대상: {'·'.join(experiment.formats)})")
                skipped.append(experiment.key)
                # **결과물에도 남긴다.** 로그는 흘러가지만 document.json 은
                # 남는다 — 나중에 결과만 보고 "이 실험이 돌았나" 를 가려야
                # 한다. 첫 쪽에 한 번만 적는다.
                if pages:
                    pages[0].trace.add("docstruct.experiments", "실험 건너뜀",
                                       reason, status="warn")
                continue
            if experiment.run is None:
                continue
            # 형식은 통과했지만 **재료가 없는** 실험 — 돌긴 돌고 빈손이다.
            lacking = experiment.missing_for(gate_fmt)
            if lacking:
                barren.append((experiment.key, lacking))
            try:
                hits = experiment.run(pages, pdf_path=resolved)
            except Exception as exc:             # noqa: BLE001
                _log.warning("실험 %s 실패: %s", experiment.key, exc)
                continue
            if hits:
                _log.info("실험 %s — %d건", experiment.key, hits)
            ran += 1
            report_progress(ran)
        # **돌긴 도는데 재료가 없는 실험** 을 따로 알린다 (0.4.90).
        # `formats` 는 "돌아도 되는가" 만 정하고, 재료가 있는지는 말하지
        # 않는다. 그래서 HWP 에서 `hole_fill` 은 통과했지만 `cells` 가
        # 없어 **아무 일도 하지 않고 아무 말도 없었다** — 실험_총정리 §6
        # 이 "조용히 비켜 간다" 고 적은 그 자리다. 이제 소리가 난다.
        if barren:
            _log.info(
                "%s 에는 %s 이(가) 없어 다음 실험은 돌아도 빈손입니다: %s "
                "(docstruct --guide '%s 표' 로 어디를 고쳐야 하는지 봅니다)",
                gate_fmt, "·".join(sorted({m for _k, miss in barren for m in miss})),
                ", ".join(key for key, _miss in barren), gate_fmt,
            )
        if skipped:
            # **한 줄로 모은다** (0.4.86). 기본 켬이 늘어 형식이 안 맞는
            # 실험도 늘었고, 문서마다 WARNING 이 여덟 줄씩 쌓여 일괄 처리
            # 로그에서 진짜 경고가 묻혔다. 건별 사유는 그대로 trace 에 남는다.
            _log.info("%s 형식에 해당 없는 실험 %d개를 건너뜁니다: %s",
                      gate_fmt, len(skipped), ", ".join(skipped))

    _run_experiments("tables")

    # ═══ 구간 6 — 표 재구성 [표 축 · 바꾼다] ════════════════════════════════
    # 서식 이상 표시 → OCR 좌표 격자 재구성(tables/grid_rebuild) → VLM 재구성
    # (tables/vlm_rebuild). 바꾼 표는 original_markdown 으로 되짚을 수 있다.
    report("table_rebuild")
    if fmt == "pdf" and get_settings().flag_odd_tables:
        from docstruct.images.picture_tables import link_tables_in_pictures

        nested = link_tables_in_pictures(pages)
        if nested:
            _log.info("그림 안에 있는 표 %d개 — 글자가 OCR 산물입니다", nested)
        doubted = _flag_ocr_language(pages)
        if doubted:
            _log.info("한자로 나온 표 %d개 — VLM 재구성 대상에 넣습니다", doubted)
        odd = _flag_odd_tables(pages)
        if odd:
            _log.info("서식이 어긋난 표 %d개", odd)

    if fmt == "pdf" and get_settings().rebuild_grid:
        # VLM 보다 먼저 시도한다. 좌표 재구성은 결정적이고 비용이 없으며,
        # 성공하면 결함 표시가 지워져 VLM 대상에서 빠진다.
        _t = time.perf_counter()
        regrid = _rebuild_broken_grids(pages, scale=render_scale)
        timings[STAGE_GRID_REBUILD] = time.perf_counter() - _t
        if regrid:
            _log.info("격자를 다시 세운 표 %d개", regrid)

    if fmt == "pdf" and get_settings().vlm_fix_tables:
        from docstruct.tables.vlm_rebuild import rebuild_broken_tables

        _t = time.perf_counter()
        fixed = rebuild_broken_tables(pages, progress=progress)
        timings[STAGE_TABLE_REBUILD] = time.perf_counter() - _t
        if fixed:
            _log.info("VLM 으로 다시 만든 표 %d개", fixed)

    # ═══ 구간 7 — 그림 판독 [그림 축] ══════════════════════════════════════
    # images/picture 가 그림마다 경로(표·글·그래프)를 고르고 images/vlm_read·
    # chart_read 가 읽는다. 결과는 ImageInfo 와 본문 주석으로 남는다.
    report("picture", total=sum(len(p.images) for p in pages))
    if read_pictures and any(p.images for p in pages):
        from docstruct.images.vlm_read import read_picture_regions

        t0 = time.perf_counter()
        count = read_picture_regions(pages, progress=progress)
        if count:
            timings[STAGE_PICTURE_READ] = time.perf_counter() - t0
            _log.info("그림 %d개의 내용을 VLM 으로 읽었습니다", count)

    # **그림이 남긴 것을 보는 실험은 여기서 돈다** (0.4.63). 위의 사다리
    # 자리는 그림 판독보다 앞이라, `legibility`·`transcribed` 를 보는
    # 실험이 거기서 돌면 빈손이 된다 — 실측(조달청): `--exp chart_gate`
    # 기록이 0건이었고, 실험은 돌았으므로 로그에도 아무 말이 없었다.
    # ═══ 구간 8 — 상시 검사 [표 축 · 정답 없이 재는 것] ══════════════════════
    # 격자 결함(grid_check)·셀 오염(leak_check→repair_leaks) — 실험이 아니라
    # 매번 돌고, 고친 것은 cells·markdown·본문 블록 셋에 같이 반영한다.
    # **격자 온전성은 실험이 아니라 상시 검사다** (0.4.69). 정답이 없어도
    # 확실하고(표 자신의 내적 모순), 고치지 않으며(표시만), 형식을 가리지
    # 않는다 — HWPX·PDF 를 같은 잣대로 본다. 표가 다 확정된 뒤에 한 번 센다.
    report("integrity", total=sum(len(p.tables) for p in pages))
    from docstruct.structuring.checks import (cell_text_diff, grid_check,
                                              leak_check, patch_markdown_cells,
                                              repair_leaks)
    from docstruct.tables.tags import sync_table_block

    faulty = leaked = repaired = 0
    checked = 0
    for page in pages:
        for table in page.tables:
            checked += 1
            if checked % 25 == 0:
                report_progress(checked)
            # **새어 든 글자를 먼저 지운다** (0.4.80). 그 글자는 앞 칸에
            # 이미 있는 **중복**이므로 지우면 원래 값으로 돌아간다 —
            # 은폐가 아니라 복원이다. 원본 대조로 확인했다: 오염 표의 셀이
            # 원본과 글자까지 일치하는 비율이 **79~81% → 96~98%**.
            #
            # 한 표에서 두 번 이상 같은 모양일 때만 고친다(`repair_leaks`).
            # 한 번은 우연일 수 있고, 실측에서 남은 것은 전부 그런
            # 오탐이었다(`회 계` 다음 칸이 `계 정` — 진짜 표 머리다).
            #
            # **markdown 도 함께 고친다** (0.4.83). 0.4.80 은 `cells` 만
            # 고쳐서 trace 는 "복원" 이라 적는데 document.md 와 JSON 의
            # `markdown` 에는 `8 66,578` 이 그대로 나갔다 — 결과물의 두
            # 필드가 다른 말을 하는 상태였다. 고치기 전 값을 찍어 두고
            # 차이를 markdown 과 본문 `<table N>` 블록에 칸 단위로 반영한다.
            snapshot = {(c.get("row", 0), c.get("col", 0)): (c.get("text") or "").strip()
                        for c in (table.cells or [])}
            got_fix = repair_leaks(table.cells)
            if got_fix:
                repaired += got_fix
                table.cell_leaks = {"repaired": got_fix}
                pairs = cell_text_diff(snapshot, table.cells)
                table.markdown, in_md = patch_markdown_cells(table.markdown, pairs)
                if in_md:
                    page.content = sync_table_block(
                        page.content or "", table.table_num, table.markdown)
                if in_md != got_fix:
                    # 머리 칸은 렌더러가 여러 행을 이어 붙여 그대로 찍히지
                    # 않을 수 있다. 몇 칸을 markdown 에 못 옮겼는지 남긴다 —
                    # `cells` 와 `markdown` 이 어긋난 칸이 있다는 뜻이다.
                    table.cell_leaks["markdown_unpatched"] = got_fix - in_md
                page.trace.add(
                    "docstruct.structuring.checks", "셀 오염 복원",
                    f"{table.id} · 이웃 칸에서 새어 든 글자 {got_fix}개를 "
                    f"지웠습니다 (앞 칸에 이미 있던 중복) · markdown 반영 "
                    f"{in_md}/{got_fix}칸")
            spill = leak_check(table.cells)
            if spill:
                # **정답 없이 확인되는 오염이다** (0.4.79). 앞 칸의 끝
                # 글자가 다음 칸 앞에 딸려 오면 경계가 밀린 것이다 —
                # 격자 온전성으로는 못 잡는다(자리는 다 덮였다).
                # 고치고도 남은 것 — 한 번뿐이라 우연일 수 있어 손대지
                # 않았다. 기록만 남긴다.
                table.cell_leaks = {**(table.cell_leaks or {}), **spill}
                leaked += 1
                page.trace.add(
                    "docstruct.structuring.checks", "셀 텍스트 오염",
                    f"{table.id} · 이웃 칸으로 새어 든 자리 {spill['leaks']}개 "
                    f"— 예: {spill['samples'][0] if spill['samples'] else ''}",
                    status="warn")
            got = grid_check(table.cells)
            if not got:
                continue
            table.grid_faults = got
            if got["ok"]:
                continue
            faulty += 1
            page.trace.add(
                "docstruct.structuring.checks", "격자 결함",
                f"{table.id} · {got['width']}×{got['height']} 중 구멍 "
                f"{got['holes']}칸 · 겹침 {got['overlaps']}칸"
                + (" — 많이 깨졌습니다" if got["heavy"] else ""),
                status="warn")
    if faulty:
        _log.info("격자가 어긋난 표 %d개 — `grid_faults` 참고", faulty)
    if repaired:
        _log.info("이웃 칸에서 새어 든 글자 %d개를 지웠습니다 "
                  "(앞 칸에 있던 중복)", repaired)
    if leaked:
        _log.warning("셀 텍스트가 이웃 칸으로 새어 든 표 %d개 — 한 번뿐이라 "
                     "손대지 않았습니다 (`cell_leaks` 참고)", leaked)

    _run_experiments("images")

    # ═══ 구간 9 — 표 LLM 판정·재추출 [표 축] ════════════════════════════════
    # tables/assess 가 표마다 sufficient/wrong/insufficient 를 판정하고,
    # tables/fill 이 미달 표를 쪽 이미지에서 다시 뽑는다. LLM 이 없으면
    # "판정 안 함" 으로 기록하고 넘어간다 — 결과를 지어내지 않는다.
    report("table_llm")
    if assess_tables and any(p.tables for p in pages):
        # LLM 이 없으면 assess_document 는 모든 표를 table/sufficient 로
        # 기본 표시만 하고 끝난다. 그것을 "LLM 판정 완료" 로 기록하면 결과
        # JSON 이 거짓말을 한다 — 판정한 적 없는 212개 표가 전부 sufficient
        # 로 남아 사람이 품질을 확인했다고 오해한다. 여기서 미리 갈라 둔다.
        from docstruct.infrastructure.llm.client import llm_available
        from docstruct.tables.assess import UNASSESSED_REASON

        llm_on = llm_available()
        _log.info("표 품질 평가 중..." if llm_on
                  else "LLM 미설정 — 표 판정을 건너뜁니다 (모두 기본값 처리)")
        t0 = time.perf_counter()
        assess_document(pages, progress=progress)
        timings[STAGE_ASSESS] = time.perf_counter() - t0
        elapsed = (time.perf_counter() - t0) * 1000 / max(
            sum(1 for p in pages if p.tables), 1
        )
        for page in pages:
            if not page.tables:
                continue
            # `llm_available()` 만 보면 부족하다 — 엔드포인트가 설정돼 있어도
            # 사내망 밖이라 연결이 안 되면 역시 판정이 안 된다. 실제 결과를
            # 보고 판단한다.
            unassessed = [t for t in page.tables if t.reason == UNASSESSED_REASON]
            if unassessed:
                page.trace.add(
                    "docstruct.tables.assess", "표 판정 생략",
                    f"LLM 응답 없음(미설정 또는 연결 불가) — {len(unassessed)}개를 "
                    "기본값(table/sufficient)으로 표시했을 뿐, 품질을 확인한 것이 "
                    "아닙니다",
                    status="skip",
                )
                continue
            page.trace.assessed = True
            verdicts = ", ".join(
                f"{t.id}:{t.content_type or '?'}"
                + (f"/{t.quality}" if t.quality else "")
                for t in page.tables
            )
            page.trace.add(
                "docstruct.tables.assess", "LLM 표 판정", verdicts,
                duration_ms=elapsed,
            )

        before = {id(t): t.markdown for p in pages for t in p.tables}
        targets = [t.id for p in pages for t in p.tables if t.needs_fill]

        t0 = time.perf_counter()
        process_tables(
            pages, fill_tables=fill_tables, fill_all=fill_all,
            table_html=table_html, progress=progress,
        )
        timings[STAGE_FILL] = time.perf_counter() - t0
        fill_elapsed = (time.perf_counter() - t0) * 1000

        for page in pages:
            page.trace.refilled = [
                t.id for t in page.tables
                if t.was_filled and before.get(id(t)) != t.markdown
            ]
            if page.trace.refilled:
                basis = "페이지 이미지" if page.page_image_path else "원본 표 HTML"
                page.trace.add(
                    "docstruct.tables.fill", "LLM 표 재추출",
                    f"{', '.join(page.trace.refilled)} 교체 ({basis} 근거)",
                    duration_ms=fill_elapsed / max(len(pages), 1),
                )
            elif fill_tables and any(t.id in targets for t in page.tables):
                # 재추출은 페이지 이미지를 근거로 삼습니다. 이미지가 없으면
                # 애초에 호출조차 하지 않으므로 사유를 구분해 남깁니다.
                if not page.page_image_path:
                    pending = [t for t in page.tables if t.needs_fill]
                    detail = "; ".join(
                        f"{t.id}({t.quality}): {t.reason or '사유 없음'}"
                        for t in pending[:5]
                    )
                    if len(pending) > 5:
                        detail += f" … 외 {len(pending) - 5}건"
                    page.trace.add(
                        "docstruct.tables.fill", "재추출 불가",
                        "근거 부재(페이지 이미지·원본 표 HTML 모두 없음) — "
                        f"{detail}",
                        status="warn",
                    )
                else:
                    page.trace.add(
                        "docstruct.tables.fill", "재추출 시도했으나 미교체",
                        "LLM 응답이 비었거나 요청 실패", status="warn",
                    )
            elif fill_tables and page.tables:
                page.trace.add(
                    "docstruct.tables.fill", "재추출 생략",
                    "품질 sufficient — LLM 호출 없음"
                    if not any(t.reason == UNASSESSED_REASON for t in page.tables)
                    else "LLM 응답 없음 — 판정 자체를 못 해 재추출 대상이 없음",
                    status="skip",
                )
    else:
        if not assess_tables:
            _log.info("표 평가 생략 (--no-llm 또는 --no-assess)")
            report_skip("table_llm",
                        "LLM 을 쓰지 않도록 설정했습니다 (--no-llm/--no-assess)")
            for page in pages:
                if page.tables:
                    page.trace.add(
                        "docstruct.tables.assess", "표 판정 생략",
                        "LLM 미사용 — 원본 파싱 결과 그대로", status="skip",
                    )

    # **구간 9 에서 새로 생긴 그림을 읽는다** (0.5.30). 표가 그림으로
    # 판정되면 그 자리에 ImageInfo 가 생기는데, 그림 판독(구간 7)은 이미
    # 지나갔다. 앞서 읽혔거나 사유가 붙은 그림은 건드리지 않는다.
    if read_pictures:
        from docstruct.images.vlm_read import read_new_pictures

        late = read_new_pictures(pages, progress=progress)
        if late:
            _log.info("표에서 승격된 그림 %d개를 읽었습니다", late)

    # OCR 검증은 **표 재추출이 끝난 뒤에** 한다.
    #
    # 재추출은 지면을 보고 표를 다시 쓰므로, 그전에 검증하면 곧 고쳐질
    # 것을 의심 목록에 올리게 된다. 지면을 보는 쪽이 먼저고, 텍스트만
    # 보는 검증이 **남은 것**을 훑는 순서가 맞다.
    #
    # ═══ 구간 10 — OCR 검증·재판독 [텍스트 축] ═══════════════════════════════
    # text/ocr_verify 가 스캔·전사 쪽의 의심 자리를 짚고, text/ocr_reread 가
    # 그 자리만 다시 읽는다. 고치기 전 본문은 ocr_original 에 남는다.
    # **스캔으로 다시 읽은 쪽이 있을 때만** 돈다 — 텍스트 레이어가
    # 온전하면 이 문제가 없다. 게이트는 소비돼 줄어든 `ocr_targets` 가
    # 아니라 원본 집합(`scanned_pages`)을 본다: VLM 이 읽은 쪽도 수치
    # 오독 위험은 같으므로 검증 대상이다 (0.4.56 — 이전에는 VLM 기본
    # 경로에서 verify→reread 가 도달 불가능했다).
    # **전사된 그림이 있는 쪽도 검증 대상이다** (0.4.58). 한글 문서에 붙은
    # 스캔 지면은 그림으로 들어와 `is_page_like` 로 갈린 뒤 **스캔 쪽과
    # 같은 전사 지시문**으로 읽힌다 — 읽는 행위가 같으면 수치 오독 위험도
    # 같다. 실측(조달청 HWPX image_1): 조직도 전문이 전사돼 본문에
    # 들어갔는데 아무 검증도 받지 않았다.
    report("ocr_verify")
    transcribed_pages = {
        page.page_no for page in pages
        if isinstance(page.page_no, int)
        and any(getattr(i, "transcribed", False) for i in (page.images or ()))
    }
    if (scanned_pages | transcribed_pages) and get_settings().verify_ocr:
        from docstruct.text.ocr_verify import find_doubts

        _t = time.perf_counter()
        found = find_doubts(pages)
        timings[STAGE_VERIFY_OCR] = time.perf_counter() - _t
        if found:
            _log.info("OCR 이 의심스러운 곳 %d군데 — `ocr_doubts` 참고", found)

        # 짚은 자리를 지면 보고 다시 읽는다. **짚은 쪽만** 태우므로
        # 전면 재판독보다 훨씬 싸다.
        if found and get_settings().reread_doubts:
            from docstruct.text.ocr_reread import reread_doubts

            _t = time.perf_counter()
            fixed = reread_doubts(pages)
            timings[STAGE_REREAD_OCR] = time.perf_counter() - _t
            if fixed:
                _log.info("재판독으로 %d군데 고침", fixed)

    # 목차는 **본문이 확정된 뒤에** 찾는다. 재판독이 글자를 고치므로
    # 그전에 뽑으면 고쳐지기 전 본문에서 뽑게 된다.
    # ═══ 구간 11 — 문서 구조 [텍스트 축] ══════════════════════════════════════
    # outline/toc 가 목차와 인쇄 쪽번호 차이를 찾는다.
    report("outline")
    if get_settings().detect_toc:
        # 형식과 무관하다 — 목차 줄 모양은 어디서나 같다.
        from docstruct.outline.toc import (find_toc, page_offset,
                                           printed_page_offset, title_page_offset)

        doc.toc = find_toc(pages)
        # 바닥글 쪽번호로 재는 쪽이 정확하다. 목차만으로는 잴 수 없는
        # 경우가 있다 — 목차가 앞쪽인데 항목이 뒤를 가리키면 그렇다.
        # **세 가지를 순서대로 본다** (0.5.24).
        #   ① 바닥글 쪽번호      지면에 찍힌 값 — 가장 곧다
        #   ② 목차 제목 대조     제목이 실제로 실린 쪽을 찾아 뺀다
        #   ③ source_page 차이   마지막 수단 (실측에서 틀렸다)
        measured, samples = printed_page_offset(pages)
        if measured is None:
            measured, by_title = title_page_offset(doc.toc, pages)
            if measured is not None:
                samples = by_title
        doc.toc_offset = measured if measured is not None else page_offset(doc.toc)
        _fill_printed_pages(pages, doc.toc_offset)
        if doc.toc or measured is not None:
            _log.info("목차 항목 %d개 · 쪽 차이 %s (근거 %d쪽)",
                      len(doc.toc), doc.toc_offset, samples)


    # ═══ 구간 12 — 마무리 [공통] ═════════════════════════════════════════════
    # 누름틀 잔재 제거, 빈 결과 경고, 실행 설정·시간 기록, scratch 정리.
    # **누름틀 잔재를 뗀다.** 한글 필드 상태가 직렬화돼 PDF 텍스트
    # 레이어까지 새어 나온다 — 실측(조달청 p75 별첨7): 표 머리 첫 칸이
    # `{"fields": {},"simplefields": {}} 프로 그램 (코드 번호)` 로 나왔다.
    # HWPX·HWP 경로는 각자 걸렀지만 PDF 경로는 거르는 곳이 없었다.
    report("finish")
    from docstruct.text.korean_text import strip_field_payload

    for page in pages:
        page.content = strip_field_payload(page.content or "")
        for table in page.tables or []:
            if table.markdown:
                table.markdown = strip_field_payload(table.markdown)
            for cell in table.cells or []:
                if cell.get("text"):
                    cell["text"] = strip_field_payload(cell["text"])

    for page in pages:
        page.content = normalize_table_blocks(page.content or "")
        page.trace.add("docstruct.tables.tags", "표 블록 정규화", "<table N> 태그 정리")
        page.trace.table_count = len(page.tables)
        page.trace.picture_count = len(page.images)
        if not (page.content or "").strip():
            page.trace.failed = True
            page.trace.notes.append("본문이 비어 있음")

    # 계수기를 먼저 닫아 이 문서가 도는 동안의 최대 겹침을 확정한다.
    _counter.__exit__(None, None, None)
    doc.pipeline = _pipeline_settings(fmt, assess_tables, fill_tables, fill_all)
    doc.timings = {k: round(v, 2) for k, v in timings.items()}
    _log_timings(doc.timings, concurrency_peak[0])
    if scratch is not None:
        _bind_scratch(doc, scratch)
    return doc


def _bind_scratch(doc: PageDocument, scratch: Path) -> None:
    """임시 작업 폴더를 문서 수명에 묶는다.

    입력: doc — 결과 문서, scratch — 지울 임시 폴더
    출력: 없음 (문서가 회수될 때 폴더 삭제)
    비고:
        out_dir 없이 실행하면 그림·페이지 PNG 가 이 폴더에 남는다. 문서가
        살아 있는 동안은 preview 와 save() 가 그 경로를 읽으므로 지우면
        안 되고, 문서가 사라진 뒤에는 아무도 안 쓰므로 남기면 안 된다.
        weakref.finalize 는 그 두 시점을 정확히 맞춰 준다.
    """
    import shutil
    import weakref

    # 저장 시 "이 폴더 안의 파일만" 이관하도록 위치를 남긴다.
    # out_dir 을 준 실행에서는 이 속성이 없으므로 이관도 일어나지 않는다.
    doc.scratch_dir = str(scratch)
    weakref.finalize(doc, shutil.rmtree, scratch, True)


#: HWP 를 HWPX 로 바꿔 읽었을 때 추출기가 남기는 표식.
HWPX_VIA_CONVERT = "hwp2hwpx→hwpx-tree"


def _gate_format(fmt: str, pages: list[PageContent]) -> str:
    """실험·단계 게이트에 쓸 형식 — **재료 기준** (0.5.2).

    입력: fmt — 입력 파일의 형식, pages — 추출 결과
    출력: 게이트에 쓸 형식 이름
    비고:
        HWP 를 HWPX 로 바꿔 읽으면 `cells` 가 생긴다. 그런데 `fmt` 로만
        게이트를 잡으면 여전히 "hwp" 라 격자 실험이 전부 막힌다 — 변환한
        이유가 그 공백을 닫는 것이었는데 게이트가 그대로 닫고 있었다.

        `source_format` 은 바꾸지 않는다. 입력이 HWP 였다는 것은 사실이고,
        결과물은 사실을 적어야 한다. **게이트만** 재료를 따른다.
    """
    if fmt != "hwp":
        return fmt
    if any(page.trace and page.trace.extractor == HWPX_VIA_CONVERT
           for page in pages):
        return "hwpx"
    return fmt


def _fill_printed_pages(pages: list[PageContent], offset: int | None) -> None:
    """물리 쪽에서 **인쇄 쪽번호**를 계산해 채운다 (0.5.25).

    입력: pages — 쪽 목록, offset — 인쇄 쪽과 물리 쪽의 차이
    출력: 없음 (page.printed_page_no 갱신)
    비고:
        `인쇄 = 물리 − offset`. 공공문서는 표지·목차 뒤부터 1 쪽을 매기므로
        둘이 어긋난다 — 사람이 "54쪽" 이라 할 때 가리키는 것은 인쇄 쪽이다.

        **표지·목차 지면은 비워 둔다.** 계산값이 1 보다 작으면 그 지면에는
        아직 번호가 붙지 않은 것이다. 0 이나 음수를 적으면 있지도 않은 쪽을
        가리키게 된다.

        오프셋을 모르면 아무것도 채우지 않는다 — 틀린 번호보다 없는 편이
        낫다. 어느 쪽인지는 `toc_offset` 이 None 인지로 구별된다.
    """
    if offset is None:
        return
    for page in pages:
        if not isinstance(page.page_no, int):
            continue
        printed = page.page_no - offset
        page.printed_page_no = printed if printed >= 1 else None


def _report_unused_steps(fmt: str) -> None:
    """이 형식이 지나가지 않는 단계를 이유와 함께 알린다.

    입력: fmt — 'pdf' | 'hwpx' | 'hwp'
    출력: 없음 (진행 이벤트)
    비고:
        이유는 `core.guide.FORMAT_CAPABILITIES` 에서 가져온다 — "재료가
        없다" 가 진짜 이유이고, 그 표가 재료의 유일한 출처다(0.4.90).
    """
    from docstruct.core.steps import STEPS

    passing = {step.id for step in steps_for(fmt)}
    reasons = {
        "scan_detect": "쪽 경계가 없어 지면을 그릴 수 없습니다",
        "scan_read": "지면 이미지가 없어 스캔 판독을 하지 않습니다",
        "table_mark": "셀 좌표가 없어 표 표시 단계를 건너뜁니다",
        "table_rebuild": "격자 근거(좌표·벡터)가 없어 다시 세울 수 없습니다",
        "picture": "지면에서 그림 영역을 떼어 낼 수 없습니다",
        "integrity": "cells 가 없어 격자·오염 검사를 할 수 없습니다",
        "table_llm": "표 판정에 쓸 지면 이미지가 없습니다",
        "ocr_verify": "스캔 판독이 없어 검증할 것이 없습니다",
        "experiments": "이 형식에 해당하는 실험이 없습니다",
    }
    for step in STEPS:
        if step.id in passing:
            continue
        report_skip(step.id, reasons.get(step.id, f"{fmt} 형식에는 해당 없음"))


def _log_timings(timings: dict[str, float], concurrency: int = 1) -> None:
    """단계별 소요 시간을 비중과 함께 로그로 남긴다.

    입력: timings — 단계명 → 초, concurrency — 이 문서가 도는 동안의 최대 동시 실행 수
    출력: 없음 (INFO 로그)
    비고:
        **겹쳐 돌았으면 그렇게 적는다** (0.4.88). 이 줄만 보고 "이 문서가
        1663초 걸린다" 고 읽으면 안 된다 — 겹친 수만큼 부푼 값이다.
        1건이면 문서가 정말 그만큼 걸린 것이고, N건이면 대략 1/N 이 그
        문서의 몫이다. 어느 쪽인지가 대응을 가른다.
    """
    total = sum(timings.values())
    if total <= 0:
        return
    if concurrency > 1:
        _log.info(
            "── 단계별 소요 시간 (총 %.1f초 · **동시 실행 최대 %d건** — "
            "겹친 만큼 부푼 값입니다. 이 문서만의 몫은 대략 %.1f초) ──",
            total, concurrency, total / concurrency,
        )
    else:
        _log.info("── 단계별 소요 시간 (총 %.1f초 · 단독 실행) ──", total)
    for label, seconds in sorted(timings.items(), key=lambda kv: -kv[1]):
        _log.info("   %-32s %6.1f초  %4.0f%%", label, seconds, seconds / total * 100)


#: 지금 build_document 안에 들어와 있는 문서 수 (0.4.88).
#:
#: **왜 세나.** 로그에 `추출 (HWP 파싱) 1663초 100%` 만 있으면 그 문서가
#: 진짜 무거운 것인지, 여러 건이 겹쳐 돌아 각자의 벽시계가 부푼 것인지
#: 가릴 수가 없다. 실측(같은 문서, 스레드 수만 바꿈): 1개 0.35초 · 2개
#: 0.53초 · 4개 0.93초 · 8개 1.60초 — 처리량은 3.8건/초로 평평하다.
#: 즉 **겹친 수만큼 건당 시간이 늘어난다.** 그 수를 결과에 남기면 로그
#: 한 줄로 갈린다.
_inflight_lock = threading.Lock()
#: 지금 돌고 있는 문서들 — (최대치 상자, 설정 지문).
_inflight: list[tuple[list[int], str]] = []


def _settings_fingerprint() -> str:
    """지금 환경변수가 만드는 설정의 지문.

    입력: 없음 (os.environ)
    출력: 짧은 해시 문자열
    비고:
        설정은 `DOCSTRUCT_*` 환경변수로 들어오고 `get_settings()` 는
        **프로세스 전역 캐시**다. 같은 프로세스에서 서로 다른 설정으로
        동시에 돌리면 뒤에 온 쪽이 앞의 것을 덮어써, 두 문서가 섞인 설정으로
        처리된다. 지문이 다르면 그 일이 벌어지고 있다는 뜻이다.
    """
    import hashlib
    import os as _os

    items = sorted((k, v) for k, v in _os.environ.items()
                   if k.startswith("DOCSTRUCT_"))
    raw = "\x00".join(f"{k}={v}" for k, v in items)
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:12]


@contextmanager
def _counted(display_name: str, fmt: str) -> Iterator[list[int]]:
    """이 문서가 도는 동안의 최대 동시 실행 수를 센다.

    입력: display_name — 사람에게 보일 이름, fmt — 형식
    출력: [최대 동시 실행 수] — 한 칸짜리 목록 (도는 동안 갱신된다)
    비고:
        **진입할 때 살아 있는 모두의 최대치를 함께 올린다.** 자기가 들어온
        수와 나가는 수만 보면 놓친다 — 겹침이 중간에만 있었고 나갈 때는
        이미 혼자인 경우가 그렇다. 서버에서 정확히 그 모양이 나온다.
        상자는 진행 중인 문서 수만큼이라 비용이 없다.
    """
    peak = [0]
    fingerprint = _settings_fingerprint()
    with _inflight_lock:
        _inflight.append((peak, fingerprint))
        now = len(_inflight)
        for box, _fp in _inflight:
            box[0] = max(box[0], now)
        clashing = {fp for _b, fp in _inflight if fp != fingerprint}
    _log.info("추출 시작: %s (%s)%s", display_name, fmt,
              f" · 동시 실행 {now}건" if now > 1 else "")
    if clashing:
        # **같은 프로세스에서 서로 다른 설정으로 동시에 돈다.** 설정은
        # `os.environ` 을 거쳐 들어가고 `get_settings()` 는 프로세스 전역
        # 캐시라, 한쪽이 바꾸면 **다른 쪽 문서가 그 설정으로 처리된다.**
        # `DocStruct.run()` 은 `api._applied` 의 락으로 이것을 막지만
        # `build_document` 를 직접 부르면 막을 것이 없다 — 조용히 섞이는
        # 대신 소리를 낸다. 고치는 법은 하나뿐이다: 프로세스를 가른다.
        _log.warning(
            "%s: 같은 프로세스에서 **다른 설정**의 문서가 동시에 처리되고 "
            "있습니다 (동시 %d건). 설정은 프로세스 전역이라 서로의 값으로 "
            "처리될 수 있습니다 — `docstruct --jobs N` 이나 프로세스 분리를 "
            "쓰세요.", display_name, now,
        )
    try:
        yield peak
    finally:
        with _inflight_lock:
            _inflight[:] = [entry for entry in _inflight if entry[0] is not peak]


#: 본문이 이보다 적으면 추출이 사실상 실패한 것으로 본다.
_EMPTY_THRESHOLD = 50


def _warn_if_empty(name: str, pages: list[PageContent]) -> None:
    """내용이 사실상 비었으면 눈에 띄게 알린다.

    입력: name — 파일 이름, pages — 추출 결과
    출력: 없음 (경고 로그)
    비고:
        예외가 없으면 배치는 "성공" 으로 셉니다. 그런데 본문이 비어 있으면
        실패 목록에도 안 뜨고 JSON 만 텅 빈 채로 남습니다. 배포용 문서나
        파서가 조용히 실패한 경우가 그렇습니다 — 여기서 잡아 둡니다.
    """
    chars = sum(len(p.content or "") for p in pages)
    tables = sum(len(p.tables) for p in pages)
    if chars >= _EMPTY_THRESHOLD or tables:
        return
    _log.warning(
        "%s: 본문이 %d자뿐입니다 — 추출에 실패했을 수 있습니다. "
        "배포용(DRM) 문서이거나 파서가 내용을 읽지 못한 경우입니다",
        name, chars,
    )


def _pipeline_settings(
    fmt: str, assess_tables: bool, fill_tables: bool, fill_all: bool
) -> dict:
    """이 실행에 적용된 설정 스냅샷을 만든다.

    입력: fmt, assess_tables, fill_tables, fill_all 과 전역 설정
    출력: dict — pdf_backend, ocr_backend, llm_model 등 (document.json 의 pipeline)
    """
    from docstruct.core.version import details as version_details

    settings = get_settings()
    # **판을 결과물에 적는다** (0.5.13). 없으면 "이 JSON 은 몇 판이
    # 만든 것인가" 를 증상으로 되짚어야 한다 — `〃` 가 있는지, markdown 이
    # cells 보다 짧은지 따위로. 세 번 연속 그렇게 추측했다.
    got = version_details()
    info: dict = {
        "source_format": fmt,
        "docstruct_version": got["version"],
        "docstruct_install": got["source"],
    }

    if fmt == "pdf":
        info.update(
            pdf_backend=settings.pdf_backend,
            ocr_backend=settings.ocr_backend,
            force_full_page_ocr=settings.force_full_page_ocr,
            code_formula_enrichment=settings.code_formula_enrichment,
            picture_description=(
                settings.docling_picture.model if settings.docling_picture else None
            ),
            # 아래 넷은 결과 해석에 필요하다. 빠져 있으면 어떤 설정으로 돌린
            # 산출물인지 알 수 없어, 같은 결과를 두고 다른 판단을 하게 된다.
            korean_ocr=settings.korean_ocr,
            flag_odd_tables=settings.flag_odd_tables,
            mark_table_continuation=settings.mark_table_continuation,
            read_charts=settings.read_charts,
            detect_toc=settings.detect_toc,
            scanned_skip_docling_ocr=settings.scanned_skip_docling_ocr,
            verify_ocr=settings.verify_ocr,
            reread_doubts=settings.reread_doubts,
            flag_broken_tables=settings.flag_broken_tables,
            rebuild_grid=settings.rebuild_grid,
            vlm_fix_tables=settings.vlm_fix_tables,
            ocr_lang=settings.ocr_lang or None,
        )

    if fmt in ("hwp", "hwpx"):
        # HWP 도 마찬가지다. 재추출 근거 HTML 을 뽑았는지에 따라 결과가
        # 크게 달라지는데, 기록이 없으면 나중에 알 수 없다.
        info.update(hwp_fill_html=settings.hwp_fill_html)

    info.update(
        assess_tables=assess_tables,
        fill_tables=fill_tables,
        fill_all=fill_all,
        llm_model=settings.llm.model if settings.llm else None,
        llm_url=settings.llm.url if settings.llm else None,
        # 성능 관련 값도 남긴다. 나중에 "왜 느렸나" 를 볼 때 필요하다.
        llm_concurrency=settings.llm_concurrency,
        llm_fallback_model=(
            settings.llm_fallback.model if settings.llm_fallback else None
        ),
        device=resolve_device()[0],
        num_threads=settings.num_threads or None,
    )
    return info
