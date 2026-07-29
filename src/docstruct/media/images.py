"""이미지 파일 → base64 인코딩.

역할:
    LLM 에 이미지를 첨부할 때 쓸 data URI 재료를 만든다.
호출부:
    docstruct.tables.assess / docstruct.tables.fill
출력:
    (MIME 타입, base64 문자열) 또는 실패 시 None
"""
from __future__ import annotations

import base64
from pathlib import Path


def encode_image_file(path: str) -> tuple[str, str] | None:
    """이미지 파일을 base64 로 인코딩한다.

    입력: path — 이미지 파일 경로
    출력: (mime, base64) 튜플. 파일이 없거나 읽기 실패 시 None
    """
    file_path = Path(path)
    if not file_path.is_file():
        return None
    data = file_path.read_bytes()
    b64 = base64.standard_b64encode(data).decode("ascii")
    suffix = file_path.suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg"
    return mime, b64
