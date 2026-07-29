# 배포 방법

## 빌드 전 검사 (필수)

패키지 구조상 `converters` `core` `infrastructure` 가 `docstruct` 하위로
들어가 있습니다. 이 경로를 잘못 적으면 **설치 전까지는 드러나지 않고**
설치본에서 `ModuleNotFoundError` 가 납니다. 함수 안의 지연 import 는
구문 검사로도 안 잡히므로 아래를 돌리세요.

```bash
python tools/verify_package.py                       # 구 경로 검사만
python tools/verify_package.py .venv/bin/python      # + 실제 import 검증
```

```
① 구 경로 import 검사 (converters / core / infrastructure)
   OK — 없음
② 내부 모듈 실제 import (.venv/bin/python)
   OK — 46개 모듈 모두 정상
통과
```

## 빌드

```bash
pip install build
python -m build --wheel        # dist/docstruct-0.1.12-py3-none-any.whl
python -m build                # sdist 도 함께
```

## 배포 경로 선택

| 방법 | 명령 | 적합한 경우 |
|------|------|-------------|
| wheel 직접 전달 | `pip install ./docstruct-0.1.12-py3-none-any.whl` | 소수 인원, 폐쇄망 |
| git 저장소 | `pip install git+ssh://git@183.96.152.133/mjseo/docstruct.git@v0.1.12` | 사내 git 이 있을 때 |
| 사내 PyPI | `pip install --index-url https://pypi.내부/simple docstruct` | 여러 팀 배포 |
| 공개 PyPI | `twine upload dist/*` | 외부 공개 시 |

사내 PyPI 는 devpi 나 pypiserver 로 간단히 띄울 수 있습니다.

```bash
pip install pypiserver
pypi-server run -p 8080 ~/packages/     # dist/*.whl 을 여기에 복사
pip install --index-url http://서버:8080/simple docstruct
```

## 버전 올리기

`pyproject.toml` 의 `version` 을 수정한 뒤 다시 빌드합니다.
설치된 쪽은 `pip install -U` 로 갱신합니다.

## 개발 중 설치 (권장 작업 방식)

```bash
pip install -e ".[all,dev]"
```

`-e` 로 설치하면 `src/docstruct/` 를 고치는 즉시 반영되므로,
**소스를 한 벌만 유지하면 됩니다.** 재설치도 필요 없습니다.

소스 트리를 복사해 여러 곳에서 따로 고치면 반드시 어긋납니다.
이 저장소를 유일한 소스로 두고, 실행은 editable 설치나 wheel 설치로 하세요.

## 소스 트리에서 바로 실행하기

설치 없이 쓰려면 `src/` 를 경로에 넣으면 됩니다.

```bash
PYTHONPATH=src python -m docstruct.cli 문서.pdf -o out/
```

`.env` 는 다음 순서로 찾으므로 어느 방식이든 동작합니다.

1. `DOCSTRUCT_ENV` 환경변수
2. 현재 작업 디렉터리에서 위로 (프로젝트 루트에서 중단)
3. 패키지가 놓인 디렉터리와 그 상위 (소스 트리 실행용)
4. `~/.config/docstruct/.env`
