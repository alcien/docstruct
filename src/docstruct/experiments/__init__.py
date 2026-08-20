"""검증이 끝나지 않은 보완 기법들.

각 기법은 **한 파일에 하나씩** 두고 `registry` 에 등록한다. 폐기할 때는
파일을 지우고 등록을 빼면 된다 — 파이프라인 본체는 건드리지 않는다.

무엇이 있고 어디까지 검증됐는지는 `docstruct.experiments.report` 로 본다.
"""
from docstruct.experiments.registry import (
    Experiment,
    all_experiments,
    enabled_experiments,
    register,
)

__all__ = ["Experiment", "all_experiments", "enabled_experiments", "register"]
