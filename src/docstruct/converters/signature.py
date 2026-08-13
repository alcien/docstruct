"""파일 시그니처로 실제 형식을 판별한다.

역할:
    확장자와 내용이 어긋난 파일을 알아본다. 실제 문서에서
    `2027년도 성과계획서_[24]기후에너지환경부.hwpx` 가 이름만 `.hwpx` 이고
    내용은 HWP 바이너리였다. 한글에서 "다른 이름으로 저장" 할 때 형식을
    `한글 문서(*.hwp)` 로 둔 채 파일명에 `.hwpx` 를 직접 타이핑하면 이렇게
    된다. 부처마다 담당자가 다르니 묶음 중 몇 건만 그런 경우가 생긴다.
호출부:
    docstruct.converters.registry, docstruct.extractors.registry
입력: 파일 경로
출력: 실제 형식 문자열 ('hwp' | 'hwpx' | 'pdf' | None)

왜 확장자를 믿지 않는가
--------------------
확장자는 사용자가 붙이는 이름표일 뿐이고, 내용은 저장할 때 고른 형식이
결정한다. 둘이 어긋나면 라이브러리가 엉뚱한 오류를 낸다 —
python-hwpx 는 `BadZipFile`, docling 은 파싱 실패다. 사용자는 "왜 HWPX 를
넣었는데 HWP 라고 하지?" 를 알 수 없다.

**다만 조용히 넘기지 않는다.** 잘못 저장된 파일은 다른 도구에서도 계속
문제를 일으키므로, 처리는 하되 경고를 남겨 사실을 알린다.
"""
from __future__ import annotations

import logging
from pathlib import Path

_log = logging.getLogger(__name__)

#: OLE2/CFBF 컨테이너 서명 — HWP 5.0 바이너리.
#: MS Office 구형 형식(.doc/.xls)도 같은 서명을 쓰므로, 이것만으로
#: "HWP 다" 라고 단정할 수는 없다. 확장자와 함께 판단한다.
OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

#: ZIP 서명 — HWPX·DOCX 등 OOXML 계열.
ZIP_MAGIC = b"PK\x03\x04"

#: PDF 서명.
PDF_MAGIC = b"%PDF-"

#: 시그니처를 읽을 바이트 수.
_HEAD_BYTES = 8


def read_signature(path: str | Path) -> bytes:
    """파일 앞부분을 읽는다.

    입력: path — 파일 경로
    출력: 앞 8바이트. 읽지 못하면 빈 바이트열
    """
    try:
        with open(path, "rb") as handle:
            return handle.read(_HEAD_BYTES)
    except OSError:
        return b""


def detect_format(path: str | Path) -> str | None:
    """내용으로 실제 형식을 알아본다.

    입력: path — 파일 경로
    출력: 'hwp' | 'hwpx' | 'pdf' | None (알 수 없음)
    비고:
        ZIP 서명은 HWPX·DOCX·일반 zip 이 공유하므로 'hwpx' 로 단정하지
        않고, 확장자가 `.hwpx` 일 때만 그렇게 본다. 여기서 하려는 것은
        형식 감별이 아니라 **어긋남 감지**다.
    """
    head = read_signature(path)
    if not head:
        return None
    if head.startswith(OLE2_MAGIC):
        return "hwp"
    if head.startswith(PDF_MAGIC):
        return "pdf"
    if head.startswith(ZIP_MAGIC):
        return "hwpx"
    return None


def effective_suffix(path: str | Path) -> str:
    """실제 내용에 맞는 확장자를 돌려준다.

    입력: path — 파일 경로
    출력: '.hwp' | '.hwpx' | '.pdf' — 어긋남이 없으면 원래 확장자 그대로
    비고:
        어긋난 경우 **경고를 남긴다.** 조용히 고쳐 주면 사용자는 파일이
        잘못 저장된 사실을 모른 채 다음에도 같은 일을 겪는다. 다른
        도구(한컴 뷰어·타 시스템)에서는 여전히 실패할 수 있다.
    """
    source = Path(path)
    declared = source.suffix.lower()
    actual = detect_format(source)

    if actual is None or declared == f".{actual}":
        return declared
    # ZIP 서명은 HWPX 말고도 많다. `.pdf` 로 선언된 zip 을 `.hwpx` 로
    # 바꿔치기하면 더 이상해지므로, HWPX 로의 정정은 하지 않는다.
    if actual == "hwpx" and declared != ".hwpx":
        return declared

    _log.warning(
        "%s: 확장자는 %s 인데 내용은 %s 입니다 — 내용에 맞춰 처리합니다. "
        "한글에서 '다른 이름으로 저장' 시 파일 형식을 확인하세요.",
        source.name, declared or "(없음)", actual.upper(),
    )
    return f".{actual}"


def mismatched(path: str | Path) -> bool:
    """확장자와 내용이 어긋나는지.

    입력: path — 파일 경로
    출력: 어긋나면 True
    """
    return effective_suffix(path) != Path(path).suffix.lower()
