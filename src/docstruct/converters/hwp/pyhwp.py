"""pyhwp 로 HWP → HTML 변환.

역할:
    hwp5html 을 실행해 HTML 을 얻고, 결과가 쓸 만한지 판정한다.
    Windows 에서 실행 파일을 못 찾는 경우를 우회한다.
호출부:
    converters.hwp.converter
출력:
    (HTML 문자열, stderr) 및 품질 판정 결과
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys

from docstruct.converters.deps import BS4_AVAILABLE, BeautifulSoup, PYHWP_AVAILABLE

#: Jupyter/GUI 에서 자식 프로세스가 콘솔 창을 띄우지 않게 합니다 (Windows 전용).
_log = logging.getLogger(__name__)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _hwp5html_command() -> list[str]:
    """`hwp5html` 실행 방법을 결정한다.

    입력: 없음
    출력: subprocess 에 넘길 명령 목록
    동작: PATH 에서 실행파일을 먼저 찾고, 없으면 같은 인터프리터의 모듈
          진입점(`-m hwp5.hwp5html`)으로 우회한다. Windows 에서 Jupyter 를
          가상환경 밖에서 띄우면 Scripts 가 PATH 에 없어 못 찾는 경우가 흔하다.
    """
    found = shutil.which("hwp5html")
    if found:
        return [found]
    return [sys.executable, "-m", "hwp5.hwp5html"]


class HwpTimeout(RuntimeError):
    """hwp5html 이 제한 시간 안에 끝나지 않은 경우."""


def html_timeout() -> float:
    """hwp5html 제한 시간(초).

    입력: 없음
    출력: `DOCSTRUCT_HWP_TIMEOUT` 값 또는 기본 300
    비고:
        표·이미지가 많은 큰 문서는 hwp5html 이 매우 느리다. 3.5MB 문서가
        수 분을 넘기기도 한다. 넘기면 텍스트 전용 경로로 내려간다.
    """
    import os

    raw = os.environ.get("DOCSTRUCT_HWP_TIMEOUT", "").strip()
    try:
        return float(raw) if raw else 300.0
    except ValueError:
        return 300.0


def hwp_to_html_str(hwp_path: str) -> tuple[str, str]:
    """hwp5html CLI 로 HWP 를 HTML 문자열로 변환한다.

    입력: hwp_path — HWP 파일 경로
    출력: (html, stderr) 튜플
    예외:
        HwpTimeout   제한 시간 초과 (호출부가 텍스트 폴백으로 전환)
        RuntimeError 실행파일 부재·pyhwp 미설치 등 그 밖의 실패
    비고: 제한 시간은 DOCSTRUCT_HWP_TIMEOUT (기본 300초) 를 따른다.
    """
    if not PYHWP_AVAILABLE:
        raise RuntimeError(
            "pyhwp가 필요합니다: pip install pyhwp\n"
            "pyhwp 없이는 텍스트 전용 폴백만 사용 가능"
        )
    limit = html_timeout()
    size_mb = os.path.getsize(hwp_path) / 1_048_576
    if size_mb > 1.0:
        _log.info(
            "HWP → HTML 변환 중 (%.1fMB) — 큰 문서는 몇 분 걸릴 수 있습니다 "
            "(제한 %.0f초, DOCSTRUCT_HWP_TIMEOUT 로 조정)",
            size_mb, limit,
        )
    try:
        result = subprocess.run(
            [*_hwp5html_command(), "--html", hwp_path],
            capture_output=True, text=True, encoding="utf-8",
            timeout=limit, creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise HwpTimeout(
            f"hwp5html 이 {limit:.0f}초 안에 끝나지 않았습니다 ({size_mb:.1f}MB).\n"
            "  표 구조 없이 텍스트만 뽑아 계속합니다.\n"
            "  더 기다리려면 DOCSTRUCT_HWP_TIMEOUT 을 늘리세요 (초 단위)."
        ) from exc
    except FileNotFoundError as exc:
        raise RuntimeError(
            "hwp5html 실행파일을 찾지 못했습니다.\n"
            "  Windows: 가상환경을 활성화한 상태에서 실행하거나, "
            "<venv>\\Scripts 를 PATH 에 추가하세요.\n"
            "  확인: python -c \"import shutil; print(shutil.which(\'hwp5html\'))\""
        ) from exc
    if result.returncode != 0:
        raise RuntimeError(f"hwp5html 실패:\n{result.stderr.strip()}")
    return result.stdout, result.stderr or ""

#: 본문이 이보다 적으면 추출이 실패한 것으로 본다 (파일이 클 때만 적용).
MIN_BODY_CHARS = 500

#: 이 크기 이하의 파일은 원래 내용이 적을 수 있어 본문 길이로 판정하지 않는다.
MIN_FILE_SIZE = 10_000

#: 필드 경고가 있을 때 적용하는 완화된 본문 하한. 경고가 떠도 이만큼 나오면
#: 변환은 성공한 것으로 본다.
FIELD_WARNING_BODY_CHARS = 1_000


def assess_pyhwp_html(html: str, stderr: str, file_size: int) -> bool:
    """pyhwp HTML 이 본문 추출에 불충분한지 판별한다 (사유 없이 여부만).

    입력: html, stderr, file_size
    출력: 불충분하면 True
    비고: 사유가 필요하면 pyhwp_html_verdict() 를 쓴다.
    """
    return pyhwp_html_verdict(html, stderr, file_size)[0]


def pyhwp_html_verdict(html: str, stderr: str, file_size: int) -> tuple[bool, str]:
    """pyhwp HTML 이 본문 추출에 불충분한지 판별하고 사유를 함께 돌려준다.

    입력:
        html       hwp5html 이 만든 XHTML
        stderr     hwp5html 의 경고 출력
        file_size  원본 HWP 크기(bytes)
    출력: (불충분 여부, 사유 설명)
    비고:
        예전에는 stderr 에 ``unmatched field end`` 가 보이기만 하면 결과를
        보지도 않고 폴백했다. 그런데 pyhwp 는 필드 경고를 흘리면서도 변환을
        정상으로 끝내는 경우가 많다. 실제로 표 67개·본문 8천여 자가 멀쩡히
        나온 문서가 이 규칙 하나로 통째로 버려졌다.

        지금은 경고를 **즉시 폴백 사유가 아니라 의심 신호**로 쓴다. 결과가
        빈약할 때만 폴백하고, 내용이 충분하면 그대로 쓴다.
    """
    field_warning = "unmatched field end" in stderr.lower()

    if not BS4_AVAILABLE:
        # 파싱 없이 판단해야 하므로 원문 길이만 본다.
        if len(html) < MIN_BODY_CHARS and file_size > MIN_FILE_SIZE:
            return True, f"HTML 이 {len(html)}자뿐 (bs4 없음)"
        if field_warning and len(html) < FIELD_WARNING_BODY_CHARS:
            return True, f"필드 경고 + HTML {len(html)}자 (bs4 없음)"
        return False, f"HTML {len(html)}자 (bs4 없음 — 표 검사 생략)"

    soup = BeautifulSoup(html, "html.parser")
    body_text = soup.get_text(strip=True)

    if file_size > MIN_FILE_SIZE and len(body_text) < MIN_BODY_CHARS:
        return True, (
            f"본문이 {len(body_text)}자뿐 — 원본 {file_size:,}bytes 에 비해 너무 적음"
        )

    tables = soup.find_all("table")
    cells = soup.find_all("td")
    filled = sum(1 for td in cells if td.get_text(strip=True))
    if tables and cells and len(cells) >= 10:
        threshold = max(3, len(cells) // 6)
        if filled <= threshold:
            return True, (
                f"표 {len(tables)}개 · 셀 {len(cells)}개 중 내용 있는 셀이 "
                f"{filled}개뿐 (기준 {threshold})"
            )

    if field_warning:
        # 필드 경고가 있으면 기준을 높여 한 번 더 본다. 표가 살아 있으면
        # 구조 손실이 훨씬 크므로 폴백하지 않는다.
        if filled >= 10:
            return False, (
                f"필드 경고가 있으나 표가 살아 있음 (표 {len(tables)}개 · "
                f"내용 있는 셀 {filled}개) — HTML 그대로 사용"
            )
        if len(body_text) < FIELD_WARNING_BODY_CHARS:
            return True, f"필드 경고 + 본문 {len(body_text)}자 (표도 없음)"
        return False, f"필드 경고가 있으나 본문 {len(body_text)}자 확보"

    return False, (
        f"정상 — 본문 {len(body_text)}자 · 표 {len(tables)}개 · 내용 있는 셀 {filled}개"
    )
