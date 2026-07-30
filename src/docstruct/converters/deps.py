"""선택적 의존성 가용성 확인.

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

try:
    import hwp5.hwp5html  # noqa: F401
    PYHWP_AVAILABLE = True
except (ImportError, Exception):
    PYHWP_AVAILABLE = False

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
