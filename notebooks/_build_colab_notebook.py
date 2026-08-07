"""preview_colab.ipynb 생성 스크립트."""
from __future__ import annotations

import json
from pathlib import Path

CELLS: list[tuple[str, str]] = []


def md(text: str) -> None:
    CELLS.append(("markdown", text.strip("\n")))


def code(text: str) -> None:
    CELLS.append(("code", text.strip("\n")))


# ---------------------------------------------------------------- 안내
md("""
# 문서 파싱 결과 확인 — Google Colab

HWP / HWPX / PDF를 업로드하면 파싱·구조화 결과를 확인합니다.

## 로컬 Jupyter 판과 다른 점

| | 로컬 | Colab |
|--|--|--|
| 코드 | 이미 있음 | **매 세션 반입** (아래 2번 셀) |
| 의존성 | 한 번 설치 | **매 세션 설치** (docling은 수 분) |
| 설정 | `app/.env` | 코드 또는 Colab Secrets |
| LLM 서버 | 사내망에서 접근 | **대개 방화벽에 막힘** — 5번 셀에서 확인 |
| 결과 파일 | 디스크에 남음 | **런타임 종료 시 소멸** — 마지막 셀에서 다운로드 |

> ⚠️ **런타임을 재시작하면 1~3번 셀을 다시 실행해야 합니다.**
""")

# ---------------------------------------------------------------- 1. 설치
md("""
## 1. 의존성 설치

> 💡 **PDF 를 다루면 먼저 GPU 런타임으로 바꾸세요** —
> 런타임 → 런타임 유형 변경 → 하드웨어 가속기 **T4 GPU**.
> 나중에 바꾸면 런타임이 재시작되어 1번 셀부터 다시 해야 합니다.
> (HWP/HWPX 만 다루면 GPU 는 필요 없습니다.)

PDF를 안 다룰 거면 `PDF = False` 로 두세요 — 설치가 10배 빨라집니다
(docling이 torch 등 대용량 패키지를 끌어옵니다).
""")

code('''
PDF = True          # PDF 파싱 필요 여부 (docling 설치)
TESSERACT = False   # DOCLING_OCR_BACKEND=tesseract 를 쓸 때만 True

# --- 부트스트랩: 아직 docstruct 가 없으므로 pip 명령을 직접 실행합니다 ---
import subprocess, sys

BASE = ["beautifulsoup4", "requests", "python-dotenv", "olefile", "six",
        "pyhwp", "python-hwpx", "pypdfium2", "pillow"]
print("기본 파서 설치 중...")
subprocess.call([sys.executable, "-m", "pip", "install", "-q", *BASE])

if PDF:
    print("Docling 설치 중 — 수 분 걸립니다...")
    subprocess.call([sys.executable, "-m", "pip", "install", "-q",
                     "docling", "rapidocr-onnxruntime"])
if TESSERACT:
    subprocess.call(["apt-get", "-qq", "install", "-y",
                     "tesseract-ocr", "tesseract-ocr-kor", "tesseract-ocr-eng"])

import importlib.util
for mod, label in [("bs4","beautifulsoup4"), ("hwp5","pyhwp"), ("hwpx","python-hwpx"),
                   ("pypdfium2","pypdfium2"), ("docling","docling")]:
    ok = importlib.util.find_spec(mod) is not None
    print(f"  {'OK  ' if ok else 'MISS'} {label}")
'''.strip())

# ---------------------------------------------------------------- 2. 코드 반입
md("""
## 2. 코드 반입

`docstruct-local.zip` 을 업로드하세요. 실행하면 파일 선택 창이 뜹니다.

> Git 저장소가 있다면 아래 셀 대신 이렇게 해도 됩니다.
> ```python
> !git clone <저장소> /content/repo
> import sys; sys.path.insert(0, "/content/repo/app")
> ```
""")

code('''
from google.colab import files
import shutil, sys
from pathlib import Path

print("docstruct-local.zip 을 선택하세요.")
uploaded = files.upload()
zip_name = next(iter(uploaded))

dest = Path("/content/app")
if dest.exists():
    shutil.rmtree(dest)
dest.mkdir(parents=True)
shutil.unpack_archive(zip_name, str(dest))

# zip 구조가 어떻든 docstruct/ 를 담은 디렉터리를 찾습니다.
def _find_root(base: Path):
    if (base / "docstruct").is_dir():
        return base
    for c in sorted(base.rglob("docstruct")):
        if c.is_dir() and (c / "pipeline.py").is_file():
            return c.parent
    return None

APP_ROOT = _find_root(dest)
if APP_ROOT is None:
    raise RuntimeError(f"{dest} 안에서 docstruct 패키지를 찾지 못했습니다.")

sys.path.insert(0, str(APP_ROOT))
print(f"APP_ROOT = {APP_ROOT}")
'''.strip())

# ---------------------------------------------------------------- 3. import
md("## 3. 모듈 로드")

code('''
import logging, warnings
warnings.filterwarnings("ignore")
logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
logging.getLogger("docling").setLevel(logging.WARNING)

from docstruct import build_document
from docstruct import preview, report
from docstruct import colab
from docstruct.checks import show_environment
from docstruct.nbui import FilePicker
from docstruct.pipeline import SUPPORTED_SUFFIXES

print("로드 완료 —", ", ".join(SUPPORTED_SUFFIXES))
'''.strip())

# ---------------------------------------------------------------- 4. 설정
md("""
## 4. LLM 설정 — OpenAI

사내 LLM 은 Colab 에서 방화벽에 막히므로 **OpenAI 를 씁니다.**
코드가 보내는 페이로드는 이미 OpenAI `/v1/chat/completions` 형식이라
엔드포인트와 인증 헤더만 바뀝니다.

### 키 등록 (필수)

왼쪽 **🔑 Secrets** 탭 → `OPENAI_API_KEY` 추가 → 이 노트북에 **접근 권한 켜기**.

> ⚠️ `configure_openai(api_key="sk-...")` 로 직접 적으면 **노트북을 공유할 때 키가 함께 나갑니다.**
> Secrets 를 쓰세요.
""")

code("""
colab.configure_openai(
    # api_key 는 Secrets 의 OPENAI_API_KEY 에서 자동으로 읽습니다.
    model="gpt-5.6-luna",     # 이미지 입력 지원 모델이어야 합니다
    timeout=180,
    ocr_backend="rapidocr",
    device="auto",            # auto | cpu | cuda — auto 는 GPU 있으면 자동 사용

    # ── 속도 ────────────────────────────────────────────────
    llm_concurrency=4,        # 표 평가·재추출 동시 호출. 여기가 가장 큰 지렛대입니다.
                              # 429(rate limit)가 자주 뜨면 2로 낮추세요.
    threaded_pipeline=False,  # Docling 단계 병렬 — Colab 무료는 vCPU 2개라 효과 제한적
)
""".strip())

code("""
# 이 키로 실제 접근 가능한 모델 확인 (라인업이 자주 바뀝니다)
# colab.list_openai_models(vision_only=True)
""".strip())

md("""
### 사내 LLM 을 쓰려면 (사내망에서 실행하는 경우에만)

```python
colab.configure(url="http://<host>:<port>/v1/chat/completions",
                model="/model/<model-id>/", use_secrets=True)
```

### 글자가 깨져 나올 때

PDF 폰트의 ToUnicode 매핑이 깨져 있으면 텍스트 레이어에서 엉뚱한 글자가
나옵니다(`d띠는正음허리古키리릅...`). 이건 OCR 오류가 아니라 **PDF 안에
그렇게 들어 있는 것**이라, 텍스트 레이어를 무시하고 전면 OCR 해야 합니다.

```python
colab.configure(..., force_full_page_ocr=True)   # 세션 전체
```

한 파일만 시험한다면 실행 옵션으로 주는 편이 낫습니다.

```python
DocStruct("문서.pdf", force_full_page_ocr=True).run()
```

느려지므로 필요한 문서에만 켜세요.
""")

# ---------------------------------------------------------------- 5. 연결성
md("""
## 5. 연결 확인

실제로 1회 호출해 봅니다. 키 오류·모델명 오타·권한 문제가 여기서 드러납니다.
""")

code("""
ok, message = colab.check_llm_reachable(timeout=15)  # = docstruct.checks.check_llm_reachable
print(("✅ " if ok else "⚠️ ") + message)

USE_LLM = ok
if not ok:
    print("\\n→ LLM 단계 없이 파싱만 진행합니다. 파싱 결과 자체는 동일하게 볼 수 있습니다.")
""".strip())

show_environment_cell = True

code("show_environment()")

md("""
### GPU 확인

Docling 의 레이아웃 모델과 TableFormer 가 GPU 로 돌면 PDF 처리가 크게 빨라집니다.
표 평가·재추출은 OpenAI 원격 호출이라 GPU 와 무관합니다.
""")

code("colab.check_gpu()")

# ---------------------------------------------------------------- 6. 파일
md("""
## 6. 문서 업로드

실행하면 파일 선택 창이 뜹니다. HWP / HWPX / PDF 중 하나를 고르세요.
""")

code('''
picker = FilePicker(
    work_dir=Path("/content/work"),
    sample_dir=Path("/content/samples"),
    suffixes=SUPPORTED_SUFFIXES,
)
SRC = picker.colab_upload()
'''.strip())

# ---------------------------------------------------------------- 7. 옵션
md("""
## 7. 옵션

`USE_LLM` 이 `False` 면 아래 LLM 옵션은 자동으로 무시됩니다.

**표 파싱이 어디서 깨지는지 보려면** `FILL_TABLES = False` 가 가장 유용합니다 —
판정 근거만 보여주고 원본 markdown은 건드리지 않습니다.
""")

code('''
ASSESS_TABLES = True    # 표 판정 (table / text / image + 품질)
FILL_TABLES   = True    # 불량 표만 LLM 재추출
FILL_ALL      = False   # 품질 무시하고 전부 재추출
RENDER_PAGES  = True    # 표가 있는 PDF 페이지를 PNG로 렌더
RENDER_SCALE  = 2.0

OUT_DIR = Path("/content/out") / SRC.stem
print(f"대상 파일    : {SRC}")
print(f"출력 디렉터리 : {OUT_DIR}")
print(f"LLM 단계     : {'사용' if USE_LLM else '생략 (연결 불가)'}")
'''.strip())

# ---------------------------------------------------------------- 8. 실행
md("""
## 8. 실행

> 💰 **비용 주의** — 표 평가는 페이지당 1회, 표 재추출은 표당 1회 호출하며
> **매번 페이지 이미지가 함께 나갑니다.** 페이지가 많은 PDF 는 호출이 빠르게 늘어납니다.
> 처음에는 `FILL_TABLES = False` 로 판정만 보거나, 저비용 모델로 시작하세요.
> 실행 후 9번 셀에서 실제 호출 횟수를 확인할 수 있습니다.

PDF 첫 실행은 Docling 모델 다운로드로 몇 분 걸립니다.
같은 런타임 안에서는 캐시되므로 두 번째부터 빠릅니다.
""")

code('''
import time

_t0 = time.perf_counter()
doc = build_document(
    SRC,
    assess_tables=USE_LLM and ASSESS_TABLES,
    fill_tables=USE_LLM and FILL_TABLES,
    fill_all=FILL_ALL,
    render_pages=RENDER_PAGES,
    out_dir=OUT_DIR,
    render_scale=RENDER_SCALE,
)
print(f"\\n완료 — {time.perf_counter() - _t0:.1f}초")
'''.strip())

# ---------------------------------------------------------------- 9~12 결과
md("## 9. 요약")
code("preview.show_summary(doc)")

code("""
# LLM 호출 횟수 집계 (정확한 견적이 아니라 규모 감각용)
colab.estimate_cost(doc, fill_tables=FILL_TABLES)
""".strip())

md("""
### 처리 경로

각 페이지가 **어떤 경로로** 처리됐는지 보여줍니다.
`text_layer` = PDF 내장 텍스트를 읽음, `ocr` = 이미지 인식, `mixed` = 둘 다,
`none` = 텍스트 없음(스캔인데 OCR 실패 의심).
HWP 는 `hwpml-xml`/`pyhwp-html`(표 보존) vs `olefile-text`(**표 손실**) 로 갈립니다.
""")

code("preview.show_pipeline(doc)")

md("""
## 10. 표 판정

`재추출` 이 ✅ 인 표는 아래에 **전(빨강) / 후(초록)** 이 나란히 표시됩니다.
LLM을 못 쓰는 상태면 판정 없이 원본 markdown만 나옵니다.
""")
code("preview.show_tables(doc)")

md("""
## 11. 본문

각 페이지 위 **처리 경로**를 펼치면 그 페이지가 거친 모듈이 순서대로 나옵니다
(텍스트 레이어 / OCR / 표 재추출 등. '미측정'은 정상이며 출처 구분만 안 된 상태입니다)

**레이아웃 인식** 을 펼치면 모델이 각 영역에 붙인 라벨과 파이프라인 처리 결과를
대조할 수 있습니다 — 표 문제가 모델 오인식인지 변환 문제인지 여기서 갈립니다.. `!` 는 경고, `–` 는 생략된 단계입니다.

`<table N>` 태그는 markdown에서 안 보이므로 **▦ table N** 라벨로 바꿔 표시합니다.
표 자체는 실제 표로 렌더링됩니다.
""")

code('''
# Colab은 ipywidgets 슬라이더가 불안정할 수 있어 페이지 번호를 직접 지정합니다.
PAGE = 0     # 0부터 시작

preview.show_page(doc.pages[PAGE])
'''.strip())

code('''
# 전체 페이지 한 번에 보기 (페이지가 많으면 limit 조정)
# preview.show_pages(doc, limit=5, show_image=False)
'''.strip())

md("## 12. 추출된 이미지")
code("preview.show_images(doc)")

# ---------------------------------------------------------------- 13. 반출
md("""
## 13. 결과 저장 ⚠️

**Colab 런타임이 끝나면 파일이 전부 사라집니다.** 확인이 끝났으면 내려받으세요.
""")

code('''
report.write_markdown(doc, OUT_DIR / "document.md")
report.write_json(doc, OUT_DIR / "document.json")
report.write_tables_report(doc, OUT_DIR / "tables.md")

colab.download_outputs(OUT_DIR)      # zip 으로 묶어 브라우저 다운로드
'''.strip())

code('''
# Google Drive에 남기려면:
# colab.mount_drive()
# colab.save_to_drive(OUT_DIR)
'''.strip())

# ---------------------------------------------------------------- 트러블슈팅
md("""
---

## 자주 겪는 상황

**5번 셀에서 `401` / `invalid_api_key`**
Secrets 에 `OPENAI_API_KEY` 를 등록했는지, **이 노트북에 접근 권한을 켰는지** 확인하세요.
Secrets 는 등록만 하고 토글을 안 켜면 읽히지 않습니다.

**`model_not_found`**
모델명이 틀렸거나 이 키의 티어에서 접근이 안 되는 모델입니다.
`colab.list_openai_models(vision_only=True)` 로 실제 목록을 확인하세요.

**`429 rate limit` 이 반복됨**
클라이언트가 `Retry-After` 를 존중해 최대 3회 재시도합니다. 그래도 계속 막히면
티어 한도 문제이므로 페이지가 적은 문서로 나눠 돌리세요.

**표 재추출 결과가 비어 있음**
추론형 모델이 사고 토큰만 쓰고 본문을 못 낸 경우입니다 (로그에 '길이 제한' 경고).
페이지를 나누거나 다른 모델을 시도하세요.

**사내 LLM 으로 5번 셀 연결 실패**
사내망 전용 주소라 Colab에서 막힌 것입니다. OpenAI 를 쓰거나(4번 셀 기본값),
사내망에서 로컬 Jupyter 판(`notebooks/preview.ipynb`)을 쓰세요.

**`Stage preprocess failed for run N, pages [...]: 'utf-8' codec can't decode byte ...`**
PDF 안의 문자열이 레거시 인코딩(EUC-KR 등)이라 기본 파서가 디코딩에 실패한 것입니다.
**예외가 아니라 로그로만 남아서 해당 페이지가 조용히 결과에서 빠집니다** —
9번 셀 요약의 `⚠ 파싱 실패` 항목에서 어느 페이지가 빠졌는지 확인하세요.

```python
# 다른 백엔드로 재시도 (문자열 처리 방식이 달라 대개 이걸로 해결됩니다)
doc = colab.retry_failed_pages(SRC, OUT_DIR, backend="pypdfium2",
                               assess_tables=False, fill_tables=False)

# 그래도 안 되면 텍스트 레이어를 무시하고 전면 OCR (느림)
doc = colab.retry_failed_pages(SRC, OUT_DIR, backend="auto",
                               force_full_page_ocr=True,
                               assess_tables=False, fill_tables=False)
```

**본문이 비어 있음 (PDF)**
스캔 문서인데 OCR이 안 돈 경우입니다. Docling은 텍스트 레이어가 없는 영역만 OCR하는데,
깨진 텍스트 레이어가 얹혀 있으면 건너뜁니다.
이제 환경변수로 켤 수 있습니다:

```python
import os
os.environ["DOCLING_FORCE_FULL_PAGE_OCR"] = "true"
from docstruct.core.config import rebuild_settings; from docstruct.checks import invalidate_caches
rebuild_settings(); invalidate_caches()
```

**표가 이상하게 잘림**
10번 셀에서 판정을 먼저 확인하세요. `insufficient` 는 대개 페이지 경계 분할입니다.
`RENDER_PAGES=True` 여야 재추출 품질이 나옵니다.

**HWP 표가 통째로 사라짐**
`hwp5html` 이 없으면 olefile 폴백으로 떨어지고 이때 표 구조가 유실됩니다.
4번 셀의 `HWP 파싱` 항목을 확인하세요.

**`ModuleNotFoundError: docstruct`**
런타임이 재시작되어 `sys.path` 가 초기화된 것입니다. 2~3번 셀을 다시 실행하세요.

**너무 느림**
대부분 원격 LLM 대기입니다. 9번 셀 아래 `pipeline.md` 의 단계별 소요 시간표를 보세요.
`llm_concurrency` 를 올리면 거의 선형으로 줄지만, OpenAI 티어 한도를 넘으면
429 가 나면서 오히려 느려집니다. 4 → 8 정도로 올려보고 로그에
`LLM HTTP 429` 가 반복되면 되돌리세요.

GPU 는 파싱 구간(레이아웃·TableFormer·OCR)만 줄입니다 — 이 구간이 전체의
10% 미만이면 GPU 를 켜도 체감이 거의 없습니다.

**설치가 너무 오래 걸림**
PDF를 안 다루면 1번 셀의 `PDF = False` 로 두세요. docling과 torch를 건너뜁니다.

**다른 파일로 바꿔 돌리기**
6번 셀(업로드)부터 다시 실행하면 됩니다. 1~5번은 세션당 한 번이면 충분합니다.
""")

# ---------------------------------------------------------------- 빌드
nb = {
    "cells": [
        {
            "cell_type": kind,
            "id": f"cell-{i:02d}",
            "metadata": {},
            "source": src.splitlines(keepends=True),
            **({"execution_count": None, "outputs": []} if kind == "code" else {}),
        }
        for i, (kind, src) in enumerate(CELLS)
    ],
    "metadata": {
        "colab": {"provenance": [], "toc_visible": True},
        "kernelspec": {
            "display_name": "Python 3",
            "language": "python",
            "name": "python3",
        },
        "language_info": {"name": "python"},
    },
    "nbformat": 4,
    "nbformat_minor": 5,
}

out = Path(__file__).resolve().parent / "preview_colab.ipynb"
out.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"생성: {out}  (셀 {len(CELLS)}개)")
