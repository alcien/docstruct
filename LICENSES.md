# 의존성 라이선스 조사 (법무 검토 요청 자료)

**작성 목적**: docstruct 가 사용하는 외부 패키지의 라이선스를 확인하고,
계약·배포 조건과 충돌 가능성이 있는 항목을 법무 검토에 넘긴다.

**조사 기준일**: 2026-08-07 · **대상 버전**: docstruct 0.1.78

> 이 문서는 **사실 확인 자료**이며 법률 자문이 아니다. 결론(사용 가능 여부,
> 대응 방안 선택)은 법무 검토를 거쳐야 한다.

---

## 1. 요약 — 검토가 필요한 항목

| 패키지 | 라이선스 | 판정 |
|---|---|---|
| **pyhwp** | **AGPL-3.0-or-later** | **검토 필요** |
| 그 외 전부 | MIT / Apache-2.0 / BSD / MPL-2.0 | 통상 문제 없음 |

검토가 필요한 것은 **pyhwp 하나**다. 나머지는 모두 허용적(permissive)
라이선스다.

---

## 2. 직접 의존성 전체

`pyproject.toml` 의 `dependencies` 및 `optional-dependencies` 기준.

### 필수 의존성

| 패키지 | 버전 | 라이선스 | 성격 |
|---|---|---|---|
| pyhwp | 0.1b15 | **AGPL-3.0-or-later** | **강한 카피레프트** |
| python-hwpx | 6.0.2 | Apache-2.0 | 허용적 |
| olefile | 0.47 | BSD | 허용적 |
| six | 1.17.0 | MIT | 허용적 |
| requests | 2.33.1 | Apache-2.0 | 허용적 |
| python-dotenv | 1.2.2 | BSD-3-Clause | 허용적 |
| tqdm | 4.70.0 | MPL-2.0 AND MIT | 약한 카피레프트 (파일 단위) |
| beautifulsoup4 | 4.14.3 | MIT | 허용적 |
| docling | 2.26+ | MIT | 허용적 |
| docling-slim | 2.26+ | MIT | 허용적 |
| docling-core | 2.0+ | MIT | 허용적 |
| pypdfium2 | 5.6.0 | BSD-3-Clause, Apache-2.0 | 허용적 |
| pillow | 12.1.1 | MIT-CMU | 허용적 |
| rapidocr | 3.9.1+ | Apache-2.0 | 허용적 |
| onnxruntime | 1.24.4 | MIT | 허용적 |

### 선택 의존성

| 그룹 | 패키지 | 라이선스 |
|---|---|---|
| notebook | ipywidgets, ipython | BSD-3-Clause |
| dev | pytest, nbclient, nbformat, build | MIT / BSD-3-Clause |

### tqdm 에 대한 참고

`MPL-2.0 AND MIT` 이다. MPL-2.0 은 **파일 단위** 카피레프트로, 해당
파일을 수정해 배포할 때만 그 파일의 소스 공개 의무가 생긴다. 우리는
tqdm 을 수정 없이 import 만 하므로 통상 문제되지 않는다.

### docling 모델 가중치에 대한 참고

docling **코드**는 MIT 이지만, 공식 안내는 "개별 모델 사용은 원 패키지의
모델 라이선스를 따르라" 고 명시한다. 레이아웃 분석(DocLayNet)·표 구조
인식(TableFormer) 모델 가중치는 코드와 별도로 확인이 필요하다.
현재 조사 범위 밖이며, 실제 배포 시 별도 확인을 권한다.

---

## 3. pyhwp 상세

### 확인 근거

- `pyhwp-0.1b15.dist-info/LICENSE` → GNU AFFERO GENERAL PUBLIC LICENSE v3
- 패키지 메타데이터 `License:` → `GNU Affero General Public License v3 or later (AGPLv3+)`
- 모든 소스 파일 헤더에 AGPL 고지 (`hwp5/__init__.py` 등)
- 저작권자: mete0r <mete0r@sarangbang.or.kr>, 2010-2015

### 우리 코드의 사용 방식

**(가) 같은 프로세스 내 직접 import — 결합도 높음**

    converters/hwp/hwp5tree.py:97   import hwp5.xmlmodel
    converters/hwp/hwp5tree.py:126  from hwp5.treeop import ENDEVENT
    converters/hwp/hwp5tree.py:127  from hwp5.xmlmodel import Hwp5File
    converters/deps.py:25           import hwp5.hwp5html

**(나) 서브프로세스 CLI 호출 — 결합도 낮음**

    converters/hwp/pyhwp.py:39   [sys.executable, "-m", "hwp5.hwp5html"]
    converters/hwp/pyhwp.py:88   subprocess.run(...)

`hwp5tree` 는 HWP 처리의 **기본 경로**다. 폴백이 아니라 주 경로이며,
표 구조 보존 품질이 다른 경로보다 크게 높다(원본 대조 시 셀 유실 0%).

### 우리 패키지의 라이선스 선언

    pyproject.toml:11   license = { text = "Proprietary" }

AGPL 패키지를 필수 의존성으로 두면서 Proprietary 로 선언하고 있다.
`LICENSE` 파일은 저장소에 없다.

### AGPL 13조 (네트워크 사용)

GPL 은 통상 **배포 시점**에 소스 공개 의무가 발생하지만, AGPL 13조는
**네트워크를 통해 서비스만 제공해도** 사용자에게 소스를 제공하도록
규정한다.

현재 backend 는 다음 엔드포인트로 서비스한다.

    POST /convert, /convert/markdown, /convert/text, ...
    POST /rag/parse, /rag/index, /rag/path_index
    POST /export_json

즉 AGPL 13조가 겨냥하는 형태에 해당할 수 있다.

---

## 4. 대응 선택지 (법무 판단 필요)

| # | 방안 | 장점 | 검토 지점 |
|---|---|---|---|
| ① | 해당 부분 AGPL 로 공개 | 명확한 준수 | 발주처 계약 조건과 충돌 가능 |
| ② | pyhwp 제거 (HWPX·olefile 만 사용) | 라이선스 부담 해소 | HWP 표 품질 크게 저하. HWP→HWPX 사전 변환 워크플로 필요 |
| ③ | 별도 프로세스·서비스로 분리 | 결합도 낮춤 | **13조 회피 수단으로 유효한지 불확실** |
| ④ | 저작권자에게 상용 라이선스 문의 | 현 구조 유지 | 응답·조건 불확실 (저작권자 mete0r) |

### ② 를 택할 경우의 기술적 영향

- HWP(.hwp) 바이너리 처리 경로가 `olefile` 폴백만 남는다. 표·그림
  구조가 복원되지 않고 텍스트만 나온다.
- HWPX(.hwpx)는 `python-hwpx`(Apache-2.0) 로 정상 처리된다.
- 실무상 HWP 를 HWPX 로 일괄 변환하는 전처리가 필요하다.

---

## 5. 재현 방법

    python3 -c "
    from importlib.metadata import metadata, version
    m = metadata('pyhwp')
    print(version('pyhwp'), m.get('License'))
    "

    # LICENSE 원문
    cat <site-packages>/pyhwp-*.dist-info/LICENSE | head -3
