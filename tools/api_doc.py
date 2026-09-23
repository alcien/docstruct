"""docstruct 함수 정의서(HTML)를 **살아 있는 코드에서** 만든다.

입력: 없음 (설치된 docstruct 를 import 해 읽는다)
출력: 한 파일짜리 HTML — `python tools/api_doc.py 출력경로.html`
비고:
    손으로 적은 정의서는 코드와 어긋난다(이 프로젝트에서 여러 번 겪었다).
    서명은 `inspect.signature`, 설명은 docstring 의 `입력`·`출력`·`예외`·
    `비고` 칸, 옵션 기본값은 `core/config.py`, CLI 는 argparse 객체에서 읽는다.
    판을 올릴 때마다 다시 돌리면 된다.
"""
from __future__ import annotations

import argparse
import dataclasses
import html
import inspect
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import docstruct  # noqa: E402
from docstruct import api as _api  # noqa: E402
from docstruct import models  # noqa: E402
from docstruct.align import documents as _align_docs  # noqa: E402
from docstruct.align import pair as _align_pair  # noqa: E402

VERSION = re.search(r'^version\s*=\s*"([^"]+)"',
                    (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)


# ─── docstring 읽기 ──────────────────────────────────────────────────────────
SECTION = re.compile(r"^(입력(?:\([^)]*\))?|출력(?:\([^)]*\))?|예외|비고|사용 예|반환)\s*:{1,2}\s*(.*)$")


def parse_doc(doc: str) -> dict:
    """`입력`·`출력`·`예외`·`비고` 칸으로 나눈다."""
    lines = (doc or "").splitlines()
    summary: list[str] = []
    i = 0
    while i < len(lines) and lines[i].strip() and not SECTION.match(lines[i]):
        summary.append(lines[i].strip())
        i += 1
    parts: dict[str, list[str]] = {}
    current = None
    for line in lines[i:]:
        match = SECTION.match(line) if not line.startswith((" ", "\t")) else None
        if match:
            label = match.group(1)
            key = ("in" if label.startswith("입력") else "out" if label.startswith(("출력", "반환"))
                   else "raise" if label == "예외" else "example" if label == "사용 예" else "note")
            current = key
            parts.setdefault(key, [])
            if match.group(2).strip():
                parts[key].append(match.group(2))
            continue
        if current is None:
            if line.strip():
                summary.append(line.strip())
            continue
        parts[current].append(line)
    out = {"summary": " ".join(summary)}
    for key, body in parts.items():
        text = "\n".join(body)
        out[key] = dedent(text).strip("\n")
    # 비고 안의 `예)` 를 예제로 떼어 낸다
    note = out.get("note", "")
    ex = re.search(r"(?m)^예\)\s*\n+((?:(?:[ \t]+.*)?\n?)+)", note)
    if ex and "example" not in out:
        out["example"] = dedent(ex.group(1)).strip("\n")
        out["note"] = (note[:ex.start()] + note[ex.end():]).strip("\n")
    return out


def dedent(text: str) -> str:
    rows = text.splitlines()
    pad = min((len(r) - len(r.lstrip()) for r in rows if r.strip()), default=0)
    return "\n".join(r[pad:] for r in rows)


def parse_params(block: str, known: set[str] | None = None) -> dict[str, str]:
    """`입력` 칸 → {이름: 설명}.

    서명에서 **실제 매개변수 이름**을 알면 그것으로 줄을 가른다 — 이름이
    길면 설명과 한 칸만 띄는 줄(`as_markdown True 면 …`)이 있고, 설명 안에
    `—` 가 들어간 줄도 있어 모양만으로는 가를 수 없다.
    """
    out: dict[str, str] = {}
    if not block:
        return out
    known = known or set()
    if "\n" not in block.strip() and "—" in block:
        for piece in re.split(r",\s*(?=[\w*]+\s*—)", block.strip()):
            match = re.match(r"\s*([\w*]+(?:\s*[,/·]\s*[\w*]+)*)\s*—\s*(.+)", piece)
            if match:
                for name in re.split(r"\s*[,/·]\s*", match.group(1)):
                    out[name.strip("* ")] = match.group(2).strip()
        return out
    last: list[str] | None = None
    for line in block.splitlines():
        if not line.strip():
            continue
        head = re.match(r"^([\w*]+(?:\s*[,/·]\s*[\w*]+)*)(\s+—\s+|\s+)(.+)$", line)
        names = []
        if head and not line.startswith((" ", "\t")):
            names = [n.strip("* ") for n in re.split(r"\s*[,/·]\s*", head.group(1))]
            by_name = bool(known) and all(n in known for n in names)
            by_shape = (not known) and (len(head.group(2)) >= 2 or "—" in head.group(2))
            if not (by_name or by_shape):
                names = []
        if names:
            for name in names:
                out[name] = head.group(3).strip()
            last = names
        elif last:
            # 이어지는 줄이 `값 — 뜻` 꼴이면 선택지 목록이다 — 줄을 살린다
            glue = "\n" if re.match(r"^\S+\s+—\s", line.strip()) else " "
            for name in last:
                out[name] += glue + line.strip()
    return out


# ─── 글 → HTML ──────────────────────────────────────────────────────────────
PRIVATE_URL = re.compile(r"https?://\d{1,3}(?:\.\d{1,3}){3}[^\s'\"]*")


def safe(text) -> str:
    """내부 주소는 가린다 — 이 문서는 밖으로 나갈 수 있다."""
    return PRIVATE_URL.sub("(설치 환경의 주소)", str(text))


def inline(text: str) -> str:
    text = html.escape(safe(text))
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    return text


def prose(text: str) -> str:
    """문단은 <p>, 더 들여 쓴 줄은 <pre>."""
    if not text:
        return ""
    out, para, code = [], [], []

    def flush_para():
        if para:
            out.append(f"<p>{inline(' '.join(s.strip() for s in para))}</p>")
            para.clear()

    def flush_code():
        if code:
            out.append("<pre class=\"code\">" + html.escape(safe(dedent("\n".join(code)))) + "</pre>")
            code.clear()

    for line in text.splitlines():
        if line.startswith(("    ", "\t")) and line.strip():
            flush_para()
            code.append(line)
        elif not line.strip():
            if code and code[-1] != "":
                code.append("")
            flush_para()
        else:
            flush_code()
            para.append(line)
    flush_para()
    flush_code()
    return "\n".join(out).replace("\n</pre>", "</pre>")


def code_block(text: str) -> str:
    return f"<pre class=\"code\">{html.escape(safe(text.strip()))}</pre>"


# ─── 항목 만들기 ─────────────────────────────────────────────────────────────
def slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "-", name).strip("-").lower()


def fmt_default(value) -> str:
    if value is inspect.Parameter.empty:
        return ""
    return safe(repr(value))


def signature_html(name: str, obj, *, drop_self: bool) -> tuple[str, list]:
    try:
        sig = inspect.signature(obj)
    except (TypeError, ValueError):
        return f"<span class=\"fn\">{html.escape(name)}</span>", []
    params = [p for p in sig.parameters.values() if not (drop_self and p.name in ("self", "cls"))]
    rows, pieces = [], []
    star_seen = False
    for p in params:
        ann = "" if p.annotation is inspect.Parameter.empty else str(p.annotation).strip("'\"")
        default = fmt_default(p.default)
        label = p.name
        if p.kind is p.VAR_KEYWORD:
            label = "**" + p.name
        elif p.kind is p.VAR_POSITIONAL:
            label = "*" + p.name
        elif p.kind is p.KEYWORD_ONLY and not star_seen:
            pieces.append("<span class=\"punct\">*</span>")
            star_seen = True
        piece = f"<span class=\"pn\">{html.escape(label)}</span>"
        if ann:
            piece += f"<span class=\"punct\">: </span><span class=\"ty\">{html.escape(ann)}</span>"
        if default:
            piece += f"<span class=\"punct\"> = </span><span class=\"dv\">{html.escape(default)}</span>"
        pieces.append(piece)
        rows.append({"name": p.name, "label": label, "type": ann, "default": default,
                     "required": p.default is inspect.Parameter.empty
                     and p.kind not in (p.VAR_KEYWORD, p.VAR_POSITIONAL)})
    ret = "" if sig.return_annotation is inspect.Signature.empty else str(sig.return_annotation).strip("'\"")
    head = f"<span class=\"fn\">{html.escape(name)}</span>"
    if len(pieces) > 2:
        inner = "(\n    " + ",\n    ".join(pieces) + "\n)"
    else:
        inner = "(" + ", ".join(pieces) + ")"
    tail = f"<span class=\"punct\"> → </span><span class=\"ty\">{html.escape(ret)}</span>" if ret else ""
    return head + inner + tail, rows


def entry(name: str, obj, *, kind: str, module: str = "", drop_self: bool = False,
          prop: bool = False, member_of: str = "") -> dict:
    doc = parse_doc(inspect.getdoc(obj) or "")
    if prop:
        sig_html, rows = f"<span class=\"fn\">{html.escape(name)}</span>", []
    else:
        sig_html, rows = signature_html(name.split(".")[-1] if member_of else name, obj,
                                        drop_self=drop_self)
    described = parse_params(doc.get("in", ""), {r["name"] for r in rows})
    for row in rows:
        row["desc"] = described.get(row["name"], "")
    return {"id": slug(name), "name": name, "kind": kind, "module": module,
            "sig": sig_html, "rows": rows, "doc": doc, "member_of": member_of}


def entry_html(e: dict) -> str:
    d = e["doc"]
    parts = [f"<article class=\"entry\" id=\"{e['id']}\" data-search=\"{html.escape((e['name'] + ' ' + d.get('summary', '')).lower())}\">"]
    parts.append(f"<header><h3><code>{html.escape(e['name'])}</code></h3>"
                 f"<span class=\"kind\">{e['kind']}</span>"
                 + (f"<span class=\"mod\">{html.escape(e['module'])}</span>" if e["module"] else "")
                 + "</header>")
    parts.append(f"<pre class=\"sig\">{e['sig']}</pre>")
    if d.get("summary"):
        parts.append(f"<p class=\"lead\">{inline(d['summary'])}</p>")
    if e["rows"]:
        body = "".join(
            f"<tr><td><code>{html.escape(r['label'])}</code>"
            + ("<span class=\"req\">필수</span>" if r["required"] else "")
            + f"</td><td>{html.escape(r['type']) or '—'}</td>"
            f"<td>{('<code>' + html.escape(r['default']) + '</code>') if r['default'] else '—'}</td>"
            f"<td>{inline(r['desc']).replace(chr(10), '<br>') if r['desc'] else '<span class=none>—</span>'}</td></tr>"
            for r in e["rows"])
        parts.append("<div class=\"scroll\"><table class=\"params sigp\"><thead><tr><th>매개변수</th>"
                     "<th>형</th><th>기본값</th><th>설명</th></tr></thead>"
                     f"<tbody>{body}</tbody></table></div>")
    elif d.get("in") and d["in"].strip() not in ("없음",) and not d["in"].startswith("없음"):
        parts.append(f"<div class=\"field\"><h4>입력</h4>{prose(d['in'])}</div>")
    if d.get("out"):
        parts.append(f"<div class=\"field\"><h4>반환</h4>{prose(d['out'])}</div>")
    if d.get("raise"):
        parts.append(f"<div class=\"field\"><h4>예외</h4>{prose(d['raise'])}</div>")
    if d.get("example"):
        parts.append(f"<div class=\"field\"><h4>예</h4>{code_block(d['example'])}</div>")
    if d.get("note"):
        parts.append(f"<details class=\"note\"><summary>설계 메모</summary>{prose(d['note'])}</details>")
    parts.append("</article>")
    return "\n".join(parts)


def members(cls, cls_name: str) -> list[dict]:
    out = []
    for k in dir(cls):
        if k.startswith("_"):
            continue
        raw = inspect.getattr_static(cls, k)
        is_prop = isinstance(raw, property)
        fn = raw.fget if is_prop else getattr(raw, "__func__", raw)
        if not (callable(fn) or is_prop):
            continue
        if dataclasses.is_dataclass(cls) and k in {f.name for f in dataclasses.fields(cls)}:
            continue
        out.append(entry(f"{cls_name}.{k}", fn, kind="속성" if is_prop else "메서드",
                         drop_self=True, prop=is_prop, member_of=cls_name))
    return out


# ─── 모델 필드 ───────────────────────────────────────────────────────────────
def field_notes(cls) -> dict[str, str]:
    """`#:` 주석과 끝 주석을 필드 설명으로."""
    src = inspect.getsource(cls).splitlines()
    notes, pending = {}, []
    for line in src:
        s = line.strip()
        if s.startswith("#:"):
            pending.append(s[2:].strip())
            continue
        match = re.match(r"^(\w+)\s*:\s*[^=#]+?(?:=\s*[^#]+?)?\s*(?:#\s*(.*))?$", s)
        if match and not s.startswith(("def ", "return", "class ")):
            text = " ".join(pending) or (match.group(2) or "")
            if text:
                notes[match.group(1)] = text
            pending = []
        elif not s.startswith("#"):
            pending = []
    return notes


def fields_html(cls) -> str:
    # 필드 옆 주석이 먼저, 없으면 클래스 docstring 의 `입력(필드)` 표
    names = {f.name for f in dataclasses.fields(cls)}
    notes = parse_params(parse_doc(inspect.getdoc(cls) or "").get("in", ""), names)
    notes.update(field_notes(cls))
    rows = []
    for f in dataclasses.fields(cls):
        if f.default is not dataclasses.MISSING:
            default = f"<code>{html.escape(safe(repr(f.default)))}</code>"
        elif f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            default = "빈 값"
        else:
            default = "<span class=\"req\">필수</span>"
        rows.append(f"<tr><td><code>{f.name}</code></td><td>{html.escape(str(f.type))}</td>"
                    f"<td>{default}</td><td>{inline(notes[f.name]) if notes.get(f.name) else '<span class=none>—</span>'}</td></tr>")
    return ("<div class=\"scroll\"><table class=\"params\"><thead><tr><th>필드</th><th>형</th>"
            "<th>기본값</th><th>설명</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>")


# ─── 설정 ────────────────────────────────────────────────────────────────────
OPTION_WORDS = {
    "assess_tables": "표를 LLM 으로 평가한다", "fill_tables": "평가에서 부족하다고 나온 표를 다시 추출한다",
    "fill_all": "평가와 상관없이 모든 표를 다시 추출한다",
    "read_pictures": "텍스트 레이어가 없는 그림(캡처 표·조직도)을 VLM 으로 읽는다",
    "render_pages": "쪽 이미지를 만든다", "render_scale": "쪽 이미지 배율",
    "split_chars": "쪽 경계가 없는 문서를 이 글자 수로 조각낸다 — 0 이면 나누지 않는다",
    "out_dir": "산출 뿌리 — 주면 `run()` 이 끝날 때 `<파일이름.확장자>/` 아래에 산출물을 쓴다",
    "progress": "진행 막대를 보인다", "steps": "진행 표시 — `brief` · `dev` · `silent`",
    "write_outputs": "`out_dir` 을 줬을 때 산출물을 쓸지",
    "llm_url": "표 평가·재추출에 쓸 LLM 주소", "llm_model": "그 LLM 모델 이름", "llm_key": "그 LLM 의 API 키",
    "llm_timeout": "LLM 요청 제한 시간(초)", "llm_concurrency": "LLM 동시 요청 수",
    "fallback_url": "기본 LLM 에 연결이 안 될 때 쓸 대비책 주소", "fallback_model": "대비책 모델 이름",
    "fallback_key": "대비책 API 키", "fallback_timeout": "대비책 요청 제한 시간(초)",
    "fallback_enabled": "대비책을 쓸지", "openai_key": "OpenAI API 키",
    "vlm_model": "이 장비에서 직접 돌릴 VLM — 지정하면 HTTP 대신 이것을 쓴다",
    "vlm_device": "그 VLM 을 올릴 장치", "vlm_dtype": "그 VLM 의 자료형", "vlm_max_tokens": "그 VLM 의 최대 생성 토큰",
    "llm_adapter": "HTTP 호출에 쓸 외부 어댑터 모듈 — 미지정이면 requests 로 직접 부른다",
    "picture_url": "그림 설명 VLM 주소", "picture_model": "그림 설명 모델 이름", "picture_key": "그림 설명 API 키",
    "picture_enabled": "그림 설명을 쓸지",
    "picture_area_threshold": "지면 대비 넓이 비율이 이보다 작은 그림은 읽지 않는다",
    "pdf_backend": "PDF 파서 백엔드", "ocr_backend": "OCR 엔진", "ocr_lang": "OCR 언어",
    "force_full_page_ocr": "텍스트 레이어가 있어도 쪽 전체를 OCR 한다",
    "generate_parsed_pages": "쪽별 텍스트 레이어/OCR 판별을 남긴다 — 끄면 처리 경로가 \"측정 안 함\" 으로 표시된다",
    "hwp_fill_html": "HWP 표 재추출에 HTML 경로를 쓴다", "korean_ocr": "한국어 OCR 보강을 쓴다",
    "flag_broken_tables": "깨진 표에 표시를 남긴다", "flag_odd_tables": "같은 서식 표와 열 수가 다른 표에 표시를 남긴다",
    "mark_table_continuation": "쪽을 넘어 이어지는 표를 표시한다",
    "read_charts": "그래프를 VLM 으로 읽는다 — LLM 이 설정돼 있으면 기본으로 켜진다",
    "detect_toc": "목차를 찾아 쪽 번호 오프셋을 잰다",
    "scanned_skip_docling_ocr": "스캔본에서 Docling OCR 을 건너뛴다", "verify_ocr": "OCR 결과를 다시 확인한다",
    "reread_doubts": "의심스러운 판독 조각을 다시 읽는다", "rebuild_grid": "표 격자를 다시 세운다",
    "vlm_fix_tables": "VLM 으로 표를 고친다",
    "code_formula_enrichment": "수식·코드 보강 — 표·본문 추출에는 쓰이지 않으면서 모델을 추가로 받는다",
    "device": "연산 장치 (`cpu` · `cuda` 등)", "num_threads": "스레드 수",
    "rapidocr_runtime": "RapidOCR 런타임", "threaded_pipeline": "스레드 파이프라인을 쓴다",
}


def config_defaults() -> dict[str, str]:
    text = (ROOT / "src/docstruct/core/config.py").read_text(encoding="utf-8")
    return {env: value.strip() for env, value in
            re.findall(r'_get_\w+\(\s*"([A-Z0-9_]+)"\s*,\s*([^)\n]+?)\s*\)', text)}


def options_html() -> str:
    env_of = dict(_api._ENV_KEYS)
    defaults = config_defaults()
    live = docstruct.defaults()
    groups: list[tuple[str, list[str]]] = [("실행", list(_api._RUN_KEYS)), ("화면 표시", list(_api._VIEW_KEYS))]
    src = inspect.getsource(_api).split("_ENV_KEYS", 1)[1].split("\n}\n", 1)[0]
    current = "환경"
    bucket: list[str] = []
    for line in src.splitlines():
        comment = re.match(r"\s*#\s*(.+)", line)
        key = re.match(r'\s*"(\w+)":', line)
        if comment:
            if bucket:
                groups.append((current, bucket))
            current, bucket = comment.group(1).strip(), []
        elif key:
            bucket.append(key.group(1))
    if bucket:
        groups.append((current, bucket))

    out = []
    for title, keys in groups:
        rows = []
        for k in keys:
            env = env_of.get(k, "")
            if k in _api._RUN_KEYS:
                dv = repr(_api._RUN_KEYS[k])
            elif k in _api._VIEW_KEYS:
                dv = repr(_api._VIEW_KEYS[k])
            else:
                dv = defaults.get(env) or (repr(live.get(k)) if live.get(k) is not None else "")
            if k in _api._SECRET_KEYS:
                dv = ""
            rows.append(f"<tr data-search=\"{k} {env.lower()}\"><td><code>{k}</code></td>"
                        f"<td>{('<code>' + env + '</code>') if env else '—'}</td>"
                        f"<td>{('<code>' + html.escape(safe(dv)) + '</code>') if dv else '—'}</td>"
                        f"<td>{inline(OPTION_WORDS.get(k, ''))}</td></tr>")
        out.append(f"<h4 class=\"group\">{html.escape(title)}</h4><div class=\"scroll\"><table class=\"params\">"
                   "<thead><tr><th>키</th><th>환경변수</th><th>기본값</th><th>뜻</th></tr></thead>"
                   f"<tbody>{''.join(rows)}</tbody></table></div>")
    return "\n".join(out)


# ─── aligned.json 필드 ───────────────────────────────────────────────────────
ALIGNED = {
    "문서 전체": [
        ("filename", "HWPX 원본 파일 이름"), ("page_count", "문서의 쪽 수 — PDF 가 정한다"),
        ("aligned_pages", "내용을 채운 쪽 수"), ("unaligned_pages", "대응을 못 찾은 쪽 번호 — 대개 표지·간지"),
        ("estimated_pages", "보간으로 채운 쪽 수"), ("text_anchors", "본문 눈금 수"),
        ("head", "첫 눈금 앞 머리말 — 쪽을 모른다"), ("head_chars", "그 글자 수"),
        ("wide_gap_pages", "눈금이 9쪽 넘게 떨어진 구간의 쪽 — ±1~2 여지"),
        ("page_span_pages", "PDF 좌표로 표가 두 쪽에 걸친 쪽"),
        ("split_block_pages", "표 블록이 쪽 경계에 걸린 쪽 수"),
        ("disputed_tables", "본문 위치와 표 짝이 다른 쪽을 가리켰던 표"),
        ("unmatched", "끝내 쪽을 모르는 표 — 각 표에 `align_note` 가 붙는다"),
        ("unplaced_images", "쪽을 정하지 못한 그림"),
    ],
    "표 배정 통계": [
        ("total_tables", "HWPX 표 전체"), ("matchable_tables", "맞출 수 있는 표 — 지면 장식 제외"),
        ("matchable_matched", "그중 짝을 지은 표 (본문 위치로 쪽을 얻은 표는 넣지 않는다)"),
        ("matched_tables", "PDF 표와 짝을 지은 표"), ("matched_by_text", "PDF 본문 대조로 쪽을 얻은 표"),
        ("text_order_check", "본문 대조로 얻은 쪽의 순서 검증"),
        ("unmatched_tables", "PDF 표와 짝을 못 지은 표 — 짝짓기 성적"),
        ("placed_by_body", "그중 본문 블록 위치로 쪽을 얻은 표"),
        ("moved_to_body_page", "짝이 가리킨 쪽에서 본문 쪽으로 옮긴 표"),
        ("unmatched_layout_like", "짝 못 지은 표 중 제목 상자로 보이는 것"),
    ],
    "pages[] — 쪽 하나": [
        ("page_no", "PDF 물리 쪽 번호"), ("printed_page_no", "사람이 말하는 인쇄 쪽 번호 — 표지·목차는 null"),
        ("page_no_kind", "`exact` 눈금으로 잡음 · `approximate` 보간"),
        ("estimated", "`approximate` 와 같은 뜻의 참/거짓"),
        ("content", "그 쪽 본문 markdown — 표·그림 블록 포함. **쪽 배정의 기준이다**"),
        ("tables", "그 쪽에 속한 표 — 본문 블록이 시작하는 쪽에 둔다"),
        ("similarity", "`tables` 중 짝지은 표의 닮음 — 앞에서부터 차례로 대응"),
        ("images", "그 쪽의 그림"),
        ("blank", "원래 내용이 없는 지면 (쪽번호만 찍힌 간지 등)"),
        ("wide_gap", "눈금이 먼 구간에 든 쪽"), ("gap_pages", "그 구간의 쪽 수"),
        ("page_span", "이 쪽의 내용이 걸친 범위 `[시작, 끝]` — 인용할 때 쪽 대신 범위를 댄다"),
        ("span_reason", "범위가 붙은 까닭"),
        ("starts_inside_table", "앞 쪽 표 안에서 시작하는 쪽 — 그 표 id"),
        ("split_blocks", "쪽 경계에 걸린 표 블록 `[{table_num, side}]` — side 는 `open`·`close`"),
        ("reordered_by_pdf", "쪽 안 순서를 PDF 에 맞춰 바꿨다"),
    ],
    "tables[] — 표에 덧붙는 값": [
        ("page_source", "`text` PDF 본문 대조로 쪽을 얻음 · `body` 본문 블록 위치로 쪽을 얻음 · 없으면 표 짝"),
        ("page_evidence", "본문 대조의 근거 (포함률·차이)"),
        ("pair_similarity", "PDF 표와의 닮음"),
        ("paired_page", "표 짝이 가리켰던 쪽 — 본문 쪽과 다를 때"),
        ("paired_pages", "PDF 조각 여럿과 짝지어진 쪽들"),
        ("body_page", "본문 블록이 있는 쪽"),
        ("pairing_doubt", "PDF 에 대응 표가 없는데 짝이 잡혔다 — 본문 위치를 따른다"),
        ("doubt_reason", "그 까닭"),
        ("page_span", "표가 걸친 범위"), ("span_reason", "까닭 — 두 쪽 인쇄 · 경계 차이 · 쪽 경계가 표 안 등"),
    ],
    "images[] — 그림에 덧붙는 값": [
        ("page_moved_from", "본문 자리표시자가 가리킨 쪽 — PDF 에서 그림이 실린 쪽으로 옮겼을 때"),
        ("move_reason", "옮긴 까닭"),
        ("number_check", "판독 수치 검산 — `verified` · `partial` · `unseen` · `none`"),
        ("numbers_checked", "검산한 수치 개수"), ("numbers_found", "그중 그 쪽에서 찾은 개수"),
    ],
}


def aligned_html() -> str:
    out = []
    for title, rows in ALIGNED.items():
        body = "".join(f"<tr data-search=\"{k}\"><td><code>{k}</code></td><td>{inline(v)}</td></tr>" for k, v in rows)
        out.append(f"<h4 class=\"group\">{html.escape(title)}</h4><div class=\"scroll\"><table class=\"params two\">"
                   f"<thead><tr><th>필드</th><th>뜻</th></tr></thead><tbody>{body}</tbody></table></div>")
    out.append("<div class=\"field\"><h4>쪽 번호를 인용할 때</h4>" + prose(
        "`page_no_kind` 가 `exact` 이면 눈금으로 잡은 쪽이다. `approximate` 에 `wide_gap` 이 붙었거나 "
        "`page_span` · `starts_inside_table` 이 있으면 쪽 하나가 아니라 **범위**로 답한다. "
        "`blank` 는 원래 내용이 없는 지면이다.") + "</div>")
    return "\n".join(out)


# ─── CLI ─────────────────────────────────────────────────────────────────────
def cli_html() -> str:
    from docstruct import cli

    parser = cli._build_parser()
    rows = []
    for action in parser._actions:
        if isinstance(action, argparse._HelpAction):
            continue
        names = ", ".join(action.option_strings) or action.dest
        meta = ""
        if action.choices:
            meta = " {" + ",".join(map(str, action.choices)) + "}"
        elif action.nargs != 0 and action.option_strings:
            meta = " " + (action.metavar or action.dest.upper())
        default = "" if action.default in (None, False, argparse.SUPPRESS) else repr(action.default)
        rows.append(f"<tr data-search=\"{html.escape(names.lower())}\"><td><code>{html.escape(names + meta)}</code></td>"
                    f"<td>{('<code>' + html.escape(safe(default)) + '</code>') if default else '—'}</td>"
                    f"<td>{inline((action.help or '').replace('%(default)s', str(action.default)))}</td></tr>")
    return ("<div class=\"scroll\"><table class=\"params\"><thead><tr><th>선택지</th><th>기본값</th>"
            f"<th>뜻</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>")


def env_html() -> str:
    names = set()
    notes: dict[str, str] = {}
    where: dict[str, str] = {}
    defaults: dict[str, str] = {}
    for path in (ROOT / "src/docstruct").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        for match in re.finditer(r'"(DOCSTRUCT_[A-Z0-9_]+)"', text):
            name = match.group(1)
            if name in _api._ENV_KEYS.values():
                continue
            names.add(name)
            where.setdefault(name, str(path.relative_to(ROOT / "src/docstruct")))
            got = re.search(rf'(?:environ\.get|getenv)\(\s*"{name}"\s*,\s*([^)\n]+)\)', text)
            if got and name not in defaults:
                defaults[name] = got.group(1).strip()
            if name not in notes:
                row = text[:match.start()].count("\n")
                buf, j = [], row - 1
                while j >= 0 and lines[j].strip().startswith("#") and len(buf) < 3:
                    s = lines[j].strip().lstrip("#:").strip()
                    if "═" in s or "─" in s:
                        break
                    buf.insert(0, s)
                    j -= 1
                if buf:
                    notes[name] = " ".join(buf)
    exp = sorted(n for n in names if n.startswith("DOCSTRUCT_EXP_"))
    rest = sorted(names - set(exp))

    def table(items):
        body = "".join(
            f"<tr data-search=\"{n.lower()}\"><td><code>{n}</code></td>"
            f"<td>{('<code>' + html.escape(safe(defaults[n])) + '</code>') if n in defaults else '—'}</td>"
            f"<td><code>{html.escape(where.get(n, ''))}</code></td><td>{inline(notes[n]) if notes.get(n) else '<span class=none>—</span>'}</td></tr>"
            for n in items)
        return ("<div class=\"scroll\"><table class=\"params\"><thead><tr><th>환경변수</th><th>기본값</th>"
                f"<th>쓰는 곳</th><th>뜻</th></tr></thead><tbody>{body}</tbody></table></div>")

    return (f"<h4 class=\"group\">동작 조절 ({len(rest)})</h4>{table(rest)}"
            f"<h4 class=\"group\">실험 켜고 끄기 ({len(exp)})</h4>{table(exp)}")


def module_html(mod, title: str) -> list[dict]:
    out = []
    for name, fn in vars(mod).items():
        if name.startswith("_") or not inspect.isfunction(fn) or fn.__module__ != mod.__name__:
            continue
        out.append(entry(f"{title}.{name}", fn, kind="함수", module=mod.__name__))
    return out


# ─── 쪽 조립 ─────────────────────────────────────────────────────────────────
def build() -> str:
    E = entry
    topics: list[dict] = []

    def topic(tid, title, intro, entries=None, extra=""):
        topics.append({"id": tid, "title": title, "intro": intro, "entries": entries or [], "extra": extra})

    quick = (
        "<div class=\"field\"><h4>설치</h4>"
        + code_block(f'pip install "docstruct @ git+https://github.com/alcien/docstruct.git@v{VERSION}"')
        + "</div><div class=\"field\"><h4>문서 하나 판독</h4>"
        + code_block('import docstruct\n\ndoc = docstruct.structure("성과계획서.hwpx")\n'
                     'doc["pages"][0]["content"]      # 본문 markdown\n'
                     'doc["pages"][0]["tables"]       # 표 (셀 격자 포함)')
        + "</div><div class=\"field\"><h4>HWPX 에 PDF 쪽 번호 붙이기</h4>"
        + code_block('got = docstruct.align_pair("성과계획서.hwpx", "성과계획서.pdf", out_dir="out")\n'
                     'got.save()        # out/<성과계획서.hwpx>/aligned.json · aligned.md')
        + "</div><div class=\"field\"><h4>명령줄</h4>"
        + code_block("docstruct 성과계획서.hwpx -o out\n"
                     "docstruct 성과계획서.hwpx --align 성과계획서.pdf -o out\n"
                     "docstruct hwpx폴더/ --align pdf폴더/ -o out        # 이름이 같은 쌍을 모두")
        + "</div>")
    topic("quick", "빠른 시작", "설치하고 문서 하나를 판독하기까지.", extra=quick)

    ds = [E("DocStruct", docstruct.DocStruct, kind="클래스", module="docstruct.api")] + members(docstruct.DocStruct, "DocStruct")
    batch = [E("DocStructBatch", docstruct.DocStructBatch, kind="클래스", module="docstruct.api")] + members(docstruct.DocStructBatch, "DocStructBatch")
    topic("read", "판독",
          "HWP·HWPX·PDF 를 쪽·본문·표·그림 구조로 바꾼다. 한 줄이면 `structure`, 설정을 바꿔 가며 여러 번 쓰면 `DocStruct`, 폴더째면 `DocStructBatch`.",
          [E("structure", docstruct.structure, kind="함수", module="docstruct.api"),
           E("structure_to_json", docstruct.structure_to_json, kind="함수", module="docstruct.api"),
           *ds, *batch,
           E("build_document", docstruct.build_document, kind="함수", module="docstruct.pipeline"),
           E("DocStructError", docstruct.DocStructError, kind="예외", module="docstruct.api")],
          extra=f"<div class=\"field\"><h4>지원 형식</h4><p><code>SUPPORTED_SUFFIXES</code> = "
                f"<code>{html.escape(repr(docstruct.SUPPORTED_SUFFIXES))}</code></p></div>")

    ap = [E("AlignPair", docstruct.AlignPair, kind="클래스", module="docstruct.align.pair")] + members(docstruct.AlignPair, "AlignPair")
    ab = [E("AlignBatch", docstruct.AlignBatch, kind="클래스", module="docstruct.align.batch")] + members(docstruct.AlignBatch, "AlignBatch")
    topic("align", "쪽 맞춤",
          "HWPX(쪽 없음)의 본문을 PDF(쪽 있음)의 쪽 경계로 자른다. 한 쌍은 `align_pair`, 폴더 둘이면 `align_folders`. 결과는 `aligned.json` — 필드 뜻은 아래 “aligned.json 필드” 에 있다.",
          [E("align_pair", docstruct.align_pair, kind="함수", module="docstruct.align.pair"), *ap,
           E("align_folders", docstruct.align_folders, kind="함수", module="docstruct.align.batch"), *ab,
           E("prepare", docstruct.prepare, kind="함수", module="docstruct.align.pair"),
           E("Prepared", docstruct.Prepared, kind="클래스", module="docstruct.align.pair"),
           E("find_counterpart", docstruct.find_counterpart, kind="함수", module="docstruct.align.pair"),
           E("align_documents", docstruct.align_documents, kind="함수", module="docstruct.align.documents"),
           E("align.documents.to_markdown", _align_docs.to_markdown, kind="함수", module="docstruct.align.documents"),
           E("align.documents.summary_lines", _align_docs.summary_lines, kind="함수", module="docstruct.align.documents"),
           E("align.documents.tables_of", _align_docs.tables_of, kind="함수", module="docstruct.align.documents"),
           E("align.pair.out_folder_name", _align_pair.out_folder_name, kind="함수", module="docstruct.align.pair")])

    topic("aligned-json", "aligned.json 필드",
          "쪽 맞춤 결과의 모양. 쪽 번호를 얼마나 믿을지가 쪽마다 적혀 있다.", extra=aligned_html())

    topic("config", "설정·키·모델",
          "설정은 `DocStruct(…, 키=값)` 이나 `configure(키=값)` 으로 준다. 키마다 대응하는 환경변수가 있다 — `run()` 동안에만 적용된다.",
          [E("option_keys", docstruct.option_keys, kind="함수", module="docstruct.api"),
           E("defaults", docstruct.defaults, kind="함수", module="docstruct.api"),
           E("configure", docstruct.configure, kind="함수", module="docstruct.api"),
           E("set_api_key", docstruct.set_api_key, kind="함수", module="docstruct.api"),
           E("set_model", docstruct.set_model, kind="함수", module="docstruct.api"),
           E("mask", docstruct.mask, kind="함수", module="docstruct.api"),
           E("enable_logging", docstruct.enable_logging, kind="함수", module="docstruct.api")],
          extra="<h3 class=\"sub\">설정 키 전체</h3>" + options_html())

    model_entries = []
    model_extra = []
    for cname in ("PageDocument", "PageContent", "TableInfo", "ImageInfo", "PageTrace", "TraceStep"):
        cls = getattr(models, cname)
        d = parse_doc(inspect.getdoc(cls) or "")
        model_extra.append(
            f"<article class=\"entry\" id=\"{slug(cname)}\" data-search=\"{cname.lower()} {html.escape(d.get('summary', '').lower())}\">"
            f"<header><h3><code>{cname}</code></h3><span class=\"kind\">결과 모델</span>"
            f"<span class=\"mod\">docstruct.models</span></header>"
            + (f"<p class=\"lead\">{inline(d['summary'])}</p>" if d.get("summary") else "")
            + fields_html(cls)
            + "".join(f"<div class=\"member\">{entry_html(m)}</div>" for m in members(cls, cname))
            + "</article>")
    topic("models", "결과 모델",
          "`DocStruct.document` 가 돌려주는 객체들. `to_dict()` 로 바꾸면 document.json 과 같은 모양이다.",
          model_entries, extra="".join(model_extra))

    from docstruct import preview, report, winfix
    topic("report", "파일 산출 (report)", "판독 결과를 사람이 읽는 파일로 쓴다.", module_html(report, "report"))
    topic("preview", "노트북 표시 (preview)", "Jupyter 에서 판독 결과를 HTML 로 본다.", module_html(preview, "preview"))
    topic("winfix", "Windows 로케일 (winfix)", "cp949 같은 비 UTF-8 로케일에서 생기는 문제를 우회한다.", module_html(winfix, "winfix"))

    topic("cli", "명령줄", "`docstruct 파일 [선택지]` — 폴더를 주면 안의 문서를 모두 처리한다.", extra=cli_html())
    topic("env", "그 밖의 환경변수",
          "설정 키가 없는 조절값들. 대개 기본값 그대로 두면 되고, 실험(`DOCSTRUCT_EXP_*`)은 측정할 때만 켠다.",
          extra=env_html())

    # 목차
    nav = []
    for t in topics:
        items = "".join(
            f"<li class=\"{'m' if e['member_of'] else ''}\" data-target=\"{e['id']}\"><a href=\"#{e['id']}\">{html.escape(e['name'].split('.')[-1] if e['member_of'] else e['name'])}</a></li>"
            for e in t["entries"])
        if t["id"] == "models":
            items = "".join(f"<li data-target=\"{slug(c)}\"><a href=\"#{slug(c)}\">{c}</a></li>"
                            for c in ("PageDocument", "PageContent", "TableInfo", "ImageInfo", "PageTrace", "TraceStep"))
        nav.append(f"<li class=\"topic\"><a href=\"#{t['id']}\">{html.escape(t['title'])}</a>"
                   + (f"<ul>{items}</ul>" if items else "") + "</li>")

    body = []
    for t in topics:
        body.append(f"<section class=\"topic\" id=\"{t['id']}\"><h2>{html.escape(t['title'])}</h2>"
                    f"<p class=\"intro\">{inline(t['intro'])}</p>"
                    + "".join(entry_html(e) if not e["member_of"] else f"<div class=\"member\">{entry_html(e)}</div>"
                              for e in t["entries"])
                    + t["extra"] + "</section>")

    count = sum(len(t["entries"]) for t in topics) + 6
    return PAGE.format(version=VERSION, nav="".join(nav), body="".join(body), count=count, css=CSS, js=JS)


CSS = r"""
:root{
  --paper:#F7F9FB; --panel:#FFFFFF; --ink:#1C2633; --muted:#5A6676; --rule:#D8DEE6;
  --sig:#EDF2F7; --sigink:#23364D; --accent:#1F5E8C; --accentsoft:#E3EEF7; --req:#8C5A12;
  --codebg:#F1F4F8; --mark:#FFF4C9;
  box-sizing:border-box; padding-top:env(safe-area-inset-top,0px); padding-bottom:env(safe-area-inset-bottom,0px);
}
@media (prefers-color-scheme:dark){ :root:not([data-theme="light"]){
  --paper:#161B22; --panel:#1C222B; --ink:#E4E9EF; --muted:#9AA6B5; --rule:#2E3743;
  --sig:#212A36; --sigink:#CFE0F2; --accent:#7FB4E0; --accentsoft:#1F2E3E; --req:#E0B266;
  --codebg:#1A2029; --mark:#4A3F16; } }
:root[data-theme="dark"]{
  --paper:#161B22; --panel:#1C222B; --ink:#E4E9EF; --muted:#9AA6B5; --rule:#2E3743;
  --sig:#212A36; --sigink:#CFE0F2; --accent:#7FB4E0; --accentsoft:#1F2E3E; --req:#E0B266;
  --codebg:#1A2029; --mark:#4A3F16; }
*,*::before,*::after{box-sizing:inherit}
html{height:100%;scroll-padding-top:calc(env(safe-area-inset-top,0px) + 16px)}
body{margin:0;background:var(--paper);color:var(--ink);
  font:15.5px/1.7 "IBM Plex Sans KR","Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif;
  -webkit-text-size-adjust:100%}
code,pre{font-family:"IBM Plex Mono",ui-monospace,"SFMono-Regular",Consolas,monospace}
a{color:var(--accent);text-decoration:none} a:hover{text-decoration:underline}
a:focus-visible,button:focus-visible,input:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:3px}

.layout{display:grid;grid-template-columns:minmax(230px,280px) minmax(0,1fr);min-height:100%}
aside{position:sticky;top:0;align-self:start;height:100vh;height:100dvh;overflow:auto;
  border-right:1px solid var(--rule);background:var(--panel);padding:20px 16px 40px}
.brand{font-weight:700;font-size:17px;letter-spacing:-.01em;margin:0 0 2px}
.brand small{display:block;font-weight:400;color:var(--muted);font-size:13px;margin-top:2px}
.search{width:100%;margin:16px 0 6px;padding:9px 11px;border:1px solid var(--rule);border-radius:6px;
  background:var(--paper);color:var(--ink);font:inherit;font-size:14px}
.hits{font-size:12.5px;color:var(--muted);min-height:18px;margin-bottom:10px}
nav ul{list-style:none;margin:0;padding:0}
nav li.topic{margin:10px 0 4px}
nav li.topic>a{font-weight:600;color:var(--ink);font-size:14px}
nav li.topic ul{margin:4px 0 0 2px;border-left:1px solid var(--rule)}
nav li li a{display:block;padding:2px 0 2px 12px;font-size:13px;color:var(--muted);
  font-family:"IBM Plex Mono",ui-monospace,monospace;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
nav li li.m a{padding-left:24px}
nav li li a:hover{color:var(--accent)}
nav li.hide{display:none}

main{padding:36px clamp(18px,4vw,56px) 96px;max-width:980px;min-width:0}
.hero h1{font-size:clamp(28px,4vw,38px);line-height:1.2;letter-spacing:-.02em;margin:0 0 10px;font-weight:700}
.hero p{margin:0 0 6px;color:var(--muted);max-width:62ch}
.hero .ver{display:inline-block;margin-top:10px;font-size:13px;color:var(--sigink);background:var(--sig);
  border:1px solid var(--rule);border-radius:999px;padding:2px 10px}
section.topic{margin-top:56px}
section.topic>h2{font-size:24px;letter-spacing:-.01em;margin:0 0 6px;padding-bottom:8px;border-bottom:2px solid var(--ink)}
.intro{color:var(--muted);margin:8px 0 18px;max-width:70ch}
h3.sub{font-size:18px;margin:34px 0 4px}
h4.group{font-size:14.5px;margin:22px 0 8px;color:var(--sigink)}

.entry{background:var(--panel);border:1px solid var(--rule);border-radius:8px;padding:18px 20px 16px;margin:16px 0}
.member .entry{margin-left:22px;border-left:3px solid var(--accentsoft)}
.entry header{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 12px;margin-bottom:10px}
.entry h3{margin:0;font-size:17px;font-weight:600}
.entry h3 code{background:none;padding:0;font-size:16.5px}
.kind{font-size:12.5px;color:var(--accent);background:var(--accentsoft);border-radius:4px;padding:1px 7px}
.mod{font-size:12.5px;color:var(--muted);font-family:"IBM Plex Mono",ui-monospace,monospace}
pre.sig{margin:0 0 12px;padding:12px 14px;background:var(--sig);color:var(--sigink);border-radius:6px;
  font-size:13.5px;line-height:1.6;overflow-x:auto;white-space:pre}
pre.sig .fn{font-weight:600} pre.sig .pn{color:var(--ink)} pre.sig .ty{color:var(--accent)}
pre.sig .dv{color:var(--req)} pre.sig .punct{color:var(--muted)}
.lead{margin:0 0 12px;max-width:72ch}
.field{margin:12px 0}
.field h4,.note summary{font-size:13px;font-weight:600;color:var(--muted);margin:0 0 4px}
.field p,.note p{margin:4px 0;max-width:72ch}
code{background:var(--codebg);border-radius:4px;padding:1px 5px;font-size:.88em}
pre.code{background:var(--codebg);border-radius:6px;padding:12px 14px;margin:6px 0;overflow-x:auto;
  font-size:13px;line-height:1.55;white-space:pre}
.scroll{overflow-x:auto;margin:8px 0}
table.params{border-collapse:collapse;width:100%;font-size:14px;min-width:560px}
table.params.two{min-width:0}
table.params th{text-align:left;font-weight:600;font-size:12.5px;color:var(--muted);
  border-bottom:1px solid var(--rule);padding:6px 10px 6px 0}
table.params td{border-bottom:1px solid var(--rule);padding:7px 10px 7px 0;vertical-align:top}
table.params td:first-child{white-space:nowrap}
.none{color:var(--muted)}
table.sigp{table-layout:fixed}
table.sigp th:nth-child(1){width:26%}
table.sigp th:nth-child(2){width:20%}
table.sigp th:nth-child(3){width:14%}
table.sigp td{overflow-wrap:anywhere}
table.sigp td:first-child{white-space:normal}
.req{margin-left:6px;font-size:11.5px;color:var(--req);border:1px solid currentColor;border-radius:3px;padding:0 4px;white-space:nowrap}
details.note{margin-top:10px;border-top:1px dashed var(--rule);padding-top:8px}
details.note summary{cursor:pointer;list-style:none}
details.note summary::before{content:"＋ ";color:var(--accent)}
details.note[open] summary::before{content:"－ "}
.entry.hide,section.topic.hide,tr.hide{display:none}
mark{background:var(--mark);color:inherit;border-radius:2px}
footer{margin-top:72px;color:var(--muted);font-size:13px;border-top:1px solid var(--rule);padding-top:14px}

.menu{display:none}
@media (max-width:860px){
  .layout{grid-template-columns:minmax(0,1fr)}
  aside{position:fixed;inset:0 auto 0 0;width:min(86vw,320px);transform:translateX(-102%);
    transition:transform .2s ease;z-index:20;box-shadow:0 0 0 100vmax rgba(0,0,0,0);
    padding-top:calc(env(safe-area-inset-top,0px) + 20px)}
  aside.open{transform:none;box-shadow:0 0 0 100vmax rgba(0,0,0,.35)}
  .menu{display:inline-flex;position:fixed;right:14px;bottom:calc(env(safe-area-inset-bottom,0px) + 14px);z-index:21;
    background:var(--ink);color:var(--paper);border:0;border-radius:999px;padding:10px 16px;font:inherit;font-size:14px}
  main{padding-top:24px}
  .member .entry{margin-left:10px}
}
@media (prefers-reduced-motion:reduce){ aside{transition:none} }
"""

JS = r"""
(function(){
  const q=document.getElementById('q'), hits=document.getElementById('hits');
  const entries=[...document.querySelectorAll('.entry')];
  const rows=[...document.querySelectorAll('tr[data-search]')];
  const sections=[...document.querySelectorAll('section.topic')];
  const navItems=[...document.querySelectorAll('nav li[data-target]')];
  function run(){
    const t=q.value.trim().toLowerCase();
    let n=0;
    entries.forEach(e=>{const on=!t||(e.dataset.search||'').includes(t)||e.textContent.toLowerCase().includes(t);
      e.classList.toggle('hide',!on); if(on&&t) n++;});
    rows.forEach(r=>{const on=!t||r.textContent.toLowerCase().includes(t); r.classList.toggle('hide',!on); if(on&&t) n++;});
    sections.forEach(s=>{const any=!t||s.querySelector('.entry:not(.hide), tr[data-search]:not(.hide)')||s.id==='quick'&&!t;
      s.classList.toggle('hide',!any);});
    navItems.forEach(li=>{const el=document.getElementById(li.dataset.target);
      li.classList.toggle('hide',!!t&&(!el||el.classList.contains('hide')));});
    hits.textContent=t?(n?`${n}건 찾음`:'찾은 것이 없습니다 — 다른 낱말로 찾아 보세요'):'';
  }
  q.addEventListener('input',run);
  document.addEventListener('keydown',e=>{ if(e.key==='/'&&document.activeElement!==q){e.preventDefault();q.focus();}
    if(e.key==='Escape'&&document.activeElement===q){q.value='';run();q.blur();} });
  const aside=document.querySelector('aside'), menu=document.querySelector('.menu');
  menu.addEventListener('click',()=>{const o=aside.classList.toggle('open');menu.setAttribute('aria-expanded',o);});
  aside.addEventListener('click',e=>{ if(e.target.closest('a')) {aside.classList.remove('open');menu.setAttribute('aria-expanded',false);} });
})();
"""

PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>docstruct 함수 정의서 · {version}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans+KR:wght@400;600;700&display=swap" rel="stylesheet">
<style>{css}</style></head>
<body><div class="layout">
<aside aria-label="목차">
  <p class="brand">docstruct 함수 정의서<small>판 {version} · 공개 API</small></p>
  <input id="q" class="search" type="search" placeholder="함수·옵션·필드 찾기  ( / )" aria-label="찾기">
  <div id="hits" class="hits" aria-live="polite"></div>
  <nav><ul>{nav}</ul></nav>
</aside>
<main>
  <div class="hero">
    <h1>docstruct 함수 정의서</h1>
    <p>한국 정부 문서(HWP·HWPX·PDF)를 쪽·본문·표·그림 구조로 바꾸는 파이썬 라이브러리. 주제별로 함수와 매개변수, 돌려주는 값, 설정 키를 정리했다.</p>
    <p>이 문서는 코드에서 바로 만든 것이다 — 서명은 실제 함수에서, 설명은 docstring 에서, 기본값은 설정 모듈에서 읽었다.</p>
    <span class="ver">판 {version}</span>
  </div>
  {body}
  <footer>docstruct {version} · <code>python tools/api_doc.py 출력.html</code> 로 다시 만든다.</footer>
</main></div>
<button class="menu" type="button" aria-expanded="false">목차</button>
<script>{js}</script>
</body></html>
"""


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "API_정의서.html")
    out.write_text(build(), encoding="utf-8")
    print(f"{out} · {out.stat().st_size:,} 바이트")
