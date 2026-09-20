"""지금 돌고 있는 docstruct 의 판.

입력:
    없음
역할:
    **어느 판이 이 결과를 만들었는지** 한 곳에서 답한다. pip 설치본과
    폴더 배포본(local·overlay)이 함께 있을 수 있으므로, **불러온 자리**를
    먼저 보고 그 옆의 `VERSION` 을 권위로 삼는다.
호출부:
    docstruct.pipeline (document.json 의 pipeline 스냅샷) · docstruct.cli (--where)
출력:
    판 문자열 · 자세한 내역 dict

왜 필요한가 (0.5.13)
-------------------
결과물에 판이 적혀 있지 않았다. 그래서 "이 JSON 은 몇 판이 만든 것인가" 를
`〃` 가 있는지, `markdown` 이 `cells` 보다 짧은지 같은 **증상으로 되짚어야**
했다 — 세 번 연속 그렇게 추측했다. 판을 적어 두면 한 줄로 끝난다.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def details() -> dict:
    """판과 그 근거.

    입력: 없음
    출력: {version, source, location, pip_version}
        version      지금 도는 판 (모르면 "unknown")
        source       "folder" | "pip" | "unknown"
        location     불러온 패키지 폴더
        pip_version  pip 설치본이 따로 있으면 그 판 (없으면 None)
    비고:
        **불러온 자리가 먼저다.** pip 메타데이터를 먼저 믿으면, 폴더
        배포본 안에서 돌면서 pip 판을 보고하게 된다 — 0.5.12 에서 겪었다.
    """
    import docstruct

    here = Path(docstruct.__file__).resolve().parent
    folder = None
    for base in (here, here.parent):
        marker = base / "VERSION"
        if marker.is_file():
            try:
                folder = marker.read_text(encoding="utf-8").strip()
            except OSError:
                folder = None
            break

    pip_version = pip_path = None
    try:
        from importlib.metadata import distribution

        dist = distribution("docstruct")
        pip_version = dist.version
        pip_path = Path(str(dist.locate_file("docstruct"))).resolve()
    except Exception:                            # noqa: BLE001 - 없으면 없는 대로
        pass

    if folder and (pip_path is None or pip_path != here):
        return {"version": folder, "source": "folder", "location": str(here),
                "pip_version": pip_version}
    if pip_version:
        return {"version": pip_version, "source": "pip", "location": str(here),
                "pip_version": pip_version}

    # **소스 트리에서 바로 돌린 경우** (pkg/src). VERSION 파일도 pip 메타도
    # 없지만 `pyproject.toml` 이 두 단계 위에 있다 — 개발 중 만든
    # document.json 도 판을 밝혀야 한다 (0.5.13).
    source_version = _from_pyproject(here)
    if source_version:
        return {"version": f"{source_version} (소스 트리)", "source": "source",
                "location": str(here), "pip_version": None}
    return {"version": "unknown", "source": "unknown", "location": str(here),
            "pip_version": None}


def _from_pyproject(here: Path) -> str | None:
    """소스 트리의 `pyproject.toml` 에서 판을 읽는다.

    입력: here — 패키지 폴더 (`…/src/docstruct`)
    출력: 판 문자열 또는 None
    비고:
        toml 파서를 쓰지 않는다 — 파이썬 3.10 에도 없고, 이 한 줄을 읽자고
        의존성을 더할 이유가 없다. `version = "…"` 첫 줄만 본다.
    """
    import re

    for base in (here.parent.parent, here.parent.parent.parent):
        marker = base / "pyproject.toml"
        if not marker.is_file():
            continue
        try:
            text = marker.read_text(encoding="utf-8")
        except OSError:
            return None
        found = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
        return found.group(1) if found else None
    return None


def current() -> str:
    """판 문자열만.

    입력: 없음
    출력: 예 `docstruct-local 0.5.13` 또는 `0.5.13`
    """
    return details()["version"]
