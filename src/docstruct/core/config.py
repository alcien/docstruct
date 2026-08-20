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
from typing import Any

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
        """start 부터 부모로 올라가며 .env 를 찾는다.

        입력: start — 탐색 시작 폴더
        출력: 찾은 .env 경로. 없으면 None
        비고: .git 이나 pyproject.toml 을 만나면 프로젝트 경계로 보고 멈춘다
              — 경계를 넘으면 남의 .env 를 읽을 수 있다.
        """
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
    "DOCSTRUCT_PICTURE_MODE": "read",
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


def device_available(name: str) -> bool:
    """요청한 연산 장치를 실제로 쓸 수 있는지 확인한다.

    입력: name — auto | cpu | cuda | mps
    출력: 사용 가능하면 True
    비고:
        torch 가 없으면 cpu 만 True 다. cuda/mps 는 torch 로 확인하며,
        확인 중 예외가 나면 사용 불가로 본다.
    """
    if name == "cpu":
        return True

    head, _, idx = name.partition(":")
    index = int(idx) if idx.isdigit() else 0

    try:
        import torch
    except ImportError:
        return name == "auto"        # torch 가 없으면 auto 는 CPU 로 간다

    try:
        if head in ("cuda", "auto"):
            if not torch.cuda.is_available():
                return name == "auto"     # auto 는 GPU 가 없어도 CPU 로 진행
            # is_available() 이 True 여도 실제 장치 접근에서 터지는 경우가 있다.
            # (드라이버·CUDA_VISIBLE_DEVICES 불일치, 컨테이너 GPU 매핑 문제 등)
            # 여기서 한 번 만져 보고 안 되면 쓸 수 없는 것으로 본다.
            if index >= torch.cuda.device_count():
                return False              # 있지도 않은 번호를 지정한 경우
            torch.cuda.get_device_properties(index)
            return True
        if head == "mps":
            backend = getattr(torch.backends, "mps", None)
            if not (backend and backend.is_available()):
                return False
            torch.zeros(1, device="mps")
            return True
    except Exception:   # 드라이버 문제 등으로 확인 자체가 실패할 수 있다
        return name == "auto"
    return False


#: 인덱스 없이 쓸 수 있는 장치 이름
_DEVICE_NAMES = ("auto", "cpu", "cuda", "mps", "xpu")


def _cuda_device_count() -> int:
    """이 프로세스에 보이는 GPU 수.

    입력: 없음
    출력: 개수. torch 가 없거나 확인 실패면 0
    """
    try:
        import torch

        return torch.cuda.device_count()
    except Exception:
        return 0


def _get_device() -> str:
    """연산 장치 설정을 읽는다.

    입력: 없음
    출력: auto | cpu | cuda | mps | xpu, 또는 `cuda:1` 처럼 인덱스가 붙은 값
    비고:
        GPU 가 여러 장이면 어느 것을 쓸지 골라야 한다. Docling 은
        `cuda:1` 형태를 그대로 받으므로 통과시킨다.
    """
    raw = _get("DOCLING_DEVICE", "auto").lower()
    if raw in _DEVICE_NAMES:
        return raw

    head, _, idx = raw.partition(":")
    if head in ("cuda", "xpu") and idx.isdigit():
        return raw

    _log.warning(
        "DOCLING_DEVICE=%r 는 알 수 없는 값 — 'auto' 사용 "
        "(가능: %s, 또는 cuda:0 처럼 인덱스 지정)",
        raw, ", ".join(_DEVICE_NAMES),
    )
    return "auto"


def cuda_visibility_conflict() -> str | None:
    """CUDA_VISIBLE_DEVICES 와 torch 가 보는 GPU 수가 어긋나는지 확인한다.

    입력: 없음
    출력: 어긋나면 설명 문자열, 정상이면 None
    비고:
        `import torch` 뒤에 `os.environ["CUDA_VISIBLE_DEVICES"]` 를 바꾸면
        torch 가 캐시한 개수와 실제 보이는 개수가 달라진다. 그 상태에서
        CUDA 를 호출하면 `device=N, num_gpus=M` ASSERT 로 죽는다.
        환경변수는 **프로세스를 띄우기 전에** 설정해야 한다.
    """
    import os
    import sys

    if "torch" not in sys.modules:
        return None                      # 아직 초기화 전이면 문제없다

    raw = os.environ.get("CUDA_VISIBLE_DEVICES")
    if raw is None or raw.strip() == "":
        return None                      # 제한 없음 — 어긋날 일이 없다

    try:
        import torch

        if not torch.cuda.is_available():
            return None
        seen = torch.cuda.device_count()
    except Exception:
        return None

    wanted = len([x for x in raw.split(",") if x.strip() != ""])
    if seen == wanted:
        return None

    return (
        f"CUDA_VISIBLE_DEVICES={raw!r} 는 GPU {wanted}개를 지정하는데 "
        f"torch 는 {seen}개로 알고 있습니다.\n"
        "         import torch 뒤에 이 변수를 바꾸면 이렇게 어긋나고, "
        "CUDA 호출이 실패합니다.\n"
        "         프로세스를 띄우기 전에 설정하거나(예: "
        "CUDA_VISIBLE_DEVICES=0 jupyter lab), 커널을 재시작하세요."
    )


def first_usable_cuda() -> int | None:
    """실제로 쓸 수 있는 첫 GPU 번호를 찾는다.

    입력: 없음
    출력: 장치 번호. 하나도 못 쓰면 None
    비고:
        GPU 가 여러 장 보여도 실제로 할당된 것은 일부일 수 있다
        (공용 서버에서 1장만 배정받은 경우 등). 0번이 남의 것이면
        `device="cuda"` 는 실패하므로, 만져지는 첫 번째를 골라 준다.
    """
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        for i in range(torch.cuda.device_count()):
            try:
                torch.cuda.get_device_properties(i)
                return i
            except Exception:
                continue
    except Exception:
        return None
    return None


def _cuda_usable(index: int = 0) -> bool:
    """CUDA 장치를 실제로 만질 수 있는지.

    입력: 없음
    출력: 0번 장치 정보를 읽을 수 있으면 True
    """
    try:
        import torch

        if not torch.cuda.is_available():
            return False
        if index >= torch.cuda.device_count():
            return False
        torch.cuda.get_device_properties(index)
        return True
    except Exception:
        return False


def resolve_device() -> tuple[str, str | None]:
    """설정된 장치를 검사해 실제로 쓸 장치를 정한다.

    입력: 없음 (설정의 device 사용)
    출력:
        device  실제로 쓸 장치 이름
        note    대체가 일어났으면 그 사유, 아니면 None
    비고:
        Docling 은 쓸 수 없는 장치를 지정하면 예외를 던지고 변환 전체가
        실패한다. 여기서 미리 걸러 CPU 로 내리고 사유를 남긴다.
    """
    conflict = cuda_visibility_conflict()
    if conflict:
        return "cpu", f"{conflict}\n         — CPU 로 처리합니다"

    requested = get_settings().device

    # auto / cuda(번호 없음) 는 쓸 수 있는 장치를 직접 찾아 못 박는다.
    # 0번이 남의 것이거나 접근할 수 없는 환경에서 실패하지 않도록.
    if requested in ("auto", "cuda"):
        usable = first_usable_cuda()
        if usable is None:
            return "cpu", None if requested == "auto" else (
                "CUDA 를 쓸 수 없습니다 (GPU 미탑재이거나 접근 불가) — CPU 로 처리합니다"
            )
        return f"cuda:{usable}", None

    if device_available(requested):
        return requested, None

    head, _, idx = requested.partition(":")
    if idx.isdigit():
        count = _cuda_device_count()
        usable = first_usable_cuda()
        if not count:
            reason = f"{requested} 를 쓸 수 없습니다 — 보이는 GPU 가 없습니다"
        elif int(idx) >= count:
            reason = (
                f"{requested} 를 쓸 수 없습니다 — 보이는 GPU 는 {count}개"
                f"(0~{count - 1}번)입니다"
            )
        elif usable is not None:
            # 번호는 있는데 그 장치에 접근할 수 없는 경우
            # (공용 서버에서 다른 사람에게 할당된 GPU 등)
            reason = (
                f"{requested} 에 접근할 수 없습니다 — "
                f"쓸 수 있는 것은 cuda:{usable} 입니다"
            )
        else:
            reason = f"{requested} 에 접근할 수 없습니다 — 쓸 수 있는 GPU 가 없습니다"
    else:
        reason = {
            "cuda": "CUDA 를 쓸 수 없습니다 (GPU 미탑재이거나 CPU 전용 PyTorch)",
            "mps": "MPS 를 쓸 수 없습니다 (Apple Silicon 아님 또는 미지원 PyTorch)",
        }.get(head, f"{requested} 를 쓸 수 없습니다")
    return "cpu", f"{reason} — CPU 로 처리합니다"


def device_status() -> str:
    """연산 장치 상태를 한 줄로 알려준다.

    입력: 없음
    출력:
        요청한 장치를 그대로 쓰면 `cuda` 처럼 장치 이름만,
        쓸 수 없어 대체했으면 `cpu (요청: cuda — 사유)` 형태
    비고: 서버 기동 로그나 상태 조회에 쓴다.
    """
    settings = get_settings()
    device, note = resolve_device()

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
    """긴 값을 표시용으로 자른다.

    입력: value — 문자열, limit — 최대 길이 (기본 60)
    출력: limit 이하면 원문, 넘으면 말줄임표를 붙인 앞부분
    """
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
    """http(s) URL 형태인지 확인한다.

    입력: value — 문자열
    출력: http:// 또는 https:// 로 시작하면 True
    """
    return value.startswith("http://") or value.startswith("https://")


def _looks_truncated_path(url: str) -> bool:
    """URL 이 `/v1/chat/completions` 경로 중간에서 끊겼는지 확인한다.

    입력: url — 검사할 주소
    출력: `/v1/chat` 까지만 있고 완전한 접미로 끝나지 않으면 True
    비고: .env 의 긴 URL 이 줄바꿈으로 잘리면 `http://` 로는 정상 시작해
          _looks_like_url 을 통과한다. 잘린 줄이 저장 과정에서 아예
          사라지면 고아 키도 안 남아, 경로 문자열만 보고 흔적을 잡는다.
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
    server_url: str    # 접두사만 (외부 어댑터용)
    model: str
    timeout: float
    prompt: str | None = None
    api_key: str = ""   # OpenAI 등 인증이 필요한 엔드포인트용

    def headers(self) -> dict[str, str]:
        """인증 헤더를 만든다.

        입력: 없음 (api_key 필드 사용)
        출력: {"Authorization": "Bearer …"} 또는 빈 dict
        """
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def masked_key(self) -> str:
        """로그·화면 표시용으로 키를 가린다.

        입력: 없음 (api_key 필드 사용)
        출력: 앞 7자·뒤 4자만 남긴 문자열. 짧으면 "(설정됨)", 없으면 "(없음)"
        비고: 절대 원문을 노출하지 않는다.
        """
        if not self.api_key:
            return "(없음)"
        return f"{self.api_key[:7]}…{self.api_key[-4:]}" if len(self.api_key) > 14 else "(설정됨)"

    def as_dict(self) -> dict[str, object]:
        """클라이언트가 쓰는 설정 dict 로 바꾼다.

        입력: 없음
        출력: {url, server_url, model, timeout, headers}
        비고: 기존 llm_api_config() 반환 형식과 호환된다.
        """
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
    local_vlm: "LocalVLM | None"         # 이 장비에서 직접 돌릴 VLM (지정 시 HTTP 대신 사용)
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
    #: 그림 처리 방식 — read | describe | both | off
    #:
    #:   read     (기본) 그림 **내용**을 VLM 으로 옮긴다. docstruct 클라이언트가
    #:            호출하므로 재시도·대비 엔드포인트·도달 불가 표시가 걸린다
    #:   describe docling 내장 그림 설명(한 문장 캡션)만 쓴다
    #:   both     둘 다 — 같은 그림에 두 번 호출된다. 비용이 두 배다
    #:   off      그림에 LLM 을 쓰지 않는다
    picture_mode: str
    code_formula_enrichment: bool        # 수식·코드 VLM (무거움, 표 추출엔 불필요)
    generate_parsed_pages: bool          # 페이지 셀 보관 (OCR/텍스트레이어 측정용, 메모리↑)
    hwp_fill_html: bool                  # HWP 표 재추출 근거용 HTML 을 추가로 뽑을지 (느림)
    korean_ocr: bool                     # 텍스트 레이어가 없는 쪽을 한국어 OCR 로 읽을지
    flag_broken_tables: bool             # 빈 칸이 있는 표를 표시할지 (기본 끔)
    flag_odd_tables: bool                # 같은 서식 중 열 수가 다른 표를 표시할지
    mark_table_continuation: bool        # 쪽을 넘는 표에 이어짐 관계를 표시할지
    read_charts: bool                    # 그래프를 VLM 으로 읽을지 (기본 끔)
    detect_toc: bool                     # 목차를 규칙으로 찾을지
    scanned_skip_docling_ocr: bool       # 스캔본에서 docling OCR 을 끌지
    rebuild_grid: bool                   # 그런 표의 격자를 OCR 좌표로 다시 세울지 (기본 끔)
    vlm_fix_tables: bool                 # 그런 표를 VLM 으로 다시 만들지

    def describe(self) -> list[tuple[str, str, bool]]:
        """사람이 읽는 설정 요약을 만든다.

        입력: 없음 (자기 필드 사용)
        출력: [(항목, 값, ok)] 목록 — checks.show_config 가 표로 그린다
        """
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

        if self.local_vlm:
            v = self.local_vlm
            rows.append((
                "로컬 VLM",
                f"{v.model_id} · {v.device} · {v.dtype} — HTTP 대신 이 모델을 씁니다",
                True,
            ))

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
        effective, note = resolve_device()
        if note:
            note = note.replace("\n", " ").replace("         ", " ")
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


#: 그림 처리 방식으로 인정하는 값.
_PICTURE_MODES = ("read", "describe", "both", "off")


def _picture_mode() -> str:
    """그림 처리 방식.

    입력: 없음 (DOCSTRUCT_PICTURE_MODE)
    출력: read | describe | both | off
    비고:
        기본은 read — 캡션이 아니라 **내용**을 옮긴다. 검색·인용이 목적이라
        "조직도를 나타낸 그림입니다" 같은 캡션은 쓸모가 적다.

        describe(docling 내장)를 쓰면 docling 이 직접 HTTP 를 호출해서
        우리 재시도·폴백 로직을 타지 않는다. 서버가 느리면 그림 하나에
        수 분씩 멈춘다.
    """
    raw = _get("DOCSTRUCT_PICTURE_MODE", "read").strip().lower()
    if raw in _PICTURE_MODES:
        return raw
    _log.warning(
        "DOCSTRUCT_PICTURE_MODE=%r 는 알 수 없는 값 — 'read' 를 씁니다 (%s)",
        raw, " | ".join(_PICTURE_MODES),
    )
    return "read"


def _make_endpoint(
    url: str, model: str, timeout: float, prompt: str | None, *, label: str,
    api_key: str = "",
) -> LLMEndpoint | None:
    """LLMEndpoint 를 만든다 (검증 포함).

    입력: url, model, timeout, prompt, label — 경고 표시용 이름, api_key
    출력: LLMEndpoint. url 이 비어 있으면 None
    동작: http(s) 로 시작하지 않거나 경로가 잘린 흔적이 보이면 경고를
          남긴다 (.env 줄바꿈 사고를 조기에 드러내기 위함).
    """
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


@dataclass(frozen=True)
class LocalVLM:
    """이 장비에서 직접 돌릴 VLM 설정.

    입력(필드):
        model_id        HuggingFace 이름 또는 로컬 경로
        device          auto | cpu | cuda | cuda:0 …
        dtype           auto | float16 | bfloat16 | float32
        max_new_tokens  생성 상한
    출력:
        as_dict()  local_vlm.invoke() 에 넘길 인자
    """

    model_id: str
    device: str = "auto"
    dtype: str = "auto"
    max_new_tokens: int = 2048

    def as_dict(self) -> dict[str, Any]:
        """local_vlm.invoke 에 넘길 kwargs dict.

        입력: 없음
        출력: {model_id, device, dtype, max_new_tokens}
        """
        return {
            "model_id": self.model_id,
            "device": self.device,
            "dtype": self.dtype,
            "max_new_tokens": self.max_new_tokens,
        }


def _build_local_vlm() -> "LocalVLM | None":
    """로컬 VLM 설정을 만든다.

    입력: 없음
    출력: LocalVLM. 모델이 지정되지 않았으면 None
    비고:
        지정되면 표 판정·재추출이 HTTP 대신 이 모델을 쓴다.
        장치는 따로 주지 않으면 전역 device 설정을 따른다.
    """
    model_id = _get("DOCSTRUCT_VLM_MODEL")
    if not model_id:
        return None
    return LocalVLM(
        model_id=model_id,
        device=_get("DOCSTRUCT_VLM_DEVICE") or _get_device(),
        dtype=_get_choice(
            "DOCSTRUCT_VLM_DTYPE", "auto",
            ("auto", "float16", "bfloat16", "float32"),
        ),
        max_new_tokens=_get_int("DOCSTRUCT_VLM_MAX_TOKENS", 2048),
    )


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
    """환경변수 전체를 읽어 Settings 를 조립한다.

    입력: 없음 (os.environ · 내장 기본값)
    출력: Settings (frozen dataclass)
    동작: LLM(표)·대비책·그림 설명·로컬 VLM 엔드포인트를 각각 구성한다.
          OPENAI_API_KEY 는 OpenAI 주소일 때만 붙인다 — 사내 엔드포인트로
          남의 키를 보내지 않기 위함이다.
    """
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

    # 주소가 하나도 없는데 **OpenAI 키만 있으면** OpenAI 를 주 엔드포인트로
    # 쓴다. `--ask-key` 로 키만 넣고 돌리는 경우가 그렇다.
    #
    # 이것이 없으면 키를 넣어도 LLM 이 미설정으로 남아, 표 평가가 조용히
    # 건너뛰고 모든 표가 기본값 `sufficient` 로 채워진다 — 정상처럼 보여
    # 알아차리기 어렵다.
    if not table_url and openai_key:
        table_url = _get("DOCLING_TABLE_API_FALLBACK_URL")
        table_model = table_model or _get("DOCLING_TABLE_API_FALLBACK_MODEL")
        table_key = openai_key

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
        picture_mode=_picture_mode(),
        # 기본 꺼짐. 표·본문 추출에는 쓰이지 않으면서 모델을 추가로 받는다.
        code_formula_enrichment=_get_bool("DOCLING_CODE_FORMULA_ENRICHMENT", False),
        # 켜면 페이지별 text_layer/OCR 판별이 가능해집니다. 끄면 파싱은
        # 동일하게 되지만 처리 경로가 "측정 안 함" 으로 표시됩니다.
        generate_parsed_pages=_get_bool("DOCLING_GENERATE_PARSED_PAGES", False),
        hwp_fill_html=_get_bool("DOCSTRUCT_HWP_FILL_HTML", False),
        korean_ocr=_get_bool("DOCSTRUCT_KOREAN_OCR", True),
        flag_broken_tables=_get_bool("DOCSTRUCT_FLAG_BROKEN_TABLES", False),
        flag_odd_tables=_get_bool("DOCSTRUCT_FLAG_ODD_TABLES", True),
        mark_table_continuation=_get_bool("DOCSTRUCT_MARK_TABLE_CONTINUATION", True),
        read_charts=_get_bool("DOCSTRUCT_READ_CHARTS", False),
        detect_toc=_get_bool("DOCSTRUCT_DETECT_TOC", True),
        scanned_skip_docling_ocr=_get_bool(
            "DOCSTRUCT_SCANNED_SKIP_DOCLING_OCR", False),
        rebuild_grid=_get_bool("DOCSTRUCT_REBUILD_GRID", False),
        vlm_fix_tables=_get_bool("DOCSTRUCT_VLM_FIX_TABLES", False),
        llm=llm,
        llm_fallback=_build_fallback(),
        local_vlm=_build_local_vlm(),
        docling_picture=picture,
        ocr_backend=_get("DOCLING_OCR_BACKEND", "rapidocr").lower(),
        ocr_lang=_get("DOCLING_OCR_LANG"),
        device=_get_device(),
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
