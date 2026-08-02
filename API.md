# API 참조

`import docstruct` 로 쓸 수 있는 것 전체입니다.

---

## 모듈 함수

| 함수 | 하는 일 | 반환 |
|------|---------|------|
| `configure(**옵션)` | 설정을 프로세스 전역에 적용 | 적용된 설정 (키는 가려짐) |
| `set_api_key(키, target=...)` | API 키 설정 | 없음 |
| `defaults()` | 내장 기본값 | `{설정키: 값}` |
| `option_keys()` | 설정 가능한 키 전체 | 키 이름 튜플 |
| `mask(값)` | 비밀값 가리기 | `sk-abc…7890` |
| `enable_logging(level="INFO")` | 진행 로그 표시 | 없음 |
| `structure(경로, **옵션)` | 문서 하나를 구조화 | dict |
| `structure_to_json(경로, 저장경로, **옵션)` | 구조화 후 파일 저장 | 저장된 Path |
| `build_document(경로, ...)` | 하위 수준 진입점 | `PageDocument` |

```python
import docstruct

docstruct.configure(llm_url="http://내부:11060/v1", llm_concurrency=8)
docstruct.set_api_key("sk-...")           # target: fallback(기본) | llm | picture
```

---

## 로컬 VLM 으로 갈아끼우기

HTTP 엔드포인트(사내 서버·OpenAI) 대신 **이 장비에 내려받은 VLM** 을
직접 돌릴 수 있습니다.

```python
import docstruct

docstruct.set_model("Qwen/Qwen3-VL-4B-Instruct")      # HuggingFace 이름
docstruct.set_model("/models/qwen3-vl")                # 내려받은 로컬 경로
docstruct.set_model(None)                              # 해제 — 다시 HTTP

docstruct.DocStruct("문서.pdf").run()
```

| 인자 | 기본 | 설명 |
|------|------|------|
| `model_id` | — | HuggingFace 이름 또는 로컬 경로. `None` 이면 해제 |
| `device` | 전역 `device` 설정 | `auto` · `cpu` · `cuda` · `cuda:0` … |
| `dtype` | `auto` | `float16` · `bfloat16` · `float32` |
| `max_tokens` | 2048 | 생성 상한 |

```python
docstruct.set_model("Qwen/Qwen3-VL-4B-Instruct",
                    device="cuda:0", dtype="bfloat16", max_tokens=4096)
```

`configure()` 로도 됩니다.

```python
docstruct.configure(vlm_model="/models/qwen3-vl", vlm_dtype="bfloat16")
```

```bash
docstruct 문서.pdf --set vlm_model=/models/qwen3-vl --set vlm_dtype=bfloat16
export DOCSTRUCT_VLM_MODEL=/models/qwen3-vl
```

### 호출 경로

```
tables/assess · tables/fill · outline
        │
        ▼
infrastructure/llm/client.invoke_llm()      ← 여기서 갈림
        │
        ├─ 로컬 VLM 이 설정됨  → infrastructure/llm/local_vlm.invoke()
        │                        (transformers 로 직접 실행)
        │
        └─ 아니면 HTTP
             ├─ DOCSTRUCT_LLM_ADAPTER 지정 → 그 모듈의 create_llm_adapter()
             └─ 미지정(기본)              → requests 로 직접 POST
                   └─ 연결 불가 시 → 대비 엔드포인트
```

`client.py` 가 분배기이고, `local_vlm.py` 는 그중 한 갈래입니다.

### 외부 HTTP 어댑터

기본은 `requests` 로 직접 호출합니다. 사내 게이트웨이 라이브러리를 거쳐야
한다면 모듈 이름을 지정하세요.

```python
docstruct.configure(llm_adapter="사내_게이트웨이_모듈")
```

```bash
export DOCSTRUCT_LLM_ADAPTER=사내_게이트웨이_모듈
```

해당 모듈에 `create_llm_adapter(kind, model_name=..., server_url=...)` 가
있어야 하며, 반환 객체는 `invoke(prompt, image_urls=..., span_name=...)` 로
`.content` 를 가진 응답을 돌려주면 됩니다.

불러올 수 없으면 경고 후 `requests` 로 넘어갑니다 — 처리가 멈추지 않습니다.

### 동작 방식

지정하면 표 판정·재추출이 **HTTP 를 쓰지 않고** 이 모델을 직접 돌립니다.
엔드포인트가 아예 설정되지 않아도 됩니다.

```
INFO  로컬 VLM 사용: Qwen/Qwen3-VL-4B-Instruct (device=cuda:0, dtype=bfloat16)
      — HTTP 엔드포인트를 쓰지 않습니다
```

`docstruct --check` 의 `로컬 VLM` 행에서 확인합니다.

### 알아둘 점

- 모델은 **처음 쓸 때 한 번 로드**하고 이후 재사용합니다. 첫 호출이 느립니다.
- 생성은 **직렬화**됩니다 — `llm_concurrency` 를 올려도 로컬 모델에서는
  병렬로 돌지 않습니다 (VLM 은 동시 호출에 안전하지 않고, GPU 메모리
  측면에서도 그 편이 낫습니다).
- **이미지 입력을 지원하는 모델**이어야 합니다. 표 판정·재추출은 페이지
  이미지를 근거로 쓰기 때문입니다. 텍스트 전용 모델은 표가 깨진 이유를
  볼 수 없습니다.
- `transformers` 와 `torch` 가 필요합니다 (docling 을 설치했다면 이미 있습니다).
- 모델 크기만큼 메모리를 씁니다. 4B 모델은 `bfloat16` 기준 약 8GB 입니다.

## 진행 상황 보기

라이브러리는 기본적으로 로깅을 설정하지 않습니다 (쓰는 쪽 설정을
덮어쓰지 않기 위함). 진행 로그를 보려면 한 줄 부르면 됩니다.

```python
import docstruct

docstruct.enable_logging()        # INFO — 단계별 진행·소요 시간
docstruct.enable_logging("DEBUG") # 더 자세히
```

```
INFO    추출 시작: 보고서.pdf (pdf)
INFO    추출 완료: 16페이지, 표 12개
INFO    표 품질 평가 중...
INFO    ── 단계별 소요 시간 (총 40.8초) ──
INFO       추출 (백엔드+레이아웃+TableFormer+OCR)   35.6초    87%
INFO       표 평가 LLM (원격)                     5.0초    12%
```

**로그를 켜지 않아도 소요 시간은 볼 수 있습니다.**

```python
ds.summary()          # 마지막 줄에 "소요 시간 : 40.8초 (가장 큰 단계 ...)"
ds.document.timings   # {'추출': 35.6, '표 평가 LLM (원격)': 5.0, ...}
```

`pipeline.md` 에는 단계별 표와 GPU 영향 여부까지 나옵니다.

## `DocStruct` — 문서 하나

```python
ds = docstruct.DocStruct("보고서.pdf", assess_tables=True)
ds.set(device="cuda").run()
```

### 설정

| 메서드 | 반환 | 설명 |
|--------|------|------|
| `set(키, 값)` / `set(**옵션)` | `self` | 설정 지정 (연쇄 호출 가능) |
| `get(키, default=None)` | 값 | 설정 읽기 (미지정 시 기본값) |
| `options()` | dict | 명시적으로 지정한 설정만 |
| `reset()` | `self` | 설정·결과 지우기 |

### 실행

| 메서드 | 반환 |
|--------|------|
| `run()` | `self` |

### 결과 — 데이터

| 멤버 | 반환 | 설명 |
|------|------|------|
| `document` | `PageDocument` | 구조화 결과 객체 |
| `pages` | `list[PageContent]` | 페이지 목록 |
| `tables` | `list[TableInfo]` | 문서 전체의 표 |
| `to_dict()` | dict | 파이썬 자료구조로 |
| `to_json_str(indent=2)` | **str** | **JSON 문자열로** (파일 저장 없음) |
| `summary()` | `list[str]` | 콘솔용 요약 |

### 결과 — 파일

| 메서드 | 반환 | 설명 |
|--------|------|------|
| `to_json(경로=None)` | **Path** | JSON 파일 저장. 반환은 **경로** |
| `save(디렉터리, unique=False)` | `{이름: Path}` | json + md 4종 |

> `to_json()` 은 **파일을 쓰고 경로를 돌려줍니다.**
> 내용이 필요하면 `to_dict()`(dict) 또는 `to_json_str()`(str) 을 쓰세요.

```python
data = ds.to_dict()                    # dict
text = ds.to_json_str()                # str  — HTTP 응답·로그용
text = ds.to_json_str(indent=None)     # str  — 한 줄 압축
path = ds.to_json("결과.json")          # Path — 파일
files = ds.save("out/")                # {'document': Path, 'markdown': Path, ...}
```

---

## 어느 것을 쓰나

**"분석용 / 처리용" 이 아닙니다.** 처리 경로 추적은 양쪽 모두에 있습니다.

| | `DocStruct` | `DocStructBatch` |
|--|-------------|------------------|
| 쓰는 때 | 문서 **하나를 깊게** | 문서 **여럿을 넓게** |
| 접근 | `ds.tables` — 바로 | `b.documents[i]` — 한 단계 더 |
| 실패 | 예외를 던짐 | `failures` 에 모으고 계속 진행 |
| `to_dict()` | 문서 구조 | `{total, succeeded, failed, documents, failures}` |
| 추적 | 있음 | **있음** (문서마다) |

### 추적은 양쪽 다 됩니다

배치 결과도 `trace` · `layout` · `timings` 를 그대로 갖습니다.

```python
b = DocStructBatch("문서모음/").run()

for doc in b.documents:
    page = doc.pages[0]
    print(doc.filename, page.trace.summary())   # 'docling · OCR 92% · 표3 · 평가'
    print(page.trace.log())                      # 순차 실행 로그
    print(doc.timings)                           # 단계별 소요 시간
```

`b.save("out/")` 하면 문서마다 `pipeline.md` · `layout.md` 까지 나옵니다.

### 실패를 예외로 받을지 데이터로 받을지

이것이 실질적인 갈림길입니다.

```python
DocStruct("깨진.hwp").run()          # RuntimeError — 여기서 멈춤
b = DocStructBatch("깨진.hwp").run()  # 예외 없음
b.failures                            # [(Path, RuntimeError)]
b.to_dict()                           # {'total': 1, 'succeeded': 0, 'failed': 1, ...}
```

파일이 하나여도 서버·배치 잡에서는 `DocStructBatch` 가 편합니다 —
파일이 하나든 백 개든 같은 코드로 다룹니다.

### 입력 방향

| | 받나 |
|--|------|
| `DocStruct("파일.pdf")` | O |
| `DocStruct("폴더/")` | **X** — `IsADirectoryError` 로 `DocStructBatch` 안내 |
| `DocStructBatch("폴더/")` | O |
| `DocStructBatch("파일.pdf")` | O — 1건짜리 목록 |

문서 하나를 다룰 때 `ds.tables` 는 명확하지만, 폴더에서는 "어느 문서의
표인지" 가 모호해집니다. 반대로 목록의 원소가 하나인 것은 모호하지 않으므로
`DocStructBatch` 는 파일 하나를 받습니다.

CLI 는 폴더를 그대로 받습니다 — 반환값이 없고 파일로만 출력하므로
그 모호함이 없습니다.

### 오가는 방법

배치 결과 중 하나를 파고들려면 `DocStruct.from_document()` 로 되돌립니다.

```python
b = DocStructBatch("문서모음/", progress=True).run()
print(b.summary())                       # 성공 47 / 실패 3

for path, exc in b.failures:
    print(path.name, exc)

ds = DocStruct.from_document(b.documents[12])   # 12번째를 문서처럼
print(ds.pages[0].trace.log())
docstruct.preview.show_page(ds.pages[0])
ds.save("out/문제문서")
```

> 이 흐름을 그대로 담은 노트북이 `notebooks/batch_review.ipynb` 에 있습니다.

### 성능

`DocStructBatch` 는 설정을 **한 번만** 적용하고 전체를 그 안에서 처리합니다.
문서마다 적용·해제를 반복하면 그때마다 Docling 컨버터가 버려져
**모델을 다시 로드**하기 때문입니다 (0.1.29 이전에는 그랬습니다).

문서는 순차 처리합니다. 병렬화는 LLM 호출 쪽(`llm_concurrency`)에서
일어나며, 이쪽이 대부분의 시간을 차지합니다.

```python
batch.set(llm_concurrency=8)     # 표 평가·재추출 동시 호출
```

### 전형적인 흐름

```python
# ① 여럿 돌리기
b = DocStructBatch("문서모음/", progress=True).run()

# ② 실패·이상 확인
print(b.summary())
for path, exc in b.failures:
    print(path.name, exc)

# ③ 이상한 문서 하나를 파고들기
ds = DocStruct.from_document(b.documents[3])
print(ds.pages[0].trace.log())        # 어느 단계가 문제였는지
docstruct.preview.show_layout(ds.document)   # 레이아웃 오인식인지
```

### 구현

설정 관리(`set` `get` `options`)는 `_SettingsMixin` 에 공통으로 두고,
결과의 모양이 다른 부분만 각자 구현합니다.

## `DocStructBatch` — 여러 문서

```python
batch = docstruct.DocStructBatch("문서모음/", pattern="*.pdf", progress=True)
batch.run()
```

입력은 네 가지를 받습니다.

```python
DocStructBatch("문서모음/")                    # 디렉터리
DocStructBatch("문서모음/", pattern="*.pdf")   # 디렉터리 + 패턴
DocStructBatch("docs/보고서*.hwp")             # glob
DocStructBatch(["a.pdf", "b.hwp"])            # 경로 목록
```

### `DocStruct` 와 동일한 것

`set` `get` `options` `reset` `run` `to_dict` `to_json_str` `to_json` `save` `summary`

### 다른 것

| 멤버 | `DocStruct` | `DocStructBatch` |
|------|-------------|------------------|
| 결과 객체 | `document` | `documents` (목록) |
| 페이지 | `pages` | — (`documents[i].pages`) |
| 표 | `tables` | — (`documents[i]` 순회) |
| 대상 파일 | — | `paths` |
| 실패 목록 | — | `failures` — `[(경로, 예외)]` |
| `run()` 인자 | 없음 | `stop_on_error=False` |
| `to_json()` 인자 | `path` | `out`, `combined=False` |
| `save()` 반환 | `{이름: Path}` | `{문서명: [Path]}` |

```python
batch.to_json("결과/")                    # 문서별 JSON
batch.to_json("전체.json", combined=True) # 하나로 합쳐서
batch.to_json_str()                       # 전체를 문자열로
batch.save("out/")                        # 문서별 폴더에 json + md 4종

batch.failures                            # 실패한 문서와 원인
```

`to_dict()` 구조:

```python
{
  "total": 12, "succeeded": 11, "failed": 1,
  "documents": [ ... ],                    # PageDocument.to_dict() 목록
  "failures": [{"file": "...", "error": "RuntimeError: ..."}],
}
```

---

## 설정 키 (33개)

`option_keys()` 로 확인할 수 있습니다. `set()` · `configure()` 모두 같은 키를 씁니다.

| 갈래 | 키 |
|------|-----|
| LLM | `llm_url` `llm_model` `llm_key` `llm_timeout` `llm_concurrency` `llm_adapter` |
| 로컬 VLM | `vlm_model` `vlm_device` `vlm_dtype` `vlm_max_tokens` |
| LLM 대비책 | `fallback_url` `fallback_model` `fallback_key` `fallback_timeout` `fallback_enabled` `openai_key` |
| 그림 설명 | `picture_url` `picture_model` `picture_key` `picture_enabled` `picture_area_threshold` |
| PDF 파싱 | `pdf_backend` `ocr_backend` `ocr_lang` `force_full_page_ocr` `generate_parsed_pages` `code_formula_enrichment` |
| 성능 | `device` (`auto`·`cpu`·`cuda`·`cuda:0`·`mps`) `num_threads` `rapidocr_runtime` `threaded_pipeline` |
| 분할 | `split_chars` |
| 실행 | `assess_tables` `fill_tables` `fill_all` `render_pages` `render_scale` `out_dir` `progress` |

오타는 즉시 잡힙니다.

```python
ds.set(gpu=True)
# DocStructError: 알 수 없는 설정 키: 'gpu'
#                 사용 가능: assess_tables, code_formula_enrichment, ...
```

---

## 긴 문서 나누기

HWP 처럼 **페이지 경계가 없는 문서**는 본문 전체가 한 덩어리로 나옵니다
(40만 자짜리 페이지 하나). 후속 처리를 위해 나누려면:

```python
ds = docstruct.DocStruct("보고서.hwp", split_chars=50_000).run()
len(ds.pages)        # 8
```

```bash
docstruct 보고서.hwp --set split_chars=50000
```

**구조 경계를 지키면서** 목표 크기까지 모읍니다. 문단 중간이 끊기지
않습니다.

| 경계 | 우선순위 |
|------|---------|
| `제N장` · 로마숫자 `Ⅰ.` · markdown `#` | 큰 단위 |
| `□` · `◇` · `○` | 중간 |
| `1.` `2.` 번호 항목 | 작은 단위 |

목표 조각 수에 맞는 단위를 자동으로 고릅니다. 목차에만 몰려 있는 표시는
쓰지 않습니다 (경계가 문서 뒤쪽까지 퍼져 있는지 확인).

```
목표  30,000자 →  13조각
목표  50,000자 →   8조각
목표 100,000자 →   4조각
```

조각이 목표보다 커질 수 있습니다 — 경계 없이 이어지는 긴 표를 중간에서
자르지 않기 때문입니다. 그 편이 문맥 유지에 낫습니다.

`page_no_kind` 가 `"chunk"` 로 표시되고, `trace` 에 어느 경계로 나눴는지
남습니다.

```python
ds.pages[0].trace.log()
#  ... docstruct.split  긴 문서 분할 — □ 경계 기준 1/8 조각 — 50,121자
```

## 처리 경로 확인

문서마다 어떤 경로로 처리됐는지 기록됩니다.

```python
page = ds.pages[0]

page.trace.summary()      # 'hwpml-xml · 표1 · 평가'
page.trace.log()          # 순차 실행 로그 전문
page.trace.steps          # [TraceStep, ...]
page.layout               # [LayoutItem, ...]  레이아웃 인식 결과 (PDF)

doc = ds.document
doc.timings               # {'추출': 2.4, '표 평가 LLM (원격)': 0.2, ...}
doc.pipeline              # 이 실행에 적용된 설정
doc.failed_pages          # 파싱 실패로 빠진 페이지
```

```
  1. converters.hwp.hwpml       HWPML(XML) 직접 파싱 — 표 구조 보존
  2. docstruct.tables.markdown  표 블록 placeholder 삽입 — <table N> 1개
  3. docstruct.tables.assess    LLM 표 판정 — table_1:table/wrong  (0.1s)
! 4. docstruct.tables.fill      재추출 불가 — 페이지 이미지도 원본 HTML도 없음
  5. docstruct.tables.tags      표 블록 정규화
```

`!` 경고 · `–` 생략 · `✕` 실패. LLM 단계에는 소요 시간이 붙습니다.

### 노트북 표시 — `docstruct.preview`

| 함수 | 내용 |
|------|------|
| `show_document(doc)` | 요약 → 처리 경로 → 표 판정 → 본문 전체 |
| `show_summary(doc)` | 문서 요약 표 |
| `show_pipeline(doc)` | 페이지별 처리 경로 표 |
| `show_tables(doc)` | 표 판정 + 재추출 전/후 비교 |
| `show_images(doc)` | 추출된 그림 + LLM 설명 |

> 그림은 `out_dir` 을 주지 않아도 저장됩니다 (임시 폴더).
> `save()` 할 때 산출물 폴더의 `images/` 로 함께 옮겨집니다.
> 오래 보관하려면 `save()` 를 부르세요 — 임시 폴더는 나중에 사라집니다.
| `show_page(page)` | 페이지 하나 (처리 경로·이미지·본문) |
| `show_pages(doc)` | 모든 페이지 |
| `show_trace(page)` | 실행 로그만 |
| `show_layout(doc)` | 레이아웃 인식 결과 |

`show_*` 는 IPython 이 필요합니다 (`pip install "docstruct[notebook]"`).
없으면 터지지 않고 안내만 출력합니다.

HTML 문자열만 필요하면 `*_html()` 을 씁니다 — IPython 없이도 동작합니다.

```python
docstruct.preview.summary_html(doc)      # str
docstruct.preview.pipeline_html(doc)     # str
docstruct.preview.trace_log_html(page)   # str
docstruct.preview.layout_html(page)      # str
```

### 파일 출력 — `docstruct.report`

| 함수 | 산출물 |
|------|--------|
| `write_json(doc, 경로)` | `document.json` |
| `write_markdown(doc, 경로)` | `document.md` |
| `write_tables_report(doc, 경로)` | `tables.md` |
| `write_pipeline_report(doc, 경로)` | `pipeline.md` |
| `write_layout_report(doc, 경로)` | `layout.md` |
| `summary_lines(doc)` | 콘솔용 문자열 목록 |

`DocStruct.save()` 가 이 다섯을 한 번에 호출합니다.

## 모델 클래스

`from docstruct import ...` 로 가져올 수 있습니다.

| 클래스 | 내용 |
|--------|------|
| `PageDocument` | `filename` `source_format` `pages` `failed_pages` `pipeline` `timings` |
| `PageContent` | `page_no` `content` `tables` `images` `page_image_path` `trace` `layout` |
| `TableInfo` | `id` `markdown` `content_type` `quality` `original_markdown` `bbox` … |
| `ImageInfo` | `id` `placeholder` `description` `image_path` `mime_type` |
| `PageTrace` | `extractor` `text_source` `steps` — `summary()` `log()` |
| `TraceStep` | `module` `action` `detail` `status` `duration_ms` |

전부 `to_dict()` 를 갖습니다.

---

## 예외

| 예외 | 언제 |
|------|------|
| `DocStructError` | 잘못된 설정 키, 경로 미지정, `run()` 전 결과 접근 |
| `FileNotFoundError` | 파일이 없음 |
| `ValueError` | 지원하지 않는 확장자 |
| `ImportError` | 필요한 파서 미설치 (원인·해결 방법 포함) |
| `RuntimeError` | LLM 미설정, Docling 모델 다운로드 실패 |

---

## CLI

```bash
docstruct 문서.pdf -o out/
docstruct 문서모음/ --glob "*.hwp" -o out/ --progress
docstruct --check
docstruct 문서.pdf --ask-key          # 키를 입력받기
docstruct 문서.pdf --key-file 경로     # 키를 파일에서
```

플래그 전체는 `README.md` 를 보세요.
