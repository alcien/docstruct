"""실험 목록과 상태를 보여 준다.

역할:
    무엇이 등록돼 있고, 어디까지 검증됐고, 지금 무엇이 켜져 있는지 낸다.
호출부:
    사용자 (`python -m docstruct.experiments.report`)
    docstruct.report (결과 요약에 켜진 실험 표시)

왜 필요한가
---------
실험이 늘면 **무엇을 왜 만들었는지 잊는다.** 설정만 20개가 넘은 적이 있고,
그중 일부는 이미 폐기 대상이었다. 한곳에서 훑을 수 있어야 정리가 된다.
"""
from __future__ import annotations

from docstruct.experiments.registry import all_experiments

_STATUS_MARK = {
    "proposed": "제안",
    "testing": "시험 중",
    "verified": "검증됨",
    "retired": "폐기",
}


def lines() -> list[str]:
    """실험 목록을 줄 단위로 낸다.

    입력: 없음
    출력: 출력용 문자열 목록
    """
    out = ["실험 기법 (기본은 모두 꺼져 있음)", ""]
    for exp in all_experiments():
        mark = "●" if exp.enabled else "○"
        out.append(f"{mark} {exp.key}  [{_STATUS_MARK.get(exp.status, exp.status)}]")
        out.append(f"    {exp.title}")
        out.append(f"    보완  : {exp.purpose}")
        out.append(f"    출처  : {exp.origin}")
        out.append(f"    형식  : {', '.join(exp.formats)}")
        out.append(f"    켜기  : {exp.env}=true")
        for name, why in exp.knobs.items():
            out.append(f"      └ {name}  {why}")
        out.append(f"    비고  : {exp.note}")
        out.append("")
    return out


def main() -> None:
    """CLI 진입점.

    입력: 없음
    출력: 없음 (stdout)
    """
    print("\n".join(lines()))


if __name__ == "__main__":
    main()
