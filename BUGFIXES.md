# 원본(backend-main) 대비 수정한 버그

원본 `backend-main.zip` 을 검토·재편하면서 고친 것들입니다.
각 항목은 원본 코드에서 실제로 확인한 내용입니다.

---

## A. 파싱 결과가 틀리던 것

### A-1. HWP/HWPX 표가 3행에서 잘림

`rag/tables/markdown.py`

```python
r"(\|[^\n]+\|\n\|[-:\s|]+\|(?:\n\|[^\n]+\|)*)"
#                  ^^^ \s 가 개행까지 삼킨다
```

구분선 문자 클래스에 `\s` 가 들어 있어 개행을 소비합니다. 그 결과 구분선
매칭이 다음 행까지 먹어 **표가 3행에서 끊겼습니다.**

```python
r"(\|[^\n]+\|\n\|[-:| \t]+\|(?:\n\|[^\n]+\|)*)"
```

검증: 4행짜리 표(`구분/예산/인원`)가 온전히 나오는 것을 `/convert/markdown`
응답으로 확인.

### A-2. HWPML 표 셀 텍스트가 본문에 중복

`converters/hwp/hwpml.py:77`

```python
for text_elem in p_elem.findall(".//TEXT"):
#                                ^^^ CELL 내부까지 재귀 수집
```

`.//` 는 하위 전체를 훑으므로 표 안 `CELL` 의 `TEXT` 까지 본문 문단으로
수집됩니다. 표는 별도로 렌더되므로 **같은 내용이 두 번** 나왔습니다.

원본에도 `text_elem.find("TABLE")` 검사는 있었지만, 이건 "TEXT 가 TABLE 을
직접 자식으로 갖는 경우"만 걸러냅니다. 표 **안쪽** 의 TEXT 는 그 조건에
걸리지 않아 그대로 통과했습니다.

TABLE 서브트리에 속한 노드를 미리 모아 제외하도록 고쳤습니다.

```python
inside_table = {
    id(node)
    for tbl in p_elem.findall(".//TABLE")
    for node in tbl.iter()
}
for text_elem in p_elem.findall(".//TEXT"):
    if id(text_elem) in inside_table:
        continue
    ...
```

### A-3. Docling 표의 다단 헤더가 데이터 행으로 밀림

`rag/tables/docling.py`

병합셀을 좌상단 칸에만 채우고 span 을 전파하지 않아, 2단 헤더가 이렇게
나왔습니다.

```
| 구분     | 2023년 |       |   ← GFM 이 이 줄만 헤더로 인식
|----------|--------|-------|
|          | 예산   | 결산  |   ← 헤더 2단이 데이터 행이 됨
| 연구개발 | 1,240  | 1,198 |
```

`column_header` 플래그로 헤더 행 수를 세고, 헤더는 span 전체에 값을
전파한 뒤 열별로 병합하도록 고쳤습니다.

```
| 구분     | 2023년 예산 | 2023년 결산 |
|----------|-------------|-------------|
| 연구개발 | 1,240       | 1,198       |
```

HTML 경로(`converters/html/tables.py`)에는 이미 다단 헤더 병합이 있었는데
Docling 경로에만 없던 비대칭이었습니다. **표가 `wrong` 판정을 받는 주요
원인**이었으므로 LLM 재추출 호출 수도 함께 줄어듭니다.

---

## B. 판정 결과가 무시되던 것

### B-1. 표 품질 게이트 미적용

`rag/tables/fill.py:184`

```python
if ctype == "table" and fill_tables:
    _fill_table_with_llm(page, table, cfg, page_b64)
```

`assess` 가 매긴 `quality` 를 보지 않고 **모든 표를 재추출**했습니다.
`sufficient` 로 판정된 표까지 LLM 을 호출하므로 비용이 그대로 낭비됩니다.

`TableInfo.needs_fill`(`content_type == table` 이고
`quality in {wrong, insufficient}`)로 걸러내고, 전체 재추출은
`fill_all=True` 로 명시할 때만 하도록 분리했습니다.

### B-2. 이미지 그룹으로 흡수된 표가 메타에 남음

`_convert_group_to_image()` 가 본문에서 표 블록을 이미지 placeholder 로
바꾸면서 **어떤 표가 사라졌는지 알려주지 않았습니다.** 호출부는
`remaining_tables` 에 그대로 담아, 본문엔 없는 표가 `tables[]` 에 남는
orphan 이 생겼습니다.

제거된 id 집합을 함께 반환하도록 바꿨습니다. `ImageInfo` 도 호출부에서
`page.images` 에 추가되지 않아 유실되던 것을 함께 고쳤습니다.

---

## C. 실행이 실패하던 것

### C-1. 수식·코드 VLM 이 항상 켜짐

`converters/pdf/docling_backend.py:110-112`

```python
pipeline_options.do_formula_enrichment = True
pipeline_options.do_code_enrichment = True
```

표·본문 추출에는 쓰이지 않는데 별도 VLM 을 내려받고 `torch.compile` 까지
태웁니다. **Windows 비 UTF-8 로케일에서 파싱이 시작조차 못 하던 크래시의
직접 원인**이었습니다.

기본 꺼짐으로 바꾸고 `DOCLING_CODE_FORMULA_ENRICHMENT` 로 설정 가능하게
했습니다. 모델 로딩 시간도 줄어듭니다.

### C-2. `_log` 미정의 (NameError)

`converters/pdf/docling_backend.py` 에 `import logging` 도 `_log` 정의도
없는 상태에서 `_log.warning(...)` 을 호출하는 곳이 있었습니다. 전부 오류
처리 경로라 평소엔 드러나지 않고, **정작 문제가 생겼을 때 원인 메시지 대신
`NameError` 가 났습니다.**

### C-3. `find_spec` 이 예외를 던질 수 있음

`converters/deps.py:29`

```python
DOCLING_AVAILABLE = importlib.util.find_spec("docling") is not None
```

`sys.modules` 에 `__spec__` 이 없는 모듈이 있으면 `ValueError` 가 나면서
**모듈 import 자체가 실패**합니다. 예외를 잡고, 캐시가 낡았을 때를 위해
`invalidate_caches()` 후 재시도하도록 했습니다.

### C-4. Windows cp949 로케일 크래시

PyTorch inductor 의 템플릿 로더가 시스템 기본 인코딩으로 파일을 읽어,
cp949 환경에서 UTF-8 파일을 읽다 죽었습니다. `winfix.py` 로 호출 시점에
판정해 우회하고, 영구 해결(`PYTHONUTF8=1`)을 안내합니다.

---

## D. LLM 호출 관련

### D-1. 재시도·오류 표면화 없음

원본 `infrastructure/llm/client.py` 에는 재시도가 없었습니다. 429(사용량
한도)나 5xx(일시 오류)에 그대로 실패했고, 오류 본문도 버려져 원인을 알 수
없었습니다.

`Retry-After` 를 존중하는 재시도(최대 3회)와 오류 본문 파싱을 넣었습니다.

### D-2. 인증 헤더 누락

`llm_api_config()` 가 `api_key` 를 담지 않아, 키가 필요한 엔드포인트
(OpenAI 등)에서 유효한 키로도 401 이 났습니다.

### D-3. OCR 언어 코드 오류

```python
opts.lang = ["korean", "en"]   # rapidocr
```

`RapidOcrOptions.lang` 기본값이 `["chinese"]` 입니다 — 축약형이 아니라
전체 단어 규약이므로 `"en"` 이 아니라 `"english"` 여야 합니다.
엔진별 규약이 다르므로 `DOCLING_OCR_LANG` 로 설정 가능하게 했습니다.

| 엔진 | 규약 |
|------|------|
| rapidocr | `korean`, `english`, `chinese` |
| easyocr | `ko`, `en` |
| tesseract | `kor`, `eng` |

> 이 수정이 실제 인식률을 바꾸는지는 확인하지 못했습니다.
> `"en"` 이 조용히 무시됐을 뿐 한국어 인식엔 영향이 없었을 수도 있습니다.

---

## E. 진단이 불가능하던 것

원본에는 "왜 이렇게 나왔는지" 알 수 있는 수단이 없었습니다.
버그는 아니지만 문제 추적을 막던 구조적 결함이라 함께 정리합니다.

| 추가한 것 | 해결한 문제 |
|-----------|-------------|
| `PageTrace` / `TraceStep` | 페이지가 어떤 경로로 처리됐는지 (텍스트 레이어 / OCR / 재추출) |
| `layout.md` | 레이아웃 모델 라벨 vs 파이프라인 처리 결과 대조 — 오인식인지 변환 문제인지 구분 |
| `failed_pages` | Docling 이 로그로만 남기고 결과에서 조용히 빠뜨리던 페이지 |
| `timings` | 어느 단계가 느린지 (GPU 로 줄어드는 구간 표시) |
| `--check` | 환경·의존성·LLM 연결 상태 |

---

## F. 성능

| 항목 | 원본 | 수정 후 |
|------|------|---------|
| LLM 호출 | 순차 | `DOCLING_LLM_CONCURRENCY` 로 병렬 (실측 12초 → 2초) |
| 페이지 이미지 인코딩 | 표마다 반복 | 페이지당 1회 (스레드 안전 캐시) |
| 연산 장치 | 미설정 (CPU 고정) | `DOCLING_DEVICE` 로 지정 |
| 연결 불가 시 | 표마다 재시도 (15회) | 첫 실패 후 중단 (1회) |

---

## 검증 한계

- LLM 은 스텁으로 검증했습니다. 실제 사내 엔드포인트 호출은 확인하지
  못했습니다.
- Docling 실물·GPU 는 이 환경에 없어, 모의 객체로 옵션 구성 로직만
  확인했습니다.
- OCR 언어 코드 수정(D-3)의 실제 인식률 변화는 미확인입니다.
