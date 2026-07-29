# 계층 구조 정리 — 현재 대비 제안

제안된 구조와 현재 구조를 실제 파일 기준으로 대조한 결과입니다.
**그대로 옮길 수 있는 것**과 **껍데기만 생기는 것**을 나눠 정리했습니다.

---

## 1. 현재 구조의 실제 문제

### 1-1. `converters/` 와 `extractors/` 가 겹칩니다

```
converters/pdf/converter.py   → DoclingDocument 반환
extractors/pdf.py             → DoclingDocument 를 PageContent[] 로
```

원본 프로젝트의 `converters/`(포맷 변환 API)와, 로컬 도구로 재편하며 만든
`extractors/`(구조화)가 나란히 남아 있습니다. **PDF 하나를 처리하는 데
두 폴더를 오가야 합니다.**

| 폴더 | 하는 일 | 소비자 |
|------|---------|--------|
| `converters/` | 파일 → 문자열(md/html/xml/text) | `converters.cli`, FastAPI `/convert` |
| `extractors/` | 파일 → `PageContent[]` | `pipeline` |

`converters/` 는 원본 backend 의 `/convert/*` 엔드포인트가 쓰므로 지울 수
없습니다. 다만 **역할이 다르다는 것이 이름에 드러나지 않습니다.**

### 1-2. 최상위에 단일 파일이 흩어져 있습니다

```
layout.py  models.py  pipeline.py  content.py  progress.py
report.py  preview.py  checks.py  nbui.py  colab.py  api.py  cli.py
```

`models.py`(369줄)와 `layout.py`(196줄)는 데이터 모델인데 최상위에 있고,
`report`/`preview` 는 출력 계층인데 파이프라인과 같은 높이에 있습니다.

---

## 2. 제안 구조에서 **바로 적용 가능한 것**

| 제안 | 현재 | 조치 |
|------|------|------|
| `models/` | `models.py` + `layout.py` | 패키지로 분리 (`document.py`, `page.py`, `table.py`, `layout.py`) |
| `parser/` | `extractors/` | 이름 변경 — 역할이 더 잘 드러남 |
| `table/` | `tables/` | 유지 (이미 분리됨) |
| `pipeline/` | `pipeline.py` + `core/config.py` | 패키지로 (`pipeline.py`, `config.py`) |
| `utils/` | `content.py`, `progress.py`, `media/images.py` | 모아서 정리 |

여기까지는 **파일 이동과 import 경로 수정**이면 끝납니다.

---

## 3. 제안 구조에서 **껍데기가 되는 것**

### `ocr/`

제안:
```
ocr/base.py           OCR 인터페이스
ocr/paddle_ocr.py     PaddleOCR 구현
ocr/tesseract_ocr.py  Tesseract 구현
```

현실: **OCR 은 docling 내부에서 실행됩니다.** 저희 코드가 하는 일은
설정값을 docling 옵션 객체로 옮기는 것뿐입니다.

```python
def _ocr_options():
    opts = RapidOcrOptions()
    opts.lang = _ocr_langs(["korean", "english"])
    return opts
```

`ocr/base.py` 를 만들어도 `process(image) -> text` 를 구현할 대상이
없습니다. docling 이 페이지를 받아 내부에서 OCR·레이아웃·표 인식을
한꺼번에 끝내고 `DoclingDocument` 를 돌려주기 때문입니다.

껍데기를 만들면 이렇게 됩니다.

```python
class DoclingOcr(OcrBase):
    def process(self, page):
        raise NotImplementedError("docling 이 내부에서 처리합니다")
```

이건 구조를 개선하는 게 아니라 **없는 추상화를 흉내내는 것**입니다.

실측으로도 확인됩니다. OCR 을 실제로 수행하는 호출
(`.ocr()`, `readtext()`, `image_to_string()` 등)이 코드베이스에
**한 건도 없습니다.** OCR 이라는 단어가 나오는 곳은 전부
설정 전달·상태 표시·진단입니다.

| 파일 | ocr 언급 | 하는 일 |
|------|---------|---------|
| `converters/pdf/docling_backend.py` | 37 | docling 에 옵션 전달 |
| `core/config.py` | 20 | 설정 읽기 |
| `extractors/pdf.py` | 16 | 처리 경로 기록 |
| `models.py` | 15 | 상태 라벨 |
| 나머지 | — | 화면 표시·진단 |

### `layout/`

같은 이유입니다. 현재 `layout.py` 는 **레이아웃 모델을 호출하지 않고,
docling 이 붙인 라벨을 읽어 기록**합니다 (진단용).

```python
def collect_layout(doc) -> list[LayoutItem]   # 결과를 읽을 뿐
```

`layout/docling_layout.py` + `layout/custom_layout.py` 구조는 두 번째
구현이 있을 때 의미가 있는데, 지금은 `custom_layout` 에 넣을 것이 없습니다.

---

## 4. 실제로 적용할 구조

껍데기를 만들지 않고, 역할이 실재하는 것만 나눕니다.

```
docstruct/
├─ __init__.py            공개 API 재노출
├─ api.py                 DocStruct / DocStructBatch / configure
├─ cli.py                 명령행
│
├─ models/                데이터 모델
│  ├─ document.py         PageDocument
│  ├─ page.py             PageContent · PageTrace · TraceStep
│  ├─ table.py            TableInfo
│  ├─ image.py            ImageInfo
│  └─ layout.py           LayoutItem
│
├─ parser/                파일 → PageContent[]        (구 extractors/)
│  ├─ registry.py         확장자 → 파서 매핑
│  ├─ pdf.py
│  ├─ hwp.py
│  └─ hwpx.py
│
├─ backend/               외부 파싱 엔진 어댑터        (구 converters/)
│  ├─ docling/            PDF — 옵션 구성 · 변환 실행 · 진단 수집
│  ├─ hwp/                pyhwp · HWPML · olefile
│  ├─ hwpx/
│  └─ html/               HTML → markdown (HWP 경로 공용)
│
├─ table/                 표 처리                      (구 tables/)
│  ├─ grid.py             Docling TableItem → GFM      (구 docling.py)
│  ├─ markdown.py         markdown 표 → 블록
│  ├─ tags.py             <table N> 블록 유틸
│  ├─ assess.py           품질 판정 [LLM]
│  └─ fill.py             재추출 [LLM]
│
├─ media/                 이미지
│  ├─ page_render.py
│  ├─ picture.py
│  └─ encode.py           (구 images.py)
│
├─ outline/               목차 추출 [LLM]
│
├─ pipeline/              조립
│  ├─ config.py           설정                          (구 core/config.py)
│  ├─ runner.py           build_document                (구 pipeline.py)
│  └─ trace.py            처리 경로 수집                (구 layout.py 일부)
│
├─ llm/                   LLM 호출                      (구 infrastructure/llm/)
│  ├─ client.py
│  └─ json_parse.py
│
├─ output/                산출물
│  ├─ report.py           md · json
│  └─ preview.py          노트북 표시
│
└─ util/
   ├─ content.py          placeholder 확장
   ├─ progress.py         진행 표시
   ├─ platform.py         Windows 우회             (구 core/winfix.py)
   └─ checks.py           환경 점검
```

### 바뀌는 점

| 항목 | 효과 |
|------|------|
| `converters/` → `backend/` | "외부 엔진 어댑터"임이 이름에 드러남 |
| `extractors/` → `parser/` | 제안 구조와 용어 일치 |
| `models.py` → `models/` | 369줄 한 파일을 역할별로 분리 |
| `core/config.py` → `pipeline/config.py` | 설정이 파이프라인 관심사임을 명시 |
| `infrastructure/llm/` → `llm/` | 계층 하나 축소 |
| `report`·`preview` → `output/` | 출력 계층 묶음 |

---

## 5. 비용과 위험

| 항목 | 내용 |
|------|------|
| 이동 파일 | 63개 중 약 50개 |
| import 수정 | 약 200곳 |
| 영향 | `docstruct-local`, `docstruct-backend-overlay` 모두 재작성 |
| 하위호환 | `from docstruct.models import ...` 등 기존 경로가 깨짐 |
| 검증 | `tools/verify_package.py` 로 import 전수 확인 가능 |

**하위호환을 유지하려면** 옛 경로에 재노출 모듈을 남겨야 합니다.

```python
# docstruct/models.py  (구 경로 유지용)
from docstruct.models.page import PageContent, PageTrace, TraceStep  # noqa
```

다만 이러면 파일 수가 늘어 정리 효과가 반감됩니다.

---

## 6. 권고

**지금 하지 않는 것을 권합니다.** 이유는 셋입니다.

1. **효과의 대부분이 이름 바꾸기입니다.** 실제 결합도는 이미 낮습니다 —
   계층 순환 0, 포맷별 파서 독립, 표 처리 단독 호출 가능.
   측정 결과 체크리스트 7개 중 5개를 이미 만족합니다.

2. **못 고치는 두 항목(`ocr/`, `layout/`)이 이번 재편으로 해결되지
   않습니다.** 그건 docling 의존을 걷어내는 별개의 작업입니다.

3. **직전에 버그를 여러 건 잡았습니다.** 50개 파일을 옮기면 그 검증이
   무효가 됩니다. 이 세션에서 리팩터링 중 `_render_page_images` 삭제,
   패키지 경로 누락 같은 사고가 이미 있었습니다.

### 대신 권하는 순서

1. **`[pdf]` `[hwp]` extras 복원** — 체크리스트 ⑥. 30분이면 됩니다.
2. **`converters/` → `backend/` 이름만 변경** — 가장 혼란스러운 지점 하나만
3. 위 둘을 쓰면서 문제가 없으면 그때 전체 재편

### 재편이 꼭 필요한 경우

상대방이 요구한 것이 **"OCR 엔진을 직접 붙일 수 있게 하라"** 라면
폴더 재편으로는 해결되지 않습니다. 그때는 docling 을 걷어내고
`pypdfium2`(텍스트·렌더) + OCR 엔진 직접 호출 + 레이아웃 모델 직접 로드로
파이프라인을 다시 짜야 합니다. 그건 이 재편보다 훨씬 큰 작업입니다.
