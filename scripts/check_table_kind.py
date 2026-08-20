#!/usr/bin/env python3
"""표 유형 판단이 제대로 되는지 확인한다.

쓰는 법
    python3 check_table_kind.py document.json
    python3 check_table_kind.py document.json --show-wrong

세 가지를 순서대로 본다.

    ① 판단이 돌았는가       table_kind 가 채워졌는가
    ② 판단이 맞는가         헤더로 아는 정답과 견준다
    ③ 유형별로 다른가       실험 검출률이 유형마다 다른가

②의 정답은 **헤더 문구로 정한다.** `회계 구분`·`'25결산` 이 있으면 예산표
이고, `성과지표`·`달성률` 이면 지표표다. 사람이 봐도 같은 판단이므로
정답으로 쓸 수 있다. 헤더로 알 수 없는 표는 채점에서 뺀다.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

#: 헤더 문구 → 정답 유형. 앞에 있는 규칙이 이긴다.
#: 실측(행안부 성과계획서 321표): 189개(59%)가 이 규칙으로 판별된다.
_ANSWER_RULES: list[tuple[tuple[str, ...], str]] = [
    (("회계 구분", "회계구분", "'25결산", "'26예산", "예산현액", "집행률"), "budget"),
    (("성과지표", "달성률", "목표대비", "달성여부", "측정산식"), "indicator"),
    (("단위사업", "세부사업", "사업코드", "프로그램명"), "program"),
    (("지적사항", "개선사항", "개선계획", "선정사유"), "review"),
]

#: 실험이 남기는 검출 필드.
_SIGNALS = ("match_disagreements", "consensus_drift", "split_merge_hints",
            "edge_drift")


def header_text(table: dict) -> str:
    """표의 첫 줄(헤더)을 한 줄로.

    입력: table — 표 dict
    출력: 셀을 공백으로 이은 문자열
    """
    rows = [
        line for line in (table.get("markdown") or "").splitlines()
        if line.startswith("|") and set(line.strip()) - set("|-: ")
    ]
    if not rows:
        return ""
    return " ".join(c.strip().strip("*") for c in rows[0].strip("|").split("|"))


def answer_kind(table: dict) -> str | None:
    """헤더로 아는 정답 유형.

    입력: table — 표 dict
    출력: 유형 문자열. 알 수 없으면 None
    비고:
        **모르면 None 을 낸다.** 억지로 정답을 만들면 채점이 무의미해진다.
    """
    head = header_text(table)
    if not head:
        return None
    for words, kind in _ANSWER_RULES:
        if any(w in head for w in words):
            return kind
    return None


def has_signal(table: dict) -> bool:
    """실험이 무언가 잡았는가."""
    return any(table.get(name) for name in _SIGNALS)


def check(path: Path, show_wrong: bool = False) -> int:
    """검증을 돌린다.

    입력: path — document.json, show_wrong — 틀린 사례를 보일지
    출력: 종료 코드 (0 성공)
    """
    with path.open(encoding="utf-8") as fp:
        document = json.load(fp)
    tables = [t for p in document.get("pages", []) for t in (p.get("tables") or [])]
    if not tables:
        print("표가 없습니다.", file=sys.stderr)
        return 1

    print(f"파일 {document.get('filename')} · 표 {len(tables)}개\n")

    # ── ① 판단이 돌았는가 ────────────────────────────────────────
    judged = [t for t in tables if t.get("table_kind")]
    print("① 판단 실행")
    print(f"   table_kind 채워짐  {len(judged)}/{len(tables)} "
          f"({len(judged) / len(tables):.0%})")
    if not judged:
        print("\n   → 판단이 돌지 않았습니다. LLM 평가를 켜고 다시 돌리세요.")
        print("      (`--no-llm` 이면 유형이 나오지 않습니다)")
        return 1
    print(f"   유형 분포          {dict(Counter(t['table_kind'] for t in judged))}\n")

    # ── ② 판단이 맞는가 ──────────────────────────────────────────
    scored = [(t, answer_kind(t)) for t in tables]
    gradable = [(t, a) for t, a in scored if a and t.get("table_kind")]
    print("② 정확도 (헤더로 아는 표만 채점)")
    if not gradable:
        print("   채점할 표가 없습니다.\n")
    else:
        hit = sum(1 for t, a in gradable if t["table_kind"] == a)
        print(f"   채점 대상          {len(gradable)}개")
        print(f"   맞음               {hit} ({hit / len(gradable):.0%})")

        confusion: Counter = Counter()
        for table, answer in gradable:
            if table["table_kind"] != answer:
                confusion[(answer, table["table_kind"])] += 1
        if confusion:
            print("   틀린 유형 (정답 → 판단):")
            for (answer, guess), count in confusion.most_common(6):
                print(f"      {answer:10s} → {guess:10s} {count}건")
        if show_wrong:
            print("\n   틀린 사례:")
            shown = 0
            for table, answer in gradable:
                if table["table_kind"] == answer or shown >= 5:
                    continue
                print(f"      {table['id']} · 정답 {answer} · 판단 {table['table_kind']}")
                print(f"         {header_text(table)[:70]}")
                shown += 1
        print()

    # ── ③ 유형별로 다른가 ────────────────────────────────────────
    print("③ 유형별 실험 검출률")
    by_kind: dict[str, list] = defaultdict(list)
    for table in judged:
        by_kind[table["table_kind"]].append(table)
    if not any(has_signal(t) for t in judged):
        print("   실험 검출이 없습니다 — `--exp` 로 켜고 다시 돌리세요.\n")
        return 0
    print(f"   {'유형':12s}{'표':>5}{'검출':>6}{'비율':>7}")
    for kind, group in sorted(by_kind.items(), key=lambda x: -len(x[1])):
        hits = sum(1 for t in group if has_signal(t))
        print(f"   {kind:12s}{len(group):>5}{hits:>6}{hits / len(group):>7.0%}")
    print()
    print("   → 유형마다 비율이 크게 다르면 유형별 적용이 의미 있습니다.")
    print("      비슷하면 전부 켜는 편이 낫습니다.")
    return 0


def main() -> int:
    """CLI 진입점."""
    parser = argparse.ArgumentParser(
        description="표 유형 판단이 제대로 되는지 확인합니다.")
    parser.add_argument("json_path", type=Path, help="document.json 경로")
    parser.add_argument("--show-wrong", action="store_true",
                        help="틀린 사례를 함께 보입니다")
    args = parser.parse_args()

    if not args.json_path.is_file():
        print(f"파일이 없습니다: {args.json_path}", file=sys.stderr)
        return 1
    return check(args.json_path, args.show_wrong)


if __name__ == "__main__":
    raise SystemExit(main())
