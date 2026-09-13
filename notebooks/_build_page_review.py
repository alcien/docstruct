"""page_review.ipynb 를 만든다 — 쪽별로 원본과 판독 결과를 견준다.

역할:
    판독 결과(document.json)와 정답(HWPX)을 쪽 단위로 나란히 보여 주는
    노트북을 생성한다. 중간 검증용 — 수치가 아니라 **무엇이 어떻게
    바뀌었는지**를 눈으로 본다.
호출부:
    python notebooks/_build_page_review.py
출력:
    notebooks/page_review.ipynb
"""
from __future__ import annotations

from pathlib import Path

import nbformat

nb = nbformat.v4.new_notebook()
cells: list = []


def md(text: str) -> None:
    """마크다운 셀."""
    cells.append(nbformat.v4.new_markdown_cell(text.strip("\n")))


def code(text: str) -> None:
    """코드 셀."""
    cells.append(nbformat.v4.new_code_cell(text.strip("\n")))


md("""
# 쪽별 판독 검증

판독 결과가 원본을 **어떻게 바꿨는지** 쪽 단위로 본다. 수치 채점이 아니라
중간 확인용이다.

| 셀 | 내용 |
|----|------|
| 1 | 설정 — 결과 폴더·정답 HWPX·PDF 경로 |
| 2 | 불러오기와 정렬 (정답 표를 인식 표에 차례로 맞춘다) |
| 3 | 전체 요약 — 어느 쪽에 무엇이 어긋났나 |
| 4 | **쪽 하나 펼쳐 보기** — 지면 이미지 · 인식 표 · 정답 표 · 병합 차이 |
| 5 | 실험이 손댄 표만 훑기 (⑦⑫⑬⑭) |

## HWPX 에 쪽이 없다는 문제

HWPX 는 쪽을 저장하지 않는다. `lineseg vertpos`(쪽 안 세로 위치)의
되감김을 세면 짐작은 되지만 **쪽을 넘는 표**에서 되감기지 않아 어긋난다
— 실측: 조달청 68쪽(실제 76) · 행안부 326쪽(실제 429). 행안부 별첨3
처럼 한 표가 21쪽에 걸치는 문서에서 특히 크게 벌어진다.

그래서 쪽은 **PDF 에서 가져오고**, 정답 표를 순서 정렬로 거기에 붙인다.
표마다 가장 닮은 정답을 따로 고르는 방식은 성과계획서에서 무너진다 —
서식이 같은 표가 수십 개라 실측(행안부)에서 1열짜리 표에 12열 표가
붙었다. 두 목록이 같은 차례라는 제약을 쓰면 자리로 갈린다.

한 가지 더 — **쪽을 넘는 표는 인식에서 조각난다.** TableFormer 가 쪽마다
하나씩 내기 때문이다(행안부 32표). 그대로 두면 앞 조각이 정답을 가져가고
뒤 조각이 빈자리가 되므로, 파이프라인이 남긴 `continues_from` 을 보고
이어지는 조각은 **같은 정답에 함께 붙인다.**

정렬이 짝을 못 지으면 "정답 없음"으로 남긴다 — 억지로 붙여 엉뚱한 표를
나란히 보여 주는 것보다 낫다. 실측(행안부 317 대 580): 짝 230 · 닮음
중앙값 0.78.
""")

# ---------------------------------------------------------------- 1. 설정
md("""
## 1. 설정

아래 네 줄만 고치면 된다.
""")

code('''
from pathlib import Path

# ── CONFIG ──────────────────────────────────────────────────────────
#: 판독 결과 폴더 (document.json 이 들어 있는 곳)
OUT_DIR   = Path("out_base/행정안전부")
#: 정답 HWPX. 없으면 None — 인식 결과만 본다
HWPX_PATH = Path("행정안전부.hwpx")
#: 원본 PDF. 지면 이미지를 그리는 데 쓴다 (OUT_DIR/pages 가 있으면 없어도 됨)
PDF_PATH  = Path("행정안전부.pdf")
#: gt_align.py 가 있는 곳 (시험 꾸러미)
KIT_DIR   = Path(".")
# ────────────────────────────────────────────────────────────────────

import sys
if str(KIT_DIR) not in sys.path:
    sys.path.insert(0, str(KIT_DIR))
print("결과:", OUT_DIR, "·", "있음" if (OUT_DIR / "document.json").is_file() else "없음 ←")
print("정답:", HWPX_PATH, "·", "있음" if HWPX_PATH and HWPX_PATH.is_file() else "없음 (인식만 봅니다)")
''')

# ---------------------------------------------------------------- 2. 정렬
md("""
## 2. 불러오기와 정렬

정답 표가 인식 표에 붙으면서 **PDF 쪽 번호를 얻는다** — HWPX 에 없는 쪽
구분이 이렇게 생긴다. 붙지 못한 것은 두 종류로 남는다.

    정답 없음   인식은 표라고 했는데 정답에 짝이 없다 (표지·간지 등)
    인식 못함   정답에 있는데 인식이 통째로 놓쳤다
""")

code('''
import json
import gt_align as A

doc = json.loads((OUT_DIR / "document.json").read_text(encoding="utf-8"))
detected = [(pg["page_no"], t) for pg in doc["pages"] for t in (pg.get("tables") or [])]

gt = []
if HWPX_PATH and HWPX_PATH.is_file():
    from docstruct.converters.hwpx.hwpxtree import table_grids
    gt = [g for g in table_grids(HWPX_PATH) if g]

pairs = A.align(detected, gt) if gt else [
    A.Pair(p, t.get("id"), None, 0.0, t, None) for p, t in detected]
by_page = A.pages(pairs)

matched = [p for p in pairs if p.kind == "짝"]
print(f"인식 표 {len(detected)} · 정답 표 {len(gt)} · 쪽 {len(doc['pages'])}")
if gt:
    sims = sorted(p.similarity for p in matched)
    print(f"짝 {len(matched)} · 정답없음 {sum(1 for p in pairs if p.kind=='정답 없음')}"
          f" · 인식못함 {sum(1 for p in pairs if p.kind=='인식 못함')}")
    if sims:
        print(f"닮음 중앙값 {sims[len(sims)//2]:.2f} (낮으면 정렬을 의심한다)")
''')

# ---------------------------------------------------------------- 3. 요약
md("""
## 3. 전체 요약 — 어디를 봐야 하나

쪽마다 병합이 얼마나 맞았는지 낸다. **낮은 쪽부터** 보면 된다.

> 여기 수치는 자리 그대로 비교한 것이다. 정답의 잉여 열 접기와 행
> 오프셋 보정(`kit/truth_align.py`)을 하지 않으므로 **최종 채점보다 낮게
> 나온다.** 순위를 보는 용도다.
""")

code('''
rows = []
for page_no in sorted(by_page):
    hit = want = got = 0
    for pair in by_page[page_no]:
        h, w, g = A.score_merges(pair)
        hit += h; want += w; got += g
    if want or got:
        rows.append((page_no, hit, want, got, hit / want if want else 0.0))

print(f"{'쪽':>5}{'맞힘':>6}{'정답':>6}{'인식':>6}{'재현':>8}")
for page_no, hit, want, got, rate in sorted(rows, key=lambda r: r[4])[:25]:
    print(f"{page_no:5}{hit:6}{want:6}{got:6}{rate:8.0%}")
print(f"\\n... 전체 {len(rows)}쪽 중 낮은 25쪽. 특정 쪽을 보려면 아래 PAGE 를 바꾼다.")
''')

# ---------------------------------------------------------------- 4. 한 쪽
md("""
## 4. 쪽 하나 펼쳐 보기

`PAGE` 를 바꿔 가며 돌린다. 지면 이미지가 있으면 함께 띄운다.
""")

code('''
PAGE = 63          # ← 여기만 바꾼다

from IPython.display import display, HTML, Image

# ── 지면 이미지 ──────────────────────────────────────────────────────
# document.json 의 page_image_path 는 **판독을 돌린 기계의 절대경로**다
# (예: C:/Users/.../out_col/... 의 윈도우 형태). 다른 기계에서 열면 없는
# 파일 이름만 떼어 OUT_DIR/pages 에서 찾고, 그래도 없으면 PDF 에서 그린다.
page = next((p for p in doc["pages"] if p.get("page_no") == PAGE), None)

shot = None
if page and page.get("page_image_path"):
    name = page["page_image_path"].replace(chr(92), "/").rsplit("/", 1)[-1]
    if (OUT_DIR / "pages" / name).is_file():
        shot = OUT_DIR / "pages" / name
if shot is None and (OUT_DIR / "pages").is_dir():
    found = sorted((OUT_DIR / "pages").glob(f"*_{PAGE}.png"))
    shot = found[0] if found else None
if shot is not None:
    display(Image(filename=str(shot), width=760))
elif PDF_PATH and PDF_PATH.is_file():
    try:
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(str(PDF_PATH))
        pil = pdf[PAGE - 1].render(scale=2.0).to_pil()
        display(pil.resize((760, int(pil.height * 760 / pil.width))))
        pdf.close()
    except Exception as exc:
        print("지면 이미지를 그리지 못했습니다:", exc)
else:
    print("지면 이미지가 없습니다 — OUT_DIR/pages 나 PDF_PATH 를 확인하세요.")

# ── 처리 경로 · 레이아웃 (preview.ipynb 와 같은 정보) ────────────────
trace = (page or {}).get("trace") or {}
steps = trace.get("steps") or []
if steps:
    rows = "".join(
        f"<tr><td style='color:#94a3b8;padding-right:6px'>{i + 1}</td>"
        f"<td style='padding-right:10px'><code>{s.get('module','')}</code></td>"
        f"<td style='padding-right:10px'>{s.get('action','')}</td>"
        f"<td style='color:#64748b'>{s.get('detail','')}</td></tr>"
        for i, s in enumerate(steps))
    head = (f"추출기 {trace.get('extractor','?')} · 표 {trace.get('table_count',0)}"
            f" · 셀 {trace.get('cell_count',0)}"
            + (" · 재추출됨" if trace.get("refilled") else ""))
    display(HTML("<details open><summary style='cursor:pointer;font-size:12px;color:#475569'>"
                 f"처리 경로 — {head}</summary>"
                 f"<table style='font-size:11px;margin:6px 0'>{rows}</table></details>"))
    for note in trace.get("notes") or []:
        display(HTML(f"<div style='color:#b45309;font-size:12px'>&#9888; {note}</div>"))

layout = (page or {}).get("layout") or []
if layout:
    rows = "".join(
        f"<tr><td style='padding-right:10px'>{a.get('label','')}</td>"
        f"<td style='padding-right:10px;color:#64748b'>{a.get('outcome','')}</td>"
        f"<td style='padding-right:10px'>{a.get('ref') or ''}</td>"
        f"<td style='color:#94a3b8'>{a.get('char_count',0)}자</td></tr>"
        for a in layout)
    display(HTML("<details><summary style='cursor:pointer;font-size:12px;color:#475569'>"
                 f"레이아웃 인식 — 영역 {len(layout)}개</summary>"
                 f"<table style='font-size:11px;margin:6px 0'>{rows}</table></details>"))

def table_html(cells, title):
    """셀 목록을 표로 그린다 (병합 그대로)."""
    if not cells:
        return f"<p><b>{title}</b> — 없음</p>"
    rows, cols = A.size_of(cells)
    grid = [[None] * cols for _ in range(rows)]
    for c in cells:
        r0, c0 = c.get("row", 0), c.get("col", 0)
        if 0 <= r0 < rows and 0 <= c0 < cols:
            grid[r0][c0] = c
    covered = set()
    out = [f"<p><b>{title}</b> · {rows}행 × {cols}열</p>",
           "<table style='border-collapse:collapse;font-size:11px'>"]
    for r in range(rows):
        out.append("<tr>")
        for c in range(cols):
            if (r, c) in covered:
                continue
            cell = grid[r][c]
            if cell is None:
                out.append("<td style='border:1px solid #ddd'>&nbsp;</td>")
                continue
            rs = cell.get("rowspan", 1) or 1
            cs = cell.get("colspan", 1) or 1
            for rr in range(r, r + rs):
                for cc in range(c, c + cs):
                    if (rr, cc) != (r, c):
                        covered.add((rr, cc))
            bg = "#eef6ff" if (rs > 1 or cs > 1) else "#fff"
            text = (cell.get("text") or "").strip()[:40] or "&nbsp;"
            out.append(f"<td rowspan={rs} colspan={cs} "
                       f"style='border:1px solid #999;background:{bg};padding:2px'>{text}</td>")
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)

for pair in by_page.get(PAGE, []):
    hit, want, got = A.score_merges(pair)
    head = (f"<h4>{pair.detected_id or '—'} · {pair.kind}"
            + (f" · 닮음 {pair.similarity:.2f}" if pair.similarity else "")
            + f" · 병합 {hit}/{want} 맞힘 (인식이 낸 것 {got})</h4>")
    marks = []
    for key, label in (("source", "경로"), ("col_grid", "⑬"), ("head_grid", "⑫"),
                       ("agreed_grid", "⑭"), ("grid_score", "⑪")):
        value = (pair.detected or {}).get(key)
        if value:
            marks.append(f"{label}={value if key == 'source' else '적용'}")
    if marks:
        head += "<p style='color:#666;font-size:12px'>" + " · ".join(marks) + "</p>"
    left = table_html((pair.detected or {}).get("cells"), "인식")
    right = table_html(pair.gt, "정답(HWPX)")
    display(HTML(head + "<div style='display:flex;gap:16px;align-items:flex-start'>"
                 + f"<div>{left}</div><div>{right}</div></div><hr>"))
if not by_page.get(PAGE):
    print(f"{PAGE}쪽에는 표가 없습니다.")
''')

# ---------------------------------------------------------------- 5. 실험
md("""
## 5. 실험이 손댄 표만 훑기

⑦·⑫·⑬·⑭ 가 발화한 표를 모아 본다. **무엇이 바뀌었는지**를 확인할
자리다 — 실험을 승격할지 판정하는 근거가 여기서 나온다.
""")

code('''
WHICH = "col_grid"      # col_grid(⑬) · head_grid(⑫) · agreed_grid(⑭) · grid(⑦)

hits = []
for pair in pairs:
    t = pair.detected or {}
    if WHICH == "grid":
        if t.get("source") == "grid":
            hits.append(pair)
    elif t.get(WHICH):
        hits.append(pair)

print(f"{WHICH} 발화 {len(hits)}표")
print(f"{'표':14}{'쪽':>5}{'닮음':>7}{'맞힘':>6}{'정답':>6}  기록")
for pair in hits:
    hit, want, got = A.score_merges(pair)
    note = (pair.detected or {}).get(WHICH)
    print(f"{pair.detected_id or '-':14}{pair.page_no or 0:5}{pair.similarity:7.2f}"
          f"{hit:6}{want:6}  {note if note is not True else ''}")
print("\\n한 표를 자세히 보려면 위 4번 셀의 PAGE 를 그 쪽으로 바꾼다.")
''')

nb["cells"] = cells
nb["metadata"] = {
    "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
    "language_info": {"name": "python"},
}

out = Path(__file__).resolve().parent / "page_review.ipynb"
out.write_text(nbformat.writes(nb), encoding="utf-8")
print(f"만들었습니다: {out} ({len(cells)}셀)")
