"""라이브러리 공개 API.

역할:
    파일명과 설정을 받아 문서를 구조화하고 JSON 으로 내보내는 단일 진입점.
    설정은 get()/set() 으로 다루며, 인스턴스마다 독립적으로 관리되고
    run() 실행 동안에만 전역 설정에 적용된 뒤 원래대로 되돌아간다.
호출부:
    외부 사용자 코드, 노트북, 다른 서비스
출력:
    DocStruct.to_json() 이 저장한 JSON 파일 경로,
    또는 to_dict() 가 반환한 구조화 결과 dict

사용 예::

    from docstruct import DocStruct

    ds = DocStruct("보고서.pdf")
    ds.set(device="cuda", llm_concurrency=8)
    ds.run()
    ds.to_json("결과.json")

    # 한 줄로
    from docstruct import structure
    result = structure("보고서.pdf", assess_tables=False)

실행 격리:
    프로세스가 다르면 설정이 서로 영향을 주지 않는다 (환경변수가 프로세스마다
    별도이므로 접속 세션이 달라도 무관하다). 같은 프로세스에서 여러 스레드가
    동시에 run() 하면 설정 교체 구간이 락으로 직렬화된다.

    다만 다음은 프로세스가 달라도 공유되므로 주의한다.
      - 출력 디렉터리   같은 경로로 저장하면 서로 덮어쓴다 (save(unique=True) 로 회피)
      - 모델 캐시       첫 실행 시 Docling 모델을 내려받는데, 동시에 시작하면
                        같은 캐시 경로를 두고 경쟁할 수 있다 (한 번 받아두면 무관)
      - GPU 메모리      여러 프로세스가 같은 장치를 쓰면 메모리를 나눠 쓴다
      - LLM 사용량 한도 동시 실행 수만큼 원격 호출이 늘어 429 가 날 수 있다
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from docstruct.models import PageDocument

_log = logging.getLogger(__name__)


class DocStructError(Exception):
    """API 사용 오류 (잘못된 설정 키, 실행 순서 위반 등)."""


#: 설정 키 → 환경변수. run() 동안에만 적용된다.
_ENV_KEYS: dict[str, str] = {
    # LLM (표 평가·재추출)
    "llm_url": "DOCLING_TABLE_API_URL",
    "llm_model": "DOCLING_TABLE_API_MODEL",
    "llm_key": "DOCLING_TABLE_API_KEY",
    "llm_timeout": "DOCLING_TABLE_API_TIMEOUT",
    "llm_concurrency": "DOCLING_LLM_CONCURRENCY",
    # 기본 LLM 에 연결이 안 될 때 쓸 대비책
    "fallback_url": "DOCLING_TABLE_API_FALLBACK_URL",
    "fallback_model": "DOCLING_TABLE_API_FALLBACK_MODEL",
    "fallback_key": "DOCLING_TABLE_API_FALLBACK_KEY",
    "fallback_timeout": "DOCLING_TABLE_API_FALLBACK_TIMEOUT",
    "fallback_enabled": "DOCLING_TABLE_API_FALLBACK",
    "openai_key": "OPENAI_API_KEY",
    # 그림 설명 VLM
    "picture_url": "DOCLING_PICTURE_API_URL",
    "picture_model": "DOCLING_PICTURE_API_MODEL",
    "picture_key": "DOCLING_PICTURE_API_KEY",
    "picture_enabled": "DOCLING_PICTURE_API",
    "picture_area_threshold": "DOCLING_PICTURE_AREA_THRESHOLD",
    # PDF 파싱
    "pdf_backend": "DOCLING_PDF_BACKEND",
    "ocr_backend": "DOCLING_OCR_BACKEND",
    "ocr_lang": "DOCLING_OCR_LANG",
    "force_full_page_ocr": "DOCLING_FORCE_FULL_PAGE_OCR",
    "generate_parsed_pages": "DOCLING_GENERATE_PARSED_PAGES",
    "code_formula_enrichment": "DOCLING_CODE_FORMULA_ENRICHMENT",
    # 성능
    "device": "DOCLING_DEVICE",
    "num_threads": "DOCLING_NUM_THREADS",
    "rapidocr_runtime": "DOCLING_RAPIDOCR_RUNTIME",
    "threaded_pipeline": "DOCLING_THREADED_PIPELINE",
}

#: 실행 옵션 → 기본값. build_document 에 그대로 전달된다.
_RUN_KEYS: dict[str, Any] = {
    "assess_tables": True,
    "fill_tables": True,
    "fill_all": False,
    "render_pages": True,
    "render_scale": 2.0,
    "out_dir": None,
    "progress": False,
}


#: 값이 노출되면 안 되는 설정 키.
_SECRET_KEYS = frozenset({"llm_key", "picture_key", "fallback_key", "openai_key"})


def mask(value: str) -> str:
    """비밀값을 표시용으로 가린다.

    입력: value — 원본 문자열
    출력: `sk-abc…7890` 형태. 짧으면 `(설정됨)`
    """
    if not value:
        return "(없음)"
    return f"{value[:6]}…{value[-4:]}" if len(value) > 12 else "(설정됨)"


def set_api_key(key: str, *, target: str = "fallback") -> None:
    """API 키를 이 프로세스 전체에 설정한다.

    입력:
        key     API 키
        target  fallback — 연결 실패 시 쓰는 대비 엔드포인트 (기본)
                llm      — 기본 LLM 엔드포인트
                picture  — 그림 설명 VLM
    출력: 없음
    예외: 알 수 없는 target 이면 DocStructError

    비고:
        키를 소스나 저장소에 두지 않고 실행 시점에 넣기 위한 함수다.
        이후 만드는 DocStruct 인스턴스에 모두 적용된다.

    사용 예::

        import docstruct, getpass
        docstruct.set_api_key(getpass.getpass("OpenAI 키: "))
    """
    mapping = {
        "fallback": "OPENAI_API_KEY",
        "llm": "DOCLING_TABLE_API_KEY",
        "picture": "DOCLING_PICTURE_API_KEY",
    }
    if target not in mapping:
        raise DocStructError(
            f"알 수 없는 target: {target!r} (가능: {', '.join(mapping)})"
        )

    key = (key or "").strip()
    if not key:
        raise DocStructError("빈 키는 설정할 수 없습니다.")

    os.environ[mapping[target]] = key
    _refresh_settings()
    _log.info("%s 키 설정됨 — %s", target, mask(key))


def configure(**options: Any) -> dict[str, Any]:
    """설정을 이 프로세스 전체에 적용한다.

    입력: options — DocStruct.set() 과 같은 키
    출력: 적용된 설정 (비밀값은 가려짐)
    예외: 알 수 없는 키면 DocStructError

    비고:
        DocStruct.set() 은 그 인스턴스에만, run() 동안에만 적용된다.
        이 함수는 프로세스 전역에 남으므로 노트북에서 한 번 설정해 두고
        여러 문서를 처리할 때 쓴다.

    사용 예::

        import docstruct
        docstruct.configure(
            openai_key="sk-...",
            llm_url="http://내부주소:포트/v1",
            llm_concurrency=8,
        )
    """
    applied: dict[str, Any] = {}
    for name, value in options.items():
        if name not in _ENV_KEYS:
            raise DocStructError(
                f"알 수 없는 설정 키: {name!r}\n"
                f"사용 가능: {', '.join(sorted(_ENV_KEYS))}"
            )
        os.environ[_ENV_KEYS[name]] = _as_env_value(value)
        applied[name] = mask(str(value)) if name in _SECRET_KEYS else value

    _refresh_settings()
    return applied


def _refresh_settings() -> None:
    """전역 설정과 캐시를 새 환경변수로 갱신한다.

    입력: 없음
    출력: 없음
    """
    from docstruct.core.config import rebuild_settings
    from docstruct.checks import invalidate_caches

    rebuild_settings()
    invalidate_caches()


def defaults() -> dict[str, Any]:
    """내장 기본값을 설정 키 이름으로 돌려준다.

    입력: 없음
    출력: {설정 키: 기본값} — set() 으로 덮을 수 있는 항목만
    비고:
        설치 직후 별도 설정 없이 동작하도록 사내 공용 엔드포인트가
        기본값으로 들어 있다. 환경변수·.env·set() 이 모두 우선한다.
    """
    from docstruct.core.config import defaults as _env_defaults

    env = _env_defaults()
    rev = {v: k for k, v in _ENV_KEYS.items()}
    return {rev[k]: v for k, v in env.items() if k in rev}


def option_keys() -> tuple[str, ...]:
    """설정할 수 있는 키 전체.

    입력: 없음
    출력: 정렬된 키 이름 튜플
    """
    return tuple(sorted({*_ENV_KEYS, *_RUN_KEYS}))


def _as_env_value(value: Any) -> str:
    """설정값을 환경변수 문자열로 바꾼다.

    입력: value — 불리언·숫자·문자열
    출력: 환경변수에 넣을 문자열 (불리언은 'true'/'false')
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


#: 설정 교체 구간을 보호하는 락.
#: 하위 모듈이 전역 설정(core.config)과 Docling·LLM 캐시를 공유하므로,
#: 같은 프로세스에서 두 run() 이 겹치면 서로의 설정을 덮어쓴다.
#: 프로세스가 다르면(별도 세션·별도 실행) os.environ 이 분리되어 무관하다.
_RUN_LOCK = threading.RLock()


@contextmanager
def _applied(env_overrides: dict[str, str]) -> Iterator[None]:
    """설정을 잠시 적용했다가 되돌린다.

    입력: env_overrides — {환경변수: 값}
    출력: 없음 (컨텍스트 매니저)
    비고:
        전역 설정과 Docling·LLM 캐시를 함께 갱신하고, 블록을 벗어나면
        원래 값으로 복원한다. 같은 프로세스에서 동시에 진입하면 락으로
        직렬화되므로 설정이 섞이지 않는다 (뒤에 온 쪽이 기다린다).
        run() 안의 LLM 병렬 호출은 이 락과 무관하게 그대로 동작한다.
    """
    from docstruct.core.config import rebuild_settings
    from docstruct.checks import invalidate_caches

    if not env_overrides:
        yield
        return

    with _RUN_LOCK:
        saved = {k: os.environ.get(k) for k in env_overrides}
        try:
            os.environ.update(env_overrides)
            rebuild_settings()
            invalidate_caches()
            yield
        finally:
            for key, old in saved.items():
                if old is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old
            rebuild_settings()
            invalidate_caches()


class _SettingsMixin:
    """설정 관리 공통부.

    역할:
        DocStruct 와 DocStructBatch 가 똑같이 갖는 설정 관리(set/get/options/
        reset)를 한 곳에 둔다. 두 클래스는 **결과의 모양이 달라서** 나뉘어
        있을 뿐, 설정을 다루는 방식은 같다.
    호출부:
        DocStruct, DocStructBatch
    출력:
        _options 딕셔너리를 갱신·조회
    """

    _options: dict[str, Any]

    def set(self, key: str | None = None, value: Any = None, **options: Any):
        """설정값을 지정한다.

        입력: key/value 또는 키워드 인자
        출력: self (연쇄 호출 가능)
        예외: 알 수 없는 키면 DocStructError
        """
        if key is not None:
            options = {key: value, **options}
        for name, val in options.items():
            if name == "source":
                self._set_source(val)
                continue
            if name not in _ENV_KEYS and name not in _RUN_KEYS:
                raise DocStructError(
                    f"알 수 없는 설정 키: {name!r}\n"
                    f"사용 가능: {', '.join(option_keys())}"
                )
            self._options[name] = val
        return self

    def get(self, key: str, default: Any = None) -> Any:
        """설정값을 읽는다.

        입력: key — 설정 키, default — 지정하지 않았을 때 값
        출력: 지정값 → 실행 옵션 기본값 → default 순
        예외: 알 수 없는 키면 DocStructError
        """
        if key == "source":
            return self._get_source(default)
        if key not in _ENV_KEYS and key not in _RUN_KEYS:
            raise DocStructError(
                f"알 수 없는 설정 키: {key!r}\n사용 가능: {', '.join(option_keys())}"
            )
        if key in self._options:
            return self._options[key]
        if key in _RUN_KEYS:
            return _RUN_KEYS[key]
        return default

    def options(self) -> dict[str, Any]:
        """지정한 설정 전체.

        입력: 없음
        출력: {키: 값} — 명시적으로 set 한 것만
        """
        return dict(self._options)

    def _env_overrides(self) -> dict[str, str]:
        """지정한 설정 중 환경변수로 넘길 것.

        입력: 없음
        출력: {환경변수명: 문자열 값}
        """
        return {
            _ENV_KEYS[name]: _as_env_value(val)
            for name, val in self._options.items()
            if name in _ENV_KEYS
        }

    def _run_kwargs(self) -> dict[str, Any]:
        """build_document 에 넘길 실행 옵션.

        입력: 없음
        출력: {옵션명: 값}
        """
        return {name: self.get(name) for name in _RUN_KEYS}

    # 아래 둘은 하위 클래스가 구현한다 (source 의 의미가 다르므로).
    def _set_source(self, value: Any) -> None:
        raise NotImplementedError

    def _get_source(self, default: Any) -> Any:
        raise NotImplementedError


class DocStruct(_SettingsMixin):
    """문서 구조화 진입점.

    입력(생성자):
        source   문서 경로 (.pdf | .hwp | .hwpx). 나중에 set(source=...) 로도 지정 가능
        options  설정값. option_keys() 로 목록을 볼 수 있다
    출력:
        run() 후 document 속성에 PageDocument,
        to_json() 으로 JSON 파일, to_dict() 로 dict
    """

    def __init__(self, source: str | Path | None = None, **options: Any) -> None:
        self._source: Path | None = Path(source).expanduser() if source else None
        self._options: dict[str, Any] = {}
        self._document: PageDocument | None = None
        if options:
            self.set(**options)

    # ── 설정 -----------------------------------------------------------



    def _set_source(self, value: Any) -> None:
        """처리 대상을 바꾼다 (set(source=...) 경유).

        입력: value — 문서 경로. None 이면 해제
        출력: 없음
        """
        self._source = Path(value).expanduser() if value else None

    def _get_source(self, default: Any) -> Any:
        """현재 대상 경로.

        입력: default — 미지정 시 돌려줄 값
        출력: 경로 문자열 또는 default
        """
        return str(self._source) if self._source else default

    def reset(self) -> "DocStruct":
        """설정과 실행 결과를 모두 지운다.

        입력: 없음
        출력: self
        """
        self._options.clear()
        self._document = None
        return self

    # ── 실행 -----------------------------------------------------------
    def run(self) -> "DocStruct":
        """문서를 구조화한다.

        입력: 없음 (생성자·set() 으로 지정한 source 와 설정 사용)
        출력: self (document 속성에 결과가 채워진다)
        예외:
            source 미지정 시 DocStructError
            파일이 없으면 FileNotFoundError
            지원하지 않는 형식이면 ValueError
        """
        if self._source is None:
            raise DocStructError("문서 경로가 없습니다 — DocStruct('파일.pdf') 또는 set(source=...)")

        from docstruct.pipeline import build_document

        with _applied(self._env_overrides()):
            self._document = build_document(self._source, **self._run_kwargs())
        return self

    @classmethod
    def from_document(
        cls, doc: PageDocument, *, source: str | Path | None = None, **options: Any
    ) -> "DocStruct":
        """이미 만들어진 PageDocument 를 감싼다 (실행 없이 결과만 다룰 때).

        입력:
            doc      구조화 결과
            source   원본 경로. 생략하면 doc.filename
            options  설정 (저장 경로 계산 등에만 쓰임)
        출력: run() 을 마친 것과 같은 상태의 DocStruct
        비고:
            배치 결과를 문서별로 저장하거나, 저장해 둔 결과를 다시 다룰 때
            쓴다. 이 경로가 없으면 호출부가 비공개 필드를 직접 건드리게 된다.
        """
        obj = cls(source or doc.filename, **options)
        obj._document = doc
        return obj

    # ── 결과 -----------------------------------------------------------
    @property
    def document(self) -> PageDocument:
        """구조화 결과.

        입력: 없음
        출력: PageDocument
        예외: run() 전이면 DocStructError
        """
        if self._document is None:
            raise DocStructError("아직 실행하지 않았습니다 — run() 을 먼저 호출하세요.")
        return self._document

    @property
    def pages(self) -> list:
        """페이지 목록.

        입력: 없음
        출력: list[PageContent]
        """
        return self.document.pages

    @property
    def tables(self) -> list:
        """문서 전체의 표.

        입력: 없음
        출력: list[TableInfo]
        """
        return [t for p in self.document.pages for t in p.tables]

    def to_dict(self) -> dict[str, Any]:
        """구조화 결과를 dict 로 얻는다.

        입력: 없음
        출력: document.json 과 같은 구조의 dict
        """
        return self.document.to_dict()

    def to_json_str(self, *, indent: int = 2) -> str:
        """구조화 결과를 JSON 문자열로 얻는다 (파일 저장 없음).

        입력: indent — 들여쓰기 칸 수. None 이면 한 줄로 압축
        출력: JSON 문자열
        비고:
            파이썬 자료구조로 다루려면 to_dict(), 파일로 쓰려면 to_json() 을
            쓴다. 이 메서드는 HTTP 응답 본문이나 로그처럼 문자열이 필요할 때
            쓴다.
        """
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def to_json(self, path: str | Path | None = None, *, indent: int = 2) -> Path:
        """구조화 결과를 JSON 파일로 저장한다.

        입력:
            path    저장 경로. 생략하면 원본 파일명 옆에 <문서명>.json
            indent  들여쓰기 칸 수
        출력: 저장된 Path (내용이 아니라 **경로**)
        비고: 내용이 필요하면 to_dict() 또는 to_json_str() 을 쓴다.
        """
        if path is None:
            base = self._source or Path("document")
            path = base.with_suffix(".json")
        path = Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json_str(indent=indent), encoding="utf-8")
        _log.info("JSON 저장: %s", path)
        return path

    def save(self, out_dir: str | Path, *, unique: bool = False) -> dict[str, Path]:
        """모든 산출물을 저장한다.

        입력:
            out_dir  저장 디렉터리
            unique   True 면 디렉터리 이름에 PID 와 시각을 붙여 충돌을 피한다.
                     여러 사람이 같은 서버에서 같은 경로로 저장할 때 쓴다.
        출력:
            {이름: 경로} — document(.json), markdown(.md), tables(.md),
            pipeline(.md), layout(.md)
        """
        from docstruct.report import (
            write_json,
            write_layout_report,
            write_markdown,
            write_pipeline_report,
            write_tables_report,
        )

        out = Path(out_dir).expanduser()
        if unique:
            # mkdtemp 은 디렉터리 생성을 원자적으로 처리하므로, 같은 순간에
            # 여러 프로세스가 호출해도 서로 다른 경로를 받는다.
            out.mkdir(parents=True, exist_ok=True)
            out = Path(
                tempfile.mkdtemp(
                    prefix=f"{time.strftime('%Y%m%d-%H%M%S')}_{os.getpid()}_",
                    dir=out,
                )
            )
        else:
            out.mkdir(parents=True, exist_ok=True)
        doc = self.document
        return {
            "document": write_json(doc, out / "document.json"),
            "markdown": write_markdown(doc, out / "document.md"),
            "tables": write_tables_report(doc, out / "tables.md"),
            "pipeline": write_pipeline_report(doc, out / "pipeline.md"),
            "layout": write_layout_report(doc, out / "layout.md"),
        }

    def summary(self) -> list[str]:
        """콘솔용 요약.

        입력: 없음
        출력: 문자열 목록 (페이지 수·표·이미지·소요 시간 등)
        """
        from docstruct.report import summary_lines

        return summary_lines(self.document)

    def __repr__(self) -> str:
        state = "실행 전" if self._document is None else f"{len(self._document.pages)}페이지"
        name = self._source.name if self._source else "(경로 없음)"
        return f"<DocStruct {name} · {state} · 설정 {len(self._options)}개>"


class DocStructBatch(_SettingsMixin):
    """여러 문서를 한 번에 구조화한다.

    입력(생성자):
        sources  다음 중 하나
                   - 디렉터리 경로 (pattern 으로 걸러냄)
                   - 파일 경로 목록
                   - glob 문자열 (예: "docs/*.pdf")
        pattern  디렉터리를 줬을 때 적용할 glob (기본 "*")
        options  DocStruct 와 동일한 설정
    출력:
        run() 후 documents 에 PageDocument 목록,
        to_json() 으로 문서별 JSON, failures 에 실패 목록

    사용 예::

        batch = DocStructBatch("문서모음/", pattern="*.pdf", progress=True)
        batch.set(device="cuda")
        batch.run()
        batch.to_json("결과/")
        print(batch.failures)
    """

    def __init__(
        self,
        sources: str | Path | Iterable[str | Path],
        *,
        pattern: str = "*",
        **options: Any,
    ) -> None:
        self._paths = _resolve_sources(sources, pattern)
        self._options = dict(options)
        self._documents: list[PageDocument] = []
        self._failures: list[tuple[Path, Exception]] = []
        # 설정 검증은 DocStruct 에 위임한다 (잘못된 키를 여기서 바로 잡는다).
        DocStruct(**self._options)

    # ── 설정 -----------------------------------------------------------



    def _set_source(self, value: Any) -> None:
        """대상 목록을 바꾼다 (set(source=...) 경유)."""
        self._paths = _resolve_sources(value, "*")

    def _get_source(self, default: Any) -> Any:
        """현재 대상 파일 목록."""
        return [str(p) for p in self._paths] or default

    def reset(self) -> "DocStructBatch":
        """설정과 실행 결과를 지운다 (대상 파일 목록은 유지).

        입력: 없음
        출력: self
        """
        self._options.clear()
        self._documents = []
        self._failures = []
        return self

    # ── 실행 -----------------------------------------------------------
    @property
    def paths(self) -> list[Path]:
        """처리 대상 파일 목록.

        입력: 없음
        출력: Path 목록 (정렬됨)
        """
        return list(self._paths)

    def run(self, *, stop_on_error: bool = False) -> "DocStructBatch":
        """모든 문서를 순서대로 구조화한다.

        입력:
            stop_on_error  True 면 첫 실패에서 중단. False 면 실패를 모아
                           failures 에 담고 계속 진행한다
        출력: self
        비고:
            문서 단위 진행은 options 의 progress 설정을 따른다.
            문서 하나 안의 단계별 진행도 같은 설정으로 표시된다.
        """
        from docstruct.progress import ProgressBar

        show = bool(self._options.get("progress", False))
        self._documents = []
        self._failures = []

        bar = ProgressBar(len(self._paths), "문서 처리", unit="건", enabled=show)
        try:
            for path in self._paths:
                bar.update(0, path.name)
                try:
                    ds = DocStruct(path, **self._options).run()
                    self._documents.append(ds.document)
                except Exception as exc:
                    _log.warning("%s 처리 실패: %s", path.name, exc)
                    self._failures.append((path, exc))
                    if stop_on_error:
                        raise
                bar.update(1, path.name)
        finally:
            bar.close()

        if self._failures:
            _log.warning(
                "%d건 중 %d건 실패", len(self._paths), len(self._failures)
            )
        return self

    # ── 결과 -----------------------------------------------------------
    @property
    def documents(self) -> list[PageDocument]:
        """성공한 문서들의 구조화 결과.

        입력: 없음
        출력: PageDocument 목록
        """
        return list(self._documents)

    @property
    def failures(self) -> list[tuple[Path, Exception]]:
        """실패한 문서와 원인.

        입력: 없음
        출력: (경로, 예외) 목록
        """
        return list(self._failures)

    def to_dict(self) -> dict[str, Any]:
        """전체 결과를 dict 로 얻는다.

        입력: 없음
        출력:
            {total, succeeded, failed, documents[], failures[]}
        """
        return {
            "total": len(self._paths),
            "succeeded": len(self._documents),
            "failed": len(self._failures),
            "documents": [d.to_dict() for d in self._documents],
            "failures": [
                {"file": str(p), "error": f"{type(e).__name__}: {e}"}
                for p, e in self._failures
            ],
        }

    def to_json(
        self, out: str | Path, *, combined: bool = False, indent: int = 2
    ) -> list[Path] | Path:
        """결과를 JSON 으로 저장한다.

        입력:
            out       combined=False 면 디렉터리, True 면 파일 경로
            combined  True 면 전체를 파일 하나에 담는다
            indent    들여쓰기 칸 수
        출력:
            combined=False 면 저장된 Path 목록, True 면 Path 하나
        """
        out = Path(out).expanduser()

        if combined:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps(self.to_dict(), ensure_ascii=False, indent=indent),
                encoding="utf-8",
            )
            _log.info("JSON 저장: %s (%d건)", out, len(self._documents))
            return out

        out.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for doc in self._documents:
            path = out / f"{Path(doc.filename).stem}.json"
            path.write_text(
                json.dumps(doc.to_dict(), ensure_ascii=False, indent=indent),
                encoding="utf-8",
            )
            written.append(path)
        _log.info("JSON 저장: %s 아래 %d건", out, len(written))
        return written

    def to_json_str(self, *, indent: int = 2) -> str:
        """전체 결과를 JSON 문자열로 얻는다 (파일 저장 없음).

        입력: indent — 들여쓰기 칸 수. None 이면 한 줄로 압축
        출력: JSON 문자열 (to_dict() 와 같은 구조)
        """
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def save(self, out_dir: str | Path, *, unique: bool = False) -> dict[str, list[Path]]:
        """문서별로 산출물 전체를 저장한다.

        입력:
            out_dir  저장 디렉터리. 문서마다 하위 폴더가 생긴다
            unique   True 면 실행마다 별도 폴더를 만들어 충돌을 피한다
        출력: {문서명: [저장된 경로]}
        비고: DocStruct.save() 를 문서마다 호출한다 (json + md 4종).
        """
        out = Path(out_dir).expanduser()
        if unique:
            out.mkdir(parents=True, exist_ok=True)
            out = Path(
                tempfile.mkdtemp(
                    prefix=f"{time.strftime('%Y%m%d-%H%M%S')}_{os.getpid()}_", dir=out
                )
            )

        written: dict[str, list[Path]] = {}
        for doc in self._documents:
            stem = Path(doc.filename).stem
            holder = DocStruct.from_document(doc, **self._options)
            written[stem] = list(holder.save(out / stem).values())
        _log.info("산출물 저장: %s 아래 %d건", out, len(written))
        return written

    def summary(self) -> list[str]:
        """배치 처리 요약.

        입력: 없음
        출력: 문자열 목록 (성공·실패 건수와 실패 사유)
        """
        lines = [
            f"대상       : {len(self._paths)}건",
            f"성공       : {len(self._documents)}건",
            f"실패       : {len(self._failures)}건",
        ]
        for path, exc in self._failures:
            lines.append(f"  ✘ {path.name} — {type(exc).__name__}: {exc}")
        return lines

    def __len__(self) -> int:
        return len(self._paths)

    def __repr__(self) -> str:
        state = "실행 전" if not self._documents and not self._failures else (
            f"성공 {len(self._documents)} · 실패 {len(self._failures)}"
        )
        return f"<DocStructBatch {len(self._paths)}건 · {state}>"


def _resolve_sources(
    sources: str | Path | Iterable[str | Path], pattern: str
) -> list[Path]:
    """입력을 처리 대상 파일 목록으로 바꾼다.

    입력:
        sources  디렉터리 · glob 문자열 · 경로 목록
        pattern  디렉터리일 때 적용할 glob
    출력: 지원 확장자에 해당하는 Path 목록 (정렬됨)
    예외: 대상이 없으면 DocStructError
    """
    from docstruct.pipeline import SUPPORTED_SUFFIXES

    found: list[Path] = []

    if isinstance(sources, (str, Path)):
        path = Path(sources).expanduser()
        if path.is_dir():
            found = [f for f in path.glob(pattern) if f.is_file()]
        elif path.is_file():
            found = [path]
        else:
            # glob 문자열로 해석
            base = Path(path.anchor or ".")
            rel = str(path.relative_to(path.anchor)) if path.anchor else str(path)
            found = [f for f in base.glob(rel) if f.is_file()]
    else:
        found = [Path(s).expanduser() for s in sources]

    usable = sorted({f.resolve() for f in found if f.suffix.lower() in SUPPORTED_SUFFIXES})
    if not usable:
        raise DocStructError(
            f"처리할 문서가 없습니다: {sources!r} (패턴={pattern!r}, "
            f"지원={', '.join(SUPPORTED_SUFFIXES)})"
        )
    return usable


def structure(source: str | Path, **options: Any) -> dict[str, Any]:
    """문서 하나를 구조화해 dict 로 돌려준다 (한 줄 사용).

    입력: source — 문서 경로, options — DocStruct 와 동일한 설정
    출력: document.json 과 같은 구조의 dict
    """
    return DocStruct(source, **options).run().to_dict()


def structure_to_json(
    source: str | Path, out_path: str | Path | None = None, **options: Any
) -> Path:
    """문서 하나를 구조화해 JSON 파일로 저장한다 (한 줄 사용).

    입력:
        source    문서 경로
        out_path  저장 경로. 생략하면 원본 옆에 <문서명>.json
        options   DocStruct 와 동일한 설정
    출력: 저장된 Path
    """
    return DocStruct(source, **options).run().to_json(out_path)
