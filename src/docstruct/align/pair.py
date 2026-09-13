"""쪽 맞춤 준비 — 두 판독 결과를 마련하고 맞춘다.

입력:
    HWPX(또는 HWP) 한 건 · PDF 한 건 — **원본이든 이미 돌린 결과든**
역할:
    `align.documents.align_documents` 는 `document.json` 두 벌을 받는다.
    그 두 벌을 **마련하는 일**이 여기다. 이미 돌려 둔 결과가 있으면 다시
    돌리지 않고, 없으면 그 자리에서 판독한다.
호출부:
    docstruct.align_pair (패키지 진입점) · docstruct.cli (`--align`)
출력:
    AlignPair — 맞춘 결과와 **무엇을 다시 쓰고 무엇을 새로 돌렸는지**

왜 따로 있나 (0.4.93)
--------------------
OCR·판독은 형식마다 따로 돈다 — HWPX 는 XML 을 걷고 PDF 는 Docling 이
읽는다. 쪽 맞춤은 **그 둘이 끝난 뒤**의 일이다. 그런데 예전에는 맞출
때마다 두 건을 **처음부터 다시 판독**했다. PDF 한 건이 몇 분씩 걸리는데,
바로 앞에 돌려 둔 결과가 옆 폴더에 있어도 쓰지 않았다.

이제 순서가 이렇다:

    1. 산출 폴더에 그 문서의 `document.json` 이 있나
    2. 있고 원본보다 오래되지 않았으면 → **그것을 쓴다**
    3. 없거나 낡았으면 → 그 건만 판독하고 저장한다
    4. 둘이 갖춰지면 맞춘다

산출 폴더 이름은 **확장자를 포함한 파일 이름**이다(0.4.93). `성과계획서.hwpx`
와 `성과계획서.pdf` 가 각자의 폴더를 갖는다 — 쪽 맞춤은 이 짝을 늘 다루므로
확장자가 폴더 이름에 있어야 어느 것이 어느 것인지 보인다.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

#: 원본으로 받아들이는 확장자 (판독이 필요한 것).
_SOURCE_SUFFIXES = (".hwpx", ".hwp", ".pdf")


@dataclass
class Prepared:
    """한 쪽의 판독 결과와 그것을 어떻게 얻었는지.

    입력(필드):
        document  document.json 을 읽은 dict
        source    원본 경로 (결과 json 을 직접 받았으면 그 경로)
        json_path 결과 json 경로 (메모리에서만 만들었으면 None)
        reused    이미 있던 것을 다시 썼는가
        reason    그렇게 한 이유 (사람이 읽을 한 줄)
    """

    document: dict
    source: Path
    json_path: Path | None = None
    reused: bool = False
    reason: str = ""


@dataclass
class AlignPair:
    """쪽 맞춤 한 번의 결과.

    입력(필드):
        result   맞춘 결과 (dict 또는 markdown 문자열)
        hwpx     쪽 없는 쪽의 준비 내역
        pdf      쪽 가진 쪽의 준비 내역
        notes    사람이 읽을 진행 요약
    """

    result: Any
    hwpx: Prepared
    pdf: Prepared
    notes: list[str] = field(default_factory=list)


def out_folder_name(name: str | Path) -> str:
    """산출 폴더 이름 — **확장자를 포함한** 파일 이름.

    입력: name — 파일 이름 또는 경로
    출력: 폴더로 쓸 수 있게 정리한 이름 (`성과 계획서.hwpx` → `성과_계획서.hwpx`)
    비고:
        **규칙은 `output.names.safe_file_name` 하나뿐이다.** 이 함수는 쪽
        맞춤 쪽에서 부르기 좋은 이름일 뿐 다른 규칙이 아니다 — 두 벌로
        두면 반드시 어긋난다.

        0.4.92 까지는 확장자를 뗐다. 그래서 `성과계획서.hwpx` 와
        `성과계획서.pdf` 가 **같은 폴더**를 써서 하나가 조용히 덮였다 —
        쪽 맞춤이 늘 다루는 바로 그 짝이다.
    """
    from docstruct.output.names import safe_file_name

    return safe_file_name(Path(name).name)


#: `build_document` 에 넘기지 **않는** 설정 — 화면 표시용 (0.5.5).
#: `api._VIEW_KEYS` 와 같은 뜻이다. 여기서 걸러 내지 않으면
#: `build_document() got an unexpected keyword argument 'steps'` 로 터진다.
_VIEW_ONLY = ("steps", "write_outputs")


def _split_options(options: dict) -> tuple[dict, dict]:
    """실행 인자와 화면 설정을 가른다.

    입력: options — 호출부가 준 키워드 전부
    출력: (build_document 에 넘길 것, 화면 설정)
    """
    view = {k: options[k] for k in _VIEW_ONLY if k in options}
    run = {k: v for k, v in options.items() if k not in _VIEW_ONLY}
    return run, view


def prepare(src: str | Path, out_dir: str | Path | None = None, *,
            reuse: bool = True, **options: Any) -> Prepared:
    """한 건의 `document.json` 을 마련한다 — 있으면 쓰고 없으면 판독한다.

    입력:
        src      원본 문서(.hwpx·.hwp·.pdf) 또는 이미 돌린 document.json
        out_dir  산출 뿌리. 여기 아래 `<파일이름.확장자>/document.json` 을 본다
        reuse    False 면 있어도 다시 판독한다
        options  build_document 에 넘길 값 (assess_tables·fill_tables …)
    출력: Prepared
    예외: 파일이 없거나 지원하지 않는 형식이면 ValueError
    비고:
        **낡은 결과는 다시 돌린다.** 결과 json 이 원본보다 오래됐으면
        원본이 바뀐 것이므로 그대로 쓰면 안 된다. 이 판단을 사람에게
        미루면 조용히 옛 결과로 맞추게 된다.

        `out_dir` 이 없으면 결과를 저장하지 않고 메모리에서만 만든다 —
        그때는 다시 쓸 것도 없으므로 언제나 판독한다.
    """
    path = Path(src).expanduser()
    if not path.is_file():
        raise ValueError(f"파일이 없습니다: {path}")

    # ① 이미 결과 json 을 직접 준 경우 — 그대로 읽는다.
    if path.suffix.lower() == ".json":
        try:
            return Prepared(json.loads(path.read_text(encoding="utf-8")),
                            source=path, json_path=path, reused=True,
                            reason="결과 json 을 직접 받았습니다")
        except (OSError, ValueError) as exc:
            raise ValueError(f"{path.name} 을 읽지 못했습니다: {exc}") from exc

    if path.suffix.lower() not in _SOURCE_SUFFIXES:
        raise ValueError(
            f"{path.name}: 지원하지 않는 형식입니다 — "
            f"{', '.join(_SOURCE_SUFFIXES)} 또는 document.json 을 주세요")

    root = Path(out_dir).expanduser().resolve() if out_dir is not None else None
    target = root / out_folder_name(path) if root is not None else None
    ready = target / "document.json" if target is not None else None

    # ② 돌려 둔 결과가 있고 원본보다 새것이면 그것을 쓴다.
    if reuse and ready is not None and ready.is_file():
        if ready.stat().st_mtime >= path.stat().st_mtime:
            try:
                return Prepared(json.loads(ready.read_text(encoding="utf-8")),
                                source=path, json_path=ready, reused=True,
                                reason=f"이미 돌린 결과를 씁니다 ({ready})")
            except (OSError, ValueError) as exc:
                _log.warning("%s 를 읽지 못해 다시 판독합니다: %s", ready, exc)
        else:
            _log.info("%s 가 원본보다 오래되어 다시 판독합니다", ready)

    # ③ 없거나 낡았으면 그 건만 판독한다.
    document = _build_and_save(path, target, options)
    return Prepared(document, source=path, json_path=ready, reused=False,
                    reason="새로 판독했습니다")


def _build_and_save(path: Path, target: Path | None,
                    options: dict[str, Any]) -> dict:
    """판독하고, 산출 폴더가 있으면 저장한다.

    입력: path — 원본, target — 산출 폴더(없으면 저장 안 함), options — 실행·화면 설정
    출력: document.json 에 해당하는 dict
    비고:
        **화면 설정을 걸러 낸다** (0.5.5). `steps` 같은 값이 그대로
        `build_document` 로 가면 `unexpected keyword argument` 로 터진다 —
        `align_pair(..., steps="silent")` 가 실제로 그랬다. 다른 진입점
        (`DocStruct.run`)은 `_VIEW_KEYS` 로 가르고 있었는데 쪽 맞춤만
        빠져 있었다.

        진행 단계도 같은 자리에서 낸다 — 쪽 맞춤이 두 건을 판독할 때도
        "멈춘 건가" 를 겪지 않아야 한다.
    """
    from docstruct.api import _reporting_for
    from docstruct.output.report import write_json, write_markdown
    from docstruct.pipeline import build_document

    run_options, view = _split_options(dict(options))
    if target is not None:
        target.mkdir(parents=True, exist_ok=True)
    with _reporting_for(path, target, view.get("steps")):
        doc = build_document(path, out_dir=target, **run_options)
    if target is None:
        return doc.to_dict()
    write_markdown(doc, target / "document.md")
    write_json(doc, target / "document.json")
    return doc.to_dict()


def align_pair(hwpx: str | Path, pdf: str | Path,
               out_dir: str | Path | None = None, *,
               reuse: bool = True, as_markdown: bool = False,
               **options: Any) -> AlignPair:
    """두 건을 마련해 쪽 맞춤까지 한 번에.

    입력:
        hwpx        쪽이 **없는** 쪽 — .hwpx·.hwp 원본 또는 그 document.json
        pdf         쪽을 **가진** 쪽 — .pdf 원본 또는 그 document.json
        out_dir     산출 뿌리. 결과를 여기서 찾고 여기에 저장한다
        reuse       False 면 있어도 다시 판독한다
        as_markdown True 면 맞춘 결과를 markdown 문자열로
        options     build_document 에 넘길 값
    출력: AlignPair (결과 + 무엇을 다시 쓰고 무엇을 새로 돌렸는지)
    예외: 자리를 바꿔 주거나 맞출 근거가 없으면 ValueError
    비고:
        **자리를 먼저 본다.** PDF 를 첫 자리에 주면 PDF 본문을 PDF 쪽에
        맞추는 꼴이라 뜻이 없다. 몇 분을 쓰고 나서 알면 늦으므로 판독
        전에 알린다.

        예)

            from docstruct import align_pair

            got = align_pair("성과계획서.hwpx", "성과계획서.pdf", out_dir="out")
            print("\\n".join(got.notes))
            got.result["pages"][0]["page_no"]
    """
    from docstruct.align.documents import align_documents

    left, right = Path(hwpx).expanduser(), Path(pdf).expanduser()
    if left.suffix.lower() == ".pdf":
        raise ValueError(
            "첫 자리는 쪽이 **없는** 쪽(HWPX·HWP 또는 그 document.json)입니다. "
            "둘째 자리에 PDF 를 주세요.")

    prepared_hwpx = prepare(left, out_dir, reuse=reuse, **options)
    prepared_pdf = prepare(right, out_dir, reuse=reuse, **options)

    notes = [
        f"{left.name}: {prepared_hwpx.reason}",
        f"{right.name}: {prepared_pdf.reason}",
    ]
    for got in (prepared_hwpx, prepared_pdf):
        _log.info("%s — %s", got.source.name, got.reason)

    result = align_documents(prepared_hwpx.document, prepared_pdf.document,
                             as_markdown=as_markdown)
    return AlignPair(result=result, hwpx=prepared_hwpx, pdf=prepared_pdf,
                     notes=notes)


def find_counterpart(src: str | Path) -> Path | None:
    """같은 이름의 짝 파일을 옆에서 찾는다.

    입력: src — 한쪽 원본 경로
    출력: 짝 경로 또는 없으면 None
    비고:
        `성과계획서.hwpx` 옆의 `성과계획서.pdf` 를 찾는다. 흔한 배치지만
        **보장은 아니므로** 못 찾으면 None 을 낸다 — 호출부가 사람에게
        물어야 한다. 이름이 다른 짝을 지어 주는 일은 하지 않는다.
    """
    path = Path(src).expanduser()
    wanted = (".pdf",) if path.suffix.lower() in (".hwpx", ".hwp") else (".hwpx", ".hwp")
    for suffix in wanted:
        mate = path.with_suffix(suffix)
        if mate.is_file():
            return mate
    return None
