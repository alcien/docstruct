"""실험 목록과 상태를 보여 준다.

입력:
    registry
출력:
    `--exp list` 출력 줄

역할:
    무엇이 등록돼 있고, 어디까지 검증됐고, 지금 무엇이 켜져 있는지 낸다.
호출부:
    사용자 (`python -m docstruct.experiments.report`)
    docstruct.output.report (결과 요약에 켜진 실험 표시)

왜 필요한가
---------
실험이 늘면 **무엇을 왜 만들었는지 잊는다.** 설정만 20개가 넘은 적이 있고,
그중 일부는 이미 폐기 대상이었다. 한곳에서 훑을 수 있어야 정리가 된다.
"""
from __future__ import annotations

from docstruct.experiments.registry import DEFAULT_ON, all_experiments

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
    out = ["실험 기법 (● 지금 켜짐 · ★ 승격되어 기본 켬 · ○ 꺼짐)", ""]
    for exp in all_experiments():
        promoted = exp.key in DEFAULT_ON
        mark = ("★" if promoted and exp.enabled else "●" if exp.enabled else "○")
        out.append(f"{mark} {exp.key}  [{_STATUS_MARK.get(exp.status, exp.status)}]")
        out.append(f"    {exp.title}")
        out.append(f"    보완  : {exp.purpose}")
        out.append(f"    출처  : {exp.origin}")
        out.append(f"    형식  : {', '.join(exp.formats)}")
        if promoted:
            out.append(f"    끄기  : {exp.env}=false  (또는 --exp no_{exp.key})")
        else:
            out.append(f"    켜기  : {exp.env}=true")
        for name, why in exp.knobs.items():
            # **자기 켜기 손잡이는 위 한 줄이 이미 말했다.** 승격된 실험의
            # `knobs` 에 "1 이면 켬 (기본 꺼짐)" 이 등록 당시 그대로 남아,
            # `--exp list` 가 같은 실험을 두고 "기본 켬" 과 "기본 꺼짐" 을
            # 나란히 찍었다 (0.4.83 정정). 진짜 손잡이(문턱·상한)만 낸다.
            if name == exp.env:
                continue
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
