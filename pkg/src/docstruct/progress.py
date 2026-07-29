"""진행 상황 표시.

역할:
    긴 작업의 진행률을 보여준다. tqdm 이 있으면 진행 막대를, 없으면 로그
    한 줄씩 출력한다. 노트북에서는 위젯 막대를 쓴다.
    하위 모듈(pipeline, tables)은 tqdm 을 직접 import 하지 않고 이 모듈이
    제공하는 인터페이스만 쓴다.
호출부:
    docstruct.api      문서 단위 진행
    docstruct.pipeline 단계 단위 진행
    docstruct.tables.* 표 평가·재추출 진행
    docstruct.cli
출력:
    ProgressBar — update() / close() 를 갖는 객체. 컨텍스트 매니저로도 쓴다.
"""
from __future__ import annotations

import logging
import sys
from typing import Any, Iterable, Iterator

_log = logging.getLogger(__name__)


def tqdm_available() -> bool:
    """tqdm 설치 여부.

    입력: 없음
    출력: 설치되어 있으면 True
    """
    try:
        import tqdm  # noqa: F401

        return True
    except ImportError:
        return False


def _in_notebook() -> bool:
    """Jupyter 커널에서 실행 중인지 판별한다.

    입력: 없음
    출력: 노트북이면 True (콘솔·스크립트면 False)
    """
    try:
        shell = get_ipython().__class__.__name__  # type: ignore[name-defined]
        return shell in ("ZMQInteractiveShell", "Shell")
    except NameError:
        return False


class ProgressBar:
    """진행 막대 (tqdm 이 없으면 로그로 대체).

    입력(생성자):
        total    전체 개수. 모르면 None
        desc     설명 문구
        unit     단위 표기 (기본 '건')
        enabled  False 면 아무것도 출력하지 않는다
    출력:
        update(n, postfix)  진행 갱신
        close()             정리
        write(msg)          막대를 흐트러뜨리지 않고 한 줄 출력
    """

    def __init__(
        self,
        total: int | None = None,
        desc: str = "",
        *,
        unit: str = "건",
        enabled: bool = True,
    ) -> None:
        self.total = total
        self.desc = desc
        self.unit = unit
        self.enabled = enabled
        self.n = 0
        self._bar: Any = None

        if not enabled:
            return

        if tqdm_available():
            if _in_notebook():
                try:
                    from tqdm.notebook import tqdm as _tqdm
                except ImportError:
                    from tqdm import tqdm as _tqdm
            else:
                from tqdm import tqdm as _tqdm
            self._bar = _tqdm(
                total=total,
                desc=desc,
                unit=unit,
                leave=True,
                dynamic_ncols=True,
                file=sys.stderr,
            )

    def update(self, n: int = 1, postfix: str | None = None) -> None:
        """진행을 갱신한다.

        입력: n — 증가량, postfix — 막대 뒤에 붙일 설명 (현재 파일명 등)
        출력: 없음
        """
        self.n += n
        if not self.enabled:
            return
        if self._bar is not None:
            if postfix:
                self._bar.set_postfix_str(postfix, refresh=False)
            self._bar.update(n)
        else:
            where = f"{self.n}/{self.total}" if self.total else str(self.n)
            tail = f" — {postfix}" if postfix else ""
            _log.info("%s %s%s%s", self.desc, where, self.unit, tail)

    def write(self, message: str) -> None:
        """막대를 유지한 채 메시지를 출력한다.

        입력: message — 출력할 문자열
        출력: 없음
        """
        if self._bar is not None:
            self._bar.write(message)
        else:
            print(message)

    def close(self) -> None:
        """막대를 닫는다.

        입력: 없음
        출력: 없음
        """
        if self._bar is not None:
            self._bar.close()
            self._bar = None

    def __enter__(self) -> "ProgressBar":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def track(
    iterable: Iterable[Any],
    desc: str = "",
    *,
    total: int | None = None,
    unit: str = "건",
    enabled: bool = True,
) -> Iterator[Any]:
    """반복을 진행 막대로 감싼다.

    입력:
        iterable  반복 대상
        desc      설명 문구
        total     전체 개수. 생략하면 len() 으로 시도
        unit      단위 표기
        enabled   False 면 그대로 통과
    출력: 원소를 하나씩 내주는 제너레이터
    """
    if not enabled:
        yield from iterable
        return

    if total is None:
        try:
            total = len(iterable)  # type: ignore[arg-type]
        except TypeError:
            total = None

    bar = ProgressBar(total=total, desc=desc, unit=unit)
    try:
        for item in iterable:
            yield item
            bar.update(1)
    finally:
        bar.close()


def install_hint() -> str:
    """tqdm 미설치 안내 문구.

    입력: 없음
    출력: 설치 방법을 담은 문자열
    """
    return "진행 막대를 보려면: pip install tqdm  (없어도 로그로 진행이 표시됩니다)"
