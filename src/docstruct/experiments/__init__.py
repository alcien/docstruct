"""experiments — 검증이 끝나지 않은 보완 기법들 (실험 사다리).

축: 인식 — 폴더가 곧 축이다 (0.4.85).
역할:
    각 기법은 **한 파일에 하나씩** 두고 `registry` 에 등록한다. 폐기할
    때는 파일을 지우고 등록을 빼면 된다 — 파이프라인 본체는 건드리지
    않는다. 파이프라인은 구간 5(표)·구간 8 뒤(그림)에서 등록 순서대로 돈다.

    무엇이 있고 어디까지 검증됐는지는 `docstruct --exp list`
    (`docstruct.experiments.report`) 로 본다. 기본 켬 집합은 `registry.DEFAULT_ON`.

폴더 (실험 종류):
    tsr/measure/   [① 표] 격자 근거를 만들고 **재기만** 한다 — markdown 을 바꾸지 않는다
    tsr/restore/   [① 표] 격자로 표를 **고친다** — original_markdown 을 남기고, 나빠지면 되돌린다
    image/         [③ 그림] 그림 판독 경로 계측
    text/          [② 텍스트] 스캔 판독·쪽 껍데기 계측

    "재는 것" 과 "바꾸는 것" 을 폴더로 갈랐다. 측정 전용 실험이 표를
    바꾸면 약속이 깨진다 — 시험이 폴더를 못 박는다
    (`test_experiments_live_in_typed_folders`).

기반 모듈 (입력 → 출력 · 역할):
    registry.py           (등록) → Experiment 목록 · enabled_experiments · DEFAULT_ON
                          하위 폴더를 재귀로 걷는다. 최상위에 남은 옛 사본과
                          폐기 키는 불러오지 않고 `stale_modules` 로 알린다.
    report.py             registry → `--exp list` 출력 줄

읽는 순서: registry → 폴더 __init__ → 관심 실험 → report. 승격·강등 이력은 BUGFIXES.md.
"""
from docstruct.experiments.registry import (
    Experiment,
    all_experiments,
    enabled_experiments,
    register,
    stale_modules,
)

__all__ = ["Experiment", "all_experiments", "enabled_experiments", "register",
           "stale_modules"]
