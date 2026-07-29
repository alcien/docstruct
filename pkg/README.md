# docstruct — 문서 파싱·구조화 로컬 확인 도구

HWP / HWPX / PDF를 페이지 단위로 파싱해 **본문 markdown + 표 메타데이터 + 이미지**로
구조화하고, 결과를 로컬 파일로 덤프합니다. 벡터 DB·검색은 포함하지 않습니다.

## 설치

```bash
# Windows 는 한 번만
git config --global credential.allowUnsafeRemotes true

pip install "docstruct @ git+http://183.96.152.133/mjseo/docstruct.git@v0.1.11"

# 포맷별 의존성 (필요한 것만)
pip install "docstruct[hwp] @ git+http://183.96.152.133/mjseo/docstruct.git@v0.1.11"
pip install "docstruct[pdf] @ git+http://183.96.152.133/mjseo/docstruct.git@v0.1.11"
pip install "docstruct[all] @ git+http://183.96.152.133/mjseo/docstruct.git@v0.1.11"
```

인증·오류 대응과 wheel 배포는 `INSTALL.md` 를 보세요.

## 실행

```bash
docstruct 문서.pdf                    # out/문서/ 에 결과
docstruct 문서.pdf --no-llm           # 완전 오프라인 (파싱만)
docstruct 문서.hwp -o 결과
docstruct 샘플들/ --glob '*.pdf' --progress   # 일괄 처리
docstruct --check                     # 환경·LLM 연결 점검
```

포맷 변환만 필요하면 기존 CLI가 그대로 있습니다:

```bash
python -m docstruct.converters.cli 문서.hwp -f markdown -o 문서.md
```

## Windows

`WINDOWS.md` 를 참고하세요. 요약:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -c "import shutil; print(shutil.which('hwp5html'))"   # 경로가 나와야 HWP 표가 살아납니다
$env:PYTHONPATH = "."
python -m docstruct.cli 문서.pdf --no-llm
```

## 설정

모든 환경변수는 `app/.env` 하나에서만 오고, **`core/config.py` 가 유일한 읽기 지점**입니다.
코드 어디에도 `os.environ.get()` 이 흩어져 있지 않습니다.

```python
from core.config import get_settings
s = get_settings()
s.llm                    # 표 평가/재추출/목차용 엔드포인트 (None 이면 해당 단계 생략)
s.docling_picture        # Docling 그림 설명용 엔드포인트
s.ocr_backend            # rapidocr | tesseract | easyocr | auto
s.picture_area_threshold
```

| 변수 | 적용 경로 |
|--|--|
| `DOCLING_TABLE_API_URL/MODEL/TIMEOUT` | docstruct — 표 평가·재추출·목차 |
| `DOCLING_PICTURE_API_*`, `..._AREA_THRESHOLD` | Docling 그림 설명 |
| `DOCLING_OCR_BACKEND` | PDF OCR |
| `DOCLING_TABLE_LLM`, `..._LLM_MODE`, `DOCLING_TABLE_FORMAT` | **`converters.cli` 전용** — docstruct 파이프라인엔 무영향 |

`TABLE_API_*` 를 비우면 `PICTURE_API_*` 값을 필드별로 물려받습니다.
URL은 `/v1/chat/completions` 접미사를 생략해도 자동으로 붙습니다.

노트북을 켜둔 채 `.env` 를 고쳤다면 `reload_environment()` 를 호출해야 반영됩니다
(LLM 어댑터·Docling 파이프라인이 캐시되기 때문).

## 노트북으로 확인 (권장)

```bash
pip install jupyterlab ipywidgets
jupyter lab notebooks/preview.ipynb
```

셀을 위에서 아래로 한 번 실행하면, 파일 선택 셀의 **[파일 첨부]** 버튼으로 문서를 올려
파싱 결과(요약 · 표 판정 · 본문 · 이미지)를 바로 볼 수 있습니다.
`notebooks/samples/` 에 문서를 넣어두면 드롭다운에도 뜨고, 경로 직접 입력도 됩니다.

파일 선택은 `docstruct/nbui.py` 의 `FilePicker` 가 담당하며, **실행 시점에 위젯 상태를
직접 읽습니다.** 콜백으로 전역 변수를 세팅하지 않으므로 같은 파일 재업로드,
위젯 프론트엔드 미설치, Run All 순서 꼬임 어느 경우에도 선택이 유실되지 않습니다.
위젯을 못 쓰는 환경에서는 `picker.set_path("/경로/문서.pdf")` 가 동일하게 동작합니다.

## Google Colab

`notebooks/preview_colab.ipynb` 를 Colab에 올리고, 이 프로젝트 zip을 2번 셀에서 업로드하면 됩니다.

로컬 판과 다른 점:

| | 로컬 | Colab |
|--|--|--|
| 코드 | 이미 있음 | 매 세션 반입 (zip / git / Drive) |
| 의존성 | 한 번 설치 | 매 세션 설치 — docling은 수 분 |
| 설정 | `app/.env` | `colab.configure()` 또는 Colab Secrets |
| LLM 서버 | 사내망에서 접근 | **대개 방화벽에 막힘** |
| 결과 파일 | 디스크에 남음 | 런타임 종료 시 소멸 |

⚠️ **사내 LLM 엔드포인트는 Colab에서 접근이 막히는 것이 보통입니다.**
노트북 5번 셀의 `colab.check_llm_reachable()` 이 먼저 확인하고, 막혀 있으면
`USE_LLM=False` 로 표 평가·재추출 없이 파싱만 진행합니다 — 파싱 결과 자체는 동일하게 볼 수 있습니다.
LLM 단계까지 확인하려면 사내망에서 로컬 판을 쓰세요.

### Colab + OpenAI(GPT)

사내 LLM 이 막히므로 Colab 노트북은 **OpenAI 를 기본**으로 씁니다.
코드가 보내는 페이로드는 이미 OpenAI `/v1/chat/completions` 형식
(`messages` + `image_url` 파트)이라, 엔드포인트와 인증 헤더만 바뀝니다.

```python
from docstruct import colab

colab.configure_openai(model="gpt-5.6-luna")   # 키는 Colab Secrets 의 OPENAI_API_KEY
colab.list_openai_models(vision_only=True)     # 접근 가능한 모델 확인
ok, msg = colab.check_llm_reachable()          # 키·모델·권한 사전 검증
colab.estimate_cost(doc, fill_tables=True)     # LLM 호출 횟수 집계

colab.download_outputs(OUT_DIR)                # 결과 zip 다운로드
colab.save_to_drive(OUT_DIR)                   # 또는 Drive 보존
```

표 재추출은 **페이지 이미지를 함께 보내므로** 이미지 입력을 지원하는 모델이어야 하고,
호출당 입력 토큰이 큽니다. 처음에는 `FILL_TABLES=False` 로 판정만 보시길 권합니다.

사내 LLM 을 쓰려면 `colab.configure(url=..., model=...)` 입니다.

## GPU 가속

Docling 의 **레이아웃 모델 · TableFormer · OCR** 이 GPU 대상입니다.
PDF 텍스트 추출·페이지 렌더·표 평가 LLM(원격 API)은 해당 없습니다.

```
DOCLING_DEVICE=cuda                # auto | cpu | cuda | mps(Apple)
DOCLING_NUM_THREADS=8              # 0 이면 Docling 기본값
DOCLING_RAPIDOCR_RUNTIME=torch     # onnxruntime(기본,CPU) | torch | openvino | paddle
```

`DOCLING_DEVICE=cuda` 만으로는 OCR 이 CPU 에 남습니다 — RapidOCR 기본 런타임이
CPU 전용이라 `torch` 로 바꾸거나 `onnxruntime-gpu` 를 설치해야 합니다.

Colab 에서는 **런타임 → 런타임 유형 변경 → T4 GPU** 로 바꾼 뒤
`colab.configure_openai(device="auto")` 면 자동 감지되고, `colab.check_gpu()` 로 확인합니다.

## 라이브러리 사용

```python
from docstruct import DocStruct

ds = DocStruct("보고서.pdf")
ds.set(device="cuda", llm_concurrency=8)   # 설정 (연쇄 호출 가능)
ds.run()                                   # 실행
ds.to_json("결과.json")                     # JSON 저장

ds.get("device")        # 'cuda'
ds.options()            # 지정한 설정 전체
len(ds.tables)          # 표 개수
ds.save("out/")         # 모든 산출물 (json + md 4종)
```

### 여러 문서 한 번에

```python
from docstruct import DocStructBatch

batch = DocStructBatch("문서모음/", pattern="*.pdf", progress=True)
batch.set(device="cuda")
batch.run()                      # 실패한 문서가 있어도 계속 진행
batch.to_json("결과/")            # 문서별 JSON
batch.to_json("전체.json", combined=True)   # 하나로 합쳐서

print(batch.summary())           # 성공·실패 건수와 사유
print(batch.failures)            # [(경로, 예외), ...]
```

입력은 네 가지를 받습니다.

```python
DocStructBatch("문서모음/")                    # 디렉터리
DocStructBatch("문서모음/", pattern="*.pdf")   # 디렉터리 + 패턴
DocStructBatch("docs/보고서*.hwp")             # glob 문자열
DocStructBatch(["a.pdf", "b.hwp"])            # 경로 목록
```

`progress=True` 면 문서 단위 진행과 문서 안의 표 평가·재추출 진행이 함께 표시됩니다.
tqdm 이 없으면 로그 한 줄씩으로 대체되며 동작에는 영향이 없습니다.

CLI 도 마찬가지입니다.

```bash
docstruct 문서모음/ --glob "*.pdf" -o out/ --progress
```

한 줄로 쓸 수도 있습니다.

```python
from docstruct import structure, structure_to_json

data = structure("보고서.pdf", assess_tables=False)          # → dict
path = structure_to_json("보고서.pdf", "결과.json")           # → JSON 파일
```

설정 키 목록은 `option_keys()` 로 확인합니다. 크게 세 갈래입니다.

| 갈래 | 키 |
|------|-----|
| LLM | `llm_url` `llm_model` `llm_key` `llm_timeout` `llm_concurrency` |
| PDF 파싱 | `pdf_backend` `ocr_backend` `ocr_lang` `force_full_page_ocr` `generate_parsed_pages` `code_formula_enrichment` |
| 성능 | `device` `num_threads` `rapidocr_runtime` `threaded_pipeline` |
| 실행 | `assess_tables` `fill_tables` `fill_all` `render_pages` `render_scale` `out_dir` |

설정은 인스턴스마다 독립적이며, `run()` 실행 동안에만 적용된 뒤 원래 값으로 돌아갑니다.

### 여러 사람이 같은 서버에서 쓸 때

접속 세션이 다르면 **프로세스가 분리되므로 설정이 서로 영향을 주지 않습니다**
(환경변수가 프로세스마다 별도입니다). 같은 프로세스에서 여러 스레드가 동시에
`run()` 해도 설정 교체 구간이 락으로 직렬화되어 안전합니다.

다만 다음은 프로세스가 달라도 공유되므로 주의하세요.

| 자원 | 영향 | 대처 |
|------|------|------|
| 출력 디렉터리 | 같은 경로면 덮어씀 | `save("out/", unique=True)` |
| Docling 모델 캐시 | 첫 실행 동시 시작 시 다운로드 경쟁 | 한 번 받아두면 무관 |
| GPU 메모리 | 같은 장치를 나눠 씀 | 프로세스별 `device` 분리 |
| LLM 사용량 한도 | 동시 호출 합계가 한도 초과 | `llm_concurrency` 하향 |

## 출력물

```
out/문서/
  document.md      본문 (페이지 구분 + <table N> 블록 인라인)
  document.json    전체 구조 (페이지·표·이미지 메타 전부)
  tables.md        표별 판정표 + 재추출 전/후 비교
  pipeline.md      페이지별 처리 경로 (텍스트레이어/OCR, 추출기, 렌더·평가·재추출)
  layout.md        레이아웃 모델 인식 결과 (라벨·좌표 + 파이프라인 처리 결과 대조)
  outline.md       의미 경로 트리 (--outline 지정 시)
  pages/           표가 있는 페이지 PNG
  images/          문서에서 추출한 그림
```

## Windows

`WINDOWS.md` 를 참고하세요. 요약:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -c "import shutil; print(shutil.which('hwp5html'))"   # 경로가 나와야 HWP 표가 살아납니다
$env:PYTHONPATH = "."
python -m docstruct.cli 문서.pdf --no-llm
```

## 설정 (core/config.py)

`DOCLING_TABLE_API_*` / `DOCLING_PICTURE_API_*` / `DOCLING_OCR_BACKEND` 는
전부 `core/config.py` 하나에서만 읽습니다. 예전에는 `infrastructure/llm/client.py`,
`converters/pdf/docling_backend.py`, `converters/pdf/table_extract.py` 세 곳이
각자 `os.environ`을 읽어서, 셋 중 하나만 고치면 나머지는 안 바뀌는 문제가 있었습니다.

`.env`를 읽을 때 자동으로 다음을 검사하고 문제가 있으면 경고를 남깁니다:

- **줄바꿈으로 잘린 값** — 에디터가 긴 URL을 자동 줄바꿈해서 저장한 경우
- **`/v1/chat`에서 끊긴 URL** — `http://`로는 정상이지만 경로가 중간에 잘린 경우
- **셸에 이미 설정된 값이 `.env`를 무시하는 경우** — `export`가 파일보다 우선하는 게
  기본 동작(`override=False`)인데, 이걸 모르면 "고쳐도 안 먹는다"로 보입니다

노트북/REPL처럼 프로세스가 오래 사는 환경에서 `.env`를 고쳤다면:

```python
from docstruct.checks import reload_environment
reload_environment()   # 커널 재시작 없이 새 값 반영 (LLM 어댑터·Docling 파이프라인 캐시까지 갱신)
```

## 파이프라인

```
파일
 └─ converters/         포맷별 파싱 (Docling / pyhwp / python-hwpx)
     └─ docstruct/extractors/    → PageContent[] (본문 + <table N> 블록)
         └─ media/page_render     표 있는 페이지 PNG 렌더        [PDF, 선택]
             └─ tables/assess     표 판정: table|text|image + 품질  [LLM, 선택]
                 └─ tables/fill   wrong/insufficient만 재추출      [LLM, 선택]
                     └─ tables/tags  블록 정규화
                         └─ report/  md · json · 표 리포트
```

LLM 단계는 전부 선택입니다. `--no-llm`이면 Docling/pyhwp 파싱 결과만 그대로 나옵니다.

## 플래그

| 플래그 | 효과 |
|--------|------|
| `--no-llm` | 표 평가·재추출·목차 전부 생략 (네트워크 불필요) |
| `--no-assess` | 평가 생략 (표를 원본 그대로 둠) |
| `--no-fill` | 평가만 하고 재추출은 안 함 — **판정 결과만 보고 싶을 때** |
| `--fill-all` | 품질과 무관하게 모든 표 재추출 (LLM 호출 최대) |
| `--no-render` | 페이지 PNG 렌더 생략 (표 평가 정확도 하락) |
| `--outline` | 의미 경로 추출 (페이지당 LLM 1회 추가) |
| `-v` | DEBUG 로그 + 실패 시 traceback |
