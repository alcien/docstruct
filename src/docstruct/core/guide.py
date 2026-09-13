"""고치고 싶은 것 → 갈 곳 (코드 길잡이).

입력:
    관심사 키워드 (예: "스캔 pdf 표", "hwpx 쪽 맞춤")
역할:
    **"스캔 PDF 의 표 정합성을 고치고 싶다" 에서 파일까지** 한 번에 잇는다.
    폴더 구조(두 축 — 형식 · 인식)는 코드를 **짜기** 좋은 모양이지만, 고칠
    자리를 **찾기** 에는 한 축이 모자란다. 실제 작업은 언제나 두 축이
    만나는 자리에서 일어나기 때문이다:

        스캔 PDF 의 표    →  converters/pdf(형식) + tables(인식) + experiments
        HWPX 의 쪽 번호   →  converters/hwpx(형식) + align(공통)

    파일 하나는 폴더 하나에만 살 수 있으니 이 교차는 **폴더로는 표현할 수
    없다.** 그래서 표로 만들었다. 이 표는 코드다 — 경로가 실제로 있는지
    시험이 확인하므로(`test_guide_paths_all_exist`) 문서처럼 낡지 않는다.
호출부:
    docstruct.cli (`docstruct --where <키워드>`) · 구조.md
출력:
    Topic 목록 (관심사 → 순서대로 거쳐야 할 자리)

읽는 법
------
각 관심사는 **파이프라인이 지나가는 순서**로 적혀 있다. 위에서부터 읽으면
"입력이 어떻게 여기까지 왔는가" 가 되고, 고칠 자리는 대개 증상이 처음
드러나는 단계다. 구간 번호는 `pipeline.build_document` 의 배너와 같다.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Stop:
    """관심사 하나가 거치는 자리 하나.

    입력(필드):
        path   `src/docstruct/` 아래 상대 경로
        does   그 파일이 이 관심사에서 하는 일
        phase  파이프라인 구간 번호 (해당 없으면 None)
    """

    path: str
    does: str
    phase: int | None = None


@dataclass(frozen=True)
class Topic:
    """고치고 싶은 것 하나.

    입력(필드):
        key       짧은 이름
        title     한 줄 설명
        keywords  `--where` 가 찾을 말들
        stops     거치는 자리 (파이프라인 순서)
        note      먼저 알아야 할 것
    """

    key: str
    title: str
    keywords: tuple[str, ...]
    stops: tuple[Stop, ...]
    note: str = ""
    formats: tuple[str, ...] = field(default_factory=tuple)


#: 형식이 **무엇을 줄 수 있는가**. 실험이 왜 어떤 형식에서 안 도는지는
#: 거의 전부 이 표로 설명된다 — "안 맞는 형식" 이 아니라 "재료가 없는 형식"
#: 이다. `pdf` 는 실행 중에 텍스트본/스캔본으로 다시 갈린다(구간 2).
FORMAT_CAPABILITIES: dict[str, dict[str, str]] = {
    "pdf": {
        "cells": "TableFormer 가 셀 격자를 준다 (스캔본은 OCR 조각을 좌표로 귀속)",
        "geometry": "글자·단어 좌표가 있다 (스캔본은 OCR 조각 좌표)",
        "vector": "도형·괘선을 읽을 수 있다 — **스캔본에는 없다**",
        "page": "쪽 경계가 있다",
        "image": "그림을 꺼낼 수 있다",
        "raster": "쪽을 화소로 그릴 수 있다 (스캔 판독·표 판정의 시각 근거)",
    },
    "hwpx": {
        "cells": "XML 이 병합까지 그대로 준다 — 가장 정확하다",
        "geometry": "없다. 좌표가 없으므로 격자 근거를 만들 수 없다",
        "vector": "없다",
        "page": "**없다.** 쪽 경계가 없어 align/ 으로 PDF 에서 물려받는다",
        "image": "BinData 에서 꺼낸다",
        "raster": "없다. 쪽이 없으니 지면을 그릴 수 없다",
    },
    "hwp": {
        "cells": "**없다.** 사다리가 markdown 만 만든다 — 가장 큰 공백",
        "geometry": "없다",
        "vector": "없다",
        "page": "없다",
        "image": "미리보기 스트림 정도만",
        "raster": "없다",
    },
}


TOPICS: tuple[Topic, ...] = (
    Topic(
        key="scan-pdf-table",
        title="스캔 PDF 의 표 정합성 (셀이 밀리거나 합쳐질 때)",
        keywords=("스캔", "scan", "pdf", "표", "table", "정합성", "격자", "ocr"),
        formats=("pdf",),
        note="벡터 도형이 없다. `vector_grid`·`grid_restore` 는 여기서 못 "
             "돈다 — 선을 **화소에서** 찾는 `scan_grid` 가 그 자리를 맡는다. "
             "증상이 '셀에 글자가 잘못 들어간다' 면 cell_match, '행·열 수가 "
             "다르다' 면 scan_grid·grid_rebuild 를 먼저 본다.",
        stops=(
            Stop("converters/pdf/scanned.py",
                 "이 PDF 를 스캔본으로 볼지 정한다. 여기가 틀리면 아래 전부가 어긋난다", 2),
            Stop("images/page_render.py", "표 판정·판독에 쓸 쪽 PNG 를 배율별로 그린다", 2),
            Stop("converters/pdf/rapidocr_ko.py",
                 "쪽 이미지 → OcrLine(글자·신뢰도·**좌표**). 좌표가 아래 전부의 재료다", 3),
            Stop("converters/pdf/cell_match.py",
                 "OCR 조각을 셀 bbox 에 귀속시킨다 — **글자가 엉뚱한 칸에 들어가면 여기**"),
            Stop("experiments/tsr/measure/scan_grid.py",
                 "스캔 지면에서 선을 찾아 격자를 만든다 (벡터 대신)", 5),
            Stop("experiments/tsr/measure/grid_score.py",
                 "격자들이 서로 얼마나 맞는지 잰다 — 고치기 전에 재는 자리", 5),
            Stop("experiments/tsr/restore/lattice_fill.py",
                 "격자 결함이 줄 때만 표를 다시 세운다. 기각 사유는 fill_gate", 5),
            Stop("tables/grid_rebuild.py",
                 "OCR 좌표만으로 표를 다시 세운다 — **행·열을 통째로 놓쳤을 때**", 6),
            Stop("tables/vlm_rebuild.py", "그래도 안 되면 VLM 후보를 만들어 grade 로 고른다", 6),
            Stop("structuring/checks.py",
                 "격자 결함·셀 오염을 매번 잰다(실험 아님). 고친 값은 여기서 확인된다", 8),
        ),
    ),
    Topic(
        key="text-pdf-table",
        title="텍스트 PDF 의 표 정합성",
        keywords=("텍스트", "text", "pdf", "표", "table", "정합성", "격자", "벡터"),
        formats=("pdf",),
        note="스캔본과 갈리는 지점은 **재료**다. 여기는 글자 좌표와 벡터 "
             "도형이 둘 다 있어 근거가 가장 많다. 한글 내보내기 PDF 는 셀 "
             "배경이 사각형으로 남아 `vector_grid` 가 잘 듣는다.",
        stops=(
            Stop("converters/pdf/converter.py", "Docling 이 표를 뽑는다", 1),
            Stop("tables/docling.py", "Docling 표 → GFM · cell_grid (머리 전파 규칙)", 1),
            Stop("converters/pdf/text_runs.py", "글자·단어 좌표 — 격자 복원의 재료"),
            Stop("experiments/tsr/measure/vector_grid.py", "도형(셀 배경 사각형)에서 격자를 복원", 5),
            Stop("experiments/tsr/measure/line_grid.py", "괘선·모서리로 격자를 합성", 5),
            Stop("experiments/tsr/restore/grid_restore.py",
                 "사각형 격자가 표를 다 덮으면 표를 다시 쓴다", 5),
            Stop("experiments/tsr/restore/lattice_restore.py", "괘선 격자로 열 밀림을 바로잡는다", 5),
            Stop("experiments/tsr/restore/head_grid.py", "머리행 계층을 복원", 5),
            Stop("structuring/checks.py", "격자 결함·셀 오염 상시 검사", 8),
        ),
    ),
    Topic(
        key="hwpx-table",
        title="HWPX 의 표 (셀은 정확한데 순서·병합이 이상할 때)",
        keywords=("hwpx", "표", "table", "병합", "순서", "중첩"),
        formats=("hwpx",),
        note="셀 구조는 XML 이 그대로 준다 — **격자 실험이 필요 없고 돌지도 "
             "않는다.** 여기서 나는 문제는 보통 '읽는 순서' 다: 글자 취급 "
             "표(inline)를 제자리에 두는 일은 아직 안 됐다(향후 과제).",
        stops=(
            Stop("converters/hwpx/hwpxtree.py",
                 "zip+XML 을 직접 걷는다. 표는 `_read_table`, 순서는 `_walk`", 1),
            Stop("extractors/hwpx.py", "markdown → PageContent·TableInfo(cells 포함)", 1),
            Stop("tables/markdown.py", "표를 `<table N>` 블록으로 바꾼다", 1),
            Stop("experiments/tsr/restore/hole_fill.py",
                 "격자 구멍을 빈 칸으로 메운다 — HWPX 에서도 도는 몇 안 되는 실험", 5),
            Stop("structuring/checks.py", "격자 결함·셀 오염 상시 검사", 8),
        ),
    ),
    Topic(
        key="hwp-table",
        title="HWP 의 표 (왜 격자 검사가 안 도는가)",
        keywords=("hwp", "표", "table", "pyhwp", "사다리"),
        formats=("hwp",),
        note="**HWP 는 `cells` 를 만들지 못한다.** 사다리가 markdown 만 "
             "만들기 때문이다. 그래서 격자 검사·오염 검사·hole_fill 이 "
             "조용히 비켜 간다 — 실험_총정리 §6 이 말하는 가장 큰 공백이다. "
             "여기를 메우려면 `hwp5tree` 가 표를 읽을 때 cells 를 함께 "
             "내도록 해야 한다.",
        stops=(
            Stop("converters/hwp/converter.py", "6단 사다리 — 어느 단으로 읽을지 정한다", 1),
            Stop("converters/hwp/pyhwp_backend/hwp5tree.py",
                 "pyhwp 트리 → markdown. **cells 를 만들 자리** "
                 "(AGPL — 이 폴더는 떼어낼 수 있다)", 1),
            Stop("converters/hwp/marks.py", "쪽 나눔 표식 — 백엔드를 떼어내도 남는다"),
            Stop("converters/hwp/diagnose.py", "읽을 수 있는 파일인지 먼저 가린다", 1),
            Stop("extractors/hwp.py", "사다리 결과 → PageContent (fallback_reason 기록)", 1),
        ),
    ),
    Topic(
        key="page-align",
        title="HWPX 에 PDF 쪽 번호를 물려주기 (쪽이 안 맞을 때)",
        keywords=("hwpx", "pdf", "쪽", "페이지", "align", "맞춤", "정렬"),
        formats=("hwpx", "pdf"),
        note="HWPX 에는 쪽 경계가 없다. 본문 눈금이 주력, 표 짝짓기가 보완, "
             "사이는 비례로 채운다. 못 맞춘 표는 버리지 않고 사유와 함께 "
             "`unmatched` 로 낸다 — **쪽을 모르는 것과 없는 것은 다르다.**",
        stops=(
            Stop("align/documents.py", "진입점 `align_documents`. 성적은 맞출 수 있는 표만으로 잰다"),
            Stop("align/page_map.py", "눈금 찾기·병합·보간·쪽 나누기의 기계"),
            Stop("converters/hwpx/hwpxtree.py", "숨은 글을 걸러 낸다 — 이것이 열 수를 흔든다", 1),
            Stop("images/tags.py",
                 "`strip_image_blocks` 로 판독 내용을 뺀 본문만 눈금으로 쓸 수 있다"),
        ),
    ),
    Topic(
        key="scan-text",
        title="스캔 PDF 의 본문 글자 (오독·누락)",
        keywords=("스캔", "scan", "ocr", "본문", "글자", "오독", "vlm", "판독"),
        formats=("pdf",),
        stops=(
            Stop("converters/pdf/scanned.py", "스캔본 판별", 2),
            Stop("text/scan_vlm.py", "VLM 으로 쪽을 읽는다 (DOCSTRUCT_SCAN_BACKEND=vlm)", 3),
            Stop("converters/pdf/rapidocr_ko.py", "한국어 OCR 모델을 강제해 읽는다", 3),
            Stop("text/korean_text.py", "균등배분 복원·한컴 PUA 매핑"),
            Stop("text/ocr_verify.py", "읽은 것을 문맥으로 검증해 의심 자리를 짚는다 (바꾸지 않음)", 10),
            Stop("text/ocr_reread.py", "그 자리만 잘라 다시 읽힌다. 원본은 ocr_original 에", 10),
            Stop("experiments/text/scan_ab.py", "VLM 과 OCR 의 답을 대조해 잰다", 5),
        ),
    ),
    Topic(
        key="picture",
        title="그림 판독 (조직도·그래프가 비거나 잘못 읽힐 때)",
        keywords=("그림", "이미지", "image", "판독", "조직도", "그래프", "chart", "vlm"),
        formats=("pdf", "hwpx"),
        note="그림은 먼저 **정체**를 가린다(표/글/그래프/장식). 그 판정이 "
             "틀리면 뒤가 전부 어긋난다. 본문에서 그림의 몫은 `<image N> … "
             "</image N>` 안이다(0.4.89).",
        stops=(
            Stop("converters/pdf/text_probe.py", "영역 안 글자 밀도를 잰다 — 정체 판정의 재료"),
            Stop("converters/pdf/region_kind.py", "TABLE | TEXT | IMAGE 를 LLM 없이 가른다"),
            Stop("images/picture.py", "그림을 파일로 떨어뜨리고 본문에 블록을 남긴다", 1),
            Stop("images/native_image.py", "렌더 배율에 묶이지 않게 원본 화소로 잘라 낸다"),
            Stop("images/legibility.py", "글자 획 높이로 읽을 만한지 잰다 (dpi 가 아니다)"),
            Stop("images/image_prep.py", "보내기 전 다듬는다 (basic·full·lanczos·model)"),
            Stop("images/vlm_read.py", "VLM 으로 읽어 `<image-read>` 칸에 넣는다", 7),
            Stop("images/chart_read.py", "그래프 값을 읽고 본문 숫자와 대조", 4),
            Stop("images/chart_verify.py", "막대 화소 길이로 옮겨적기 오류를 잡는다"),
            Stop("images/tags.py", "`<image N>` 블록 — 설명(파서)과 내용(판독)을 가른다"),
            Stop("experiments/image/chart_gate.py", "판독 경로가 어떻게 갈렸는지 잰다", 5),
        ),
    ),
    Topic(
        key="table-llm",
        title="표 LLM 판정·재추출 (표가 그림으로 잡히거나 반대일 때)",
        keywords=("llm", "표", "판정", "재추출", "assess", "fill", "vlm"),
        formats=("pdf", "hwpx", "hwp"),
        stops=(
            Stop("tables/assess.py", "sufficient | wrong | insufficient 판정. LLM 없으면 판정 안 함", 9),
            Stop("tables/fill.py", "미달 표를 쪽 이미지에서 다시 뽑고, 그림 판정 표는 옮긴다", 9),
            Stop("tables/vlm_rebuild.py", "격자에 구멍이 있는 표를 VLM 후보로 다시 만든다", 6),
            Stop("tables/grade.py", "후보를 결정론으로 채점 — 심판"),
            Stop("infrastructure/llm/client.py", "엔드포인트·재시도·대비책. 여기가 비면 전부 '판정 안 함'"),
        ),
    ),
    Topic(
        key="outline",
        title="목차·제목 계층",
        keywords=("목차", "toc", "제목", "계층", "outline", "쪽번호"),
        formats=("pdf", "hwpx", "hwp"),
        stops=(
            Stop("outline/toc.py", "목차를 찾고 인쇄 쪽번호와의 차이를 잰다", 11),
            Stop("outline/builder.py", "제목 수준으로 의미 경로(1 > 1.2 > 가)를 세운다"),
        ),
    ),
    Topic(
        key="output",
        title="산출물 모양 (document.md · document.json 이 이상할 때)",
        keywords=("산출물", "출력", "json", "md", "document", "report", "블록"),
        formats=("pdf", "hwpx", "hwp"),
        note="`table.markdown` 이 권위다. `cells` 만 고치고 markdown 을 안 "
             "고치면 두 필드가 다른 말을 한다(0.4.83 의 병). 태그는 "
             "파이프라인 안에서만 살고 산출물에서는 펼쳐진다.",
        stops=(
            Stop("output/report.py", "document.md · document.json · tables.md · pipeline.md 를 쓴다", 12),
            Stop("output/content.py", "`<table N>`·`<image N>` 블록을 실제 내용으로 펼친다"),
            Stop("tables/tags.py", "표 블록 생성·파싱·동기화"),
            Stop("images/tags.py", "그림 블록 생성·파싱·동기화"),
            Stop("models.py", "결과 모델 — 필드를 더하면 여기부터"),
        ),
    ),
    Topic(
        key="experiment",
        title="실험을 새로 만들거나 승격하기",
        keywords=("실험", "experiment", "승격", "registry", "exp"),
        formats=("pdf", "hwpx", "hwp"),
        note="재기만 하는 실험은 `tsr/measure`·`image`·`text` 에, 표를 "
             "바꾸는 실험은 `tsr/restore` 에 둔다. 폴더가 그 약속을 말한다.",
        stops=(
            Stop("experiments/registry.py", "등록·기본 켬(DEFAULT_ON)·형식 게이트"),
            Stop("experiments/report.py", "`--exp list` 출력"),
            Stop("pipeline.py", "구간 5(표)·구간 8 뒤(그림)에서 등록 순서대로 돈다", 5),
        ),
    ),
    Topic(
        key="settings",
        title="설정·환경변수·성능",
        keywords=("설정", "환경변수", "config", "성능", "동시", "속도", "타임아웃"),
        formats=("pdf", "hwpx", "hwp"),
        stops=(
            Stop("core/config.py", "환경변수·.env·사이트 기본값 → Settings"),
            Stop("core/checks.py", "`docstruct --check` — 엔드포인트에 실제로 닿는지 본다"),
            Stop("api.py", "설정 적용 컨텍스트(`_applied`)와 파사드"),
            Stop("pipeline.py", "단계별 시간·동시 실행 수 기록 (`_counted`·`_log_timings`)", 12),
        ),
    ),
)


#: local·overlay 트리에서 **최상위로 승격되는** 폴더 (tools/sync_trees.py).
#: 서버는 `python -m experiments.report` 처럼 부르므로 이름이 계약이다.
_PROMOTED = ("converters", "core", "infrastructure", "experiments")


def resolve_stop(path: str) -> "pathlib.Path | None":
    """길잡이의 상대 경로를 이 트리의 실제 파일로 푼다.

    입력: path — `converters/pdf/scanned.py` 같은 상대 경로
    출력: 있으면 Path, 없으면 None
    비고:
        pkg 는 전부 `docstruct/` 아래 있지만 local·overlay 는 승격 폴더
        넷이 **최상위**로 나간다. 길잡이는 한 벌이므로 여기서 두 자리를
        모두 본다 — 트리마다 다른 표를 두면 반드시 어긋난다.
    """
    import pathlib

    import docstruct

    package = pathlib.Path(docstruct.__file__).parent
    inside = package / path
    if inside.exists():
        return inside
    if path.split("/", 1)[0] in _PROMOTED:
        promoted = package.parent / path
        if promoted.exists():
            return promoted
    return None


def find_topics(query: str) -> list[Topic]:
    """키워드로 관심사를 찾는다.

    입력: query — 사람이 친 말 (예: "스캔 pdf 표")
    출력: 맞은 개수 순 Topic 목록 (없으면 빈 목록)
    비고:
        띄어쓴 말마다 키워드·제목을 본다. 한 낱말만 맞아도 내주되 **많이
        맞은 것을 앞에** 둔다 — "pdf" 만 쳐도 뭔가 나오는 편이, 정확히
        치지 않으면 아무것도 안 나오는 편보다 낫다.
    """
    words = [w for w in (query or "").lower().replace(",", " ").split() if w]
    if not words:
        return list(TOPICS)
    scored: list[tuple[int, Topic]] = []
    for topic in TOPICS:
        hay = " ".join((topic.key, topic.title, *topic.keywords)).lower()
        score = sum(1 for w in words if w in hay)
        if score:
            scored.append((score, topic))
    scored.sort(key=lambda pair: -pair[0])
    return [topic for _score, topic in scored]


def format_topic(topic: Topic) -> str:
    """관심사 하나를 사람이 읽을 글로.

    입력: topic — Topic
    출력: 여러 줄 문자열
    """
    lines = [f"■ {topic.title}"]
    if topic.formats:
        lines.append(f"  형식: {'·'.join(topic.formats)}")
    if topic.note:
        lines.append("")
        for chunk in _wrap(topic.note, 74):
            lines.append(f"  {chunk}")
    lines.append("")
    lines.append("  거치는 자리 (파이프라인 순서):")
    for stop in topic.stops:
        phase = f"[{stop.phase:>2}] " if stop.phase is not None else "     "
        lines.append(f"    {phase}{stop.path}")
        lines.append(f"          {stop.does}")
    return "\n".join(lines)


def _wrap(text: str, width: int) -> list[str]:
    """긴 글을 폭에 맞춰 자른다 (한글 폭은 고려하지 않는 대략치).

    입력: text — 글, width — 폭
    출력: 줄 목록
    """
    out: list[str] = []
    line = ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out
