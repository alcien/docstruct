"""HWP 파일이 읽을 수 있는 종류인지 미리 확인한다.

역할:
    본문이 비어 나오는데 오류가 없으면 원인을 찾기 어렵다. 배포용(DRM)·
    암호 문서, 구버전 HWP 3.0, 본문 스트림 자체가 없는 파일은 파서가
    조용히 빈 결과를 낸다. 읽기 전에 짚어 준다.
호출부:
    docstruct.converters.hwp.converter.HwpConverter
출력:
    HwpDiagnosis — 읽을 수 있는지와 그 이유

배경
----
`FileHeader` 스트림의 32~35 바이트가 버전, 36~39 바이트가 플래그다.

    비트 0  압축
    비트 1  암호
    비트 2  배포용(DRM)
    비트 4  DRM 보안

배포용 문서는 본문이 `BodyText` 가 아니라 `ViewText` 에 암호화되어 들어간다.
pyhwp 는 이걸 못 읽고 빈 결과를 돌려주는데, 예외가 아니라서 "성공했지만
내용이 없음" 이 된다. 배치 처리에서 특히 위험하다 — 실패 목록에도 안 뜬다.
"""
from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger(__name__)

#: FileHeader 플래그 비트.
_FLAG_COMPRESSED = 0x01
_FLAG_ENCRYPTED = 0x02
_FLAG_DISTRIBUTION = 0x04
_FLAG_DRM = 0x10

#: HWP 5.0 파일의 서명.
_HWP5_SIGNATURE = b"HWP Document File"


@dataclass
class HwpDiagnosis:
    """HWP 파일 상태.

    입력(필드):
        readable  본문을 읽을 수 있다고 보는지
        reason    읽을 수 없다면 그 이유 (사용자에게 보여 줄 문장)
        flags     FileHeader 플래그 원값 (로그용)
    """

    readable: bool
    reason: str = ""
    flags: int = 0


def diagnose(path: str | Path) -> HwpDiagnosis:
    """HWP 파일을 읽을 수 있는지 미리 본다.

    입력: path — HWP 파일 경로
    출력: HwpDiagnosis
    비고:
        확신할 수 없을 때는 readable=True 로 둔다. 판단이 애매하다고 처리를
        막으면, 지금까지 잘 되던 문서가 갑자기 실패한다.
    """
    try:
        import olefile
    except ImportError:
        return HwpDiagnosis(True, "olefile 미설치 — 진단 생략")

    try:
        if not olefile.isOleFile(str(path)):
            return HwpDiagnosis(
                False,
                "OLE 형식이 아닙니다 — HWP 3.0 이하이거나 확장자만 .hwp 인 "
                "다른 형식일 수 있습니다. 한글에서 '한글 문서(*.hwp)' 로 "
                "다시 저장하거나 .hwpx 로 내보내세요",
            )
        with olefile.OleFileIO(str(path)) as ole:
            entries = {name[0] for name in ole.listdir()}
            if not ole.exists("FileHeader"):
                return HwpDiagnosis(False, "FileHeader 스트림이 없습니다 — 손상된 파일")
            header = ole.openstream("FileHeader").read()
            if len(header) < 40:
                return HwpDiagnosis(False, "FileHeader 가 너무 짧습니다 — 손상된 파일")
            if not header.startswith(_HWP5_SIGNATURE):
                return HwpDiagnosis(False, "HWP 5.0 서명이 없습니다 — 다른 형식")

            flags = struct.unpack_from("<I", header, 36)[0]
            if flags & _FLAG_ENCRYPTED:
                return HwpDiagnosis(
                    False, "암호가 걸린 문서입니다 — 한글에서 암호를 풀고 저장하세요",
                    flags,
                )
            if flags & (_FLAG_DISTRIBUTION | _FLAG_DRM):
                return HwpDiagnosis(
                    False,
                    "배포용(DRM) 문서입니다 — 본문이 암호화되어 있어 읽을 수 "
                    "없습니다. 한글에서 열어 '다른 이름으로 저장'(일반 문서) "
                    "하거나 .hwpx 로 내보내세요",
                    flags,
                )
            if "BodyText" not in entries:
                extra = " (ViewText 만 있음 — 배포용 문서)" if "ViewText" in entries else ""
                return HwpDiagnosis(
                    False, f"본문(BodyText) 스트림이 없습니다{extra}", flags
                )
            return HwpDiagnosis(True, "", flags)
    except Exception as exc:                     # noqa: BLE001 - 진단이 처리를 막으면 안 된다
        _log.debug("HWP 진단 실패 — 그대로 진행합니다: %s", exc)
        return HwpDiagnosis(True, "")
