"""HWP → markdown/HTML/XML/텍스트.

입력:
    .hwp 경로

역할:
    HWP 파일을 세 경로 중 하나로 읽는다.
      hwpml-xml     내용이 실제로는 XML 인 경우 직접 파싱 (표 구조 보존)
      hwp5-tree     pyhwp 파서 트리를 직접 읽음 (표·중첩표·병합 보존, 기본)
      pyhwp-html    hwp5html 로 HTML 변환 후 파싱 (폴백)
      olefile-text  위가 모두 불충분할 때 텍스트만 추출 (표·그림 구조 손실)
호출부:
    docstruct.extractors.hwp
    converters.registry (BaseConverter 인터페이스)
출력:
    markdown / HTML / XML / 텍스트 문자열, 원본 `<table>` HTML 조각,
    실제 사용된 경로 이름
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from docstruct.converters.base import BaseConverter
from docstruct.converters import deps
from docstruct.converters.deps import BS4_AVAILABLE, BeautifulSoup, OLEFILE_AVAILABLE
from docstruct.converters.html import html_to_markdown, html_to_text, html_to_xml
from docstruct.converters.hwp.hwpml import is_hwpml, to_html as hwpml_to_html
from docstruct.converters.hwp.hwpml import to_markdown as hwpml_to_markdown
from docstruct.converters.hwp.hwpml import to_text as hwpml_to_text
from docstruct.converters.hwp.hwpml import to_xml as hwpml_to_xml
from docstruct.core.config import get_settings
from docstruct.converters.hwp.diagnose import diagnose
from docstruct.converters.hwp.olefile import clean_text, extract_raw_text, text_to_html, text_to_markdown, text_to_xml
import logging

_log = logging.getLogger(__name__)


def _backend():
    """pyhwp 백엔드를 얻는다 — **없으면 None** (0.5.0).

    입력: 없음
    출력: `converters.hwp.pyhwp_backend` 모듈 또는 None
    비고:
        **함수 안에서 부른다.** 최상위 import 로 두었더니 그 폴더를 지웠을
        때 `HwpConverter` 자체가 ImportError 로 죽었다 — AGPL 과 무관한
        나머지 사다리(HWP→HWPX 변환·HWPML·OLE 텍스트·미리보기)까지 함께.
        지우려던 것보다 훨씬 많이 잃는 구조였다.

        이제 폴더가 없는 것은 **정상 경로의 하나**다. 1단·3단만 빠지고
        나머지는 그대로 돈다.
    """
    try:
        from docstruct.converters.hwp import pyhwp_backend

        return pyhwp_backend
    except ImportError:
        return None


def _first_real_error(exc: Exception) -> str:
    """예외 메시지에서 상시 경고를 뺀 첫 줄을 뽑는다.

    입력: exc — 백엔드의 HTML 단이 낸 RuntimeError
    출력: 로그 한 줄에 넣을 짧은 사유
    비고: 백엔드가 없으면 예외 문장을 그대로 쓴다.
    """
    backend = _backend()
    if backend is None:
        return str(exc).strip().splitlines()[-1] if str(exc).strip() else "원인 미상"
    lines = backend.real_errors(str(exc))
    return lines[-1].strip() if lines else "원인 미상 (경고 외 메시지 없음)"

#: 파서 트리 결과가 이보다 적으면 실패로 보고 기존 경로로 넘어간다.
_MIN_TREE_CHARS = 200


def _pipeline_markdown(path) -> str:
    """파이프라인을 돌려 사람이 볼 수 있는 markdown 을 만든다.

    입력: path — 원본 문서 경로
    출력: markdown 문자열
    예외: 실패하면 그대로 올린다 (호출부가 옛 경로로 폴백한다)
    비고:
        컨버터 자체 경로는 **원재료**다. 그것을 그대로 내보내면 뒤가 전부
        빠진다 — 한글 정규화·누름틀 잔재 제거·표 placeholder·그림 추출·
        VLM 판독·펼치기. PDF 는 여기에 더해 0.4.11 의 OCR 게이트 수정과
        0.4.13 의 텍스트 레이어 메우기도 못 받는다.

        서비스의 `/convert/markdown` 이 이 자리를 탄다. 실측(조달청 HWPX):
        document.json 에는 VLM 이 조직도를 계층·정원표까지 복원해 담겼는데
        같은 실행의 `.md` 에는 `<!-- hwpx-image:image1 -->` 원형 표식만
        남았다 — 컨버터가 파이프라인을 건너뛰었기 때문이다.
    """
    from docstruct.pipeline import build_document
    from docstruct.output.report import document_markdown

    doc = build_document(path, assess_tables=False, fill_tables=False)
    return document_markdown(doc)


def skip_pyhwp() -> bool:
    """pyhwp(AGPL) 경로를 건너뛸지.

    입력: 없음 (환경변수 `DOCSTRUCT_HWP_NO_PYHWP`)
    출력: 건너뛰면 True
    비고:
        pyhwp 를 설치하지 않거나 라이선스(AGPL) 때문에 쓰지 않으려는
        환경을 위한 손잡이다. **코드는 지우지 않는다** — 켜면 그대로
        돌아간다. 끄면 사다리의 위 두 단(hwp5-tree · pyhwp-html)을
        건너뛰고 olefile 텍스트 폴백이 받는다.

        미설치와 다른 점: 미설치는 import 실패로 걸러지지만 그때마다
        경고가 쌓이고 진단 메시지가 "설치하세요" 를 권한다. 이 손잡이는
        **의도한 구성**임을 기록으로 남긴다.
    """
    import os

    return os.getenv("DOCSTRUCT_HWP_NO_PYHWP", "").strip().lower() in (
        "1", "true", "on", "yes")


class HwpConverter(BaseConverter):
    """
    HWP 파일을 text / markdown / html / xml 로 변환

    핵심 전략
    ----------
    1. hwp5html로 HTML을 얻고 HTML 파싱으로 각 포맷 변환 (우선)
    2. HTML이 불충분하면 olefile 직접 파싱으로 폴백 (필드 문서 등)
    3. pyhwp 미설치 시 olefile 폴백
    """

    def __init__(self, hwp_path: str | Path):
        super().__init__(hwp_path)
        self._html_cache: str | None = None
        self._html_stderr: str = ""
        self._ole_fallback: bool | None = None
        self._ole_text_cache: str | None = None
        self._tree_cache: str | None = None
        self._tree_tried = False
        #: 기본 경로(hwp5-tree)가 실패한 사유. 폴백까지 실패했을 때
        #: 사람에게 보여야 할 **첫 실패**다.
        self._tree_failure: str | None = None

    @property
    def source_format(self) -> str:
        """원본 형식 이름.

        입력: 없음
        출력: 'hwp'
        """
        return "hwp"

    def extraction_path(self) -> str:
        """실제로 사용된 추출 경로.

        입력: 없음
        출력: 'hwpml-xml' | 'hwp5-tree' | 'pyhwp-html' | 'olefile-text'
        """
        if is_hwpml(self.path):
            return "hwpml-xml"
        if self._get_tree_markdown() is not None:
            return "hwp5-tree"
        return "olefile-text" if self._uses_ole_fallback() else "pyhwp-html"

    def _get_tree_markdown(self) -> str | None:
        """pyhwp 파서 트리 경로 결과 (한 번만 시도하고 캐시).

        입력: 없음
        출력: markdown. 쓸 수 없으면 None
        비고:
            같은 pyhwp 안에서 파서 층(hwp5.xmlmodel)을 직접 읽는다. HTML
            생성기(hwp5html)는 XSLT 단계에서 내용이 크게 깎이고 문서에 따라
            통째로 실패한다 — 626KB 문서에서 표 48개/글자 7천 vs
            표 111개/글자 3만7천, 26초 vs 1.6초 차이였다.

            결과가 비면 None 을 돌려주고 기존 경로가 이어받는다.
        """
        if self._tree_tried:
            return self._tree_cache
        self._tree_tried = True
        if skip_pyhwp():
            self._tree_failure = "pyhwp 경로를 설정으로 껐습니다 (DOCSTRUCT_HWP_NO_PYHWP)"
            _log.info("pyhwp 경로 건너뜀 — olefile 폴백으로 처리합니다")
            return None
        backend = _backend()
        if backend is None:
            self._tree_failure = (
                "pyhwp 백엔드가 설치본에 없습니다 "
                "(converters/hwp/pyhwp_backend/ 를 떼어낸 상태)")
            return None
        if not backend.is_available():
            self._tree_failure = "pyhwp 파서 모듈(hwp5.xmlmodel)을 불러올 수 없음"
            return None
        try:
            md = backend.tree_markdown(self.path)
        except Exception as exc:                 # noqa: BLE001 - 폴백이 있으므로 삼킨다
            # **기본 경로가 죽은 것**이므로 INFO 로 묻으면 안 된다. 예전에는
            # INFO 였고, 기본 로깅(WARNING)에서 보이지 않았다. 그래서 뒤이어
            # hwp5html 까지 실패했을 때 사람이 두 번째 실패만 보고 그것을
            # 원인으로 오해했다. 첫 실패가 진짜 원인이다.
            self._tree_failure = f"{type(exc).__name__}: {exc}"
            _log.warning(
                "hwp5-tree(기본 경로) 실패 — 폴백으로 내려갑니다: %s",
                self._tree_failure,
            )
            return None
        if not md or len(md.strip()) < _MIN_TREE_CHARS:
            chars = len(md.strip()) if md else 0
            self._tree_failure = (
                f"본문이 {chars}자뿐 (기준 {_MIN_TREE_CHARS}자) — 파싱은 됐으나 내용이 없음"
            )
            _log.warning(
                "hwp5-tree(기본 경로) 결과가 %d자뿐 — 폴백으로 내려갑니다", chars,
            )
            return None
        self._tree_cache = md
        return md

    @property
    def tree_failure(self) -> str | None:
        """기본 경로(hwp5-tree)가 실패한 사유.

        입력: 없음
        출력: 실패 사유 문자열. 기본 경로가 성공했으면 None
        비고:
            폴백 경로까지 실패했을 때, 사람에게 보여야 할 것은 **첫 실패**다.
            두 경로는 같은 pyhwp 파서를 공유하므로 대개 원인이 같다.
        """
        return getattr(self, "_tree_failure", None)

    def _uses_ole_fallback(self) -> bool:
        """pyhwp 결과가 불충분해 텍스트 폴백이 필요한지 판단한다.

        입력: 없음
        출력: 폴백이 필요하면 True
        """
        if self._ole_fallback is not None:
            return self._ole_fallback
        if is_hwpml(self.path):
            self._ole_fallback = False
            return False
        if skip_pyhwp():
            # 두 번째 단(hwp5html)도 pyhwp 이므로 함께 건너뛴다.
            self._fallback_reason = (
                "pyhwp 경로를 설정으로 껐습니다 (DOCSTRUCT_HWP_NO_PYHWP) — "
                "텍스트 경로로 처리합니다")
            self._ole_fallback = True
            return True
        backend = _backend()
        if backend is None:
            # **왜 내려왔는지 남긴다** (0.5.1). 예전에는 조용히 True 만
            # 돌려줘서 결과물에 사유가 없었다 — "표가 왜 없지" 를 로그로
            # 뒤져야 했다. 백엔드를 떼어낸 배포에서는 이것이 정상 경로이므로
            # 더더욱 적혀 있어야 한다.
            self._fallback_reason = (
                "pyhwp 백엔드가 설치본에 없어 텍스트 경로로 처리합니다 "
                "(표·그림 구조 없음). HWP→HWPX 변환기를 붙이면 표가 살아납니다 "
                "— converters/hwpx/convert.py")
            self._ole_fallback = True
            return True
        if not deps.PYHWP_AVAILABLE:
            self._fallback_reason = (
                "pyhwp 패키지가 설치되지 않아 텍스트 경로로 처리합니다 "
                "(표·그림 구조 없음)")
            self._ole_fallback = True
            return True
        try:
            html, stderr = backend.html(self.path)
        except backend.timeout_error() as exc:
            # 시간 안에 못 끝내면 표 구조를 포기하고 텍스트만이라도 뽑는다.
            _log.warning("%s", exc)
            self._ole_fallback = True
            return True
        except RuntimeError as exc:
            # hwp5html 이 0 이 아닌 코드로 끝난 경우. 이건 olefile 로 내려갈
            # **가장 강한 근거**다 — 그런데 예전에는 예외가 그대로 위로 튀어
            # 문서가 통째로 실패했다. olefile 로 본문은 건질 수 있는데도
            # 아무것도 못 건지던 자리다.
            # 두 경로는 같은 pyhwp 파서를 공유한다. 둘 다 실패했다면 원인이
            # 같을 가능성이 높으므로, 먼저 죽은 쪽(hwp5-tree)의 사유를 함께
            # 싣는다 — 그쪽이 진짜 원인에 가깝다.
            first = self.tree_failure
            self._fallback_reason = (
                f"hwp5html 실행 실패 — {_first_real_error(exc)}"
                + (f" / 앞서 hwp5-tree 도 실패: {first}" if first else "")
            )
            _log.warning(
                "hwp5html 이 실패해 olefile 폴백으로 내려갑니다 "
                "(표·그림 구조 손실): %s", self._fallback_reason,
            )
            self._ole_fallback = True
            return True
        self._html_cache = html
        self._html_stderr = stderr
        file_size = os.path.getsize(self.path)
        insufficient, reason = _backend().html_verdict(html, stderr, file_size)
        self._fallback_reason = reason
        if insufficient:
            if OLEFILE_AVAILABLE:
                _log.warning(
                    "pyhwp HTML 불충분 — olefile 폴백 사용 (표/그림 구조 손실): %s",
                    reason,
                )
                self._ole_fallback = True
            else:
                _log.warning(
                    "pyhwp HTML 불충분이나 olefile 미설치 — HTML 결과 사용: %s",
                    reason,
                )
                self._ole_fallback = False
        else:
            _log.debug("pyhwp HTML 사용: %s", reason)
            self._ole_fallback = False
        return self._ole_fallback

    @property
    def fallback_reason(self) -> str | None:
        """폴백 판정 사유.

        입력: 없음
        출력: 판정 문구. 아직 판정하지 않았으면 None
        비고: 트레이스·리포트에 남겨 왜 그 경로를 골랐는지 드러낸다.
        """
        return getattr(self, "_fallback_reason", None)

    def _get_html(self) -> str:
        """pyhwp 로 변환한 HTML 을 얻는다 (최초 1회 변환 후 재사용).

        입력: 없음
        출력: HTML 문자열
        """
        if self._uses_ole_fallback():
            return text_to_html(self._get_ole_text())
        if self._html_cache is None:
            backend = _backend()
            if backend is None:
                raise RuntimeError(
                    "pyhwp 백엔드가 설치본에 없습니다 "
                    "(converters/hwp/pyhwp_backend/ 를 떼어낸 상태)")
            self._html_cache, self._html_stderr = backend.html(self.path)
        return self._html_cache

    def _get_ole_text(self) -> str:
        """olefile 로 원문 텍스트를 추출한다 (최초 1회 후 재사용).

        입력: 없음
        출력: 텍스트 문자열
        """
        if self._ole_text_cache is None:
            if not OLEFILE_AVAILABLE:
                raise ImportError("olefile 패키지를 설치하세요: pip install olefile")
            try:
                self._ole_text_cache = clean_text(extract_raw_text(self.path))
            except Exception as exc:             # noqa: BLE001 - 아래에 한 단이 더 있다
                # **마지막 단이 아니다** (0.5.1). olefile 이 컨테이너 자체를
                # 못 여는 문서(손상·부분 전송)가 있는데, 예전에는 그 예외가
                # 그대로 위로 튀어 문서가 통째로 실패했다. 미리보기 스트림
                # (PrvText)에는 본문 일부가 남아 있는 경우가 많다 — 그것이
                # 진짜 마지막 단이다.
                # `_fallback_reason` 은 아직 설정되지 않았을 수 있다 —
                # 이 경로로 곧장 들어오는 호출부가 있다.
                before = getattr(self, "_fallback_reason", None)
                self._fallback_reason = (
                    (before + " / " if before else "")
                    + f"olefile 이 파일을 열지 못했습니다 ({type(exc).__name__}: {exc})"
                )
                _log.warning("olefile 실패 — 미리보기 스트림으로 내려갑니다: %s", exc)
                self._ole_text_cache = self._preview_text()
        return self._ole_text_cache

    def _preview_text(self) -> str:
        """사다리 마지막 단 — 미리보기 스트림(PrvText).

        입력: 없음
        출력: 텍스트. 그것마저 없으면 빈 문자열
        비고:
            **여기서는 예외를 내지 않는다.** 사다리의 끝이므로 더 물러날
            곳이 없다. 빈 결과는 파이프라인이 "내용이 사실상 비었습니다"
            경고로 알린다 — 예외로 문서를 통째로 잃는 것보다 낫다.
        """
        try:
            from docstruct.converters.hwp.preview import read_prv_text

            return read_prv_text(self.path) or ""
        except Exception as exc:                 # noqa: BLE001 - 끝이다
            _log.warning("미리보기 스트림도 읽지 못했습니다: %s", exc)
            return ""

    def to_html(self) -> str:
        """본문을 HTML 로 변환한다.

        입력: 없음
        출력: HTML 문자열
        """
        if is_hwpml(self.path):
            return hwpml_to_html(self.path)
        return self._get_html()

    def to_text(self) -> str:
        """본문을 평문으로 변환한다.

        입력: 없음
        출력: 텍스트 문자열
        """
        if is_hwpml(self.path):
            return hwpml_to_text(self.path)
        if self._uses_ole_fallback():
            return self._get_ole_text()
        if deps.PYHWP_AVAILABLE:
            return html_to_text(self._get_html())
        print("[경고] pyhwp 없음 — olefile 폴백 (표/그림 구조 손실)", file=sys.stderr)
        return self._get_ole_text()

    def to_markdown(self) -> str:
        """본문을 markdown 으로 변환한다.

        입력: 없음
        출력: markdown 문자열
        """
        try:
            return _pipeline_markdown(self.path)
        except Exception as exc:                 # noqa: BLE001 - 폴백이 있다
            _log.warning("파이프라인 markdown 실패 — 원재료로 물러납니다: %s", exc)

        if is_hwpml(self.path):
            return hwpml_to_markdown(self.path)
        report = diagnose(self.path)
        if not report.readable:
            # 조용히 빈 결과를 내면 배치에서 "성공했는데 내용 없음" 이 된다.
            raise ValueError(f"{Path(self.path).name}: {report.reason}")
        tree = self._get_tree_markdown()
        if tree is not None:
            return tree
        if self._uses_ole_fallback():
            return text_to_markdown(self._get_ole_text())
        return html_to_markdown(self._get_html())

    def table_html_fragments(self) -> list[str]:
        """원본 `<table>` HTML 조각을 문서 순서로 얻는다.

        입력: 없음
        출력: HTML 문자열 목록. 근거를 만들 수 없으면 빈 목록
        비고:
            페이지 이미지가 없는 HWP 에서 표 재추출의 근거로 쓴다.

            hwp5-tree 로 이미 잘 뽑았다면 기본적으로는 빈 목록을 준다 —
            근거를 만들려고 hwp5html 을 한 번 더 돌리는 비용(문서당 수십 초)
            이 크기 때문이다. 그런데 그 때문에 **hwp5-tree 로 성공한 문서는
            표 재추출을 아예 할 수 없었다.** 판정에서 insufficient 가 나와도
            고칠 방법이 없었다는 뜻이다.

            정확성이 속도보다 중요한 작업에서는 `hwp_fill_html=True` 로
            켜면 hwp5-tree 경로에서도 hwp5html 을 추가로 돌려 근거를 만든다.
        """
        if is_hwpml(self.path):
            return []
        if self._get_tree_markdown() is not None and not get_settings().hwp_fill_html:
            return []
        if self._uses_ole_fallback():
            return []
        if not BS4_AVAILABLE:
            return []
        try:
            html = self._get_html()
        except Exception as exc:                 # noqa: BLE001 - 근거가 없을 뿐, 추출은 이미 끝났다
            _log.warning(
                "표 재추출 근거용 HTML 을 얻지 못했습니다 (추출 결과에는 영향 없음): %s",
                exc,
            )
            return []
        soup = BeautifulSoup(html, "html.parser")
        return [str(table) for table in soup.find_all("table")]

    def to_xml(self) -> str:
        """본문을 XML 로 변환한다.

        입력: 없음
        출력: XML 문자열
        """
        if is_hwpml(self.path):
            return hwpml_to_xml(self.path)
        if self._uses_ole_fallback():
            return text_to_xml(self._get_ole_text())
        return html_to_xml(self._get_html())

    def save(self, output_path: str | Path, fmt: str | None = None) -> None:
        """변환 결과를 파일로 저장하고 완료를 알린다.

        입력: output_path — 저장 경로, fmt — 형식 (None 이면 확장자로 추정)
        출력: 없음 (파일 기록 + stdout 안내)
        """
        super().save(output_path, fmt)
        print(f"저장 완료: {output_path}  ({fmt or 'auto'})")
