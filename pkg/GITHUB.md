# GitHub 로 배포하기

사내 GitLab 과 방식은 같습니다. 다만 **공개 저장소라면 사내 정보가 소스에
들어가지 않도록** 먼저 정리해야 합니다.

## 설치 (사내 GitLab 과 동일)

```bash
# 공개 저장소
pip install "docstruct @ git+https://github.com/<사용자>/docstruct.git@v0.1.11"

# 비공개 저장소 (토큰 필요)
pip install "docstruct @ git+https://<token>@github.com/<사용자>/docstruct.git@v0.1.11"

# SSH 키가 등록되어 있으면
pip install "docstruct @ git+ssh://git@github.com/<사용자>/docstruct.git@v0.1.11"
```

`requirements.txt` 에도 같은 문자열을 씁니다.

## 공개 전 확인 — 사내 정보 분리

기본 엔드포인트는 `src/docstruct/core/site_defaults.py` 에 분리되어 있고
이 파일은 `.gitignore` 대상입니다. 소스 자체에는 사내 주소가 없습니다.

```bash
# 올라갈 파일에 사내 정보가 없는지 확인
git ls-files | xargs grep -l "내부IP\|사내모델명" 2>/dev/null || echo "없음"
```

`site_defaults.py` 가 없으면 LLM 관련 기능(표 평가·재추출·그림 설명)만
비활성화되고, 파싱·표 추출·구조화는 그대로 동작합니다. 각 사용자는
`.env` 로 자기 엔드포인트를 지정하면 됩니다.

```bash
cp src/docstruct/core/site_defaults.example.py src/docstruct/core/site_defaults.py
# 값을 채운 뒤 사용
```

## 사내 배포와 공개 배포를 함께 할 때

`site_defaults.py` 는 `.gitignore` 되어 있어 **wheel 에도 포함되지 않습니다**
(hatchling 이 gitignore 를 따릅니다). 사내 배포용 wheel 에 기본값을 넣으려면
둘 중 하나를 고르세요.

| 방법 | 내용 |
|------|------|
| 사내 저장소에서는 커밋 | 비공개 GitLab 저장소의 `.gitignore` 에서 그 줄만 빼면 wheel 에 포함됩니다 |
| `.env` 로 배포 | `site_defaults.py` 없이 배포하고, 사용자에게 `.env` 를 함께 전달 |

권장은 **사내 GitLab = 커밋 / 공개 GitHub = 제외** 입니다.
두 원격을 같은 로컬 저장소에 두면 `.gitignore` 가 하나뿐이라 곤란하므로,
공개용은 별도 저장소로 두는 편이 단순합니다.

```bash
git remote add gitlab http://183.96.152.133/mjseo/docstruct.git   # 사내
git remote add github https://github.com/<사용자>/docstruct.git    # 공개
```

## API 키는 소스에 두지 마세요

공개 저장소에 키를 커밋하면 GitHub 이 자동으로 탐지해 차단하거나
무효화합니다. 실행 시점에 넣으세요.

```python
import docstruct, getpass

docstruct.set_api_key(getpass.getpass("OpenAI 키: "))
```

환경변수로 넣어도 됩니다.

```bash
set OPENAI_API_KEY=sk-...           # Windows
export OPENAI_API_KEY=sk-...        # Linux/macOS
```

여러 설정을 한 번에 지정하려면:

```python
docstruct.configure(
    openai_key="sk-...",
    llm_url="http://내부주소:포트/v1",
    llm_concurrency=8,
)
# → {'openai_key': 'sk-abc…7890', 'llm_url': '...', 'llm_concurrency': 8}
#    (키는 가려서 반환합니다)
```

`.env` 는 `.gitignore` 대상이므로 여기 넣어도 됩니다.

### 키가 새지 않도록

`OPENAI_API_KEY` 는 **주소가 OpenAI 계열일 때만** 인증 헤더로 붙습니다.
사내 엔드포인트로는 전송되지 않습니다.

| 엔드포인트 | OPENAI_API_KEY 사용 |
|-----------|--------------------|
| `http://사내주소:포트/...` | 안 붙음 |
| `https://api.openai.com/...` | 붙음 |
| `https://*.openai.azure.com/...` | 붙음 |

사내 엔드포인트에 별도 인증이 필요하면 `DOCLING_TABLE_API_KEY` 를 쓰세요.

## 패키지 이름

공개 PyPI 에 이미 `docstruct` 라는 다른 패키지가 있습니다
(OCR 결과를 트리로 파싱하는 별개 라이브러리, 1.0.192).

- **git 주소로 설치하는 한 무관합니다** — pip 이 PyPI 를 조회하지 않습니다.
- 나중에 PyPI 에 올리거나 사내 인덱스를 운영한다면 이름을 바꾸세요
  (`pyproject.toml` 의 `name` 만 수정, `import docstruct` 는 유지).
  미등록 확인한 후보: `jjocr`, `docstruct-kr`, `hwpstruct`, `docparse-kr`

## GitHub Releases 로 wheel 배포

태그를 밀 때 wheel 을 자동으로 올려두면 git clone 없이 받을 수 있습니다.

`.github/workflows/release.yml`:

```yaml
name: release
on:
  push:
    tags: ["v*"]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install build && python -m build --wheel
      - uses: softprops/action-gh-release@v2
        with: { files: dist/*.whl }
```

설치:

```bash
pip install https://github.com/<사용자>/docstruct/releases/download/v0.1.9/docstruct-0.1.11-py3-none-any.whl
```
