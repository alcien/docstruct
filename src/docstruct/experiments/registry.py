"""실험 단계 모듈을 한곳에 모은다.

역할:
    검증이 끝나지 않은 보완 기법들을 등록해 두고, 설정으로 켜고 끈다.
호출부:
    docstruct.pipeline (실험 단계 실행)
    docstruct.experiments.report (무엇이 켜져 있는지 보고)

왜 따로 두는가
------------
표 구조 인식을 보완하는 기법을 여럿 시험하는 중인데, 각각이 파이프라인
본체에 섞이면 **나중에 무엇을 지워야 할지 알 수 없다.** 실제로 지금까지
만든 설정이 20개를 넘었고, 그중 일부는 이미 폐기 대상이다(빈 칸 비율 판정은
정상 표를 82% 오판해 껐다).

그래서 실험 기법은 **한 모듈에 하나씩** 두고 여기에 등록한다. 폐기할 때는
파일을 지우고 등록을 빼면 된다 — 본체는 건드리지 않는다.

등록 정보에 **무엇을 보완하려는지, 어디까지 검증됐는지**를 함께 적는다.
그러지 않으면 몇 달 뒤에 이 설정이 무엇이었는지 알 수 없다.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Experiment:
    """실험 기법 하나.

    입력(필드):
        key       설정 이름 (`DOCSTRUCT_EXP_{KEY}` 환경변수로 켠다)
        title     한 줄 설명
        purpose   무엇을 보완하려는가
        origin    어느 연구 계보에서 빌린 발상인가
        formats   적용되는 문서 형식
        status    proposed | testing | verified | retired
        note      검증 결과·한계
        run       실행 함수 (pages, **kwargs) -> int
    """

    key: str
    title: str
    purpose: str
    origin: str
    formats: tuple[str, ...]
    status: str
    note: str
    run: Callable | None = None
    #: 이 실험이 쓰는 추가 환경변수 (이름 → 설명).
    knobs: dict[str, str] = field(default_factory=dict)

    @property
    def env(self) -> str:
        """이 실험을 켜는 환경변수 이름."""
        return f"DOCSTRUCT_EXP_{self.key.upper()}"

    @property
    def enabled(self) -> bool:
        """켜져 있는지.

        입력: 없음 (환경변수)
        출력: 켜져 있으면 True
        비고: 실험은 **기본으로 꺼져 있다.** 검증이 끝나면 본체로 옮긴다.
        """
        raw = os.environ.get(self.env, "").strip().lower()
        return raw in ("1", "true", "on", "yes")


_REGISTRY: dict[str, Experiment] = {}


def register(experiment: Experiment) -> Experiment:
    """실험을 등록한다.

    입력: experiment — Experiment
    출력: 그대로 돌려준다 (모듈 최상단에서 바로 쓰기 위함)
    """
    if experiment.key in _REGISTRY:
        _log.warning("실험 키가 겹칩니다: %s", experiment.key)
    _REGISTRY[experiment.key] = experiment
    return experiment


#: 실행 순서. 뒤엣것이 앞엣것의 결과를 읽는다.
#: 여기 없는 실험은 이름순으로 맨 뒤에 붙는다.
_RUN_ORDER = ("two_way_match", "otsl_diff", "cell_repair")


def _run_order(key: str) -> tuple[int, str]:
    """실행 순서 열쇠.

    입력: key — 실험 키
    출력: (순번, 키). 목록에 없으면 맨 뒤
    """
    if key in _RUN_ORDER:
        return (_RUN_ORDER.index(key), key)
    return (len(_RUN_ORDER), key)


def all_experiments() -> list[Experiment]:
    """등록된 실험 전부 (키 순).

    입력: 없음
    출력: Experiment 목록
    """
    _load_all()
    # **먼저 돌아야 하는 것을 앞에 둔다.** 이름순으로만 두었더니
    # `cell_repair` 가 `two_way_match` 보다 먼저 돌아, 아직 채워지지 않은
    # `match_disagreements` 를 읽고 행 분리를 건너뛰었다 — 25표가 12표로
    # 줄었다.
    return [_REGISTRY[k] for k in sorted(_REGISTRY, key=_run_order)]


def enabled_experiments() -> list[Experiment]:
    """켜져 있는 실험만.

    입력: 없음
    출력: Experiment 목록
    """
    return [e for e in all_experiments() if e.enabled]


def _load_all() -> None:
    """실험 모듈을 모두 불러온다 (등록을 일으키기 위함).

    입력: 없음
    출력: 없음
    비고:
        모듈을 import 해야 `register()` 가 실행된다. 하나가 깨져도 나머지는
        살린다 — 실험 코드가 본체를 멈추게 하면 안 된다.
    """
    import importlib
    import pkgutil

    import docstruct.experiments as package

    for info in pkgutil.iter_modules(package.__path__):
        if info.name.startswith("_") or info.name in ("registry", "report"):
            continue
        try:
            importlib.import_module(f"docstruct.experiments.{info.name}")
        except Exception as exc:                 # noqa: BLE001 - 실험이 본체를 막지 않는다
            _log.warning("실험 모듈 %s 를 불러오지 못했습니다: %s", info.name, exc)
