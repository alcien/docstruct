"""로컬 CLI 진입점.

입력:
    명령행 인자 (`docstruct 문서 -o out --exp … --set …`)

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
import os
import sys
import traceback
from contextlib import contextmanager
from pathlib import Path

from docstruct.core.steps import PROGRESS_FILENAME
from docstruct.output.names import (assign_out_dirs, describe_renames,
                                    safe_file_name)
from docstruct.pipeline import SUPPORTED_SUFFIXES, build_document
from docstruct.output.report import (
    summary_lines,
    write_json,
    write_markdown,
    write_layout_report,
    write_pipeline_report,
    write_tables_report,
)

_log = logging.getLogger("docstruct")

# ═══ 구간 1 — 도움말·인자 정의 ════════════════════════════════════════════════════
# 사용 예(_EPILOG)와 argparse 구성(_build_parser). 옵션 하나가 곧 문서다.
_EPILOG = """예시:
  docstruct 보고서.pdf                            out/보고서/ 에 결과
  docstruct 보고서.pdf -o 결과 --no-llm            LLM 없이 파싱만
  docstruct 문서모음/ --glob "*.hwp" --progress     일괄 처리
  docstruct 보고서.pdf --no-fill                  표 판정만 (재추출 안 함)
  docstruct --check                              환경·LLM 연결 확인
  docstruct 문서.hwpx --align 문서.pdf             HWPX 에 PDF 쪽번호 붙이기
  docstruct out/문서/document.json --align out/문서_pdf/document.json
                                                 이미 돌린 결과끼리 맞추기

산출물 (out/<문서명>/):
  document.json  전체 구조     document.md   본문
  tables.md      표 판정       pipeline.md   처리 경로·소요 시간
  layout.md      레이아웃 인식 (PDF)
  aligned.json / aligned.md   쪽 맞춤 결과 (--align 일 때 · 이름은 --align-name)
  pages/         페이지 PNG    images/       추출된 그림

종료 코드: 0 성공 · 1 실패 · 2 인자 오류
전체 옵션은 --help 를 보세요.
"""


def _build_parser() -> argparse.ArgumentParser:
    """명령행 파서를 만든다.

    입력: 없음
    출력: ArgumentParser
    """
    p = argparse.ArgumentParser(
        prog="docstruct",
        description="HWP/HWPX/PDF를 구조화하고 결과를 로컬 파일로 덤프합니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        # 모듈 docstring 을 그대로 붙이면 개발자용 설명이 사용자 화면에
        # 노출된다. 실제로 쓸 만한 예시만 보여준다.
        epilog=_EPILOG,
    )
    p.add_argument("input", nargs="?", default=None,
                   help="문서 파일 또는 디렉터리 (--check 만 할 때는 생략 가능)")
    p.add_argument("-o", "--out", default="out", help="출력 디렉터리 (기본: out)")
    p.add_argument(
        "--slim", action="store_true",
        help="document.json 에서 실행 기록(trace)을 빼고 본문·표만 담는다",
    )
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

    exp = p.add_argument_group("실험")
    exp.add_argument(
        "--exp",
        metavar="KEY[,KEY...]",
        help="실험 기법과 VLM 손잡이를 켭니다 (쉼표로 여럿). "
             "`--exp list` 로 목록을 봅니다. "
             "예: --exp grid_score,grid_restore,vlm_hint,vlm_steps",
    )

    align = p.add_argument_group("쪽 맞춤 (HWPX·HWP 에 PDF 쪽번호 물려주기)")
    align.add_argument(
        "--align",
        metavar="PDF",
        help="같은 문서의 PDF 를 지정해 쪽 번호를 붙입니다. 두 자리 모두 "
             "원본 문서(.hwpx/.pdf) 또는 이미 돌린 document.json 을 받습니다. "
             "원본을 주면 그 자리에서 판독까지 합니다. **두 자리 모두 폴더**를 "
             "주면 이름이 같은 쌍을 한꺼번에 맞춥니다 (HWPX 폴더 --align PDF 폴더)",
    )
    align.add_argument(
        "--align-format",
        choices=("json", "markdown", "both"),
        default="both",
        help="쪽 맞춤 산출 형식 (기본: both — aligned.json·aligned.md)",
    )
    p.add_argument(
        "--align-name",
        default="aligned",
        metavar="이름",
        help="쪽 맞춤 산출 파일 이름 (기본: aligned → aligned.json·aligned.md)",
    )

    render = p.add_argument_group("렌더링")
    render.add_argument(
        "--no-render",
        action="store_true",
        help="PDF 페이지 PNG 렌더 생략 (표 평가 정확도 하락)",
    )
    render.add_argument(
        "--render",
        action="store_true",
        help="표가 없는 쪽까지 전부 렌더 (기본은 표가 있는 쪽만)",
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
        "--no-pyhwp",
        action="store_true",
        help="HWP 를 pyhwp(AGPL) 없이 처리합니다 — olefile 텍스트 폴백으로 내려갑니다",
    )
    p.add_argument(
        "--cpu",
        action="store_true",
        help="GPU 를 쓰지 않습니다 (CUDA 오류가 날 때). --set device=cpu 와 같되 더 확실합니다",
    )
    p.add_argument(
        "--set",
        metavar="키=값",
        action="append",
        dest="settings",
        help="설정 지정 (여러 번 사용 가능). 예: --set llm_url=http://호스트:11060/v1",
    )
    p.add_argument(
        "--list-options",
        action="store_true",
        help="지정할 수 있는 설정 키를 출력하고 종료",
    )
    p.add_argument(
        "--ask-key",
        action="store_true",
        help="OpenAI 키를 입력받고 **OpenAI 만 씁니다** (화면·히스토리에 "
             "남지 않음). 사내 엔드포인트·로컬 VLM 설정은 이 실행에서 "
             "무시됩니다",
    )
    p.add_argument(
        "--key-file",
        metavar="경로",
        help="키가 담긴 파일에서 읽고 **OpenAI 만 씁니다** (첫 줄만 사용)",
    )
    p.add_argument(
        "--where",
        action="store_true",
        help="설치 위치와 버전만 출력하고 종료 (업그레이드가 반영됐는지 확인용)",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="환경·LLM 연결만 확인하고 종료 (파일 처리 안 함)",
    )
    p.add_argument(
        "--steps",
        choices=("brief", "user", "off", "dev", "silent", "none"),
        default="brief",
        help="진행 단계 표시 — brief: 굵은 13단계(기본) · dev: 진행수·건너뛴 "
             "이유·시간까지 · silent: 아무것도 내지 않음. "
             "`user`·`off` 는 brief, `none` 은 silent 의 별칭",
    )
    p.add_argument(
        "--steps-file",
        metavar="경로",
        help="진행 이벤트를 JSONL 로 남긴다 (프런트 스트리밍용). "
             "생략하면 산출 폴더의 progress.jsonl",
    )
    p.add_argument(
        "--align-rebuild",
        action="store_true",
        help="`--align` 에서 이미 돌린 결과가 있어도 다시 판독한다 "
             "(기본은 있으면 다시 씀)",
    )
    p.add_argument(
        "--jobs", "-j",
        type=int,
        default=1,
        metavar="N",
        help="문서 여러 건을 **프로세스 N개**로 나눠 처리 (기본 1). "
             "파싱은 순수 파이썬이라 스레드로는 빨라지지 않는다 — "
             "프로세스여야 한다. 0 이면 CPU 수만큼",
    )
    p.add_argument(
        # `--where` 는 이미 설치 위치를 내는 데 쓴다(0.4.x). 코드 길잡이는
        # `--guide` 로 둔다 — 묻는 것이 "내 설치가 어디냐" 가 아니라
        # "이 증상을 고치려면 어느 파일이냐" 이기 때문이다.
        "--guide",
        metavar="키워드",
        nargs="?",
        const="",
        help="고치고 싶은 것이 코드 어디에 있는지 찾는다 "
             "(예: --guide '스캔 pdf 표'). 인자 없이 쓰면 전체 목록",
    )
    return p


# ═══ 구간 2 — 실행 (단일 · 일괄 · 쪽 맞춤) ══════════════════════════════════════════
# _targets 가 대상을 모으고 _process 가 build_document → report 로 산출. _run_align 은 --align.
def _run_align(args) -> int:
    """`--align` 실행 — 두 판독 결과를 맞춰 쪽으로 나눈다.

    입력: args — 명령행 인자 (input · align · align_format · out)
    출력: 종료 코드 (0 성공, 1 실패)
    비고:
        쪽 없는 쪽(HWPX)이 `input`, 쪽을 가진 쪽(PDF)이 `--align` 이다.
        순서를 거꾸로 주면 PDF 본문을 PDF 쪽에 맞추는 꼴이라 뜻이 없어
        형식을 보고 미리 알린다 — 실행이 끝난 뒤에 알면 늦다.
    """
    import json

    from docstruct.align.documents import summary_lines, to_markdown
    from docstruct.align.pair import align_pair, out_folder_name

    left = Path(args.input).expanduser()
    right = Path(args.align).expanduser()
    # **폴더 둘이면 일괄** (0.5.70). 쪽 맞춤은 두 결과가 모두 있어야 되는
    # 일이므로 한쪽만 폴더인 것은 받지 않는다 — 짝을 추측하지 않는다.
    if left.is_dir() or right.is_dir():
        if not (left.is_dir() and right.is_dir()):
            print("오류: 둘 다 폴더이거나 둘 다 파일이어야 합니다 — "
                  f"{'폴더' if left.is_dir() else '파일'} {left} · "
                  f"{'폴더' if right.is_dir() else '파일'} {right}", file=sys.stderr)
            return 1
        return _run_align_folders(args, left, right)
    for path in (left, right):
        if not path.is_file():
            print(f"오류: 파일이 없습니다 — {path}", file=sys.stderr)
            return 1
    if left.suffix.lower() == ".pdf":
        print("오류: 첫 자리는 쪽이 **없는** 쪽(HWPX·HWP 또는 그 "
              "document.json)입니다. --align 자리에 PDF 를 주세요.",
              file=sys.stderr)
        return 1

    out_root = Path(args.out).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    print(f"\n=== 쪽 맞춤: {left.name} ← {right.name} ===")
    # **이미 돌린 결과가 있으면 다시 돌리지 않는다** (0.4.93). 예전에는
    # 맞출 때마다 두 건을 처음부터 판독했다 — 바로 앞에 돌려 둔 결과가
    # 옆 폴더에 있어도 쓰지 않았다. PDF 한 건이 몇 분씩 걸리는데.
    use_llm = not args.no_llm
    try:
        got = align_pair(
            left, right, out_root,
            reuse=not getattr(args, "align_rebuild", False),
            assess_tables=use_llm and not args.no_assess,
            fill_tables=use_llm and not args.no_fill,
            fill_all=args.fill_all,
            render_pages=not args.no_render,
            render_all=args.render,
            render_scale=args.scale,
            progress=getattr(args, "progress", False),
        )
    except ValueError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    for line in got.notes:
        print(f"  {line}")
    result = got.result

    # **저장 규칙은 `AlignPair.save` 하나뿐이다** (0.5.40). 예전에는 CLI 가
    # 직접 썼고 API 로 맞춘 쪽은 부르는 사람이 따로 썼다 — 같은 일을 두
    # 벌로 하면 반드시 어긋난다(이 프로젝트에서 여러 번 겪었다).
    try:
        written = got.save(out_root, formats=args.align_format,
                           stem=args.align_name)
    except ValueError as exc:                    # 이름이 쓸 수 없는 모양
        raise SystemExit(f"--align-name: {exc}") from exc

    for line in summary_lines(result):
        print(f"  {line}")
    if not args.quiet:
        print("  출력:")
        for path in written:
            print(f"    {path}")
    # **못 맞춘 표가 있어도 실패가 아니다.** 표지·간지 장식이나 목차처럼
    # PDF 에 표로 잡히지 않는 것이 늘 남는다 (실측: HWPX 580표 중 348개가
    # 레이아웃 표). 수치를 보여 주고 판단은 쓰는 쪽에 맡긴다.
    return 0


def _run_align_folders(args: argparse.Namespace, left: Path, right: Path) -> int:
    """HWPX 폴더 · PDF 폴더의 이름이 같은 쌍을 모두 맞춘다 (0.5.70).

    입력: args — 명령줄 인자, left — HWPX 폴더, right — PDF 폴더
    출력: 종료 코드 (실패한 쌍이 없으면 0)
    비고:
        짝 없는 파일은 실패로 치지 않지만 **반드시 알린다** — 조용히 건너뛰면
        빠진 줄 모른다.
    """
    from docstruct.align.batch import align_folders

    out_root = Path(args.out).expanduser().resolve()
    use_llm = not args.no_llm
    print(f"\n=== 쪽 맞춤 (폴더): {left} ← {right} ===")

    def show(index: int, total: int, name: str) -> None:
        print(f"  [{index}/{total}] {name}")

    try:
        batch = align_folders(
            left, right, out_root,
            reuse=not getattr(args, "align_rebuild", False),
            formats=args.align_format, stem=args.align_name, on_pair=show,
            assess_tables=use_llm and not args.no_assess,
            fill_tables=use_llm and not args.no_fill,
            fill_all=args.fill_all,
            render_pages=not args.no_render,
            render_all=args.render,
            render_scale=args.scale,
            progress=getattr(args, "progress", False),
        )
    except ValueError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    print()
    for line in batch.summary():
        print(f"  {line}")
    if not args.quiet:
        print(f"  기록: {out_root / 'align_batch.json'}")
    return 0 if batch.ok else 1


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


@contextmanager
def _step_reporting(src: Path, out_dir: Path, args):
    """이 문서를 도는 동안 진행 단계를 내보낸다.

    입력: src — 원본, out_dir — 산출 폴더, args — 명령행 인자
    출력: 없음 (컨텍스트 매니저)
    비고:
        **두 겹으로 낸다** (0.4.94). 화면에는 `--steps` 가 정한 만큼만
        보이고, JSONL 파일에는 언제나 전부 들어간다 — 프런트가 그 파일을
        읽어 스트리밍하기 때문이다. 화면 설정 때문에 스트림이 얇아지면
        안 된다.

        `--steps off` 라도 파일은 쓴다. 끄고 싶은 것은 터미널 소음이지
        기록이 아니다.
    """
    from docstruct.core.steps import (console_sink, jsonl_sink, normalize_mode,
                                      reporting, source_format_of)

    # `off` 는 **상세를 끈다**는 뜻으로 읽힌다 — 굵은 13단계로 보낸다.
    # 정말 아무것도 내지 않으려면 `silent` (0.4.99).
    sinks = []
    mode = normalize_mode(getattr(args, "steps", "brief"))
    if mode != "silent":
        sinks.append(console_sink(mode))

    target = getattr(args, "steps_file", None) or (out_dir / PROGRESS_FILENAME)
    try:
        Path(target).unlink(missing_ok=True)     # 이번 실행분만 남긴다
    except OSError:
        pass
    sinks.append(jsonl_sink(target))

    with reporting(src.name, source_format_of(src), sinks=sinks,
                   detail=mode) as got:
        try:
            yield got
        finally:
            got.done()


def _resolve_jobs(requested: int, target_count: int) -> int:
    """실제로 띄울 프로세스 수.

    입력: requested — `--jobs` 값 (0 이면 CPU 수), target_count — 문서 수
    출력: 1 이상의 정수
    비고:
        문서보다 많은 프로세스는 낭비다. 1건이면 언제나 1 — 프로세스를
        띄우는 비용(모듈 임포트·모델 적재)이 이득보다 크다.
    """
    import os as _os

    if target_count <= 1:
        return 1
    count = _os.cpu_count() or 1 if requested == 0 else max(1, requested)
    return min(count, target_count)


def _worker(payload: tuple) -> tuple[str, str | None]:
    """자식 프로세스에서 문서 하나를 처리한다.

    입력: payload — (경로 문자열, 산출 뿌리 문자열, 옵션 dict, 산출 폴더 이름)
    출력: (파일 이름, 실패 사유 또는 None)
    비고:
        **argparse 객체는 프로세스 사이로 못 보낸다** — dict 로 풀어 보내고
        여기서 다시 조립한다. 예외도 넘길 수 없으므로(자취가 안 실린다)
        사유 문자열로 바꿔 돌려준다. 자식이 죽어도 부모는 나머지를 계속한다.
    """
    import argparse

    src_s, out_s, opts, folder = payload
    args = argparse.Namespace(**opts)
    src, out_root = Path(src_s), Path(out_s)
    try:
        _process(src, out_root, args, folder)
        return src.name, None
    except Exception as exc:                     # noqa: BLE001 - 한 건이 전체를 멈추지 않는다
        spot = traceback.extract_tb(exc.__traceback__)
        where = ""
        if spot:
            last = spot[-1]
            where = f" @ {Path(last.filename).name}:{last.lineno} ({last.name})"
        return src.name, f"{type(exc).__name__}: {exc}{where}"


def _run_parallel(targets: list[Path], out_root: Path, args, jobs: int,
                  bar, folders: dict[str, str]) -> int:
    """문서들을 프로세스 여러 개로 나눠 처리한다.

    입력: targets — 문서 목록, out_root — 산출 뿌리, args, jobs — 프로세스 수, bar — 진행 막대
    출력: 실패 건수
    비고:
        **왜 프로세스인가.** 파싱(hwpxtree·hwp5tree)은 순수 파이썬이라 GIL 을
        놓지 않는다. 실측(조달청, 같은 문서): 스레드 1·2·4·8개에서 건당
        0.35·0.53·0.93·1.60초 — 처리량은 3.8건/초로 평평했다. 스레드를
        늘리면 **처리량은 그대로고 건당 응답만 나빠진다.**

        그리고 안전하다. 설정은 `os.environ` 을 거쳐 들어가고 `get_settings()`
        는 프로세스 전역 캐시다. 한 프로세스에서 서로 다른 설정으로 동시에
        돌리면 섞이므로 `api._applied` 가 락으로 직렬화한다 — 즉 같은
        프로세스 안에서는 애초에 병렬이 되지 않는다. 프로세스를 가르면
        전역이 각자의 것이 되어 락도 GIL 도 무관해진다.

        자식은 **부모의 환경변수를 물려받는다**(fork·spawn 모두). `--set`
        으로 준 설정이 그대로 따라가므로 자식마다 다시 적용할 필요가 없다.
    """
    import concurrent.futures as cf

    opts = vars(args).copy()
    opts.pop("input", None)
    payloads = [(str(src), str(out_root), opts, folders[str(src)])
                for src in targets]

    print(f"프로세스 {jobs}개로 {len(targets)}건을 처리합니다 "
          f"(같은 프로세스 안에서는 설정이 전역이라 병렬이 되지 않습니다).")
    failures = 0
    with cf.ProcessPoolExecutor(max_workers=jobs) as pool:
        for name, error in pool.map(_worker, payloads):
            if error:
                failures += 1
                print(f"\n=== {name} === 실패: {error}", file=sys.stderr)
            bar.update(1)
    bar.close()
    return failures


def _process(src: Path, out_root: Path, args, folder: str | None = None) -> None:
    """파일 하나를 처리하고 산출물을 저장한다.

    입력: path, out_dir, 실행 옵션, folder — 배정받은 산출 폴더 이름
    출력: 저장된 파일 경로 목록
    비고:
        `folder` 를 주지 않으면 예전처럼 파일 이름에서 만든다 — 한 건만
        돌릴 때는 겹칠 것이 없다. 여러 건일 때는 `assign_out_dirs` 가
        **겹치지 않게** 배정한 이름을 받는다(0.4.92).

        기본값도 **확장자를 포함한** 이름이다(`safe_file_name`) — 산출
        폴더 이름을 정하는 자리는 전부 이 규칙 하나를 쓴다(0.4.98).
    """
    out_dir = out_root / (folder or safe_file_name(src.name))
    out_dir.mkdir(parents=True, exist_ok=True)

    use_llm = not args.no_llm
    with _step_reporting(src, out_dir, args):
        doc = build_document(
            src,
            assess_tables=use_llm and not args.no_assess,
            fill_tables=use_llm and not args.no_fill,
            fill_all=args.fill_all,
            render_pages=not args.no_render,
            render_all=args.render,
            out_dir=out_dir,
            render_scale=args.scale,
            progress=getattr(args, "progress", False),
        )

    written = [
        write_markdown(doc, out_dir / "document.md"),
        write_json(doc, out_dir / "document.json", slim=args.slim),
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
        # **진행 기록이 어디 있는지 말한다** (0.4.95). 파일이 생겼는데
        # 위치를 말하지 않으면 없는 것과 같다.
        steps_file = getattr(args, "steps_file", None) or (out_dir / PROGRESS_FILENAME)
        if Path(steps_file).is_file():
            print(f"    {steps_file}   (진행 기록 · 프런트 스트리밍 원본)")


# ═══ 구간 3 — 설정·키 적용과 점검 출력 ═══════════════════════════════════════════════
# --set/--key/--check 처리. 키는 소스에 남기지 않는다(_apply_key).
def _print_code_guide(query: str) -> int:
    """`--guide` — 고치고 싶은 것이 어느 파일에 있는지 낸다.

    입력: query — 키워드 (빈 문자열이면 전체 목록)
    출력: 종료 코드 0
    비고:
        폴더 구조는 두 축(형식 · 인식)인데 실제 작업은 **두 축이 만나는
        자리**에서 일어난다 — "스캔 PDF 의 표" 는 converters/pdf 와 tables 와
        experiments 에 걸쳐 있다. 파일은 폴더 하나에만 살 수 있으니 이
        교차는 폴더로 표현할 수 없다. 표로 잇는다(`core.guide`).
    """
    from docstruct.core.guide import TOPICS, find_topics, format_topic

    hits = find_topics(query)
    if not hits:
        print(f"'{query}' 에 맞는 항목이 없습니다.\n")
        print("찾을 수 있는 것:")
        for topic in TOPICS:
            print(f"  {topic.key:<16} {topic.title}")
        print("\n예: docstruct --guide '스캔 pdf 표'")
        return 0

    if not query.strip():
        print("고치고 싶은 것 → 갈 곳 (자세히 보려면 키워드를 주세요)\n")
        for topic in TOPICS:
            print(f"  {topic.key:<16} {topic.title}")
        print("\n예: docstruct --guide '스캔 pdf 표'")
        return 0

    # 가장 잘 맞은 것은 펼쳐서, 나머지는 이름만.
    print(format_topic(hits[0]))
    if len(hits) > 1:
        print("\n  이것도 맞을 수 있습니다:")
        for topic in hits[1:5]:
            print(f"    {topic.key:<16} {topic.title}")
    return 0


def _print_where() -> None:
    """지금 **실제로 실행되는** docstruct 의 자리와 판을 낸다.

    입력: 없음
    출력: 없음 (표준출력)
    비고:
        **불러온 자리가 먼저다** (0.5.12). 예전에는 `importlib.metadata`
        를 먼저 물어 pip 정보가 있으면 그것을 찍었다. 그런데 pip 설치본과
        폴더 배포본이 **함께 있을 수 있다** — 폴더 안에서 실행하면 폴더가
        이기는데 화면에는 pip 의 판이 나왔다.

        실측 제보: 폴더 `docstruct-local-0_3_55` 안에서 돌렸는데

            docstruct 위치 : ...(윈도우 경로)...\\docstruct-local\\docstruct
            버전           : 0.4.99 (pip 설치본)      ← 저 폴더의 판이 아니다

        **고친 코드가 안 고쳐진 것처럼 보이는 가장 흔한 원인**이 이것이다.
        이제 불러온 자리 옆의 `VERSION` 을 권위로 삼고, pip 이 함께 있고
        판이 다르면 **둘 다 보여 주며 어느 쪽이 실행 중인지 밝힌다.**
    """
    import sys
    from pathlib import Path

    import docstruct

    from docstruct.core.version import details as version_details

    got = version_details()
    here = Path(got["location"])
    print(f"실행 중인 파이썬 : {sys.executable}")
    print(f"docstruct 위치   : {here}")

    folder_version = got["version"] if got["source"] == "folder" else None
    pip_version = got["pip_version"]
    pip_path = None
    if pip_version:
        try:
            from importlib.metadata import distribution

            pip_path = Path(str(distribution("docstruct").locate_file("docstruct"))).resolve()
        except Exception:                        # noqa: BLE001
            pip_path = None
    running_from_folder = got["source"] == "folder"

    if running_from_folder:
        print(f"버전             : {folder_version} (폴더 배포본 — 지금 이것이 돕니다)")
    elif pip_version:
        print(f"버전             : {pip_version} (pip 설치본)")
    else:
        print("버전             : 알 수 없음")

    # **둘이 함께 있으면 반드시 말한다.** 어느 쪽이 도는지 모르면
    # "갱신했는데 그대로" 를 계속 겪는다.
    if running_from_folder and pip_version:
        print()
        print(f"⚠ pip 설치본도 있습니다: {pip_version} @ {pip_path}")
        print("  지금은 **폴더 쪽**이 돕니다 (이 폴더 안에서 실행 중).")
        print("  다른 디렉터리에서 실행하면 pip 쪽이 돌아 판이 달라집니다.")
        print(f'  pip 쪽을 지우려면: "{sys.executable}" -m pip uninstall docstruct')

    print()
    if running_from_folder:
        print("이 설치는 **폴더 배포본**입니다. 갱신하려면 폴더를 통째로")
        print("바꾸세요 — 덮어쓰기(cp -r)는 사라진 파일을 남깁니다:")
        print("  rsync -a --delete <새 폴더>/ <이 폴더>/")
        print("  (윈도우: 옛 폴더를 지우고 새로 풀어 쓰세요)")
    else:
        print("업그레이드가 반영되지 않았다면:")
        print(f'  "{sys.executable}" -m pip install -U --force-reinstall --no-cache-dir \\')
        print(f'    "docstruct @ git+http://183.96.152.133/mjseo/docstruct.git@'
              f'v{pip_version or folder_version or "<판>"}"')


def _print_options() -> None:
    """지정할 수 있는 설정 키를 출력한다.

    입력: 없음
    출력: 없음 (표준출력)
    """
    from docstruct.api import _ENV_KEYS, _RUN_KEYS, defaults

    known = defaults()
    print("--set 으로 지정할 수 있는 키\n")
    for title, keys in (
        ("설정 (환경변수로 전달)", sorted(_ENV_KEYS)),
        ("실행 옵션 (전용 플래그가 따로 있음)", sorted(_RUN_KEYS)),
    ):
        print(f"  [{title}]")
        for k in keys:
            hint = f"  (기본 {known[k]})" if k in known else ""
            print(f"    {k}{hint}")
        print()
    print("예: docstruct 문서.pdf --set llm_url=http://호스트:11060/v1 \\")
    print("                      --set llm_model=/model/이름 --set llm_concurrency=8")


def _apply_settings(pairs: list[str] | None) -> None:
    """``--set 키=값`` 목록을 적용한다.

    입력: pairs — "키=값" 문자열 목록. None 이면 아무것도 하지 않음
    출력: 없음
    예외: 형식이 잘못됐거나 알 수 없는 키면 SystemExit

    비고:
        파이썬 API 의 configure() 와 같은 키를 쓴다. 값은 문자열로 오지만
        불리언·숫자는 설정 계층에서 해석하므로 그대로 넘긴다.
    """
    if not pairs:
        return

    from docstruct.api import DocStructError, configure

    options: dict[str, str] = {}
    for item in pairs:
        if "=" not in item:
            raise SystemExit(
                f"--set 형식이 잘못됐습니다: {item!r}\n"
                "  키=값 형태로 주세요. 예: --set llm_concurrency=8\n"
                "  사용 가능한 키는 --list-options 로 확인하세요."
            )
        key, value = item.split("=", 1)
        options[key.strip()] = value.strip()

    try:
        applied = configure(**options)
    except DocStructError as exc:
        raise SystemExit(f"{exc}\n  --list-options 로 키 목록을 확인하세요.") from exc

    _log.info("설정 적용: %s", applied)


def _apply_key(args) -> None:
    """명령행 옵션으로 지정한 API 키를 적용한다.

    입력: args — 파싱된 명령행 인자
    출력: 없음 (환경변수 설정)
    예외: 파일을 읽지 못하거나 내용이 비면 OSError/ValueError

    비고:
        키를 인자로 직접 받지 않는다. ``--api-key sk-...`` 형태는 셸
        히스토리와 프로세스 목록(`ps`)에 그대로 남기 때문이다.
        입력받거나(``--ask-key``) 파일에서 읽는다(``--key-file``).

        **키를 주면 OpenAI 만 쓴다** (0.4.59). 예전에는 키만 넣었는데,
        사내 배치는 엔드포인트가 내장 기본값(site_defaults.py)에 들어
        있어 주소가 늘 차 있고 `_key_for` 는 OpenAI 가 아닌 주소에
        OpenAI 키를 붙이지 않는다 — 그래서 **키를 입력받고도 사내
        엔드포인트로 가고 키는 버려졌다.** 키를 물어 놓고 쓰지 않는 것은
        조용한 거짓말이다.
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

        # **여기서 걸러야 한다.** 못 쓸 키를 들고 가면 추출을 마친 뒤에야
        # 호출 단계에서 터진다 — 실측(행정안전부 429쪽): 6분 걸려 추출을
        # 끝내고 나서 쪽마다 실패해 표 321개가 미판정으로 남았다. 키가
        # 잘못된 것은 **1초 만에 알 수 있는 일**이다.
        from docstruct.core.config import key_problem

        problem = key_problem(key)
        if problem:
            raise ValueError(problem)
        os.environ["OPENAI_API_KEY"] = key
        # **이 실행은 OpenAI 로 간다.** 주소·로컬 VLM 내장 기본값을 덮는다.
        # (환경변수로 직접 지정한 주소·모델은 그대로 이긴다 — 모델을 고를
        # 길까지 막으면 안 된다.)
        os.environ["DOCSTRUCT_FORCE_OPENAI"] = "1"
        from docstruct.core.config import rebuild_settings
        from docstruct.core.checks import invalidate_caches

        rebuild_settings()
        invalidate_caches()
        from docstruct.core.config import get_settings

        endpoint = get_settings().llm
        if endpoint:
            print(f"  키를 받았습니다 — {endpoint.url} · {endpoint.model} "
                  "(사내 엔드포인트·로컬 VLM 은 이 실행에서 쓰지 않습니다)")
        else:
            print("  키를 받았지만 쓸 엔드포인트를 세우지 못했습니다 — "
                  "`--check` 로 확인하세요")


def _print_check() -> int:
    """환경과 LLM 연결을 확인해 출력한다.

    입력: 없음
    출력: 종료 코드 (연결 성공 0, 실패 1)
    """
    from docstruct.core.config import get_settings

    from docstruct.core.checks import environment

    print("=== 환경 ===")
    for item in environment():
        print(f"  {'OK  ' if item['ok'] else 'WARN'} {item['item']:20} {item['note']}")

    print("\n=== 설정 ===")
    for label, value, ok in get_settings().describe():
        print(f"  {'OK  ' if ok else 'WARN'} {label:20} {value}")

    print("\n=== LLM 연결 ===")
    from docstruct.core.checks import check_llm_reachable

    ok, message = check_llm_reachable()
    print(("  OK   " if ok else "  WARN ") + message)
    return 0 if ok else 1


#: `--exp` 로 켤 수 있는 VLM 손잡이. 실험 키와 이름이 겹치지 않는다.
#: 왜 여기 두는가: A/B 를 돌릴 때 실험은 `--exp`, VLM 은 환경변수로 갈라져
#: 있으면 판마다 두 곳을 건드려야 하고, 앞 판의 환경변수를 지우지 않으면
#: 다음 판에 새어 들어 **비교가 조용히 망가진다.** 한 자리에서 켠다.
#: 손잡이 설명 (`--exp list` 출력용).
# ═══ 구간 4 — 실험 손잡이 (--exp) ═══════════════════════════════════════════════
# 실험 키 → 환경변수. no_<키> 로 기본 켬을 끈다. 목록은 docstruct.experiments.report.
_KNOB_HELP = {
    "vlm": "표 재구성을 켠다 (LLM 이 있으면 이미 기본 켜짐)",
    "vlm_hint": "지면 격자가 짚은 병합 자리를 지시문에 넣는다 (H10)",
    "vlm_steps": "단계별 지시판을 쓴다 — 행 세기→열 세기→병합→작성 (H10)",
    "vlm_best4": "후보 4판을 만들고 결정론 채점으로 고른다 (H12-b)",
    "vlm_formula": "산식을 계산하지 말고 기호 그대로 옮기게 한다 (C2)",
    "no_vlm": "표 재구성을 끈다 — A/B 의 대조군",
    "no_vlm_hint": "힌트를 끈다 — 기본 켬(0.4.3)이므로 대조군에 쓴다",
}

_VLM_KNOBS: dict[str, tuple[str, str]] = {
    "vlm": ("DOCSTRUCT_VLM_FIX_TABLES", "1"),
    "vlm_hint": ("DOCSTRUCT_VLM_HINT_MISSING", "1"),
    "vlm_steps": ("DOCSTRUCT_VLM_PROMPT", "steps"),
    "vlm_best4": ("DOCSTRUCT_VLM_BEST_OF", "4"),
    "vlm_formula": ("DOCSTRUCT_VLM_KEEP_FORMULA", "1"),
    "no_vlm": ("DOCSTRUCT_VLM_FIX_TABLES", "false"),
    "no_vlm_hint": ("DOCSTRUCT_VLM_HINT_MISSING", "false"),
}


def _enable_experiments(spec: str) -> list[str] | None:
    """`--exp` 로 지정한 실험을 켠다.

    입력: spec — 쉼표로 이은 키. `list` 면 목록만 낸다
    출력: 켠 키 목록. 목록 출력이거나 잘못된 키면 None
    비고:
        실험은 **환경변수로만** 켠다. `Settings` 에 넣지 않는 것은 폐기할
        때 본체를 건드리지 않기 위함이다. 이 함수는 그 환경변수를 대신
        세팅해 준다 — 서버에서는 여전히 환경변수를 직접 쓴다.
    """
    from docstruct.experiments import all_experiments
    known = {e.key: e for e in all_experiments()}
    if spec.strip().lower() in ("list", "?"):
        from docstruct.experiments.report import lines

        print("\n".join(lines()))
        print("\nVLM 손잡이 (같은 --exp 로 켭니다)")
        print("─" * 60)
        for name, (env, value) in sorted(_VLM_KNOBS.items()):
            print(f"  {name:<12} {_KNOB_HELP.get(name, '')}")
            print(f"  {'':<12} ({env}={value})")
        return None

    keys = [k.strip() for k in spec.split(",") if k.strip()]

    # **VLM 손잡이도 같은 자리에서 켠다.** 실험은 `--exp`, VLM 은 환경변수로
    # 갈라져 있으면 A/B 를 돌릴 때 판마다 두 곳을 건드려야 하고, cmd 에서
    # 지우는 것을 잊으면 앞 판의 설정이 다음 판에 새어 든다 — 비교가 조용히
    # 망가진다. 이름은 실험 키와 겹치지 않는다.
    knobs = [k for k in keys if k in _VLM_KNOBS]
    keys = [k for k in keys if k not in _VLM_KNOBS]
    for knob in knobs:
        name, value = _VLM_KNOBS[knob]
        os.environ[name] = value

    # **끄기(`no_<키>`).** 승격된 실험은 기본으로 켜져 있으므로(0.4.3),
    # A/B 대조군을 만들려면 빼는 수단이 있어야 한다. `--exp` 에서 빼는 것
    # 만으로는 꺼지지 않는다 — 그 점을 모르면 대조군이 조용히 실험군과
    # 같아진다.
    offs = [k[3:] for k in keys if k.startswith("no_") and k[3:] in known]
    keys = [k for k in keys if not (k.startswith("no_") and k[3:] in known)]
    for key in offs:
        os.environ[known[key].env] = "false"

    unknown = [k for k in keys if k not in known]
    if unknown:
        from docstruct.experiments import stale_modules
        print(f"모르는 실험: {', '.join(unknown)}", file=sys.stderr)
        print(f"쓸 수 있는 것: {', '.join(sorted(known))}", file=sys.stderr)
        print(f"VLM 손잡이: {', '.join(sorted(_VLM_KNOBS))}", file=sys.stderr)
        try:
            from importlib.metadata import version

            print(f"설치된 docstruct: {version('docstruct')}", file=sys.stderr)
        except Exception:                        # noqa: BLE001 - 진단 보조다
            pass
        # 폐기된 실험 파일이 보이면 배포본이 낡은 것이다 — 새 실험이 없는
        # 이유도 대개 그것이다 (덮어쓰기 배포는 사라진 파일을 지우지 않는다).
        stale = stale_modules()
        if stale:
            print(
                "  ⚠ 폐기된 실험 파일이 남아 있습니다: "
                f"{', '.join(f'{s}.py' for s in stale)}\n"
                "     배포본이 낡았을 수 있습니다. 새로 받은 폴더로 "
                "덮어쓰고 남은 파일을 지우세요.",
                file=sys.stderr,
            )
        return None

    for key in keys:
        os.environ[known[key].env] = "true"
    if keys or knobs:
        parts = list(keys) + [f"{k}(VLM)" for k in knobs]
        print(f"실험 켬: {', '.join(parts)}")
    if offs:
        print(f"실험 끔: {', '.join(offs)}")
    return keys


# ═══ 구간 5 — 진입점 ══════════════════════════════════════════════════════════
# 예외를 사람이 읽을 힌트로 바꾸고(_failure_hint) main 이 순서를 잇는다.
def _failure_hint(exc: Exception) -> str | None:
    """실패 메시지에 덧붙일 안내.

    입력: exc — 잡힌 예외
    출력: 안내 문자열. 짚을 것이 없으면 None
    비고:
        원인이 환경 설정인데 메시지만으로는 무엇을 만져야 할지 알 수 없는
        경우가 있다. **다음 수를 알려 주는 것**이 목적이다.
    """
    import os

    message = str(exc)
    if "set_num_threads" in message or "positive integer" in message:
        current = os.environ.get("OMP_NUM_THREADS", "(없음)")
        return (
            f"  -> 스레드 수 설정 문제입니다 (OMP_NUM_THREADS={current!r}).\n"
            "     torch 는 양수만 받습니다. 이렇게 다시 해 보세요:\n"
            "       --set num_threads=4\n"
            "     또는 셸에서: set OMP_NUM_THREADS=4   (Windows)\n"
            "                  export OMP_NUM_THREADS=4 (Linux/macOS)"
        )
    if "docling" in message.lower() and "install" in message.lower():
        return "  -> docling-slim 설치가 필요합니다. `--check` 로 진단해 보세요."
    return None


def main(argv: list[str] | None = None) -> int:
    """CLI 실행.

    입력: argv — 명령행 인자 (None 이면 sys.argv)
    출력: 종료 코드 (0 성공, 1 실패)
    """
    # **가장 먼저 한다.** cp949 콘솔(윈도우 한국어 기본)은 줄표(`—`) 하나에
    # UnicodeEncodeError 를 내며 죽는다 — 출력·로그 96곳에 그런 문자가 있다.
    from docstruct.core.winfix import make_console_safe

    make_console_safe()

    # OMP_NUM_THREADS=0 같은 값은 Docling 을 거쳐 torch 에서 죽는다
    # (set_num_threads expects a positive integer). 여기서 바로잡는다.
    from docstruct.core.config import sanitize_thread_env

    sanitize_thread_env()

    args = _build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else (logging.WARNING if args.quiet else logging.INFO),
        format="%(levelname)-7s %(name)s: %(message)s",
    )

    if getattr(args, "no_pyhwp", False):
        # 코드는 그대로 두고 경로만 건너뛴다 — 켜면 다시 쓸 수 있다.
        os.environ["DOCSTRUCT_HWP_NO_PYHWP"] = "1"

    if args.cpu:
        # docling 의 import 사슬이 CUDA 를 건드리기 전에 막아야 한다.
        # 여기가 가장 이른 지점이다 (아직 torch 를 import 하지 않았다).
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
        os.environ["DOCLING_DEVICE"] = "cpu"

    if args.where:
        _print_where()
        return 0

    if args.list_options:
        _print_options()
        return 0

    if args.exp:
        if _enable_experiments(args.exp) is None:
            return 0 if args.exp.strip().lower() in ("list", "?") else 1

    try:
        _apply_settings(args.settings)
        _apply_key(args)
    except (OSError, ValueError) as exc:
        print(f"키 설정 실패: {exc}", file=sys.stderr)
        return 1

    if args.guide is not None:
        return _print_code_guide(args.guide)

    if args.check:
        return _print_check()

    if not args.input:
        _build_parser().error("처리할 문서 파일 또는 디렉터리를 지정하세요.")

    if args.align:
        # 쪽 맞춤은 **두 결과가 모두 있어야** 되는 일이라 일괄 처리
        # 루프(디렉터리·--glob)와 성질이 다르다. 따로 간다.
        return _run_align(args)

    try:
        targets = _targets(Path(args.input).expanduser(), args.glob)
    except FileNotFoundError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1

    out_root = Path(args.out).expanduser().resolve()
    failures = 0

    from docstruct.core.progress import ProgressBar

    bar = ProgressBar(
        len(targets), "문서 처리", unit="건",
        enabled=args.progress and len(targets) > 1,
    )
    # **겹치지 않는 산출 폴더를 먼저 배정한다** (0.4.92). 예전에는 파일
    # 이름에서 바로 만들어, `성과계획서.hwpx` 와 `성과계획서.pdf` 가 같은
    # 폴더를 써서 하나가 조용히 덮였다 — 그러고도 "n건 성공" 이라 적었다.
    folders = assign_out_dirs([str(src) for src in targets])
    renamed = describe_renames(folders)
    if renamed:
        print(f"산출 폴더 이름이 겹쳐 {len(renamed)}건을 구분했습니다:")
        for line in renamed:
            print(f"  {line}")

    jobs = _resolve_jobs(args.jobs, len(targets))
    if jobs > 1:
        return _run_parallel(targets, out_root, args, jobs, bar, folders)

    for src in targets:
        try:
            _process(src, out_root, args, folders[str(src)])
        except Exception as exc:
            failures += 1
            print(f"\n=== {src.name} === 실패: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            # **어디서 났는지** 한 줄은 항상 남긴다. 메시지만으로는 손댈
            # 곳을 알 수 없다 (예: "cannot unpack non-iterable NoneType").
            spot = traceback.extract_tb(exc.__traceback__)
            if spot:
                last = spot[-1]
                print(f"    위치: {Path(last.filename).name}:{last.lineno} "
                      f"({last.name}) — {(last.line or '').strip()[:70]}",
                      file=sys.stderr)
                print("    전체 자취를 보려면 --verbose 를 붙이세요.",
                      file=sys.stderr)
            hint = _failure_hint(exc)
            if hint:
                print(hint, file=sys.stderr)
            if args.verbose:
                traceback.print_exc()
        bar.update(1, src.name)

    bar.close()

    if len(targets) > 1:
        print(f"\n총 {len(targets)}건 중 {len(targets) - failures}건 성공, {failures}건 실패")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
