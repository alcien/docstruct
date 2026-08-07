"""LLM 호출 클라이언트.

역할:
    설정된 엔드포인트로 프롬프트(및 이미지)를 보내고 응답 텍스트를 받는다.
    기본은 requests 로 직접 호출한다. `DOCSTRUCT_LLM_ADAPTER` 로 외부
    어댑터 모듈을 지정하면 그것을 쓴다. 어느 경로든 목적지와 페이로드
    형식은 동일하다.
    일시적 오류(429·5xx)는 재시도하고, 오류 본문은 그대로 드러낸다.
호출부:
    docstruct.tables.assess / docstruct.tables.fill
    docstruct.outline.builder
    converters.pdf.picture_inspect / converters.pdf.table_extract
출력:
    응답 본문 문자열. 실패 시 RuntimeError
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from docstruct.core.config import get_settings

_log = logging.getLogger(__name__)

#: 엔드포인트에 연결 자체가 안 되면 이후 호출을 건너뛴다.
#: 페이지·표마다 같은 오류를 반복 시도하면 시간만 쓰고 로그만 더럽힌다.
#: (url, model) → (사유, 표시 시각). reset_unreachable() 로 초기화된다.
_UNREACHABLE: dict[tuple[str, str], tuple[str, float]] = {}
_UNREACHABLE_LOCK = threading.Lock()

#: 도달 불가 표시의 유효 시간(초). 지나면 표시가 풀리고 다시 시도한다.
#:
#: 이 값이 없으면 오래 도는 프로세스(FastAPI 워커)에서 LLM 이 잠깐만
#: 끊겨도 재기동할 때까지 표 판정·재추출이 통째로 비활성화된다. CLI 는
#: 실행이 짧아 드러나지 않지만 서버는 며칠씩 산다.
UNREACHABLE_TTL = 60.0

#: 대비 엔드포인트 전환을 알렸는지 (한 번만 로그)
_FALLBACK_ANNOUNCED = False

#: 로컬 VLM 사용을 알렸는지 (한 번만 로그)
_LOCAL_ANNOUNCED = False

#: 연결 대기 상한(초). 응답 대기(cfg["timeout"])와 별개다.
#: 서버가 죽어 있을 때 첫 실패까지의 시간을 결정한다.
CONNECT_TIMEOUT = 5.0


class LLMUnreachableError(RuntimeError):
    """엔드포인트에 연결할 수 없어 호출을 건너뛴 경우."""


def mark_unreachable(url: str, model: str, reason: str) -> None:
    """이 엔드포인트를 당분간 도달 불가로 표시한다.

    입력: url, model, reason — 실패 사유
    출력: 없음
    비고: 표시는 UNREACHABLE_TTL 초 뒤 자동으로 풀린다. 로그는 표시가
          새로 생길 때만 남기므로 장애가 길어져도 한 번씩만 찍힌다.
    """
    now = time.monotonic()
    with _UNREACHABLE_LOCK:
        prev = _UNREACHABLE.get((url, model))
        fresh = prev is None or now - prev[1] >= UNREACHABLE_TTL
        _UNREACHABLE[(url, model)] = (reason, now)
    if not fresh:
        return
    has_backup = get_settings().llm_fallback is not None
    _log.warning(
        "%s 연결 불가 (%s) — 앞으로 %.0f초 동안 %s",
        url, reason, UNREACHABLE_TTL,
        "대비 엔드포인트로 보냅니다" if has_backup
        else "건너뜁니다 (대비책 없음 — OPENAI_API_KEY 를 넣으면 전환됩니다)",
    )


def unreachable_reason(url: str, model: str) -> str | None:
    """도달 불가로 표시돼 있으면 그 사유를 돌려준다.

    입력: url, model
    출력: 사유 문자열. 표시가 없거나 TTL 이 지났으면 None
    비고: TTL 이 지난 표시는 여기서 지워지므로 다음 호출은 실제로 시도된다.
    """
    now = time.monotonic()
    with _UNREACHABLE_LOCK:
        entry = _UNREACHABLE.get((url, model))
        if entry is None:
            return None
        reason, marked_at = entry
        if now - marked_at >= UNREACHABLE_TTL:
            del _UNREACHABLE[(url, model)]
            _log.info("%s 도달 불가 표시 해제 — 다시 시도합니다", url)
            return None
        return reason


def reset_unreachable() -> None:
    """도달 불가 표시를 초기화한다.

    입력: 없음
    출력: 없음
    """
    global _FALLBACK_ANNOUNCED, _LOCAL_ANNOUNCED
    with _UNREACHABLE_LOCK:
        _UNREACHABLE.clear()
        _FALLBACK_ANNOUNCED = False
        _LOCAL_ANNOUNCED = False


_adapter_cache: dict[tuple[str, str], Any] = {}


def llm_available() -> bool:
    """표 판정·재추출을 수행할 수단이 있는지.

    입력: 없음
    출력: HTTP 엔드포인트나 로컬 VLM 중 하나라도 있으면 True
    비고:
        호출부는 이 함수로 판단해야 한다. llm_api_config() 만 보면
        로컬 VLM 만 설정한 경우를 "미설정" 으로 오인한다.
    """
    settings = get_settings()
    return settings.llm is not None or settings.local_vlm is not None


def llm_api_config() -> dict[str, Any] | None:
    """현재 LLM 엔드포인트 설정을 dict 로 얻는다.

    입력: 없음
    출력: {url, server_url, model, timeout, headers}. 미설정이면 None
    """
    endpoint = get_settings().llm
    return endpoint.as_dict() if endpoint else None


def clear_adapter_cache() -> None:
    """어댑터 캐시를 비운다.

    입력: 없음
    출력: 없음
    비고: 설정을 바꾼 뒤 새 엔드포인트를 쓰게 하려면 호출한다
    """
    _adapter_cache.clear()


def adapter_module_name() -> str:
    """HTTP 호출에 쓸 외부 어댑터 모듈 이름.

    입력: 없음
    출력: 모듈 이름. 빈 문자열이면 어댑터를 쓰지 않는다
    비고:
        기본값은 없다. `DOCSTRUCT_LLM_ADAPTER` 를 지정했을 때만 그 모듈을
        찾는다. 이름이 다른 사내 라이브러리를 쓰거나, 같은 이름의 외부
        패키지와 충돌할 때 여기서 바꾼다.

        지정하지 않으면 requests 로 직접 호출한다 — 기능 차이는 없다.
    """
    import os

    return os.environ.get("DOCSTRUCT_LLM_ADAPTER", "").strip()


def _get_adapter(server_url: str, model: str) -> Any | None:
    """외부 HTTP 어댑터를 얻는다 (설정된 경우).

    입력: server_url, model
    출력:
        어댑터 객체. 미설정이거나 불러올 수 없으면 None
        (호출부가 requests 직접 호출로 넘어간다)
    """
    key = (server_url, model)
    if key in _adapter_cache:
        return _adapter_cache[key]

    name = adapter_module_name()
    if not name:
        _adapter_cache[key] = None      # 기본 경로 — requests 로 직접 호출
        return None

    try:
        import importlib

        create_llm_adapter = importlib.import_module(name).create_llm_adapter
    except (ImportError, AttributeError) as exc:
        _log.warning(
            "어댑터 %r 를 쓸 수 없어 requests 로 직접 호출합니다 (%s). "
            "DOCSTRUCT_LLM_ADAPTER 로 다른 모듈을 지정할 수 있습니다.",
            name, type(exc).__name__,
        )
        _adapter_cache[key] = None
        return None

    adapter = create_llm_adapter(
        "custom_server",
        model_name=model or "default",
        server_url=server_url,
    )
    _log.info("외부 어댑터 사용: %s", name)
    _adapter_cache[key] = adapter
    return adapter


#: 재시도 대상 HTTP 상태 (429=rate limit, 5xx=일시 장애)
_RETRY_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})
MAX_RETRIES = 4
BACKOFF_BASE = 2.0


def _error_detail(response: Any) -> str:
    """오류 응답 본문에서 원인을 뽑는다.

    입력: response — HTTP 응답
    출력: 사람이 읽을 수 있는 오류 메시지 (최대 300자)
    """
    try:
        body = response.json()
    except Exception:
        return (response.text or "")[:300]
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        parts = [err.get("message"), err.get("code"), err.get("type")]
        return " / ".join(str(p) for p in parts if p)[:300]
    return str(body)[:300]


def _retry_delay(response: Any, attempt: int) -> float:
    """재시도 대기 시간을 정한다.

    입력: response — HTTP 응답, attempt — 시도 횟수
    출력: 대기 초. Retry-After 헤더가 있으면 우선, 없으면 지수 백오프
    """
    header = response.headers.get("Retry-After") if response is not None else None
    if header:
        try:
            return min(float(header), 60.0)
        except ValueError:
            pass
    return min(BACKOFF_BASE ** attempt, 30.0)


def _short_connection_reason(exc: Exception) -> str:
    """연결 실패 사유를 한 줄로 줄인다.

    입력: exc — requests 의 ConnectionError
    출력: 짧은 사유 문자열
    """
    detail = str(exc)
    if "Connection refused" in detail or "10061" in detail:
        return "연결 거부 (서버가 내려갔거나 포트가 다름)"
    if "No route to host" in detail or "10065" in detail:
        return "경로 없음 (네트워크 분리)"
    if "Name or service not known" in detail or "11001" in detail:
        return "이름 해석 실패 (DNS)"
    if "timed out" in detail.lower() or "10060" in detail:
        return "응답 없음 (방화벽 가능성)"
    return type(exc).__name__


def _requests_fallback(
    prompt: str,
    cfg: dict[str, Any],
    *,
    image_urls: list[str] | None = None,
) -> str:
    """requests 로 직접 호출한다 (어댑터 미설치 시).

    입력: prompt, cfg, image_urls
    출력: 응답 본문 문자열
    동작: OpenAI 호환 chat completions 형식으로 POST 하고 choices[0].message.content 를 반환.
          429·5xx 는 Retry-After 를 존중해 재시도한다
    """
    import requests

    key_url, key_model = cfg["url"], cfg.get("model") or ""
    skip = unreachable_reason(key_url, key_model)
    if skip:
        raise LLMUnreachableError(f"{key_url} 연결 불가 — {skip}")

    if image_urls:
        content: Any = [
            {"type": "text", "text": prompt},
            *[{"type": "image_url", "image_url": {"url": url}} for url in image_urls],
        ]
    else:
        content = prompt

    payload: dict[str, Any] = {"messages": [{"role": "user", "content": content}]}
    if cfg.get("model"):
        payload["model"] = cfg["model"]

    headers = {"Content-Type": "application/json", **(cfg.get("headers") or {})}
    last_error = ""

    for attempt in range(MAX_RETRIES):
        try:
            response = requests.post(
                cfg["url"],
                json=payload,
                headers=headers,
                # (연결, 응답) 을 분리한다. 연결은 금방 되거나 안 되거나이므로
                # 오래 기다릴 이유가 없다. 응답 생성은 오래 걸릴 수 있다.
                timeout=(CONNECT_TIMEOUT, cfg["timeout"]),
            )
        except requests.exceptions.ConnectionError as exc:
            # 연결 자체가 안 되는 상태는 재시도해도 같다.
            # 첫 실패에서 표시해 두고 이후 호출은 건너뛴다.
            reason = _short_connection_reason(exc)
            mark_unreachable(key_url, key_model, reason)
            raise LLMUnreachableError(f"{key_url} 연결 불가 — {reason}") from exc
        except requests.exceptions.Timeout:
            last_error = f"타임아웃 ({cfg['timeout']}초)"
            if attempt == MAX_RETRIES - 1:
                break
            time.sleep(BACKOFF_BASE ** attempt)
            continue

        if response.status_code in _RETRY_STATUS and attempt < MAX_RETRIES - 1:
            delay = _retry_delay(response, attempt)
            _log.warning(
                "LLM HTTP %s — %.1f초 후 재시도 (%d/%d)",
                response.status_code, delay, attempt + 1, MAX_RETRIES - 1,
            )
            time.sleep(delay)
            continue

        if response.status_code >= 400:
            raise RuntimeError(
                f"LLM 요청 실패 HTTP {response.status_code}: {_error_detail(response)}"
            )

        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        text = (message.get("content") or "").strip()

        # 추론형 모델은 사고 토큰만 쓰고 본문이 비어 돌아올 수 있습니다.
        if not text and choices[0].get("finish_reason") == "length":
            _log.warning(
                "응답이 길이 제한으로 잘렸습니다 (출력 토큰 소진) — "
                "더 작은 페이지로 나누거나 다른 모델을 쓰세요."
            )
        return text

    raise RuntimeError(f"LLM 요청 실패 — {last_error or '재시도 초과'}")


def invoke_llm(
    prompt: str,
    *,
    span_name: str = "invoke",
    image_urls: list[str] | None = None,
    cfg: dict[str, Any] | None = None,
) -> str:
    """프롬프트를 LLM 에 보내고 응답을 받는다.

    입력:
        prompt      보낼 텍스트
        span_name   호출 구분용 이름 (로깅·추적)
        image_urls  첨부할 이미지 data URI 목록
        cfg         엔드포인트 설정. None 이면 전역 설정 사용
    출력: 응답 본문 문자열
    예외: 설정 누락·재시도 초과·4xx 응답 시 RuntimeError
    비고:
        기본 엔드포인트에 **연결 자체가 안 되면** 설정된 대비 엔드포인트로
        한 번 전환한다 (``DOCLING_TABLE_API_FALLBACK_*``). 인증 실패나
        잘못된 응답은 전환 대상이 아니다 — 그건 설정 문제이지 가용성
        문제가 아니기 때문이다.
    """
    settings = get_settings()
    primary = settings.llm

    # 로컬 VLM 이 지정돼 있으면 HTTP 대신 그것을 쓴다.
    # 호출부(assess/fill)가 성능상 cfg 를 미리 얻어 넘기는 경우도 포함해야
    # 하므로, cfg 를 줬는지가 아니라 **기본 엔드포인트를 향한 요청인지**로
    # 판단한다 (대비책 전환과 같은 기준).
    if settings.local_vlm is not None:
        to_primary = cfg is None or (
            primary is not None and cfg.get("url") == primary.url
        )
        if to_primary:
            return _invoke_local(prompt, settings.local_vlm, image_urls=image_urls)

    if cfg is None:
        if primary is None:
            raise RuntimeError(
                "LLM API 미설정 — .env 의 DOCLING_TABLE_API_URL 을 확인하세요."
            )
        cfg = primary.as_dict()

    # 폴백 대상은 "기본 엔드포인트로 보낸 요청"이다.
    # 호출부가 성능상 cfg 를 미리 얻어 넘기는 경우(assess/fill)도 포함해야 하므로,
    # cfg 를 줬는지가 아니라 **주소가 기본 엔드포인트와 같은지**로 판단한다.
    is_primary = primary is not None and cfg.get("url") == primary.url

    try:
        return _invoke_one(prompt, cfg, span_name=span_name, image_urls=image_urls)
    except LLMUnreachableError:
        # 기본 엔드포인트가 아닌 곳(호출부가 지정한 다른 주소)은 그대로 둔다.
        if not is_primary:
            raise
        backup = _fallback_cfg()
        if backup is None:
            raise
        _announce_fallback(backup)
        return _invoke_one(
            prompt, backup, span_name=span_name, image_urls=image_urls
        )


def _invoke_local(prompt: str, vlm: Any, *, image_urls: list[str] | None) -> str:
    """로컬 VLM 으로 호출한다.

    입력: prompt, vlm — LocalVLM 설정, image_urls — data URI 목록
    출력: 응답 본문 문자열
    예외: transformers 미설치·모델 로드 실패 시 RuntimeError
    """
    from docstruct.infrastructure.llm import local_vlm

    _announce_local(vlm)
    return local_vlm.invoke(prompt, image_urls=image_urls, **vlm.as_dict())


def _announce_local(vlm: Any) -> None:
    """로컬 VLM 사용을 한 번만 알린다.

    입력: vlm — LocalVLM 설정
    출력: 없음
    """
    global _LOCAL_ANNOUNCED
    with _UNREACHABLE_LOCK:
        if _LOCAL_ANNOUNCED:
            return
        _LOCAL_ANNOUNCED = True
    _log.info(
        "로컬 VLM 사용: %s (device=%s, dtype=%s) — HTTP 엔드포인트를 쓰지 않습니다",
        vlm.model_id, vlm.device, vlm.dtype,
    )


def _fallback_cfg() -> dict[str, Any] | None:
    """대비 엔드포인트 설정을 dict 로 얻는다.

    입력: 없음
    출력: 설정 dict. 대비책이 없으면 None
    """
    endpoint = get_settings().llm_fallback
    return endpoint.as_dict() if endpoint else None


def _announce_fallback(cfg: dict[str, Any]) -> None:
    """대비 엔드포인트로 전환한다는 사실을 한 번만 알린다.

    입력: cfg — 전환할 엔드포인트 설정
    출력: 없음
    """
    global _FALLBACK_ANNOUNCED
    with _UNREACHABLE_LOCK:
        if _FALLBACK_ANNOUNCED:
            return
        _FALLBACK_ANNOUNCED = True
    _log.warning(
        "기본 LLM 에 연결할 수 없어 대비 엔드포인트로 전환합니다 — %s (%s)",
        cfg.get("model"), cfg.get("url"),
    )


def _invoke_one(
    prompt: str,
    cfg: dict[str, Any],
    *,
    span_name: str,
    image_urls: list[str] | None,
) -> str:
    """엔드포인트 하나로 호출한다 (전환 없음).

    입력: prompt, cfg, span_name, image_urls
    출력: 응답 본문 문자열
    """
    adapter = _get_adapter(str(cfg["server_url"]), str(cfg.get("model") or ""))
    if adapter is None:
        return _requests_fallback(prompt, cfg, image_urls=image_urls)

    response = adapter.invoke(prompt, image_urls=image_urls, span_name=span_name)
    return (response.content or "").strip()
