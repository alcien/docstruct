"""Docling 파이프라인 구성.

역할:
    설정값(core.config)을 Docling 옵션 객체로 옮긴다. PDF 백엔드, OCR 엔진과
    언어, 연산 장치, 단계 병렬화, 그림 설명 VLM 등을 여기서 정한다.
    Docling 버전에 따라 없는 옵션은 경고 후 기본값으로 넘어간다.
호출부:
    converters.pdf.converter.PdfConverter
출력:
    DocumentConverter (설정이 반영된 상태). 인스턴스는 재사용을 위해 캐시된다.
"""
from __future__ import annotations

import logging
import threading
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from docstruct.core.config import get_settings, resolve_device

_log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from docling.document_converter import DocumentConverter


def _reachable(url: str, timeout: float = 3.0) -> bool:
    """엔드포인트에 TCP 연결이 되는지 빠르게 확인한다.

    입력: url — 대상 주소, timeout — 연결 대기 초
    출력: 연결되면 True
    비고:
        HTTP 요청이 아니라 소켓 연결만 본다. 응답 내용은 관심 없고
        "닿는가"만 알면 되므로 빠르고 부작용이 없다.
    """
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host, port = parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _picture_description_options() -> Any | None:
    """그림 설명 VLM 옵션을 만든다.

    입력: 없음 (설정의 docling_picture, llm_fallback 사용)
    출력: PictureDescriptionApiOptions. 쓸 수 없으면 None
    비고:
        이 호출은 **docling 이 직접** 수행한다. docling 내부 재시도가
        5회 × 연결 타임아웃이라, 서버가 죽어 있으면 그림 하나당 수 분씩
        멈춘다. 우리 LLM 클라이언트의 폴백 로직도 개입하지 못한다.

        그래서 컨버터를 만들기 전에 한 번 확인해, 닿지 않으면
        대비 엔드포인트로 바꾸거나 그림 설명을 끈다.
    """
    settings = get_settings()
    if settings.picture_mode not in ("describe", "both"):
        # 기본(read)에서는 media.vlm_read 가 그림을 맡는다. 여기서 함께 켜면
        # 같은 그림에 두 번 호출되고, docling 내부 호출은 우리 재시도·폴백을
        # 타지 않는다. docling import 보다 앞에 둬야 미설치 환경에서도 돈다.
        _log.debug("그림 설명 생략 — picture_mode=%s", settings.picture_mode)
        return None

    from docling.datamodel.pipeline_options import PictureDescriptionApiOptions

    endpoint = settings.docling_picture
    if endpoint is None:
        return None

    headers = endpoint.headers()

    if not _reachable(endpoint.url):
        backup = settings.llm_fallback
        if backup is not None and _reachable(backup.url):
            _log.warning(
                "그림 설명 엔드포인트에 연결할 수 없어 대비 엔드포인트로 전환합니다 "
                "— %s (%s)", backup.model, backup.url,
            )
            endpoint, headers = backup, backup.headers()
        else:
            _log.warning(
                "그림 설명 엔드포인트(%s)에 연결할 수 없어 그림 캡션을 생략합니다. "
                "(docling 내부 재시도로 문서 처리가 크게 지연되는 것을 막기 위함)",
                endpoint.url,
            )
            return None

    return PictureDescriptionApiOptions(
        url=endpoint.url,
        params={"model": endpoint.model},
        headers=headers,
        prompt=getattr(endpoint, "prompt", None)
        or "Describe this image concisely and accurately in Korean.",
        timeout=int(endpoint.timeout),
        picture_area_threshold=settings.picture_area_threshold,
    )


def _ocr_langs(default: list[str]) -> list[str]:
    """OCR 언어 코드를 정한다.

    입력: default — 백엔드별 기본 코드
    출력: 설정값이 있으면 그것, 없으면 기본값
    비고: 코드 규약이 엔진마다 다르다 (rapidocr=korean, easyocr=ko, tesseract=kor)
    """
    raw = get_settings().ocr_lang
    if not raw:
        return default
    langs = [item.strip() for item in raw.split(",") if item.strip()]
    return langs or default


def _warn_if_ocr_unusable(backend: str) -> None:
    """OCR 엔진을 실제로 못 쓰는 상태면 진짜 원인을 미리 알린다.

    입력: backend — 설정된 OCR 엔진 이름
    출력: 없음 (경고 로그만)
    비고:
        docling 은 rapidocr import 가 **어떤 이유로 실패하든** 
        "pip install rapidocr onnxruntime" 이라고 안내한다. 컨테이너에서
        흔한 실제 원인은 libGL.so.1 부재라, 그 안내를 따라도 해결되지 않고
        시간만 쓴다. 변환이 시작되기 전에 진짜 이유를 남겨 둔다.
    """
    if backend in ("auto", "none"):
        return
    try:
        from docstruct.checks import _ocr_ready
    except ImportError:
        return
    try:
        ready, note = _ocr_ready()
    except Exception:                            # noqa: BLE001 - 진단이 변환을 막으면 안 된다
        return
    if not ready:
        _log.warning("OCR 사용 불가: %s", note)


def _ocr_options() -> Any:
    """OCR 엔진 옵션을 만든다.

    입력: 없음 (설정의 ocr_backend, ocr_lang 사용)
    출력: 엔진별 OcrOptions 객체
    """
    from docling.datamodel.pipeline_options import (
        EasyOcrOptions,  # pip install easyocr
        OcrAutoOptions,
        RapidOcrOptions,  # pip install rapidocr-onnxruntime
        TesseractCliOcrOptions,
    )

    backend = get_settings().ocr_backend
    _warn_if_ocr_unusable(backend)

    if backend == "rapidocr":
        # 한국어 포함 동아시아 문자에서 Tesseract보다 정확도 높음.
        #
        # 주의: RapidOcrOptions.lang 기본값은 ["chinese"] 입니다 — 축약형("zh")이
        # 아니라 전체 단어 규약이므로 "en" 이 아니라 "english" 를 써야 합니다.
        # 잘못된 토큰을 넣으면 조용히 무시되거나 엉뚱한 인식 모델이 선택되어
        # "RapidOCR returned empty result!" 로 이어질 수 있습니다.
        opts = RapidOcrOptions()
        opts.lang = _ocr_langs(["korean", "english"])
        # 추론 런타임. 기본 onnxruntime 은 CPU 전용이라,
        # GPU 를 쓰려면 torch 로 바꾸거나 onnxruntime-gpu 를 설치해야 합니다.
        runtime = get_settings().rapidocr_runtime
        if runtime != "onnxruntime" and hasattr(opts, "backend"):
            opts.backend = runtime
        return opts

    if backend == "easyocr":
        # 가장 정확하지만 느리고 무거움, GPU 있을 때 유리. easyocr 은 ISO 코드.
        opts = EasyOcrOptions()
        opts.lang = _ocr_langs(["ko", "en"])
        return opts

    if backend == "auto":
        opts = OcrAutoOptions()
        opts.lang = _ocr_langs(["ko", "en"])
        return opts

    # fallback: tesseract — tesseract 는 3글자 코드.
    return TesseractCliOcrOptions(lang=_ocr_langs(["kor", "eng"]))


def _accelerator_options():
    """연산 장치 옵션을 만든다.

    입력: 없음 (설정의 device, num_threads 사용)
    출력: AcceleratorOptions. 지정이 없거나 미지원 버전이면 None
    비고: 요청한 장치를 쓸 수 없으면 CPU 로 대체하고 경고를 남긴다.
    """
    settings = get_settings()
    device, note = resolve_device()
    if note:
        _log.warning("%s", note)

    if device == "auto" and not settings.num_threads:
        return None   # Docling 자동 판단에 맡김

    try:
        from docling.datamodel.pipeline_options import AcceleratorDevice, AcceleratorOptions
    except ImportError:
        try:
            from docling.datamodel.accelerator_options import (  # type: ignore
                AcceleratorDevice, AcceleratorOptions,
            )
        except ImportError:
            _log.warning("AcceleratorOptions 를 불러올 수 없습니다 — Docling 기본값 사용")
            return None

    kwargs: dict[str, Any] = {}
    if device != "auto":
        resolved = getattr(AcceleratorDevice, device.upper(), None)
        kwargs["device"] = resolved if resolved is not None else device
    if settings.num_threads:
        kwargs["num_threads"] = settings.num_threads

    try:
        return AcceleratorOptions(**kwargs)
    except Exception as exc:
        _log.warning("AcceleratorOptions 구성 실패 (%s) — Docling 기본값 사용", exc)
        return None


def _pipeline_options_class(threaded: bool):
    """파이프라인 옵션·구현 클래스를 고른다.

    입력: threaded — 단계 병렬화 사용 여부
    출력: (옵션 클래스, 파이프라인 클래스). 병렬 미지원 버전이면 (표준 옵션, None)
    """
    from docling.datamodel.pipeline_options import PdfPipelineOptions

    if not threaded:
        return PdfPipelineOptions, None

    try:
        from docling.datamodel.pipeline_options import ThreadedPdfPipelineOptions
        from docling.pipeline.threaded_standard_pdf_pipeline import (
            ThreadedStandardPdfPipeline,
        )

        return ThreadedPdfPipelineOptions, ThreadedStandardPdfPipeline
    except ImportError as exc:
        _log.warning(
            "스레드 파이프라인을 쓸 수 없습니다 (%s) — 표준 파이프라인 사용", exc
        )
        return PdfPipelineOptions, None


def build_pdf_pipeline_options(*, skip_ocr: bool = False):
    """PDF 파이프라인 옵션을 만든다.

    입력: 없음 (core.config 의 설정을 읽음)
    출력: PdfPipelineOptions (OCR·표 구조·연산 장치·그림 생성 설정 포함)
    """
    settings = get_settings()
    options_cls, _ = _pipeline_options_class(settings.threaded_pipeline)
    pipeline_options = options_cls()

    # 연산 장치 — 레이아웃 모델 · TableFormer · OCR 이 공유합니다.
    accelerator = _accelerator_options()
    if accelerator is not None:
        pipeline_options.accelerator_options = accelerator
        effective, _ = resolve_device()
        _log.info("가속 설정: device=%s threads=%s",
                  effective, settings.num_threads or "(기본)")

    # OCR을 쓸건지.
    #
    # 스캔본에서는 끌 수 있다 — 한국어 재판독이 어차피 도는데 docling 내장
    # OCR 은 중국어 모델이라 결과를 버린다. 같은 지면을 두 번 읽는 셈이다.
    #
    # 표 격자는 TableFormer 가 이미지 레이아웃으로 잡으므로 OCR 없이도
    # 나온다. 셀 텍스트는 `cell_match` 가 재판독 조각으로 채운다.
    pipeline_options.do_ocr = not skip_ocr
    if skip_ocr:
        _log.info("스캔본이므로 docling OCR 을 건너뜁니다 — 한국어 재판독이 대신합니다")
    # OCR 옵션 설정
    pipeline_options.ocr_options = _ocr_options()

    # 테이블 구조 추출
    pipeline_options.do_table_structure = True
    # 셀 매칭 옵션 설정
    pipeline_options.table_structure_options.do_cell_matching = True

    # 텍스트 레이어를 무시하고 페이지 전체를 OCR.
    # 스캔 PDF 에 깨진 텍스트 레이어가 얹혀 있거나, 레거시 인코딩(EUC-KR 등)
    # 문자열이 들어 있어 preprocess 가 실패하는 페이지를 살릴 때 씁니다.
    if settings.force_full_page_ocr:
        pipeline_options.ocr_options.force_full_page_ocr = True

    # 페이지 셀 보관 — 켜면 페이지별 OCR/텍스트레이어 판별이 가능해집니다.
    # (Docling 기본값은 False 라 파싱 후 셀을 버립니다.)
    if settings.generate_parsed_pages:
        pipeline_options.generate_parsed_pages = True

    # 그림 이미지 생성
    pipeline_options.enable_remote_services = True
    pipeline_options.generate_picture_images = True

    return pipeline_options


def _pdf_backend_class(name: str):
    """PDF 텍스트 추출 백엔드 클래스를 고른다.

    입력: name — auto | pypdfium2 | dlparse (v4/v2 는 별칭)
    출력: 백엔드 클래스. auto 이거나 로드 실패 시 None (Docling 기본값 사용)
    """
    if name == "auto":
        return None

    if name == "pypdfium2":
        try:
            from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend

            return PyPdfiumDocumentBackend
        except ImportError as exc:
            _log.warning("pypdfium2 백엔드 로드 실패 (%s) — 기본값 사용", exc)
            return None

    # dlparse / dlparse_v4 / dlparse_v2 → 통합 클래스 우선
    try:
        from docling.backend.docling_parse_backend import DoclingParseDocumentBackend

        if name in ("dlparse_v4", "dlparse_v2"):
            _log.info(
                "%s 는 docling 2.74.0 에서 DoclingParseDocumentBackend 로 통합됐습니다 "
                "— 통합 클래스를 사용합니다.", name,
            )
        return DoclingParseDocumentBackend
    except ImportError:
        pass

    # 구버전 docling 폴백
    legacy = {
        "dlparse_v4": ("docling.backend.docling_parse_v4_backend", "DoclingParseV4DocumentBackend"),
        "dlparse_v2": ("docling.backend.docling_parse_v2_backend", "DoclingParseV2DocumentBackend"),
        "dlparse": ("docling.backend.docling_parse_v4_backend", "DoclingParseV4DocumentBackend"),
    }.get(name)
    if legacy:
        try:
            module = __import__(legacy[0], fromlist=[legacy[1]])
            return getattr(module, legacy[1])
        except (ImportError, AttributeError) as exc:
            _log.warning("PDF 백엔드 %r 로드 실패 (%s) — 기본값 사용", name, exc)
    return None


def _hide_gpu_if_unusable() -> None:
    """GPU 를 쓸 수 없으면 이 프로세스에서 감춘다.

    입력: 없음
    출력: 없음 (CUDA_VISIBLE_DEVICES 설정)
    비고:
        `device=cpu` 로 정해졌는데 GPU 가 보이면, docling 의 import 사슬
        (transformers → torch)이 CUDA 를 초기화하다 실패할 수 있다.
        (드라이버 불일치, 잘못된 장치 인덱스 등)

        torch 가 아직 import 되지 않았을 때만 효과가 있다. 이미 import
        됐다면 조용히 넘어간다 — 그 경우는 convert() 단계의 CPU 재시도가 받는다.
    """
    import os
    import sys

    device, _ = resolve_device()
    if device != "cpu":
        return
    if os.environ.get("CUDA_VISIBLE_DEVICES") == "":
        return

    if "torch" in sys.modules:
        _log.debug("torch 가 이미 로드되어 CUDA_VISIBLE_DEVICES 를 바꿔도 효과가 없습니다")
        return

    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    _log.info("CPU 로 처리합니다 — 이 프로세스에서 GPU 를 감춥니다 (CUDA 초기화 회피)")


#: 컨버터 생성 직렬화용. lru_cache 는 조회만 원자적이고 **생성은 아니므로**,
#: 서버에서 첫 요청 두 개가 겹치면 Docling 모델이 두 번 로드된다(수 GB).
_CONVERTER_LOCK = threading.Lock()


def get_document_converter(pdf_path: str | None = None) -> "DocumentConverter":
    """설정이 반영된 DocumentConverter 를 얻는다 (캐시·스레드 안전).

    입력: pdf_path — 처리할 PDF (스캔본 판정에만 씀). 없으면 판정하지 않음
    출력: DocumentConverter (설정 조합별로 캐시됨)
    비고:
        **스캔본이면 docling OCR 을 끌 수 있다.** 스캔본은 한국어 재판독이
        어차피 도는데, docling 내장 OCR 은 중국어 모델이라 결과를 버린다.
        실측(주택과세금 377쪽): 추출 1,096초 + 재판독 627초 = 29분.

        `scanned_skip_docling_ocr` 이 켜져 있을 때만 판정한다 — 켜지 않으면
        경로를 읽지도 않는다.
    """
    skip_ocr = False
    if pdf_path and get_settings().scanned_skip_docling_ocr:
        from docstruct.converters.pdf.scanned import looks_scanned

        skip_ocr = looks_scanned(pdf_path)
    with _CONVERTER_LOCK:
        return _build_document_converter(skip_ocr)


@lru_cache(maxsize=2)
def _build_document_converter(skip_ocr: bool = False) -> "DocumentConverter":
    """DocumentConverter 를 실제로 만든다 (락 안에서만 호출).

    입력: 없음
    출력: DocumentConverter
    """
    # Windows 비 UTF-8 로케일에서 Docling 이 모델을 로드하며 torch.compile 을
    # 호출하는데, 그 경로가 cp949 로 UTF-8 템플릿을 읽다 죽습니다.
    # 모델 로딩 직전에 우회를 걸어둡니다 (해당 환경에서만 동작).
    from docstruct.core.winfix import apply as _apply_winfix

    _apply_winfix(verbose=False)

    # docling 은 import 사슬(transformers → torch)에서 CUDA 를 건드린다.
    # GPU 를 쓸 수 없는 상태면 그 시점에 터지므로, **import 전에** 판정해
    # GPU 자체를 감춘다. 이렇게 하면 torch 가 CUDA 를 시도하지 않는다.
    _hide_gpu_if_unusable()

    from docling.datamodel.base_models import InputFormat
    from docling.document_converter import DocumentConverter, PdfFormatOption

    settings = get_settings()
    pipeline_options = build_pdf_pipeline_options(skip_ocr=skip_ocr)

    # 그림 설명 옵션 설정
    picture_opts = _picture_description_options()
    if picture_opts is not None:
        pipeline_options.do_picture_description = True
        pipeline_options.picture_description_options = picture_opts

    # 수식·코드 확장 (기본 꺼짐).
    # 별도 VLM 을 내려받아 torch.compile 까지 태우지만 표·본문 추출 결과에는
    # 쓰이지 않는다. Windows 비 UTF-8 로케일에서는 이 단계에서 초기화가 실패한다.
    pipeline_options.do_formula_enrichment = settings.code_formula_enrichment
    pipeline_options.do_code_enrichment = settings.code_formula_enrichment

    fmt_kwargs = {"pipeline_options": pipeline_options}

    _, pipeline_cls = _pipeline_options_class(settings.threaded_pipeline)
    if pipeline_cls is not None:
        fmt_kwargs["pipeline_cls"] = pipeline_cls
        _log.info("스레드 파이프라인 사용: %s", pipeline_cls.__name__)

    backend_cls = _pdf_backend_class(settings.pdf_backend)
    if backend_cls is not None:
        fmt_kwargs["backend"] = backend_cls
        _log.info("PDF 백엔드: %s", backend_cls.__name__)

    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(**fmt_kwargs)}
    )


def reset_document_converter() -> None:
    """캐시된 DocumentConverter 를 버린다.

    입력: 없음
    출력: 없음
    비고: 설정 변경 후 새 값을 반영하려면 호출한다
    """
    with _CONVERTER_LOCK:
        _build_document_converter.cache_clear()
