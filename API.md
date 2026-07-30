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
