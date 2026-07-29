# 설치와 배포

저장소: `http://183.96.152.133/mjseo/docstruct.git`

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
pip install "docstruct @ git+http://183.96.152.133/mjseo/docstruct.git@v0.1.11"

# 최신
pip install "docstruct @ git+http://183.96.152.133/mjseo/docstruct.git"
```

**HWP·HWPX·PDF 처리에 필요한 것이 모두 함께 설치됩니다** (0.1.1 부터).
docling 등을 따로 깔 필요가 없습니다.

노트북 UI(파일 선택 위젯)가 필요할 때만 extras 를 붙이세요.

```bash
pip install "docstruct[notebook] @ git+http://183.96.152.133/mjseo/docstruct.git@v0.1.11"
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
docstruct[hwp,pdf] @ git+http://183.96.152.133/mjseo/docstruct.git@v0.1.11
```

### 업데이트

```bash
pip install -U "docstruct @ git+http://183.96.152.133/mjseo/docstruct.git@v0.1.11"

# 같은 태그를 다시 밀었다면 캐시를 비웁니다
pip install --force-reinstall --no-cache-dir \
  "docstruct @ git+http://183.96.152.133/mjseo/docstruct.git@v0.1.11"
```

## 대안 — wheel 파일 전달

git·인증·GCM 설정이 전부 불필요합니다. 막힐 여지가 가장 적습니다.

```bash
# 배포 측
python -m build --wheel          # dist/docstruct-0.1.11-py3-none-any.whl

# 설치 측
pip install docstruct-0.1.11-py3-none-any.whl
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

태그를 붙여 설치하면 이 문제를 겪지 않습니다 (`@v0.1.11`).

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
pip install -U "docstruct @ git+http://183.96.152.133/mjseo/docstruct.git@v0.1.11"
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

## LLM 대비책

사내 엔드포인트에 **연결이 안 되면** 자동으로 대비 엔드포인트로 전환합니다.
기본 대상은 `gpt-5.6-luna` 이며, **키를 넣어야 동작합니다.**

```bash
set OPENAI_API_KEY=sk-...            # Windows
export OPENAI_API_KEY=sk-...         # Linux/macOS
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
