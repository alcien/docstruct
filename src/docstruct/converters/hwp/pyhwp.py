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

import os
import shutil
import subprocess
import sys

from docstruct.converters.deps import BS4_AVAILABLE, BeautifulSoup, PYHWP_AVAILABLE

#: Jupyter/GUI 에서 자식 프로세스가 콘솔 창을 띄우지 않게 합니다 (Windows 전용).
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _hwp5html_command() -> list[str]:
    """``hwp5html`` 실행 방법을 결정합니다.

    Windows 에서는 ``<venv>\\Scripts\\hwp5html.exe`` 로 설치되는데, Jupyter 를
    가상환경 밖에서 띄웠거나 PATH 에 Scripts 가 없으면 찾지 못합니다.
    그 경우 같은 인터프리터의 모듈 진입점으로 우회합니다.
    """
    found = shutil.which("hwp5html")
    if found:
        return [found]
    return [sys.executable, "-m", "hwp5.hwp5html"]


def hwp_to_html_str(hwp_path: str) -> tuple[str, str]:
    """hwp5html CLI로 HWP를 HTML 문자열로 변환합니다. (html, stderr) 반환."""
    if not PYHWP_AVAILABLE:
        raise RuntimeError(
            "pyhwp가 필요합니다: pip install pyhwp\n"
            "pyhwp 없이는 텍스트 전용 폴백만 사용 가능"
        )
    try:
        result = subprocess.run(
            [*_hwp5html_command(), "--html", hwp_path],
            capture_output=True, text=True, encoding="utf-8",
            timeout=120, creationflags=_NO_WINDOW,
        )
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

def assess_pyhwp_html(html: str, stderr: str, file_size: int) -> bool:
    """
    pyhwp HTML이 본문 추출에 불충분한지 판별합니다.

    필드(누름틀) 문서에서 'unmatched field end' 후 내용이 비는 경우 등.
    """
    if "unmatched field end" in stderr.lower():
        return True
    if not BS4_AVAILABLE:
        return len(html) < 500 and file_size > 10_000

    soup = BeautifulSoup(html, "html.parser")
    body_text = soup.get_text(strip=True)
    if file_size > 10_000 and len(body_text) < 500:
        return True

    tables = soup.find_all("table")
    if tables:
        cells = soup.find_all("td")
        if cells:
            filled = sum(1 for td in cells if td.get_text(strip=True))
            if len(cells) >= 10 and filled <= max(3, len(cells) // 6):
                return True
    return False
