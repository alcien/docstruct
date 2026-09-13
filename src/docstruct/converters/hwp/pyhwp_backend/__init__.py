"""pyhwp 기반 HWP 읽기 — **폴더째 떼어낼 수 있는 자리** (AGPL 격리).

축: 형식 — HWP.
역할:
    pyhwp(패키지 이름 `pyhwp`, 임포트 이름 `hwp5`)를 쓰는 코드를 **전부**
    이 폴더에 모은다. 라이선스 때문에 떼어내야 할 때 이 폴더 하나만
    지우면 되고, 다시 붙일 때도 이 폴더만 넣으면 된다.
호출부:
    docstruct.converters.hwp.converter (사다리 1단·3단) — **지연 import 로만**
출력:
    `is_available()` · `tree_markdown()` · `html()` 과 판정 보조

왜 폴더로 갈랐나 (0.5.0)
----------------------
pyhwp 는 **AGPL** 이다. 이 프로젝트가 그것을 쓰지 않기로 하면 관련 코드가
남아 있으면 안 된다. 그런데 예전에는 AGPL 표면이 **두 파일에 흩어져** 있고
`converter.py` 가 그것을 **최상위에서 import** 했다:

    converters/hwp/hwp5tree.py     hwp5.xmlmodel · hwp5.treeop 를 직접 쓴다
    converters/hwp/pyhwp.py        hwp5html 프로그램을 실행한다
    converters/hwp/converter.py    ↑ 둘을 파일 첫머리에서 import

실측(0.4.99): 두 파일을 지우면 `import docstruct` 는 살아남지만
`HwpConverter` 가 ImportError 로 죽었다. **AGPL 과 무관한 나머지 사다리
(HWP→HWPX 변환·HWPML·OLE 텍스트·미리보기)까지 함께 죽는다** — 지우려던
것보다 훨씬 많이 잃는다.

이제 이렇다:

    이 폴더가 있으면    1단 pyhwp 트리 · 3단 pyhwp HTML 이 사다리에 낀다
    이 폴더가 없으면    그 두 단만 빠지고 나머지는 그대로 돈다
                        (사유는 trace 와 fallback_reason 에 남는다)

`converter.py` 는 이 폴더를 **함수 안에서** 부른다. 없으면 None 을 받고
다음 단으로 내려간다 — 없다는 것이 정상 경로의 하나다.

같이 옮기지 **않은** 것
---------------------
    styling.py    HWP 서식 → 제목·강조·목록. hwp5 를 import 하지 않고,
                  **HWPX 경로(hwpxtree)도 쓴다.** 옮기면 HWPX 가 깨진다.
    diagnose.py   OLE 헤더만 본다.
    hwpml.py · olefile.py · preview.py   pyhwp 와 무관하다.

떼어낼 때
--------
    rm -r docstruct/converters/hwp/pyhwp_backend/
    pip uninstall pyhwp

`pyproject.toml` 의 선택 의존성에서도 빼면 끝난다. 시험
`test_hwp_works_without_the_pyhwp_backend` 가 이 상태를 확인한다.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

#: 이 폴더가 감싸는 외부 패키지 (설치 이름 · 임포트 이름 · 라이선스).
DEPENDENCY = {"pip": "pyhwp", "import": "hwp5", "license": "AGPL-3.0"}


def is_available() -> bool:
    """pyhwp 로 읽을 수 있는 상태인가.

    입력: 없음
    출력: 파서 모듈이 import 되면 True
    비고:
        폴더가 있어도 패키지가 안 깔렸을 수 있다. 둘은 다른 조건이므로
        따로 본다 — 폴더 유무는 `converter` 가, 패키지 유무는 여기가.
    """
    from docstruct.converters.hwp.pyhwp_backend import hwp5tree

    return hwp5tree.is_available()


def tree_markdown(path: str | Path) -> str:
    """사다리 1단 — pyhwp 파서 트리를 markdown 으로.

    입력: path — .hwp 경로
    출력: markdown 문자열
    예외: pyhwp 가 없거나 읽지 못하면 그대로 올라간다 (호출부가 다음 단으로)
    """
    from docstruct.converters.hwp.pyhwp_backend import hwp5tree

    return hwp5tree.to_markdown(str(path))


def html(path: str | Path) -> tuple[str, str]:
    """사다리 3단 — hwp5html 로 HTML 을 얻는다.

    입력: path — .hwp 경로
    출력: (HTML 문자열, stderr 문자열)
    예외: HwpTimeout · RuntimeError
    """
    from docstruct.converters.hwp.pyhwp_backend.html_export import hwp_to_html_str

    return hwp_to_html_str(Path(path))


def html_verdict(html_text: str, stderr: str, file_size: int) -> Any:
    """3단 결과를 믿을 만한지 판정한다.

    입력: html_text — 받은 HTML, stderr — 표준 에러, file_size — 원본 크기
    출력: (불충분한가, 사유)
    """
    from docstruct.converters.hwp.pyhwp_backend.html_export import pyhwp_html_verdict

    return pyhwp_html_verdict(html_text, stderr, file_size)


def real_errors(stderr: str, limit: int = 3) -> list[str]:
    """stderr 에서 진짜 오류 줄만 고른다 (상시 경고 제외).

    입력: stderr — 표준 에러 전체, limit — 최대 줄 수
    출력: 오류로 볼 줄 목록
    """
    from docstruct.converters.hwp.pyhwp_backend.html_export import real_error_lines

    return real_error_lines(stderr, limit=limit)


def timeout_error() -> type[Exception]:
    """제한 시간 초과 예외 클래스.

    입력: 없음
    출력: HwpTimeout
    비고:
        호출부가 `except backend.timeout_error()` 로 잡는다. 클래스를
        최상위에서 import 하면 폴더가 없을 때 그 줄에서 죽는다.
    """
    from docstruct.converters.hwp.pyhwp_backend.html_export import HwpTimeout

    return HwpTimeout


__all__ = ["DEPENDENCY", "html", "html_verdict", "is_available", "real_errors",
           "timeout_error", "tree_markdown"]
