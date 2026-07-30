"""로컬 CLI 진입점.

역할:
    파일이나 디렉터리를 받아 구조화하고 산출물을 저장한다.
    --check 로 환경·LLM 연결만 확인할 수도 있다.
호출부:
    `python -m docstruct.cli <파일|디렉터리>`
출력:
    표준출력에 요약, out_dir/<문서명>/ 에 document.md·document.json·
    tables.md·pipeline.md 및 pages/·images/
    종료 코드 0(성공) / 1(실패)
"""
from __future__ import annotations

import argparse
import logging
import sys
import traceback
from pathlib import Path

from docstruct.media.page_render import safe_file_stem
from docstruct.pipeline import SUPPORTED_SUFFIXES, build_document
from docstruct.report import (
    summary_lines,
    write_json,
    write_markdown,
    write_layout_report,
    write_pipeline_report,
    write_tables_report,
)

_log = logging.getLogger("docstruct")


def _build_parser() -> argparse.ArgumentParser:
    """명령행 파서를 만든다.

    입력: 없음
    출력: ArgumentParser
    """
    p = argparse.ArgumentParser(
        prog="docstruct",
        description="HWP/HWPX/PDF를 구조화하고 결과를 로컬 파일로 덤프합니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("input", nargs="?", default=None,
                   help="문서 파일 또는 디렉터리 (--check 만 할 때는 생략 가능)")
    p.add_argument("-o", "--out", default="out", help="출력 디렉터리 (기본: out)")
    p.add_argument(
        "--glob",
        default="*",
        help="input이 디렉터리일 때 대상 패턴 (기본: *)",
    )

    llm = p.add_argument_group("LLM 단계")
    llm.add_argument(
        "--no-llm",
        action="store_true",
        help="표 평가·재추출·목차를 모두 끕니다 (네트워크 없이 동작)",
    )
    llm.add_argument("--no-assess", action="store_true", help="표 품질 평가 생략")
    llm.add_argument("--no-fill", action="store_true", help="표 재추출 생략 (평가만)")
    llm.add_argument(
        "--fill-all",
        action="store_true",
        help="품질과 무관하게 모든 표를 재추출 (기본은 wrong/insufficient만)",
    )
    llm.add_argument(
        "--outline",
        action="store_true",
        help="의미 경로(목차) 추출 — 페이지당 LLM 1회 추가",
    )

    render = p.add_argument_group("렌더링")
    render.add_argument(
        "--no-render",
        action="store_true",
        help="PDF 페이지 PNG 렌더 생략 (표 평가 정확도 하락)",
    )
    render.add_argument(
        "--scale", type=float, default=2.0, help="페이지 렌더 배율 (기본: 2.0)"
    )

    p.add_argument("-v", "--verbose", action="store_true", help="DEBUG 로그")
    p.add_argument("-q", "--quiet", action="store_true", help="요약만 출력")
    p.add_argument(
        "--progress", action="store_true",
        help="진행 막대 표시 (tqdm 미설치 시 로그로 대체)",
    )
    p.add_argument(
        "--ask-key",
        action="store_true",
        help="OpenAI 키를 입력받습니다 (화면·히스토리에 남지 않음)",
    )
    p.add_argument(
        "--key-file",
        metavar="경로",
        help="키가 담긴 파일에서 읽습니다 (첫 줄만 사용)",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="환경·LLM 연결만 확인하고 종료 (파일 처리 안 함)",
    )
    return p


def _targets(input_path: Path, pattern: str) -> list[Path]:
    """처리 대상 파일 목록을 만든다.

    입력: path — 파일 또는 디렉터리, pattern — 디렉터리일 때 glob
    출력: 지원 확장자에 해당하는 파일 경로 목록
    예외: 경로가 없으면 FileNotFoundError
    """
    if input_path.is_file():
        return [input_path]
    if not input_path.is_dir():
        raise FileNotFoundError(f"경로를 찾을 수 없습니다: {input_path}")
    found = sorted(
        f
        for f in input_path.glob(pattern)
        if f.is_file() and f.suffix.lower() in SUPPORTED_SUFFIXES
    )
    if not found:
        raise FileNotFoundError(
            f"{input_path} 안에 처리할 문서가 없습니다 "
            f"(패턴={pattern!r}, 지원={', '.join(SUPPORTED_SUFFIXES)})"
        )
    return found


def _process(src: Path, out_root: Path, args) -> None:
    """파일 하나를 처리하고 산출물을 저장한다.

    입력: path, out_dir, 실행 옵션
    출력: 저장된 파일 경로 목록
    """
    out_dir = out_root / safe_file_stem(src.name)
    out_dir.mkdir(parents=True, exist_ok=True)

    use_llm = not args.no_llm
    doc = build_document(
        src,
        assess_tables=use_llm and not args.no_assess,
        fill_tables=use_llm and not args.no_fill,
        fill_all=args.fill_all,
        render_pages=not args.no_render,
        out_dir=out_dir,
        render_scale=args.scale,
        progress=getattr(args, "progress", False),
    )

    written = [
        write_markdown(doc, out_dir / "document.md"),
        write_json(doc, out_dir / "document.json"),
        write_tables_report(doc, out_dir / "tables.md"),
        write_pipeline_report(doc, out_dir / "pipeline.md"),
        write_layout_report(doc, out_dir / "layout.md"),
    ]

    if args.outline and use_llm:
        from docstruct.outline.builder import build_outline, outline_to_markdown

        nodes = build_outline(doc)
        path = out_dir / "outline.md"
        path.write_text(outline_to_markdown(nodes), encoding="utf-8")
        written.append(path)
    elif args.outline:
        _log.warning("--no-llm 이므로 --outline 을 건너뜁니다.")

    print(f"\n=== {src.name} ===")
    for line in summary_lines(doc):
        print(f"  {line}")
    if not args.quiet:
        print("  출력:")
        for path in written:
            print(f"    {path}")


def _apply_key(args) -> None:
    """명령행 옵션으로 지정한 API 키를 적용한다.

    입력: args — 파싱된 명령행 인자
    출력: 없음 (환경변수 설정)
    예외: 파일을 읽지 못하거나 내용이 비면 OSError/ValueError

    비고:
        키를 인자로 직접 받지 않는다. ``--api-key sk-...`` 형태는 셸
        히스토리와 프로세스 목록(`ps`)에 그대로 남기 때문이다.
        입력받거나(``--ask-key``) 파일에서 읽는다(``--key-file``).
    """
    key = ""
    if getattr(args, "key_file", None):
        path = Path(args.key_file).expanduser()
        lines = path.read_text(encoding="utf-8").splitlines()
        key = next((l.strip() for l in lines if l.strip()), "")
        if not key:
            raise ValueError(f"{path} 에 키가 없습니다 (빈 파일).")
    elif getattr(args, "ask_key", False):
        import getpass

        key = getpass.getpass("OpenAI 키: ").strip()
        if not key:
            raise ValueError("입력이 비었습니다.")

    if key:
        import os

        os.environ["OPENAI_API_KEY"] = key
        from docstruct.core.config import rebuild_settings
        from docstruct.checks import invalidate_caches

        rebuild_settings()
        invalidate_caches()


def _print_check() -> int:
    """환경과 LLM 연결을 확인해 출력한다.

    입력: 없음
    출력: 종료 코드 (연결 성공 0, 실패 1)
    """
    from docstruct.core.config import get_settings

    from docstruct.checks import environment

    print("=== 환경 ===")
    for item in environment():
        print(f"  {'OK  ' if item['ok'] else 'WARN'} {item['item']:20} {item['note']}")

    print("\n=== 설정 ===")
    for label, value, ok in get_settings().describe():
        print(f"  {'OK  ' if ok else 'WARN'} {label:20} {value}")

    print("\n=== LLM 연결 ===")
    from docstruct.checks import check_llm_reachable

    ok, message = check_llm_reachable()
    print(("  OK   " if ok else "  WARN ") + message)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    """CLI 실행.

    입력: argv — 명령행 인자 (None 이면 sys.argv)
    출력: 종료 코드 (0 성공, 1 실패)
    """
    args = _build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else (logging.WARNING if args.quiet else logging.INFO),
        format="%(levelname)-7s %(name)s: %(message)s",
    )

    try:
        _apply_key(args)
    except (OSError, ValueError) as exc:
        print(f"키 설정 실패: {exc}", file=sys.stderr)
        return 1

    if args.check:
        return _print_check()

    if not args.input:
        _build_parser().error("처리할 문서 파일 또는 디렉터리를 지정하세요.")

    try:
        targets = _targets(Path(args.input).expanduser(), args.glob)
    except FileNotFoundError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1

    out_root = Path(args.out).expanduser().resolve()
    failures = 0

    from docstruct.progress import ProgressBar

    bar = ProgressBar(
        len(targets), "문서 처리", unit="건",
        enabled=args.progress and len(targets) > 1,
    )
    for src in targets:
        try:
            _process(src, out_root, args)
        except Exception as exc:
            failures += 1
            print(f"\n=== {src.name} === 실패: {exc}", file=sys.stderr)
            if args.verbose:
                traceback.print_exc()
        bar.update(1, src.name)

    bar.close()

    if len(targets) > 1:
        print(f"\n총 {len(targets)}건 중 {len(targets) - failures}건 성공, {failures}건 실패")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
