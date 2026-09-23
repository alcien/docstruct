"""라이브러리 공개 API.

입력:
    문서 경로 또는 경로 목록·글롭 + 설정 키워드(option_keys)

역할:
    파일명과 설정을 받아 문서를 구조화하고 JSON 으로 내보내는 단일 진입점.
    설정은 get()/set() 으로 다루며, 인스턴스마다 독립적으로 관리되고
    run() 실행 동안에만 전역 설정에 적용된 뒤 원래대로 되돌아간다.
호출부:
    외부 사용자 코드, 노트북, 다른 서비스
출력:
    DocStruct.to_json() 이 저장한 JSON 파일 경로,
    또는 to_dict() 가 반환한 구조화 결과 dict

사용 예::

    from docstruct import DocStruct

    ds = DocStruct("보고서.pdf")
    ds.set(device="cuda", llm_concurrency=8)
    ds.run()
    ds.to_json("결과.json")

    # 한 줄로
    from docstruct import structure
    result = structure("보고서.pdf", assess_tables=False)

실행 격리:
    프로세스가 다르면 설정이 서로 영향을 주지 않는다 (환경변수가 프로세스마다
    별도이므로 접속 세션이 달라도 무관하다). 같은 프로세스에서 여러 스레드가
    동시에 run() 하면 설정 교체 구간이 락으로 직렬화된다.

    다만 다음은 프로세스가 달라도 공유되므로 주의한다.
      - 출력 디렉터리   같은 경로로 저장하면 서로 덮어쓴다 (save(unique=True) 로 회피)
      - 모델 캐시       첫 실행 시 Docling 모델을 내려받는데, 동시에 시작하면
                        같은 캐시 경로를 두고 경쟁할 수 있다 (한 번 받아두면 무관)
      - GPU 메모리      여러 프로세스가 같은 장치를 쓰면 메모리를 나눠 쓴다
      - LLM 사용량 한도 동시 실행 수만큼 원격 호출이 늘어 429 가 날 수 있다
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from docstruct.models import PageDocument

_log = logging.getLogger(__name__)


# ═══ 구간 1 — 예외·키 관리 ══════════════════════════════════════════════════════
# DocStructError · mask(키 가리기) · set_api_key(소스에 키를 두지 않는 경로).
class DocStructError(Exception):
    """API 사용 오류 (잘못된 설정 키, 실행 순서 위반 등)."""


#: 설정 키 → 환경변수. run() 동안에만 적용된다.
_ENV_KEYS: dict[str, str] = {
    # LLM (표 평가·재추출)
    "llm_url": "DOCLING_TABLE_API_URL",
    "llm_model": "DOCLING_TABLE_API_MODEL",
    "llm_key": "DOCLING_TABLE_API_KEY",
    "llm_timeout": "DOCLING_TABLE_API_TIMEOUT",
    "llm_concurrency": "DOCLING_LLM_CONCURRENCY",
    # 기본 LLM 에 연결이 안 될 때 쓸 대비책
    "fallback_url": "DOCLING_TABLE_API_FALLBACK_URL",
    "fallback_model": "DOCLING_TABLE_API_FALLBACK_MODEL",
    "fallback_key": "DOCLING_TABLE_API_FALLBACK_KEY",
    "fallback_timeout": "DOCLING_TABLE_API_FALLBACK_TIMEOUT",
    "fallback_enabled": "DOCLING_TABLE_API_FALLBACK",
    "openai_key": "OPENAI_API_KEY",
    # 이 장비에서 직접 돌릴 VLM (지정하면 HTTP 대신 이것을 씀)
    "vlm_model": "DOCSTRUCT_VLM_MODEL",
    "vlm_device": "DOCSTRUCT_VLM_DEVICE",
    "vlm_dtype": "DOCSTRUCT_VLM_DTYPE",
    "vlm_max_tokens": "DOCSTRUCT_VLM_MAX_TOKENS",
    # HTTP 호출에 쓸 외부 어댑터 모듈 (미지정이면 requests 로 직접)
    "llm_adapter": "DOCSTRUCT_LLM_ADAPTER",
    # 그림 설명 VLM
    "picture_url": "DOCLING_PICTURE_API_URL",
    "picture_model": "DOCLING_PICTURE_API_MODEL",
    "picture_key": "DOCLING_PICTURE_API_KEY",
    "picture_enabled": "DOCLING_PICTURE_API",
    "picture_area_threshold": "DOCLING_PICTURE_AREA_THRESHOLD",
    # PDF 파싱
    "pdf_backend": "DOCLING_PDF_BACKEND",
    "ocr_backend": "DOCLING_OCR_BACKEND",
    "ocr_lang": "DOCLING_OCR_LANG",
    "force_full_page_ocr": "DOCLING_FORCE_FULL_PAGE_OCR",
    "generate_parsed_pages": "DOCLING_GENERATE_PARSED_PAGES",
    "hwp_fill_html": "DOCSTRUCT_HWP_FILL_HTML",
    "korean_ocr": "DOCSTRUCT_KOREAN_OCR",
    "flag_broken_tables": "DOCSTRUCT_FLAG_BROKEN_TABLES",
    "flag_odd_tables": "DOCSTRUCT_FLAG_ODD_TABLES",
    "mark_table_continuation": "DOCSTRUCT_MARK_TABLE_CONTINUATION",
    "read_charts": "DOCSTRUCT_READ_CHARTS",
    "detect_toc": "DOCSTRUCT_DETECT_TOC",
    "scanned_skip_docling_ocr": "DOCSTRUCT_SCANNED_SKIP_DOCLING_OCR",
    "verify_ocr": "DOCSTRUCT_VERIFY_OCR",
    "reread_doubts": "DOCSTRUCT_REREAD_DOUBTS",
    "rebuild_grid": "DOCSTRUCT_REBUILD_GRID",
    "vlm_fix_tables": "DOCSTRUCT_VLM_FIX_TABLES",
    "code_formula_enrichment": "DOCLING_CODE_FORMULA_ENRICHMENT",
    # 성능
    "device": "DOCLING_DEVICE",
    "num_threads": "DOCLING_NUM_THREADS",
    "rapidocr_runtime": "DOCLING_RAPIDOCR_RUNTIME",
    "threaded_pipeline": "DOCLING_THREADED_PIPELINE",
}

#: 실행 옵션 → 기본값. build_document 에 그대로 전달된다.
_RUN_KEYS: dict[str, Any] = {
    "assess_tables": True,
    "fill_tables": True,
    "fill_all": False,
    # 텍스트 레이어가 없는 그림을 VLM 으로 읽을지 (캡처 이미지 표·조직도)
    "read_pictures": True,
    "render_pages": True,
    "render_scale": 2.0,
    # 0 이면 나누지 않는다. 페이지 경계가 없는 문서를 조각낼 때 쓴다.
    "split_chars": 0,
    #: **산출 뿌리.** 여기를 주면 `run()` 이 끝날 때 그 아래
    #: `<파일이름.확장자>/` 를 만들고 산출물 전부를 거기에 쓴다
    #: (0.4.96 저장 · 0.4.97 폴더). CLI·일괄과 같은 모양이다.
    #: 예전에는 이 값이 판독 **중간 산물**(그림·쪽
    #: 이미지)의 자리로만 쓰여서, `DocStruct(fn, out_dir="out").run()` 을
    #: 돌리면 `out/` 에 `images/` 만 남았다 — 이름이 "출력 폴더" 인데
    #: 출력이 없었다. CLI 는 `save()` 를 따로 불러 다섯 파일을 냈으므로
    #: 같은 인자가 두 경로에서 다른 뜻이었다.
    "out_dir": None,
    "progress": False,
}

#: **화면 표시 설정** — build_document 에 넘기지 않는다 (0.4.95).
#: `steps` 는 진행 단계를 어떻게 보여줄지만 정한다: "user"(굵은 13단계) ·
#: "dev"(진행수·건너뛴 이유·시간) · "off". 기록 파일은 이 값과 무관하게
#: 산출 폴더가 있으면 언제나 쓴다 — 끄고 싶은 것은 화면 소음이지 기록이 아니다.
_VIEW_KEYS: dict[str, Any] = {
    #: 진행 표시 — "brief"(굵은 13단계 · 기본) | "dev"(상세) | "silent"(끔).
    #: `off`·`user` 는 brief 의 별칭이다 (0.4.99).
    "steps": "brief",
    #: `out_dir` 을 줬을 때 `run()` 이 산출물을 쓸지 (0.4.96).
    #: 중간 산물만 두고 저장은 직접 하고 싶으면 False.
    "write_outputs": True,
}


#: 값이 노출되면 안 되는 설정 키.
_SECRET_KEYS = frozenset({"llm_key", "picture_key", "fallback_key", "openai_key"})


def mask(value: str) -> str:
    """비밀값을 표시용으로 가린다.

    입력: value — 원본 문자열
    출력: `sk-abc…7890` 형태. 짧으면 `(설정됨)`
    """
    if not value:
        return "(없음)"
    return f"{value[:6]}…{value[-4:]}" if len(value) > 12 else "(설정됨)"


def set_api_key(key: str, *, target: str = "openai") -> None:
    """API 키를 이 프로세스 전체에 설정한다.

    입력:
        key     API 키
        target  openai   — **OpenAI 를 기본 LLM 으로 강제한다** (기본값)
                fallback — 연결 실패 시 쓰는 대비 엔드포인트에만 넣는다
                llm      — 기본 LLM 엔드포인트 키만 바꾼다 (주소는 그대로)
                picture  — 그림 설명 VLM
    출력: 없음
    예외: 알 수 없는 target 이면 DocStructError

    비고:
        키를 소스나 저장소에 두지 않고 실행 시점에 넣기 위한 함수다.
        이후 만드는 DocStruct 인스턴스에 모두 적용된다.

        **기본값이 `openai` 인 이유.** 예전 기본값은 `fallback` 이라
        `OPENAI_API_KEY` 만 넣었는데, 그 키는 *대비* 엔드포인트에만 쓰이고
        그것도 URL·모델이 함께 설정돼 있을 때만 산다. 사내 엔드포인트가
        잡혀 있으면 표 평가·재추출은 계속 그쪽으로 가고, 사내망이 아닌
        곳(노트북·Colab)에서는 **키를 넣었는데 아무 일도 안 일어났다.**
        노트북 안내문의 예시가 `getpass("OpenAI 키: ")` 인 만큼, 그 키를
        넣으면 OpenAI 로 도는 것이 사람이 기대하는 동작이다.

        옛 동작이 필요하면 `target="fallback"` 을 명시한다.

    사용 예::

        import docstruct, getpass
        docstruct.set_api_key(getpass.getpass("OpenAI 키: "))
    """
    mapping = {
        "fallback": "OPENAI_API_KEY",
        "llm": "DOCLING_TABLE_API_KEY",
        "picture": "DOCLING_PICTURE_API_KEY",
    }
    if target != "openai" and target not in mapping:
        raise DocStructError(
            f"알 수 없는 target: {target!r} "
            f"(가능: openai, {', '.join(mapping)})"
        )

    key = (key or "").strip()
    if not key:
        raise DocStructError("빈 키는 설정할 수 없습니다.")

    if target == "openai":
        # 기본 엔드포인트를 OpenAI 로 세운다. 이미 OpenAI 주소를 쓰고
        # 있으면 주소는 건드리지 않는다 (모델 지정을 존중한다).
        from docstruct.core.config import _DEFAULTS as DEFAULTS

        url = DEFAULTS["DOCLING_TABLE_API_FALLBACK_URL"]
        model = DEFAULTS["DOCLING_TABLE_API_FALLBACK_MODEL"]
        current = os.environ.get("DOCLING_TABLE_API_URL", "")
        if "api.openai.com" not in current:
            os.environ["DOCLING_TABLE_API_URL"] = url
            os.environ["DOCLING_TABLE_API_MODEL"] = model
        os.environ["DOCLING_TABLE_API_KEY"] = key
        os.environ["OPENAI_API_KEY"] = key        # 대비 경로도 함께
        _refresh_settings()
        _log.info("OpenAI 로 설정됨 — %s · %s",
                  os.environ["DOCLING_TABLE_API_URL"], mask(key))
        return

    os.environ[mapping[target]] = key
    _refresh_settings()
    _log.info("%s 키 설정됨 — %s", target, mask(key))


# ═══ 구간 2 — 산출물 수집 보조 ════════════════════════════════════════════════════
# scratch 의 그림·쪽 이미지를 out 으로 옮긴다. out 밖 경로는 건드리지 않는다(_is_under).
def _is_under(path: Path, root: Path) -> bool:
    """path 가 root 아래에 있는지.

    입력: path, root
    출력: bool (판정할 수 없으면 False)
    """
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


def _rescue_scratch(doc: Any, out: Path) -> None:
    """임시 작업 폴더의 그림·페이지 PNG 를 out 옆으로 건져낸다.

    입력: doc — PageDocument, out — 기준 폴더 (보통 JSON 파일이 놓일 곳)
    출력: 없음
    비고:
        out_dir 없이 run() 하면 이미지가 임시 폴더에 남고 그 폴더는 문서가
        회수될 때 지워진다. JSON 만 저장하면 그 안의 경로가 나중에 끊기므로,
        저장 시점에 옆으로 복사하고 경로를 갱신한다. out_dir 을 준 실행에는
        scratch_dir 이 없어 아무 일도 하지 않는다.
    """
    scratch = getattr(doc, "scratch_dir", None)
    if not scratch:
        return
    root = Path(scratch)
    _collect_images(doc, out / "images", only_from=root)
    _collect_page_images(doc, out / "pages", only_from=root)


def _collect_images(doc: Any, target: Path, *, only_from: Path | None = None) -> None:
    """흩어진 그림 파일을 산출물 폴더로 모은다.

    입력:
        doc        PageDocument
        target     옮길 위치 (out/images)
        only_from  주어지면 이 폴더 안에 있는 파일만 옮긴다.
                   임시 작업 폴더의 파일만 건져낼 때 쓴다
    출력: 없음 (ImageInfo.image_path 를 새 위치로 갱신)
    비고:
        out_dir 없이 run() 하면 그림이 임시 폴더에 저장된다. 그대로 두면
        나중에 사라지므로, save() 할 때 함께 옮긴다. 이미 목적지 안에
        있는 파일은 건드리지 않는다.
    """
    import shutil

    items = [(page, info) for page, info in doc.all_images() if info.image_path]
    if not items:
        return

    target = target.expanduser()
    moved = 0
    for _page, info in items:
        src = Path(info.image_path)
        if not src.is_file():
            continue
        if only_from is not None and not _is_under(src, only_from):
            continue
        try:
            src.relative_to(target)
            continue                      # 이미 제자리
        except ValueError:
            pass
        target.mkdir(parents=True, exist_ok=True)
        dst = target / src.name
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
        info.image_path = str(dst)
        moved += 1
    if moved:
        _log.info("그림 %d개를 %s 로 옮겼습니다", moved, target)


def _collect_page_images(doc: Any, target: Path, *, only_from: Path | None = None) -> None:
    """렌더된 페이지 PNG 를 산출물 폴더로 모은다.

    입력:
        doc        PageDocument
        target     옮길 위치 (out/pages)
        only_from  주어지면 이 폴더 안에 있는 파일만 옮긴다
    출력: 없음 (PageContent.page_image_path 를 새 위치로 갱신)
    비고:
        out_dir 없이 run() 하면 페이지 PNG 가 임시 폴더에 남고, 그 폴더는
        문서가 회수될 때 지워진다. save() 로 남기려면 여기서 복사해야
        document.json 의 경로가 나중에도 살아 있다.
    """
    import shutil

    target = target.expanduser()
    moved = 0
    for page in doc.pages:
        src_path = getattr(page, "page_image_path", None)
        if not src_path:
            continue
        src = Path(src_path)
        if not src.is_file():
            continue
        if only_from is not None and not _is_under(src, only_from):
            continue
        try:
            src.relative_to(target)
            continue                      # 이미 제자리
        except ValueError:
            pass
        target.mkdir(parents=True, exist_ok=True)
        dst = target / src.name
        if src.resolve() != dst.resolve():
            shutil.copy2(src, dst)
        page.page_image_path = str(dst)
        moved += 1
    if moved:
        _log.info("페이지 PNG %d개를 %s 로 옮겼습니다", moved, target)


# ═══ 구간 3 — 전역 설정 함수 ═════════════════════════════════════════════════════
# enable_logging · set_model · configure · defaults · option_keys — 환경변수를 거쳐 Settings 로 간다.
def enable_logging(level: str | int = "INFO", *, fmt: str | None = None) -> None:
    """진행 로그를 화면에 표시한다.

    입력:
        level  DEBUG | INFO | WARNING | ERROR (또는 logging 상수)
        fmt    로그 형식. 생략하면 간결한 기본 형식
    출력: 없음

    비고:
        라이브러리는 기본적으로 로깅을 설정하지 않는다 (쓰는 쪽 설정을
        덮어쓰면 안 되므로). 단계별 소요 시간·처리 경로를 화면에서 보려면
        이 함수를 부르거나 직접 ``logging.basicConfig`` 를 쓴다.

        소요 시간은 로그 없이도 ``summary()`` 와 ``document.timings`` 에서
        볼 수 있다.

    사용 예::

        import docstruct
        docstruct.enable_logging()          # 진행 상황 표시
        docstruct.DocStruct("문서.pdf").run()
    """
    import logging

    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(fmt or "%(levelname)-7s %(message)s"))

    logger = logging.getLogger("docstruct")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False

    # 파이프라인·변환 계층도 같은 설정을 따르게 한다.
    for name in ("converters", "core", "infrastructure",
                 "docstruct.converters", "docstruct.core", "docstruct.infrastructure"):
        other = logging.getLogger(name)
        other.handlers.clear()
        other.addHandler(handler)
        other.setLevel(level)
        other.propagate = False


def set_model(
    model_id: str | None,
    *,
    device: str | None = None,
    dtype: str | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """표 판정·재추출에 쓸 모델을 이 장비의 VLM 으로 바꾼다.

    입력:
        model_id    HuggingFace 이름(`Qwen/Qwen3-VL-4B-Instruct`) 또는
                    내려받은 로컬 경로. None 이면 해제하고 HTTP 로 돌아간다
        device      auto | cpu | cuda | cuda:0 …  (생략하면 전역 device 설정)
        dtype       auto | float16 | bfloat16 | float32
        max_tokens  생성 상한 (기본 2048)
    출력: 적용된 설정
    예외: transformers·torch 가 없으면 DocStructError

    비고:
        지정하면 HTTP 엔드포인트(사내 서버·OpenAI)를 쓰지 않고 이 모델을
        직접 돌린다. 모델은 처음 쓸 때 한 번 로드해 재사용하며, 생성은
        직렬화되므로 `llm_concurrency` 는 영향을 주지 않는다.

    사용 예::

        import docstruct
        docstruct.set_model("Qwen/Qwen3-VL-4B-Instruct", dtype="bfloat16")
        docstruct.DocStruct("문서.pdf").run()

        docstruct.set_model(None)      # 해제 — 다시 HTTP 사용
    """
    from docstruct.infrastructure.llm import local_vlm

    if model_id is None:
        for key in ("vlm_model", "vlm_device", "vlm_dtype", "vlm_max_tokens"):
            os.environ.pop(_ENV_KEYS[key], None)
        local_vlm.clear_cache()
        _refresh_settings()
        _log.info("로컬 VLM 해제 — HTTP 엔드포인트를 씁니다")
        return {}

    if not local_vlm.available():
        raise DocStructError(
            "로컬 VLM 을 쓰려면 transformers 와 torch 가 필요합니다.\n"
            "  pip install transformers torch"
        )

    options: dict[str, Any] = {"vlm_model": model_id}
    if device is not None:
        options["vlm_device"] = device
    if dtype is not None:
        options["vlm_dtype"] = dtype
    if max_tokens is not None:
        options["vlm_max_tokens"] = max_tokens

    local_vlm.clear_cache()          # 모델이 바뀌면 이전 것은 버린다
    return configure(**options)


def configure(**options: Any) -> dict[str, Any]:
    """설정을 이 프로세스 전체에 적용한다.

    입력: options — DocStruct.set() 과 같은 키
    출력: 적용된 설정 (비밀값은 가려짐)
    예외: 알 수 없는 키면 DocStructError

    비고:
        DocStruct.set() 은 그 인스턴스에만, run() 동안에만 적용된다.
        이 함수는 프로세스 전역에 남으므로 노트북에서 한 번 설정해 두고
        여러 문서를 처리할 때 쓴다.

    사용 예::

        import docstruct
        docstruct.configure(
            openai_key="sk-...",
            llm_url="http://내부주소:포트/v1",
            llm_concurrency=8,
        )
    """
    applied: dict[str, Any] = {}
    for name, value in options.items():
        if name not in _ENV_KEYS:
            raise DocStructError(
                f"알 수 없는 설정 키: {name!r}\n"
                f"사용 가능: {', '.join(sorted(_ENV_KEYS))}"
            )
        os.environ[_ENV_KEYS[name]] = _as_env_value(value)
        applied[name] = mask(str(value)) if name in _SECRET_KEYS else value

    _refresh_settings()
    return applied


def _refresh_settings() -> None:
    """전역 설정과 캐시를 새 환경변수로 갱신한다.

    입력: 없음
    출력: 없음
    """
    from docstruct.core.config import rebuild_settings
    from docstruct.core.checks import invalidate_caches

    rebuild_settings()
    invalidate_caches()


def defaults() -> dict[str, Any]:
    """내장 기본값을 설정 키 이름으로 돌려준다.

    입력: 없음
    출력: {설정 키: 기본값} — set() 으로 덮을 수 있는 항목만
    비고:
        설치 직후 별도 설정 없이 동작하도록 사내 공용 엔드포인트가
        기본값으로 들어 있다. 환경변수·.env·set() 이 모두 우선한다.
    """
    from docstruct.core.config import defaults as _env_defaults

    env = _env_defaults()
    rev = {v: k for k, v in _ENV_KEYS.items()}
    return {rev[k]: v for k, v in env.items() if k in rev}


def option_keys() -> tuple[str, ...]:
    """설정할 수 있는 키 전체.

    입력: 없음
    출력: 정렬된 키 이름 튜플
    """
    return tuple(sorted({*_ENV_KEYS, *_RUN_KEYS, *_VIEW_KEYS}))


def _as_env_value(value: Any) -> str:
    """설정값을 환경변수 문자열로 바꾼다.

    입력: value — 불리언·숫자·문자열
    출력: 환경변수에 넣을 문자열 (불리언은 'true'/'false')
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


#: 설정 교체 구간을 보호하는 락.
#: 하위 모듈이 전역 설정(core.config)과 Docling·LLM 캐시를 공유하므로,
#: 같은 프로세스에서 두 run() 이 겹치면 서로의 설정을 덮어쓴다.
#: 프로세스가 다르면(별도 세션·별도 실행) os.environ 이 분리되어 무관하다.
# ═══ 구간 4 — 설정 적용 컨텍스트 ═══════════════════════════════════════════════════
# _applied: run() 동안만 환경변수를 바꾸고 되돌린다. 같은 프로세스의 동시 run 은 락으로 직렬화.
_RUN_LOCK = threading.RLock()


@contextmanager
def _applied(env_overrides: dict[str, str]) -> Iterator[None]:
    """설정을 잠시 적용했다가 되돌린다.

    입력: env_overrides — {환경변수: 값}
    출력: 없음 (컨텍스트 매니저)
    비고:
        전역 설정과 Docling·LLM 캐시를 함께 갱신하고, 블록을 벗어나면
        원래 값으로 복원한다. 같은 프로세스에서 동시에 진입하면 락으로
        직렬화되므로 설정이 섞이지 않는다 (뒤에 온 쪽이 기다린다).
        run() 안의 LLM 병렬 호출은 이 락과 무관하게 그대로 동작한다.
    """
    from docstruct.core.config import rebuild_settings
    from docstruct.core.checks import invalidate_caches

    if not env_overrides:
        yield
        return

    with _RUN_LOCK:
        saved = {k: os.environ.get(k) for k in env_overrides}
        # 이미 같은 값이 들어가 있으면 다시 세울 이유가 없다.
        # 배치에서 문서마다 컨버터를 새로 만드는 것을 막는다
        # (Docling 은 컨버터를 새로 만들 때 모델을 다시 로드한다).
        if all(saved.get(k) == v for k, v in env_overrides.items()):
            yield
            return
        try:
            os.environ.update(env_overrides)
            rebuild_settings()
            invalidate_caches()
            yield
        finally:
            for key, old in saved.items():
                if old is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old
            rebuild_settings()
            invalidate_caches()


# ═══ 구간 5 — 파사드 ══════════════════════════════════════════════════════════
# _SettingsMixin(set/get/options) → DocStruct(단일) · DocStructBatch(일괄). 결과 모양만 다르고 설정 방식은 같다.
@contextmanager
def _reporting_for(source: Any, out_dir: Any, detail: Any) -> Iterator[None]:
    """이 문서를 도는 동안 진행 단계를 낸다 (노트북·라이브러리 공용).

    입력: source — 문서 경로, out_dir — 산출 폴더(없어도 된다), detail — "user"|"dev"|"off"
    출력: 없음 (컨텍스트 매니저)
    비고:
        **CLI 와 같은 문구가 노트북에서도 나온다** (0.4.95). 예전에는
        `--steps` 가 CLI 안에만 있어서 `DocStruct(...).run()` 이나
        `structure()` 는 조용히 몇 분을 썼다 — 멈춘 것처럼 보인다.

        기록 파일은 산출 폴더가 있을 때만 만든다(`steps.progress_path`).
        없으면 화면에만 낸다 — 저장할 곳을 지어내면 어디에 생겼는지
        아무도 모른다.
    """
    from docstruct.core.steps import (console_sink, jsonl_sink, normalize_mode,
                                      progress_path, reporting,
                                      source_format_of)

    # `off` 는 **상세를 끈다**는 뜻으로 읽힌다 — 굵은 13단계로 보낸다.
    # 정말 아무것도 내지 않으려면 `silent` (0.4.99).
    mode = normalize_mode(detail)
    sinks = []
    if mode != "silent":
        sinks.append(console_sink(mode))
    target = progress_path(out_dir)
    if target is not None:
        try:
            Path(target).unlink(missing_ok=True)
        except OSError:
            pass
        sinks.append(jsonl_sink(target))
    if not sinks:
        yield
        return

    name = Path(str(source)).name
    with reporting(name, source_format_of(source), sinks=sinks,
                   detail=mode) as got:
        try:
            yield
        finally:
            got.done()


class _SettingsMixin:
    """설정 관리 공통부.

    역할:
        DocStruct 와 DocStructBatch 가 똑같이 갖는 설정 관리(set/get/options/
        reset)를 한 곳에 둔다. 두 클래스는 **결과의 모양이 달라서** 나뉘어
        있을 뿐, 설정을 다루는 방식은 같다.
    호출부:
        DocStruct, DocStructBatch
    출력:
        _options 딕셔너리를 갱신·조회
    """

    _options: dict[str, Any]

    def set(self, key: str | None = None, value: Any = None, **options: Any):
        """설정값을 지정한다.

        입력:
            key      설정 키 하나 — value 와 함께 쓴다
            value    그 값
            options  키=값 여러 개를 한 번에 (option_keys() 의 키)
        출력: self (연쇄 호출 가능)
        예외: 알 수 없는 키면 DocStructError
        """
        if key is not None:
            options = {key: value, **options}
        for name, val in options.items():
            if name == "source":
                self._set_source(val)
                continue
            if (name not in _ENV_KEYS and name not in _RUN_KEYS
                    and name not in _VIEW_KEYS):
                raise DocStructError(
                    f"알 수 없는 설정 키: {name!r}\n"
                    f"사용 가능: {', '.join(option_keys())}"
                )
            self._options[name] = val
        return self

    def get(self, key: str, default: Any = None) -> Any:
        """설정값을 읽는다.

        입력: key — 설정 키, default — 지정하지 않았을 때 값
        출력: 지정값 → 실행 옵션 기본값 → default 순
        예외: 알 수 없는 키면 DocStructError
        """
        if key == "source":
            return self._get_source(default)
        if (key not in _ENV_KEYS and key not in _RUN_KEYS
                and key not in _VIEW_KEYS):
            raise DocStructError(
                f"알 수 없는 설정 키: {key!r}\n사용 가능: {', '.join(option_keys())}"
            )
        if key in self._options:
            return self._options[key]
        if key in _RUN_KEYS:
            return _RUN_KEYS[key]
        if key in _VIEW_KEYS:
            return _VIEW_KEYS[key]
        return default

    def options(self) -> dict[str, Any]:
        """지정한 설정 전체.

        입력: 없음
        출력: {키: 값} — 명시적으로 set 한 것만
        """
        return dict(self._options)

    def _env_overrides(self) -> dict[str, str]:
        """지정한 설정 중 환경변수로 넘길 것.

        입력: 없음
        출력: {환경변수명: 문자열 값}
        """
        return {
            _ENV_KEYS[name]: _as_env_value(val)
            for name, val in self._options.items()
            if name in _ENV_KEYS
        }

    def _run_kwargs(self) -> dict[str, Any]:
        """build_document 에 넘길 실행 옵션.

        입력: 없음
        출력: {옵션명: 값}
        """
        return {name: self.get(name) for name in _RUN_KEYS}

    # 아래 둘은 하위 클래스가 구현한다 (source 의 의미가 다르므로).
    def _set_source(self, value: Any) -> None:
        """source 를 하위 클래스 방식으로 저장한다.

        입력: value — 파일 경로(DocStruct) 또는 경로 목록·패턴(DocStructBatch)
        출력: 없음
        비고: 단일/배치가 source 의 의미를 달리 해석하므로 여기서는 구현하지 않는다.
        """
        raise NotImplementedError

    def _get_source(self, default: Any) -> Any:
        """현재 source 를 하위 클래스 방식으로 읽는다.

        입력: default — 미설정 시 돌려줄 값
        출력: 저장된 source 또는 default
        """
        raise NotImplementedError


class DocStruct(_SettingsMixin):
    """문서 구조화 진입점.

    입력(생성자):
        source   문서 경로 (.pdf | .hwp | .hwpx). 나중에 set(source=...) 로도 지정 가능
        options  설정값. option_keys() 로 목록을 볼 수 있다
    출력:
        run() 후 document 속성에 PageDocument,
        to_json() 으로 JSON 파일, to_dict() 로 dict
    """

    def __init__(self, source: str | Path | None = None, **options: Any) -> None:
        self._source: Path | None = Path(source).expanduser() if source else None
        self._options: dict[str, Any] = {}
        self._document: PageDocument | None = None
        if options:
            self.set(**options)

    # ── 설정 -----------------------------------------------------------



    def _set_source(self, value: Any) -> None:
        """처리 대상을 바꾼다 (set(source=...) 경유).

        입력: value — 문서 경로. None 이면 해제
        출력: 없음
        """
        self._source = Path(value).expanduser() if value else None

    def _get_source(self, default: Any) -> Any:
        """현재 대상 경로.

        입력: default — 미지정 시 돌려줄 값
        출력: 경로 문자열 또는 default
        """
        return str(self._source) if self._source else default

    def reset(self) -> "DocStruct":
        """설정과 실행 결과를 모두 지운다.

        입력: 없음
        출력: self
        """
        self._options.clear()
        self._document = None
        return self

    # ── 실행 -----------------------------------------------------------
    def run(self) -> "DocStruct":
        """문서를 구조화한다.

        입력: 없음 (생성자·set() 으로 지정한 source 와 설정 사용)
        출력: self (document 속성에 결과가 채워진다)
        예외:
            source 미지정 시 DocStructError
            파일이 없으면 FileNotFoundError
            지원하지 않는 형식이면 ValueError
        """
        if self._source is None:
            raise DocStructError("문서 경로가 없습니다 — DocStruct('파일.pdf') 또는 set(source=...)")

        from docstruct.pipeline import build_document

        from docstruct.output.names import safe_file_name

        # **`out_dir` 은 산출 뿌리다** (0.4.97). 그 아래 `<파일이름.확장자>/`
        # 를 만들고 거기에 넣는다 — CLI·일괄과 같은 모양이다. 예전에는
        # 단건만 뿌리에 바로 쏟아, 같은 뿌리로 두 번 돌리면 앞 결과가
        # 덮였고 CLI 와 결과 배치가 달랐다.
        out_root = self.get("out_dir")
        target = (Path(out_root) / safe_file_name(Path(str(self._source)).name)
                  if out_root is not None else None)

        run_kwargs = self._run_kwargs()
        if target is not None:
            # 그림·쪽 이미지도 같은 폴더 안으로 (뿌리에 흩어지지 않게).
            run_kwargs["out_dir"] = target

        with _applied(self._env_overrides()):
            with _reporting_for(self._source, target, self.get("steps")):
                self._document = build_document(self._source, **run_kwargs)
        # **산출 폴더를 줬으면 거기에 낸다** (0.4.96). 이름이 "출력 폴더" 인데
        # 그림만 남는 것은 말과 결과가 다른 것이다. CLI 와 같은 다섯 파일을
        # 같은 자리에 쓴다 — 노트북과 명령행의 결과가 같아야 한다.
        if target is not None and self.get("write_outputs"):
            self.save(target)
        return self

    @classmethod
    def from_document(
        cls, doc: PageDocument, *, source: str | Path | None = None, **options: Any
    ) -> "DocStruct":
        """이미 만들어진 PageDocument 를 감싼다 (실행 없이 결과만 다룰 때).

        입력:
            doc      구조화 결과
            source   원본 경로. 생략하면 doc.filename
            options  설정 (저장 경로 계산 등에만 쓰임)
        출력: run() 을 마친 것과 같은 상태의 DocStruct
        비고:
            배치 결과를 문서별로 저장하거나, 저장해 둔 결과를 다시 다룰 때
            쓴다. 이 경로가 없으면 호출부가 비공개 필드를 직접 건드리게 된다.
        """
        obj = cls(source or doc.filename, **options)
        obj._document = doc
        return obj

    # ── 결과 -----------------------------------------------------------
    @property
    def document(self) -> PageDocument:
        """구조화 결과.

        입력: 없음
        출력: PageDocument
        예외: run() 전이면 DocStructError
        """
        if self._document is None:
            raise DocStructError("아직 실행하지 않았습니다 — run() 을 먼저 호출하세요.")
        return self._document

    @property
    def pages(self) -> list:
        """페이지 목록.

        입력: 없음
        출력: list[PageContent]
        """
        return self.document.pages

    @property
    def tables(self) -> list:
        """문서 전체의 표.

        입력: 없음
        출력: list[TableInfo]
        """
        return [t for p in self.document.pages for t in p.tables]

    def to_dict(self, *, slim: bool = False) -> dict[str, Any]:
        """구조화 결과를 dict 로 얻는다.

        입력: slim — True 면 실행 기록(trace)을 빼고 본문·표만 담는다
        출력: document.json 과 같은 구조의 dict
        """
        return self.document.to_dict(slim=slim)

    def to_json_str(self, *, indent: int = 2, slim: bool = False) -> str:
        """구조화 결과를 JSON 문자열로 얻는다 (파일 저장 없음).

        입력: indent — 들여쓰기 칸 수. None 이면 한 줄로 압축, slim — True 면 실행 기록(trace)을 뺀다
        출력: JSON 문자열
        비고:
            파이썬 자료구조로 다루려면 to_dict(), 파일로 쓰려면 to_json() 을
            쓴다. 이 메서드는 HTTP 응답 본문이나 로그처럼 문자열이 필요할 때
            쓴다.
        """
        return json.dumps(self.to_dict(slim=slim), ensure_ascii=False, indent=indent)

    def to_json(self, path: str | Path | None = None, *, indent: int = 2,
                slim: bool = False) -> Path:
        """구조화 결과를 JSON 파일로 저장한다.

        입력:
            path    저장 경로. 생략하면 원본 파일명 옆에 <문서명>.json
            indent  들여쓰기 칸 수
            slim    True 면 실행 기록(trace)을 빼고 본문·표만 담는다
        출력: 저장된 Path (내용이 아니라 **경로**)
        비고:
            내용이 필요하면 to_dict() 또는 to_json_str() 을 쓴다.
            slim 은 사람이 읽거나 RAG 로 넘길 때 쓴다 — 72쪽 문서에서
            trace 가 파일의 85%를 차지한다. 진단이 필요하면 끄면 된다.
        """
        if path is None:
            base = self._source or Path("document")
            path = base.with_suffix(".json")
        path = Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        # 임시 폴더에 있는 이미지를 JSON 옆으로 건져낸 뒤 경로를 쓴다.
        _rescue_scratch(self.document, path.parent)
        path.write_text(self.to_json_str(indent=indent, slim=slim), encoding="utf-8")
        _log.info("JSON 저장: %s", path)
        return path

    def save(self, out_dir: str | Path, *, unique: bool = False) -> dict[str, Path]:
        """모든 산출물을 저장한다.

        입력:
            out_dir  저장 디렉터리
            unique   True 면 디렉터리 이름에 PID 와 시각을 붙여 충돌을 피한다.
                     여러 사람이 같은 서버에서 같은 경로로 저장할 때 쓴다.
        출력:
            {이름: 경로} — document(.json), markdown(.md), tables(.md),
            pipeline(.md), layout(.md)
        """
        from docstruct.output.report import (
            write_json,
            write_layout_report,
            write_markdown,
            write_pipeline_report,
            write_tables_report,
        )

        out = Path(out_dir).expanduser()
        if unique:
            # mkdtemp 은 디렉터리 생성을 원자적으로 처리하므로, 같은 순간에
            # 여러 프로세스가 호출해도 서로 다른 경로를 받는다.
            out.mkdir(parents=True, exist_ok=True)
            out = Path(
                tempfile.mkdtemp(
                    prefix=f"{time.strftime('%Y%m%d-%H%M%S')}_{os.getpid()}_",
                    dir=out,
                )
            )
        else:
            out.mkdir(parents=True, exist_ok=True)
        doc = self.document
        _collect_images(doc, out / "images")
        _collect_page_images(doc, out / "pages")
        return {
            "document": write_json(doc, out / "document.json"),
            "markdown": write_markdown(doc, out / "document.md"),
            "tables": write_tables_report(doc, out / "tables.md"),
            "pipeline": write_pipeline_report(doc, out / "pipeline.md"),
            "layout": write_layout_report(doc, out / "layout.md"),
        }

    def summary(self) -> list[str]:
        """콘솔용 요약.

        입력: 없음
        출력: 문자열 목록 (페이지 수·표·이미지·소요 시간 등)
        """
        from docstruct.output.report import summary_lines

        return summary_lines(self.document)

    def __repr__(self) -> str:
        state = "실행 전" if self._document is None else f"{len(self._document.pages)}페이지"
        name = self._source.name if self._source else "(경로 없음)"
        return f"<DocStruct {name} · {state} · 설정 {len(self._options)}개>"


class DocStructBatch(_SettingsMixin):
    """여러 문서를 한 번에 구조화한다.

    입력(생성자):
        sources  다음 중 하나
                   - 디렉터리 경로 (pattern 으로 걸러냄)
                   - 파일 경로 목록
                   - glob 문자열 (예: "docs/*.pdf")
        pattern  디렉터리를 줬을 때 적용할 glob (기본 "*")
        options  DocStruct 와 동일한 설정
    출력:
        run() 후 documents 에 PageDocument 목록,
        to_json() 으로 문서별 JSON, failures 에 실패 목록

    사용 예::

        batch = DocStructBatch("문서모음/", pattern="*.pdf", progress=True)
        batch.set(device="cuda")
        batch.run()
        batch.to_json("결과/")
        print(batch.failures)
    """

    def __init__(
        self,
        sources: str | Path | Iterable[str | Path],
        *,
        pattern: str = "*",
        **options: Any,
    ) -> None:
        self._paths = _resolve_sources(sources, pattern)
        self._options = dict(options)
        self._documents: list[PageDocument] = []
        self._failures: list[tuple[Path, Exception]] = []
        # 설정 검증은 DocStruct 에 위임한다 (잘못된 키를 여기서 바로 잡는다).
        DocStruct(**self._options)

    # ── 설정 -----------------------------------------------------------



    def _set_source(self, value: Any) -> None:
        """대상 목록을 바꾼다 (set(source=...) 경유).

        입력: value — 경로·글롭 패턴·폴더, 또는 그 목록
        출력: 없음 (self._paths 갱신)
        """
        self._paths = _resolve_sources(value, "*")

    def _get_source(self, default: Any) -> Any:
        """현재 대상 파일 목록.

        입력: default — 목록이 비어 있을 때 돌려줄 값
        출력: 경로 문자열 목록 또는 default
        """
        return [str(p) for p in self._paths] or default

    def reset(self) -> "DocStructBatch":
        """설정과 실행 결과를 지운다 (대상 파일 목록은 유지).

        입력: 없음
        출력: self
        """
        self._options.clear()
        self._documents = []
        self._failures = []
        return self

    # ── 실행 -----------------------------------------------------------
    @property
    def paths(self) -> list[Path]:
        """처리 대상 파일 목록.

        입력: 없음
        출력: Path 목록 (정렬됨)
        """
        return list(self._paths)

    def run(self, *, stop_on_error: bool = False) -> "DocStructBatch":
        """모든 문서를 순서대로 구조화한다.

        입력:
            stop_on_error  True 면 첫 실패에서 중단. False 면 실패를 모아
                           failures 에 담고 계속 진행한다
        출력: self
        비고:
            문서 단위 진행은 options 의 progress 설정을 따른다.
            문서 하나 안의 단계별 진행도 같은 설정으로 표시된다.
        """
        from docstruct.core.progress import ProgressBar

        show = bool(self._options.get("progress", False))
        self._documents = []
        self._failures = []

        from docstruct.pipeline import build_document

        from docstruct.output.names import assign_out_dirs

        run_kwargs = self._run_kwargs()
        out_root = self.get("out_dir")
        # 진행 기록과 산출물이 **같은 폴더**에 가야 한다. save() 도 같은
        # 함수로 이름을 정하므로 둘이 어긋나지 않는다(0.4.96).
        folders = assign_out_dirs([path.name for path in self._paths])
        # 문서마다 폴더가 갈리므로 파이프라인에는 뿌리를 넘기지 않는다.
        run_kwargs.pop("out_dir", None)
        bar = ProgressBar(len(self._paths), "문서 처리", unit="건", enabled=show)

        # 설정은 **한 번만** 적용한다. 문서마다 적용·해제를 반복하면
        # 그때마다 Docling 컨버터가 버려져 모델을 다시 로드한다.
        try:
            with _applied(self._env_overrides()):
                for path in self._paths:
                    bar.update(0, path.name)
                    try:
                        # 일괄에서는 문서마다 **자기 폴더**를 쓴다 —
                        # `<파일이름.확장자>/`(0.4.93). 한 폴더에 몰면
                        # 뒤엣것이 앞엣것을 덮는다(0.4.92 에서 겪었다).
                        per_doc = (Path(out_root) / folders[path.name]
                                   if out_root is not None else None)
                        with _reporting_for(path, per_doc, self.get("steps")):
                            self._documents.append(
                                build_document(path, **run_kwargs))
                    except Exception as exc:
                        _log.warning("%s 처리 실패: %s", path.name, exc)
                        self._failures.append((path, exc))
                        if stop_on_error:
                            raise
                    bar.update(1, path.name)
        finally:
            bar.close()

        # **산출 폴더를 줬으면 거기에 낸다** (0.4.96). 단건 run() 과 같은
        # 약속이다 — 노트북에서 out_dir 을 주고도 결과가 없으면 이름이
        # 거짓말이 된다.
        if out_root is not None and self.get("write_outputs") and self._documents:
            self.save(out_root)

        if self._failures:
            _log.warning(
                "%d건 중 %d건 실패", len(self._paths), len(self._failures)
            )
        return self

    # ── 결과 -----------------------------------------------------------
    @property
    def documents(self) -> list[PageDocument]:
        """성공한 문서들의 구조화 결과.

        입력: 없음
        출력: PageDocument 목록
        """
        return list(self._documents)

    @property
    def failures(self) -> list[tuple[Path, Exception]]:
        """실패한 문서와 원인.

        입력: 없음
        출력: (경로, 예외) 목록
        """
        return list(self._failures)

    def to_dict(self, *, slim: bool = False) -> dict[str, Any]:
        """전체 결과를 dict 로 얻는다.

        입력: slim — True 면 문서마다 실행 기록(trace)을 뺀다
        출력:
            {total, succeeded, failed, documents[], failures[]}
        """
        return {
            "total": len(self._paths),
            "succeeded": len(self._documents),
            "failed": len(self._failures),
            "documents": [d.to_dict(slim=slim) for d in self._documents],
            "failures": [
                {"file": str(p), "error": f"{type(e).__name__}: {e}"}
                for p, e in self._failures
            ],
        }

    def to_json(
        self, out: str | Path, *, combined: bool = False, indent: int = 2,
        slim: bool = False,
    ) -> list[Path] | Path:
        """결과를 JSON 으로 저장한다.

        입력:
            out       combined=False 면 디렉터리, True 면 파일 경로
            combined  True 면 전체를 파일 하나에 담는다
            indent    들여쓰기 칸 수
            slim      True 면 실행 기록(trace)을 빼고 본문·표만 담는다
        출력:
            combined=False 면 저장된 Path 목록, True 면 Path 하나
        비고:
            slim 은 단건 DocStruct.to_json 과 같은 의미다. 0.1.71 에서
            단건에만 연결하고 배치에는 빠뜨려, 배치 결과만 trace 가
            그대로 실렸다.
        """
        out = Path(out).expanduser()

        if combined:
            out.parent.mkdir(parents=True, exist_ok=True)
            for doc in self._documents:
                _rescue_scratch(doc, out.parent)
            out.write_text(
                json.dumps(self.to_dict(slim=slim), ensure_ascii=False, indent=indent),
                encoding="utf-8",
            )
            _log.info("JSON 저장: %s (%d건)", out, len(self._documents))
            return out

        out.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for doc in self._documents:
            path = out / f"{Path(doc.filename).stem}.json"
            _rescue_scratch(doc, out)
            path.write_text(
                json.dumps(doc.to_dict(slim=slim), ensure_ascii=False, indent=indent),
                encoding="utf-8",
            )
            written.append(path)
        _log.info("JSON 저장: %s 아래 %d건", out, len(written))
        return written

    def to_json_str(self, *, indent: int = 2, slim: bool = False) -> str:
        """전체 결과를 JSON 문자열로 얻는다 (파일 저장 없음).

        입력: indent — 들여쓰기 칸 수. None 이면 한 줄로 압축, slim — True 면 실행 기록(trace)을 뺀다
        출력: JSON 문자열 (to_dict() 와 같은 구조)
        """
        return json.dumps(self.to_dict(slim=slim), ensure_ascii=False, indent=indent)

    def save(self, out_dir: str | Path, *, unique: bool = False) -> dict[str, list[Path]]:
        """문서별로 산출물 전체를 저장한다.

        입력:
            out_dir  저장 디렉터리. 문서마다 하위 폴더가 생긴다
            unique   True 면 실행마다 별도 폴더를 만들어 충돌을 피한다
        출력: {산출 폴더 이름: [저장된 경로]}
        비고:
            DocStruct.save() 를 문서마다 호출한다 (json + md 4종).
            폴더 이름은 겹치지 않게 배정한다(`output.names`) — 이름이 같은
            문서가 있으면 확장자·해시로 갈린다. 겹치는 것이 없으면 예전과
            같은 이름이다.
        """
        out = Path(out_dir).expanduser()
        if unique:
            out.mkdir(parents=True, exist_ok=True)
            out = Path(
                tempfile.mkdtemp(
                    prefix=f"{time.strftime('%Y%m%d-%H%M%S')}_{os.getpid()}_", dir=out
                )
            )

        # **겹치지 않는 폴더를 배정한다** (0.4.92). 예전에는 `Path(filename)
        # .stem` 을 그대로 써서 `성과계획서.hwpx` 와 `성과계획서.pdf` 가 같은
        # 폴더에 쓰였다 — 뒤엣것이 앞엣것을 덮었다. 게다가 **반환 dict 의
        # 키도 겹쳐** 호출부는 몇 건이 사라졌는지조차 알 수 없었다.
        from docstruct.output.names import assign_out_dirs, describe_renames

        folders = assign_out_dirs([doc.filename for doc in self._documents])
        for line in describe_renames(folders):
            _log.info("산출 폴더 이름이 겹쳐 구분했습니다: %s", line)

        written: dict[str, list[Path]] = {}
        for doc in self._documents:
            folder = folders[doc.filename]
            holder = DocStruct.from_document(doc, **self._options)
            written[folder] = list(holder.save(out / folder).values())
        _log.info("산출물 저장: %s 아래 %d건", out, len(written))
        return written

    def summary(self) -> list[str]:
        """배치 처리 요약.

        입력: 없음
        출력: 문자열 목록 (성공·실패 건수와 실패 사유)
        """
        lines = [
            f"대상       : {len(self._paths)}건",
            f"성공       : {len(self._documents)}건",
            f"실패       : {len(self._failures)}건",
        ]
        for path, exc in self._failures:
            lines.append(f"  ✘ {path.name} — {type(exc).__name__}: {exc}")
        return lines

    def __len__(self) -> int:
        return len(self._paths)

    def __repr__(self) -> str:
        state = "실행 전" if not self._documents and not self._failures else (
            f"성공 {len(self._documents)} · 실패 {len(self._failures)}"
        )
        return f"<DocStructBatch {len(self._paths)}건 · {state}>"


# ═══ 구간 6 — 한 줄 사용 ═══════════════════════════════════════════════════════
# structure / structure_to_json — 파사드 없이 함수 한 번.
def _resolve_sources(
    sources: str | Path | Iterable[str | Path], pattern: str
) -> list[Path]:
    """입력을 처리 대상 파일 목록으로 바꾼다.

    입력:
        sources  디렉터리 · glob 문자열 · 경로 목록
        pattern  디렉터리일 때 적용할 glob
    출력: 지원 확장자에 해당하는 Path 목록 (정렬됨)
    예외: 대상이 없으면 DocStructError
    """
    from docstruct.pipeline import SUPPORTED_SUFFIXES

    found: list[Path] = []

    if isinstance(sources, (str, Path)):
        path = Path(sources).expanduser()
        if path.is_dir():
            found = [f for f in path.glob(pattern) if f.is_file()]
        elif path.is_file():
            found = [path]
        else:
            # glob 문자열로 해석
            base = Path(path.anchor or ".")
            rel = str(path.relative_to(path.anchor)) if path.anchor else str(path)
            found = [f for f in base.glob(rel) if f.is_file()]
    else:
        found = [Path(s).expanduser() for s in sources]

    usable = sorted({f.resolve() for f in found if f.suffix.lower() in SUPPORTED_SUFFIXES})
    if not usable:
        raise DocStructError(
            f"처리할 문서가 없습니다: {sources!r} (패턴={pattern!r}, "
            f"지원={', '.join(SUPPORTED_SUFFIXES)})"
        )
    return usable


def structure(source: str | Path, **options: Any) -> dict[str, Any]:
    """문서 하나를 구조화해 dict 로 돌려준다 (한 줄 사용).

    입력: source — 문서 경로, options — DocStruct 와 동일한 설정
    출력: document.json 과 같은 구조의 dict
    """
    return DocStruct(source, **options).run().to_dict()


def structure_to_json(
    source: str | Path, out_path: str | Path | None = None, **options: Any
) -> Path:
    """문서 하나를 구조화해 JSON 파일로 저장한다 (한 줄 사용).

    입력:
        source    문서 경로
        out_path  저장 경로. 생략하면 원본 옆에 <문서명>.json
        options   DocStruct 와 동일한 설정
    출력: 저장된 Path
    """
    return DocStruct(source, **options).run().to_json(out_path)
