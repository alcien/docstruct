"""Windows 비 UTF-8 로케일 우회.

입력:
    (없음)

역할:
    cp949 같은 로케일에서 PyTorch/Docling 초기화가 파일을 읽다 실패하는
    문제를 우회한다. 해당 환경이 아니면 아무 일도 하지 않는다.
호출부:
    converters.pdf.docling_backend (컨버터 생성 직전)
    notebooks/preview.ipynb (첫 셀)
    docstruct.core.checks (진단 표시)
출력:
    없음 (환경변수 설정과 함수 패치). diagnose() 는 진단 문자열
"""
from __future__ import annotations

import locale
import logging
import os
import sys

_log = logging.getLogger(__name__)

_applied = False


def is_windows() -> bool:
    """Windows 인지.

    입력: 없음
    출력: True/False
    """
    return os.name == "nt"


def preferred_encoding() -> str:
    """시스템 기본 인코딩.

    입력: 없음
    출력: 인코딩 이름 (예: 'cp949')
    """
    try:
        return locale.getpreferredencoding(False) or "unknown"
    except Exception:
        return "unknown"


def is_utf8_locale() -> bool:
    """기본 인코딩이 UTF-8 인지.

    입력: 없음
    출력: True/False
    """
    return preferred_encoding().lower().replace("-", "") in ("utf8", "cp65001")


def needs_fix() -> bool:
    """우회가 필요한 환경인지 판별한다.

    입력: 없음
    출력: Windows 이고 UTF-8 로케일이 아니면 True
    """
    return is_windows() and not is_utf8_locale()


#: 콘솔이 못 찍는 문자를 대신할 글자.
#: cp949(윈도우 한국어 기본)에는 줄표(`—`)·기호(`⚠`·`✅`)가 없어, 이 문자가
#: 든 줄을 찍는 순간 `UnicodeEncodeError` 로 **프로그램이 죽는다.**
#: 실측: 본체 출력·로그 96곳에 이런 문자가 들어 있었고 그중 91곳이 줄표다.
_CONSOLE_SUBSTITUTES = {
    "—": "-", "–": "-", "―": "-",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "⚠": "[!]", "✅": "[v]", "❌": "[x]", "️": "",   # 마지막은 변이 선택자
    "·": "·",                                        # cp949 에 있다 — 그대로
}

_ERROR_HANDLER = "docstruct_console"
_console_ready = False


def _substitute(error: UnicodeError):
    """콘솔이 못 찍는 문자를 비슷한 글자로 바꾼다.

    입력: error — UnicodeEncodeError
    출력: (대체 문자열, 이어서 인코딩할 위치)
    비고:
        codecs 오류 처리기 규약을 따른다. 표에 없으면 `?` 로 둔다 —
        **어떤 경우에도 예외를 다시 던지지 않는다.** 출력 한 글자 때문에
        처리 결과를 잃는 것이 훨씬 나쁘다.
    """
    text = error.object[error.start:error.end]          # type: ignore[union-attr]
    return ("".join(_CONSOLE_SUBSTITUTES.get(ch, "?") for ch in text), error.end)


def make_console_safe() -> bool:
    """콘솔 출력이 인코딩 때문에 죽지 않게 한다.

    입력: 없음
    출력: 손을 댔으면 True
    비고:
        `sys.stdout`/`sys.stderr` 의 오류 처리기를 바꿔, 콘솔이 못 찍는
        문자를 비슷한 글자로 대신하게 한다. **UTF-8 콘솔에서는 아무 일도
        하지 않는다** — 바꿀 필요가 없다.

        인코딩 자체를 UTF-8 로 돌리지 않는 이유: 코드페이지가 949 인 콘솔에
        UTF-8 바이트를 보내면 한글이 통째로 깨진다. 못 찍는 몇 글자만
        바꾸는 편이 안전하다.
    """
    global _console_ready
    if _console_ready:
        return False

    import codecs

    try:
        codecs.lookup_error(_ERROR_HANDLER)
    except LookupError:
        codecs.register_error(_ERROR_HANDLER, _substitute)

    touched = False
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", "") or "").lower()
        if encoding.replace("-", "") in ("utf8", "utf8mb4"):
            continue                                    # 이미 안전하다
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue                                    # 파이프·리다이렉트 등
        try:
            reconfigure(errors=_ERROR_HANDLER)
            touched = True
        except (ValueError, OSError):                   # noqa: PERF203
            continue
    _console_ready = True
    return touched


def _disable_torch_compile() -> bool:
    """torch.compile 을 끈다.

    입력: 없음
    출력: 없음
    """
    if os.environ.get("TORCHDYNAMO_DISABLE") == "1":
        return False
    os.environ["TORCHDYNAMO_DISABLE"] = "1"
    return True


def _patch_inductor_load_template() -> bool:
    """템플릿 로더가 UTF-8 로 읽도록 패치한다.

    입력: 없음
    출력: 패치했으면 True
    """
    if "torch" not in sys.modules:
        return False
    try:
        from torch._inductor import utils as inductor_utils
    except Exception:
        return False

    original = getattr(inductor_utils, "load_template", None)
    if original is None or getattr(original, "_docstruct_patched", False):
        return False

    from pathlib import Path

    def load_template(name: str, template_dir) -> str:  # type: ignore[no-untyped-def]
        """torch inductor 의 템플릿 로더를 경로 결합 방식으로 대체한다.

        입력: name — 템플릿 이름, template_dir — 템플릿 폴더
        출력: 템플릿 파일 내용 문자열
        """
        return Path(template_dir, f"{name}.py.jinja").read_text(encoding="utf-8")

    load_template._docstruct_patched = True  # type: ignore[attr-defined]
    inductor_utils.load_template = load_template  # type: ignore[assignment]
    return True


def apply(*, force: bool = False, verbose: bool = True) -> list[str]:
    """필요한 경우 우회를 적용한다.

    입력: 없음
    출력: 적용했으면 True, 해당 환경이 아니면 False
    비고: 반드시 torch/docling import 전에 호출해야 한다
    """
    global _applied

    if not (force or needs_fix()):
        return []

    actions: list[str] = []
    if _disable_torch_compile():
        actions.append("TORCHDYNAMO_DISABLE=1 (torch.compile 무력화)")
    if _patch_inductor_load_template():
        actions.append("torch._inductor.utils.load_template → UTF-8")

    _applied = True

    if verbose and actions:
        print(f"Windows 인코딩 우회 적용 (기본 코덱: {preferred_encoding()})")
        for a in actions:
            print(f"  · {a}")
        if not is_utf8_locale():
            print("  ※ 근본 해결은 PYTHONUTF8=1 입니다 — docstruct.winfix.instructions() 참고")
    return actions


def instructions() -> str:
    """영구 해결 방법 안내문.

    입력: 없음
    출력: 설정 방법을 담은 문자열
    """
    return (
        f"현재 기본 코덱: {preferred_encoding()}  (UTF-8 아님)\n"
        "\n"
        "PyTorch 가 UTF-8 파일을 기본 코덱으로 읽다 죽는 문제입니다.\n"
        "아래 중 하나로 영구 해결하세요. **모두 재시작이 필요합니다.**\n"
        "\n"
        "① 이 세션만 (PowerShell 에서 Jupyter 를 다시 띄우기)\n"
        "     $env:PYTHONUTF8 = \"1\"\n"
        "     jupyter lab\n"
        "\n"
        "② 사용자 계정에 영구 적용 (PowerShell, 이후 터미널·Anaconda 재시작)\n"
        "     [Environment]::SetEnvironmentVariable(\"PYTHONUTF8\", \"1\", \"User\")\n"
        "\n"
        "③ Windows 전체를 UTF-8 로 (재부팅 필요)\n"
        "     제어판 → 국가 또는 지역 → 관리자 옵션 →\n"
        "     시스템 로캘 변경 → 'Beta: 세계 언어 지원을 위해 Unicode UTF-8 사용' 체크\n"
        "\n"
        "임시로는 docstruct.winfix.apply() 가 torch.compile 을 꺼서 우회합니다\n"
        "(추론 속도에만 영향, 파싱 결과는 동일).\n"
    )


def diagnose(*, show_instructions: bool = True) -> None:
    """현재 환경 진단.

    입력: 없음
    출력: OS·인코딩·적용 여부를 담은 문자열
    """
    print(f"플랫폼        : {sys.platform} ({'Windows' if is_windows() else 'POSIX'})")
    print(f"기본 코덱     : {preferred_encoding()}")
    print(f"UTF-8 모드    : {'ON' if os.environ.get('PYTHONUTF8') == '1' else 'OFF'}")
    print(f"torch.compile : {'비활성' if os.environ.get('TORCHDYNAMO_DISABLE') == '1' else '활성'}")

    if not needs_fix():
        print("\n문제 없음 — 우회가 필요하지 않습니다.")
        return

    print("\n⚠ cp949 크래시가 발생할 수 있는 환경입니다.")
    if _applied:
        print("  런타임 우회는 이미 적용되어 있습니다.")
    if show_instructions:
        print()
        print(instructions())
