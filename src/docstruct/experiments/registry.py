"""실험 단계 모듈을 한곳에 모은다.

입력:
    (등록)
출력:
    Experiment 목록 · enabled_experiments · DEFAULT_ON

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
        stage     언제 도는가 — "tables"(기본) | "images"
    """

    key: str
    title: str
    purpose: str
    origin: str
    formats: tuple[str, ...]
    status: str
    note: str
    run: Callable | None = None
    #: **언제 도는가** (0.4.63). 실험은 표 사다리가 끝난 자리에서 한 번
    #: 도는데, 그 자리는 **그림 판독보다 앞이다.** 그림이 남기는 것
    #: (`legibility`·`transcribed`)을 보는 실험은 거기서 돌면 빈손이 된다.
    #:
    #: 실측(조달청): `--exp chart_gate` 를 켰는데 기록이 0건이었다.
    #: `legibility` 가 아직 없었기 때문이다 — 실험은 돌았고, 볼 것이
    #: 없었을 뿐이라 로그에도 아무 말이 없었다.
    #:
    #: "images" 로 적으면 그림 판독이 끝난 뒤에 돈다. 자리를 옮기는 대신
    #: **자리를 밝혀 적는다** — 사다리 순서를 건드리면 다른 실험이 조용히
    #: 달라진다(0.4.2 의 교훈).
    stage: str = "tables"
    #: 이 실험이 쓰는 추가 환경변수 (이름 → 설명).
    knobs: dict[str, str] = field(default_factory=dict)
    #: **무엇이 있어야 도는가** (0.4.90). `core.guide.FORMAT_CAPABILITIES`
    #: 의 재료 이름 — "cells" · "geometry" · "vector" · "page" · "image".
    #:
    #: 왜 두는가 — `formats` 는 결과만 적는다("pdf 에서만 돈다"). 그래서
    #: 로그가 *"grid_restore 는 hwp 형식에 적용되지 않습니다"* 라고만 말했다.
    #: 진짜 이유는 형식이 아니라 **재료**다: HWP 는 셀이 없고, 스캔 PDF 는
    #: 벡터가 없다. 재료로 적으면 이유가 그대로 설명이 되고, 새 형식이
    #: 생겨도 무엇이 도는지 사람이 다시 세지 않아도 된다.
    #:
    #: 비워 두면 검사하지 않는다 — 기존 실험을 한꺼번에 고치지 않기 위함이다.
    needs: tuple[str, ...] = ()

    def missing_for(self, fmt: str) -> tuple[str, ...]:
        """이 형식에서 모자란 재료.

        입력: fmt — 'pdf' | 'hwpx' | 'hwp'
        출력: 없는 재료 이름들 (다 있으면 빈 튜플)
        비고:
            `needs` 를 적지 않은 실험은 빈 튜플을 돌려준다 — 판단하지
            않는다는 뜻이다. 실행 여부는 여전히 `formats` 가 정한다.
        """
        if not self.needs:
            return ()
        from docstruct.core.guide import FORMAT_CAPABILITIES

        have = FORMAT_CAPABILITIES.get(fmt, {})
        return tuple(need for need in self.needs
                     if need not in have or have[need].startswith(("없다", "**없다"))) 

    @property
    def env(self) -> str:
        """이 실험을 켜는 환경변수 이름."""
        return f"DOCSTRUCT_EXP_{self.key.upper()}"

    @property
    def enabled(self) -> bool:
        """켜져 있는지.

        입력: 없음 (환경변수)
        출력: 켜져 있으면 True
        비고:
            **승격된 실험(DEFAULT_ON)은 기본으로 켜져 있다** — `--exp` 없이
            돌려도 적용된다(0.4.3). 끄려면 환경변수를 거짓으로 두거나
            `--exp no_<키>` 를 쓴다. 승격되지 않은 실험은 여전히 기본 꺼짐.
        """
        raw = os.environ.get(self.env, "").strip().lower()
        if raw in ("1", "true", "on", "yes"):
            return True
        if raw in ("0", "false", "off", "no"):
            return False
        return self.key in DEFAULT_ON


#: 기본으로 켜지는 실험 — 다부처 정답 대조로 검증이 끝난 것만 (0.4.3 승격).
#:
#:     ⑦ grid_restore  3부처 병합 623개 **오류 0** (행안부 233·문체부 364·
#:                     조달청 26). 덮개 ≥ 1.0 게이트가 문서군을 타지 않는다
#:     ⑫ head_grid     행안부 708/772(91.7%)·정밀도 98.3% · 조달청 100.0% ·
#:                     회귀 0. 틀려도 머리 계층만 바뀌어 손해 상한이 구조로
#:                     제한된다
#:     ⑪ grid_score    아무것도 바꾸지 않는 계측. 힌트 근거(74.6% → 89.1%)와
#:                     ⑭ 좌표계 검사의 원천이라 이것이 꺼지면 힌트가 예비
#:                     근거로 격이 내려간다
#:     ⑧ sum_check     아무것도 바꾸지 않는 검증. 오탐 세 종류를 정답 대조로
#:                     제거해 3부처 오탐 0
#:     ⑥⑨ vector/line  표시만 한다(내용 불변). VLM 대상 선정과 힌트 예비
#:                     근거가 여기 의존한다 — 빼면 VLM 순이득(+4/+9)이 사라진다
#:
#: **승격의 단위는 실험이 아니라 조합이다.** 측정된 성적(조달청 72.6/98.5 ·
#: 행안부 85.1/96.4)은 이 여섯이 함께 돈 결과이고, 코드의 의존 관계가 그것을
#: 강제한다. 표를 바꾸는 것은 ⑦⑫뿐이며 둘 다 오탐 0 / 손해 상한이 실측됐다.
#:
#: 아직 승격하지 않은 것: ⑬ col_grid·⑭ agreed_grid (0.4.3 의 경계 접기로
#: 판정 근거가 바뀌었다 — 재측정이 먼저다), ⑩ scan_grid (정답 없음),
#: ①②③ (검증됐으나 조합 기여가 작다 — 2차 후보).
#: 0.4.7 추가 승격 — **조달청 기준**으로 유리한 것 (노트북이 `--exp` 없이
#: 도는 상황을 위해). 근거와 되돌리는 법은 BUGFIXES 0.4.7 을 보라.
#:
#:     ⑮ lattice_restore 행안부 46표 발화 · 정답 대조 **9/9 오탐 0** ·
#:                       지면 격자 대조 46/46 일치 (0.4.19 실측).
#:                       열 밀림의 값 귀속까지 고치는 유일한 경로다
#:
#: **⑬ col_grid 는 0.4.20 에서 내렸다.** 행안부에서 **0표 발화** —
#: ⑮이 `_RUN_ORDER` 에서 앞서고 같은 표를 `source="grid"` 로 가져가므로
#: 뒤에 남는 자리가 구조적으로 없다. 열 수만 맞추는 ⑬은 값 귀속까지
#: 고치는 ⑮의 하위 호환이 됐다. 켜려면 `--exp col_grid`.
#:
#: **0.4.80 승격 복원 — lattice_fill.** 오염을 판독 후처리(`repair_leaks`)
#: 가 지운다. 새어 든 글자는 앞 칸에 있는 중복이므로 지우면 원래 값으로
#: 돌아간다 — 원본 대조로 확인했다(오염 표 셀의 원본 일치 79~81% →
#: **96~98%**). 남은 오염은 한 번뿐인 우연이라 손대지 않는다.
#:
#: 아래는 강등 당시의 기록이다 — 왜 지표 둘이 눈이 멀었는지가 값이 있다.
#:
#: **0.4.79 강등 — lattice_fill.** 승격(0.4.73) 근거가 무너졌었다. 격자로
#: 다시 세운 표에서 **셀 텍스트가 오염된다** — 앞 칸의 끝 글자가 다음 칸
#: 앞에 딸려 온다(`63,618` → `8 66,578`). 실측:
#:
#:     해경  parser 60표 오염 0%   ·  lattice_fill 36표 오염 **33%**
#:     문체  parser 149표 오염 1%  ·  lattice_fill 215표 오염 **21%**
#:                                   오염 칸 722개
#:
#: 승격 때 쓴 두 지표가 **둘 다 이 오류에 눈이 멀었다** — 격자 온전성은
#: 자리가 다 덮여 `ok` 이고, 토큰 닮음은 `8 66,578` 에서 `66,578` 이 그대로
#: 나와 떨어지지 않는다. 격자가 예뻐진 대가로 값이 오염된 것이다.
#:
#: 되살리려면 `_cell_texts` 의 경계 처리를 고치고 `leak_check` 로 0을
#: 확인해야 한다. `--exp lattice_fill` 로 켤 수는 있다.
#:
#: 0.4.73 추가 승격 — **격자 온전성이라는 새 잣대**로 검증한 첫 실험.
#:
#:     lattice_fill  ⑮과 같은 기계를 열 수 차이가 아니라 격자 결함으로
#:                   발화시킨다. 2부처 실측: 격자 결함 문체부 50%→7% ·
#:                   행안부 53%→6% · 복원 365표 **전부 결함 0** · 폐기 0건.
#:                   행안부 HWPX 원본 대조에서 **내용도 개선**된다 —
#:                   숫자 93개 회복, 지어낸 숫자 0 증가, 닮음이 나빠진 표
#:                   2/133. 남은 결함은 100% 사유가 기록되고(`fill_gate`)
#:                   **괘선이 없어 못 고친 표는 0건**이다.
#:
#: 0.4.79 추가 승격 — hole_fill.
#:
#:     hole_fill  덮이지 않은 자리에 **빈 셀**을 넣어 격자를 닫는다.
#:                글을 한 자도 만들지 않으므로 지면이 필요 없고, 괘선이
#:                없어 lattice_fill 이 물러난 표에도 쓸 수 있다.
#:                실측: 격자 결함 해경 14%→**4%** · 문체부 7%→**1%**,
#:                메운 표 41개 전부 markdown 정상(빈 것 0). 남은 7표는
#:                겹침 5 · lattice_fewer 2 로 설계상 대상이 아니다.
#:                최악의 경우도 "병합 관계를 잘못 표현" 수준이고, 그것은
#:                지금의 찢어진 격자보다 낫다.
#:
#: 이 승격의 근거는 다른 것들과 성질이 다르다. 앞의 것들은 정답 표
#: 12개에 기대야 했지만, 격자 온전성은 **정답 없이 확인되는 신호**다
#: (HWPX 653표 결함 0% 가 음성 대조군 · 0.4.69).
#:
#: 되돌리려면 이 집합에서 키를 빼거나 실행 단위로 `--exp no_<키>`.
DEFAULT_ON = frozenset({
    "grid_score", "grid_restore", "head_grid",
    "vector_grid", "line_grid", "sum_check",
    "lattice_restore", "lattice_fill", "hole_fill",
})


#: 폐기한 실험 키 (0.3.48). 파일은 지웠지만 **덮어쓰기 배포에서는 남는다** —
#: `cp -r overlay/app/* .` 는 사라진 파일을 지우지 않는다. 그러면 옛 모듈이
#: 그대로 import 돼 폐기한 기법이 되살아나고, `--exp` 목록에도 나온다.
#: 실제로 사내 배치에서 `grid_refine`·`split_merge`·`grid_consensus` 가
#: 목록에 남아 있는 것을 확인했다 (0.3.62).
RETIRED_KEYS = frozenset({"grid_refine", "split_merge", "grid_consensus"})

_REGISTRY: dict[str, Experiment] = {}

#: 실험 모듈을 이미 불러왔는가 (0.4.86 — 요청마다 다시 걷지 않는다).
_LOADED = False


def register(experiment: Experiment) -> Experiment:
    """실험을 등록한다.

    입력: experiment — Experiment
    출력: 그대로 돌려준다 (모듈 최상단에서 바로 쓰기 위함)
    비고:
        폐기한 키는 **등록하지 않는다.** 옛 파일이 남아 있어도 되살아나지
        않게 하려는 것이다. 어느 파일이 남았는지 함께 알린다.
    """
    if experiment.key in RETIRED_KEYS:
        _log.warning(
            "폐기된 실험 모듈이 남아 있습니다: %s — 배포본에서 파일을 지우세요 "
            "(덮어쓰기 배포는 사라진 파일을 지우지 않습니다). "
            "이 실험은 등록하지 않습니다.",
            experiment.key,
        )
        return experiment
    if experiment.key in _REGISTRY:
        _log.warning("실험 키가 겹칩니다: %s", experiment.key)
    _REGISTRY[experiment.key] = experiment
    return experiment


#: 실행 순서 = 복원 사다리 (가설 문서 §17). 유형(T1~T5)을 따로 판정하는
#: 오케스트레이터는 없다 — 각 실험이 "내 물리 근거가 서는가" 를 스스로
#: 확인하고 아니면 조용히 물러난다. **물러남의 연쇄가 곧 라우팅이다.**
#:
#:     ⑪ grid_score    계측 — 반드시 ⑦보다 먼저 (원본 TF 대비를 재야 한다.
#:                     ⑦이 표를 바꾼 뒤에 재면 tf~rect 가 1.0 으로 오염된다)
#:     ⑦ grid_restore  복원 — 표를 통째로 바꾼다
#:     ⑮ lattice_restore 복원 — 괘선 격자로 표를 다시 세운다 (열 밀림)
#:     ⑬ col_grid      복원 — 열 수를 바로잡는다 (⑫보다 먼저)
#:     ⑫ head_grid     복원 — 머리 계층만 바꾼다 (⑦이 물러난 표)
#:     ⑭ agreed_grid   복원 — 두 기하 근거가 합의한 병합만 (본문 포함)
#:     ⑥⑨⑩            표시 — ⑦ 뒤에 두면 복원된 표는 셀==격자라 자연히
#:                     표시 0 이 된다 (특례 코드 없이 자기일관)
#:     ①②③            텍스트 후처리 — 뒤엣것이 앞엣것의 결과를 읽는다
#:                     (③이 ①의 match_disagreements 를 읽는다)
#:     ⑧ sum_check     검증 — 맨 끝. 어느 경로로 왔든 결과를 잰다
#:
#: 여기 없는 실험은 이름순으로 맨 뒤에 붙는다.
#:     scan_ab·scan_scale_ab — 쪽 단위 이중 판독 계측 (0.4.56). 표를 보지
#:     않으므로 사다리와 무관하나, 본문이 확정된 뒤여야 하므로 맨 뒤.
#:     lattice_fill 은 ⑮ 바로 뒤다 — ⑮이 이긴 자리를 지키고, 남은 것만
#:     격자 결함으로 다시 본다 (0.4.71).
_RUN_ORDER = ("grid_score", "grid_restore", "lattice_restore", "lattice_fill", "hole_fill",
              "col_grid", "head_grid", "agreed_grid",
              "vector_grid", "line_grid", "scan_grid",
              "two_way_match", "otsl_diff", "cell_repair",
              "sum_check", "over_split",
              "scan_ab", "scan_scale_ab", "page_chrome", "chart_gate")


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


def enabled_experiments(stage: str | None = None) -> list[Experiment]:
    """켜져 있는 실험만.

    입력: stage — 이 단계의 것만 (None 이면 전부)
    출력: Experiment 목록 (실행 순서)
    비고:
        `stage` 를 주면 그 자리에서 돌 것만 골라 준다. 파이프라인이 두
        군데에서 부르되 **각자 자기 것만** 돌리기 위함이다.
    """
    got = [e for e in all_experiments() if e.enabled]
    return got if stage is None else [e for e in got if e.stage == stage]


def _iter_experiment_modules():
    """실험 모듈을 (이름, 전체 import 경로, 최상위 여부) 로 훑는다.

    입력: 없음
    출력: (모듈 이름, "docstruct.experiments.…", 최상위인가) 튜플 반복자
    비고:
        0.4.85 부터 실험은 **하위 폴더**(tsr/measure · tsr/restore · image ·
        text)에 산다. 폴더를 재귀로 걷되 `registry`·`report`·`_` 로 시작하는
        것과 폴더 자체(`__init__`)는 건너뛴다. 최상위에 `.py` 로 남은
        실험은 옮기기 전의 **옛 사본**이다 — 덮어쓰기 배포가 지우지 않은
        것이므로 불러오지 않는다(같은 키가 두 번 등록되는 것을 막는다).
    """
    import pkgutil

    import docstruct.experiments as package

    for info in pkgutil.walk_packages(package.__path__, prefix="docstruct.experiments."):
        short = info.name.rsplit(".", 1)[-1]
        if info.ispkg or short.startswith("_") or short in ("registry", "report"):
            continue
        # 최상위 = 바로 이 패키지 아래 (local/overlay 에서는 접두사가
        # `experiments.` 로 짧아지므로 점 개수가 아니라 부모 이름으로 본다)
        top_level = info.name.rsplit(".", 1)[0] == package.__name__
        yield short, info.name, top_level


def _load_all() -> None:
    """실험 모듈을 모두 불러온다 (등록을 일으키기 위함).

    입력: 없음
    출력: 없음
    비고:
        모듈을 import 해야 `register()` 가 실행된다. 하나가 깨져도 나머지는
        살린다 — 실험 코드가 본체를 멈추게 하면 안 된다.
        폐기된 키(RETIRED_KEYS)와 최상위에 남은 옛 사본은 불러오지 않고
        어느 파일을 지우라고 알린다.
    """
    import importlib

    import docstruct.experiments as package

    # **한 번만 돈다** (0.4.86). `all_experiments()` 가 이것을 부르고,
    # 파이프라인은 문서마다 그것을 부른다 — 서버에서는 요청마다 패키지를
    # 다시 걷고 같은 경고를 다시 찍었다. 실측 로그에서 "폐기된 실험 파일이
    # 남아 있습니다" 가 문서 한 건에 두 번씩 수백 줄로 쌓였다.
    global _LOADED
    if _LOADED:
        return
    _LOADED = True

    modules = list(_iter_experiment_modules())
    in_folders = {short for short, _full, top in modules if not top}
    stale: list[str] = []
    for short, full, top in modules:
        if short in RETIRED_KEYS or (top and short in in_folders):
            stale.append(short)
            continue                             # 아예 불러오지 않는다
        try:
            importlib.import_module(full)
        except Exception as exc:                 # noqa: BLE001 - 실험이 본체를 막지 않는다
            _log.warning("실험 모듈 %s 를 불러오지 못했습니다: %s", full, exc)
    if stale:
        _log.warning(
            "폐기됐거나 옮긴 실험 파일이 남아 있습니다: %s — %s 에서 지우세요. "
            "덮어쓰기 배포(cp -r)는 사라진 파일을 지우지 않습니다.",
            ", ".join(f"{name}.py" for name in sorted(stale)),
            package.__path__[0] if package.__path__ else "experiments/",
        )


def stale_modules() -> list[str]:
    """폐기됐거나 옮기기 전 자리에 남아 있는 실험 파일 이름.

    입력: 없음
    출력: 모듈 이름 목록 (없으면 빈 목록)
    비고:
        `docstruct --check` 와 진단 도구가 배포본이 깨끗한지 보는 데 쓴다.
        남아 있어도 등록되지 않으므로 결과는 바뀌지 않지만, **배포가 낡았다는
        신호**다 — 다른 파일도 함께 낡았을 수 있다. 0.4.85 부터는 최상위에
        남은 옛 사본(하위 폴더로 옮긴 것)도 여기에 든다.
    """
    modules = list(_iter_experiment_modules())
    in_folders = {short for short, _full, top in modules if not top}
    return sorted(short for short, _full, top in modules
                  if short in RETIRED_KEYS or (top and short in in_folders))
