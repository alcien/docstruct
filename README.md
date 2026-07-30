# docstruct

HWP / HWPX / PDF 를 페이지 단위로 파싱해 **본문 markdown + 표 + 이미지**로
구조화하고 JSON 으로 내보냅니다.

표가 제대로 뽑혔는지 LLM 으로 판정하고, 잘못된 표만 골라 다시 추출합니다.
LLM 없이도 파싱은 그대로 동작합니다.

```python
from docstruct import DocStruct

ds = DocStruct("보고서.pdf")
ds.run()
ds.to_json("결과.json")
```

---

## 설치

```bash
pip install "docstruct @ git+https://github.com/alcien/docstruct.git@v0.1.26"
```

HWP · HWPX · PDF 처리에 필요한 것이 모두 함께 설치됩니다 (약 5.6 GB —
docling 이 PyTorch 를 끌고 옵니다).

GPU 를 쓰지 않으면 CPU 전용 torch 를 먼저 깔아 2.7 GB 를 줄일 수 있습니다.

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install "docstruct @ git+https://github.com/alcien/docstruct.git@v0.1.26"
```

노트북 UI(파일 선택 위젯)가 필요하면 `[notebook]` 을 붙이세요.
자세한 내용은 `INSTALL.md` 를 보세요.

지원 파이썬: **3.10 ~ 3.13**

---

## 설정

### 방법 1 — 코드에서 (권장)

```python
import docstruct

docstruct.configure(
    llm_url="http://내부주소:11060/v1",      # 표 평가·재추출용 LLM
    llm_model="모델명",
    llm_concurrency=8,                       # 동시 호출 수
    device="auto",                           # auto | cpu | cuda | mps
)
```

이 설정은 프로세스 전역에 남아 이후 만드는 모든 `DocStruct` 에 적용됩니다.
반환값에서 **키는 가려집니다.**

```python
docstruct.configure(openai_key="sk-proj-...")
# → {'openai_key': 'sk-pro…abcd'}
```

API 키만 넣을 때는 전용 함수가 더 간결합니다.

```python
import getpass

docstruct.set_api_key(getpass.getpass("OpenAI 키: "))       # 대비책용
docstruct.set_api_key("sk-...", target="llm")               # 기본 LLM 용
```

### 방법 2 — `.env`

작업 디렉터리에 두면 자동으로 읽습니다 (`.env.example` 참고).

```
DOCLING_TABLE_API_URL=http://내부주소:11060/v1/chat/completions
DOCLING_TABLE_API_MODEL=모델명
DOCLING_LLM_CONCURRENCY=8
```

### 방법 3 — 문서별로

```python
ds = DocStruct("보고서.pdf")
ds.set(device="cuda", llm_concurrency=4)     # 이 문서에만, run() 동안만
```

### 우선순위

**환경변수 · `configure()` → `.env` → 내장 기본값**

현재 무엇이 적용 중인지는 이렇게 확인합니다.

```bash
docstruct --check
```

```python
from docstruct import defaults, option_keys
print(defaults())        # 내장 기본값
print(option_keys())     # 설정 가능한 키 전체
```

> 설정이 없어도 파싱·표 추출·구조화는 동작합니다.
> LLM 관련 단계(표 판정·재추출·그림 설명)만 생략됩니다.

---

## 사용

### 문서 하나

```python
from docstruct import DocStruct

ds = DocStruct("보고서.pdf")
ds.set(assess_tables=True, fill_tables=True)
ds.run()

ds.to_dict()                     # dict  — 파이썬 자료구조
ds.to_json_str()                 # str   — JSON 문자열 (파일 저장 없음)
ds.to_json("결과.json")           # Path  — 파일 저장 (반환은 경로)
ds.save("out/")                  # json + md 4종
ds.save("out/", unique=True)     # 여러 사람이 같은 경로를 쓸 때

len(ds.tables)                   # 표 개수
ds.pages[0].trace.summary()      # 이 페이지가 거친 처리 경로
print("\n".join(ds.summary()))   # 콘솔 요약
```

### 여러 문서

`DocStruct` 는 문서 하나를 깊게, `DocStructBatch` 는 여럿을 넓게 다룹니다.
**처리 경로 추적은 양쪽 모두에 있습니다** — 자세한 구분은 `API.md` 의
"어느 것을 쓰나" 를 보세요.

```python
from docstruct import DocStructBatch

batch = DocStructBatch("문서모음/", pattern="*.pdf", progress=True)
batch.run()                              # 실패해도 계속 진행

batch.to_json("결과/")                    # 문서별 JSON
batch.to_json("전체.json", combined=True) # 하나로 합쳐서

print(batch.summary())                   # 성공·실패 건수
print(batch.failures)                    # [(경로, 예외), ...]
```

입력은 네 가지를 받습니다.

```python
DocStructBatch("문서모음/")                    # 디렉터리
DocStructBatch("문서모음/", pattern="*.pdf")   # 디렉터리 + 패턴
DocStructBatch("docs/보고서*.hwp")             # glob
DocStructBatch(["a.pdf", "b.hwp"])            # 경로 목록
```

### 한 줄로

```python
from docstruct import structure, structure_to_json

data = structure("보고서.pdf", assess_tables=False)      # → dict
path = structure_to_json("보고서.pdf", "결과.json")       # → 파일
```

---

## CLI

```bash
docstruct 문서.pdf -o out/                       # 단일
docstruct 문서모음/ --glob "*.hwp" -o out/ --progress   # 일괄
docstruct 문서.pdf --no-llm                      # 완전 오프라인
docstruct --check                                # 환경·LLM 연결 확인
```

### API 키

CLI 에는 키를 인자로 직접 받는 옵션이 없습니다.
`--api-key sk-...` 형태는 셸 히스토리와 프로세스 목록(`ps`)에 남기 때문입니다.

```bash
# 1) 환경변수
export OPENAI_API_KEY=sk-...          # Linux/macOS
set OPENAI_API_KEY=sk-...             # Windows

# 2) 입력받기 — 화면에도 히스토리에도 남지 않음
docstruct 문서.pdf --ask-key

# 3) 파일에서
docstruct 문서.pdf --key-file ~/.openai_key

# 4) .env (작업 디렉터리)
echo "OPENAI_API_KEY=sk-..." > .env
```

설정 여부는 `docstruct --check` 의 `LLM 대비책` 행에서 확인합니다
(값은 가려서 표시됩니다).

| 플래그 | 효과 |
|--------|------|
| `--no-llm` | 표 평가·재추출·목차 전부 생략 (네트워크 불필요) |
| `--no-assess` | 평가 생략 (표를 원본 그대로 둠) |
| `--no-fill` | 평가만 하고 재추출 안 함 — **판정 결과만 보고 싶을 때** |
| `--fill-all` | 품질과 무관하게 모든 표 재추출 (LLM 호출 최대) |
| `--no-render` | 페이지 PNG 렌더 생략 (표 평가 정확도 하락) |
| `--outline` | 의미 경로(목차) 추출 — 페이지당 LLM 1회 추가 |
| `--progress` | 진행 막대 (tqdm 없으면 로그로 대체) |
| `--scale N` | 페이지 렌더 배율 (기본 2.0) |
| `-q` / `-v` | 요약만 / DEBUG 로그 |

종료 코드: 0 성공, 1 실패, 2 인자 오류. 자세한 내용은 `CLI.md` 를 보세요.

---

## 출력물

```
out/<문서명>/
├── document.json    전체 구조 (아래 참고)
├── document.md      본문 (표·이미지가 실제 내용으로 펼쳐짐)
├── tables.md        표별 판정 + 재추출 전/후 비교
├── pipeline.md      단계별 소요 시간 · 페이지별 처리 경로
├── layout.md        레이아웃 모델 인식 결과 (PDF)
├── pages/           페이지 PNG (표가 있는 페이지만)
└── images/          추출된 그림
```

`document.json` 구조:

```jsonc
{
  "filename": "보고서.pdf",
  "source_format": "pdf",
  "page_count": 16,
  "failed_pages": [],          // 파싱 실패로 빠진 페이지
  "pipeline": { },             // 이 실행에 적용된 설정
  "timings": { },              // 단계별 소요 시간(초)
  "pages": [{
    "page_no": 1,
    "content": "## 제1장 …",   // 표는 <table 1> 블록으로 치환
    "tables": [{
      "id": "table_1",
      "markdown": "| 구분 | 2025년 |\n|---|---|\n…",
      "content_type": "table",  // table | text | image
      "quality": "sufficient",  // sufficient | wrong | insufficient
      "original_markdown": null // 재추출됐으면 원본이 여기
    }],
    "images": [],
    "trace": { },              // 이 페이지가 거친 처리 경로
    "layout": []               // 레이아웃 모델이 인식한 영역
  }]
}
```

---

## 처리 흐름

```
파일
 └─ converters/              포맷별 파싱 (Docling / pyhwp / python-hwpx)
     └─ extractors/          → PageContent[] (본문 + <table N> 블록)
         └─ media/page_render   표 있는 페이지 PNG 렌더        [PDF, 선택]
             └─ tables/assess   표 판정: table|text|image + 품질  [LLM, 선택]
                 └─ tables/fill wrong·insufficient만 재추출      [LLM, 선택]
                     └─ tables/tags  블록 정규화
                         └─ report/  json · md
```

LLM 단계는 전부 선택입니다. 끄면 파싱 결과가 그대로 나옵니다.

**표 재추출 근거**는 PDF 는 페이지 이미지, HWP 는 원본 `<table>` HTML 을 씁니다.
HWP 는 이미지가 없어도 `rowspan`/`colspan` 이 살아 있어 구조 복원이 가능합니다.

---

## 처리 경로 확인

문서마다 어떤 경로로 처리됐는지 기록됩니다. 표가 이상할 때 원인을 가릅니다.

```python
for page in ds.pages:
    print(page.trace.summary())
    print(page.trace.log())
```

```
1. converters.pdf.converter     PDF 페이지 로드 — backend=auto
2. docling.ocr                  OCR 수행 (스캔 페이지) — rapidocr · 96셀 전부 OCR
3. docstruct.extractors.pdf     요소 분류 — 텍스트블록 9 · 표 1 · 그림 2
4. docstruct.tables.docling     TableItem → GFM markdown — 1개 (병합셀 grid 복원)
5. docstruct.media.page_render  페이지 PNG 렌더 — pypdfium2 · 2.0x
6. docstruct.tables.assess      LLM 표 판정 — table_2:table/insufficient  (2.1s)
7. docstruct.tables.fill        LLM 표 재추출 — table_2 교체  (3.4s)
```

`layout.md` 에는 레이아웃 모델이 각 영역에 붙인 라벨과 파이프라인 처리 결과가
나란히 나옵니다.

| 관찰 | 원인 |
|------|------|
| 실제로는 표인데 라벨이 `그림`/`본문` | 레이아웃 모델 오인식 |
| 라벨은 `표` 인데 내용이 깨짐 | 표 구조 복원 또는 변환 문제 |
| 처리가 `버려짐` | 영역은 잡았으나 텍스트 추출 실패 |

---

## 노트북

```
notebooks/preview.ipynb         로컬
notebooks/preview_colab.ipynb   Google Colab
```

파일을 고르고 실행하면 요약 · 처리 경로 · 표 판정 전후 비교 · 본문 · 이미지를
순서대로 보여줍니다.

**API 키는 1번 셀 아래 "API 키 (선택)" 셀에서** 넣습니다. 노트북 셀에 키를
직접 적으면 저장 시 파일에 남으므로, 입력받아 쓰세요.

```python
import docstruct, getpass
docstruct.set_api_key(getpass.getpass("OpenAI 키: "))
```

Colab 노트북은 `colab.configure_openai()` 가 Secrets 의 `OPENAI_API_KEY` 를
자동으로 읽습니다. Colab 노트북에는 GPU 확인, OpenAI 연동, 비용 추정,
결과 반출이 포함되어 있습니다.

---

## 성능

| 설정 | 성격 | 코어 수에 묶이나 |
|------|------|-----------------|
| `llm_concurrency` | I/O 대기 | **아니오** — 코어 1개에서도 효과 있음 |
| `threaded_pipeline` | CPU 계산 | 예 |
| `num_threads` | CPU 계산 | 예 |
| `device=cuda` | GPU | 레이아웃 모델·TableFormer·OCR |

**대부분의 시간은 원격 LLM 대기**입니다. `llm_concurrency` 를 올리는 것이
GPU 보다 효과가 큽니다. `pipeline.md` 의 단계별 소요 시간표로 확인하세요.

```
표 재추출 LLM (원격)                    24.1초    57%
표 평가 LLM (원격)                      13.8초    33%
추출 (백엔드+레이아웃+TableFormer+OCR)     3.2초     8%
페이지 렌더 (pypdfium2)                  1.2초     3%
```

GPU 는 '추출' 구간만 줄입니다. `429` 가 잦으면 `llm_concurrency` 를 낮추세요.

---

## 여러 사람이 같은 서버에서 쓸 때

접속 세션이 다르면 프로세스가 분리되어 설정이 서로 영향을 주지 않습니다.
같은 프로세스에서 여러 스레드가 동시에 `run()` 해도 안전합니다.

다만 다음은 프로세스가 달라도 공유됩니다.

| 자원 | 대처 |
|------|------|
| 출력 디렉터리 | `save("out/", unique=True)` |
| Docling 모델 캐시 | 한 번 받아두면 무관 |
| GPU 메모리 | 프로세스별 `device` 분리 |
| LLM 사용량 한도 | `llm_concurrency` 하향 |

---

## LLM 연결이 안 될 때

파싱은 그대로 동작하고 표 평가·재추출만 생략됩니다.

연결 실패 시 대비 엔드포인트로 자동 전환할 수 있습니다 (기본 `gpt-5.6-luna`).

```python
docstruct.set_api_key("sk-...")     # 키가 있어야 동작합니다
```

전환은 **연결 불가일 때만** 일어납니다. 인증 실패나 잘못된 응답은
설정 문제이므로 그대로 알립니다.

원인별 대처는 `INSTALL.md` 를 보세요.

---

## Windows

비 UTF-8 로케일(cp949)에서 PyTorch/Docling 초기화가 실패하는 문제가 있습니다.
`docstruct` 가 자동으로 우회하지만, 영구 해결은 환경변수 하나입니다.

```cmd
setx PYTHONUTF8 1
```

자세한 내용은 `WINDOWS.md` 를 보세요.

---

## 문서

| 파일 | 내용 |
|------|------|
| `API.md` | 공개 API 전체 참조 |
| `CLI.md` | 명령행 사용법 |
| `INSTALL.md` | 설치·설정·문제 해결 |
| `BUGFIXES.md` | 원본 대비 수정한 버그 |
| `RESTRUCTURE.md` | 계층 구조 재편 검토 |
| `GIT.md` | git 명령어 (공개/사내 저장소) |
| `GITHUB.md` | GitHub 배포 |
| `GITLAB.md` | 사내 GitLab 배포 |
| `BUILD.md` | 빌드·배포 절차 |
| `WINDOWS.md` | Windows 관련 |
