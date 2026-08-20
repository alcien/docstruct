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
pip install "docstruct @ git+https://github.com/alcien/docstruct.git@v0.3.44"
```

HWP · HWPX · PDF 처리에 필요한 것이 모두 함께 설치됩니다 (약 5.6 GB —
docling 이 PyTorch 를 끌고 옵니다).

GPU 를 쓰지 않으면 CPU 전용 torch 를 먼저 깔아 2.7 GB 를 줄일 수 있습니다.

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install "docstruct @ git+https://github.com/alcien/docstruct.git@v0.3.44"
```

사내 GitLab 에서 받을 때는 주소만 바꾸면 됩니다.

```bash
pip install -U --force-reinstall --no-cache-dir \
  "docstruct @ git+http://183.96.152.133/mjseo/docstruct.git@v0.3.44"
```

> **노트북에서는 커널을 재시작하세요.** `pip install` 만으로는 이미 로드된
> 모듈이 바뀌지 않습니다.

노트북 UI(파일 선택 위젯)가 필요하면 `[notebook]` 을 붙이세요.

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

로컬에 내려받은 VLM 으로 갈아끼울 수도 있습니다 (HTTP 를 쓰지 않습니다).

```python
docstruct.set_model("Qwen/Qwen3-VL-4B-Instruct", dtype="bfloat16")
docstruct.set_model(None)      # 해제
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

### backend API

`docstruct-backend-overlay` 를 적용하면 HTTP 로도 쓸 수 있습니다.
자세한 내용은 overlay 의 `README.md` 를 보세요.

| 엔드포인트 | 하는 일 |
|---|---|
| `POST /convert` | 문서 → 텍스트·마크다운·HTML·XML |
| `POST /export_json` | 문서 → 구조화 JSON 파일 |
| `POST /export_folder` | 폴더·여러 파일·zip → JSON 묶음 (백그라운드) |
| `POST /export_group` | zip → DocStructBatch 처리 (백그라운드) |
| `GET /jobs/{id}` | 진행 상황 |
| `GET /jobs/{id}/download` | 결과 zip |
| `GET /ui` | 브라우저에서 업로드·진행·다운로드 |

수십 분 걸리는 묶음 작업은 작업 ID 를 먼저 돌려주고, `/jobs/{id}` 로
진행을 조회한 뒤 완료되면 내려받습니다. `/ui` 가 그 과정을 자동으로
처리합니다.

---

## 문서 하나

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
print("\n".join(ds.summary()))   # 콘솔 요약 (소요 시간 포함)

docstruct.enable_logging()       # 진행 상황을 보고 싶으면
```

### 여러 문서

`DocStruct` 는 문서 하나를 깊게, `DocStructBatch` 는 여럿을 넓게 다룹니다.
**처리 경로 추적은 양쪽 모두에 있습니다** — 자세한 구분은
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

종료 코드: 0 성공, 1 실패, 2 인자 오류. 전체 옵션은 `docstruct --help`.

---

## 목차

`목차`·`차례`·`순서`·`CONTENTS` 머리글이 있는 쪽과 앞뒤 2쪽에서
`제목 … 쪽번호` 를 찾습니다. 자간을 벌려 쓴 `차  례`·`순  서` 도 잡습니다.

**머리글이 없는 목차**도 알아봅니다. 제목만 있고 바로 항목이 이어지는
문서가 있어, `제목 … 쪽번호` 가 5줄 이상이고 쪽의 40% 이상이면 목차로
봅니다.
LLM 없이 규칙으로 합니다.

```json
"toc": [{"title": "1. 취득세 과세대상", "page": 25, "source_page": 7}],
"toc_offset": 2
```

`page` 는 **문서에 인쇄된 쪽번호**입니다. PDF 쪽 번호와 다를 수 있어
`toc_offset` 에 차이를 함께 냅니다(표지·간지 때문).

차이는 **본문 바닥글의 쪽번호**로 잽니다. 목차만으로는 잴 수 없는 경우가
있어서입니다 — 목차가 앞쪽인데 항목이 뒤를 가리키면 그렇습니다.

    목차 25쪽 + 오프셋 2 → PDF 27쪽 (실제로 그 장 표지)

브라우저 인쇄 표시(`31/380`)는 건너뜁니다. 근거가 20쪽 미만이거나 값이
흩어지면 `null` 로 둡니다 — 쪽번호가 본문에 남지 않는 문서가 있습니다.

스캔본은 OCR 이 제목과 쪽번호를 다른 줄로 읽는데, 그 모양도 잡습니다.

    '5.취득세에 부가되는세금'
    '57'

**본문 금액과 구분합니다.** 스캔본은 제목과 숫자가 나뉘어 `가. 취득세액 /
537` 도 목차처럼 보이는데, 쪽번호는 문서를 따라 거의 단조 증가한다는 성질로
거릅니다.

    57 · 57 · 58 · 60 · 62 · 62 · 64 …   ← 목차
    537 · 842                            ← 금액·건수 (크게 뜀)

실측(주택과세금 377쪽): 항목 90개 · 쪽번호 25~369 · 단조 증가.

번호 매김은 여러 형태를 봅니다 — `제1부` `제1장` `01` `003` `1.` `가.`
`Q1.` `I.` `Ⅱ.` `①` `◆` `▶`. 점선 없이 공백만으로 벌린 목차도 잡습니다.

**앞쪽 30쪽만** 봅니다. 목차는 앞에 있고, 뒤까지 뒤지면 본문의 `차례`
언급이 걸립니다. 뒤쪽 목차가 있는 문서는 `DOCSTRUCT_TOC_HEAD_PAGES=0` 으로
전체를 봅니다.

끄려면 `detect_toc=false` 입니다.

---

## 표 유형

평가 LLM 이 표마다 유형을 판단합니다. 이미 표를 보고 있으므로 호출이 늘지
않습니다.

```json
{"table_kind": "budget", "quality": "sufficient"}
```

| 유형 | 무엇 |
|---|---|
| `budget` | 예산·결산 (금액, 회계구분, 집행률) |
| `indicator` | 성과지표 (목표·실적·달성률) |
| `program` | 사업·프로그램 목록 |
| `org` | **조직도·체계도를 표로 그린 것** |
| `review` | 지적사항·개선계획 등 서술형 |
| `cover` | 표지·간지·목차 |
| `other` | 그 밖 |

`org` 는 markdown 으로 표현할 수 없습니다 — 계층과 연결선이 사라집니다.
빈 칸이 많아도 파싱 결함이 아니며, 평가도 그 점을 감안합니다.

---

## 무엇이 만들었는가

표와 그림마다 **출처**가 남습니다. 모델이 손댄 것을 파서가 뽑은 것과 구분할
수 있어야 어디까지 믿을지 정할 수 있습니다.

| `source` | 뜻 |
|---|---|
| `parser` | 파서가 뽑은 그대로 |
| `llm` | LLM 이 다시 만듦 (표 재추출) |
| `vlm` | VLM 이 지면을 보고 다시 씀 (표 재작성·그래프 읽기) |

콘솔 요약과 HTML 미리보기에도 나옵니다.

```
표 재추출  : 2개  (LLM 1, VLM 1)  ※ 값이 빠진 표 1개
그래프     : 2개  (VLM 으로 읽음 1)
```

표 목록에는 배지로 표시되고, 값이 빠진 표는 그 수가 붙습니다(`LLM -3`).

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

### 실행 기록 빼기 (`slim`)

`document.json` 에는 어느 모듈이 어떤 단계를 처리했는지 기록(`trace`)이
함께 들어갑니다. 진단에는 쓸모 있지만 본문을 찾기 어려워질 만큼 큽니다 —
72쪽 문서에서 파일의 대부분을 차지했습니다.

```python
ds.to_json("결과.json", slim=True)      # 단건
batch.to_json("결과/", slim=True)       # 배치
```

```bash
docstruct 문서.hwp -o out --slim
```

`trace`·`layout`·`pipeline`·`timings` 를 빼고 본문·표·그림만 남깁니다.

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

## 스캔 PDF (OCR)

텍스트 레이어가 없는 스캔본은 OCR 로 읽습니다. 두 가지를 확인하세요.

### 전면 OCR

```python
ds = docstruct.DocStruct("스캔본.pdf", force_full_page_ocr=True).run()
```

```bash
docstruct 스캔본.pdf -o out --set force_full_page_ocr=true
```

기본값(`False`)은 **텍스트 레이어가 없는 영역만** OCR 합니다. 그래서
브라우저로 인쇄한 PDF 처럼 머리말·꼬리말만 텍스트로 들어 있으면, 그것을
"텍스트가 있다" 고 보고 본문을 읽지 않습니다.

### 한국어 인식 모델

rapidocr 3.x 의 기본 인식 모델(PP-OCRv6 small)에는 **한국어가 없습니다.**
한글 지면이 한자·가나로 나오면 이 문제입니다.

```
气····吾·咎今          ← 원본은 "2025 주택과 세금"
ヤ君居 |0号 |0 后雨立
```

한 쪽만 30초 안에 확인할 수 있습니다.

```bash
python -m converters.pdf.rapidocr_ko 문서.pdf 16
```

```
── 기본 설정: 한글 0.0% · 26.5.11. 5:44 2025 wwo. 2025号 1 2.10. Y* ...
── 한국어 모델: (여기에 한글 비율이 나옵니다)
```

### 자동 분류

**따로 켤 것이 없습니다.** 페이지마다 텍스트 레이어를 확인해 필요한 쪽만
다시 읽습니다. 스캔본과 텍스트 PDF 를 섞어 넣어도 됩니다.

```python
ds = docstruct.DocStruct("문서.pdf").run()
```

| 입력 | 처리 |
|---|---|
| 스캔본 | 텍스트 레이어 없음 → OCR 로 읽음 |
| 텍스트 PDF | 레이어 그대로 사용 (렌더도 하지 않음) |
| 영어·혼용 문서 | 레이어 그대로 사용 |
| 혼합 | 쪽마다 갈라서 처리 |

    실측 판정 정확도: 텍스트 PDF 98% · 스캔 PDF 100%

스캔본은 텍스트 파서로 읽을 길이 없습니다 — 20쪽 전체에서 한글 340자가
나오는데 전부 파일명이 URL·머리말에 반복된 것이고 본문은 0자입니다.

끄려면 `korean_ocr=false`, 판정을 무시하고 모든 쪽을 다시 읽으려면
`DOCSTRUCT_KOREAN_OCR_FORCE=true` 입니다.

페이지 이미지를 한국어 모델로 다시 읽어 **본문과 표 안 텍스트**를 바꿉니다.

표는 행·열·병합을 그대로 두고 텍스트만 갈아끼웁니다. 셀 좌표와 OCR 조각
좌표를 겹쳐 어느 셀에 속하는지 정합니다 — 두 좌표계 모두 원점이 TOPLEFT 라
배율만 나누면 맞습니다(`포인트 = 픽셀 / render_scale`).

    | 品品品 | 昆品 |        | 구분   | 2025년     |
    | 早     | 全气 |   →    | 유튜브 | 13,391,527 |

### 표 진단 (기본 꺼짐)

셋 다 기본으로 꺼져 있습니다. 실측에서 도움이 되지 않았거나 오히려 해로웠기
때문입니다.

| 설정 | 기본 | 하는 일 |
|---|---|---|
| `flag_odd_tables` | **켬** | 같은 서식 표 중 열 수가 다른 것을 표시 |
| `flag_broken_tables` | 끔 | 빈 칸 비율 표시 (정상 표를 82% 잡음) |
| `rebuild_grid` | 끔 | 좌표로 격자 재구성 (13회 시도 13회 폐기) |
| `vlm_fix_tables` | 끔 | **서식이 어긋난 표**를 VLM 으로 재작성 |

### 쪽을 넘는 표

한 표가 여러 쪽에 걸치면 docling 은 쪽마다 별개 표로 봅니다. 첫 쪽에만
헤더가 있으면 뒤쪽 값이 어느 열인지 알 수 없습니다.

**관계만 표시하고 표는 건드리지 않습니다.**

```json
{
  "id": "table_7",
  "continues_from": "table_6",
  "inherited_header": ["회 계", "계 정", "분 야", ...]
}
```

헤더를 표에 끼워 넣으면 원본이 변형되고, 열 수가 쪽마다 달라(실측 13~17)
앞에서부터 억지로 맞추게 됩니다. 관계만 기록하면 구조화 단계가 실제 값을
보고 맞출 수 있습니다.

헤더가 매 쪽 반복되는 표는 표시하지 않습니다 — 이미 쓸 수 있습니다.

실측(행안부 성과계획서 별첨3, 27쪽): 21쪽에 걸친 표에서 **20개를 이어짐
으로 표시**하고, 헤더 반복형 6개와 열 수가 다른 합계 표는 제외했습니다.

끄려면 `mark_table_continuation=false` 입니다.

---

### 서식 불일치 검출

정부 문서는 같은 서식 표를 여러 쪽에 반복합니다. 헤더가 같은 표들을 묶어
열 수를 견주고, 다수와 다른 표를 표시합니다.

```
표 서식 불일치 · table_10 · 7열 — 같은 서식 표 다수는 8열입니다
```

실측(행안부 성과계획서 72-100쪽): 표 17개 중 헤더가 같은 12개를 묶어
**7열짜리 하나만** 검출했습니다. 그 표는 헤더 두 칸이 하나로 뭉쳐 있었고,
오탐은 없었습니다.

    정상  ... | 재정사업 평가명 | 성과평가 결과 | 비고 |
    이상  ... | 재정사업 성과평가 평가명 결과 | 비고 |

같은 서식 표가 셋 이상 있어야 다수를 판단합니다. 한 번만 나오는 표는
비교 대상이 없어 검사하지 못합니다.

헤더의 **내용 있는 셀**로 묶습니다. 앞쪽이 빈 표가 많아(병합 헤더의 좌상단,
`(단위: 백만원)` 같은 안내) 그것까지 세면 전혀 다른 표가 한 그룹이 됩니다.
열 수 차이가 크면 다른 표로 봅니다.

실측 오탐: HWP 162개 표에서 5건 → **0건**.

`vlm_fix_tables=true` 로 켜면 이렇게 표시된 표만 VLM 으로 다시 만듭니다.
스캔본은 표 서식이 제각각이라 대상이 잡히지 않습니다.

**빈 칸은 결함이 아닙니다.** docling 은 값이 없는 칸에 셀 객체를 만들지
않으므로, 덮이지 않은 칸은 원본에서 비어 있던 자리입니다. 표 세 개를
확인하니 텍스트가 빈 셀이 하나도 없었습니다 — 셀이 있으면 반드시 값이
있습니다.

격자 크기 자체가 원본과 다른 경우(스캔본에서 13행 표가 7행으로 인식)는 이
값으로 잡히지 않습니다. 자동 판정 방법은 아직 없습니다.

---

### 셀이 비어 있을 때

처리 경로에 표별 진단이 남습니다.

    table_2 · 셀 34개 교체
    table_1 · 셀 5개 교체, 빈 셀 7, 표 안 미배정 3

| 값 | 뜻 | 대처 |
|---|---|---|
| 표 안 미배정 > 0 | 표 안이지만 어느 칸과도 겹침이 모자람 | 겹침 임계를 낮춥니다 |
| 빈 셀만 많음 | 셀 좌표가 글자를 안 덮음 (표 구조 인식 문제) | 임계로는 해결되지 않습니다 |

```bash
DOCSTRUCT_CELL_MIN_OVERLAP=0.2      # 기본 0.3
```

배정은 **셀 기준**입니다. 셀마다 자기 영역과 겹치는 조각을 모으므로, 한
조각이 두 칸에 걸치면 양쪽이 모두 가져갑니다 — 표 괘선이 얇아 조각이 칸을
넘는 일이 흔하고, 한쪽을 비우는 것보다 낫습니다. 다만 한 칸에 70% 이상
들어간 조각은 그 칸에만 넣습니다.

**OCR 신뢰도 임계(`DOCSTRUCT_RAPIDOCR_MIN_SCORE`)와 다릅니다.** 이 값을
낮춰도 잡음이 늘지 않습니다 — 이미 신뢰도 검사를 통과한 조각 중 어느 셀에
넣을지만 정하기 때문입니다.

처리 경로에 `재판독 생략 — 텍스트 레이어를 그대로 씁니다` 로 남으므로
어느 쪽이 어떻게 처리됐는지 확인할 수 있습니다.

한국어 모델은 처음 실행할 때 자동으로 내려받습니다. 사내망에서
`modelscope.cn` 이 막혀 있으면 미리 받아 두고 지정하세요.

| 환경변수 | 뜻 |
|---|---|
| `DOCSTRUCT_RAPIDOCR_MODEL_DIR` | 미리 받아 둔 모델 폴더 |
| `DOCSTRUCT_RAPIDOCR_VERSION` | `v5`(기본) 또는 `v4` |
| `DOCSTRUCT_RAPIDOCR_MIN_SCORE` | 낮은 신뢰도 조각 제거 (기본 0.7) |
| `DOCSTRUCT_OCR_KEEP_NOISE` | 잡음 조각을 그대로 두기 (기본은 제거) |

실측 (2025 주택과 세금):

| 쪽 성격 | 한글 비율 |
|---|---|
| 도표 위주 | 46.8% |
| 개정 표 | 65.4% |
| 텍스트 위주 | 70.6% |

`v4` 는 `v5` 보다 나쁩니다 — 쉼표·마침표를 잃고 `C168zs운道lYR` 같은
손상이 더 납니다. 특별한 이유가 없으면 기본값(`v5`)을 쓰세요.

### 표 재추출 가드레일

LLM 재추출은 결과를 검사한 뒤에만 반영합니다. LLM 은 못 읽은 것을 지어내고
있던 값을 빠뜨리기도 합니다.

| 검사 | 기준 |
|---|---|
| 표 형태 | 표가 아니면 거부 |
| 내용 분량 | 공백 뺀 내용이 원본의 50% 미만이면 거부 |
| **금액 일치** | **사라지거나 새로 생기면 거부** |
| 숫자 보존 | 20% 넘게 사라지면 거부 |

**빠짐뿐 아니라 바뀜도 봅니다.** 집합으로 비교하면 `103` 이 `1034` 가 되어도
"하나 사라지고 하나 생김" 이라 상쇄돼 보입니다. 개수를 함께 세어 **원본에
없던 값**을 잡습니다 — 재추출은 옮겨 적는 작업이므로 없던 금액이 나오면
지어낸 것입니다.

숫자를 셀 때 **HWP 필드 잔재와 문단 ID 는 뺍니다.** 그것들이 사라지는 것은
정리이지 손실이 아닙니다.

    {"fields": {...}}          필드 잔재
    의원외교활 동 | 49625 ...   문단 ID

글자 수가 아니라 **공백을 뺀 내용**으로 견줍니다 — markdown 표는 열 폭을
맞추느라 빈 칸에 공백을 채워, 정리된 결과가 짧아진 것처럼 보입니다(실측:
7,601자 → 1,545자인데 내용은 동일).

받아들인 표에는 **무엇이 얼마나 다른지** 그대로 냅니다.

```json
"fill_diff": {
  "amounts": 12, "amounts_lost": 0, "amounts_new": 0,
  "numbers": 3, "numbers_lost": 1
}
```

**점수로 뭉치지 않습니다.** 표는 대조할 기준이 없습니다 — 원본 markdown
자체가 깨져 있어 재추출한 것이므로 그것과 견줘 "맞다" 고 할 수 없고, 글자가
바뀌었는지(`국회` → `국외`)는 더더욱 알 수 없습니다. 하나로 뭉친 점수는
"0.5 면 반쯤 맞다" 처럼 읽혀 오해를 부릅니다.

거부하면 원본을 유지하고 처리 경로에 남깁니다.

    재추출 반영 · table_31 · 금액 12개 일치 (신뢰 100%)
    재추출 폐기 · table_95 · 숫자 6개 중 6개 소실 — 원본을 유지합니다

실측(성과계획서 41건 재추출): **41건 전부 수용, 오탐 0**.

---

### 그래프

원그래프·막대그래프는 값이 **그림 안에** 있어 텍스트로 옮겨지지 않습니다.
그런 영역을 `chart` 로 표시합니다.

```json
{
  "region_kind": "chart",
  "region_kind_reason": "그림이 영역의 100% · 글자 0자 — 그래프로 보입니다"
}
```

### 무엇을 그래프로 보는가

한 신호로 가르지 않고 **여러 신호를 모아** 판단합니다. 문서마다 그래프
모양이 달라, 하나에 맞추면 다른 문서에서 무너집니다.

| 신호 | 기준 |
|---|---|
| 그림이 영역을 덮는 비율 | 35% 이상 |
| 지면에서 차지하는 비율 | 3% ~ 60% |
| 가로세로 비 | 0.25 ~ 4.0 |
| 벡터 도형 수 | 참고만 — **필수 아님** |

**벡터를 조건으로 걸지 않습니다.** 뉴스 그래프를 캡처해 붙인 문서가 있어,
래스터 그림도 그래프일 수 있습니다.

    벡터 원그래프  지면 22% · 도형 30개   → 그래프
    사진 그래프    지면 25% · 래스터      → 그래프
    스캔 전면      지면 81%              → 스캔 원본
    머리말 배너    가로세로 6.6           → 띠·구분선
    QR·로고       지면 1%               → 장식

---

기본은 표시만 합니다. 값을 읽으려면 `read_charts` 를 켭니다.

```bash
docstruct 문서.pdf -o out --set read_charts=true
```

VLM 이 읽은 값을 `description` 에 담고, **같은 쪽 본문과 대조해** 신뢰도를
함께 남깁니다. 표는 원본 markdown 과 견줄 수 있지만 그래프는 대조할 원본이
없기 때문입니다.

```json
{
  "region_kind": "chart",
  "description": "| 항목 | 값 |\n| 전략목표 Ⅰ | 20.7% |",
  "chart_verified": 0.75
}
```

`chart_verified` 는 읽어낸 숫자 중 본문에서 확인된 비율입니다. **낮다고
값이 틀린 것은 아니고 확인하지 못했다는 뜻입니다** — 그래프가 본문에 없는
값을 보여 주면(합계·비율 등) 낮게 나옵니다.

**같은 쪽만 보지 않습니다.** 공공문서는 설명과 그림이 쪽을 걸쳐 흩어집니다 —
실측에서 43쪽 그래프의 값이 41~42쪽 표에 있었습니다.

| 범위 | 검증률 |
|---|---|
| 같은 쪽만 | 0% |
| **±2쪽 (기본)** | **89%** |
| 문서 전체 | 100% |

넓힐수록 우연히 맞을 확률도 커집니다. 문서 전체를 보면 관계없는 쪽의 숫자와도
맞아 근거가 약해집니다.

```bash
DOCSTRUCT_CHART_VERIFY_SPAN=2        # 앞뒤 쪽 수 (기본 2)
DOCSTRUCT_CHART_VERIFY_SOURCE=page   # page | document | off
```

본문이 정확하다는 전제가 필요합니다. 스캔본처럼 본문 자체가 OCR 결과라면
`off` 로 대조를 끕니다.

실측(행안부 성과계획서 433쪽): 실제 그래프는 43쪽 2개뿐이고 모두 벡터라
확대해도 선명합니다. 스캔 이미지는 한 장도 없었습니다. 그리고 같은 값이
바로 아래 표에 있어, 이 문서에서는 데이터를 잃지 않습니다.

---

### 표 안 텍스트

본문이 한국어로 읽혀도 **표 안은 docling 이 넣은 값**이 남습니다. 표 구조
(행·열·병합)는 TableFormer 가 만든 것을 그대로 두고 텍스트만 갈아끼웁니다 —
인식 언어가 틀린 것이지 구조가 틀린 것이 아니기 때문입니다.

셀 좌표와 OCR 조각 좌표를 겹쳐 어느 셀에 속하는지 정합니다. 두 좌표계 모두
원점이 TOPLEFT 라 배율만 나누면 맞습니다.

    포인트 = 픽셀 / render_scale

조각 넓이 중 셀과 겹치는 비율이 50% 를 넘으면 그 셀에 넣습니다. 어느 셀에도
안 들어간 조각은 개수로 알립니다 — 표 밖 본문이거나 괘선 오인식입니다.

### 잡음 조각 제거

색상 블록이나 로고를 글자로 잘못 읽은 조각(`YoHIYL`, `OSUMMM`)은
형태소 분석으로 걸러냅니다. 원본에 대응하는 글자가 없으므로 고칠
대상이 아니라 지울 대상입니다.

`kiwipiepy` 가 없으면 이 단계를 건너뜁니다 — 설치 여부가 동작을
깨뜨리지 않습니다. 원문을 그대로 보려면 `DOCSTRUCT_OCR_KEEP_NOISE=true`.

### 글자 깨짐 진단

HWP 에서 내보낸 PDF 는 글머리표(□ ○ ※)의 폰트 매핑이 깨져 한글 음절로
나오는 일이 있습니다(`숿`, `슻` 등). 정상 한글이라 자동 교정이 위험하므로
진단만 제공합니다.

```bash
python -m converters.pdf.glyph_probe 문서.pdf 5
```

---

## HWPX 처리

HWPX(OOXML)는 zip + XML 이라 표준 파서로 읽습니다. `python-hwpx` 의
markdown 내보내기는 쓰지 않습니다 — 같은 문서로 재어 보면 손실이 큽니다.

| | 표 | 셀 보존 | 취소선 |
|---|---|---|---|
| XML 직접 파싱 (기본) | **212** | **100%** | 0 |
| python-hwpx markdown | 94 | 93.8% | 4,456회 |

변환 파일 자체에는 표 212개가 온전히 들어 있습니다. 손실은 파일이 아니라
**내보내기 단계**에서 생깁니다. 취소선은 밑줄 스타일 값이 라이브러리 표에
없어 생기며, pyhwp 의 `UnderlineStyle 15` 와 같은 뿌리입니다.

XML 파싱이 실패하면 `python-hwpx` 로 물러납니다. 어느 경로로 읽었는지는
`document.md` 의 처리 경로에 남습니다 (`hwpx-tree` 또는 `python-hwpx`).

---

## 표 정확도

### HWP 표 재추출 (`hwp_fill_html`)

HWP 는 페이지 이미지가 없어 표 재추출의 근거가 부족합니다. 기본 경로
(hwp5-tree)로 성공한 문서는 재추출 자체를 하지 못합니다.

```python
ds = docstruct.DocStruct("문서.hwp", hwp_fill_html=True).run()
```

켜면 재추출 근거를 만들지만 느려집니다. 같은 문서 실측: 근거 0개 → 114개,
2.4초 → 126초. 기본값은 `False` 입니다.

### 병합 정보

markdown 은 병합을 표현하지 못합니다. `colspan=3` 인 셀도 한 칸에만 값이
들어갑니다. 그래서 셀 격자를 JSON 에 함께 냅니다.

```json
"cells": [
  {"row": 0, "col": 0, "rowspan": 2, "colspan": 1, "text": "구분"},
  {"row": 0, "col": 1, "rowspan": 1, "colspan": 2, "text": "예산"}
]
```

구조화 단계가 이 정보로 **병합 셀 값을 하위 행에 전파**할 수 있습니다 —
표 조각을 RAG 청크로 잘라도 레이블이 붙어 있게 하는 표준 대응입니다.

HWPX 는 XML 에 병합이 명시돼 있어(`cellSpan`, `cellAddr`) 추측하지 않습니다.
실측 문서에서 병합 셀 968개를 그대로 읽었습니다. PDF 는 TableFormer 가 준
값을 씁니다. **두 경로가 같은 형태**라 쓰는 쪽이 분기할 필요가 없습니다.

---

### 병합 셀 표기

markdown 은 병합 셀(rowspan)을 표현하지 못합니다. 값을 맨 윗행에만 두고
아래를 비우면 **그 값이 윗행만의 것으로 읽힙니다.**

```
| 페이스북   | 콘텐츠 상호작용 | 15.7만 |
| 인스타그램 | 〃              | 〃     |   ← 두 행이 공유하는 값
```

`〃` 로 이어짐을 표시합니다. 예전 산출물과 대조할 때는
`DOCSTRUCT_TABLE_MERGE_MARK=off` 로 끌 수 있습니다.

---

## 노트북

```
notebooks/preview.ipynb         문서 하나 확인 (로컬)
notebooks/preview_colab.ipynb   문서 하나 확인 (Google Colab)
notebooks/batch_review.ipynb    폴더 일괄 처리 → 개별 분석
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

원인별 대처는 `docstruct --check` 가 안내합니다.

---

## Windows

PowerShell 기준입니다. Python 3.10~3.12 를 권장합니다.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install "docstruct @ git+http://183.96.152.133/mjseo/docstruct.git@v0.3.44"
```

### 한글이 깨져 보일 때

산출물(`document.md` 등)은 항상 UTF-8 로 기록하므로 **파일은 멀쩡합니다.**
콘솔이 cp949 라 표시만 깨집니다.

```powershell
chcp 65001
```

파일을 파이썬으로 읽을 때는 인코딩을 명시하세요 — 생략하면 cp949 로 읽어
또 깨집니다.

```powershell
python -c "print(open(r'out\문서\document.md', encoding='utf-8').read()[:200])"
```

### `UnicodeDecodeError: 'cp949' codec ...`

docling 이 UTF-8 템플릿을 인코딩 없이 열어 생깁니다. 인터프리터를 UTF-8
모드로 돌리면 사라집니다.

```powershell
$env:PYTHONUTF8 = "1"
```

영구적으로는 **설정 → 시간 및 언어 → 언어 → 관리자 언어 설정 →
시스템 로캘 변경 → "Beta: 세계 언어 지원을 위해 Unicode UTF-8 사용"** 을
체크하고 재부팅합니다.

## 라이선스

의존성 대부분은 MIT · Apache-2.0 · BSD 입니다. **`pyhwp` 하나가
AGPL-3.0-or-later** 이며, HWP 처리의 기본 경로에서 같은 프로세스로
import 합니다.

AGPL 13조는 네트워크로 서비스만 제공해도 소스 제공 의무를 규정합니다.
backend API 로 서비스한다면 이 조항에 해당할 수 있으므로 **법무 검토가
필요합니다.**

대체 경로(HWPX XML 직접 파싱)는 `converters/hwpx/hwpxtree.py` 에 있으며,
같은 문서에서 pyhwp 와 동등한 품질(셀 100%, 표 212/212)을 9배 빠르게
냈습니다. 다만 HWP → HWPX 변환 수단 확보가 선행 과제입니다.

이 문단은 사실 확인이며 법률 자문이 아닙니다.

---

## 실험 기법

검증이 끝나지 않은 보완 기법은 `docstruct/experiments/` 에 격리돼 있습니다.
기본은 모두 꺼져 있습니다.

```bash
docstruct --exp list                              # 무엇이 있는지
docstruct 문서.pdf -o out --exp split_merge       # 하나 켜기
docstruct 문서.pdf -o out --exp split_merge,otsl_diff
```

서버에서는 환경변수를 씁니다.

```
DOCSTRUCT_EXP_SPLIT_MERGE=true
DOCSTRUCT_EXP_OTSL_DIFF=true
```

한 파일당 하나씩 두어 **폐기할 때 파일과 등록만 빼면** 됩니다. 각 기법이
무엇을 보완하고 어느 연구에서 발상을 빌렸는지 함께 적혀 있습니다.

---

## 변경 이력

판별 근거와 실측치는 `BUGFIXES.md` 에 남습니다 — 무엇을 왜 고쳤는지,
어떤 방법을 검토했다가 접었는지가 함께 적혀 있습니다. 배포물에는 포함하지
않고 따로 전달합니다(2,800줄이 넘어 패키지를 무겁게 합니다).
