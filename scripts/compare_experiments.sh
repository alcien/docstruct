#!/usr/bin/env bash
# 실험 기법을 하나씩 켜서 돌리고 결과를 한 표로 견준다.
#
# 쓰는 법
#   ./compare_experiments.sh 행안부_인쇄329-355.pdf
#   ./compare_experiments.sh 문서.pdf out_dir
#
# 하는 일
#   기준(모두 끔) 한 번 + 실험 하나씩 켠 횟수만큼 돌린다. 결과는 실험별
#   폴더에 따로 두고, 마지막에 표·본문 지표를 나란히 낸다.
#
# 왜 하나씩 켜는가
#   여럿을 함께 켜면 어느 기법이 무엇을 바꿨는지 알 수 없다. 기준과 견줘
#   차이가 나는 것만 남긴다.

set -uo pipefail

PDF="${1:-}"
OUT="${2:-exp_compare}"

if [[ -z "$PDF" || ! -f "$PDF" ]]; then
  echo "사용법: $0 <문서.pdf> [출력폴더]" >&2
  echo "예시  : $0 행안부_인쇄329-355.pdf" >&2
  exit 1
fi

# 실험 키 목록을 코드에서 가져온다. 손으로 적으면 실험이 늘거나 폐기될 때
# 어긋난다.
mapfile -t EXPERIMENTS < <(
  python3 -c "
from docstruct.experiments import all_experiments
for e in all_experiments():
    print(e.key)
" 2>/dev/null
)
if [[ ${#EXPERIMENTS[@]} -eq 0 ]]; then
  echo "실험 목록을 읽지 못했습니다. docstruct 설치를 확인하세요." >&2
  exit 1
fi

# 공통 설정. LLM 을 끄고 순수 파서 결과만 견준다 — LLM 이 끼면 어느 쪽이
# 바꾼 것인지 구분되지 않는다.
COMMON_ARGS=(--no-llm --progress)

mkdir -p "$OUT"

# 돌리기 전에 준비 상태를 본다. 여섯 번 다 실패한 뒤에 원인을 찾는 것보다
# 먼저 알려 주는 편이 낫다.
if ! command -v docstruct >/dev/null 2>&1; then
  echo "docstruct 명령을 찾을 수 없습니다." >&2
  echo "  설치: pip install \"docstruct @ git+...\"" >&2
  exit 1
fi
if ! python3 -c "import docling" >/dev/null 2>&1; then
  echo "docling 이 설치돼 있지 않아 PDF 를 열 수 없습니다." >&2
  echo "  docstruct --check 로 자세한 안내를 보세요." >&2
  exit 1
fi

echo "문서   : $PDF"
echo "출력   : $OUT/"
echo "실험   : ${#EXPERIMENTS[@]}개 + 기준"
echo

run_one() {
  # 한 번 돌린다.
  #   $1 이름(폴더명), $2 켤 실험 키(비면 기준)
  local name="$1" key="${2:-}"
  local dir="$OUT/$name"

  if [[ -d "$dir" ]]; then
    echo "  건너뜀 (이미 있음): $name"
    return 0
  fi

  echo "── $name"
  # `--exp` 로 켠다. 환경변수를 직접 다루지 않으므로 이전 실행이 남을
  # 걱정이 없다.
  local args=("${COMMON_ARGS[@]}")
  [[ -n "$key" ]] && args+=(--exp "$key")

  if ! docstruct "$PDF" -o "$dir" "${args[@]}" >"$OUT/$name.log" 2>&1; then
    echo "     실패 — $OUT/$name.log 를 보세요"
    return 1
  fi
  return 0
}

run_one baseline ""
for key in "${EXPERIMENTS[@]}"; do
  run_one "$key" "$key"
done

echo
echo "═══ 비교"
python3 - "$OUT" <<'PYEOF'
"""실험별 결과를 한 표로 낸다."""
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])


def load(folder: Path) -> dict | None:
    """그 폴더의 document.json 을 읽는다."""
    hits = list(folder.rglob("document.json"))
    if not hits:
        return None
    with hits[0].open(encoding="utf-8") as fp:
        return json.load(fp)


def measure(doc: dict) -> dict:
    """비교할 지표를 뽑는다.

    실험은 대개 **표시만** 하므로 본문·표 수는 그대로다. 달라지는 것은
    trace 에 남는 표시 수다.
    """
    pages = doc.get("pages", [])
    tables = [t for p in pages for t in (p.get("tables") or [])]
    marks: dict[str, int] = {}
    for page in pages:
        for step in (page.get("trace") or {}).get("steps", []):
            module = step.get("module", "")
            if module.startswith("experiments."):
                marks[module.split(".", 1)[1]] = marks.get(module.split(".", 1)[1], 0) + 1
    # 실험이 남긴 필드
    fields = {
        "split_merge_hints": sum(1 for t in tables if t.get("split_merge_hints")),
        "match_disagreements": sum(1 for t in tables if t.get("match_disagreements")),
        "edge_drift": sum(1 for t in tables if t.get("edge_drift")),
        "consensus_drift": sum(1 for t in tables if t.get("consensus_drift")),
        "otsl": sum(1 for t in tables if t.get("otsl")),
    }
    return {
        "표": len(tables),
        "본문": sum(len((p.get("content") or "").split()) for p in pages),
        "표시": sum(marks.values()),
        "필드": {k: v for k, v in fields.items() if v},
    }


folders = [d for d in sorted(root.iterdir()) if d.is_dir()]
rows = []
for folder in folders:
    doc = load(folder)
    if doc is None:
        rows.append((folder.name, None))
        continue
    rows.append((folder.name, measure(doc)))

base = dict(rows).get("baseline")
print(f"{'실험':18s}{'표':>5}{'본문':>8}{'표시':>6}  남긴 값")
print("─" * 64)
for name, stat in rows:
    if stat is None:
        print(f"{name:18s}{'(결과 없음)':>20}")
        continue
    fields = ", ".join(f"{k} {v}" for k, v in stat["필드"].items()) or "-"
    print(f"{name:18s}{stat['표']:>5}{stat['본문']:>8}{stat['표시']:>6}  {fields}")

if base:
    print()
    print("기준 대비 표·본문이 달라진 실험은 **결과를 바꾼 것**입니다.")
    for name, stat in rows:
        if not stat or name == "baseline":
            continue
        if stat["표"] != base["표"] or stat["본문"] != base["본문"]:
            print(f"  ⚠ {name}: 표 {base['표']}→{stat['표']} · "
                  f"본문 {base['본문']}→{stat['본문']}")
PYEOF

echo
echo "각 실행 로그: $OUT/<이름>.log"
echo "다시 돌리려면 폴더를 지우세요: rm -rf $OUT/<이름>"
