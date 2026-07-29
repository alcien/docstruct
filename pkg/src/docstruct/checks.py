"""실행 환경·LLM 연결 점검.

역할:
    파싱을 돌리기 전에 무엇이 준비되어 있고 무엇이 빠졌는지 확인한다.
    설정값 유무만이 아니라 LLM 엔드포인트에 실제로 도달하는지도 호출해 본다.
    .env 를 고친 뒤 커널 재시작 없이 반영하는 경로도 제공한다.
호출부:
    notebooks/preview.ipynb (환경 표·연결 확인 셀)
    docstruct.cli --check
    docstruct.colab (check_llm_reachable 재노출)
출력:
    점검 항목 목록 / HTML 표 / (성공여부, 메시지) 튜플
"""
from __future__ import annotations

import html
import importlib.util
import os
import shutil
from typing import Any

from docstruct.core.config import get_settings, loaded_env_path, reload_config
from docstruct import winfix


def _version_note() -> str:
    """실행 중인 docstruct 버전과 위치.

    입력: 없음
    출력: 버전 + 경로 문자열
    비고:
        압축본을 직접 풀어 쓰는 경우 pip 메타데이터가 없으므로,
        패키지 옆의 VERSION 파일을 함께 본다. 어느 쪽도 없으면
        오래된 압축본일 가능성이 높다.
    """
    from importlib.metadata import PackageNotFoundError, version
    from pathlib import Path

    import docstruct

    root = Path(docstruct.__file__).resolve().parent
    try:
        return f"{version('docstruct')} (설치본) — {root}"
    except PackageNotFoundError:
        pass

    for candidate in (root / "VERSION", root.parent / "VERSION"):
        if candidate.is_file():
            return f"{candidate.read_text(encoding='utf-8').strip()} (압축본) — {root}"
    return f"버전 미상 (VERSION 파일 없음 — 오래된 압축본일 수 있음) — {root}"


def _dist_installed(name: str) -> bool:
    """배포 패키지(dist) 설치 여부.

    입력: name — 배포명 (모듈명이 아님)
    출력: pip 이 인식하면 True
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        version(name)
        return True
    except PackageNotFoundError:
        return False


def _docling_note(importable: bool, meta: bool, core: bool, executable: str) -> str:
    """PDF 파싱 항목의 설명 문구.

    입력:
        importable  `import docling` 가능 여부
        meta        docling 배포물 설치 여부
        core        docling-slim 설치 여부 (실제 코드가 여기 있음)
        executable  실행 중인 파이썬 경로
    출력: 상태에 맞는 안내 문자열
    """
    if importable:
        return "docling"
    if meta and not core:
        return (
            "docling 은 있으나 코드가 없음 — "
            f'"{executable}" -m pip install docling-slim'
        )
    return f'PDF 처리 불가 — "{executable}" -m pip install docling'


def _installed(module: str) -> bool:
    """모듈 설치 여부.

    입력: module — 모듈명
    출력: 설치되어 있으면 True (조회 실패 시 False)
    """
    import sys

    if module in sys.modules:
        return True
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def environment() -> list[dict[str, Any]]:
    """환경 점검 항목을 모은다.

    입력: 없음 (설치 패키지·설정값·시스템 정보를 읽음)
    출력: [{item, ok, note}] 목록 — 항목명, 정상 여부, 설명
    """
    settings = get_settings()

    import sys

    # docling 배포물에는 코드가 없다 — 실제 모듈은 docling-slim 이 제공한다.
    docling = _installed("docling")
    docling_meta = _dist_installed("docling")
    docling_core_pkg = _dist_installed("docling-slim")
    hwpx = _installed("hwpx")
    pyhwp = _installed("hwp5")
    pdfium = _installed("pypdfium2")
    hwp5html = shutil.which("hwp5html") is not None

    checks: list[dict[str, Any]] = [
        {
            "item": "docstruct",
            "ok": True,
            "note": _version_note(),
        },
        {
            "item": "파이썬",
            "ok": True,
            "note": f"{sys.version.split()[0]} — {sys.executable}",
        },
        {
            # .env 는 선택 사항이다 (내장 기본값으로 동작).
            # 없다고 경고하지 않고 어느 값을 쓰는지만 알린다.
            "item": "설정 출처",
            "ok": True,
            "note": (
                str(loaded_env_path())
                if loaded_env_path()
                else "내장 기본값 (.env 없음 — 덮으려면 cp .env.example .env)"
            ),
        },
        {
            "item": "PDF 파싱",
            "ok": docling,
            "note": _docling_note(docling, docling_meta, docling_core_pkg, sys.executable),
        },
        {
            "item": "HWP 파싱",
            "ok": pyhwp and hwp5html,
            "note": (
                "pyhwp + hwp5html"
                if (pyhwp and hwp5html)
                else "pyhwp 미설치/hwp5html 미탐지 — olefile 폴백(표 구조 손실)"
            ),
        },
        {
            "item": "HWPX 파싱",
            "ok": hwpx,
            "note": "python-hwpx" if hwpx else "pip install python-hwpx — HWPX 처리 불가",
        },
        {
            "item": "페이지 렌더",
            "ok": pdfium,
            "note": (
                "pypdfium2"
                if pdfium
                else "pip install pypdfium2 — 표 평가가 텍스트만으로 수행됨"
            ),
        },
        {
            "item": "CPU 코어",
            "ok": True,
            "note": (
                f"{os.cpu_count() or '?'}개 — CPU 계산형 설정"
                "(THREADED_PIPELINE·NUM_THREADS)의 상한. "
                "LLM_CONCURRENCY 는 I/O 대기라 코어 수와 무관합니다."
            ),
        },
        {
            "item": "텍스트 인코딩",
            "ok": not winfix.needs_fix(),
            "note": (
                winfix.preferred_encoding()
                if not winfix.needs_fix()
                else f"{winfix.preferred_encoding()} — PyTorch cp949 크래시 위험 "
                     "(런타임 우회 자동 적용). winfix.diagnose() 로 영구 해결법 확인"
            ),
        },
    ]

    for label, value, ok in settings.describe():
        checks.append({"item": label, "ok": ok, "note": value})

    return checks


def environment_html() -> str:
    """점검 결과를 HTML 표로 만든다.

    입력: 없음
    출력: HTML 문자열
    """
    rows = []
    for c in environment():
        mark = "✅" if c["ok"] else "⚠️"
        color = "#16a34a" if c["ok"] else "#d97706"
        rows.append(
            '<tr style="border-bottom:1px solid #f1f5f9;">'
            f'<td style="padding:4px 10px 4px 0;">{mark}</td>'
            f'<td style="padding:4px 14px 4px 0;white-space:nowrap;font-weight:600;">'
            f'{html.escape(c["item"])}</td>'
            f'<td style="padding:4px 0;color:{color};">{html.escape(str(c["note"]))}</td>'
            "</tr>"
        )
    return (
        '<table style="border-collapse:collapse;font-size:12.5px;">'
        + "".join(rows)
        + "</table>"
    )


def show_environment() -> None:
    """점검 결과를 노트북에 표시한다.

    입력: 없음
    출력: 없음 (화면 출력)
    """
    from IPython.display import HTML, display

    display(HTML(environment_html()))


def _in_container() -> bool:
    """컨테이너 안에서 실행 중인지 추정한다.

    입력: 없음
    출력: Docker/Podman 등 컨테이너로 보이면 True
    """
    import os
    from pathlib import Path

    if Path("/.dockerenv").exists():
        return True
    try:
        cgroup = Path("/proc/1/cgroup").read_text(encoding="utf-8", errors="ignore")
        return any(k in cgroup for k in ("docker", "kubepods", "containerd", "podman"))
    except OSError:
        return False


def _proxy_hint() -> str:
    """프록시 환경변수가 설정되어 있으면 알린다.

    입력: 없음
    출력: 설정된 프록시가 있으면 안내 문자열, 없으면 빈 문자열
    비고:
        requests 는 HTTP_PROXY / HTTPS_PROXY 를 자동으로 따릅니다. 사내
        프록시가 잡혀 있으면 사내 LLM 으로 직접 가지 않고 프록시를 거쳐
        거부될 수 있으므로, NO_PROXY 에 해당 호스트를 넣어야 합니다.
    """
    import os

    found = {
        k: v
        for k in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY")
        if (v := os.environ.get(k))
    }
    if not found:
        return ""
    names = ", ".join(f"{k}={v}" for k, v in found.items())
    no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or "(미설정)"
    return (
        f"\n         프록시가 설정되어 있습니다: {names}\n"
        f"         NO_PROXY={no_proxy}\n"
        "         사내 주소는 프록시를 거치지 않도록 NO_PROXY 에 넣으세요.\n"
        "         예: set NO_PROXY=218.145.29.207,localhost,127.0.0.1"
    )


def _connection_hint(exc: Exception, url: str) -> str:
    """연결 실패의 실제 사유와 대처를 문장으로 만든다.

    입력: exc — requests 의 ConnectionError, url — 시도한 주소
    출력: 사유 + 대처 안내 문자열
    비고:
        ConnectionError 는 "거부됨 / 경로 없음 / 이름 못 찾음 / 시간 초과" 가
        모두 같은 타입으로 오므로, 내부 원인 문자열을 보고 갈라준다.
        컨테이너 안이면 네트워크 격리가 가장 흔한 원인이라 함께 알린다.
    """
    from urllib.parse import urlparse

    detail = str(exc)
    host = urlparse(url).hostname or "?"
    port = urlparse(url).port or 80

    if (
        "Connection refused" in detail
        or "ECONNREFUSED" in detail
        or "10061" in detail          # WinError 10061 (연결 거부)
    ):
        cause = (
            f"{host}:{port} 가 연결을 **거부** — 호스트에는 닿았으나 그 포트가 열려 있지 않습니다.\n"
            "         방화벽 차단이면 보통 '응답 없음' 이 됩니다. 거부는 서버가 내려갔거나\n"
            "         포트가 바뀌었을 때 나옵니다.\n"
            f"         포트 확인 (Windows): Test-NetConnection {host} -Port {port}\n"
            f"         포트 확인 (Linux)  : nc -zv {host} {port}"
        )
    elif "No route to host" in detail or "EHOSTUNREACH" in detail:
        cause = f"{host} 로 가는 경로 없음 — 네트워크 분리 또는 라우팅 문제"
    elif "Name or service not known" in detail or "NameResolutionError" in detail:
        cause = f"{host} 이름을 찾을 수 없음 — DNS 문제"
    elif "timed out" in detail.lower():
        cause = f"{host}:{port} 응답 없음 — 방화벽에 막혔을 가능성"
    else:
        cause = f"{type(exc).__name__}"

    tail = _proxy_hint()
    if _in_container():
        tail = (
            "\n         컨테이너 안에서 실행 중입니다. 호스트는 되는데 여기서만 "
            "안 되면 네트워크 격리가 원인입니다.\n"
            "         확인: docker exec <컨테이너> "
            f"curl -sv --max-time 5 {url}\n"
            "         해결: docker run --network host ... "
            "(또는 compose 에 network_mode: host)"
        )
    return cause + tail


def check_llm_reachable(timeout: float = 5.0) -> tuple[bool, str]:
    """LLM 엔드포인트에 실제로 도달하는지 확인한다.

    입력: timeout — 응답 대기 초
    출력: (성공여부, 메시지). 실패 시 원인을 방화벽·인증·모델명으로 구분해 알려준다
    비고: 설정과 동일한 URL·모델로 1회 호출한다
    """
    endpoint = get_settings().llm
    if endpoint is None:
        return False, "LLM 미설정 — .env 의 DOCLING_TABLE_API_URL 을 확인하세요."

    import requests

    try:
        response = requests.post(
            endpoint.url,
            json={
                "model": endpoint.model or "default",
                "messages": [{"role": "user", "content": "ping"}],
                # max_tokens 는 일부러 빼둡니다: 추론형 모델은 이 필드를 거부하고
                # max_completion_tokens 를 요구하며, 값이 작으면 사고 토큰만
                # 소모하고 본문이 비어 돌아옵니다. 핑 1회 비용은 무시할 수준입니다.
            },
            # 인증 헤더 누락 시 유효한 키로도 401 이 납니다.
            headers={"Content-Type": "application/json", **endpoint.headers()},
            timeout=timeout,
        )
    except requests.exceptions.ConnectTimeout:
        return False, (
            f"{endpoint.url} 연결 시간 초과 — 방화벽에 막혔거나 "
            "(Colab 등 외부망에서 사내 엔드포인트 접근 시 흔함) 서버가 꺼져 있을 수 있습니다."
        )
    except requests.exceptions.ConnectionError as exc:
        return False, f"{endpoint.url} 연결 실패 — {_connection_hint(exc, endpoint.url)}"
    except Exception as exc:
        return False, f"{endpoint.url} 확인 실패 — {type(exc).__name__}: {exc}"

    if response.status_code < 400:
        return True, (
            f"{endpoint.url} 응답 정상 (HTTP {response.status_code}) "
            f"· model={endpoint.model or '(서버 기본)'}"
        )

    detail = ""
    try:
        err = response.json().get("error") or {}
        detail = f" — {err.get('message') or err.get('code') or ''}"
    except Exception:
        pass

    hint = {
        401: " (키가 없거나 잘못됨)",
        403: " (이 키로 해당 모델에 접근할 수 없습니다)",
        404: f" (모델 {endpoint.model!r} 이 존재하지 않습니다)",
        429: " (사용량 한도 초과 — 잠시 후 재시도하세요)",
    }.get(response.status_code, "")
    return False, f"{endpoint.url} HTTP {response.status_code}{detail}{hint}"


def show_llm_check(timeout: float = 5.0) -> None:
    """연결 확인 결과를 출력한다.

    입력: timeout
    출력: 없음 (화면 출력)
    """
    ok, message = check_llm_reachable(timeout=timeout)
    print(("✅ " if ok else "⚠️ ") + message)


def reload_environment() -> None:
    """.env 를 다시 읽고 캐시를 비운다.

    입력: 없음
    출력: 없음
    비고: 커널을 살린 채 설정을 바꿨을 때 쓴다
    """
    reload_config()
    invalidate_caches()
    show_environment()


def invalidate_caches() -> None:
    """LLM 어댑터와 Docling 파이프라인 캐시를 비운다.

    입력: 없음
    출력: 없음
    비고: 이걸 하지 않으면 설정을 바꿔도 최초 사용 시점 값이 계속 쓰인다
    """
    from docstruct.infrastructure.llm.client import clear_adapter_cache, reset_unreachable

    clear_adapter_cache()
    # 설정이 바뀌면 이전 실행의 "연결 불가" 표시도 무효다.
    reset_unreachable()

    try:
        from docstruct.converters.pdf.docling_backend import reset_document_converter

        reset_document_converter()
    except ImportError:
        pass  # docling 미설치 — PDF 파이프라인 자체가 없으므로 무관
