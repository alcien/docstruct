# GitLab 으로 배포하기

## 먼저 — 패키지 이름을 바꿔야 합니다

**공개 PyPI 에 이미 `docstruct` 가 있습니다** (버전 1.0.192, OCR 결과를 트리로
파싱하는 별개 패키지). 이대로 두면 두 가지 문제가 생깁니다.

1. `pip install docstruct` 하면 남의 패키지가 설치됩니다.
2. 사내 인덱스를 `--extra-index-url` 로 추가하면, pip 이 두 인덱스를 합쳐
   **가장 높은 버전**을 고릅니다. 사내 `0.1.0` vs 공개 `1.0.192` → 공개 것이
   설치됩니다. (dependency confusion)

`pyproject.toml` 의 배포명만 바꾸면 됩니다. **import 이름은 그대로 `docstruct`**
이므로 코드는 손대지 않습니다.

```toml
[project]
name = "jjocr"          # 저장소 이름과 맞춤. PyPI 미등록 확인함

[project.optional-dependencies]
all = ["jjocr[pdf,hwp,hwpx,notebook]"]
```

```bash
pip install jjocr        # 설치는 jjocr
python -c "import docstruct"   # import 는 docstruct
```

미등록 확인한 다른 후보: `cslee-docstruct`, `docstruct-kr`, `hwpstruct`, `docparse-kr`


## 1. 저장소에 올리기

`pyproject.toml` 이 저장소 루트에 있으면 됩니다. 현재 구조가 이미 그렇습니다.

```
docstruct/                 ← 저장소 루트
├── pyproject.toml         ← pip 이 이 파일을 찾는다
├── .gitignore
├── README.md
├── src/docstruct/         ← 실제 코드
└── notebooks/
```

```bash
cd pkg
git init
git branch -M main                    # git 2.x 는 기본이 master 이므로 맞춰준다
git remote add origin http://183.96.152.133/mjseo/docstruct.git
git add -A
git commit -m "docstruct 0.1.0"
git tag v0.1.1
git push -u origin main --tags
```

### `error: src refspec main does not match any`

로컬 브랜치가 `main` 이 아니어서 나는 오류입니다. `git init` 만 하면
브랜치 이름이 `master` 입니다 (git 2.x 기본값).

```bash
git branch --show-current             # 확인 → master
git branch -M main                    # main 으로 변경
git push -u origin main --tags
```

`master` 를 그대로 쓰셔도 됩니다. 그 경우 `git push -u origin master` 로 미세요.

### push 는 됐는데 `pip install` 이 실패할 때

```
warning: remote HEAD refers to nonexistent ref, unable to checkout
fatal: ambiguous argument 'HEAD': unknown revision
```

**저장소의 기본 브랜치 설정이 실제로 올라간 브랜치와 다를 때** 납니다.
`master` 로 설정된 프로젝트에 `main` 을 밀었거나 그 반대인 경우입니다.

GitLab 에서 바로잡습니다.

> Settings → Repository → Branch defaults → Default branch → 실제 브랜치 선택

브랜치나 태그를 명시하면 이 설정과 무관하게 설치되므로, **버전 고정 설치를
쓰면 애초에 겪지 않습니다.**

```bash
pip install "docstruct @ git+http://183.96.152.133/mjseo/docstruct.git@v0.1.11"
```

`.gitignore` 가 `.env`(실값), `dist/`, `__pycache__` 를 제외합니다.
`.env.example` 만 올라갑니다.

## 2. 설치

```bash
# 최신 (기본 브랜치)
pip install "docstruct @ git+https://183.96.152.133/mjseo/docstruct.git"

# 버전 고정 (권장 — 태그)
pip install "docstruct @ git+https://183.96.152.133/mjseo/docstruct.git@v0.1.11"

# 포맷별 의존성 함께
pip install "docstruct[hwp] @ git+https://183.96.152.133/mjseo/docstruct.git@v0.1.11"
pip install "docstruct[all] @ git+https://183.96.152.133/mjseo/docstruct.git@v0.1.11"

# SSH (키가 등록되어 있을 때)
pip install "docstruct @ git+ssh://git@183.96.152.133/mjseo/docstruct.git@v0.1.11"
```

`requirements.txt` 에 넣을 때도 같은 문자열을 씁니다.

```
docstruct[hwp] @ git+https://183.96.152.133/mjseo/docstruct.git@v0.1.11
```

## 3. 비공개 저장소 인증

| 방법 | 형태 | 쓰임 |
|------|------|------|
| Deploy Token | `git+https://<user>:<token>@gitlab.../docstruct.git` | 서버·CI 배포용 (읽기 전용 토큰 발급) |
| Personal Token | `git+https://oauth2:<token>@gitlab.../docstruct.git` | 개인 개발 환경 |
| SSH 키 | `git+ssh://git@gitlab.../docstruct.git` | 키가 이미 등록된 환경 |
| CI Job Token | `git+https://gitlab-ci-token:${CI_JOB_TOKEN}@gitlab.../docstruct.git` | 다른 프로젝트의 CI 에서 |

토큰을 명령줄에 직접 쓰면 셸 히스토리에 남습니다. `~/.netrc` 나 환경변수를 쓰세요.

```bash
# ~/.netrc
machine 183.96.152.133
login <user>
password <token>
```

## 4. `pip install <이름>` 한 줄로 쓰기

저장소 주소 없이 설치하려면 사내 인덱스를 pip 기본 설정에 넣습니다.
GitLab Package Registry 에 올린 뒤 아래처럼 설정합니다.

`~/.pip/pip.conf` (Linux/macOS) 또는 `%APPDATA%\pip\pip.ini` (Windows):

```ini
[global]
index-url = https://pypi.org/simple
extra-index-url = https://__token__:<deploy-token>@183.96.152.133/api/v4/projects/<프로젝트ID>/packages/pypi/simple
```

```bash
pip install jjocr        # 주소 없이 설치됨
```

### `extra-index-url` 의 함정

pip 은 `index-url` 과 `extra-index-url` 을 **합쳐서 가장 높은 버전**을 고릅니다.
공개 PyPI 에 같은 이름이 생기면 그쪽이 설치될 수 있습니다.
사내 패키지만 쓰는 환경이라면 `index-url` 자체를 사내로 바꾸는 편이 안전합니다.

```ini
[global]
index-url = https://__token__:<token>@183.96.152.133/api/v4/projects/<ID>/packages/pypi/simple
extra-index-url = https://pypi.org/simple     # 외부 의존성용
```

이 경우에도 순서상 사내가 먼저 조회되지만 버전 비교는 여전히 합쳐서 하므로,
**고유한 이름을 쓰는 것이 가장 확실한 방어**입니다.

## 5. GitLab Package Registry (선택)

여러 팀에 배포한다면 저장소 주소 대신 PyPI 호환 인덱스를 쓸 수 있습니다.
설치 명령이 짧아지고 버전 관리가 쉬워집니다.

`.gitlab-ci.yml`:

```yaml
publish:
  stage: deploy
  image: python:3.12
  rules:
    - if: $CI_COMMIT_TAG          # 태그를 밀 때만
  script:
    - pip install build twine
    - python -m build
    - TWINE_USERNAME=gitlab-ci-token
      TWINE_PASSWORD=$CI_JOB_TOKEN
      python -m twine upload
        --repository-url ${CI_API_V4_URL}/projects/${CI_PROJECT_ID}/packages/pypi
        dist/*
```

설치:

```bash
pip install docstruct \
  --index-url https://gitlab-ci-token:<token>@183.96.152.133/api/v4/projects/<프로젝트ID>/packages/pypi/simple
```

## 6. 버전 올리기

`pyproject.toml` 의 `version` 을 고치고 태그를 답니다.

```bash
# pyproject.toml: version = "0.1.2"
git commit -am "0.2.0"
git tag v0.1.2
git push --tags
```

설치한 쪽:

```bash
pip install -U "docstruct @ git+https://183.96.152.133/mjseo/docstruct.git@v0.1.11"
```

git 설치는 캐시가 남을 수 있으므로 같은 태그를 다시 밀었다면
`--force-reinstall --no-cache-dir` 을 붙이세요.

## 확인된 동작

로컬 git 저장소를 만들어 실제로 설치해 검증했습니다.

| 항목 | 결과 |
|------|------|
| `git+` 주소로 설치 | 성공 |
| 태그 지정 (`@v0.1.11`) | 성공 |
| extras (`[hwp]`) 동시 설치 | 성공 — bs4·lxml·olefile 함께 설치됨 |
| `docstruct` CLI 등록 | 성공 |
| 설치본으로 문서 구조화 → JSON | 성공 |
| `.env` 실값 제외 | 확인 (`.env.example` 만 커밋됨) |
| 브랜치명 `main` / `master` | 둘 다 설치 가능 (기본 브랜치 설정만 맞으면 됨) |
| 기본 브랜치 불일치 시 | `pip install` 실패 재현 → 브랜치/태그 명시로 회피 확인 |

GitLab HTTPS/SSH 주소 문법은 pip 파싱을 통과하는 것까지 확인했습니다
(실제 사내 GitLab 접속은 이 환경에서 불가).
