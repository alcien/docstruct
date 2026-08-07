"""pkg(단일 소스) → docstruct-local / backend-overlay 생성.

역할:
    배포 형태가 셋(pip 패키지·로컬 개발본·backend 덮어쓰기)인데 코드는
    하나다. 셋의 차이는 **임포트 루트와 폴더 배치뿐**이므로, pkg 를 원본으로
    두고 나머지 둘을 기계적으로 만들어 낸다. 손으로 세 벌을 맞추면 수정
    한 건마다 세 곳을 고쳐야 하고 언젠가 갈라진다.
호출부:
    `python tools/sync_trees.py --out dist/` (배포 전 수동 실행 또는 CI)
    `python tools/sync_trees.py --check`     (세 벌이 일치하는지만 검사)
출력:
    dist/docstruct-local/ 과 dist/overlay/ . --check 는 종료 코드로만 알린다.

트리별 차이
-----------
=========  ================================  =============================
트리       임포트 루트                        배치
=========  ================================  =============================
pkg        ``docstruct.converters`` 등        ``src/docstruct/**``
local      ``converters`` / ``docstruct``     ``converters/`` ``docstruct/`` …
overlay    local 과 동일 + ``rag``            ``app/`` 아래에 같은 배치
=========  ================================  =============================

overlay 에는 backend 원본이 소유한 ``rag/`` 와
``infrastructure/observability/`` 가 추가로 들어간다. 이 둘은 pkg 에 없으므로
``--extras`` 로 지정한 폴더에서 그대로 복사한다.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "docstruct"

#: pkg 에서 최상위 패키지로 승격되는 하위 패키지.
#: ``docstruct.converters`` → ``converters`` 처럼 접두사가 벗겨진다.
PROMOTED = ("converters", "core", "infrastructure")

#: local/overlay 에만 있고 pkg 에는 없는 파일 (배포 형태 전용).
PKG_ONLY = ("core/site_defaults.example.py",)


def rewrite(text: str) -> str:
    """pkg 임포트를 local/overlay 임포트로 바꾼다.

    입력: text — pkg 기준 소스
    출력: 임포트 루트가 치환된 소스
    비고:
        ``docstruct.converters`` 처럼 승격 대상이 뒤에 붙은 경우가 먼저다.
        순서를 바꾸면 ``docstruct.`` 가 먼저 지워져 ``converters`` 를 못 찾는다.
        문자열·주석 안의 경로 표기(``docstruct.core.config`` 등)도 같이 바꾼다 —
        오류 메시지가 실제 임포트 경로와 어긋나면 디버깅이 어려워진다.
    """
    promoted = "|".join(PROMOTED)
    # 1) docstruct.converters.* → converters.*
    text = re.sub(rf"\bdocstruct\.({promoted})\b", r"\1", text)
    # 2) 나머지 docstruct.xxx 는 그대로 둔다 (local/overlay 도 docstruct/ 패키지 유지)
    return text


def target_path(rel: Path) -> Path:
    """pkg 안의 상대 경로를 local/overlay 배치로 옮긴다.

    입력: rel — src/docstruct 기준 상대 경로
    출력: 대상 트리 기준 상대 경로
    """
    if rel.parts and rel.parts[0] in PROMOTED:
        return rel                      # converters/… core/… infrastructure/…
    return Path("docstruct") / rel      # 나머지는 docstruct/ 아래


def build(dest: Path, *, prefix: Path = Path(".")) -> list[Path]:
    """pkg 를 dest 에 배치·치환해 쓴다.

    입력: dest — 만들 트리의 루트, prefix — 트리 안 추가 접두사(overlay 는 app/)
    출력: 쓴 파일 목록
    """
    written = []
    for src in sorted(SRC.rglob("*")):
        if not src.is_file() or "__pycache__" in src.parts:
            continue
        rel = src.relative_to(SRC)
        if rel.as_posix() in PKG_ONLY:
            continue
        out = dest / prefix / target_path(rel)
        out.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix == ".py":
            out.write_text(rewrite(src.read_text(encoding="utf-8")), encoding="utf-8")
        else:
            shutil.copy2(src, out)
        written.append(out)
    return written


#: 세 트리가 공유하는 비-코드 파일. 여기서 한 번만 관리한다.
#: (.env.example 이 트리마다 따로 관리되면서 서로 다른 키가 빠져 있었다.)
SHARED_DOCS = (
    ".env.example", ".gitignore", "API.md", "BUGFIXES.md", "CLI.md",
    "README.md", "RESTRUCTURE.md", "WINDOWS.md", "GIT.md",
)


def copy_docs(dest: Path, version: str) -> int:
    """공유 문서와 VERSION 을 트리에 넣는다.

    입력: dest — 트리 루트, version — VERSION 파일에 쓸 문자열
    출력: 쓴 파일 수
    """
    count = 0
    for name in SHARED_DOCS:
        src = ROOT / name
        if not src.is_file():
            continue
        shutil.copy2(src, dest / name)
        count += 1
    (dest / "VERSION").write_text(version + "\n", encoding="utf-8")
    return count + 1


def copy_notebooks(dest: Path) -> int:
    """노트북과 샘플을 임포트 치환해 트리에 넣는다.

    입력: dest — 트리 루트
    출력: 쓴 파일 수
    비고:
        .ipynb 안의 코드 셀에도 `docstruct.core` 같은 경로가 들어 있다.
        여기서 치환하지 않으면 pkg 에서 만든 노트북이 local 트리에서
        `ModuleNotFoundError` 를 낸다 (실제로 그런 상태였다).
    """
    src_dir = ROOT / "notebooks"
    if not src_dir.is_dir():
        return 0
    count = 0
    for src in sorted(src_dir.rglob("*")):
        if not src.is_file() or "__pycache__" in src.parts:
            continue
        out = dest / "notebooks" / src.relative_to(src_dir)
        out.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix in (".py", ".ipynb"):
            out.write_text(rewrite(src.read_text(encoding="utf-8")), encoding="utf-8")
        else:
            shutil.copy2(src, out)
        count += 1
    return count


def copy_tests(dest: Path) -> int:
    """테스트를 임포트 치환해 트리에 넣는다.

    입력: dest — 트리 루트
    출력: 쓴 파일 수
    비고:
        local 트리에만 넣는다. overlay 는 backend 원본 위에 덮어쓰는
        폴더라 테스트가 섞이면 배포물이 지저분해진다.
    """
    src_dir = ROOT / "tests"
    if not src_dir.is_dir():
        return 0
    count = 0
    for src in sorted(src_dir.rglob("*.py")):
        if "__pycache__" in src.parts:
            continue
        out = dest / "tests" / src.relative_to(src_dir)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rewrite(src.read_text(encoding="utf-8")), encoding="utf-8")
        count += 1
    return count


def project_version() -> str:
    """pyproject.toml 의 version 을 읽는다.

    입력: 없음
    출력: 버전 문자열 (없으면 'unknown')
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    return m.group(1) if m else "unknown"


def copy_extras(dest: Path, extras: Path, *, prefix: Path = Path(".")) -> int:
    """overlay 전용 폴더(rag, observability)를 그대로 복사한다.

    입력: dest — 트리 루트, extras — 원본이 있는 overlay/app 경로
    출력: 복사한 파일 수
    """
    count = 0
    for rel in ("rag", "infrastructure/observability", "main.py.patched"):
        src = extras / rel
        if not src.exists():
            print(f"  ! 없음, 건너뜀: {src}")
            continue
        dst = dest / prefix / rel
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__"))
            count += sum(1 for _ in dst.rglob("*") if _.is_file())
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            count += 1
    return count


def check(existing: Path, prefix: Path) -> list[str]:
    """이미 있는 트리가 pkg 에서 생성한 것과 같은지 검사한다.

    입력: existing — 검사할 트리 루트, prefix — 트리 안 접두사
    출력: 어긋난 항목 설명 목록 (비면 일치)
    """
    problems = []
    seen = set()
    for src in sorted(SRC.rglob("*.py")):
        if "__pycache__" in src.parts:
            continue
        rel = src.relative_to(SRC)
        if rel.as_posix() in PKG_ONLY:
            continue
        want = rewrite(src.read_text(encoding="utf-8"))
        got_path = existing / prefix / target_path(rel)
        seen.add(got_path)
        if not got_path.is_file():
            problems.append(f"없음: {got_path}")
        elif got_path.read_text(encoding="utf-8") != want:
            problems.append(f"내용 다름: {got_path}")
    return problems


def main() -> int:
    """명령행 진입점.

    입력: 없음 (argv 사용)
    출력: 종료 코드 (0 정상, 1 불일치)
    """
    ap = argparse.ArgumentParser(description="pkg → local/overlay 생성")
    ap.add_argument("--out", type=Path, default=ROOT / "dist",
                    help="생성 위치 (기본 dist/)")
    ap.add_argument("--extras", type=Path,
                    help="overlay 전용 폴더가 있는 기존 overlay/app 경로")
    ap.add_argument("--check", nargs=2, metavar=("LOCAL", "OVERLAY"),
                    help="기존 두 트리가 pkg 와 일치하는지만 검사")
    args = ap.parse_args()

    if args.check:
        local, overlay = Path(args.check[0]), Path(args.check[1])
        bad = check(local, Path(".")) + check(overlay, Path("app"))
        if bad:
            print(f"불일치 {len(bad)}건")
            for b in bad[:40]:
                print("  ✘", b)
            return 1
        print("세 트리 일치")
        return 0

    local = args.out / "docstruct-local"
    overlay = args.out / "overlay"
    for d in (local, overlay):
        if d.exists():
            shutil.rmtree(d)

    version = project_version()

    n1 = len(build(local))
    n1 += copy_docs(local, f"docstruct-local {version}")
    n1 += copy_tests(local)
    n1 += copy_notebooks(local)
    print(f"docstruct-local : {n1}개 파일 (문서·VERSION·tests 포함)")

    n2 = len(build(overlay, prefix=Path("app")))
    (overlay / "app" / "VERSION").write_text(
        f"docstruct-overlay {version}\n", encoding="utf-8"
    )
    print(f"overlay         : {n2 + 1}개 파일")
    # 노트북은 pkg 에서 생성한다. extras 쪽 사본을 쓰면 pkg 를 고쳐도
    # overlay 만 옛 버전으로 남는다 (실제로 그런 상태였다).
    n2 += copy_notebooks(overlay / "app")
    print(f"  + 노트북        : pkg 기준으로 재생성")
    if args.extras:
        n3 = copy_extras(overlay, args.extras, prefix=Path("app"))
        print(f"  + overlay 전용  : {n3}개 파일 (rag / observability / main.py.patched)")
    else:
        print("  ! --extras 미지정 — rag/ observability/ 가 빠집니다")

    return 0


if __name__ == "__main__":
    sys.exit(main())
