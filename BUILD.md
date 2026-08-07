# 배포 방법

## 배포 형태는 셋, 원본은 하나

같은 코드가 세 가지로 나갑니다. 차이는 **임포트 루트와 폴더 배치뿐**입니다.

| 트리 | 임포트 루트 | 배치 | 용도 |
|------|------------|------|------|
| `pkg` (여기) | `docstruct.converters` … | `src/docstruct/**` | pip 패키지 |
| `docstruct-local` | `converters` / `docstruct` | 평면 4-패키지 | 로컬 개발·노트북 |
| `overlay` | 위와 동일 + `rag` | `app/` 아래 | backend-main 덮어쓰기 |

**손으로 세 벌을 맞추지 마세요.** 수정은 `pkg` 에만 하고 나머지는 생성합니다.

```bash
python tools/sync_trees.py --out dist/ --extras /경로/overlay/app
```

`--extras` 는 pkg 에 없는 overlay 전용 폴더(`rag/`,
`infrastructure/observability/`, `main.py.patched`)를 가져올 위치입니다.
이 셋은 backend 원본 소유라 pkg 가 관리하지 않습니다.

CI 에서는 드리프트만 검사할 수도 있습니다.

```bash
python tools/sync_trees.py --check ../docstruct-local ../overlay
```

`.env.example` 과 문서(`API.md` `BUGFIXES.md` …)도 pkg 것이 배포됩니다.
예전에 트리마다 따로 관리하다 서로 다른 키가 빠져 있었습니다(BUGFIXES G-8).

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
② 캐시 데코레이터 검사
   OK — 없음
③ 내부 모듈 실제 import (.venv/bin/python)
   OK — 46개 모듈 모두 정상
통과
```

②는 `X.cache_clear()` 를 부르는데 X 에 `@lru_cache` 가 없는 경우를 찾습니다.

회귀 테스트도 함께 돌리세요.

```bash
pytest tests/ -q          # 12건, 무거운 의존성 없이 도는 것만
```
함수를 삽입하다 **데코레이터와 함수 사이에 끼워 넣으면** 데코레이터가
엉뚱한 함수에 붙는데, 구문 오류가 나지 않아 실행 시점에야 드러납니다.

## 빌드

```bash
pip install build
python -m build --wheel        # dist/docstruct-0.1.46-py3-none-any.whl
python -m build                # sdist 도 함께
```

## 배포 경로 선택

| 방법 | 명령 | 적합한 경우 |
|------|------|-------------|
| wheel 직접 전달 | `pip install ./docstruct-0.1.46-py3-none-any.whl` | 소수 인원, 폐쇄망 |
| git 저장소 | `pip install git+ssh://git@183.96.152.133/mjseo/docstruct.git@v0.1.46` | 사내 git 이 있을 때 |
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
