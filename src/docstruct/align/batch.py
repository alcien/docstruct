"""HWPX 폴더와 PDF 폴더를 받아 **이름이 같은 쌍**을 한꺼번에 맞춘다 (0.5.70).

입력: HWPX 폴더 · PDF 폴더 · 산출 뿌리
출력: AlignBatch — 맞춘 쌍 · 실패 · 짝 없는 파일
비고:
    쪽 맞춤은 **두 결과가 모두 있어야** 되는 일이다. 그래서 입력에서부터
    폴더 둘을 강제한다 — 한 폴더에서 짝을 추측하지 않는다.

    짝은 **파일 이름(확장자 뺀 것)이 같은 것**끼리다. 이름이 다른 짝을
    지어 주지 않는다 — 비슷한 이름을 이어 붙이면 틀린 쌍을 맞추게 된다.
    짝 없는 파일은 조용히 건너뛰지 않고 결과에 남긴다.

    한 쌍이 실패해도 나머지는 계속 간다. 쌍마다 곧바로 저장하므로 중간에
    멈춰도 끝난 쌍은 남는다.
"""
from __future__ import annotations

import json
import logging
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

_log = logging.getLogger(__name__)

#: 쪽이 **없는** 쪽. 이름이 같으면 앞의 것을 쓴다 — HWPX 가 HWP 보다 낫다.
HWPX_SUFFIXES = (".hwpx", ".hwp")
#: 쪽을 **가진** 쪽.
PDF_SUFFIXES = (".pdf",)
#: 일괄 결과를 모아 적는 파일 — 산출 뿌리에 둔다.
REPORT_NAME = "align_batch.json"


def _key(path: Path) -> str:
    """짝을 가를 이름 — 확장자를 떼고 **유니코드 정규화**한다.

    입력: path — 파일 경로
    출력: 비교용 이름
    비고:
        macOS 는 한글 파일 이름을 자모로 풀어(NFD) 저장한다. 같은 이름이
        바이트로는 달라 짝을 못 짓는 일이 생기므로 NFC 로 맞춘다.
    """
    return unicodedata.normalize("NFC", path.stem).strip()


def _collect(folder: Path, suffixes: tuple[str, ...]) -> tuple[dict[str, Path], list[str]]:
    """폴더에서 그 형식의 파일을 이름별로 모은다.

    입력: folder — 폴더, suffixes — 받을 확장자 (앞이 우선)
    출력: ({이름: 경로}, 알림)
    """
    found: dict[str, Path] = {}
    notes: list[str] = []
    files = sorted((p for p in folder.iterdir()
                    if p.is_file() and p.suffix.lower() in suffixes),
                   key=lambda p: (suffixes.index(p.suffix.lower()), p.name))
    for path in files:
        key = _key(path)
        if key in found:
            notes.append(f"{path.name}: 같은 이름의 {found[key].name} 을 씁니다")
            continue
        found[key] = path
    return found, notes


def _count(folder: Path, suffixes: tuple[str, ...]) -> int:
    return sum(1 for p in folder.iterdir() if p.is_file() and p.suffix.lower() in suffixes)


@dataclass
class AlignBatch:
    """폴더 쌍 쪽 맞춤의 결과.

    입력(필드):
        hwpx_dir       쪽 없는 쪽 폴더
        pdf_dir        쪽 가진 쪽 폴더
        out_dir        산출 뿌리
        done           맞춘 쌍 — 이름 · 두 원본 · 쓴 파일 · 쪽 수 요약
        failures       실패한 쌍 — 이름 · 오류
        unpaired_hwpx  PDF 짝이 없는 HWPX·HWP
        unpaired_pdf   HWPX 짝이 없는 PDF
        notes          사람이 읽을 알림 (같은 이름이 둘인 파일 등)
    """

    hwpx_dir: Path
    pdf_dir: Path
    out_dir: Path
    done: list[dict] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)
    unpaired_hwpx: list[str] = field(default_factory=list)
    unpaired_pdf: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """실패한 쌍이 없는가. 짝 없는 파일은 실패로 치지 않는다."""
        return not self.failures

    def summary(self) -> list[str]:
        """사람이 읽을 요약 줄.

        입력: 없음
        출력: 줄 목록
        """
        total = len(self.done) + len(self.failures)
        lines = [f"쌍 {total}개 중 맞춤 {len(self.done)} · 실패 {len(self.failures)}"]
        if self.unpaired_hwpx:
            lines.append(f"PDF 짝이 없는 HWPX {len(self.unpaired_hwpx)}개: "
                         + ", ".join(self.unpaired_hwpx))
        if self.unpaired_pdf:
            lines.append(f"HWPX 짝이 없는 PDF {len(self.unpaired_pdf)}개: "
                         + ", ".join(self.unpaired_pdf))
        for fail in self.failures:
            lines.append(f"실패 {fail['name']}: {fail['error']}")
        lines += self.notes
        return lines

    def to_dict(self) -> dict[str, Any]:
        """JSON 으로 쓸 수 있는 dict.

        입력: 없음
        출력: dict
        """
        return {
            "hwpx_dir": str(self.hwpx_dir), "pdf_dir": str(self.pdf_dir),
            "out_dir": str(self.out_dir),
            "pairs": len(self.done) + len(self.failures),
            "aligned": len(self.done), "failed": len(self.failures),
            "done": self.done, "failures": self.failures,
            "unpaired_hwpx": self.unpaired_hwpx, "unpaired_pdf": self.unpaired_pdf,
            "notes": self.notes,
        }

    def save_report(self, path: str | Path | None = None) -> Path:
        """일괄 결과를 JSON 으로 남긴다.

        입력: path — 쓸 곳. 비우면 `out_dir/align_batch.json`
        출력: 쓴 경로
        """
        target = Path(path) if path else self.out_dir / REPORT_NAME
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                          encoding="utf-8")
        return target


def _check_folders(hwpx_dir: Path, pdf_dir: Path) -> None:
    """판독에 들어가기 **전에** 자리를 확인한다.

    입력: hwpx_dir · pdf_dir
    출력: 없음
    예외: 폴더가 아니거나, 자리가 바뀌었거나, 그 형식이 없으면 ValueError
    비고:
        한 쌍에 몇 분이 걸린다. 다 돌고 나서 자리가 바뀐 걸 알면 늦다.
    """
    for label, folder in (("HWPX", hwpx_dir), ("PDF", pdf_dir)):
        if not folder.is_dir():
            raise ValueError(f"{label} 자리에 폴더를 주세요 — {folder} 는 폴더가 아닙니다")
    hwpx_here = _count(hwpx_dir, HWPX_SUFFIXES)
    pdf_here = _count(pdf_dir, PDF_SUFFIXES)
    if not hwpx_here and not pdf_here and _count(hwpx_dir, PDF_SUFFIXES) and _count(pdf_dir, HWPX_SUFFIXES):
        raise ValueError("자리가 바뀌었습니다 — 첫 자리는 HWPX·HWP 폴더, "
                         "둘째 자리는 PDF 폴더입니다")
    if not hwpx_here:
        raise ValueError(f"{hwpx_dir} 에 HWPX·HWP 가 없습니다")
    if not pdf_here:
        raise ValueError(f"{pdf_dir} 에 PDF 가 없습니다")


def align_folders(hwpx_dir: str | Path, pdf_dir: str | Path,
                  out_dir: str | Path = "out", *,
                  reuse: bool = True, formats: str = "both", stem: str = "aligned",
                  on_pair: Callable[[int, int, str], None] | None = None,
                  **options: Any) -> AlignBatch:
    """HWPX 폴더와 PDF 폴더에서 **이름이 같은 쌍**을 모두 맞춘다.

    입력:
        hwpx_dir  쪽이 **없는** 쪽 폴더 — .hwpx·.hwp 원본
        pdf_dir   쪽을 **가진** 쪽 폴더 — .pdf 원본
        out_dir   산출 뿌리. 쌍마다 `<문서.hwpx>/aligned.json` 을 쓰고, 뿌리에
                  `align_batch.json` 을 남긴다
        reuse     False 면 판독 결과가 있어도 다시 판독한다
        formats   "json" · "markdown" · "both"
        stem      쌍마다 쓸 파일 이름의 앞부분 (기본 `aligned`)
        on_pair   쌍을 시작할 때 부를 함수 `(순번, 전체, 이름)` — 진행 표시용
        options   build_document 에 넘길 값
    출력: AlignBatch
    예외: 폴더가 아니거나, 자리가 바뀌었거나, 짝이 하나도 없으면 ValueError
    비고:
        짝은 **파일 이름(확장자 뺀 것)이 같은 것**끼리다 — 한글 이름은
        유니코드 정규화(NFC)해서 견준다. 이름이 다른 짝은 짓지 않는다.

        한 쌍이 실패해도 나머지는 계속 간다. 쌍마다 곧바로 저장한다.

        예)

            from docstruct import align_folders

            batch = align_folders("hwpx/", "pdf/", out_dir="out")
            print("\\n".join(batch.summary()))
    """
    from docstruct.align.pair import align_pair

    left = Path(hwpx_dir).expanduser()
    right = Path(pdf_dir).expanduser()
    root = Path(out_dir).expanduser()
    _check_folders(left, right)

    hwpx_files, hwpx_notes = _collect(left, HWPX_SUFFIXES)
    pdf_files, pdf_notes = _collect(right, PDF_SUFFIXES)
    names = sorted(set(hwpx_files) & set(pdf_files))
    if not names:
        sample = lambda found: ", ".join(sorted(p.name for p in found.values())[:3])  # noqa: E731
        raise ValueError("이름이 같은 짝이 하나도 없습니다 — "
                         f"HWPX 쪽 예: {sample(hwpx_files)} · PDF 쪽 예: {sample(pdf_files)}")

    batch = AlignBatch(
        hwpx_dir=left, pdf_dir=right, out_dir=root,
        unpaired_hwpx=sorted(hwpx_files[k].name for k in set(hwpx_files) - set(pdf_files)),
        unpaired_pdf=sorted(pdf_files[k].name for k in set(pdf_files) - set(hwpx_files)),
        notes=hwpx_notes + pdf_notes)
    root.mkdir(parents=True, exist_ok=True)

    for index, name in enumerate(names, start=1):
        if on_pair:
            on_pair(index, len(names), name)
        hwpx, pdf = hwpx_files[name], pdf_files[name]
        try:
            got = align_pair(hwpx, pdf, root, reuse=reuse, **options)
            written = got.save(root, formats=formats, stem=stem)
        except Exception as exc:   # 한 쌍의 실패가 나머지를 막지 않는다
            _log.warning("%s 쪽 맞춤 실패: %s", name, exc)
            batch.failures.append({"name": name, "hwpx": hwpx.name, "pdf": pdf.name,
                                   "error": f"{type(exc).__name__}: {exc}"})
            continue
        result = got.result if isinstance(got.result, dict) else {}
        batch.done.append({
            "name": name, "hwpx": hwpx.name, "pdf": pdf.name,
            "written": [str(p) for p in written],
            "page_count": result.get("page_count"),
            "aligned_pages": result.get("aligned_pages"),
            "unaligned_pages": result.get("unaligned_pages"),
            "notes": got.notes,
        })
    batch.save_report()
    return batch
