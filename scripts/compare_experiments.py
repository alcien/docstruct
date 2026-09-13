"""실험 기법을 하나씩 켜서 돌리고 결과를 한 표로 견준다 — .sh 의 파이썬 판.

역할:
    기준(모두 끔) 한 번 + 실험 하나씩 켠 횟수만큼 돌리고, 표·본문·표시
    수를 나란히 낸다. 어느 기법이 무엇을 바꿨는지 가르는 도구다.
호출부:
    수동 실행. 윈도우에서는 .sh 를 못 돌리므로 이 파일을 쓴다 (0.4.56).

        python scripts/compare_experiments.py 문서.pdf
        python scripts/compare_experiments.py 문서.pdf 출력폴더

.sh 와 다른 점 — 기준을 진짜 기준으로 만든다
------------------------------------------
.sh 는 "실험은 모두 기본 꺼짐" 시절(0.4.3 이전)에 쓰였다. 지금은 일곱이
기본 켬이라, 아무 `--exp` 없이 돌린 "기준" 에 이미 일곱이 들어 있고,
승격된 키를 `--exp 키` 로 켠 판은 기준과 같아져 **비교가 조용히 무의미**
해진다. 그래서 이 판은:

    기준           기본 켬 전부를 `no_<키>` 로 끈다
    실험 판        같은 끄기 + 시험할 키 하나만 켠다

기본 켬 목록은 코드(registry.DEFAULT_ON)에서 가져온다 — 손으로 적으면
승격·강등 때 어긋난다.
"""
from __future__ import annotations

# ── 설정 ──────────────────────────────────────────────────────────
#: 공통 인자. LLM 을 끄고 순수 파서 결과만 견준다 — LLM 이 끼면 어느
#: 쪽이 바꾼 것인지 구분되지 않는다.
COMMON_ARGS = ["--no-llm", "--progress"]
#: 결과 폴더 기본값 (둘째 인자로 바꾼다).
DEFAULT_OUT = "exp_compare"
# ─────────────────────────────────────────────────────────────────

import json
import subprocess
import sys
from pathlib import Path


def _experiment_keys() -> tuple[list[str], list[str]]:
    """등록 실험 키와 기본 켬 키를 코드에서 가져온다.

    입력: 없음
    출력: (전체 키 목록 — 실행 순서, 기본 켬 키 목록)
    비고:
        설치본이든 로컬 트리든 임포트 경로가 다르므로 둘 다 시도한다.
        로컬 트리에서는 **트리 루트에서** 실행해야 한다.
    """
    try:
        from docstruct.experiments import all_experiments
        from docstruct.experiments.registry import DEFAULT_ON
    except ImportError:
        try:
            from experiments import all_experiments      # 로컬 트리
            from experiments.registry import DEFAULT_ON
        except ImportError:
            print("실험 목록을 읽지 못했습니다. docstruct 설치를 확인하거나 "
                  "로컬 트리 루트에서 실행하세요.", file=sys.stderr)
            raise SystemExit(1)
    return [e.key for e in all_experiments()], sorted(DEFAULT_ON)


def _runner() -> list[str]:
    """docstruct 를 부를 명령. 콘솔 스크립트가 없으면 `python -m docstruct`."""
    import shutil

    exe = shutil.which("docstruct")
    return [exe] if exe else [sys.executable, "-m", "docstruct"]


def _run_one(pdf: Path, out: Path, name: str, exp_spec: str,
             runner: list[str]) -> bool:
    """한 판 돌린다. 이미 결과 폴더가 있으면 건너뛴다."""
    folder = out / name
    if folder.is_dir():
        print(f"  건너뜀 (이미 있음): {name}")
        return True
    print(f"── {name}")
    args = runner + [str(pdf), "-o", str(folder)] + COMMON_ARGS
    if exp_spec:
        args += ["--exp", exp_spec]
    log = out / f"{name}.log"
    with log.open("w", encoding="utf-8") as fp:
        proc = subprocess.run(args, stdout=fp, stderr=subprocess.STDOUT)
    if proc.returncode != 0:
        print(f"     실패 — {log} 를 보세요")
        return False
    return True


def _load(folder: Path) -> dict | None:
    """그 폴더의 document.json 을 읽는다."""
    hits = list(folder.rglob("document.json"))
    if not hits:
        return None
    with hits[0].open(encoding="utf-8") as fp:
        return json.load(fp)


def _measure(doc: dict) -> dict:
    """비교할 지표를 뽑는다.

    실험은 대개 **표시만** 하므로 본문·표 수는 그대로다. 달라지는 것은
    trace 에 남는 표시 수와 실험 필드다.
    """
    pages = doc.get("pages", [])
    tables = [t for p in pages for t in (p.get("tables") or [])]
    marks: dict[str, int] = {}
    for page in pages:
        for step in (page.get("trace") or {}).get("steps", []):
            module = step.get("module", "")
            if "experiments." in module:
                key = module.split("experiments.", 1)[1]
                marks[key] = marks.get(key, 0) + 1
    # **실험이 남기는 표 필드 전부** (0.4.83). 손으로 적은 목록이 0.4.71
    # 이후 실험(lattice_fill·hole_fill·over_split·chart_gate·page_chrome)과
    # 상시 검사(grid_faults·cell_leaks)를 빠뜨려, 그 실험들은 이 도구로
    # 견주면 "남긴 값" 칸이 비어 나왔다 — 돌았는지조차 가릴 수 없었다.
    field_names = (
        "split_merge_hints", "match_disagreements", "cell_repairs",
        "edge_drift", "consensus_drift", "otsl", "grid_merge_gap",
        "synth_grid", "scan_grid", "grid_score", "grid_restore",
        "lattice_restore", "lattice_fill", "fill_gate", "hole_fill",
        "col_grid", "col_gate", "head_grid", "agreed_grid", "sum_check",
        "over_split", "grid_faults", "cell_leaks",
    )
    fields = {name: sum(1 for t in tables if t.get(name))
              for name in field_names}
    page_fields = {name: sum(1 for p in pages if p.get(name))
                   for name in ("scan_ab", "scan_scale_ab", "page_chrome")}
    images = [i for p in pages for i in (p.get("images") or [])]
    fields["chart_gate"] = sum(1 for i in images if i.get("chart_gate"))
    fields.update(page_fields)
    return {
        "tables": len(tables),
        "words": sum(len((p.get("content") or "").split()) for p in pages),
        "marks": sum(marks.values()),
        "fields": {k: v for k, v in fields.items() if v},
    }


def main(argv: list[str]) -> int:
    """진입점. 사용법은 모듈 머리를 보라."""
    if not argv or not Path(argv[0]).is_file():
        print(f"사용법: python {Path(__file__).name} <문서.pdf> [출력폴더]",
              file=sys.stderr)
        print(f"예시  : python {Path(__file__).name} 행안부_인쇄329-355.pdf",
              file=sys.stderr)
        return 1
    pdf = Path(argv[0])
    out = Path(argv[1] if len(argv) > 1 else DEFAULT_OUT)
    out.mkdir(parents=True, exist_ok=True)

    keys, default_on = _experiment_keys()
    offs = ",".join(f"no_{k}" for k in default_on)
    runner = _runner()

    print(f"문서   : {pdf}")
    print(f"출력   : {out}{Path('/')}")
    print(f"실험   : {len(keys)}개 + 기준 (기본 켬 {len(default_on)}개는 "
          "매 판 끄고 시작)")
    print()

    _run_one(pdf, out, "baseline", offs, runner)
    for key in keys:
        spec = ",".join(x for x in (offs, key) if x)
        # 시험할 키가 기본 켬이면 끄기 목록에서 빼고 켠다.
        if key in default_on:
            spec = ",".join([f"no_{k}" for k in default_on if k != key] + [key])
        _run_one(pdf, out, key, spec, runner)

    print()
    print("═══ 비교")
    rows = []
    for folder in sorted(d for d in out.iterdir() if d.is_dir()):
        doc = _load(folder)
        rows.append((folder.name, _measure(doc) if doc else None))

    base = dict(rows).get("baseline")
    print(f"{'실험':<18}{'표':>5}{'본문':>8}{'표시':>6}  남긴 값")
    print("─" * 64)
    warned = False
    for name, stat in rows:
        if stat is None:
            print(f"{name:<18}{'(결과 없음)':>20}")
            continue
        fields = ", ".join(f"{k} {v}" for k, v in stat["fields"].items()) or "-"
        print(f"{name:<18}{stat['tables']:>5}{stat['words']:>8}"
              f"{stat['marks']:>6}  {fields}")
        if (base and name != "baseline"
                and (stat["tables"] != base["tables"]
                     or stat["words"] != base["words"])):
            # 표를 바꾸는 실험(⑦⑫⑬⑭⑮·lattice_fill·hole_fill·cell_repair)은
            # 원래 바꾼다 — 그 외가 바꿨다면 의도치 않은 변경이다. 판정은
            # 사람이 한다.
            print(f"{'':<18}  ⚠ 기준과 표/본문이 다릅니다 — 표시 전용 "
                  "실험이라면 의도치 않은 변경입니다")
            warned = True
    if warned:
        print()
        print("⚠ 가 붙은 판을 확인하세요. 다시 돌리려면 해당 폴더를 지웁니다:")
        print(f"    rmdir /s /q {out}{Path('/')}<이름>   (윈도우)")
        print(f"    rm -rf {out}/<이름>                  (리눅스)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
