# CLI 사용법

```bash
docstruct [입력] [옵션]
```

설치하면 `docstruct` 명령이 함께 등록됩니다.
소스에서 바로 쓸 때는 `python -m docstruct.cli` 로 대체하세요.

---

## 빠른 시작

```bash
docstruct 보고서.pdf                          # out/보고서/ 에 결과
docstruct 보고서.pdf -o 결과                   # 출력 위치 지정
docstruct 보고서.pdf --no-llm                 # LLM 없이 파싱만
docstruct 문서모음/ --glob "*.hwp" --progress   # 일괄 처리
docstruct --check                            # 환경·LLM 연결 확인
```

지원 형식: `.pdf` `.hwp` `.hwpx`

---

## 입력

| 형태 | 예 | 동작 |
|------|-----|------|
| 파일 | `docstruct 보고서.pdf` | 그 파일만 |
| 디렉터리 | `docstruct 문서모음/` | 안의 모든 지원 파일 |
| 디렉터리 + 패턴 | `docstruct 문서모음/ --glob "*.hwp"` | 패턴에 맞는 것만 |
| 생략 | `docstruct --check` | `--check` 만 할 때 가능 |

`--glob` 은 디렉터리를 줬을 때만 의미가 있습니다 (기본 `*`).

> **패턴은 반드시 인용부호로 감싸세요.** 셸이 먼저 확장하면 `--glob` 에
> 여러 값이 들어가 인자 오류(종료 코드 2)가 납니다.
>
> ```bash
> docstruct 문서모음/ --glob "*.hwp"     # 맞음
> docstruct 문서모음/ --glob *.hwp       # 셸이 확장 → 오류
> ```

---

## 옵션

### 출력

| 옵션 | 기본 | 설명 |
|------|------|------|
| `-o`, `--out 경로` | `out` | 출력 디렉터리. 문서마다 하위 폴더가 생김 |

### LLM 단계

전부 선택입니다. 끄면 파싱 결과가 그대로 나옵니다.

| 옵션 | 효과 |
|------|------|
| `--no-llm` | 표 평가·재추출·목차를 **모두** 끔 — 네트워크 불필요 |
| `--no-assess` | 표 품질 평가 생략 (표를 원본 그대로 둠) |
| `--no-fill` | 평가만 하고 재추출 안 함 |
| `--fill-all` | 품질과 무관하게 모든 표 재추출 (기본은 `wrong`·`insufficient` 만) |
| `--outline` | 의미 경로(목차) 추출 → `outline.md` 추가. 페이지당 LLM 1회 |

`--no-llm` 이 나머지를 덮습니다. `--no-llm --fill-all` 을 함께 주면
재추출은 일어나지 않고, `--no-llm --outline` 은 목차를 건너뜁니다.

```
WARNING --no-llm 이므로 --outline 을 건너뜁니다.
```

```
INFO  표 평가 생략 (--no-llm 또는 --no-assess)
  표         : 1개  (미평가 1)
  표 재추출  : 0개
```

### 연산 장치

| 옵션 | 설명 |
|------|------|
| `--cpu` | GPU 를 쓰지 않습니다. CUDA 오류가 날 때 |

```bash
docstruct 문서.pdf --cpu
```

`--set device=cpu` 와 목적은 같지만 **더 확실합니다.** docling 의 import
사슬(transformers → torch)이 CUDA 를 초기화하기 전에 GPU 를 감추기
때문입니다. `--set` 은 설정만 바꾸므로, import 단계에서 터지는 오류는
막지 못합니다.

```
CUDA call failed lazily at initialization with error:
  device >= 0 && device < num_gpus INTERNAL ASSERT FAILED ... device=1, num_gpus=1
```

이런 오류가 나면 `--cpu` 를 붙이세요. 자동 대처도 있지만
(GPU 를 못 쓰면 감추고, 그래도 터지면 CPU 로 재시도), 처음부터
안 쓰는 편이 빠릅니다.

특정 GPU 를 고르려면:

```bash
docstruct 문서.pdf --set device=cuda:0
```

### 렌더링 (PDF 만 해당)

| 옵션 | 기본 | 설명 |
|------|------|------|
| `--no-render` | — | 페이지 PNG 렌더 생략. **표 평가 정확도가 떨어집니다** |
| `--scale 배율` | `2.0` | 렌더 배율. `1.0` = 72dpi |

표 평가·재추출은 페이지 이미지를 근거로 씁니다. 끄면 텍스트만으로 판정합니다.
HWP·HWPX 는 페이지 개념이 없어 이 옵션이 무관합니다.

### LLM 이 어떻게 정해지나

우선순위는 **`--set` → 환경변수·`.env` → 내장 기본값** 입니다.
연결이 안 되면 대비 엔드포인트로 전환합니다.

```
사내 LLM (기본)
   │  연결 불가 (거부·경로없음·응답없음)
   ▼
대비 엔드포인트 (gpt-5.6-luna)   ← OPENAI_API_KEY 가 있을 때만
   │  키가 없으면
   ▼
표 판정·재추출만 생략 (파싱은 정상)
```

```
WARNING http://218.145... 연결 불가 (연결 거부) — 이후 호출은 대비 엔드포인트로 보냅니다
WARNING 기본 LLM 에 연결할 수 없어 대비 엔드포인트로 전환합니다 — gpt-5.6-luna
```

전환은 **연결 불가일 때만** 일어납니다. 인증 실패(401)나 잘못된 응답은
설정 문제이므로 그대로 알립니다.

> **내장 기본값은 배포 형태에 따라 다릅니다.**
>
> | 받은 것 | 사내 LLM 기본값 |
> |---------|----------------|
> | `docstruct-local.zip` (압축본) | **있음** — 바로 동작 |
> | `pip install` (git·wheel) | **없음** — `.env` 나 `--set` 으로 지정 |
>
> 공개 저장소에 사내 주소를 올리지 않으려고 `site_defaults.py` 를
> 분리했기 때문입니다. `docstruct --check` 의 `설정 출처` 행에서 확인합니다.
>
> ```
> 내장 기본값 (.env 없음 — 덮으려면 cp .env.example .env)
> 내장 기본값 없음 — LLM 을 쓰려면 .env 를 두거나 --set llm_url=... 로 지정하세요
> ```
>
> 사내 배포용 wheel 에 기본값을 넣으려면 `GITLAB.md` 의
> "사내 배포와 공개 배포를 함께 할 때" 를 보세요.

### 설정 (`configure()` 상당)

파이썬 API 의 `configure()` 와 같은 키를 `--set` 으로 전달합니다.

| 옵션 | 설명 |
|------|------|
| `--set 키=값` | 설정 지정. 여러 번 쓸 수 있습니다 |
| `--list-options` | 지정 가능한 키를 출력하고 종료 |

```bash
docstruct 보고서.pdf \
  --set llm_url=http://218.145.29.206:11060/v1 \
  --set llm_model=/model/google_gemma-4-26B-A4B-it \
  --set llm_concurrency=8
```

환경변수나 `.env` 로도 같은 결과를 얻습니다.

```bash
export DOCLING_TABLE_API_URL=http://218.145.29.206:11060/v1
export DOCLING_TABLE_API_MODEL=/model/google_gemma-4-26B-A4B-it
export DOCLING_LLM_CONCURRENCY=8
docstruct 보고서.pdf
```

적용된 값은 `--check` 로 확인하거나, 처리 후 `document.json` 의
`pipeline` 항목에서 볼 수 있습니다.

```jsonc
"pipeline": {
  "llm_url": "http://218.145.29.206:11060/v1/chat/completions",
  "llm_model": "/model/google_gemma-4-26B-A4B-it",
  "llm_concurrency": 8,
  "device": "auto",
  ...
}
```

잘못된 키·형식은 처리 전에 걸립니다 (종료 코드 1).

```
--set 형식이 잘못됐습니다: 'llm_url'
  키=값 형태로 주세요. 예: --set llm_concurrency=8

알 수 없는 설정 키: 'gpu'
  --list-options 로 키 목록을 확인하세요.
```

> 키는 `--set llm_key=...` 로도 되지만, 셸 히스토리에 남으므로
> 아래 `--ask-key` 나 `--key-file` 을 쓰세요.

### API 키

키를 인자로 직접 받는 옵션은 **일부러 두지 않았습니다** — 셸 히스토리와
프로세스 목록(`ps`)에 남기 때문입니다.

| 옵션 | 설명 |
|------|------|
| `--ask-key` | 입력받기. 화면에도 히스토리에도 남지 않음 |
| `--key-file 경로` | 파일에서 읽기 (첫 유효 줄만) |

환경변수나 `.env` 도 그대로 동작합니다.

```bash
export OPENAI_API_KEY=sk-...          # Linux/macOS
set OPENAI_API_KEY=sk-...             # Windows
echo "OPENAI_API_KEY=sk-..." > .env
```

```bash
chmod 600 ~/.openai_key               # 파일로 쓸 때는 권한 확인
docstruct 보고서.pdf --key-file ~/.openai_key
```

### 로그·진행

| 옵션 | 설명 |
|------|------|
| `--progress` | 진행 막대. tqdm 이 없으면 로그로 대체 |
| `-q`, `--quiet` | 요약만 출력 |
| `-v`, `--verbose` | DEBUG 로그 + 실패 시 traceback |

`--progress` 는 문서가 2개 이상일 때 문서 단위 막대를 보여주고,
문서 하나 안에서도 표 평가·재추출 진행을 표시합니다.

```
문서 처리: 100%|██████████| 5/5 [00:12<00:00, 2.41s/건, 문서5.hwp]
```

### 점검

| 옵션 | 설명 |
|------|------|
| `--check` | 환경·LLM 연결만 확인하고 종료. 파일 처리 안 함 |
| `--where` | 설치 위치·버전만 출력. 업그레이드 반영 확인용 |

```
=== 환경 ===
  OK   docstruct       0.1.46 (설치본) — /path/to/docstruct
  OK   파이썬           3.12.3 — /path/to/python
  OK   설정 출처         내장 기본값 (.env 없음)
  OK   PDF 파싱         docling
  OK   HWP 파싱         pyhwp + hwp5html
  ...
=== LLM 연결 ===
  OK   http://... 응답 정상 (HTTP 200)
```

---

## 산출물

```
out/<문서명>/
├── document.json    전체 구조 (본문·표·이미지·처리경로·설정·소요시간)
├── document.md      본문 (표·이미지가 실제 내용으로 펼쳐짐)
├── tables.md        표별 판정 + 재추출 전/후 비교
├── pipeline.md      적용 설정 · 단계별 소요 시간 · 페이지별 처리 경로
├── layout.md        레이아웃 모델 인식 결과 (PDF)
├── outline.md       의미 경로(목차) — `--outline` 을 줬을 때만
├── pages/           페이지 PNG (표가 있는 페이지만, PDF)
└── images/          추출된 그림
```

같은 경로로 다시 실행하면 덮어씁니다. 여러 사람이 같은 서버를 쓰면
`-o` 를 각자 다르게 주거나, 파이썬 API 의 `save(unique=True)` 를 쓰세요.

---

## 종료 코드

| 코드 | 상황 |
|------|------|
| `0` | 성공 |
| `1` | 처리 실패 · 없는 파일 · 대상 없는 디렉터리 · `--check` 연결 실패 |
| `2` | 인자 오류 (argparse 규약) |

스크립트에서 쓸 수 있습니다.

```bash
#!/bin/bash
set -e

if ! docstruct --check >/dev/null 2>&1; then
    echo "LLM 연결 안 됨 → 파싱만 수행"
    OPT="--no-llm"
fi

docstruct 문서모음/ --glob "*.hwp" -o 결과 $OPT --progress -q
echo "처리 완료: $(ls 결과 | wc -l)건"
```

여러 문서를 처리할 때 일부가 실패해도 나머지는 계속 진행하고,
끝에 집계가 나옵니다.

```
총 12건 중 11건 성공, 1건 실패
```

---

## 상황별 조합

| 목적 | 명령 |
|------|------|
| **표가 어디서 깨지는지 보기** | `docstruct 문서.pdf --no-fill` — 판정만, 원본 유지 |
| 완전 오프라인 | `docstruct 문서.pdf --no-llm` |
| 표 품질 우선 (최종 산출) | `docstruct 문서.pdf` (기본값) |
| 표가 대부분 깨져 있음 | `docstruct 문서.pdf --fill-all` |
| 속도 우선 | `docstruct 문서.pdf --no-render` (정확도 하락) |
| 스캔 문서 (해상도 필요) | `docstruct 문서.pdf --scale 3.0` |
| 대량 일괄 | `docstruct 폴더/ --progress -q` |
| 목차까지 | `docstruct 문서.pdf --outline` |

---

## 문제 해결

### `docstruct: command not found`

설치는 됐지만 PATH 에 없습니다.

```bash
python -m pip show docstruct           # Location 확인
python -m docstruct.cli --help         # 이렇게도 됩니다
```

가상환경을 쓴다면 활성화 후 실행하세요. Windows 는 `Scripts\docstruct.exe` 입니다.

### `docling 을 불러올 수 없습니다`

오류 메시지가 실행 중인 파이썬 경로를 알려줍니다. **대부분 다른 파이썬에
설치한 경우**입니다.

```bash
python -m pip show docling docling-slim
```

`docling-slim` 이 없으면 그게 원인입니다 — `docling` 배포물에는 코드가 없고
실제 모듈은 `docling-slim` 이 제공합니다.

### `Docling 모델을 내려받지 못했습니다`

폐쇄망에서 첫 PDF 처리 시 발생합니다. 인터넷이 되는 곳에서 미리 받으세요.

```bash
python -c "from docling.utils.model_downloader import download_models; download_models()"
# ~/.cache/docling 폴더를 대상 장비로 복사
```

HWP·HWPX 만 다루면 이 단계가 필요 없습니다.

### LLM 연결 실패

파싱은 그대로 되고 표 평가·재추출만 생략됩니다. 원인별 대처는
`docstruct --check` 출력과 `INSTALL.md` 를 보세요.

### 로그가 너무 많음

```bash
docstruct 문서.pdf -q                  # 요약만
docstruct 문서.pdf 2>/dev/null         # 로그를 버림 (요약은 남음)
```

---

## 파이썬 API 와의 관계

CLI 는 파이썬 API 를 감싼 얇은 층입니다. 같은 문서를 양쪽으로 처리하면
`document.json` 이 동일합니다.

```python
from docstruct import DocStruct

DocStruct("문서.pdf", assess_tables=False).run().save("out/문서")
```

프로그램에서 결과를 바로 다루려면 API 쪽이 낫습니다 — `to_dict()`,
`to_json_str()` 로 파일을 거치지 않고 받을 수 있습니다.
자세한 내용은 `API.md` 를 보세요.
