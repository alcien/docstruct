"""환경변수 → 설정 객체.

역할:
    .env 와 환경변수를 읽는 유일한 지점. 다른 모듈은 os.environ 을 직접
    읽지 않고 get_settings() 를 통해 값을 얻는다. 잘못된 값은 경고 후
    기본값으로 대체해 실행이 멈추지 않게 한다.
호출부:
    converters.pdf.docling_backend  Docling 파이프라인 구성
    docstruct.tables.*              LLM 설정·동시 실행 수
    infrastructure.llm.client       엔드포인트
    docstruct.checks / cli          환경 표시
출력:
    Settings — LLM 엔드포인트, OCR/PDF 백엔드, 연산 장치, 동시 실행 수 등
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)

def _default_env_path() -> Path:
    """``.env`` 를 찾을 위치를 정한다.

    입력: 없음
    출력: 첫 번째로 발견된 .env 경로. 없으면 작업 디렉터리의 .env
    비고:
        설치본은 패키지 안에 .env 가 없고, 소스 트리에서 바로 쓸 때는
        프로젝트 루트에 있다. 두 경우를 모두 처리하려고 아래 순서로 찾는다.
          1. DOCSTRUCT_ENV 환경변수에 적힌 경로
          2. 현재 작업 디렉터리에서 위로 (프로젝트 루트에서 멈춤)
          3. 패키지가 놓인 디렉터리와 그 상위 (소스 트리 실행용)
          4. ~/.config/docstruct/.env
    """
    explicit = os.environ.get("DOCSTRUCT_ENV")
    if explicit:
        return Path(explicit).expanduser()

    def _search(start: Path) -> Path | None:
        for folder in (start, *start.parents):
            candidate = folder / ".env"
            if candidate.is_file():
                return candidate
            # 프로젝트 경계를 넘어가면 남의 .env 를 읽을 수 있다.
            if (folder / ".git").exists() or (folder / "pyproject.toml").is_file():
                break
        return None

    cwd = Path.cwd().resolve()
    found = _search(cwd)
    if found is not None:
        return found

    found = _search(Path(__file__).resolve().parent)
    if found is not None:
        return found

    user_conf = Path.home() / ".config" / "docstruct" / ".env"
    if user_conf.is_file():
        return user_conf
    return cwd / ".env"
_CHAT_SUFFIX = "/v1/chat/completions"

# ── 사이트 기본값 ────────────────────────────────────────────────────────
# 설치 직후 별도 설정 없이 동작하도록 기본 엔드포인트를 둡니다.
# 우선순위는 환경변수(.env 포함) → 사이트 설정 파일 → 아래 내장값 순입니다.
#
# 저장소를 외부에 공개한다면 내장값에 사내 주소를 두지 마세요.
# 대신 `site_defaults.py` 를 만들어 두면 (gitignore 대상) 그쪽이 쓰입니다.
#
#   src/docstruct/core/site_defaults.py
#   DEFAULTS = {"DOCLING_TABLE_API_URL": "http://내부주소:포트/v1/chat/completions", ...}
_BUILTIN_DEFAULTS: dict[str, str] = {
    "DOCLING_TABLE_API_TIMEOUT": "120",
    "DOCLING_PICTURE_API_PROMPT": "Describe this image concisely and accurately in Korean.",
    "DOCLING_PICTURE_API_TIMEOUT": "120",
    "DOCLING_PICTURE_AREA_THRESHOLD": "0.01",
    "DOCLING_TABLE_LLM": "on",
    "DOCLING_TABLE_LLM_MODE": "selective",
    "DOCLING_TABLE_FORMAT": "html",
    "DOCLING_OCR_BACKEND": "rapidocr",
    # 연결 실패 시 대비책 (키는 각자 지정)
    "DOCLING_TABLE_API_FALLBACK_URL": "https://api.openai.com/v1/chat/completions",
    "DOCLING_TABLE_API_FALLBACK_MODEL": "gpt-5.6-luna",
}


def _load_site_defaults() -> dict[str, str]:
    """사이트 전용 기본값을 읽는다 (있으면).

    입력: 없음
    출력: {환경변수명: 값}. 파일이 없으면 빈 dict
    비고:
        같은 폴더의 ``site_defaults.py`` 에 ``DEFAULTS`` 를 두면 내장값을
        덮습니다. 이 파일은 gitignore 대상이라 공개 저장소에 올라가지
        않으므로, 사내 주소를 코드에서 분리할 수 있습니다.
    """
    site_defaults = None
    for module_name in ("docstruct.core.site_defaults", "core.site_defaults"):
        try:
            import importlib

            site_defaults = importlib.import_module(module_name)
            break
        except ImportError:
            continue
    if site_defaults is None:
        return {}
    values = getattr(site_defaults, "DEFAULTS", None)
    if not isinstance(values, dict):
        return {}
    return {str(k): str(v) for k, v in values.items()}


_DEFAULTS: dict[str, str] = {**_BUILTIN_DEFAULTS, **_load_site_defaults()}


def defaults() -> dict[str, str]:
    """코드에 내장된 기본값 전체.

    입력: 없음
    출력: {환경변수명: 기본값}
    """
    return dict(_DEFAULTS)


def is_default(name: str) -> bool:
    """해당 항목이 기본값으로 동작 중인지.

    입력: name — 환경변수명
    출력: 환경변수·.env 로 지정되지 않아 기본값을 쓰고 있으면 True
    """
    return name in _DEFAULTS and not os.environ.get(name, "").strip()


# ── .env 로드 -----------------------------------------------------------

_env_loaded_from: Path | None = None


def load_env(env_file: str | Path | None = None, *, override: bool = False) -> Path | None:
    """.env 파일을 읽어 환경변수에 채운다.

    입력: path — .env 경로. None 이면 프로젝트 루트에서 탐색
    출력: 읽은 파일 경로. 없으면 None
    비고: 이미 설정된 환경변수는 덮어쓰지 않는다
    """
    global _env_loaded_from

    try:
        from dotenv import dotenv_values, load_dotenv
    except ImportError:
        _log.warning("python-dotenv 미설치 — OS 환경변수만 사용합니다.")
        return None

    path = Path(env_file).expanduser().resolve() if env_file else _default_env_path()
    if not path.is_file():
        if env_file is not None:
            _log.warning(".env 파일을 찾을 수 없습니다: %s", path)
        else:
            _log.info("%s 없음 — 필요하면 cp .env.example .env", path)
        return None

    raw_values = dotenv_values(path)
    _warn_wrapped_lines(path, raw_values)

    file_values = {k: v for k, v in raw_values.items() if v is not None}
    changed = {
        k for k, v in file_values.items() if k in os.environ and os.environ[k] != v
    }
    for key in sorted(changed):
        if override:
            _log.info(
                "환경변수 %s 갱신: %r → %r",
                key, _truncate(os.environ[key]), _truncate(file_values[key]),
            )
        else:
            _log.warning(
                "환경변수 %s 는 이미 프로세스에 설정되어 있어 .env 값이 무시됩니다 "
                "(현재=%r, .env=%r). 쉘에서 unset %s 하거나 커널/터미널을 재시작하세요.",
                key,
                _truncate(os.environ[key]),
                _truncate(file_values[key]),
                key,
            )

    load_dotenv(path, override=override)
    _env_loaded_from = path.resolve()
    return _env_loaded_from


def _warn_wrapped_lines(path: Path, raw_values: dict[str, str | None]) -> None:
    """.env 에서 줄바꿈된 값을 경고한다.

    입력: 없음
    출력: 없음 (의심 줄을 로그로 알림)
    """
    orphaned = [k for k, v in raw_values.items() if v is None]
    if not orphaned:
        return
    for key in orphaned:
        _log.warning(
            "%s 에 '=' 없는 줄 %r 이 있습니다 — 윗줄의 값이 줄바꿈되어 잘렸을 "
            "가능성이 높습니다. 에디터에서 긴 URL/경로가 자동 줄바꿈되지 않았는지 "
            "확인하고, 그 값을 한 줄로 합치세요.",
            path,
            key,
        )


def device_status() -> str:
    """연산 장치 상태를 한 줄로 알려준다.

    입력: 없음
    출력:
        요청한 장치를 그대로 쓰면 `cuda` 처럼 장치 이름만,
        쓸 수 없어 대체했으면 `cpu (요청: cuda — 사유)` 형태
    비고: 서버 기동 로그나 상태 조회에 쓴다.
    """
    settings = get_settings()
    try:
        from docstruct.converters.pdf.docling_backend import resolve_device

        device, note = resolve_device()
    except ImportError:
        return f"{settings.device} (docling 미설치 — PDF 처리 불가)"

    threads = f", {settings.num_threads}스레드" if settings.num_threads else ""
    if note is None:
        return f"{device}{threads}"
    return f"{device}{threads} (요청: {settings.device} — {note})"


def docling_picture_api_status() -> str:
    """그림 설명 API 설정 상태를 한 줄로 알려준다.

    입력: 없음
    출력: 사람이 읽는 상태 문자열 (설정되어 있으면 URL·모델 포함)
    비고: FastAPI 서버 기동 로그 등 외부 소비자용 하위호환 함수.
    """
    endpoint = get_settings().docling_picture
    if endpoint is None:
        return "disabled (DOCLING_PICTURE_API_URL 미설정)"
    return (
        "enabled, do_picture_description=True "
        f"({endpoint.url}, model={endpoint.model})"
    )


def loaded_env_path() -> Path | None:
    """현재 적용된 .env 경로.

    입력: 없음
    출력: Path 또는 None
    """
    return _env_loaded_from


def _truncate(value: str, limit: int = 60) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


# ── 원시 값 파싱 -----------------------------------------------------------

def _get(name: str, default: str = "") -> str:
    """환경변수 값을 읽는다.

    입력: name — 변수명, default — 없을 때 값
    출력: 문자열 (양끝 공백·따옴표 제거)
    비고:
        우선순위는 환경변수(.env 포함) → 호출부가 준 default → 내장 기본값
        순이다. 내장 기본값 덕분에 설정 없이도 바로 동작한다.
    """
    value = os.environ.get(name, "").strip()
    if value:
        return value
    if default:
        return default
    return _DEFAULTS.get(name, "")


def _get_float(name: str, default: float) -> float:
    """실수 환경변수를 읽는다.

    입력: key, default
    출력: 실수. 값이 실수가 아니면 default
    """
    raw = _get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        _log.warning("%s=%r 는 숫자가 아닙니다 — 기본값 %s 사용", name, raw, default)
        return default


def _get_int(key: str, default: int) -> int:
    """정수 환경변수를 읽는다.

    입력: key, default
    출력: 정수. 값이 정수가 아니면 경고 후 default
    """
    raw = _get(key).strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        _log.warning("%s=%r 는 정수가 아닙니다 — 기본값 %d 사용", key, raw, default)
        return default


def _get_choice(key: str, default: str, allowed: tuple[str, ...]) -> str:
    """허용 목록이 있는 환경변수를 읽는다.

    입력: key, default, allowed — 허용 값 튜플
    출력: 허용 목록에 있으면 그 값, 아니면 경고 후 default
    """
    raw = _get(key, default).strip().lower()
    if raw not in allowed:
        _log.warning("%s=%r 는 알 수 없는 값 — %r 사용 (가능: %s)",
                     key, raw, default, ", ".join(allowed))
        return default
    return raw


def _get_bool(key: str, default: bool) -> bool:
    """불리언 환경변수를 읽는다.

    입력: key, default
    출력: True/False. 1/true/on/yes 계열은 True, 0/false/off/no 계열은 False,
          그 외에는 경고 후 default
    """
    raw = _get(key).strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "on", "yes", "y"):
        return True
    if raw in ("0", "false", "off", "no", "n"):
        return False
    _log.warning("%s=%r 는 불리언이 아닙니다 — 기본값 %s 사용", key, raw, default)
    return default


def _looks_like_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def _looks_truncated_path(url: str) -> bool:
    """URL이 ``/v1/chat/completions`` 경로 중간에서 끊긴 것처럼 보이는지.

    ``http://`` 로는 정상 시작해서 :func:`_looks_like_url` 를 통과하더라도,
    줄바꿈 등으로 뒷부분이 잘려 ``.../v1/chat`` 까지만 남는 경우가 있습니다
    (``_warn_wrapped_lines`` 가 항상 잡아주는 건 아닙니다 — 잘린 줄이 아예
    저장 과정에서 사라진 경우는 고아 키도 안 남습니다). 이 함수는 그 흔적을
    경로 문자열만 보고 판단합니다.
    """
    stripped = url.rstrip("/")
    return "/v1/chat" in stripped and not stripped.endswith(_CHAT_SUFFIX)


def _split_url(url: str) -> tuple[str, str]:
    """서버 주소와 chat completions 주소를 함께 만든다.

    입력: url — 사용자가 적은 엔드포인트 주소
    출력: (서버 기본 주소, /v1/chat/completions 로 끝나는 전체 주소)
    비고:
        아래 세 가지 표기를 모두 같은 결과로 정규화한다.
          http://host:11060
          http://host:11060/v1
          http://host:11060/v1/chat/completions
    """
    url = url.rstrip("/")

    if url.endswith(_CHAT_SUFFIX):
        base = url[: -len(_CHAT_SUFFIX)]
    elif url.endswith("/chat/completions"):
        base = url[: -len("/chat/completions")].rstrip("/")
        if base.endswith("/v1"):
            base = base[: -len("/v1")]
    elif url.endswith("/v1"):
        base = url[: -len("/v1")]
    else:
        base = url

    return base, f"{base}{_CHAT_SUFFIX}"


# ── 설정 모델 -------------------------------------------------------------

@dataclass(frozen=True)
class LLMEndpoint:
    """OpenAI 호환 ``/v1/chat/completions`` 서버 하나."""

    url: str          # 항상 /v1/chat/completions 로 끝남
    server_url: str    # 접두사만 (llm_request 어댑터용)
    model: str
    timeout: float
    prompt: str | None = None
    api_key: str = ""   # OpenAI 등 인증이 필요한 엔드포인트용

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def masked_key(self) -> str:
        """로그·화면 표시용. 절대 원문을 노출하지 않습니다."""
        if not self.api_key:
            return "(없음)"
        return f"{self.api_key[:7]}…{self.api_key[-4:]}" if len(self.api_key) > 14 else "(설정됨)"

    def as_dict(self) -> dict[str, object]:
        """기존 ``llm_api_config()`` 반환 형식과 호환되는 dict."""
        return {
            "url": self.url,
            "server_url": self.server_url,
            "model": self.model,
            "timeout": self.timeout,
            "headers": self.headers(),
        }


@dataclass(frozen=True)
class Settings:
    llm: LLMEndpoint | None              # 표 평가·재추출·목차 추출용 (TABLE, 없으면 PICTURE 필드별 fallback)
    llm_fallback: LLMEndpoint | None     # 위 엔드포인트에 연결이 안 될 때 쓸 대비책
    docling_picture: LLMEndpoint | None  # Docling 그림 설명 전용 (fallback 없음)
    ocr_backend: str
    ocr_lang: str                        # 쉼표 구분. 비우면 백엔드별 기본값
    device: str                          # auto | cpu | cuda | mps
    num_threads: int                     # CPU 스레드 수 (0=Docling 기본)
    rapidocr_runtime: str                # onnxruntime | torch | openvino | paddle
    llm_concurrency: int                 # LLM 동시 호출 수 (1=순차)
    threaded_pipeline: bool              # Docling 단계 병렬 파이프라인                     # rapidocr | tesseract | easyocr | auto
    picture_area_threshold: float
    table_llm_enabled: bool              # /convert 경로(legacy)의 표 LLM 스위치
    table_llm_mode: str                  # selective | always
    table_format: str                    # html | json
    pdf_backend: str                     # auto | pypdfium2 | dlparse (v4/v2 는 별칭)
    force_full_page_ocr: bool            # 텍스트 레이어를 무시하고 전면 OCR
    code_formula_enrichment: bool        # 수식·코드 VLM (무거움, 표 추출엔 불필요)
    generate_parsed_pages: bool          # 페이지 셀 보관 (OCR/텍스트레이어 측정용, 메모리↑)

    def describe(self) -> list[tuple[str, str, bool]]:
        """[(항목, 값, ok)] — 사람이 읽는 요약."""
        rows: list[tuple[str, str, bool]] = []
        if self.llm:
            rows.append((
                "LLM (표 평가/재추출/목차)",
                f"{self.llm.url} · {self.llm.model or '(모델 미지정)'}"
                + (" · 내장 기본값" if is_default("DOCLING_TABLE_API_URL") else " · .env"),
                bool(self.llm.model),
            ))
        else:
            rows.append(("LLM (표 평가/재추출/목차)", "미설정 — 해당 단계 자동 생략", True))

        if self.llm_fallback:
            rows.append((
                "LLM 대비책",
                f"{self.llm_fallback.model} · 키 {self.llm_fallback.masked_key()}"
                " — 기본 LLM 연결 실패 시 자동 전환",
                True,
            ))
        else:
            rows.append((
                "LLM 대비책",
                "없음 — OPENAI_API_KEY 를 넣으면 연결 실패 시 gpt-5.6-luna 로 전환됩니다",
                True,
            ))

        if self.docling_picture:
            rows.append((
                "Docling 그림 설명",
                f"{self.docling_picture.url} · {self.docling_picture.model or '(모델 미지정)'}"
                + (" · 내장 기본값" if is_default("DOCLING_PICTURE_API_URL") else " · .env"),
                bool(self.docling_picture.model),
            ))
        else:
            rows.append(("Docling 그림 설명", "미설정 — 그림 캡션 생략", True))
        rows.append((
            "LLM 동시 호출",
            f"{self.llm_concurrency}개" + (" (순차)" if self.llm_concurrency == 1 else ""),
            True,
        ))
        try:
            from docstruct.converters.pdf.docling_backend import resolve_device

            effective, note = resolve_device()
        except ImportError:
            effective, note = self.device, None
        rows.append((
            "연산 장치",
            effective
            + (f" · {self.num_threads}스레드" if self.num_threads else "")
            + (f" · rapidocr={self.rapidocr_runtime}"
               if self.ocr_backend == "rapidocr" else "")
            + (f" · 요청 {self.device} 불가: {note}" if note else ""),
            note is None,
        ))
        rows.append((
            "OCR 백엔드",
            self.ocr_backend + (f" · lang={self.ocr_lang}" if self.ocr_lang else ""),
            True,
        ))
        rows.append((
            "PDF 백엔드",
            self.pdf_backend + (" · 전면 OCR" if self.force_full_page_ocr else ""),
            True,
        ))
        if self.code_formula_enrichment:
            rows.append((
                "수식·코드 확장",
                "ON — VLM 추가 로딩 (표 추출엔 불필요, 느려짐)",
                False,
            ))
        return rows


def _make_endpoint(
    url: str, model: str, timeout: float, prompt: str | None, *, label: str,
    api_key: str = "",
) -> LLMEndpoint | None:
    if not url:
        return None
    if not _looks_like_url(url):
        _log.warning(
            "%s URL=%r 이 http(s):// 로 시작하지 않습니다 — 값이 잘렸을 수 "
            "있습니다 (.env 에서 긴 URL이 줄바꿈되지 않았는지 확인하세요).",
            label,
            _truncate(url),
        )
    elif _looks_truncated_path(url):
        _log.warning(
            "%s URL=%r 이 '/v1/chat' 에서 끊긴 것처럼 보입니다 (뒤에 "
            "'/completions' 가 없음) — 값이 잘렸을 가능성이 큽니다.",
            label,
            _truncate(url),
        )
    if not model:
        _log.warning("%s 모델이 비어 있습니다 — 서버 기본 모델로 요청됩니다.", label)
    server_url, chat_url = _split_url(url)
    if not api_key and "api.openai.com" in url:
        _log.warning(
            "%s 가 OpenAI 엔드포인트인데 API 키가 없습니다 — "
            "DOCLING_TABLE_API_KEY 또는 OPENAI_API_KEY 를 설정하세요.", label
        )
    return LLMEndpoint(
        url=chat_url, server_url=server_url, model=model,
        timeout=timeout, prompt=prompt, api_key=api_key,
    )


def _openai_like(url: str) -> bool:
    """OpenAI(호환 상용) 주소인지 판별한다.

    입력: url — 엔드포인트 주소
    출력: openai.com / azure openai 계열이면 True
    """
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    return host.endswith("openai.com") or host.endswith("openai.azure.com")


def _key_for(url: str, openai_key: str) -> str:
    """이 주소에 OPENAI_API_KEY 를 붙여도 되는지 판단한다.

    입력: url — 대상 주소, openai_key — OPENAI_API_KEY 값
    출력: 붙여도 되면 키, 아니면 빈 문자열
    비고:
        사내 엔드포인트에 OpenAI 키를 보내면 키가 외부로 새는 셈이 된다.
        주소가 OpenAI 계열일 때만 사용한다.
    """
    if not url or not openai_key:
        return ""
    return openai_key if _openai_like(url) else ""


def _same_host(a: str, b: str) -> bool:
    """두 주소가 같은 호스트인지.

    입력: a, b — 주소 문자열
    출력: 호스트가 같으면 True
    """
    from urllib.parse import urlparse

    return bool(a) and bool(b) and urlparse(a).hostname == urlparse(b).hostname


def _build_fallback() -> "LLMEndpoint | None":
    """연결 실패 시 쓸 대비 엔드포인트를 만든다.

    입력: 없음
    출력:
        LLMEndpoint — 주소·모델·키가 모두 갖춰졌을 때만.
        키가 없으면 None (호출해도 401 이 나므로 아예 만들지 않는다)
    비고:
        키는 DOCLING_TABLE_API_FALLBACK_KEY → OPENAI_API_KEY 순으로 찾는다.
        DOCLING_TABLE_API_FALLBACK=off 로 끌 수 있다.
    """
    if _get("DOCLING_TABLE_API_FALLBACK", "on").lower() in ("off", "0", "false", "no"):
        return None

    url = _get("DOCLING_TABLE_API_FALLBACK_URL")
    model = _get("DOCLING_TABLE_API_FALLBACK_MODEL")
    key = _get("DOCLING_TABLE_API_FALLBACK_KEY") or _get("OPENAI_API_KEY")
    if not (url and model and key):
        return None

    server_url, full = _split_url(url)
    return LLMEndpoint(
        url=full,
        server_url=server_url,
        model=model,
        timeout=_get_float("DOCLING_TABLE_API_FALLBACK_TIMEOUT", 180),
        api_key=key,
    )


def _build_settings() -> Settings:
    picture_url = _get("DOCLING_PICTURE_API_URL")
    picture_model = _get("DOCLING_PICTURE_API_MODEL")
    picture_timeout = _get_float("DOCLING_PICTURE_API_TIMEOUT", 120.0)
    picture_prompt = _get("DOCLING_PICTURE_API_PROMPT") or None
    # OPENAI_API_KEY 는 **OpenAI 주소일 때만** 씁니다.
    # 사내 엔드포인트로 남의 키를 보내지 않기 위함입니다.
    openai_key = _get("OPENAI_API_KEY")
    picture_key = _get("DOCLING_PICTURE_API_KEY") or _key_for(picture_url, openai_key)

    picture = _make_endpoint(
        picture_url, picture_model, picture_timeout, picture_prompt,
        label="DOCLING_PICTURE_API", api_key=picture_key,
    )

    # TABLE_* 가 필드별로 비어 있으면 PICTURE_* 로 채웁니다 (원본 프로젝트 동작 유지).
    table_url = _get("DOCLING_TABLE_API_URL") or picture_url
    table_model = _get("DOCLING_TABLE_API_MODEL") or picture_model
    table_timeout = _get_float("DOCLING_TABLE_API_TIMEOUT", picture_timeout)
    table_key = _get("DOCLING_TABLE_API_KEY") or _key_for(table_url, openai_key) or (
        picture_key if _same_host(table_url, picture_url) else ""
    )
    llm = _make_endpoint(
        table_url, table_model, table_timeout, None,
        label="DOCLING_TABLE_API", api_key=table_key,
    )

    table_format = _get("DOCLING_TABLE_FORMAT", "html").lower()
    if table_format not in ("html", "json"):
        table_format = "html"

    table_llm_flag = _get("DOCLING_TABLE_LLM", "off").lower()
    if table_llm_flag in ("0", "false", "off", "no"):
        table_llm_enabled = False
    elif table_llm_flag in ("1", "true", "on", "yes", "auto"):
        table_llm_enabled = llm is not None
    else:
        table_llm_enabled = False

    pdf_backend = _get("DOCLING_PDF_BACKEND", "auto").lower()
    if pdf_backend not in ("auto", "pypdfium2", "dlparse", "dlparse_v4", "dlparse_v2"):
        _log.warning("DOCLING_PDF_BACKEND=%r 는 알 수 없는 값 — auto 사용", pdf_backend)
        pdf_backend = "auto"

    return Settings(
        pdf_backend=pdf_backend,
        force_full_page_ocr=_get_bool("DOCLING_FORCE_FULL_PAGE_OCR", False),
        # 기본 꺼짐. 표·본문 추출에는 쓰이지 않으면서 모델을 추가로 받는다.
        code_formula_enrichment=_get_bool("DOCLING_CODE_FORMULA_ENRICHMENT", False),
        # 켜면 페이지별 text_layer/OCR 판별이 가능해집니다. 끄면 파싱은
        # 동일하게 되지만 처리 경로가 "측정 안 함" 으로 표시됩니다.
        generate_parsed_pages=_get_bool("DOCLING_GENERATE_PARSED_PAGES", False),
        llm=llm,
        llm_fallback=_build_fallback(),
        docling_picture=picture,
        ocr_backend=_get("DOCLING_OCR_BACKEND", "rapidocr").lower(),
        ocr_lang=_get("DOCLING_OCR_LANG"),
        device=_get_choice(
            "DOCLING_DEVICE", "auto", ("auto", "cpu", "cuda", "mps")
        ),
        num_threads=_get_int("DOCLING_NUM_THREADS", 0),
        rapidocr_runtime=_get_choice(
            "DOCLING_RAPIDOCR_RUNTIME", "onnxruntime",
            ("onnxruntime", "torch", "openvino", "paddle"),
        ),
        # 원격 LLM 은 I/O 대기라 병렬화 효과가 큽니다. 다만 너무 높이면
        # 429(rate limit)를 유발하므로 보수적 기본값을 씁니다.
        llm_concurrency=max(1, _get_int("DOCLING_LLM_CONCURRENCY", 4)),
        # Docling 이 OCR·레이아웃·표추출을 순차가 아닌 병렬 스레드로 돌립니다.
        threaded_pipeline=_get_bool("DOCLING_THREADED_PIPELINE", False),
        picture_area_threshold=_get_float("DOCLING_PICTURE_AREA_THRESHOLD", 0.01),
        table_llm_enabled=table_llm_enabled,
        table_llm_mode=_get("DOCLING_TABLE_LLM_MODE", "selective").lower(),
        table_format=table_format,
    )


_settings: Settings | None = None


def get_settings(*, reload: bool = False) -> Settings:
    """설정 객체를 얻는다 (최초 호출 시 생성 후 재사용).

    입력: 없음
    출력: Settings
    """
    global _settings
    if _settings is not None and not reload:
        return _settings
    # reload=True 는 사용자가 명시적으로 다시 읽어달라고 요청한 경우이므로
    # override=True — 최초 로드 때 os.environ에 남은 이전 값보다 파일이 이깁니다.
    load_env(override=reload)
    _settings = _build_settings()
    return _settings


def rebuild_settings() -> Settings:
    """현재 환경변수로 설정을 다시 만든다.

    입력: 없음
    출력: 새 Settings
    비고: .env 를 다시 읽지 않는다. 코드로 os.environ 을 바꾼 뒤 쓴다
    """
    global _settings
    _settings = _build_settings()
    return _settings


def reload_config() -> Settings:
    """.env 를 다시 읽고 설정을 새로 만든다.

    입력: 없음
    출력: 새 Settings
    """
    global _settings
    _settings = None
    return get_settings(reload=True)
