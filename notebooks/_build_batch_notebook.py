"""배치 처리 → 개별 분석 노트북을 만든다.

역할:
    폴더 하나를 DocStructBatch 로 돌리고, 그 결과 중 하나를 골라
    파고드는 흐름을 노트북으로 만든다.
호출부:
    python notebooks/_build_batch_notebook.py
출력:
    notebooks/batch_review.ipynb
"""
from __future__ import annotations

from pathlib import Path

import nbformat

nb = nbformat.v4.new_notebook()
cells: list = []


def md(text: str) -> None:
    """마크다운 셀을 추가한다."""
    cells.append(nbformat.v4.new_markdown_cell(text.strip()))


def code(text: str) -> None:
    """코드 셀을 추가한다."""
    cells.append(nbformat.v4.new_code_cell(text.strip()))


# ────────────────────────────────────────────────────────── 0
md("""
# 폴더 일괄 처리 → 개별 분석

폴더 하나를 통째로 돌린 뒤, 결과 중 **문제가 있어 보이는 문서 하나**를 골라
왜 그런지 파고드는 흐름입니다.

| 단계 | 하는 일 |
|------|---------|
| 1~2 | 준비 · 설정 |
| 3 | 폴더 일괄 처리 |
| 4 | 전체 결과 훑기 — 실패·이상 징후 찾기 |
| 5 | 하나 골라 파고들기 |
| 6 | 저장 |

`DocStructBatch` 결과에도 처리 경로(`trace`)가 그대로 있습니다.
`DocStruct.from_document()` 로 되돌리면 문서 하나처럼 다룰 수 있습니다.
""")

# ────────────────────────────────────────────────────────── 1
md("""
## 1. 준비

설치본이면 그대로, 압축본이면 폴더 경로를 잡아 줍니다.
""")

code('''
import sys
from pathlib import Path

# 압축본을 풀어 쓰는 경우에만 필요합니다 (pip 설치본이면 건너뜁니다).
for _cand in (Path.cwd(), Path.cwd().parent):
    if (_cand / "docstruct").is_dir():
        sys.path.insert(0, str(_cand))
        break

import docstruct
from docstruct import DocStruct, DocStructBatch
from docstruct import preview          # 속성 대신 명시적 import

print("docstruct :", getattr(docstruct, "__file__", "?"))

# 이 노트북은 아래 버전 이상이 필요합니다 (DocStruct.from_document).
REQUIRED = (0, 1, 23)
UPGRADE = (
    "pip install -U --force-reinstall --no-cache-dir "
    + chr(34) + "docstruct @ git+http://183.96.152.133/mjseo/docstruct.git@v0.1.27" + chr(34)
)

def _installed_version():
    """설치본 버전을 (major, minor, patch) 로. 압축본이면 None."""
    try:
        from importlib.metadata import version
        return tuple(int(x) for x in version("docstruct").split(".") if x.isdigit())
    except Exception:
        return None

_v = _installed_version()
if _v is None:
    _f = Path(sys.path[0] or ".") / "VERSION"
    print("버전      :", _f.read_text(encoding="utf-8").strip() if _f.is_file() else "(압축본)")
else:
    print("버전      :", ".".join(map(str, _v)))
    if _v < REQUIRED:
        print("  이 노트북은", ".".join(map(str, REQUIRED)), "이상이 필요합니다.")
        print("  업그레이드:", UPGRADE)

from docstruct.checks import show_environment
show_environment()
''')

# ────────────────────────────────────────────────────────── 2
md("""
## 2. 설정 (선택)

LLM 을 쓰지 않아도 파싱·표 추출은 그대로 됩니다.
표 품질 판정·재추출을 쓰려면 엔드포인트를 지정하세요.

키를 셀에 직접 적으면 저장 시 `.ipynb` 파일에 남습니다.
`getpass` 로 입력받으세요.
""")

code('''
# 필요할 때만 주석을 푸세요.

# docstruct.configure(
#     llm_url="http://내부주소:11060/v1",
#     llm_model="/model/모델이름",
#     llm_concurrency=8,
# )

# import getpass
# docstruct.set_api_key(getpass.getpass("OpenAI 키(대비책): "))

from docstruct.checks import show_llm_check
show_llm_check()
''')

# ────────────────────────────────────────────────────────── 3
md("""
## 3. 폴더 일괄 처리

`SRC_DIR` 만 바꾸면 됩니다. 일부 문서가 실패해도 나머지는 계속 진행합니다.
""")

code('''
SRC_DIR  = Path("문서모음")        # ← 처리할 폴더
PATTERN  = "*"                     # "*.pdf" 처럼 좁힐 수 있습니다
OUT_DIR  = Path("out") / SRC_DIR.name

ASSESS_TABLES = True               # 표 품질 판정 [LLM]
FILL_TABLES   = True               # 불량 표 재추출 [LLM]
RENDER_PAGES  = True               # 페이지 PNG 렌더 (PDF)

batch = DocStructBatch(
    SRC_DIR,
    pattern=PATTERN,
    assess_tables=ASSESS_TABLES,
    fill_tables=FILL_TABLES,
    render_pages=RENDER_PAGES,
    progress=True,                 # 진행 막대
)

print(f"대상 {len(batch)}건")
for p in batch.paths[:10]:
    print("  ", p.name)
if len(batch) > 10:
    print(f"   ... 외 {len(batch) - 10}건")
''')

code('''
import time

_t0 = time.perf_counter()
batch.run()                        # 실패해도 멈추지 않습니다
print(f"\\n완료 — {time.perf_counter() - _t0:.1f}초")
''')

# ────────────────────────────────────────────────────────── 4
md("""
## 4. 전체 결과 훑기

어느 문서를 파고들지 고르는 단계입니다.
""")

code('''
print("\\n".join(batch.summary()))

if batch.failures:
    print("\\n실패한 문서:")
    for path, exc in batch.failures:
        print(f"  {path.name}: {type(exc).__name__}: {str(exc).splitlines()[0][:70]}")
''')

md("""
### 문서별 한눈 보기

표가 많은데 판정이 `wrong`·`insufficient` 로 많이 나온 문서,
OCR 비율이 높은 문서, 파싱 실패 페이지가 있는 문서가 살펴볼 후보입니다.
""")

code('''
from IPython.display import HTML, display

rows = []
for i, doc in enumerate(batch.documents):
    tables = [t for p in doc.pages for t in p.tables]
    bad = sum(1 for t in tables if t.quality in ("wrong", "insufficient"))
    refilled = sum(1 for t in tables if t.was_filled)
    sources = {p.trace.text_source for p in doc.pages}
    took = sum(doc.timings.values())
    rows.append(
        f"<tr>"
        f"<td style='padding:3px 8px;'>{i}</td>"
        f"<td style='padding:3px 8px;'>{doc.filename}</td>"
        f"<td style='padding:3px 8px;text-align:right;'>{len(doc.pages)}</td>"
        f"<td style='padding:3px 8px;text-align:right;'>{len(tables)}</td>"
        f"<td style='padding:3px 8px;text-align:right;"
        f"{'color:#b45309;font-weight:600;' if bad else ''}'>{bad}</td>"
        f"<td style='padding:3px 8px;text-align:right;'>{refilled}</td>"
        f"<td style='padding:3px 8px;text-align:right;"
        f"{'color:#dc2626;' if doc.failed_pages else ''}'>"
        f"{len(doc.failed_pages)}</td>"
        f"<td style='padding:3px 8px;'>{', '.join(sorted(sources))}</td>"
        f"<td style='padding:3px 8px;text-align:right;'>{took:.1f}s</td>"
        f"</tr>"
    )

head = "".join(
    f"<th style='padding:4px 8px;text-align:left;font-size:11.5px;'>{h}</th>"
    for h in ("#", "파일", "페이지", "표", "불량", "재추출", "실패p", "텍스트 출처", "소요")
)
display(HTML(
    f"<table style='border-collapse:collapse;font-size:12px;'>"
    f"<tr style='background:#f1f5f9;'>{head}</tr>{''.join(rows)}</table>"
))
''')

# ────────────────────────────────────────────────────────── 5
md("""
## 5. 하나 골라 파고들기

위 표의 `#` 번호를 `PICK` 에 넣으세요.
`from_document()` 로 되돌리면 문서 하나를 다루듯 쓸 수 있습니다.
""")

code('''
PICK = 0                           # ← 위 표의 # 번호

doc = batch.documents[PICK]
ds  = DocStruct.from_document(doc)

print(f"{doc.filename} · {len(doc.pages)}페이지 · 표 {len(ds.tables)}개")
print("\\n".join(ds.summary()))
''')

md("""
### 5-1. 처리 경로

이 문서가 어떤 단계를 거쳤는지, 어디서 경고가 났는지 봅니다.
`!` 는 경고, `–` 는 생략, `✕` 는 실패입니다.
""")

code('''
preview.show_pipeline(ds.document)
''')

code('''
# 특정 페이지의 순차 실행 로그
PAGE = 0

page = ds.pages[PAGE]
print(page.trace.summary())
print()
print(page.trace.log())
''')

md("""
### 5-2. 단계별 소요 시간

대부분의 시간은 원격 LLM 대기입니다.
`GPU 영향` 이 `O` 인 구간만 GPU 로 줄어듭니다.
""")

code('''
total = sum(doc.timings.values())
for label, sec in sorted(doc.timings.items(), key=lambda kv: -kv[1]):
    share = sec / total * 100 if total else 0
    print(f"  {label:44} {sec:6.1f}초  {share:3.0f}%")
''')

md("""
### 5-3. 표 판정 결과

`wrong`·`insufficient` 로 판정된 표가 재추출 대상입니다.
재추출된 표는 전/후를 나란히 보여줍니다.
""")

code('''
preview.show_tables(ds.document)
''')

md("""
### 5-4. 레이아웃 인식 (PDF)

표가 이상할 때 **레이아웃 모델 오인식인지 변환 문제인지** 가릅니다.

| 관찰 | 원인 |
|------|------|
| 실제로는 표인데 라벨이 `그림`/`본문` | 레이아웃 모델 오인식 |
| 라벨은 `표` 인데 내용이 깨짐 | 표 구조 복원 또는 변환 문제 |
| 처리가 `버려짐` | 영역은 잡았으나 텍스트 추출 실패 |
""")

code('''
preview.show_layout(ds.document)
''')

md("""
### 5-5. 본문

페이지 이미지와 본문을 나란히 봅니다 (PDF 는 렌더된 PNG 가 함께 나옵니다).
""")

code('''
preview.show_page(ds.pages[PAGE], show_image=True, image_width=760)
''')

# ────────────────────────────────────────────────────────── 6
md("""
## 6. 저장

전체를 저장하거나, 파고든 문서 하나만 저장할 수 있습니다.
""")

code('''
# ① 전체 — 문서마다 폴더 하나 (json + md 4종)
written = batch.save(OUT_DIR)
print(f"{len(written)}건 저장 → {OUT_DIR}")

# ② 전체를 JSON 하나로
combined = batch.to_json(OUT_DIR / "전체.json", combined=True)
print("통합 JSON:", combined)

# ③ 파고든 문서만
one = ds.save(OUT_DIR / "_검토" / Path(doc.filename).stem)
print("개별 저장:", {k: v.name for k, v in one.items()})
''')

md("""
### JSON 을 파일 없이 바로 쓰기

```python
data = batch.to_dict()          # dict
text = batch.to_json_str()      # str
text = ds.to_json_str()         # 문서 하나만
```

### 다음 단계

- 표가 계속 깨지면 `--fill-all` 상당인 `fill_all=True` 로 전체 재추출
- 스캔 문서면 `render_scale` 을 3.0 으로 올려 판정 정확도 개선
- 처리가 느리면 `llm_concurrency` 를 올리세요 (I/O 대기라 CPU 코어와 무관)

```python
batch.set(fill_all=True, render_scale=3.0, llm_concurrency=8).run()
```
""")

nb.cells = cells
nb.metadata.kernelspec = {
    "display_name": "Python 3",
    "language": "python",
    "name": "python3",
}
nb.metadata.language_info = {"name": "python", "version": "3"}

out = Path(__file__).resolve().parent / "batch_review.ipynb"
nbformat.write(nb, out)
print(f"생성: {out}  (셀 {len(cells)}개)")
