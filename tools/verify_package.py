"""패키지 무결성 검사.

역할:
    빌드 전에 패키지가 실제로 동작하는지 확인한다. 특히 함수 안에 숨은
    지연 import 는 구문 검사로 잡히지 않으므로, 모든 import 문을 정적으로
    수집해 실제로 불러본다.
호출부:
    `python tools/verify_package.py` (배포 전 수동 실행 또는 CI)
출력:
    표준출력에 항목별 결과. 실패가 하나라도 있으면 종료 코드 1
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "docstruct"

#: 패키지로 옮기면서 docstruct 하위로 들어간 최상위 이름들.
#: 이 이름으로 시작하는 import 가 남아 있으면 설치 환경에서 깨진다.
MOVED = ("converters", "core", "infrastructure")


def check_moved_imports() -> list[str]:
    """구 경로 import 가 남아 있는지 검사한다.

    입력: 없음
    출력: 문제 위치 목록 (없으면 빈 목록)
    비고:
        함수 안의 지연 import 까지 AST 로 훑는다. 문자열 검색이 아니라
        구문 트리를 보므로 주석·문자열에 걸리는 오탐이 없다.
    """
    problems = []
    for f in sorted(SRC.rglob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mod = None
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mod = node.module
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] in MOVED:
                        problems.append(f"{f.relative_to(ROOT)}:{node.lineno} import {a.name}")
                continue
            if mod and mod.split(".")[0] in MOVED:
                problems.append(f"{f.relative_to(ROOT)}:{node.lineno} from {mod}")
    return problems


def check_cache_decorators() -> list[str]:
    """`X.cache_clear()` 를 부르는데 X 에 캐시 데코레이터가 없는 경우를 찾는다.

    입력: 없음
    출력: 문제 설명 목록 (없으면 빈 목록)
    비고:
        함수 정의를 삽입하다 데코레이터와 함수 사이에 끼워 넣으면
        데코레이터가 엉뚱한 함수에 붙는다. 구문 오류가 나지 않아
        실행 시점에야 AttributeError 로 드러난다.
    """
    import re

    CACHED = {"lru_cache", "cache", "cached_property"}
    decorated: set[str] = set()
    for f in SRC.rglob("*.py"):
        if "__pycache__" in str(f):
            continue
        for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for d in n.decorator_list:
                    name = (
                        d.func.id
                        if isinstance(d, ast.Call) and isinstance(d.func, ast.Name)
                        else d.id if isinstance(d, ast.Name)
                        else getattr(d, "attr", None)
                    )
                    if name in CACHED:
                        decorated.add(n.name)

    problems = []
    for f in sorted(SRC.rglob("*.py")):
        if "__pycache__" in str(f):
            continue
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"(\w+)\.cache_clear\(\)", text):
            target = m.group(1)
            if target not in decorated:
                line = text[: m.start()].count("\n") + 1
                problems.append(
                    f"{f.relative_to(ROOT)}:{line} {target}.cache_clear() — "
                    f"{target} 에 캐시 데코레이터가 없습니다"
                )
    return problems


def collect_imports() -> set[str]:
    """패키지 안에서 쓰는 docstruct 내부 모듈 경로를 모은다.

    입력: 없음
    출력: `docstruct.xxx` 형태 모듈 경로 집합
    """
    mods = set()
    for f in sorted(SRC.rglob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                if node.module.startswith("docstruct"):
                    mods.add(node.module)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.startswith("docstruct"):
                        mods.add(a.name)
    return mods


def check_importable(python: str) -> list[str]:
    """수집한 모듈을 실제로 import 해 본다.

    입력: python — 검사에 쓸 인터프리터 경로 (docstruct 가 설치된 환경)
    출력: 실패한 모듈과 사유 목록
    비고:
        설치되지 않은 인터프리터를 주면 전부 실패로 나오므로,
        패키지를 설치한 venv 의 python 을 지정해야 한다.
    """
    mods = sorted(collect_imports())
    code = (
        "import importlib, sys\n"
        f"mods = {mods!r}\n"
        "bad = []\n"
        "for m in mods:\n"
        "    try:\n"
        "        importlib.import_module(m)\n"
        "    except Exception as e:\n"
        "        bad.append(f'{m} — {type(e).__name__}: {e}')\n"
        "print('\\n'.join(bad))\n"
    )
    out = subprocess.run([python, "-c", code], capture_output=True, text=True)
    return [l for l in out.stdout.strip().splitlines() if l]


def main() -> int:
    """검사를 수행한다.

    입력: 없음 (argv[1] 로 인터프리터 지정 가능)
    출력: 종료 코드 (문제 없으면 0)
    """
    python = sys.argv[1] if len(sys.argv) > 1 else sys.executable
    failed = False

    # docstruct 가 설치되지 않은 인터프리터면 ② 는 의미가 없다.
    probe = subprocess.run(
        [python, "-c", "import docstruct"], capture_output=True, text=True
    )
    installed = probe.returncode == 0

    print("① 구 경로 import 검사 (converters / core / infrastructure)")
    problems = check_moved_imports()
    if problems:
        failed = True
        for p in problems:
            print(f"   ✘ {p}")
    else:
        print("   OK — 없음")

    print("\n② 캐시 데코레이터 검사")
    cache_problems = check_cache_decorators()
    if cache_problems:
        failed = True
        for c in cache_problems:
            print(f"   ✘ {c}")
    else:
        print("   OK — 없음")

    print(f"\n③ 내부 모듈 실제 import ({python})")
    if not installed:
        print("   건너뜀 — 이 인터프리터에 docstruct 가 설치되어 있지 않습니다.")
        print("   사용법: python tools/verify_package.py <설치된-venv>/bin/python")
        print("\n실패" if failed else "\n①② 통과 (③ 미수행)")
        return 1 if failed else 0

    bad = check_importable(python)
    if bad:
        failed = True
        for b in bad:
            print(f"   ✘ {b}")
    else:
        print(f"   OK — {len(collect_imports())}개 모듈 모두 정상")

    print("\n실패" if failed else "\n통과")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
