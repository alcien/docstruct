"""LLM 호출 클라이언트.

역할:
    설정된 엔드포인트로 프롬프트(및 이미지)를 보내고 응답 텍스트를 받는다.
    llm_request 어댑터가 설치되어 있으면 그것을 쓰고, 없으면 requests 로
    직접 호출한다. 어느 경로든 목적지와 페이로드 형식은 동일하다.
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
from typing import Any

from docstruct.core.config import get_settings

_log = logging.getLogger(__name__)

#: 엔드포인트에 연결 자체가 안 되면 이후 호출을 건너뛴다.
#: 페이지·표마다 같은 오류를 반복 시도하면 시간만 쓰고 로그만 더럽힌다.
#: (url, model) 별로 기록하며, clear_adapter_cache() 로 초기화된다.
_UNREACHABLE: dict[tuple[str, str], str] = {}
_UNREACHABLE_LOCK = threading.Lock()

#: 대비 엔드포인트 전환을 알렸는지 (한 번만 로그)
_FALLBACK_ANNOUNCED = False


class LLMUnreachableError(RuntimeError):
    """엔드포인트에 연결할 수 없어 호출을 건너뛴 경우."""


def mark_unreachable(url: str, model: str, reason: str) -> None:
    """이 엔드포인트를 이번 실행에서 도달 불가로 표시한다.

    입력: url, model, reason — 최초 실패 사유
    출력: 없음
    """
    with _UNREACHABLE_LOCK:
        if (url, model) not in _UNREACHABLE:
            _UNREACHABLE[(url, model)] = reason
            _log.warning(
                "%s 연결 불가 — 이후 LLM 호출을 건너뜁니다 (%s)", url, reason
            )


def unreachable_reason(url: str, model: str) -> str | None:
    """도달 불가로 표시됐으면 그 사유를 돌려준다.

    입력: url, model
    출력: 사유 문자열, 표시되지 않았으면 None
    """
    with _UNREACHABLE_LOCK:
        return _UNREACHABLE.get((url, model))


def reset_unreachable() -> None:
    """도달 불가 표시를 초기화한다.

    입력: 없음
    출력: 없음
    """
    global _FALLBACK_ANNOUNCED
    with _UNREACHABLE_LOCK:
        _UNREACHABLE.clear()
        _FALLBACK_ANNOUNCED = False

_adapter_cache: dict[tuple[str, str], Any] = {}


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


def _get_adapter(server_url: str, model: str) -> Any | None:
    """llm_request 어댑터를 얻는다.

    입력: server_url, model
    출력: 어댑터 객체. llm_request 미설치면 None (호출부가 폴백으로 전환)
    """
    key = (server_url, model)
    if key in _adapter_cache:
        return _adapter_cache[key]

    try:
        from llm_request import create_llm_adapter
    except ImportError:
        _log.warning("llm_request 미설치 — requests fallback 사용")
        _adapter_cache[key] = None
        return None

    adapter = create_llm_adapter(
        "custom_server",
        model_name=model or "default",
        server_url=server_url,
    )
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
    import time

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
                cfg["url"], json=payload, headers=headers, timeout=cfg["timeout"]
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
    explicit_cfg = cfg is not None

    if cfg is None:
        endpoint = get_settings().llm
        if endpoint is None:
            raise RuntimeError(
                "LLM API 미설정 — .env 의 DOCLING_TABLE_API_URL 을 확인하세요."
            )
        cfg = endpoint.as_dict()

    try:
        return _invoke_one(prompt, cfg, span_name=span_name, image_urls=image_urls)
    except LLMUnreachableError:
        # 호출부가 cfg 를 직접 준 경우는 그 엔드포인트만 쓰겠다는 뜻이므로
        # 임의로 다른 곳에 보내지 않는다.
        if explicit_cfg:
            raise
        backup = _fallback_cfg()
        if backup is None:
            raise
        _announce_fallback(backup)
        return _invoke_one(
            prompt, backup, span_name=span_name, image_urls=image_urls
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
