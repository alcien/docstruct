"""HWP 미리보기 스트림(PrvText·PrvImage) 활용.

역할:
    pyhwp 가 본문을 못 읽어 olefile 텍스트 폴백으로 내려가면 표 구조가
    통째로 사라진다. 그런데 한글이 저장할 때 만들어 둔 미리보기 스트림에는
    셀 경계(`<셀><셀>`)와 첫 페이지 이미지가 이미 들어 있다. 공짜로 얻을 수
    있는 구조 정보라 폴백 품질을 크게 끌어올린다.
호출부:
    docstruct.extractors.hwp.extract_hwp_pages (olefile 폴백 경로에서만)
출력:
    표 행으로 복원된 markdown, 첫 페이지 미리보기 이미지

한계 — 반드시 커버리지를 확인할 것
--------------------------------
``PrvText`` 는 **1,023자에서 잘린다**. 짧은 문서는 거의 전체가 담기지만,
5만 자짜리 문서에서는 2% 만 담긴다. 그 상태로 표를 만들면 앞부분만 살고
나머지는 없는 것이 되어 오히려 손해다.

``PrvImage`` 도 **첫 페이지 한 장**이다. HWP 는 페이지 경계가 없어 문서
전체가 하나의 PageContent 가 되는데, 긴 문서에 이 이미지를 근거로 붙이면
LLM 이 뒷부분 표를 첫 페이지 그림으로 판단하게 된다.

그래서 둘 다 ``coverage()`` 가 충분할 때만 쓴다.
"""
from __future__ import annotations

import logging
from pathlib import Path

from docstruct.converters.korean_text import normalize_korean_text

_log = logging.getLogger(__name__)

#: PrvText 가 잘리는 길이. 한글이 이 상한으로 저장한다.
PRV_TEXT_LIMIT = 1023

#: 미리보기를 본문 대용으로 쓰기 위한 최소 커버리지.
#: 이보다 낮으면 앞부분만 담긴 것이라 쓰면 안 된다.
MIN_COVERAGE = 0.8

#: 이미지 시그니처 → 확장자.
_IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"BM", ".bmp"),
    (b"GIF8", ".gif"),
)


def read_prv_text(path: str | Path) -> str | None:
    """PrvText 스트림을 읽는다.

    입력: path — HWP 파일 경로
    출력: 미리보기 텍스트. 없거나 읽지 못하면 None
    """
    try:
        import olefile
    except ImportError:
        return None
    try:
        with olefile.OleFileIO(str(path)) as ole:
            if not ole.exists("PrvText"):
                return None
            raw = ole.openstream("PrvText").read()
    except Exception as exc:                     # noqa: BLE001 - 미리보기는 선택 사항
        _log.debug("PrvText 읽기 실패: %s", exc)
        return None
    text = raw.decode("utf-16le", "replace").replace("\x00", "").strip()
    return normalize_korean_text(text)


def read_prv_image(path: str | Path) -> tuple[bytes, str] | None:
    """PrvImage 스트림을 읽는다.

    입력: path — HWP 파일 경로
    출력: (이미지 바이트, 확장자). 없거나 형식을 모르면 None
    비고: 파일마다 PNG 이기도 JPEG 이기도 해서 시그니처로 판별한다.
    """
    try:
        import olefile
    except ImportError:
        return None
    try:
        with olefile.OleFileIO(str(path)) as ole:
            if not ole.exists("PrvImage"):
                return None
            data = ole.openstream("PrvImage").read()
    except Exception as exc:                     # noqa: BLE001
        _log.debug("PrvImage 읽기 실패: %s", exc)
        return None
    if not data:
        return None
    for signature, suffix in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            return data, suffix
    _log.debug("PrvImage 형식을 알 수 없습니다: %r", data[:8])
    return None


def coverage(prv_text: str | None, body_text: str) -> float:
    """미리보기가 본문의 몇 할을 담고 있는지.

    입력:
        prv_text   PrvText 내용
        body_text  olefile 로 뽑은 본문
    출력: 0.0~1.0 비율
    비고:
        셀 구분자 `<`, `>` 를 뺀 순수 글자 수로 비교한다. 본문이 비어 있으면
        비교가 무의미하므로 0 을 돌려준다.
    """
    if not prv_text or not body_text:
        return 0.0
    plain = _letters(prv_text.replace("<", " ").replace(">", " "))
    body = _letters(body_text)
    if body == 0:
        return 0.0
    return min(1.0, plain / body)


def _letters(text: str) -> int:
    """공백을 뺀 글자 수.

    입력: text
    출력: 글자 수
    """
    return len("".join(text.split()))


def to_markdown(prv_text: str) -> str:
    """미리보기 텍스트를 표가 살아 있는 markdown 으로 바꾼다.

    입력: prv_text — PrvText 내용
    출력: markdown 문자열
    비고:
        `<셀><셀>` 형태의 줄은 표 행으로, 나머지는 일반 문단으로 본다.
        연속한 표 행은 하나의 표로 묶고, 열 수가 다르면 가장 넓은 행에
        맞춰 빈 칸을 채운다.

        원본 표가 한 줄로 뭉개져 들어오는 경우가 있어(복잡한 표에서 흔함)
        행 구조가 완전하지는 않다. 그래도 셀 경계가 살아나므로 평문보다
        훨씬 낫고, 이후 표 재추출 LLM 이 다듬을 근거가 된다.
    """
    out: list[str] = []
    rows: list[list[str]] = []

    def flush() -> None:
        """모아둔 표 행을 markdown 표로 내보낸다.

        입력: 없음 (둘러싼 rows·out 사용)
        출력: 없음 (out 에 GFM 표 줄 추가 후 rows 비움)
        비고: 행마다 열 수가 다를 수 있어 최대 폭에 맞춰 빈 칸을 채운다.
        """
        if not rows:
            return
        width = max(len(r) for r in rows)
        padded = [r + [""] * (width - len(r)) for r in rows]
        out.append("| " + " | ".join(padded[0]) + " |")
        out.append("| " + " | ".join(["---"] * width) + " |")
        for row in padded[1:]:
            out.append("| " + " | ".join(row) + " |")
        out.append("")
        rows.clear()

    for line in prv_text.splitlines():
        stripped = line.strip()
        cells = _split_cells(stripped)
        if cells is None:
            flush()
            if stripped:
                out.append(stripped)
                out.append("")
        else:
            rows.append(cells)
    flush()
    return "\n".join(out).strip()


def _split_cells(line: str) -> list[str] | None:
    """`<a><b><c>` 형태의 줄을 셀 목록으로 나눈다.

    입력: line — 한 줄
    출력: 셀 목록. 표 행이 아니면 None
    """
    if not line.startswith("<") or not line.endswith(">"):
        return None
    inner = line[1:-1]
    if "<" in inner and "><" not in line:
        return None                              # 표 행이 아니라 꺾쇠 문장
    cells = [c.strip() for c in inner.split("><")]
    if len(cells) < 2:
        return None                              # `<한 칸>` 은 표로 보지 않는다
    return cells


def save_preview_image(path: str | Path, out_dir: str | Path, stem: str) -> str | None:
    """첫 페이지 미리보기 이미지를 저장한다.

    입력:
        path     HWP 파일 경로
        out_dir  저장 위치
        stem     파일 이름 앞부분
    출력: 저장된 경로 문자열. 없으면 None
    비고:
        HWP 는 페이지 렌더가 불가능해 표 재추출이 근거 이미지 없이 돌아간다.
        이 미리보기가 유일한 시각 근거다 — 다만 첫 페이지뿐이므로 호출부에서
        커버리지를 확인한 뒤 써야 한다.
    """
    result = read_prv_image(path)
    if result is None:
        return None
    data, suffix = result
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    dest = target / f"{stem}_preview{suffix}"
    try:
        dest.write_bytes(data)
    except OSError as exc:
        _log.debug("미리보기 이미지 저장 실패: %s", exc)
        return None
    return str(dest)
