# GitHub 에 올리기 — git 명령어

## 먼저 확인 — 이력에 사내 정보가 남아 있습니다

`site_defaults.py` 로 분리하기 전(0.1.8 이하) 커밋에는 사내 LLM 주소가
소스에 박혀 있습니다. **현재 파일만 지운다고 사라지지 않습니다** — git 은
과거 커밋을 그대로 보관하므로 공개 저장소에 밀면 누구나 볼 수 있습니다.

```bash
# 어느 커밋에 들어 있는지
git log --all --oneline -S "218.145.29.207" -- "src/**/*.py"
```

그래서 **공개용은 이력 없이 새로 시작**하는 방법을 권합니다.

---

## 공개용 저장소 만들기 (이력 없이)

```bash
# 1. 작업 사본 (원본 pkg 는 건드리지 않음)
cd ..
cp -r pkg docstruct-public
cd docstruct-public

# 2. 기존 이력 버리기
rm -rf .git dist

# 3. 새로 시작
git init
git branch -M main
git add -A
git commit -m "docstruct 0.1.15"
git tag v0.1.15

# 4. 올리기 전 점검 — 아무것도 안 나와야 함
git grep -n "218\.145\|183\.96" HEAD -- "src/**/*.py"
git ls-files | grep -E "\.env$|site_defaults\.py$"

# 5. GitHub 으로
git remote add origin https://github.com/alcien/docstruct.git
git push -u origin main --tags
```

설치:

```bash
pip install "docstruct @ git+https://github.com/alcien/docstruct.git@v0.1.18"
```

## 이후 갱신

사내에서 고친 것을 공개본에 반영합니다.
`site_defaults.py` 는 제외해야 사내 주소가 넘어가지 않습니다.

```bash
cd docstruct-public

rsync -a --delete --exclude='.git' --exclude='dist' \
      --exclude='src/docstruct/core/site_defaults.py' \
      ../pkg/ ./

# pyproject.toml 의 version 을 올린 뒤
git add -A
git commit -m "0.1.15"
git tag v0.1.15
git push origin main --tags
```

Windows 에 rsync 가 없으면 robocopy 를 쓰세요.

```cmd
robocopy ..\pkg . /MIR /XD .git dist __pycache__ /XF site_defaults.py
```

## 방법 B — 같은 저장소에 원격 두 개

이력에 사내 정보가 없다고 확신할 때만 쓰세요.

```bash
cd pkg
git remote add origin http://183.96.152.133/mjseo/docstruct.git   # 사내
git remote add github https://github.com/alcien/docstruct.git   # 공개

git push origin main --tags     # 사내
git push github main --tags     # 공개
```

`site_defaults.py` 는 `.gitignore` 대상이라 양쪽 모두에 올라가지 않습니다.
사내 wheel 에 기본값을 넣으려면 사내 저장소에서만 `.gitignore` 에서 그 줄을
빼야 하는데, `.gitignore` 는 하나뿐이므로 결국 방법 A 가 단순합니다.

---

## 방법 C — 이미 올렸고 이력을 지워야 할 때

공개 저장소에 이미 밀었다면 **그 정보는 노출된 것으로 간주**하고
서버 주소 변경이나 접근 제한을 먼저 검토하세요. 그 다음 이력을 정리합니다.

```bash
pip install git-filter-repo

# 특정 문자열을 이력 전체에서 치환
echo "218.145.29.207==>INTERNAL_HOST" > replace.txt
git filter-repo --replace-text replace.txt

git push --force origin main --tags
```

`--force` 는 협업자의 사본을 깨뜨립니다. 혼자 쓰는 저장소가 아니면 미리 알리세요.

---

## 일상 작업

```bash
# 상태 확인
git status
git log --oneline -5

# 변경 올리기
git add -A
git commit -m "설명"
git push origin main

# 새 버전 배포
# (pyproject.toml 의 version 을 먼저 수정)
git commit -am "0.1.15"
git tag v0.1.15
git push origin main --tags
```

### 태그를 잘못 달았을 때

```bash
git tag -d v0.1.15                    # 로컬 삭제
git push origin :refs/tags/v0.1.15    # 원격 삭제
git tag -a v0.1.15 -m "0.1.15"        # 다시 달기
git push origin --tags
```

같은 태그를 다시 밀었다면 설치하는 쪽에서 캐시를 비워야 합니다.

```bash
pip install --force-reinstall --no-cache-dir \
  "docstruct @ git+https://github.com/alcien/docstruct.git@v0.1.18"
```

---

## 자주 겪는 오류

**`error: src refspec main does not match any`**

로컬 브랜치가 `main` 이 아닙니다.

```bash
git branch --show-current    # master 로 나오면
git branch -M main
```

**`remote HEAD refers to nonexistent ref`** (push 는 됐는데 pip install 실패)

저장소 기본 브랜치 설정이 실제 브랜치와 다릅니다.
GitHub → Settings → Branches → Default branch 에서 맞추세요.
태그를 지정해 설치하면 이 문제를 겪지 않습니다.

**GitHub 이 push 를 거부 (secret scanning)**

커밋에 API 키가 들어 있습니다. 해당 키는 **이미 노출된 것으로 보고 폐기**한 뒤,
`git filter-repo` 로 이력에서 제거하고 다시 미세요.
키는 `docstruct.set_api_key()` 나 환경변수로 실행 시점에 넣으세요.

---

## 올리기 전 점검

```bash
# ① 현재 소스의 사내 정보 (docstring 예시인 sk-abc… 는 무관)
git grep -n "218\.145\|183\.96" HEAD -- "src/**/*.py"

# ② 커밋되면 안 되는 파일
git ls-files | grep -E "\.env$|site_defaults\.py$"

# ③ 이력까지 포함
git log --all -S "218.145.29.207" --oneline -- "src/**/*.py"
```

①②가 비어 있어도 **③에 결과가 나오면 방법 B(원격 두 개)는 쓸 수 없습니다.**
현재 저장소가 그 상태입니다 — 0.1.4·0.1.7·0.1.9 커밋의 소스에 사내 주소가
들어 있으므로, 공개용은 방법 A로 이력 없이 새로 시작하세요.
