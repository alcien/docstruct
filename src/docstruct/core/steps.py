"""진행 단계 — 지금 어디를 보고 있는가.

입력:
    파이프라인이 구간에 들어설 때 부르는 신호
역할:
    **한 벌의 사실을 두 겹으로 낸다.** 사람에게 보여줄 굵은 13단계와,
    개발자가 볼 진행수·건너뛴 이유·소요 시간. 문구가 두 곳에 흩어지면
    화면과 로그가 서로 다른 말을 하게 되므로(0.4.83 에서 겪었다) 표는
    **여기 한 곳**에만 둔다.
호출부:
    docstruct.pipeline (구간마다 `enter`) · docstruct.cli · overlay(SSE)
출력:
    이벤트 dict 흐름 — 콜백·JSONL 파일·로그로 나간다

두 겹인 이유
-----------
    출력용   `표 다시 세우는 중…`          13단계. 끝나면 잊어도 되는 말
    개발용   `[6/12] 표 재구성 · 48/119표 · 2.1초 · lattice_fill 채택 12`

앞엣것은 "기다려도 되는가" 에 답하고, 뒤엣것은 "왜 이런 결과인가" 에
답한다. 섞으면 둘 다 못 한다.

동시 실행에 안전한가
------------------
현재 문서의 보고자는 `ContextVar` 에 둔다. 스레드마다·태스크마다 각자의
것을 보므로 서버에서 두 건이 겹쳐 돌아도 진행이 섞이지 않는다 — 0.4.87 의
HWPX 수집기와 같은 사정이고 같은 해법이다.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Step:
    """단계 하나.

    입력(필드):
        id      짧은 이름 (이벤트·필터에 쓴다)
        phase   `pipeline.build_document` 의 구간 번호
        user    사람에게 보여줄 말 (진행형)
        dev     개발자용 짧은 이름
        axis    형식 · 텍스트 · 표 · 그림 · 공통
        counts  진행수를 낼 수 있는 단계인가 (쪽·표·그림 단위)
    """

    id: str
    phase: int
    user: str
    dev: str
    axis: str
    counts: str = ""


#: **문구의 유일한 출처.** 화면·로그·SSE 가 모두 이 표를 읽는다.
STEPS: tuple[Step, ...] = (
    Step("open", 0, "파일 확인 중…", "입력 확인·작업 폴더", "공통"),
    Step("extract", 1, "문서 여는 중…", "추출", "형식"),
    Step("scan_detect", 2, "지면 이미지 만드는 중…", "스캔 판별·지면 렌더", "텍스트", "쪽"),
    Step("scan_read", 3, "스캔 쪽 글자 읽는 중…", "스캔 본문 판독", "텍스트", "쪽"),
    Step("table_mark", 4, "표 살펴보는 중…", "표 표시(바꾸지 않음)", "표", "표"),
    Step("experiments", 5, "표 보정 기법 적용 중…", "실험 사다리", "표", "실험"),
    Step("table_rebuild", 6, "표 다시 세우는 중…", "표 재구성(바꿈)", "표", "표"),
    Step("picture", 7, "VLM 기반 이미지 판독 중…", "그림 판독", "그림", "그림"),
    Step("integrity", 8, "표 정합성 검사 중…", "상시 검사", "표", "표"),
    Step("table_llm", 9, "표 판정 중…", "표 LLM 판정·재추출", "표", "표"),
    Step("ocr_verify", 10, "읽은 글자 검증 중…", "OCR 검증·재판독", "텍스트", "쪽"),
    Step("outline", 11, "목차 찾는 중…", "문서 구조", "텍스트"),
    Step("finish", 12, "정리하고 저장 중…", "마무리", "공통"),
)

_BY_ID = {step.id: step for step in STEPS}

#: 형식마다 **실제로 지나가는** 단계. 나머지는 건너뛴 것으로 남긴다 —
#: 목록에서 지우지 않는다. HWP 에서 표 단계가 왜 없는지가 이 화면의 가장
#: 쓸모 있는 정보이기 때문이다.
STEPS_BY_FORMAT: dict[str, tuple[str, ...]] = {
    "pdf": tuple(step.id for step in STEPS),
    "hwpx": ("open", "extract", "experiments", "integrity", "outline", "finish"),
    "hwp": ("open", "extract", "outline", "finish"),
}


def steps_for(fmt: str) -> tuple[Step, ...]:
    """이 형식이 지나가는 단계.

    입력: fmt — 'pdf' | 'hwpx' | 'hwp'
    출력: Step 목록 (모르는 형식이면 전체)
    """
    allowed = STEPS_BY_FORMAT.get(fmt)
    if allowed is None:
        return STEPS
    return tuple(step for step in STEPS if step.id in allowed)


@dataclass
class Reporter:
    """문서 한 건의 진행을 모으고 내보낸다.

    입력(필드):
        document  문서 이름
        fmt       형식
        sinks     이벤트를 받을 함수들
        detail    "user" 면 굵은 단계만, "dev" 면 진행수·이유·시간까지
    비고:
        `enter` 는 **들어설 때** 부른다. 구간은 순서대로 지나가므로 다음
        구간에 들어서는 것이 곧 앞 구간의 끝이다 — 끝 신호를 따로 받지
        않아도 되어 파이프라인에 넣는 자리가 절반으로 준다.
    """

    document: str = ""
    fmt: str = ""
    sinks: list[Callable[[dict], None]] = field(default_factory=list)
    detail: str = "dev"
    started: float = field(default_factory=time.perf_counter)
    _seq: int = 0
    _current: str | None = None
    _entered: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ── 내보내기 ──────────────────────────────────────────────────────
    def _emit(self, **fields: Any) -> dict:
        """이벤트 하나를 만들어 모든 sink 에 보낸다.

        입력: fields — 이벤트 내용
        출력: 만든 이벤트 dict
        비고:
            sink 하나가 터져도 나머지는 받는다. 진행 표시가 판독을 멈추게
            해서는 안 된다 — 파일이 잠겼거나 프런트가 끊긴 정도로 문서가
            실패하면 곤란하다.
        """
        with self._lock:
            self._seq += 1
            event = {
                "seq": self._seq,
                "ts": time.time(),
                "elapsed": round(time.perf_counter() - self.started, 3),
                "document": self.document,
                "format": self.fmt,
                **fields,
            }
        for sink in list(self.sinks):
            try:
                sink(event)
            except Exception as exc:              # noqa: BLE001
                _log.debug("진행 sink 실패(무시): %s", exc)
        return event

    # ── 단계 ─────────────────────────────────────────────────────────
    def enter(self, step_id: str, *, total: int | None = None,
              note: str = "") -> dict:
        """이 단계에 들어섰다.

        입력: step_id — STEPS 의 id, total — 진행수 분모, note — 한 줄 덧붙임
        출력: 이벤트 dict
        """
        step = _BY_ID[step_id]
        # **이 형식이 지나가지 않는 단계는 내지 않는다** — 파이프라인은
        # 구간을 순서대로 지나며 신호를 보내지만, 그 안에서 아무 일도 하지
        # 않는 구간이 있다(HWPX 에는 스캔 판독이 없다). 이미 `skip` 으로
        # 이유와 함께 알렸으므로 여기서 또 "…중" 이라 하면 거짓말이 된다.
        if self.fmt and step.id not in {s.id for s in steps_for(self.fmt)}:
            return {}
        now = time.perf_counter()
        prev, spent = self._current, (now - self._entered) if self._current else 0.0
        self._current, self._entered = step_id, now
        return self._emit(
            kind="enter", step=step.id, phase=step.phase, axis=step.axis,
            user=step.user, dev=step.dev, note=note, total=total, done=0,
            previous=prev, previous_seconds=round(spent, 3),
        )

    def advance(self, done: int, total: int | None = None) -> dict:
        """지금 단계의 진행수를 갱신한다.

        입력: done — 마친 수, total — 분모(모르면 생략)
        출력: 이벤트 dict
        비고: 개발자용에서만 쓸모가 있어 출력용 렌더러는 무시한다.
        """
        step = _BY_ID.get(self._current or "", None)
        return self._emit(kind="progress", step=self._current,
                          phase=step.phase if step else None,
                          done=done, total=total,
                          unit=step.counts if step else "")

    def skip(self, step_id: str, reason: str) -> dict:
        """이 단계를 건너뛴다 — **이유와 함께.**

        입력: step_id, reason — 왜 건너뛰는가
        출력: 이벤트 dict
        비고:
            목록에서 지우지 않는다. "HWP 에는 cells 가 없어 표 검사를 하지
            않았다" 는 사실은 결과를 읽는 사람에게 필요한 정보다 — 단계가
            그냥 사라지면 안 본 것인지 볼 것이 없었던 것인지 알 수 없다.
        """
        step = _BY_ID[step_id]
        return self._emit(kind="skip", step=step.id, phase=step.phase,
                          axis=step.axis, user=step.user, dev=step.dev,
                          reason=reason)

    def done(self, note: str = "") -> dict:
        """문서 한 건이 끝났다.

        입력: note — 한 줄 요약
        출력: 이벤트 dict
        """
        spent = (time.perf_counter() - self._entered) if self._current else 0.0
        event = self._emit(kind="done", step=self._current, note=note,
                           previous=self._current,
                           previous_seconds=round(spent, 3))
        self._current = None
        return event


# ── 현재 보고자 ────────────────────────────────────────────────────────
#: 문서마다·스레드마다 따로 본다. 서버에서 두 건이 겹쳐도 섞이지 않는다.
_current: ContextVar[Reporter | None] = ContextVar("docstruct_reporter", default=None)


def reporter() -> Reporter | None:
    """지금 문서의 보고자 (없으면 None).

    입력: 없음
    출력: Reporter 또는 None
    """
    return _current.get()


def report(step_id: str, *, total: int | None = None, note: str = "") -> None:
    """지금 단계를 알린다 — 보고자가 없으면 조용히 지나간다.

    입력: step_id, total, note
    출력: 없음
    비고:
        파이프라인 안에서 부르기 좋게 만든 얇은 겉면이다. 보고자가 없을
        때(라이브러리를 그냥 부른 경우) 아무 일도 하지 않으므로 호출부가
        None 검사를 하지 않아도 된다.
    """
    got = _current.get()
    if got is not None:
        got.enter(step_id, total=total, note=note)


def report_progress(done: int, total: int | None = None) -> None:
    """지금 단계의 진행수를 알린다.

    입력: done, total
    출력: 없음
    """
    got = _current.get()
    if got is not None:
        got.advance(done, total)


def report_skip(step_id: str, reason: str) -> None:
    """단계를 건너뛴다고 알린다.

    입력: step_id, reason
    출력: 없음
    """
    got = _current.get()
    if got is not None:
        got.skip(step_id, reason)


@contextmanager
def reporting(document: str, fmt: str, *,
              sinks: list[Callable[[dict], None]] | None = None,
              detail: str = "dev") -> Iterator[Reporter]:
    """이 블록 동안의 보고자를 세운다.

    입력: document — 문서 이름, fmt — 형식, sinks — 받을 곳, detail
    출력: Reporter
    """
    got = Reporter(document=document, fmt=fmt, sinks=list(sinks or []),
                   detail=detail)
    token = _current.set(got)
    try:
        yield got
    finally:
        _current.reset(token)


# ── 내보낼 곳 ──────────────────────────────────────────────────────────
def jsonl_sink(path: str | Path) -> Callable[[dict], None]:
    """이벤트를 JSONL 파일에 한 줄씩 덧붙인다.

    입력: path — 쓸 파일
    출력: sink 함수
    비고:
        **줄 단위로 쓰고 매번 닫는다.** 프런트가 `tail -f` 하듯 읽는 동안
        파일이 열려 있어도 되고, 도중에 죽어도 그때까지의 줄은 온전하다.
        열어 둔 채 버퍼에 쌓으면 진행이 뭉텅이로 나와 실시간이 아니게 된다.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()

    def write(event: dict) -> None:
        line = json.dumps(event, ensure_ascii=False)
        with lock, target.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()

    return write


def log_sink(level: int = logging.INFO) -> Callable[[dict], None]:
    """이벤트를 로그로 낸다 (개발자용 문구).

    입력: level — 로그 수준
    출력: sink 함수
    """
    def write(event: dict) -> None:
        _log.log(level, "%s", render_dev(event))

    return write


# ── 사람이 읽을 줄로 ───────────────────────────────────────────────────
def render_user(event: dict) -> str | None:
    """출력용 한 줄 — 굵은 13단계만.

    입력: event — 이벤트 dict
    출력: 보여줄 문자열, 보여줄 것이 없으면 None
    비고:
        진행수·이유·시간을 넣지 않는다. 이 줄은 "기다려도 되는가" 에만
        답한다. 건너뛴 단계도 내지 않는다 — 사용자에게는 지나간 일이다.
    """
    if event.get("kind") == "enter":
        return event.get("user")
    if event.get("kind") == "done":
        return "완료"
    return None


def render_dev(event: dict) -> str:
    """개발자용 한 줄 — 진행수·이유·시간까지.

    입력: event — 이벤트 dict
    출력: 문자열
    """
    kind = event.get("kind")
    phase = event.get("phase")
    head = f"[{phase:>2}/12]" if isinstance(phase, int) else "[  ·  ]"
    if kind == "enter":
        parts = [f"{head} {event.get('dev')}"]
        if event.get("total"):
            parts.append(f"0/{event['total']}")
        if event.get("note"):
            parts.append(str(event["note"]))
        if event.get("previous"):
            parts.append(f"(앞 단계 {event['previous_seconds']}초)")
        return " · ".join(parts)
    if kind == "progress":
        total = event.get("total")
        unit = event.get("unit") or ""
        shown = f"{event.get('done')}/{total}{unit}" if total else f"{event.get('done')}{unit}"
        return f"{head} {event.get('step')} · {shown}"
    if kind == "skip":
        return f"{head} {event.get('dev')} · 건너뜀 — {event.get('reason')}"
    if kind == "done":
        return f"[완료] {event.get('document')} · {event.get('elapsed')}초 " \
               f"{event.get('note') or ''}".rstrip()
    return f"{head} {kind}"


def source_format_of(path: str | Path) -> str:
    """경로에서 형식 이름을 얻는다 (모르면 빈 문자열).

    입력: path — 문서 경로
    출력: 'pdf' | 'hwpx' | 'hwp' | ''
    비고:
        진행 표시가 형식 판별 때문에 터지면 안 되므로 예외를 삼킨다 —
        형식을 모르면 전체 단계를 보여 주면 그만이다.
    """
    try:
        from docstruct.pipeline import source_format

        return source_format(Path(path))
    except Exception:                            # noqa: BLE001
        return ""


#: 진행 기록 파일 이름. 한 곳에만 적는다 — CLI·API·서버가 같은 이름을 쓴다.
PROGRESS_FILENAME = "progress.jsonl"


def progress_path(out_dir: str | Path | None) -> Path | None:
    """진행 기록을 남길 자리.

    입력: out_dir — 산출 폴더 (없으면 None)
    출력: 파일 경로, 남길 곳이 없으면 None
    비고:
        **규칙은 하나다: 산출물 옆.** 문서 하나의 산출 폴더 안에
        `progress.jsonl` 로 둔다 — `document.json` 과 같은 자리다. 결과와
        그 결과가 어떻게 나왔는지가 떨어져 있으면 둘을 짝지어 볼 수 없다.

            out/조달청.hwpx/document.json
            out/조달청.hwpx/progress.jsonl   ← 여기

        산출 폴더가 없으면(노트북에서 `out_dir` 없이 부른 경우) 파일을
        만들지 않는다. 저장할 곳을 지어내면 어디에 생겼는지 아무도 모른다 —
        그때는 화면에만 보인다.
    """
    if out_dir is None:
        return None
    return Path(out_dir) / PROGRESS_FILENAME


#: 표시 모드의 별칭 → 표준 이름 (0.4.99).
#:
#: `off` 를 "완전히 끔" 으로 두었더니 **"상세를 끈다"** 는 뜻으로 읽혔다.
#: 자연스러운 읽기다 — 끄고 싶은 것은 보통 개발자용 소음이지 진행 표시
#: 자체가 아니다. 그래서 `off` 는 굵은 13단계로 보내고, 정말 아무것도
#: 내지 않으려면 `silent` 를 쓰게 했다.
_MODE_ALIASES = {
    "brief": "brief", "user": "brief", "off": "brief", "coarse": "brief",
    "on": "brief", "true": "brief", "1": "brief",
    "dev": "dev", "detail": "dev", "detailed": "dev", "verbose": "dev",
    "silent": "silent", "none": "silent", "quiet": "silent",
    "false": "silent", "0": "silent",
}

#: 화면에 낼 수 있는 표준 모드.
MODES = ("brief", "dev", "silent")


def normalize_mode(value: object) -> str:
    """표시 모드 이름을 표준으로 맞춘다.

    입력: value — "off" · "user" · "dev" · True · None …
    출력: "brief" | "dev" | "silent"
    비고:
        모르는 값은 `brief` 로 본다 — 오타 하나로 진행 표시가 통째로
        사라지면 "멈춘 건가" 를 다시 겪는다. 조용히 하려면 **또렷하게**
        `silent` 라고 적어야 한다.
    """
    if value is None:
        return "brief"
    if value is True:
        return "brief"
    if value is False:
        return "silent"
    return _MODE_ALIASES.get(str(value).strip().lower(), "brief")


def console_sink(detail: str = "brief") -> Callable[[dict], None]:
    """이벤트를 화면에 낸다 (CLI·노트북 공용).

    입력: detail — "brief"(굵은 13단계) | "dev"(상세) | "silent"(끔).
          `off`·`user` 는 brief 의 별칭이다 (0.4.99)
    출력: sink 함수
    비고:
        노트북에서도 그대로 쓴다. `print` 는 셀 출력으로 나가고 tqdm 막대와
        섞이지 않는다 — 막대는 진행 **건수**를, 이 줄은 진행 **단계**를
        말하므로 둘 다 있어도 겹치지 않는다.
    """
    mode = normalize_mode(detail)

    def write(event: dict) -> None:
        if mode == "silent":
            return
        if mode == "dev":
            print(f"  {render_dev(event)}")
            return
        line = render_user(event)
        if line:
            print(f"  … {line}" if event.get("kind") == "enter" else f"  {line}")

    return write
