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
| `structure(경로, **옵션)` | 문서 하나를 구조화 | dict |
| `structure_to_json(경로, 저장경로, **옵션)` | 구조화 후 파일 저장 | 저장된 Path |
| `build_document(경로, ...)` | 하위 수준 진입점 | `PageDocument` |

```python
import docstruct

docstruct.configure(llm_url="http://내부:11060/v1", llm_concurrency=8)
docstruct.set_api_key("sk-...")           # target: fallback(기본) | llm | picture
```

---

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
| LLM | `llm_url` `llm_model` `llm_key` `llm_timeout` `llm_concurrency` |
| LLM 대비책 | `fallback_url` `fallback_model` `fallback_key` `fallback_timeout` `fallback_enabled` `openai_key` |
| 그림 설명 | `picture_url` `picture_model` `picture_key` `picture_enabled` `picture_area_threshold` |
| PDF 파싱 | `pdf_backend` `ocr_backend` `ocr_lang` `force_full_page_ocr` `generate_parsed_pages` `code_formula_enrichment` |
| 성능 | `device` `num_threads` `rapidocr_runtime` `threaded_pipeline` |
| 실행 | `assess_tables` `fill_tables` `fill_all` `render_pages` `render_scale` `out_dir` `progress` |

오타는 즉시 잡힙니다.

```python
ds.set(gpu=True)
# DocStructError: 알 수 없는 설정 키: 'gpu'
#                 사용 가능: assess_tables, code_formula_enrichment, ...
```

---

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
