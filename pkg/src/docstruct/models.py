"""문서 구조화 결과를 담는 데이터 모델.

역할:
    파싱 결과(본문·표·이미지·처리이력)를 보관하는 순수 데이터 컨테이너.
    로직을 갖지 않으며, 직렬화(to_dict)와 파생 속성만 제공한다.
호출부:
    docstruct.extractors.*   객체 생성
    docstruct.pipeline       조립·상태 갱신
    docstruct.tables.*       표 판정·재추출 결과 기록
    docstruct.report/preview 출력
출력:
    PageDocument / PageContent / TableInfo / ImageInfo / PageTrace / TraceStep
    각 클래스의 to_dict() 는 document.json 의 스키마가 된다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# content_type
TABLE = "table"
TEXT = "text"
IMAGE = "image"

# quality
SUFFICIENT = "sufficient"
WRONG = "wrong"
INSUFFICIENT = "insufficient"

#: LLM 재추출이 필요한 품질 등급
NEEDS_FILL = frozenset({WRONG, INSUFFICIENT})


@dataclass
class TableInfo:
    """표 하나의 파싱 결과와 LLM 판정 상태.

    입력(필드):
        id, table_num, placeholder  식별자와 본문 내 `<table N>` 태그
        markdown                    현재 표 내용 (GFM)
        original_markdown           재추출 전 원본 (없으면 None)
        content_type / quality      LLM 판정 결과
        llm_title / reason          판정 부가 정보
        group_image_ids             이미지로 묶일 표 id 목록
    출력(파생):
        needs_fill  재추출 대상 여부
        was_filled  재추출로 내용이 바뀌었는지
    """

    id: str
    table_num: int
    placeholder: str          # `<table N>` 여는 태그
    markdown: str             # 현재 markdown (fill 후에는 LLM 결과)
    bbox: dict[str, float] | None = None      # PDF 페이지 좌표(TOPLEFT, points)
    llm_title: str | None = None
    content_type: str | None = None           # table | text | image
    quality: str | None = None                # sufficient | wrong | insufficient
    original_markdown: str | None = None      # fill 이전 원본 (비교용)
    group_image_ids: list[str] | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def needs_fill(self) -> bool:
        """LLM 재추출 대상인지 여부.

        입력: content_type, quality
        출력: content_type 이 table 이고 quality 가 wrong/insufficient 이면 True
        """
        return self.content_type == TABLE and self.quality in NEEDS_FILL

    @property
    def was_filled(self) -> bool:
        """재추출이 실제로 내용을 바꿨는지 여부.

        입력: original_markdown, markdown
        출력: 원본이 있고 현재 내용과 다르면 True
        """
        return bool(self.original_markdown) and self.original_markdown != self.markdown


@dataclass
class ImageInfo:
    id: str
    placeholder: str
    description: str | None = None
    image_path: str | None = None   # 저장된 이미지 파일 경로
    mime_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


#: summary() 에 표시할 출처 (unmeasured/n/a 는 정보가 없으므로 생략)
_SHOWN_SOURCES = frozenset({"text_layer", "ocr", "mixed", "empty"})

#: 화면·리포트용 한국어 라벨
SOURCE_LABELS = {
    "text_layer": "텍스트 레이어",
    "ocr": "OCR",
    "mixed": "혼합",
    "empty": "추출 실패",
    "unmeasured": "미측정",
    "n/a": "해당 없음",
    "unknown": "미상",
}


def source_label(source: str, ratio: float | None = None) -> str:
    """텍스트 출처 코드를 화면용 한국어 라벨로 바꾼다.

    입력:
        source  text_layer | ocr | mixed | empty | unmeasured | n/a
        ratio   OCR 비율 (ocr/mixed 일 때만 표시에 반영)
    출력: `OCR 92%` / `혼합 34%` / `미측정` 형태 문자열
    """
    label = SOURCE_LABELS.get(source, source)
    if ratio is not None and source in ("ocr", "mixed"):
        label += f" {ratio:.0%}"
    return label


@dataclass
class TraceStep:
    """파이프라인 단계 하나의 실행 기록.

    입력(필드):
        module       수행 모듈 경로 (예: docstruct.tables.assess)
        action       수행 동작
        detail       결과 요약
        status       ok | skip | warn | fail
        duration_ms  소요 시간 (측정한 경우)
    출력:
        line(index)  로그 한 줄 문자열
    """

    module: str                      # 실제 수행 모듈 (converters.pdf.docling 등)
    action: str                      # 무엇을 했는지
    detail: str = ""                 # 결과 요약
    status: str = "ok"               # ok | skip | warn | fail
    duration_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def line(self, index: int) -> str:
        """실행 로그 한 줄을 만든다.

        입력: index — 표시할 순번 (1-based)
        출력: `  3. docstruct.tables.assess  LLM 표 판정 — ... (2.1s)` 형태 문자열
        """
        mark = {"ok": " ", "skip": "-", "warn": "!", "fail": "x"}.get(self.status, " ")
        text = f"{mark} {index}. {self.module:<28} {self.action}"
        if self.detail:
            text += f" — {self.detail}"
        if self.duration_ms is not None:
            text += f"  ({self.duration_ms / 1000:.1f}s)"
        return text


@dataclass
class PageTrace:
    """페이지 하나가 거친 처리 경로.

    입력(필드):
        extractor     docling | hwpml-xml | pyhwp-html | olefile-text | python-hwpx
        text_source   text_layer | ocr | mixed | empty | unmeasured | n/a
        ocr_ratio     OCR 로 만들어진 셀 비율 (PDF 에서만)
        cell_count    텍스트 셀 수
        table_count / picture_count
        rendered / assessed / refilled   단계별 수행 여부
        failed / notes / steps
    출력:
        summary()  한 줄 요약
        log()      순차 실행 로그 전문
        to_dict()  document.json 의 page.trace
    """

    #: 본문을 뽑아낸 주체
    #: docling | pyhwp-html | hwpml-xml | olefile-text | python-hwpx
    extractor: str = "unknown"

    #: 텍스트 출처.
    #:   text_layer  PDF 내장 텍스트 레이어를 읽음
    #:   ocr         이미지 인식으로 얻음
    #:   mixed       둘이 섞임
    #:   empty       본문이 실제로 비어 있음 (진짜 문제)
    #:   unmeasured  구분 불가 — Docling 이 셀 데이터를 보관하지 않음
    #:               (파싱 자체는 정상. generate_parsed_pages 로 측정 가능)
    #:   n/a         PDF 가 아님 (HWP/HWPX)
    text_source: str = "unmeasured"

    #: OCR 로 만들어진 텍스트 셀 비율 (0.0~1.0). PDF 에서만 의미 있음.
    ocr_ratio: float | None = None

    #: 원소 개수 (파싱 결과 규모)
    cell_count: int | None = None
    table_count: int = 0
    picture_count: int = 0

    #: 페이지 PNG 렌더 여부 (표 평가/재추출의 시각 근거)
    rendered: bool = False

    #: LLM 단계 수행 여부
    assessed: bool = False
    #: LLM 으로 다시 뽑은 표 ID
    refilled: list[str] = field(default_factory=list)

    #: 파싱 실패로 내용이 비었는지
    failed: bool = False

    #: 사람이 읽을 부가 설명 (경고·특이사항)
    notes: list[str] = field(default_factory=list)

    #: 이 페이지에 대해 실행된 단계들 (실행 순서대로)
    steps: list[TraceStep] = field(default_factory=list)

    def add(
        self,
        module: str,
        action: str,
        detail: str = "",
        *,
        status: str = "ok",
        duration_ms: float | None = None,
    ) -> None:
        """실행 단계를 순서대로 추가한다.

        입력: module, action, detail, status, duration_ms
        출력: 없음 (steps 에 TraceStep 추가)
        """
        self.steps.append(
            TraceStep(
                module=module, action=action, detail=detail,
                status=status, duration_ms=duration_ms,
            )
        )

    def log(self) -> str:
        """이 페이지의 순차 실행 로그 전문.

        입력: steps
        출력: 줄바꿈으로 이어진 로그 문자열 (단계가 없으면 안내 문구)
        """
        if not self.steps:
            return "(기록된 단계 없음)"
        return "\n".join(step.line(i) for i, step in enumerate(self.steps, 1))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        """처리 경로 한 줄 요약.

        입력: extractor, text_source, 각 단계 수행 여부
        출력: `docling · OCR 92% · 표3 · 렌더 · 평가 · 재추출2` 형태 문자열
        """
        parts = [self.extractor]
        if self.text_source in _SHOWN_SOURCES:
            parts.append(source_label(self.text_source, self.ocr_ratio))
        if self.table_count:
            parts.append(f"표{self.table_count}")
        if self.picture_count:
            parts.append(f"그림{self.picture_count}")
        if self.rendered:
            parts.append("렌더")
        if self.assessed:
            parts.append("평가")
        if self.refilled:
            parts.append(f"재추출{len(self.refilled)}")
        if self.failed:
            parts.append("실패")
        return " · ".join(parts)


@dataclass
class PageContent:
    """페이지 하나의 구조화 결과.

    입력(필드):
        page_no / page_no_kind   페이지 번호와 그 성격 (exact | document)
        content                  본문 markdown (표는 `<table N>` 블록으로 치환)
        tables / images          페이지에 속한 표·이미지 메타
        page_image_path          렌더된 페이지 PNG 경로 (PDF 만)
        trace                    처리 경로 기록
        layout                   레이아웃 모델 인식 영역 목록 (PDF 만)
    출력:
        to_dict()  document.json 의 pages[] 원소
    """
    page_no: int | str
    page_no_kind: str                # exact | document
    content: str
    tables: list[TableInfo] = field(default_factory=list)
    images: list[ImageInfo] = field(default_factory=list)
    page_image_path: str | None = None   # 렌더된 페이지 PNG (PDF 전용)
    trace: PageTrace = field(default_factory=PageTrace)
    #: 레이아웃 모델이 인식한 영역 목록 (PDF 만). docstruct.layout.LayoutItem
    layout: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_no": self.page_no,
            "page_no_kind": self.page_no_kind,
            "page_image_path": self.page_image_path,
            "trace": self.trace.to_dict(),
            "layout": [i.to_dict() for i in self.layout],
            "content": self.content,
            "tables": [t.to_dict() for t in self.tables],
            "images": [i.to_dict() for i in self.images],
        }


@dataclass
class PageDocument:
    """문서 하나의 구조화 결과 (파이프라인 최종 산출물).

    입력(필드):
        filename / source_format  원본 파일명과 형식 (pdf | hwp | hwpx)
        pages                     페이지 목록
        failed_pages              파싱 실패로 빠진 페이지 번호
        pipeline                  이 실행에 적용된 설정 스냅샷
        timings                   단계별 소요 시간(초)
    출력:
        to_dict()  document.json 전체
    """
    filename: str
    source_format: str
    pages: list[PageContent] = field(default_factory=list)
    #: Docling 이 파싱에 실패해 결과에서 빠진 페이지 번호.
    #: 예외가 아니라 로그로만 남는 부분 실패라 명시적으로 들고 다닙니다.
    failed_pages: list[int] = field(default_factory=list)
    #: 이 실행에 적용된 파이프라인 설정 (백엔드·OCR·LLM 등)
    pipeline: dict[str, Any] = field(default_factory=dict)
    #: 단계별 소요 시간(초). 어디에 시간이 쓰였는지 판단하는 근거.
    timings: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "source_format": self.source_format,
            "page_count": len(self.pages),
            "failed_pages": self.failed_pages,
            "pipeline": self.pipeline,
            "timings": self.timings,
            "pages": [p.to_dict() for p in self.pages],
        }

    # -- 집계 헬퍼 (report에서 사용) -------------------------------------

    def all_tables(self) -> list[tuple[PageContent, TableInfo]]:
        return [(p, t) for p in self.pages for t in p.tables]

    def all_images(self) -> list[tuple[PageContent, ImageInfo]]:
        return [(p, i) for p in self.pages for i in p.images]

    def char_count(self) -> int:
        return sum(len(p.content or "") for p in self.pages)
