# 설치와 배포

저장소: `http://183.96.152.133/mjseo/docstruct.git`

## 업그레이드했는데 옛 오류가 그대로일 때

**설치가 갱신되지 않은 것입니다.** 가장 흔한 원인 셋입니다.

```bash
docstruct --where
```

```
실행 중인 파이썬 : /opt/conda/bin/python3.11
docstruct 위치   : /opt/conda/lib/python3.11/site-packages/docstruct
버전             : 0.1.37 (pip 설치본)      ← 옛 버전이면 이것
```

| 원인 | 대처 |
|------|------|
| 캐시된 옛 버전 설치 | `--force-reinstall --no-cache-dir` 를 꼭 붙이세요 |
| 다른 파이썬에 설치 | `--where` 의 파이썬 경로로 pip 실행 |
| 노트북 커널이 옛 모듈 유지 | **커널 재시작** (모듈은 한 번 로드되면 안 바뀝니다) |

```bash
"/opt/conda/bin/python3.11" -m pip install -U --force-reinstall --no-cache-dir \
  "docstruct @ git+http://183.96.152.133/mjseo/docstruct.git@v0.1.46"
```

> **노트북에서는 반드시 커널을 재시작하세요.** `pip install` 만으로는
> 이미 import 된 모듈이 바뀌지 않습니다. 재설치 후에도 같은 오류가 나는
> 가장 흔한 이유입니다.

## 버전별 기능

설치본이 오래되면 최신 API 가 없습니다. 버전을 먼저 확인하세요.

```bash
python -m pip show docstruct        # Version
docstruct --check                   # 첫 줄에 버전·경로
```

| 기능 | 최소 버전 |
|------|----------|
| `DocStruct` · `DocStructBatch` · `structure()` | 0.1.0 |
| `configure()` · `set_api_key()` · `defaults()` | **0.1.11** |
| LLM 연결 실패 시 대비 엔드포인트 전환 | 0.1.18 |
| Python 3.13 지원 | 0.1.3 |

`ImportError: cannot import name 'configure' from 'docstruct'` 가 나면
0.1.11 이전 버전입니다.

```bash
pip install -U --force-reinstall --no-cache-dir \
  "docstruct @ git+http://183.96.152.133/mjseo/docstruct.git@v0.1.46"
```

업그레이드가 어려우면 아래 두 방법으로 같은 설정을 할 수 있습니다.

```python
# ① 문서별 (0.1.0 부터)
ds = docstruct.DocStruct("문서.pdf")
ds.set(llm_url="http://...", llm_model="...", llm_concurrency=8)

# ② 환경변수 (모든 버전)
import os
os.environ["DOCLING_TABLE_API_URL"] = "http://.../v1"
os.environ["DOCLING_TABLE_API_MODEL"] = "..."
os.environ["DOCLING_LLM_CONCURRENCY"] = "8"
from docstruct.core.config import rebuild_settings
rebuild_settings()
```

## 지원 파이썬

**3.10 ~ 3.13** 입니다.

0.1.2 까지는 구버전 `rapidocr-onnxruntime` 을 쓰고 있었는데, 그 패키지의
`requires-python` 이 `<3.13` 이라 **Python 3.13 에서 설치가 막혔습니다.**
0.1.3 부터 docling 이 쓰는 신규 `rapidocr` 로 바꿔 해결했습니다.

3.13 에서 설치가 안 되면 버전을 확인하세요.

```bash
python -m pip show docstruct        # Version 이 0.1.3 이상인지
```

## 설치

```bash
# Windows 는 GCM 의 HTTP 차단을 먼저 풀어야 합니다 (한 번만)
git config --global credential.allowUnsafeRemotes true

# 버전 고정 (권장)
pip install "docstruct @ git+http://183.96.152.133/mjseo/docstruct.git@v0.1.46"

# 최신
pip install "docstruct @ git+http://183.96.152.133/mjseo/docstruct.git"
```

**HWP·HWPX·PDF 처리에 필요한 것이 모두 함께 설치됩니다** (0.1.1 부터).
docling 등을 따로 깔 필요가 없습니다.

노트북 UI(파일 선택 위젯)가 필요할 때만 extras 를 붙이세요.

```bash
pip install "docstruct[notebook] @ git+http://183.96.152.133/mjseo/docstruct.git@v0.1.46"
```

`[hwp]` `[hwpx]` `[pdf]` 는 빈 별칭으로 남겨 두었으므로 기존 명령을 써도
오류 없이 동작합니다.

저장소가 Public 이면 인증을 묻지 않습니다. 여전히 묻는다면 캐시된 자격증명이
남아 있는 것입니다.

> 제어판 → 자격 증명 관리자 → Windows 자격 증명 → `183.96.152.133` 항목 삭제

### requirements.txt

주소를 함께 적어야 합니다. 이름만 적으면 공개 PyPI 의 **다른 패키지**가
설치됩니다 (같은 이름의 무관한 패키지가 존재합니다).

```
docstruct[hwp,pdf] @ git+http://183.96.152.133/mjseo/docstruct.git@v0.1.46
```

### 업데이트

```bash
pip install -U "docstruct @ git+http://183.96.152.133/mjseo/docstruct.git@v0.1.46"

# 같은 태그를 다시 밀었다면 캐시를 비웁니다
pip install --force-reinstall --no-cache-dir \
  "docstruct @ git+http://183.96.152.133/mjseo/docstruct.git@v0.1.46"
```

## 대안 — wheel 파일 전달

git·인증·GCM 설정이 전부 불필요합니다. 막힐 여지가 가장 적습니다.

```bash
# 배포 측
python -m build --wheel          # dist/docstruct-0.1.46-py3-none-any.whl

# 설치 측
pip install docstruct-0.1.46-py3-none-any.whl
pip install --find-links \\파일서버\share\python docstruct
```

GitLab **Releases** 에 wheel 을 첨부해 두는 것도 같은 효과입니다.

## 올리기

```bash
cd pkg
git init
git branch -M main                   # git 기본값이 master 이므로 맞춰준다
git remote add origin http://183.96.152.133/mjseo/docstruct.git
git add -A
git commit -m "docstruct 0.1.0"
git tag v0.1.1
git push -u origin main --tags
```

`.gitignore` 가 `.env`(실값), `dist/`, `__pycache__` 를 제외합니다.
`.env.example` 만 올라갑니다.

### 자주 겪는 오류

**`error: src refspec main does not match any`**

로컬 브랜치가 `main` 이 아닙니다.

```bash
git branch --show-current    # master 로 나오면
git branch -M main
git push -u origin main --tags
```

**`fatal: Unencrypted HTTP is not recommended for GitLab`**

Git Credential Manager 가 평문 HTTP 를 거부한 것입니다.

```bash
git config --global credential.allowUnsafeRemotes true
```

이걸로 안 되면 해당 호스트만 GCM 을 건너뜁니다.

```bash
git config --global "credential.http://183.96.152.133.provider" generic
git config --global "credential.http://183.96.152.133.helper" store
```

**push 는 됐는데 `pip install` 이 실패**

```
warning: remote HEAD refers to nonexistent ref, unable to checkout
```

저장소 기본 브랜치 설정과 실제 올라간 브랜치가 다릅니다.

> GitLab → Settings → Repository → Branch defaults → Default branch

태그를 붙여 설치하면 이 문제를 겪지 않습니다 (`@v0.1.46`).

## 버전 올리기

`pyproject.toml` 의 `version` 을 고치고 태그를 답니다.
**태그를 새로 달지 않으면 설치하는 쪽에서 옛 버전을 계속 받습니다.**

```bash
# pyproject.toml 에서 version = "0.1.2" 로 수정
git commit -am "0.1.2"
git tag v0.1.2
git push origin main --tags
```

설치하는 쪽:

```bash
pip install -U "docstruct @ git+http://183.96.152.133/mjseo/docstruct.git@v0.1.46"
```

## 설정 — 기본값이 들어 있습니다

> 기본 엔드포인트는 `src/docstruct/core/site_defaults.py` 에 있습니다.
> 이 파일이 없으면(공개 저장소에서 받은 경우) LLM 기능만 비활성화되고
> 파싱·표 추출은 그대로 동작합니다. `.env` 로 지정하거나
> `site_defaults.example.py` 를 복사해 채우세요.

사내 공용 LLM 엔드포인트·모델이 코드에 기본값으로 들어 있어 **설치 직후
별도 설정 없이 동작합니다.** `.env` 는 기본값을 덮고 싶을 때만 씁니다.

우선순위: **환경변수 → `.env` → 내장 기본값**

```bash
docstruct --check          # 현재 값과 출처(내장 기본값 / .env) 표시
```

```python
from docstruct import defaults
print(defaults())          # 내장 기본값 확인

from docstruct import DocStruct
ds = DocStruct("문서.pdf")
ds.set(llm_url="http://다른서버:9000/v1")   # 코드에서 덮기
```

바꾸고 싶은 항목만 `.env` 에 적으면 됩니다.

```
DOCLING_TABLE_API_URL=http://다른서버:9000/v1/chat/completions
DOCLING_LLM_CONCURRENCY=8
```

서버 주소가 바뀌면 `core/config.py` 의 `_DEFAULTS` 를 고치고 버전을 올리거나,
각 사용자가 `.env` 로 덮으면 됩니다.

## "RapidOCR is not installed" — 깔려 있는데도 나올 때

```
RapidOCR is not installed. Please install it via `pip install rapidocr onnxruntime`
```

`pip freeze` 에 `rapidocr` 가 보이는데도 이 메시지가 나옵니다.

**`rapidocr` 3.x 는 실행 백엔드를 의존성으로 선언하지 않습니다.**
`onnxruntime` 이 없으면 `import rapidocr` 은 성공하지만 실제 인식에서
`onnxruntime is not installed` 로 실패하고, docling 이 이를
"RapidOCR is not installed" 로 바꿔 보여 줍니다.

```bash
python -m pip install onnxruntime
```

`docstruct` 를 통해 설치하면 함께 깔립니다. `--no-deps` 를 쓰셨거나
docling 만 따로 설치한 경우 빠질 수 있습니다.

`docstruct --check` 의 `OCR 실행 준비` 행에서 확인합니다.

```
WARN OCR 실행 준비   rapidocr 는 있으나 실행 백엔드(onnxruntime)가 없음 — "..." -m pip install onnxruntime
OK   OCR 실행 준비   rapidocr + onnxruntime
```

다른 런타임을 쓸 수도 있습니다.

```bash
# GPU 를 쓴다면
export DOCLING_RAPIDOCR_RUNTIME=torch
python -m pip install torch
```

| `DOCLING_RAPIDOCR_RUNTIME` | 필요한 패키지 |
|---------------------------|--------------|
| `onnxruntime` (기본) | `onnxruntime` |
| `torch` | `torch` |
| `openvino` | `openvino` |
| `paddle` | `paddlepaddle` |

## 큰 HWP 가 멈춘 것처럼 보일 때

`hwp5html`(pyhwp)은 표·이미지가 많은 문서에서 매우 느립니다. 3.5MB 문서가
수 분을 넘기기도 합니다.

```
INFO  HWP → HTML 변환 중 (3.4MB) — 큰 문서는 몇 분 걸릴 수 있습니다
      (제한 300초, DOCSTRUCT_HWP_TIMEOUT 로 조정)
```

제한 시간을 넘기면 **멈추지 않고 텍스트 전용 경로로 내려갑니다.**

```
WARNING hwp5html 이 300초 안에 끝나지 않았습니다 (3.4MB).
        표 구조 없이 텍스트만 뽑아 계속합니다.
        더 기다리려면 DOCSTRUCT_HWP_TIMEOUT 을 늘리세요 (초 단위).
```

```bash
export DOCSTRUCT_HWP_TIMEOUT=900     # 15분까지 기다리기
```

텍스트 경로(`olefile-text`)로 내려가면 **표 구조가 사라집니다.**
`trace.summary()` 에서 어느 경로였는지 확인할 수 있습니다.

| 경로 | 표 구조 | 속도 |
|------|---------|------|
| `pyhwp-html` | 보존 | 느림 |
| `hwpml-xml` | 보존 | 빠름 (XML 형식 문서) |
| `olefile-text` | **없음** | 빠름 |

## GPU 관련 오류

### `device >= 0 && device < num_gpus` / `DeferredCudaCallError`

```
RuntimeError: device >= 0 && device < num_gpus INTERNAL ASSERT FAILED
              device=1, num_gpus=1
```

**장치 인덱스가 어긋난 것**입니다. torch 는 GPU 를 1개(0번)만 보는데
1번을 요구하고 있습니다. 흔한 원인은 셋입니다.

| 원인 | 확인 |
|------|------|
| `CUDA_VISIBLE_DEVICES` 설정과 실제 GPU 불일치 | `echo $CUDA_VISIBLE_DEVICES` |
| 컨테이너에 GPU 가 일부만 매핑됨 | `nvidia-smi` 와 `torch.cuda.device_count()` 비교 |
| 커널 시작 후 GPU 구성이 바뀜 | 커널·프로세스 재시작 |

```python
import torch
print(torch.cuda.is_available(), torch.cuda.device_count())
```

**CLI 에서는 `--cpu` 로 확실하게 피할 수 있습니다.**

```bash
docstruct 문서.pdf --cpu
```

docling 의 import 사슬이 CUDA 를 건드리기 전에 GPU 를 감춥니다.
`--set device=cpu` 는 설정만 바꾸므로 import 단계 오류는 막지 못합니다.

**0.1.31 부터는 자동 대처도 합니다.**

- `device="auto"` 일 때 GPU 를 실제로 만져 보고, 안 되면 CPU 로 못 박습니다
  (`is_available()` 만으로는 이 상황이 걸러지지 않습니다).
- CPU 로 정해지면 docling 을 import 하기 전에 GPU 를 감춥니다
  (torch 가 아직 로드되지 않았을 때만 효과가 있습니다).
- 그래도 GPU 오류가 나면 컨버터 생성·변환 어느 단계든 CPU 로 재시도합니다.

```
WARNING GPU 로 처리하지 못해 CPU 로 다시 시도합니다 — DeferredCudaCallError: device=1, num_gpus=1
```

### GPU 가 여러 장 보이지만 일부만 쓸 수 있을 때

공용 서버에서 1장만 배정받은 경우가 흔합니다. `torch.cuda.device_count()`
는 2를 돌려주지만 실제로 만질 수 있는 것은 하나뿐입니다.

**0.1.39 부터 `auto` 가 쓸 수 있는 장치를 직접 찾습니다.**

```python
docstruct.configure(device="auto")   # 기본값 — 그대로 두면 됩니다
```

```
연산 장치: cuda:1        ← 0번이 남의 것이면 알아서 1번을 씁니다
```

번호를 직접 지정했는데 그 장치에 접근할 수 없으면 사유를 알려줍니다.

```
cuda:0 에 접근할 수 없습니다 — 쓸 수 있는 것은 cuda:1 입니다 — CPU 로 처리합니다
cuda:5 를 쓸 수 없습니다 — 보이는 GPU 는 2개(0~1번)입니다 — CPU 로 처리합니다
```

`docstruct --check` 의 `연산 장치` 행에서 실제로 무엇을 쓰는지 확인합니다.

### GPU 가 여러 장일 때 (직접 고르기)

`CUDA_VISIBLE_DEVICES` 는 **torch 가 CUDA 를 초기화하기 전에** 설정해야
합니다. 노트북에서 `import torch` 뒤에 설정하면 **아무 효과가 없습니다** —
이미 초기화된 상태라 조용히 무시되고, 오히려 인덱스가 어긋나
`device=1, num_gpus=1` 같은 오류가 납니다.

```python
# ✘ 효과 없음 — torch 가 이미 CUDA 를 초기화한 뒤
import torch
import os
os.environ["CUDA_VISIBLE_DEVICES"] = "0"
```

> **가장 흔한 함정입니다.** `import torch` 뒤에 `os.environ` 으로 바꾸면
> torch 가 캐시한 GPU 개수와 실제 보이는 개수가 어긋나
> `device=1, num_gpus=1` 로 죽습니다.
>
> ```
> 1. import torch            → device_count()=2 로 캐시
> 2. os.environ[...] = "0"   → 새 CUDA 컨텍스트는 1개만 봄
> 3. CUDA 호출              → torch 는 0,1 번을 확인 → 1 번이 없음 → ASSERT
> ```
>
> 0.1.38 부터는 이 불일치를 감지해 CPU 로 내려가고 사유를 알려줍니다.
>
> ```
> CUDA_VISIBLE_DEVICES='0' 는 GPU 1개를 지정하는데 torch 는 2개로 알고 있습니다.
> import torch 뒤에 이 변수를 바꾸면 이렇게 어긋나고, CUDA 호출이 실패합니다.
> ```

노트북에서 쓰려면 **맨 첫 셀에서, 어떤 import 보다도 먼저** 설정하고
커널을 재시작하세요.

```python
# ✔ 첫 셀 — torch·docstruct 를 import 하기 전
import os
os.environ["CUDA_VISIBLE_DEVICES"] = "0"
```

커널 밖에서 지정하는 편이 확실합니다.

```bash
CUDA_VISIBLE_DEVICES=0 jupyter lab
```

**또는 docstruct 에서 장치를 직접 고르세요.** 환경변수를 건드리지 않아
초기화 시점 문제가 없습니다 (0.1.32 부터).

```python
docstruct.configure(device="cuda:0")     # 0번 GPU
ds.set(device="cuda:1")                  # 1번 GPU
```

```bash
docstruct 문서.pdf --set device=cuda:0
export DOCLING_DEVICE=cuda:0
```

없는 번호를 지정하면 처리 전에 알려주고 CPU 로 내려갑니다.

```
cuda:3 를 쓸 수 없습니다 — 이 프로세스에 보이는 GPU 는 2개(0~1번)입니다 — CPU 로 처리합니다
```

명시적으로 CPU 를 쓰려면:

```python
ds.set(device="cpu")
docstruct.configure(device="cpu")
```

```bash
docstruct 문서.pdf --set device=cpu
export DOCLING_DEVICE=cpu
```

`docstruct --check` 의 `연산 장치` 행에서 실제로 무엇을 쓰는지 확인합니다.

## LLM 대비책

사내 엔드포인트에 **연결이 안 되면** 자동으로 대비 엔드포인트로 전환합니다.
기본 대상은 `gpt-5.6-luna` 이며, **키를 넣어야 동작합니다.**

```bash
set OPENAI_API_KEY=sk-...            # Windows
export OPENAI_API_KEY=sk-...         # Linux/macOS
```

CLI 에서는 아래도 됩니다. 인자로 키를 직접 넘기는 옵션은 일부러 두지
않았습니다 — 셸 히스토리와 프로세스 목록에 남기 때문입니다.

```bash
docstruct 문서.pdf --ask-key                 # 입력받기 (화면에 안 보임)
docstruct 문서.pdf --key-file ~/.openai_key  # 파일에서
```

```
WARNING http://218.145... 연결 불가 — 이후 LLM 호출을 건너뜁니다 (연결 거부)
WARNING 기본 LLM 에 연결할 수 없어 대비 엔드포인트로 전환합니다 — gpt-5.6-luna
```

`docstruct --check` 의 `LLM 대비책` 행에서 준비 상태를 볼 수 있습니다.

전환 조건은 **연결 불가일 때뿐**입니다. 인증 실패(401)나 잘못된 응답은
설정 문제이므로 그대로 알리고 전환하지 않습니다.

끄려면 `DOCLING_TABLE_API_FALLBACK=off` 를 지정하세요.

> 폐쇄망이라면 OpenAI 에도 닿지 않으므로 대비책이 의미가 없습니다.
> 그 경우 키를 넣지 않으면 전환 시도 없이 표 평가만 생략됩니다.

## LLM 연결이 안 될 때

`docstruct --check` 의 `=== LLM 연결 ===` 이 실패하면 사유가 함께 나옵니다.

| 메시지 | 원인 | 대처 |
|--------|------|------|
| 연결을 거부 | 서버가 꺼졌거나 포트가 다름 | 서버·포트 확인 |
| 가는 경로 없음 | 네트워크 분리·라우팅 | 아래 컨테이너 항목 참고 |
| 이름을 찾을 수 없음 | DNS | 주소를 IP 로 지정 |
| 응답 없음 | 방화벽 | 사내 방화벽 정책 확인 |

### Docker 컨테이너에서

컨테이너 안에서 실행 중이면 그 사실을 감지해 함께 알려줍니다.
호스트에서는 되는데 컨테이너에서만 안 된다면 **네트워크 격리**가 원인입니다.

```bash
# 컨테이너 안에서 실제로 닿는지 확인
docker exec <컨테이너> curl -sv --max-time 5 http://218.145.29.207:11060/v1/models

# 호스트 네트워크를 그대로 쓰기
docker run --network host ...
```

`docker-compose.yml` 이면:

```yaml
services:
  app:
    network_mode: host
```

`--network host` 를 쓸 수 없는 환경(예: Docker Desktop for Mac/Windows)이라면
사내 LLM 이 컨테이너 네트워크에서 보이도록 라우팅을 열어야 합니다.

LLM 없이 파싱만 하실 거면 연결 실패는 무시해도 됩니다 —
표 평가·재추출 단계만 자동으로 생략됩니다.

```python
DocStruct("문서.pdf", assess_tables=False).run()
```

## 사용

```bash
docstruct 문서.pdf -o out/
docstruct 문서모음/ --glob "*.pdf" --progress
docstruct --check                    # 환경·LLM 연결 점검
```

```python
from docstruct import DocStruct, DocStructBatch

ds = DocStruct("보고서.pdf")
ds.set(device="auto", llm_concurrency=4)
ds.run()
ds.to_json("결과.json")

batch = DocStructBatch("문서모음/", pattern="*.pdf", progress=True).run()
batch.to_json("결과/")
```

LLM(표 평가·재추출·그림 설명)을 쓸 때만 설정이 필요합니다.
작업 디렉터리에 `.env` 를 두면 자동으로 읽습니다 (`.env.example` 참고).

## 참고 — 사내 인덱스로 전환하게 되면

`pip install docstruct` 처럼 주소 없이 설치하려면 GitLab Package Registry 를
쓰면 되는데, 그때는 **배포명을 고유한 값으로 바꿔야 합니다**.

공개 PyPI 에 같은 이름(`docstruct`)의 다른 패키지가 1.0.x 버전으로 올라와
있어서, `extra-index-url` 을 함께 쓰면 pip 이 두 인덱스를 합쳐 더 높은 버전인
공개 패키지를 설치합니다.

- `pyproject.toml` 의 `name` 만 바꾸면 됩니다 (예: `jjocr`). `import docstruct` 는 그대로.
- 받는 쪽 PC 에 `C:\ProgramData\pip\pip.ini` 를 한 번 배포하면 사용자는
  `pip install <이름>` 만 치면 됩니다.
- `.gitlab-ci.yml` 에 태그 push 시 Registry 로 배포하는 설정이 들어 있습니다.

git 주소로 설치하는 동안은 pip 이 PyPI 를 조회하지 않으므로 이 문제가 없습니다.
