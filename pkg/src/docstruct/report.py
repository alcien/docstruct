"""PageDocument → 파일 산출물.

역할:
    구조화 결과를 사람이 읽는 markdown 과 기계가 읽는 JSON 으로 내보낸다.
호출부:
    docstruct.cli.main
출력:
    document.md    본문 (표는 GFM 으로 펼쳐진 상태)
    document.json  전체 구조 (trace·pipeline·timings 포함)
    tables.md      표별 판정 결과와 재추출 전/후 비교
    pipeline.md    적용 설정 · 단계별 소요 시간 · 페이지별 처리 경로
"""
from __future__ import annotations

import json
from pathlib import Path

from docstruct.models import IMAGE, TABLE, TEXT, PageDocument


def write_json(doc: PageDocument, path: str | Path) -> Path:
    """구조화 결과를 JSON 으로 저장한다.

    입력: doc — PageDocument, path — 저장 경로
    출력: 저장된 Path (document.json)
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(doc.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out


def write_markdown(doc: PageDocument, path: str | Path) -> Path:
    """본문을 markdown 으로 저장한다.

    입력: doc, path
    출력: 저장된 Path (표·이미지 placeholder 가 실제 내용으로 펼쳐진 상태)
    """
    parts = [f"# {doc.filename}", ""]
    for page in doc.pages:
        label = (
            f"페이지 {page.page_no}"
            if page.page_no_kind == "exact"
            else "문서 전체"
        )
        parts.append(f"\n---\n\n## {label}\n")
        parts.append(page.content or "_(내용 없음)_")
    return _write(path, "\n".join(parts) + "\n")


def write_tables_report(doc: PageDocument, path: str | Path) -> Path:
    """표별 판정과 재추출 전/후를 저장한다.

    입력: doc, path
    출력: 저장된 Path (판정 표 + 변경된 표의 원본/결과 대조)
    """
    lines = [f"# 표 리포트 — {doc.filename}", ""]

    rows = doc.all_tables()
    if not rows:
        lines.append("_표가 없습니다._")
        return _write(path, "\n".join(lines) + "\n")

    lines += [
        "| ID | 페이지 | 판정 | 품질 | 재추출 | 제목 |",
        "|----|--------|------|------|--------|------|",
    ]
    for page, t in rows:
        lines.append(
            f"| `{t.id}` | {page.page_no} | {t.content_type or '-'} "
            f"| {t.quality or '-'} | {'O' if t.was_filled else '-'} "
            f"| {t.llm_title or '-'} |"
        )

    lines.append("\n---\n")

    for page, t in rows:
        lines.append(f"## `{t.id}` (페이지 {page.page_no})")
        if t.reason:
            lines.append(f"\n> 판단 근거: {t.reason}\n")

        if t.was_filled:
            lines.append("\n### 재추출 전\n")
            lines.append(t.original_markdown or "_(없음)_")
            lines.append("\n### 재추출 후\n")
            lines.append(t.markdown or "_(없음)_")
        else:
            lines.append("")
            lines.append(t.markdown or "_(없음)_")
        lines.append("")

    return _write(path, "\n".join(lines) + "\n")


# 출처 라벨은 models.SOURCE_LABELS 하나로 관리합니다 (중복 정의 금지).
_SOURCE_DESC = {
    "text_layer": "PDF 내장 텍스트 레이어를 읽음 (OCR 미사용)",
    "ocr": "이미지 인식으로 텍스트를 얻음 (스캔 영역)",
    "mixed": "텍스트 레이어 + 일부 영역 OCR",
    "empty": "본문이 전혀 나오지 않음 — 확인 필요",
    "unmeasured": "파싱은 정상. 출처(레이어/OCR) 구분만 불가 "
                  "— Docling 이 셀 데이터를 보관하지 않음",
    "n/a": "PDF 가 아님 (HWP/HWPX)",
    "unknown": "미상",
}

_EXTRACTOR_LABEL = {
    "docling": "Docling (레이아웃 분석 + 표 구조 인식)",
    "hwpml-xml": "HWPML XML 직접 파싱",
    "pyhwp-html": "pyhwp → HTML → BS4 파싱",
    "olefile-text": "olefile 텍스트 추출 (표·그림 구조 손실)",
    "python-hwpx": "python-hwpx (OOXML 파싱)",
    "unknown": "미상",
}


def write_pipeline_report(doc: PageDocument, path: str | Path) -> Path:
    """처리 경로와 소요 시간을 저장한다.

    입력: doc, path
    출력: 저장된 Path (적용 설정 · 단계별 시간 · 페이지별 경로 · 순차 실행 로그)
    """
    lines = [f"# 파이프라인 처리 이력 — {doc.filename}", ""]

    lines.append("## 이 실행에 적용된 설정\n")
    lines += ["| 항목 | 값 |", "|------|-----|"]
    for key, value in doc.pipeline.items():
        lines.append(f"| `{key}` | {value} |")

    total = sum(doc.timings.values()) if doc.timings else 0.0
    if doc.timings and total > 0:
        lines += ["", f"## 단계별 소요 시간 (총 {total:.1f}초)", ""]
        lines += ["| 단계 | 초 | 비중 | GPU 영향 |", "|------|----|------|----------|"]
        from docstruct.pipeline import GPU_ACCELERATED

        for label, sec in sorted(doc.timings.items(), key=lambda kv: -kv[1]):
            gpu = "O" if label in GPU_ACCELERATED else "X"
            lines.append(f"| {label} | {sec:.1f} | {sec / total * 100:.0f}% | {gpu} |")
    elif doc.timings:
        lines += ["", "## 단계별 소요 시간", "", "모든 단계가 0.01초 미만입니다."]
        lines.append("")
        lines.append(
            "> GPU 는 '추출' 구간(레이아웃 모델·TableFormer·OCR)만 줄입니다. "
            "원격 LLM 과 CPU 렌더는 영향이 없습니다."
        )

    lines += ["", "## 페이지별 처리 경로", ""]
    lines += [
        "| 페이지 | 추출기 | 텍스트 출처 | 셀 | 표 | 그림 | 렌더 | 평가 | 재추출 |",
        "|--------|--------|-------------|----|----|------|------|------|--------|",
    ]
    from docstruct.models import SOURCE_LABELS, source_label

    for page in doc.pages:
        t = page.trace
        lines.append(
            f"| {page.page_no} | {t.extractor} | {source_label(t.text_source, t.ocr_ratio)} "
            f"| {t.cell_count if t.cell_count is not None else '-'} "
            f"| {t.table_count} | {t.picture_count} "
            f"| {'O' if t.rendered else '-'} | {'O' if t.assessed else '-'} "
            f"| {len(t.refilled) or '-'} |"
        )

    notes = [(p.page_no, n) for p in doc.pages for n in p.trace.notes]
    if notes or doc.failed_pages:
        lines += ["", "## 특이사항", ""]
        if doc.failed_pages:
            lines.append(f"- 파싱 실패로 결과에서 빠진 페이지: {doc.failed_pages}")
        for page_no, note in notes:
            lines.append(f"- p.{page_no}: {note}")

    lines += ["", "## 페이지별 순차 실행 로그", ""]
    for page in doc.pages:
        lines.append(f"### 페이지 {page.page_no}\n")
        lines.append("```")
        lines.append(page.trace.log())
        lines.append("```\n")

    lines += ["", "## 경로 설명", ""]
    used_extractors = {p.trace.extractor for p in doc.pages}
    for name in sorted(used_extractors):
        lines.append(f"- **{name}** — {_EXTRACTOR_LABEL.get(name, '?')}")
    used_sources = {p.trace.text_source for p in doc.pages} - {"n/a"}
    for name in sorted(used_sources):
        label = SOURCE_LABELS.get(name, name)
        lines.append(f"- **{label}** — {_SOURCE_DESC.get(name, '?')}")

    return _write(path, "\n".join(lines) + "\n")


def write_layout_report(doc: PageDocument, path: str | Path) -> Path:
    """레이아웃 모델 판정과 파이프라인 처리 결과를 대조해 저장한다.

    입력: doc — PageDocument, path — 저장 경로
    출력: 저장된 Path (layout.md)
    비고:
        표가 깨졌을 때 라벨이 잘못 붙은 것(모델 문제)인지, 라벨은 맞는데
        변환이 어긋난 것(파이프라인 문제)인지 구분하는 데 쓴다.
    """
    from docstruct.layout import LABEL_KO, OUTCOME_KO, label_counts, overlapping_pairs

    lines = [f"# 레이아웃 인식 결과 — {doc.filename}", ""]

    all_items = [i for p in doc.pages for i in p.layout]
    if not all_items:
        lines.append("레이아웃 정보 없음 (PDF 가 아니거나 추출 전 결과입니다).")
        return _write(path, "\n".join(lines) + "\n")

    lines += ["## 라벨 분포 (문서 전체)", ""]
    lines += ["| 라벨 | 개수 | 처리 결과 |", "|------|------|-----------|"]
    counts = label_counts(all_items)
    for label, n in counts.items():
        outcomes = {i.outcome for i in all_items if i.label == label}
        desc = ", ".join(OUTCOME_KO.get(o, o) for o in sorted(outcomes) if o)
        lines.append(f"| {LABEL_KO.get(label, label)} (`{label}`) | {n} | {desc} |")

    dropped = [i for i in all_items if i.outcome == "dropped"]
    if dropped:
        lines += ["", f"> 내용이 비어 버려진 영역 {len(dropped)}개 "
                  "— 레이아웃은 잡았으나 텍스트가 추출되지 않은 경우입니다."]

    lines += ["", "## 페이지별 인식 영역", ""]
    for page in doc.pages:
        if not page.layout:
            continue
        lines.append(f"### 페이지 {page.page_no}\n")
        lines += [
            "| # | 라벨 | 좌표 (l,t,r,b) | 글자수 | 처리 | 산출물 | 내용 |",
            "|---|------|----------------|--------|------|--------|------|",
        ]
        for item in page.layout:
            bbox = (
                f"{item.bbox['l']:.0f},{item.bbox['t']:.0f},"
                f"{item.bbox['r']:.0f},{item.bbox['b']:.0f}"
                if item.bbox else "-"
            )
            text = (item.text or "").replace("|", "\\|")
            lines.append(
                f"| {item.order} | {item.label_ko} | {bbox} | {item.char_count} "
                f"| {OUTCOME_KO.get(item.outcome, item.outcome)} "
                f"| {item.ref or '-'} | {text} |"
            )

        overlaps = overlapping_pairs(page.layout)
        if overlaps:
            lines.append("")
            lines.append("**겹치는 영역** — 같은 자리를 두 번 인식했을 수 있습니다.")
            for a, b, ratio in overlaps:
                lines.append(
                    f"- #{a.order} {a.label_ko} ↔ #{b.order} {b.label_ko} "
                    f"(겹침 {ratio:.0%})"
                )
        lines.append("")

    lines += [
        "## 원인 구분",
        "",
        "- 실제로는 표인데 라벨이 `그림`/`본문` 이면 → **레이아웃 모델 오인식**",
        "- 라벨은 `표` 인데 내용이 깨졌으면 → **표 구조 복원(TableFormer) 또는 변환 문제**",
        "- 라벨이 아예 없으면 (영역 누락) → **레이아웃 모델 미검출**",
        "- 처리가 `버려짐` 이면 → 영역은 잡았으나 텍스트 추출 실패 (OCR/텍스트 레이어 확인)",
    ]
    return _write(path, "\n".join(lines) + "\n")


def summary_lines(doc: PageDocument) -> list[str]:
    """콘솔 출력용 요약을 만든다.

    입력: doc — PageDocument
    출력: 파일명·페이지 수·본문 길이·표/이미지 개수 등을 담은 문자열 목록
    """
    tables = [t for _, t in doc.all_tables()]
    images = doc.all_images()

    by_type: dict[str, int] = {}
    for t in tables:
        by_type[t.content_type or "미평가"] = by_type.get(t.content_type or "미평가", 0) + 1

    filled = sum(1 for t in tables if t.was_filled)

    lines = [
        f"파일       : {doc.filename} ({doc.source_format})",
        f"페이지     : {len(doc.pages)}",
        f"본문 길이  : {doc.char_count():,}자",
        f"표         : {len(tables)}개"
        + (f"  ({', '.join(f'{k} {v}' for k, v in sorted(by_type.items()))})" if by_type else ""),
        f"표 재추출  : {filled}개",
        f"이미지     : {len(images)}개",
    ]

    empty = [p.page_no for p in doc.pages if not (p.content or "").strip()]
    if empty:
        lines.append(f"⚠ 빈 페이지 : {empty}")
    if doc.failed_pages:
        lines.append(f"⚠ 파싱 실패 : {doc.failed_pages}  ← 결과에서 빠진 페이지")

    return lines


def _write(path: str | Path, text: str) -> Path:
    """텍스트를 파일로 쓴다 (상위 디렉터리 자동 생성).

    입력: path, text
    출력: 저장된 Path
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return out
