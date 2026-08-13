"""Colab 환경 부트스트랩.

역할:
    Colab 에서 이 도구를 쓰기 위한 준비를 한 곳에 모은다 — 의존성 설치,
    코드 반입, 드라이브 연결, LLM·가속 설정, 결과 반출, 비용 추정.
    로컬 실행에는 필요 없다.
호출부:
    notebooks/preview_colab.ipynb
출력:
    함수마다 다름 (설치 로그·설정 요약·파일 경로 등)
"""
from __future__ import annotations

import importlib.util
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

_log = logging.getLogger(__name__)

#: docstruct 파싱에 필요한 pip 패키지 (docling 은 별도 — 매우 큼)
BASE_PACKAGES = (
    "beautifulsoup4",
    "requests",
    "python-dotenv",
    "olefile",
    "six",
    "pyhwp",
    "python-hwpx",
    "pypdfium2",
    "pillow",
)
PDF_PACKAGES = ("docling", "rapidocr-onnxruntime")


def in_colab() -> bool:
    """Colab 런타임인지 판별한다.

    입력: 없음
    출력: Colab 이면 True
    """
    import sys

    if "google.colab" in sys.modules:
        return True
    try:
        return importlib.util.find_spec("google.colab") is not None
    except (ImportError, ValueError, AttributeError):
        return False


# ── 의존성 설치 -------------------------------------------------------------

def _pip(*args: str) -> int:
    """pip 명령을 실행한다.

    입력: args — pip 인자, quiet — 로그 억제
    출력: 없음
    """
    cmd = [sys.executable, "-m", "pip", "install", "-q", *args]
    print("  $", " ".join(cmd[-len(args):]))
    return subprocess.call(cmd)


def install(*, pdf: bool = True, tesseract: bool = False) -> None:
    """필요한 패키지를 설치한다.

    입력: pdf — docling(PDF 처리) 포함 여부, quiet — 로그 억제
    출력: 없음
    """
    print("기본 파서 설치 중...")
    _pip(*BASE_PACKAGES)

    if pdf:
        print("\nDocling 설치 중 — 수 분 걸립니다 (torch 등 대용량 의존성)...")
        _pip(*PDF_PACKAGES)

    if tesseract:
        print("\ntesseract 설치 중...")
        subprocess.call(["apt-get", "-qq", "install", "-y",
                         "tesseract-ocr", "tesseract-ocr-kor", "tesseract-ocr-eng"])

    print("\n설치 완료 — 아래 항목을 확인하세요.")
    for mod, label in (
        ("bs4", "beautifulsoup4"),
        ("hwp5", "pyhwp"),
        ("hwpx", "python-hwpx"),
        ("pypdfium2", "pypdfium2"),
        ("docling", "docling"),
    ):
        ok = importlib.util.find_spec(mod) is not None
        print(f"  {'OK  ' if ok else 'MISS'} {label}")


# ── 코드 반입 ---------------------------------------------------------------

def install_from_zip(zip_path: str | Path, dest: str | Path = "/content/app") -> Path:
    """프로젝트 zip 을 풀어 설치한다.

    입력: zip_path — 업로드한 zip 경로
    출력: 패키지 루트 경로
    """
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    shutil.unpack_archive(str(zip_path), str(dest))

    root = _find_package_root(dest)
    if root is None:
        raise RuntimeError(
            f"{dest} 안에서 docstruct 패키지를 찾지 못했습니다. "
            "zip 안에 app/docstruct/ 가 들어 있는지 확인하세요."
        )
    return register(root)


def _find_package_root(base: Path) -> Path | None:
    """압축을 푼 디렉터리에서 패키지 루트를 찾는다.

    입력: base — 탐색 시작 경로
    출력: 패키지 루트 경로
    """
    if (base / "docstruct").is_dir():
        return base
    for candidate in sorted(base.rglob("docstruct")):
        if candidate.is_dir() and (candidate / "pipeline.py").is_file():
            return candidate.parent
    return None


def register(app_root: str | Path) -> Path:
    """패키지 경로를 sys.path 에 등록한다.

    입력: root — 패키지 루트
    출력: 없음
    """
    root = Path(app_root).resolve()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    print(f"sys.path 등록: {root}")
    return root


def mount_drive(mountpoint: str = "/content/drive") -> Path:
    """Google Drive 를 연결한다.

    입력: 없음
    출력: 마운트 경로
    """
    from google.colab import drive

    drive.mount(mountpoint)
    return Path(mountpoint)


# ── LLM 설정 ---------------------------------------------------------------

OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_MODELS_URL = "https://api.openai.com/v1/models"

#: 참고용 기본값. 모델 라인업은 자주 바뀌므로 list_openai_models() 로 실제
#: 키가 접근 가능한 목록을 확인하는 편이 확실합니다.
DEFAULT_OPENAI_MODEL = "gpt-5.6-luna"

_ENV_KEYS = (
    "OPENAI_API_KEY",
    "DOCLING_TABLE_API_KEY",
    "DOCLING_TABLE_API_URL",
    "DOCLING_TABLE_API_MODEL",
    "DOCLING_TABLE_API_TIMEOUT",
    "DOCLING_PICTURE_API_URL",
    "DOCLING_PICTURE_API_MODEL",
    "DOCLING_PICTURE_API_PROMPT",
    "DOCLING_PICTURE_API_TIMEOUT",
    "DOCLING_PICTURE_AREA_THRESHOLD",
    "DOCLING_OCR_BACKEND",
)


def configure(
    *,
    url: str | None = None,
    model: str | None = None,
    timeout: float = 120,
    ocr_backend: str = "rapidocr",
    force_full_page_ocr: bool = False,
    use_secrets: bool = True,
    picture: bool = True,
    device: str = "auto",
    llm_concurrency: int = 4,
    threaded_pipeline: bool = False,
) -> None:
    """사내 LLM 엔드포인트로 설정한다.

    입력:
        url, model, timeout   엔드포인트 정보
        ocr_backend           OCR 엔진
        force_full_page_ocr   텍스트 레이어를 무시하고 전면 OCR.
                              PDF 폰트의 ToUnicode 매핑이 깨져 글자가
                              엉뚱하게 나올 때 켠다 (느려집니다)
        use_secrets           Colab Secrets 에서 값을 읽을지
        picture               그림 설명 VLM 사용 여부
        device                연산 장치 (auto | cpu | cuda)
        llm_concurrency       LLM 동시 호출 수
        threaded_pipeline     Docling 단계 병렬화
    출력: 없음 (설정 요약 출력)
    """
    if use_secrets:
        _load_secrets()

    if url:
        os.environ["DOCLING_TABLE_API_URL"] = url
        if picture:
            os.environ["DOCLING_PICTURE_API_URL"] = url
    if model:
        os.environ["DOCLING_TABLE_API_MODEL"] = model
        if picture:
            os.environ["DOCLING_PICTURE_API_MODEL"] = model

    os.environ["DOCLING_TABLE_API_TIMEOUT"] = str(timeout)
    os.environ["DOCLING_OCR_BACKEND"] = ocr_backend
    os.environ["DOCLING_FORCE_FULL_PAGE_OCR"] = "true" if force_full_page_ocr else "false"
    _apply_device(device, ocr_backend)
    _apply_speed(llm_concurrency, threaded_pipeline)

    # reload_config() 가 아니라 rebuild_settings() 입니다 — 전자는 .env 를
    # override=True 로 다시 읽어서, 방금 위에서 넣은 값을 파일 값이 덮어씁니다.
    from docstruct.core.config import rebuild_settings

    from docstruct.checks import invalidate_caches

    settings = rebuild_settings()
    invalidate_caches()
    if settings.llm:
        print(f"LLM 설정됨: {settings.llm.url}")
        print(f"  model  = {settings.llm.model or '(미지정)'}")
    else:
        print("LLM 미설정 — 표 평가·재추출·목차 없이 파싱만 수행합니다.")
    print(
        f"OCR 백엔드: {settings.ocr_backend}"
        + (" · 전면 OCR (텍스트 레이어 무시)" if settings.force_full_page_ocr else "")
    )
    print(f"연산 장치 : {settings.device}")
    print(f"LLM 동시  : {settings.llm_concurrency}개")


def _load_secrets() -> None:
    """Colab Secrets 에서 값을 읽는다.

    입력: key — Secret 이름
    출력: 값. 없거나 Colab 이 아니면 None
    """
    try:
        from google.colab import userdata
    except ImportError:
        return
    for key in _ENV_KEYS:
        try:
            value = userdata.get(key)
        except Exception:
            continue  # 미등록이거나 접근 거부 — 조용히 넘어감
        if value:
            os.environ[key] = str(value)


# check_llm_reachable() 은 docstruct/checks.py 로 옮겼습니다 (로컬 CLI/노트북
# 에서도 쓰이므로 Colab 전용 모듈에 있을 이유가 없습니다). 기존 코드·노트북과의
# 호환을 위해 여기서 그대로 재노출합니다.
from docstruct.checks import check_llm_reachable  # noqa: E402,F401


# ── HWP → HWPX 변환기 -------------------------------------------------------
# 설치·확인 기능은 converters/hwpx/convert.py 로 옮겼습니다. Colab 전용이
# 아니라 사내 서버·도커에서도 쓰기 때문입니다 — `colab.` 이름을 달고 있으면
# 서버에서 부를 때 헷갈립니다.
#
# 기존 노트북과의 호환을 위해 여기서 그대로 재노출합니다.
from docstruct.converters.hwpx.convert import (  # noqa: E402,F401
    check_converter as check_hwp2hwpx,
    install_converter as install_hwp2hwpx,
    use_converter as use_hwp2hwpx,
)


# ── 결과 반출 ---------------------------------------------------------------

def download_outputs(out_dir: str | Path, *, name: str | None = None) -> Path:
    """결과를 zip 으로 내려받는다.

    입력: out_dir — 산출물 디렉터리
    출력: 생성된 zip 경로
    """
    out_dir = Path(out_dir)
    if not out_dir.is_dir():
        raise FileNotFoundError(f"출력 디렉터리가 없습니다: {out_dir}")

    archive_base = Path("/content") / (name or out_dir.name)
    zip_path = Path(shutil.make_archive(str(archive_base), "zip", str(out_dir)))
    size = zip_path.stat().st_size

    print(f"{zip_path.name} ({size:,} bytes)")
    try:
        from google.colab import files

        files.download(str(zip_path))
    except ImportError:
        print("Colab 환경이 아니라 다운로드는 생략합니다.")
    return zip_path


def save_to_drive(out_dir: str | Path, drive_subdir: str = "docstruct") -> Path:
    """결과를 Google Drive 에 복사한다.

    입력: out_dir — 산출물 디렉터리, dest — 드라이브 내 경로
    출력: 복사된 경로
    """
    out_dir = Path(out_dir)
    target = Path("/content/drive/MyDrive") / drive_subdir / out_dir.name
    if not Path("/content/drive/MyDrive").is_dir():
        raise RuntimeError("Drive가 마운트되지 않았습니다 — mount_drive() 를 먼저 호출하세요.")
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(out_dir, target)
    print(f"저장됨: {target}")
    return target


# ── OpenAI(GPT) 사용 ---------------------------------------------------------

def configure_openai(
    *,
    api_key: str | None = None,
    model: str = DEFAULT_OPENAI_MODEL,
    timeout: float = 180,
    ocr_backend: str = "rapidocr",
    picture_model: str | None = None,
    device: str = "auto",
    llm_concurrency: int = 4,
    threaded_pipeline: bool = False,
) -> None:
    """OpenAI 엔드포인트로 설정한다.

    입력:
        api_key        API 키. 생략하면 Colab Secrets 의 OPENAI_API_KEY
        model          모델명 (이미지 입력 지원 필요)
        timeout        응답 대기 초
        ocr_backend    OCR 엔진
        picture_model  그림 설명용 모델. 생략하면 model 과 동일
        device         연산 장치
        llm_concurrency / threaded_pipeline   가속 설정
    출력: 없음 (설정 요약 출력, 키는 마스킹)
    """
    _load_secrets()

    key = api_key or os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise ValueError(
            "OpenAI API 키가 없습니다.\n"
            "  · Colab 왼쪽 🔑 Secrets 탭에 OPENAI_API_KEY 를 등록하고 "
            "이 노트북에 접근 권한을 켜거나,\n"
            "  · configure_openai(api_key=...) 로 직접 넘기세요 "
            "(노트북을 공유하면 키가 함께 노출됩니다)."
        )

    os.environ["OPENAI_API_KEY"] = key
    os.environ["DOCLING_TABLE_API_URL"] = OPENAI_CHAT_URL
    os.environ["DOCLING_TABLE_API_MODEL"] = model
    os.environ["DOCLING_TABLE_API_TIMEOUT"] = str(timeout)
    os.environ["DOCLING_PICTURE_API_URL"] = OPENAI_CHAT_URL
    os.environ["DOCLING_PICTURE_API_MODEL"] = picture_model or model
    os.environ["DOCLING_PICTURE_API_TIMEOUT"] = str(timeout)
    os.environ.setdefault(
        "DOCLING_PICTURE_API_PROMPT",
        "Describe this image concisely and accurately in Korean.",
    )
    os.environ["DOCLING_OCR_BACKEND"] = ocr_backend
    _apply_device(device, ocr_backend)
    _apply_speed(llm_concurrency, threaded_pipeline)

    from docstruct.core.config import rebuild_settings

    from docstruct.checks import invalidate_caches

    settings = rebuild_settings()
    invalidate_caches()

    print("OpenAI 설정 완료")
    print(f"  endpoint : {settings.llm.url}")
    print(f"  model    : {settings.llm.model}")
    print(f"  api_key  : {settings.llm.masked_key()}")
    print(f"  timeout  : {settings.llm.timeout}초")
    print(f"  OCR      : {settings.ocr_backend}")
    print(f"  LLM 동시  : {settings.llm_concurrency}개")
    print(f"  연산 장치 : {settings.device}"
          + (f" · rapidocr={settings.rapidocr_runtime}"
             if settings.ocr_backend == "rapidocr" else ""))


def list_openai_models(*, vision_only: bool = False, limit: int = 40) -> list[str]:
    """사용 가능한 모델 목록을 조회한다.

    입력: 없음 (설정된 키 사용)
    출력: 모델 id 목록
    """
    import requests

    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise ValueError("OPENAI_API_KEY 가 설정되지 않았습니다 — configure_openai() 를 먼저 호출하세요.")

    response = requests.get(
        OPENAI_MODELS_URL,
        headers={"Authorization": f"Bearer {key}"},
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"모델 목록 조회 실패 HTTP {response.status_code}: {response.text[:200]}")

    ids = sorted(m["id"] for m in response.json().get("data", []))
    chat = [
        m for m in ids
        if m.startswith("gpt-") and not any(
            x in m for x in ("audio", "realtime", "transcribe", "tts", "image", "embedding", "moderation")
        )
    ]
    result = chat if vision_only else ids
    print(f"접근 가능한 모델 {len(ids)}개 (chat 계열 {len(chat)}개)")
    for m in result[:limit]:
        print(f"  {m}")
    if len(result) > limit:
        print(f"  ... 외 {len(result) - limit}개")
    return result


def estimate_cost(doc, *, fill_tables: bool = True, outline: bool = False) -> None:
    """이번 실행의 LLM 호출 비용을 추정한다.

    입력: doc — PageDocument
    출력: 없음 (호출 횟수와 추정 비용 출력)
    """
    pages = len(doc.pages)
    tables = [t for _, t in doc.all_tables()]
    fillable = [t for t in tables if t.needs_fill]
    pictures = len(doc.all_images())

    assess_calls = sum(1 for p in doc.pages if p.tables)
    fill_calls = len(fillable) if fill_tables else 0
    outline_calls = pages if outline else 0
    total = assess_calls + fill_calls + outline_calls + pictures

    print(f"페이지 {pages} · 표 {len(tables)} (재추출 대상 {len(fillable)}) · 그림 {pictures}")
    print()
    print(f"  표 평가    : {assess_calls}회  (페이지 이미지 포함)")
    print(f"  표 재추출  : {fill_calls}회  (페이지 이미지 포함)")
    print(f"  그림 설명  : {pictures}회  (Docling 내부 호출)")
    print(f"  목차 추출  : {outline_calls}회")
    print("  ─────────────────────")
    print(f"  합계       : {total}회")
    print()
    print("이미지가 붙는 호출은 텍스트 전용보다 입력 토큰이 훨씬 큽니다.")
    print("비용을 줄이려면: FILL_TABLES=False 로 판정만 먼저 보거나,")
    print("저비용 모델(예: gpt-5.6-luna)로 시작하세요.")


# ── PDF 파싱 실패 페이지 대응 --------------------------------------------

def retry_failed_pages(src, out_dir, *, backend: str = "pypdfium2", **kwargs):
    """다른 PDF 백엔드로 다시 처리한다.

    입력:
        src, out_dir           원본과 출력 위치
        backend                시도할 PDF 백엔드
        force_full_page_ocr    텍스트 레이어를 무시하고 전면 OCR
    출력: PageDocument (실패 페이지가 남으면 안내 출력)
    """
    import os

    from docstruct.core.config import rebuild_settings
    from docstruct import build_document
    from docstruct.checks import invalidate_caches

    os.environ["DOCLING_PDF_BACKEND"] = backend
    if kwargs.pop("force_full_page_ocr", False):
        os.environ["DOCLING_FORCE_FULL_PAGE_OCR"] = "true"

    settings = rebuild_settings()
    invalidate_caches()   # Docling 컨버터 싱글톤을 버려야 새 백엔드가 적용됩니다
    print(f"PDF 백엔드={settings.pdf_backend} · 전면OCR={settings.force_full_page_ocr} 로 재시도")

    doc = build_document(src, out_dir=out_dir, **kwargs)
    if doc.failed_pages:
        print(f"\n여전히 실패: {doc.failed_pages}")
        print("  → force_full_page_ocr=True 를 추가로 시도해 보세요.")
    else:
        print("\n실패 페이지 없이 완료")
    return doc


# ── GPU 가속 ---------------------------------------------------------------

def _apply_device(device: str, ocr_backend: str) -> None:
    """연산 장치 환경변수를 세팅한다.

    입력: device — auto|cpu|cuda, ocr_backend — OCR 엔진명
    출력: 없음
    비고: auto 면 CUDA 가용 여부로 결정하고, cuda 면 RapidOCR 런타임도 GPU 용으로 맞춘다
    """
    if device == "auto":
        detected = "cuda" if _cuda_available() else "cpu"
        os.environ["DOCLING_DEVICE"] = detected
        device = detected
    else:
        os.environ["DOCLING_DEVICE"] = device

    if device == "cuda" and ocr_backend == "rapidocr":
        os.environ.setdefault("DOCLING_RAPIDOCR_RUNTIME", "torch")


def _apply_speed(llm_concurrency: int, threaded_pipeline: bool) -> None:
    """가속 환경변수를 세팅한다.

    입력: llm_concurrency — LLM 동시 호출 수, threaded_pipeline — 단계 병렬화 여부
    출력: 없음
    """
    os.environ["DOCLING_LLM_CONCURRENCY"] = str(max(1, int(llm_concurrency)))
    os.environ["DOCLING_THREADED_PIPELINE"] = "true" if threaded_pipeline else "false"


def _cuda_available() -> bool:
    """CUDA 사용 가능 여부.

    입력: 없음
    출력: torch 로 확인한 결과. torch 미설치면 False
    """
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def check_gpu() -> bool:
    """GPU 사용 가능 여부를 확인한다.

    입력: 없음
    출력: 사용 가능하면 True (장치명·메모리·가속 대상 출력)
    """
    try:
        import torch
    except ImportError:
        print("⚠️  torch 미설치 — docling(PDF)을 설치하지 않았다면 정상입니다.")
        print("    HWP/HWPX 만 다루면 GPU 가 필요 없습니다.")
        return False

    available = bool(torch.cuda.is_available())
    print(f"torch      : {torch.__version__}")
    print(f"CUDA 사용   : {available}")

    if not available:
        print()
        print("⚠️  GPU 런타임이 아닙니다.")
        print("    런타임 → 런타임 유형 변경 → 하드웨어 가속기: T4 GPU")
        print("    (변경 시 런타임이 재시작되므로 1번 셀부터 다시 실행해야 합니다)")
        return False

    name = torch.cuda.get_device_name(0)
    total = torch.cuda.get_device_properties(0).total_memory / 1024**3
    print(f"장치       : {name} ({total:.1f} GB)")
    print()
    print("가속 대상  : Docling 레이아웃 모델 · TableFormer · (런타임에 따라) OCR")
    print("가속 안 됨 : PDF 텍스트 추출, 페이지 렌더, 표 평가·재추출 LLM(원격 API)")
    return True
