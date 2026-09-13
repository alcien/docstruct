"""선택적 의존성 가용성 확인.

입력:
    (없음)

역할:
    docling·bs4·olefile·pyhwp·hwpx 처럼 없어도 되는 패키지의 설치 여부를
    한 곳에서 판별하고, 있으면 심볼을 함께 노출한다. 미설치 시에도
    import 가 실패하지 않게 해 해당 기능만 비활성화되도록 한다.
호출부:
    converters.*, docstruct.extractors.hwpx
출력:
    *_AVAILABLE 불리언과 (설치된 경우) 해당 심볼
"""
from __future__ import annotations

import importlib.util

try:
    from bs4 import BeautifulSoup, NavigableString, Tag
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False
    BeautifulSoup = None  # type: ignore
    Tag = NavigableString = None  # type: ignore

def _pyhwp_available() -> bool:
    """pyhwp(AGPL) 로 읽을 수 있는 상태인가.

    입력: 없음
    출력: 백엔드 폴더와 패키지가 **둘 다** 있으면 True
    비고:
        `import hwp5` 를 여기서 직접 하지 않는다 (0.5.0). AGPL 표면은
        `converters/hwp/pyhwp_backend/` 한 폴더에만 있어야 폴더째 떼어낼
        수 있다 — 이 파일에 import 가 남으면 지워도 남는다.
    """
    try:
        from docstruct.converters.hwp import pyhwp_backend
    except ImportError:
        return False                             # 폴더를 떼어낸 상태
    try:
        return pyhwp_backend.is_available()
    except Exception:                            # noqa: BLE001 - 못 재면 없는 것으로
        return False


#: 재어 둔 값 (한 번만 잰다). None 이면 아직 안 쟀다는 뜻.
_pyhwp_cache: bool | None = None


def __getattr__(name: str):
    """`PYHWP_AVAILABLE` 을 **쓸 때** 잰다 (0.5.4 · PEP 562).

    입력: name — 속성 이름
    출력: 속성 값
    예외: 모르는 이름이면 AttributeError
    비고:
        예전에는 이 파일을 import 하는 것만으로 `hwp5` 를 불렀다. 그러면
        **hwp2hwpx 로만 처리하는 배포에서도** AGPL 모듈이 시작할 때 올라온다.
        쓰지도 않을 것을 부르는 셈이라, 처음 필요해질 때까지 미룬다.

        `deps.PYHWP_AVAILABLE` 이라는 이름은 그대로 쓴다 — 호출부를 고치지
        않는다.
    """
    if name == "PYHWP_AVAILABLE":
        global _pyhwp_cache
        if _pyhwp_cache is None:
            _pyhwp_cache = _pyhwp_available()
        return _pyhwp_cache
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

try:
    import olefile
    OLEFILE_AVAILABLE = True
except ImportError:
    olefile = None  # type: ignore
    OLEFILE_AVAILABLE = False

# docling은 cv2/numpy 등 무거운 의존성을 끌어옵니다.
# import 시점이 아닌 PDF 변환 시점에만 로드합니다 (uvicorn reload 충돌 방지).
def _module_available(name: str) -> bool:
    """모듈 설치 여부를 확인한다.

    입력: name — 모듈명
    출력: 설치되어 있으면 True. 조회 자체가 실패해도 예외 없이 False
    비고:
        노트북에서 ``!pip install`` 로 설치한 뒤 커널을 재시작하지 않으면
        임포터 캐시가 낡아 find_spec 이 못 찾는다. 한 번 실패하면 캐시를
        비우고 다시 본다.
    """
    import sys

    if name in sys.modules:
        return True

    def _probe() -> bool:
        """find_spec 으로 모듈 존재를 확인한다.

        입력: 없음 (둘러싼 name 사용)
        출력: 찾으면 True. 조회 자체가 실패해도 예외 없이 False
        """
        try:
            return importlib.util.find_spec(name) is not None
        except (ImportError, ValueError, AttributeError):
            return False

    if _probe():
        return True

    importlib.invalidate_caches()
    return _probe()


DOCLING_AVAILABLE = _module_available("docling")

# python-hwpx도 변환 시점에만 로드합니다.
HWPX_AVAILABLE = _module_available("hwpx")
