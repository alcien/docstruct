"""원본 변환 CLI.

역할:
    문서를 지정 형식으로 변환해 파일로 저장한다.
    (구조화·표 판정은 docstruct.cli 가 담당한다.)
호출부:
    `python -m converters.cli <파일>`
출력:
    변환된 파일과 종료 코드
"""
from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

from docstruct.core.config import load_env
from docstruct.converters.registry import get_converter


def main(argv: list[str] | None = None) -> None:
    """원본 변환 CLI.

    입력: argv — 명령행 인자
    출력: 종료 코드
    """
    p = argparse.ArgumentParser(
        description="문서 파일을 text / markdown / html / xml 로 변환합니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""
            예시:
              python -m converters.cli 문서.hwp
              python -m converters.cli 문서.hwp -f markdown
              python -m converters.cli 문서.hwp -f html -o 문서.html
              python -m converters.cli 문서.hwpx -f markdown
              python -m converters.cli 문서.pdf -f text
        """),
    )
    p.add_argument("input_file", help="변환할 문서 파일")
    p.add_argument(
        "--format",
        "-f",
        choices=["text", "markdown", "html", "xml"],
        default="text",
        help="출력 포맷 (기본값: text)",
    )
    p.add_argument(
        "--output",
        "-o",
        default=None,
        help="저장할 파일 경로 (생략 시 stdout)",
    )
    args = p.parse_args(argv)

    load_env()
    conv = get_converter(args.input_file)
    result = conv.convert(args.format)

    if args.output:
        Path(args.output).write_text(result, encoding="utf-8")
        print(f"저장 완료: {args.output}", file=sys.stderr)
    else:
        print(result)


if __name__ == "__main__":
    main()
