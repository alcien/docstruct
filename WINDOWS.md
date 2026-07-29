# Windows 실행 안내

PowerShell 기준입니다. Python 3.10~3.12 권장 (3.13은 일부 의존성 wheel 미비 가능).

## 1. 가상환경

```powershell
cd app
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

> `Activate.ps1` 실행이 막히면:
> `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`

## 2. 설치

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

PDF가 필요 없으면 `requirements.txt` 에서 docling / pypdfium2 / rapidocr 줄을 빼면
설치가 훨씬 빠릅니다 (docling이 torch를 끌어옵니다).

## 3. 설치 확인

```powershell
python -c "import shutil; print(shutil.which('hwp5html'))"
```

경로가 나와야 합니다. `None` 이면 **HWP 표 구조가 통째로 유실되는 olefile 폴백**으로
떨어집니다. 가상환경을 활성화한 상태인지, `<venv>\Scripts` 가 PATH 에 있는지 확인하세요.

## 4. 실행

```powershell
$env:PYTHONPATH = "."
python -m docstruct.cli 문서.pdf --no-llm
```

`cmd.exe` 라면 `set PYTHONPATH=.` 입니다.

## 5. 노트북

```powershell
pip install jupyterlab ipywidgets
jupyter lab notebooks\preview.ipynb
```

노트북은 `APP_ROOT` 를 스스로 찾으므로 `PYTHONPATH` 설정이 필요 없습니다.

---

## Windows 특이사항

### ⚠ `UnicodeDecodeError: 'cp949' codec can't decode byte 0xe2`

**가장 먼저 겪게 되는 문제입니다.** PDF 파싱 시작과 동시에 터집니다.

원인은 우리 코드가 아니라 PyTorch 입니다. `torch/_inductor/utils.py::load_template()`
이 UTF-8 커널 템플릿(`*.py.jinja`)을 `encoding=` 없이 열어서, 한국어 Windows
기본 코덱인 cp949 로 읽다 실패합니다. Docling 은 `do_code_enrichment` 가
**False 여도** `CodeFormulaVlmModel` 을 생성하며 `torch.compile()` 을 부르기 때문에
이 경로를 피할 수 없습니다.

**런타임 우회 (자동 적용됨)**

```python
from docstruct import winfix
winfix.apply()     # 노트북 1번 셀에 이미 들어 있습니다
```

`TORCHDYNAMO_DISABLE=1` 로 `torch.compile()` 을 무력화합니다. torch 가 이 값을
호출 시점에 읽으므로 이미 켜진 커널에서도 먹습니다. 파싱 결과는 동일하고,
추론 속도에만 영향이 있습니다.

**영구 해결 — 인터프리터를 UTF-8 모드로**

`PYTHONUTF8` 은 프로세스 시작 전에 설정해야 하므로 **재시작이 필요합니다.**

```powershell
# 이 세션만
$env:PYTHONUTF8 = "1"
jupyter lab

# 사용자 계정에 영구 적용 (이후 터미널·Anaconda 재시작)
[Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "User")
```

또는 시스템 전체: 제어판 → 국가 또는 지역 → 관리자 옵션 → 시스템 로캘 변경 →
**'Beta: 세계 언어 지원을 위해 Unicode UTF-8 사용'** 체크 (재부팅).

현재 상태는 `winfix.diagnose()` 로 확인합니다.


**`hwp5html` 을 못 찾음**
pyhwp 는 `<venv>\Scripts\hwp5html.exe` 로 설치됩니다. 가상환경 밖에서 Jupyter 를 띄우면
찾지 못합니다. `converters/hwp/pyhwp.py` 가 `shutil.which()` → `python -m hwp5.hwp5html`
순으로 우회를 시도하지만, 가상환경을 활성화한 셸에서 실행하는 것이 가장 확실합니다.

**콘솔 창이 깜빡임**
HWP 변환 시 자식 프로세스가 뜨는 현상입니다. `CREATE_NO_WINDOW` 플래그로 억제했습니다.

**한글 출력이 깨짐 (`cmd.exe`)**
Python 은 콘솔에 유니코드 API 를 쓰므로 화면 출력은 정상입니다. 다만 **리다이렉션**
(`> out.txt`) 시에는 cp949 로 저장되어 깨질 수 있습니다:

```powershell
$env:PYTHONIOENCODING = "utf-8"
```

산출물 파일(`document.md` 등)은 항상 UTF-8 로 기록하므로 영향 없습니다.

**경로 길이 260자 제한**
한글 파일명이 긴 문서를 깊은 경로에서 처리하면 걸릴 수 있습니다.
`-o` 로 짧은 출력 경로를 주거나, 레지스트리에서 긴 경로를 활성화하세요.

**pyhwp 설치 실패**
sdist 만 제공되어 소스 빌드가 필요합니다. 실패하면 Visual C++ Build Tools 를 설치하거나,
HWPX 로 변환해 처리하세요 (`python-hwpx` 는 순수 Python 이라 문제 없습니다).

**tesseract 백엔드**
기본값은 `rapidocr` 이며 pip 만으로 설치됩니다. tesseract 를 쓰려면
<https://github.com/UB-Mannheim/tesseract/wiki> 에서 설치 후 PATH 등록하고,
한국어 데이터(`kor.traineddata`)를 포함시켜야 합니다.

## 대안

| 방법 | LLM 단계 | 비고 |
|--|--|--|
| **Windows 네이티브** | 사내망이면 가능 | 위 안내대로 |
| **WSL2 (Ubuntu)** | 사내망이면 가능 | Linux 환경이라 의존성 문제 가장 적음 |
| **Colab** | **대개 불가** (방화벽) | `notebooks/preview_colab.ipynb` — 파싱 결과만 확인 |
