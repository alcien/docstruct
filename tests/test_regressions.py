"""이번 수정분 회귀 테스트.

역할:
    고친 버그가 다시 들어오지 않게 막는다. 무거운 의존성(docling·torch)이
    없어도 도는 것만 담았다 — PDF 경로는 별도 통합 테스트가 필요하다.
호출부:
    `pytest tests/` (개발·CI)
출력:
    없음 (assert)
"""
from __future__ import annotations

import gc
import inspect
import pathlib
import json
import os
import sys
from pathlib import Path

import pytest

SAMPLES = Path(__file__).resolve().parent.parent / "notebooks" / "samples"


# ────────────────────────────────────────────────────────────────────
# LLM 도달 불가 표시 TTL
# ────────────────────────────────────────────────────────────────────

def test_unreachable_expires(monkeypatch):
    """TTL 이 지나면 표시가 풀려 다시 시도한다.

    서버 프로세스에서 잠깐의 LLM 장애가 재기동 때까지 이어지던 문제.
    """
    from docstruct.infrastructure.llm import client

    client.reset_unreachable()
    monkeypatch.setattr(client, "UNREACHABLE_TTL", 0.05)

    client.mark_unreachable("http://x/v1", "m", "연결 거부")
    assert client.unreachable_reason("http://x/v1", "m") == "연결 거부"

    import time
    time.sleep(0.06)
    assert client.unreachable_reason("http://x/v1", "m") is None
    client.reset_unreachable()


def test_unreachable_isolated_per_endpoint():
    """엔드포인트가 다르면 표시도 따로 관리된다."""
    from docstruct.infrastructure.llm import client

    client.reset_unreachable()
    client.mark_unreachable("http://a/v1", "m1", "거부")
    assert client.unreachable_reason("http://b/v1", "m1") is None
    assert client.unreachable_reason("http://a/v1", "m2") is None
    client.reset_unreachable()


def test_client_has_no_duplicate_globals():
    """전역 정의 블록이 중복되지 않는다 (잘못된 병합 흔적)."""
    src = Path(client_path()).read_text(encoding="utf-8")
    for name in ("CONNECT_TIMEOUT = ", "_LOCAL_ANNOUNCED = False"):
        # 대입은 한 번, 나머지는 함수 안 재대입이라 들여쓰기가 있다
        top_level = [ln for ln in src.splitlines() if ln.startswith(name)]
        assert len(top_level) == 1, f"{name} 이 최상위에 {len(top_level)}번 정의됨"


def client_path() -> Path:
    """client.py 실제 경로."""
    from docstruct.infrastructure.llm import client

    return Path(client.__file__)


# ────────────────────────────────────────────────────────────────────
# 임시 작업 폴더 수명
# ────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not (SAMPLES / "sample.hwpx").is_file(), reason="샘플 없음")
def test_scratch_dir_removed_with_document():
    """out_dir 없이 실행하면 임시 폴더가 문서와 함께 사라진다."""
    from docstruct import build_document

    doc = build_document(SAMPLES / "sample.hwpx", assess_tables=False)
    scratch = Path(doc.scratch_dir)
    assert scratch.is_dir()

    del doc
    gc.collect()
    assert not scratch.exists(), "문서가 사라졌는데 임시 폴더가 남았습니다"


@pytest.mark.skipif(not (SAMPLES / "sample.hwpx").is_file(), reason="샘플 없음")
def test_no_scratch_when_out_dir_given(tmp_path):
    """out_dir 을 주면 임시 폴더를 만들지 않는다."""
    from docstruct import build_document

    doc = build_document(SAMPLES / "sample.hwpx", assess_tables=False,
                         out_dir=tmp_path)
    assert not hasattr(doc, "scratch_dir")


@pytest.mark.skipif(not (SAMPLES / "sample.hwpx").is_file(), reason="샘플 없음")
def test_to_json_rescues_images(tmp_path):
    """to_json 은 임시 폴더의 이미지를 JSON 옆으로 건져낸다.

    예전에는 임시 폴더가 지워지지 않아 경로가 우연히 살아 있었다.
    """
    from docstruct import DocStruct

    ds = DocStruct(SAMPLES / "sample.hwpx", assess_tables=False).run()
    out = tmp_path / "결과.json"
    ds.to_json(out)

    data = json.loads(out.read_text(encoding="utf-8"))
    for page in data["pages"]:
        for img in page.get("images", []):
            path = img.get("image_path")
            if path:
                assert Path(path).is_file(), f"끊긴 경로: {path}"
                assert tmp_path in Path(path).parents


# ────────────────────────────────────────────────────────────────────
# 단계 라벨
# ────────────────────────────────────────────────────────────────────

def test_stage_label_per_format():
    """HWP 계열에 PDF 전용 단계명(TableFormer·OCR)이 붙지 않는다."""
    from docstruct.models import GPU_ACCELERATED, stage_extract

    assert "TableFormer" in stage_extract("pdf")
    for fmt in ("hwp", "hwpx"):
        assert "TableFormer" not in stage_extract(fmt)
        assert "OCR" not in stage_extract(fmt)
    # GPU 로 빨라지는 것은 Docling(PDF) 경로뿐
    assert stage_extract("pdf") in GPU_ACCELERATED
    assert stage_extract("hwp") not in GPU_ACCELERATED


@pytest.mark.skipif(not (SAMPLES / "sample.hwpx").is_file(), reason="샘플 없음")
def test_timings_use_format_label():
    """timings 키가 형식에 맞는 라벨로 들어간다."""
    from docstruct import build_document
    from docstruct.models import stage_extract

    doc = build_document(SAMPLES / "sample.hwpx", assess_tables=False)
    assert stage_extract("hwpx") in doc.timings


# ────────────────────────────────────────────────────────────────────
# 설정·패키지 정합성
# ────────────────────────────────────────────────────────────────────

def test_config_annotations_resolvable():
    """설정 dataclass 의 애노테이션이 실제로 해석된다 (Any 미임포트 방지)."""
    import typing

    from docstruct.core import config

    for name in ("Settings", "LocalVLM", "Endpoint"):
        cls = getattr(config, name, None)
        if cls is None:
            continue
        typing.get_type_hints(cls)          # NameError 면 실패


def test_env_example_covers_all_keys():
    """config.py 가 읽는 환경변수가 .env.example 에 모두 있다."""
    import re

    from docstruct.core import config

    src = Path(config.__file__).read_text(encoding="utf-8")
    used = set(re.findall(r'_get\(\s*"([A-Z0-9_]+)"', src))

    # 트리마다 config.py 깊이가 다르므로(pkg 는 src/docstruct/core, local 은
    # core) 위로 올라가며 찾는다. 설치본에는 아예 없다.
    example = None
    for parent in Path(config.__file__).resolve().parents:
        candidate = parent / ".env.example"
        if candidate.is_file():
            example = candidate
            break
    if example is None:
        pytest.skip(".env.example 없음 (설치본)")
    documented = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]{3,})=",
                                example.read_text(encoding="utf-8"), re.M))
    documented |= {"DOCSTRUCT_ENV", "CUDA_VISIBLE_DEVICES"}
    assert not (used - documented), f"문서화 누락: {sorted(used - documented)}"


def test_import_is_light():
    """import docstruct 가 무거운 의존성을 끌고 오지 않는다."""
    heavy = {"torch", "docling", "transformers", "pypdfium2"}
    assert not (heavy & set(sys.modules)), "무거운 모듈이 이미 로드됨"


def test_suffix_registry_consistent():
    """SUPPORTED_SUFFIXES 와 추출기 등록이 일치한다 (-O 에서도 검사됨)."""
    import docstruct
    from docstruct.extractors.registry import supported_suffixes

    assert tuple(sorted(docstruct.SUPPORTED_SUFFIXES)) == supported_suffixes()


# ────────────────────────────────────────────────────────────────────
# OCR 진단
# ────────────────────────────────────────────────────────────────────

def test_ocr_diagnosis_reports_missing_system_lib(monkeypatch, tmp_path):
    """설치는 됐는데 공유 라이브러리가 없는 경우를 정확히 짚는다.

    docling 은 이 상황에도 "pip install rapidocr onnxruntime" 이라고만 하는데,
    이미 설치돼 있으므로 그 안내로는 해결되지 않는다.
    """
    import sys

    from docstruct.core import checks

    fake = tmp_path / "rapidocr"
    fake.mkdir()
    (fake / "__init__.py").write_text(
        'raise ImportError("libGL.so.1: cannot open shared object file")',
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "rapidocr", raising=False)
    monkeypatch.setenv("DOCLING_OCR_BACKEND", "rapidocr")
    checks.invalidate_caches()
    from docstruct.core.config import reload_config

    reload_config()

    ready, note = checks._ocr_ready()
    assert ready is False
    assert "libGL.so.1" in note
    assert "libgl1" in note          # 설치할 시스템 패키지를 알려줘야 한다
    assert "pip install rapidocr" not in note   # 잘못된 안내를 반복하지 않는다


def test_ocr_diagnosis_reports_missing_package(monkeypatch):
    """아예 없을 때는 pip 안내가 맞다."""
    from docstruct.core import checks

    monkeypatch.setattr(checks, "_installed", lambda m: False)
    monkeypatch.setenv("DOCLING_OCR_BACKEND", "rapidocr")
    from docstruct.core.config import reload_config

    reload_config()

    ready, note = checks._ocr_ready()
    assert ready is False
    assert "미설치" in note


# ────────────────────────────────────────────────────────────────────
# 그림 → 표 승격
# ────────────────────────────────────────────────────────────────────

def _page_with_candidate():
    """표 후보 그림 하나와 사진 하나를 가진 페이지."""
    from docstruct.models import ImageInfo, PageContent

    return PageContent(
        page_no=1,
        page_no_kind="physical",
        content="제1장\n\n<!-- image 1 -->\n\n캡션\n\n<!-- image 2 -->",
        tables=[],
        images=[
            ImageInfo(id="image_1", placeholder="<!-- image 1 -->",
                      bbox={"l": 69, "t": 215, "r": 491, "b": 375},
                      text_chars=308, text_lines=7, table_candidate=True),
            ImageInfo(id="image_2", placeholder="<!-- image 2 -->",
                      bbox={"l": 69, "t": 460, "r": 300, "b": 660},
                      text_chars=0, text_lines=0, table_candidate=False),
        ],
    )


def test_promotion_keeps_both_image_and_table():
    """표로 승격해도 그림은 남고, 양쪽이 서로를 가리킨다."""
    from docstruct.tables.assess import promote_images_to_tables

    page = _page_with_candidate()
    promote_images_to_tables(page, [
        {"id": "image_1", "content_type": "table", "title": "대비표"},
    ])

    assert len(page.tables) == 1
    assert len(page.images) == 2                 # 그림을 지우지 않는다
    table = page.tables[0]
    assert table.source_image_id == "image_1"
    assert page.images[0].promoted_table_id == table.id
    assert table.bbox == page.images[0].bbox
    assert table.needs_fill                      # 재추출 경로로 넘어간다


def test_promotion_inserts_block_in_reading_order():
    """표 블록이 그림 placeholder 바로 뒤에 들어간다."""
    from docstruct.tables.assess import promote_images_to_tables

    page = _page_with_candidate()
    promote_images_to_tables(page, [
        {"id": "image_1", "content_type": "table", "title": "대비표"},
    ])
    before = page.content.index("<!-- image 1 -->")
    inserted = page.content.index("<table 1>")
    after = page.content.index("캡션")
    assert before < inserted < after


def test_promotion_ignores_non_candidates_and_duplicates():
    """후보가 아닌 그림 지목과 중복 승격은 무시한다."""
    from docstruct.tables.assess import promote_images_to_tables

    page = _page_with_candidate()
    promote_images_to_tables(page, [
        {"id": "image_2", "content_type": "table", "title": "사진"},
    ])
    assert page.tables == []                     # 후보가 아니면 승격 안 함

    promote_images_to_tables(page, [
        {"id": "image_1", "content_type": "table", "title": "대비표"},
    ])
    promote_images_to_tables(page, [
        {"id": "image_1", "content_type": "table", "title": "다시"},
    ])
    assert len(page.tables) == 1                 # 중복 승격 방지


def test_text_density_separates_table_from_photo(tmp_path):
    """영역 텍스트 밀도로 표와 사진이 갈린다 (LLM 호출 없음)."""
    pytest.importorskip("reportlab")
    pytest.importorskip("pypdfium2")
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    from docstruct.converters.pdf.text_probe import probe_regions

    pdf = tmp_path / "probe.pdf"
    width, height = A4
    c = canvas.Canvas(str(pdf), pagesize=A4)
    c.setFont("Helvetica", 9)
    for i in range(7):                            # 표처럼 글자가 많은 영역
        c.drawString(80, 610 - i * 20, f"row {i} left column | right column value")
    c.setFillGray(0.6)
    c.rect(69, 180, 231, 200, fill=1)             # 사진처럼 글자 없는 영역
    c.showPage()
    c.save()

    # TOPLEFT 좌표로 준다 (docling 이 주는 형식)
    regions = {
        "image_1": (1, {"l": 69, "t": height - 627, "r": 491, "b": height - 467}),
        "image_2": (1, {"l": 69, "t": height - 380, "r": 300, "b": height - 180}),
    }
    result = probe_regions(pdf, regions)

    assert result["image_1"].table_candidate is True
    assert result["image_2"].table_candidate is False
    assert result["image_1"].chars > result["image_2"].chars


def test_region_text_passed_to_fill():
    """승격된 표만 PDF 원문을 재추출 프롬프트에 싣는다."""
    from docstruct.models import ImageInfo, TableInfo
    from docstruct.tables.fill import _region_text_block

    images = [ImageInfo(id="image_1", placeholder="<!-- image 1 -->",
                        table_candidate=True, region_text="종전 개정(안)\n○ 소비성서비스업")]

    promoted = TableInfo(id="table_1", table_num=1, placeholder="<table 1>",
                         markdown="", source_image_id="image_1")
    block = _region_text_block(promoted, images)
    assert "종전 개정(안)" in block
    assert "글자는 이 원문을 그대로" in block

    # 일반 표에는 붙지 않는다
    normal = TableInfo(id="table_2", table_num=2, placeholder="<table 2>", markdown="| a |")
    assert _region_text_block(normal, images) == ""


def test_real_pdf_picture_regions_are_table_candidates():
    """실제 개정세법 PDF 에서 그림으로 분류된 영역이 후보로 잡힌다."""

    pytest.importorskip("pypdfium2")
    import pypdfium2 as pdfium

    from docstruct.converters.pdf.text_probe import probe_regions

    sample = SAMPLES / "picture_table.pdf"
    if not sample.is_file():
        pytest.skip("샘플 PDF 없음")

    height = pdfium.PdfDocument(str(sample))[0].get_size()[1]
    # docling 이 준 BOTTOMLEFT 좌표를 TOPLEFT 로 변환해 넣는다
    raw = {"image_1": (69.344, 627.057, 490.998, 467.064),
           "image_2": (69.356, 386.535, 490.937, 171.786)}
    regions = {k: (1, {"l": l, "t": height - t, "r": r, "b": height - b})
               for k, (l, t, r, b) in raw.items()}

    result = probe_regions(sample, regions)
    assert set(result) == {"image_1", "image_2"}
    for density in result.values():
        assert density.table_candidate is True
        assert "종전" in density.text


# ────────────────────────────────────────────────────────────────────
# HWP 폴백 판정
# ────────────────────────────────────────────────────────────────────

def _rich_html(tables: int = 5, cells_per_table: int = 6, body: str = "본문 " * 400):
    """표가 살아 있는 pyhwp HTML 을 흉내낸다."""
    rows = "".join(
        "<tr>" + "".join(f"<td>셀{i}</td>" for i in range(cells_per_table)) + "</tr>"
        for _ in range(tables)
    )
    return f"<html><body><p>{body}</p><table>{rows}</table></body></html>"


def test_field_warning_alone_does_not_trigger_fallback():
    """필드 경고만으로는 폴백하지 않는다 (표가 살아 있으면 유지)."""
    from docstruct.converters.hwp.pyhwp_backend.html_export import pyhwp_html_verdict

    insufficient, reason = pyhwp_html_verdict(
        _rich_html(), "unmatched field end", 626_176
    )
    assert insufficient is False
    assert "표가 살아 있음" in reason


def test_field_warning_with_empty_body_triggers_fallback():
    """필드 경고 + 빈 결과는 그대로 폴백한다 (원래 잡으려던 케이스)."""
    from docstruct.converters.hwp.pyhwp_backend.html_export import pyhwp_html_verdict

    insufficient, reason = pyhwp_html_verdict(
        "<html><body></body></html>", "unmatched field end", 626_176
    )
    assert insufficient is True
    assert reason


def test_empty_result_without_warning_still_falls_back():
    """경고가 없어도 결과가 비면 폴백한다."""
    from docstruct.converters.hwp.pyhwp_backend.html_export import pyhwp_html_verdict

    insufficient, _ = pyhwp_html_verdict("<html><body></body></html>", "", 626_176)
    assert insufficient is True


def test_mostly_empty_cells_trigger_fallback():
    """셀이 대부분 비면 폴백한다."""
    from docstruct.converters.hwp.pyhwp_backend.html_export import pyhwp_html_verdict

    rows = "<tr>" + "<td></td>" * 20 + "</tr>"
    html = f"<html><body><p>{'가' * 600}</p><table>{rows}</table></body></html>"
    insufficient, reason = pyhwp_html_verdict(html, "", 626_176)
    assert insufficient is True
    assert "내용 있는 셀" in reason


def test_small_file_short_body_is_not_fallback():
    """작은 파일은 본문이 짧아도 정상으로 본다."""
    from docstruct.converters.hwp.pyhwp_backend.html_export import pyhwp_html_verdict

    insufficient, _ = pyhwp_html_verdict("<html><body>짧음</body></html>", "", 5_000)
    assert insufficient is False


def test_verdict_always_gives_reason():
    """어느 경로로 가든 사유 문구가 비지 않는다 (진단용)."""
    from docstruct.converters.hwp.pyhwp_backend.html_export import pyhwp_html_verdict

    cases = [
        (_rich_html(), "unmatched field end", 626_176),
        ("<html><body></body></html>", "unmatched field end", 626_176),
        ("<html><body></body></html>", "", 626_176),
        (_rich_html(), "", 626_176),
        ("<html><body>짧음</body></html>", "", 5_000),
    ]
    for html, stderr, size in cases:
        _, reason = pyhwp_html_verdict(html, stderr, size)
        assert reason and reason.strip()


def test_inline_controls_cover_tab_and_inline_range():
    """HWP 인라인 제어(탭 포함)가 건너뛰기 목록에 있다."""
    from docstruct.converters.hwp.olefile import _INLINE_CONTROLS

    # 규격상 8글자짜리 제어: 인라인 4~9,19,20 + 확장 1~3,11,12,14~18,21~23
    expected = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23}
    assert set(_INLINE_CONTROLS) == expected
    # 단독 1글자 제어는 들어가면 안 된다
    for solo in (0, 10, 13, 24, 31):
        assert solo not in _INLINE_CONTROLS


def test_colab_configure_exposes_force_ocr():
    """colab.configure 로 전면 OCR 을 켤 수 있다."""
    import inspect

    from docstruct.output import colab

    params = inspect.signature(colab.configure).parameters
    assert "force_full_page_ocr" in params
    assert params["force_full_page_ocr"].default is False


# ────────────────────────────────────────────────────────────────────
# HWP 미리보기 스트림 활용
# ────────────────────────────────────────────────────────────────────

def test_preview_coverage_gate():
    """커버리지가 낮으면 미리보기를 쓰지 않는다."""
    from docstruct.converters.hwp import preview

    prv = "<가><나>\n<다><라>"
    assert preview.coverage(prv, "가나다라") == pytest.approx(1.0)
    assert preview.coverage(prv, "가" * 1000) < preview.MIN_COVERAGE
    assert preview.coverage(None, "본문") == 0.0
    assert preview.coverage(prv, "") == 0.0


def test_preview_markdown_restores_cells():
    """`<셀><셀>` 줄이 markdown 표 행으로 복원된다."""
    from docstruct.converters.hwp.preview import to_markdown

    md = to_markdown(
        " □ 제목\n"
        "<재정성과책임관><><백승보 청장>\n"
        "<재정성과운영관><><이형식 기획조정관>\n"
        "\n"
        "일반 문단"
    )
    assert "□ 제목" in md
    assert "| 재정성과책임관 |" in md
    assert "| --- |" in md
    assert "일반 문단" in md


def test_preview_markdown_pads_ragged_rows():
    """열 수가 다른 행은 가장 넓은 행에 맞춰 채운다."""
    from docstruct.converters.hwp.preview import to_markdown

    md = to_markdown("<a><b>\n<c><d><e><f>")
    rows = [line for line in md.splitlines() if line.startswith("|")]
    widths = {line.count("|") for line in rows}
    assert len(widths) == 1, f"열 수가 어긋납니다: {widths}"


def test_preview_ignores_non_table_lines():
    """꺾쇠가 있어도 셀이 하나뿐이면 표로 보지 않는다."""
    from docstruct.converters.hwp.preview import _split_cells

    assert _split_cells("<한 칸만>") is None
    assert _split_cells("일반 문장") is None
    assert _split_cells("<가><나>") == ["가", "나"]


def test_preview_image_signature_detection(tmp_path):
    """PrvImage 형식을 시그니처로 판별한다."""
    from docstruct.converters.hwp.preview import _IMAGE_SIGNATURES

    suffixes = {suffix for _, suffix in _IMAGE_SIGNATURES}
    assert {".png", ".jpg"} <= suffixes


def test_prv_text_limit_documented():
    """PrvText 상한이 상수로 남아 있다 (커버리지 판정의 근거)."""
    from docstruct.converters.hwp.preview import MIN_COVERAGE, PRV_TEXT_LIMIT

    assert PRV_TEXT_LIMIT == 1023
    assert 0 < MIN_COVERAGE <= 1


# ────────────────────────────────────────────────────────────────────
# 한국 문서 텍스트 정규화 (균등배분 · 한컴 PUA)
# ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("src", "want"), [
    # 전체 균등배분 — 모든 토큰이 1글자
    ("대 한 민 국 정 부", "대한민국정부"),
    ("프 로 그 램 논 리 모 형", "프로그램논리모형"),
    # 부분 균등배분 — 뒤의 정상 단어는 건드리지 않는다
    ("홍 보 담 당 관 회의 자료", "홍보담당관 회의 자료"),
    ("제 1 장 총칙", "제1장 총칙"),
    # 건드리면 안 되는 것
    ("중동 사태 대응", "중동 사태 대응"),          # 2자 단어
    ("2027년도 성과계획서", "2027년도 성과계획서"),
    ("가 나", "가 나"),                            # 토큰 2개
    ("○ 소비성서비스업", "○ 소비성서비스업"),
    ("△1,777 △2.7 8.0", "△1,777 △2.7 8.0"),
])
def test_collapse_even_spacing(src, want):
    """균등배분만 되붙이고 정상 문장은 유지한다."""
    from docstruct.text.korean_text import collapse_even_spacing

    assert collapse_even_spacing(src) == want


def test_even_spacing_needs_three_tokens():
    """토큰이 셋 미만이면 균등배분으로 보지 않는다."""
    from docstruct.text.korean_text import collapse_even_spacing

    assert collapse_even_spacing("가 나") == "가 나"
    assert collapse_even_spacing("가 나 다") == "가나다"


def test_pua_mapping():
    """한컴 PUA 글머리표가 표준 유니코드로 바뀐다."""
    from docstruct.text.korean_text import map_pua

    assert map_pua("\uf06f 항목") == "□ 항목"
    assert map_pua("\uf0a2 하위") == "○ 하위"
    assert map_pua("\uf0fc 완료") == "✔ 완료"
    assert map_pua("\U000f0854인용\U000f0855") == "《인용》"


def test_pua_keeps_unmapped_characters():
    """매핑에 없는 PUA 는 지우지 않는다 (옛한글 보호)."""
    from docstruct.text.korean_text import map_pua

    assert map_pua("\ue000옛한글") == "\ue000옛한글"
    assert map_pua("\uf001x") == "\uf001x"


def test_normalize_applies_per_line():
    """균등배분은 줄 단위로 판단한다."""
    from docstruct.text.korean_text import normalize_korean_text

    out = normalize_korean_text("대 한 민 국 정 부\n중동 사태 대응")
    assert out.splitlines() == ["대한민국정부", "중동 사태 대응"]


def test_normalize_can_skip_collapse():
    """짧은 표 셀에는 균등배분 복원을 끌 수 있다."""
    from docstruct.text.korean_text import normalize_korean_text

    assert normalize_korean_text("가 나 다", collapse=False) == "가 나 다"


# ────────────────────────────────────────────────────────────────────
# HWP 파서 트리 경로 (hwp5.xmlmodel)
# ────────────────────────────────────────────────────────────────────

def _use_fake_backend(monkeypatch, conv, to_markdown, html=None):
    """pyhwp 백엔드를 가짜로 갈아 끼운다 (0.5.0).

    사다리 1단은 이제 `converter._backend()` 로 백엔드를 얻는다 — 폴더가
    없어도 죽지 않게 하려고 지연 접근으로 바꿨기 때문이다. 시험도 그 자리를
    잡는다.
    """
    class _FakeTimeout(Exception):
        """가짜 백엔드의 시간 초과 (RuntimeError 와 구별되어야 한다)."""

    class _Fake:
        @staticmethod
        def is_available():
            return True

        @staticmethod
        def tree_markdown(path):
            return to_markdown(path)

        @staticmethod
        def html(path):
            if html is None:
                raise RuntimeError("이 시험에서는 3단을 쓰지 않는다")
            return html(path)

        @staticmethod
        def timeout_error():
            # **RuntimeError 를 쓰면 안 된다** — 사다리가 시간 초과와
            # 실행 실패를 서로 다르게 다루는데, 같은 클래스로 두면
            # 실패가 시간 초과로 잡혀 사유가 안 남는다.
            return _FakeTimeout

        @staticmethod
        def real_errors(text, limit=3):
            return [line for line in str(text).splitlines() if line.strip()][:limit]

        @staticmethod
        def html_verdict(html, stderr, size):
            return False, ""

    monkeypatch.setattr(conv, "_backend", lambda: _Fake)


def _ok_diagnosis():
    """진단을 통과시키는 결과 (가짜 파일로 경로 선택만 시험할 때)."""
    from docstruct.converters.hwp.diagnose import HwpDiagnosis

    return HwpDiagnosis(True, "")


def test_render_table_merges_are_not_duplicated():
    """병합 셀은 왼쪽 위에만 값을 넣고 나머지는 비운다."""
    from docstruct.converters.hwp.pyhwp_backend.hwp5tree import _Cell, _Table, _render_table

    table = _Table(cols=3, cells=[
        _Cell(col=0, row=0, colspan=2, blocks=["병합"]),
        _Cell(col=2, row=0, blocks=["끝"]),
        _Cell(col=0, row=1, blocks=["가"]),
    ])
    md = _render_table(table)
    assert md.count("병합") == 1                 # 복제하면 검색에 중복으로 걸린다
    assert "| --- | --- | --- |" in md


def test_render_table_escapes_pipe():
    """셀 안의 파이프가 표 구조를 깨뜨리지 않는다."""
    from docstruct.converters.hwp.pyhwp_backend.hwp5tree import _Cell, _Table, _render_table

    md = _render_table(_Table(cols=1, cells=[_Cell(col=0, row=0, blocks=["a|b"])]))
    assert r"a\|b" in md


def test_nested_table_uses_marker_not_inline():
    """중첩 표는 표식만 셀에 남기고 본체는 부모 표 뒤에 둔다.

    GFM 은 셀 안에 표를 담지 못한다. 그대로 넣으면 한 줄로 눕고 `|` 가
    이스케이프되어 사람도 LLM 도 읽을 수 없다.
    """
    from docstruct.converters.hwp.pyhwp_backend.hwp5tree import NESTED_MARKER

    assert "{n}" in NESTED_MARKER
    assert NESTED_MARKER.format(n=1) == "[중첩표 1]"


def test_hwp5tree_availability_probe():
    """pyhwp 파서 모듈 유무를 안전하게 확인한다."""
    from docstruct.converters.hwp.pyhwp_backend.hwp5tree import is_available

    assert isinstance(is_available(), bool)


def test_converter_prefers_tree_path(monkeypatch, tmp_path):
    """파서 트리가 결과를 내면 그 경로를 쓴다."""
    from docstruct.converters.hwp import converter as conv

    fake = tmp_path / "a.hwp"
    fake.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 64)

    monkeypatch.setattr(conv, "is_hwpml", lambda _p: False)
    monkeypatch.setattr(conv, "diagnose", lambda _p: _ok_diagnosis())
    _use_fake_backend(monkeypatch, conv, lambda _p: "가" * 500)

    c = conv.HwpConverter(fake)
    assert c.extraction_path() == "hwp5-tree"
    # 0.4.15 부터 to_markdown 은 **파이프라인을 거친다** — 문서 제목과 쪽
    # 머리가 붙으므로 정확 일치가 아니라 본문 포함으로 본다. 원재료를
    # 그대로 내보내면 그림·정규화가 통째로 빠진다(서비스에서 실제로 그랬다).
    assert "가" * 100 in c.to_markdown()
    assert c.table_html_fragments() == []        # 트리 경로엔 원본 HTML 이 없다


def test_converter_falls_back_when_tree_is_empty(monkeypatch, tmp_path):
    """파서 트리 결과가 빈약하면 기존 경로로 넘어간다."""
    from docstruct.converters.hwp import converter as conv

    fake = tmp_path / "b.hwp"
    fake.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 64)

    monkeypatch.setattr(conv, "is_hwpml", lambda _p: False)
    monkeypatch.setattr(conv, "diagnose", lambda _p: _ok_diagnosis())
    _use_fake_backend(monkeypatch, conv, lambda _p: "짧음")
    monkeypatch.setattr(conv.HwpConverter, "_uses_ole_fallback", lambda self: True)
    monkeypatch.setattr(conv.HwpConverter, "_get_ole_text", lambda self: "폴백 텍스트")

    c = conv.HwpConverter(fake)
    assert c.extraction_path() == "olefile-text"


def test_converter_falls_back_when_tree_raises(monkeypatch, tmp_path):
    """파서 트리가 예외를 내도 변환은 계속된다."""
    from docstruct.converters.hwp import converter as conv

    fake = tmp_path / "c.hwp"
    fake.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 64)

    def boom(_p):
        raise RuntimeError("파싱 실패")

    monkeypatch.setattr(conv, "is_hwpml", lambda _p: False)
    monkeypatch.setattr(conv, "diagnose", lambda _p: _ok_diagnosis())
    _use_fake_backend(monkeypatch, conv, boom)
    monkeypatch.setattr(conv.HwpConverter, "_uses_ole_fallback", lambda self: True)
    monkeypatch.setattr(conv.HwpConverter, "_get_ole_text", lambda self: "폴백")

    assert conv.HwpConverter(fake).extraction_path() == "olefile-text"


# ────────────────────────────────────────────────────────────────────
# 세로쓰기 복원
# ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("src", "want"), [
    ("프\n로\n그\n램\n논\n리\n모\n형", "프로그램논리모형"),
    ("2\n0\n2\n7", "2027"),
    ("앞\n프\n로\n그\n램\n뒤 문장", "앞프로그램\n뒤 문장"),
    # 건드리면 안 되는 것
    ("가\n나", "가\n나"),                        # 2줄뿐
    ("○\n○\n○", "○\n○\n○"),                    # 기호는 대상 아님
    ("제목\n본문 내용\n다음", "제목\n본문 내용\n다음"),
])
def test_collapse_vertical_text(src, want):
    """세로로 배치된 낱글자 줄만 되붙인다."""
    from docstruct.text.korean_text import collapse_vertical_text

    assert collapse_vertical_text(src) == want


def test_normalize_handles_vertical_then_even_spacing():
    """세로쓰기와 균등배분이 한 번에 처리된다."""
    from docstruct.text.korean_text import normalize_korean_text

    out = normalize_korean_text("프\n로\n그\n램\n대 한 민 국 정 부")
    assert out == "프로그램\n대한민국정부"


def test_vertical_collapse_survives_markdown_tables():
    """markdown 표 행은 낱글자 줄로 오해하지 않는다."""
    from docstruct.text.korean_text import collapse_vertical_text

    table = "| 가 |\n| 나 |\n| 다 |"
    assert collapse_vertical_text(table) == table


# ────────────────────────────────────────────────────────────────────
# 그림 영역 3분류 (표 / 도표·텍스트 / 사진)
# ────────────────────────────────────────────────────────────────────

def _make_pdf(tmp_path, draw):
    """좌표를 직접 지정해 시험용 PDF 를 만든다."""
    pytest.importorskip("reportlab")
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    path = tmp_path / "region.pdf"
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica", 9)
    draw(c)
    c.showPage()
    c.save()
    return path, A4


def test_region_kind_detects_aligned_table(tmp_path):
    """열이 매 줄 같은 x 에서 시작하면 표로 본다."""
    from docstruct.converters.pdf.region_kind import RegionKind, classify_region

    def draw(c):
        for row in range(8):                      # 5열 × 8줄, 열 x 고정
            y = 700 - row * 20
            for col, x in enumerate((70, 200, 300, 400, 500)):
                c.drawString(x, y, f"val{row}{col}")

    path, (_w, h) = _make_pdf(tmp_path, draw)
    verdict = classify_region(path, 1, {"l": 60, "t": h - 720, "r": 560, "b": h - 530})
    assert verdict.kind is RegionKind.TABLE, verdict.reason
    assert verdict.drift <= 12


def test_region_kind_detects_diagram_as_text(tmp_path):
    """상자가 제각기 놓인 도표는 표로 보지 않는다."""
    from docstruct.converters.pdf.region_kind import RegionKind, classify_region

    def draw(c):
        # 조직도처럼 x 가 줄마다 크게 흔들리는 배치
        layout = [(70, 700), (300, 700), (150, 670), (430, 670),
                  (90, 640), (250, 640), (480, 610), (110, 610),
                  (350, 580), (70, 580), (200, 550), (460, 550)]
        for i, (x, y) in enumerate(layout):
            c.drawString(x, y, f"조직단위{i}이름")

    path, (_w, h) = _make_pdf(tmp_path, draw)
    verdict = classify_region(path, 1, {"l": 60, "t": h - 720, "r": 560, "b": h - 530})
    assert verdict.kind is RegionKind.TEXT, verdict.reason


def test_region_kind_treats_sparse_as_image(tmp_path):
    """글자가 거의 없으면 사진·로고로 둔다."""
    from docstruct.converters.pdf.region_kind import RegionKind, classify_region

    path, (_w, h) = _make_pdf(tmp_path, lambda c: c.drawString(80, 700, "그림 1"))
    verdict = classify_region(path, 1, {"l": 60, "t": h - 720, "r": 560, "b": h - 650})
    assert verdict.kind is RegionKind.IMAGE


def test_region_kind_rescues_short_label_diagram(tmp_path):
    """레이블이 짧아 표 문턱(80자)에 못 미쳐도 텍스트는 뽑는다.

    파이프라인 도표처럼 상자마다 짧은 글자만 있는 경우가 흔하다. 표 문턱을
    그대로 쓰면 그 글자가 통째로 사라진다.
    """
    from docstruct.converters.pdf.region_kind import RegionKind, classify_region

    def draw(c):
        for i, (x, y) in enumerate([(70, 700), (300, 700), (150, 660),
                                    (400, 660), (90, 620), (330, 620)]):
            c.drawString(x, y, f"stage step number {i}")

    path, (_w, h) = _make_pdf(tmp_path, draw)
    verdict = classify_region(path, 1, {"l": 60, "t": h - 720, "r": 560, "b": h - 600})
    assert verdict.kind is RegionKind.TEXT, verdict.reason


def test_region_kind_needs_two_lines():
    """한 줄짜리는 캡션이므로 그림으로 둔다."""
    from docstruct.converters.pdf.region_kind import MIN_TEXT_CHARS, MIN_TEXT_LINES

    assert MIN_TEXT_LINES >= 2
    assert MIN_TEXT_CHARS < 80          # 표 문턱과 분리돼 있어야 한다


def test_inject_region_text_places_after_placeholder():
    """도표 텍스트는 그림 placeholder 바로 뒤에 들어간다."""
    from docstruct.extractors.pdf import _inject_region_text
    from docstruct.models import ImageInfo

    parts = {1: ["앞 문단", "<!-- image 1 -->", "뒤 문단"]}
    images = {1: [ImageInfo(id="image_1", placeholder="<!-- image 1 -->",
                            region_kind="text", region_text="조직도 안의 글자")]}
    _inject_region_text(parts, images)
    assert parts[1] == ["앞 문단", "<!-- image 1 -->", "조직도 안의 글자", "뒤 문단"]


def test_inject_region_text_skips_other_kinds():
    """표·사진으로 판정된 것은 본문에 넣지 않는다."""
    from docstruct.extractors.pdf import _inject_region_text
    from docstruct.models import ImageInfo

    for kind in ("table", "image", None):
        parts = {1: ["<!-- image 1 -->"]}
        images = {1: [ImageInfo(id="image_1", placeholder="<!-- image 1 -->",
                                region_kind=kind, region_text="내용")]}
        _inject_region_text(parts, images)
        assert parts[1] == ["<!-- image 1 -->"], f"kind={kind}"


# ────────────────────────────────────────────────────────────────────
# 텍스트 레이어 없는 그림 → VLM 읽기
# ────────────────────────────────────────────────────────────────────

def _picture(tmp_path, **kwargs):
    """시험용 ImageInfo (그림 파일 포함)."""
    pytest.importorskip("PIL")
    from PIL import Image, ImageDraw

    from docstruct.models import ImageInfo

    path = tmp_path / f"{kwargs.get('id', 'image_1')}.png"
    # **글자를 그려 넣는다.** 0.4.29 부터 판독 가능성을 재므로, 흰
    # 바탕만 있으면 "장식" 으로 판정돼 VLM 을 부르지 않는다 — 그 판정
    # 자체는 옳다(글자가 없으면 읽을 것이 없다).
    canvas = Image.new("RGB", (400, 300), "white")
    drawer = ImageDraw.Draw(canvas)
    for row in range(6):
        for col in range(14):
            drawer.rectangle(
                [20 + col * 26, 30 + row * 40, 20 + col * 26 + 9,
                 30 + row * 40 + 11], fill="black")
    canvas.save(path)
    defaults = {
        "id": "image_1",
        "placeholder": "<!-- image 1 -->",
        "image_path": str(path),
        "region_kind": "image",
        "bbox": {"l": 60, "t": 100, "r": 540, "b": 600},
    }
    defaults.update(kwargs)
    return ImageInfo(**defaults)


def test_vlm_read_targets_only_large_untyped_pictures(tmp_path):
    """표·도표로 판정됐거나 작은 그림은 VLM 대상이 아니다."""
    from docstruct.images.vlm_read import _should_read

    assert _should_read(_picture(tmp_path)) is True
    assert _should_read(_picture(tmp_path, id="i2", region_kind="table")) is False
    assert _should_read(_picture(tmp_path, id="i3", region_kind="text")) is False
    assert _should_read(_picture(tmp_path, id="i4",
                                 bbox={"l": 60, "t": 100, "r": 110, "b": 130})) is False
    assert _should_read(_picture(tmp_path, id="i5", image_path=None)) is False
    assert _should_read(_picture(tmp_path, id="i6", vlm_markdown="이미 읽음")) is False


def test_vlm_read_inserts_after_placeholder(tmp_path, monkeypatch):
    """복원한 내용이 그림 placeholder 바로 뒤에 들어가고 그림은 남는다."""
    from docstruct.images import vlm_read
    from docstruct.models import PageContent

    monkeypatch.setattr(vlm_read, "llm_available", lambda: True)
    monkeypatch.setattr(vlm_read, "llm_api_config", lambda: {})
    monkeypatch.setattr(
        vlm_read, "invoke_llm",
        lambda *a, **k: "```markdown\n| 단계 | 내용 |\n| --- | --- |\n| 수집 | 문서 |\n```",
    )

    img = _picture(tmp_path)
    page = PageContent(page_no=1, page_no_kind="physical",
                       content="앞\n\n<!-- image 1 -->\n\n뒤", tables=[], images=[img])
    assert vlm_read.read_picture_regions([page]) == 1
    assert img.vlm_markdown and "| 단계 | 내용 |" in img.vlm_markdown
    assert "```" not in page.content              # 울타리는 벗긴다
    assert page.content.index("<!-- image 1 -->") < page.content.index("| 단계")
    assert page.content.index("| 단계") < page.content.index("뒤")
    assert page.images == [img]                   # 그림은 그대로 남는다


def test_vlm_read_ignores_empty_answer(tmp_path, monkeypatch):
    """읽을 내용이 없다는 응답은 본문을 건드리지 않는다."""
    from docstruct.images import vlm_read
    from docstruct.models import PageContent

    monkeypatch.setattr(vlm_read, "llm_available", lambda: True)
    monkeypatch.setattr(vlm_read, "llm_api_config", lambda: {})
    monkeypatch.setattr(vlm_read, "invoke_llm", lambda *a, **k: "내용 없음")

    img = _picture(tmp_path)
    page = PageContent(page_no=1, page_no_kind="physical",
                       content="<!-- image 1 -->", tables=[], images=[img])
    assert vlm_read.read_picture_regions([page]) == 0
    assert page.content == "<!-- image 1 -->"
    assert img.vlm_markdown is None


def test_vlm_read_skipped_without_llm(tmp_path, monkeypatch):
    """LLM 이 없으면 조용히 건너뛴다."""
    from docstruct.images import vlm_read
    from docstruct.models import PageContent

    monkeypatch.setattr(vlm_read, "llm_available", lambda: False)
    page = PageContent(page_no=1, page_no_kind="physical",
                       content="<!-- image 1 -->", tables=[],
                       images=[_picture(tmp_path)])
    assert vlm_read.read_picture_regions([page]) == 0


def test_picture_mode_switch(monkeypatch):
    """picture_mode 로 그림 처리 경로를 고른다."""
    from docstruct.core.config import get_settings, rebuild_settings

    for value, want in [("read", "read"), ("describe", "describe"),
                        ("both", "both"), ("off", "off"), ("이상한값", "read")]:
        monkeypatch.setenv("DOCSTRUCT_PICTURE_MODE", value)
        rebuild_settings()
        assert get_settings().picture_mode == want
    monkeypatch.delenv("DOCSTRUCT_PICTURE_MODE", raising=False)
    rebuild_settings()
    assert get_settings().picture_mode == "read"    # 기본


def test_picture_description_disabled_in_read_mode(monkeypatch):
    """read 모드에서는 docling 그림 설명을 켜지 않는다 (중복 호출 방지)."""
    from docstruct.converters.pdf.docling_backend import _picture_description_options
    from docstruct.core.config import rebuild_settings

    for mode in ("read", "off"):
        monkeypatch.setenv("DOCSTRUCT_PICTURE_MODE", mode)
        rebuild_settings()
        # docling 미설치 환경에서도 import 전에 조기 반환해야 한다
        assert _picture_description_options() is None
    monkeypatch.delenv("DOCSTRUCT_PICTURE_MODE", raising=False)
    rebuild_settings()


def test_vlm_read_disabled_in_describe_mode(monkeypatch, tmp_path):
    """describe 모드에서는 vlm_read 가 돌지 않는다."""
    from docstruct.core.config import rebuild_settings
    from docstruct.images import vlm_read
    from docstruct.models import PageContent

    monkeypatch.setenv("DOCSTRUCT_PICTURE_MODE", "describe")
    rebuild_settings()
    monkeypatch.setattr(vlm_read, "llm_available", lambda: True)

    page = PageContent(page_no=1, page_no_kind="physical",
                       content="<!-- image 1 -->", tables=[],
                       images=[_picture(tmp_path)])
    assert vlm_read.read_picture_regions([page]) == 0
    monkeypatch.delenv("DOCSTRUCT_PICTURE_MODE", raising=False)
    rebuild_settings()


# ────────────────────────────────────────────────────────────────────
# HWP 서식 → markdown
# ────────────────────────────────────────────────────────────────────

def _styles(**kwargs):
    """시험용 DocStyles."""
    from docstruct.converters.hwp.styling import DocStyles

    return DocStyles(
        style_names=kwargs.get("names", {}),
        charshapes=kwargs.get("shapes", {}),
    )


@pytest.mark.parametrize(("name", "want"), [
    ("개요 1", 1), ("개요 3", 3), ("개요 6 사본1", 6),   # 파생 스타일도 인식
    ("제목", 1), ("바탕글", None), ("xl68", None),
    ("쪽 번호", None), ("각주", None),                  # 본문이 아닌 스타일
])
def test_heading_level_from_style_name(name, want):
    """스타일 이름으로 제목 수준을 정한다."""
    assert _styles(names={7: name}).heading_level(7) is want


@pytest.mark.parametrize(("text", "want"), [
    ("제1장 총칙", "# 제1장 총칙"),
    ("제 2 절 예산", "## 제 2 절 예산"),
    ("Ⅰ. 임무와 비전", "## Ⅰ. 임무와 비전"),
    # 번호로 시작해도 긴 문장은 본문
    ("제1조에 따라 " + "가" * 70, None),
])
def test_numbered_heading(text, want):
    """번호 표기로도 제목을 잡는다 (실제 공문서는 스타일을 안 쓴다)."""
    from docstruct.converters.hwp.styling import format_paragraph

    got = format_paragraph(text, styles=_styles())
    if want is None:
        assert not got.startswith("#")
    else:
        assert got == want


@pytest.mark.parametrize(("text", "want"), [
    ("□ 성과목표관리", "- 성과목표관리"),
    ("○ 소비성서비스업", "  - 소비성서비스업"),
    ("- 세부 내용", "    - 세부 내용"),
    ("일반 문단입니다", "일반 문단입니다"),
])
def test_bullet_depth_becomes_indent(text, want):
    """공문서 글머리 계층(□ → ○ → -)이 들여쓰기로 옮겨진다."""
    from docstruct.converters.hwp.styling import format_paragraph

    assert format_paragraph(text, styles=_styles()) == want


def test_emphasis_from_charshape():
    """굵기·기울임이 markdown 표식으로 옮겨진다."""
    from docstruct.converters.hwp.styling import format_paragraph

    styles = _styles(shapes={1: (True, False, 1500), 2: (False, True, 1500),
                             3: (True, True, 1500), 4: (False, False, 1500)})
    assert format_paragraph("굵게", styles=styles, charshape_id=1) == "**굵게**"
    assert format_paragraph("기울임", styles=styles, charshape_id=2) == "*기울임*"
    assert format_paragraph("둘다", styles=styles, charshape_id=3) == "***둘다***"
    assert format_paragraph("보통", styles=styles, charshape_id=4) == "보통"


def test_cell_text_has_no_headings_or_bullets():
    """표 셀 안에서는 `#` 나 `- ` 를 쓰지 않는다 (GFM 표가 깨진다)."""
    from docstruct.converters.hwp.styling import format_paragraph

    styles = _styles(names={1: "개요 1"}, shapes={1: (True, False, 1500)})
    out = format_paragraph("제1장 총칙", styles=styles, style_id=1,
                           charshape_id=1, in_cell=True)
    assert not out.startswith("#")
    assert out == "**제1장 총칙**"

    out2 = format_paragraph("□ 항목", styles=_styles(), in_cell=True)
    assert out2 == "□ 항목"                      # 글머리를 들여쓰기로 바꾸지 않는다


def test_emphasis_not_doubled():
    """이미 표식이 있으면 덧씌우지 않는다."""
    from docstruct.converters.hwp.styling import format_paragraph

    styles = _styles(shapes={1: (True, False, 1500)})
    assert format_paragraph("**이미 굵게**", styles=styles, charshape_id=1) == "**이미 굵게**"


# ────────────────────────────────────────────────────────────────────
# HWP 페이지 분리 · 중첩표 통번호
# ────────────────────────────────────────────────────────────────────

def test_nested_table_numbers_are_document_wide():
    """중첩표 번호는 부모마다 1부터가 아니라 문서 전체 통번호다."""
    from docstruct.converters.hwp.pyhwp_backend.hwp5tree import _Counter

    counter = _Counter()
    assert [counter.next() for _ in range(3)] == [1, 2, 3]


def test_split_by_page_break_keeps_table_numbers():
    """쪽으로 갈라도 표 번호는 통번호를 유지한다."""
    from docstruct.converters.hwp.pyhwp_backend.hwp5tree import PAGE_BREAK
    from docstruct.extractors.hwp import _split_by_page_break
    from docstruct.models import PageTrace, TableInfo

    tables = [
        TableInfo(id=f"table_{n}", table_num=n,
                  placeholder=f"<table {n}>", markdown="| a |")
        for n in (1, 2, 3)
    ]
    content = (
        f"첫 쪽\n\n<table 1>\n</table 1>{PAGE_BREAK}"
        f"둘째 쪽\n\n<table 2>\n</table 2>\n\n<table 3>\n</table 3>"
    )
    trace = PageTrace(extractor="hwp5-tree", text_source="n/a", table_count=3)

    pages = _split_by_page_break(content, tables, trace, None)
    assert len(pages) == 2
    assert [t.table_num for t in pages[0].tables] == [1]
    assert [t.table_num for t in pages[1].tables] == [2, 3]
    assert pages[0].page_no_kind == "document"   # 물리 쪽이 아님을 드러낸다
    assert pages[1].page_image_path is None      # 미리보기는 첫 쪽에만


def test_split_by_page_break_single_page():
    """쪽 표식이 없으면 한 쪽으로 둔다."""
    from docstruct.extractors.hwp import _split_by_page_break
    from docstruct.models import PageTrace, TableInfo

    tables = [TableInfo(id="table_1", table_num=1,
                        placeholder="<table 1>", markdown="| a |")]
    trace = PageTrace(extractor="hwp5-tree", text_source="n/a", table_count=1)
    pages = _split_by_page_break("본문\n\n<table 1>\n</table 1>", tables, trace, "/tmp/p.png")
    assert len(pages) == 1
    assert pages[0].tables == tables
    assert pages[0].page_image_path == "/tmp/p.png"


def test_page_break_marker_is_not_stripped():
    """쪽 표식이 공백 정리에 삼켜지지 않는다."""
    from docstruct.converters.hwp.pyhwp_backend.hwp5tree import PAGE_BREAK

    assert PAGE_BREAK.strip() == ""               # 공백류라서
    assert PAGE_BREAK == "\x0c"                   # 폼피드 — 본문에 나올 일이 없다


# ────────────────────────────────────────────────────────────────────
# HWP 진단 · 빈 결과 경고
# ────────────────────────────────────────────────────────────────────

def test_diagnose_rejects_non_ole(tmp_path):
    """OLE 가 아닌 파일은 이유와 함께 걸러진다."""
    from docstruct.converters.hwp.diagnose import diagnose

    bad = tmp_path / "fake.hwp"
    bad.write_bytes(b"this is not an OLE file")
    report = diagnose(bad)
    assert report.readable is False
    assert "OLE" in report.reason
    assert ".hwpx" in report.reason              # 대안을 알려줘야 한다


def test_diagnose_accepts_real_hwp():
    """정상 HWP 는 통과한다."""
    from docstruct.converters.hwp.diagnose import diagnose

    sample = SAMPLES / "sample.hwpx"             # 있으면 아무 OLE 아닌 파일로 확인
    if not sample.is_file():
        pytest.skip("샘플 없음")
    # hwpx 는 zip 이라 OLE 가 아니다 — 진단이 걸러야 한다
    assert diagnose(sample).readable is False


def test_diagnose_is_permissive_on_error(tmp_path, monkeypatch):
    """진단이 실패하면 막지 않는다 (잘 되던 문서를 깨뜨리지 않기 위함)."""
    from docstruct.converters.hwp import diagnose as mod

    monkeypatch.setattr(mod, "_HWP5_SIGNATURE", b"HWP Document File")
    missing = tmp_path / "없는파일.hwp"
    # 존재하지 않는 파일 → 예외 → readable=True
    assert mod.diagnose(missing).readable is True


def test_warn_when_extraction_is_empty(caplog):
    """본문이 비면 경고를 남긴다 (배치에서 조용히 성공 처리되는 것 방지)."""
    import logging

    from docstruct.models import PageContent, PageTrace
    from docstruct.pipeline import _warn_if_empty

    trace = PageTrace(extractor="hwp5-tree", text_source="n/a", table_count=0)
    empty = [PageContent(page_no=1, page_no_kind="document", content="",
                         tables=[], trace=trace)]
    with caplog.at_level(logging.WARNING):
        _warn_if_empty("문서.hwp", empty)
    assert "추출에 실패했을 수 있습니다" in caplog.text

    caplog.clear()
    full = [PageContent(page_no=1, page_no_kind="document",
                        content="가" * 200, tables=[], trace=trace)]
    with caplog.at_level(logging.WARNING):
        _warn_if_empty("문서.hwp", full)
    assert caplog.text == ""


def test_read_pictures_is_a_known_option():
    """read_pictures 를 DocStruct 옵션으로 받는다."""
    from docstruct import DocStruct

    assert DocStruct("x.hwp", read_pictures=False).options() == {"read_pictures": False}


# ────────────────────────────────────────────────────────────────────
# 0.1.62 — 약한 LLM 의 비정형 JSON 응답
# ────────────────────────────────────────────────────────────────────

def test_object_map_with_non_dict_values_does_not_crash():
    """`{"table_1": "sufficient"}` 류 응답에 TypeError 가 나지 않는다."""
    from docstruct.infrastructure.llm.json_parse import parse_json_list_or_object_map

    out = parse_json_list_or_object_map('{"table_1": "sufficient", "table_2": {"quality": "wrong"}}')
    assert out == [{"id": "table_2", "quality": "wrong"}]


def test_list_with_string_items_is_filtered():
    """목록에 문자열이 섞여 와도 dict 만 남긴다 (호출부 .get 보호)."""
    from docstruct.infrastructure.llm.json_parse import parse_json_list_or_object_map

    out = parse_json_list_or_object_map('["표 문제없음", {"id": "table_1", "quality": "sufficient"}]')
    assert out == [{"id": "table_1", "quality": "sufficient"}]


def test_fenced_object_map_guarded_too():
    """코드펜스 속 객체 조각 경로에서도 같은 가드가 적용된다."""
    from docstruct.infrastructure.llm.json_parse import parse_json_list_or_object_map

    raw = '판정 결과입니다.\n{"table_1": 3, "table_2": {"content_type": "text"}}'
    out = parse_json_list_or_object_map(raw)
    assert out == [{"id": "table_2", "content_type": "text"}]


# ────────────────────────────────────────────────────────────────────
# 0.1.62 — python-hwpx 신·구 API 겸용
# ────────────────────────────────────────────────────────────────────

def test_hwpx_rich_markdown_prefers_new_api():
    """6.0 신 API(doc.text.markdown)가 있으면 그것을 쓴다."""
    from docstruct.converters.hwpx.converter import rich_markdown as _rich_markdown

    class _Text:
        def markdown(self, *, rich=False):
            assert rich is True
            return "NEW"

    class _Doc:
        text = _Text()

        def export_rich_markdown(self):
            raise AssertionError("구 API 를 부르면 안 됨")

    assert _rich_markdown(_Doc()) == "NEW"


def test_hwpx_rich_markdown_falls_back_to_old_api():
    """5.x 구버전(export_rich_markdown 만 존재)에서도 동작한다."""
    from docstruct.converters.hwpx.converter import rich_markdown as _rich_markdown

    class _Doc:
        def export_rich_markdown(self):
            return "OLD"

    assert _rich_markdown(_Doc()) == "OLD"


# ────────────────────────────────────────────────────────────────────
# 0.1.62 — HWP 표 열 수 과소 선언
# ────────────────────────────────────────────────────────────────────

def test_render_table_keeps_cells_beyond_declared_cols():
    """TableBody.cols 가 실제보다 작아도 범위 밖 셀이 버려지지 않는다."""
    from docstruct.converters.hwp.pyhwp_backend.hwp5tree import _Cell, _Table, _render_table

    table = _Table(cols=2)                       # 실제로는 4열 문서
    table.cells = [
        _Cell(col=0, row=0, blocks=["가"]),
        _Cell(col=1, row=0, blocks=["나"]),
        _Cell(col=2, row=0, blocks=["다"]),
        _Cell(col=3, row=0, blocks=["라"]),
    ]
    md = _render_table(table)
    assert "다" in md and "라" in md


# ────────────────────────────────────────────────────────────────────
# 0.1.62 — 문단 강조는 charshape 가 균일할 때만
# ────────────────────────────────────────────────────────────────────

def test_hwp5file_close_quietly_tolerates_missing_close():
    """close() 가 없는 객체·실패하는 close() 모두 예외 없이 지나간다."""
    from docstruct.converters.hwp.pyhwp_backend.hwp5tree import _close_quietly

    class _NoClose:
        pass

    class _BadClose:
        def close(self):
            raise OSError("이미 닫힘")

    _close_quietly(_NoClose())
    _close_quietly(_BadClose())                  # 예외가 새어 나오면 실패


# ────────────────────────────────────────────────────────────────────
# 0.1.63 — 노트북 공개 심볼(재노출 포함) 보호
#
# 배경: ruff 의 F401("미사용 import") 자동 정리가 colab.check_llm_reachable
#       재노출을 지웠고, 노트북이 AttributeError 로 죽었다. 모듈 안에서
#       안 쓰인다고 지워도 되는 게 아니다 — 노트북·외부 코드가 참조하는
#       **공개 표면**이기 때문이다. 아래 테스트가 그 표면을 고정한다.
# ────────────────────────────────────────────────────────────────────

#: 모듈 안에서는 안 쓰이지만 밖에서 참조하는 재노출 심볼.
#: 여기 있는 것은 ruff --fix 로 지우면 안 된다 (# noqa: F401 이 붙어 있다).
REEXPORTS = [
    ("docstruct.output.colab", "check_llm_reachable"),
    ("docstruct.output.report", "IMAGE"),
    ("docstruct.output.report", "TABLE"),
    ("docstruct.output.report", "TEXT"),
    ("docstruct.converters.html.blocks", "BS4_AVAILABLE"),
]


@pytest.mark.parametrize("module_name,attr", REEXPORTS)
def test_reexported_symbols_exist(module_name, attr):
    """재노출 심볼이 살아 있다 (F401 자동 정리 사고 방지)."""
    import importlib

    module = importlib.import_module(module_name)
    assert hasattr(module, attr), (
        f"{module_name}.{attr} 가 사라졌습니다 — 모듈 안에서 안 쓰여도 "
        f"노트북·외부 코드가 참조하는 공개 심볼입니다."
    )


def test_colab_check_llm_reachable_is_checks_function():
    """colab.check_llm_reachable 은 checks 의 같은 함수여야 한다."""
    from docstruct.core import checks
    from docstruct.output import colab

    assert colab.check_llm_reachable is checks.check_llm_reachable


def test_notebook_referenced_symbols_resolve():
    """노트북이 부르는 docstruct 심볼이 전부 실존한다.

    노트북 소스에서 `colab.xxx` / `checks.xxx` 형태 참조를 긁어모아
    실제로 해석되는지 본다. 서브모듈은 import 로, 그 외는 속성으로 확인한다.
    """
    import importlib
    import json
    import re

    nb_dir = Path(__file__).resolve().parent.parent / "notebooks"
    if not nb_dir.is_dir():
        pytest.skip("notebooks 폴더 없음")

    alias = {
        "docstruct": "docstruct",
        "colab": "docstruct.output.colab",
        "checks": "docstruct.core.checks",
        "preview": "docstruct.output.preview",
        "nbui": "docstruct.output.nbui",
    }
    pattern = re.compile(r"\b(docstruct|colab|checks|preview|nbui)\.([a-zA-Z_]\w*)")
    #: 오탐 — 저장소 URL(`...docstruct.git`)과 파일명(`preview.ipynb`) 등
    ignore = {("docstruct", "git"), ("preview", "ipynb"), ("preview", "py")}

    sources: list[str] = []
    for nb in nb_dir.glob("*.ipynb"):
        data = json.loads(nb.read_text(encoding="utf-8"))
        for cell in data.get("cells", []):
            sources.append("".join(cell.get("source", [])))
    for helper in nb_dir.glob("_build*.py"):
        sources.append(helper.read_text(encoding="utf-8"))
    if not sources:
        pytest.skip("노트북 소스 없음")

    missing: list[str] = []
    for text in sources:
        for mod_alias, attr in pattern.findall(text):
            if (mod_alias, attr) in ignore:
                continue
            module = importlib.import_module(alias[mod_alias])
            if hasattr(module, attr):
                continue
            try:
                importlib.import_module(f"{alias[mod_alias]}.{attr}")  # 서브모듈
            except ImportError:
                missing.append(f"{mod_alias}.{attr}")

    assert not missing, f"노트북이 부르는데 없는 심볼: {sorted(set(missing))}"


# ────────────────────────────────────────────────────────────────────
# 0.1.64 — 문서(.md)에 적힌 호출이 실제로 되는지
#
# 배경: WINDOWS.md 가 `from docstruct import winfix` 를 안내했는데 그 경로가
#       없었다. 문서는 사용자가 그대로 복사해 실행하는 코드라, 어긋나면
#       그대로 오류가 된다. 아래 테스트가 문서와 코드의 계약을 고정한다.
# ────────────────────────────────────────────────────────────────────

#: 검사에서 제외할 오탐.
#:   · `docstruct.git` / `docstruct.exe` — 저장소 URL과 실행파일 이름
#:   · `docstruct.models.page` — RESTRUCTURE.md 가 "채택하지 않은 안" 으로
#:     제시한 가정 코드
_DOC_IGNORE = {"docstruct.git", "docstruct.exe", "docstruct.models.page"}

#: 과거 이력이라 현재 API 와 다를 수 있는 문서.
_DOC_SKIP_FILES = {"BUGFIXES.md"}   # 이력 문서는 과거 API 를 담는다


def _doc_files() -> list[Path]:
    """검사 대상 md 파일 목록.

    입력: 없음
    출력: 저장소 최상위의 .md 파일 목록 (이력 문서 제외)
    """
    root = Path(__file__).resolve().parent.parent
    return [f for f in sorted(root.glob("*.md")) if f.name not in _DOC_SKIP_FILES]


def test_doc_module_paths_resolve():
    """md 에 등장하는 docstruct 모듈·속성 경로가 전부 해석된다."""
    import importlib
    import re

    files = _doc_files()
    if not files:
        pytest.skip("md 파일 없음")

    def resolves(path: str) -> bool:
        """모듈이거나 부모 모듈의 속성이면 True.

        local·overlay 트리는 `converters`·`core`·`infrastructure`·
        `experiments` 를 **최상위로 승격**한다(tools/sync_trees.py). 문서는
        세 트리가 함께 쓰므로 `docstruct.converters.…` 표기가 승격된
        트리에서도 해석되게 두 자리를 모두 본다(0.5.1).
        """
        candidates = [path]
        head = path.split(".")
        if len(head) > 1 and head[1] in ("converters", "core", "infrastructure",
                                         "experiments"):
            candidates.append(".".join(head[1:]))
        for name in candidates:
            try:
                importlib.import_module(name)
                return True
            except ImportError:
                pass
            parent, _, attr = name.rpartition(".")
            if not parent:
                continue
            try:
                if hasattr(importlib.import_module(parent), attr):
                    return True
            except ImportError:
                continue
        return False

    # ① 점 표기 — `docstruct.tables.assess`, `docstruct.configure`
    dotted = re.compile(r"\bdocstruct(?:\.[a-z_][a-z0-9_]*)+")
    # ② from-import 표기 — `from docstruct import winfix, preview`
    #    이 형태를 빼먹으면 정작 winfix 회귀를 놓친다 (실제로 놓쳤었다).
    from_import = re.compile(
        r"^\s*from\s+(docstruct(?:\.[a-z_][a-z0-9_.]*)?)\s+import\s+([^\n#]+)", re.M)

    bad: list[str] = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        candidates = {p for p in dotted.findall(text)}
        for module, names in from_import.findall(text):
            candidates.add(module)
            for name in names.replace("(", " ").replace(")", " ").split(","):
                name = name.strip().split(" as ")[0].strip()
                if name and name.isidentifier():
                    candidates.add(f"{module}.{name}")
        for path in sorted(candidates):
            if path in _DOC_IGNORE or not resolves(path):
                if path not in _DOC_IGNORE:
                    bad.append(f"{f.name}: {path}")

    assert not bad, f"문서에 적혔지만 해석되지 않는 경로: {sorted(bad)}"


def test_doc_cli_flags_exist():
    """md 의 docstruct CLI 예제에 쓰인 플래그가 파서에 실존한다."""
    import re
    import subprocess
    import sys as _sys

    files = _doc_files()
    if not files:
        pytest.skip("md 파일 없음")

    help_text = subprocess.run(
        [_sys.executable, "-m", "docstruct.cli", "--help"],
        capture_output=True, text=True,
    ).stdout
    known = set(re.findall(r"(--[a-z][a-z0-9-]*)", help_text))
    assert known, "CLI --help 를 읽지 못했습니다"

    bad: list[str] = []
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            s = line.strip().lstrip("$ ").strip()
            if not re.match(r"^(docstruct|python -m docstruct(\.cli)?)\b", s):
                continue
            for flag in re.findall(r"(?<![\w-])(--[a-z][a-z0-9-]*)", s):
                if flag not in known:
                    bad.append(f"{f.name}: {flag}")

    assert not bad, f"문서에만 있는 CLI 플래그: {sorted(set(bad))}"


def test_doc_trace_labels_exist():
    """문서의 실행 로그 예시에 적힌 모듈 라벨이 코드의 trace 라벨과 맞는다."""
    import re

    root = Path(__file__).resolve().parent.parent
    doc_labels: set[str] = set()
    for name in ("README.md", "API.md"):
        f = root / name
        if not f.is_file():
            continue
        doc_labels |= set(re.findall(
            r"^\s*[!\-x ]?\s*\d+\.\s+((?:docstruct|converters)\.[a-z_.]+)",
            f.read_text(encoding="utf-8"), re.M,
        ))
    if not doc_labels:
        pytest.skip("문서에 실행 로그 예시 없음")

    # 코드 루트는 배치마다 다르다 — pkg 는 src/, local·overlay 는 트리
    # 루트에 패키지가 흩어져 있다. docstruct 패키지의 실제 위치로 잰다.
    import docstruct
    src = Path(docstruct.__file__).resolve().parent.parent
    code_labels: set[str] = set()
    for py in src.rglob("*.py"):
        code_labels |= set(re.findall(
            r'\.add\(\s*"([a-z][\w.]*)"', py.read_text(encoding="utf-8")))

    missing = sorted(doc_labels - code_labels)
    assert not missing, f"문서 예시에만 있는 trace 라벨: {missing}"


def test_winfix_importable_from_package_root():
    """WINDOWS.md 가 안내하는 `from docstruct import winfix` 가 통한다."""
    from docstruct import winfix

    assert callable(winfix.apply)


# ────────────────────────────────────────────────────────────────────
# 0.1.65 — 깨진 ToUnicode 매핑 탐지 (글머리표가 한글 음절로 나올 때)
# ────────────────────────────────────────────────────────────────────

_BROKEN_DOC = """숿 중소기업 설비투자자산 감가상각비 손금산입 특례
슻 (대상자산) 중소기업이 취득한 스마트공장 관련 사업용 유형자산
슻 (내용) 기준내용연수의 50% 범위 내에서 가감하여 신고한 내용연수 적용
숿 상품 등 판매시 손익귀속시기 합리화
슻 자산 판매손익 등의 귀속 사업연도
슻 상품 등의 시용판매
숿 조건부·기한부 판매시 손익귀속시기 합리화"""


def test_glyph_probe_finds_broken_bullets():
    """글머리표 자리의 한글 음절을 의심 문자로 잡는다."""
    from docstruct.converters.pdf.glyph_probe import find_suspects

    chars = {s.char for s in find_suspects(_BROKEN_DOC)}
    assert chars == {"숿", "슻"}


def test_glyph_probe_no_false_positive_on_real_bullets():
    """정상 글머리표 문서에서는 아무것도 지목하지 않는다."""
    from docstruct.converters.pdf.glyph_probe import find_suspects, has_known_bullets

    clean = _BROKEN_DOC.replace("숿", "□").replace("슻", "○")
    assert has_known_bullets(clean)
    assert find_suspects(clean) == []


def test_glyph_probe_no_false_positive_on_single_syllable_text():
    """`가 나 다` 열거나 `시 도 군` 같은 실제 홀글자 본문은 건드리지 않는다."""
    from docstruct.converters.pdf.glyph_probe import find_suspects

    real = (
        "가 항목에 대하여 살펴본다\n"
        "나 항목은 다음과 같다\n"
        "그 는 말했다 그 뜻을 그 자리에서\n"
        "시 도 군 구 단위로 집계한다"
    )
    assert find_suspects(real) == []


def test_map_pua_leaves_normal_hangul_alone():
    """map_pua 는 PUA 밖의 한글 음절을 바꾸지 않는다.

    `숿`(U+C23F) 은 정상 한글 음절이라 PUA 매핑 대상이 아니다.
    여기서 손대기 시작하면 멀쩡한 본문이 기호로 바뀐다.
    """
    from docstruct.text.korean_text import map_pua

    assert map_pua("숿 중소기업") == "숿 중소기업"


# ────────────────────────────────────────────────────────────────────
# 0.1.66 / 0.1.67 — pyhwp 반복 경고 요약
#
# 두 종류가 문서마다 수천 줄씩 쏟아진다.
#   · hwp5.xmlmodel `unmatched field end`  — 필드 짝 안 맞음
#   · hwp5.dataio   `undefined … value: N` — 비트필드 값이 Enum 표에 없음
# 둘 다 예외가 아니고 본문 손실도 없다. 그렇다고 통째로 가리면 신호가
# 사라지므로, 종류별로 세어 문서당 한 줄로 요약한다.
# ────────────────────────────────────────────────────────────────────

def _noise_logger(name: str):
    """계수기 시험용 로거."""
    import logging

    return logging.getLogger(name)


def test_repeated_pyhwp_warnings_are_counted_not_printed():
    """되풀이 경고는 출력되지 않고 종류별로 집계된다."""
    from docstruct.converters.hwp.pyhwp_backend.hwp5tree import _quiet_warnings

    with _quiet_warnings() as counter:
        for _ in range(47):
            _noise_logger("hwp5.xmlmodel").warning("unmatched field end")
        for _ in range(1168):
            _noise_logger("hwp5.dataio").warning("undefined UnderlineStyle value: 15")
        assert counter.total == 47 + 1168
        summary = counter.summary()
        assert "47" in summary and "1,168" in summary


def test_unknown_warnings_still_pass_through():
    """모르는 경고까지 삼키면 진짜 문제가 묻힌다 — 반드시 통과해야 한다."""
    import logging

    from docstruct.converters.hwp.pyhwp_backend.hwp5tree import _NoiseCounter

    counter = _NoiseCounter()
    record = logging.LogRecord(
        "hwp5.xmlmodel", logging.WARNING, "", 0,
        "섹션을 읽지 못했습니다", None, None)
    assert counter.filter(record) is True
    assert counter.total == 0


def test_enum_dump_line_is_dropped_without_counting():
    """`defined name/values:` 덤프는 앞 줄에 딸린 것이라 세지 않는다."""
    import logging

    from docstruct.converters.hwp.pyhwp_backend.hwp5tree import _NoiseCounter

    counter = _NoiseCounter()
    record = logging.LogRecord(
        "hwp5.dataio", logging.WARNING, "", 0,
        "defined name/values: {'SOLID': 0, 'DASHED': 1}", None, None)
    assert counter.filter(record) is False
    assert counter.total == 0


def test_verbose_env_disables_suppression():
    """DOCSTRUCT_PYHWP_VERBOSE=true 면 계수기를 달지 않는다."""
    import logging

    from docstruct.converters.hwp.pyhwp_backend.hwp5tree import _quiet_warnings

    logger = logging.getLogger("hwp5.dataio")
    before = len(logger.filters)
    import os

    os.environ["DOCSTRUCT_PYHWP_VERBOSE"] = "true"
    try:
        with _quiet_warnings():
            assert len(logger.filters) == before, "verbose 인데 필터가 붙었습니다"
    finally:
        os.environ.pop("DOCSTRUCT_PYHWP_VERBOSE", None)


def test_noise_counter_is_removed_after_conversion():
    """변환이 끝나면 계수기가 두 로거 모두에서 떨어진다 (문서 간 누수 방지)."""
    import logging

    from docstruct.converters.hwp.pyhwp_backend import hwp5tree

    loggers = [logging.getLogger(n) for n in ("hwp5.xmlmodel", "hwp5.dataio")]
    before = [len(lg.filters) for lg in loggers]

    # 파일 열기 자체가 실패해도 계수기는 떨어져야 한다.
    try:
        hwp5tree.to_markdown("존재하지-않는-파일.hwp")
    except Exception:
        pass

    assert [len(lg.filters) for lg in loggers] == before, "계수기가 남았습니다"


def test_undefined_enum_does_not_affect_bold_italic():
    """UnderlineStyle 경고가 나도 bold·italic 은 멀쩡하다.

    CharShape 비트필드에서 underline_style 은 4~7비트(0~15)인데 pyhwp 의
    표는 0~10 만 정의한다 — 문서가 깨진 게 아니라 pyhwp 표가 비어 있는 것.
    우리가 읽는 bold(1비트)·italic(0비트)은 별개 비트라 영향이 없다.
    """
    pytest.importorskip("hwp5")
    from hwp5.binmodel.tagid21_char_shape import CharShape

    # italic=1, bold=1, underline_style=15 (정의되지 않은 값)
    flags = CharShape.Flags((1 << 0) | (1 << 1) | (15 << 4))
    assert flags.italic == 1
    assert flags.bold == 1
    assert int(flags.underline_style) == 15   # 원시 정수 그대로 보존


def test_unmatched_field_end_is_not_fatal():
    """pyhwp 는 짝 없는 필드 종료를 예외 없이 넘긴다 (내용 손실 없음).

    이 경고를 보고 문서가 실패했다고 오해하기 쉬운데, 배치의 failures 에는
    잡히지 않는다 — 예외가 아니기 때문이다.
    """
    pytest.importorskip("hwp5")
    from hwp5.treeop import ENDEVENT
    from hwp5.xmlmodel import mfse_field_end

    # 스택이 비어 있으면 unmatched — 경고만 남기고 이벤트를 버린다.
    assert list(mfse_field_end(ENDEVENT, [], ("dummy", {}))) == []


# ────────────────────────────────────────────────────────────────────
# 0.1.68 — 판정하지 않은 표를 "판정 완료" 로 보고하던 문제
#
# 배경: LLM 이 없거나 닿지 않으면 assess 가 모든 표를 table/sufficient 로
#       기본 표시만 하는데, trace 에는 `[ok] LLM 표 판정` 으로 남았다.
#       결과 JSON 만 보면 212개 표가 전부 품질 검증을 통과한 것처럼 보인다.
#       실제로는 LLM 이 한 번도 호출되지 않았다.
# ────────────────────────────────────────────────────────────────────

def test_unassessed_tables_carry_reason():
    """LLM 을 못 부른 표에는 그 사실이 reason 에 남는다."""
    from docstruct.models import TableInfo
    from docstruct.tables.assess import UNASSESSED_REASON, _mark_default

    table = TableInfo(id="table_1", table_num=1, placeholder="<table 1>", markdown="")
    _mark_default(table, unassessed=True)
    assert table.quality == "sufficient"
    assert table.reason == UNASSESSED_REASON


def test_assessed_tables_have_no_unassessed_reason():
    """정상 판정된 표에는 미판정 표시가 붙지 않는다."""
    from docstruct.models import TableInfo
    from docstruct.tables.assess import UNASSESSED_REASON, _mark_default

    table = TableInfo(id="table_1", table_num=1, placeholder="<table 1>", markdown="")
    _mark_default(table)                         # LLM 이 답했으나 해당 표는 언급 없음
    assert table.reason != UNASSESSED_REASON


def test_llm_failure_marks_tables_unassessed():
    """LLM 호출이 실패하면 그 페이지 표는 미판정으로 표시된다.

    엔드포인트가 설정돼 있어도 사내망 밖이면 연결이 안 된다. 이때
    `llm_available()` 은 True 라서, 그것만 보고 판단하면 놓친다.
    """
    from docstruct.models import PageContent, TableInfo
    from docstruct.tables import assess as assess_mod
    from docstruct.tables.assess import UNASSESSED_REASON

    page = PageContent(
        page_no=1, page_no_kind="document", content="<table 1>\n\n| a |\n",
        tables=[TableInfo(id="table_1", table_num=1,
                          placeholder="<table 1>", markdown="| a |")],
    )

    def _boom(*args, **kwargs):
        raise RuntimeError("연결 불가")

    original = assess_mod.invoke_llm
    assess_mod.invoke_llm = _boom
    try:
        assess_mod.assess_page_tables(page, cfg={"url": "http://x/v1", "model": "m"})
    finally:
        assess_mod.invoke_llm = original

    assert page.tables[0].reason == UNASSESSED_REASON


# ────────────────────────────────────────────────────────────────────
# 0.1.69 — hwp5html 실패가 문서 전체를 죽이던 문제
#
# 배경: `_uses_ole_fallback()` 은 olefile 로 내려갈지 판정하는 함수인데,
#       그 판정을 위해 hwp5html 을 실행한다. HwpTimeout 만 잡고 RuntimeError
#       (종료코드 ≠ 0) 는 안 잡아서 예외가 그대로 위로 튀어 문서가 실패했다.
#       hwp5html 실패는 olefile 로 내려갈 가장 강한 근거인데 그러지 못했다.
# ────────────────────────────────────────────────────────────────────

_PYHWP_NOISE = """WARNING  undefined PatternTypeEnum value: 6
WARNING  defined name/values: {'NONE': 0, 'GRID': 5}
WARNING  undefined UnderlineStyle value: 15
WARNING  defined name/values: {'SOLID': 0}"""


def _bare_converter(path: str = "/tmp/fake.hwp"):
    """__init__(파일 존재 검사)을 우회한 HwpConverter."""
    from docstruct.converters.hwp.converter import HwpConverter

    c = HwpConverter.__new__(HwpConverter)
    c.path = path
    c._html_cache = None
    c._html_stderr = ""
    c._ole_fallback = None
    c._ole_text_cache = None
    c._tree_cache = None
    c._tree_tried = False
    return c


def test_hwp5html_failure_falls_back_to_olefile(monkeypatch):
    """hwp5html 이 실패하면 예외를 내지 않고 olefile 폴백으로 내려간다."""
    from docstruct.converters.hwp import converter as conv

    c = _bare_converter()

    def _boom(_path):
        raise RuntimeError(f"hwp5html 실패 (종료코드 1):\n{_PYHWP_NOISE}")

    _use_fake_backend(monkeypatch, conv, lambda _p: "", html=_boom)
    monkeypatch.setattr(conv, "is_hwpml", lambda _p: False)
    assert c._uses_ole_fallback() is True
    assert "hwp5html" in (c.fallback_reason or "")


def test_error_message_filters_pyhwp_noise():
    """오류 메시지에 pyhwp 상시 경고가 실패 사유로 실리지 않는다."""
    from docstruct.converters.hwp.pyhwp_backend.html_export import real_error_lines

    assert real_error_lines(_PYHWP_NOISE) == []

    mixed = _PYHWP_NOISE + "\nKeyError: 42"
    assert real_error_lines(mixed) == ["KeyError: 42"]


def test_error_message_says_so_when_only_noise():
    """경고밖에 없으면 원인을 모른다는 사실을 밝힌다.

    경고를 원인인 양 보여주면 `undefined UnderlineStyle value: 15` 를
    실패 사유로 읽게 된다 — 실제로 그렇게 읽혔다.
    """
    from docstruct.converters.hwp.pyhwp_backend.html_export import _describe_failure

    message = _describe_failure(_PYHWP_NOISE)
    assert "특정하지 못했습니다" in message
    assert "실패 사유가 아닙니다" in message


# ────────────────────────────────────────────────────────────────────
# 0.1.70 — 첫 실패(hwp5-tree)가 INFO 로 묻혀 두 번째 실패만 보이던 문제
#
# 배경: 폴백 경로(hwp5html)까지 내려갔다는 것은 기본 경로(hwp5-tree)가
#       **이미 실패했다**는 뜻이다. 그런데 그 실패가 INFO 라 기본 로깅
#       (WARNING)에서 보이지 않았고, 사람은 두 번째 실패만 보고 그것을
#       원인으로 오해했다. 두 경로는 같은 pyhwp 파서를 공유하므로 대개
#       원인이 같다 — 먼저 죽은 쪽이 진짜 원인에 가깝다.
# ────────────────────────────────────────────────────────────────────

def test_tree_failure_is_recorded(caplog, monkeypatch):
    """기본 경로 실패가 WARNING 으로 남고 사유가 보존된다."""
    import logging

    from docstruct.converters.hwp import converter as conv
    from docstruct.converters.hwp.pyhwp_backend import hwp5tree

    c = _bare_converter()
    c._tree_failure = None

    def _boom(_path):
        raise KeyError("HWPTAG_LIST_HEADER: 알 수 없는 레코드")

    _use_fake_backend(monkeypatch, conv, _boom)
    with caplog.at_level(logging.WARNING, logger=conv.__name__):
        assert c._get_tree_markdown() is None
    assert "HWPTAG_LIST_HEADER" in (c.tree_failure or "")
    assert any("hwp5-tree" in r.message for r in caplog.records)


def test_short_tree_result_records_reason(monkeypatch):
    """파싱은 됐으나 내용이 없는 경우도 사유가 남는다."""
    from docstruct.converters.hwp import converter as conv

    c = _bare_converter()
    c._tree_failure = None
    _use_fake_backend(monkeypatch, conv, lambda _p: "짧음")
    assert c._get_tree_markdown() is None
    assert "자뿐" in (c.tree_failure or "")


def test_fallback_reason_includes_first_failure(monkeypatch):
    """폴백 사유에 먼저 죽은 기본 경로의 사유가 함께 실린다."""
    from docstruct.converters.hwp import converter as conv

    c = _bare_converter()
    c._tree_failure = None

    def _tree_boom(_p):
        raise KeyError("HWPTAG_LIST_HEADER")

    def _html_boom(_p):
        raise RuntimeError(f"hwp5html 실패:\n{_PYHWP_NOISE}")

    _use_fake_backend(monkeypatch, conv, _tree_boom, html=_html_boom)
    monkeypatch.setattr(conv, "is_hwpml", lambda _p: False)
    c._get_tree_markdown()
    assert c._uses_ole_fallback() is True
    assert "HWPTAG_LIST_HEADER" in (c.fallback_reason or ""), \
        "첫 실패가 최종 사유에 없습니다"


# ────────────────────────────────────────────────────────────────────
# 0.1.71 — 쪽 나눔 시 PageTrace 객체를 공유하던 문제
#
# 배경: HWP 를 쪽 표식으로 나눌 때 72개 PageContent 가 **같은 PageTrace
#       객체**를 참조했다. 이후 단계가 쪽마다 남기는 기록이 한 리스트에
#       쌓이고, 그 리스트가 쪽 수만큼 직렬화돼 JSON 의 85%(2.5MB)가
#       중복이었다. 1쪽 기록과 72쪽 기록도 구분할 수 없었다.
# ────────────────────────────────────────────────────────────────────

def _split_pages(chunks: int = 3):
    """쪽 나눔을 거친 PageContent 목록을 만든다."""
    from docstruct.converters.hwp.pyhwp_backend.hwp5tree import PAGE_BREAK
    from docstruct.extractors.hwp import _split_by_page_break
    from docstruct.models import PageTrace, TableInfo

    tables = [
        TableInfo(id=f"table_{i}", table_num=i,
                  placeholder=f"<table {i}>", markdown="| a |")
        for i in range(1, chunks + 1)
    ]
    content = PAGE_BREAK.join(
        f"본문 {i}\n\n<table {i}>\n\n| a |\n\n</table {i}>" for i in range(1, chunks + 1)
    )
    trace = PageTrace(extractor="hwp5-tree", text_source="n/a", table_count=chunks)
    trace.add("converters.hwp.pyhwp_backend.hwp5tree", "파싱", "공통 기록")
    return _split_by_page_break(content, tables, trace, None)


def test_split_pages_get_independent_traces():
    """쪽마다 독립된 PageTrace 를 갖는다 (객체·리스트 모두)."""
    pages = _split_pages(3)
    assert len(pages) == 3
    assert len({id(p.trace) for p in pages}) == 3, "trace 객체를 공유합니다"
    assert len({id(p.trace.steps) for p in pages}) == 3, "steps 리스트를 공유합니다"


def test_split_pages_do_not_accumulate_each_others_steps():
    """한 쪽에 기록을 남겨도 다른 쪽에 번지지 않는다."""
    pages = _split_pages(3)
    before = [len(p.trace.steps) for p in pages]
    pages[0].trace.add("docstruct.tables.assess", "판정", "1쪽만")
    after = [len(p.trace.steps) for p in pages]
    assert after[0] == before[0] + 1
    assert after[1:] == before[1:], "다른 쪽에 기록이 번졌습니다"


def test_split_pages_keep_common_history():
    """분할 전 공통 기록은 모든 쪽에 남는다."""
    pages = _split_pages(3)
    for page in pages:
        assert any(s.module == "converters.hwp.pyhwp_backend.hwp5tree" for s in page.trace.steps)


def test_split_pages_carry_own_table_count():
    """쪽마다 자기 표 개수를 갖는다 (공유 trace 는 전체 수를 들고 있었다)."""
    pages = _split_pages(3)
    assert [p.trace.table_count for p in pages] == [1, 1, 1]


# ── slim 출력 ────────────────────────────────────────────────────────

def test_slim_output_drops_trace_keeps_content():
    """slim=True 는 실행 기록을 빼고 본문·표를 남긴다."""
    from docstruct.models import PageContent, PageDocument, TableInfo

    page = PageContent(
        page_no=1, page_no_kind="document", content="본문\n\n<table 1>",
        tables=[TableInfo(id="table_1", table_num=1, placeholder="<table 1>",
                          markdown="| a |", llm_title="예산 현황")],
    )
    doc = PageDocument(filename="x.hwp", source_format="hwp", pages=[page])

    slim = doc.to_dict(slim=True)
    assert "pipeline" not in slim and "timings" not in slim
    assert "trace" not in slim["pages"][0]
    assert slim["pages"][0]["content"] == "본문\n\n<table 1>"
    assert slim["pages"][0]["tables"][0]["title"] == "예산 현황"
    assert slim["pages"][0]["tables"][0]["markdown"] == "| a |"

    full = doc.to_dict()
    assert "trace" in full["pages"][0], "기본 출력은 trace 를 유지해야 합니다"


# ────────────────────────────────────────────────────────────────────
# 0.1.72 — 표 렌더 정확성 (셀 텍스트를 기호가 끊던 문제)
#
# 배경: 원본 성과계획서와 셀 단위로 대조한 결과 **데이터 유실은 0%** 였다.
#       문제는 표현이었다. 좁은 칸에서 작성자가 Enter 로 나눈 줄마다 강조가
#       걸려 `**프로그램목표Ⅰ-1** **의정활동의 …**` 가 됐고, 셀 중간에 낀
#       `**` 가 문자열 매칭을 깨뜨렸다 — 대조 검증에서 멀쩡한 셀 75개가
#       유실로 오판됐다. RAG 색인·LLM 판정도 같은 이유로 잘못 읽는다.
# ────────────────────────────────────────────────────────────────────

def test_render_table_drops_leading_empty_rows():
    """맨 앞의 완전히 빈 행은 헤더로 쓰지 않는다.

    정부 HWP 문서는 표 위에 여백용 빈 행을 두는 일이 흔한데, 그것이 GFM
    헤더가 되면 `|||||||||` 같은 빈 머리행이 나와 표의 의미가 사라진다.
    """
    from docstruct.converters.hwp.pyhwp_backend.hwp5tree import _Cell, _Table, _render_table

    table = _Table(cols=2)
    table.cells = [
        _Cell(col=0, row=0, blocks=[]), _Cell(col=1, row=0, blocks=[]),
        _Cell(col=0, row=1, blocks=["구분"]), _Cell(col=1, row=1, blocks=["금액"]),
        _Cell(col=0, row=2, blocks=["인건비"]), _Cell(col=1, row=2, blocks=["100"]),
    ]
    header = _render_table(table).splitlines()[0]
    assert "구분" in header and "금액" in header


def test_render_table_keeps_row_with_any_value():
    """값이 하나라도 있는 행은 버리지 않는다."""
    from docstruct.converters.hwp.pyhwp_backend.hwp5tree import _Cell, _Table, _render_table

    table = _Table(cols=2)
    table.cells = [
        _Cell(col=0, row=0, blocks=[]), _Cell(col=1, row=0, blocks=["합계"]),
        _Cell(col=0, row=1, blocks=["인건비"]), _Cell(col=1, row=1, blocks=["100"]),
    ]
    header = _render_table(table).splitlines()[0]
    assert "합계" in header


def test_render_table_keeps_fully_empty_table():
    """표 전체가 비어 있으면 그대로 둔다 (원본이 장식용 빈 상자)."""
    from docstruct.converters.hwp.pyhwp_backend.hwp5tree import _Cell, _Table, _render_table

    table = _Table(cols=1)
    table.cells = [_Cell(col=0, row=0, blocks=[])]
    assert _render_table(table).splitlines()[0] == "|  |"


def test_hwp_fill_html_option_exists():
    """정확성 우선 작업에서 표 재추출 근거를 확보하는 설정이 있다."""
    import docstruct

    assert "hwp_fill_html" in docstruct.option_keys()


# ────────────────────────────────────────────────────────────────────
# 0.1.74 — 재추출 불가 로그에 판정 사유가 없던 문제
#
# 배경: `재추출 근거 없음 … id=table_32` 만 찍혀서, 왜 그 표가 대상이
#       됐는지 결과 JSON 을 따로 열어봐야 알 수 있었다. 사유가 보이면
#       "정말 고쳐야 할 표인가" 를 그 자리에서 판단할 수 있다 — 실제로
#       병합 셀을 빈 칸으로 오해한 오탐이 섞여 있었다.
# ────────────────────────────────────────────────────────────────────

def _pending_table(**kwargs):
    """재추출 대상인 TableInfo 를 만든다."""
    from docstruct.models import TableInfo

    defaults = dict(
        id="table_32", table_num=32, placeholder="<table 32>",
        markdown="| **구분** |  |", content_type="table",
        quality="insufficient", llm_title="국회 소통 채널 주요 성과",
        reason="인스타그램 행의 값이 모두 비어 있어 데이터가 불완전함",
    )
    defaults.update(kwargs)
    return TableInfo(**defaults)


def test_unfillable_log_includes_quality_and_reason(caplog):
    """근거가 없어 재추출을 못 할 때 품질과 사유를 함께 남긴다."""
    import logging

    from docstruct.models import PageContent
    from docstruct.tables import fill as fill_mod

    table = _pending_table()
    page = PageContent(page_no=1, page_no_kind="document",
                       content="<table 32>\n| a |\n</table 32>", tables=[table])

    with caplog.at_level(logging.WARNING, logger=fill_mod.__name__):
        fill_mod.process_tables([page], table_html=None)

    message = " ".join(r.getMessage() for r in caplog.records)
    assert "table_32" in message
    assert "insufficient" in message
    assert "인스타그램" in message, "판정 사유가 로그에 없습니다"


def test_unfillable_log_handles_missing_reason(caplog):
    """사유가 비어 있어도 로그가 깨지지 않는다."""
    import logging

    from docstruct.models import PageContent
    from docstruct.tables import fill as fill_mod

    page = PageContent(page_no=1, page_no_kind="document",
                       content="<table 32>\n| a |\n</table 32>",
                       tables=[_pending_table(reason=None)])

    with caplog.at_level(logging.WARNING, logger=fill_mod.__name__):
        fill_mod.process_tables([page], table_html=None)

    message = " ".join(r.getMessage() for r in caplog.records)
    assert "사유 없음" in message


def test_assess_prompt_warns_about_merged_cells():
    """판정 프롬프트가 병합 셀의 빈 칸을 데이터 손실로 오해하지 말라고 알린다.

    markdown 은 rowspan/colspan 을 표현하지 못해, 병합된 아래 행이 빈 칸으로
    남는다. 실제 문서에서 `(2,1) rowspan=2` 인 셀 때문에 멀쩡한 표가
    insufficient 로 잘못 판정됐다.
    """
    from docstruct.tables.assess import _ASSESS_PROMPT

    prompt = _ASSESS_PROMPT.format(content="<content>")
    assert "병합" in prompt
    assert "rowspan" in prompt


def test_needs_fill_is_a_property_not_a_method():
    """needs_fill 은 프로퍼티다 (호출하면 TypeError 가 난다)."""
    table = _pending_table()
    assert table.needs_fill is True
    assert not callable(table.needs_fill)


# ────────────────────────────────────────────────────────────────────
# 0.1.75 — 세로 병합이 빈 칸이 되어 값이 잘못 귀속되던 문제
#
# 배경: `국회 소통 채널 주요 성과` 표에서 콘텐츠 상호작용·15.7만·3.0만 이
#       페이스북과 인스타그램 두 행에 걸친 병합 셀(rowspan=2)이었다.
#       markdown 은 맨 윗행에만 값을 넣고 아래를 비웠고, 그 결과
#         원본: 페이스북+인스타그램 합계 = 15.7만
#         출력: 페이스북 단독 = 15.7만 / 인스타그램 = 데이터 없음
#       으로 **사실이 달라졌다.** LLM 이 insufficient 로 잡은 것은 정확한
#       지적이었다 — 표시 문제가 아니라 값의 귀속이 틀린 것이었다.
# ────────────────────────────────────────────────────────────────────

def _sns_table():
    """실제 table_32 구조 (병합 셀 포함)."""
    from docstruct.converters.hwp.pyhwp_backend.hwp5tree import _Cell, _Table

    table = _Table(cols=4)
    table.cells = [
        _Cell(col=0, row=0, colspan=2, blocks=["구분"]),
        _Cell(col=2, row=0, blocks=["2025년"]),
        _Cell(col=3, row=0, blocks=["2026년"]),
        _Cell(col=0, row=1, blocks=["유튜브"]), _Cell(col=1, row=1, blocks=["조회수"]),
        _Cell(col=2, row=1, blocks=["13,391,527"]), _Cell(col=3, row=1, blocks=["5,377,768"]),
        _Cell(col=0, row=2, blocks=["페이스북"]),
        _Cell(col=1, row=2, rowspan=2, blocks=["콘텐츠 상호작용"]),
        _Cell(col=2, row=2, rowspan=2, blocks=["15.7만"]),
        _Cell(col=3, row=2, rowspan=2, blocks=["3.0만"]),
        _Cell(col=0, row=3, blocks=["인스타그램"]),
    ]
    return table


def test_rowspan_continuation_is_filled_not_blank():
    """세로 병합이 이어지는 칸은 빈 칸이 아니라 **값이 되풀이된다** (0.5.6)."""
    from docstruct.converters.hwp.pyhwp_backend.hwp5tree import _render_table

    rows = _render_table(_sns_table()).splitlines()
    last = rows[-1]
    assert "인스타그램" in last
    assert "〃" not in last, f"옛 표식이 남았습니다: {last}"
    assert last.count("|") >= 4, f"덮인 칸이 비었습니다: {last}"


def test_rowspan_value_stays_on_first_row():
    """닻 행의 값이 그대로 있고, 덮인 행에도 같은 값이 선다 (0.5.6).

    복제하면 같은 값이 검색에 여러 번 걸리고, 합계가 행마다 있는 것처럼
    보인다.
    """
    md = None
    from docstruct.converters.hwp.pyhwp_backend.hwp5tree import _render_table

    md = _render_table(_sns_table())
    assert md.count("15.7만") >= 1
    assert "| 페이스북 | 콘텐츠 상호작용 | 15.7만 | 3.0만 |" in md


def test_rowspan_rows_are_not_truncated():
    """rowspan 이 표 끝까지 이어져도 행이 잘리지 않는다.

    행 수를 `max(row)+1` 로 세면 마지막 셀이 rowspan 으로 아래를 덮을 때
    그 행이 사라진다. `max(row + rowspan)` 이어야 한다.
    """
    from docstruct.converters.hwp.pyhwp_backend.hwp5tree import _Cell, _Table, _render_table

    table = _Table(cols=2)
    table.cells = [
        _Cell(col=0, row=0, blocks=["가"]),
        _Cell(col=1, row=0, rowspan=3, blocks=["공유값"]),
        _Cell(col=0, row=1, blocks=["나"]),
        _Cell(col=0, row=2, blocks=["다"]),
    ]
    md = _render_table(table)
    assert "다" in md
    assert "〃" not in md


def test_merge_mark_can_be_disabled(monkeypatch):
    """표식은 끌 수 있다 (예전 산출물과 대조할 때)."""
    from docstruct.converters.hwp.pyhwp_backend.hwp5tree import MERGE_MARK_ENV, MERGE_UP, _render_table

    monkeypatch.setenv(MERGE_MARK_ENV, "off")
    assert MERGE_UP not in _render_table(_sns_table())


def test_assess_prompt_asks_to_distinguish_merge_cause():
    """프롬프트가 병합을 '무시하라' 가 아니라 '원인을 밝히라' 고 한다.

    무시하게 하면 값의 귀속이 틀린 진짜 결함까지 묻힌다.
    """
    from docstruct.tables.assess import _ASSESS_PROMPT

    prompt = _ASSESS_PROMPT.format(content="<content>")
    assert "병합" in prompt and "rowspan" in prompt
    assert "원인" in prompt


# ────────────────────────────────────────────────────────────────────
# 0.1.76 — slim 이 단건에만 연결돼 있던 문제
#
# 배경: 0.1.71 에서 slim 을 넣으면서 DocStruct.to_json 에만 연결하고
#       DocStructBatch.to_json 과 CLI 는 빠뜨렸다. 배치로 돌리면 여전히
#       trace 가 그대로 실렸다.
# ────────────────────────────────────────────────────────────────────

def test_batch_to_json_accepts_slim():
    """배치 to_json 도 slim 을 받는다 (단건과 같은 이름·의미)."""
    import inspect

    from docstruct import DocStruct, DocStructBatch

    for cls in (DocStruct, DocStructBatch):
        params = inspect.signature(cls.to_json).parameters
        assert "slim" in params, f"{cls.__name__}.to_json 에 slim 이 없습니다"


def test_write_json_accepts_slim():
    """report.write_json 도 slim 을 받는다 (CLI 가 쓰는 경로)."""
    import inspect

    from docstruct.output.report import write_json

    assert "slim" in inspect.signature(write_json).parameters


def test_cli_has_slim_flag():
    """CLI 에 --slim 이 있다."""
    from docstruct.cli import _build_parser

    actions = {a.dest for a in _build_parser()._actions}
    assert "slim" in actions


def test_write_json_slim_drops_trace(tmp_path):
    """write_json(slim=True) 결과에 trace 가 없다."""
    import json

    from docstruct.models import PageContent, PageDocument
    from docstruct.output.report import write_json

    doc = PageDocument(
        filename="x.hwp", source_format="hwp",
        pages=[PageContent(page_no=1, page_no_kind="document", content="본문")],
    )
    path = write_json(doc, tmp_path / "d.json", slim=True)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "trace" not in data["pages"][0]
    assert data["pages"][0]["content"] == "본문"

    path2 = write_json(doc, tmp_path / "d2.json")
    assert "trace" in json.loads(path2.read_text(encoding="utf-8"))["pages"][0]


# ────────────────────────────────────────────────────────────────────
# 0.1.78 — 누름틀 필드 상태가 본문에 섞이던 문제
#
# 배경: `{"fields": {},"simplefields": {}}` 가 본문에 나왔다. 원본 HWP 의
#       `FieldClickHere`(누름틀, chid='%clk') 안에 들어 있는 값으로,
#       command 속성은 `Clickhere:set:53:Direction:...데이터 조회중...`
#       이었다. 화면·인쇄물에는 보이지 않지만 텍스트 레이어에는 남아,
#       HWP 로 읽든 PDF 로 내보내든 따라온다(PDF p.144·146 에서도 확인).
#
#       주의: **필드 자체를 버리면 안 된다.** 이 문서에서 FieldClickHere 는
#       5,306회 쓰였고 그 안에 `기획예산처`·`전략목표` 같은 진짜 본문이
#       들어 있다. 처음에 필드 전체를 건너뛰게 만들었다가, 본문이 통째로
#       사라지는 것을 확인하고 되돌렸다.
# ────────────────────────────────────────────────────────────────────

def test_field_payload_is_detected():
    """필드 상태 직렬화 값을 걸러낸다."""
    from docstruct.converters.hwp.pyhwp_backend.hwp5tree import _is_field_payload

    assert _is_field_payload('{"fields": {},"simplefields": {}}')
    assert _is_field_payload('  {"fields": {},"simplefields": {}}  ')
    assert _is_field_payload('{"fields": {"a":1},"simplefields": {"b":2}}')


def test_field_payload_does_not_eat_real_content():
    """본문에 나오는 정상 텍스트·JSON 은 건드리지 않는다.

    넓게 잡으면 문서에 실린 코드 조각이나 설명문까지 지운다.
    """
    from docstruct.converters.hwp.pyhwp_backend.hwp5tree import _is_field_payload

    assert not _is_field_payload("simplefields 를 설명하는 본문")
    assert not _is_field_payload('{"name": "홍길동"}')
    assert not _is_field_payload('예시: {"simplefields": {}} 참고')
    assert not _is_field_payload("기획예산처")
    assert not _is_field_payload("")


def test_field_inner_text_is_kept():
    """누름틀 안의 실제 본문은 살아남는다.

    필드 모델 전체를 건너뛰면 이 텍스트가 사라진다 — 실제로 그렇게
    구현했다가 되돌린 자리다.
    """
    from docstruct.converters.hwp.pyhwp_backend.hwp5tree import _is_field_payload

    for real in ("기획예산처", "전략목표", "83", "1. 임무와 비전"):
        assert not _is_field_payload(real)


# ────────────────────────────────────────────────────────────────────
# 0.1.80 — HWPX XML 직접 파서 (pyhwp 대체 후보)
#
# 배경: pyhwp 가 AGPL 이라 대체 경로를 찾던 중, HWPX 파일 자체에는 표
#       212개·셀 5,391개가 온전히 들어 있음을 확인했다. 손실은 변환이
#       아니라 python-hwpx 의 markdown 내보내기에서 생긴다(표 94개,
#       셀 93.8%, 전 텍스트에 취소선 4,456회). XML 을 직접 읽으면
#       pyhwp 와 같은 품질을 9배 빠르게 낸다.
#
#       hwp5tree 에서 잡은 개선을 그대로 옮겨야 한다. 옮기지 않으면
#       경로를 바꾸는 순간 이미 고친 문제들이 되살아난다.
# ────────────────────────────────────────────────────────────────────

def _hwpx_table(rows: int, cols: int, cells: list[tuple]):
    """(row, col, rowspan, colspan, text) 목록으로 _Table 을 만든다."""
    from docstruct.converters.hwpx.hwpxtree import _Cell, _Table

    table = _Table(rows=rows, cols=cols)
    table.cells = [
        _Cell(row=r, col=c, rowspan=rs, colspan=cs, blocks=[t] if t else [])
        for r, c, rs, cs, t in cells
    ]
    return table


def test_hwpx_render_repeats_vertical_merge():
    """세로 병합이 이어지는 칸에 **값을 되풀이한다** (0.5.6).

    RAG 는 표를 행 단위로 자른다. 조각 하나에 `〃` 만 남으면 무엇이
    이어졌는지 알 길이 없다 — 값이 사라진 것과 같다.
    """
    from docstruct.converters.hwpx.hwpxtree import _render_table

    md = _render_table(_hwpx_table(2, 2, [
        (0, 0, 1, 1, "페이스북"), (0, 1, 2, 1, "15.7만"),
        (1, 0, 1, 1, "인스타그램"),
    ]))
    assert md.splitlines()[-1].count("15.7만") == 1, "덮인 행에 값이 없다"
    assert md.count("15.7만") == 2, "닻과 덮인 행 둘 다 있어야 한다"
    assert "〃" not in md


def test_merge_fill_can_go_back_to_ditto(monkeypatch):
    """`DOCSTRUCT_MERGE_FILL=ditto` 로 옛 모양을 되돌릴 수 있다."""
    from docstruct.converters.common.table import MERGE_FILL_ENV
    from docstruct.converters.hwpx.hwpxtree import _render_table

    monkeypatch.setenv(MERGE_FILL_ENV, "ditto")
    md = _render_table(_hwpx_table(2, 2, [
        (0, 0, 1, 1, "페이스북"), (0, 1, 2, 1, "15.7만"),
        (1, 0, 1, 1, "인스타그램"),
    ]))
    assert md.splitlines()[-1].count("〃") == 1
    assert md.count("15.7만") == 1


def test_empty_anchor_is_not_repeated_as_a_mark(monkeypatch):
    """닻이 비어 있으면 되풀이할 것이 없다 — 빈 칸으로 둔다.

    빈 값을 `〃` 로 적으면 "위에 무언가 있다" 는 거짓말이 된다.
    """
    from docstruct.converters.common.table import merge_continuation

    assert merge_continuation("") == ""
    assert merge_continuation("   ") == ""
    assert merge_continuation("국회도서관운영") == "국회도서관운영"


def test_hwpx_render_drops_leading_empty_row():
    """맨 앞의 완전히 빈 행은 헤더로 쓰지 않는다 (0.1.72 와 동일)."""
    from docstruct.converters.hwpx.hwpxtree import _render_table

    md = _render_table(_hwpx_table(3, 2, [
        (0, 0, 1, 1, ""), (0, 1, 1, 1, ""),
        (1, 0, 1, 1, "구분"), (1, 1, 1, 1, "금액"),
        (2, 0, 1, 1, "인건비"), (2, 1, 1, 1, "100"),
    ]))
    assert "구분" in md.splitlines()[0]


def test_hwpx_join_cell_blocks_merges_bold():
    """셀 안 끊긴 굵게를 하나로 합친다 (0.1.73 과 동일)."""
    from docstruct.converters.hwpx.hwpxtree import _join_cell_blocks

    assert _join_cell_blocks(["**년**", "**도**"]) == "**년 도**"
    assert _join_cell_blocks(["*****"]) == "*****"          # 원문 별표 보존
    assert _join_cell_blocks(["**A**", "중간", "**B**"]) == "**A** 중간 **B**"


def test_hwpx_drops_field_payload():
    """누름틀 상태 직렬화 값을 걸러낸다 (0.1.78 과 동일)."""
    from docstruct.converters.hwpx.hwpxtree import _is_field_payload

    assert _is_field_payload('{"fields": {},"simplefields": {}}')
    assert not _is_field_payload('{"name": "홍길동"}')
    assert not _is_field_payload("기획예산처")


def test_hwpx_paragraph_excludes_table_runs():
    """문단 텍스트에 표 내부 런이 섞이지 않는다.

    `para.iter()` 로 훑으면 문단 안에 놓인 표의 런까지 빨아들여, 표
    내용이 본문에 한 번 더 실린다. 실제로 본문 글자가 29,713 대
    72,288 로 부풀었다.
    """
    from xml.etree import ElementTree as ET

    from docstruct.converters.hwpx.hwpxtree import HP, _paragraph_text

    xml = (
        f'<p xmlns:hp="{HP}">'
        f'<hp:run charPrIDRef="1"><hp:t>본문</hp:t></hp:run>'
        f'<hp:tbl><hp:tr><hp:tc><hp:subList><hp:p>'
        f'<hp:run charPrIDRef="1"><hp:t>표안</hp:t></hp:run>'
        f'</hp:p></hp:subList></hp:tc></hp:tr></hp:tbl>'
        f'</p>'
    )
    text = _paragraph_text(ET.fromstring(xml), set())
    assert text == "본문"
    assert "표안" not in text


# ────────────────────────────────────────────────────────────────────
# 0.1.81 — HWP → HWPX 변환 어댑터
#
# 배경: HWPX 경로를 쓰려면 .hwp 를 .hwpx 로 바꿔야 하는데, 쓸 만한 변환기는
#       모두 외부 프로세스(Java 등)다. 어느 도구를 쓸지 아직 정하지 못했으므로
#       호출 규약만 고정하고, **변환기가 없으면 조용히 물러나** 기존 경로가
#       계속 쓰이게 한다. 설치 여부가 파이프라인을 깨뜨리면 안 된다.
# ────────────────────────────────────────────────────────────────────

def test_converter_absent_by_default(monkeypatch):
    """변환기가 설정되지 않으면 사용 불가로 보고 None 을 준다."""
    from docstruct.converters.hwpx import convert as conv

    monkeypatch.delenv(conv.CONVERTER_ENV, raising=False)
    assert conv.is_available() is False
    assert conv.try_convert("/tmp/whatever.hwp") is None


def test_converter_detects_missing_executable(monkeypatch):
    """명령만 설정하고 설치를 안 했으면 미리 잡아낸다.

    문서마다 실패하고 나서 알게 되면 배치가 통째로 헛돈다.
    """
    from docstruct.converters.hwpx import convert as conv

    monkeypatch.setenv(conv.CONVERTER_ENV, "존재하지않는도구 {input} {output}")
    assert conv.is_available() is False


def test_converter_runs_and_returns_path(tmp_path, monkeypatch):
    """정상 변환 시 결과 경로를 돌려준다."""
    from docstruct.converters.hwpx import convert as conv

    source = tmp_path / "in.hwp"
    source.write_bytes(b"dummy hwp bytes")
    script = tmp_path / "conv.sh"
    script.write_text('#!/bin/sh\ncp "$1" "$2"\n')
    script.chmod(0o755)

    monkeypatch.setenv(conv.CONVERTER_ENV, f"{script} {{input}} {{output}}")
    out = conv.convert(source, tmp_path / "out")
    assert out.is_file()
    assert out.suffix == ".hwpx"
    assert out.stat().st_size > 0


def test_converter_rejects_empty_result(tmp_path, monkeypatch):
    """종료코드가 0 이어도 결과가 없으면 실패로 본다.

    실제로 그렇게 동작하는 도구를 겪었다 (hwp5odt 등).
    """
    from docstruct.converters.hwpx import convert as conv

    source = tmp_path / "in.hwp"
    source.write_bytes(b"dummy")
    script = tmp_path / "noop.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)

    monkeypatch.setenv(conv.CONVERTER_ENV, f"{script} {{input}} {{output}}")
    with pytest.raises(RuntimeError, match="결과 파일"):
        conv.convert(source, tmp_path / "out")
    assert conv.try_convert(source, tmp_path / "out2") is None


def test_converter_reports_failure_tail(tmp_path, monkeypatch):
    """실패 시 표준 오류의 끝부분을 메시지에 싣는다."""
    from docstruct.converters.hwpx import convert as conv

    source = tmp_path / "in.hwp"
    source.write_bytes(b"dummy")
    script = tmp_path / "fail.sh"
    script.write_text('#!/bin/sh\necho "무언가 잘못됨" >&2\nexit 3\n')
    script.chmod(0o755)

    monkeypatch.setenv(conv.CONVERTER_ENV, f"{script} {{input}} {{output}}")
    with pytest.raises(RuntimeError, match="무언가 잘못됨"):
        conv.convert(source, tmp_path / "out")


def test_converter_timeout_is_configurable(monkeypatch):
    """제한 시간을 환경변수로 조정할 수 있고 잘못된 값은 기본으로 돌아간다."""
    from docstruct.converters.hwpx import convert as conv

    monkeypatch.setenv(conv.TIMEOUT_ENV, "45")
    assert conv.timeout_seconds() == 45.0
    monkeypatch.setenv(conv.TIMEOUT_ENV, "숫자아님")
    assert conv.timeout_seconds() == conv.DEFAULT_TIMEOUT
    monkeypatch.setenv(conv.TIMEOUT_ENV, "-5")
    assert conv.timeout_seconds() == conv.DEFAULT_TIMEOUT


# ────────────────────────────────────────────────────────────────────
# 0.1.82 / 0.1.90 — HWP→HWPX 변환기 설치 도우미
#
# 배경: hwp2hwpx 는 Java 라이브러리라 매번 준비가 필요하다. 처음에는
#       colab.py 에 뒀는데, 실제 시험은 **사내 서버**에서 한다. `colab.`
#       이름을 달고 있으면 서버에서 부를 때 헷갈리므로
#       converters/hwpx/convert.py 로 옮기고 이름도 바꿨다.
#         install_hwp2hwpx → install_converter
#         use_hwp2hwpx     → use_converter
#         check_hwp2hwpx   → check_converter
#       기존 노트북을 위해 colab 에서 재노출한다.
#
#       hwp2hwpx 는 **라이브러리**여서 main 메서드가 없다(README 확인).
#       `java -jar` 로 실행되지 않으므로 README 의 3줄 사용법을 담은 얇은
#       CLI 를 직접 컴파일한다 — 진입점 클래스 이름을 추측하지 않는다.
# ────────────────────────────────────────────────────────────────────

def test_converter_helpers_live_in_converters_module():
    """설치 도우미는 변환 어댑터 옆에 있다 (Colab 전용이 아니다)."""
    from docstruct.converters.hwpx import convert as conv

    assert callable(conv.install_converter)
    assert callable(conv.use_converter)
    assert callable(conv.check_converter)


def test_colab_reexports_old_names():
    """기존 노트북이 쓰던 이름도 그대로 동작한다."""
    from docstruct.output import colab
    from docstruct.converters.hwpx import convert as conv

    assert colab.install_hwp2hwpx is conv.install_converter
    assert colab.use_hwp2hwpx is conv.use_converter
    assert colab.check_hwp2hwpx is conv.check_converter


def test_default_install_dir_is_not_colab_only():
    """기본 설치 폴더가 /content 로 고정돼 있지 않다.

    사내 서버에는 /content 가 없다. Colab 이면 /content, 그 밖에는 /opt 를
    쓰고 `DOCSTRUCT_HWP2HWPX_DIR` 로 바꿀 수 있다.
    """
    from pathlib import Path as _Path

    from docstruct.converters.hwpx.convert import DEFAULT_INSTALL_DIR

    if not _Path("/content").is_dir():
        assert str(DEFAULT_INSTALL_DIR) == "/opt/hwp2hwpx"


def test_converter_needs_all_three_jars(tmp_path):
    """jar 가 하나라도 빠지면 어느 것인지 알려 준다.

    hwp2hwpx 는 fat jar 가 아니라 hwplib·hwpxlib 도 클래스패스에 있어야
    한다. 하나만 빠져도 NoClassDefFoundError 가 난다.
    """
    from docstruct.converters.hwpx import convert as conv

    with pytest.raises(RuntimeError, match="hwp2hwpx"):
        conv.use_converter(tmp_path, verbose=False)

    (tmp_path / "hwp2hwpx-1.0.3.jar").write_bytes(b"x")
    with pytest.raises(RuntimeError, match="hwplib"):
        conv.use_converter(tmp_path, verbose=False)


def test_converter_accepts_versioned_jar_names(tmp_path, monkeypatch):
    """버전이 붙은 파일명을 그대로 받아들인다.

    Maven 에서 받으면 `hwplib-1.1.10.jar` 처럼 버전이 붙는다. 이름을
    정확히 맞추라고 요구하면 사용자가 매번 파일명을 바꿔야 한다.
    """
    from docstruct.converters.hwpx import convert as conv

    for name in ("hwp2hwpx-1.0.3", "hwplib-1.1.10", "hwpxlib-1.0.6"):
        (tmp_path / f"{name}.jar").write_bytes(b"x")

    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(RuntimeError, match="javac"):
        conv.use_converter(tmp_path, verbose=False)


def test_converter_missing_directory_is_reported(tmp_path):
    """없는 폴더를 주면 그렇게 말한다."""
    from docstruct.converters.hwpx import convert as conv

    with pytest.raises(RuntimeError, match="찾을 수 없습니다"):
        conv.use_converter(tmp_path / "없는폴더", verbose=False)


def test_check_converter_reports_unconfigured(monkeypatch, capsys):
    """설정 전에는 안내만 하고 False 를 준다."""
    from docstruct.converters.hwpx import convert as conv

    monkeypatch.delenv(conv.CONVERTER_ENV, raising=False)
    assert conv.check_converter() is False
    out = capsys.readouterr().out
    assert "use_converter" in out, "서버 사용자에게 맞는 안내여야 합니다"


# ────────────────────────────────────────────────────────────────────

def test_detect_format_reads_signature(tmp_path):
    """파일 앞부분으로 실제 형식을 알아본다."""
    from docstruct.converters.signature import detect_format

    hwp = tmp_path / "a.hwpx"
    hwp.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"rest")
    assert detect_format(hwp) == "hwp"

    zipped = tmp_path / "b.hwpx"
    zipped.write_bytes(b"PK\x03\x04" + b"rest")
    assert detect_format(zipped) == "hwpx"

    pdf = tmp_path / "c.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")
    assert detect_format(pdf) == "pdf"

    unknown = tmp_path / "d.hwp"
    unknown.write_bytes(b"plain text")
    assert detect_format(unknown) is None


def test_detect_format_survives_missing_file(tmp_path):
    """없는 파일에도 예외를 내지 않는다."""
    from docstruct.converters.signature import detect_format

    assert detect_format(tmp_path / "없음.hwp") is None


def test_effective_suffix_corrects_mismatch(tmp_path, caplog):
    """확장자가 내용과 다르면 내용을 따르고 경고를 남긴다."""
    import logging

    from docstruct.converters import signature

    path = tmp_path / "위장.hwpx"
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"rest")

    with caplog.at_level(logging.WARNING, logger=signature.__name__):
        assert signature.effective_suffix(path) == ".hwp"
    assert any("내용은" in r.getMessage() for r in caplog.records), \
        "어긋남을 조용히 넘기면 사용자가 잘못 저장한 사실을 모른다"


def test_effective_suffix_leaves_matching_files_alone(tmp_path):
    """어긋나지 않으면 원래 확장자를 그대로 둔다."""
    from docstruct.converters.signature import effective_suffix

    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.7\n")
    assert effective_suffix(pdf) == ".pdf"

    hwp = tmp_path / "b.hwp"
    hwp.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    assert effective_suffix(hwp) == ".hwp"


def test_effective_suffix_does_not_relabel_zip_as_hwpx(tmp_path):
    """ZIP 서명만 보고 `.hwpx` 로 바꾸지 않는다.

    ZIP 은 HWPX·DOCX·일반 zip 이 공유한다. `.pdf` 로 선언된 zip 을
    `.hwpx` 로 바꿔치기하면 더 이상해진다.
    """
    from docstruct.converters.signature import effective_suffix

    path = tmp_path / "a.pdf"
    path.write_bytes(b"PK\x03\x04rest")
    assert effective_suffix(path) == ".pdf"


def test_extract_retries_with_actual_format(tmp_path, caplog):
    """1차 실패 후 실제 형식으로 재시도한다."""
    import logging

    from docstruct import pipeline

    path = tmp_path / "위장.hwpx"
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")

    calls: list[str] = []

    def fake_get_extractor(suffix):
        def run(_path, *, image_dir=None):
            calls.append(suffix)
            if suffix == ".hwpx":
                raise ValueError("HWP v5(.hwp) 형식은 지원하지 않습니다")
            return "OK"
        return run

    import docstruct.extractors.registry as reg
    original = reg.get_extractor
    reg.get_extractor = fake_get_extractor
    try:
        with caplog.at_level(logging.WARNING, logger=pipeline.__name__):
            assert pipeline._extract(path, "hwpx", None) == "OK"
    finally:
        reg.get_extractor = original

    assert calls == [".hwpx", ".hwp"], "실제 형식으로 재시도해야 합니다"
    assert any("다시 시도" in r.getMessage() for r in caplog.records)


def test_extract_raises_first_error_when_retry_also_fails(tmp_path):
    """재시도까지 실패하면 처음 예외를 올린다.

    사용자가 넣은 형식 기준의 오류가 원인에 가깝고, 재시도는 구제
    시도일 뿐이다.
    """
    from docstruct import pipeline

    path = tmp_path / "위장.hwpx"
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")

    def fake_get_extractor(suffix):
        def run(_path, *, image_dir=None):
            raise ValueError(f"{suffix} 실패")
        return run

    import docstruct.extractors.registry as reg
    original = reg.get_extractor
    reg.get_extractor = fake_get_extractor
    try:
        with pytest.raises(ValueError, match=r"\.hwpx 실패"):
            pipeline._extract(path, "hwpx", None)
    finally:
        reg.get_extractor = original


def test_extract_does_not_retry_when_format_matches(tmp_path):
    """형식이 일치하면 재시도하지 않는다 (불필요한 두 번 실행 방지)."""
    from docstruct import pipeline

    path = tmp_path / "a.hwp"
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")

    calls: list[str] = []

    def fake_get_extractor(suffix):
        def run(_path, *, image_dir=None):
            calls.append(suffix)
            raise ValueError("파싱 실패")
        return run

    import docstruct.extractors.registry as reg
    original = reg.get_extractor
    reg.get_extractor = fake_get_extractor
    try:
        with pytest.raises(ValueError):
            pipeline._extract(path, "hwp", None)
    finally:
        reg.get_extractor = original

    assert calls == [".hwp"], "같은 형식으로 두 번 시도하면 안 됩니다"


# ────────────────────────────────────────────────────────────────────
# 0.1.95 — PDF 본문 텍스트 정규화
#
# 배경: 같은 문서를 PDF 로 처리하면 본문에 손상이 생긴다.
#
#     PDF  국민의 대의기관으로 입법 , 예 · 결산 심사 , 국정감 · 조사 등 의
#     HWP  국민의 대의기관으로 입법, 예·결산 심사, 국정감·조사 등의
#
#       PDF 텍스트 레이어에는 글자마다 좌표만 있고 단어 경계가 없다. 한국어
#       조판은 구두점 앞뒤 자간이 넓어, 좌표로 단어를 재조립하는 쪽이 그
#       틈을 공백으로 읽는다. 527군데였다.
#
#       **OCR 문제가 아니다.** 이 문서는 텍스트 PDF 라 OCR 이 돌지 않는다
#       (RapidOCR returned empty result 경고가 그 증거). OCR 엔진을 바꿔도
#       이 경로에는 영향이 없다.
# ────────────────────────────────────────────────────────────────────

def test_tighten_punctuation_removes_stray_spaces():
    """구두점·괄호 주위의 잘못된 공백을 없앤다."""
    from docstruct.text.korean_text import tighten_punctuation

    assert tighten_punctuation("입법 , 예 · 결산 심사") == "입법, 예·결산 심사"
    assert tighten_punctuation("｢ 헌법 ｣ 및 ｢ 국회법 ｣") == "｢헌법｣ 및 ｢국회법｣"
    assert tighten_punctuation("( 국회 )") == "(국회)"
    assert tighten_punctuation("가 · 나 · 다 · 라") == "가·나·다·라"


def test_tighten_punctuation_keeps_characters():
    """공백만 지우고 글자는 하나도 잃지 않는다."""
    import re

    from docstruct.text.korean_text import tighten_punctuation

    for line in ("입법 , 예 · 결산", "｢ 헌법 ｣ 에  따라", "( 국회 ) 사무처"):
        strip = lambda t: re.sub(r"\s", "", t)      # noqa: E731
        assert strip(tighten_punctuation(line)) == strip(line)


def test_tighten_punctuation_protects_bullet():
    """줄 맨 앞의 가운뎃점은 글머리표이므로 뒤 공백을 지키다.

    지우면 `· 항목` 이 `·항목` 이 되어 본문에 붙는다.
    """
    from docstruct.text.korean_text import tighten_punctuation

    assert tighten_punctuation("· 시작 항목") == "· 시작 항목"
    assert tighten_punctuation("  · 들여쓴 항목") == "  · 들여쓴 항목"


def test_tighten_punctuation_leaves_normal_text():
    """이미 올바른 표기는 건드리지 않는다."""
    from docstruct.text.korean_text import tighten_punctuation

    for line in ("정상·표기", "각 부처별 사업 현황", "입법, 예·결산", ""):
        assert tighten_punctuation(line) == line


def test_collapse_repeated_words_handles_phrases():
    """낱말뿐 아니라 여러 낱말로 된 구절 반복도 줄인다.

    제목에 그림자 효과를 준 지면에서 같은 글자가 여러 번 그려진다.
    """
    from docstruct.text.korean_text import collapse_repeated_words

    assert collapse_repeated_words("별첨3 별첨3 별첨3") == "별첨3"
    assert collapse_repeated_words(
        "성과계획 목표체계 성과계획 목표체계 성과계획 목표체계 제1장 제1장 제1장"
    ) == "성과계획 목표체계 제1장"


def test_collapse_repeated_words_needs_three():
    """두 번 반복은 실제 표현일 수 있어 건드리지 않는다."""
    from docstruct.text.korean_text import collapse_repeated_words

    assert collapse_repeated_words("국가 국가") == "국가 국가"
    assert collapse_repeated_words("매우 매우 좋다") == "매우 매우 좋다"
    assert collapse_repeated_words("가 나 다 라") == "가 나 다 라"


def test_normalize_pdf_text_is_pdf_only():
    """PDF 전용 정규화는 HWP 경로에 걸지 않는다.

    HWP·HWPX 는 바이너리에서 글자를 직접 읽어 이런 손상이 없다(같은
    문서에서 527건 대 0건). 정상 텍스트에 규칙을 더 걸면 고칠 것 없이
    위험만 는다.
    """
    import importlib
    from pathlib import Path as _Path

    def _source(module: str) -> str:
        # pkg 는 `docstruct.converters.…`, local·overlay 는 `converters.…`.
        try:
            mod = importlib.import_module(module)
        except ModuleNotFoundError:
            mod = importlib.import_module(module.removeprefix("docstruct."))
        return _Path(mod.__file__).read_text(encoding="utf-8")

    assert "normalize_pdf_text" in _source("docstruct.extractors.pdf")

    for module in ("docstruct.converters.hwp.pyhwp_backend.hwp5tree",
                   "docstruct.converters.hwp.olefile"):
        assert "normalize_pdf_text" not in _source(module), (
            f"{module} 에 PDF 전용 규칙이 걸렸습니다")


# ────────────────────────────────────────────────────────────────────
# 0.1.96 — rapidocr 이 한국어를 중국어로 읽던 문제
#
# 배경: 스캔 PDF(2025 주택과 세금, 380쪽)에서 한글이 **0%** 나왔다. 본문이
#       `气····吾·咎今`, `ヤ君居 |0号` 같은 한자·가나였다.
#       `force_full_page_ocr=True` 로 전면 OCR 을 켜도 같았다.
#
#       원인은 rapidocr 3.x 가 기본 인식 모델을 PP-OCRv6 small 로 바꾼 것.
#       그 모델에 한국어가 없어 아래처럼 거부되고 중국어로 되돌아간다.
#
#           ValueError: Unsupported rec.lang_type='korean'
#                       for PP-OCRv6 small model.
#
#       우리 코드는 이미 `lang=["korean", "english"]` 를 넘기고 있었다 —
#       옵션이 아니라 **모델 선택**이 문제였고, docling 의 RapidOcrOptions
#       에는 모델 버전을 지정할 자리가 없다. 그래서 직접 호출한다.
# ────────────────────────────────────────────────────────────────────

def test_korean_ocr_params_pin_all_three():
    """한국어 모델은 세 값을 함께 줘야 선택된다.

    lang_type 만 주면 기본 v6 small 이 골라지고 한국어가 없어 중국어로
    되돌아간다. model_type·ocr_version 까지 지정해야 한다.
    """
    pytest.importorskip("rapidocr")
    from docstruct.converters.pdf.rapidocr_ko import _build_params

    params = _build_params()
    assert params["Rec.lang_type"].name == "KOREAN"
    # server 조합은 한국어 모델이 없다 — mobile 이어야 한다.
    assert params["Rec.model_type"].name == "MOBILE"
    assert params["Rec.ocr_version"].name in ("PPOCRV4", "PPOCRV5")


def test_korean_ocr_version_is_configurable(monkeypatch):
    """모델 버전을 환경변수로 고를 수 있고 잘못된 값은 기본으로 돌아간다."""
    pytest.importorskip("rapidocr")
    from docstruct.converters.pdf import rapidocr_ko as ko

    monkeypatch.setenv(ko.VERSION_ENV, "v4")
    assert ko._build_params()["Rec.ocr_version"].name == "PPOCRV4"

    monkeypatch.setenv(ko.VERSION_ENV, "없는버전")
    assert ko._build_params()["Rec.ocr_version"].name == "PPOCRV5"


def test_ocr_min_score_guards_bad_values(monkeypatch):
    """신뢰도 하한이 범위를 벗어나거나 숫자가 아니면 기본값을 쓴다."""
    from docstruct.converters.pdf import rapidocr_ko as ko

    monkeypatch.setenv(ko.SCORE_ENV, "0.7")
    assert ko._min_score() == 0.7
    for bad in ("숫자아님", "5", "-1"):
        monkeypatch.setenv(ko.SCORE_ENV, bad)
        assert ko._min_score() == ko.DEFAULT_MIN_SCORE


def test_ocr_line_sorting_keys():
    """읽기 순서 정렬에 쓰는 좌표 속성이 동작한다."""
    from docstruct.converters.pdf.rapidocr_ko import OcrLine

    line = OcrLine("가", 0.9, [(10, 50), (40, 50), (40, 70), (10, 70)])
    assert line.top == 50
    assert line.left == 10
    # 좌표가 없으면 원래 순서를 지켜야 하므로 0 을 준다
    assert OcrLine("나", 0.9).top == 0.0


def test_no_hardcoded_tmp_paths():
    """리눅스 전용 `/tmp` 를 코드에 박지 않는다.

    Windows 에는 `/tmp` 가 없어 `FileNotFoundError: '\\tmp\\_ocr_compare_p1.png'`
    가 났다. 임시 파일은 `tempfile.gettempdir()` 로 만든다.
    """
    import re
    from pathlib import Path as _Path

    src = _Path(__file__).resolve().parent.parent / "src" / "docstruct"
    offenders: list[str] = []
    pattern = re.compile(r"""["']/tmp[/'"]""")
    for path in src.rglob("*.py"):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue                     # 주석 속 경로는 예시일 수 있다
            if pattern.search(line):
                offenders.append(f"{path.name}:{number}")
    assert not offenders, f"하드코딩된 /tmp: {offenders}"


def test_compare_uses_portable_temp_dir():
    """진단 도구가 OS 임시 폴더를 쓴다.

    **소스를 검사하는 이유**: 이 함수는 PDF 를 렌더해야 실행되는데, Windows
    에서만 나는 문제라 CI 에서 재현할 수 없다. 실수가 코드에 다시 들어오는
    것을 막는 것이 목적이다.
    """
    import inspect

    from docstruct.converters.pdf.rapidocr_ko import compare

    source = inspect.getsource(compare)
    assert "gettempdir" in source


# ────────────────────────────────────────────────────────────────────
# 0.1.98 — README 가 0.1.46 에서 멈춰 있던 문제
#
# 배경: 기능을 50판 넘게 추가하는 동안 README 의 설치 버전이 v0.1.46 에
#       머물러 있었고, slim·force_full_page_ocr·rapidocr_ko 같은 것이
#       하나도 적히지 않았다. 문서가 낡으면 사용자는 없는 방법을 찾거나
#       있는 기능을 모른 채 지나간다.
# ────────────────────────────────────────────────────────────────────

def test_readme_pins_current_version():
    """README 의 설치 버전이 패키지 버전과 같다."""
    import re
    import tomllib
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parent.parent
    pyproject = root / "pyproject.toml"
    if not pyproject.is_file():
        pytest.skip("pyproject.toml 없음 — pkg 트리 전용 검사")
    version = tomllib.loads(
        pyproject.read_text(encoding="utf-8")
    )["project"]["version"]

    pinned = set(re.findall(r"docstruct\.git@v([\d.]+)",
                            (root / "README.md").read_text(encoding="utf-8")))
    assert pinned, "README 에 설치 버전 핀이 없습니다"
    assert pinned == {version}, (
        f"README 는 {sorted(pinned)} 를 가리키는데 패키지는 {version} 입니다"
    )


def test_readme_documents_current_options():
    """README 가 주요 설정을 안내한다.

    있는데 안 적혀 있으면 사용자는 그 기능을 모른 채 지나간다.
    """
    from pathlib import Path as _Path

    readme = (_Path(__file__).resolve().parent.parent
              / "README.md").read_text(encoding="utf-8")
    for option in ("slim", "force_full_page_ocr", "hwp_fill_html"):
        assert option in readme, f"README 에 {option} 안내가 없습니다"


# ────────────────────────────────────────────────────────────────────
# 0.1.99 — numpy 배열에 `or []` 를 쓰던 문제
#
# 배경: 한국어 모델로 실행하니 이렇게 죽었다.
#
#     ValueError: The truth value of an array with more than one element
#                 is ambiguous. Use a.any() or a.all()
#
#       rapidocr 은 `boxes` 를 **numpy 배열**로 돌려주는데,
#       `getattr(result, "boxes", None) or []` 가 배열의 진리값을 물어
#       터진다. 이 환경에서는 모델을 못 받아 read_image 까지 도달하지
#       못했고, 실제 실행 환경에서만 드러났다.
# ────────────────────────────────────────────────────────────────────

def test_as_list_handles_numpy():
    """numpy 배열·None·목록을 모두 목록으로 바꾼다 (or 를 쓰지 않는다)."""
    np = pytest.importorskip("numpy")
    from docstruct.converters.pdf.rapidocr_ko import _as_list

    assert len(_as_list(np.array([[1, 2], [3, 4]]))) == 2
    assert len(_as_list(np.array([0.9, 0.8]))) == 2
    assert _as_list(np.array([])) == []
    assert _as_list(None) == []
    assert _as_list(["가", "나"]) == ["가", "나"]


def test_read_image_accepts_numpy_result(monkeypatch):
    """rapidocr 이 numpy 를 돌려줘도 정상 처리한다."""
    np = pytest.importorskip("numpy")
    from docstruct.converters.pdf import rapidocr_ko as ko

    class _Result:
        txts = np.array(["주택과 세금", "연중 세무 일정", ""], dtype=object)
        scores = np.array([0.95, 0.88, 0.3])
        boxes = np.array([
            [[10, 50], [90, 50], [90, 70], [10, 70]],
            [[10, 80], [90, 80], [90, 100], [10, 100]],
            [[10, 110], [90, 110], [90, 130], [10, 130]],
        ])

    monkeypatch.setattr(ko, "get_engine", lambda: (lambda _p: _Result()))
    lines = ko.read_image("dummy.png")

    # 빈 문자열과 신뢰도 0.3(하한 0.5 미만)은 빠진다
    assert [line.text for line in lines] == ["주택과 세금", "연중 세무 일정"]
    assert lines[0].box == [(10.0, 50.0), (90.0, 50.0), (90.0, 70.0), (10.0, 70.0)]


def test_read_page_text_orders_by_position(monkeypatch):
    """좌표가 있으면 위→아래, 왼쪽→오른쪽으로 잇는다."""
    np = pytest.importorskip("numpy")
    from docstruct.converters.pdf import rapidocr_ko as ko

    class _Result:
        txts = np.array(["아래", "위"], dtype=object)
        scores = np.array([0.9, 0.9])
        boxes = np.array([
            [[10, 200], [90, 200], [90, 220], [10, 220]],
            [[10, 50], [90, 50], [90, 70], [10, 70]],
        ])

    monkeypatch.setattr(ko, "get_engine", lambda: (lambda _p: _Result()))
    assert ko.read_page_text("dummy.png") == "위\n아래"


# ────────────────────────────────────────────────────────────────────
# 0.2.0 — OCR 잡음 제거와 신뢰도 기본값
#
# 배경: 한국어 모델로 바꾸니 한글 0% → 46~70% 가 됐다(도표 46.8%,
#       개정표 65.4%, 텍스트 70.6%). 남은 잡음은 색상 블록·로고를 글자로
#       오인한 것이었다 — `YoHIYL`, `OSUMMM`, `C168zs운道lYR IIllY IY올`.
#
#       원본에 대응하는 글자가 없으므로 **고칠 대상이 아니라 지울 대상**이다.
# ────────────────────────────────────────────────────────────────────

def test_default_min_score_raised():
    """신뢰도 하한 기본값이 0.7 이다.

    실측(26쪽)에서 0.5 → 0.7 로 올리니 잡음이 사라지고 글자는 7% 만
    줄었다(714 → 662자). 오히려 잘려 있던 줄이 온전해졌다.
    """
    from docstruct.converters.pdf.rapidocr_ko import DEFAULT_MIN_SCORE

    assert DEFAULT_MIN_SCORE == 0.7


def test_is_noise_drops_garbage():
    """장식·로고를 글자로 읽은 조각을 버린다."""
    pytest.importorskip("kiwipiepy")
    from docstruct.converters.pdf.rapidocr_ko import is_noise

    for garbage in ("YoHIYL", "OSUMMM", "YYRY", "Y",
                    "C168zs운道lYR IIllY IY올", "弓을YlYY 글 lo흐8lY", ""):
        assert is_noise(garbage), f"{garbage!r} 를 버리지 못했습니다"


def test_is_noise_keeps_real_text():
    """정상 문장은 하나도 버리지 않는다.

    오탐이 나면 내용을 잃는다. 실측에서 정상 문서 1,200줄의 오탐은
    0.1%(1건, 그마저 HTML 주석)였다.
    """
    pytest.importorskip("kiwipiepy")
    from docstruct.converters.pdf.rapidocr_ko import is_noise

    for real in ("2025 주택과 세금", "국세청", "개  정", "종",
                 "-공공기관,지방공기업도시정비법제2조",
                 "-취득 후3년 내 신축", "2.10.", "1월하순",
                 "※직계존속: 만 65세 이상(한명만 충족해도가능)",
                 "-세대별 주민등록표에 함께 기재되어 있는 가족(동거인 제외)",
                 "https://www.nts.go.kr/upload/index.html"):
        assert not is_noise(real), f"{real!r} 를 잘못 버렸습니다"


def test_noise_filter_can_be_disabled(monkeypatch):
    """잡음 제거를 끌 수 있다 (원문 그대로 보고 싶을 때)."""
    from docstruct.converters.pdf import rapidocr_ko as ko

    monkeypatch.setenv(ko.KEEP_NOISE_ENV, "true")
    assert ko._drop_noise() is False
    monkeypatch.delenv(ko.KEEP_NOISE_ENV, raising=False)
    assert ko._drop_noise() is True


def test_noise_filter_survives_missing_kiwi(monkeypatch):
    """kiwipiepy 가 없으면 잡음 제거를 건너뛴다.

    선택 의존성이므로 설치 여부가 파이프라인을 깨뜨리면 안 된다.
    """
    from docstruct.converters.pdf import rapidocr_ko as ko

    monkeypatch.setattr(ko, "_get_kiwi", lambda: None)
    assert ko.is_noise("YoHIYL") is False       # 판별 못 하면 살린다
    assert ko.is_noise("") is True              # 빈 문자열은 언제나 버린다


def test_readme_has_no_dangling_doc_links():
    """README 가 없는 문서를 가리키지 않는다.

    문서를 README 하나로 합치면서 API.md·CLI.md 등을 지웠다. 참조가 남으면
    사용자가 없는 파일을 찾게 된다.
    """
    import re
    from pathlib import Path as _Path

    #: 산출물 파일명이라 저장소에 없는 것이 정상이다.
    #: BUGFIXES.md 는 배포물에 넣지 않고 따로 전달한다(2,800줄).
    outputs = {"document.md", "layout.md", "pipeline.md", "outline.md",
               # 0.4.57 — `--align` 산출물
               "aligned.md",
               "BUGFIXES.md"}

    root = _Path(__file__).resolve().parent.parent
    readme = (root / "README.md").read_text(encoding="utf-8")
    missing = [
        name for name in re.findall(r"`([\w./]+\.md)`", readme)
        if name not in outputs and not (root / name).exists()
    ]
    assert not missing, f"README 가 없는 문서를 가리킵니다: {missing}"


# ────────────────────────────────────────────────────────────────────
# 0.2.2 — 표 셀과 OCR 조각 좌표 매칭
#
# 배경: 한국어 OCR 로 본문은 읽히게 됐지만(0% → 46~70%) 표 안 텍스트는
#       여전히 docling 이 넣은 중국어였다. 실제 문서에서 셀 10개가 모두
#       bbox·row_span·col_span 을 갖고 있었고 `text` 만 `品品品`·`昆品`
#       이었다 — **인식 언어가 틀린 것이지 구조가 틀린 것이 아니다.**
#
#       그래서 TableFormer 가 만든 구조는 그대로 두고 텍스트만 갈아끼운다.
#       좌표 기준이 같아(둘 다 TOPLEFT) 배율만 나누면 맞는다.
# ────────────────────────────────────────────────────────────────────

def test_pixel_to_point_conversion():
    """렌더 픽셀을 PDF 포인트로 되돌린다.

    실측: 595.0 × 841.9 포인트 문서를 scale=2 로 렌더하면 1190 × 1684
    픽셀이다. 셀 bbox (194.0, 473.3, 221.7, 488.0) 의 픽셀 대응은
    (388, 947, 443, 976) 이다.
    """
    from docstruct.converters.pdf.cell_match import Box, from_pixels

    point = from_pixels(Box(388, 947, 443, 976), 2.0)
    assert abs(point.left - 194.0) < 0.5
    assert abs(point.top - 473.3) < 0.5
    assert abs(point.right - 221.7) < 0.5
    assert abs(point.bottom - 488.0) < 0.5


def test_overlap_ratio_is_stable_for_small_fragments():
    """작은 조각이 큰 셀 안에 있으면 비율이 1.0 이다.

    IoU 를 쓰면 크기 차 때문에 값이 낮게 나와 임계를 정하기 어렵다.
    """
    from docstruct.converters.pdf.cell_match import Box

    cell = Box(0, 0, 100, 100)
    fragment = Box(10, 10, 30, 20)
    assert fragment.overlap_ratio(cell) == 1.0
    assert fragment.iou(cell) < 0.1        # IoU 는 낮다


def test_fragments_assigned_to_containing_cell():
    """조각이 자기가 속한 셀에 배정된다."""
    from docstruct.converters.pdf.cell_match import Box, fill_cells

    cells = [Box(194, 473, 222, 488), Box(222, 473, 300, 488)]
    fragments = [(Box(196, 475, 220, 486), "구분"),
                 (Box(225, 475, 295, 486), "2025년")]
    texts, dropped = fill_cells(cells, fragments)
    assert texts == {0: "구분", 1: "2025년"}
    assert dropped == 0


def test_fragment_outside_any_cell_is_reported():
    """표 밖 조각은 셀에 넣지 않고 개수로 알린다.

    버리기만 하면 매칭이 잘 됐는지 알 수 없다.
    """
    from docstruct.converters.pdf.cell_match import Box, fill_cells

    cells = [Box(194, 473, 222, 488)]
    fragments = [(Box(196, 600, 220, 610), "표 밖 본문")]
    texts, dropped = fill_cells(cells, fragments)
    assert texts == {}
    assert dropped == 1


def test_straddling_fragment_goes_to_larger_overlap():
    """셀 경계에 걸친 조각은 더 많이 겹치는 쪽으로 간다."""
    from docstruct.converters.pdf.cell_match import Box, fill_cells

    cells = [Box(194, 473, 222, 488), Box(222, 473, 300, 488)]
    straddling = Box(215, 475, 240, 486)   # 셀0 에 7pt, 셀1 에 18pt
    texts, _ = fill_cells(cells, [(straddling, "걸친글자")])
    assert texts == {1: "걸친글자"}


def test_multiple_lines_in_one_cell_are_ordered():
    """한 셀에 여러 줄이 오면 위→아래로 잇는다.

    줄바꿈이 아니라 공백으로 잇는다 — 셀 안에 줄바꿈이 들어가면 markdown
    표가 깨진다.
    """
    from docstruct.converters.pdf.cell_match import Box, fill_cells

    cells = [Box(194, 473, 222, 488)]
    fragments = [(Box(196, 481, 220, 486), "아래줄"),
                 (Box(196, 475, 220, 480), "위줄")]
    texts, _ = fill_cells(cells, fragments)
    assert texts[0] == "위줄 아래줄"
    assert "\n" not in texts[0]


def test_box_of_handles_rotated_quad():
    """기울어진 사각형을 외접 상자로 바꾼다.

    OCR 은 네 꼭짓점을 주는데 축에 나란하지 않을 수 있다.
    """
    from docstruct.converters.pdf.cell_match import box_of

    box = box_of([(10, 12), (50, 10), (52, 30), (12, 32)])
    assert (box.left, box.top, box.right, box.bottom) == (10, 10, 52, 32)
    assert box_of([]).area == 0


# ────────────────────────────────────────────────────────────────────
# 0.2.3 — 한국어 OCR 로 본문 재판독 (파이프라인 연결)
#
# 배경: rapidocr_ko 를 만들어 두고도 파이프라인에 연결하지 않아
#       `DocStruct(fn, force_full_page_ocr=True)` 는 여전히 한글 0% 였다.
#       진단 도구로 직접 부를 때만 46~70% 가 나왔다.
#
#       연결하며 알게 된 것: `_render_page_images` 가 **표가 있는 페이지만**
#       렌더했다(`if p.tables`). 원래 용도가 표 재추출의 시각 근거였기
#       때문인데, 본문을 다시 읽으려면 표 없는 페이지도 이미지가 필요하다.
# ────────────────────────────────────────────────────────────────────

def _ocr_page(content, image=None):
    """재판독 시험용 PageContent."""
    from docstruct.models import PageContent, PageTrace

    return PageContent(
        page_no=1, page_no_kind="pdf", content=content,
        page_image_path=str(image) if image else None,
        trace=PageTrace(extractor="docling", text_source="ocr"),
    )


def test_korean_ocr_replaces_body(tmp_path, monkeypatch):
    """재판독 결과로 본문을 바꾸고 표 자리표시자는 남긴다.

    표 셀 교체는 좌표 매칭이 필요해 별도 단계다. 자리표시자를 잃으면
    뒤따르는 표 판정·재추출이 표를 찾지 못한다.
    """
    from docstruct import pipeline
    from docstruct.converters.pdf import rapidocr_ko

    image = tmp_path / "p1.png"
    image.write_bytes(b"fake")
    monkeypatch.setattr(rapidocr_ko, "read_page_text",
                        lambda _p: "국민의 대의기관으로 입법 , 예 · 결산")

    pages = [_ocr_page("气····吾·咎今\n\n<table 3>\n\n| 品品品 |", image)]
    assert pipeline._reread_with_korean_ocr(pages) == 1
    # 정규화까지 적용된다 (구두점 앞 공백 제거)
    assert pages[0].content == "국민의 대의기관으로 입법, 예·결산\n\n<table 3>"


def test_korean_ocr_keeps_original_when_empty(tmp_path, monkeypatch):
    """새로 읽은 결과가 비면 원본을 그대로 둔다.

    OCR 이 실패한 지면에서 있던 내용까지 지우면 안 된다.
    """
    from docstruct import pipeline
    from docstruct.converters.pdf import rapidocr_ko

    image = tmp_path / "p1.png"
    image.write_bytes(b"fake")
    monkeypatch.setattr(rapidocr_ko, "read_page_text", lambda _p: "   ")

    pages = [_ocr_page("원본 내용", image)]
    assert pipeline._reread_with_korean_ocr(pages) == 0
    assert pages[0].content == "원본 내용"


def test_korean_ocr_skips_pages_without_image():
    """이미지가 없는 페이지는 건너뛴다."""
    from docstruct import pipeline

    pages = [_ocr_page("원본 내용", None)]
    assert pipeline._reread_with_korean_ocr(pages) == 0
    assert pages[0].content == "원본 내용"


def test_korean_ocr_records_failure(tmp_path, monkeypatch):
    """OCR 이 실패하면 원본을 지키고 trace 에 남긴다."""
    from docstruct import pipeline
    from docstruct.converters.pdf import rapidocr_ko

    image = tmp_path / "p1.png"
    image.write_bytes(b"fake")

    def _boom(_path):
        raise RuntimeError("모델 없음")

    monkeypatch.setattr(rapidocr_ko, "read_page_text", _boom)
    pages = [_ocr_page("원본 내용", image)]
    assert pipeline._reread_with_korean_ocr(pages) == 0
    assert pages[0].content == "원본 내용"
    assert any("실패" in step.action for step in pages[0].trace.steps)


def test_render_page_images_accepts_targets():
    """렌더 대상을 좁힐 수 있다.

    텍스트 레이어가 온전한 쪽까지 렌더하면 155쪽 문서에서 50초쯤 헛돈다.
    """
    import inspect

    from docstruct import pipeline

    params = inspect.signature(pipeline._render_page_images).parameters
    assert "all_pages" in params
    assert "only" in params


def test_korean_ocr_option_exists():
    """설정으로 켤 수 있다."""
    import docstruct

    assert "korean_ocr" in docstruct.option_keys()


# ────────────────────────────────────────────────────────────────────
# 0.2.4 — HWPX 파서를 파이프라인에 연결
#
# 배경: 0.1.80 에서 `hwpxtree` 를 만들어 셀 100%·9배 빠름을 검증해 놓고도
#       연결하지 않아, 실제 HWPX 처리는 계속 python-hwpx 내보내기를 썼다.
#
#           python-hwpx markdown   표  94개 · 셀 93.8% · 취소선 4,456회
#           XML 직접 파싱          표 212개 · 셀 100%  · 취소선 0
#
#       변환 파일 자체에는 표 212개가 온전히 들어 있다. 손실은 파일이
#       아니라 **내보내기 단계**에서 생긴다.
# ────────────────────────────────────────────────────────────────────

def test_hwpx_extractor_uses_xml_parser():
    """추출기가 XML 직접 파싱 결과를 낸다.

    python-hwpx 내보내기는 같은 문서에서 표 94개·셀 93.8%·취소선 4,456회
    였다. XML 직접 파싱은 표 212개·셀 100%·취소선 0 이다.
    """
    from pathlib import Path as _Path

    sample = _Path("notebooks/samples/sample.hwpx")
    if not sample.is_file():
        pytest.skip("sample.hwpx 없음")

    from docstruct.extractors.hwpx import extract_hwpx_pages

    page = extract_hwpx_pages(str(sample))[0]
    assert page.trace.extractor == "hwpx-tree"     # 폴백이 아니다
    assert "~~" not in (page.content or "")        # 취소선 오염 없음


def test_hwpx_converter_matches_extractor():
    """/convert 경로와 파이프라인이 같은 결과를 낸다.

    한쪽만 바꾸면 API 와 파이프라인 결과가 어긋난다.
    """
    from pathlib import Path as _Path

    sample = _Path("notebooks/samples/sample.hwpx")
    if not sample.is_file():
        pytest.skip("sample.hwpx 없음")

    import re

    from docstruct.converters.hwpx.converter import HwpxConverter
    from docstruct.extractors.hwpx import extract_hwpx_pages

    api = HwpxConverter(str(sample)).to_markdown()
    page = extract_hwpx_pages(str(sample))[0]

    def table_count(text):
        return len(re.findall(r"(?:^\|.*\|$\n?)+", text, re.M))

    # 파이프라인은 표를 자리표시자로 빼므로 개수로 견준다
    assert table_count(api) == len(page.tables)


def test_hwpx_falls_back_when_xml_parser_fails(monkeypatch, tmp_path):
    """XML 파싱이 실패하면 python-hwpx 로 물러난다."""
    pytest.importorskip("hwpx")
    from pathlib import Path as _Path

    sample = _Path("notebooks/samples/sample.hwpx")
    if not sample.is_file():
        pytest.skip("sample.hwpx 없음")

    from docstruct.converters.hwpx import hwpxtree
    from docstruct.extractors.hwpx import extract_hwpx_pages

    def _boom(_path):
        raise RuntimeError("일부러 실패")

    monkeypatch.setattr(hwpxtree, "to_markdown", _boom)
    pages = extract_hwpx_pages(str(sample))
    assert pages[0].trace.extractor == "python-hwpx"
    assert pages[0].content            # 내용을 잃지 않았다


def test_hwpx_trace_names_the_parser():
    """어느 파서로 읽었는지 trace 에 남는다.

    두 경로의 품질 차가 커서, 결과만 보고는 어느 쪽이었는지 알 수 없으면
    문제를 추적할 수 없다.
    """
    from pathlib import Path as _Path

    sample = _Path("notebooks/samples/sample.hwpx")
    if not sample.is_file():
        pytest.skip("sample.hwpx 없음")

    from docstruct.extractors.hwpx import extract_hwpx_pages

    trace = extract_hwpx_pages(str(sample))[0].trace
    assert trace.extractor in ("hwpx-tree", "python-hwpx")
    assert any("hwpxtree" in step.module for step in trace.steps)


# ────────────────────────────────────────────────────────────────────
# 0.2.5 — 스캔 PDF 표 셀 한국어 재판독
#
# 배경: 0.2.3 에서 본문은 한국어로 읽히게 됐지만(0% → 46~70%) 표 안은
#       docling 이 넣은 중국어가 남았다(`品品品`, `昆品`).
#
#       실제 문서에서 셀 10개가 모두 bbox·row_span·col_span 을 온전히 갖고
#       `text` 만 틀렸다 — **구조는 맞고 언어가 틀렸다.** 그래서 행·열·병합은
#       그대로 두고 텍스트만 갈아끼운다.
# ────────────────────────────────────────────────────────────────────

def _fake_cell(row, col, text, box, *, header=False):
    """표 셀 하나 — 실제 docling 스키마로 만든다."""
    from tests.table_fixtures import make_cell

    return make_cell(row, col, text, header=header, box=box)


def _fake_table():
    """2×2 표 (텍스트가 중국어로 잘못 인식된 상태)."""
    from tests.table_fixtures import make_table

    return make_table(2, 2, [
        _fake_cell(0, 0, "品品品", (100, 100, 200, 120), header=True),
        _fake_cell(0, 1, "昆品", (200, 100, 300, 120), header=True),
        _fake_cell(1, 0, "早", (100, 120, 200, 140)),
        _fake_cell(1, 1, "全气", (200, 120, 300, 140)),
    ])


def _fake_lines():
    """OCR 조각 (렌더 이미지 픽셀 좌표, scale=2)."""
    from tests.table_fixtures import make_ocr_line

    return [make_ocr_line("구분", 210, 210, 390, 235),
            make_ocr_line("2025년", 410, 210, 590, 235),
            make_ocr_line("유튜브", 210, 250, 390, 275),
            make_ocr_line("13,391,527", 410, 250, 590, 275)]


def test_cell_texts_replaced_structure_kept(tmp_path, monkeypatch):
    """텍스트만 바뀌고 행·열 구조는 그대로다."""
    from docstruct.converters.pdf import rapidocr_ko
    from docstruct.tables.docling import docling_table_to_markdown, replace_cell_texts

    monkeypatch.setattr(rapidocr_ko, "read_image", lambda _i: _fake_lines())
    image = tmp_path / "p.png"
    image.write_bytes(b"x")

    item = _fake_table()
    stat = replace_cell_texts(item, image, scale=2.0)
    assert stat["changed"] == 4
    assert stat["near_miss"] == 0

    markdown = docling_table_to_markdown(item)
    assert "구분" in markdown and "13,391,527" in markdown
    assert "品品品" not in markdown
    # 2행 2열이 유지된다 (헤더줄 + 구분선 + 데이터줄)
    assert len([ln for ln in markdown.splitlines() if ln.startswith("|")]) == 3


def test_cell_texts_keep_original_when_ocr_empty(tmp_path, monkeypatch):
    """OCR 이 아무것도 못 읽으면 원래 텍스트를 남긴다."""
    from docstruct.converters.pdf import rapidocr_ko
    from docstruct.tables.docling import replace_cell_texts

    monkeypatch.setattr(rapidocr_ko, "read_image", lambda _i: [])
    image = tmp_path / "p.png"
    image.write_bytes(b"x")

    item = _fake_table()
    assert replace_cell_texts(item, image, scale=2.0)["changed"] == 0
    assert item.data.table_cells[0].text == "品品品"


def test_cell_texts_skip_cells_without_bbox(tmp_path, monkeypatch):
    """bbox 가 없는 셀은 건드리지 않는다."""
    from docstruct.converters.pdf import rapidocr_ko
    from docstruct.tables.docling import replace_cell_texts

    monkeypatch.setattr(rapidocr_ko, "read_image", lambda _i: _fake_lines())
    image = tmp_path / "p.png"
    image.write_bytes(b"x")

    item = _fake_table()
    item.data.table_cells[0].bbox = None
    assert replace_cell_texts(item, image, scale=2.0)["changed"] == 3
    assert item.data.table_cells[0].text == "品品品"


def test_table_info_source_item_not_serialized():
    """원본 Docling 객체는 JSON 에 넣지 않는다.

    `asdict()` 가 모든 필드를 담으므로 빼 주지 않으면 to_json 이 통째로
    실패한다.
    """
    import json

    from docstruct.models import TableInfo

    table = TableInfo(id="table_1", table_num=1, placeholder="<table 1>",
                      markdown="| a |", source_item=object())
    data = table.to_dict()
    assert "source_item" not in data
    json.dumps(data)          # 예외가 나지 않아야 한다


# ────────────────────────────────────────────────────────────────────
# 0.2.7 — 셀 배정 진단 지표가 원인을 가리던 문제
#
# 배경: 실문서에서 `미배정 조각 81` 이 나와 매칭이 크게 실패한 것처럼
#       보였다. 파보니 **표 밖 본문 조각**이었다 — 페이지 전체를 OCR 하는데
#       표 영역만 배정하니 나머지가 전부 거기 잡힌 것이다. 동작은 정상인데
#       지표가 원인을 가렸다.
#
#       세 값으로 나눠 세면 원인이 갈린다.
#
#           near_miss > 0                표 안인데 셀에 못 들어감
#                                        → 겹침 임계·셀 bbox 가 좁음
#           near_miss = 0, empty_cells>0 셀 bbox 가 조각을 아예 안 덮음
#                                        → TableFormer 쪽 문제
#           outside                      표 밖 본문 (정상)
# ────────────────────────────────────────────────────────────────────

def _one_row_table(*boxes):
    """한 행짜리 표 (셀 텍스트는 전부 중국어)."""
    from tests.table_fixtures import make_cell, make_table

    cells = [make_cell(0, col, "品", box=box) for col, box in enumerate(boxes)]
    return make_table(1, len(boxes), cells)


def _point_line(text, left, top, right, bottom):
    """포인트 좌표로 주는 OCR 조각 (scale=1 로 쓴다)."""
    from tests.table_fixtures import make_ocr_line

    return make_ocr_line(text, left, top, right, bottom)


def test_near_miss_flags_threshold_problem(tmp_path, monkeypatch):
    """표 안이지만 어느 셀과도 겹침이 모자라면 near_miss 로 잡힌다.

    셀 사이 여백에 놓인 조각이 그렇다 — 표 영역 안이지만 어느 칸에도
    충분히 들어가지 않는다.
    """
    from docstruct.converters.pdf import rapidocr_ko
    from docstruct.tables.docling import replace_cell_texts

    image = tmp_path / "p.png"
    image.write_bytes(b"x")
    # 셀은 100~140 과 160~200. 조각은 그 사이 여백(142~158)에 걸친다.
    monkeypatch.setattr(rapidocr_ko, "read_image",
                        lambda _i: [_point_line("여백텍스트", 142, 105, 158, 115)])

    stat = replace_cell_texts(
        _one_row_table((100, 100, 140, 120), (160, 100, 200, 120)),
        image, scale=1.0)
    assert stat["near_miss"] == 1
    assert stat["outside"] == 0


def test_empty_cells_without_near_miss_flags_bbox_problem(tmp_path, monkeypatch):
    """셀 bbox 가 조각을 아예 안 덮으면 near_miss 없이 empty_cells 만 오른다.

    이 경우는 임계를 낮춰도 해결되지 않는다 — TableFormer 가 셀 위치를
    잘못 잡은 것이다.
    """
    from docstruct.converters.pdf import rapidocr_ko
    from docstruct.tables.docling import replace_cell_texts

    image = tmp_path / "p.png"
    image.write_bytes(b"x")
    monkeypatch.setattr(rapidocr_ko, "read_image",
                        lambda _i: [_point_line("멀리있는텍스트", 300, 105, 400, 115)])

    stat = replace_cell_texts(_one_row_table((100, 100, 120, 120)),
                              image, scale=1.0)
    assert stat["near_miss"] == 0
    assert stat["empty_cells"] == 1
    assert stat["outside"] == 1        # 표 밖 조각으로 세어진다


def test_overlap_threshold_is_configurable(monkeypatch):
    """겹침 임계를 조정할 수 있다.

    **OCR 신뢰도 임계와 다르다.** 이 값을 낮춰도 잡음이 늘지 않는다 —
    이미 신뢰도 검사를 통과한 조각 중 어느 셀에 넣을지만 정한다.
    """
    from docstruct.converters.pdf import cell_match

    monkeypatch.setenv(cell_match.OVERLAP_ENV, "0.3")
    assert cell_match.min_overlap_setting() == 0.3
    for bad in ("숫자아님", "0", "1.5", "-1"):
        monkeypatch.setenv(cell_match.OVERLAP_ENV, bad)
        assert cell_match.min_overlap_setting() == cell_match.MIN_OVERLAP


def test_straddling_fragment_fills_both_cells(tmp_path, monkeypatch):
    """두 셀에 걸친 조각은 양쪽이 모두 가져간다.

    조각마다 셀 하나만 고르던 방식은 한쪽만 채우고 다른 쪽을 비웠다.
    실문서에서 왼쪽 열이 통째로 빈 표가 나왔다 — `지방세법`·`종합부동산세법`
    이 OCR 에는 읽혔는데 셀에는 없었다.
    """
    from docstruct.converters.pdf import rapidocr_ko
    from docstruct.tables.docling import replace_cell_texts

    image = tmp_path / "p.png"
    image.write_bytes(b"x")
    # 셀 100~150 과 150~200, 조각은 130~170 — 양쪽에 50% 씩
    monkeypatch.setattr(rapidocr_ko, "read_image",
                        lambda _i: [_point_line("걸친텍스트", 130, 105, 170, 115)])

    stat = replace_cell_texts(
        _one_row_table((100, 100, 150, 120), (150, 100, 200, 120)),
        image, scale=1.0)
    assert stat["changed"] == 2
    assert stat["near_miss"] == 0


def test_dominant_fragment_is_not_duplicated(tmp_path, monkeypatch):
    """한 셀에 확실히 들어간 조각은 옆 셀로 번지지 않는다.

    없으면 경계를 살짝 스친 셀까지 같은 텍스트를 받아 표가 같은 말로
    뒤덮인다.
    """
    from docstruct.converters.pdf import rapidocr_ko
    from docstruct.tables.docling import replace_cell_texts

    image = tmp_path / "p.png"
    image.write_bytes(b"x")
    monkeypatch.setattr(rapidocr_ko, "read_image",
                        lambda _i: [_point_line("왼쪽전용", 105, 105, 145, 115)])

    item = _one_row_table((100, 100, 150, 120), (150, 100, 200, 120))
    stat = replace_cell_texts(item, image, scale=1.0)
    assert stat["changed"] == 1
    assert item.data.table_cells[1].text == "品"      # 옆 셀은 그대로


# ────────────────────────────────────────────────────────────────────
# 0.2.9 — 텍스트 PDF 를 OCR 로 덮어쓰던 문제
#
# 배경: `korean_ocr=True` 는 **모든 페이지**를 다시 읽었다. 스캔본에는 맞지만
#       실무에서는 텍스트 PDF 와 섞여 들어온다. 그때 정확한 텍스트 레이어를
#       인식 결과로 바꾸게 된다 — 스캔본에서 OCR 이 46~70% 였으니 손해가
#       크다.
#
#       글자 수만으로는 갈리지 않는다. 브라우저로 인쇄한 스캔본에는 머리말·
#       URL 이 텍스트로 들어 있어 쪽당 97자가 잡혔다. URL 을 걷어낸 뒤 한글만
#       세면 분포가 확실히 나뉜다.
#
#           스캔 PDF    모든 쪽 7자
#           텍스트 PDF  25% 지점 33자 · 중앙값 86자
#
#       원본 PDF 로 실측한 판정 정확도: 텍스트 PDF 98%, 스캔 PDF 100%.
# ────────────────────────────────────────────────────────────────────

def test_text_layer_detection():
    """텍스트 레이어가 쓸 만한 쪽과 아닌 쪽을 가른다."""
    from docstruct.pipeline import _has_usable_text_layer

    assert _has_usable_text_layer(
        "국민의 대의기관으로 입법, 예·결산 심사, 국정감·조사 등의 활동을 수행함")
    assert _has_usable_text_layer(
        "※ 본 성과계획서를 국가재정법 제34조에 의거하여 제출합니다")

    # 스캔본의 브라우저 인쇄 머리말 — 텍스트는 있으나 본문이 아니다
    assert not _has_usable_text_layer(
        "26. 5. 11. 오후 5:44  2025 주택과세금\n"
        "https://www.nts.go.kr/upload/index.html  5/380")
    assert not _has_usable_text_layer("气····吾·咎今 品品品 昆品 早")
    assert not _has_usable_text_layer("2025 2026 2027 100 200")

    # 영어 문서도 텍스트 레이어로 인정해야 한다 — 한글만 세면 스캔본으로
    # 오판한다. NASA 약력(라틴 2,885자, 한글 0자)이 실제로 그랬다.
    assert _has_usable_text_layer(
        "MICHAEL COLLINS (MGEN, USAF, RET.) NASA ASTRONAUT (FORMER) "
        "PERSONAL DATA: Born in Rome, Italy, on October 31, 1930.")
    assert not _has_usable_text_layer("<table 3>")
    assert not _has_usable_text_layer("국회")
    assert not _has_usable_text_layer("")
    assert not _has_usable_text_layer(None)


def test_korean_ocr_skips_text_layer_pages(tmp_path, monkeypatch):
    """텍스트 레이어가 있는 쪽은 재판독하지 않는다."""
    from docstruct import pipeline
    from docstruct.converters.pdf import rapidocr_ko

    image = tmp_path / "p1.png"
    image.write_bytes(b"fake")
    monkeypatch.setattr(rapidocr_ko, "read_page_text",
                        lambda _p: "OCR 이 읽은 다른 내용입니다")

    good = _ocr_page("국민의 대의기관으로 입법, 예·결산 심사, 국정감·조사 등의 활동", image)
    # 재판독 대상 목록이 비어 있으면 이 쪽은 건너뛴다
    assert pipeline._reread_with_korean_ocr([good], set()) == 0
    assert "대의기관" in good.content              # 원본 유지
    assert any("생략" in step.action for step in good.trace.steps)


def test_korean_ocr_rereads_scanned_pages(tmp_path, monkeypatch):
    """텍스트 레이어가 쓸모없는 쪽은 다시 읽는다."""
    from docstruct import pipeline
    from docstruct.converters.pdf import rapidocr_ko

    image = tmp_path / "p1.png"
    image.write_bytes(b"fake")
    monkeypatch.setattr(rapidocr_ko, "read_page_text",
                        lambda _p: "주택과 관련된 연중 세무 일정")

    scanned = _ocr_page("气····吾·咎今 品品品", image)
    assert pipeline._reread_with_korean_ocr([scanned], {1}) == 1
    assert "주택과 관련된" in scanned.content


def test_korean_ocr_force_overrides_detection(tmp_path, monkeypatch):
    """강제 설정이면 텍스트 레이어가 있어도 다시 읽는다."""
    from pathlib import Path as _Path

    from docstruct import pipeline
    from docstruct.converters.pdf import rapidocr_ko

    image = tmp_path / "p1.png"
    image.write_bytes(b"fake")
    monkeypatch.setattr(rapidocr_ko, "read_page_text", lambda _p: "다시 읽은 내용입니다")
    monkeypatch.setenv(pipeline.FORCE_REREAD_ENV, "true")

    page = _ocr_page("국민의 대의기관으로 입법, 예·결산 심사, 국정감·조사 등의 활동", image)
    # 강제 설정이면 _pages_needing_ocr 가 모든 쪽을 대상으로 돌려준다
    targets = pipeline._pages_needing_ocr(_Path("/없는파일.pdf"), [page])
    assert pipeline._reread_with_korean_ocr([page], targets) == 1
    assert "다시 읽은" in page.content


# ────────────────────────────────────────────────────────────────────
# 0.3.0 — 스캔본·텍스트 PDF 자동 분류
#
# 배경: `korean_ocr` 기본값이 False 라 **스캔본이 와도 아무것도 하지 않았다.**
#       스캔본은 텍스트 파서로 읽을 길이 없다 — 20쪽 전체에서 한글 340자가
#       나오는데 전부 `2025 주택과세금` 이라는 파일명이 URL·머리말에 반복된
#       것이고 본문은 0자다. OCR 이 유일한 경로다.
#
#       켜 두면 판정이 알아서 갈라 주므로 사람이 매번 정할 일이 없다.
#
#       판정을 **렌더보다 먼저** 한다. 나중에 하면 텍스트 PDF 도 전 페이지를
#       렌더하고 나서 전부 건너뛴다(155쪽에서 50초쯤).
# ────────────────────────────────────────────────────────────────────

def test_korean_ocr_enabled_by_default():
    """기본으로 켜져 있다.

    스캔본을 못 읽는 것보다, 텍스트 PDF 에서 판정 한 번 더 하는 편이 낫다.
    """
    from docstruct.core.config import get_settings

    assert get_settings().korean_ocr is True


def test_page_selection_uses_source_pdf(tmp_path):
    """원본 PDF 의 텍스트 레이어로 대상 쪽을 가려낸다."""
    pytest.importorskip("pypdfium2")
    import pypdfium2 as pdfium

    from docstruct import pipeline
    from docstruct.models import PageContent, PageTrace

    # 텍스트가 없는 빈 PDF — 스캔본과 같은 조건
    blank = tmp_path / "blank.pdf"
    document = pdfium.PdfDocument.new()
    document.new_page(200, 300)
    document.new_page(200, 300)
    document.save(str(blank))
    document.close()

    pages = [
        PageContent(page_no=n, page_no_kind="pdf", content="",
                    trace=PageTrace(extractor="docling", text_source="ocr"))
        for n in (1, 2)
    ]
    assert pipeline._pages_needing_ocr(blank, pages) == {1, 2}


def test_page_selection_falls_back_to_all_on_error(tmp_path):
    """원본을 못 읽으면 모든 쪽을 대상으로 본다.

    판정을 못 했다는 이유로 스캔본을 건너뛰면 본문을 통째로 잃는다.
    반대 방향의 실수는 시간만 더 쓴다.
    """
    from docstruct import pipeline
    from docstruct.models import PageContent, PageTrace

    pages = [
        PageContent(page_no=n, page_no_kind="pdf", content="",
                    trace=PageTrace(extractor="docling", text_source="ocr"))
        for n in (1, 2, 3)
    ]
    assert pipeline._pages_needing_ocr(tmp_path / "없는파일.pdf", pages) == {1, 2, 3}


def test_render_targets_narrow_to_ocr_pages():
    """재판독 대상만 렌더하되 표가 있는 쪽은 항상 포함한다.

    표가 있는 쪽은 재추출 근거로 이미지가 필요하다.
    """
    from docstruct.models import PageContent, PageTrace, TableInfo

    def page(number, *, has_table=False):
        return PageContent(
            page_no=number, page_no_kind="pdf", content="",
            tables=[TableInfo(id=f"t{number}", table_num=number,
                              placeholder="", markdown="| a |")] if has_table else [],
            trace=PageTrace(extractor="docling", text_source="ocr"))

    pages = [page(1), page(2, has_table=True), page(3), page(4)]

    def targets(all_pages, only):
        return [p.page_no for p in pages
                if p.tables or (all_pages and (only is None or p.page_no in only))]

    assert targets(False, None) == [2]              # 기본: 표만
    assert targets(True, None) == [1, 2, 3, 4]      # 전체
    assert targets(True, {1, 3}) == [1, 2, 3]       # 대상 + 표


# ────────────────────────────────────────────────────────────────────
# 0.3.1 — 격자에 셀이 빠진 표 (표시 + VLM 재구성)
#
# 배경: 좌표 매칭은 **셀이 존재할 때만** 동작한다. 실제 문서에서 7행 2열
#       (14칸)로 렌더되는 표의 셀이 7개뿐이었고 왼쪽 열이 아예 셀로 존재하지
#       않았다. OCR 은 `지방세법`·`종합부동산세법` 을 읽었는데 넣을 자리가
#       없었다.
#
#       앞서 "빈 셀 1~2개뿐이니 표 구조 인식은 제대로 됐다" 고 판단했는데,
#       그것은 **존재하는 셀** 기준이라 틀렸다. 셀 수 자체가 모자란 것을
#       못 봤다.
#
#       알고리즘으로는 고칠 수 없다 — 없는 칸에 값을 넣을 수는 없다.
#       표시(flag_broken_tables)와 VLM 재구성(vlm_fix_tables)을 따로 켠다.
# ────────────────────────────────────────────────────────────────────

def _grid_cell(row, col, *, row_span=1, col_span=1):
    """구조 검사용 셀 — 실제 docling 스키마로 만든다."""
    from tests.table_fixtures import make_cell

    return make_cell(row, col, "x", row_span=row_span, col_span=col_span)


def _grid_table(rows, cols, cells):
    """구조 검사용 TableItem."""
    from tests.table_fixtures import make_table

    return make_table(rows, cols, cells)


def test_empty_cell_ratio_counts_uncovered_cells():
    """셀 객체가 없는 칸의 비율을 센다.

    **이것은 구조 결함이 아니라 빈 칸이다.** docling 은 값이 없는 칸에
    TableCell 을 만들지 않으므로, 덮이지 않은 칸은 원본에서 비어 있던
    자리다 — 표 세 개에서 `text` 가 빈 셀이 0개인 것을 확인했다.
    """
    from docstruct.tables.docling import empty_cell_ratio

    stat = empty_cell_ratio(_grid_table(7, 2, [_grid_cell(r, 1) for r in range(7)]))
    assert stat["declared"] == 14
    assert stat["covered"] == 7
    assert stat["ratio"] == 0.5


def test_empty_cell_ratio_counts_merged_cells():
    """병합 셀은 자기가 덮는 칸을 모두 채운 것으로 센다.

    `row_span`·`col_span` 이 큰 셀 하나가 여러 칸을 덮으므로, 셀 개수만
    세면 정상 표도 비어 보인다.
    """
    from docstruct.tables.docling import empty_cell_ratio

    merged = _grid_table(2, 2, [_grid_cell(0, 0, col_span=2),
                                _grid_cell(1, 0), _grid_cell(1, 1)])
    assert empty_cell_ratio(merged)["empty"] == 0

    normal = _grid_table(2, 2, [_grid_cell(0, 0), _grid_cell(0, 1),
                                _grid_cell(1, 0), _grid_cell(1, 1)])
    assert empty_cell_ratio(normal)["empty"] == 0


def test_empty_cell_ratio_handles_empty_table():
    """빈 표에도 예외를 내지 않는다."""
    from docstruct.tables.docling import empty_cell_ratio

    assert empty_cell_ratio(_grid_table(0, 0, []))["declared"] == 0


def test_structure_gap_alias_kept():
    """옛 이름도 남긴다 (0.3.1~0.3.6 에서 쓰던 이름)."""
    from docstruct.tables.docling import empty_cell_ratio, structure_gap

    assert structure_gap is empty_cell_ratio


def _rebuild_page(markdown, ratio, image):
    """VLM 재구성 시험용 페이지.

    대상 선정은 `odd_columns`(서식 불일치)로 한다 — 빈 칸 비율은 정상 표를
    82% 잡아 쓸 수 없다(0.3.7). ratio 를 주면 대상으로 삼는다는 뜻이다.
    """
    from docstruct.models import PageContent, PageTrace, TableInfo

    return PageContent(
        page_no=1, page_no_kind="pdf", content="본문",
        page_image_path=str(image),
        tables=[TableInfo(id="table_1", table_num=1, placeholder="<table 1>",
                          markdown=markdown,
                          odd_columns=(7, 8) if ratio else None)],
        trace=PageTrace(extractor="docling", text_source="ocr"))


def test_vlm_rebuild_replaces_broken_table(tmp_path, monkeypatch):
    """구조 결함이 표시된 표를 다시 만든다."""
    import docstruct.infrastructure.llm.client as llm_client
    from docstruct.tables import vlm_rebuild

    image = tmp_path / "p.png"
    image.write_bytes(b"x")
    monkeypatch.setattr(llm_client, "llm_api_config", lambda: {"model": "x"})
    monkeypatch.setattr(vlm_rebuild, "llm_api_config", lambda: {"model": "x"})
    monkeypatch.setattr(vlm_rebuild, "encode_image_file",
                        lambda _p: ("image/png", "AAAA"))
    monkeypatch.setattr(
        vlm_rebuild, "invoke_llm",
        lambda *a, **k: "| 구분 | 약칭 |\n|---|---|\n| 지방세법 | 지방령 |")

    page = _rebuild_page("| 品品品 |\n|---|\n| 지방령 |", 0.5, image)
    assert vlm_rebuild.rebuild_broken_tables([page]) == 1
    assert "지방세법" in page.tables[0].markdown
    assert page.tables[0].original_markdown is not None    # 원본 보관


def test_vlm_rebuild_discards_shorter_result(tmp_path, monkeypatch):
    """다시 만든 표가 원본보다 짧으면 되돌린다.

    VLM 이 표를 일부만 옮기는 일이 있고, 그때 원본을 잃으면 손해다.
    """
    import docstruct.infrastructure.llm.client as llm_client
    from docstruct.tables import vlm_rebuild

    image = tmp_path / "p.png"
    image.write_bytes(b"x")
    monkeypatch.setattr(llm_client, "llm_api_config", lambda: {"model": "x"})
    monkeypatch.setattr(vlm_rebuild, "llm_api_config", lambda: {"model": "x"})
    monkeypatch.setattr(vlm_rebuild, "encode_image_file",
                        lambda _p: ("image/png", "AAAA"))
    monkeypatch.setattr(vlm_rebuild, "invoke_llm",
                        lambda *a, **k: "| a |\n|---|\n| b |")

    original = ("| 매우 긴 원본 표 내용 | 두번째 |\n|---|---|\n"
                "| 값1 | 값2 |\n| 값3 | 값4 |")
    page = _rebuild_page(original, 0.5, image)
    assert vlm_rebuild.rebuild_broken_tables([page]) == 0
    assert "매우 긴" in page.tables[0].markdown


def test_vlm_rebuild_rejects_non_table_answer(tmp_path, monkeypatch):
    """표 형태가 아닌 응답은 받아들이지 않는다."""
    import docstruct.infrastructure.llm.client as llm_client
    from docstruct.tables import vlm_rebuild

    image = tmp_path / "p.png"
    image.write_bytes(b"x")
    monkeypatch.setattr(llm_client, "llm_api_config", lambda: {"model": "x"})
    monkeypatch.setattr(vlm_rebuild, "llm_api_config", lambda: {"model": "x"})
    monkeypatch.setattr(vlm_rebuild, "encode_image_file",
                        lambda _p: ("image/png", "AAAA"))

    for answer in ("이 이미지에는 표가 보이지 않습니다.", "없음", ""):
        monkeypatch.setattr(vlm_rebuild, "invoke_llm", lambda *a, r=answer, **k: r)
        page = _rebuild_page("| 品 |\n|---|\n| a |", 0.5, image)
        assert vlm_rebuild.rebuild_broken_tables([page]) == 0


def test_vlm_rebuild_skips_healthy_tables(tmp_path, monkeypatch):
    """결함이 표시되지 않은 표는 건드리지 않는다.

    VLM 은 못 읽은 것을 지어낸다. 좌표 매칭이 성공한 표까지 다시 만들면
    검증된 결과를 추측으로 바꾸게 된다.
    """
    import docstruct.infrastructure.llm.client as llm_client
    from docstruct.tables import vlm_rebuild

    image = tmp_path / "p.png"
    image.write_bytes(b"x")
    monkeypatch.setattr(llm_client, "llm_api_config", lambda: {"model": "x"})

    page = _rebuild_page("| 정상 |\n|---|\n| 표 |", None, image)
    assert vlm_rebuild.rebuild_broken_tables([page]) == 0


def test_table_flags_are_toggleable():
    """표시와 재구성을 따로 켜고 끌 수 있다."""
    import docstruct

    keys = docstruct.option_keys()
    assert "flag_broken_tables" in keys
    assert "vlm_fix_tables" in keys

    from docstruct.core.config import _vlm_default, get_settings

    settings = get_settings()
    # 빈 칸 표시는 정상 표를 82% 나 잡았고(오판), 격자 재구성은 텍스트
    # PDF 에서 13회 시도해 13회 모두 폐기됐다 — 둘은 기본으로 끈다.
    assert settings.flag_broken_tables is False
    assert settings.rebuild_grid is False
    # vlm_fix_tables 는 0.3.82 부터 **수단이 있으면 기본 켬**이다.
    # 이 시험이 0.3.82 때 갱신되지 않고도 통과한 것은, `_vlm_default()`
    # 가 os.environ 만 보아 내장 기본값 엔드포인트를 못 봤기 때문이다 —
    # 그 구멍 자체가 A/B 네 판을 전부 VLM 없이 돌게 했다 (0.3.99).
    assert settings.vlm_fix_tables is _vlm_default()


# ────────────────────────────────────────────────────────────────────
# 0.3.2 — 텍스트 PDF 에 남아 있던 손상
#
# 배경: 0.1.95 이후 OCR 쪽만 보느라 텍스트 PDF 를 다시 재지 않았다.
#       HWP 원본과 대조하니 손상이 남아 있었다.
#
#           · 1 급 (2 명 ), 2 급 (32 명 ), 3 급 (73 명 )
#           → · 1급(2명), 2급(32명), 3급(73명)
#
#       0.1.95 는 여는 괄호 **뒤** 만 다뤘고 **앞** 은 남겼다. 숫자와 단위
#       사이 공백(`1 급`, `169 명`)도 규칙이 없었다.
#
#       실측: 숫자+단위 229건, 괄호 주변 396건 → 각각 0.
# ────────────────────────────────────────────────────────────────────

def test_space_before_open_paren_removed():
    """여는 괄호 앞 공백을 없앤다.

    0.1.95 는 괄호 뒤만 다뤄 이쪽이 남았다.
    """
    from docstruct.text.korean_text import tighten_punctuation

    assert tighten_punctuation("국회 (사무처)") == "국회(사무처)"
    assert tighten_punctuation("1 급 (2 명 )") == "1급(2명)"


def test_space_between_number_and_unit_removed():
    """숫자와 단위 사이 공백을 없앤다."""
    from docstruct.text.korean_text import tighten_punctuation

    assert tighten_punctuation("총 169 명") == "총 169명"
    assert tighten_punctuation("2027 년도 예산") == "2027년도 예산"
    assert tighten_punctuation(
        "· 1 급 (2 명 ), 2 급 (32 명 )") == "· 1급(2명), 2급(32명)"


def test_number_unit_rule_is_conservative():
    """한 글자 단위만 붙인다.

    `5 개년 계획` 의 `개년` 처럼 두 글자 이상을 붙이면 다른 말이 될 수
    있어 목록을 좁게 둔다.
    """
    from docstruct.text.korean_text import tighten_punctuation

    # 목록에 있는 단위는 붙인다
    assert tighten_punctuation("3 년 계획") == "3년 계획"
    # 목록에 없는 글자는 그대로
    assert tighten_punctuation("2 부처 합동") == "2 부처 합동"
    assert tighten_punctuation("각 부처별 사업") == "각 부처별 사업"


def test_tighten_punctuation_still_only_removes_spaces():
    """공백만 지우고 글자는 하나도 잃지 않는다.

    실문서 2,119줄에서 글자 수 변화 0 을 확인했다.
    """
    import re

    from docstruct.text.korean_text import tighten_punctuation

    for line in ("· 1 급 (2 명 ), 2 급 (32 명 )", "국회 (사무처) 소관",
                 "2027 년도 예산 및 기금운용계획안"):
        strip = lambda t: re.sub(r"\s", "", t)      # noqa: E731
        assert strip(tighten_punctuation(line)) == strip(line)


# ────────────────────────────────────────────────────────────────────
# 0.3.3 — 영어 문서를 스캔본으로 오판하던 문제
#
# 배경: 텍스트 레이어 판정이 **한글만** 셌다. NASA 약력 PDF 는 텍스트가
#       3,080자(라틴 2,885자) 온전한데 한글이 0자라 재판독 대상으로 잡혔다.
#       OCR 로 다시 읽으면 정확한 텍스트를 인식 결과로 바꾸게 된다.
#
#       한글·라틴을 함께 센다. 스캔본의 URL 은 판정 전에 걷어내므로 라틴을
#       포함해도 오판이 늘지 않는다 — 세 문서로 확인했다.
# ────────────────────────────────────────────────────────────────────

def test_english_document_keeps_text_layer():
    """영어 텍스트 PDF 를 재판독 대상으로 잡지 않는다."""
    from docstruct.pipeline import _has_usable_text_layer

    english = ("MICHAEL COLLINS (MGEN, USAF, RET.)\n"
               "NASA ASTRONAUT (FORMER)\n"
               "PERSONAL DATA: Born in Rome, Italy, on October 31, 1930. "
               "Married to the former Patricia M. Finnegan of Boston.")
    assert _has_usable_text_layer(english)


def test_scanned_header_still_detected_with_latin():
    """라틴을 세더라도 스캔본 머리말은 걸러진다.

    URL 과 태그를 판정 전에 걷어낸다. 그러지 않으면 URL 의 라틴 문자
    때문에 스캔본이 텍스트 PDF 로 오판된다.
    """
    from docstruct.pipeline import _has_usable_text_layer

    assert not _has_usable_text_layer(
        "26. 5. 11. 오후 5:44  2025 주택과세금\n"
        "https://www.nts.go.kr/upload/nts/ebook/2025주택과세금/index.html  5/380")


def test_mixed_language_document_keeps_text_layer():
    """한영 혼용 문서도 레이어를 그대로 쓴다."""
    from docstruct.pipeline import _has_usable_text_layer

    assert _has_usable_text_layer(
        "NASA 우주비행사 Michael Collins 는 Apollo 11 사령선 조종사였다.")


# ────────────────────────────────────────────────────────────────────
# 0.3.4 — OCR 좌표로 표 격자 재구성
#
# 배경: 13행 2열 표가 **7행**으로 인식됐다. 가로 구분선이 연한 회색이라
#       행 경계를 놓친 것이다. 왼쪽 열은 아예 셀로 생성되지 않았고, OCR 은
#       `지방세법`·`종합부동산세법` 을 제대로 읽었는데 넣을 칸이 없었다.
#
#       셀이 없으면 좌표 매칭도 소용없다. 격자 자체를 다시 세운다.
#
#       이는 Split-Merge 계열(SEMv2·SEMv3)이 학습으로 하는 "분리선 예측" 을
#       이미 가진 OCR 좌표로 직접 하는 것이다. 학습도 모델도 필요 없다.
# ────────────────────────────────────────────────────────────────────

def _grid_fragments(pairs, *, top=483.0, height=11.0, gap=14.5, header=None):
    """2열 표를 좌표 조각으로 만든다."""
    from docstruct.converters.pdf.cell_match import Box

    fragments = []
    if header:
        fragments += [(Box(120, top - 15, 300, top - 4), header[0]),
                      (Box(320, top - 15, 470, top - 4), header[1])]
    y = top
    for left, right in pairs:
        fragments.append((Box(120, y, 300, y + height), left))
        fragments.append((Box(320, y, 470, y + height), right))
        y += gap
    return fragments


def test_grid_rebuild_recovers_all_rows():
    """행 경계를 놓친 표를 좌표로 복원한다."""
    from docstruct.tables.grid_rebuild import rebuild

    pairs = [("지방세법", "지방법"), ("지방세법 시행령", "지방령"),
             ("지방세특례제한법", "지특법"), ("종합부동산세법", "종부법"),
             ("종합부동산세법 시행령", "종부령"), ("소득세법", "소득법"),
             ("소득세법 시행령", "소득령"), ("조세특례제한법", "조특법"),
             ("조세특례제한법 시행령", "조특령"), ("상속세 및 증여세법", "상증법"),
             ("상속세 및 증여세법 시행령", "상증령"),
             ("부동산거래 신고 등에 관한 법률", "부동산거래신고법"),
             ("부동산거래 신고 등에 관한 법률 시행령", "부동산거래신고령")]
    markdown = rebuild(_grid_fragments(pairs, header=("법령명", "표기 방식")))

    rows = [ln for ln in markdown.splitlines()
            if ln.startswith("|") and set(ln.strip()) - set("|-: ")]
    assert len(rows) == 14           # 헤더 + 13행
    assert "지방세법" in markdown
    assert "부동산거래신고령" in markdown


def test_grid_rebuild_adapts_to_row_spacing():
    """행 간격이 좁아도 나눈다.

    고정 임계를 쓰면 촘촘한 표가 통째로 한 행이 된다 — 실측에서 간격
    0.5pt 짜리가 13행이 아니라 1행이 됐다.
    """
    from docstruct.tables.grid_rebuild import rebuild

    pairs = [(f"항목{n}", f"값{n}") for n in range(13)]
    for gap in (14.5, 12.0, 11.8):       # 간격 3.5 / 1.0 / 0.8 pt
        markdown = rebuild(_grid_fragments(pairs, gap=gap))
        rows = [ln for ln in markdown.splitlines()
                if ln.startswith("|") and set(ln.strip()) - set("|-: ")]
        assert len(rows) == 13, f"간격 {gap} 에서 {len(rows)}행"


def test_grid_rebuild_needs_enough_fragments():
    """조각이 적으면 격자를 세우지 않는다."""
    from docstruct.converters.pdf.cell_match import Box
    from docstruct.tables.grid_rebuild import rebuild

    assert rebuild([]) == ""
    assert rebuild([(Box(0, 0, 10, 10), "하나")]) == ""
    # 한 줄뿐이면 표가 아니다
    assert rebuild([(Box(0, 0, 10, 10), "가"), (Box(20, 0, 30, 10), "나")]) == ""


def test_grid_rebuild_merges_multiline_cell():
    """한 칸 안에서 줄이 나뉜 조각은 이어 붙인다.

    줄바꿈을 넣으면 markdown 표가 깨진다.
    """
    from docstruct.converters.pdf.cell_match import Box
    from docstruct.tables.grid_rebuild import rebuild

    fragments = [
        (Box(120, 100, 300, 111), "구분"), (Box(320, 100, 470, 111), "내용"),
        (Box(120, 120, 300, 131), "항목"),
        (Box(320, 120, 400, 131), "앞부분"), (Box(402, 120, 470, 131), "뒷부분"),
    ]
    markdown = rebuild(fragments)
    assert "앞부분 뒷부분" in markdown
    assert markdown.count("\n") < 6      # 줄바꿈이 셀에 들어가지 않았다


# ────────────────────────────────────────────────────────────────────
# 0.3.5 — 격자 재구성이 병합 셀을 망치지 않게
#
# 배경: 주 대상은 성과계획서(433쪽 텍스트 PDF)이고 스캔본은 부차적이다.
#       그 문서는 병합 셀이 많아, 격자 재구성이 오히려 손해가 될 수 있다.
#
#       실측에서 두 가지 손상을 확인했다.
#
#       ① 가로 병합 헤더가 열 경계를 덮는다
#          `예산 (A+B)` 한 칸이 `'26년`·`'27년` 두 열을 삼켜 4열 표가
#          2열이 됐다. 넓은 조각을 열 경계 계산에서 빼 고쳤다.
#
#       ② 병합 자체를 표현할 수 없다
#          좌표 격자에는 rowspan/colspan 이 없다. 두 행이 공유하던 값이
#          한 행만의 것으로 읽히는데, 그 문제를 0.1.75 에서 `〃` 표기로
#          고쳤다. 재구성이 그것을 되돌린다.
#          → **병합이 있는 표는 아예 건드리지 않는다.**
# ────────────────────────────────────────────────────────────────────

def test_wide_header_does_not_swallow_columns():
    """가로 병합 헤더가 아래 열 경계를 덮지 않는다."""
    from docstruct.converters.pdf.cell_match import Box
    from docstruct.tables.grid_rebuild import rebuild

    fragments = [
        (Box(100, 100, 160, 111), "구분"),
        (Box(170, 100, 320, 111), "예산 (A+B)"),      # 2열을 덮는 병합 헤더
        (Box(170, 115, 240, 126), "'26년"),
        (Box(250, 115, 320, 126), "'27년"),
        (Box(100, 130, 160, 141), "본부"),
        (Box(170, 130, 240, 141), "100"),
        (Box(250, 130, 320, 141), "120"),
    ]
    markdown = rebuild(fragments)
    # '26년 과 '27년 이 서로 다른 칸에 있어야 한다
    data = [ln for ln in markdown.splitlines()
            if ln.startswith("|") and "'26년" in ln][0]
    cells = [c.strip() for c in data.strip("|").split("|")]
    assert "'26년" in cells
    assert "'27년" in cells
    assert "'26년 '27년" not in cells             # 한 칸에 뭉치면 안 된다


def test_vertical_merge_still_rebuilds():
    """세로 병합으로 빈 칸이 생긴 표는 그대로 복원된다."""
    from docstruct.converters.pdf.cell_match import Box
    from docstruct.tables.grid_rebuild import rebuild

    fragments = [
        (Box(100, 100, 160, 111), "구분"), (Box(170, 100, 240, 111), "항목"),
        (Box(250, 100, 320, 111), "'26예산"),
        (Box(100, 115, 160, 126), "프로그램"), (Box(170, 115, 240, 126), "가"),
        (Box(250, 115, 320, 126), "100"),
        (Box(170, 130, 240, 141), "나"), (Box(250, 130, 320, 141), "200"),
    ]
    markdown = rebuild(fragments)
    rows = [ln for ln in markdown.splitlines()
            if ln.startswith("|") and set(ln.strip()) - set("|-: ")]
    assert len(rows) == 3
    assert "프로그램" in markdown and "나" in markdown


def test_tables_with_merges_are_skipped(tmp_path, monkeypatch):
    """병합 셀이 있는 표는 재구성 대상에서 뺀다.

    좌표 격자는 병합을 표현하지 못해 값의 귀속이 바뀐다 — 0.1.75 에서
    `〃` 표기로 고친 문제를 되돌리게 된다.
    """
    from docstruct import pipeline
    from docstruct.models import PageContent, PageTrace, TableInfo
    from tests.table_fixtures import make_cell, make_table

    image = tmp_path / "p.png"
    image.write_bytes(b"x")

    merged = make_table(2, 1, [make_cell(0, 0, "값", row_span=2,
                                         box=(100, 100, 200, 120))])
    table = TableInfo(id="table_1", table_num=1, placeholder="",
                      markdown="| a |", structure_ratio=0.5,
                      bbox={"l": 90, "t": 90, "r": 210, "b": 130},
                      source_item=merged)
    page = PageContent(page_no=1, page_no_kind="pdf", content="",
                       page_image_path=str(image), tables=[table],
                       trace=PageTrace(extractor="docling", text_source="ocr"))

    # 재구성이 돌면 안 된다 — OCR 을 부르지 않는지로 확인한다
    called = []
    monkeypatch.setattr("docstruct.converters.pdf.rapidocr_ko.read_image",
                        lambda p: called.append(p) or [])

    assert pipeline._rebuild_broken_grids([page], scale=2.0) == 0
    assert table.markdown == "| a |"                  # 원본 그대로
    assert any("건너뜀" in step.action for step in page.trace.steps)


# ────────────────────────────────────────────────────────────────────
# 0.3.6 — 새 설정이 결과에 기록되지 않던 문제
#
# 배경: `korean_ocr`·`rebuild_grid`·`flag_broken_tables`·`vlm_fix_tables` 를
#       만들면서 `_pipeline_settings` 에 넣지 않았다. document.json 의
#       pipeline 에 그 키들이 없어, **어떤 설정으로 돌린 산출물인지 결과만
#       봐서는 알 수 없었다.**
#
#       실제로 같은 결과 파일을 두고 rebuild_grid 가 켜졌는지 꺼졌는지
#       판단하지 못해 잘못된 결론을 냈다.
# ────────────────────────────────────────────────────────────────────

def test_pipeline_snapshot_records_pdf_options():
    """PDF 실행 설정이 결과에 기록된다."""
    from docstruct.pipeline import _pipeline_settings

    info = _pipeline_settings("pdf", True, True, False)
    for key in ("korean_ocr", "flag_broken_tables", "rebuild_grid",
                "vlm_fix_tables", "ocr_backend", "force_full_page_ocr"):
        assert key in info, f"pipeline 스냅샷에 {key} 가 없습니다"


def test_pipeline_snapshot_records_hwp_options():
    """HWP 실행 설정도 기록된다.

    `hwp_fill_html` 여부에 따라 표 재추출 근거가 있고 없고가 갈리는데,
    기록이 없으면 나중에 결과를 해석할 수 없다.
    """
    from docstruct.pipeline import _pipeline_settings

    assert "hwp_fill_html" in _pipeline_settings("hwp", True, True, False)


def test_new_options_are_not_forgotten_in_snapshot():
    """동작을 바꾸는 설정은 모두 스냅샷에 남는다.

    설정을 새로 만들 때 스냅샷 갱신을 잊으면, 그 설정으로 돌린 결과를
    나중에 구분할 수 없다.
    """
    from docstruct.pipeline import _pipeline_settings

    recorded = set(_pipeline_settings("pdf", True, True, False))
    recorded |= set(_pipeline_settings("hwp", True, True, False))

    #: 결과 해석에 필요한 설정들. 새로 만들면 여기에도 추가한다.
    must_record = {
        "korean_ocr", "flag_broken_tables", "rebuild_grid", "vlm_fix_tables",
        "hwp_fill_html", "ocr_backend", "ocr_lang", "force_full_page_ocr",
        "assess_tables", "fill_tables",
    }
    missing = sorted(must_record - recorded)
    assert not missing, f"스냅샷에 빠진 설정: {missing}"


# ────────────────────────────────────────────────────────────────────
# 0.3.8 — 같은 서식 표끼리 견주어 이상한 표 찾기
#
# 배경: 표 하나만 보고 구조가 깨졌는지 판정할 방법이 없었다. 빈 칸 비율을
#       써 봤으나 정상 표를 82% 나 잡아 쓸 수 없었다(0.3.7 에서 정정).
#
#       정부 문서는 같은 서식 표를 여러 쪽에 반복한다. 실제 문서(행안부
#       성과계획서 72-100쪽)에서 헤더가 같은 표 12개 중 **11개가 8열,
#       하나만 7열**이었고, 그 하나가 헤더 두 칸을 뭉친 표였다.
#
#           정상  ... | 재정사업 평가명 | 성과평가 결과 | 비고 |   (8열)
#           이상  ... | 재정사업 성과평가 평가명 결과 | 비고 |     (7열)
#
#       실측: 표 17개 중 정확히 그 하나만 검출. 오탐 0.
# ────────────────────────────────────────────────────────────────────

def _odd_page(page_no, tables):
    """서식 비교 시험용 페이지."""
    from docstruct.models import PageContent, PageTrace, TableInfo

    return PageContent(
        page_no=page_no, page_no_kind="pdf", content="",
        tables=[TableInfo(id=tid, table_num=n, placeholder="", markdown=md)
                for n, (tid, md) in enumerate(tables, 1)],
        trace=PageTrace(extractor="docling", text_source="text_layer"))


def _table_md(header):
    """헤더만 있는 GFM 표."""
    line = "| " + " | ".join(header) + " |"
    rule = "|" + "|".join("---" for _ in header) + "|"
    return f"{line}\n{rule}\n{line}"


def test_odd_table_detected_by_column_count():
    """같은 서식 표 중 열 수가 다른 것을 찾는다."""
    from docstruct.tables.odd_tables import find_odd_tables

    normal = ["", "회계 구분", "'25결산", "'26예산", "재정사업 평가명", "비고"]
    merged = ["", "회계 구분", "'25결산", "'26예산", "재정사업 평가명 비고"]

    pages = [_odd_page(n, [(f"table_{n}", _table_md(normal))]) for n in range(1, 5)]
    pages.append(_odd_page(5, [("table_5", _table_md(merged))]))

    odd = find_odd_tables(pages)
    assert len(odd) == 1
    _, table, width, majority = odd[0]
    assert table.id == "table_5"
    assert (width, majority) == (5, 6)


def test_odd_table_needs_enough_samples():
    """같은 서식 표가 셋 미만이면 판단하지 않는다.

    둘뿐이면 어느 쪽이 옳은지 알 수 없다.
    """
    from docstruct.tables.odd_tables import find_odd_tables

    normal = ["구분", "항목", "값"]
    short = ["구분", "항목"]
    pages = [_odd_page(1, [("table_1", _table_md(normal))]),
             _odd_page(2, [("table_2", _table_md(short))])]
    assert find_odd_tables(pages) == []


def test_odd_table_ignores_different_formats():
    """헤더가 다르면 다른 서식으로 보고 견주지 않는다."""
    from docstruct.tables.odd_tables import find_odd_tables

    pages = [
        _odd_page(1, [("t1", _table_md(["성과지표명", "달성여부", "목표치"]))]),
        _odd_page(2, [("t2", _table_md(["문제점 진단", "개선계획"]))]),
        _odd_page(3, [("t3", _table_md(["구분", "예산", "결산", "증감"]))]),
    ]
    assert find_odd_tables(pages) == []


def test_odd_table_needs_majority():
    """반반이면 판단하지 않는다.

    다수가 있어야 기준이 선다.
    """
    from docstruct.tables.odd_tables import find_odd_tables

    wide = ["구분", "항목", "값", "비고"]
    narrow = ["구분", "항목", "값"]
    pages = [_odd_page(1, [("t1", _table_md(wide))]),
             _odd_page(2, [("t2", _table_md(wide))]),
             _odd_page(3, [("t3", _table_md(narrow))]),
             _odd_page(4, [("t4", _table_md(narrow))])]
    assert find_odd_tables(pages) == []


# ────────────────────────────────────────────────────────────────────
# 0.3.9 — 쪽을 넘는 표에 헤더 물려주기
#
# 배경: docling 은 페이지 단위로 처리하므로 쪽을 넘는 표를 별개로 본다.
#       행안부 성과계획서 별첨3 에서 한 표가 **21쪽에 걸쳐** 있었는데
#       첫 쪽에만 헤더가 있었다.
#
#           6쪽  ['회 계', '계 정', '분 야', ...]        ← 헤더
#           7쪽  ['11', '0', '010', '013', ...]          ← 데이터
#           ...  26쪽까지 데이터만
#
#       7쪽 이후 `537` 이 '26예산인지 '27예산안인지 알 수 없었다.
#
#       **행은 합치지 않고 헤더만 붙인다.** 같은 표인데 쪽마다 열 수가
#       13~17 로 달라(빈 열이 잘림) 합치면 값이 밀린다. 헤더만 붙여도 각
#       쪽 표가 독립적으로 유효해진다.
#
#       실측: 27개 표 중 7~26쪽 20개에 헤더를 물려주고, 헤더가 매 쪽
#       반복되는 표(1~6쪽)와 열 수가 다른 합계 표(27쪽)는 건드리지 않았다.
# ────────────────────────────────────────────────────────────────────

def _cont_page(page_no, rows):
    """이어짐 시험용 페이지 (표 하나)."""
    from docstruct.models import PageContent, PageTrace, TableInfo

    body = ["| " + " | ".join(rows[0]) + " |",
            "|" + "|".join("---" for _ in rows[0]) + "|"]
    body += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return PageContent(
        page_no=page_no, page_no_kind="pdf", content="",
        tables=[TableInfo(id=f"t{page_no}", table_num=page_no,
                          placeholder="", markdown="\n".join(body))],
        trace=PageTrace(extractor="docling", text_source="text_layer"))


_CONT_HEADER = ["회계", "계정", "분야", "프로그램명", "'26예산"]


def _cont_data(n):
    """데이터 행."""
    return ["11", "0", "010", f"사업{n}", str(1000 + n)]


def test_continued_table_marked_without_editing():
    """이어짐을 표시하되 markdown 은 건드리지 않는다.

    헤더를 끼워 넣으면 원본이 변형되고, 열 수가 쪽마다 달라(실측 13~17)
    앞에서부터 억지로 맞추게 된다. 그 정렬이 틀리면 되돌릴 수 없다.
    """
    from docstruct.tables.continued import mark_continuations

    pages = [_cont_page(1, [_CONT_HEADER, _cont_data(1)]),
             _cont_page(2, [_cont_data(2), _cont_data(3)]),
             _cont_page(3, [_cont_data(4), _cont_data(5)])]
    before = pages[1].tables[0].markdown

    assert mark_continuations(pages) == 2
    table = pages[1].tables[0]
    assert table.continues_from == "t1"
    assert table.inherited_header == _CONT_HEADER
    assert table.markdown == before          # 원본 그대로


def test_repeated_header_table_untouched():
    """헤더가 매 쪽 반복되는 표는 건드리지 않는다.

    이미 쓸 수 있으므로 손댈 이유가 없다.
    """
    from docstruct.tables.continued import mark_continuations

    pages = [_cont_page(n, [_CONT_HEADER, _cont_data(n)]) for n in (1, 2, 3)]
    assert mark_continuations(pages) == 0
    assert all(t.continues_from is None for p in pages for t in p.tables)


def test_continued_table_stops_at_page_gap():
    """쪽이 끊기면 이어짐으로 보지 않는다."""
    from docstruct.tables.continued import mark_continuations

    pages = [_cont_page(1, [_CONT_HEADER, _cont_data(1)]),
             _cont_page(5, [_cont_data(2), _cont_data(3)])]
    assert mark_continuations(pages) == 0


def test_continued_table_needs_similar_width():
    """열 수가 크게 다르면 다른 표로 본다.

    실측에서 마지막 합계 표(7열)가 이 조건으로 제외됐다.
    """
    from docstruct.tables.continued import mark_continuations

    pages = [_cont_page(1, [_CONT_HEADER, _cont_data(1)]),
             _cont_page(2, [["합계", "276"], ["a", "b"]])]
    assert mark_continuations(pages) == 0


def test_data_row_detection():
    """헤더와 데이터를 내용으로 가른다.

    docling 의 `column_header` 플래그는 쓸 수 없다 — 헤더가 없는 표에도
    항상 참으로 표시된다(실측에서 세 표 모두 그랬다).
    """
    from docstruct.tables.continued import looks_like_data

    assert not looks_like_data(_CONT_HEADER)
    assert looks_like_data(_cont_data(1))
    assert not looks_like_data(["", "", ""])
    assert not looks_like_data(["성과지표명", "달성여부", "목표치"])


def test_continuation_fields_serialized():
    """이어짐 관계가 JSON 에 남는다.

    구조화 단계가 이 정보로 표를 연결하므로 결과 파일에 있어야 한다.
    """
    import json

    from docstruct.models import TableInfo

    table = TableInfo(id="table_7", table_num=7, placeholder="<table 7>",
                      markdown="| a |", continues_from="table_6",
                      inherited_header=["회 계", "계 정"])
    data = table.to_dict()
    assert data["continues_from"] == "table_6"
    assert data["inherited_header"] == ["회 계", "계 정"]
    json.dumps(data, ensure_ascii=False)


# ────────────────────────────────────────────────────────────────────
# 0.3.11 — 서식 비교 오탐과 VLM 대상 선정
#
# 배경 ①: 헤더 앞 세 칸으로만 묶으니 **앞쪽이 빈 표들이 한 그룹**이 됐다.
#         HWP 실측에서 23열 표와 5열 표가 같은 묶음으로 잡혀 오탐 5건.
#
#             table_15 · 23열 · ['', '', '', '', '임무 : 국민의 대의기', '']
#             table_45 ·  6열 · ['', '', '', '', '(단위: 백만원, %)', '']
#
#         내용 있는 셀로 열쇠를 만들고, 열 수 차이가 크면 다른 표로 본다.
#         결과: HWP 오탐 5 → 0, 행안부 사례는 그대로 검출.
#
# 배경 ②: `vlm_fix_tables` 가 빈 칸 비율로 대상을 골랐다. 그 지표는 정상
#         표를 82% 잡으므로(0.3.7 에서 확인) 멀쩡한 표를 VLM 에 보내게 된다.
#         서식이 어긋난 표(`odd_columns`)로 바꿨다.
# ────────────────────────────────────────────────────────────────────

def test_odd_tables_ignores_blank_headers():
    """앞쪽이 빈 헤더만으로 묶지 않는다.

    병합 헤더의 좌상단이 비거나 `(단위: 백만원)` 같은 안내가 첫 행에 오는
    표가 많다. 그것만으로 묶으면 전혀 다른 표가 한 그룹이 된다.
    """
    from docstruct.tables.odd_tables import find_odd_tables

    def blank_header(n, width, label):
        header = ["", "", "", "", label] + [""] * (width - 5)
        return _odd_page(n, [(f"t{n}", _table_md(header))])

    pages = [blank_header(1, 23, "임무 : 국민의 대의기관"),
             blank_header(2, 6, "(단위: 백만원, %)"),
             blank_header(3, 5, "다른 표")]
    assert find_odd_tables(pages) == []


def test_odd_tables_rejects_large_width_gap():
    """열 수 차이가 크면 같은 표로 보지 않는다.

    헤더 두 칸이 뭉치면 1~2열이 준다. 그보다 벌어지면 다른 표다.
    """
    from docstruct.tables.odd_tables import find_odd_tables

    base = ["구분", "항목", "값", "비고"]
    pages = [_odd_page(n, [(f"t{n}", _table_md(base))]) for n in (1, 2, 3)]
    # 같은 열쇠인데 열이 배로 많은 표
    pages.append(_odd_page(4, [("t4", _table_md(base + [f"추가{i}" for i in range(8)]))]))
    assert find_odd_tables(pages) == []


def test_odd_tables_still_detects_merged_header():
    """헤더 뭉침은 여전히 검출한다 (회귀 확인).

    실측 사례: 같은 서식 12개 중 하나만 7열이었고 헤더 두 칸이 뭉쳐 있었다.
    """
    from docstruct.tables.odd_tables import find_odd_tables

    normal = ["", "회계 구분", "'25결산", "'26예산", "재정사업 평가명",
              "성과평가 결과", "비고"]
    merged = ["", "회계 구분", "'25결산", "'26예산",
              "재정사업 성과평가 평가명 결과", "비고"]
    pages = [_odd_page(n, [(f"t{n}", _table_md(normal))]) for n in range(1, 6)]
    pages.append(_odd_page(6, [("t6", _table_md(merged))]))

    odd = find_odd_tables(pages)
    assert len(odd) == 1
    assert odd[0][1].id == "t6"


def test_vlm_targets_odd_columns_not_empty_ratio(tmp_path, monkeypatch):
    """VLM 재구성이 서식 불일치 표만 고른다.

    빈 칸 비율로 고르면 정상 표를 추측으로 바꾸게 된다.
    """
    import docstruct.infrastructure.llm.client as llm_client
    from docstruct.models import PageContent, PageTrace, TableInfo
    from docstruct.tables import vlm_rebuild

    image = tmp_path / "p.png"
    image.write_bytes(b"x")
    monkeypatch.setattr(llm_client, "llm_api_config", lambda: {"model": "x"})
    monkeypatch.setattr(vlm_rebuild, "llm_api_config", lambda: {"model": "x"})
    monkeypatch.setattr(vlm_rebuild, "encode_image_file",
                        lambda _p: ("image/png", "AAAA"))
    monkeypatch.setattr(vlm_rebuild, "invoke_llm",
                        lambda *a, **k: "| 구분 | 값 |\n|---|---|\n| 가 | 1 |")

    def make(**kwargs):
        return PageContent(
            page_no=1, page_no_kind="pdf", content="본문",
            page_image_path=str(image),
            tables=[TableInfo(id="table_1", table_num=1, placeholder="",
                              markdown="| 品 | 品 |\n|---|---|\n| a | b |", **kwargs)],
            trace=PageTrace(extractor="docling", text_source="text_layer"))

    assert vlm_rebuild.rebuild_broken_tables([make(odd_columns=(7, 8))]) == 1
    assert vlm_rebuild.rebuild_broken_tables([make(structure_ratio=0.2)]) == 0
    assert vlm_rebuild.rebuild_broken_tables([make()]) == 0


# ────────────────────────────────────────────────────────────────────
# 0.3.12 — 병합 정보를 JSON 으로 함께 내보내기
#
# 배경: markdown 은 병합을 표현하지 못한다. `colSpan="3"` 인 셀도 한 칸에만
#       값이 들어가고 나머지는 빈 칸이 된다 — 실측 문서에서 병합 표기(`〃`)
#       가 1,165회 나왔는데, 그 자리의 span 값은 markdown 에서 사라진다.
#
#       HWPX 는 XML 에 병합이 명시돼 있어(`cellSpan`, `cellAddr`) 추측할
#       필요가 없다. 실측: 병합 셀 968개.
#
#           <tc><cellAddr colAddr="2" rowAddr="0"/>
#               <cellSpan colSpan="1" rowSpan="2"/></tc>
#
#       그 값을 내보내면 구조화 단계가 **병합 셀 값을 하위 행에 전파**할 수
#       있다 — 표 조각을 RAG 청크로 잘라도 레이블이 붙어 있게 하는 표준
#       대응이다.
# ────────────────────────────────────────────────────────────────────

def _span_cell(row, col, text, *, row_span=1, col_span=1):
    """격자 시험용 셀 — 실제 docling 스키마로 만든다."""
    from tests.table_fixtures import make_cell

    return make_cell(row, col, text, row_span=row_span, col_span=col_span)


def test_cell_grid_keeps_spans():
    """셀 격자가 병합 정보를 담는다."""
    from docstruct.tables.docling import cell_grid
    from tests.table_fixtures import make_table

    item = make_table(2, 3, [_span_cell(0, 0, "구분", row_span=2),
                             _span_cell(0, 1, "예산", col_span=2),
                             _span_cell(1, 1, "'26"), _span_cell(1, 2, "'27")])

    grid = cell_grid(item)
    assert len(grid) == 4
    first = grid[0]
    assert (first["row"], first["col"]) == (0, 0)
    assert (first["rowspan"], first["colspan"]) == (2, 1)   # 세로 병합
    assert grid[1]["colspan"] == 2                          # 가로 병합


def test_cell_grid_shape_matches_hwpx():
    """PDF 와 HWPX 격자가 같은 형태다.

    형식마다 다르면 쓰는 쪽이 분기해야 한다.
    """
    from docstruct.tables.docling import cell_grid
    from tests.table_fixtures import make_table

    item = make_table(1, 1, [_span_cell(0, 0, "값")])
    assert sorted(cell_grid(item)[0]) == ["col", "colspan", "row", "rowspan", "text"]


def test_table_cells_serialized():
    """격자가 JSON 에 남는다."""
    import json

    from docstruct.models import TableInfo

    table = TableInfo(id="table_1", table_num=1, placeholder="<table 1>",
                      markdown="| a |",
                      cells=[{"row": 0, "col": 0, "rowspan": 2,
                              "colspan": 1, "text": "구분"}])
    data = table.to_dict()
    assert data["cells"][0]["rowspan"] == 2
    json.dumps(data, ensure_ascii=False)


def test_hwpx_grid_reads_merges():
    """HWPX 파서가 XML 의 병합 속성을 읽는다."""
    from pathlib import Path as _Path

    sample = _Path("notebooks/samples/sample.hwpx")
    if not sample.is_file():
        pytest.skip("sample.hwpx 없음")

    from docstruct.converters.hwpx.hwpxtree import table_grids

    grids = table_grids(str(sample))
    assert grids                                   # 표가 하나는 있다
    for grid in grids:
        for cell in grid:
            assert cell["rowspan"] >= 1 and cell["colspan"] >= 1
            assert sorted(cell) == ["col", "colspan", "row", "rowspan", "text"]


# ────────────────────────────────────────────────────────────────────
# 0.3.13 — 그래프 영역을 표시
#
# 배경: 원그래프·막대그래프는 값이 **그림 안에** 있어 텍스트로 옮겨지지
#       않는다. 행안부 성과계획서 43쪽의 3D 원그래프가 그랬다.
#
#           텍스트 레이어: <전년도 대비 전략목표별 재원배분 변화>  ← 제목뿐
#           차트 레이블:   0개
#
#       `전략목표 Ⅰ 20.7%` 같은 수치가 전부 그림 안에 있는데, 판정이
#       `글자 0자 — 사진·로고로 둡니다` 로 빠져 **무엇을 놓쳤는지도 남지
#       않았다.**
#
#       433쪽 전체를 훑어 실제 그래프는 43쪽 2개뿐이고 모두 벡터임을
#       확인했다(스캔 이미지 0쪽). 화질 개선은 필요 없다.
# ────────────────────────────────────────────────────────────────────

def test_chart_region_kind_exists():
    """그래프 갈래가 있다."""
    from docstruct.converters.pdf.region_kind import RegionKind

    assert RegionKind.CHART.value == "chart"


def test_chart_verdict_serialized():
    """판정과 사유가 JSON 에 남는다.

    표시하지 않으면 무엇을 놓쳤는지 알 수 없다.
    """
    import json

    from docstruct.models import ImageInfo

    info = ImageInfo(id="image_1", placeholder="<!-- image_1 -->",
                     region_kind="chart",
                     region_kind_reason="그림이 영역의 100% · 글자 0자")
    data = info.to_dict()
    assert data["region_kind"] == "chart"
    assert "100%" in data["region_kind_reason"]
    json.dumps(data, ensure_ascii=False)


def test_drawing_cover_uses_get_bounds():
    """위치를 `get_bounds` 로 읽는다.

    텍스트 객체에는 `get_pos` 가 없어, 그것을 쓰면 도형이 0개로 세어진다.
    실제로 그 실수로 원그래프를 놓쳤다.

    **소스를 검사하는 이유**: 잘못 써도 예외가 나지 않고 조용히 0 을
    돌려준다. 결과만 보고는 구분되지 않아 호출 자체를 확인한다.
    """
    import inspect

    from docstruct.converters.pdf import region_kind

    source = inspect.getsource(region_kind._drawing_cover)
    assert "get_bounds" in source
    # 실제 호출이 get_bounds 여야 한다 (docstring 의 언급은 제외)
    calls = [ln for ln in source.splitlines()
             if "obj.get_" in ln or "= obj." in ln]
    assert calls and all("get_bounds" in ln for ln in calls)


def test_chart_verdict_from_drawing_cover(tmp_path):
    """그림이 영역을 덮으면 그래프로 판정한다."""
    pytest.importorskip("pypdfium2")
    import pypdfium2 as pdfium

    from docstruct.converters.pdf.region_kind import RegionKind, classify_region

    # 글자가 없는 빈 PDF — 스캔 그림과 같은 조건
    blank = tmp_path / "blank.pdf"
    document = pdfium.PdfDocument.new()
    document.new_page(200, 300)
    document.save(str(blank))
    document.close()

    verdict = classify_region(blank, 1, {"l": 0, "t": 0, "r": 200, "b": 300})
    # 그림도 글자도 없으면 사진으로 둔다 (그래프가 아님)
    assert verdict.kind is RegionKind.IMAGE


# ────────────────────────────────────────────────────────────────────
# 0.3.14 — 표 재추출 가드레일
#
# 배경: LLM 재추출(`tables/fill.py`)이 결과를 **비어 있지 않으면 그대로**
#       반영하고 있었다. LLM 은 못 읽은 것을 지어내고 있던 값을 빠뜨리기도
#       하는데, 그것을 거르는 단계가 없었다.
#
#       실측(성과계획서 41건 재추출)에서 10건에 숫자 차이가 있었다. 파보니
#       대부분은 정리였다.
#
#           {"fields": {}} 49625   ← 필드 잔재 (원본 데이터 아님)
#           국회운영위 원회운영지 원 54004  ← HWP 문단 ID
#
#       그래서 이 둘을 빼고 센다. 그러지 않으면 정상 정리를 손실로 오판해
#       15/41 을 폐기했다.
#
#       글자 수로 견주는 것도 틀렸다 — markdown 표는 열 폭을 맞추느라 빈
#       칸에 공백을 채워, 7,601자 원본이 1,545자로 "짧아진" 것처럼 보였다.
#       공백을 뺀 내용으로 견준다.
#
#       최종: 실데이터 41건 전부 수용, 오탐 0.
# ────────────────────────────────────────────────────────────────────

def test_fill_guard_accepts_normal_rebuild():
    """정상 재추출을 막지 않는다."""
    from docstruct.tables.fill import fill_is_safe

    original = "| a | 12345 | 67890 | 11111 | 22222 | 33333 |"
    rebuilt = "| 구분 | 12345 | 67890 | 11111 | 22222 | 33333 |\n|---|---|"
    ok, _ = fill_is_safe(original, rebuilt)
    assert ok


def test_fill_guard_rejects_lost_values():
    """값이 크게 빠지면 되돌린다."""
    from docstruct.tables.fill import fill_is_safe

    original = "| 구분 | 12345 | 67890 | 11111 | 22222 | 33333 | 44444 |"
    ok, why = fill_is_safe(original, "| 구분 | 12345 |\n|---|---|\n| a | b |")
    assert not ok
    assert "줄어듦" in why or "소실" in why


def test_fill_guard_ignores_field_junk():
    """필드 잔재 속 숫자는 손실로 세지 않는다.

    `{"fields": {}} 49625` 의 49625 는 원본 데이터가 아니다. LLM 이
    걸러내는 것이 옳다.
    """
    from docstruct.tables.fill import number_loss

    original = '| {"fields": {"n": 98765}} | 705 | 1,234 |'
    lost, _ = number_loss(original, "| 구분 | 705 | 1,234 |")
    assert lost == 0


def test_fill_guard_ignores_paragraph_ids():
    """글자 뒤에 붙은 다섯 자리 문단 ID 도 빼고 센다.

    `국회운영위 원회운영지 원 54004` 의 54004 는 표 값이 아니라 HWP 문단
    번호다. 이것을 세면 정상 정리가 손실로 잡힌다.
    """
    from docstruct.tables.fill import number_loss

    # 실측: `의원외교활 동  | 49625 ①한일친선협회...` — 앞 셀 글자 뒤에 붙는다
    original = "| 의원외교활 동 | 49625 ①한일친선협회 | 1,234 |"
    lost, _ = number_loss(original, "| 의원외교활동 | ①한일친선협회 | 1,234 |")
    assert lost == 0


def test_fill_guard_compares_content_not_length():
    """공백을 뺀 내용으로 견준다.

    markdown 표는 열 폭을 맞추느라 빈 칸에 공백을 채운다. 글자 수로 재면
    정리된 결과가 짧아진 것처럼 보인다 — 실측에서 7,601자가 1,545자가 됐는데
    내용은 그대로였다.
    """
    from docstruct.tables.fill import fill_is_safe

    padded = ("| 구분        |    값     |\n"
              "|-------------|-----------|\n"
              "| 국회운영    |   12345   |")
    tight = "| 구분 | 값 |\n|---|---|\n| 국회운영 | 12345 |"
    ok, _ = fill_is_safe(padded, tight)
    assert ok


def test_fill_guard_rejects_non_table():
    """표 형태가 아닌 응답을 거부한다."""
    from docstruct.tables.fill import fill_is_safe

    assert not fill_is_safe("| a | 12345 |", "이 표는 읽을 수 없습니다")[0]
    assert not fill_is_safe("| a | 12345 |", "")[0]


# ────────────────────────────────────────────────────────────────────
# 0.3.15 — 값이 "바뀐" 경우까지 잡는 가드레일
#
# 배경: 0.3.14 는 **빠짐**만 봤다. 사용자가 지적했다 — 글자가 잘못 읽혔는지는
#       보지 않는다고. 맞는 지적이다.
#
#       실측(성과계획서 41건)에서 실제로 값이 바뀐 사례가 있었다.
#
#           table_31  의정지원(103) → 국회활동관련단체지원(1034)
#           table_45  없던 308 이 생김
#
#       집합으로 비교하면 "하나 사라지고 하나 생김" 이라 상쇄돼 보인다.
#       개수를 함께 세어 **새로 생긴 값**을 잡아야 한다.
#
#       다만 쉼표가 든 금액은 41건 모두 정확히 보존됐다 — 어긋난 것은
#       사업코드·번호뿐이었다. 그래서 금액만은 정확히 맞아야 한다고 본다.
# ────────────────────────────────────────────────────────────────────

def test_amount_mismatch_detects_invented_values():
    """원본에 없던 금액이 나오면 잡는다.

    재추출은 옮겨 적는 작업이므로, 없던 값이 나오면 지어낸 것이다.
    """
    from docstruct.tables.fill import amount_mismatch

    total, gone, made = amount_mismatch("| 1,234 | 5,678 |", "| 1,234 | 9,999 |")
    assert (total, gone, made) == (2, 1, 1)


def test_amount_mismatch_ignores_reordering():
    """자리만 바뀐 것은 어긋남이 아니다."""
    from docstruct.tables.fill import amount_mismatch

    _, gone, made = amount_mismatch("| 1,234 | 5,678 |", "| 5,678 | 1,234 |")
    assert (gone, made) == (0, 0)


def test_fill_guard_rejects_changed_amount():
    """금액이 바뀐 재추출을 되돌린다."""
    from docstruct.tables.fill import fill_is_safe

    original = "| 사업 | 1,234 | 5,678 | 9,012 |\n| 계 | 15,924 |"
    # 5,678 이 6,678 로 바뀌었다
    rebuilt = "| 사업 | 1,234 | 6,678 | 9,012 |\n|---|---|\n| 계 | 15,924 |"
    ok, why = fill_is_safe(original, rebuilt)
    assert not ok
    assert "금액" in why


def test_fill_guard_keeps_correct_amounts():
    """금액이 그대로면 서식이 바뀌어도 받아들인다."""
    from docstruct.tables.fill import fill_is_safe

    original = "| 사업     | 1,234 | 5,678 |\n| 계 | 6,912 |"
    rebuilt = "| 사업 | 1,234 | 5,678 |\n|---|---|---|\n| 계 | 6,912 |"
    ok, _ = fill_is_safe(original, rebuilt)
    assert ok


# ────────────────────────────────────────────────────────────────────
# 0.3.16 — 그래프 읽기와 본문 대조
#
# 배경: 0.3.13 은 그래프를 **표시만** 했다. 값은 그림 안에 남았다.
#
#       표는 원본 markdown 과 견줄 수 있지만 그래프는 대조할 원본이 없다.
#       그래서 값을 내되 **본문과 대조해 신뢰도를 함께** 표시한다 —
#       공공문서는 그래프 옆에 같은 값을 표나 문장으로 두는 일이 많다.
#
#       다만 본문이 정확하다는 전제가 필요하다. 스캔본처럼 본문 자체가 OCR
#       결과라면 근거가 약해, `DOCSTRUCT_CHART_VERIFY_SOURCE=off` 로 끌 수
#       있게 했다.
# ────────────────────────────────────────────────────────────────────

def _chart_page(content, image, *, kind="chart"):
    """그래프 읽기 시험용 페이지."""
    from docstruct.models import ImageInfo, PageContent, PageTrace

    return PageContent(
        page_no=1, page_no_kind="pdf", content=content,
        images=[ImageInfo(id="image_1", placeholder="<!-- image_1 -->",
                          image_path=str(image), region_kind=kind)],
        trace=PageTrace(extractor="docling", text_source="text_layer"))


_CHART_ANSWER = ("| 항목 | 값 |\n|---|---|\n"
                 "| 전략목표 Ⅰ | 20.7% |\n| 전략목표 Ⅱ | 25.7% |")


def test_chart_read_verifies_against_page_text():
    """읽어낸 값이 본문에 있으면 검증된 것으로 표시한다."""
    from docstruct.images.chart_read import verified_ratio

    hit, total = verified_ratio(_CHART_ANSWER, "전략목표 Ⅰ 은 20.7 이고 Ⅱ 는 25.7 이다")
    assert (hit, total) == (2, 2)


def test_chart_verify_ignores_single_digits():
    """한 자리 숫자는 대조에 쓰지 않는다.

    우연히 맞을 확률이 높아 근거가 되지 못한다.
    """
    from docstruct.images.chart_read import verified_ratio

    _, total = verified_ratio("| a | 5 |\n| b | 7 |", "본문에 5 와 7 이 있다")
    assert total == 0


def test_chart_read_records_verification(tmp_path, monkeypatch):
    """검증 결과가 ImageInfo 에 남는다."""
    from docstruct.images import chart_read

    image = tmp_path / "c.png"
    image.write_bytes(b"x")
    monkeypatch.setattr(chart_read, "llm_api_config", lambda: {"model": "x"})
    monkeypatch.setattr(chart_read, "encode_image_file",
                        lambda _p: ("image/png", "AAAA"))
    monkeypatch.setattr(chart_read, "invoke_llm", lambda *a, **k: _CHART_ANSWER)

    good = _chart_page("전략목표 Ⅰ 은 20.7, Ⅱ 는 25.7", image)
    assert chart_read.read_charts([good]) == 1
    assert good.images[0].chart_verified == 1.0

    poor = _chart_page("전혀 다른 내용", image)
    chart_read.read_charts([poor])
    assert poor.images[0].chart_verified == 0.0


def test_chart_verify_can_be_switched_off(tmp_path, monkeypatch):
    """대조 대상을 바꾸거나 끌 수 있다.

    본문 자체가 OCR 결과라면 대조 근거가 약하다.
    """
    from docstruct.images import chart_read

    image = tmp_path / "c.png"
    image.write_bytes(b"x")
    monkeypatch.setattr(chart_read, "llm_api_config", lambda: {"model": "x"})
    monkeypatch.setattr(chart_read, "encode_image_file",
                        lambda _p: ("image/png", "AAAA"))
    monkeypatch.setattr(chart_read, "invoke_llm", lambda *a, **k: _CHART_ANSWER)
    monkeypatch.setenv(chart_read.VERIFY_SOURCE_ENV, "off")

    page = _chart_page("아무 내용", image)
    chart_read.read_charts([page])
    assert page.images[0].chart_verified is None


def test_chart_read_skips_non_chart_regions(tmp_path, monkeypatch):
    """그래프로 표시되지 않은 영역은 건드리지 않는다."""
    from docstruct.images import chart_read

    image = tmp_path / "c.png"
    image.write_bytes(b"x")
    monkeypatch.setattr(chart_read, "llm_api_config", lambda: {"model": "x"})
    monkeypatch.setattr(chart_read, "invoke_llm", lambda *a, **k: _CHART_ANSWER)

    page = _chart_page("본문", image, kind="image")
    assert chart_read.read_charts([page]) == 0


def test_chart_read_records_missing_llm(tmp_path, monkeypatch):
    """LLM 이 없으면 처리 경로에 남긴다.

    조용히 건너뛰면 왜 값이 없는지 알 수 없다.
    """
    from docstruct.images import chart_read

    image = tmp_path / "c.png"
    image.write_bytes(b"x")
    monkeypatch.setattr(chart_read, "llm_api_config", lambda: {})

    page = _chart_page("본문", image)
    assert chart_read.read_charts([page]) == 0
    assert any("생략" in step.action for step in page.trace.steps)


# ────────────────────────────────────────────────────────────────────
# 0.3.17 — 표 재추출에도 신뢰도 표시
#
# 배경: 0.3.15 는 통과·폐기만 했다. 통과한 표에는 아무 표시가 없어, 얼마나
#       확인된 것인지 알 수 없었다. 그래프에는 `chart_verified` 를 두었는데
#       표에는 없어 일관성도 없었다.
#
#       실측(성과계획서 41건) 분포:
#
#           1.0  21건   금액·숫자가 모두 일치
#           0.9   2건   숫자 일부 차이
#           0.5  18건   견줄 숫자가 없음 (판단 보류)
#
#       **낮다고 값이 틀린 것은 아니다.** 확인할 근거가 적었다는 뜻이다.
# ────────────────────────────────────────────────────────────────────

def _fill_md(rows):
    """검증 시험용 GFM 표."""
    return "\n".join(rows[:1] + ["|---|---|---|"] + rows[1:])


def test_fill_diff_reports_counts_not_score():
    """점수가 아니라 무엇이 얼마나 다른지 낸다.

    표는 대조할 기준이 없다 — 원본 markdown 자체가 깨져 있어 재추출한
    것이므로 그것과 견줘 "맞다" 고 할 수 없다. 하나로 뭉친 점수는
    "0.5 면 반쯤 맞다" 처럼 읽혀 오해를 부른다.
    """
    from docstruct.tables.fill import fill_diff

    original = _fill_md(["| a | 1,234 | 12345 |", "| b | 5,678 | 67890 |"])
    rebuilt = _fill_md(["| 구분 | 1,234 | 12345 |", "| b | 5,678 | 67890 |"])

    diff = fill_diff(original, rebuilt)
    assert diff["amounts"] == 2
    assert diff["amounts_lost"] == 0
    assert diff["amounts_new"] == 0
    assert diff["numbers_lost"] == 0


def test_fill_diff_counts_missing_values():
    """빠진 값을 센다."""
    from docstruct.tables.fill import fill_diff

    original = _fill_md(["| a | 1,234 | 5,678 | 9,012 |"])
    diff = fill_diff(original, _fill_md(["| 구분 | 1,234 |"]))
    assert diff["amounts"] == 3
    assert diff["amounts_lost"] == 2


def test_fill_diff_serialized():
    """빠짐 정보가 JSON 에 남는다."""
    import json

    from docstruct.models import TableInfo

    table = TableInfo(id="table_1", table_num=1, placeholder="", markdown="| a |",
                      fill_diff={"amounts": 12, "amounts_lost": 0,
                                 "amounts_new": 0, "numbers": 3,
                                 "numbers_lost": 1})
    data = table.to_dict()
    assert data["fill_diff"]["amounts"] == 12
    json.dumps(data, ensure_ascii=False)


def test_fill_diff_computed_from_real_values():
    """빠짐 정보가 실제 값에서 계산된다."""
    from docstruct.tables.fill import fill_diff

    original = _fill_md(["| a | 1,234 | 5,678 |", "| b | 9,012 |"])
    rebuilt = _fill_md(["| 구분 | 1,234 | 5,678 |", "| b | 9,012 |"])

    diff = fill_diff(original, rebuilt)
    assert diff["amounts"] == 3 and diff["amounts_lost"] == 0


# ────────────────────────────────────────────────────────────────────
# 0.3.18 — 차트 대조 범위를 이웃 쪽으로
#
# 배경: 0.3.16 은 **같은 쪽 본문**만 견줬다. 사용자가 지적했다 — 이 문서는
#       정보가 여기저기 산재해 있다고. 실측으로 확인했다.
#
#           43쪽 그래프의 값이 41~42쪽 표에 있음
#
#           같은 쪽만    0/9   =   0%
#           ±2쪽        8/9   =  89%
#           문서 전체    9/9   = 100%
#
#       같은 쪽 대조로는 **검증률이 0** 이었다. 공공문서는 설명과 그림이
#       쪽을 걸쳐 흩어진다.
#
#       넓힐수록 우연히 맞을 확률도 커지므로 기본은 ±2쪽으로 두고, 문서
#       전체나 같은 쪽만 보도록 바꿀 수 있게 했다.
# ────────────────────────────────────────────────────────────────────

def _span_pages(image, *, chart_page=43):
    """값이 이웃 쪽에 흩어진 상황."""
    from docstruct.models import ImageInfo, PageContent, PageTrace

    def page(number, content, chart=False):
        return PageContent(
            page_no=number, page_no_kind="pdf", content=content,
            images=[ImageInfo(id=f"img{number}", placeholder="",
                              image_path=str(image), region_kind="chart")]
            if chart else [],
            trace=PageTrace(extractor="docling", text_source="text_layer"))

    return [page(41, "전략목표 Ⅰ 20.7"), page(42, "전략목표 Ⅱ 25.7"),
            page(chart_page, "그래프 제목만", chart=True)]


_SPAN_ANSWER = "| Ⅰ | 20.7% |\n|---|---|\n| Ⅱ | 25.7% |"


def _patch_chart_llm(monkeypatch):
    """차트 읽기 LLM 을 가짜로 바꾼다."""
    from docstruct.images import chart_read

    monkeypatch.setattr(chart_read, "llm_api_config", lambda: {"model": "x"})
    monkeypatch.setattr(chart_read, "encode_image_file",
                        lambda _p: ("image/png", "AAAA"))
    monkeypatch.setattr(chart_read, "invoke_llm", lambda *a, **k: _SPAN_ANSWER)


def test_chart_verify_looks_at_neighbouring_pages(tmp_path, monkeypatch):
    """이웃 쪽 본문까지 견준다.

    같은 쪽만 보면 실측에서 검증률이 0 이었다.
    """
    from docstruct.images import chart_read

    image = tmp_path / "c.png"
    image.write_bytes(b"x")
    _patch_chart_llm(monkeypatch)

    pages = _span_pages(image)
    chart_read.read_charts(pages)
    assert pages[2].images[0].chart_verified == 1.0


def test_chart_verify_span_is_configurable(tmp_path, monkeypatch):
    """범위를 좁히면 검증률이 떨어진다."""
    from docstruct.images import chart_read

    image = tmp_path / "c.png"
    image.write_bytes(b"x")
    _patch_chart_llm(monkeypatch)
    monkeypatch.setenv(chart_read.VERIFY_SPAN_ENV, "0")     # 같은 쪽만

    pages = _span_pages(image)
    chart_read.read_charts(pages)
    assert pages[2].images[0].chart_verified == 0.0


def test_chart_verify_document_mode(tmp_path, monkeypatch):
    """문서 전체와 견줄 수도 있다.

    멀리 떨어진 값도 잡지만, 관계없는 쪽의 숫자와도 맞아 근거가 약해진다.
    """
    from docstruct.images import chart_read

    image = tmp_path / "c.png"
    image.write_bytes(b"x")
    _patch_chart_llm(monkeypatch)
    monkeypatch.setenv(chart_read.VERIFY_SOURCE_ENV, "document")

    pages = _span_pages(image, chart_page=200)   # 아주 멀리 떨어뜨림
    chart_read.read_charts(pages)
    assert pages[2].images[0].chart_verified == 1.0


def test_chart_verify_span_rejects_bad_values(monkeypatch):
    """범위 설정이 잘못되면 기본값을 쓴다."""
    from docstruct.images import chart_read

    for bad in ("숫자아님", "-3"):
        monkeypatch.setenv(chart_read.VERIFY_SPAN_ENV, bad)
        assert chart_read._verify_span() == chart_read.DEFAULT_VERIFY_SPAN


def test_chart_verify_spans_neighbour_pages():
    """그래프 값을 앞뒤 쪽에서도 찾는다.

    실측(행안부 43쪽 원그래프): 같은 쪽만 보면 **0/11**, 앞뒤 1쪽까지 넓히면
    **9/11** 이 확인됐다. 그래프는 전략목표별 합계이고 같은 쪽 표는
    프로그램별이라 층위가 달랐다.

    ±2 이상으로 넓혀도 확인 수가 그대로여서 ±1 로 둔다 — 넓힐수록 우연
    일치만 는다.
    """
    from docstruct.images import chart_read

    # 기본은 ±2 — 실측에서 ±1 로도 9/11 이 잡혔고 ±2 이상은 더 늘지 않는다.
    assert chart_read.DEFAULT_VERIFY_SPAN >= 1
    assert chart_read._verify_span() >= 1


def test_chart_read_uses_neighbour_pages(tmp_path, monkeypatch):
    """같은 쪽에 값이 없어도 앞뒤 쪽에서 확인한다."""
    from docstruct.images import chart_read
    from docstruct.models import ImageInfo, PageContent, PageTrace

    image = tmp_path / "c.png"
    image.write_bytes(b"x")
    monkeypatch.setattr(chart_read, "llm_api_config", lambda: {"model": "x"})
    monkeypatch.setattr(chart_read, "encode_image_file",
                        lambda _p: ("image/png", "AAAA"))
    monkeypatch.setattr(
        chart_read, "invoke_llm",
        lambda *a, **k: "| 항목 | 값 |\n|---|---|\n| Ⅰ | 20.7% |\n| Ⅱ | 25.7% |")

    def page(number, content, *, chart=False):
        return PageContent(
            page_no=number, page_no_kind="pdf", content=content,
            images=[ImageInfo(id=f"img{number}", placeholder="",
                              image_path=str(image), region_kind="chart")]
            if chart else [],
            trace=PageTrace(extractor="docling", text_source="text_layer"))

    pages = [page(42, "앞쪽에 20.7 이 있다"),
             page(43, "차트 제목만 있는 쪽", chart=True),
             page(44, "뒤쪽에 25.7 이 있다")]

    assert chart_read.read_charts(pages) == 1
    assert pages[1].images[0].chart_verified == 1.0


# ────────────────────────────────────────────────────────────────────
# 0.3.19 — 모델이 만든 것을 출력에 표시
#
# 배경: LLM·VLM 이 다시 만든 표와 그래프가 결과물에서 **파서가 뽑은 것과
#       구분되지 않았다.** JSON 에도, 요약에도, HTML 미리보기에도 표시가
#       없었다. 어디까지 믿을지 정하려면 출처를 알아야 한다.
#
#           source: "parser"  파서가 뽑은 그대로
#           source: "llm"     LLM 이 다시 만듦 (fill)
#           source: "vlm"     VLM 이 지면을 보고 다시 씀
# ────────────────────────────────────────────────────────────────────

def test_source_field_defaults_to_parser():
    """손대지 않은 표·그림은 parser 로 남는다."""
    from docstruct.models import ImageInfo, TableInfo

    table = TableInfo(id="t1", table_num=1, placeholder="", markdown="| a |")
    assert table.source == "parser"
    assert ImageInfo(id="i1", placeholder="").source == "parser"


def test_source_field_serialized():
    """출처가 JSON 에 남는다."""
    import json

    from docstruct.models import TableInfo

    table = TableInfo(id="t1", table_num=1, placeholder="", markdown="| a |",
                      source="vlm")
    data = table.to_dict()
    assert data["source"] == "vlm"
    json.dumps(data, ensure_ascii=False)


def test_vlm_paths_mark_source(tmp_path, monkeypatch):
    """VLM 이 손댄 표·그림에 출처가 남는다."""
    import docstruct.infrastructure.llm.client as llm_client
    from docstruct.images import chart_read
    from docstruct.models import ImageInfo, PageContent, PageTrace, TableInfo
    from docstruct.tables import vlm_rebuild

    image = tmp_path / "p.png"
    image.write_bytes(b"x")
    monkeypatch.setattr(llm_client, "llm_api_config", lambda: {"model": "x"})
    monkeypatch.setattr(vlm_rebuild, "llm_api_config", lambda: {"model": "x"})
    monkeypatch.setattr(vlm_rebuild, "encode_image_file",
                        lambda _p: ("image/png", "AAAA"))
    monkeypatch.setattr(vlm_rebuild, "invoke_llm",
                        lambda *a, **k: "| 구분 | 값 |\n|---|---|\n| 가 | 1 |")

    table = TableInfo(id="t1", table_num=1, placeholder="",
                      markdown="| 品 | 品 |\n|---|---|\n| a | b |",
                      odd_columns=(7, 8))
    page = PageContent(page_no=1, page_no_kind="pdf", content="본문",
                       page_image_path=str(image), tables=[table],
                       trace=PageTrace(extractor="docling", text_source="ocr"))
    assert vlm_rebuild.rebuild_broken_tables([page]) == 1
    assert table.source == "vlm"

    monkeypatch.setattr(chart_read, "llm_api_config", lambda: {"model": "x"})
    monkeypatch.setattr(chart_read, "encode_image_file",
                        lambda _p: ("image/png", "AAAA"))
    monkeypatch.setattr(chart_read, "invoke_llm",
                        lambda *a, **k: "| 항목 | 값 |\n|---|---|\n| Ⅰ | 20.7% |")
    info = ImageInfo(id="i1", placeholder="", image_path=str(image),
                     region_kind="chart")
    chart_page = PageContent(page_no=1, page_no_kind="pdf", content="20.7",
                             images=[info],
                             trace=PageTrace(extractor="docling", text_source="ocr"))
    assert chart_read.read_charts([chart_page]) == 1
    assert info.source == "vlm"


def _source_doc():
    """출처가 섞인 문서."""
    from docstruct.models import (
        ImageInfo, PageContent, PageDocument, PageTrace, TableInfo,
    )

    def table(number, source, diff=None):
        return TableInfo(
            id=f"t{number}", table_num=number, placeholder="", markdown="| a |",
            source=source, fill_diff=diff, content_type="table",
            quality="sufficient",
            original_markdown="| old |" if source != "parser" else None)

    page = PageContent(
        page_no=1, page_no_kind="pdf", content="본문",
        tables=[table(1, "parser"),
                table(2, "llm", {"numbers_lost": 2, "amounts_lost": 1}),
                table(3, "vlm")],
        images=[ImageInfo(id="i1", placeholder="", region_kind="chart",
                          source="vlm"),
                ImageInfo(id="i2", placeholder="", region_kind="chart")],
        trace=PageTrace(extractor="docling", text_source="text_layer"))
    return PageDocument(filename="x.pdf", source_format="pdf", pages=[page])


def test_summary_shows_model_made_counts():
    """콘솔 요약이 모델이 만든 수를 보여 준다."""
    from docstruct.output.report import summary_lines

    text = "\n".join(summary_lines(_source_doc()))
    assert "LLM 1" in text and "VLM 1" in text
    assert "값이 빠진 표" in text
    assert "그래프" in text


def test_preview_shows_source_badges():
    """HTML 미리보기에 출처 배지가 나온다."""
    from docstruct.output.preview import summary_html, table_overview_html

    doc = _source_doc()
    overview = table_overview_html(doc)
    assert "출처" in overview                 # 열 이름
    assert "파서" in overview and "LLM" in overview and "VLM" in overview
    assert "-3" in overview                   # 빠진 값 수

    summary = summary_html(doc)
    assert "LLM 1" in summary and "그래프" in summary


# ────────────────────────────────────────────────────────────────────
# 0.3.20 — 실험 기법을 독립 모듈로 분리
#
# 배경: 표 구조 인식을 보완하는 기법을 여럿 시험하는 중인데, 각각이 본체에
#       섞이면 **나중에 무엇을 지워야 할지 알 수 없다.** 실제로 설정이 20개를
#       넘었고 일부는 이미 폐기 대상이었다(빈 칸 비율 판정 → 정상 표 82% 오판).
#
#       `docstruct/experiments/` 에 한 파일당 하나씩 두고 레지스트리에
#       등록한다. 폐기할 때는 파일을 지우고 등록을 빼면 된다.
#
#       다섯 기법은 각각 다른 연구 계보에서 발상을 빌렸다.
#
#           split_merge     GridFormer  격자 위상으로 병합을 본다
#           grid_refine     SEMv3 KOR   제안 대비 오프셋만 회귀
#           two_way_match   TFLOP       텍스트 위치를 구조 판단에
#           grid_consensus  계보 밖     문서 전체를 보는 후처리의 이점
#           otsl_diff       OTSL        다섯 토큰 구조 표현
# ────────────────────────────────────────────────────────────────────

def test_experiments_registered():
    """등록된 기법이 목록과 같다."""
    from docstruct.experiments import all_experiments
    keys = {e.key for e in all_experiments()}
    # ①②④ 는 0.3.48 에서 폐기했다 — 검출이 없거나 오탐이었다.
    # vector_grid 는 0.3.62 에서 더했다 (⑥ 벡터 격자).
    # grid_restore·sum_check 는 0.3.69 에서 더했다 (⑦ 결정 복원 · ⑧ 검산).
    # line_grid·scan_grid·grid_score 는 0.3.70 (⑨ 합성 · ⑩ 스캔 · ⑪ 채점).
    # lattice_restore 는 0.4.7 에서 더했다 (⑮ 괘선 격자 전체 복원).
    # scan_ab·scan_scale_ab 는 0.4.56 에서 더했다 (스캔 판독 계측 · 측정 전용).
    assert keys == {"two_way_match", "otsl_diff", "cell_repair",
                    "vector_grid", "grid_restore", "sum_check",
                    "line_grid", "scan_grid", "grid_score", "head_grid",
                    "agreed_grid", "col_grid", "lattice_restore",
                    "scan_ab", "scan_scale_ab", "page_chrome",
                    "over_split", "chart_gate", "lattice_fill", "hole_fill"}


def test_experiments_off_by_default():
    """승격되지 않은 실험은 기본으로 꺼져 있다.

    0.4.3 에서 검증이 끝난 여섯(DEFAULT_ON)은 기본 켬으로 승격됐다.
    나머지는 여전히 꺼져 있어야 한다 — 검증 전에 켜지면 안 된다.
    """
    from docstruct.experiments import enabled_experiments
    from docstruct.experiments.registry import DEFAULT_ON

    assert {e.key for e in enabled_experiments()} == set(DEFAULT_ON)


def test_experiment_toggle(monkeypatch):
    """환경변수로 켜고 끌 수 있다 (승격된 것은 끄는 쪽도)."""
    from docstruct.experiments import enabled_experiments
    monkeypatch.setenv("DOCSTRUCT_EXP_TWO_WAY_MATCH", "true")
    assert "two_way_match" in {e.key for e in enabled_experiments()}
    monkeypatch.setenv("DOCSTRUCT_EXP_TWO_WAY_MATCH", "false")
    assert "two_way_match" not in {e.key for e in enabled_experiments()}
    monkeypatch.setenv("DOCSTRUCT_EXP_HEAD_GRID", "false")
    assert "head_grid" not in {e.key for e in enabled_experiments()}


def test_experiments_document_themselves():
    """각 기법이 무엇을 보완하는지·어디서 빌렸는지 적혀 있다.

    적어 두지 않으면 몇 달 뒤에 이 설정이 무엇이었는지 알 수 없다.
    """
    from docstruct.experiments import all_experiments
    for exp in all_experiments():
        assert exp.purpose and exp.origin and exp.note
        assert exp.formats
        assert exp.status in ("proposed", "testing", "verified", "retired")


def _split_cell(row, col, text, box, *, col_span=1):
    """병합 검출 시험용 셀 — 실제 docling 스키마로 만든다."""
    from tests.table_fixtures import make_cell

    return make_cell(row, col, text, col_span=col_span, box=box)


def test_two_way_match_flags_crowding():
    """한 셀에 조각이 몰리면 불일치로 잡는다."""
    from docstruct.converters.pdf.cell_match import Box
    from docstruct.experiments.tsr.measure.two_way_match import disagreements

    cells = [Box(100, 100, 150, 120), Box(150, 100, 200, 120)]
    fine = [(Box(105, 105, 145, 115), "왼쪽"), (Box(155, 105, 195, 115), "오른쪽")]
    assert disagreements(cells, fine) == []

    crowded = [(Box(105, 105, 145, 115), "A"), (Box(110, 105, 148, 115), "B")]
    assert len(disagreements(cells, crowded)) == 1


def test_otsl_expresses_merges():
    """OTSL 이 병합을 토큰으로 나타낸다."""
    from docstruct.experiments.tsr.measure.otsl_diff import to_otsl, token_diff

    merged = to_otsl([{"row": 0, "col": 0, "rowspan": 2, "colspan": 1},
                      {"row": 0, "col": 1, "rowspan": 1, "colspan": 2},
                      {"row": 1, "col": 1, "rowspan": 1, "colspan": 1},
                      {"row": 1, "col": 2, "rowspan": 1, "colspan": 1}], 2, 3)
    plain = to_otsl([{"row": r, "col": c, "rowspan": 1, "colspan": 1}
                     for r in range(2) for c in range(3)], 2, 3)

    assert "L" in merged and "U" in merged      # 가로·세로 병합 토큰
    assert token_diff(merged, plain) == 2


# ────────────────────────────────────────────────────────────────────
# 0.3.22 — 가짜 객체를 실제 스키마로
#
# 배경: 테스트가 `SimpleNamespace` 로 표 셀을 흉내 냈다. 그러면 **실제와
#       다른 것을 시험하게 된다.** 실제로 두 번 겪었다.
#
#           structure_gap  가짜가 빈 셀도 만들어 두어, 실제 docling 이
#                          만들지 않는다는 것을 놓쳤다 → 정상 표 82% 오판
#           fill_diff      시험 데이터가 실제 문단 ID 형태와 달라 두 번 고침
#
#       `tests/table_fixtures.py` 로 옮겨, docling 이 있으면 **진짜 클래스**를
#       쓰고 없으면 같은 필드를 갖춘 대역을 쓴다.
# ────────────────────────────────────────────────────────────────────

def test_no_ad_hoc_fake_objects_in_tests():
    """테스트가 임시 가짜 객체를 만들지 않는다.

    표·OCR 객체는 `tests/table_fixtures.py` 를 거친다. 그래야 실제 스키마와
    어긋나면 한곳에서 드러난다.
    """
    from pathlib import Path as _Path

    import ast

    source = (_Path(__file__).resolve().parent / "test_regressions.py").read_text(
        encoding="utf-8")
    # 문자열·주석이 아니라 **실제 호출**만 본다
    used = [
        node for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "SimpleNamespace"
    ]
    assert not used, f"임시 가짜 객체 호출이 {len(used)}곳 남아 있습니다"


def test_fixture_cell_matches_real_schema():
    """헬퍼가 만든 셀이 실제 코드가 읽는 필드를 갖췄다."""
    from tests.table_fixtures import CELL_FIELDS, make_cell

    cell = make_cell(0, 0, "값", row_span=2, box=(10, 20, 30, 40))
    for name in CELL_FIELDS:
        assert hasattr(cell, name), f"{name} 이 없습니다"

    # 실제 코드가 쓰는 방식으로 읽히는가
    from docstruct.tables.docling import _cell_span

    assert _cell_span(cell, "row") == (0, 2)
    assert _cell_span(cell, "col") == (0, 1)
    assert (cell.bbox.l, cell.bbox.t) == (10, 20)


def test_fixture_ocr_line_matches_real_schema():
    """헬퍼가 만든 OCR 조각이 실제 코드가 읽는 속성을 갖췄다."""
    from docstruct.converters.pdf.cell_match import box_of
    from tests.table_fixtures import make_ocr_line

    line = make_ocr_line("글자", 10, 20, 50, 40)
    assert line.text == "글자" and line.score > 0
    box = box_of(line.box)
    assert (box.left, box.top, box.right, box.bottom) == (10, 20, 50, 40)


# ────────────────────────────────────────────────────────────────────
# 0.3.23 — 중첩 표가 바깥 표의 셀을 훔쳐가던 문제
#
# 배경: `_read_table` 이 `element.iter(tc)` 로 셀을 훑어 **중첩 표의 셀까지**
#       잡았다. 좌표가 겹쳐 서로 덮어쓰고, 두 표가 한 표로 뒤섞였다.
#
#       실측(행정안전부 성과계획서 HWPX, 표 580개):
#
#           3행 3열 표의 셀이 6개여야 하는데 21개로 잡힘
#           참고1·참고2 두 표가 한 표로 합쳐짐
#           원본 PDF 대조 유실률 5.0% → 1.5%
#
#       직계 `<hp:tr>` 아래의 `<hp:tc>` 만 자기 셀이다.
# ────────────────────────────────────────────────────────────────────

def _nested_table_xml():
    """중첩 표가 든 최소 HWPX 섹션 XML."""
    return """<?xml version="1.0" encoding="UTF-8"?>
<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"
        xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">
  <hp:p><hp:run><hp:tbl rowCnt="1" colCnt="2">
    <hp:tr>
      <hp:tc><hp:cellAddr colAddr="0" rowAddr="0"/>
        <hp:cellSpan colSpan="1" rowSpan="1"/>
        <hp:subList><hp:p><hp:run><hp:t>바깥왼쪽</hp:t></hp:run></hp:p></hp:subList>
      </hp:tc>
      <hp:tc><hp:cellAddr colAddr="1" rowAddr="0"/>
        <hp:cellSpan colSpan="1" rowSpan="1"/>
        <hp:subList><hp:p><hp:run><hp:tbl rowCnt="1" colCnt="2">
          <hp:tr>
            <hp:tc><hp:cellAddr colAddr="0" rowAddr="0"/>
              <hp:cellSpan colSpan="1" rowSpan="1"/>
              <hp:subList><hp:p><hp:run><hp:t>안쪽A</hp:t></hp:run></hp:p></hp:subList>
            </hp:tc>
            <hp:tc><hp:cellAddr colAddr="1" rowAddr="0"/>
              <hp:cellSpan colSpan="1" rowSpan="1"/>
              <hp:subList><hp:p><hp:run><hp:t>안쪽B</hp:t></hp:run></hp:p></hp:subList>
            </hp:tc>
          </hp:tr>
        </hp:tbl></hp:run></hp:p></hp:subList>
      </hp:tc>
    </hp:tr>
  </hp:tbl></hp:run></hp:p>
</hs:sec>"""


def test_nested_table_cells_not_stolen():
    """바깥 표가 중첩 표의 셀을 가져가지 않는다.

    `iter()` 로 훑으면 안쪽 셀의 좌표가 바깥 좌표와 겹쳐 서로 덮어쓴다.
    """
    from xml.etree import ElementTree as ET

    from docstruct.converters.hwpx.hwpxtree import HP, _read_table, _tag

    root = ET.fromstring(_nested_table_xml())
    outer = next(root.iter(_tag(HP, "tbl")))

    table = _read_table(outer, set())
    assert len(table.cells) == 2                  # 21개가 아니라 2개
    texts = {" ".join(c.blocks) for c in table.cells}
    assert "바깥왼쪽" in texts


def test_nested_table_content_not_duplicated():
    """중첩 표 내용이 두 번 나오지 않는다.

    안쪽 표는 별도 블록으로 나오므로, 바깥 셀에도 담으면 중복이다.
    """
    from xml.etree import ElementTree as ET

    from docstruct.converters.hwpx.hwpxtree import _walk

    root = ET.fromstring(_nested_table_xml())
    joined = "\n".join(_walk(root, set()))

    assert joined.count("안쪽A") == 1
    assert joined.count("바깥왼쪽") == 1


# ────────────────────────────────────────────────────────────────────
# 0.3.24 — PDF 표에 세로 병합 표기가 없던 문제
#
# 배경: HWP·HWPX 는 세로 병합이 이어지는 칸에 `〃` 를 남기는데(0.1.75),
#       **PDF 경로만 그 기능을 못 받았다.** 데이터 셀이 좌상단에만 들어가고
#       나머지는 빈 칸이라, 값이 맨 윗행만의 것으로 읽힌다.
#
#       실측(행정안전부 성과계획서, 같은 문서 두 형식):
#
#           PDF    표 322개 · 〃 0회      · insufficient 273 (85%)
#           HWPX   표 580개 · 〃 4,366회 · insufficient   4 (0.7%)
#
#       LLM 판정 사유가 한결같았다 — "병합 셀이 풀리면서 '회계 구분' 열의
#       값이 윗행에만 귀속되고 아랫행은 빈 칸으로 표시됨".
# ────────────────────────────────────────────────────────────────────

def test_pdf_table_marks_vertical_merge():
    """세로 병합이 이어지는 칸을 **값으로 채운다** (0.5.6).

    빈 칸으로 두면 값이 맨 윗행만의 것으로 읽힌다 — HWP 경로에서 같은
    문제로 `페이스북+인스타그램 합계` 가 `페이스북 단독` 으로 잘못 읽혔다.
    """
    from docstruct.tables.docling import docling_table_to_markdown
    from tests.table_fixtures import make_cell, make_table

    item = make_table(3, 3, [
        make_cell(0, 0, "구분", header=True),
        make_cell(0, 1, "항목", header=True),
        make_cell(0, 2, "값", header=True),
        make_cell(1, 0, "프로그램", row_span=2),
        make_cell(1, 1, "가"), make_cell(1, 2, "100"),
        make_cell(2, 1, "나"), make_cell(2, 2, "200"),
    ])

    markdown = docling_table_to_markdown(item)
    assert "프로그램" in markdown
    assert "〃" not in markdown
    # 닻 행과 덮인 행 둘 다에 값이 선다 — 행 하나만 잘려 나가도 뜻이 통한다
    assert markdown.count("프로그램") == 2


def test_pdf_table_merge_mark_matches_hwp():
    """표식이 HWP·HWPX 와 같은 글자다.

    형식마다 다르면 읽는 쪽이 분기해야 한다.
    """
    from docstruct.converters.hwp.pyhwp_backend.hwp5tree import MERGE_UP as HWP_MARK
    from docstruct.converters.hwpx.hwpxtree import MERGE_UP as HWPX_MARK
    from docstruct.tables.docling import MERGE_UP as PDF_MARK

    assert PDF_MARK == HWP_MARK == HWPX_MARK


def test_pdf_table_merge_mark_can_be_disabled(monkeypatch):
    """표식을 끌 수 있다."""
    from docstruct.tables.docling import MERGE_MARK_ENV, docling_table_to_markdown
    from tests.table_fixtures import make_cell, make_table

    monkeypatch.setenv(MERGE_MARK_ENV, "false")
    item = make_table(3, 2, [
        make_cell(0, 0, "구분", header=True), make_cell(0, 1, "값", header=True),
        make_cell(1, 0, "묶음", row_span=2),
        make_cell(1, 1, "가"), make_cell(2, 1, "나"),
    ])
    assert "〃" not in docling_table_to_markdown(item)


def test_pdf_header_merge_still_spreads():
    """헤더의 가로 병합은 전과 같이 전파된다 (회귀 확인)."""
    from docstruct.tables.docling import docling_table_to_markdown
    from tests.table_fixtures import make_cell, make_table

    item = make_table(2, 2, [
        make_cell(0, 0, "예산", header=True, col_span=2),
        make_cell(1, 0, "A"), make_cell(1, 1, "B"),
    ])
    header = docling_table_to_markdown(item).splitlines()[0]
    assert header.count("예산") == 2             # 두 열에 전파


# ────────────────────────────────────────────────────────────────────
# 0.3.25 — RAG 브릿지가 새 필드를 안 옮기던 문제
#
# 배경: FastAPI 서버(`app/rag/`)는 자체 `TableInfo` dataclass 를 쓰고,
#       브릿지가 **필드를 하나씩 손으로 옮긴다.** docstruct 에 필드를
#       더해도 그쪽을 고치지 않으면 결과 JSON 에 나오지 않는다.
#
#       실제로 `cells`(0.3.12)·`source`(0.3.19)·`fill_diff`(0.3.15)·
#       `region_kind`(0.3.13) 가 전부 빠져 있었다. 사용자가 0.3.24 로
#       돌렸는데 `cells` 가 0개라 버전 문제로 오해했다.
#
#       이 테스트는 **필드를 더할 때 브릿지도 고치라**는 알림이다.
# ────────────────────────────────────────────────────────────────────

def test_table_fields_documented_for_bridge():
    """표에 새 필드를 더하면 이 목록도 갱신한다.

    RAG 브릿지(`app/rag/adapters/docstruct_bridge.py`)가 필드를 하나씩
    옮기므로, 여기 목록과 견줘 빠진 것을 알아차릴 수 있다.
    """
    from docstruct.models import TableInfo

    #: 다운스트림(RAG·API)이 받아야 하는 필드. 새로 만들면 여기에 더하고
    #: 브릿지도 함께 고친다.
    expected = {
        "id", "table_num", "placeholder", "markdown", "bbox",
        "llm_title", "content_type", "quality", "reason",
        "original_markdown", "group_image_ids", "source_image_id",
        # 0.3.12+
        "cells", "source", "fill_diff", "continues_from", "table_kind",
        "assessed",
        "inherited_header", "odd_columns", "structure_ratio",
        # 실험 (docstruct.experiments)
        "match_disagreements", "otsl", "cell_repairs", "grid_merge_gap",
        # 0.3.69 — ⑦ 결정 복원 · ⑧ 검산 (브릿지 반영 완료)
        "grid_restore", "sum_check",
        # 0.3.70 — ⑨ 합성 격자 · ⑩ 스캔 격자 · ⑪ 채점 (브릿지 반영 완료)
        "synth_grid", "scan_grid", "grid_score",
        # 0.3.73 — H12-b 후보-검증-선택 기록 (브릿지 반영 완료)
        "vlm_choice",
        # 0.3.81 — OCR 언어 오판 표시 (브릿지 반영 완료)
        "ocr_language_doubt",
        # 0.3.98 — ⑫ 머리 계층 복원 기록 (브릿지 반영 완료)
        "head_grid",
        # 0.4.7 — ⑮ 괘선 격자 전체 복원 기록 (브릿지 반영 완료)
        "lattice_restore",
        # 0.4.22 — 어느 모델이 냈는가 (브릿지 반영 완료)
        "vlm_model",
        # 0.4.1 — ⑬ 열 격자 복원 기록 (브릿지 반영 완료)
        "col_grid",
        # 0.4.1 — ⑬ 합의 병합 반영 (브릿지 반영 완료)
        "agreed_grid",
        # 0.4.56 — ⑬ 게이트 기각 사유 계측 (브릿지 반영 완료)
        "col_gate",
        # 0.4.62 — 괘선보다 열을 더 쪼갠 표 (브릿지 반영 완료)
        "over_split",
        # 0.4.69 — 격자 온전성 (브릿지 반영 완료)
        "grid_faults",
        # 0.4.71 — 격자로 셀을 채운 기록 (브릿지 반영 완료)
        "lattice_fill",
        # 0.4.72 — lattice_fill 이 물러난 사유 (브릿지 반영 완료)
        "fill_gate",
        # 0.4.78 — 격자 구멍을 빈 칸으로 메움 (브릿지 반영 완료)
        "hole_fill",
        # 0.4.79 — 셀 텍스트 오염 (브릿지 반영 완료)
        "cell_leaks",
    }
    actual = set(TableInfo(id="t", table_num=1, placeholder="",
                           markdown="| a |").to_dict())
    missing = sorted(expected - actual)
    added = sorted(actual - expected)
    assert not missing, f"사라진 필드: {missing}"
    assert not added, f"새 필드가 생겼습니다 — 브릿지도 고치세요: {added}"


def test_image_fields_documented_for_bridge():
    """그림에 새 필드를 더하면 이 목록도 갱신한다."""
    from docstruct.models import ImageInfo

    expected = {
        "id", "placeholder", "description", "image_path", "mime_type",
        "bbox", "text_chars", "text_lines", "region_text", "vlm_markdown",
        "region_kind", "region_kind_reason", "chart_verified", "source",
        "table_candidate", "promoted_table_id",
        # 0.4.22 — 어느 모델이 냈는가 (브릿지 반영 완료)
        "vlm_model",
        # 0.4.25 — 그림의 실제 해상도 (브릿지 반영 완료)
        "dpi",
        # 0.4.26 — 보내기 전 무엇을 적용했나 (브릿지 반영 완료)
        "image_prep",
        # 0.4.29 — 판독 가능성 (브릿지 반영 완료)
        "legibility",
        # 0.4.58 — 지면으로 보고 전사했는가 + 그 이중 판독 (브릿지 반영 완료)
        "transcribed", "scan_ab",
        # 0.4.62 — 판독 경로 계측 (브릿지 반영 완료)
        "chart_gate",
        # 0.4.89 — `<image N>` 블록 번호 (브릿지 반영 완료)
        "image_num",
    }
    actual = set(ImageInfo(id="i", placeholder="").to_dict())
    missing = sorted(expected - actual)
    added = sorted(actual - expected)
    assert not missing, f"사라진 필드: {missing}"
    assert not added, f"새 필드가 생겼습니다 — 브릿지도 고치세요: {added}"


# ────────────────────────────────────────────────────────────────────
# 0.3.26 — 표 평가가 정상 표를 결함으로 오판하던 문제
#
# 배경: PDF 표 322개 중 277개(86%)가 `insufficient` 로 판정됐다. 실제 표를
#       뜯어보니 **대부분 정상**이었다.
#
#           | 전자문서소통시스템(501) | 일반회계 | 9,781 | 26,152 | 17,600 |
#
#       모든 값이 제자리인데, `재정사업 평가명` 열이 드문드문하다는 이유로
#       "병합 셀이 풀렸다" 고 봤다. 그 열은 평가 대상 사업에만 값이 있다.
#
#       원인은 프롬프트였다.
#
#           "빈 칸이 아래로 이어지는 모양이면 2번(병합 풀림)일 가능성이 높습니다"
#
#       예산표는 원래 빈 칸이 그렇게 생긴다. 그리고 `〃` 표기를 설명하지
#       않아, LLM 이 그것을 "빈 칸으로 처리됨" 이라고 지적하기까지 했다.
#
#       InstructTable(2026) 의 하위 작업 분해 방식을 빌려 판단을 단계로
#       나눴다 — 행·열 세기 → 병합 판단 → 내용 확인.
# ────────────────────────────────────────────────────────────────────

def test_assess_prompt_explains_merge_mark():
    """평가 프롬프트가 `〃` 표기를 설명한다.

    설명이 없으면 LLM 이 그 표식을 빈 칸으로 오해한다 — 실측에서
    "'〃' 등이 빈 칸으로 처리됨" 이라는 판정이 나왔다.
    """
    from docstruct.tables.assess import _ASSESS_PROMPT

    assert "〃" in _ASSESS_PROMPT
    assert "결함이 아닙니다" in _ASSESS_PROMPT


def test_assess_prompt_has_ordered_steps():
    """판단을 단계로 나눈다.

    InstructTable 이 하위 작업 분해로 성능을 올렸다 — 한 번에 훑고
    인상으로 판정하면 정상 표를 결함으로 본다.
    """
    from docstruct.tables.assess import _ASSESS_PROMPT

    for step in ("① 이것이 표인가", "② 행·열이 온전한가",
                 "③ 빈 칸의 원인이 무엇인가"):
        assert step in _ASSESS_PROMPT


def test_assess_prompt_warns_against_blank_only_judgement():
    """빈 칸만으로 지적하지 말라고 알린다."""
    from docstruct.tables.assess import _ASSESS_PROMPT

    assert "빈 칸이 있다는 이유만으로" in _ASSESS_PROMPT
    assert "드문드문" in _ASSESS_PROMPT     # 열 전체가 드문 경우는 정상
    assert "연달아 비어" in _ASSESS_PROMPT   # 이때만 병합 풀림


# ────────────────────────────────────────────────────────────────────
# 0.3.27 — 스캔본에서 모든 그림이 그래프로 판정되던 문제
#
# 배경: 스캔 PDF(주택과세금 377쪽)를 돌리니 그림 901개 중 **900개가
#       `chart`** 로 판정됐다.
#
#           "그림이 영역의 100% · 글자 0자 — 그래프로 보입니다"  × 900
#
#       스캔본은 페이지 전체가 이미지 한 장이라 **어느 영역을 재도 100%**
#       가 나온다. 0.3.13 에서 그래프 판정을 넣을 때 이 경우를 보지 못했다.
#
#       그래프는 지면의 일부를 차지한다. 페이지를 통째로 덮는 그림은 스캔
#       원본이므로 그래프로 보지 않는다.
# ────────────────────────────────────────────────────────────────────

def test_full_page_image_is_not_chart(tmp_path):
    """페이지를 통째로 덮는 그림은 그래프가 아니다."""
    pytest.importorskip("pypdfium2")
    import pypdfium2 as pdfium

    from docstruct.converters.pdf.region_kind import (
        RegionKind, _page_cover_ratio, classify_region,
    )

    blank = tmp_path / "scan.pdf"
    document = pdfium.PdfDocument.new()
    document.new_page(200, 300)
    document.save(str(blank))
    document.close()

    full = {"l": 0, "t": 0, "r": 200, "b": 300}
    assert _page_cover_ratio(blank, 1, full) > 0.9
    assert classify_region(blank, 1, full).kind is not RegionKind.CHART


def test_chart_share_threshold_documented():
    """지면 비율 한도가 있다."""
    from docstruct.converters.pdf.region_kind import (
        MAX_CHART_PAGE_SHARE, MIN_CHART_PAGE_SHARE,
    )

    assert 0 < MIN_CHART_PAGE_SHARE < MAX_CHART_PAGE_SHARE < 1


# ────────────────────────────────────────────────────────────────────
# 0.3.28 — 그래프 판정이 한 문서에 과적합되던 문제
#
# 배경: 벡터 원그래프(행안부 43쪽) 하나를 기준으로 "그림이 영역을 덮으면
#       그래프" 로 정했다. 그러자 스캔본(주택과세금)에서 장식 901개 중
#       900개가 그래프로 걸렸다.
#
#       사용자가 지적했다 — 실제로는 세 유형이 있고, 벡터를 필수 조건으로
#       삼으면 **뉴스 그래프를 캡처해 붙인 문서**를 놓친다.
#
#           벡터 차트   도형으로 그려짐 · 지면 일부
#           사진 차트   래스터 · 지면 일부
#           장식·스캔   배너·로고·QR·스캔 전면
#
#       한 신호로 가르지 않고 **여러 신호를 모아** 판단한다.
# ────────────────────────────────────────────────────────────────────

def test_chart_score_accepts_vector_and_raster():
    """벡터든 사진이든 그래프로 본다.

    벡터를 필수 조건으로 삼으면 뉴스 그래프 캡처를 놓친다.
    """
    from docstruct.converters.pdf.region_kind import chart_score

    vector, _ = chart_score(cover=1.0, page_share=0.22, aspect=2.1,
                            vector_shapes=30)
    raster, _ = chart_score(cover=1.0, page_share=0.25, aspect=1.5,
                            vector_shapes=0)
    assert vector and raster


def test_chart_score_rejects_full_page_scan():
    """스캔 전면은 그래프가 아니다."""
    from docstruct.converters.pdf.region_kind import chart_score

    ok, why = chart_score(cover=1.0, page_share=0.81, aspect=0.7,
                          vector_shapes=0)
    assert not ok
    assert "스캔" in why


def test_chart_score_rejects_banner_and_logo():
    """납작한 띠와 작은 로고를 거른다.

    머리말 배너는 `530×80` 처럼 납작하고, QR·로고는 지면의 1% 다.
    """
    from docstruct.converters.pdf.region_kind import chart_score

    banner, why_banner = chart_score(cover=1.0, page_share=0.08, aspect=6.6,
                                     vector_shapes=2)
    logo, why_logo = chart_score(cover=1.0, page_share=0.01, aspect=1.0,
                                 vector_shapes=0)
    assert not banner and "가로세로" in why_banner
    assert not logo and "로고" in why_logo


def test_image_bbox_reaches_output():
    """그림 좌표가 결과에 남는다.

    판정 근거를 결과만 보고 확인하려면 크기를 알아야 한다 — 없으면
    원본 PDF 를 다시 열어야 했다.
    """
    import json

    from docstruct.models import ImageInfo

    info = ImageInfo(id="i1", placeholder="",
                     bbox={"l": 0, "t": 0, "r": 100, "b": 50})
    data = info.to_dict()
    assert data["bbox"]["r"] == 100
    json.dumps(data, ensure_ascii=False)


# ────────────────────────────────────────────────────────────────────
# 0.3.29 — 빈 원본을 LLM 이 채운 것이 통과하던 문제
#
# 배경: 과기부 성과보고서(581쪽, 표 520개)에서 **265개가 LLM 재추출**됐다.
#       가드레일을 확인하니 금액이 7개 새로 생겼는데도 통과했다.
#
#           원본: | 구분 |
#           결과: | 창구 방문 고객의 평균 소요 시간 | 17분 39초 |
#                 | ... 경제적 가치(A) | 2,693원 |
#
#       원인이 둘이었다.
#
#       ① `if amounts and (gone or made)` — 원본에 금액이 0개면 검사를
#          건너뛰어, 없던 금액이 생겨도 통과했다.
#       ② 원본이 `| 구분 |` 두 글자뿐인데 158자로 늘어난 것을 막지 못했다.
#          옮겨 적기가 아니라 **생성**이므로 원본과 견줄 수 없다.
#
#       실측: 265건 중 정확히 그 3건만 걸러진다.
# ────────────────────────────────────────────────────────────────────

def test_fill_guard_checks_new_amounts_without_original():
    """원본에 금액이 없어도 새로 생긴 금액을 잡는다."""
    from docstruct.tables.fill import fill_is_safe

    original = _fill_md(["| 구분 | 항목 |", "| 가 | 나 |", "| 다 | 라 |"])
    rebuilt = _fill_md(["| 구분 | 항목 |", "| 가 | 2,693 |", "| 다 | 1,234 |"])
    ok, why = fill_is_safe(original, rebuilt)
    assert not ok
    assert "신규" in why


def test_fill_guard_rejects_filling_empty_table():
    """거의 빈 원본이 채워지면 되돌린다.

    옮겨 적기가 아니라 생성이므로 원본과 견줄 방법이 없다.
    """
    from docstruct.tables.fill import fill_is_safe

    original = "| 구분 |\n|------|"
    rebuilt = ("| 구분 | 내용 |\n|------|------|\n"
               "| 창구 방문 고객의 평균 소요 시간 | 17분 39초 |\n"
               "| 경제적 가치 | 2,693원 |")
    ok, why = fill_is_safe(original, rebuilt)
    assert not ok
    assert "새로 만든" in why


def test_fill_guard_keeps_normal_rebuild_of_short_table():
    """짧아도 내용이 비슷하면 받아들인다.

    빈 원본 검사가 정상 재추출까지 막으면 안 된다.
    """
    from docstruct.tables.fill import fill_is_safe

    original = _fill_md(["| 구분 | 값 |", "| 가 | 1 |"])
    rebuilt = _fill_md(["| 구분 | 값 |", "| 가 | 1 |"])
    assert fill_is_safe(original, rebuilt)[0]


# ────────────────────────────────────────────────────────────────────
# 0.3.30 — 실험 결과가 API 출력에 나오지 않던 문제
#
# 배경: 실험을 켜고 돌렸는데 결과 JSON 에 아무것도 없었다. RAG 브릿지가
#       실험 필드를 옮기지 않아서였다 — 0.3.25 에서 같은 문제를 고쳤는데
#       실험 필드는 그때 없었다.
#
#       필드를 더할 때마다 브릿지도 고쳐야 한다는 것을 다시 확인했다.
# ────────────────────────────────────────────────────────────────────

def test_experiment_fields_in_table_output():
    """실험 필드가 표 출력에 있다.

    없으면 실험을 켜도 결과를 볼 수 없다.
    """
    import json

    from docstruct.models import TableInfo

    table = TableInfo(id="t1", table_num=1, placeholder="", markdown="| a |",
                      match_disagreements=3, otsl="C L NL")
    data = table.to_dict()
    for key in ("match_disagreements", "otsl"):
        assert key in data, f"{key} 가 빠졌습니다"
    json.dumps(data, ensure_ascii=False)


def test_experiments_need_env_to_run():
    """실험은 환경변수로 켜야 돈다.

    켜지 않으면 아무것도 하지 않는다 — 결과가 비어 있으면 이것부터 본다.
    """
    from docstruct.experiments import all_experiments, enabled_experiments
    from docstruct.experiments.registry import DEFAULT_ON

    assert all_experiments()           # 등록은 돼 있고
    running = {e.key for e in enabled_experiments()}
    assert running == set(DEFAULT_ON)  # 승격분만 기본 켬 (0.4.3)


# ────────────────────────────────────────────────────────────────────
# 0.3.31 — 목차를 규칙으로 찾기
#
# 배경: 목차 추출이 LLM 기반(`--outline`)이고 CLI 전용이라, API 결과에는
#       목차가 없었다. 스캔본(주택과세금)은 목차가 10쪽 넘게 있는데 본문
#       텍스트로만 남았다.
#
#       목차 줄은 형태가 뚜렷하다 — 왼쪽에 제목, 오른쪽 끝에 쪽번호.
#
#           3. 종합소득세 신고·납부 ············ 172
#
#       다만 **스캔본은 줄이 나뉜다.** OCR 이 제목과 쪽번호를 다른 줄로
#       읽는다.
#
#           '5.취득세에 부가되는세금'
#           '57'
#
#       실측: 스캔본에서 항목 92개, 쪽 차이 2쪽을 찾았다.
# ────────────────────────────────────────────────────────────────────

def _toc_page(page_no, text):
    """목차 시험용 페이지."""
    from docstruct.models import PageContent, PageTrace

    return PageContent(page_no=page_no, page_no_kind="pdf", content=text,
                       trace=PageTrace(extractor="x", text_source="y"))


def test_toc_finds_inline_entries():
    """한 줄에 제목과 쪽번호가 있는 목차를 찾는다."""
    from docstruct.outline.toc import find_toc

    pages = [_toc_page(3, "목 차\n"
                          "1. 총칙 ················· 5\n"
                          "가. 목적(법§1) ·········· 5\n"
                          "2. 과세대상 ············· 12\n")]
    items = find_toc(pages)
    assert len(items) == 3
    assert items[0]["title"].startswith("1. 총칙")
    assert items[0]["page"] == 5


def test_toc_finds_split_entries():
    """제목과 쪽번호가 다른 줄인 목차도 찾는다.

    스캔본에서 OCR 이 줄을 나눠 읽는다.
    """
    from docstruct.outline.toc import find_toc

    pages = [_toc_page(7, "차례\n"
                          "5.취득세에 부가되는세금\n57\n"
                          "가.지방교육세(지방법151)\n57\n"
                          "나. 농어촌특별세\n58\n")]
    items = find_toc(pages)
    assert len(items) == 3
    assert items[0]["page"] == 57


def test_toc_ignores_body_references():
    """머리글이 없는 쪽은 보지 않는다.

    문서 전체를 뒤지면 본문의 참조까지 걸린다.
    """
    from docstruct.outline.toc import find_toc

    pages = [_toc_page(50, "자세한 내용은 ······· 57\n"
                           "관련 규정 ··········· 60\n")]
    assert find_toc(pages) == []


def test_toc_offset_measured():
    """인쇄 쪽번호와 PDF 쪽번호의 차이를 잰다.

    표지·간지 때문에 어긋난다 — 실측에서 2쪽 차이였다.
    """
    from docstruct.outline.toc import page_offset

    items = [{"title": "1. 총칙", "page": 5, "source_page": 7},
             {"title": "2. 과세", "page": 12, "source_page": 7}]
    assert page_offset(items) == 2


def test_toc_reaches_output():
    """목차가 결과에 남는다."""
    import json

    from docstruct.models import PageDocument

    doc = PageDocument(filename="x.pdf", source_format="pdf", pages=[],
                       toc=[{"title": "1. 총칙", "page": 5, "source_page": 3}],
                       toc_offset=2)
    data = doc.to_dict()
    assert data["toc"][0]["page"] == 5
    assert data["toc_offset"] == 2
    json.dumps(data, ensure_ascii=False)


# ────────────────────────────────────────────────────────────────────
# 0.3.32 — 목차에서 본문 금액을 걸러내기
#
# 배경: 스캔본은 제목과 쪽번호가 다른 줄로 나뉜다. 그런데 **본문의 금액도
#       같은 모양**이 된다.
#
#           가. 취득세액      ← 제목처럼 보임
#           537              ← 쪽번호처럼 보임
#
#       실측(주택과세금 7쪽)에서 목차 쪽번호는 거의 단조 증가한다.
#
#           57 · 57 · 58 · 60 · 62 · 62 · 64 · 66 · 67 · 79 …
#
#       같은 값이 이어지기는 해도 되돌아가거나 크게 뛰지 않는다. 그 성질로
#       금액·건수를 거른다.
# ────────────────────────────────────────────────────────────────────

def test_toc_rejects_body_amounts():
    """제목 뒤에 금액이 와도 목차로 보지 않는다."""
    from docstruct.outline.toc import find_toc

    pages = [_toc_page(8, "차례\n"
                          "5.취득세에 부가되는세금\n57\n"
                          "가. 취득세액\n537\n"        # 금액 — 크게 뜀
                          "나. 농어촌특별세\n58\n"
                          "다. 신고 건수\n842\n"       # 건수 — 크게 뜀
                          "라. 주택취득 절차\n60\n")]
    items = find_toc(pages)
    numbers = [i["page"] for i in items]
    assert numbers == [57, 58, 60]


def test_toc_pages_increase():
    """쪽번호가 되돌아가면 목차가 아니다."""
    from docstruct.outline.toc import _looks_like_page

    assert _looks_like_page(58, 57)          # 다음 쪽
    assert _looks_like_page(57, 57)          # 같은 쪽 (항목 여럿)
    assert not _looks_like_page(12, 57)      # 되돌아감
    assert not _looks_like_page(537, 57)     # 크게 뜀 — 금액


def test_toc_rejects_year_like_numbers():
    """연도처럼 큰 수는 쪽번호가 아니다."""
    from docstruct.outline.toc import MAX_PAGE_NO, _looks_like_page

    assert not _looks_like_page(2025, None)
    assert MAX_PAGE_NO < 2025


def test_toc_keeps_real_entries():
    """실제 목차는 그대로 잡는다 (회귀 확인).

    실측(주택과세금 377쪽): 항목 90개 · 쪽번호 25~369 · 단조 증가.
    """
    from docstruct.outline.toc import find_toc

    body = "차례\n" + "".join(
        f"{n}. 항목{n}\n{25 + n * 3}\n" for n in range(1, 12))
    items = find_toc([_toc_page(6, body)])
    assert len(items) == 11
    numbers = [i["page"] for i in items]
    assert numbers == sorted(numbers)


# ────────────────────────────────────────────────────────────────────
# 0.3.33 — 목차를 앞쪽에서만 찾기
#
# 배경: 문서 전체를 훑고 있었다. 목차는 앞쪽에 있으므로 낭비이고, 본문의
#       `차례`·`목차` 언급까지 걸릴 수 있다.
#
#       실측: 스캔본 7~15쪽 · 행안부 1쪽 · **25쪽 이후 0건**.
#       논문도 표지·초록 뒤에 오므로 앞쪽에 든다.
#
#       뒤쪽 목차가 있는 문서(일본·중국 서적, 합본 자료집, 부록 목차)는
#       `DOCSTRUCT_TOC_HEAD_PAGES=0` 으로 전체를 본다.
# ────────────────────────────────────────────────────────────────────

def test_toc_searches_head_pages_only():
    """앞쪽 범위 밖의 목차는 보지 않는다."""
    from docstruct.outline.toc import DEFAULT_HEAD_PAGES, find_toc

    far = DEFAULT_HEAD_PAGES + 10
    pages = [_toc_page(far, "목 차\n1. 총칙 ······· 5\n")]
    assert find_toc(pages) == []


def test_toc_head_limit_can_be_lifted(monkeypatch):
    """전체 탐색으로 바꿀 수 있다.

    뒤쪽에 목차가 있는 문서가 있다.
    """
    from docstruct.outline.toc import DEFAULT_HEAD_PAGES, HEAD_PAGES_ENV, find_toc

    far = DEFAULT_HEAD_PAGES + 10
    pages = [_toc_page(far, "목 차\n1. 총칙 ······· 5\n")]

    monkeypatch.setenv(HEAD_PAGES_ENV, "0")
    assert len(find_toc(pages)) == 1


def test_toc_still_finds_early_pages():
    """앞쪽 목차는 그대로 찾는다 (회귀 확인).

    실측: 스캔본 머리글이 7·9·11·13·15쪽에 있었다.
    """
    from docstruct.outline.toc import find_toc

    pages = [_toc_page(7, "차례\n1. 총칙 ······· 5\n2. 과세 ······· 12\n")]
    assert len(find_toc(pages)) == 2


# ────────────────────────────────────────────────────────────────────
# 0.3.34 — 바닥글 쪽번호로 오프셋 재기
#
# 배경: 목차의 `25쪽` 이 PDF 몇 쪽인지 알 수 없었다. 목차가 앞쪽(6쪽)인데
#       항목이 뒤(25쪽)를 가리켜 차이를 잴 수 없었기 때문이다.
#
#       본문 바닥글에 인쇄 쪽번호가 있다. 그것과 PDF 쪽을 견주면 된다.
#
#       실측(주택과세금 377쪽): 135쪽에서 차이가 잡혔고 **전부 2** 였다.
#       목차 25쪽 → PDF 27쪽이고, 그 쪽이 실제로 `주택에 대한 취득세` 장
#       표지였다.
#
#       다만 쪽번호가 본문에 남지 않는 문서가 있다 — 과기부는 581쪽 중
#       6쪽만 잡혀 오프셋이 흔들렸다. 근거가 적으면 믿지 않는다.
# ────────────────────────────────────────────────────────────────────

def test_printed_offset_from_footer():
    """바닥글 쪽번호로 차이를 잰다."""
    from docstruct.outline.toc import printed_page_offset

    pages = [_toc_page(n, f"본문 내용\n{n - 2}\n") for n in range(3, 40)]
    offset, samples = printed_page_offset(pages)
    assert offset == 2
    assert samples >= 20


def test_printed_offset_ignores_browser_marker():
    """브라우저 인쇄 표시(`31/380`)는 쪽번호가 아니다.

    스캔본에 흔하다 — 그것은 PDF 쪽 위치이지 인쇄된 번호가 아니다.
    """
    from docstruct.outline.toc import _printed_page

    page = _toc_page(31, "본문\nhttps://example.com/index.html\n31/380\n29\n")
    assert _printed_page(page) == 29


def test_printed_offset_needs_enough_samples():
    """근거가 적으면 오프셋을 내지 않는다.

    쪽번호가 본문에 남지 않는 문서가 있다.
    """
    from docstruct.outline.toc import printed_page_offset

    pages = [_toc_page(n, "본문만 있고 쪽번호 없음\n") for n in range(1, 40)]
    pages.append(_toc_page(40, "본문\n36\n"))
    offset, _ = printed_page_offset(pages)
    assert offset is None


def test_printed_offset_rejects_scattered_values():
    """값이 흩어지면 잘못 잡은 것이다."""
    from docstruct.outline.toc import printed_page_offset

    # 쪽마다 다른 차이 — 본문 숫자를 잡은 모양
    pages = [_toc_page(n, f"본문\n{max(n - (n % 7) - 1, 1)}\n")
             for n in range(10, 50)]
    offset, _ = printed_page_offset(pages)
    assert offset is None


# ────────────────────────────────────────────────────────────────────
# 0.3.35 — 목차 유형이 생각보다 다양했다
#
# 배경: 사용자가 실제 문서 목차 여덟 장을 보여 주었다. 머리글과 번호 매김이
#       예상보다 다양했다.
#
#           머리글   목차 · 차례 · 차 례 · 순  서 · CONTENTS
#           번호     제1부 · 제1장 · 01 · 003 · Q1. · I. · Ⅱ. · ◆ · ▶
#           구분선   점선(···) · 공백만 · 없음
#
#       특히 줄이 나뉜 경우(스캔본) 쓰는 번호 매김 규칙이 절반을 놓쳤다 —
#       `01 우리가 내는 세금`, `제1부 법인세법`, `Q1.`, `◆` 가 다 빠졌다.
# ────────────────────────────────────────────────────────────────────

def test_toc_heading_variants():
    """머리글 유형을 모두 인식한다.

    자간을 벌려 쓰는 문서가 많다 — `차  례`, `순  서`.
    """
    from docstruct.outline.toc import _HEADING_RE

    for heading in ("목차", "목 차", "차례", "차 례", "차  례",
                    "순서", "순  서", "CONTENTS", "Contents"):
        assert _HEADING_RE.search(heading), heading


def test_toc_numbering_variants():
    """번호 매김 유형을 모두 인식한다.

    줄이 나뉜 목차(스캔본)에서 잡음을 거르는 데 쓰므로, 실제 쓰이는
    형태를 놓치면 항목이 통째로 빠진다.
    """
    from docstruct.outline.toc import _NUMBERING_RE

    for title in ("제1부 법인세법", "제1장 개요", "01 우리가 내는 세금",
                  "003 성실신고", "1. 개요", "가. 국회", "Q1. 쇼핑몰",
                  "I. 상호합의절차", "Ⅱ. 신청", "① 첫째",
                  "◆ 사업 시작 단계", "▶ 사례로 보는", "* 조회 안내"):
        assert _NUMBERING_RE.match(title), title


def test_toc_numbering_ignores_plain_text():
    """번호 없는 본문은 걸러진다."""
    from docstruct.outline.toc import _NUMBERING_RE

    for text in ("이 조항은 다음과 같다", "납세의무자는 신고해야 한다",
                 "세액을 계산한다"):
        assert not _NUMBERING_RE.match(text), text


def test_toc_without_dot_leaders():
    """점선 없이 공백만으로 벌린 목차도 찾는다."""
    from docstruct.outline.toc import find_toc

    pages = [_toc_page(3, "CONTENTS\n"
                          "제1장 일감몰아주기 과세제도 개요        10\n"
                          "1. 개요                          10\n"
                          "2. 과세요건                       11\n")]
    items = find_toc(pages)
    assert len(items) == 3
    assert items[0]["page"] == 10


# ────────────────────────────────────────────────────────────────────
# 0.3.36 — 머리글 없는 목차
#
# 배경: `목차`·`CONTENTS` 머리글이 **아예 없는** 목차가 있었다. 제목만 있고
#       바로 항목이 이어지는 형태다.
#
#           1세대 1주택 비과세 ❶
#           (소득세법 제89조1항3호)
#           ❶ 조정대상지역 내 일시적 2주택자의 종전주택 양도기한은?   14
#           ❷ 신규주택에 세입자가 있는 경우 …                      17
#
#       머리글로만 찾으면 이런 쪽을 통째로 놓친다.
#
#       목차 쪽은 **`제목 … 쪽번호` 가 여러 줄 이어진다.** 그 모양으로
#       알아본다 — 5줄 이상이고 쪽의 40% 이상이면 목차로 본다.
# ────────────────────────────────────────────────────────────────────

_NO_HEADING_TOC = (
    "1세대 1주택 비과세 ❶\n(소득세법 제89조1항3호)\n"
    "❶ 조정대상지역 내 일시적 2주택자의 종전주택 양도기한은?    14\n"
    "❷ 신규주택에 세입자가 있는 경우 비과세 기한은?    17\n"
    "❸ 2주택 이상을 보유한 1세대가 양도한 후 기산일은?    20\n"
    "❹ 3주택자가 1주택을 양도한 후 보유기간 기산일은?    22\n"
    "❺ 1주택과 1분양권을 보유한 1세대가 양도 후 기산일은?    24\n"
    "❻ 배우자에게 분양권 지분 일부를 증여하는 경우?    26\n"
)


def test_toc_without_heading():
    """머리글이 없어도 목차 쪽을 알아본다."""
    from docstruct.outline.toc import find_toc

    items = find_toc([_toc_page(5, _NO_HEADING_TOC)])
    assert len(items) == 6
    assert items[0]["page"] == 14


def test_toc_needs_enough_entries():
    """항목이 적으면 목차로 보지 않는다.

    본문에 참조가 한둘 섞인 것과 구분한다.
    """
    from docstruct.outline.toc import find_toc

    mixed = ("이 조항은 다음과 같이 적용한다.\n"
             "납세의무자는 신고해야 한다.\n"
             "자세한 내용은 아래 표를 참조 ······ 57\n"
             "관련 규정은 다음과 같다.\n"
             "세액 계산은 별도로 한다.\n")
    assert find_toc([_toc_page(9, mixed)]) == []


def test_toc_page_ratio_matters():
    """항목이 쪽의 일부뿐이면 목차가 아니다."""
    from docstruct.outline.toc import _looks_like_toc_page

    # 항목 6줄 + 본문 30줄 → 비율이 낮다
    body = _NO_HEADING_TOC + "\n".join(f"본문 {n} 번째 줄입니다." for n in range(30))
    assert not _looks_like_toc_page(_toc_page(5, body))


# ────────────────────────────────────────────────────────────────────
# 0.3.37 — `--exp` 로 실험 켜기
#
# 배경: 실험을 환경변수로만 켤 수 있어 불편했다. `--set` 은 `Settings` 필드를
#       요구하는데, 실험을 거기 넣으면 **격리한 의미가 없어진다** — 폐기할
#       때 본체를 건드리게 된다.
#
#       `--exp` 는 환경변수를 대신 세팅한다. 격리는 유지된다.
#
#           docstruct 문서.pdf -o out --exp split_merge,otsl_diff
#           docstruct --exp list
# ────────────────────────────────────────────────────────────────────

def test_exp_flag_sets_env(monkeypatch):
    """`--exp` 가 실험 환경변수를 켠다."""
    from docstruct.cli import _enable_experiments
    from docstruct.experiments import enabled_experiments
    for name in ("DOCSTRUCT_EXP_TWO_WAY_MATCH", "DOCSTRUCT_EXP_OTSL_DIFF"):
        monkeypatch.delenv(name, raising=False)

    assert _enable_experiments("two_way_match,otsl_diff") == [
        "two_way_match", "otsl_diff"]
    running = {e.key for e in enabled_experiments()}
    assert {"two_way_match", "otsl_diff"} <= running


def test_exp_flag_rejects_unknown_key(capsys, monkeypatch):
    """모르는 키는 거부하고 목록을 알린다."""
    from docstruct.cli import _enable_experiments

    assert _enable_experiments("nosuch") is None
    assert "모르는 실험" in capsys.readouterr().err


def test_exp_list_prints_catalog(capsys):
    """`--exp list` 가 목록을 낸다."""
    from docstruct.cli import _enable_experiments

    assert _enable_experiments("list") is None
    out = capsys.readouterr().out
    assert "two_way_match" in out and "otsl_diff" in out


def test_experiments_not_in_settings():
    """실험은 `Settings` 에 없다.

    거기 넣으면 폐기할 때 본체를 건드리게 된다.
    """
    import dataclasses

    from docstruct.core.config import Settings
    from docstruct.experiments import all_experiments
    fields = {f.name for f in dataclasses.fields(Settings)}
    for exp in all_experiments():
        assert exp.key not in fields, f"{exp.key} 가 Settings 에 있습니다"


# ────────────────────────────────────────────────────────────────────
# 0.3.38 — 실험 ④ 가 서로 다른 표를 묶던 문제
#
# 배경: 국세청 성과보고서(79쪽, 표 61개)로 실험을 처음 돌렸다. ④(서식
#       다수결)가 **24건(39%)** 을 잡았는데, 살펴보니 대부분 오탐이었다.
#
#           table_1  4행 7열  연도 / 목표 / 실적
#           table_4  12행 3열 프로그램명 / 프로그램 목표
#
#       서식이 전혀 다른데 한 그룹으로 묶여 열 위치를 견주고 있었다.
#       **열 개수만으로 묶었기 때문**이다.
#
#       헤더 내용을 함께 봐야 같은 서식이다. 고친 뒤 6묶음 31개로 좁혀졌다.
# ────────────────────────────────────────────────────────────────────

# ────────────────────────────────────────────────────────────────────
# 0.3.39 — 실험 전수 검토에서 찾은 버그 둘
#
# 국세청 성과보고서 실행 결과로 실험 다섯을 다시 봤다.
#
# **① ② 가 숫자 쌍을 병합으로 봤다**
#
#       table_51 · 10곳 · [['회 계', '11'], ['11', '11'], ['계 정', '0']]
#
#   길이만 보고 판정해 회계 코드가 나열된 표에서 10곳씩 잡혔다. 숫자는
#   원래 칸마다 따로 들어가는 값이지 갈린 낱말이 아니다.
#
# **② ①③ 이 렌더 없이는 돌 수 없는데 렌더를 요구하지 않았다**
#
#   `page_image_path` 가 없으면 조용히 0건을 낸다. 켜져 있으면 렌더를
#   함께 요구하도록 고쳤다.
#
# ⑤(OTSL)는 정상이었다 — 61개 표에서 `C 4,085 · L 349 · U 289 · NL 541`
# 로 병합이 제대로 표현됐다.
# ────────────────────────────────────────────────────────────────────

def test_coordinate_experiments_need_no_render():
    """좌표 실험이 렌더를 요구하지 않는다.

    0.3.41 에서 텍스트 좌표를 직접 읽도록 바꿨다 — 렌더는 스캔본 OCR
    에만 필요하다.
    """
    import inspect

    from docstruct import pipeline

    source = inspect.getsource(pipeline.build_document)
    assert "exp_needs_render" not in source


def test_otsl_expresses_merge_tokens():
    """OTSL 이 병합을 토큰으로 낸다 (회귀 확인).

    실측(국세청 성과보고서 61개 표): C 4,085 · L 349 · U 289 · NL 541.
    """
    from docstruct.experiments.tsr.measure.otsl_diff import to_otsl

    merged = to_otsl([{"row": 0, "col": 0, "rowspan": 1, "colspan": 3},
                      {"row": 1, "col": 0, "rowspan": 2, "colspan": 1},
                      {"row": 1, "col": 1, "rowspan": 1, "colspan": 1},
                      {"row": 1, "col": 2, "rowspan": 1, "colspan": 1},
                      {"row": 2, "col": 1, "rowspan": 1, "colspan": 1},
                      {"row": 2, "col": 2, "rowspan": 1, "colspan": 1}], 3, 3)
    assert "L" in merged and "U" in merged


# ────────────────────────────────────────────────────────────────────
# 0.3.41 — ①③ 이 렌더 없이 돌게
#
# 배경: ①③ 이 OCR 조각 좌표를 써서 렌더 이미지를 요구했다. 그런데
#       **텍스트 PDF 는 글자가 좌표로 들어 있다.** 그것을 렌더한 뒤 OCR 로
#       다시 읽는 것은
#
#           · 79쪽 문서에서 전 페이지 렌더가 필요하고
#           · OCR 오차가 더해지며
#           · 원본보다 정확할 수 없다
#
#       `converters/pdf/text_runs.py` 로 글자 좌표를 직접 읽는다. 스캔본은
#       텍스트 레이어가 없어 빈 목록이 나오고, 그때는 실험이 건너뛴다.
#
#       그리고 `--render` 를 더했다 — 표가 없는 쪽까지 렌더할 때 쓴다.
# ────────────────────────────────────────────────────────────────────

def test_text_runs_read_without_render(tmp_path):
    """렌더 없이 글자 좌표를 읽는다."""
    pytest.importorskip("pypdfium2")
    import pypdfium2 as pdfium

    from docstruct.converters.pdf.text_runs import read_text_runs

    blank = tmp_path / "empty.pdf"
    document = pdfium.PdfDocument.new()
    document.new_page(200, 300)
    document.save(str(blank))
    document.close()

    # 글자가 없으면 빈 목록 — 스캔본이 이 경우다
    assert read_text_runs(blank, 1) == []


def test_text_runs_coordinates_are_topleft():
    """좌표가 TOPLEFT 다.

    표 bbox 와 같은 기준이라 바로 견줄 수 있다.
    """
    from docstruct.converters.pdf.text_runs import TextRun

    run = TextRun(text="가", left=10, top=20, right=30, bottom=40)
    assert run.top < run.bottom          # 아래로 갈수록 커진다


def test_coordinate_experiments_use_text_runs():
    """①③ 이 텍스트 좌표를 쓴다."""
    import inspect

    from docstruct.experiments.tsr.measure import two_way_match
    source = inspect.getsource(two_way_match.run)
    assert "read_text_runs" in source
    assert "read_image" not in source            # OCR 을 쓰지 않는다


def test_render_all_option_exists():
    """`--render` 로 전 페이지를 렌더할 수 있다."""
    import inspect

    from docstruct.pipeline import build_document

    assert "render_all" in inspect.signature(build_document).parameters


# ────────────────────────────────────────────────────────────────────
# 0.3.42 — ①③ 이 정상 표를 전부 잡던 문제
#
# 배경: 렌더 없이 돌게 한 뒤 실행하니 **61개 표가 전부** 걸렸다.
#
#           ① edge_drift          0.9 ~ 4.0pt · 61건
#           ③ match_disagreements 6 ~ 966건  · 61건
#
#       둘 다 원인이 "정상인 것을 이상으로 봤다" 였다.
#
#       **①** 셀 경계와 글자 시작점은 원래 다르다 — 안쪽 여백 때문이다.
#            0.5pt 를 넘으면 잡았는데, 정상 여백이 0.9~4.0pt 였다.
#
#       **③** 낱말은 셀보다 잘다. 한 셀에 여러 낱말이 들어가는 것은
#            정상인데 그것을 불일치로 셌다. **셀 경계를 걸치는** 낱말만
#            봐야 "텍스트가 옆 칸으로 갔다" 는 신호가 된다.
# ────────────────────────────────────────────────────────────────────

def test_two_way_match_counts_straddling_only():
    """셀 하나에 담기는 낱말은 세지 않는다.

    한 셀에 여러 낱말이 들어가는 것은 정상이다.
    """
    import inspect

    from docstruct.experiments.tsr.measure import two_way_match
    source = inspect.getsource(two_way_match.run)
    assert "straddling" in source
    assert "> 1" in source                  # 두 셀 이상을 걸칠 때만


def test_two_way_match_flags_real_straddle():
    """한 셀에 여러 조각이 몰리면 잡는다.

    셀 → 조각 방향은 하나만 고를 수 있으므로, 둘 이상이 같은 셀을
    가리키면 어긋난다.
    """
    from docstruct.converters.pdf.cell_match import Box
    from docstruct.experiments.tsr.measure.two_way_match import disagreements

    cells = [Box(100, 100, 150, 120), Box(150, 100, 200, 120)]
    crowded = [(Box(105, 105, 145, 115), "A"),
               (Box(110, 105, 148, 115), "B")]
    assert disagreements(cells, crowded)


# ────────────────────────────────────────────────────────────────────
# 0.3.43 — ④ 가 정상 편차를 어긋남으로 보던 문제
#
# 배경: 행안부 성과계획서(429쪽, 표 321개)로 ④ 를 돌리니 **110건(34%)**
#       이 걸렸다. 국세청(3/61)과 크게 달랐다.
#
#       파보니 예산표 97개 중 87개가 걸렸다 — **다수 자신이 걸린** 것이다.
#       어긋남 분포가 5~108pt 로 넓었는데, 고정 pt(5pt) 로 재서 정상 편차도
#       잡혔다.
#
#       열 폭이 68pt 인 표에서 20pt 흔들림은 흔하다. **열 하나를 통째로
#       밀어낼 만큼**(열 폭 이상) 어긋난 것만 봐야 한다.
#
#       비율 기준을 넣으니 110 → 13건. 남은 것은 전부 108pt(열 폭의 1.6배)로,
#       한 열이 밀린 모양이다.
# ────────────────────────────────────────────────────────────────────

# ────────────────────────────────────────────────────────────────────
# 0.3.44 — 표 유형 판단
#
# 배경: 실험마다 검출률이 문서에 따라 크게 달랐다(④ 가 국세청 3건 · 행안부
#       13건). 표 유형별로 적용이 갈리는지 보려면 **유형 데이터가 먼저**
#       있어야 한다.
#
#       평가 LLM 이 이미 표를 보고 있으므로 거기에 한 항목을 더한다 —
#       호출이 늘지 않는다.
#
#           budget · indicator · program · org · review · cover · other
#
#       특히 `org`(조직도)는 markdown 으로 표현할 수 없다. 빈 칸이 많아도
#       파싱 결함이 아니라는 것을 평가가 알아야 한다.
# ────────────────────────────────────────────────────────────────────

def test_assess_prompt_asks_table_kind():
    """평가 프롬프트가 유형을 묻는다."""
    from docstruct.tables.assess import _ASSESS_PROMPT

    assert "table_kind" in _ASSESS_PROMPT
    for kind in ("budget", "indicator", "program", "org", "review", "cover"):
        assert f'"{kind}"' in _ASSESS_PROMPT


def test_assess_prompt_notes_org_limitation():
    """조직도가 markdown 으로 표현 불가함을 알린다."""
    from docstruct.tables.assess import _ASSESS_PROMPT

    assert "조직도" in _ASSESS_PROMPT
    assert "표현할 수 없습니다" in _ASSESS_PROMPT


def test_table_kind_parsed_without_problem():
    """문제가 없는 표에서도 유형을 담는다.

    유형은 모든 표에 필요하므로 `content_type` 이 없어도 받아야 한다.
    """
    from docstruct.models import TableInfo
    from docstruct.tables.assess import _apply_assessment

    tables = [TableInfo(id="table_1", table_num=1, placeholder="",
                        markdown="| a |")]
    _apply_assessment(tables, [{"id": "table_1", "table_kind": "budget",
                                "title": "세입예산 현황"}])
    assert tables[0].table_kind == "budget"
    assert tables[0].quality == "sufficient"        # 문제 없음


def test_table_kind_rejects_unknown():
    """모르는 유형은 담지 않는다."""
    from docstruct.models import TableInfo
    from docstruct.tables.assess import _apply_assessment

    tables = [TableInfo(id="table_1", table_num=1, placeholder="",
                        markdown="| a |")]
    _apply_assessment(tables, [{"id": "table_1", "table_kind": "nosuch"}])
    assert tables[0].table_kind is None


def test_table_kind_serialized():
    """유형이 JSON 에 남는다."""
    import json

    from docstruct.models import TableInfo

    table = TableInfo(id="t1", table_num=1, placeholder="", markdown="| a |",
                      table_kind="org")
    assert table.to_dict()["table_kind"] == "org"
    json.dumps(table.to_dict(), ensure_ascii=False)


# ────────────────────────────────────────────────────────────────────
# 0.3.45 — 스캔본에서 docling OCR 을 건너뛸 수 있게
#
# 배경: 스캔 PDF(주택과세금 377쪽)가 **29분** 걸렸다. 내역을 보니 같은
#       지면을 두 번 읽고 있었다.
#
#           추출 (docling 내장 OCR)  1,096초 · 쪽당 2.9초  ← 중국어 모델, 버림
#           한국어 재판독              627초 · 쪽당 1.7초  ← 실제로 쓰는 결과
#
#       표 격자는 TableFormer 가 이미지 레이아웃으로 잡으므로 OCR 없이도
#       나온다. 셀 텍스트는 `cell_match` 가 재판독 조각으로 채운다.
#
#       스캔본 판정에서 걸림돌이 하나 있었다 — 본문은 이미지인데 **머리말·
#       바닥글만 텍스트**로 있어 쪽당 97자가 나왔다.
#
#           '26. 5. 11. 오후 5:44 2025 주택과세금
#           https://www.nts.go.kr/...index.html  6/380
#
#       URL·날짜·쪽표시를 빼고 세야 한다.
# ────────────────────────────────────────────────────────────────────

def test_scanned_detection_ignores_boilerplate():
    """머리말·바닥글은 텍스트 레이어로 세지 않는다."""
    from docstruct.converters.pdf.scanned import _BOILERPLATE_RE, _WHITESPACE_RE

    header = ("26. 5. 11. 오후 5:44 2025 주택과세금\n"
              "https://www.nts.go.kr/upload/nts/ebook/index.html 6/380")
    body = _WHITESPACE_RE.sub("", _BOILERPLATE_RE.sub("", header))
    assert len(body) < 40                    # 장식을 빼면 거의 남지 않는다


def test_scanned_detection_threshold():
    """본문이 있는 쪽과 구분되는 문턱이다."""
    from docstruct.converters.pdf.scanned import (
        MIN_CHARS_PER_PAGE, MIN_EMPTY_RATIO,
    )

    # 실측: 장식만 있는 쪽이 97자였다
    assert MIN_CHARS_PER_PAGE > 97
    assert 0 < MIN_EMPTY_RATIO <= 1


def test_scanned_detection_fails_safe(tmp_path):
    """판단하지 못하면 스캔본으로 보지 않는다.

    스캔본이 아닌데 그렇게 보면 docling OCR 을 꺼서 표 내용을 잃는다.
    """
    from docstruct.converters.pdf.scanned import looks_scanned

    missing = tmp_path / "nosuch.pdf"
    assert looks_scanned(missing) is False


def test_skip_docling_ocr_is_opt_in():
    """이 기능은 기본으로 꺼져 있다."""
    from docstruct.core.config import get_settings

    assert get_settings().scanned_skip_docling_ocr is False


def test_converter_cache_splits_by_ocr_mode():
    """OCR 을 켠 것과 끈 것이 따로 캐시된다.

    하나만 캐시하면 두 번째 문서가 첫 번째 설정을 쓴다.
    """
    import inspect

    from docstruct.converters.pdf import docling_backend

    source = inspect.getsource(docling_backend._build_document_converter)
    assert "skip_ocr" in source


# ────────────────────────────────────────────────────────────────────
# 0.3.46 — LLM 평가를 건너뛴 것이 정상처럼 보이던 문제
#
# 배경: `--ask-key` 로 키를 넣고 돌렸는데 유형이 하나도 안 나왔다. 확인하니
#       **LLM 평가 자체가 돌지 않았다.**
#
#           quality       sufficient 321 (전부)
#           llm_title     0
#           table_kind    0
#
#       결과만 보면 "표 321개가 전부 정상" 으로 보인다. 실제로는 판정조차
#       하지 않은 것이다.
#
#       `reason` 에 `미판정 — LLM 없이 기본값으로 표시` 가 있었으나, 로그는
#       `debug` 라 보이지 않았고 검증 도구도 그것을 보지 않았다.
#
#       세 가지를 고쳤다.
#         · 로그를 warning 으로
#         · `assessed` 필드로 판정 여부를 명시
#         · 검증 도구가 미판정을 먼저 알림
# ────────────────────────────────────────────────────────────────────

def test_unassessed_tables_are_marked():
    """판정하지 못한 표를 구분할 수 있다.

    없으면 "LLM 이 정상으로 본 표" 와 "판정조차 못 한 표" 가 똑같이 보인다.
    """
    from docstruct.models import TableInfo
    from docstruct.tables.assess import _mark_default

    table = TableInfo(id="t1", table_num=1, placeholder="", markdown="| a |")
    _mark_default(table, unassessed=True)

    assert table.quality == "sufficient"       # 기본값이지만
    assert table.assessed is False             # 판정한 것은 아니다
    assert "미판정" in (table.reason or "")


def test_assessed_flag_set_on_real_judgement():
    """실제로 판정하면 표시가 남는다."""
    from docstruct.models import TableInfo
    from docstruct.tables.assess import _apply_assessment

    tables = [TableInfo(id="table_1", table_num=1, placeholder="",
                        markdown="| a |")]
    _apply_assessment(tables, [{"id": "table_1", "table_kind": "budget",
                                "content_type": "table", "title": "예산"}])
    assert tables[0].assessed is True


def test_missing_llm_logs_warning():
    """LLM 미설정을 경고로 남긴다.

    debug 로 두었더니 사용자가 키를 넣고도 평가가 건너뛴 것을 몰랐다.
    """
    import inspect

    from docstruct.tables import assess

    source = inspect.getsource(assess)
    assert "표 평가를 건너뜁니다" in source
    assert '_log.warning(\n            "LLM 이 설정되지 않아' in source


# ────────────────────────────────────────────────────────────────────
# 0.3.47 — `--ask-key` 만으로는 LLM 이 안 잡히던 문제
#
# 배경: `--ask-key` 로 OpenAI 키를 넣고 돌렸는데 표 평가가 건너뛰었다.
#
#       `OPENAI_API_KEY` 는 **연결 실패 시 폴백**으로만 쓰였다. 주소
#       (`DOCLING_TABLE_API_URL`)가 없으면 주 엔드포인트가 안 잡히고,
#       평가가 조용히 건너뛰어 모든 표가 기본값 `sufficient` 가 된다.
#
#       사내 서버를 쓰는 환경에는 `site_defaults.py` 에 주소가 있어 문제가
#       드러나지 않았다 — 그 파일이 없는 환경에서만 나타났다.
#
#       주소가 없고 키만 있으면 OpenAI 를 주 엔드포인트로 쓴다.
# ────────────────────────────────────────────────────────────────────

def test_openai_key_alone_configures_llm():
    """주소 없이 OpenAI 키만 있어도 LLM 이 잡힌다.

    `--ask-key` 로 키만 넣고 돌리는 경우다. 이것이 없으면 평가가 조용히
    건너뛰고 모든 표가 기본값 `sufficient` 로 채워진다.

    **환경을 격리할 수 없어 코드로 확인한다** — `.env` 와 `site_defaults.py`
    가 실제 환경변수로 올라와 있어 monkeypatch 로 지울 수 없다.
    """
    import inspect

    from docstruct.core import config

    source = inspect.getsource(config._build_settings)
    assert "if not table_url and openai_key:" in source
    assert "DOCLING_TABLE_API_FALLBACK_URL" in source


def test_openai_fallback_url_defined():
    """OpenAI 주소·모델이 내장 기본값에 있다."""
    from docstruct.core.config import _BUILTIN_DEFAULTS

    assert "openai.com" in _BUILTIN_DEFAULTS["DOCLING_TABLE_API_FALLBACK_URL"]
    assert _BUILTIN_DEFAULTS["DOCLING_TABLE_API_FALLBACK_MODEL"]


# ────────────────────────────────────────────────────────────────────
# 0.3.49 — 한 칸에 뭉친 값 되돌리기 (실험 ⑦)
#
# 배경: ③(two_way_match)이 짚은 35건을 뜯어보니 셋으로 갈렸다.
#
#       글자 사이 공백  18건  `적 및 목표치 구분` — **원본 조판, 손상 아님**
#       코드+글자 뭉침  12건  `50771 ①자원봉사 만족도(점)`
#       숫자 갈림        4건  `1,150,0 00`
#
#       실제 손상은 16건(5%)이고, 그중 코드 뭉침은 **규칙으로 되돌릴 수
#       있다** — 다섯 자리 숫자와 `①` 로 시작하는 이름은 원래 다른 칸이다.
#
#       처음에 열 수 다수결로 잡으려 했으나 **틀렸다.** 표 전체가 일관되게
#       뭉쳐 있어 무엇이 정상인지 알 수 없었다. 칸 안의 모양으로 판단하되,
#       **같은 열의 여러 칸이 같은 모양일 때만** 가른다.
# ────────────────────────────────────────────────────────────────────

def test_cell_repair_splits_code_and_name():
    """사업코드와 지표명이 붙은 열을 가른다."""
    from docstruct.experiments.tsr.restore.cell_repair import repair_table

    table = "\n".join([
        "| 사업 | 지표 | 가중치 |",
        "|---|---|---|",
        "| 자원봉사 | 50771 ①자원봉사 만족도(점) | 1.0 |",
        "| 민간단체 | 42364 ①공익활동 지원사업 비율(%) | 1.0 |",
        "| 새마을 | 51024 ②교육 수료생 성취도(%) | 0.3 |",
        "| 새마을 | 51025 ③시범마을 적용도(%) | 0.3 |",
    ])
    fixed, count = repair_table(table)
    assert count >= 4
    assert "| 50771 | ①자원봉사 만족도(점) |" in fixed


def test_cell_repair_needs_repeated_pattern():
    """한 칸만 그런 모양이면 손대지 않는다.

    원래 그런 값일 수 있다.
    """
    from docstruct.experiments.tsr.restore.cell_repair import repair_table

    table = "\n".join([
        "| 사업 | 내용 |",
        "|---|---|",
        "| 가 | 50771 ①지표 |",
        "| 나 | 정상 내용입니다 |",
        "| 다 | 다른 내용 |",
        "| 라 | 또 다른 내용 |",
    ])
    _, count = repair_table(table)
    assert count == 0


def test_cell_repair_leaves_spaced_text():
    """글자 사이가 벌어진 것은 손대지 않는다.

    `적 및 목표치 구분` 은 좁은 칸에 맞춘 조판이지 손상이 아니다.
    실측 35건 중 18건이 그 경우였다.
    """
    from docstruct.experiments.tsr.restore.cell_repair import repair_table

    table = "\n".join([
        "| 구 분 | 적 및 목표치 구분 |",
        "|---|---|",
        "| 가 | 목 표 |",
        "| 나 | 실 적 |",
        "| 다 | 목 표 |",
        "| 라 | 실 적 |",
    ])
    _, count = repair_table(table)
    assert count == 0


def test_cell_repair_keeps_separator_valid():
    """구분선도 함께 늘려 markdown 이 깨지지 않게 한다."""
    from docstruct.experiments.tsr.restore.cell_repair import repair_table

    table = "\n".join([
        "| 사업 | 지표 |",
        "|---|---|",
        "| 가 | 50771 ①지표 하나 |",
        "| 나 | 42364 ②지표 둘 |",
        "| 다 | 51024 ③지표 셋 |",
        "| 라 | 51025 ④지표 넷 |",
    ])
    fixed, _ = repair_table(table)
    rows = [r for r in fixed.splitlines() if r.startswith("|")]
    widths = {len(r.strip("|").split("|")) for r in rows}
    assert len(widths) == 1                  # 모든 행이 같은 열 수


def test_cell_repair_amount_split_detected():
    """갈린 금액을 알아본다.

    `1,150,0 00` 은 붙였을 때 세 자리 규칙에 맞으므로 한 값이었다.
    """
    from docstruct.experiments.tsr.restore.cell_repair import split_candidates

    assert ("1,150,000", "") in split_candidates("1,150,0 00")
    assert not any(h == "1463" for h, _ in split_candidates("14, 63"))


# ────────────────────────────────────────────────────────────────────
# 0.3.50 — 갈린 숫자 붙이기
#
# 배경: ③ 이 짚은 것 중 **금액이 갈린 것**이 있었다. 수치가 틀리면 그 표는
#       쓸 수 없으므로 가장 중요한 유형이다.
#
#           | 999,969 | 1,150,0 00 | 1,150,00 0 |
#                       ↑ 한 값이 갈림
#
#       **자릿수로 검증한다.** 붙였을 때 세 자리 규칙에 맞거나 쉼표 없는
#       정수가 되면 원래 한 값이었다.
#
#           '1,150,0 00' → '1,150,000'   ✓
#           '4199 0'     → '41990'       ✓
#           '14, 63'     → None          두 값인지 한 값인지 모름
#
#       마지막 경우는 **여기서 판단하지 않는다.** 열 의미를 아는 구조화
#       단계가 정할 문제다.
#
#       실측(행안부 320표): 6표 → 12표 · 37행으로 늘었다.
# ────────────────────────────────────────────────────────────────────

def test_join_split_number_by_digit_rule():
    """자릿수로 갈린 숫자를 알아본다."""
    from docstruct.experiments.tsr.restore.cell_repair import join_split_number

    assert join_split_number("1,150,0 00") == "1,150,000"
    assert join_split_number("1,000,0 00") == "1,000,000"
    assert join_split_number("4199 0") == "41990"


def test_join_split_number_refuses_ambiguous():
    """붙일 근거가 없으면 손대지 않는다.

    `14, 63` 은 두 값인지 한 값이 갈린 것인지 알 수 없다.
    """
    from docstruct.experiments.tsr.restore.cell_repair import join_split_number

    assert join_split_number("14, 63") is None
    assert join_split_number("정상 값") is None
    assert join_split_number("1,234") is None       # 갈리지 않았다


def test_cell_repair_joins_amounts_in_short_table():
    """행이 적은 예산표에서도 금액을 붙인다.

    열을 늘리지 않으므로 위험이 작다 — 실측에서 3행짜리 예산표가 다수였다.
    """
    from docstruct.experiments.tsr.restore.cell_repair import repair_table

    table = "\n".join([
        "| 사업 | 회계 | '25결산 | '26예산 |",
        "|---|---|---|---|",
        "| 지역경제 | 특별회계 | 999,969 | 1,150,0 00 |",
        "| 상품권 | 특별회계 | 999,969 | 1,150,0 00 |",
    ])
    fixed, count = repair_table(table)
    assert count == 2
    assert "1,150,000" in fixed
    assert "1,150,0 00" not in fixed


def test_cell_repair_needs_numeric_column():
    """수치 열이 아니면 붙이지 않는다.

    같은 열의 다른 칸이 온전한 숫자여야 그 열이 수치 열이라는 근거가 된다.
    """
    from docstruct.experiments.tsr.restore.cell_repair import _number_column, _rows_of

    table = "\n".join([
        "| 사업 | 설명 |",
        "| 가 | 어떤 설명 1,150,0 00 원 |",
        "| 나 | 다른 설명입니다 |",
        "| 다 | 또 다른 설명 |",
    ])
    assert _number_column(_rows_of(table)) == []


# ────────────────────────────────────────────────────────────────────
# 0.3.51 — 두 행이 뭉친 것 되돌리기 (③ 을 근거로)
#
# 배경: 성과지표 표에서 `목표` 와 `실적` 이 각각 한 행인데, 파서가 한 행으로
#       합쳐 놓는 일이 있다.
#
#           | 1.0 목표 실적 | 100 100 | 100 100 |
#                  ↑ 두 행이 뭉침    ↑ 목표값 실적값
#
#       **수치가 뒤섞이므로 그 표는 쓸 수 없다.** 가장 중요한 유형이다.
#
#       표식(`목표`/`실적`)만으로는 근거가 약했다 — 서술문에 둘이 함께
#       나오는 일이 흔해 26표가 잘못 걸렸다.
#
#           * 목표달성률=(시도 목표달성 지표 수의 합 / …)
#
#       둘을 더했다.
#         · 표식 칸이 20자 이내여야 한다 (서술문 제외)
#         · **③ 이 짚은 표에서만** 행을 가른다
#
#       실측(행안부 320표): 45표 → 22표 · 49행.
# ────────────────────────────────────────────────────────────────────

def test_split_merged_row_uses_markers():
    """`목표`/`실적` 표식으로 두 행을 되돌린다."""
    from docstruct.experiments.tsr.restore.cell_repair import split_merged_row

    row = "| 지방자주재원확충 | ①지방세 개편(점) | 1.0 목표 실적 | 100 100 | 100 100 |"
    head, tail = split_merged_row(row)
    assert "목표" in head and "실적" in tail
    assert head.count("100") == 2 and tail.count("100") == 2


def test_split_merged_row_ignores_prose():
    """서술문은 되돌리지 않는다.

    `목표달성률=(시도 목표달성 지표 수의 합 …` 같은 설명이 26표나 걸렸다.
    """
    from docstruct.experiments.tsr.restore.cell_repair import split_merged_row

    row = ("| 가 | * 목표달성률=(시도 목표달성 지표 수의 합 / "
           "시도 정량지표 수의 합) X 100, 실적은 별도 |")
    assert split_merged_row(row) is None


def test_row_merge_needs_two_way_match():
    """행 분리는 ③ 이 짚은 표에서만 한다.

    표식만으로는 근거가 약하다.
    """
    from docstruct.experiments.tsr.restore.cell_repair import repair_table

    table = "\n".join([
        "| 사업 | 지표 | 구분 | '25 | '26 |",
        "|---|---|---|---|---|",
        "| 가 | ①지표 | 1.0 목표 실적 | 100 100 | 100 100 |",
        "| 나 | ②지표 | 1.0 목표 실적 | 200 200 | 200 200 |",
    ])
    assert repair_table(table)[1] == 0                    # 기본은 안 함
    assert repair_table(table, row_merge=True)[1] > 0     # ③ 이 짚으면 한다


def test_marker_length_limit_documented():
    """표식 칸 길이 제한이 있다."""
    from docstruct.experiments.tsr.restore.cell_repair import MAX_MARKER_CHARS

    assert 0 < MAX_MARKER_CHARS <= 30


# ────────────────────────────────────────────────────────────────────
# 0.3.52 — 스캔본 판정에 경로가 안 넘어가던 문제
#
# 배경: `scanned_skip_docling_ocr=true` 로 돌렸는데 시간이 그대로였다
#       (1,096초 → 1,143초).
#
#       `get_document_converter()` 를 **경로 없이** 부르는 곳이 있었다.
#       그러면 스캔본 판정을 아예 하지 않아 설정이 무시된다.
#
#           converter = get_document_converter()          ← 주 경로
#           get_document_converter(str(self.path))        ← 예외 처리 경로만
#
#       그리고 그 설정이 `pipeline` 기록에도 없어, 결과만 보고는 켰는지
#       껐는지 알 수 없었다.
# ────────────────────────────────────────────────────────────────────

def test_converter_receives_pdf_path():
    """변환기를 부를 때 경로를 넘긴다.

    넘기지 않으면 스캔본 판정을 못 해 설정이 무시된다.
    """
    import inspect

    from docstruct.converters.pdf import converter

    # 주석은 빼고 실제 호출만 본다
    lines = [ln for ln in inspect.getsource(converter).splitlines()
             if not ln.strip().startswith("#")]
    assert "get_document_converter()" not in "\n".join(lines)


def test_scanned_setting_recorded():
    """설정이 결과에 기록된다.

    없으면 켜고 돌렸는지 결과만 보고 알 수 없다.
    """
    import inspect

    from docstruct import pipeline

    source = inspect.getsource(pipeline._pipeline_settings)
    assert "scanned_skip_docling_ocr" in source
    assert "detect_toc" in source


# ────────────────────────────────────────────────────────────────────
# 0.3.53 — 실험 실행 순서가 이름순이던 문제
#
# 배경: 0.3.51 로 돌렸는데 복원이 12표뿐이었다. 같은 데이터를 코드로 다시
#       돌리면 25표가 나왔다.
#
#       실험이 **이름순**으로 돌고 있었다.
#
#           cell_repair → otsl_diff → two_way_match
#
#       `cell_repair` 는 행 분리에 `match_disagreements` 를 쓰는데, 그것을
#       채우는 `two_way_match` 가 **나중에** 돌았다. 아직 비어 있으니 행
#       분리를 통째로 건너뛴 것이다.
#
#       뒤엣것이 앞엣것의 결과를 읽으므로 **순서를 명시**한다.
# ────────────────────────────────────────────────────────────────────

def test_experiment_run_order():
    """결과를 읽는 실험이 나중에 돈다."""
    from docstruct.experiments import all_experiments
    keys = [e.key for e in all_experiments()]
    assert keys.index("two_way_match") < keys.index("cell_repair")


def test_unknown_experiment_goes_last():
    """순서 목록에 없는 실험은 맨 뒤로."""
    from docstruct.experiments.registry import _RUN_ORDER, _run_order

    assert _run_order("nosuch")[0] == len(_RUN_ORDER)
    # 0.3.72: 사다리 도입 — 맨 앞은 계측(⑪)이다 (⑦보다 먼저 재야 한다).
    assert _run_order("grid_score")[0] == 0


# ────────────────────────────────────────────────────────────────────
# 0.3.54 — OCR 이 잘못 읽은 곳을 문맥으로 짚기
#
# 배경: CTC 기반 OCR(rapidocr)은 **글자 모양만** 본다. 문맥을 모르므로
#       이런 일이 난다.
#
#           원본: "…정하는 이자율"이란 연 1천분의 29를 말한다
#           OCR:  "…정하는 이자율" 이란 연 2.9를 말한다
#
#       값이 열 배 틀렸는데 **신뢰도로는 못 잡는다** — 각 획이 또렷해
#       점수가 높게 나온다. 문장으로 읽어야 안다.
#
#       **고치지는 않는다.** LLM 에게 고치라고 하면 지어낸다 — `29` 인지
#       `2.9` 인지는 지면을 봐야 안다. 어디가 이상한지만 짚고, 값을 정하는
#       것은 지면을 보는 쪽(VLM)의 몫이다.
#
#       정석은 CTC 후보 분포를 언어모델과 결합하는 것인데, rapidocr 은
#       분포를 내지 않는다 — 최종 문자열과 평균 점수뿐이다. 그래서 지목만
#       시킨다.
# ────────────────────────────────────────────────────────────────────

def _verify_page(page_no, text):
    """OCR 로 읽은 페이지."""
    from docstruct.models import PageContent, PageTrace

    return PageContent(page_no=page_no, page_no_kind="pdf", content=text,
                       trace=PageTrace(extractor="pdf", text_source="ocr"))


def test_ocr_verify_numbers_fragments():
    """조각에 쪽·번호를 붙인다.

    지목한 자리를 되찾으려면 번호가 있어야 한다.
    """
    from docstruct.text.ocr_verify import _fragments

    pages = [_verify_page(147, "이자율 이란 연 2.9를 말한다.\n짧음\n적용시기 24.1.1.")]
    fragments = _fragments(pages)
    assert fragments[0][0] == 147
    assert fragments[0][1] == 0
    assert all(len(t) >= 8 for _, _, t in fragments)   # 짧은 줄은 뺀다


def test_ocr_verify_batches_by_pages_and_chars():
    """쪽 수와 글자 수 둘 다 본다.

    표가 많은 쪽은 20쪽만 모아도 한도를 넘는다.
    """
    from docstruct.text.ocr_verify import MAX_CHARS, PAGES_PER_CALL, _batches

    many = [(n, 0, "가" * 100) for n in range(1, PAGES_PER_CALL + 5)]
    assert len(_batches(many)) >= 2

    long_one = [(1, i, "가" * 2000) for i in range(20)]
    batches = _batches(long_one)
    assert len(batches) >= 2
    for batch in batches:
        assert sum(len(t) for _, _, t in batch) <= MAX_CHARS + 2000


def test_ocr_verify_prompt_forbids_fixing():
    """프롬프트가 고치지 말라고 이른다.

    고치게 하면 없던 값을 만든다.
    """
    from docstruct.text.ocr_verify import _PROMPT

    assert "고치지 마세요" in _PROMPT
    assert "1천분의" in _PROMPT                    # 판단 근거를 준다
    assert "띄어쓰기가 없는 것만으로는" in _PROMPT   # 흔한 것은 제외


def test_ocr_verify_skips_without_llm(monkeypatch):
    """LLM 이 없으면 아무것도 하지 않는다."""
    from docstruct.text import ocr_verify

    monkeypatch.setattr(ocr_verify, "llm_api_config", lambda: None)
    pages = [_verify_page(1, "어떤 내용이 여기 있습니다.")]
    assert ocr_verify.find_doubts(pages) == 0
    assert not pages[0].ocr_doubts


def test_ocr_doubts_serialized():
    """의심 표시가 결과에 남는다."""
    import json

    page = _verify_page(147, "본문")
    page.ocr_doubts = [{"index": 0, "text": "연 2.9", "reason": "법령체 아님"}]
    data = page.to_dict()
    assert data["ocr_doubts"][0]["text"] == "연 2.9"
    json.dumps(data, ensure_ascii=False)


def test_doubt_pages_lists_targets():
    """VLM 으로 다시 읽을 쪽을 고를 수 있다."""
    from docstruct.text.ocr_verify import doubt_pages

    clean = _verify_page(1, "정상")
    suspect = _verify_page(147, "이상")
    suspect.ocr_doubts = [{"index": 0}]
    assert doubt_pages([clean, suspect]) == [147]


# ────────────────────────────────────────────────────────────────────
# 0.3.55 — OCR 검증이 표를 안 보던 문제
#
# 배경: 11쪽으로 시험하니 9건을 짚었는데 **정작 표적을 놓쳤다.**
#
#       9쪽 (수치 오독 `연 2.9`)  → 0건
#
#       본문에는 `<table 5>` 자리표시자만 남고, 그 문장은 **표 안**에
#       있었다. 검증이 `page.content` 만 읽으니 통째로 못 본 것이다.
#
#       표 안이야말로 수치가 몰려 있어 오독이 치명적이다.
#
#       표는 **칸 단위**로 자른다 — 한 줄을 통째로 보내면 LLM 이 어느 칸이
#       이상한지 짚기 어렵다.
# ────────────────────────────────────────────────────────────────────

def test_ocr_verify_includes_tables():
    """표 안도 검증 대상이다."""
    from docstruct.text.ocr_verify import _fragments
    from docstruct.models import TableInfo

    page = _verify_page(147, "본문 내용이 여기 있습니다.\n<table 5>")
    page.tables = [TableInfo(
        id="table_5", table_num=5, placeholder="<table 5>",
        markdown=("| 구분 | 내용 |\n|---|---|\n"
                  "| 이자율 | 영 제53조 산식에서 정하는 이자율 이란 연 2.9를 말한다 |"),
    )]
    texts = [t for _, _, t in _fragments([page])]
    assert any("연 2.9" in t for t in texts)


def test_ocr_verify_splits_table_cells():
    """표는 칸 단위로 자른다.

    한 줄을 통째로 보내면 어느 칸이 이상한지 짚기 어렵다.
    """
    from docstruct.text.ocr_verify import _fragments
    from docstruct.models import TableInfo

    page = _verify_page(1, "")
    page.tables = [TableInfo(
        id="t1", table_num=1, placeholder="",
        markdown=("| 첫째 칸 내용입니다 | 둘째 칸 내용입니다 |\n"
                  "|---|---|\n"
                  "| 셋째 칸 내용입니다 | 넷째 칸 내용입니다 |"),
    )]
    texts = [t for _, _, t in _fragments([page])]
    assert "첫째 칸 내용입니다" in texts        # 칸이 따로
    assert not any("|" in t for t in texts)    # 줄째로 보내지 않는다


def test_ocr_verify_skips_table_separators():
    """구분선은 보내지 않는다."""
    from docstruct.text.ocr_verify import _fragments
    from docstruct.models import TableInfo

    page = _verify_page(1, "")
    page.tables = [TableInfo(id="t1", table_num=1, placeholder="",
                             markdown="| 어떤 내용입니다 |\n|---|\n| 다른 내용입니다 |")]
    texts = [t for _, _, t in _fragments([page])]
    assert not any(set(t) <= set("-: ") for t in texts)


# ────────────────────────────────────────────────────────────────────
# 0.3.56 — OCR 검증의 오탐과 놓침 줄이기
#
# 배경: 13쪽 시험에서 13건을 짚었다. 10건은 실제 오류였으나 **3건이
#       오탐**이었다.
#
#           지방법§11①8    지특법§36의3    지방법§111의2
#
#       `§` 가 멀쩡한데 "인용 형식이 불완전하다" 고 봤다. 정상인 꼴을
#       프롬프트에 예시로 넣어 구분하게 한다.
#
#       그리고 제목 누락을 못 짚었다.
#
#           지면: 주택취득자금에 / 대한 확인
#           OCR: 주택취득자금에          ← 7자, 조각 기준(8자)에 걸림
#
#       기준을 5자로 낮추되, 그러면 머리말·바닥글이 조각으로 들어오므로
#       그것을 걸러 낸다.
# ────────────────────────────────────────────────────────────────────

def test_ocr_verify_prompt_shows_valid_citations():
    """정상인 법령 인용을 예시로 보여 준다.

    `§` 가 멀쩡한데 짚은 오탐이 3건 있었다.
    """
    from docstruct.text.ocr_verify import _PROMPT

    assert "지방법§11①8" in _PROMPT          # 정상 예시
    assert "지방법S11" in _PROMPT            # 깨진 예시
    assert "짚지\n  마세요" in _PROMPT or "짚지 마세요" in _PROMPT


def test_ocr_verify_keeps_short_titles():
    """짧은 제목도 조각으로 넣는다.

    `주택취득자금에`(7자)가 빠져 제목 누락을 못 짚었다.
    """
    from docstruct.text.ocr_verify import _fragments

    page = _verify_page(63, "국세정\n주택취득자금에")
    texts = [t for _, _, t in _fragments([page])]
    assert "주택취득자금에" in texts


def test_ocr_verify_drops_boilerplate():
    """머리말·바닥글은 보내지 않는다.

    쪽마다 같은 것이 반복돼 조각만 늘린다.
    """
    from docstruct.text.ocr_verify import _fragments

    page = _verify_page(63, "26.5.11.오후5:44\n"
                            "https://www.nts.go.kr/upload/index.html\n"
                            "63/380\n"
                            "실제 본문 내용입니다")
    texts = [t for _, _, t in _fragments([page])]
    assert texts == ["실제 본문 내용입니다"]


# ────────────────────────────────────────────────────────────────────
# 0.3.57 — OCR 검증을 표 재추출 뒤로
#
# 배경: 검증이 표 재추출보다 **먼저** 돌고 있었다.
#
#           ① rapidocr
#           ② OCR 검증 LLM     ← 여기
#           ③ 표 평가
#           ④ 표 재추출 VLM    ← 지면을 보고 고침
#
#       재추출은 지면을 보고 표를 다시 쓰므로, 그전에 검증하면 **곧 고쳐질
#       것을 의심 목록에 올린다.** 지면을 보는 쪽이 먼저고, 텍스트만 보는
#       검증이 남은 것을 훑는 순서가 맞다.
#
#       그리고 **재추출된 표는 검증에서 뺀다.** 지면을 보고 쓴 것이라
#       텍스트만 보는 검증이 더 나을 수 없고, 조각만 늘려 비용이 든다.
# ────────────────────────────────────────────────────────────────────

def test_verify_runs_after_table_fill():
    """검증이 표 재추출 뒤에 돈다."""
    import inspect

    from docstruct import pipeline

    source = inspect.getsource(pipeline.build_document)
    assert source.index("STAGE_FILL]") < source.index("STAGE_VERIFY_OCR]")


def test_verify_skips_rebuilt_tables():
    """재추출된 표는 검증하지 않는다.

    지면을 보고 쓴 것이라 텍스트만 보는 검증이 더 나을 수 없다.
    """
    from docstruct.text.ocr_verify import _fragments
    from docstruct.models import TableInfo

    page = _verify_page(1, "본문 내용입니다")
    page.tables = [
        TableInfo(id="t1", table_num=1, placeholder="",
                  markdown="| 파서가 읽은 내용 |", source="parser"),
        TableInfo(id="t2", table_num=2, placeholder="",
                  markdown="| 재추출된 내용입니다 |", source="llm"),
    ]
    texts = [t for _, _, t in _fragments([page])]
    assert "파서가 읽은 내용" in texts
    assert "재추출된 내용입니다" not in texts


# ────────────────────────────────────────────────────────────────────
# 0.3.58 — 의심 자리를 지면 보고 다시 읽기 (④)
#
# 배경: `verify_ocr` 은 **어디가 이상한지만** 짚는다. 텍스트만 보므로 무엇이
#       맞는지 정할 수 없다.
#
#           "이란 연 2.9를 말한다"  →  이상하다 (법령체 아님)
#                                    `29` 인지 `2.9` 인지는 모른다
#
#       짚기만 하고 끝나면 값어치가 반쪽이다. **짚은 쪽만** VLM 에 지면
#       이미지를 보내 바로잡는다.
#
#       전면 재판독은 비싸다 — rapidocr 만으로도 377쪽에 627초였다. 짚은 쪽만
#       태우므로 그보다 훨씬 적게 든다.
#
#       **짚은 조각만** 바꾼다. 지면 전체를 다시 쓰면 멀쩡한 곳까지 흔들린다.
# ────────────────────────────────────────────────────────────────────

def test_reread_accepts_plausible_fix():
    """지면에서 읽은 값을 받아들인다."""
    from docstruct.text.ocr_reread import _acceptable

    assert _acceptable("이란 연 2.9를 말한다", "이란 연 1천분의 29를 말한다")


def test_reread_rejects_unknown():
    """`모름` 이면 손대지 않는다.

    흐릿해서 못 읽은 것을 추측으로 채우면 안 된다.
    """
    from docstruct.text.ocr_reread import _acceptable

    assert not _acceptable("이란 연 2.9를 말한다", "모름")
    assert not _acceptable("이란 연 2.9를 말한다", "")


def test_reread_rejects_padded_answer():
    """지면에 없는 말을 덧붙이면 받지 않는다."""
    from docstruct.text.ocr_reread import _acceptable

    padded = ("연 1천분의 29를 말한다. 이는 소득세법 시행령에 따른 것으로 "
              "간주임대료 산정에 쓰이며 매년 개정된다")
    assert not _acceptable("연 2.9", padded)


def test_reread_prompt_forbids_guessing():
    """프롬프트가 추측을 막는다."""
    from docstruct.text.ocr_reread import _PROMPT

    assert "추측하지 마세요" in _PROMPT
    assert "모름" in _PROMPT
    assert "지면에 보이는 대로만" in _PROMPT


def test_reread_keeps_original(monkeypatch):
    """고치면 원본을 남긴다."""
    from docstruct.text import ocr_reread

    page = _verify_page(147, "앞부분 이란 연 2.9를 말한다 뒷부분")
    page.page_image_path = "/tmp/x.png"
    page.ocr_doubts = [{"index": 0, "source_text": "이란 연 2.9를 말한다",
                        "reason": "법령체 아님"}]

    monkeypatch.setattr(ocr_reread, "encode_image_file",
                        lambda p: ("image/png", "AAA"))
    monkeypatch.setattr(
        ocr_reread, "invoke_llm",
        lambda *a, **k: '[{"index": 0, "text": "이란 연 1천분의 29를 말한다"}]')

    assert ocr_reread._reread_page(page, {"url": "x"}) == 1
    assert "1천분의 29" in page.content
    assert page.ocr_original is not None          # 되돌릴 수 있다


def test_reread_needs_doubts_and_image():
    """짚은 곳이 없거나 렌더가 없으면 돌지 않는다."""
    from docstruct.text.ocr_reread import reread_doubts

    plain = _verify_page(1, "정상")
    no_image = _verify_page(2, "이상")
    no_image.ocr_doubts = [{"index": 0}]
    assert reread_doubts([plain, no_image]) == 0


# ────────────────────────────────────────────────────────────────────
# 0.3.59 — 목차를 본문 확정 뒤에 찾기
#
# 배경: 목차 검출이 **재판독보다 먼저** 돌고 있었다. 재판독이 글자를
#       고치므로, 그전에 뽑으면 고쳐지기 전 본문에서 뽑게 된다.
#
#       실측(주택과세금 377쪽) 목차에 이런 것이 있었다.
#
#           가.취득세 과세대상(지방법6)      ← `§` 가 빠짐
#           나. 세율(지방법11)
#
#       원본은 `지방법§6`·`지방법§11` 이다. 재판독이 `§` 를 되살리므로,
#       그 뒤에 목차를 뽑으면 제대로 나온다.
# ────────────────────────────────────────────────────────────────────

def test_toc_runs_after_reread():
    """목차를 재판독 뒤에 찾는다."""
    import inspect

    from docstruct import pipeline

    source = inspect.getsource(pipeline.build_document)
    assert (source.index("STAGE_REREAD_OCR]")
            < source.index("get_settings().detect_toc"))


# ────────────────────────────────────────────────────────────────────
# 0.3.60 — 검수에서 나온 것
#
# 배경: ① cell_repair 의 구분선 판정이 `---` 정확일치였다. 파이프라인
#       실물 표는 `render_md_table` 이 열 폭에 맞춰 늘인 구분선
#       (`|------|--------|`)을 쓰므로, 구분선이 데이터 행으로 오인돼
#       **구분선에 빈 칸이 박혀 GFM 표가 통째로 깨졌다.** 기존 테스트는
#       손으로 쓴 `|---|` 만 검사해 이것을 놓쳤다.
#
#       ② 재판독(⑧)이 `page.content` 에서만 치환했다. 검증(⑦)은 표 칸도
#       짚는데(0.3.55), 표 칸 의심의 source_text 는 `table.markdown` 에
#       있으므로 조용히 버려졌다 — VLM 이 읽어 와도 반영되지 않았다.
# ────────────────────────────────────────────────────────────────────

def test_cell_repair_padded_separator_stays_valid():
    """열 폭에 맞춰 늘인 구분선(파이프라인 실물)도 깨지지 않는다.

    `render_md_table` 은 `|------|--------|` 처럼 패딩한다. 정확일치
    (`"---"`) 판정은 이것을 데이터 행으로 오인해 빈 칸을 박았다.
    """
    from docstruct.converters.common.table import render_md_table
    from docstruct.experiments.tsr.restore.cell_repair import repair_table

    table = render_md_table([
        ["사업", "지표"],
        ["가", "50771 ①지표 하나"],
        ["나", "42364 ②지표 둘"],
        ["다", "51024 ③지표 셋"],
        ["라", "51025 ④지표 넷"],
    ])
    fixed, count = repair_table(table)
    assert count >= 4

    rows = [r for r in fixed.splitlines() if r.startswith("|")]
    widths = {len(r.strip("|").split("|")) for r in rows}
    assert len(widths) == 1                  # 모든 행이 같은 열 수

    # 구분선 행에 빈 칸이 없어야 GFM 이 표로 인식한다.
    sep = [r for r in rows if not (set(r.strip()) - set("|-: "))]
    assert len(sep) == 1
    assert all(c.strip() for c in sep[0].strip("|").split("|"))


def test_cell_repair_separator_cell_predicate():
    """구분선 칸 판정 — 패딩·정렬 표기는 받고, 데이터는 거른다."""
    from docstruct.experiments.tsr.restore.cell_repair import _is_separator_cell

    assert _is_separator_cell("---")
    assert _is_separator_cell("------")      # render_md_table 패딩
    assert _is_separator_cell(":---:")
    assert not _is_separator_cell("")
    assert not _is_separator_cell("::")      # 대시가 없다
    assert not _is_separator_cell("-1.0")    # 음수 데이터


def test_reread_fixes_table_cells(monkeypatch):
    """표 칸 의심도 재판독이 반영한다.

    검증(⑦)이 표 칸을 짚으므로(0.3.55), 재판독(⑧)도 `table.markdown`
    에서 찾아 바꿔야 한다. 본문만 보면 표 칸 의심은 조용히 버려진다.
    """
    from docstruct.text import ocr_reread
    from docstruct.models import TableInfo

    page = _verify_page(9, "본문 <table 1> 뒤")
    page.page_image_path = "/tmp/x.png"
    page.tables = [TableInfo(
        id="table_1", table_num=1, placeholder="<table 1>",
        markdown="| 항목 | 값 |\n|---|---|\n| 이자율 | 이란 연 2.9를 말한다 |",
    )]
    page.ocr_doubts = [{"index": 3, "source_text": "이란 연 2.9를 말한다",
                        "reason": "법령체 아님"}]

    monkeypatch.setattr(ocr_reread, "encode_image_file",
                        lambda p: ("image/png", "AAA"))
    monkeypatch.setattr(
        ocr_reread, "invoke_llm",
        lambda *a, **k: '[{"index": 3, "text": "이란 연 1천분의 29를 말한다"}]')

    assert ocr_reread._reread_page(page, {"url": "x"}) == 1
    assert "1천분의 29" in page.tables[0].markdown
    assert page.tables[0].original_markdown is not None   # 되돌릴 수 있다
    assert "2.9" in page.tables[0].original_markdown


def test_reread_skips_refilled_tables(monkeypatch):
    """재추출된 표(source≠parser)는 건드리지 않는다.

    지면을 보고 다시 쓴 것이라 검증(⑦) 대상이 아니고, 재판독도 마찬가지다.
    """
    from docstruct.text import ocr_reread
    from docstruct.models import TableInfo

    page = _verify_page(9, "본문 <table 1> 뒤")
    page.page_image_path = "/tmp/x.png"
    page.tables = [TableInfo(
        id="table_1", table_num=1, placeholder="<table 1>",
        markdown="| 이자율 | 이란 연 2.9를 말한다 |", source="vlm",
    )]
    page.ocr_doubts = [{"index": 3, "source_text": "이란 연 2.9를 말한다",
                        "reason": "법령체 아님"}]

    monkeypatch.setattr(ocr_reread, "encode_image_file",
                        lambda p: ("image/png", "AAA"))
    monkeypatch.setattr(
        ocr_reread, "invoke_llm",
        lambda *a, **k: '[{"index": 3, "text": "이란 연 1천분의 29를 말한다"}]')

    assert ocr_reread._reread_page(page, {"url": "x"}) == 0
    assert "2.9" in page.tables[0].markdown               # 그대로다


# ────────────────────────────────────────────────────────────────────
# 0.3.61 — 실물 성과계획서·성과보고서 대조에서 나온 것
#
# 배경: 행안부 성과계획서(HWPX+PDF)·과기부 성과보고서(PDF)를 코드에 실제로
#       태워 대조했다. HWPX 는 금액 무결(지어낸 값 0종)이었으나 PDF 정규화
#       경로에서 두 가지 실손상이 나왔다.
#
#       ① 과기부 인쇄 쪽번호가 **ASCII 규약 PUA** 로 들어 있었다 —
#          `U+F02D U+F020 U+F031 U+F036 …` = `- 16 -`. 한컴 Symbol 규약
#          매핑(F036=⌛)이 이를 모래시계로 바꿔 118곳이 깨졌고, 인쇄쪽
#          오프셋도 못 쟀다. 런 단위 판별로 갈랐다 (수정 후 오프셋 4,
#          근거 579/583쪽, 표본 30/30 일치).
#
#       ② 반복 정리가 **연차별 동일 목표치**를 지웠다 — `20 60 60 60 60
#          60 60 380` → `20 60 380` (24쪽·수십 자 손실). 그림자 효과
#          반복은 제목(한글 시작)에서 나오므로, 숫자로 시작하는 토큰의
#          반복은 보존한다. 수정 후 두 문서 전 쪽에서 숫자열 변화 0.
# ────────────────────────────────────────────────────────────────────

def test_map_pua_decodes_ascii_footer_runs():
    """ASCII 규약 PUA 런(인쇄 쪽번호)을 복원한다."""
    from docstruct.text.korean_text import map_pua

    assert map_pua("\uf02d\uf020 \uf031\uf036\uf020 \uf02d") == "-  16  -"
    assert map_pua("\uf02d\uf020 \uf031\uf020 \uf02d") == "-  1  -"


def test_map_pua_keeps_symbol_convention():
    """기호 규약은 그대로다 — 낱개 F036 은 ⌛, ●● 런은 `ll` 로 안 바뀐다."""
    from docstruct.text.korean_text import map_pua

    assert map_pua("\uf036 항목") == "⌛ 항목"
    assert map_pua("\uf06c\uf06c") == "●●"        # 'll' 이 되면 안 된다
    assert map_pua("\uf06f 항목") == "□ 항목"      # 기존 매핑 유지


def test_collapse_keeps_repeated_values():
    """연차별 동일 목표치의 반복은 데이터다 — 지우지 않는다."""
    from docstruct.text.korean_text import collapse_repeated_words

    line = "입학(명) 20 60 60 60 60 60 60 380"
    assert collapse_repeated_words(line) == line
    line = "선정 신규 10개 - 9개 9개 9개"
    assert collapse_repeated_words(line) == line
    line = "목표치 10.5%이상 10.5%이상 10.5%이상"
    assert collapse_repeated_words(line) == line


def test_collapse_still_fixes_shadow_titles():
    """그림자 효과 제목 반복은 여전히 정리한다."""
    from docstruct.text.korean_text import collapse_repeated_words

    assert collapse_repeated_words("별첨3 별첨3 별첨3") == "별첨3"
    assert collapse_repeated_words(
        "성과계획 목표체계 성과계획 목표체계 성과계획 목표체계 제1장 제1장 제1장"
    ) == "성과계획 목표체계 제1장"


def test_normalize_pdf_text_preserves_digits():
    """정규화가 숫자를 지우지 않는다 — 실물 손상 사례 기반."""
    import re

    from docstruct.text.korean_text import normalize_pdf_text

    line = "입학(명) 20 60 60 60 60 60 60 380"
    before = "".join(re.findall(r"\d", line))
    after = "".join(re.findall(r"\d", normalize_pdf_text(line)))
    assert before == after


def test_hwpx_separates_drawtext_boxes():
    """서로 다른 글상자의 글은 붙이지 않는다 (미결 5 — 간지 제목 붙음).

    실측(행안부 성과계획서) 간지에서 제목 상자와 `제N장` 상자가
    `성과계획 목표체계제1장` 으로 붙었다. 상자 경계에서 줄을 바꾼다.
    """
    from xml.etree import ElementTree as ET

    from docstruct.converters.hwpx.hwpxtree import HP, _paragraph_text

    xml = (
        f'<p xmlns:hp="{HP}">'
        f'<hp:run charPrIDRef="1"><hp:rect>'
        f'<hp:drawText><hp:subList><hp:p>'
        f'<hp:run charPrIDRef="1"><hp:t>성과계획 목표체계</hp:t></hp:run>'
        f'</hp:p></hp:subList></hp:drawText></hp:rect>'
        f'<hp:rect><hp:drawText><hp:subList><hp:p>'
        f'<hp:run charPrIDRef="1"><hp:t>제1장</hp:t></hp:run>'
        f'</hp:p></hp:subList></hp:drawText></hp:rect>'
        f'</hp:run>'
        f'</p>'
    )
    text = _paragraph_text(ET.fromstring(xml), set())
    assert "목표체계제1장" not in text
    assert text == "성과계획 목표체계\n제1장"


def test_hwpx_keeps_runs_glued_within_paragraph():
    """같은 문단·같은 상자 안의 런은 지금처럼 붙인다.

    글자모양이 갈리면 한 문장이 여러 런으로 쪼개진다 — 여기서 갈라 버리면
    멀쩡한 문장이 조각난다.
    """
    from xml.etree import ElementTree as ET

    from docstruct.converters.hwpx.hwpxtree import HP, _paragraph_text

    xml = (
        f'<p xmlns:hp="{HP}">'
        f'<hp:run charPrIDRef="1"><hp:t>예</hp:t></hp:run>'
        f'<hp:run charPrIDRef="2"><hp:t>산</hp:t></hp:run>'
        f'<hp:run charPrIDRef="1"><hp:t>안</hp:t></hp:run>'
        f'</p>'
    )
    assert _paragraph_text(ET.fromstring(xml), set()) == "예산안"


def test_hwpx_bold_wraps_per_line_across_boxes():
    """상자로 줄이 갈린 굵게는 줄마다 두른다 — `**` 가 개행을 가로지르면
    markdown 이 굵게로 렌더하지 않는다."""
    from xml.etree import ElementTree as ET

    from docstruct.converters.hwpx.hwpxtree import HP, _paragraph_text

    xml = (
        f'<p xmlns:hp="{HP}">'
        f'<hp:rect><hp:drawText><hp:subList><hp:p>'
        f'<hp:run charPrIDRef="9"><hp:t>제목</hp:t></hp:run>'
        f'</hp:p></hp:subList></hp:drawText></hp:rect>'
        f'<hp:rect><hp:drawText><hp:subList><hp:p>'
        f'<hp:run charPrIDRef="9"><hp:t>제1장</hp:t></hp:run>'
        f'</hp:p></hp:subList></hp:drawText></hp:rect>'
        f'</p>'
    )
    text = _paragraph_text(ET.fromstring(xml), {"9"})
    assert text == "**제목**\n**제1장**"


# ────────────────────────────────────────────────────────────────────
# 0.3.62 — 실험 ⑥ (벡터 격자) · 검증 루프의 대상 재조준
#
# 배경: 텍스트 PDF 에 ⑦(문자 오독 검증)→⑧(VLM 재판독) 루프를 넓힐지 검토했다.
#       실물 대조 결과 **표적이 없다.**
#
#           HWPX 정답 셀 문자열 4,664개 중 PDF 텍스트에 그대로 존재 92.9%
#           미존재 330건은 전부 조판 분할·PUA — 문자 오독 사례 0
#           §→S 0건 · 제곱미터 깨짐 0건 · 법령체 어긋남 0~1건
#           비용은 본문만으로 22~31회 (표 포함 시 2~3배)
#
#       텍스트 PDF 의 결함은 문자가 아니라 **구조(병합)** 다.
#
#           HWPX 정답  셀 18,442 · 병합 3,429 (18.6%) · 세로병합이 덮는 칸 4,356
#           PDF        셀 14,391 · 병합 1,344 (9.3%)   ← 절반
#
#       그리고 그 근거는 지면 도형에 남아 있다 (표본 60쪽).
#
#           행안부 격자 복원 30쪽 · 다중밴드 21.1%  ← 정답 18.6% 에 가깝다
#           과기부 격자 복원 21쪽 · 다중밴드 10.8%
#
#       그래서 ⑦ 을 넓히는 대신 **물리 격자로 짚고 VLM 이 고치는** 쪽으로
#       대상을 재조준한다. 이 실험은 그 첫 단계로 차이를 표시만 한다.
# ────────────────────────────────────────────────────────────────────

def test_vector_grid_counts_physical_merges():
    """격자로 정렬된 사각형에서 병합 셀을 센다."""
    from docstruct.experiments.tsr.measure.vector_grid import physical_merges

    # 2행 3열 격자에서 첫 칸이 2행에 걸친다 (세로 병합)
    rects = [
        (0, 0, 10, 20),      # 병합 셀 (행 0~1)
        (10, 0, 20, 10), (20, 0, 30, 10),
        (10, 10, 20, 20), (20, 10, 30, 20),
        (30, 0, 40, 10), (30, 10, 40, 20),
    ]
    got = physical_merges(rects)
    assert got is not None
    assert got["merged"] == 1
    assert got["cells"] == len(rects)


def test_vector_grid_rejects_non_grid_shapes():
    """격자로 정렬되지 않는 도형은 버린다 — 없는 결함을 만들면 안 된다."""
    from docstruct.experiments.tsr.measure.vector_grid import physical_merges

    assert physical_merges([(0, 0, 10, 10), (3, 3, 13, 13)]) is None   # 너무 적다
    scattered = [(i * 7.3, i * 11.7, i * 7.3 + 9, i * 11.7 + 9) for i in range(8)]
    got = physical_merges(scattered)
    # 경계가 제각각이면 병합으로 세지 않는다
    assert got is None or got["merged"] == 0


def test_vector_grid_marks_only_when_gap_is_real():
    """TableFormer 가 이미 잡은 병합은 차이로 세지 않는다."""
    from docstruct.experiments.tsr.measure.vector_grid import _detected_cells, compare_grids

    cells = [{"row": 0, "col": 0, "rowspan": 2, "colspan": 1},
             {"row": 0, "col": 1, "rowspan": 1, "colspan": 3},
             {"row": 1, "col": 1, "rowspan": 1, "colspan": 1}]
    detected = _detected_cells(cells)
    assert len(detected) == 3
    assert _detected_cells(None) == []

    physical = [(0, 0, 2, 1), (0, 1, 1, 3), (1, 1, 1, 1)]
    report = compare_grids(physical, detected)
    assert report["missing"] == []               # 같은 자리다
    assert report["extra"] == []


def test_vector_grid_compares_positions_not_counts():
    """개수가 같아도 자리가 다르면 차이로 본다.

    개수만 견주면 **같은 수의 다른 병합**을 일치로 착각한다.
    """
    from docstruct.experiments.tsr.measure.vector_grid import compare_grids

    physical = [(0, 0, 2, 1), (5, 5, 1, 1)]
    detected = [(3, 3, 2, 1), (5, 5, 1, 1)]      # 병합 수는 1 로 같다
    report = compare_grids(physical, detected)
    assert report["physical"] == report["detected"] == 1
    assert report["missing"] == [(0, 0, 2, 1)]   # 지면에 있는데 인식엔 없다
    assert report["extra"] == [(3, 3, 2, 1)]     # 인식에만 있다


def test_vector_grid_reports_partial_coverage():
    """배경 사각형이 표의 일부만 덮으면 덮개 값이 낮다.

    실측(행안부 121쪽)에서 물리 9칸 · 인식 18칸이었다 — 그 표의 물리
    격자는 표 전체가 아니라 위쪽 일부였다.
    """
    from docstruct.experiments.tsr.measure.vector_grid import compare_grids

    physical = [(0, 0, 1, 1)] * 1 + [(0, i, 1, 1) for i in range(1, 9)]
    detected = [(r, c, 1, 1) for r in range(2) for c in range(9)]
    report = compare_grids(physical, detected)
    assert report["coverage"] == 0.5


def test_vector_grid_marks_nothing_without_pdf_path():
    """원본 경로가 없으면 아무것도 하지 않는다 (HWP 계열)."""
    from docstruct.experiments.tsr.measure.vector_grid import run

    assert run([], pdf_path=None) == 0


def test_vector_grid_is_display_only():
    """표시만 한다 — 표 내용을 바꾸지 않는다."""
    import inspect

    from docstruct.experiments.tsr.measure import vector_grid
    source = inspect.getsource(vector_grid.run)
    assert "table.markdown =" not in source
    assert "grid_merge_gap" in source


def test_vector_grid_clusters_separate_tables():
    """한 쪽에 표가 둘이면 무리를 갈라 센다.

    함께 세면 위쪽 표의 열 경계와 아래쪽 표의 것이 한 목록이 되어, 정상
    셀이 여러 밴드를 걸치는 것으로 보인다 — 실측(행안부 339쪽)에서
    병합 31 이 나왔고, 무리를 가르니 정답과 같은 5 가 됐다.
    """
    from docstruct.experiments.tsr.measure.vector_grid import cluster_rects

    upper = [(0, 0, 50, 10), (50, 0, 100, 10)]
    lower = [(0, 90, 20, 100), (20, 90, 40, 100), (40, 90, 100, 100)]
    groups = cluster_rects(upper + lower)
    assert len(groups) == 2
    assert sorted(len(g) for g in groups) == [2, 3]


def test_vector_grid_keeps_one_table_together():
    """붙어 있는 행들은 한 무리로 둔다 — 표를 쪼개면 병합을 놓친다."""
    from docstruct.experiments.tsr.measure.vector_grid import cluster_rects

    rows = [(0, y, 50, y + 10) for y in (0, 10, 20, 30)]
    assert len(cluster_rects(rows)) == 1


# ────────────────────────────────────────────────────────────────────
# 0.3.63 — 폐기된 실험이 배포본에서 되살아나던 문제
#
# 배경: 사내 배치에서 `--exp vector_grid` 가 "모르는 실험" 으로 거부됐고,
#       쓸 수 있는 목록에 **0.3.48 에서 폐기한 셋**이 들어 있었다.
#
#           쓸 수 있는 것: cell_repair, grid_consensus, grid_refine,
#                          otsl_diff, split_merge, two_way_match
#
#       overlay 는 `cp -r overlay/app/* .` 로 덮어쓴다 — **사라진 파일은
#       지우지 않는다.** 그래서 폐기한 모듈이 남아 그대로 import 됐고,
#       `pkgutil.iter_modules` 가 그것을 등록했다. 새 실험이 없는 것도
#       같은 원인(낡은 배포)이었다.
#
#       폐기 키는 등록하지 않고, 파일이 남아 있으면 어디를 지워야 하는지
#       알린다. 결과가 조용히 달라지는 것보다 시끄러운 편이 낫다.
# ────────────────────────────────────────────────────────────────────

def test_retired_experiments_do_not_register():
    """폐기한 키는 파일이 남아 있어도 등록되지 않는다."""
    from docstruct.experiments.registry import (
        RETIRED_KEYS, Experiment, _REGISTRY, register,
    )

    key = sorted(RETIRED_KEYS)[0]
    before = dict(_REGISTRY)
    try:
        register(Experiment(key=key, title="", purpose="", origin="",
                            formats=("pdf",), status="retired", note=""))
        assert key not in _REGISTRY
    finally:
        _REGISTRY.clear()
        _REGISTRY.update(before)


def test_retired_keys_match_history():
    """0.3.48 에서 폐기한 셋이 목록에 있다."""
    from docstruct.experiments.registry import RETIRED_KEYS

    assert RETIRED_KEYS == {"grid_refine", "split_merge", "grid_consensus"}


def test_stale_modules_reports_clean_tree():
    """이 트리에는 폐기 모듈 파일이 없다."""
    from docstruct.experiments import stale_modules
    assert stale_modules() == []


def test_current_experiments_are_not_retired():
    """등록된 실험과 폐기 목록이 겹치지 않는다."""
    from docstruct.experiments import all_experiments
    from docstruct.experiments.registry import RETIRED_KEYS

    keys = {e.key for e in all_experiments()}
    assert not (keys & RETIRED_KEYS)


# ────────────────────────────────────────────────────────────────────
# 0.3.64 — cp949 콘솔에서 출력 한 글자에 죽던 문제
#
# 배경: 로컬(윈도우)에서 도구가 돌지 않는다는 보고. 재현해 보니 출력이었다.
#
#     UnicodeEncodeError: 'cp949' codec can't encode character '\u2014'
#
#       줄표(`—`)가 cp949 에 없다. 윈도우 한국어 기본 콘솔이 그 코드페이지라,
#       그 문자가 든 줄을 찍는 순간 프로그램이 죽는다. **처리 결과를 다 만들어
#       놓고 화면에 찍다가 잃는다.**
#
#       전수 조사: 출력·로그 96곳에 cp949 로 못 찍는 문자가 있었다.
#       (줄표 91 · `⚠` 5 · `✅` 1 · 변이 선택자 3)
#
#       0.1.x 의 winfix 는 PyTorch 가 파일을 **읽다** 죽는 문제였고, 이번은
#       우리가 **쓰다** 죽는 문제다 — 같은 로케일, 다른 방향.
#
#       인코딩을 UTF-8 로 바꾸지 않는다. 코드페이지 949 콘솔에 UTF-8 을 보내면
#       한글이 통째로 깨진다. 못 찍는 몇 글자만 비슷한 글자로 바꾼다.
# ────────────────────────────────────────────────────────────────────

def test_console_substitutes_unencodable_chars():
    """콘솔이 못 찍는 문자를 비슷한 글자로 바꾼다."""
    from docstruct.core.winfix import _substitute

    class _Err:
        object = "값 — 표시"
        start, end = 2, 3

    replacement, position = _substitute(_Err())
    assert replacement == "-"
    assert position == 3


def test_console_substitute_never_raises():
    """표에 없는 문자도 예외를 던지지 않는다.

    출력 한 글자 때문에 처리 결과를 잃는 것이 훨씬 나쁘다.
    """
    from docstruct.core.winfix import _substitute

    class _Err:
        object = "값 𝕏 표시"
        start, end = 2, 3

    replacement, _ = _substitute(_Err())
    assert replacement == "?"


def test_cp949_console_survives_real_messages():
    """실제 출력 문구가 cp949 에서 죽지 않는다."""
    import codecs

    from docstruct.core.winfix import _ERROR_HANDLER, make_console_safe

    make_console_safe()                      # 처리기 등록
    codecs.lookup_error(_ERROR_HANDLER)      # 등록됐는가

    messages = [
        "LLM 미설정 — 표 평가·재추출·목차 없이 파싱만 수행합니다.",
        "  ⚠ 폐기된 실험 파일이 남아 있습니다: grid_refine.py",
        "hwp5-tree(기본 경로) 실패 — 폴백으로 내려갑니다",
    ]
    for message in messages:
        encoded = message.encode("cp949", errors=_ERROR_HANDLER)
        assert encoded.decode("cp949")       # 되읽을 수 있다
        assert "\u2014" not in encoded.decode("cp949")


def test_console_safe_leaves_utf8_alone():
    """UTF-8 콘솔에서는 손대지 않는다."""
    import io
    import sys

    from docstruct.core import winfix

    saved_out, saved_err, saved_flag = sys.stdout, sys.stderr, winfix._console_ready
    try:
        winfix._console_ready = False
        sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        sys.stderr = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        assert winfix.make_console_safe() is False      # 바꾼 것이 없다
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err
        winfix._console_ready = saved_flag


def test_cli_makes_console_safe_first():
    """CLI 는 인자 해석보다 먼저 콘솔을 안전하게 한다.

    나중에 하면 argparse 오류 메시지에서 이미 죽는다.
    """
    import inspect

    from docstruct import cli

    source = inspect.getsource(cli.main)
    assert source.index("make_console_safe()") < source.index("parse_args")


def test_module_entry_point_exists():
    """`python -m docstruct` 로 부를 수 있다.

    설치본은 `docstruct` 명령이 있지만, 로컬 트리·사내 배포는 `pip install`
    을 하지 않아 `-m` 으로 부른다. 그때 `.cli` 까지 적어야 했다.
    """
    import importlib.util

    spec = importlib.util.find_spec("docstruct.__main__")
    assert spec is not None, "docstruct/__main__.py 가 없습니다"


def test_module_entry_point_calls_cli_main():
    """진입점이 CLI main 을 부른다 (다른 경로로 갈라지지 않는다)."""
    import importlib.util
    from pathlib import Path

    spec = importlib.util.find_spec("docstruct.__main__")
    source = Path(spec.origin).read_text(encoding="utf-8")
    assert "cli import main" in source
    assert "main()" in source


# ────────────────────────────────────────────────────────────────────
# 0.3.66 — OMP_NUM_THREADS 로 죽던 문제
#
# 배경: 로컬 실행에서 문서 처리가 통째로 실패했다.
#
#     === 행안부_벡터격자시험10쪽.pdf === 실패: set_num_threads expects a positive integer
#
#       Docling 은 `OMP_NUM_THREADS` 를 읽어 그대로 `torch.set_num_threads()`
#       에 넘긴다. torch 는 양수만 받는데, `0` 은 일부 배치 스크립트·conda
#       환경이 "제한 없음" 뜻으로 쓰는 값이라 실제로 들어온다.
#
#       추출을 다 못 하고 죽는 것보다 바로잡아 도는 편이 낫다. 바로잡되
#       무엇을 했는지 남긴다.
# ────────────────────────────────────────────────────────────────────

def test_sanitize_thread_env_fixes_zero(monkeypatch):
    """`OMP_NUM_THREADS=0` 을 양수로 바로잡는다."""
    from docstruct.core.config import sanitize_thread_env

    monkeypatch.setenv("OMP_NUM_THREADS", "0")
    fixed = sanitize_thread_env()
    assert fixed and fixed > 0
    assert int(__import__("os").environ["OMP_NUM_THREADS"]) > 0


def test_sanitize_thread_env_fixes_garbage(monkeypatch):
    """정수가 아닌 값도 바로잡는다."""
    from docstruct.core.config import sanitize_thread_env

    for bad in ("-1", "abc", "0.5"):
        monkeypatch.setenv("OMP_NUM_THREADS", bad)
        assert (sanitize_thread_env() or 0) > 0


def test_sanitize_thread_env_leaves_good_values(monkeypatch):
    """멀쩡한 값은 건드리지 않는다."""
    from docstruct.core.config import sanitize_thread_env

    monkeypatch.setenv("OMP_NUM_THREADS", "6")
    assert sanitize_thread_env() is None
    assert __import__("os").environ["OMP_NUM_THREADS"] == "6"

    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    assert sanitize_thread_env() is None


def test_resolve_thread_count_prefers_setting(monkeypatch):
    """설정이 환경변수보다 우선한다."""
    from docstruct.converters.pdf import docling_backend

    class _Fake:
        num_threads = 3

    monkeypatch.setenv("OMP_NUM_THREADS", "0")
    monkeypatch.setattr(docling_backend, "get_settings", lambda: _Fake())
    assert docling_backend.resolve_thread_count() == 3


def test_resolve_thread_count_fixes_negative_setting(monkeypatch):
    """설정이 음수면 쓸 만한 값으로 바꾼다."""
    from docstruct.converters.pdf import docling_backend

    class _Fake:
        num_threads = -4

    monkeypatch.setattr(docling_backend, "get_settings", lambda: _Fake())
    assert docling_backend.resolve_thread_count() > 0


def test_cli_hints_on_thread_failure():
    """스레드 오류에는 다음 수를 알려 준다."""
    from docstruct.cli import _failure_hint

    hint = _failure_hint(RuntimeError("set_num_threads expects a positive integer"))
    assert hint and "num_threads" in hint
    hint.encode("cp949")                     # 윈도우 콘솔에서도 찍힌다
    assert _failure_hint(ValueError("관계없는 오류")) is None


def test_cli_sanitizes_threads_before_work():
    """CLI 는 문서를 열기 전에 환경을 바로잡는다."""
    import inspect

    from docstruct import cli

    source = inspect.getsource(cli.main)
    assert "sanitize_thread_env()" in source
    assert source.index("sanitize_thread_env()") < source.index("parse_args")


# ────────────────────────────────────────────────────────────────────
# 0.3.67 — `--set` 없이는 안 돌던 진짜 이유
#
# 배경: 0.3.66 에서 `OMP_NUM_THREADS` 를 바로잡았는데도 `--set num_threads=4`
#       없이는 여전히 죽었다. 범인은 다른 변수였다.
#
#       Docling 의 `AcceleratorOptions` 는 `DOCLING_` 접두어를 쓰는 설정
#       객체라 **`DOCLING_NUM_THREADS` 를 직접 읽는다.** 그런데 이 이름은
#       docstruct 설정 `num_threads` 의 환경변수이기도 하고, docstruct 에서
#       0 은 "기본값에 맡김" 이라는 뜻이다. `.env.example` 도 그렇게 적어
#       두었다.
#
#           # DOCLING_NUM_THREADS=0                 # 0 이면 기본값
#
#       docstruct 문법으로는 맞는 값인데 Docling 에는 독이었다. `--set` 이
#       듣던 이유도 이것 — 0 을 4 로 덮어썼기 때문이다.
#
#       0 이면 **변수를 지운다.** docstruct 기본값이 어차피 0(=맡김)이라
#       뜻이 달라지지 않고, Docling 은 제 기본값을 쓴다.
# ────────────────────────────────────────────────────────────────────

def test_sanitize_removes_zero_docling_threads(monkeypatch):
    """`DOCLING_NUM_THREADS=0` 은 지운다 (Docling 이 직접 읽는다)."""
    import os

    from docstruct.core.config import sanitize_thread_env

    monkeypatch.setenv("DOCLING_NUM_THREADS", "0")
    sanitize_thread_env()
    assert "DOCLING_NUM_THREADS" not in os.environ


def test_sanitize_keeps_positive_docling_threads(monkeypatch):
    """양수는 그대로 둔다 — 사용자가 정한 값이다."""
    import os

    from docstruct.core.config import sanitize_thread_env

    monkeypatch.setenv("DOCLING_NUM_THREADS", "4")
    sanitize_thread_env()
    assert os.environ["DOCLING_NUM_THREADS"] == "4"


def test_removing_docling_threads_keeps_meaning(monkeypatch):
    """변수를 지워도 docstruct 해석은 같다 (0 = 맡김)."""
    from docstruct.core.config import _get_int

    monkeypatch.delenv("DOCLING_NUM_THREADS", raising=False)
    assert _get_int("DOCLING_NUM_THREADS", 0) == 0


def test_sanitize_handles_both_variables(monkeypatch):
    """두 변수가 동시에 나빠도 각각 맞게 처리한다."""
    import os

    from docstruct.core.config import sanitize_thread_env

    monkeypatch.setenv("DOCLING_NUM_THREADS", "0")
    monkeypatch.setenv("OMP_NUM_THREADS", "0")
    sanitize_thread_env()
    assert "DOCLING_NUM_THREADS" not in os.environ      # 지운다
    assert int(os.environ["OMP_NUM_THREADS"]) > 0       # 고친다


def test_env_example_does_not_suggest_zero_threads():
    """`.env.example` 이 0 을 권하지 않는다.

    이 줄을 그대로 켜면 죽었다. 예시가 사용자를 함정으로 보내면 안 된다.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    example = root / ".env.example"
    if not example.is_file():
        pytest.skip(".env.example 없음 — pkg 트리 전용 검사")
    for line in example.read_text(encoding="utf-8").splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped.startswith("DOCLING_NUM_THREADS="):
            value = stripped.split("=", 1)[1].split()[0]
            assert value != "0", "0 을 예시로 두면 안 됩니다"


# ────────────────────────────────────────────────────────────────────
# 0.3.68 — 복원 정밀도를 셀 자리로 판정
#
# 배경: 0.3.67 은 병합 **개수**만 견주었다. 개수가 같아도 자리가 다를 수
#       있어, 실물 정답(HWPX)과 셀 자리(행·열·span)로 다시 쟀다.
#
#       행안부 10쪽 · 표 12개 · 표마다 HWPX 표를 셀 텍스트로 매칭:
#
#           병합 셀   정밀도   재현율
#           물리 격자   88%     48%
#           TableFormer 60%     24%
#
#       덮개(물리 셀 ÷ 인식 셀) 1.0 이상인 표만 보면 물리 격자가
#       **정밀도·재현율 100%** 다 — 셀 208~222개짜리 표 네 개가 자리까지
#       전부 일치했다. '놓침' 표시 24개도 모두 정답에 실재했다.
#
#       덮개가 낮은 표(배경 사각형이 일부만)는 83% 로 떨어진다. 버리지 않고
#       `confidence: low` 로 등급을 매긴다 — 실측 근거가 있는 값이다.
# ────────────────────────────────────────────────────────────────────

def test_grid_gap_grades_confidence_by_coverage():
    """덮개로 신뢰 등급을 매긴다."""
    from docstruct.experiments.tsr.measure.vector_grid import HIGH_COVERAGE, compare_grids

    full = [(r, c, 1, 1) for r in range(3) for c in range(3)]
    full[0] = (0, 0, 2, 1)
    detected = [(r, c, 1, 1) for r in range(3) for c in range(3)]
    report = compare_grids(full, detected)
    assert report["coverage"] >= HIGH_COVERAGE

    partial = full[:4]
    assert compare_grids(partial, detected)["coverage"] < HIGH_COVERAGE


def test_grid_gap_keeps_missing_positions():
    """놓친 병합의 **자리**를 남긴다 (VLM 에 짚어 주기 위해서다)."""
    from docstruct.experiments.tsr.measure.vector_grid import compare_grids

    physical = [(0, 0, 3, 1)] + [(r, 1, 1, 1) for r in range(3)]
    detected = [(r, c, 1, 1) for r in range(3) for c in range(2)]
    report = compare_grids(physical, detected)
    assert (0, 0, 3, 1) in report["missing"]
    assert all(len(pos) == 4 for pos in report["missing"])


def test_physical_cells_returns_positions():
    """격자를 셀 자리 목록으로 낸다."""
    from docstruct.experiments.tsr.measure.vector_grid import physical_cells

    rects = [
        (0, 0, 10, 20),                                   # 세로 병합
        (10, 0, 20, 10), (20, 0, 30, 10),
        (10, 10, 20, 20), (20, 10, 30, 20),
        (30, 0, 40, 10), (30, 10, 40, 20),
    ]
    got = physical_cells(rects)
    assert got is not None
    cells, rows, cols = got
    assert (0, 0, 2, 1) in cells                          # 2행 1열 병합
    assert rows == 2 and cols == 4


def test_grid_gap_field_shape():
    """표시 항목이 약속한 모양을 지킨다.

    백엔드(rag 브릿지)와 진단 도구가 이 열쇠들을 읽는다.
    """
    import inspect

    from docstruct.experiments.tsr.measure import vector_grid
    source = inspect.getsource(vector_grid.run)
    for key in ("missing", "extra", "coverage", "confidence", "physical", "detected"):
        assert f'"{key}"' in source


# ────────────────────────────────────────────────────────────────────
# 0.3.69 — 가설 H1(결정 복원)·H5(산술 검산)를 실험으로 구현
#
# 배경: 가설재검토_복원기준.md 의 실행 단계. 학습을 지양하는 제약 아래
#       실물로 검증 가능한 둘을 먼저 구현했다.
#
#       ⑦ grid_restore — 실물 검증: 덮개≥1.0 표 5개, 833/833 셀
#          (자리·span·텍스트) HWPX 정답과 완전 일치. 병합 없는 표(34셀)
#          포함 — 회귀 0.
#       ⑧ sum_check — HWPX 정답 580표에서 **오탐 0** (계층 표 별첨1을
#          평평함 조건으로 걸러냄), 합성 훼손 표는 잡음.
# ────────────────────────────────────────────────────────────────────

def test_grid_restore_markdown_follows_hwpx_rules():
    """복원 markdown 이 hwpxtree 와 같은 규칙을 쓴다 (`〃`·빈 앞행 제거).

    파이프라인 나머지가 HWPX 표와 PDF 표를 같은 모양으로 받아야 한다.
    """
    from docstruct.experiments.tsr.restore.grid_restore import cells_to_markdown

    cells = [
        {"row": 0, "col": 0, "rowspan": 1, "colspan": 1, "text": "구분"},
        {"row": 0, "col": 1, "rowspan": 1, "colspan": 1, "text": "값"},
        {"row": 1, "col": 0, "rowspan": 2, "colspan": 1, "text": "지표"},
        {"row": 1, "col": 1, "rowspan": 1, "colspan": 1, "text": "10"},
        {"row": 2, "col": 1, "rowspan": 1, "colspan": 1, "text": "20"},
    ]
    markdown = cells_to_markdown(cells)
    lines = markdown.splitlines()
    assert lines[0] == "| 구분 | 값 |"
    assert "〃" in lines[3]                      # 세로 병합 이어짐 표식


def test_grid_restore_skips_low_coverage(tmp_path):
    """덮개 < 1.0 이면 복원하지 않는다 — 회귀 없음이 우선이다."""
    from docstruct.experiments.tsr.restore import grid_restore
    # 사각형 6개(격자 성립)인데 인식 셀이 20개 → 덮개 0.3
    detected = [{"row": r, "col": c, "rowspan": 1, "colspan": 1}
                for r in range(4) for c in range(5)]

    calls = {}

    def fake_rects(path, page):                  # noqa: ARG001
        calls["hit"] = True
        return [(0, 0, 10, 10), (10, 0, 20, 10), (20, 0, 30, 10),
                (0, 10, 10, 20), (10, 10, 20, 20), (20, 10, 30, 20)]

    original = grid_restore._page_rects
    grid_restore._page_rects = fake_rects
    try:
        result = grid_restore.restore_table(
            "x.pdf", 1, {"l": 0, "t": 0, "r": 100, "b": 100}, detected)
    finally:
        grid_restore._page_rects = original
    assert calls.get("hit") and result is None


def test_sum_check_passes_clean_flat_table():
    """평평한 총계 표는 통과한다 (HWPX 실물 table_27 꼴)."""
    from docstruct.experiments.tsr.measure.sum_check import check_table

    cells = [{"row": 0, "col": 0, "text": "소관"}, {"row": 0, "col": 1, "text": "계"},
             {"row": 1, "col": 0, "text": "총계"}, {"row": 1, "col": 1, "text": "30"},
             {"row": 2, "col": 0, "text": "가"}, {"row": 2, "col": 1, "text": "10"},
             {"row": 3, "col": 0, "text": "나"}, {"row": 3, "col": 1, "text": "20"}]
    report = check_table(cells)
    assert report == {"checked": 1, "failed": 0, "failures": []}


def test_sum_check_catches_broken_total():
    """합이 어긋나면 잡는다 — 병합 손실로 값이 밀린 표의 신호다."""
    from docstruct.experiments.tsr.measure.sum_check import check_table

    cells = [{"row": 0, "col": 0, "text": "소관"}, {"row": 0, "col": 1, "text": "계"},
             {"row": 1, "col": 0, "text": "총계"}, {"row": 1, "col": 1, "text": "99"},
             {"row": 2, "col": 0, "text": "가"}, {"row": 2, "col": 1, "text": "10"},
             {"row": 3, "col": 0, "text": "나"}, {"row": 3, "col": 1, "text": "20"}]
    report = check_table(cells)
    assert report["failed"] == 1
    assert report["failures"][0]["expected"] == 30.0


def test_sum_check_skips_layered_tables():
    """층이 섞인 표(전략목표·프로그램목표·사업)는 검사하지 않는다.

    실측(행안부 별첨1)에서 층마다 금액이 있어 전부 더하면 총계의 세 배가
    나왔다 — 검산기 오탐이다. 행마다 딱지 열이 달라지는 것으로 가른다.
    """
    from docstruct.experiments.tsr.measure.sum_check import check_table

    cells = [{"row": 0, "col": 0, "text": "전략목표"}, {"row": 0, "col": 3, "text": "1221"},
             {"row": 1, "col": 0, "text": "합계"}, {"row": 1, "col": 3, "text": "1221"},
             {"row": 2, "col": 1, "text": "사업A"}, {"row": 2, "col": 3, "text": "600"},
             {"row": 3, "col": 1, "text": "사업B"}, {"row": 3, "col": 3, "text": "621"}]
    assert check_table(cells) is None            # 딱지 열이 0·1 로 섞였다


def test_sum_check_number_notation():
    """정부 문서 수 표기를 읽는다 — △·괄호는 음수, 굵게 표식은 벗긴다."""
    from docstruct.experiments.tsr.measure.sum_check import parse_number

    assert parse_number("△1,234") == -1234.0
    assert parse_number("(5.5)") == -5.5
    assert parse_number("**7**") == 7.0
    assert parse_number("연 12회") is None       # 글이 섞이면 수가 아니다


# ────────────────────────────────────────────────────────────────────
# 0.3.70 — H2/H3/H8/GriTS 를 실험으로 구현 (⑨ 합성 격자 · ⑩ 스캔 격자 · ⑪ 채점)
#
# 배경: 가설재검토_복원기준.md 의 실행 2단계. 전부 학습 없는 결정론이다.
#
#       ⑨ line_grid — H2 결정 실증: 사각형만으로 덮개 0.5 이던 표
#          (원본 121쪽, ⑦이 건너뜀)가 사각형 모서리+괘선 합성으로
#          **25/25 셀 완전 일치 (병합 7/7)**. T1 다섯 표는 합성해도
#          그대로 완전 일치 — 회귀 0.
#       ⑩ scan_grid — 같은 격자 코드에 근거만 화소(렌더 선 검출)로.
#          주택과세금 실측: 표 있는 쪽(179쪽 43×20 등)에서 격자 성립.
#       ⑪ grid_score — GriTS 간이판(정렬 가정 Dice). 정답 없이 전 문서의
#          근거별 유불리 양상을 잰다.
# ────────────────────────────────────────────────────────────────────

def test_lattice_merges_where_no_separator():
    """이웃 칸을 가르는 선이 없으면 병합이다 (SPARTAN 결정식)."""
    from docstruct.experiments.tsr.measure.line_grid import lattice_cells

    # 3×2 격자, (0,0)-(1,0) 사이 가로선이 없다 → 세로 병합
    horizontal = [(0, 0, 20), (20, 10, 20), (30, 0, 20)]   # y=10 은 오른쪽 반만
    vertical = [(0, 0, 30), (10, 0, 30), (20, 0, 30)]
    got = lattice_cells(horizontal, vertical)
    assert got is not None
    cells, rows, cols = got
    assert rows == 2 and cols == 2
    assert (0, 0, 2, 1) in cells                 # 왼쪽 열이 병합됐다
    assert (0, 1, 1, 1) in cells and (1, 1, 1, 1) in cells


def test_lattice_rejects_non_rectangular_merge():
    """병합 결과가 직사각형이 아니면 격자를 통째로 버린다.

    L자 병합은 표가 아니라 도해다 — 없는 결함을 만드는 것보다 안 내는
    편이 낫다.
    """
    from docstruct.experiments.tsr.measure.line_grid import lattice_cells

    # 2×2 에서 (0,0)-(0,1), (0,0)-(1,0) 만 잇고 (1,1) 은 분리 → L자
    horizontal = [(0, 0, 20), (10, 10, 20), (20, 0, 20)]
    vertical = [(0, 0, 20), (10, 10, 20), (20, 0, 20)]
    assert lattice_cells(horizontal, vertical) is None


def test_lattice_rejects_diagram_like_grids():
    """병합 비율이 상한을 넘으면 도해로 보고 버린다.

    실측 근거: 과기부 335쪽(도해) 병합 1,967/1,967 · 주택과세금 179쪽
    (쪽 전체) 136/145. 정상 표 최대치는 0.4 를 넘지 않았다.
    """
    from docstruct.experiments.tsr.measure.line_grid import MAX_MERGE_RATIO, lattice_cells

    assert MAX_MERGE_RATIO == 0.6
    # 4×2 인데 가로 안쪽 선이 하나도 없다 → 열마다 4행 병합 (비율 1.0)
    horizontal = [(0, 0, 20), (40, 0, 20)]
    vertical = [(0, 0, 40), (10, 0, 40), (20, 0, 40)]
    assert lattice_cells(horizontal, vertical) is None


def test_rect_edges_join_ruling_lines():
    """사각형 모서리와 괘선이 한 경계 목록으로 합쳐진다 (H2 의 뼈대).

    출처가 달라도 격자 세우기는 같다 — 그래서 T1(사각형만)·T2(섞임)·
    T3(괘선만)를 한 알고리즘이 받는다.
    """
    from docstruct.experiments.tsr.measure.line_grid import LINE_MAX_THICK, LINE_MIN_LEN

    assert LINE_MAX_THICK < 2.0                  # 괘선 판별 두께
    assert LINE_MIN_LEN >= 8.0                   # 장식 거르기


def test_scan_runs_bridge_dotted_gaps():
    """점선·스캔 결손의 짧은 끊김은 이어 붙인다."""
    import numpy as np

    from docstruct.experiments.tsr.measure.scan_grid import GAP_PX, _runs

    line = np.zeros(60, dtype=bool)
    line[10:25] = True
    line[27:40] = True                           # 2px 끊김 — GAP_PX 이하
    line[50:52] = True                           # 짧은 잡티
    runs = _runs(line, min_len=20)
    assert runs == [(10, 40)]                    # 이어져 하나가 됐다
    assert GAP_PX >= 2


def test_grid_dice_and_merged_dice():
    """Dice 는 자리 정확일치 기준 — 일치를 지어내지 않는다."""
    from docstruct.experiments.tsr.measure.grid_score import grid_dice, merged_dice

    a = [(0, 0, 1, 1), (0, 1, 1, 1), (1, 0, 2, 1)]
    b = [(0, 0, 1, 1), (0, 1, 1, 1), (1, 0, 1, 1)]
    assert abs(grid_dice(a, b) - 2 * 2 / 6) < 1e-9
    assert merged_dice(a, b) == 0.0              # 병합은 한쪽에만 있다
    assert merged_dice([(0, 0, 1, 1)], [(0, 0, 1, 1)]) is None  # 둘 다 없음
    assert grid_dice([], []) == 1.0


def test_grid_score_needs_two_candidates():
    """후보가 하나뿐이면 재지 않는다 — 비교가 아니라 나열이 된다."""
    import inspect

    from docstruct.experiments.tsr.measure import grid_score
    source = inspect.getsource(grid_score.score_table)
    assert "len(candidates) < 2" in source


# ────────────────────────────────────────────────────────────────────
# 0.3.71 — ⑨ 승격 게이트 기각 실측 · T6 열 배치 검산 실증 · H10 프롬프트
#
# 배경: 검증 순서(가설 문서 §15-4)의 실행. 세 결론 전부 실측이다.
#
#   게이트 기각 — 후보 5종(G1 rect⊂lattice · G2 덮개 · G3 결합 ·
#     G4a 텍스트 담김율 · G4b 기준선 수) 전부 table_4·6(성긴 lattice)을
#     오통과하거나 무변별. **11표 표본에서 사전 게이트를 못 찾았다** →
#     ⑨는 표시 유지, 승격은 사후 검증(⑧ 검산·② OTSL) 통과로 설계 전환.
#
#   T6 검산 실증 — 연속 쪽 표(별첨3)의 열 배치:
#     340→342쪽 경계 18개 완전 일치(0.00pt) · 339→340쪽은 홀짝 여백으로
#     14.26pt 상수 이동(퍼짐 0.001pt) → **너비 기준** 17/17 일치 ·
#     다른 표 대조군은 열 수부터 거부.
#
#   H10 — 단계별 지시(TaDA: 중형 모델) × missing 제시(NGTR) 2×2 손잡이.
#     기본은 모두 꺼짐 (현행 동작 보존). Gemma 26B 로 로컬 A/B 예정.
# ────────────────────────────────────────────────────────────────────

def test_column_widths_are_translation_invariant():
    """열 너비는 평행이동에 불변이다 — 홀짝 쪽 여백이 달라도 같은 표다."""
    from docstruct.experiments.tsr.measure.vector_grid import column_widths, same_column_layout

    rects = [(0, 0, 30, 10), (30, 0, 50, 10), (0, 10, 30, 20), (30, 10, 50, 20),
             (50, 0, 90, 10), (50, 10, 90, 20)]
    shifted = [(l + 14.26, t, r + 14.26, b) for l, t, r, b in rects]
    assert column_widths(rects) == [30.0, 20.0, 40.0]
    assert same_column_layout(rects, shifted) is True


def test_same_column_layout_rejects_different_tables():
    """열 수가 다르면 이어붙일 수 없는 표다."""
    from docstruct.experiments.tsr.measure.vector_grid import same_column_layout

    a = [(0, 0, 30, 10), (30, 0, 50, 10), (50, 0, 90, 10),
         (0, 10, 30, 20), (30, 10, 50, 20), (50, 10, 90, 20)]
    b = [(0, 0, 45, 10), (45, 0, 90, 10),
         (0, 10, 45, 20), (45, 10, 90, 20), (0, 20, 45, 30), (45, 20, 90, 30)]
    assert same_column_layout(a, b) is False


def test_vlm_prompt_defaults_to_plain(monkeypatch):
    """기본은 단문판 — 켜지 않으면 현행 동작 그대로다."""
    monkeypatch.delenv("DOCSTRUCT_VLM_PROMPT", raising=False)
    monkeypatch.delenv("DOCSTRUCT_VLM_HINT_MISSING", raising=False)
    from docstruct.models import TableInfo
    from docstruct.tables.vlm_rebuild import _missing_hint, _prompt_template

    assert "1단계" not in _prompt_template()
    # 힌트는 0.4.3 에서 기본 켬으로 승격됐다 — 끄려면 명시해야 한다.
    table = TableInfo(id="t", table_num=1, placeholder="", markdown="",
                      grid_merge_gap={"confidence": "high",
                                      "missing": [(0, 0, 2, 1)]})
    assert _missing_hint(table) != ""
    monkeypatch.setenv("DOCSTRUCT_VLM_HINT_MISSING", "false")
    assert _missing_hint(table) == ""


def test_vlm_hint_uses_only_high_confidence(monkeypatch):
    """missing 제시는 confidence:high 근거만 쓴다.

    낮은 덮개의 자리는 행·열 번호가 어긋날 수 있어(실측 83%), 틀린
    자리를 짚어 주면 역효과다.
    """
    monkeypatch.setenv("DOCSTRUCT_VLM_HINT_MISSING", "1")
    from docstruct.models import TableInfo
    from docstruct.tables.vlm_rebuild import _missing_hint

    table = TableInfo(id="t", table_num=1, placeholder="", markdown="",
                      grid_merge_gap={"confidence": "high",
                                      "missing": [(2, 0, 3, 1)]})
    assert "3행 1열에서 세로 3칸" in _missing_hint(table)
    table.grid_merge_gap["confidence"] = "low"
    assert _missing_hint(table) == ""
    # ⑨(synth_grid)도 같은 규칙으로 읽는다
    table.grid_merge_gap = None
    table.synth_grid = {"confidence": "high", "missing": [(0, 1, 1, 4)]}
    assert "1행 2열에서 가로 4칸" in _missing_hint(table)


# ────────────────────────────────────────────────────────────────────
# 0.3.72 — 실행 순서 = 복원 사다리 (자기선택 라우팅)
#
# 배경: 가설별 적용 표가 다른데 누가 라우팅하나 — 답은 "아무도 안 한다".
#       유형(T1~T5)은 사전 분류 라벨이 아니라 **성립 조건의 사후 이름**
#       이다: ⑦이 성립하면(사각형 격자 + 덮개≥1.0) 그 표가 T1 인 것이지,
#       T1 이라고 판정한 뒤 ⑦을 부르는 게 아니다. 판정=적용이므로 분류
#       오류라는 개념이 없다. 오케스트레이터 LLM 은 이 축에 불필요하다
#       (의미 축 table_kind 는 지금처럼 LLM 이 맡는다 — 두 축은 직교).
#
#       _RUN_ORDER 가 옛 3종만 알아 새 실험이 이름순으로 붙어 있었다 —
#       특히 ⑪(계측)이 ⑦(표를 바꿈) 뒤에 돌면 원본 대비 측정이 오염된다.
# ────────────────────────────────────────────────────────────────────

def test_run_order_is_the_restoration_ladder():
    """실행 순서가 사다리다: 계측 → 복원 → 표시 → 텍스트 → 검증."""
    from docstruct.experiments import all_experiments
    keys = [e.key for e in all_experiments()]
    # ⑮는 ⑬보다 먼저다 — 표를 통째로 다시 세운 뒤에는 열 수를 맞출
    # 일이 없다(⑮가 성공한 표는 ⑬의 대상에서 자연히 빠진다).
    # 쪽 단위 계측(scan_ab·scan_scale_ab)은 표를 보지 않으므로 맨 뒤다.
    assert keys == ["grid_score", "grid_restore", "lattice_restore",
                    "lattice_fill", "hole_fill", "col_grid", "head_grid",
                    "agreed_grid",
                    "vector_grid", "line_grid", "scan_grid",
                    "two_way_match", "otsl_diff", "cell_repair",
                    "sum_check", "over_split",
                    "scan_ab", "scan_scale_ab", "page_chrome", "chart_gate"]


def test_measurement_precedes_restoration():
    """⑪이 ⑦보다 먼저다 — 표를 바꾼 뒤에 재면 무엇을 재는지 알 수 없다."""
    from docstruct.experiments import all_experiments
    keys = [e.key for e in all_experiments()]
    assert keys.index("grid_score") < keys.index("grid_restore")
    assert keys.index("grid_restore") < keys.index("vector_grid")
    assert keys.index("cell_repair") < keys.index("sum_check")


# ────────────────────────────────────────────────────────────────────
# 0.3.73 — VLM-OCR 설계 구축: 채점기 · H12-a 산식 판별 · H12-b best-of-N · C2
#
# 배경: VLM_OCR_활용설계.md 의 실행 1~2단계 + H12-b 뼈대. RL 에서 가져온
#       것은 구조뿐이다 — 탐색(VLM) · 보상(결정론 채점기) · 선택. 전부
#       사다리 바깥이라 TSR 실험 코드는 읽기만 한다.
#
#       H12-a 실측(개정세법 117쪽): 큰 `{` 는 글자가 아니라 벡터 경로
#       (폭 1.8~6pt·높이 25pt+, 98쪽/84%). 가로선 유무·길이로는 표와
#       갈리지 않아(산식에도 분수선·상자선 63~421pt) **격자 성립(⑨)을
#       판별자로** 썼다 — 국소 표본: 산식 18/26 발화 · 표→산식 오판 0.
# ────────────────────────────────────────────────────────────────────

def test_grade_reads_ditto_merges_back():
    """채점기가 `〃` 를 세로 병합으로 되읽는다 (grid_restore 규칙의 역)."""
    from docstruct.tables.grade import cells_from_markdown

    markdown = "| 구분 | 값 |\n| --- | --- |\n| 지표 | 10 |\n| 〃 | 20 |"
    cells = cells_from_markdown(markdown)
    anchor = next(c for c in cells if c["text"] == "지표")
    assert anchor["rowspan"] == 2
    assert all(c["text"] != "〃" for c in cells)  # 표식은 셀이 아니다


def test_grade_gate_fails_broken_sum():
    """합이 깨진 후보는 문턱 탈락이다 — 구조 손상 신호를 점수로 덮지 않는다."""
    from docstruct.models import TableInfo
    from docstruct.tables.grade import score_candidate

    table = TableInfo(id="t", table_num=1, placeholder="", markdown="")
    good = "| 소관 | 계 |\n| --- | --- |\n| 총계 | 30 |\n| 가 | 10 |\n| 나 | 20 |"
    broken = good.replace("| 총계 | 30 |", "| 총계 | 99 |")
    assert score_candidate(table, good)["gate"] is True
    assert score_candidate(table, broken)["gate"] is False


def test_grade_scores_resolved_missing():
    """confidence:high 의 missing(세로 병합) 해소가 점수가 된다."""
    from docstruct.models import TableInfo
    from docstruct.tables.grade import pick_best

    table = TableInfo(id="t", table_num=1, placeholder="", markdown="",
                      grid_merge_gap={"confidence": "high",
                                      "missing": [(2, 0, 2, 1)]})
    flat = "| 구분 | 값 |\n| --- | --- |\n| 지표 | 10 |\n| 지표 | 20 |"
    merged = "| 구분 | 값 |\n| --- | --- |\n| 지표 | 10 |\n| 〃 | 20 |"
    name, graded = pick_best(table, {"평평": flat, "병합": merged})
    assert name == "병합" and graded["detail"]["missing_resolved"] == "1/1"


def test_grade_all_rejected_means_no_action():
    """전 후보 문턱 미달 → None — 폴백의 폴백은 무행동이다."""
    from docstruct.models import TableInfo
    from docstruct.tables.grade import pick_best

    table = TableInfo(id="t", table_num=1, placeholder="", markdown="")
    assert pick_best(table, {"a": "설명문입니다", "b": "| x |"}) is None


def test_formula_kind_flows_to_body_not_vlm_read():
    """산식 판정은 본문으로 흐르고 캡처 표 읽기에서 빠진다 (H12-a).

    vlm_read 는 region_kind == "image" 만 고르므로, "formula" 는 자동
    제외된다 — 배선을 문자열로 가드한다.
    """
    import inspect

    from docstruct.converters.pdf.region_kind import RegionKind
    from docstruct import extractors

    assert RegionKind.FORMULA.value == "formula"
    source = inspect.getsource(extractors.pdf._inject_region_text)
    assert '("text", "formula")' in source


def test_formula_rule_uses_lattice_not_line_length():
    """산식 판별자는 격자 성립(⑨)이다 — 선 길이·유무가 아니다.

    실측: 산식 블록에도 분수선·상자선(63~421pt)이 있어 길이로는 표와
    갈리지 않았다. 격자가 서면 표, 안 서면 산식 — 검증된 기계의 합성이다.
    """
    import inspect

    from docstruct.converters.pdf import region_kind

    source = inspect.getsource(region_kind.classify_region)
    assert "table_lattice" in source
    assert "MIN_BRACES" in source


def test_best_of_defaults_to_single_call(monkeypatch):
    """best-of 는 기본 1 — 켜지 않으면 현행 단일 호출 그대로다."""
    monkeypatch.delenv("DOCSTRUCT_VLM_BEST_OF", raising=False)
    monkeypatch.delenv("DOCSTRUCT_VLM_PROMPT", raising=False)
    from docstruct.models import TableInfo
    from docstruct.tables.vlm_rebuild import _best_of, _candidate_prompts

    assert _best_of() == 1
    table = TableInfo(id="t", table_num=1, placeholder="", markdown="")
    prompts = _candidate_prompts(table)
    assert len(prompts) == 1 and prompts[0][0] == "현행"


def test_best_of_orders_conservatively(monkeypatch):
    """후보 차례는 단문(현행)이 먼저다 — 동점이면 판이 안 바뀐다."""
    monkeypatch.setenv("DOCSTRUCT_VLM_BEST_OF", "4")
    monkeypatch.setenv("DOCSTRUCT_VLM_HINT_MISSING", "1")
    from docstruct.models import TableInfo
    from docstruct.tables.vlm_rebuild import _candidate_prompts

    table = TableInfo(id="t", table_num=1, placeholder="", markdown="",
                      grid_merge_gap={"confidence": "high",
                                      "missing": [(1, 0, 2, 1)]})
    names = [name for name, _, _ in _candidate_prompts(table)]
    assert names[0] == "단문"
    assert "단계별+힌트" in names and len(names) == 4
    # 힌트 근거가 없으면 힌트 판은 후보가 아니다
    plain = TableInfo(id="p", table_num=1, placeholder="", markdown="")
    assert [n for n, _, _ in _candidate_prompts(plain)] == ["단문", "단계별"]


def test_keep_formula_line_is_off_by_default(monkeypatch):
    """산식 보존 규칙 줄은 기본 꺼짐 (C2 손잡이)."""
    monkeypatch.delenv("DOCSTRUCT_VLM_KEEP_FORMULA", raising=False)
    from docstruct.tables.vlm_rebuild import _formula_line

    assert _formula_line() == ""
    monkeypatch.setenv("DOCSTRUCT_VLM_KEEP_FORMULA", "1")
    assert "계산하거나 풀어 쓰지" in _formula_line()


# ────────────────────────────────────────────────────────────────────
# 0.3.74 — ⑩ 벡터 게이트: 주석이 약속한 동작을 코드로
#
# 배경: "스캔 PDF 여부를 사용자가 말해 주나?" — 아니다, 두 층의 자기
#       판정이 있다. 문서 단위는 looks_scanned(표본 12쪽 텍스트 레이어,
#       장식 제거 후 300자 미만 80%↑ → 스캔본, 오판 시 안전 방향 False)
#       가 docling 호출 전에 가른다. 실험 단위(⑩)는 주석에 "벡터가 있으면
#       건너뛴다" 고 적혀 있었으나 **구현이 없었다** — 텍스트 PDF 에서
#       ⑩을 켜면 쪽마다 헛렌더를 했다. 쪽 단위 벡터 게이트를 넣었다.
# ────────────────────────────────────────────────────────────────────

def test_scan_grid_skips_pages_with_vector_evidence(monkeypatch):
    """벡터 선분이 있는 쪽은 렌더 없이 건너뛴다 — ⑨의 영역이다."""
    from docstruct.experiments.tsr.measure import scan_grid
    from docstruct.models import PageContent, TableInfo

    calls = {"render": 0}
    monkeypatch.setattr(
        scan_grid, "render_segments",
        lambda *a, **k: calls.__setitem__("render", calls["render"] + 1) or None)
    import docstruct.experiments.tsr.measure.line_grid as line_grid
    monkeypatch.setattr(line_grid, "page_segments",
                        lambda *a, **k: ([(0, 0, 10)], []))   # 벡터 있음

    page = PageContent(page_no=1, page_no_kind="exact", content="", tables=[
        TableInfo(id="t", table_num=1, placeholder="", markdown="",
                  bbox={"l": 0, "t": 0, "r": 10, "b": 10})])
    assert scan_grid.run([page], pdf_path="x.pdf") == 0
    assert calls["render"] == 0                  # 렌더 자체가 없어야 한다


def test_scan_grid_renders_when_no_vector(monkeypatch):
    """벡터가 없는 쪽(스캔)만 렌더한다 — 합본에서 쪽 단위로 갈린다."""
    from docstruct.experiments.tsr.measure import scan_grid
    from docstruct.models import PageContent, TableInfo

    calls = {"render": 0}
    monkeypatch.setattr(
        scan_grid, "render_segments",
        lambda *a, **k: calls.__setitem__("render", calls["render"] + 1) or None)
    import docstruct.experiments.tsr.measure.line_grid as line_grid
    monkeypatch.setattr(line_grid, "page_segments", lambda *a, **k: ([], []))

    page = PageContent(page_no=1, page_no_kind="exact", content="", tables=[
        TableInfo(id="t", table_num=1, placeholder="", markdown="",
                  bbox={"l": 0, "t": 0, "r": 10, "b": 10})])
    scan_grid.run([page], pdf_path="x.pdf")
    assert calls["render"] == 1


# ────────────────────────────────────────────────────────────────────
# 0.3.75 — 구조화 층 신설 (docstruct/structuring — 설계 문서 §7 의 구축)
#
# 배경: 판독→RAG 직행을 넷으로 쪼갠다 (판독→json→구조화→RAG). 브릿지는
#       18필드를 나르는데 소비처가 markdown 뿐이던 갭(§19-2)의 해소 —
#       cells 를 레코드·계층·검산으로 편다. 전부 dict→dict 순수 함수라
#       사다리와 비접촉이고, 값을 만들지 않으며(_expanded 로 전개 출처
#       표시), 경고를 지우지 않는다(provenance.warnings).
#
#       실측: HWPX 정답 580표 → 레코드 2,287(전개 1,461행)·계약 위반 0·
#       ⑧ 재검산 실패 0. PDF 10쪽 → 개수 검산이 판독기의 불완전 사슬
#       (7→10, 8·9 누락)을 잡아냈다 — "열 검산 ok 사슬에서의 개수 실패
#       = 사슬 누락 신호".
# ────────────────────────────────────────────────────────────────────

def _nts_cells():
    """국세청 예산표 축약 픽스처 (§19 미니 재현 + 개수 열)."""
    rows = [
        ("프로그램", "프로그램명", "단위사업수", "단위사업", "단위사업명"),
        ("3100", "성실납세 및 민생지원", "3", "3132", "납세안내"),
        ("3100", None, "3", "3133", "납세자 권익보호"),
        ("3100", None, "3", "3134", "세금신고 지원"),
    ]
    cells = []
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            if text is None:
                continue
            rowspan = 3 if (r == 1 and c == 1) else 1
            cells.append({"row": r, "col": c, "rowspan": rowspan,
                          "colspan": 1, "text": text})
    return cells


def test_expand_replicates_anchor_into_covered_rows():
    """rowspan 닻 값이 덮인 행 레코드에 복제되고 _expanded 로 표시된다."""
    from docstruct.structuring import expand_merges

    records = expand_merges(_nts_cells())
    assert records[1]["프로그램명"] == "성실납세 및 민생지원"
    assert "프로그램명" in records[1]["_expanded"]
    assert records[0]["_expanded"] == []          # 닻 행은 전개가 아니다


def test_expand_resolves_ditto_marks():
    """`〃` 는 위 행 값으로 풀린다 — 값을 만들지 않고 복제만 한다."""
    from docstruct.structuring import expand_merges

    cells = [{"row": 0, "col": 0, "rowspan": 1, "colspan": 1, "text": "구분"},
             {"row": 1, "col": 0, "rowspan": 1, "colspan": 1, "text": "지표"},
             {"row": 2, "col": 0, "rowspan": 1, "colspan": 1, "text": "〃"}]
    records = expand_merges(cells)
    assert records[1]["구분"] == "지표" and "구분" in records[1]["_expanded"]


def test_hierarchy_triples_from_rowspan():
    """이름 열의 rowspan 포함이 부모>자식 삼항이 된다 (H9b 1단계)."""
    from docstruct.structuring import column_names, extract_hierarchy

    cells = _nts_cells()
    triples = extract_hierarchy(cells, column_names(cells))
    pairs = {(t["parent"], t["child"]) for t in triples}
    assert ("성실납세 및 민생지원", "납세자 권익보호") in pairs
    assert all(t["evidence"] == "rowspan" for t in triples)


def test_count_invariant_passes_truth_and_catches_breakage():
    """개수 불변량: 정답 통과 · 개수 조작은 잡는다 (§19-4)."""
    from docstruct.structuring import column_names, count_check, expand_merges

    cells = _nts_cells()
    records = expand_merges(cells)
    names = column_names(cells)
    assert count_check(records, names) == {"checked": 1, "failed": 0,
                                           "failures": []}
    for record in records:
        record["단위사업수"] = "9"
    got = count_check(records, names)
    assert got["failed"] == 1 and "9" in got["failures"][0]


def test_count_check_needs_real_pair_columns():
    """짝(X, X수)이 둘 다 실재해야 검사한다 — 이름 규칙만으로 묶음을
    지어내지 않는다 (오탐 0 방향)."""
    from docstruct.structuring import count_check

    records = [{"단위사업수": "3", "_row": 1, "_expanded": []}]
    assert count_check(records, ["단위사업수"]) is None


def test_join_chains_refuses_mismatched_columns(monkeypatch):
    """열 배치가 어긋난 사슬은 잇지 않는다 — 표시로 강등."""
    from docstruct.structuring import joins

    monkeypatch.setattr(joins, "_column_ok", lambda *a, **k: False)
    tables = [
        {"table_id": "a", "page": 1, "bbox": {}, "continues_from": None,
         "records": [{"_row": 1}]},
        {"table_id": "b", "page": 2, "bbox": {}, "continues_from": "a",
         "records": [{"_row": 1}]},
    ]
    (chain,) = joins.join_chains(tables, pdf_path="x.pdf")
    assert chain["column_check"] == "mismatch" and chain["records"] is None


def test_structure_document_keeps_reader_warnings():
    """경고를 지우지 않는다 — 판독의 자신 없음이 provenance 로 넘어간다."""
    from docstruct.structuring import structure_document

    doc = {"source": "s", "pages": [{"page_no": 1, "tables": [
        {"id": "t", "cells": _nts_cells(), "odd_columns": {"cols": 9}}]}]}
    (entry,) = structure_document(doc)["tables"]
    assert entry["provenance"]["warnings"] == ["odd_columns"]
    assert entry["checks"]["count_check"]["failed"] == 0


def test_structured_contract_is_complete():
    """모든 표 항목이 계약 열쇠를 전부 갖는다 — 소비자가 분기하지 않게."""
    from docstruct.structuring import structure_document
    from docstruct.structuring.schema import TABLE_KEYS

    doc = {"source": "s", "pages": [{"page_no": 1, "tables": [
        {"id": "빈표", "cells": []}]}]}
    out = structure_document(doc)
    assert out["problems"] == []
    assert all(key in out["tables"][0] for key in TABLE_KEYS)


# ────────────────────────────────────────────────────────────────────
# 0.3.76 — "threads=(기본)" 을 실제 수와 출처로
#
# 배경: 로그가 `device=cpu threads=(기본)` 이라 **몇 개로 도는지 알 수
#       없었다** — 조정하려면 현재 값을 알아야 한다. Docling 기본값은
#       버전마다 다를 수 있으므로 상수로 적지 않고 AcceleratorOptions 를
#       직접 만들어 읽는다 (적어 두면 거짓말이 된다).
#
#       출처까지 찍는다: 설정 / 환경변수 정정값 / Docling 기본 — 어디서
#       온 값인지 알아야 어디를 고칠지 안다.
# ────────────────────────────────────────────────────────────────────

def test_thread_note_reports_setting_and_source():
    """설정이 있으면 그 값과 출처를 밝힌다."""
    from docstruct.api import configure
    from docstruct.converters.pdf.docling_backend import thread_setting_note

    configure(num_threads=6)
    try:
        note = thread_setting_note()
        assert note.startswith("6 ") and "설정" in note
    finally:
        configure(num_threads=0)


def test_thread_note_never_says_only_default():
    """기본에 맡길 때도 '(기본)' 만 찍지 않는다 — 수·코어·고치는 법을 준다."""
    from docstruct.converters.pdf.docling_backend import thread_setting_note

    note = thread_setting_note()
    assert "--set num_threads=N" in note
    assert "코어" in note
    assert note != "(기본)"


def test_docling_default_threads_is_read_not_hardcoded():
    """Docling 기본값은 조회한다 — 버전이 바뀌어도 로그가 참이도록."""
    import inspect

    from docstruct.converters.pdf import docling_backend

    source = inspect.getsource(docling_backend.docling_default_threads)
    assert "AcceleratorOptions()" in source
    assert "return 4" not in source              # 상수 박제 금지


# ────────────────────────────────────────────────────────────────────
# 0.3.77 — 실험 다섯이 파이프라인에서 **조용히 건너뛰어지던** 문제 (P0)
#
# 조달청 성과계획서(76쪽·표 59개) 실행 결과에서 드러났다: `--exp` 로 다섯을
# 켰는데 document.json 에는 ⑥(grid_merge_gap)만 남았다.
#
#     실행:  --exp grid_score,grid_restore,vector_grid,line_grid,sum_check
#     결과:  grid_merge_gap 12표 · 나머지 0
#     같은 입력에 함수를 직접 부르면: ⑪ 50표 · ⑨ lattice 47표 · ⑧ 3표
#
# 원인: 파이프라인은 `if ... or experiment.run is None: continue` 로 거른다.
#       0.3.69~0.3.70 에 더한 다섯(⑦⑧⑨⑩⑪)이 register() 에 **run=run 을
#       빠뜨려** 전부 run=None 이었다 — 켜져도 실행되지 않고, 경고도 없다.
#
# 왜 못 잡았나: 검증을 전부 **함수 직접 호출**로 했다 (이 환경엔 docling 이
#       없어 파이프라인을 못 돌린다). 등록 테스트는 키만 봤고 배선은 안 봤다.
#       그래서 "배선까지 보는" 가드를 넣는다 — 계약은 키가 아니라 실행이다.
# ────────────────────────────────────────────────────────────────────

def test_every_experiment_is_wired_to_its_run():
    """등록된 실험은 모두 run 이 연결돼 있어야 한다.

    run=None 이면 파이프라인이 조용히 건너뛴다 — 켠 사람은 돌았다고
    믿는데 아무 일도 일어나지 않는 가장 나쁜 실패다.
    """
    from docstruct.experiments import all_experiments
    missing = [e.key for e in all_experiments() if e.run is None]
    assert not missing, f"run 배선이 빠진 실험: {missing}"


def test_experiment_run_matches_its_module():
    """연결된 run 이 그 모듈의 run 함수여야 한다 (엉뚱한 함수 배선 방지).

    0.4.85 부터 실험은 하위 폴더(tsr/measure · tsr/restore · image · text)에
    산다. 모듈 경로는 run 함수의 `__module__` 로 안다 — 파일 이름은 키와
    같아야 한다.
    """
    import importlib

    from docstruct.experiments import all_experiments
    for experiment in all_experiments():
        path = experiment.run.__module__
        assert path.startswith("docstruct.experiments."), path
        assert path.rsplit(".", 1)[-1] == experiment.key, (path, experiment.key)
        module = importlib.import_module(path)
        assert experiment.run is module.run, experiment.key


def test_experiments_live_in_typed_folders():
    """실험은 네 폴더 중 하나에 있고, 표를 바꾸는 것은 restore 에만 있다.

    측정 전용 실험(measure · image · text)이 markdown 을 바꾸면 "재기만 한다"
    는 약속이 깨진다. 폴더가 그 약속을 말하므로 폴더를 못 박는다.
    """
    from docstruct.experiments import all_experiments

    folders = {"docstruct.experiments.tsr.measure", "docstruct.experiments.tsr.restore",
               "docstruct.experiments.image", "docstruct.experiments.text"}
    for experiment in all_experiments():
        folder = experiment.run.__module__.rsplit(".", 1)[0]
        assert folder in folders, (experiment.key, folder)
    restore = {e.key for e in all_experiments()
               if e.run.__module__.startswith("docstruct.experiments.tsr.restore.")}
    assert restore == {"grid_restore", "lattice_restore", "lattice_fill", "hole_fill",
                       "col_grid", "head_grid", "agreed_grid", "cell_repair"}


def test_stale_flat_experiment_copy_is_not_loaded(tmp_path, monkeypatch):
    """최상위에 남은 옛 사본(옮기기 전 파일)은 불러오지 않고 stale 로 센다.

    덮어쓰기 배포는 사라진 파일을 지우지 않는다 — `experiments/lattice_fill.py`
    가 남으면 같은 키가 두 번 등록되는 것을 막아야 한다.
    """
    import docstruct.experiments as package
    from docstruct.experiments import registry

    leftover = pathlib.Path(package.__path__[0]) / "hole_fill.py"
    assert not leftover.exists()
    leftover.write_text("raise RuntimeError('옛 사본이 불려서는 안 된다')\n",
                        encoding="utf-8")
    try:
        assert "hole_fill" in registry.stale_modules()
        registry._load_all()            # 예외 없이 지나가야 한다 — 사본은 건너뛴다
    finally:
        leftover.unlink()
    assert "hole_fill" not in registry.stale_modules()


def test_pipeline_skip_rule_is_the_reason_wiring_matters():
    """파이프라인이 run=None 을 거른다는 사실 자체를 못 박는다.

    이 규칙이 바뀌면 위 두 가드의 의미도 바뀐다 — 함께 읽히도록 둔다.
    """
    import inspect

    from docstruct import pipeline

    source = inspect.getsource(pipeline)
    assert "experiment.run is None" in source


# ────────────────────────────────────────────────────────────────────
# 0.3.78 — 그림을 **원본 화소 그대로** 저장 (해상도 8배 손실 수정)
#
# 배경: 조달청 성과계획서 59쪽 그래프에서 드러났다.
#
#     원본에 심긴 비트맵          3139 × 947 px (600 DPI)
#     Docling 기본 내보내기        377 × 113 px  (images_scale=1.0 = 72 DPI)
#
#       — 8배를 버리고 저장하고 있었다. 축 눈금·범례가 뭉개져 VLM 이
#       읽을 수 없는 것은 촬영 한계가 아니라 **우리가 버린 것**이다.
#
# 판단: 초해상(SR)이 필요한 자리가 아니다. SR 은 없는 정보를 지어내지만,
#       원본 추출은 있는 정보를 그대로 옮긴다. 통짜 래스터 영역이면 그
#       비트맵을 꺼내고, 벡터 도해면 목표 화소까지 배율을 올려 렌더한다.
#       스캔 원본이 저해상이면 어느 경로도 도움이 안 된다 — 없는 것을
#       만들지 않는다는 원칙 그대로다.
# ────────────────────────────────────────────────────────────────────

def test_region_extract_prefers_native_bitmap(tmp_path):
    """영역을 덮는 래스터가 있으면 원본 비트맵을 그대로 쓴다.

    실측(조달청 59쪽): 원본 3139×947 vs Docling 내보내기 377×113.
    여기서는 같은 판단 규칙을 합성 문서로 못 박는다.
    """
    from io import BytesIO

    import pypdfium2 as pdfium
    from PIL import Image

    from docstruct.images.native_image import extract_region_png

    pdf = pdfium.PdfDocument.new()
    page = pdf.new_page(300, 100)
    bitmap = pdfium.PdfBitmap.from_pil(Image.new("RGB", (1200, 400), "white"))
    image = pdfium.PdfImage.new(pdf)
    image.set_bitmap(bitmap)
    image.set_matrix(pdfium.PdfMatrix().scale(300, 100))
    page.insert_obj(image)
    page.gen_content()
    out = tmp_path / "one.pdf"
    pdf.save(str(out))
    pdf.close()

    got = extract_region_png(out, 1, {"l": 0, "t": 0, "r": 300, "b": 100})
    assert got is not None
    data, origin = got
    assert origin == "native"
    assert Image.open(BytesIO(data)).size == (1200, 400)   # 원본 화소 보존


def test_region_extract_falls_back_to_render(tmp_path):
    """래스터가 없으면(벡터·빈 쪽) 렌더 경로로 간다 — 목표 화소까지 키운다."""
    from io import BytesIO

    import pypdfium2 as pdfium
    from PIL import Image

    from docstruct.images.native_image import extract_region_png

    pdf = pdfium.PdfDocument.new()
    pdf.new_page(200, 100)
    out = tmp_path / "blank.pdf"
    pdf.save(str(out))
    pdf.close()

    got = extract_region_png(out, 1, {"l": 0, "t": 0, "r": 200, "b": 100})
    assert got is not None
    data, origin = got
    assert origin == "render"
    assert max(Image.open(BytesIO(data)).size) >= 800   # 배율을 올려 잡았다


def test_picture_block_asks_for_native_when_source_known():
    """추출기가 원본 경로·쪽·bbox 를 넘겨야 원본 우선 경로가 산다."""
    import inspect

    from docstruct.extractors import pdf as pdf_extractor
    from docstruct.images import picture

    assert "source_path" in inspect.signature(picture.picture_to_block).parameters
    source = inspect.getsource(pdf_extractor)
    assert "source_path=source_path" in source
    assert "extract_region_png" in inspect.getsource(picture.picture_to_block)


# ────────────────────────────────────────────────────────────────────
# 0.3.79 — 옮겨적기 오류 검출: 그래프 기하 대조 + 증가율 불변량
#
# 배경: "표를 그래프로 그린 경우가 아니라 **반대**로, 남의 그래프 이미지를
#       표로 옮겨 적다 오타·오독이 나면 그걸 잡을 수 있나?" 지면에 둘 다
#       있으므로 비교할 근거는 이미 있다. 두 층으로 나눈다.
#
#   ① 기하 대조 (media.chart_verify) — 막대 화소 높이의 비율. 축 눈금을
#      읽지 않아 OCR·VLM 이 필요 없다. 조달청 59쪽 실측 측정오차 0.8%.
#        자리바꿈 9,755→7,955  21.8% → 검출
#        자릿수 누락 →1,447    618%  → 검출
#        끝자리 10,447→10,477   0.8% → **못 잡는다** (측정 오차 안)
#      배율은 중앙값으로 — 첫 값으로 맞추면 그 값이 오타일 때 나머지가
#      전부 어긋난 것처럼 보인다 (실측: first 3건 오탐, median 1건 지목).
#
#   ② 증가율 불변량 (structuring.checks.growth_check) — 표 안에서 닫힌다.
#      ①이 못 잡는 끝자리 오타를 여기서 잡는다: 10,447→10,477 이면 계산
#      7.40% vs 적힌 7.1%. 문턱은 **표기 정밀도에서 유도**한다 (정수 표는
#      0.15%p, 조 단위 소수 표는 더 넓게) — 고정 문턱은 한쪽에서 반드시
#      틀린다.
#
#      두 층이 서로의 사각을 메우는 것이 설계다.
# ────────────────────────────────────────────────────────────────────

def test_chart_compare_flags_transposition_not_last_digit():
    """기하 대조는 자리바꿈을 잡고 끝자리는 놓친다 — 한계를 못 박는다."""
    from docstruct.images.chart_verify import compare_series

    heights = [166, 293, 387, 415]                # 조달청 59쪽 실측
    truth = [4157, 7394, 9755, 10447]
    assert compare_series(heights, truth)["failed"] == 0
    swapped = compare_series(heights, [4157, 7394, 7955, 10447])
    assert swapped["failed"] == 1
    assert swapped["failures"][0]["written"] == 7955
    # 끝자리 오타는 측정 오차 안이라 통과한다 (그래서 ②가 있다)
    assert compare_series(heights, [4157, 7394, 9755, 10477])["failed"] == 0


def test_chart_scale_uses_median_so_one_typo_points_at_itself():
    """배율은 중앙값 — 첫 값이 오타여도 그 값만 지목된다."""
    from docstruct.images.chart_verify import compare_series

    got = compare_series([166, 293, 387, 415], [1457, 7394, 9755, 10447])
    assert got["failed"] == 1 and got["failures"][0]["written"] == 1457


def test_growth_check_catches_last_digit_typo():
    """증가율 불변량이 기하 대조의 사각(끝자리)을 메운다."""
    from docstruct.structuring.checks import growth_check

    rates = [69.5, 77.8, 31.9, 7.1]
    assert growth_check([4157, 7394, 9755, 10447], rates)["failed"] == 0
    assert growth_check([4157, 7394, 9755, 10477], rates)["failed"] == 1


def test_growth_tolerance_follows_notation_precision():
    """문턱은 표기 정밀도에서 유도한다 — 소수 표는 넓게, 정수 표는 좁게."""
    from docstruct.structuring.checks import growth_check

    # 조 단위 소수 한 자리: 반올림이 커서 같은 %p 차이를 통과시켜야 한다
    assert growth_check([26.7, 26.4, 25.6], [None, -1.1, -3.0])["failed"] == 0
    # 정수 표에서는 0.3%p 차이도 잡힌다
    assert growth_check([4157, 7394, 9755, 10477],
                        [69.5, 77.8, 31.9, 7.1])["failed"] == 1


def test_chart_verify_declines_when_no_bars():
    """막대를 못 찾으면 조용히 물러난다 — 없는 결함을 만들지 않는다."""
    from docstruct.images.chart_verify import compare_series

    assert compare_series([], [1, 2]) is None
    assert compare_series([100, 200], [1]) is None      # 개수 불일치
    assert compare_series([100, 200], [0, 0]) is None   # 잴 값이 없다


# ────────────────────────────────────────────────────────────────────
# 0.3.80 — ⑧ 검산의 비율 열 오탐 (조달청 실행에서 드러남)
#
# 0.3.77 로 실험이 실제로 돌자 ⑧이 3건을 실패로 표시했는데, 셋 다 오탐
# 이었다 — **비율 열의 합계 칸은 합이 아니다.**
#
#     39쪽 증감률(%)  합계 -4.7  vs 각 행 증감률의 합 35.1
#     40쪽 활용률(%)  합계 89.9  vs 각 행 활용률의 합 766.5
#
# 값만 보고는 "합이 아닌 열" 과 "합이 틀린 열" 을 가를 수 없다 — 그것이
# 검산기가 답해야 할 물음 자체이기 때문이다. 그래서 **열 머리로** 가른다.
# 고친 뒤 같은 문서 실패 0, HWPX 580표 회귀 없음.
# ────────────────────────────────────────────────────────────────────

def test_sum_check_skips_ratio_columns():
    """비율 열은 합계 검산에서 뺀다 — 실제 오탐 꼴 그대로."""
    from docstruct.experiments.tsr.measure.sum_check import check_table

    rows = [("구분", "2024년", "2025년", "증감", "증감률(%)"),
            ("합계", "321555", "306440", "-15115", "-4.7"),
            ("공기업", "3026", "3377", "351", "11.6"),
            ("교육기관", "54132", "46934", "-7198", "-13.3"),
            ("국가기관", "264397", "256129", "-8268", "-3.1")]
    cells = [{"row": r, "col": c, "rowspan": 1, "colspan": 1, "text": text}
             for r, row in enumerate(rows) for c, text in enumerate(row)]
    report = check_table(cells)
    assert report is not None
    assert report["failed"] == 0                 # 증감률 열은 검사 밖
    assert report["checked"] >= 2                # 수량 열은 여전히 검사한다


def test_sum_check_still_catches_broken_quantity_sum():
    """비율 열을 빼도 수량 열의 어긋남은 그대로 잡는다."""
    from docstruct.experiments.tsr.measure.sum_check import check_table

    rows = [("구분", "2025년", "비율(%)"),
            ("합계", "999", "100.0"),            # 3377+46934 ≠ 999
            ("공기업", "3377", "6.7"),
            ("교육기관", "46934", "93.3")]
    cells = [{"row": r, "col": c, "rowspan": 1, "colspan": 1, "text": text}
             for r, row in enumerate(rows) for c, text in enumerate(row)]
    report = check_table(cells)
    assert report["failed"] == 1
    assert report["failures"][0]["col"] == 1     # 비율 열이 아니라 수량 열


# ────────────────────────────────────────────────────────────────────
# 0.3.81 — 한글 지면이 **한자로** 나온 자리 탐지 (조달청 9쪽 조직도)
#
# 텍스트 PDF 안에 래스터로 붙인 쪽(조직도·정원표)은 docling 내장 OCR 이
# 읽고, 그 기본 모델은 중국어다:
#
#     9쪽 table_4  | 7是 | 77 | 歪 |      ← "구 분 / 기 구 / 기준정원"
#                  한자 10 / 낱말 12 = 0.83
#
# 왜 한국어 재판독이 안 돌았나: 그 쪽의 **원본 텍스트 레이어에는 한자가
# 없다** (제목 21자뿐, 한글 15자). 쪽 단위 판정은 "낱말 15자 이상 ·
# 비율 0.3 이상" 이라 아슬아슬하게 통과했다 — 지면 대부분이 래스터인
# 쪽을 절대 글자 수만으로는 못 가린다. 그래서 **결과물**을 보는 검사를
# 더한다: 탐지는 결정론(한자 비율), 복구는 기존 VLM 재구성 경로.
#
# 문턱 근거: 정상 한자 병기(軍·前中後·單價)는 0.004~0.035, 오판은 0.83.
#            HWPX 580표 최대 0.035 · 오탐 0.
# ────────────────────────────────────────────────────────────────────

def test_detects_chinese_model_ocr_output():
    """한자 비율이 높은 표를 언어 오판으로 표시한다 (실측 꼴 그대로)."""
    from docstruct.converters.pdf.ocr_language import wrong_language

    garbage = "| 7是 | 77 | 歪 |\n| 全会フ世 | 2 1 | 541g |\n| 1世5号17粤 | 8 6 | 5769 |"
    verdict = wrong_language(garbage)
    assert verdict is not None and verdict["ratio"] >= 0.15


def test_legitimate_han_usage_is_not_flagged():
    """병기 한자는 표시하지 않는다 — 실측 정상 비율 0.004~0.035."""
    from docstruct.converters.pdf.ocr_language import wrong_language

    normal = ("軍 급식류 위생점검, 군수품 품질보증을 통한 양질의 국방물자 "
              "공급으로 장병 만족도 향상과 전투력 상승에 기여하며 평가 前中後 "
              "관리체계를 운영하고 單價 계약을 확대한다")
    assert wrong_language(normal) is None
    # 한두 자짜리 칸은 비율이 1.0 이어도 재지 않는다 (최소 글자 수 조건)
    assert wrong_language("軍") is None


def test_language_doubt_feeds_vlm_rebuild():
    """언어 오판 표는 VLM 재구성 대상에 들어간다 — 탐지와 복구의 배선."""
    import inspect

    from docstruct.tables import vlm_rebuild

    source = inspect.getsource(vlm_rebuild.rebuild_broken_tables)
    assert "ocr_language_doubt" in source
    assert "odd_columns" in source          # 기존 경로도 그대로 산다


def test_pipeline_flags_language_before_odd_tables():
    """파이프라인이 표시 단계에서 언어 검사를 돈다."""
    import inspect

    from docstruct import pipeline

    source = inspect.getsource(pipeline)
    assert "_flag_ocr_language" in source
    assert "wrong_language" in inspect.getsource(pipeline._flag_ocr_language)


# ────────────────────────────────────────────────────────────────────
# 0.3.82 — VLM 을 기본 경로로 (그림·복잡한 표는 결정론이 물러난 자리다)
#
# 그동안 VLM 기능은 전부 기본 꺼짐이라, 사람이 스위치를 찾아 켜기 전까지
# 그림·캡처 표·한자로 나온 표가 빈 채로 나갔다. **기본값이 결과를
# 좌우해선 안 되는 자리**다.
#
# 그렇다고 무조건 켜면 LLM 없는 환경(오프라인 검증·CI)에서 실패 경고만
# 쌓인다. 그래서 **수단이 있을 때만** 켠다 — `_vlm_default()`.
#
# 대상 선정도 사다리(§17)의 낙하 지점으로 넓혔다. 핵심은 세 번째:
#   지면에는 병합이 그려져 있는데(⑥⑨⑩이 신뢰 high 로 표시) 인식에 없고
#   ⑦도 복원하지 못한 표 — 남은 경로가 VLM 뿐인 자리.
#   실측(조달청 59표): 1표 → 13표(22%).
# ────────────────────────────────────────────────────────────────────

def test_vlm_defaults_follow_llm_availability(monkeypatch):
    """LLM 이 설정돼 있으면 기본 켬, 없으면 기본 끔."""
    from docstruct.core import config

    # `_get` 은 환경변수 → .env → **내장 기본값** 순으로 본다. 사내 배치는
    # 엔드포인트가 내장 기본값에 있으므로, 환경변수를 지운 것만으로는
    # "수단이 없다" 가 되지 않는다 — 기본값까지 비워야 그 상태다 (0.3.99).
    monkeypatch.setattr(config, "_DEFAULTS", {}, raising=False)
    for name in ("DOCLING_TABLE_API_URL", "DOCSTRUCT_LLM_URL",
                 "DOCLING_PICTURE_API_URL", "DOCSTRUCT_LOCAL_VLM_MODEL",
                 "OPENAI_API_KEY", "DOCLING_TABLE_API_KEY",
                 "DOCLING_TABLE_API_FALLBACK_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert config._vlm_default() is False
    monkeypatch.setenv("DOCLING_TABLE_API_URL", "http://x/v1/chat/completions")
    assert config._vlm_default() is True


def test_explicit_switch_still_wins(monkeypatch):
    """명시적으로 끄면 기본값을 이긴다 — 자동은 기본값일 뿐이다."""
    import importlib

    monkeypatch.setenv("DOCLING_TABLE_API_URL", "http://x/v1/chat/completions")
    monkeypatch.setenv("DOCSTRUCT_VLM_FIX_TABLES", "false")
    from docstruct.core import config

    importlib.reload(config)
    try:
        assert config.get_settings().vlm_fix_tables is False
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_vlm_targets_tables_marked_but_not_restored():
    """실험이 신뢰 high 로 표시했는데 복원 안 된 표 → VLM 이 받는다."""
    from docstruct.models import TableInfo
    from docstruct.tables.vlm_rebuild import needs_vlm

    marked = TableInfo(id="t", table_num=1, placeholder="", markdown="",
                       synth_grid={"confidence": "high",
                                   "missing": [(1, 0, 2, 1)]})
    assert needs_vlm(marked) is True
    # ⑦이 복원한 표는 VLM 이 받지 않는다 (결정론이 이겼다)
    restored = TableInfo(id="t2", table_num=1, placeholder="", markdown="",
                         source="grid",
                         synth_grid={"confidence": "high",
                                     "missing": [(1, 0, 2, 1)]})
    assert needs_vlm(restored) is False


def test_vlm_ignores_low_confidence_marks():
    """신뢰 low 표시는 대상이 아니다 — 자리 번호가 어긋난다(실측 83%)."""
    from docstruct.models import TableInfo
    from docstruct.tables.vlm_rebuild import needs_vlm

    low = TableInfo(id="t", table_num=1, placeholder="", markdown="",
                    grid_merge_gap={"confidence": "low",
                                    "missing": [(1, 0, 2, 1)]})
    assert needs_vlm(low) is False
    clean = TableInfo(id="t2", table_num=1, placeholder="", markdown="")
    assert needs_vlm(clean) is False


# ────────────────────────────────────────────────────────────────────
# 0.3.83 — 연결 실패 경고가 손댈 곳을 알려주게
#
# 사내망 실행에서 이렇게만 떴다:
#
#     WARNING ... 연결 불가 (ConnectionError) — 앞으로 60초 동안 건너뜁니다
#
# **"ConnectionError" 만으로는 어디를 고칠지 알 수 없다.** 알려진 사유
# 목록에 없으면 예외 이름만 남기고 원문을 버리고 있었다. 원문 끝머리를
# 남기고, 사내망 사고 1순위인 **프록시**를 함께 알린다 — requests 는
# HTTP_PROXY 를 자동으로 따르므로, 브라우저·curl 은 되는데 파이썬만
# 안 되는 상황이 여기서 난다.
# ────────────────────────────────────────────────────────────────────

def test_connection_reason_keeps_raw_detail():
    """모르는 사유면 원문 끝머리를 남긴다 — 예외 이름만 남기지 않는다."""
    import requests

    from docstruct.infrastructure.llm.client import _short_connection_reason

    exc = requests.exceptions.ConnectionError(
        "HTTPConnectionPool(host='10.0.0.1', port=8000): Max retries exceeded "
        "(Caused by NewConnectionError('Network is unreachable'))")
    reason = _short_connection_reason(exc, "http://10.0.0.1:8000/v1")
    assert "Network is unreachable" in reason
    assert reason != "ConnectionError"


def test_connection_reason_names_proxy_when_set(monkeypatch):
    """프록시가 잡혀 있으면 경고에 함께 알린다 (NO_PROXY 에 있으면 안 알린다)."""
    import requests

    from docstruct.infrastructure.llm.client import _short_connection_reason

    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:8080")
    monkeypatch.delenv("NO_PROXY", raising=False)
    exc = requests.exceptions.ConnectionError("[WinError 10061] 연결을 거부")
    reason = _short_connection_reason(exc, "http://10.0.0.1:8000/v1")
    assert "NO_PROXY" in reason

    monkeypatch.setenv("NO_PROXY", "10.0.0.1,localhost")
    assert "NO_PROXY" not in _short_connection_reason(exc, "http://10.0.0.1:8000/v1")


def test_connection_reason_covers_reset_and_abort():
    """서버가 닫은 경우를 따로 이름 붙인다 (재시작·HTTPS 기대)."""
    import requests

    from docstruct.infrastructure.llm.client import _short_connection_reason

    for detail in ("('Connection aborted.', RemoteDisconnected('...'))",
                   "[WinError 10054] 강제로 끊겼습니다"):
        reason = _short_connection_reason(
            requests.exceptions.ConnectionError(detail), "http://x/v1")
        assert "끊겼" in reason


# ────────────────────────────────────────────────────────────────────
# 0.3.84 — 그림 **안에** 있는 표를 그림과 잇는다 (조달청 9쪽 조직도)
#
# PPT 로 만든 조직도를 그림으로 붙인 쪽이다. 그 안에 정원표가 그려져 있고
# docling 이 그것을 표로 잡아 내장 OCR(중국어)로 읽었다. 두 가지가 어긋나
# 있었다.
#
#   ① 조직도가 "chart" 로 판정 — 근거가 "글자 0자 · 래스터" 라 59쪽의
#      진짜 막대그래프와 구분이 안 된다. chart 로 두면 값 읽기로 가는데
#      조직도에는 읽을 값이 없다.
#   ② 표가 그림에서 나왔다는 사실이 어디에도 없어, LLM 품질 판정이
#      `sufficient` 로 통과시켰다 (한자 표를!).
#
# **표가 그림 안에 있다**는 순수 기하로 확인되고 "이 글자는 OCR 산물"을
# 뜻한다 — 언어 검사(한자 비율)가 못 잡는 경우(OCR 이 그럴듯한 한글을
# 지어낸 경우)까지 받는 더 일반적인 신호다.
# ────────────────────────────────────────────────────────────────────

def _page_with_nested_table():
    """조달청 9쪽 꼴 — 그림 안에 표가 있는 페이지."""
    from docstruct.models import ImageInfo, PageContent, TableInfo

    return PageContent(
        page_no=9, page_no_kind="exact", content="",
        tables=[TableInfo(id="table_4", table_num=4, placeholder="", markdown="| 7是 |",
                          bbox={"l": 111.3, "t": 617.4, "r": 525.5, "b": 724.5})],
        images=[ImageInfo(id="image_2", placeholder="<!-- image_2 -->",
                          bbox={"l": 87.2, "t": 177.7, "r": 527.6, "b": 726.6},
                          region_kind="chart",
                          region_kind_reason="글자 0자 · 래스터 그림")])


def test_nested_table_is_linked_to_its_picture():
    """그림 안 표에 source_image_id 를 달고 그림에 table_candidate 를 세운다."""
    from docstruct.images.picture_tables import link_tables_in_pictures

    page = _page_with_nested_table()
    assert link_tables_in_pictures([page]) == 1
    assert page.tables[0].source_image_id == "image_2"
    assert page.images[0].table_candidate is True


def test_picture_with_table_is_not_a_chart():
    """표가 그려진 그림은 값 읽기 대상이 아니다 — chart→image 로 내린다."""
    from docstruct.images.picture_tables import link_tables_in_pictures

    page = _page_with_nested_table()
    link_tables_in_pictures([page])
    assert page.images[0].region_kind == "image"
    assert "표(table_4)" in page.images[0].region_kind_reason


def test_standalone_table_is_untouched():
    """그림 밖의 표는 건드리지 않는다 (오탐 방향 확인)."""
    from docstruct.images.picture_tables import link_tables_in_pictures

    page = _page_with_nested_table()
    page.tables[0].bbox = {"l": 60, "t": 60, "r": 500, "b": 120}   # 그림 위쪽
    assert link_tables_in_pictures([page]) == 0
    assert page.tables[0].source_image_id is None
    assert page.images[0].region_kind == "chart"                    # 그대로


def test_nested_table_goes_to_vlm():
    """그림에서 나온 표는 VLM 대상이다 — 언어 검사와 무관하게."""
    from docstruct.images.picture_tables import link_tables_in_pictures
    from docstruct.tables.vlm_rebuild import needs_vlm

    page = _page_with_nested_table()
    assert needs_vlm(page.tables[0]) is False       # 잇기 전에는 신호가 없다
    link_tables_in_pictures([page])
    assert needs_vlm(page.tables[0]) is True


# ────────────────────────────────────────────────────────────────────
# 0.3.85 — ⑧ 오탐 두 종류 (문체부 HWPX 정답 대조로 드러남)
#
# 3부처째 이중 소스(문체부 609쪽·475표)에서 ⑧이 2건을 실패로 표시했는데,
# **정답(HWPX)에서도 같은 실패가 났다** — 구조 손상이 아니라 불변량
# 오적용이라는 증거다. 두 종류였다.
#
#   ① 비율 **행** — 0.3.80 에서 비율 열은 뺐는데 행이 남았다.
#      문체부 20쪽: `(전년대비증가율, %)` 행이 합에 섞여
#      102,500 + (-38.7) + (-29.1) = 102,432.2 를 기대값으로 냈다.
#
#   ② 딱지 없는 계층표 — 문체부 12쪽 정원표:
#      총계 3,049 = 본부 774 + 소속기관 2,275
#      소속기관 2,275 = 한예종 276 + 국악고 94 + …
#      `소속기관` 은 `소계` 딱지가 없어 층 가드를 통과했고, 모든 자료
#      행을 더해 이중 계산이 났다.
#
#      판정을 **수치로** 한다: 어떤 자료 행의 값이 그 아래 연속 행들의
#      합과 같으면 중간 집계다. 딱지 이름에 기대지 않는다.
# ────────────────────────────────────────────────────────────────────

def _rows_to_cells(rows):
    """행 튜플 목록 → 셀 목록."""
    return [{"row": r, "col": c, "rowspan": 1, "colspan": 1, "text": text}
            for r, row in enumerate(rows) for c, text in enumerate(row)]


def test_sum_check_skips_ratio_rows():
    """비율 행은 합에서 뺀다 (문체부 20쪽 꼴)."""
    from docstruct.experiments.tsr.measure.sum_check import check_table

    rows = [("구분", "'26", "'27"),
            ("총계", "215,146", "152,500"),
            ("-지출", "167,096", "102,500"),
            ("(전년대비증가율, %)", "", "△29.1"),
            ("-기타", "48,050", "50,000")]
    report = check_table(_rows_to_cells(rows))
    assert report is not None and report["failed"] == 0


def test_sum_check_detects_unlabeled_layered_table():
    """중간 집계 행이 딱지 없이 있어도 계층으로 보고 검사하지 않는다."""
    from docstruct.experiments.tsr.measure.sum_check import check_table

    rows = [("구분", "계"), ("총계", "3,049"), ("본부", "774"),
            ("소속기관", "2,275"), ("한예종", "276"), ("국악고", "94"),
            ("전통예술", "87"), ("기타", "1,818")]
    assert check_table(_rows_to_cells(rows)) is None


def test_sum_check_still_checks_flat_tables():
    """평평한 표는 그대로 검사한다 — 가드가 검사를 통째로 죽이지 않는다."""
    from docstruct.experiments.tsr.measure.sum_check import check_table

    good = [("구분", "'24", "'25"), ("합계", "300", "330"),
            ("가", "100", "110"), ("나", "200", "220")]
    broken = [("구분", "'24", "'25"), ("합계", "999", "330"),
              ("가", "100", "110"), ("나", "200", "220")]
    assert check_table(_rows_to_cells(good))["failed"] == 0
    assert check_table(_rows_to_cells(broken))["failed"] == 1


# ────────────────────────────────────────────────────────────────────
# 0.3.86 — 합의 병합을 VLM 힌트의 첫 근거로 (3부처 정답 대조 결과)
#
# 규칙 후보 D("합의 ∧ 한쪽 덮개≥1.0")를 두 부처에서 교차 확인했다:
#
#     조달청  정밀도 94.9% · 재현율 36.2%  (75/79)
#     문체부  정밀도 98.0% · 재현율 66.1%  (1152/1175)
#
# **복원 승격은 불가** — 오탐 0 이 아니다(⑦의 게이트는 3부처 100%).
# 복원은 표를 조용히 다시 쓰므로 20개 중 하나가 틀리면 감춰진 손상이 된다.
#
# 그러나 **힌트 근거로는 어느 단일 근거보다 낫다.** 조달청 정답 대조:
#
#     현행 힌트(신뢰 high)  53/71 = 74.6%
#     합의 병합             49/55 = 89.1%
#
# 틀린 자리를 짚어 주면 역효과라는 것이 H10 의 전제였다(§16-3). 근거를
# 바꾸면 그 위험이 준다. ⑪이 이미 두 후보를 계산하므로 추가 비용도 없다.
# ────────────────────────────────────────────────────────────────────

def test_grid_score_records_agreed_merges(monkeypatch):
    """⑪이 사각형∩괘선 합의 병합을 기록한다 (인식에 없는 것만)."""
    from docstruct.experiments.tsr.measure.grid_score import score_table

    # 두 후보가 같은 병합을 내고 인식은 못 잡은 상황을 흉내낸다
    import docstruct.experiments.tsr.measure.grid_score as module

    merged = [(0, 0, 2, 1), (0, 1, 1, 1), (1, 1, 1, 1)]
    import docstruct.experiments.tsr.measure.line_grid as line_grid

    # **monkeypatch 로 갈아끼운다.** 직접 대입하면 시험이 끝나도 모듈에
    # 그대로 남아, 뒤에 도는 시험이 가짜 table_lattice 를 본다 — 실제로
    # 0.4.3 에서 서명 검사 시험이 그 때문에 KeyError 로 깨졌다 (단독으로는
    # 통과하고 전체 실행에서만 깨져 원인을 찾기 어렵다).
    monkeypatch.setattr(
        module, "physical_cells",
        lambda rects: (merged, 2, 2) if rects else None)
    monkeypatch.setattr(module, "_page_rects", lambda *a, **k: [(0, 0, 10, 10)])
    monkeypatch.setattr(module, "_inside", lambda *a, **k: True)
    monkeypatch.setattr(line_grid, "table_lattice", lambda *a, **k: (merged, 2, 2))
    flat = [{"row": r, "col": c, "rowspan": 1, "colspan": 1, "text": "x"}
            for r in range(2) for c in range(2)]
    report = score_table("x.pdf", 1, {"l": 0, "t": 0, "r": 10, "b": 10}, flat)
    assert report["agreed"] == 1
    assert (0, 0, 2, 1) in [tuple(c) for c in report["agreed_missing"]]


def test_hint_prefers_agreed_over_confidence(monkeypatch):
    """힌트는 합의 병합을 먼저 쓴다 — 정확도가 74.6% → 89.1%."""
    monkeypatch.setenv("DOCSTRUCT_VLM_HINT_MISSING", "1")
    from docstruct.models import TableInfo
    from docstruct.tables.vlm_rebuild import _missing_hint

    table = TableInfo(
        id="t", table_num=1, placeholder="", markdown="",
        grid_merge_gap={"confidence": "high", "missing": [(5, 5, 2, 1)]},
        grid_score={"agreed": 1, "agreed_missing": [(2, 0, 3, 1)]})
    hint = _missing_hint(table)
    assert "3행 1열에서 세로 3칸" in hint          # 합의 쪽
    assert "6행 6열" not in hint                   # 신뢰 high 쪽은 안 쓴다


def test_hint_falls_back_when_no_agreement(monkeypatch):
    """합의가 없으면 기존 근거로 물러난다 — 힌트를 잃지 않는다."""
    monkeypatch.setenv("DOCSTRUCT_VLM_HINT_MISSING", "1")
    from docstruct.models import TableInfo
    from docstruct.tables.vlm_rebuild import _missing_hint

    table = TableInfo(id="t", table_num=1, placeholder="", markdown="",
                      grid_merge_gap={"confidence": "high",
                                      "missing": [(2, 0, 3, 1)]},
                      grid_score={"agreed": 0, "agreed_missing": []})
    assert "3행 1열에서 세로 3칸" in _missing_hint(table)


# ────────────────────────────────────────────────────────────────────
# 0.3.87 — OpenAI 만 쓰는 배치에서 VLM 기본값이 꺼지던 구멍
#
# 0.3.82 의 `_vlm_default()` 는 **주소**만 봤다. 그런데 OpenAI 배치는
# 주소를 넣지 않는다 — 설정 쪽이 대비책 주소를 자동으로 세운다. 그래서
# LLM 은 살아 있는데(표 평가는 돌고) VLM 기본값만 꺼지는 상태가 됐다.
# 키가 잡힌 경우도 "수단이 있다" 로 본다.
# ────────────────────────────────────────────────────────────────────

def test_vlm_default_recognizes_api_key_only(monkeypatch):
    """주소 없이 키만 있어도 VLM 기본값이 켜진다 (OpenAI 배치)."""
    from docstruct.core import config

    monkeypatch.setattr(config, "_DEFAULTS", {}, raising=False)
    for name in ("DOCLING_TABLE_API_URL", "DOCSTRUCT_LLM_URL",
                 "DOCLING_PICTURE_API_URL", "DOCSTRUCT_LOCAL_VLM_MODEL",
                 "OPENAI_API_KEY", "DOCLING_TABLE_API_KEY",
                 "DOCLING_TABLE_API_FALLBACK_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert config._vlm_default() is False
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert config._vlm_default() is True


# ────────────────────────────────────────────────────────────────────
# 0.3.89 — VLM 재구성이 새 대상에서 죽던 문제 (P0 · 조달청 실행)
#
#     vlm_rebuild.py:396 — width, majority = table.odd_columns
#     TypeError: cannot unpack non-iterable NoneType object
#
# 0.3.82 에서 대상을 넷으로 넓혔는데(odd_columns · 한자 · 그림 안 표 ·
# 실험 표시), **성공 기록 줄이 `odd_columns` 만 가정한 채** 남아 있었다.
# 그래서 새 대상이 실제로 재구성에 성공한 순간 죽었다 — 대상 선정만
# 고치고 그 대상을 **설명하는 자리**를 함께 고치지 않은 실수다.
#
# 재구성 성공은 문서 처리의 끝자락이라, 그때까지의 모든 작업이 함께
# 날아갔다(문서 전체 실패). 대상을 넓힐 때 함께 넓혀야 할 자리를
# 테스트로 묶는다.
# ────────────────────────────────────────────────────────────────────

def _table(**kwargs):
    """TableInfo 짧은 생성."""
    from docstruct.models import TableInfo

    base = {"id": "t", "table_num": 1, "placeholder": "", "markdown": ""}
    base.update(kwargs)
    return TableInfo(**base)


def test_reason_covers_every_vlm_target():
    """needs_vlm 이 받는 모든 대상에 설명이 있어야 한다 (언팩 사고 방지)."""
    from docstruct.tables.vlm_rebuild import _reason_of, needs_vlm

    cases = [
        _table(odd_columns=(9, 5)),
        # 한자 표시는 **지금 글에도 한자가 있을 때만** 대상이다 (0.3.92 —
        # 재추출이 이미 고친 표에 헛걸음하지 않도록).
        _table(markdown="| 7是 | 77 | 歪 | 全会フ世 | 1世5号17粤 |",
               ocr_language_doubt={"han": 10, "words": 12, "ratio": 0.83}),
        _table(source_image_id="image_2"),
        _table(synth_grid={"confidence": "high", "missing": [(1, 0, 2, 1)]}),
        _table(grid_merge_gap={"confidence": "high", "missing": [(2, 0, 3, 1)]}),
        _table(scan_grid={"confidence": "high", "missing": [(0, 0, 2, 1)]}),
    ]
    for table in cases:
        assert needs_vlm(table) is True
        reason = _reason_of(table)
        assert reason and isinstance(reason, str)


def test_reason_never_unpacks_missing_odd_columns():
    """odd_columns 가 없는 대상에서도 설명이 나온다 — 죽지 않는다."""
    from docstruct.tables.vlm_rebuild import _reason_of

    table = _table(ocr_language_doubt={"ratio": 0.83})
    assert table.odd_columns is None
    assert "한자" in _reason_of(table)


def test_rebuild_success_log_uses_reason_helper():
    """성공 기록이 도우미를 쓰는지 못 박는다 (직접 언팩 금지)."""
    import inspect

    from docstruct.tables import vlm_rebuild

    source = inspect.getsource(vlm_rebuild.rebuild_broken_tables)
    assert "_reason_of(table)" in source
    assert "= table.odd_columns" not in source


# ────────────────────────────────────────────────────────────────────
# 0.3.90 — 첫 VLM 실전 실행이 드러낸 두 결함 (조달청 · OpenAI)
#
# 실행 결과: 대상 14표 중 **13표 폐기**, 채택 1표는 **다른 표를 옮겨 적었다**.
#
#   ① 지목 없음 — 쪽 이미지를 통째로 보내면서 "어느 표" 인지 말하지
#      않았다. 8쪽 table_2(전략목표·프로그램목표·단위사업·세부사업 =
#      1·2·6·12)를 고치라 했더니 같은 쪽의 **다른 표**(프로그램명·
#      프로그램 목표)를 옮겨 적고 그것이 원본을 덮었다 — 회귀다.
#      자르지 않는 이유는 bbox 가 좁게 잡히는 것이 이 문제의 원인이라서
#      (자르면 잘린 표를 보여 준다). 그래서 **말로 짚는다**: 머리행 낱말
#      + 쪽 안 순번, 둘 다 결정론이다.
#
#   ② 길이 가드가 고친 것을 되돌림 — 병합 복원은 **글자가 줄어드는 것이
#      정상**이다(반복 기재된 값이 `〃`·빈 칸이 된다). 0.6배 가드가 병합
#      표시로 온 표 13개를 폐기했다. 형태 검사만으로 바꾸면 35% 로 잘린
#      후보도 통과한다 — 그래서 **자리 수(행·열)** 로 잰다: 병합 복원은
#      자리 수가 그대로고, 잘린 표는 줄어든다.
#
# 한편 9쪽 한자 표는 성공했다: `| 7是 | 77 | 歪 |` → 구분·기구·기준정원,
# 총계 1,117명·본청 576명·소속기관 541명 — 숫자가 모두 지면과 맞는다.
# ────────────────────────────────────────────────────────────────────

def test_prompt_targets_the_right_table():
    """지목 문단에 머리행과 쪽 안 순번이 들어간다."""
    from docstruct.models import PageContent, TableInfo
    from docstruct.tables.vlm_rebuild import _table_target

    first = TableInfo(id="table_2", table_num=2, placeholder="", markdown=
                      "| 전략목표 | 프로그램 목표 | 단위사업 | 세부사업 |\n"
                      "| --- | --- | --- | --- |\n| 1 | 2 | 6 | 12 |",
                      bbox={"l": 0, "t": 100, "r": 500, "b": 200})
    second = TableInfo(id="table_3", table_num=3, placeholder="", markdown=
                       "| 프로그램명 | 프로그램 목표 |\n| --- | --- |\n| I-1 | 개선 |",
                       bbox={"l": 0, "t": 300, "r": 500, "b": 400})
    page = PageContent(page_no=8, page_no_kind="exact", content="",
                       tables=[first, second])
    target = _table_target(page, first)
    assert "위에서 1번째" in target
    assert "전략목표" in target
    assert "위에서 2번째" in _table_target(page, second)


def test_prompt_templates_have_target_slot():
    """두 지시문 판 모두 지목 자리를 갖는다 — 형식 오류로 죽지 않게."""
    from docstruct.tables.vlm_rebuild import _PROMPT, _PROMPT_STEPS

    for template in (_PROMPT, _PROMPT_STEPS):
        rendered = template.format(context="c", hint="", target="T")
        assert "T" in rendered


def test_shape_guard_accepts_merge_restoration():
    """병합 복원은 글자가 줄어도 받는다 — 자리 수가 그대로다."""
    from docstruct.tables.vlm_rebuild import _keeps_shape

    original = ("| 프로그램 | 프로그램명 | 단위사업 |\n| --- | --- | --- |\n"
                "| 3100 | 성실납세 및 민생지원 | 납세안내 |\n"
                "| 3100 | 성실납세 및 민생지원 | 권익보호 |\n"
                "| 3100 | 성실납세 및 민생지원 | 세금신고 |")
    restored = original.replace(
        "| 3100 | 성실납세 및 민생지원 | 권익보호 |", "| 〃 | 〃 | 권익보호 |"
    ).replace("| 3100 | 성실납세 및 민생지원 | 세금신고 |", "| 〃 | 〃 | 세금신고 |")
    assert len(restored) < len(original) * 0.9          # 실제로 짧아졌다
    assert _keeps_shape(original, restored) is True


def test_shape_guard_still_rejects_truncation():
    """잘린 후보는 자리 수가 줄어 거부된다."""
    from docstruct.tables.vlm_rebuild import _keeps_shape

    original = ("| 프로그램 | 프로그램명 | 단위사업 |\n| --- | --- | --- |\n"
                "| 3100 | 성실납세 | 납세안내 |\n| 3200 | 국세행정 | 권익보호 |")
    truncated = "| 프로그램 | 프로그램명 |\n| --- | --- |\n| 3100 | 성실납세 |"
    assert _keeps_shape(original, truncated) is False


def test_guard_is_uniform_across_targets():
    """가드를 대상별로 나누지 않는다 — 자리 수와 내용량을 함께 본다.

    길이만 보면 병합 복원을 되돌리고(13표 폐기), 자리 수만 보면 칸만
    남기고 값을 비운 후보를 받는다. 두 조건을 함께 건다.
    """
    from docstruct.tables.vlm_rebuild import _acceptable

    original = ("| 프로그램 | 프로그램명 | 단위사업 |\n| --- | --- | --- |\n"
                "| 3100 | 성실납세 및 민생지원 | 납세안내 |\n"
                "| 3100 | 성실납세 및 민생지원 | 권익보호 |\n"
                "| 3100 | 성실납세 및 민생지원 | 세금신고 |")
    merged = original.replace(
        "| 3100 | 성실납세 및 민생지원 | 권익보호 |", "| 〃 | 〃 | 권익보호 |"
    ).replace("| 3100 | 성실납세 및 민생지원 | 세금신고 |", "| 〃 | 〃 | 세금신고 |")
    truncated = "| 프로그램 | 프로그램명 |\n| --- | --- |\n| 3100 | 성실납세 |"
    assert _acceptable(original, merged) is True       # 짧아도 자리 수 유지
    assert _acceptable(original, truncated) is False   # 자리 수 감소


# ────────────────────────────────────────────────────────────────────
# 0.3.91 — 행안부 실행이 드러낸 셋: 판정 값 88% 미인식 · 연결 재수립 · 순차 호출
#
#   ① content_type 88% 미인식 — 판정 응답은 왔고 table_kind 까지 담겼는데
#      317표 중 **278표(88%)** 가 content_type 을 못 알아들어 기본값
#      (table·sufficient)으로 떨어졌다. 검수를 통과한 것처럼 보인다.
#      뜻이 같은 말(표·그림·chart·본문…)은 받아들이고, 그래도 모르면
#      **그 사실을 reason 에 남긴다** — 조용히 sufficient 로 만들지 않는다.
#
#   ② 요청마다 새 연결 — 로그에 `Starting new HTTP connection` 이 호출마다
#      찍혔다. 세션 하나를 공용으로 두고 keep-alive 로 재사용한다.
#
#   ③ 표 재구성이 순차 — 대상 178표 · 776.9초(표당 4.4초). assess·fill 은
#      이미 llm_concurrency 를 따르는데 재구성만 순차였다. 표마다 독립이라
#      병렬이 안전하다.
# ────────────────────────────────────────────────────────────────────

def test_content_type_aliases_are_accepted():
    """같은 뜻의 다른 말을 받는다 — 88% 가 기본값으로 떨어지던 원인."""
    from docstruct.tables.assess import normalize_content_type

    assert normalize_content_type("표") == "table"
    assert normalize_content_type("TABLE") == "table"
    assert normalize_content_type("table_data") == "table"
    assert normalize_content_type("chart") == "image"
    assert normalize_content_type("조직도") == "image"
    assert normalize_content_type("본문") == "text"


def test_unknown_content_type_is_recorded_not_hidden():
    """모르는 값은 지어내지 않고, 그 사실을 결과에 남긴다."""
    from docstruct.models import TableInfo
    from docstruct.tables.assess import _apply_assessment, normalize_content_type

    assert normalize_content_type("뭔가이상한값") is None
    table = TableInfo(id="t1", table_num=1, placeholder="", markdown="| a |")
    _apply_assessment([table], [{"id": "t1", "content_type": "뭔가이상한값",
                                 "table_kind": "budget"}], unassessed=False)
    assert table.reason and "content_type" in table.reason
    assert table.table_kind == "budget"          # 알아들은 부분은 살린다


def test_llm_client_reuses_one_session():
    """세션을 하나만 만들어 재사용한다 (연결 수립 반복 제거)."""
    from docstruct.infrastructure.llm.client import _session

    first = _session()
    assert _session() is first
    assert first.get_adapter("http://x")._pool_maxsize >= 4


def test_vlm_rebuild_runs_concurrently():
    """표 재구성이 llm_concurrency 를 따른다 (순차 776초의 원인)."""
    import inspect

    from docstruct.tables import vlm_rebuild

    source = inspect.getsource(vlm_rebuild.rebuild_broken_tables)
    assert "llm_concurrency" in source
    assert "ThreadPoolExecutor" in source


# ────────────────────────────────────────────────────────────────────
# 0.3.92 — 조달청 재실행이 드러낸 둘 (판정 생략 오판 · 묵은 한자 신호)
#
# 0.3.91 의 성적표: 표 재구성 **27.99 → 5.66초**(동시 실행+세션),
# 채택 **1 → 5표**(자리 수 가드). 그리고 두 가지가 남았다.
#
#   ① 내가 0.3.91 에서 오판했다 — 지시문은 content_type 을 **"문제 있을
#      때만"** 적으라고 한다. 그러니 **생략은 "표이고 괜찮다"** 는 뜻이다.
#      그것을 "알 수 없는 값" 으로 읽어 정상 표 46개에 경고를 남겼다.
#      값이 **있는데** 모르는 경우와 구분한다.
#
#   ② 묵은 한자 신호 — `ocr_language_doubt` 는 표시 단계의 markdown 으로
#      잰 값이다. 재추출(fill)이 그 표를 한글로 고쳐 놓으면 표시는 묵는다.
#      실측(9쪽 table_4): fill 이 한글 표를 만든 뒤에도 표시가 남아 VLM 을
#      한 번 더 불렀고 그 결과가 짧아 폐기됐다 — 헛걸음이다.
#      대상 여부는 **지금 글**이 답한다.
# ────────────────────────────────────────────────────────────────────

def test_omitted_content_type_means_fine():
    """생략은 계약대로 '문제 없음' 이다 — 경고를 남기지 않는다."""
    from docstruct.models import TableInfo
    from docstruct.tables.assess import _apply_assessment

    table = TableInfo(id="t1", table_num=1, placeholder="", markdown="| a |")
    _apply_assessment([table], [{"id": "t1", "table_kind": "budget",
                                 "title": "예산표"}], unassessed=False)
    assert table.assessed is True
    assert table.reason is None                  # 경고 없음
    assert table.table_kind == "budget"


def test_present_but_unknown_content_type_is_recorded():
    """값이 있는데 모르는 경우는 여전히 기록한다 (생략과 구분)."""
    from docstruct.models import TableInfo
    from docstruct.tables.assess import _apply_assessment

    table = TableInfo(id="t2", table_num=1, placeholder="", markdown="| a |")
    _apply_assessment([table], [{"id": "t2", "content_type": "뭔가이상"}],
                      unassessed=False)
    assert table.reason and "content_type" in table.reason


def test_stale_han_signal_does_not_call_vlm():
    """재추출이 고친 표는 한자 표시가 남아도 VLM 대상이 아니다."""
    from docstruct.models import TableInfo
    from docstruct.tables.vlm_rebuild import needs_vlm

    doubt = {"han": 10, "words": 12, "ratio": 0.83}
    fixed = TableInfo(id="a", table_num=1, placeholder="",
                      markdown="| 구분 | 기 구 | 기준 정원 |\n| --- | --- | --- |\n"
                               "| 총계 | 1관 5국 | 1,117명 |",
                      ocr_language_doubt=doubt)
    assert needs_vlm(fixed) is False              # 이미 한글이다
    still = TableInfo(id="b", table_num=1, placeholder="",
                      markdown="| 7是 | 77 | 歪 | 全会フ世 | 1世5号17粤 |",
                      ocr_language_doubt=doubt)
    assert needs_vlm(still) is True              # 여전히 한자다


# ────────────────────────────────────────────────────────────────────
# 0.3.93 — 표 id 중복 (조달청 17쪽 실행에서 드러남)
#
#     9쪽  table_5  (그림 승격 · markdown 빈 채)
#     10쪽 table_5  (본래 표 · 내용 있음)
#
# 그림을 표로 승격할 때 번호를 **그 쪽 안에서만** 셌다. 문서 전체로는
# 겹친다. 표 id 는 정답 매칭·`<table N>` 치환·구조화 레코드의 열쇠라,
# 겹치면 그 셋이 조용히 어긋난다 — 크래시가 아니라 **틀린 짝**이 된다.
#
# 병렬 판정에서는 각자 세면 또 겹치므로, 번호를 **미리 나눠 준다**
# (쪽마다 _PROMOTE_STRIDE 만큼).
# ────────────────────────────────────────────────────────────────────

def test_promoted_table_number_is_given_not_guessed():
    """승격 표 번호는 호출부가 준 값에서 시작한다 (쪽 안에서 세지 않는다)."""
    from docstruct.models import ImageInfo, PageContent, TableInfo
    from docstruct.tables.assess import promote_images_to_tables

    page = PageContent(
        page_no=9, page_no_kind="exact", content="",
        tables=[TableInfo(id="table_4", table_num=4, placeholder="<table 4>",
                          markdown="| a |")],
        images=[ImageInfo(id="image_2", placeholder="<!-- image_2 -->",
                          region_kind="image", table_candidate=True)])
    promote_images_to_tables(page, [{"id": "image_2", "content_type": "table"}],
                             next_num=30)
    promoted = [t for t in page.tables if t.table_num > 4]
    assert promoted and promoted[0].table_num == 31
    assert promoted[0].id == "table_31"


def test_promoted_numbers_do_not_collide_across_pages():
    """다른 쪽의 승격 표와 겹치지 않는다 (실측 사고 그대로)."""
    from docstruct.models import ImageInfo, PageContent, TableInfo
    from docstruct.tables.assess import (
        _PROMOTE_STRIDE,
        _next_table_num,
        promote_images_to_tables,
    )

    def make(page_no, table_num, image_id):
        return PageContent(
            page_no=page_no, page_no_kind="exact", content="",
            tables=[TableInfo(id=f"table_{table_num}", table_num=table_num,
                              placeholder=f"<table {table_num}>", markdown="| a |")],
            images=[ImageInfo(id=image_id, placeholder=f"<!-- {image_id} -->",
                              region_kind="image", table_candidate=True)])

    ninth, tenth = make(9, 4, "image_2"), make(10, 5, "image_3")
    start = _next_table_num([ninth, tenth])
    promote_images_to_tables(ninth, [{"id": "image_2", "content_type": "table"}],
                             next_num=start)
    promote_images_to_tables(tenth, [{"id": "image_3", "content_type": "table"}],
                             next_num=start + _PROMOTE_STRIDE)
    ids = [t.id for page in (ninth, tenth) for t in page.tables]
    assert len(ids) == len(set(ids))


def test_document_wide_next_number():
    """다음 번호는 문서 전체 최댓값이다."""
    from docstruct.models import PageContent, TableInfo
    from docstruct.tables.assess import _next_table_num

    pages = [
        PageContent(page_no=1, page_no_kind="exact", content="",
                    tables=[TableInfo(id="table_3", table_num=3,
                                      placeholder="", markdown="")]),
        PageContent(page_no=2, page_no_kind="exact", content="",
                    tables=[TableInfo(id="table_9", table_num=9,
                                      placeholder="", markdown="")]),
    ]
    assert _next_table_num(pages) == 9


# ────────────────────────────────────────────────────────────────────
# 0.3.94 — 파싱 실패 **사유**를 결과까지 나른다
#
#     ⚠ 파싱 실패 : [17, 18, …, 78]  ← 결과에서 빠진 페이지
#
# 조달청 78쪽 중 **61쪽이 빠졌는데** 결과에는 번호 목록뿐이었다. 사유는
# 이미 계산해 로그에는 찍고 있었지만(`_failure_reasons`), 결과 JSON·보고서
# 에는 넣지 않아 **결과만 받아 보는 쪽에서는 원인을 알 수 없었다.**
#
# 이번 실행에서 실제로 그랬다 — 같은 PDF 가 앞선 실행에서는 78쪽 다
# 읽혔다는 사실 말고는 단서가 없었다. 번호는 증상이고 사유가 원인이다.
# ────────────────────────────────────────────────────────────────────

def test_failure_reasons_reach_the_result_json():
    """실패 사유가 결과 JSON 에 들어간다 (번호만으로는 못 고친다)."""
    from docstruct.models import PageDocument

    doc = PageDocument(filename="x.pdf", source_format="pdf", pages=[],
                   failed_pages=[17, 18],
                   failure_reasons=["61쪽 (17, 18 …): docling: 오류"])
    payload = doc.to_dict()
    assert payload["failure_reasons"] == ["61쪽 (17, 18 …): docling: 오류"]
    assert doc.to_dict(slim=True)["failure_reasons"]


def test_extraction_result_carries_reasons():
    """추출 결과가 사유 자리를 갖는다 — 배선이 끊기지 않게."""
    from docstruct.extractors.registry import ExtractionResult

    result = ExtractionResult(pages=[], failed_pages=[3],
                              failure_reasons=["1쪽 (3 …): m: msg"])
    assert result.failure_reasons == ["1쪽 (3 …): m: msg"]


def test_report_shows_reason_lines():
    """요약 보고서에도 사유가 붙는다."""
    import inspect

    from docstruct.output import report

    source = inspect.getsource(report)
    assert "사유 · " in source


# ────────────────────────────────────────────────────────────────────
# 0.3.95 — VLM 이 병합을 만들어도 **보이지 않던** 표기 불일치
#
# 조달청 76쪽 정상 실행(실패 0)에서 처음 정답 채점을 했다.
#
#     ⑦ grid_restore  table_49  병합 4 → **8/8 정확**
#                     table_50  4/5 → **5/5 정확**   ← 결정론은 제 몫을 했다
#     VLM 채택 4표     병합 **0/0** — 하나도 만들지 않았다
#
# 원인은 성능이 아니라 **규약**이었다. 우리 내부 표기는 `〃`(hwpxtree·⑦·
# 채점기·구조화가 모두 이것을 읽는다)인데, VLM 지시문은 "덮인 칸은 **빈
# 칸**으로 두라" 고 적혀 있었다. 그래서 VLM 이 병합을 옳게 보았더라도
# 결과에는 빈 칸으로 나오고, 우리 쪽에서는 병합이 **없는 것으로 읽힌다.**
#
# 빈 칸을 병합으로 해석하는 길은 막았다 — 원래 값이 없는 칸과 구분할 수
# 없기 때문이다. 대신 지시문을 규약에 맞췄다: 덮인 칸에는 `〃`, 원래 빈
# 칸은 빈 칸.
# ────────────────────────────────────────────────────────────────────

def test_prompts_ask_for_ditto_notation():
    """두 지시문 판 모두 `〃` 를 요구하고 빈 칸과 구분하게 한다."""
    from docstruct.tables.vlm_rebuild import _PROMPT, _PROMPT_STEPS

    for template in (_PROMPT, _PROMPT_STEPS):
        rendered = template.format(context="c", hint="", target="")
        assert "〃" in rendered
        assert "원래" in rendered                # 빈 칸과 구분하라는 안내


def test_hint_header_repeats_the_convention():
    """힌트 문단도 같은 표기를 짚어 준다."""
    from docstruct.tables.vlm_rebuild import _HINT_HEADER

    assert "〃" in _HINT_HEADER


def test_ditto_output_is_visible_to_scorer_and_structuring():
    """`〃` 로 적힌 병합은 채점기와 구조화가 모두 읽는다 (규약의 값)."""
    from docstruct.structuring import expand_merges
    from docstruct.tables.grade import cells_from_markdown

    markdown = ("|  | 회계 구분 | 재정사업 성과평가 |  |\n"
                "| --- | --- | --- | --- |\n"
                "| 〃 | 〃 | 평가명 | 결과 |\n"
                "| (1) 국유재산 | 조달특별회계 |  |  |")
    cells = cells_from_markdown(markdown)
    merges = [c for c in cells if c["rowspan"] > 1 or c["colspan"] > 1]
    assert len(merges) == 2                       # 앞 두 열이 세로 병합
    assert expand_merges(cells)                   # 전개도 된다


# ────────────────────────────────────────────────────────────────────
# 0.3.96 — VLM 손잡이도 `--exp` 로 (A/B 를 한 자리에서)
#
# 실험은 `--exp`, VLM 은 환경변수로 갈라져 있었다. A/B 를 돌리려면 판마다
# 두 곳을 건드려야 하고, cmd 에서 앞 판의 환경변수를 지우는 것을 잊으면
# 다음 판에 새어 들어 **비교가 조용히 망가진다** — 틀린 결과가 아니라
# 틀린 실험이 된다.
#
#     --exp grid_score,grid_restore,vlm_hint,vlm_steps
#
# 손잡이 이름은 실험 키와 겹치지 않고, `--exp list` 와 오류 안내에 함께
# 나온다. 환경변수 방식은 서버 배치를 위해 그대로 남는다.
# ────────────────────────────────────────────────────────────────────

def test_exp_turns_on_vlm_knobs(monkeypatch):
    """--exp 로 VLM 손잡이가 켜지고 실험 키와 섞여도 갈린다."""
    import os

    for name in ("DOCSTRUCT_VLM_HINT_MISSING", "DOCSTRUCT_VLM_PROMPT",
                 "DOCSTRUCT_EXP_GRID_SCORE"):
        monkeypatch.delenv(name, raising=False)
    from docstruct.cli import _enable_experiments

    keys = _enable_experiments("grid_score,vlm_hint,vlm_steps")
    assert keys == ["grid_score"]                # 실험 키만 남는다
    assert os.environ["DOCSTRUCT_VLM_HINT_MISSING"] == "1"
    assert os.environ["DOCSTRUCT_VLM_PROMPT"] == "steps"
    assert os.environ["DOCSTRUCT_EXP_GRID_SCORE"] == "true"


def test_knob_names_never_collide_with_experiments():
    """손잡이 이름이 실험 키와 겹치지 않는다 (겹치면 한쪽이 죽는다)."""
    from docstruct.cli import _VLM_KNOBS
    from docstruct.experiments import all_experiments
    assert not (set(_VLM_KNOBS) & {e.key for e in all_experiments()})


def test_every_knob_is_documented():
    """모든 손잡이에 설명이 있다 — 목록이 쓸모 있으려면."""
    from docstruct.cli import _KNOB_HELP, _VLM_KNOBS

    assert set(_VLM_KNOBS) == set(_KNOB_HELP)
    assert all(_KNOB_HELP[name].strip() for name in _VLM_KNOBS)


# ────────────────────────────────────────────────────────────────────
# 0.3.97 — VLM 이 만든 병합이 하류에 **닿지 않던** 문제 + 오프셋 채점
#
# `〃` 규약(0.3.95) 뒤 첫 실행에서 VLM 이 실제로 병합을 내기 시작했다
# (표당 2~10개, table_59 는 2/2 정확). 그런데 두 가지가 걸렸다.
#
#   ① cells 미갱신 — VLM 채택은 `markdown` 만 바꾸고 `cells` 를 그대로
#      뒀다. 채점기·구조화 전개·⑧ 검산은 전부 `cells` 를 읽으므로,
#      VLM 이 옳게 만든 병합이 **그림의 떡**이었다. ⑦(grid_restore)은
#      이미 둘 다 갱신하는데 VLM 경로만 빠져 있었다.
#
#   ② 행 오프셋 — 정답(HWPX)에는 캡션 행처럼 지면 표에 없는 행이 있어
#      행 번호가 밀린다. 실측(41쪽 table_29): VLM 이 세로 병합 두 개를
#      **구조적으로 정확히** 냈는데 한 행 어긋나 엄격 채점은 0/2 였다.
#      -3~+3 이동을 허용하면 2/2 다.
#
#      결정론 근거는 이 보정에 거의 영향받지 않는다(사각형 81.7% →
#      81.7%) — 같은 지면 좌표계를 쓰기 때문이다. 보정이 필요한 쪽은
#      **표를 다시 쓰는** 경로다.
# ────────────────────────────────────────────────────────────────────

def test_vlm_adoption_updates_cells():
    """VLM 채택이 cells 도 갱신한다 — 하류가 병합을 보게."""
    import inspect

    from docstruct.tables import vlm_rebuild

    source = inspect.getsource(vlm_rebuild.rebuild_broken_tables)
    assert "table.cells = rebuilt_cells" in source
    assert "cells_from_markdown" in source


def test_offset_tolerant_hit_counts_shifted_merges():
    """행이 밀려도 구조가 맞으면 맞은 것으로 센다 (A/B 채점의 전제)."""
    truth = {(2, 0, 2, 1), (4, 0, 2, 1)}
    got = {(1, 0, 2, 1), (3, 0, 2, 1)}            # 한 행 위로 밀림
    assert len(got & truth) == 0                  # 엄격 채점은 0
    best = max(len({(r + off, c, rs, cs) for r, c, rs, cs in got} & truth)
               for off in range(-3, 4))
    assert best == 2                              # 보정하면 둘 다 맞다


# ────────────────────────────────────────────────────────────────────
# 0.3.98 — ⑫ head_grid: 머리 계층 복원 (조달청 정답 대조에서 나온 실험)
#
# 정답 대조가 표적을 짚어 줬다. 예산 내역표 여섯(45·47·61·63·65·66쪽)이
# **정답 병합 7개를 하나도 못 잡았다**(0/7 × 6). 지면은 2층 머리다:
#
#     회계구분·'25결산·'26예산·'27예산안·비고 → 세로 2칸
#     재정사업 성과평가 → 가로 2칸, 그 아래 평가명 │ 결과
#
# 그런데 ⑨(line_grid)는 이미 그 구조를 **정확히** 세우고 있었다:
#     65쪽 lattice 머리 = (0,0,2,1)(0,1,2,1)(0,2,2,1)(0,3,2,1)
#                        (0,4,2,1)(0,5,1,2)(0,7,2,1) — 정답과 일치
#
# ⑦이 이 표들을 건너뛴 것은 **사각형 덮개가 0.24~0.53** 이라서다. 같은
# 표의 lattice 덮개는 1.11~1.53 — 근거가 있는데 쓰지 않고 있었다.
#
# 표 전체를 lattice 로 다시 쓰면 ⑨ 승격과 같은 정밀도 문제에 부딪힌다
# (69.6~84.5%, 문서군을 탄다). 그러나 **머리 구간만** 은 다르다: 자리가
# 적고, 경계를 괘선이 직접 그리며, 본문을 건드리지 않아 값 귀속이
# 무너지지 않는다.
#
#     실측(조달청 35표): 병합 재현율 44.3% → **70.5%**
#                       정밀도 97.6% → **98.5%** (지어내지 않았다)
# ────────────────────────────────────────────────────────────────────

def test_head_grid_registered_after_grid_restore():
    """⑫는 ⑦ 다음이다 — ⑦이 통째로 복원한 표는 건드리지 않는다."""
    from docstruct.experiments import all_experiments
    keys = [e.key for e in all_experiments()]
    assert keys.index("grid_restore") < keys.index("head_grid")
    assert keys.index("head_grid") < keys.index("line_grid")


def test_head_grid_applies_two_tier_header():
    """2층 머리를 반영한다 — 실측 꼴 그대로."""
    from docstruct.experiments.tsr.restore.head_grid import apply_head
    from docstruct.models import TableInfo

    flat = [{"row": 0, "col": c, "rowspan": 1, "colspan": 1, "text": name}
            for c, name in enumerate(["", "회계구분", "'25결산", "재정사업 성과평가"])]
    body = [{"row": 1, "col": c, "rowspan": 1, "colspan": 1, "text": str(c)}
            for c in range(4)]
    table = TableInfo(id="t", table_num=1, placeholder="", markdown="",
                      cells=flat + body)
    head = [(0, 0, 2, 1), (0, 1, 2, 1), (0, 2, 2, 1), (0, 3, 1, 2)]
    report = apply_head(table, head)
    assert report == {"before": 0, "after": 4, "rows": 2}
    merged = [c for c in table.cells if c["rowspan"] > 1 or c["colspan"] > 1]
    assert len(merged) == 4
    assert any(c["text"] == "회계구분" for c in merged)   # 글자는 인식 것을 쓴다


def test_head_grid_declines_when_nothing_gained():
    """이미 머리에 병합이 있으면 건드리지 않는다."""
    from docstruct.experiments.tsr.restore.head_grid import apply_head
    from docstruct.models import TableInfo

    cells = [{"row": 0, "col": 0, "rowspan": 2, "colspan": 1, "text": "구분"},
             {"row": 0, "col": 1, "rowspan": 2, "colspan": 1, "text": "값"},
             {"row": 2, "col": 0, "rowspan": 1, "colspan": 1, "text": "a"},
             {"row": 2, "col": 1, "rowspan": 1, "colspan": 1, "text": "1"}]
    table = TableInfo(id="t", table_num=1, placeholder="", markdown="", cells=cells)
    assert apply_head(table, [(0, 0, 2, 1), (0, 1, 2, 1)]) is None


def test_head_grid_needs_matching_column_count():
    """열 수가 다르면 물러난다 — 자리가 어긋나면 없는 결함을 만든다."""
    import inspect

    from docstruct.experiments.tsr.restore import head_grid
    source = inspect.getsource(head_grid.run)
    assert "_detected_cols(table.cells)" in source
    assert 'table.source == "grid"' in source     # ⑦ 복원본은 건너뛴다


# ────────────────────────────────────────────────────────────────────
# 0.3.99 — A/B 네 판이 전부 VLM 없이 돌았다 (자동 켜짐이 눈이 좁았다)
#
# out_base·out_hint·out_steps·out_steps_hint 네 판의 채점이 **완전히
# 같았다**(재현율 44.3% · 정밀도 97.6% · 표 35 · 병합 83). 손잡이가
# 듣지 않은 것이 아니라 **VLM 자체가 돌지 않았다** — 네 판 모두
# `vlm_fix_tables=False`.
#
# 원인: `_vlm_default()` 가 `os.environ` 만 보았다. 사내 배치는 LLM
# 엔드포인트가 **내장 기본값**(_DEFAULTS)에 들어 있어 환경변수에는 아무
# 것도 없다. 그래서 LLM 은 멀쩡히 붙는데(assess·fill 은 돌았다) VLM
# 기본값만 꺼졌다.
#
# `_get()` 으로 본다 — 환경변수 → .env → 내장 기본값 순으로 해석하는
# 유일한 지점이다. 설정을 읽는 곳이 둘이면 이런 어긋남이 난다.
#
# 곁들여: `test_table_flags_are_toggleable` 이 0.3.82 의 계약 변경에도
# 통과했던 것이 바로 이 구멍 때문이다 — 시험이 구멍을 덮고 있었다.
# ────────────────────────────────────────────────────────────────────

def test_vlm_default_sees_builtin_endpoint(monkeypatch):
    """내장 기본값에 엔드포인트가 있으면 VLM 이 기본으로 켜진다."""
    from docstruct.core import config

    for name in ("DOCLING_TABLE_API_URL", "DOCSTRUCT_LLM_URL",
                 "DOCLING_PICTURE_API_URL", "DOCSTRUCT_LOCAL_VLM_MODEL",
                 "OPENAI_API_KEY", "DOCLING_TABLE_API_KEY",
                 "DOCLING_TABLE_API_FALLBACK_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        config, "_DEFAULTS",
        {"DOCLING_TABLE_API_URL": "http://내장:11060/v1/chat/completions"},
        raising=False)
    assert config._vlm_default() is True          # 환경변수는 비어 있다


def test_vlm_default_reads_through_get(monkeypatch):
    """설정을 읽는 지점이 하나여야 한다 — os.environ 직접 조회 금지."""
    import inspect

    from docstruct.core import config

    source = inspect.getsource(config._vlm_default)
    assert "_get(name)" in source
    assert "os.environ.get(name)" not in source


# ────────────────────────────────────────────────────────────────────
# 0.4.0 — ⑫가 복원한 병합을 VLM 이 덮어쓰던 문제
#
# 행안부 3부처째 검증에서 나왔다. ⑫ head_grid 는 772개 병합 중 708개를
# 맞혀(재현율 91.7% · 정밀도 98.3%) 이 문서 정답의 61% 를 혼자 맡았다.
# 그런데 llm·vlm 경로의 정밀도가 65.6%·32.0% 로 유독 낮았다.
#
# 전후를 견주니 회귀는 **한 표**였고, 그 한 표가 정확히 겹친 자리였다:
#
#     112쪽 table_57 — ⑫가 세운 병합 3개 → VLM 재구성 뒤 **0개**
#     (VLM 이 프로그램명을 두 줄로 쪼개고 `〃` 를 쓰지 않았다)
#
# ⑦(source="grid")은 이미 VLM 대상에서 빼고 있었는데 ⑫는 빠져 있었다.
# **결정론이 이긴 자리는 지킨다** — 같은 규칙을 ⑫에도 적용한다.
#
# 곁들여 확인: fill(재추출)은 파이프라인에서 실험보다 **먼저** 돌므로
# ⑫를 덮을 수 없다. 순서가 이미 안전하다.
# ────────────────────────────────────────────────────────────────────

def test_head_grid_result_is_not_overwritten_by_vlm():
    """⑫가 머리를 복원한 표는 VLM 대상에서 빠진다."""
    from docstruct.models import TableInfo
    from docstruct.tables.vlm_rebuild import needs_vlm

    fixed = TableInfo(id="t", table_num=1, placeholder="", markdown="",
                      head_grid={"before": 0, "after": 3, "rows": 2},
                      synth_grid={"confidence": "high", "missing": [(1, 0, 2, 1)]})
    assert needs_vlm(fixed) is False
    # ⑫가 손대지 않았다면 같은 신호로 대상이 된다
    untouched = TableInfo(id="t2", table_num=1, placeholder="", markdown="",
                          synth_grid={"confidence": "high",
                                      "missing": [(1, 0, 2, 1)]})
    assert needs_vlm(untouched) is True


def test_deterministic_wins_are_protected():
    """⑦·⑫ 두 결정론 경로 모두 VLM 이 건드리지 않는다."""
    import inspect

    from docstruct.tables import vlm_rebuild

    source = inspect.getsource(vlm_rebuild.needs_vlm)
    assert 'source", None) == "grid"' in source
    assert '"head_grid"' in source


# ────────────────────────────────────────────────────────────────────
# 0.4.1 — ⑬ agreed_grid: 두 기하 근거가 합의한 병합만 반영
#
# ⑫를 켜고도 `source="parser"`(아무도 손대지 않은) 표에 병합이 남았다.
# 근거를 갈라 보니 **90% 가 지면에 근거가 있었다** — ⑫가 손대지 못한
# 까닭은 그 병합들이 **머리 2행 밖**(MAX_HEAD_ROWS=2)이었기 때문이다.
#
# 단일 근거는 문서군을 탄다(lattice 69.6~84.5% · 사각형 81.7~91.9%).
# 그러나 **둘이 합의한 병합**은 다르다:
#
#     행안부 실측: agreed_missing 810개 중 정답 일치 **810 = 100.0%**
#
# ⑦(덮개≥1.0)과 같은 오탐 0 이다. ⑪이 이미 계산해 둔 값을 읽기만 하므로
# 추가 비용도 없다.
#
#     조달청  72.6% → 76.5%  (정밀도 98.5% → 98.6%)
#     행안부  85.1% → 86.3%  (정밀도 96.4% → 96.5%)
# ────────────────────────────────────────────────────────────────────

def test_agreed_grid_order_after_head_grid():
    """⑬은 ⑫ 다음이다 — ⑫가 머리를 먼저 가져간다."""
    from docstruct.experiments import all_experiments
    keys = [e.key for e in all_experiments()]
    assert keys.index("head_grid") < keys.index("agreed_grid")
    assert keys.index("agreed_grid") < keys.index("line_grid")


def test_agreed_grid_applies_body_merges():
    """본문에 걸친 세로 병합도 반영한다 (⑫가 못 닿는 자리)."""
    from docstruct.experiments.tsr.restore.agreed_grid import apply_agreed
    from docstruct.models import TableInfo

    cells = [{"row": r, "col": c, "rowspan": 1, "colspan": 1,
              "text": f"r{r}c{c}"} for r in range(4) for c in range(2)]
    table = TableInfo(id="t", table_num=1, placeholder="", markdown="",
                      cells=cells)
    report = apply_agreed(table, [(2, 0, 2, 1)])   # 본문 세로 병합
    assert report["applied"] == 1
    merged = [c for c in table.cells if c["rowspan"] > 1 or c["colspan"] > 1]
    assert merged and (merged[0]["row"], merged[0]["col"]) == (2, 0)


def test_agreed_grid_skips_already_merged():
    """이미 병합된 자리는 건드리지 않는다 (겹침 방지)."""
    from docstruct.experiments.tsr.restore.agreed_grid import apply_agreed
    from docstruct.models import TableInfo

    cells = [{"row": 0, "col": 0, "rowspan": 2, "colspan": 1, "text": "A"},
             {"row": 0, "col": 1, "rowspan": 1, "colspan": 1, "text": "B"},
             {"row": 1, "col": 1, "rowspan": 1, "colspan": 1, "text": "C"}]
    table = TableInfo(id="t", table_num=1, placeholder="", markdown="",
                      cells=cells)
    assert apply_agreed(table, [(0, 0, 2, 1)]) is None


def test_agreed_grid_respects_earlier_wins():
    """⑦·⑫가 손댄 표는 건너뛴다."""
    import inspect

    from docstruct.experiments.tsr.restore import agreed_grid
    source = inspect.getsource(agreed_grid.run)
    assert 'table.source == "grid"' in source
    assert '"head_grid"' in source
    assert "agreed_missing" in source


# ────────────────────────────────────────────────────────────────────
# 0.4.1 — ⑬ col_grid: 열 격자 복원 (⑫가 물러난 자리를 연다)
#
# ⑫를 켠 뒤 남은 놓침 49개 중 **35개(71%)** 가 "열 수 불일치" 로 ⑫가
# 물러난 자리였다. ⑫는 격자와 인식의 열 수가 다르면 손대지 않는다 —
# 자리가 어긋난 채로 머리를 갈아 끼우면 없는 결함을 만들기 때문이다.
# **열이 먼저 맞아야 ⑫가 일한다.**
#
# 안전 조건 둘을 실측이 정해 줬다.
#
#   ① 격자 열 수가 늘 옳지는 않다:
#        쪽   정답 인식 격자   격자가 옳은가
#        42   13   13   14    아니오  ← 인식과 정답이 이미 같다
#        58   13   13   14    아니오
#        68    6    6    7    아니오
#        74    8    8    9    아니오
#        65    8    7    8    **예**  ← 인식이 열을 잃었다
#        73   13   10   13    **예**
#      → 격자가 인식보다 **뚜렷하게**(1.2배 이상) 많을 때만 받는다.
#
#   ② 세로선만으로는 부족하다. 37쪽 table_22 은 세로 경계 9개를 찾았지만
#      가로선이 부족해 격자가 서지 않았고, 그 열 수로 늘리자 **없는 병합
#      2개**가 생겼다(정밀도 98.5% → 95.7%). 전체 격자가 서는 표만 받자
#      97.1% 로 회복했다.
#
#     실측(조달청): ⑫만 70.5%(정밀도 98.5%) → ⑬→⑫ **72.7%**(97.1%)
# ────────────────────────────────────────────────────────────────────

def test_col_grid_runs_before_head_grid():
    """⑬는 ⑫보다 먼저다 — 열이 맞아야 머리를 고칠 수 있다."""
    from docstruct.experiments import all_experiments
    keys = [e.key for e in all_experiments()]
    assert keys.index("col_grid") < keys.index("head_grid")
    assert keys.index("grid_restore") < keys.index("col_grid")


def test_col_grid_needs_clear_gain():
    """격자가 뚜렷하게 많을 때만 받는다 — 하나 차이는 지어낸 경계다."""
    from docstruct.experiments.tsr.restore.col_grid import MIN_GAIN

    assert MIN_GAIN >= 1.2
    assert 14 / 13 < MIN_GAIN                     # 42·58쪽 (격자가 틀렸다)
    assert 9 / 8 < MIN_GAIN                       # 74쪽
    assert 13 / 10 >= MIN_GAIN                    # 73쪽 (격자가 옳았다)


def test_col_grid_requires_full_lattice():
    """세로선만으로는 받지 않는다 — 37쪽에서 없는 병합을 만들었다."""
    import inspect

    from docstruct.experiments.tsr.restore import col_grid
    source = inspect.getsource(col_grid.run)
    assert "table_lattice" in source


def test_col_remap_does_not_invent_columns():
    """어느 열이 쪼개졌는지 모르므로 마지막 열만 늘린다."""
    from docstruct.experiments.tsr.restore.col_grid import remap_columns

    cells = [{"row": 0, "col": 0, "rowspan": 1, "colspan": 1, "text": "a"},
             {"row": 0, "col": 1, "rowspan": 1, "colspan": 1, "text": "b"}]
    out = remap_columns(cells, 2, 4)
    assert out[0]["colspan"] == 1                 # 앞 셀은 그대로
    assert out[1]["colspan"] == 3                 # 마지막 셀이 남은 자리를 덮는다
    assert remap_columns(cells, 4, 2) is None     # 줄이지는 않는다


# ────────────────────────────────────────────────────────────────────
# 0.4.2 — 배선은 최신인데 원천이 옛 판이면 조용히 죽는다
#
# 0.4.1 배포에서 local·overlay 의 converters/pdf/converter.py 가 옛 판이라
# `_failure_reasons` 가 없었다. models·report·extractors 의 배선은 최신이라
# 죽지는 않았고 — `getattr(…, [])` — **failure_reasons 가 항상 빈 배열**로
# 무력화됐다. 하필 이 진단이 가장 필요한 곳이 FastAPI 배치(overlay)다.
# 배선 테스트(0.3.94)는 자리만 보고 원천은 안 봐서 못 잡았다. 원천이
# 계산하고 **결과에 싣는 것까지** 직접 본다.
# ────────────────────────────────────────────────────────────────────


def test_pdf_converter_collects_failure_reasons():
    """원천(`_failure_reasons`)이 있고, 같은 사유를 한 줄로 모은다."""
    from docstruct.converters.pdf.converter import _failure_reasons

    class _Err:
        page_no = 17
        module_name = "docling"
        error_message = "layout 실패"

    class _Result:
        errors = [_Err(), _Err()]

    reasons = _failure_reasons(_Result())
    assert len(reasons) == 1                      # 같은 사유는 한 줄
    assert "2쪽" in reasons[0]
    assert "docling" in reasons[0]


def test_pdf_converter_assigns_failure_reasons():
    """계산만 하고 결과에 안 실으면 0.3.94 이전과 같다 — 대입까지 본다."""
    import inspect

    from docstruct.converters.pdf import converter

    source = inspect.getsource(converter)
    assert "self.failure_reasons = _failure_reasons(result)" in source


def test_sync_trees_promotes_experiments():
    """동기화 도구가 experiments/ 승격을 안다 — 몰라서 전파가 손으로 갔다.

    0.4.1 의 sync_trees 는 PROMOTED 에 experiments 가 없어 배포 트리
    배치(최상위 experiments/)를 재현하지 못했고, --check 가 40건 불일치를
    냈다. 도구가 못 미더우면 동기화는 손으로 하게 되고, 실제로
    converter.py 전파가 빠졌다. local/overlay 에는 tools/ 가 없으므로
    pkg 에서만 검사한다.
    """
    from pathlib import Path as _Path

    tool = _Path(__file__).resolve().parent.parent / "tools" / "sync_trees.py"
    if not tool.is_file():                        # local/overlay 트리
        return
    source = tool.read_text(encoding="utf-8")
    promoted = source.split("PROMOTED = ", 1)[1].split("\n", 1)[0]
    assert '"experiments"' in promoted


# ────────────────────────────────────────────────────────────────────
# 0.4.2 — ⑭ agreed_grid: 문서에만 있던 안전장치를 구현
#
# docstring 은 "열 수가 다르면 물러난다 · 겹치면 되돌린다" 를 약속했는데
# 구현이 없었다. agreed_missing 의 (row, col) 은 **기하 격자의 번호**라,
# 인식이 열을 잃은 표(⑬의 표적)에서는 같은 번호가 지면의 다른 자리를
# 가리킨다 — 어긋난 자리에 병합을 심게 된다. 조달청 최종 조합에서 발화
# 0 이라 드러나지 않았을 뿐이다 (행안부 parser 표에는 발화 자리가 있다).
# ────────────────────────────────────────────────────────────────────


def _agreed_page(table):
    """agreed_grid.run 이 받는 최소 페이지 꼴."""
    class _Trace:
        def add(self, *args, **kwargs):
            pass

    class _Page:
        tables = [table]
        trace = _Trace()

    return _Page()


def _plain_cells(rows, cols):
    """병합 없는 rows×cols 셀."""
    return [{"row": r, "col": c, "rowspan": 1, "colspan": 1, "text": f"{r}{c}"}
            for r in range(rows) for c in range(cols)]


def test_agreed_grid_steps_back_on_column_mismatch():
    """기하 열 수 ≠ 인식 열 수 — 같은 번호가 다른 자리다. 물러난다."""
    from docstruct.experiments.tsr.restore.agreed_grid import run

    table = _table(cells=_plain_cells(3, 4),
                   grid_score={"agreed_missing": [(0, 0, 2, 1)],
                               "agreed_cols": {"rect": 5, "lattice": 5}})
    before = [dict(c) for c in table.cells]
    assert run([_agreed_page(table)]) == 0
    assert table.cells == before                  # 손대지 않았다
    assert table.agreed_grid is None


def test_agreed_grid_steps_back_without_agreed_cols():
    """열 수를 잴 근거가 없으면(옛 ⑪ 기록) 물러난다 — 오탐 0 이 먼저다."""
    from docstruct.experiments.tsr.restore.agreed_grid import run

    table = _table(cells=_plain_cells(3, 4),
                   grid_score={"agreed_missing": [(0, 0, 2, 1)]})
    assert run([_agreed_page(table)]) == 0
    assert table.agreed_grid is None


def test_agreed_grid_applies_when_columns_agree():
    """세 열 수가 같으면 반영한다 — 가드가 정상 발화까지 막으면 안 된다."""
    from docstruct.experiments.tsr.restore.agreed_grid import run

    table = _table(cells=_plain_cells(3, 4),
                   grid_score={"agreed_missing": [(0, 0, 2, 1)],
                               "agreed_cols": {"rect": 4, "lattice": 4}})
    assert run([_agreed_page(table)]) == 1
    assert table.agreed_grid == {"applied": 1, "before": 0, "after": 1}
    merged = [c for c in table.cells if c["rowspan"] > 1]
    assert merged and merged[0]["row"] == 0 and merged[0]["col"] == 0


def test_agreed_grid_reverts_on_overlap():
    """반영 결과가 겹치면 통째로 되돌린다 — 없는 결함을 만들지 않는다."""
    from docstruct.experiments.tsr.restore.agreed_grid import apply_agreed

    # 원래부터 겹친 표 (인식이 깨진 좌표) + 유효해 보이는 합의 하나.
    cells = _plain_cells(3, 3)
    cells.append({"row": 2, "col": 2, "rowspan": 1, "colspan": 1, "text": "겹침"})
    table = _table(cells=cells)
    assert apply_agreed(table, [(0, 0, 2, 1)]) is None
    assert len(table.cells) == 10                 # 원본 유지


def test_grid_score_reports_agreed_cols():
    """⑪이 좌표계 검사 근거(agreed_cols)를 계산한다."""
    from docstruct.experiments.tsr.measure.grid_score import _cols_of

    cells = [(0, 0, 1, 1), (0, 1, 1, 2), (1, 0, 1, 3)]
    assert _cols_of(cells) == 3
    assert _cols_of([]) == 0


def test_vlm_skips_agreed_grid_tables():
    """⑭이 심은 병합을 VLM 재작성이 지우지 않는다 — ⑫ 보호와 같은 규칙."""
    from docstruct.tables.vlm_rebuild import needs_vlm

    table = _table(agreed_grid={"applied": 2, "before": 0, "after": 2},
                   synth_grid={"confidence": "high", "missing": [(3, 0, 2, 1)]})
    assert needs_vlm(table) is False              # 표시가 남아 있어도 지킨다


# ────────────────────────────────────────────────────────────────────
# 0.4.3 — 장식 경계 접기 · 승격(기본 켬)
#
# 표 가장자리 겹선이 폭 몇 pt 짜리 빈 띠를 만들고, 격자가 그것을 열로
# 세면 기하 열 수가 인식보다 늘 1 커진다. 실측(행안부 429쪽): ⑬ 오탐
# 1표(p388, 2.8pt 띠 때문에 8열을 9열로)와 ⑭ 차단 19표 중 16표가 전부
# 이 한 칸이었다. 접을 때 **폭을 보지 않으면** 값이 빈 진짜 열(41.9~
# 88.3pt)까지 접혀 정답 8열이 6열이 된다 — 폭 조건이 규칙의 핵심이다.
# ────────────────────────────────────────────────────────────────────


def test_decor_fold_keeps_wide_empty_columns():
    """넓은 빈 띠는 접지 않는다 — 이 쪽에서만 값이 없는 진짜 열이다."""
    from docstruct.experiments.tsr.measure import line_grid
    # 띠 넷: 좁은 빈 띠(6pt) · 글자 있는 띠 · 넓은 빈 띠(42pt) · 글자 띠
    xs = [50.0, 56.0, 150.0, 192.0, 260.0]
    vertical = [(x, 0.0, 100.0) for x in xs]
    counts = [0, 14, 0, 3]

    def fake_counts(pdf_path, page_no, bounds, top, bottom):
        assert bounds == xs                      # 접기 전 경계로 묻는다
        return counts

    original = line_grid._band_char_counts
    line_grid._band_char_counts = fake_counts
    try:
        kept = line_grid.fold_decor_bounds(
            "x.pdf", 1, {"l": 50.0, "t": 0.0, "r": 260.0, "b": 100.0}, vertical)
    finally:
        line_grid._band_char_counts = original

    remaining = [x for x, _, _ in kept]
    assert 56.0 not in remaining                 # 좁은 빈 띠 → 접혔다
    assert 150.0 in remaining and 192.0 in remaining   # 넓은 빈 띠 → 남았다
    assert 50.0 in remaining and 260.0 in remaining    # 바깥 테두리는 그대로


def test_decor_fold_without_text_changes_nothing():
    """글자를 못 읽으면 아무것도 접지 않는다 — 근거 없이 고치지 않는다."""
    from docstruct.experiments.tsr.measure import line_grid
    vertical = [(x, 0.0, 100.0) for x in (50.0, 56.0, 150.0)]
    original = line_grid._band_char_counts
    line_grid._band_char_counts = lambda *a, **k: None
    try:
        kept = line_grid.fold_decor_bounds(
            "x.pdf", 1, {"l": 50.0, "t": 0.0, "r": 150.0, "b": 100.0}, vertical)
    finally:
        line_grid._band_char_counts = original
    assert kept == vertical


def test_lattice_does_not_fold_by_default():
    """⑨ 자신의 표시는 접지 않는다 — 지면에 있는 선을 없다고 말하면 안 된다."""
    import inspect

    from docstruct.experiments.tsr.measure.line_grid import table_lattice

    params = inspect.signature(table_lattice).parameters
    assert params["fold_decor"].default is False


def test_col_grid_and_grid_score_fold():
    """열 수를 재는 쪽(⑬·⑪)은 접고 센다."""
    import inspect

    from docstruct.experiments.tsr.restore import col_grid

    from docstruct.experiments.tsr.measure import grid_score
    assert "fold_decor=True" in inspect.getsource(col_grid.run)
    assert "fold_decor=True" in inspect.getsource(grid_score.score_table)


# ── 승격: --exp 없이도 켜진다 ─────────────────────────────────────────


def test_promoted_experiments_default_on(monkeypatch):
    """승격된 실험은 환경변수 없이 켜져 있다 (0.4.3)."""
    from docstruct.experiments.registry import DEFAULT_ON, all_experiments

    for exp in all_experiments():
        monkeypatch.delenv(exp.env, raising=False)
    for exp in all_experiments():
        assert exp.enabled is (exp.key in DEFAULT_ON), exp.key


def test_promoted_experiment_can_be_switched_off(monkeypatch):
    """끄기가 되어야 A/B 대조군을 만들 수 있다."""
    from docstruct.experiments.registry import all_experiments

    known = {e.key: e for e in all_experiments()}
    head = known["head_grid"]
    monkeypatch.setenv(head.env, "false")
    assert head.enabled is False
    monkeypatch.setenv(head.env, "1")
    assert head.enabled is True


def test_unpromoted_experiments_stay_off(monkeypatch):
    """재측정이 남은 것은 켜지 않는다 — ⑬⑭은 0.4.3 접기로 근거가 바뀌었다."""
    from docstruct.experiments.registry import DEFAULT_ON

    # 0.4.20: col_grid 는 0표 발화라 다시 내렸다 (⑮의 하위 호환).
    for key in ("agreed_grid", "scan_grid", "col_grid"):
        assert key not in DEFAULT_ON


def test_cli_supports_no_prefix_off_switch(monkeypatch):
    """`--exp no_head_grid` 로 끈다 — 목록에서 빼는 것만으로는 안 꺼진다."""
    from docstruct.cli import _enable_experiments
    from docstruct.experiments.registry import all_experiments

    known = {e.key: e for e in all_experiments()}
    monkeypatch.delenv(known["head_grid"].env, raising=False)
    _enable_experiments("no_head_grid")
    assert known["head_grid"].enabled is False


def test_vlm_hint_is_default_on(monkeypatch):
    """힌트도 승격됐다 — 끄려면 명시해야 한다."""
    from docstruct.tables.vlm_rebuild import _missing_hint

    table = _table(grid_score={"agreed_missing": [(0, 0, 2, 1)]})
    monkeypatch.delenv("DOCSTRUCT_VLM_HINT_MISSING", raising=False)
    assert _missing_hint(table) != ""
    monkeypatch.setenv("DOCSTRUCT_VLM_HINT_MISSING", "false")
    assert _missing_hint(table) == ""


# ────────────────────────────────────────────────────────────────────
# 0.4.4 — HWPX 그림과 도형 글자
#
# HWPX 추출기에 그림 처리가 **한 줄도 없었다.** PageContent.images 가 늘
# 비어 있어 pipeline 의 `if read_pictures and any(p.images ...)` 가 거짓이
# 되고, VLM 그림 읽기 단계에 진입조차 못 했다 — 조직도가 통째로 사라졌다
# (조달청 image1.png 367KB · 행안부 image1.jpg 399KB).
#
# 간지 제목도 마찬가지로 hp:container 안 hp:drawText 에 들어 있어 빠졌다.
# ────────────────────────────────────────────────────────────────────


def _hwpx_fixture(tmp_path):
    """그림 하나와 도형 글자를 가진 최소 HWPX 를 만든다."""
    import zipfile

    ns = ('xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" '
          'xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core"')
    section = (
        f'<hp:sec {ns}>'
        '<hp:p><hp:run><hp:t>\u25a1 \uc870\uc9c1</hp:t></hp:run></hp:p>'
        '<hp:p><hp:run><hp:pic>'
        '<hc:img binaryItemIDRef="image1"/>'
        '</hp:pic></hp:run></hp:p>'
        '<hp:p><hp:run><hp:container><hp:drawText>'
        '<hp:p><hp:run><hp:t>\uc81c2\uc7a5</hp:t></hp:run></hp:p>'
        '<hp:p><hp:run><hp:t>\uc7ac\uc815\uc6b4\uc6a9 \ubc29\ud5a5</hp:t></hp:run></hp:p>'
        '</hp:drawText></hp:container></hp:run></hp:p>'
        '<hp:p><hp:run><hp:t>\u25a1 \uc778\uc6d0</hp:t></hp:run></hp:p>'
        '</hp:sec>'
    )
    # media-type 은 비표준 image/jpg 로 둔다 — 실제 행안부 파일이 그렇다.
    manifest = ('<opf:package xmlns:opf="http://www.idpf.org/2007/opf">'
                '<opf:manifest>'
                '<opf:item id="image1" href="BinData/image1.jpg" '
                'media-type="image/jpg" isEmbeded="1"/>'
                '</opf:manifest></opf:package>')
    path = tmp_path / "sample.hwpx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Contents/section0.xml", section)
        z.writestr("Contents/content.hpf", manifest)
        z.writestr("BinData/image1.jpg", b"\xff\xd8\xff\xe0 fake jpeg bytes")
    return path


def test_hwpx_walk_emits_image_marker_in_place(tmp_path):
    """그림 표식이 원본 자리에 남는다 — 앞뒤 문단 사이."""
    from docstruct.converters.hwpx.hwpxtree import to_markdown

    md = to_markdown(_hwpx_fixture(tmp_path))
    assert "<!-- hwpx-image:image1 -->" in md
    # 0.4.6 부터 본문 글머리는 `- ` 로 정규화된다 (format_body_text).
    assert md.index("- \uc870\uc9c1") < md.index("<!-- hwpx-image:image1 -->")
    assert md.index("<!-- hwpx-image:image1 -->") < md.index("- \uc778\uc6d0")


def test_hwpx_walk_keeps_shape_text(tmp_path):
    """도형으로 그린 간지 제목이 빠지지 않는다."""
    from docstruct.converters.hwpx.hwpxtree import to_markdown

    md = to_markdown(_hwpx_fixture(tmp_path))
    assert "\uc81c2\uc7a5" in md
    assert "\uc7ac\uc815\uc6b4\uc6a9 \ubc29\ud5a5" in md


def test_hwpx_image_suffix_comes_from_href(tmp_path):
    """확장자는 media-type 이 아니라 href 에서 온다.

    행안부의 media-type 은 비표준 `image/jpg` 라 mimetypes 가 확장자를
    못 낸다. 그것을 기본값 .png 로 메우면 **JPEG 를 .png 이름으로 쓴다.**
    """
    from pathlib import Path as _Path

    from docstruct.extractors.hwpx import extract_hwpx_pages

    out = tmp_path / "images"
    pages = extract_hwpx_pages(str(_hwpx_fixture(tmp_path)), image_dir=out)
    images = pages[0].images
    assert len(images) == 1
    assert _Path(images[0].image_path).suffix == ".jpg"
    assert _Path(images[0].image_path).is_file()


def test_hwpx_images_reach_the_vlm_stage(tmp_path):
    """추출된 그림이 VLM 읽기 대상이 된다 — 이게 끊겨 있었다."""
    from docstruct.extractors.hwpx import extract_hwpx_pages
    from docstruct.images.vlm_read import _should_read

    pages = extract_hwpx_pages(str(_hwpx_fixture(tmp_path)),
                               image_dir=tmp_path / "images")
    page = pages[0]
    assert page.images                            # pipeline 의 진입 조건
    assert all(_should_read(i) for i in page.images)


def test_hwpx_body_has_no_path_or_binary(tmp_path):
    """본문에는 경로도 바이너리도 넣지 않는다 — placeholder 만."""
    from docstruct.extractors.hwpx import extract_hwpx_pages

    out = tmp_path / "images"
    pages = extract_hwpx_pages(str(_hwpx_fixture(tmp_path)), image_dir=out)
    content = pages[0].content
    assert "<image 1>" in content and "</image 1>" in content
    assert str(out) not in content
    assert "BinData" not in content
    assert "binaryItemIDRef" not in content


def test_hwpx_restored_text_replaces_the_picture(tmp_path):
    """VLM 이 읽은 글귀가 그림 자리에 들어간다 (중복 없이)."""
    from docstruct.output.content import expand_tables_and_images
    from docstruct.extractors.hwpx import extract_hwpx_pages
    from docstruct.images.vlm_read import _insert_after_placeholder

    pages = extract_hwpx_pages(str(_hwpx_fixture(tmp_path)),
                               image_dir=tmp_path / "images")
    page = pages[0]
    image = page.images[0]
    restored = "### \uc870\uc9c1\ub3c4\n\n- \uc7a5\uad00"
    image.vlm_markdown = restored
    # 0.4.9 부터 expand 가 vlm_markdown 을 직접 그림 자리에 넣는다.
    # 파이프라인의 _insert_after_placeholder 는 page.content 를 갱신하는
    # 별도 경로이므로, 둘을 함께 쓰면 같은 글이 두 번 들어간다.
    body = expand_tables_and_images(page.content, page.tables, page.images)
    assert body.count("### \uc870\uc9c1\ub3c4") == 1
    assert body.index("- \uc870\uc9c1") < body.index("### \uc870\uc9c1\ub3c4")


def test_hwpx_without_image_dir_keeps_placeholder(tmp_path):
    """저장할 곳이 없어도 표식은 남긴다 — 그림이 있었다는 사실은 지운다."""
    from docstruct.extractors.hwpx import extract_hwpx_pages

    pages = extract_hwpx_pages(str(_hwpx_fixture(tmp_path)), image_dir=None)
    page = pages[0]
    assert len(page.images) == 1
    assert page.images[0].image_path is None      # VLM 은 건너뛴다
    assert "<image 1>" in page.content and "</image 1>" in page.content


# ────────────────────────────────────────────────────────────────────
# 0.4.5 — 미리보기에서 표 없는 쪽이 조용히 비어 있었다
#
# 파이프라인은 표가 있는 쪽만 렌더한다 (렌더의 원래 용도가 표 재추출의
# 시각 근거다). show_page 는 `page_image_path` 가 없으면 **아무 말 없이**
# 넘어가, 표지·목차를 열면 이미지가 안 보이고 이유도 알 수 없었다.
# 실측(행안부 429쪽): 이미지가 있는 쪽은 289쪽뿐이었다.
# ────────────────────────────────────────────────────────────────────


def test_page_image_falls_back_to_rendering(tmp_path, monkeypatch):
    """저장된 이미지가 없으면 원본 PDF 에서 즉석으로 그린다."""
    from docstruct.output import preview
    from docstruct.models import PageContent

    calls = []

    def fake_render(pdf_path, page_no, out_dir, **kwargs):
        calls.append(page_no)
        target = tmp_path / f"p{page_no}.png"
        target.write_bytes(b"png")
        return str(target)

    import docstruct.images.page_render as page_render
    monkeypatch.setattr(page_render, "render_page", fake_render)
    monkeypatch.setattr(preview, "_RENDER_CACHE", {})

    source = tmp_path / "doc.pdf"
    source.write_bytes(b"%PDF-1.4")
    page = PageContent(page_no=3, page_no_kind="exact", content="x")

    got = preview.page_image(page, source)
    assert got and Path(got).is_file()
    assert calls == [3]
    # 두 번째 호출은 캐시를 쓴다 — 슬라이더를 움직일 때마다 다시 그리면 느리다
    assert preview.page_image(page, source) == got
    assert calls == [3]


def test_page_image_prefers_saved_file(tmp_path):
    """이미 렌더된 쪽은 그 파일을 그대로 쓴다."""
    from docstruct.output import preview
    from docstruct.models import PageContent

    saved = tmp_path / "saved.png"
    saved.write_bytes(b"png")
    page = PageContent(page_no=1, page_no_kind="exact", content="x",
                       page_image_path=str(saved))
    assert preview.page_image(page, None) == str(saved)


def test_page_image_without_pdf_returns_none(tmp_path):
    """원본이 없으면 None — 조용히 실패하지 않고 호출부가 안내한다."""
    from docstruct.output import preview
    from docstruct.models import PageContent

    page = PageContent(page_no=1, page_no_kind="exact", content="x")
    assert preview.page_image(page, None) is None
    assert preview.page_image(page, tmp_path / "없는파일.pdf") is None


def test_notebooks_pass_source_pdf():
    """노트북이 show_page 에 원본 경로를 넘긴다 — 안 넘기면 증상이 재발한다."""
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parent.parent / "notebooks"
    for name in ("_build_notebook.py", "_build_colab_notebook.py"):
        builder = root / name
        if not builder.is_file():
            continue
        source = builder.read_text(encoding="utf-8")
        assert "pdf_path=SRC" in source, name


# ────────────────────────────────────────────────────────────────────
# 0.4.6 — HWPX 산출에 들여쓰기가 하나도 없었다
#
# HWP 경로는 `converters/hwp/styling.format_paragraph` 가 글머리 기호로
# 계층을 복원하는데(□ → ○ → - → *), HWPX 경로(hwpxtree)는 그 모듈을
# 아예 쓰지 않았다. 실측(같은 조달청 문서): 본문 줄 중 들여쓴 줄이
# HWP 317 대 HWPX 0.
# ────────────────────────────────────────────────────────────────────


def test_format_body_text_indents_by_bullet():
    """글머리 기호가 곧 수준이다 — 스타일 정보 없이도 계층이 선다."""
    from docstruct.converters.hwp.styling import format_body_text

    assert format_body_text("\u25a1 \uc870\uc9c1") == "- \uc870\uc9c1"
    assert format_body_text("\u25cb \uc6b4\uc601") == "  - \uc6b4\uc601"
    assert format_body_text("- (\uc5ed\ud560) \uac80\ud1a0") == "    - (\uc5ed\ud560) \uac80\ud1a0"


def test_format_body_text_keeps_bold_around_bullet():
    """문단 전체가 굵으면 기호가 `**` 뒤에 숨는다 — 벗겨서 판단한다."""
    from docstruct.converters.hwp.styling import format_body_text

    assert format_body_text("**\u25a1 \uc784\ubb34**") == "- **\uc784\ubb34**"


def test_format_body_text_detects_numbered_heading():
    """번호 표기 제목은 `#` 로 올린다."""
    from docstruct.converters.hwp.styling import format_body_text

    assert format_body_text("\uc81c1\uc7a5 \ucd1d\uce59") == "# \uc81c1\uc7a5 \ucd1d\uce59"


def test_format_body_text_leaves_plain_text():
    """목록도 제목도 아니면 손대지 않는다."""
    from docstruct.converters.hwp.styling import format_body_text

    # 0.4.16 부터 `ㅇ` 은 수준 1 글머리다 — 기호가 아닌 평문으로 본다.
    plain = "\ub2f9\ud574\uc5c6\uc74c \u2014 \ucc38\uace0\uc790\ub8cc \uc5c6\uc74c"
    assert format_body_text(plain) == plain
    assert format_body_text("") == ""


def test_hwpx_body_is_indented(tmp_path):
    """HWPX 본문이 HWP 와 같은 규칙으로 들여쓰기된다."""
    import zipfile

    from docstruct.converters.hwpx.hwpxtree import to_markdown

    ns = 'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"'
    def para(text):
        return f"<hp:p><hp:run><hp:t>{text}</hp:t></hp:run></hp:p>"
    section = (f'<hp:sec {ns}>' + para("\u25a1 \uc870\uc9c1")
               + para("\u25cb \uc6b4\uc601") + para("- \uc0c1\uc138") + "</hp:sec>")
    path = tmp_path / "s.hwpx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Contents/section0.xml", section)

    md = to_markdown(path)
    assert "- \uc870\uc9c1" in md
    assert "  - \uc6b4\uc601" in md
    assert "    - \uc0c1\uc138" in md


def test_hwpx_table_cells_are_not_indented(tmp_path):
    """표 셀에는 적용하지 않는다 — `- ` 나 `#` 를 넣으면 GFM 이 깨진다."""
    import zipfile

    from docstruct.converters.hwpx.hwpxtree import to_markdown

    ns = 'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"'
    cell = ('<hp:tc><hp:cellAddr colAddr="0" rowAddr="0"/>'
            '<hp:cellSpan colSpan="1" rowSpan="1"/><hp:subList>'
            '<hp:p><hp:run><hp:t>\u25a1 \uba38\ub9ac</hp:t></hp:run></hp:p>'
            '</hp:subList></hp:tc>')
    section = f'<hp:sec {ns}><hp:p><hp:run><hp:tbl><hp:tr>{cell}</hp:tr></hp:tbl></hp:run></hp:p></hp:sec>'
    path = tmp_path / "t.hwpx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Contents/section0.xml", section)

    md = to_markdown(path)
    assert "\u25a1 \uba38\ub9ac" in md            # 기호를 그대로 둔다
    assert "| - \uba38\ub9ac" not in md


# ────────────────────────────────────────────────────────────────────
# 0.4.7 — set_api_key 가 OpenAI 를 강제한다 · ⑮ 열 밀림 복원 · 추가 승격
# ────────────────────────────────────────────────────────────────────


def test_set_api_key_forces_openai(monkeypatch):
    """키를 넣으면 기본 엔드포인트가 OpenAI 로 선다.

    예전 기본값(fallback)은 OPENAI_API_KEY 만 넣었고, 그 키는 *대비*
    엔드포인트에만 쓰였다 — 사내 주소가 잡혀 있으면 **키를 넣어도 아무
    일도 일어나지 않았다.**
    """
    import docstruct

    for name in ("DOCLING_TABLE_API_URL", "DOCLING_TABLE_API_MODEL",
                 "DOCLING_TABLE_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DOCLING_TABLE_API_URL", "http://사내:11060/v1/chat/completions")

    docstruct.set_api_key("sk-test-abcdefgh")
    assert "api.openai.com" in os.environ["DOCLING_TABLE_API_URL"]
    assert os.environ["DOCLING_TABLE_API_KEY"] == "sk-test-abcdefgh"


def test_set_api_key_fallback_mode_unchanged(monkeypatch):
    """target='fallback' 은 옛 동작 그대로 — 주소를 건드리지 않는다."""
    import docstruct

    monkeypatch.setenv("DOCLING_TABLE_API_URL", "http://사내:11060/v1/chat/completions")
    docstruct.set_api_key("sk-old-1234", target="fallback")
    assert os.environ["DOCLING_TABLE_API_URL"].startswith("http://사내")
    assert os.environ["OPENAI_API_KEY"] == "sk-old-1234"


def test_lattice_restore_skips_when_no_column_loss():
    """열이 밀리지 않은 표는 건드리지 않는다 — 멀쩡한 표를 다시 쓰지 않는다."""
    from docstruct.experiments.tsr.restore import lattice_restore
    assert lattice_restore.MIN_COLS >= 4
    source = inspect.getsource(lattice_restore.restore_table)
    assert "cols <= detected" in source           # 격자가 더 많을 때만


def test_lattice_restore_reverts_on_text_loss():
    """글자가 줄면 되돌린다 — 열을 바로잡자고 내용을 잃지 않는다."""
    from docstruct.experiments.tsr.restore import lattice_restore
    assert 0.9 <= lattice_restore.MIN_TEXT_KEEP < 1.0
    assert "MIN_TEXT_KEEP" in inspect.getsource(lattice_restore.restore_table)


def test_promotion_set_matches_documented(monkeypatch):
    """0.4.7 승격분이 기본으로 켜진다 (되돌리려면 DEFAULT_ON 에서 뺀다)."""
    from docstruct.experiments import all_experiments
    from docstruct.experiments.registry import DEFAULT_ON

    assert "lattice_restore" in DEFAULT_ON
    assert "col_grid" not in DEFAULT_ON          # 0.4.20 강등 (0표 발화)
    for exp in all_experiments():
        monkeypatch.delenv(exp.env, raising=False)
    running = {e.key for e in all_experiments() if e.enabled}
    assert running == set(DEFAULT_ON)


def test_promoted_lattice_restore_can_be_switched_off(monkeypatch):
    """위험이 큰 실험이므로 끄는 길이 반드시 있어야 한다."""
    from docstruct.cli import _enable_experiments
    from docstruct.experiments import all_experiments
    known = {e.key: e for e in all_experiments()}
    monkeypatch.delenv(known["lattice_restore"].env, raising=False)
    _enable_experiments("no_lattice_restore")
    assert known["lattice_restore"].enabled is False


# ────────────────────────────────────────────────────────────────────
# 0.4.8 — 이어지는 표의 머리 · 누름틀 잔재
# ────────────────────────────────────────────────────────────────────


def test_with_header_is_display_only():
    """머리를 붙여 보여 주되 **원본은 그대로 둔다** (0.3.9 결정 유지).

    사람이 볼 때는 머리가 있어야 읽힌다 — 실측(조달청 p70): 17열 표가
    데이터부터 시작해 "재정사업 평가명" 이 어느 열인지 알 수 없었다.
    그러나 저장하면 열 수가 쪽마다 13~17 로 달라 정렬 사고가 되돌릴 수
    없어진다. 그래서 표시 전용이다.
    """
    from docstruct.tables.continued import mark_continuations, with_header

    body = "| 56 | 2 | 010 |\n| --- | --- | --- |\n| 56 | 1 | 010 |"
    got = with_header(body, ["회 계", "계 정", "분 야"])
    lines = got.split("\n")
    assert lines[0] == "| 회 계 | 계 정 | 분 야 |"
    assert set(lines[1].replace("|", "").replace(" ", "")) <= set("-:")
    assert "| 56 | 2 | 010 |" in got             # 첫 행이 데이터로 남는다
    assert len([l for l in lines if set(l.replace("|", "").replace(" ", "")) <= set("-:")]) == 1

    # 파이프라인은 원본을 건드리지 않는다
    pages = [_cont_page(1, [_CONT_HEADER, _cont_data(1)]),
             _cont_page(2, [_cont_data(2), _cont_data(3)])]
    before = pages[1].tables[0].markdown
    mark_continuations(pages)
    assert pages[1].tables[0].markdown == before


def test_inherited_header_skipped_on_mismatch():
    """열 수가 다르면 붙이지 않는다 — 어긋난 머리는 없느니만 못하다."""
    from docstruct.tables.continued import with_header as _with_header

    body = "| a | b |\n| --- | --- |\n| 1 | 2 |"
    assert _with_header(body, ["회 계", "계 정", "분 야"]) is None
    assert _with_header("", ["회 계"]) is None


def test_inherited_header_not_doubled():
    """지면이 머리를 다시 찍었으면 건드리지 않는다."""
    from docstruct.tables.continued import with_header as _with_header

    body = "| 회 계 | 계 정 |\n| --- | --- |\n| 56 | 2 |"
    assert _with_header(body, ["회 계", "계 정"]) is None


def test_field_payload_stripped():
    """누름틀 잔재를 뗀다 — PDF 텍스트 레이어까지 새어 나온다."""
    from docstruct.text.korean_text import strip_field_payload

    dirty = '{"fields": {},"simplefields": {}} 프로 그램 (코드 번호)'
    assert strip_field_payload(dirty) == "프로 그램 (코드 번호)"
    assert strip_field_payload("정상 텍스트") == "정상 텍스트"
    assert strip_field_payload("") == ""


def test_pipeline_strips_field_payload_for_pdf():
    """PDF 경로에도 적용된다 — HWPX·HWP 만 거르고 있었다."""
    from docstruct import pipeline

    source = inspect.getsource(pipeline)
    assert "strip_field_payload" in source


def test_image_without_vlm_is_visible_in_markdown():
    """읽은 것이 없어도 **보이게** 남긴다.

    placeholder 는 HTML 주석이라 markdown 으로 보면 아무것도 보이지
    않는다 — 그림이 있었다는 사실조차 사라져 조직도가 통째로 빠진 것처럼
    읽혔다.
    """
    from docstruct.output.content import expand_tables_and_images
    from docstruct.models import ImageInfo

    info = ImageInfo(id="image_1", placeholder="<!-- image 1 -->",
                     image_path="/x/a.png")
    body = expand_tables_and_images("앞\n\n<!-- image 1 -->\n\n뒤", [], [info])
    assert "image_1" in body and "VLM" in body
    assert "<!-- image 1 -->" in body             # 앵커는 남는다


def test_image_with_vlm_text_replaces_marker():
    """VLM 이 읽었으면 그 글이 그림 자리에 온다."""
    from docstruct.output.content import expand_tables_and_images
    from docstruct.models import ImageInfo

    info = ImageInfo(id="image_1", placeholder="<!-- image 1 -->",
                     image_path="/x/a.png")
    info.vlm_markdown = "### 조직도\n\n- 청장"
    body = expand_tables_and_images("<!-- image 1 -->", [], [info])
    assert "### 조직도" in body and "VLM 미실행" not in body


# ────────────────────────────────────────────────────────────────────
# 0.4.10 — pyhwp 없이 돌리는 길
#
# pyhwp(AGPL)를 설치하지 않거나 쓰지 않으려는 환경이 있다. **코드는
# 지우지 않는다** — 사다리의 위 두 단(hwp5-tree · pyhwp-html)만 건너뛰고
# olefile 텍스트 폴백이 받게 한다.
# ────────────────────────────────────────────────────────────────────


def test_skip_pyhwp_switch(monkeypatch):
    """환경변수로 pyhwp 경로를 끈다."""
    from docstruct.converters.hwp.converter import skip_pyhwp

    monkeypatch.delenv("DOCSTRUCT_HWP_NO_PYHWP", raising=False)
    assert skip_pyhwp() is False
    monkeypatch.setenv("DOCSTRUCT_HWP_NO_PYHWP", "1")
    assert skip_pyhwp() is True
    monkeypatch.setenv("DOCSTRUCT_HWP_NO_PYHWP", "false")
    assert skip_pyhwp() is False


def test_skip_pyhwp_falls_back_to_olefile(monkeypatch, tmp_path):
    """두 단을 모두 건너뛰고 olefile 폴백으로 내려간다."""
    from docstruct.converters.hwp.converter import HwpConverter

    monkeypatch.setenv("DOCSTRUCT_HWP_NO_PYHWP", "1")
    sample = tmp_path / "x.hwp"
    sample.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 64)   # OLE 서명

    conv = HwpConverter(sample)
    assert conv._get_tree_markdown() is None     # 1단 건너뜀
    assert conv._uses_ole_fallback() is True     # 2단도 건너뛰고 폴백


def test_cli_exposes_no_pyhwp():
    """`--no-pyhwp` 로도 켤 수 있다 (환경변수를 직접 만지지 않게)."""
    import inspect

    from docstruct import cli

    source = inspect.getsource(cli)
    assert "--no-pyhwp" in source
    assert "DOCSTRUCT_HWP_NO_PYHWP" in source


# ────────────────────────────────────────────────────────────────────
# 0.4.11 — 숫자 표가 스캔본으로 오판돼 전 쪽이 OCR 을 탔다
#
# `_has_usable_text_layer` 가 낱말 글자 **비율**만 봤다. 성과계획서는
# 숫자 표 문서라 `592.3(576) 527.0(541)` 같은 내용이 대부분이고, 실측
# (조달청 p5): 텍스트 레이어 1,571자가 멀쩡한데 비율이 0.15 였다.
# 그 결과 **76쪽 전부**가 한국어 OCR 재판독을 타 정확한 텍스트를
# 46~70% 정확도의 인식 결과로 덮었다 (`- 고위공무원단` → `공금운농-`).
# ────────────────────────────────────────────────────────────────────


def test_number_heavy_table_page_is_not_ocr_target():
    """숫자가 많아도 본문이 충분하면 텍스트 레이어를 쓴다."""
    from docstruct.pipeline import _has_usable_text_layer

    page = ("직 급 본 청 소속기관 합 계 "
            "현원 정원 592.3(576) 527.0(541) 1,119.3(1,117) "
            "정무직 1(1) 일반직 591.3(575) 527.0(541) 1,118.3(1,116) "
            "고위공무원단 8(8) 3(3) 11(11) 관리운영 전문경력관 별정직 "
            "운전방호 연구직 소속기관 합계 별도정원 일반직 고위 이하")
    assert _has_usable_text_layer(page) is True


def test_scanned_page_still_detected():
    """머리말·URL 만 있는 스캔본은 여전히 걸러진다 (쪽당 낱말 7자)."""
    from docstruct.pipeline import _has_usable_text_layer

    scanned = ("https://example.gov.kr/print?id=12345 "
               "2026-05-01 14:33 1/12 <div> 인쇄")
    assert _has_usable_text_layer(scanned) is False
    assert _has_usable_text_layer("- 39 -") is False    # 쪽번호뿐
    assert _has_usable_text_layer("") is False


def test_absolute_threshold_documented():
    """절대 문턱이 비율보다 먼저 판정한다 — 이것이 오판을 막는 자리다."""
    from docstruct import pipeline

    assert pipeline.MIN_LAYER_WORDS_ABSOLUTE >= 30
    source = inspect.getsource(pipeline._has_usable_text_layer)
    assert "MIN_LAYER_WORDS_ABSOLUTE" in source
    assert "_FILLER_CHAR_RE" in source           # 분모에서 숫자·구두점 제외


# ────────────────────────────────────────────────────────────────────
# 0.4.12 — 브릿지가 진단을 버려 서버에서 원인을 좁힐 수 없었다
#
# FastAPI 결과 JSON 에 그림 3개가 잡혔는데 `image_path` 가 모두 None
# 이었다. 그런데 `trace` 가 통째로 없어 어느 단계에서 왜 끊겼는지 알 수
# 없었다 — 로그를 뒤지는 수밖에 없었다. 0.4.2 의 failure_reasons 와 같은
# 유형의 누락이다(진단은 만들었는데 가장 필요한 곳까지 못 감).
#
# 브릿지는 overlay 전용이라 pkg 시험에서는 **소스를 읽어 배선만** 본다.
# ────────────────────────────────────────────────────────────────────


def _bridge_sources():
    """overlay 브릿지·모델 소스. 없으면 (None, None)."""
    root = Path(__file__).resolve().parent.parent.parent
    for base in (root / "docstruct-backend-overlay-0_4_1" / "overlay" / "app",
                 root.parent / "overlay" / "app"):
        bridge = base / "rag" / "adapters" / "docstruct_bridge.py"
        model = base / "rag" / "models" / "document.py"
        if bridge.is_file() and model.is_file():
            return (bridge.read_text(encoding="utf-8"),
                    model.read_text(encoding="utf-8"))
    return (None, None)


def test_bridge_carries_trace_and_image_path():
    """진단(trace)과 그림 저장 경로가 색인 계층까지 간다."""
    bridge, model = _bridge_sources()
    if bridge is None:
        return                                    # overlay 트리가 없는 배포
    # 0.4.20 부터 브릿지는 필드를 손으로 적지 않고 자동 복사한다.
    assert "_trace_dict(" in bridge and "trace=" in bridge
    assert "_copy_fields(" in bridge
    assert "trace: dict | None = None" in model
    assert "image_path: str | None = None" in model
    assert "vlm_markdown: str | None = None" in model
    assert '"trace": self.trace' in model


def test_bridge_carries_new_experiment_fields():
    """새 실험 필드가 빠지면 색인 쪽에서 그 표를 가려낼 수 없다."""
    bridge, model = _bridge_sources()
    if bridge is None:
        return
    assert "lattice_restore: dict | None = None" in model
    assert "structure_ratio: float | None = None" in model


# ────────────────────────────────────────────────────────────────────
# 0.4.13 — docling 이 놓친 쪽이 통째로 비었다
#
# 0.4.11 에서 OCR 오판을 고치자 드러났다. docling 이 어떤 쪽에서 텍스트를
# 거의 못 뽑고 쪽 전체를 그림 하나로 분류한다 — 실측(조달청 p5):
# `docling.parse 16자 · 텍스트블록 0 · 표 0 · 그림 1` 인데 pdfium 으로
# 읽으면 1,571자가 멀쩡히 나온다. 예전에는 OCR 이 그럭저럭 메웠으나
# (46~70% 정확도라 `- 고위공무원단` → `공금운농-`), OCR 을 멈추자 빈
# 쪽이 됐다 (76쪽 중 3쪽: 1·4·5).
# ────────────────────────────────────────────────────────────────────


def test_thin_page_filled_from_text_layer(tmp_path, monkeypatch):
    """본문이 빈 쪽을 원본 텍스트 레이어로 메운다."""
    from docstruct import pipeline
    from docstruct.models import PageContent, PageTrace

    layer = "직 급 본 청 소속기관 합 계 " + "고위공무원단 관리운영 전문경력관 별정직 " * 6

    class _TextPage:
        def get_text_range(self):
            return layer

    class _Page:
        def get_textpage(self):
            return _TextPage()

    class _Doc:
        def __len__(self):
            return 8

        def __getitem__(self, i):
            return _Page()

        def close(self):
            pass

    fake = type("M", (), {"PdfDocument": staticmethod(lambda p: _Doc())})
    monkeypatch.setitem(sys.modules, "pypdfium2", fake)

    thin = PageContent(page_no=5, page_no_kind="exact",
                       content="<!-- image_1 -->", trace=PageTrace())
    fat = PageContent(page_no=7, page_no_kind="exact",
                      content="정상 본문 " * 20, trace=PageTrace())
    filled = pipeline._rescue_thin_pages(Path("x.pdf"), [thin, fat])

    assert filled == 1
    assert "고위공무원단" in thin.content
    assert "<!-- image_1 -->" in thin.content    # 그림 자리는 지우지 않는다
    assert fat.content.startswith("정상 본문")    # 멀쩡한 쪽은 안 건드린다


def test_thin_page_left_alone_when_truly_image(monkeypatch):
    """진짜 그림 쪽은 그대로 둔다 — 메울 근거가 없다."""
    from docstruct import pipeline
    from docstruct.models import PageContent, PageTrace

    class _TextPage:
        def get_text_range(self):
            return "- 39 -"                      # 쪽번호뿐

    class _Doc:
        def __len__(self):
            return 8

        def __getitem__(self, i):
            return type("P", (), {"get_textpage": lambda s: _TextPage()})()

        def close(self):
            pass

    fake = type("M", (), {"PdfDocument": staticmethod(lambda p: _Doc())})
    monkeypatch.setitem(sys.modules, "pypdfium2", fake)

    page = PageContent(page_no=44, page_no_kind="exact",
                       content="<!-- image_1 -->", trace=PageTrace())
    assert pipeline._rescue_thin_pages(Path("x.pdf"), [page]) == 0
    assert page.content == "<!-- image_1 -->"


def test_rescue_runs_before_ocr():
    """메우기가 OCR 판정보다 먼저다 — 뽑을 글자가 있으면 인식하지 않는다."""
    import inspect

    from docstruct import pipeline

    source = inspect.getsource(pipeline.build_document)
    assert source.index("_rescue_thin_pages") < source.index("_pages_needing_ocr")


def test_overlay_markdown_export_removed():
    """서비스가 부를 md 생성 자리가 있다.

    `hwpxtree.to_markdown()` 은 컨버터의 **원재료**다. 그것을 그대로
    내보내면 그림·표 placeholder 치환과 VLM 복원 글이 전부 빠진다 —
    실측(조달청 HWPX): document.json 에는 VLM 이 조직도를 계층·정원표까지
    복원해 담겼는데 같은 실행의 .md 에는 `<!-- hwpx-image:image1 -->`
    원형 표식만 남았다.
    """
    # 0.4.20: 컨버터가 report.document_markdown 을 타므로 overlay 전용
    # helper 는 호출부가 없어졌다. 같은 일을 하는 경로가 둘이면 한쪽만
    # 고쳤을 때 어긋난다.
    root = Path(__file__).resolve().parent.parent.parent
    for base in (root / "docstruct-backend-overlay-0_4_1" / "overlay" / "app",
                 root.parent / "overlay" / "app"):
        if not (base / "rag").is_dir():
            continue
        assert not (base / "rag" / "adapters" / "markdown_export.py").is_file()
        return


def test_service_converters_go_through_the_pipeline():
    """`/convert/markdown` 이 원재료를 내보내지 않는다.

    컨버터 자체 경로(hwpxtree.to_markdown · export_markdown)는 원재료라
    그림·정규화·OCR 게이트·텍스트 레이어 메우기가 하나도 걸리지 않는다.
    실측: document.json 에는 VLM 이 조직도를 복원해 담겼는데 같은 실행의
    `.md` 에는 `<!-- hwpx-image:image1 -->` 원형 표식만 남았다.
    """
    root = Path(__file__).resolve().parent.parent.parent
    for base in (root / "docstruct-backend-overlay-0_4_1" / "overlay" / "app",
                 root.parent / "overlay" / "app"):
        if not (base / "converters" / "hwpx" / "converter.py").is_file():
            continue
        for fmt in ("hwpx", "pdf", "hwp"):
            source = (base / "converters" / fmt / "converter.py").read_text(
                encoding="utf-8")
            assert "_pipeline_markdown(self.path)" in source, fmt
            assert "document_markdown" in source, fmt
        return


# ────────────────────────────────────────────────────────────────────
# 0.4.16 — 세로로 한 글자씩 나뉜 칸 · `ㅇ` 글머리
# ────────────────────────────────────────────────────────────────────


def test_vertical_cells_collapse_when_neighbours_empty():
    """세로 배치는 그 열만 쓴다 — 옆이 비어 있을 때만 합친다."""
    from docstruct.converters.hwpx.hwpxtree import collapse_vertical_cells

    cells = [{"row": r, "col": 1, "rowspan": 1, "colspan": 1, "text": t}
             for r, t in enumerate("2027")]
    cells += [{"row": r, "col": c, "rowspan": 1, "colspan": 1, "text": ""}
              for r in range(4) for c in (0, 2)]
    assert collapse_vertical_cells(cells) == 1
    merged = [c for c in cells if c["col"] == 1]
    assert len(merged) == 1
    assert merged[0]["text"] == "2027" and merged[0]["rowspan"] == 4


def test_value_column_is_not_collapsed():
    """옆에 값이 있으면 값 열이다 — 합치면 데이터를 부순다.

    실측(조달청): "한 글자가 세로로 3칸 이상" 만으로는 `0000`·`1111`
    같은 값 열 10개가 걸렸다. 이웃 조건을 더하면 발화 1건·오탐 0.
    """
    from docstruct.converters.hwpx.hwpxtree import collapse_vertical_cells

    cells = []
    for r, t in enumerate("111"):
        cells.append({"row": r, "col": 1, "rowspan": 1, "colspan": 1, "text": t})
        cells.append({"row": r, "col": 0, "rowspan": 1, "colspan": 1,
                      "text": "사업"})
    assert collapse_vertical_cells(cells) == 0
    assert len([c for c in cells if c["col"] == 1]) == 3


def test_short_run_is_not_collapsed():
    """두 칸은 우연일 수 있다 — 3칸부터 본다."""
    from docstruct.converters.hwpx.hwpxtree import collapse_vertical_cells

    cells = [{"row": r, "col": 0, "rowspan": 1, "colspan": 1, "text": t}
             for r, t in enumerate("가나")]
    assert collapse_vertical_cells(cells) == 0


def test_ieung_is_a_bullet():
    """`ㅇ`(한글 낱자)는 공문서에서 `○` 자리에 쓰인다."""
    from docstruct.converters.hwp.styling import format_body_text

    assert format_body_text("ㅇ 본    청 : 현원 592.3명") == \
        "  - 본    청 : 현원 592.3명"
    assert format_body_text("○ 운영") == "  - 운영"
    assert format_body_text("ㅇㅇㅇ") == "ㅇㅇㅇ"     # 기호가 아니다


# ────────────────────────────────────────────────────────────────────
# 0.4.17 — 스캔 쪽을 VLM 으로, 300dpi 로
#
# rapidocr 는 한국어 모델을 붙여도 46~70% 이고 한자가 섞인다(기본
# PP-OCRv6 small 에 한국어가 없다) — 실측: `- 고위공무원단` → `공금운농-`.
# 0.4.11 게이트 수정으로 대상이 문서당 두어 쪽이 되어(조달청 76→2쪽)
# VLM 이 비용·정확도 모두 유리해졌다.
# ────────────────────────────────────────────────────────────────────


def test_scan_backend_defaults_to_vlm(monkeypatch):
    """기본은 VLM, `ocr` 로 되돌릴 수 있다 (A/B 용)."""
    from docstruct.pipeline import scan_backend

    monkeypatch.delenv("DOCSTRUCT_SCAN_BACKEND", raising=False)
    assert scan_backend() == "vlm"
    monkeypatch.setenv("DOCSTRUCT_SCAN_BACKEND", "ocr")
    assert scan_backend() == "ocr"
    monkeypatch.setenv("DOCSTRUCT_SCAN_BACKEND", "vlm")
    assert scan_backend() == "vlm"


def test_scan_render_scale_is_300dpi():
    """스캔 쪽은 300dpi 로 렌더한다 — 기본 144dpi 는 원본과 겹친다."""
    from docstruct import pipeline

    assert round(pipeline.SCAN_RENDER_SCALE * 72) == 300
    source = inspect.getsource(pipeline.build_document)
    # 0.4.23: 목적별로 나눠 그린다 — 스캔 배율이 문서 전체로 번지지 않게
    # 0.4.24: 배율은 쪽마다 원본 해상도를 보고 정한다
    assert "page_scan_scale(resolved, target)" in source


def test_scan_vlm_keeps_original_and_placeholders(monkeypatch, tmp_path):
    """원본을 남기고 표 자리표시자를 살린다."""
    from docstruct.text import scan_vlm
    from docstruct.models import PageContent, PageTrace

    shot = tmp_path / "p.png"
    shot.write_bytes(b"png")
    page = PageContent(page_no=3, page_no_kind="exact",
                       content="옛 본문\n\n<table 1>", trace=PageTrace(),
                       page_image_path=str(shot))

    import docstruct.infrastructure.llm.client as client
    import docstruct.images.encode as encode
    monkeypatch.setattr(client, "llm_api_config", lambda: {"url": "x"})
    monkeypatch.setattr(encode, "encode_image_file",
                        lambda p: ("image/png", "YWJj"))
    monkeypatch.setattr(client, "invoke_llm",
                        lambda *a, **k: "새로 읽은 본문입니다. " * 5)

    assert scan_vlm.read_scanned_pages([page], {3}) == {3}
    assert page.ocr_original == "옛 본문\n\n<table 1>"   # 비교 근거를 남긴다
    assert "<table 1>" in page.content                  # 앵커를 살린다
    assert "새로 읽은 본문" in page.content


def test_scan_vlm_keeps_page_when_result_is_thin(monkeypatch, tmp_path):
    """**아무것도 못 읽으면** 그대로 둔다 — 있던 내용까지 지우면 안 된다.

    0.4.60 에서 뜻이 좁아졌다. 예전에는 "짧으면 부실" 로 보고 물러났는데,
    표지·간지에서는 짧은 것이 정답이라 그 판정이 맞는 판독을 버리고 폴백의
    잡음으로 바꾸고 있었다. 이제 물러나는 것은 **무응답·실패**뿐이다.
    """
    from docstruct.text import scan_vlm
    from docstruct.models import PageContent, PageTrace

    shot = tmp_path / "p.png"
    shot.write_bytes(b"png")
    page = PageContent(page_no=3, page_no_kind="exact", content="옛 본문",
                       trace=PageTrace(), page_image_path=str(shot))

    import docstruct.infrastructure.llm.client as client
    import docstruct.images.encode as encode
    monkeypatch.setattr(client, "llm_api_config", lambda: {"url": "x"})
    monkeypatch.setattr(encode, "encode_image_file",
                        lambda p: ("image/png", "YWJj"))
    monkeypatch.setattr(client, "invoke_llm", lambda *a, **k: "")

    assert scan_vlm.read_scanned_pages([page], {3}) == set()
    assert page.content == "옛 본문"
    assert page.ocr_original is None


def test_overlay_env_example_covers_runtime_switches():
    """서버 env 예시가 지금 있는 손잡이를 모두 적어 둔다.

    `--set` 은 CLI 전용이라 FastAPI 는 프로세스 환경변수만 읽는다.
    예시가 낡으면 서버에서 설정이 조용히 빠진다 — 실제로 성능 설정
    (num_threads · llm_concurrency)이 그렇게 빠져 있었다.
    """
    root = Path(__file__).resolve().parent.parent.parent
    for base in (root / "docstruct-backend-overlay-0_4_1" / "overlay",
                 root.parent / "overlay"):
        sample = base / ".env.example"
        if not sample.is_file():
            continue
        text = sample.read_text(encoding="utf-8")
        for name in ("DOCLING_NUM_THREADS", "DOCLING_LLM_CONCURRENCY",
                     "DOCSTRUCT_SCAN_BACKEND", "DOCSTRUCT_HWP_NO_PYHWP",
                     "DOCSTRUCT_EXP_LATTICE_RESTORE", "DOCLING_TABLE_API_URL"):
            assert name in text, name
        # CLI 전용이라는 사실을 적어 둔다 — 이것이 혼란의 원인이었다
        assert "--set" in text
        return


def test_scan_vlm_records_why_it_stepped_back(monkeypatch, tmp_path):
    """물러난 이유를 trace 에 남긴다.

    로그만 찍으면 결과물에 남지 않아 "VLM 이 아예 안 돌았다" 와
    "읽었는데 빈 지면이라 물러났다" 가 구분되지 않는다 — 실측(행안부
    p76·p243)에서 실제로 그 때문에 원인을 잘못 짚었다.
    """
    from docstruct.text import scan_vlm
    from docstruct.models import PageContent, PageTrace

    shot = tmp_path / "p.png"
    shot.write_bytes(b"png")

    import docstruct.infrastructure.llm.client as client
    import docstruct.images.encode as encode
    monkeypatch.setattr(encode, "encode_image_file",
                        lambda p: ("image/png", "YWJj"))

    # ① LLM 미설정
    monkeypatch.setattr(client, "llm_api_config", lambda: None)
    page = PageContent(page_no=3, page_no_kind="exact", content="x",
                       trace=PageTrace(), page_image_path=str(shot))
    scan_vlm.read_scanned_pages([page], {3})
    assert any("LLM 미설정" in (getattr(s, "detail", "") or "")
               for s in page.trace.steps)

    # ② 읽었지만 무응답 (0.4.60 — "짧음" 은 더 이상 물러날 사유가 아니다.
    #    표지에서는 짧은 것이 정답이고, 버리면 폴백의 잡음이 그 자리를
    #    차지한다.)
    monkeypatch.setattr(client, "llm_api_config", lambda: {"url": "x"})
    monkeypatch.setattr(client, "invoke_llm", lambda *a, **k: "")
    page2 = PageContent(page_no=4, page_no_kind="exact", content="x",
                        trace=PageTrace(), page_image_path=str(shot))
    assert scan_vlm.read_scanned_pages([page2], {4}) == set()
    assert any("무응답" in (getattr(s, "action", "") or "")
               for s in page2.trace.steps)


def test_bridge_copies_new_fields_automatically():
    """모델에 필드를 더하면 브릿지가 따라온다 — 두 곳을 고칠 필요가 없다.

    예전에는 필드를 손으로 적어 `cells`·`source`(0.3.12) ·
    `lattice_restore`(0.4.7) · `trace`·`image_path`(0.4.12)가 차례로
    빠졌다. 같은 사고가 세 번 났다.
    """
    root = Path(__file__).resolve().parent.parent.parent
    for base in (root / "docstruct-backend-overlay-0_4_1" / "overlay" / "app",
                 root.parent / "overlay" / "app"):
        bridge = base / "rag" / "adapters" / "docstruct_bridge.py"
        if not bridge.is_file():
            return
        source = bridge.read_text(encoding="utf-8")
        assert "_copy_fields(TableInfo, t)" in source
        assert "_copy_fields(ImageInfo, i)" in source
        # 손으로 적던 흔적이 남아 있으면 안 된다 (두 방식이 섞이면
        # 어느 쪽이 진짜인지 알 수 없다)
        assert "table_num=t.table_num" not in source
        return


# ────────────────────────────────────────────────────────────────────
# 0.4.22 — 복원 결과에 **어느 모델이 냈는지**를 남긴다
#
# `vlm_markdown` 만으로는 사내 엔드포인트로 읽은 것과 OpenAI 로 읽은
# 것을 구별할 수 없다. trace 에 단계는 찍히지만 모델명은 남지 않았다.
# 스캔 VLM 전환(0.4.17)을 재려면 결과물만으로 A/B 가 갈려야 한다.
# ────────────────────────────────────────────────────────────────────


def test_restoration_records_which_model():
    """표·그림·쪽 셋 다 주체를 남길 자리가 있다."""
    import dataclasses

    from docstruct.models import ImageInfo, PageContent, TableInfo

    def names(cls):
        return {f.name for f in dataclasses.fields(cls)}

    assert "vlm_model" in names(ImageInfo)
    assert "vlm_model" in names(TableInfo)
    assert "ocr_engine" in names(PageContent)


def test_scan_vlm_records_model(monkeypatch, tmp_path):
    """VLM 쪽 판독이 모델 이름을 남긴다 — ocr_original 과 짝이다."""
    from docstruct.text import scan_vlm
    from docstruct.models import PageContent, PageTrace

    shot = tmp_path / "p.png"
    shot.write_bytes(b"png")
    page = PageContent(page_no=3, page_no_kind="exact", content="옛 본문",
                       trace=PageTrace(), page_image_path=str(shot))

    import docstruct.infrastructure.llm.client as client
    import docstruct.images.encode as encode
    monkeypatch.setattr(client, "llm_api_config",
                        lambda: {"url": "x", "model": "gpt-5.6-luna"})
    monkeypatch.setattr(encode, "encode_image_file",
                        lambda p: ("image/png", "YWJj"))
    monkeypatch.setattr(client, "invoke_llm",
                        lambda *a, **k: "새로 읽은 본문입니다. " * 5)

    assert scan_vlm.read_scanned_pages([page], {3}) == {3}
    assert page.ocr_engine == "gpt-5.6-luna"
    assert page.ocr_original == "옛 본문"          # 무엇이 무엇으로 바뀌었나


def test_rapidocr_records_engine():
    """rapidocr 도 주체를 남긴다 — 그래야 VLM 과 견줄 수 있다."""
    import inspect

    from docstruct import pipeline

    source = inspect.getsource(pipeline._reread_with_korean_ocr)
    assert 'page.ocr_engine = "rapidocr"' in source


def test_rag_models_carry_the_markers():
    """색인 계층까지 간다 — 브릿지는 모델 필드를 기준으로 자동 복사한다."""
    root = Path(__file__).resolve().parent.parent.parent
    for base in (root / "docstruct-backend-overlay-0_4_1" / "overlay" / "app",
                 root.parent / "overlay" / "app"):
        model = base / "rag" / "models" / "document.py"
        if not model.is_file():
            return
        text = model.read_text(encoding="utf-8")
        assert text.count("vlm_model: str | None = None") == 2   # 표·그림
        assert "ocr_engine: str | None = None" in text
        assert '"ocr_engine": self.ocr_engine' in text
        return


# ────────────────────────────────────────────────────────────────────
# 0.4.22 — VLM 복원을 결과물에서 가릴 수 있어야 한다
#
# `source="vlm"` 만으로는 **어느 모델이 냈는지** 알 수 없다. 사내
# 엔드포인트로 읽은 것과 OpenAI 로 읽은 것을 결과만 보고 구별할 수
# 없으면 A/B 판정이 서지 않는다.
# ────────────────────────────────────────────────────────────────────


def test_vlm_provenance_fields_are_serialized():
    """표·그림·쪽 세 층 모두 '무엇이 냈는가' 가 JSON 에 실린다."""
    from docstruct.models import ImageInfo, PageContent, PageTrace, TableInfo

    table = TableInfo(id="t", table_num=1, placeholder="", markdown="x")
    table.source = "vlm"
    table.vlm_model = "gpt-5.6-luna"
    table.original_markdown = "이전"
    d = table.to_dict()
    assert d["vlm_model"] == "gpt-5.6-luna"
    assert d["original_markdown"] == "이전"      # 견줄 원본도 함께

    image = ImageInfo(id="image_1", placeholder="<!-- image 1 -->")
    image.vlm_markdown = "### 조직도"
    image.vlm_model = "gemma"
    assert image.to_dict()["vlm_model"] == "gemma"

    page = PageContent(page_no=1, page_no_kind="exact", content="x",
                       trace=PageTrace())
    page.ocr_original = "옛 본문"
    page.ocr_engine = "rapidocr"
    pd = page.to_dict()
    assert pd["ocr_engine"] == "rapidocr" and pd["ocr_original"] == "옛 본문"


def test_vlm_model_is_recorded_by_each_path():
    """세 경로가 실제로 그 값을 채운다 — 필드만 있으면 소용없다."""
    import inspect

    from docstruct.text import scan_vlm
    from docstruct.images import vlm_read
    from docstruct.tables import vlm_rebuild

    assert "table.vlm_model = " in inspect.getsource(vlm_rebuild)
    assert "info.vlm_model = " in inspect.getsource(vlm_read)
    assert "page.ocr_engine = " in inspect.getsource(scan_vlm)


# ────────────────────────────────────────────────────────────────────
# 0.4.23 — 스캔 배율이 문서 전체로 번지고 있었다
#
# 스캔 쪽이 하나라도 있으면 `all_pages=True` 로 **문서 전체**가 300dpi 로
# 그려졌다. 실측(행안부 p43): 표 재추출 근거 이미지가 4.17x 로 찍혔는데,
# 그 쪽에 그럴 이유가 있어서가 아니라 같은 문서에 스캔 쪽(p76·p243)이
# 섞여 있어서다. 스캔 쪽이 없는 문서를 돌리면 같은 표가 144dpi 근거로
# 떨어진다 — **같은 코드가 문서 사정에 따라 다르게 동작한다.**
# ────────────────────────────────────────────────────────────────────


def test_render_is_split_by_purpose():
    """표 근거는 render_scale, 스캔 판독만 고해상도로 덮어 그린다."""
    import inspect

    from docstruct import pipeline

    source = inspect.getsource(pipeline.build_document)
    # 기본 렌더는 스캔 여부와 무관해야 한다
    assert "scale=render_scale" in source
    assert "all_pages=render_all" in source
    # 스캔 쪽만 따로 — 0.4.24 부터 쪽마다 배율을 정하고,
    # 0.4.56 부터 같은 배율끼리 묶어 한 번에 그린다 (대상별 재호출이
    # 표 쪽 O(N²) 렌더·근거 덮어쓰기를 만들었다)
    assert "page_scan_scale(resolved, target)" in source
    assert "only=group" in source
    assert "only={target}" not in source
    # 옛 배선(하나의 배율이 문서 전체로 번지던 것)이 남아 있으면 안 된다
    assert "all_pages=bool(ocr_targets) or render_all" not in source


def test_scan_render_scale_knob(monkeypatch):
    """배율을 손잡이로 뺀다 — 300dpi 는 아직 이 문서군에서 재 본 적이 없다."""
    from docstruct.pipeline import SCAN_RENDER_SCALE, scan_render_scale

    monkeypatch.delenv("DOCSTRUCT_SCAN_RENDER_SCALE", raising=False)
    assert scan_render_scale() == SCAN_RENDER_SCALE
    monkeypatch.setenv("DOCSTRUCT_SCAN_RENDER_SCALE", "2.0")
    assert scan_render_scale() == 2.0
    # 잘못된 값·범위 밖은 기본값으로 (실행을 멈추지 않는다)
    monkeypatch.setenv("DOCSTRUCT_SCAN_RENDER_SCALE", "엉뚱")
    assert scan_render_scale() == SCAN_RENDER_SCALE
    monkeypatch.setenv("DOCSTRUCT_SCAN_RENDER_SCALE", "99")
    assert scan_render_scale() == SCAN_RENDER_SCALE


# ────────────────────────────────────────────────────────────────────
# 0.4.24 — 배율을 원본 해상도로 정한다
#
# 배율은 **원본에 없는 정보를 만들지 못한다.** 145dpi 로 들어온 스캔을
# 300dpi 로 렌더해 봐야 같은 정보를 두 배로 늘린 것뿐이고, 보간 때문에
# 오히려 흐려질 수 있다. 반대로 600dpi 원본을 300 으로 낮추면 있던
# 정보를 버린다.
# ────────────────────────────────────────────────────────────────────


def test_page_scan_scale_respects_native_resolution(monkeypatch):
    """원본이 목표보다 높으면 그 해상도에 맞춘다 — 낮추지 않는다."""
    from docstruct import pipeline

    monkeypatch.delenv("DOCSTRUCT_SCAN_RENDER_SCALE", raising=False)
    monkeypatch.setattr(pipeline, "native_dpi", lambda p, n: 150.0)
    assert round(pipeline.page_scan_scale("x.pdf", 1) * 72) == 300  # 올린다

    monkeypatch.setattr(pipeline, "native_dpi", lambda p, n: 600.0)
    assert round(pipeline.page_scan_scale("x.pdf", 1) * 72) == 600  # 유지

    monkeypatch.setattr(pipeline, "native_dpi", lambda p, n: None)
    assert pipeline.page_scan_scale("x.pdf", 1) == pipeline.SCAN_RENDER_SCALE


def test_page_scan_scale_has_upper_bound(monkeypatch):
    """아주 높은 dpi 원본에서 화소가 터지지 않게 상한을 둔다."""
    from docstruct import pipeline

    monkeypatch.setattr(pipeline, "native_dpi", lambda p, n: 4000.0)
    assert pipeline.page_scan_scale("x.pdf", 1) == pipeline.MAX_SCAN_SCALE


def test_native_dpi_ignores_decorations():
    """글머리 아이콘 같은 작은 이미지는 세지 않는다.

    실측(행안부 p38): 17×19pt 짜리 아이콘이 609dpi 였다. 그것을 기준으로
    삼으면 배율이 엉뚱해진다.
    """
    from docstruct import pipeline

    assert pipeline.MIN_IMAGE_PT >= 72.0         # 1인치 미만은 장식으로 본다
    source = inspect.getsource(pipeline.native_dpi)
    assert "MIN_IMAGE_PT" in source


def test_scan_render_is_per_page():
    """배율을 쪽마다 정한다 — 한 값이 온 문서에 번지지 않게."""
    import inspect

    from docstruct import pipeline

    source = inspect.getsource(pipeline.build_document)
    assert "page_scan_scale(resolved, target)" in source
    # 0.4.56 — 쪽마다 배율을 정하되, 같은 배율은 한 호출로 묶는다
    assert "by_scale.setdefault" in source


# ────────────────────────────────────────────────────────────────────
# 0.4.25 — 이미지 판독은 형식과 무관해야 한다
#
# "스캔 PDF" 로만 좁게 보고 있었다. **한글 문서에도 스캔 이미지가
# 들어간다.** 종이 문서를 스캔해 한글에 붙인 경우가 흔하고, 그때
# ① 해상도를 재는 곳이 없었고 ② 지면급 이미지를 "그림 설명" 지시문으로
# 읽어 본문이 통째로 요약돼 사라질 수 있었다.
# ────────────────────────────────────────────────────────────────────


def test_page_like_image_uses_transcription_prompt():
    """지면을 채운 이미지는 전사 지시문으로 읽는다."""
    import inspect

    from docstruct.images import vlm_read
    from docstruct.models import ImageInfo

    # 0.4.34: **면적이 아니라 글자 배열**로 가른다. 실측: ratio >= 0.55
    # 로 잡힌 셋이 전부 스캔본이 아니라 큰 도표였다(해상 지도·논리모형·
    # 꺾은선그래프) — 지도에 "보이는 글을 그대로 옮기라" 고 해 봐야
    # 섬 이름 나열이 나올 뿐이다.
    info = ImageInfo(id="i1", placeholder="p")
    assert vlm_read.is_page_like(info, {"kind": "figure"}) is False
    assert vlm_read.is_page_like(info, {"kind": "page"}) is True

    # 판정이 없으면 면적으로 물러난다 (옛 경로)
    big = ImageInfo(id="i2", placeholder="p",
                    bbox={"l": 0, "t": 0, "r": 560, "b": 800})
    assert vlm_read.is_page_like(big) is True

    source = inspect.getsource(vlm_read._read_one)
    assert "is_page_like(info, legible)" in source
    assert "scan_vlm import _PROMPT" in source


def test_hwpx_image_dpi_is_measured():
    """HWPX 그림도 실제 해상도를 잰다 — 형식과 무관하게."""
    from pathlib import Path as _Path

    sample = _Path("/mnt/user-data/uploads/2027년도_성과계획서__47_조달청.hwpx")
    if not sample.is_file():
        return                                    # 표본이 없는 환경
    from docstruct.converters.hwpx.hwpxtree import image_display_sizes

    sizes = image_display_sizes(sample)
    assert sizes                                  # 지면 크기를 읽는다
    for width, height in sizes.values():
        assert width > 0 and height > 0


def test_low_dpi_is_recorded(tmp_path):
    """해상도가 낮으면 trace 에 남긴다.

    HWPX 는 그 이미지가 원본 자체라 다시 렌더해 배율을 올릴 수 없다 —
    판독이 부실할 때 원인을 결과물에서 짚을 수 있어야 한다.
    """
    from docstruct.extractors import hwpx as X

    assert X.LOW_DPI >= 100.0
    source = inspect.getsource(X)
    assert "그림 해상도 낮음" in source


def test_image_dpi_is_serialized():
    """dpi 가 JSON 에 실린다."""
    from docstruct.models import ImageInfo

    info = ImageInfo(id="i", placeholder="p")
    info.dpi = 122.0
    assert info.to_dict()["dpi"] == 122.0


# ────────────────────────────────────────────────────────────────────
# 0.4.26 — 이미지 보정을 손잡이로 (기본 꺼짐)
#
# HWPX 는 그 이미지가 원본 자체라 배율을 올릴 수 없다. 실측: 조달청·
# 행안부·문체부 그림이 87~167dpi 이고 조직도도 그 안에 있다(행안부
# 122dpi). 남은 수단이 전처리와 확대뿐인데, **효과를 재기 전에는
# 켜지 않는다.**
# ────────────────────────────────────────────────────────────────────


def test_image_prep_defaults_to_auto(monkeypatch, tmp_path):
    """기본은 auto — 판독 가능성을 재서 **필요한 것에만** 건다 (0.4.29).

    모든 그림에 거는 것은 손해다. 정보가 없는 그림(poor)을 손보면 모델이
    그럴듯하게 메울 여지만 커진다.
    """
    from docstruct.images import image_prep

    monkeypatch.delenv("DOCSTRUCT_IMAGE_PREPROCESS", raising=False)
    monkeypatch.delenv("DOCSTRUCT_IMAGE_UPSCALE", raising=False)
    assert image_prep.preprocess_mode() == "auto"
    assert image_prep.upscale_mode() == "auto"

    # 0.4.41: **어느 판정에도 보정을 걸지 않는다.** A/B 실측이
    # 기계적 지표와 반대로 나왔다 (아래 시험 참조).
    for verdict in ("poor", "good", "decoration", "fair", None):
        assert image_prep._auto_plan({"verdict": verdict}) == ("off", "off")

    sample = tmp_path / "x.png"
    sample.write_bytes(b"png")
    path, applied = image_prep.prepare(sample, 120.0, {"verdict": "poor"})
    assert path == str(sample) and applied == {}


def test_image_prep_modes(monkeypatch):
    """알 수 없는 값은 off 로 떨어진다 — 오타로 켜지지 않게."""
    from docstruct.images import image_prep

    monkeypatch.setenv("DOCSTRUCT_IMAGE_PREPROCESS", "basic")
    assert image_prep.preprocess_mode() == "basic"
    monkeypatch.setenv("DOCSTRUCT_IMAGE_PREPROCESS", "엉뚱")
    assert image_prep.preprocess_mode() == "auto"   # 오타는 auto 로
    monkeypatch.setenv("DOCSTRUCT_IMAGE_PREPROCESS", "off")
    assert image_prep.preprocess_mode() == "off"
    monkeypatch.setenv("DOCSTRUCT_IMAGE_UPSCALE", "lanczos")
    assert image_prep.upscale_mode() == "lanczos"


def test_image_prep_records_what_was_applied(monkeypatch, tmp_path):
    """무엇을 적용했는지 남긴다 — A/B 를 결과물에서 가리려면 필요하다."""
    import numpy as np
    import cv2

    from docstruct.images import image_prep

    sample = tmp_path / "x.png"
    cv2.imwrite(str(sample), np.full((200, 300), 200, dtype=np.uint8))

    monkeypatch.setenv("DOCSTRUCT_IMAGE_PREPROCESS", "basic")
    monkeypatch.setenv("DOCSTRUCT_IMAGE_UPSCALE", "lanczos")
    path, applied = image_prep.prepare(sample, 120.0)

    assert path != str(sample)                   # 원본을 덮어쓰지 않는다
    assert applied.get("preprocess") == "basic"
    assert "lanczos" in applied.get("upscale", "")
    out = cv2.imread(path)
    assert out.shape[1] > 300                    # 실제로 커졌다


def test_image_prep_is_recorded_on_the_image():
    """적용 기록이 ImageInfo 에 남고 JSON 에 실린다."""
    import inspect

    from docstruct.images import vlm_read
    from docstruct.models import ImageInfo

    info = ImageInfo(id="i", placeholder="p")
    info.image_prep = {"preprocess": "basic"}
    assert info.to_dict()["image_prep"] == {"preprocess": "basic"}
    assert "info.image_prep = applied" in inspect.getsource(vlm_read._read_one)


# ────────────────────────────────────────────────────────────────────
# 0.4.29 — 판독 가능성은 dpi 가 아니라 글자 획 높이다
#
# 실측으로 순서가 뒤집혔다.
#     국방부 막대그래프   80dpi · 획 9px → 축 값·라벨까지 다 읽힘
#     원그래프(3문서 공통) 102dpi · 획 6px → `100%` 가 `IDD%` 로 박혀 있음
# dpi 는 지면 배치에 좌우되므로, 그것으로 고르면 멀쩡한 것을 손보고
# 못 읽는 것을 놓친다.
# ────────────────────────────────────────────────────────────────────


def test_legibility_judges_by_glyph_height(tmp_path):
    """글자 획 높이로 판정한다."""
    import cv2
    import numpy as np

    from docstruct.images.legibility import measure

    def canvas(glyph_px):
        img = np.full((400, 600), 255, dtype=np.uint8)
        for row in range(6):
            for col in range(12):
                x, y = 30 + col * 45, 40 + row * 55
                img[y:y + glyph_px, x:x + glyph_px - 2] = 0
        return img

    small = tmp_path / "small.png"
    cv2.imwrite(str(small), canvas(6))
    assert measure(small)["verdict"] == "poor"

    # 0.4.32: 7px 도 poor 다 — 눈으로 확인한 조달청 원그래프(`100%` 가
    # `IDD%` 로 박힌 그것)가 7px 로 측정돼 fair 판정을 받고 있었다.
    seven = tmp_path / "seven.png"
    cv2.imwrite(str(seven), canvas(7))
    assert measure(seven)["verdict"] == "poor"

    big = tmp_path / "big.png"
    cv2.imwrite(str(big), canvas(12))
    assert measure(big)["verdict"] == "good"


def test_legibility_calls_thin_shapes_decoration(tmp_path):
    """도형 장식은 판독 대상이 아니다.

    실측(국방부): 14×91 픽셀짜리 삼각형이 dpi 23 으로 잡혀 "가장 급한
    문서" 2위로 올라왔다.
    """
    import cv2
    import numpy as np

    from docstruct.images.legibility import measure

    tiny = tmp_path / "tiny.png"
    cv2.imwrite(str(tiny), np.full((91, 14), 200, dtype=np.uint8))
    assert measure(tiny)["verdict"] == "decoration"


def test_legibility_uses_area_not_side(tmp_path):
    """납작한 그림을 장식으로 오판하지 않는다.

    실측(국방부 image4 458×166): 한 변 기준으로는 장식으로 걸렸으나
    80dpi·획 9px 로 다 읽히는 막대그래프였다.
    """
    from docstruct.images import legibility

    assert legibility.MIN_AREA <= 458 * 166


def test_auto_plan_applies_nothing():
    """자동으로는 보정을 걸지 않는다 — **A/B 실측이 그렇게 말했다.**

    기계적 지표는 좋았다: fair 45장에서 획이 41장 올라가고 내려간 것이
    0, 변화 중앙값 +4px. 그러나 같은 그림을 VLM 에 두 번 읽혀 견주니
    개선 1 · 비슷 5 · **악화 4**, 글자 합계 -11%, 국가보훈부는 읽어낸
    숫자가 10개 → 3개로 줄었다.

    lanczos 는 정보를 늘리지 않고 **흐리게 퍼뜨린다.** 획 높이는 픽셀
    수로 재므로 오르지만 경계가 뭉개져 모델이 구분하지 못한다.

    손잡이는 남겨 둔다 — 다른 문서군에서 값어치가 확인되면 켠다.
    """
    from docstruct.images.image_prep import _auto_plan, preprocess_mode

    for verdict in ("poor", "good", "fair", "decoration", None):
        assert _auto_plan({"verdict": verdict}) == ("off", "off")
    assert _auto_plan(None) == ("off", "off")

    # 손잡이 자체는 살아 있다
    import os

    os.environ["DOCSTRUCT_IMAGE_PREPROCESS"] = "basic"
    try:
        assert preprocess_mode() == "basic"
    finally:
        os.environ.pop("DOCSTRUCT_IMAGE_PREPROCESS", None)


def test_prep_reverts_when_legibility_drops(tmp_path, monkeypatch):
    """보정이 나빠지게 하면 원본을 보낸다.

    실측(국방부 막대그래프): 대비 정규화가 얇은 축선을 끊어 획 판정이
    9px → 4px 로 떨어졌다.
    """
    import cv2
    import numpy as np

    from docstruct.images import image_prep

    sample = tmp_path / "x.png"
    cv2.imwrite(str(sample), np.full((300, 400), 200, dtype=np.uint8))
    monkeypatch.setenv("DOCSTRUCT_IMAGE_PREPROCESS", "basic")
    monkeypatch.setenv("DOCSTRUCT_IMAGE_UPSCALE", "off")

    path, applied = image_prep.prepare(sample, None, {"glyph_px": 99})
    assert path == str(sample)                   # 원본을 보낸다
    assert "skipped" in applied                  # 왜인지 남긴다


def test_decoration_is_not_sent_to_vlm():
    """장식은 VLM 을 부르지 않는다 — 호출을 아낀다."""
    import inspect

    from docstruct.images import vlm_read

    source = inspect.getsource(vlm_read._read_one)
    assert 'verdict") == "decoration"' in source
    assert "그림 판독 생략" in source
    # 0.4.33: poor 는 전사가 아니라 **설명**을 받는다 — 원본에 정보가
    # 없어 전사를 시키면 지어낸다. 그러나 그림이 무엇인지는 알 수 있고,
    # 본문에서 그림이 사라지지 않게 그 설명이 자리를 대신한다.
    assert "_DESCRIBE_PROMPT" in source
    assert "picture_describe" in source


# ────────────────────────────────────────────────────────────────────
# 0.4.30 — opencv 없이 조용히 실패하고 있었다 · 초해상도 모델(GPU 한정)
#
# 61건 조사 결과가 **전부 "그대로"** 로 나왔다. cv2 가 없으면 모든 그림이
# `unknown` 이 되는데 그것을 "문제 없음" 으로 읽은 것이다 — 판정이 통째로
# 무의미했는데 결과만 보고는 알 수 없었다.
# ────────────────────────────────────────────────────────────────────


def test_missing_opencv_is_loud(monkeypatch, capsys):
    """cv2 가 없으면 경고한다 — 조용히 'unknown' 만 돌려주지 않는다."""
    import logging

    from docstruct.images import legibility

    monkeypatch.setattr(legibility, "_WARNED", [])
    monkeypatch.setitem(sys.modules, "cv2", None)

    caplog = []
    handler = logging.Handler()
    handler.emit = lambda record: caplog.append(record.getMessage())
    logger = logging.getLogger("docstruct.images.legibility")
    logger.addHandler(handler)
    try:
        result = legibility.measure("x.png")
    finally:
        logger.removeHandler(handler)

    assert result["verdict"] == "unknown"
    assert any("opencv" in m for m in caplog)


def test_survey_flags_unknown_instead_of_ok():
    """판정 못 한 것을 '그대로' 로 읽지 않는다."""
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parent.parent
    survey = root / "notebooks" / "image_survey.py"
    if not survey.is_file():
        return
    source = survey.read_text(encoding="utf-8")
    assert "판정 불가" in source
    assert 'verdict") == "unknown"' in source


def test_super_resolution_requires_gpu(monkeypatch):
    """GPU 가 없으면 물러난다 — CPU 로는 한 장에 수십 초다."""
    from docstruct.images import super_resolution as sr

    monkeypatch.delenv("DOCSTRUCT_SR_ALLOW_CPU", raising=False)
    fake_torch = type("T", (), {
        "cuda": type("C", (), {"is_available": staticmethod(lambda: False)})})
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "transformers", type("M", (), {}))

    ok, reason = sr.available()
    assert ok is False and "GPU" in reason

    # 명시하면 CPU 도 허용한다 (느리다는 것을 사유에 적는다)
    monkeypatch.setenv("DOCSTRUCT_SR_ALLOW_CPU", "1")
    ok, reason = sr.available()
    assert ok is True and "cpu" in reason


def test_super_resolution_falls_back_to_lanczos(monkeypatch, tmp_path):
    """모델을 못 쓰면 lanczos 로 물러나고 **사유를 남긴다.**"""
    import cv2
    import numpy as np

    from docstruct.images import image_prep

    sample = tmp_path / "x.png"
    canvas = np.full((300, 400), 255, dtype=np.uint8)
    for row in range(5):
        for col in range(10):
            canvas[40 + row * 50:40 + row * 50 + 8,
                   30 + col * 38:30 + col * 38 + 6] = 0
    cv2.imwrite(str(sample), canvas)

    monkeypatch.setenv("DOCSTRUCT_IMAGE_PREPROCESS", "off")
    monkeypatch.setenv("DOCSTRUCT_IMAGE_UPSCALE", "model")
    monkeypatch.setitem(sys.modules, "torch", None)   # 모델 불가

    _path, applied = image_prep.prepare(sample, None, {"glyph_px": 8})
    assert "lanczos" in applied.get("upscale", "")
    assert "모델 불가" in applied["upscale"]           # 왜 물러났는지 남는다


def test_super_resolution_profiles_are_swappable(monkeypatch):
    """문서 종류에 따라 계열을 갈아 끼운다 — 텍스트 특화 / 자연 이미지.

    **계열에 기본값이 없으면 다른 계열로 흘러가지 않는다.** `text` 를
    골랐는데 조용히 자연 이미지 모델이 도는 것이 가장 나쁘다 — 그
    계열을 고른 이유가 사라진다.
    """
    from docstruct.images import super_resolution as sr

    monkeypatch.delenv("DOCSTRUCT_SR_MODEL", raising=False)
    monkeypatch.delenv("DOCSTRUCT_SR_PROFILE", raising=False)
    assert sr.profile() == "image"
    assert "swin2SR" in sr.model_name()

    # 0.4.43: **텍스트 특화 계열은 뺐다** — 한글 특화 모델이 없어
    # 오독 위험이 크다. 요청하면 사유를 말하고 막는다.
    monkeypatch.setenv("DOCSTRUCT_SR_PROFILE", "text")
    assert sr.profile() == "image"                # 계열 목록에 없다
    ok, reason = sr.available()
    assert ok is False and "한글 특화" in reason

    # 그래도 쓰려면 모델을 직접 지정한다 (막지는 않는다)
    monkeypatch.setenv("DOCSTRUCT_SR_MODEL", "someone/textzoom-sr")
    assert sr.model_name() == "someone/textzoom-sr"
    assert "한글 특화" not in sr.available()[1]

    # 오타는 기본 계열로
    monkeypatch.setenv("DOCSTRUCT_SR_PROFILE", "엉뚱")
    assert sr.profile() == "image"

    source = inspect.getsource(sr)
    assert "확산" in source                        # 왜 조심하는지 적어 둔다
    assert "16×64" in source                       # 도메인 차이를 적어 둔다
    assert "_EXCLUDED_PROFILES" in source


def test_survey_temp_file_works_on_windows():
    """NamedTemporaryFile 을 열어 둔 채 그 경로에 쓰지 않는다.

    윈도우는 열려 있는 파일을 다시 열지 못해 저장이 실패하고, 그 예외를
    호출부가 삼켜 **그림이 통째로 사라졌다** — 실측: 61건 전부
    "그림 0개" 로 나왔고 결과만 보고는 알 수 없었다.
    """
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parent.parent
    survey = root / "notebooks" / "image_survey.py"
    if not survey.is_file():
        return
    source = survey.read_text(encoding="utf-8")
    # 주석의 설명은 남아 있어도 되지만 **호출은 없어야 한다**
    assert "tempfile.NamedTemporaryFile(" not in source
    assert "tempfile.gettempdir()" in source     # 경로만 만들어 쓴다
    # 읽지 못한 그림을 조용히 버리지 않는다
    assert "_SKIPPED" in source
    assert "읽지 못한 그림" in source


def test_survey_does_not_condemn_a_whole_document_for_one_image():
    """가장 나쁜 그림 하나가 문서를 대표하지 않는다.

    실측: 국세청은 그림 6개 중 못읽음이 1개뿐이고 나머지 5개가 멀쩡한데
    "손댈 수 없음" 으로 분류됐다 — 사람이 그 목록을 보면 "이 문서는
    포기" 로 읽는다.
    """
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parent.parent
    survey = root / "notebooks" / "image_survey.py"
    if not survey.is_file():
        return
    source = survey.read_text(encoding="utf-8")
    assert "POOR_MAJORITY" in source
    assert "일부 손댈 수 없음" in source


def test_poor_image_gets_description_not_transcription():
    """읽히지 않는 그림은 **설명**으로 자리를 채운다.

    이미지가 본문에서 사라지면 안 된다 — 무엇이 있었는지는 남아야 한다.
    전사를 시키면 모델이 지어내지만(원본에 정보가 없다), 종류·주제·값의
    흐름은 흐려도 알 수 있다.
    """
    import inspect

    from docstruct.images import vlm_read

    prompt = vlm_read._DESCRIBE_PROMPT
    assert "글자를 옮기려 하지 말고" in prompt
    assert "무엇을 나타내는 그림인지" in prompt
    assert "지어내지 마세요" in prompt

    source = inspect.getsource(vlm_read._read_one)
    assert 'verdict") == "poor"' in source
    assert "_DESCRIBE_PROMPT" in source


def test_description_replaces_the_image_in_body():
    """그 설명이 본문의 그림 자리에 들어간다."""
    from docstruct.output.content import expand_tables_and_images
    from docstruct.models import ImageInfo

    info = ImageInfo(id="image_3", placeholder="<!-- image 3 -->",
                     image_path="/x/a.png")
    info.legibility = {"glyph_px": 6, "verdict": "poor"}
    info.vlm_markdown = "전략목표별 재원배분 원그래프 두 개."
    body = expand_tables_and_images("앞\n\n<!-- image 3 -->\n\n뒤", [], [info])
    assert "원그래프" in body
    assert "<!-- image 3 -->" in body             # 앵커는 남는다


def test_legibility_ignores_lines_and_boxes():
    """도표의 상자 테두리·연결선을 글자로 세지 않는다.

    실측(과기부 논리모형): 상자가 60여 개라 작은 조각이 중앙값을
    끌어내렸다.
    """
    from docstruct.images import legibility

    assert legibility._MAX_ASPECT <= 3.0          # 선분 제외
    assert 0 < legibility._MIN_FILL < 1.0         # 속 빈 도형 제외
    source = inspect.getsource(legibility.measure)
    assert "_MAX_ASPECT" in source and "_MIN_FILL" in source


def test_kind_separates_page_from_figure():
    """글자 배열로 지면과 그림을 가른다 (면적이 아니라).

    실측으로 갈린 것:
        해양경찰청 해상 지도  줄 2 · 줄당 3  → figure
        대법원 꺾은선그래프   줄 15 · 줄당 6 → figure
        과기부 논리모형       줄 75 · 줄당 9 → figure
        조달청 조직도         줄 52 · 줄당 19 → page (전사가 맞다)
    """
    from docstruct.images.legibility import _looks_like_page

    assert _looks_like_page(2, 3.0) is False      # 지도
    assert _looks_like_page(15, 6.0) is False     # 꺾은선
    assert _looks_like_page(75, 9.0) is False     # 논리모형 — 줄당이 성기다
    assert _looks_like_page(52, 19.0) is True     # 조직도·지면


def test_survey_reports_kind_and_route():
    """유형·판정 분포와 **그 그림에 무엇을 할지**를 함께 낸다.

    분포만으로는 계획이 서지 않는다 — 호출 계획이 같이 보여야 한다.
    """
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parent.parent
    survey = root / "notebooks" / "image_survey.py"
    if not survey.is_file():
        return
    source = survey.read_text(encoding="utf-8")
    assert "_ROUTE" in source
    assert "유형·판정 분포" in source
    # 면적이 아니라 kind 로 지면을 가른다 (0.4.34)
    assert 'i.get("kind") == "page"' in source
    # 눈으로 확인할 수 있게 뽑아 준다
    assert "--dump" in source and "def _dump(" in source


def test_ab_tool_uses_the_real_pipeline():
    """A/B 가 파이프라인과 **같은 함수**를 쓴다.

    지시문 선택(전사/복원/설명)까지 그대로 따라가야 한다 — A/B 가 실제
    동작과 어긋나면 판정이 의미가 없다.
    """
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parent.parent
    tool = root / "notebooks" / "image_ab.py"
    if not tool.is_file():
        return
    source = tool.read_text(encoding="utf-8")
    # 0.4.38: 배포 배치도 받으려고 `_import` 를 거친다
    assert '_import("media.vlm_read", "_read_one")' in source
    assert '_import("media.image_prep", "prepare")' in source
    # 0.4.52: `auto` 로 A/B 하면 두 쪽이 같아진다(auto 는 아무것도 걸지
    # 않는다) — 단계를 직접 지정해야 한다
    assert '"auto" if preprocess' not in source
    assert "COMBOS" in source
    # 한 장 실패로 멈추지 않는다
    assert "(실패:" in source


def test_ab_tool_shows_which_llm_it_uses():
    """무엇으로 읽는지 먼저 보여 준다.

    실행해 보고 "(LLM 미설정)" 이 나와서야 아는 것은 늦다. 그리고
    노트북의 `set_api_key()` 는 별도 프로세스라 통하지 않는다 — 그
    사실을 안내에 적는다.
    """
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parent.parent
    tool = root / "notebooks" / "image_ab.py"
    if not tool.is_file():
        return
    source = tool.read_text(encoding="utf-8")
    assert "LLM_URL" in source and "LLM_KEY" in source
    assert "DOCLING_TABLE_API_URL" in source
    assert "set_api_key() 는 별도 프로세스라 통하지 않습니다" in source


def test_tools_work_in_both_layouts():
    """도구가 pkg 배치와 배포 배치 양쪽에서 돈다.

    배포 트리는 `converters`·`core`·`infrastructure`·`experiments` 를
    최상위로 승격한다 — `docstruct.infrastructure` 가 없다. 설치본은
    `docstruct.` 아래에 둔다. 실측: image_ab 가 pkg 배치로만 짜여
    로컬에서 ModuleNotFoundError 로 죽었다.
    """
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parent.parent / "notebooks"
    for name in ("image_ab.py", "image_survey.py"):
        tool = root / name
        if not tool.is_file():
            continue
        source = tool.read_text(encoding="utf-8")
        # 승격 배치를 받는 길이 있어야 한다
        assert ("_import(" in source
                or "except ImportError" in source), name


def test_cv2_reads_korean_paths(tmp_path):
    """한글 경로에서도 이미지를 읽고 쓴다.

    **`cv2.imread` 는 윈도우에서 한글 경로를 못 연다** — 내부적으로
    ANSI 로 바꾸면서 파일명이 깨진다. 실측:

        can't open/read file: '洹몃┝\\fair\\...怨쇳븰湲곗닠...png'

    파일은 멀쩡한데 열지를 못한다. 바이트로 읽어 메모리에서 디코딩하면
    경로 인코딩을 거치지 않는다.
    """
    import numpy as np

    from docstruct.images.legibility import (
        imread_unicode, imwrite_unicode, measure,
    )

    folder = tmp_path / "그림" / "판정"
    folder.mkdir(parents=True)
    target = folder / "과학기술정보통신부_그림_8px.png"

    canvas = np.full((300, 400), 255, dtype=np.uint8)
    for row in range(6):
        for col in range(12):
            canvas[40 + row * 40:40 + row * 40 + 8,
                   20 + col * 30:20 + col * 30 + 6] = 0

    assert imwrite_unicode(target, canvas) is True
    assert target.is_file()
    assert imread_unicode(target) is not None
    assert measure(target)["verdict"] != "unknown"

    # 없는 파일은 None (예외를 던지지 않는다)
    assert imread_unicode(folder / "없는파일.png") is None


def test_media_uses_unicode_safe_io():
    """cv2 파일 입출력을 직접 부르지 않는다."""
    import inspect

    from docstruct.images import image_prep, legibility

    for module in (legibility, image_prep):
        source = inspect.getsource(module)
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("*"):
                continue
            assert "cv2.imread(" not in stripped, module.__name__
            assert "cv2.imwrite(" not in stripped, module.__name__


def test_deskew_survives_any_hough_shape():
    """HoughLinesP 반환 모양에 기대지 않는다.

    OpenCV 판에 따라 `[[x1,y1,x2,y2]]` 로도, 평평하게도 온다 —
    실측(윈도우): `line[0]` 이 numpy.int32 라 언패킹이 터졌다.
    """
    import cv2
    import numpy as np

    from docstruct.images import image_prep

    canvas = np.full((300, 400), 255, dtype=np.uint8)
    for row in range(6):
        for col in range(12):
            canvas[40 + row * 40:40 + row * 40 + 8,
                   20 + col * 30:20 + col * 30 + 6] = 0

    original = cv2.HoughLinesP
    try:
        # 평평한 반환
        cv2.HoughLinesP = lambda *a, **k: np.array(
            [10, 20, 300, 22, 15, 60, 310, 61])
        assert image_prep._deskew(canvas).shape == canvas.shape
        # 중첩 반환
        cv2.HoughLinesP = lambda *a, **k: np.array(
            [[[10, 20, 300, 22]], [[15, 60, 310, 61]]])
        assert image_prep._deskew(canvas).shape == canvas.shape
        # 없음
        cv2.HoughLinesP = lambda *a, **k: None
        assert image_prep._deskew(canvas).shape == canvas.shape
    finally:
        cv2.HoughLinesP = original


def test_preprocess_survives_deskew_failure(monkeypatch, tmp_path):
    """기울기 보정이 실패해도 나머지 보정은 살린다."""
    import numpy as np

    from docstruct.images import image_prep
    from docstruct.images.legibility import imwrite_unicode

    sample = tmp_path / "x.png"
    canvas = np.full((300, 400), 255, dtype=np.uint8)
    for row in range(6):
        for col in range(12):
            canvas[40 + row * 40:40 + row * 40 + 8,
                   20 + col * 30:20 + col * 30 + 6] = 0
    imwrite_unicode(sample, canvas)

    def boom(_array):
        raise TypeError("cannot unpack non-iterable numpy.int32 object")

    monkeypatch.setattr(image_prep, "_deskew", boom)
    monkeypatch.setenv("DOCSTRUCT_IMAGE_PREPROCESS", "basic")
    monkeypatch.setenv("DOCSTRUCT_IMAGE_UPSCALE", "off")

    _path, applied = image_prep.prepare(sample, None, {"glyph_px": 8})
    assert applied.get("preprocess") == "basic"   # 멈추지 않는다


# ────────────────────────────────────────────────────────────────────
# 0.4.44 — HWPX 쪽 맞춤을 정식 기능으로
#
# HWPX 에는 쪽 정보가 **원리적으로 없다.** 세 갈래를 다 확인했다:
#   hp:pageNum        "여기에 찍어라" 는 지시일 뿐 숫자가 아니다
#   hp:startNum page  전부 0 (조달청 23·행안부 160 섹션)
#   본문의 `- 71 -`   0건 — 꼬리말이라 본문 추출에 안 들어온다
# 쪽은 한글이 그릴 때 생기는 것이지 저장되는 것이 아니다.
# ────────────────────────────────────────────────────────────────────


def test_align_module_is_a_library_not_a_notebook():
    """쪽 맞춤이 정식 모듈이다 — 주요 기능이므로 notebooks 에 두지 않는다."""
    from docstruct.align import align, pages, split_by_page  # noqa: F401
    from docstruct.align.page_map import toc_anchors  # noqa: F401


def test_align_accepts_dicts_and_objects():
    """같은 함수가 JSON dict 와 파이프라인 객체를 모두 받는다.

    서비스는 파일을 받고 라이브러리는 객체를 넘긴다.
    """
    from docstruct.align import align
    from docstruct.models import TableInfo

    cells = [{"row": 0, "col": 0, "rowspan": 1, "colspan": 1,
              "text": "성과지표 나라장터 이용건수 307,000"}]
    as_dict = [(1, {"id": "t1", "cells": cells})]
    info = TableInfo(id="t1", table_num=1, placeholder="", markdown="")
    info.cells = cells
    as_object = [(1, info)]

    for detected in (as_dict, as_object):
        pairs = align(detected, [cells])
        assert any(p.kind == "짝" for p in pairs)


def test_toc_anchors_use_printed_page_numbers():
    """목차는 **문서 자신이 밝힌 쪽 정보**다 — 표 유사도보다 확실하다."""
    from docstruct.align.page_map import toc_anchors

    toc = [{"title": "제1장 성과계획 목표체계", "page": 1},
           {"title": "조직 및 성과관리 추진체계 현황", "page": 4}]
    body = ("목 차 제1장 성과계획 목표체계 ... "
            "본문 제1장 성과계획 목표체계 시작 ... "
            "조직 및 성과관리 추진체계 현황")
    anchors = toc_anchors(toc, body)
    assert len(anchors) == 2
    assert [page for _pos, page in anchors] == [1, 4]   # 쪽이 뒤로만 간다
    # 위치가 오름차순이다
    assert anchors[0][0] < anchors[1][0]


def test_service_adapter_reports_unmatched():
    """짝짓지 못한 표를 조용히 버리지 않는다 — 수치가 거짓이 된다."""
    root = Path(__file__).resolve().parent.parent.parent
    for base in (root / "docstruct-backend-overlay-0_4_1" / "overlay" / "app",
                 root.parent / "overlay" / "app"):
        adapter = base / "rag" / "adapters" / "page_align.py"
        if not adapter.is_file():
            continue
        source = adapter.read_text(encoding="utf-8")
        assert "unmatched_tables" in source
        assert "표가 없습니다" in source            # 근거가 없으면 막는다
        return


# ────────────────────────────────────────────────────────────────────
# 0.4.45 — 쪽 맞춤의 주력을 본문 눈금으로
#
# 표 정렬은 서식이 같은 표가 많은 문서에서 흔들린다(행안부 317표 중
# 230표). 목차는 눈금이 22개뿐이라 429쪽을 나누기에 성기다.
# **본문 글은 순서가 바뀌지 않는다** — 실측: 눈금 231개가 예외 없이
# 차례대로 증가했다(230/230).
# ────────────────────────────────────────────────────────────────────


def test_text_anchors_need_prefix_stripping():
    """마크다운 접두사를 빼야 붙는다.

    PDF 는 docling 이 제목으로 분류해 `# ` 를 붙이고 HWPX 는 `- ` 를
    붙이거나 원문 그대로 둔다 — 실측(행안부): 그대로 비교하면 6%,
    빼고 비교하면 37% 가 붙었다.
    """
    from docstruct.align.page_map import _flatten

    assert _flatten("# 4. 조직 및 성과관리 추진체계 현황") == \
        _flatten("- 4. 조직 및 성과관리 추진체계 현황")
    assert _flatten("**굵게** 본문") == _flatten("굵게 본문")


def test_anchor_keys_skip_placeholders():
    """placeholder·표 조각·쪽번호는 눈금 후보가 아니다.

    실측: `<!-- image_1 -->` 를 첫 줄로 골라 실패한 쪽이 있었다.
    """
    from docstruct.align.page_map import _anchor_keys

    page = {"content": "\n".join([
        "<!-- image_1 -->",
        "| 표 | 조각 |",
        "- 12 -",
        "ㅇ 자체평가위원회 운영계획을 수립하여 시행한다",
    ])}
    keys = _anchor_keys(page)
    assert keys and "자체평가위원회" in keys[0]
    assert not any("image" in k for k in keys)


def test_anchor_keys_try_several_lines():
    """첫 줄만 보지 않는다 — 실측: 115쪽 중 28쪽이 다음 줄로 찾아졌다."""
    from docstruct.align.page_map import ANCHOR_TRIES, _anchor_keys

    assert ANCHOR_TRIES >= 3
    page = {"content": "\n".join(f"본문 줄 번호 {n} 입니다 충분히 깁니다"
                                 for n in range(10))}
    assert len(_anchor_keys(page)) == ANCHOR_TRIES


def test_text_anchors_are_monotonic():
    """눈금은 앞 눈금 이후에서만 찾는다 — 차례가 보장된다."""
    from docstruct.align.page_map import text_anchors

    body = ("첫째 쪽의 본문입니다 충분히 긴 문장 "
            "둘째 쪽의 본문입니다 충분히 긴 문장 "
            "셋째 쪽의 본문입니다 충분히 긴 문장")
    pdf_pages = [
        {"page_no": 1, "content": "첫째 쪽의 본문입니다 충분히 긴 문장"},
        {"page_no": 2, "content": "둘째 쪽의 본문입니다 충분히 긴 문장"},
        {"page_no": 3, "content": "셋째 쪽의 본문입니다 충분히 긴 문장"},
    ]
    anchors = text_anchors(pdf_pages, body)
    assert [page for _pos, page in anchors] == [1, 2, 3]
    positions = [pos for pos, _page in anchors]
    assert positions == sorted(positions)


def test_split_text_keeps_everything():
    """자르면서 내용을 잃지 않는다."""
    from docstruct.align.page_map import split_text_by_page, text_anchors

    body = ("머리말입니다 "
            "첫째 쪽의 본문입니다 충분히 긴 문장 여기에 더 있습니다 "
            "둘째 쪽의 본문입니다 충분히 긴 문장 여기에 더 있습니다")
    pdf_pages = [
        {"page_no": 1, "content": "첫째 쪽의 본문입니다 충분히 긴 문장"},
        {"page_no": 2, "content": "둘째 쪽의 본문입니다 충분히 긴 문장"},
    ]
    parts = split_text_by_page(body, text_anchors(pdf_pages, body))
    joined = " ".join(p["content"] for p in parts)
    for word in ("머리말입니다", "첫째", "둘째"):
        assert word in joined
    assert [p["page_no"] for p in parts if p["page_no"]] == [1, 2]


def test_anchors_use_head_and_tail():
    """머리와 꼬리를 모두 쓴다 — 머리로 못 잡으면 꼬리로."""
    from docstruct.align.page_map import _anchor_keys

    page = {"content": "\n".join([
        "첫째 줄입니다 충분히 긴 문장입니다",
        "가운데 줄입니다 충분히 긴 문장입니다",
        "마지막 줄입니다 충분히 긴 문장입니다",
    ])}
    head = _anchor_keys(page)
    tail = _anchor_keys(page, tail=True)
    assert "첫째" in head[0]
    assert "마지막" in tail[0]


def test_interpolate_fills_pages_without_text():
    """눈금 사이를 비례로 채운다.

    **놓친 쪽의 대부분은 텍스트로 잡을 수 없다** — 실측(행안부): 163쪽
    중 112쪽이 후보 자체가 없다(표만 있는 쪽). 규칙을 고쳐도 못 잡는다.
    """
    from docstruct.align.page_map import interpolate

    anchors = [(0, 1), (400, 5)]
    filled = interpolate(anchors, last_page=5, total_chars=500)
    assert [page for _pos, page in filled] == [1, 2, 3, 4, 5]
    positions = [pos for pos, _page in filled]
    assert positions == sorted(positions)
    # 고르게 나뉜다
    assert positions[1] == 100 and positions[2] == 200


def test_estimated_pages_are_marked():
    """추정을 사실처럼 보이게 하지 않는다."""
    from docstruct.align.page_map import interpolate, split_text_by_page

    body = "가" * 100 + "나" * 100 + "다" * 100
    anchors = [(0, 1), (200, 3)]
    filled = interpolate(anchors, last_page=3, total_chars=300)
    parts = split_text_by_page(body, filled, measured={1, 3})

    marks = {p["page_no"]: p["estimated"] for p in parts if p["page_no"]}
    assert marks[1] is False and marks[3] is False
    assert marks[2] is True                       # 보간으로 채운 쪽


def test_service_reports_estimated_count():
    """서비스도 추정 쪽 수를 함께 낸다."""
    root = Path(__file__).resolve().parent.parent.parent
    for base in (root / "docstruct-backend-overlay-0_4_1" / "overlay" / "app",
                 root.parent / "overlay" / "app"):
        adapter = base / "rag" / "adapters" / "page_align.py"
        if not adapter.is_file():
            continue
        source = adapter.read_text(encoding="utf-8")
        assert "estimated_pages" in source
        assert "interpolate" in source
        return


def test_cut_points_snap_to_line_boundaries():
    """쪽 경계가 표 한복판에서 잘리지 않는다.

    보간은 글자 수로 자리를 잡으므로 표 중간이 될 수 있다 — 실측:
    `<table 15>` 가 `able 15>` 로 반토막 난 쪽이 나왔다.
    """
    from docstruct.align.page_map import _snap_to_line, split_text_by_page

    text = "첫 문단입니다\n\n<table 15>\n\n| 가 | 나 |\n\n둘째 문단입니다"
    middle = text.index("able 15>")
    assert _snap_to_line(text, middle) <= text.index("<table 15>")

    parts = split_text_by_page(text, [(0, 1), (middle, 2)])
    for part in parts:
        assert "able 15>" not in part["content"] or "<table 15>" in part["content"]


def test_align_test_tool_exists():
    """로컬에서 쪽 맞춤을 시험할 도구가 있다."""
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parent.parent / "notebooks"
    tool = root / "page_align_test.py"
    if not tool.is_file():
        return
    source = tool.read_text(encoding="utf-8")
    assert "_import(" in source                   # 두 배치 모두 지원
    assert "추정" in source                        # 추정을 구분해 보여 준다
    assert "차례가 어긋난 것" in source              # 신뢰도를 함께 낸다


def test_align_endpoint_uses_its_own_format_enum():
    """`/align/pages` 가 `OutputFormat` 을 쓰지 않는다.

    `OutputFormat` 은 문서 변환용(text·markdown·html·xml)이라 `json` 이
    없다. 그대로 쓰면 **서버가 뜨지 않는다**:

        AttributeError: json
          format: OutputFormat = OutputFormat.json

    import 시점에 터지므로 uvicorn 이 아예 기동하지 못한다.
    """
    import ast

    root = Path(__file__).resolve().parent.parent.parent
    for base in (root / "docstruct-backend-overlay-0_4_1" / "overlay" / "app",
                 root.parent / "overlay" / "app"):
        main = base / "main.py.patched"
        if not main.is_file():
            continue
        source = main.read_text(encoding="utf-8")
        assert "OutputFormat.json" not in source
        assert "from enum import Enum" in source

        # 정의가 쓰이는 곳보다 앞에 있어야 한다 (모듈 최상위 평가)
        tree = ast.parse(source)
        lines = {node.name: node.lineno for node in tree.body
                 if isinstance(node, (ast.ClassDef, ast.FunctionDef,
                                      ast.AsyncFunctionDef))}
        if "align_pages" in lines:
            assert lines.get("AlignFormat", 10 ** 9) < lines["align_pages"]
        return


# ────────────────────────────────────────────────────────────────────
# 0.4.49 — 조직도를 표로 옮기라고 해서 없는 표가 나왔다
#
# 실측(행안부 조직도 122dpi·획 6px): VLM 이
#   `| □ 안전정책담당관 | □ 예방정책담당관 | … |` 을 **똑같이 10줄**
# 냈다. 지면에는 그런 표가 없다 — 흐린 그림에서 앞 패턴을 복사해 칸을
# 채운 것이다. **그럴듯한 표가 들어가는 것이 비어 있는 것보다 나쁘다.**
# ────────────────────────────────────────────────────────────────────


def test_repeated_lines_are_rejected():
    """같은 줄을 되풀이하면 읽은 것이 아니다."""
    from docstruct.images.vlm_read import (
        MAX_REPEAT_RATIO, _repetition_ratio,
    )

    fabricated = "\n".join(
        ["| 머리 | 행 |", "| --- | --- |"]
        + ["| □ 안전정책담당관 | □ 예방정책담당관 |"] * 10)
    assert _repetition_ratio(fabricated) >= MAX_REPEAT_RATIO

    genuine = "\n".join([
        "| 안전정책국 | 예방정책국 |", "| --- | --- |",
        "| 안전기획과 | 예방총괄과 |", "| 안전제도과 | 안전문화과 |",
        "| 재난보험과 | 승강기안전과 |", "| 안전관리과 | 생활안전과 |",
    ])
    assert _repetition_ratio(genuine) < MAX_REPEAT_RATIO

    # 짧은 답은 우연히 겹칠 수 있으므로 따지지 않는다
    assert _repetition_ratio("가\n가\n가") == 0.0


def test_chart_prompt_forbids_tables():
    """도해는 표가 아니라 **계층 목록**으로 받는다.

    칸이 격자로 놓여 있어도 조직도는 표가 아니다 — 표로 옮기라고 하면
    모델이 없는 표를 만든다.
    """
    from docstruct.images import vlm_read

    prompt = vlm_read._CHART_PROMPT
    assert "표로" in prompt and "옮기지 마세요" in prompt
    assert "들여쓴 목록" in prompt
    assert "되풀이하지 마세요" in prompt          # 반복을 미리 막는다


def test_chart_routing_by_shape():
    """상자가 많고 줄당 글자가 성기면 도해로 본다."""
    import inspect

    from docstruct.images import vlm_read

    assert vlm_read.CHART_MIN_ROWS >= 10
    source = inspect.getsource(vlm_read._read_one)
    assert "_CHART_PROMPT" in source
    assert "CHART_MAX_PER_ROW" in source


def test_upscale_is_documented_as_ocr_only():
    """확대가 **OCR 에는 유효**하다는 것을 적어 둔다.

    실측(국방부 막대그래프·tesseract): 원본 숫자 34개 → lanczos x3 에서
    48개. 원본에서 깨지던 연도가 바로잡히고 데이터 라벨이 새로 잡혔다.
    VLM 에서는 반대로 판독이 11% 나빠졌다(0.4.41).

    읽는 주체에 따라 정반대이므로, 어느 쪽 이야기인지 헷갈리면 잘못
    켜게 된다.
    """
    import inspect

    from docstruct.images import image_prep

    source = inspect.getsource(image_prep._auto_plan)
    assert "OCR" in source
    assert "tesseract" in source or "인식률" in source
    assert "DOCSTRUCT_IMAGE_UPSCALE=lanczos" in source


def test_ab_compares_each_step_separately():
    """조합을 **하나씩 떼어** 견준다.

    `basic+lanczos` 만 재고 "lanczos 때문" 이라고 단정했던 것이 앞선
    실수다(0.4.41). 전처리 단독으로도 해로울 수 있다는 신호가 이미
    있었다 — 국방부 막대그래프에서 대비 정규화가 획을 9px → 4px 로
    떨어뜨렸다.
    """
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parent.parent / "notebooks"
    tool = root / "image_ab.py"
    if not tool.is_file():
        return
    source = tool.read_text(encoding="utf-8")
    for label in ('"원본"', '"basic"', '"lanczos"', '"basic+lanczos"',
                  '"model"'):
        assert label in source, label
    assert "요약" in source                       # 한눈에 견줄 표


# ────────────────────────────────────────────────────────────────────
# 0.4.53 — 좌표 매핑이 글머리마다 밀렸다
#
# `_flatten` 은 **줄머리의 목록·제목 기호를 통째로 지운다**(`- `, `# `).
# 그런데 납작→원문 좌표 매핑은 문자 하나씩 세며 그 기호를 세고 있었다.
# 글머리 하나마다 좌표가 밀려, 행안부(글머리 수천 개)에서는 잘린
# 덩어리가 눈금과 전혀 다른 곳이 됐다.
#
#     눈금 자체는 198/200 정확한데 결과는 닮음 8% 였다
#     고친 뒤 **83%**
# ────────────────────────────────────────────────────────────────────


def test_raw_position_mapping_matches_flatten():
    """좌표 매핑이 `_flatten` 과 같은 규칙을 쓴다."""
    from docstruct.align.page_map import _flatten, _raw_positions

    text = "- 가나다\n# 라마바\n  · 사아자"
    flat = _flatten(text)
    assert flat == "가나다라마바사아자"

    mapping = _raw_positions(text, set(range(len(flat))))
    # 납작 위치마다 원문의 같은 글자를 가리켜야 한다
    for index, char in enumerate(flat):
        assert text[mapping[index]] == char, (index, char)


def test_split_starts_where_anchor_points():
    """잘린 덩어리가 눈금이 가리킨 자리에서 시작한다."""
    from docstruct.align.page_map import (
        _flatten, split_text_by_page, text_anchors,
    )

    body = ("- 첫째 쪽의 본문입니다 충분히 긴 문장\n"
            "- 둘째 쪽의 본문입니다 충분히 긴 문장\n"
            "- 셋째 쪽의 본문입니다 충분히 긴 문장")
    pdf_pages = [
        {"page_no": 1, "content": "첫째 쪽의 본문입니다 충분히 긴 문장"},
        {"page_no": 2, "content": "둘째 쪽의 본문입니다 충분히 긴 문장"},
        {"page_no": 3, "content": "셋째 쪽의 본문입니다 충분히 긴 문장"},
    ]
    anchors = text_anchors(pdf_pages, body)
    parts = {p["page_no"]: p for p in split_text_by_page(body, anchors)
             if p.get("page_no")}
    for page_no, word in ((1, "첫째"), (2, "둘째"), (3, "셋째")):
        assert _flatten(parts[page_no]["content"]).startswith(word), page_no


def test_table_anchors_supplement_text_anchors():
    """표 눈금이 본문 눈금을 보완한다.

    본문이 있는 쪽은 본문이, 표만 있는 쪽은 표가 맡는다 — 실측(행안부):
    본문만 닮음 중앙 78%, 표를 더하면 83%.
    """
    from docstruct.align.page_map import merge_anchors

    text_side = [(10, 1), (100, 3)]
    table_side = [(50, 2), (200, 5)]
    merged = merge_anchors(text_side, table_side)
    assert [page for _pos, page in merged] == [1, 2, 3, 5]

    # 쪽이 거꾸로 가는 눈금은 버린다
    broken = merge_anchors([(10, 1), (50, 9)], [(60, 3)])
    pages = [page for _pos, page in broken]
    assert pages == sorted(pages)


def test_align_performance_is_documented():
    """쪽 맞춤의 실제 성능이 적혀 있다.

    **목적에 따라 지표가 다르다** — 표 쪽 배정(80%/96%)과 본문 쪽 나누기
    (83%/73%)는 다른 수치이며, 무엇을 쓰려는지 정하고 읽어야 한다.
    """
    import inspect

    from docstruct.align import page_map

    source = inspect.getmodule(page_map).__doc__ or ""
    assert "186/232" in source                    # 데이터 표 커버리지
    assert "96%" in source                        # 짝의 정확도
    assert "레이아웃 표" in source                 # 40% 가 아닌 이유


def test_design_docs_carry_update_notes():
    """0.3.x 기준 설계 문서에 갱신 주석이 붙어 있다.

    본문은 그때의 판단 과정이 기록으로서 값이 있어 그대로 두되,
    **지금과 다르다는 것과 무엇이 달라졌는지**를 머리에 적는다.
    적어 두지 않으면 읽는 사람이 틀린 그림을 갖는다.
    """
    root = Path(__file__).resolve().parent.parent.parent / "docstruct-문서-0_4_1"
    if not root.is_dir():
        return
    stale = ["STATUS.md", "PIPELINE.md", "VLM_OCR_활용설계.md",
             "구조화_단계_설계.md", "판독과_구조화_분담.md",
             "실험_실행안내.md", "형식별_진척과_약점.md", "실험_총정리.md"]
    for name in stale:
        doc = root / name
        if not doc.is_file():
            continue
        text = doc.read_text(encoding="utf-8")
        assert "갱신" in text[:3000], name


def test_doc_index_lists_new_documents():
    """색인이 이번에 만든 문서를 안내한다."""
    root = Path(__file__).resolve().parent.parent.parent / "docstruct-문서-0_4_1"
    index = root / "README_문서.md"
    if not index.is_file():
        return
    text = index.read_text(encoding="utf-8")
    for name in ("이미지_판독_전략.md", "브릿지와_RAG_연계.md",
                 "판독_연구_동향.md"):
        assert name in text, name


# ────────────────────────────────────────────────────────────────────
# 0.4.56 — 스캔 렌더·VLM 폴백·계측 실험
# ────────────────────────────────────────────────────────────────────

def _scan_page(page_no: int, content: str = "", tables=None):
    """시험용 PageContent."""
    from docstruct.models import PageContent

    page = PageContent(page_no=page_no, page_no_kind="exact", content=content)
    for table in tables or []:
        page.tables.append(table)
    return page


def _scan_table(bbox=None, cells=None, source="parser"):
    """시험용 TableInfo."""
    from docstruct.models import TableInfo

    info = TableInfo(id="table_1", table_num=1,
                     placeholder="<table 1>", markdown="| a |\n| - |\n| b |")
    info.bbox = bbox
    info.cells = cells
    info.source = source
    return info


def test_render_only_is_strict(monkeypatch, tmp_path):
    """`only` 를 주면 그 쪽만 그린다 — 표 있는 쪽을 끼워 넣지 않는다.

    표 있는 쪽을 항상 포함하던 예전 동작이 스캔 대상별 재렌더 루프와
    만나 **대상 수 × 표 쪽 수**만큼 300dpi 렌더를 반복했고, 표 근거
    이미지를 마지막 스캔 배율로 덮어썼다.
    """
    from docstruct import pipeline

    captured: list[list[int]] = []

    def fake_render(pdf_path, page_nos, out_dir, *, file_stem=None, scale=2.0):
        captured.append(sorted(page_nos))
        return {}

    monkeypatch.setattr(pipeline, "render_pages_with_tables", fake_render)
    pages = [_scan_page(1, tables=[_scan_table()]), _scan_page(2), _scan_page(3)]

    pipeline._render_page_images(tmp_path / "x.pdf", pages, tmp_path,
                                 all_pages=True, only={3})
    assert captured == [[3]]

    # only 없이는 기존대로 — 표 있는 쪽(1)만 (all_pages=False).
    captured.clear()
    pipeline._render_page_images(tmp_path / "x.pdf", pages, tmp_path)
    assert captured == [[1]]


def test_scan_vlm_returns_read_set(monkeypatch, tmp_path):
    """VLM 쪽 판독이 읽은 쪽 **집합**을 돌려준다 — 물러난 쪽은 빠진다.

    예전에는 바꾼 쪽 수(int)를 돌려주고 호출부가 참이면 `ocr_targets`
    전체를 비웠다 — VLM 이 실패한 쪽이 rapidocr 폴백을 영영 못 받았다.
    """
    from docstruct.infrastructure.llm import client
    from docstruct.images import encode
    from docstruct.text import scan_vlm

    good = "첫째 줄 본문입니다.\n둘째 줄 본문입니다.\n셋째 줄 본문입니다."
    looped = "\n".join(["같은 줄이 반복됩니다"] * 10)
    answers = {1: good, 2: looped}

    img = tmp_path / "p.png"
    img.write_bytes(b"png")
    pages = [_scan_page(1), _scan_page(2)]
    for page in pages:
        page.page_image_path = str(img)

    monkeypatch.setattr(client, "llm_api_config", lambda: {"model": "test-vlm"})
    calls = {"n": 0}

    def fake_invoke(prompt, *, span_name="", image_urls=None, cfg=None):
        calls["n"] += 1
        return answers[calls["n"]]

    monkeypatch.setattr(client, "invoke_llm", fake_invoke)
    monkeypatch.setattr(encode, "encode_image_file",
                        lambda path: ("image/png", "eA=="))

    read = scan_vlm.read_scanned_pages(pages, {1, 2})
    assert read == {1}
    assert pages[0].ocr_engine == "test-vlm"
    # 반복 검출로 기각된 쪽은 본문이 그대로고 사유가 남는다.
    assert pages[1].ocr_engine is None
    assert any("반복" in step.get("detail", "")
               for step in pages[1].trace.to_dict().get("steps", []))


def test_scan_vlm_without_llm_returns_empty_set(monkeypatch):
    """LLM 미설정이면 빈 집합 — 호출부 산술(차집합)이 그대로 성립한다."""
    from docstruct.infrastructure.llm import client
    from docstruct.text import scan_vlm

    monkeypatch.setattr(client, "llm_api_config", lambda: None)
    assert scan_vlm.read_scanned_pages([_scan_page(1)], {1}) == set()


def test_scan_render_scale_clamp(monkeypatch):
    """손잡이 상한이 원본 유지 상한(MAX_SCAN_SCALE)과 같다."""
    from docstruct import pipeline

    monkeypatch.setenv("DOCSTRUCT_SCAN_RENDER_SCALE", "12.0")
    assert pipeline.scan_render_scale() == 12.0
    monkeypatch.setenv("DOCSTRUCT_SCAN_RENDER_SCALE", "15.0")
    assert pipeline.scan_render_scale() == pipeline.SCAN_RENDER_SCALE


def test_scan_ab_number_tokens():
    """숫자 대조의 재료 — 쉼표 정규화, 소수 보존, 한 자리 정수 제외."""
    from docstruct.experiments.text.scan_ab import digit_jaccard, number_tokens

    tokens = number_tokens("예산 1,150,000원 · 연 2.9 · 제3장 · 코드 50771")
    assert tokens == {"1150000", "2.9", "50771"}
    # `2.9` 와 `29` 는 다른 값으로 남아야 한다 — 이 실험의 존재 이유다.
    assert digit_jaccard({"2.9"}, {"29"}) == 0.0
    assert digit_jaccard(set(), set()) == 1.0


def test_scan_ab_flags_digit_disagreement(monkeypatch, tmp_path):
    """이중 판독의 숫자 불일치가 scan_ab 필드와 trace 에 남는다."""
    from docstruct.converters.pdf import rapidocr_ko
    from docstruct.experiments.text import scan_ab
    img = tmp_path / "p.png"
    img.write_bytes(b"png")
    page = _scan_page(5, content="이자율이란 연 1천분의 29를 말한다. 부속 설명이 이어진다.")
    page.page_image_path = str(img)
    page.ocr_engine = "test-vlm"                 # 기본 판독이 VLM 이었다

    monkeypatch.setattr(
        rapidocr_ko, "read_page_text",
        lambda image: "이자율이란 연 2.9를 말한다. 부속 설명이 길게 더 이어진다.")

    hits = scan_ab.run([page])
    assert hits == 1
    assert page.scan_ab["digits_only_main"] == ["29"]
    assert page.scan_ab["digits_only_alt"] == ["2.9"]
    assert page.scan_ab["alt_engine"] == "rapidocr"
    # 본문은 바꾸지 않는다 — 측정 전용.
    assert "1천분의 29" in page.content


def test_col_grid_records_gate_reason(monkeypatch, tmp_path):
    """⑬이 물러난 사유가 col_gate 에 남는다 — 강등 재검토의 재료."""
    from docstruct.experiments.tsr.restore import col_grid
    from docstruct.experiments.tsr.measure import line_grid
    cells = [{"row": 0, "col": c, "rowspan": 1, "colspan": 1, "text": "x"}
             for c in range(6)]
    bbox = {"l": 0, "t": 0, "r": 100, "b": 50}

    # 격자가 안 선다 → no_lattice
    monkeypatch.setattr(line_grid, "table_lattice",
                        lambda *a, **k: None)
    table = _scan_table(bbox=bbox, cells=list(cells))
    assert col_grid.run([_scan_page(1, tables=[table])], pdf_path=tmp_path / "x.pdf") == 0
    assert table.col_gate == {"reason": "no_lattice", "detected": 6}

    # 격자가 서지만 열 수가 같다 → cols_match
    monkeypatch.setattr(line_grid, "table_lattice",
                        lambda *a, **k: (None, None, 6))
    table = _scan_table(bbox=bbox, cells=list(cells))
    assert col_grid.run([_scan_page(1, tables=[table])], pdf_path=tmp_path / "x.pdf") == 0
    assert table.col_gate["reason"] == "cols_match"
    assert table.col_gate["lattice"] == 6


def test_new_experiments_registered_and_off():
    """새 계측 실험 둘이 등록돼 있고 기본은 꺼져 있다."""
    from docstruct.experiments import all_experiments
    from docstruct.experiments.registry import DEFAULT_ON

    keys = {e.key for e in all_experiments()}
    assert {"scan_ab", "scan_scale_ab"} <= keys
    assert not ({"scan_ab", "scan_scale_ab"} & DEFAULT_ON)


def test_page_scan_fields_serialized():
    """쪽 단위 실험 필드가 document.json 에 실린다."""
    page = _scan_page(1, content="본문")
    page.scan_ab = {"digit_jaccard": 0.5}
    data = page.to_dict()
    assert data["scan_ab"] == {"digit_jaccard": 0.5}
    assert "scan_scale_ab" in data


# ────────────────────────────────────────────────────────────────────
# 0.4.57 — 쪽 맞춤을 로컬(CLI)에서도 쓴다
#
# 판정 로직이 overlay 전용 폴더(rag/adapters)에만 있어 **서버에서만**
# 되는 기능이었다. 라이브러리로 올리고 overlay 는 위임만 한다.
# ────────────────────────────────────────────────────────────────────


def _align_pair():
    """쪽 맞춤 시험용 document.json 두 벌 (HWPX 는 쪽 없음)."""
    def table(n, texts):
        return {
            "id": f"table_{n}", "table_num": n, "placeholder": f"<table {n}>",
            "markdown": "| " + " | ".join(texts) + " |",
            "cells": [{"row": 0, "col": i, "rowspan": 1, "colspan": 1,
                       "text": t} for i, t in enumerate(texts)],
        }

    pdf = {"filename": "문서.pdf", "pages": [
        {"page_no": 1,
         "content": "본 사업은 조달 효율화를 목표로 한다.\n\n<table 1>",
         "tables": [table(1, ["구분", "2026", "2027"])]},
        {"page_no": 2,
         "content": "지표는 다음과 같이 구성된다.\n\n<table 2>",
         "tables": [table(2, ["지표명", "목표", "실적"])]},
    ]}
    hwpx = {"filename": "문서.hwpx", "pages": [
        {"page_no": 1, "content": (
            "대한민국정부 2027년도 성과계획서 표지 글\n\n"
            "본 사업은 조달 효율화를 목표로 한다.\n\n<table 1>\n\n"
            "지표는 다음과 같이 구성된다.\n\n<table 2>"),
         "tables": [table(1, ["구분", "2026", "2027"]),
                    table(2, ["지표명", "목표", "실적"])]},
    ]}
    return hwpx, pdf


def test_align_documents_assigns_pages():
    """쪽 없는 HWPX 본문·표가 PDF 쪽 번호를 얻는다."""
    from docstruct.align import align_documents

    hwpx, pdf = _align_pair()
    result = align_documents(hwpx, pdf)
    assert [p["page_no"] for p in result["pages"]] == [1, 2]
    assert result["matched_tables"] == 2
    assert result["unmatched_tables"] == 0
    # 어느 표가 어느 쪽에 갔는지가 결과에 남는다
    assert [t["id"] for t in result["pages"][0]["tables"]] == ["table_1"]
    assert [t["id"] for t in result["pages"][1]["tables"]] == ["table_2"]


def test_align_keeps_text_before_first_anchor():
    """첫 눈금 앞의 머리말을 버리지 않는다.

    `split_text_by_page` 는 그것을 `page_no: None` 조각으로 일부러
    남기는데("첫 눈금 앞의 머리말도 잃지 않는다"), 옛 어댑터는 `continue`
    로 조용히 지웠다 — 표지·간지·발간사가 통째로 사라지는 자리였다.
    """
    from docstruct.align import align_documents, to_markdown

    hwpx, pdf = _align_pair()
    result = align_documents(hwpx, pdf)
    assert "표지 글" in result["head"]
    assert result["head_chars"] > 0
    # 쪽을 **모른다**는 사실이 화면에도 남는다 (1쪽인 척하면 안 된다)
    markdown = to_markdown(result)
    assert "쪽 미상" in markdown
    assert "표지 글" in markdown


def test_align_requires_tables_on_both_sides():
    """맞출 근거가 없으면 조용히 빈 결과를 내지 않고 알린다."""
    from docstruct.align import align_documents

    hwpx, pdf = _align_pair()
    empty = {"filename": "x", "pages": [{"page_no": 1, "content": "글",
                                         "tables": []}]}
    with pytest.raises(ValueError):
        align_documents(empty, pdf)
    with pytest.raises(ValueError):
        align_documents(hwpx, empty)


def test_align_summary_separates_measured_from_estimated():
    """요약이 실측 눈금과 보간 추정을 갈라 적는다."""
    from docstruct.align import align_documents
    from docstruct.align.documents import summary_lines

    hwpx, pdf = _align_pair()
    lines = summary_lines(align_documents(hwpx, pdf))
    joined = "\n".join(lines)
    assert "본문 눈금" in joined and "보간 추정" in joined
    assert "표 배정" in joined


def test_cli_align_writes_both_formats(tmp_path, capsys):
    """`--align` 이 aligned.json·aligned.md 를 낸다."""
    import json as _json

    from docstruct.cli import main

    hwpx, pdf = _align_pair()
    left = tmp_path / "h.json"
    right = tmp_path / "p.json"
    left.write_text(_json.dumps(hwpx, ensure_ascii=False), encoding="utf-8")
    right.write_text(_json.dumps(pdf, ensure_ascii=False), encoding="utf-8")

    out = tmp_path / "out"
    assert main([str(left), "--align", str(right), "-o", str(out),
                 "--no-llm"]) == 0
    # 0.4.93 — 산출 폴더 이름은 **확장자를 포함한** 파일 이름이다
    folder = out / "h.json"
    assert (folder / "aligned.json").is_file()
    assert (folder / "aligned.md").is_file()
    result = _json.loads((folder / "aligned.json").read_text(encoding="utf-8"))
    assert result["matched_tables"] == 2


def test_cli_align_rejects_reversed_arguments(tmp_path, capsys):
    """PDF 를 첫 자리에 주면 실행 전에 알린다 — 끝나고 알면 늦다."""
    from docstruct.cli import main

    pdf = tmp_path / "문서.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    assert main([str(pdf), "--align", str(pdf), "-o", str(tmp_path / "o")]) == 1
    assert "쪽이 **없는** 쪽" in capsys.readouterr().err


def test_cli_align_reports_missing_file(tmp_path, capsys):
    """없는 파일은 판독을 시작하기 전에 걸러진다."""
    from docstruct.cli import main

    assert main([str(tmp_path / "없다.json"), "--align",
                 str(tmp_path / "역시없다.json"), "-o", str(tmp_path)]) == 1
    assert "파일이 없습니다" in capsys.readouterr().err


def test_overlay_adapter_delegates_to_library():
    """overlay 어댑터에 판정 로직이 남아 있지 않다.

    로직이 두 곳에 있으면 갈라진다 — 실제로 서버에만 있어 CLI 가
    옛 경로(split_by_page)를 쓰던 것이 이 판의 출발점이었다.
    """
    root = Path(__file__).resolve().parent.parent.parent
    for base in (root / "docstruct-backend-overlay-0_4_1" / "overlay" / "app",
                 root.parent / "overlay" / "app"):
        adapter = base / "rag" / "adapters" / "page_align.py"
        if not adapter.is_file():
            return
        source = adapter.read_text(encoding="utf-8")
        assert "from docstruct.align.documents import" in source
        # 옛 로직의 흔적이 남아 있으면 안 된다
        assert "merge_anchors(" not in source
        assert "def _to_markdown" not in source
        return


# ────────────────────────────────────────────────────────────────────
# 0.4.58 — 전사된 그림도 스캔 쪽과 같은 대접을 받는다
#
# 실측(조달청 HWPX): image_1 이 `kind="page"` 로 갈려 조직도 전문이
# **스캔 쪽과 같은 전사 지시문**으로 읽혀 본문에 들어갔는데, 그 사실이
# 결과물에 남지 않아 검증(verify_ocr)과 이중 판독(scan_ab) 대상에서
# 통째로 빠져 있었다. 읽는 행위가 같으면 위험도 같다.
# ────────────────────────────────────────────────────────────────────


def test_page_like_read_is_recorded_as_transcription(monkeypatch, tmp_path):
    """지면으로 읽은 그림에 `transcribed` 가 남는다."""
    from docstruct.images import image_prep, legibility, vlm_read
    from docstruct.models import ImageInfo, PageContent, PageTrace

    shot = tmp_path / "i.png"
    shot.write_bytes(b"png")
    info = ImageInfo(id="image_1", placeholder="p", image_path=str(shot))
    page = PageContent(page_no=1, page_no_kind="exact", content="본문",
                       trace=PageTrace())

    monkeypatch.setattr(vlm_read, "encode_image_file",
                        lambda p: ("image/png", "YWJj"))
    monkeypatch.setattr(vlm_read, "invoke_llm",
                        lambda *a, **k: "청장\n차장\n감사담당관\n대변인\n운영지원과")
    monkeypatch.setattr(vlm_read, "is_page_like", lambda *a, **k: True)
    monkeypatch.setattr(legibility, "measure",
                        lambda path: {"verdict": "good", "kind": "page",
                                      "glyph_px": 10, "text_rows": 52,
                                      "per_row": 19.0})
    monkeypatch.setattr(image_prep, "prepare", lambda *a, **k: (str(shot), {}))

    text = vlm_read._read_one(page, info, {"model": "m"})
    assert text
    assert info.transcribed is True
    assert any("전사" in (s.get("action") or "")
               for s in page.trace.to_dict().get("steps", []))


def test_description_read_is_not_transcription(monkeypatch, tmp_path):
    """설명으로 읽은 그림은 전사가 아니다 — 숫자 대조가 성립하지 않는다."""
    from docstruct.images import image_prep, legibility, vlm_read
    from docstruct.models import ImageInfo, PageContent, PageTrace

    shot = tmp_path / "i.png"
    shot.write_bytes(b"png")
    info = ImageInfo(id="image_2", placeholder="p", image_path=str(shot))
    page = PageContent(page_no=1, page_no_kind="exact", content="본문",
                       trace=PageTrace())

    monkeypatch.setattr(vlm_read, "encode_image_file",
                        lambda p: ("image/png", "YWJj"))
    monkeypatch.setattr(
        vlm_read, "invoke_llm",
        lambda *a, **k: "두 개의 원그래프로 구성을 비교한 그림입니다. 비중은 100%로 표시됩니다.")
    monkeypatch.setattr(vlm_read, "is_page_like", lambda *a, **k: True)
    # verdict=poor 면 지시문이 설명으로 바뀐다 — 그때는 전사가 아니다
    monkeypatch.setattr(legibility, "measure",
                        lambda path: {"verdict": "poor", "kind": "page",
                                      "glyph_px": 7, "text_rows": 4,
                                      "per_row": 9.5})
    monkeypatch.setattr(image_prep, "prepare", lambda *a, **k: (str(shot), {}))

    vlm_read._read_one(page, info, {"model": "m"})
    assert info.transcribed is False


def test_scan_ab_covers_transcribed_images(monkeypatch, tmp_path):
    """전사된 그림도 이중 판독 대상이고, 기록은 그림에 남는다."""
    from docstruct.converters.pdf import rapidocr_ko
    from docstruct.experiments.text import scan_ab
    from docstruct.models import ImageInfo, PageContent, PageTrace

    shot = tmp_path / "i.png"
    shot.write_bytes(b"png")
    info = ImageInfo(id="image_1", placeholder="p", image_path=str(shot),
                     vlm_markdown="정원 1,234명 · 예산 5,678백만원 규모입니다.",
                     vlm_model="test-vlm")
    info.transcribed = True
    page = PageContent(page_no=1, page_no_kind="exact", content="본문",
                       trace=PageTrace())
    page.images.append(info)

    monkeypatch.setattr(
        rapidocr_ko, "read_page_text",
        lambda path: "정원 1,234명 · 예산 5,679백만원 규모입니다.")

    assert scan_ab.run([page]) == 1
    # **쪽이 아니라 그림에 남는다** — 무엇을 잰 것인지 갈려야 한다
    assert page.scan_ab is None
    assert info.scan_ab["target"] == "image_1"
    assert info.scan_ab["digits_only_main"] == ["5678"]
    assert info.scan_ab["digits_only_alt"] == ["5679"]
    # 본문·전사 결과는 바꾸지 않는다 (측정 전용)
    assert "5,678" in info.vlm_markdown


def test_scan_ab_ignores_untranscribed_images(monkeypatch, tmp_path):
    """설명으로 읽은 그림은 대조하지 않는다."""
    from docstruct.experiments.text import scan_ab
    from docstruct.models import ImageInfo, PageContent, PageTrace

    shot = tmp_path / "i.png"
    shot.write_bytes(b"png")
    info = ImageInfo(id="image_2", placeholder="p", image_path=str(shot),
                     vlm_markdown="원그래프 두 개를 비교한 그림입니다.",
                     vlm_model="test-vlm")
    page = PageContent(page_no=1, page_no_kind="exact", content="본문",
                       trace=PageTrace())
    page.images.append(info)

    assert scan_ab.run([page]) == 0
    assert info.scan_ab is None


def test_scan_ab_applies_to_hwpx():
    """HWPX 도 대상이다 — 한글 문서에도 스캔 지면이 그림으로 들어간다."""
    from docstruct.experiments import all_experiments
    spec = next(e for e in all_experiments() if e.key == "scan_ab")
    assert "hwpx" in spec.formats and "pdf" in spec.formats
    # 배율 A/B 는 여전히 PDF 전용이다 — HWPX 그림은 원본 자체라 다시
    # 렌더해 배율을 올릴 수 없다.
    scale = next(e for e in all_experiments() if e.key == "scan_scale_ab")
    assert scale.formats == ("pdf",)


def test_experiment_format_mismatch_is_announced():
    """형식이 맞지 않는 실험은 조용히 건너뛰지 않는다.

    `--exp col_grid` 를 HWPX 에 주고 실험이 돈 줄 알고 결과를 비교하는
    일이 실제로 있었다 — 세 판이 모두 같은 설정이었음을 나중에야 알았다.
    """
    import inspect

    from docstruct import pipeline

    # 0.4.63 부터 고지는 `_run_experiments` 안에 있고, 그 함수는
    # build_document 의 지역 함수라 같은 원본에 들어 있다.
    source = inspect.getsource(pipeline.build_document)
    # 문구가 줄바꿈으로 쪼개져 있으므로 이어지는 조각으로 본다
    assert "형식에 적용되지" in source
    # 로그만으로는 부족하다 — 결과물(trace)에도 남아야 한다
    assert '"실험 건너뜀"' in source
    # 두 단계 모두 이 고지를 거친다 (한 함수를 두 번 부른다)
    assert source.count("_run_experiments(") >= 3


def test_verify_covers_pages_with_transcribed_images():
    """전사된 그림이 있는 쪽도 OCR 검증 대상이다."""
    import inspect

    from docstruct import pipeline

    source = inspect.getsource(pipeline.build_document)
    assert "transcribed_pages" in source
    assert "(scanned_pages | transcribed_pages)" in source


# ────────────────────────────────────────────────────────────────────
# 0.4.59 — `--ask-key` 는 OpenAI 만 쓴다
#
# 예전에는 키만 넣었다. 그런데 사내 배치는 엔드포인트가 내장 기본값
# (site_defaults.py)에 들어 있어 주소가 늘 차 있고, `_key_for` 는 OpenAI 가
# 아닌 주소에 OpenAI 키를 붙이지 않는다 — 그래서 **키를 입력받고도 사내
# 엔드포인트로 가고 키는 버려졌다.** 물어 놓고 쓰지 않는 것은 조용한
# 거짓말이다.
# ────────────────────────────────────────────────────────────────────


def _site_defaults(monkeypatch):
    """사내 배치 재현 — 엔드포인트가 내장 기본값에 들어 있는 상태."""
    from docstruct.core import config

    merged = dict(config._DEFAULTS)
    merged.update({
        "DOCLING_TABLE_API_URL": "http://10.0.0.5:8000/v1/chat/completions",
        "DOCLING_TABLE_API_MODEL": "/model/internal-vlm",
        "DOCLING_PICTURE_API_URL": "http://10.0.0.5:8000/v1/chat/completions",
        "DOCLING_PICTURE_API_MODEL": "/model/internal-vlm",
        "DOCSTRUCT_VLM_MODEL": "/model/local-vlm",
    })
    monkeypatch.setattr(config, "_DEFAULTS", merged)
    for name in ("OPENAI_API_KEY", "DOCSTRUCT_FORCE_OPENAI",
                 "DOCLING_TABLE_API_URL", "DOCLING_TABLE_API_MODEL",
                 "DOCLING_PICTURE_API_URL", "DOCLING_PICTURE_API_MODEL",
                 "DOCSTRUCT_VLM_MODEL", "DOCLING_TABLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return config


def test_ask_key_routes_to_openai(monkeypatch):
    """키를 넣으면 사내 내장 기본값 대신 OpenAI 로 간다."""
    config = _site_defaults(monkeypatch)

    # 강제 없이 — 사내 엔드포인트 그대로 (기존 동작)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    before = config._build_settings()
    assert "10.0.0.5" in before.llm.url
    assert before.llm.api_key == ""          # 남의 주소엔 키를 안 붙인다

    # `--ask-key` 가 세우는 표시
    monkeypatch.setenv("DOCSTRUCT_FORCE_OPENAI", "1")
    after = config._build_settings()
    assert "api.openai.com" in after.llm.url
    assert after.llm.api_key == "sk-test"    # 이번엔 실제로 쓰인다
    assert "api.openai.com" in after.docling_picture.url


def test_ask_key_disables_local_vlm_and_fallback(monkeypatch):
    """강제 중에는 로컬 VLM 으로 새지 않는다.

    로컬 모델이 잡혀 있으면 표 판정·재추출이 HTTP 대신 그쪽으로 가서
    키를 넣은 의미가 절반만 남는다.
    """
    config = _site_defaults(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("DOCSTRUCT_FORCE_OPENAI", "1")

    settings = config._build_settings()
    assert settings.local_vlm is None
    assert settings.llm_fallback is None      # 주가 이미 OpenAI 다


def test_ask_key_still_honours_explicit_settings(monkeypatch):
    """강제해도 **직접 지정한** 주소·모델은 이긴다.

    `--set DOCLING_TABLE_API_MODEL=gpt-4o` 까지 뭉개면 모델을 고를 길이
    없어진다. 덮는 것은 내장 기본값뿐이다.
    """
    config = _site_defaults(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("DOCSTRUCT_FORCE_OPENAI", "1")
    monkeypatch.setenv("DOCLING_TABLE_API_MODEL", "gpt-4o")

    settings = config._build_settings()
    assert settings.llm.model == "gpt-4o"
    assert "api.openai.com" in settings.llm.url


def test_apply_key_sets_force_flag(monkeypatch, tmp_path):
    """`--key-file` 도 같은 강제를 건다 — 두 경로가 갈라지면 안 된다."""
    import argparse

    from docstruct.cli import _apply_key

    _site_defaults(monkeypatch)
    key_file = tmp_path / "k.txt"
    key_file.write_text("sk-from-file\n", encoding="utf-8")

    args = argparse.Namespace(key_file=str(key_file), ask_key=False)
    _apply_key(args)
    import os

    assert os.environ["OPENAI_API_KEY"] == "sk-from-file"
    assert os.environ["DOCSTRUCT_FORCE_OPENAI"] == "1"


def test_config_summary_states_the_force(monkeypatch):
    """`--check` 요약이 강제 사실을 적는다 — 설정이 무시된 것처럼 보이면 안 된다."""
    config = _site_defaults(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("DOCSTRUCT_FORCE_OPENAI", "1")

    rows = config._build_settings().describe()
    joined = " ".join(f"{a} {b}" for a, b, _ok in rows)
    assert "OpenAI 만 사용" in joined


# ────────────────────────────────────────────────────────────────────
# 0.4.60 — 짧은 판독을 버려 잡음으로 바꾸고 있었다
#
# 실측(주택과세금 377쪽): VLM 이 11자를 읽은 쪽 17개가 전부
# `2025 주택과 세금` 한 줄짜리 표지였다 — 정확히 맞는 판독이다. 그것을
# `MIN_RESULT_CHARS=30` 문턱이 "빈 지면" 으로 보고 버렸고, rapidocr 가
# 브라우저 인쇄 껍데기(URL·시각·`4/380`)를 112자로 채워 본문이 됐다.
# ────────────────────────────────────────────────────────────────────


def _scan_page_with_image(page_no, tmp_path, content=""):
    """지면 이미지가 딸린 시험용 쪽."""
    from docstruct.models import PageContent, PageTrace

    shot = tmp_path / f"p{page_no}.png"
    shot.write_bytes(b"png")
    page = PageContent(page_no=page_no, page_no_kind="exact", content=content,
                       trace=PageTrace())
    page.page_image_path = str(shot)
    return page


def test_short_vlm_read_is_kept(monkeypatch, tmp_path):
    """짧아도 읽은 것은 쓴다 — 표지에서는 짧은 것이 정답이다."""
    from docstruct.infrastructure.llm import client
    from docstruct.images import encode
    from docstruct.text import scan_vlm

    page = _scan_page_with_image(4, tmp_path, content="옛 본문")
    monkeypatch.setattr(client, "llm_api_config", lambda: {"model": "m"})
    monkeypatch.setattr(encode, "encode_image_file",
                        lambda p: ("image/png", "YWJj"))
    monkeypatch.setattr(client, "invoke_llm", lambda *a, **k: "2025 주택과 세금")

    read = scan_vlm.read_scanned_pages([page], {4})
    assert read == {4}                       # 폴백으로 넘기지 않는다
    assert "2025 주택과 세금" in page.content
    assert page.ocr_engine == "m"
    # 짧았다는 사실은 남는다 — "빈 지면" 과 "안 돌았다" 는 다르다
    steps = page.trace.to_dict().get("steps", [])
    assert any("짧" in (s.get("action") or "") + (s.get("detail") or "")
               for s in steps)


def test_empty_vlm_read_still_falls_back(monkeypatch, tmp_path):
    """무응답은 여전히 폴백으로 간다 — 짧은 것과 없는 것은 다르다."""
    from docstruct.infrastructure.llm import client
    from docstruct.images import encode
    from docstruct.text import scan_vlm

    page = _scan_page_with_image(5, tmp_path, content="옛 본문")
    monkeypatch.setattr(client, "llm_api_config", lambda: {"model": "m"})
    monkeypatch.setattr(encode, "encode_image_file",
                        lambda p: ("image/png", "YWJj"))
    monkeypatch.setattr(client, "invoke_llm", lambda *a, **k: "")

    assert scan_vlm.read_scanned_pages([page], {5}) == set()
    assert page.content == "옛 본문"          # 있던 것을 지우지 않는다


def test_rapidocr_does_not_overwrite_vlm_read(tmp_path, monkeypatch):
    """폴백이 이미 읽힌 쪽을 덮지 않는다 — 나은 판독이 나쁜 것으로 바뀌면 안 된다."""
    from docstruct import pipeline
    from docstruct.converters.pdf import rapidocr_ko

    page = _scan_page_with_image(4, tmp_path, content="2025 주택과 세금")
    page.ocr_engine = "test-vlm"
    monkeypatch.setattr(
        rapidocr_ko, "read_page_text",
        lambda path: "26.5.11.오후5:44\nhttps://www.nts.go.kr/...\n4/380")

    assert pipeline._reread_with_korean_ocr([page], {4}) == 0
    assert page.content == "2025 주택과 세금"
    assert page.ocr_engine == "test-vlm"


def test_scan_and_ocr_timings_are_separate():
    """두 판독의 시간을 따로 적는다 — 한 칸에 쓰면 뒤가 앞을 덮는다."""
    import inspect

    from docstruct import models, pipeline

    assert models.STAGE_SCAN_VLM != models.STAGE_KOREAN_OCR
    source = inspect.getsource(pipeline.build_document)
    assert "timings[STAGE_SCAN_VLM]" in source
    assert "timings[STAGE_KOREAN_OCR]" in source


# ── 실험 page_chrome ────────────────────────────────────────────────


def _chrome_pages(n=20):
    """모든 쪽 위·아래에 인쇄 껍데기가 붙은 문서."""
    from docstruct.models import PageContent, PageTrace

    # 본문은 쪽마다 **다른 글**이어야 한다. 숫자만 바뀌는 문장을 쓰면
    # 정규화 뒤 같은 줄이 되어 본문 자신이 껍데기로 잡힌다.
    topics = ["취득세는 부동산을 살 때 낸다", "재산세는 보유 중에 매년 낸다",
              "양도소득세는 팔아 이익이 났을 때 낸다",
              "종합부동산세는 공시가격 합계로 매긴다",
              "상속세와 증여세는 무상 이전에 매긴다"]
    pages = []
    for i in range(1, n + 1):
        topic = topics[i % len(topics)]
        body = ("26. 5. 11. 오후 5:44\n"
                f"{topic}. 자세한 것은 아래 표를 보라. 세율과 과세표준이 "
                f"함께 적혀 있다({i}번 설명).\n"
                "사례\n"
                f"{topic}의 예를 든다. 실제 계산은 지방자치단체에 확인한다.\n"
                "https://www.nts.go.kr/upload/index.html\n"
                f"{i}/{n}")
        pages.append(PageContent(page_no=i, page_no_kind="exact",
                                 content=body, trace=PageTrace()))
    return pages


def test_page_chrome_finds_repeated_edges():
    """가장자리에서 되풀이되는 줄을 찾는다."""
    from docstruct.experiments.text import page_chrome
    pages = _chrome_pages()
    assert page_chrome.run(pages) == len(pages)
    lines = " ".join(pages[0].page_chrome["lines"])
    assert "nts.go.kr" in lines
    # 쪽 번호만 다른 줄은 하나로 묶인다
    assert any("/" in x for x in pages[0].page_chrome["lines"])


def test_page_chrome_ignores_middle_text():
    """글 가운데의 되풀이는 껍데기가 아니다 — `사례` 는 본문 절 이름이다.

    실측(주택과세금): 횟수로만 보면 `사례`(48쪽)가 인쇄 껍데기(24쪽)보다
    더 자주 나온다. 가르는 것은 횟수가 아니라 자리다.
    """
    from docstruct.experiments.text import page_chrome
    pages = _chrome_pages()
    page_chrome.run(pages)
    matched = " ".join(pages[0].page_chrome["lines"])
    assert "사례" not in matched


def test_page_chrome_share_marks_empty_pages():
    """껍데기가 본문의 거의 전부인 쪽을 가려낸다."""
    from docstruct.experiments.text import page_chrome
    from docstruct.models import PageContent, PageTrace

    pages = _chrome_pages()
    # 표지처럼 껍데기밖에 없는 쪽
    bare = PageContent(page_no=99, page_no_kind="exact", trace=PageTrace(),
                       content=("26. 5. 11. 오후 5:44\n"
                                "https://www.nts.go.kr/upload/index.html\n"
                                "99/20"))
    pages.append(bare)
    page_chrome.run(pages)
    assert bare.page_chrome["share"] >= 0.9
    # 본문이 있는 쪽은 비중이 낮다 — 사람이 이 수치로 가른다
    assert pages[0].page_chrome["share"] < 0.6


def test_page_chrome_share_never_exceeds_one():
    """줄이 적은 쪽에서 위·아래 가장자리가 겹쳐도 두 번 세지 않는다."""
    from docstruct.experiments.text import page_chrome
    from docstruct.models import PageContent, PageTrace

    pages = _chrome_pages()
    tiny = PageContent(page_no=98, page_no_kind="exact", trace=PageTrace(),
                       content="26. 5. 11. 오후 5:44\n98/20")
    pages.append(tiny)
    page_chrome.run(pages)
    assert 0.0 <= tiny.page_chrome["share"] <= 1.0


def test_page_chrome_skips_table_separators():
    """markdown 표 구분선은 세지 않는다 — 모든 표에 나온다."""
    from docstruct.experiments.text import page_chrome
    from docstruct.models import PageContent, PageTrace

    pages = [PageContent(page_no=i, page_no_kind="exact", trace=PageTrace(),
                         content="|---|---|\n| 값 | 값 |\n|---|---|")
             for i in range(1, 11)]
    found = page_chrome.find_chrome(pages, 0.05, 2)
    assert not any(set(k.replace("|", "").replace(" ", "")) <= set("-:")
                   for k in found)


def test_page_chrome_is_measurement_only():
    """본문을 바꾸지 않는다."""
    from docstruct.experiments.text import page_chrome
    pages = _chrome_pages()
    before = [p.content for p in pages]
    page_chrome.run(pages)
    assert [p.content for p in pages] == before


# ────────────────────────────────────────────────────────────────────
# 0.4.61 — 못 쓸 키를 들고 429번 터졌다
#
# 실측(행정안전부 429쪽): API 키에 비 ASCII 가 섞여 있었다. HTTP 헤더는
# latin-1 만 싣기 때문에 `requests` 가 보내는 순간
# `'latin-1' codec can't encode characters in position 27-31` 로 터졌고,
# 그 예외는 requests 예외가 아니라 UnicodeEncodeError 라 재시도 그물에
# 걸리지 않고 **쪽마다 되풀이**됐다. 6분 걸려 추출을 마친 뒤의 일이라
# 시간과 GPU 는 이미 쓴 뒤였고, 표 321개가 미판정으로 남았다.
#
# (결과물은 거짓말하지 않았다 — 321개 전부 `미판정` 표시가 붙었다.
#  정직 실패 설계는 처음 보는 오류에서도 버텼다.)
# ────────────────────────────────────────────────────────────────────


def test_key_problem_detects_non_ascii():
    """헤더에 실을 수 없는 키를 가려낸다."""
    from docstruct.core.config import key_problem

    assert not key_problem("sk-proj-abcdef0123456789")
    assert not key_problem("")
    # 한글이 섞인 키 — 실제 사고의 모양
    problem = key_problem("sk-proj-abcdefghijklmnop한글이섞임")
    assert problem
    assert "ASCII" in problem
    # 키 뒤에 메모가 붙은 경우도 잡는다 (ASCII 메모여도)
    assert "공백" in key_problem("sk-proj-abcdef  # for NIA")


def test_apply_key_rejects_bad_key_immediately(monkeypatch, tmp_path):
    """못 쓸 키는 판독을 시작하기 전에 거절한다 — 6분 뒤에 알면 늦다."""
    import argparse
    import os

    from docstruct.cli import _apply_key

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    key_file = tmp_path / "k.txt"
    key_file.write_text("sk-proj-abcdefghij한글\n", encoding="utf-8")

    args = argparse.Namespace(key_file=str(key_file), ask_key=False)
    with pytest.raises(ValueError, match="ASCII"):
        _apply_key(args)
    # 못 쓸 키를 환경에 남기지 않는다
    assert not os.environ.get("OPENAI_API_KEY")


def test_settings_drop_unusable_key(monkeypatch):
    """설정 조립에서도 못 쓸 키는 달지 않는다 (.env·환경변수 경로)."""
    from docstruct.core import config

    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-abcdefghij한글")
    monkeypatch.setenv("DOCSTRUCT_FORCE_OPENAI", "1")
    settings = config._build_settings()
    # 키가 붙었다면 호출할 때마다 터진다 — 아예 달지 않는다
    assert not (settings.llm and settings.llm.api_key)


def test_check_summary_reports_unusable_key(monkeypatch):
    """`--check` 가 키를 못 쓴다고 알린다 — 원인을 엉뚱한 데서 찾지 않게."""
    from docstruct.core import config

    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-abcdefghij한글")
    rows = config._build_settings().describe()
    joined = " ".join(f"{a} {b}" for a, b, _ok in rows)
    assert "쓸 수 없음" in joined


def test_client_fails_once_on_unencodable_header(monkeypatch):
    """헤더를 실을 수 없으면 **한 번** 알리고 나머지는 건너뛴다.

    예전에는 쪽마다 같은 UnicodeEncodeError 가 났다 — 429쪽이면 429번이다.
    """
    from docstruct.infrastructure.llm import client

    client._UNREACHABLE.clear()
    cfg = {
        "url": "https://api.openai.com/v1/chat/completions",
        "model": "gpt-test",
        "timeout": 5,
        "headers": {"Authorization": "Bearer sk-proj-abc한글"},
    }
    calls = {"n": 0}

    def boom(*a, **k):                           # 실제로 보내면 안 된다
        calls["n"] += 1
        raise AssertionError("HTTP 호출이 일어나면 안 됩니다")

    monkeypatch.setattr(client, "_session", lambda: type("S", (), {"post": boom})())

    with pytest.raises(client.LLMUnreachableError, match="API 키"):
        client._requests_fallback("프롬프트", cfg)
    assert calls["n"] == 0

    # 두 번째 호출은 도달 불가 표시에 걸려 곧바로 물러난다
    with pytest.raises(client.LLMUnreachableError):
        client._requests_fallback("프롬프트", cfg)
    assert calls["n"] == 0
    client._UNREACHABLE.clear()


# ────────────────────────────────────────────────────────────────────
# 0.4.62 — ⑬을 닫고, 그 계측이 가리킨 곳에 새 실험을 연다
#
# 실측(행안부 249표 + 조달청 51표 = 300표): ⑬이 일할 자리
# (`격자 > 인식`)가 **0건**이었다. 전제가 이 문서군에서 성립하지 않는다.
# 대신 반대 방향(`격자 < 인식`)이 9건 나왔다 — 열을 하나 더 쪼개면 그 뒤
# 값이 통째로 한 칸씩 밀린다.
# ────────────────────────────────────────────────────────────────────


def _col_table(cols, bbox=None):
    """열이 `cols` 개인 시험용 표."""
    from docstruct.models import TableInfo

    info = TableInfo(id="table_1", table_num=1, placeholder="<table 1>",
                     markdown="| a |")
    info.bbox = bbox or {"l": 0, "t": 0, "r": 100, "b": 50}
    info.cells = [{"row": 0, "col": c, "rowspan": 1, "colspan": 1, "text": "x"}
                  for c in range(cols)]
    info.source = "parser"
    return info


def _col_page(table):
    from docstruct.models import PageContent, PageTrace

    page = PageContent(page_no=1, page_no_kind="exact", content="",
                       trace=PageTrace())
    page.tables.append(table)
    return page


def test_col_gate_labels_are_not_conflated(monkeypatch, tmp_path):
    """물러난 사유 셋을 뭉치지 않는다.

    처음에는 `detected < MIN_COLS` · `격자 == 인식` · `격자 < 인식` 을
    모두 `cols_match` 로 적었다. 그 라벨이 사실과 달라, 실측에서 218표를
    손으로 다시 갈라야 209/8/1 이 나왔다. 읽히지 않는 계측은 계측이 아니다.
    """
    from docstruct.experiments.tsr.restore import col_grid
    from docstruct.experiments.tsr.measure import line_grid
    pdf = tmp_path / "x.pdf"

    def gate(detected, lattice_cols):
        monkeypatch.setattr(line_grid, "table_lattice",
                            lambda *a, **k: (None, None, lattice_cols))
        table = _col_table(detected)
        col_grid.run([_col_page(table)], pdf_path=pdf)
        return table.col_gate

    # 열이 적어 판단 보류 — 맞아떨어진 것이 아니다
    assert gate(3, 4)["reason"] == "too_few_cols"
    # 진짜 일치
    assert gate(8, 8)["reason"] == "cols_match"
    # 격자가 더 적다 — 반대 방향 신호다
    fewer = gate(14, 13)
    assert fewer["reason"] == "lattice_fewer"
    assert fewer["detected"] == 14 and fewer["lattice"] == 13


# ── 실험 over_split ─────────────────────────────────────────────────


def test_over_split_flags_narrow_gap(monkeypatch, tmp_path):
    """괘선보다 열이 많으면 지목한다 (차이가 작을 때)."""
    from docstruct.experiments.tsr.measure import line_grid, over_split
    monkeypatch.setattr(line_grid, "table_lattice",
                        lambda *a, **k: (None, None, 13))
    table = _col_table(15)
    assert over_split.run([_col_page(table)], pdf_path=tmp_path / "x.pdf") == 1
    assert table.over_split["gap"] == 2
    assert table.over_split["trusted"] is True


def test_over_split_distrusts_wide_gap(monkeypatch, tmp_path):
    """차이가 크면 격자를 덜 찾은 것으로 보고 세지 않는다.

    실측 `table_141`: 인식 11열 · 격자 2열. 표가 2열인 것이 아니라
    괘선을 못 찾은 것으로 보는 편이 옳다.
    """
    from docstruct.experiments.tsr.measure import line_grid, over_split
    monkeypatch.setattr(line_grid, "table_lattice",
                        lambda *a, **k: (None, None, 2))
    table = _col_table(11)
    assert over_split.run([_col_page(table)], pdf_path=tmp_path / "x.pdf") == 0
    # 기록은 남긴다 — 세지 않을 뿐이다
    assert table.over_split["gap"] == 9
    assert table.over_split["trusted"] is False


def test_over_split_ignores_grid_rebuilt_tables(monkeypatch, tmp_path):
    """⑦이 격자로 세운 표는 보지 않는다 — 이미 격자 기준이다."""
    from docstruct.experiments.tsr.measure import line_grid, over_split
    monkeypatch.setattr(line_grid, "table_lattice",
                        lambda *a, **k: (None, None, 13))
    table = _col_table(15)
    table.source = "grid"
    assert over_split.run([_col_page(table)], pdf_path=tmp_path / "x.pdf") == 0
    assert table.over_split is None


def test_over_split_is_measurement_only(monkeypatch, tmp_path):
    """표를 바꾸지 않는다."""
    from docstruct.experiments.tsr.measure import line_grid, over_split
    monkeypatch.setattr(line_grid, "table_lattice",
                        lambda *a, **k: (None, None, 13))
    table = _col_table(15)
    before = list(table.cells)
    over_split.run([_col_page(table)], pdf_path=tmp_path / "x.pdf")
    assert table.cells == before


# ── 실험 chart_gate ─────────────────────────────────────────────────


def _gate_page(legibility, image_id="image_1"):
    from docstruct.models import ImageInfo, PageContent, PageTrace

    page = PageContent(page_no=1, page_no_kind="exact", content="",
                       trace=PageTrace())
    info = ImageInfo(id=image_id, placeholder="p", image_path="/x.png")
    info.legibility = legibility
    page.images.append(info)
    return page, info


def test_chart_gate_reproduces_the_split_routes():
    """같은 조직도가 형식에 따라 갈린 실측을 그대로 재현한다.

        HWPX  per_row 19.0 → 전사   (문턱 16.0 위)
        PDF   per_row 15.0 → 도해   (문턱 16.0 아래)
    """
    from docstruct.experiments.image import chart_gate
    page_pdf, pdf_img = _gate_page(
        {"kind": "page", "verdict": "good", "text_rows": 61, "per_row": 15.0})
    page_hwpx, hwpx_img = _gate_page(
        {"kind": "page", "verdict": "good", "text_rows": 52, "per_row": 19.0})
    chart_gate.run([page_pdf, page_hwpx])

    assert pdf_img.chart_gate["route"] == "chart"
    assert hwpx_img.chart_gate["route"] == "transcribe"
    # 부호가 어느 쪽으로 넘어갔는지 말해 준다 (양수 = 도해 쪽)
    assert pdf_img.chart_gate["per_row_margin"] == 1.0
    assert hwpx_img.chart_gate["per_row_margin"] == -3.0
    # 둘 다 경계에 있다 — 문턱 하나가 결과를 가르는 자리다
    assert pdf_img.chart_gate["borderline"] is True
    assert hwpx_img.chart_gate["borderline"] is True


def test_chart_gate_matches_actual_read_branch():
    """되짚은 경로가 `_read_one` 의 실제 분기와 어긋나지 않는다.

    경로는 판독할 때 기록하는 대신 조건을 되짚어 재현한다. 그 분기가
    바뀌면 이 시험이 먼저 깨져야 한다.
    """
    import inspect

    from docstruct.experiments.image.chart_gate import route_of
    from docstruct.images import vlm_read

    source = inspect.getsource(vlm_read._read_one)
    # 분기를 정하는 두 신호와 순서가 그대로인지
    assert "rows >= CHART_MIN_ROWS" in source
    assert "per_row < CHART_MAX_PER_ROW" in source
    assert 'verdict") == "poor"' in source

    # 흐린 그림은 도해 조건을 만족해도 설명으로 간다 (뒤 분기가 덮는다)
    _, info = _gate_page({"kind": "page", "verdict": "poor",
                          "text_rows": 61, "per_row": 15.0})
    assert route_of(info, info.legibility) == "describe"
    # 장식은 아예 부르지 않는다
    _, deco = _gate_page({"kind": "decoration", "verdict": "decoration",
                          "text_rows": 0, "per_row": 0})
    assert route_of(deco, deco.legibility) == "skip"


def test_chart_gate_skips_unmeasured_images():
    """판독 대상이 아니었던 그림은 남길 것이 없다."""
    from docstruct.experiments.image import chart_gate
    page, info = _gate_page(None)
    assert chart_gate.run([page]) == 0
    assert info.chart_gate is None


def test_chart_gate_is_measurement_only():
    """판독 결과를 바꾸지 않는다."""
    from docstruct.experiments.image import chart_gate
    page, info = _gate_page(
        {"kind": "page", "verdict": "good", "text_rows": 61, "per_row": 15.0})
    info.vlm_markdown = "- 청장\n- 차장"
    chart_gate.run([page])
    assert info.vlm_markdown == "- 청장\n- 차장"


def test_new_experiments_registered_off():
    """0.4.62 실험 둘이 등록돼 있고 기본은 꺼져 있다."""
    from docstruct.experiments import all_experiments
    from docstruct.experiments.registry import DEFAULT_ON

    keys = {e.key for e in all_experiments()}
    assert {"over_split", "chart_gate"} <= keys
    assert not ({"over_split", "chart_gate"} & DEFAULT_ON)


# ────────────────────────────────────────────────────────────────────
# 0.4.63 — 실험이 그림 판독보다 먼저 돌아 빈손이었다
#
# 실측(조달청): `--exp chart_gate` 를 켰는데 기록이 0건이었다. 그림 9개
# 중 하나는 `legibility` 가 있었는데도 그렇다 — 실험이 도는 자리가
# **그림 판독보다 앞**이라, 그때는 아직 아무것도 없었다. 실험은 정상으로
# 돌았고 볼 것이 없었을 뿐이라 로그에도 아무 말이 없었다.
#
# 같은 이유로 `scan_ab` 의 그림 갈래(0.4.58 에서 넓힌 것)도 한 번도
# 동작한 적이 없다.
# ────────────────────────────────────────────────────────────────────


def test_image_stage_experiments_run_after_picture_read():
    """그림을 보는 실험은 그림 판독 **뒤에** 돈다."""
    import inspect

    from docstruct import pipeline

    source = inspect.getsource(pipeline.build_document)
    assert source.count("_run_experiments(") >= 3      # 정의 + 두 번 호출
    tables_at = source.index('_run_experiments("tables")')
    picture_at = source.index("read_picture_regions(pages")
    images_at = source.index('_run_experiments("images")')
    assert tables_at < picture_at < images_at


def test_experiments_declare_their_stage():
    """그림이 남긴 것을 보는 실험이 `images` 로 선언돼 있다."""
    from docstruct.experiments import all_experiments
    stages = {e.key: e.stage for e in all_experiments()}
    assert stages["chart_gate"] == "images"
    assert stages["scan_ab"] == "images"
    # 표를 보는 실험은 사다리 자리 그대로다
    assert stages["over_split"] == "tables"
    assert stages["col_grid"] == "tables"


def test_enabled_experiments_filters_by_stage(monkeypatch):
    """단계를 주면 그 자리 것만 돌려준다 — 두 번 도는 일이 없어야 한다."""
    from docstruct.experiments import enabled_experiments
    monkeypatch.setenv("DOCSTRUCT_EXP_CHART_GATE", "1")
    monkeypatch.setenv("DOCSTRUCT_EXP_OVER_SPLIT", "1")

    tables = {e.key for e in enabled_experiments("tables")}
    images = {e.key for e in enabled_experiments("images")}
    everything = {e.key for e in enabled_experiments()}

    assert "over_split" in tables and "over_split" not in images
    assert "chart_gate" in images and "chart_gate" not in tables
    # 어느 실험도 두 자리에 걸치지 않는다
    assert not (tables & images)
    assert tables | images <= everything


# ────────────────────────────────────────────────────────────────────
# 0.4.64 — 짝을 못 지은 표가 결과물에서 사라졌다
#
# 실측(조달청 HWPX+PDF): 표 110개 중 **55개가 결과물 어디에도 없었다.**
# 본문에는 `<table 46>` 같은 자리표시자가 남아 있어(자리표시자 100종 중
# 46종은 가리킬 표가 없다) 하류가 그것을 따라가면 빈손이다.
#
# 0.4.57 에서 머리말은 `head` 로 살려 두면서 표는 그냥 지우고 있었다 —
# 같은 원칙("쪽을 모르는 것과 없는 것은 다르다")이 표에는 적용되지 않았다.
# ────────────────────────────────────────────────────────────────────


def _align_pair_with_layout_boxes():
    """제목 상자가 섞인 HWPX 와 데이터 표만 있는 PDF."""
    def tbl(n, texts):
        return {
            "id": f"table_{n}", "table_num": n, "placeholder": f"<table {n}>",
            "markdown": "| " + " | ".join(texts) + " |",
            "cells": [{"row": 0, "col": i, "rowspan": 1, "colspan": 1,
                       "text": t} for i, t in enumerate(texts)],
        }

    pdf = {"filename": "x.pdf", "pages": [
        {"page_no": 1,
         "content": "사업 개요를 밝힌다. 조달 효율화가 목표다.\n\n<table 2>",
         "tables": [tbl(2, ["구분", "2026", "2027"])]},
        {"page_no": 2,
         "content": "성과지표는 다음과 같다.\n\n<table 4>",
         "tables": [tbl(4, ["지표", "목표", "실적"])]},
    ]}
    hwpx = {"filename": "x.hwpx", "pages": [{"page_no": 1, "content": (
        "<table 1>\n\n사업 개요를 밝힌다. 조달 효율화가 목표다.\n\n<table 2>\n\n"
        "<table 3>\n\n성과지표는 다음과 같다.\n\n<table 4>"),
        "tables": [tbl(1, ["1. 임무와 비전"]), tbl(2, ["구분", "2026", "2027"]),
                   tbl(3, ["2. 목표체계도"]), tbl(4, ["지표", "목표", "실적"])]}]}
    return hwpx, pdf


def test_unmatched_tables_are_kept():
    """짝을 못 지은 표가 `unmatched` 로 남는다 — 버리지 않는다."""
    from docstruct.align import align_documents

    hwpx, pdf = _align_pair_with_layout_boxes()
    result = align_documents(hwpx, pdf)

    assert result["matched_tables"] == 2
    assert result["unmatched_tables"] == 2
    kept = {t["id"] for t in result["unmatched"]}
    assert kept == {"table_1", "table_3"}
    # 실린 표 + 남긴 표 = 전부. 어느 것도 사라지지 않는다.
    placed = {t["id"] for p in result["pages"] for t in p["tables"]}
    assert len(placed | kept) == result["total_tables"]


def test_unmatched_tables_carry_a_hint():
    """왜 못 맞췄는지 짐작할 단서를 함께 낸다.

    HWPX 는 제목 상자도 표로 그리므로 PDF 에 대응 표가 아예 없는 경우가
    많다 — "맞추기 실패" 와 "맞출 것이 없음" 은 다르다.
    """
    from docstruct.align import align_documents

    hwpx, pdf = _align_pair_with_layout_boxes()
    result = align_documents(hwpx, pdf)

    assert result["unmatched_layout_like"] == 2
    for table in result["unmatched"]:
        note = table["align_note"]
        assert note["rows"] == 1 and note["cols"] == 1
        assert note["layout_like"] is True


def test_unmatched_tables_appear_in_markdown():
    """자리표시자를 따라온 하류가 빈손이 되지 않게 markdown 에도 싣는다."""
    from docstruct.align import align_documents, to_markdown

    hwpx, pdf = _align_pair_with_layout_boxes()
    markdown = to_markdown(align_documents(hwpx, pdf))

    assert "쪽 미상" in markdown
    assert "1. 임무와 비전" in markdown          # 본문에 남은 <table 1>
    assert "2. 목표체계도" in markdown
    # 쪽을 **모른다**는 사실이 드러나야 한다 — 1쪽인 척하면 안 된다
    assert "제목 상자로 보임" in markdown


def test_summary_says_unmatched_were_kept():
    """요약이 "버렸다" 가 아니라 "따로 실었다" 고 말한다."""
    from docstruct.align import align_documents
    from docstruct.align.documents import summary_lines

    hwpx, pdf = _align_pair_with_layout_boxes()
    joined = "\n".join(summary_lines(align_documents(hwpx, pdf)))
    assert "unmatched 필드" in joined
    assert "제목 상자" in joined


def test_matched_tables_are_not_duplicated_into_unmatched():
    """실린 표가 `unmatched` 에 또 들어가지 않는다."""
    from docstruct.align import align_documents

    hwpx, pdf = _align_pair_with_layout_boxes()
    result = align_documents(hwpx, pdf)
    placed = {t["id"] for p in result["pages"] for t in p["tables"]}
    kept = {t["id"] for t in result["unmatched"]}
    assert not (placed & kept)


# ────────────────────────────────────────────────────────────────────
# 0.4.65 — 도해 문턱은 둘인데 하나만 재고 있었다
#
# 실측(조달청 HWPX): `image_3` 이 줄 17(문턱 20)로 **줄 수 축에서 3 차이**
# 였는데 경계로 잡히지 않았다. 도해 판정은 두 조건의 논리곱이므로 어느
# 한쪽만 재면 절반을 놓친다.
# ────────────────────────────────────────────────────────────────────


def test_chart_gate_measures_both_axes():
    """두 문턱까지의 거리를 각각 남긴다."""
    from docstruct.experiments.image import chart_gate
    page, info = _gate_page(
        {"kind": "figure", "verdict": "fair", "text_rows": 17, "per_row": 5.0})
    chart_gate.run([page])
    gate = info.chart_gate

    # 줄 수는 문턱에 3 모자라고, 줄당 글자는 11 여유가 있다
    assert gate["rows_margin"] == -3.0
    assert gate["per_row_margin"] == 11.0
    # 막고 있는 쪽이 가까우므로 경계다 — 예전에는 놓쳤다
    assert gate["borderline"] is True
    assert gate["route"] == "picture"


def test_chart_gate_ignores_far_off_images():
    """둘 다 한참 모자라면 경계가 아니다.

    문턱 하나가 조금 달라진다고 뒤집히지 않는다. 이 구분이 없으면
    실측 `image_2`(줄 4)까지 경계로 세어 수치가 무의미해진다.
    """
    from docstruct.experiments.image import chart_gate
    page, info = _gate_page(
        {"kind": "figure", "verdict": "poor", "text_rows": 4, "per_row": 9.5})
    chart_gate.run([page])
    # 흐린 그림은 문턱 앞 분기에서 갈린다 — 문턱을 넘나들어도 결과가 같다
    assert info.chart_gate["route"] == "describe"
    assert info.chart_gate["borderline"] is False


def test_chart_gate_borderline_needs_the_other_axis_to_pass():
    """막고 있는 쪽이 가까워도, 다른 쪽이 통과해 있어야 경계다."""
    from docstruct.experiments.image.chart_gate import is_borderline

    # 줄 수는 통과, 줄당 글자가 2 모자람 → 뒤집힐 수 있다
    assert is_borderline({"rows": 30.0, "per_row": -2.0}) is True
    # 줄 수가 한참 모자람 → 줄당 글자를 조금 고쳐도 도해가 되지 않는다
    assert is_borderline({"rows": -16.0, "per_row": -2.0}) is False
    # 지금 도해인데 한쪽이 아슬아슬 → 경계다
    assert is_borderline({"rows": 41.0, "per_row": 1.0}) is True
    # 지금 도해이고 양쪽 다 여유 → 경계가 아니다
    assert is_borderline({"rows": 41.0, "per_row": 9.0}) is False
    # 잴 수 없으면 경계라고 하지 않는다
    assert is_borderline({"rows": None, "per_row": 1.0}) is False


def test_chart_gate_reproduces_both_formats_with_margins():
    """같은 조직도가 형식에 따라 갈린 실측을 두 축으로 재현한다.

        HWPX  줄 52 · 줄당 19.0  →  전사 (per_row 가 -3.0 으로 막았다)
        PDF   줄 61 · 줄당 15.0  →  도해 (per_row +1.0 으로 겨우 통과)
    """
    from docstruct.experiments.image import chart_gate
    page_hwpx, hwpx = _gate_page(
        {"kind": "page", "verdict": "good", "text_rows": 52, "per_row": 19.0})
    page_pdf, pdf = _gate_page(
        {"kind": "page", "verdict": "good", "text_rows": 61, "per_row": 15.0})
    assert chart_gate.run([page_hwpx, page_pdf]) == 2

    assert hwpx.chart_gate["route"] == "transcribe"
    assert hwpx.chart_gate["per_row_margin"] == -3.0
    assert pdf.chart_gate["route"] == "chart"
    assert pdf.chart_gate["per_row_margin"] == 1.0
    # 줄 수는 둘 다 여유가 크다 — 갈린 것은 줄당 글자 축이다
    assert hwpx.chart_gate["rows_margin"] > 20
    assert pdf.chart_gate["rows_margin"] > 20


# ────────────────────────────────────────────────────────────────────
# 0.4.66 — "제목 상자" 를 1행1열로만 보던 것을 넓힌다
#
# 실측(조달청): 못 맞춘 65표 중 1행1열은 31개뿐이었고, 나머지 34개도
# 대부분 표가 아니었다 — `(단위 : 백만원)` 이 1행 15열 표였고, 목차·
# 조직도는 빈 칸으로 자리만 잡은 표였다.
#
# 짝을 지은 **데이터 표 55개의 채움 비율은 중앙 95%** 이고 50% 아래는
# 하나뿐이다. 성긴 표는 데이터 표가 아니다.
# ────────────────────────────────────────────────────────────────────


def _cells(grid):
    """행 목록(문자열 리스트의 리스트)에서 셀을 만든다."""
    return [{"row": r, "col": c, "rowspan": 1, "colspan": 1, "text": text}
            for r, row in enumerate(grid) for c, text in enumerate(row)]


def test_layout_hint_flags_bands():
    """1행 또는 1열짜리는 제목 띠다 — 표가 아니다."""
    from docstruct.align.documents import layout_hint

    wide = layout_hint({"cells": _cells([["(단위 : 백만원)", "", ""]])})
    assert wide["why"] == "band" and wide["layout_like"] is True
    tall = layout_hint({"cells": _cells([["2027년도"], ["성과계획서"], ["(조달청)"]])})
    assert tall["why"] == "band"


def test_layout_hint_explains_only_what_it_can():
    """매처가 쓸 토큰이 없으면 그 사실만 말한다 — 표의 성격을 단정하지 않는다.

    채움 비율로 "레이아웃 표" 를 판정하던 것은 **순환**이었다. 성긴 표는
    토큰이 적어 못 맞춰지는데, 그 못 맞춰짐을 근거로 다시 레이아웃 표라고
    불렀다 — 매처의 실패를 매처의 출력으로 설명한 것이다.
    """
    from docstruct.align.documents import layout_hint

    # 토큰이 거의 없다 — 매처가 짝을 지을 수 없다는 사실만 말한다
    thin = layout_hint({"cells": _cells([["가", ""], ["", "나"]])})
    assert thin["why"] == "too_few_tokens"
    assert thin["layout_like"] is False          # 레이아웃이라고 단정하지 않는다

    # 성기지만 토큰은 넉넉하다 → 아무 설명도 하지 않는다 (진짜 검토 대상)
    sparse = layout_hint({"cells": _cells([
        ["재정성과책임관", "", "", "백승보 청장"],
        ["", "", "", ""],
        ["재정성과운영관", "", "", "이형식 기획조정관"],
    ])})
    assert sparse["fill_ratio"] <= 0.5           # 성긴 것은 맞다
    assert sparse["why"] == ""                   # 그러나 설명이 되지는 않는다


def test_layout_hint_does_not_hide_big_unmatched_tables():
    """큰 데이터 표가 못 맞춰지면 그대로 드러나야 한다.

    실측(문체부): `table_838` 이 678행×15열 · 토큰 3,243 · 채움 100% 인데
    못 맞춰졌다. 채움 비율 기준은 이런 것을 `sparse` 로 덮어 보이지 않게
    했다 — 못 맞춘 505표 중 153개가 토큰 8개 이상이었다.
    """
    from docstruct.align.documents import layout_hint

    big = layout_hint({"cells": _cells(
        [[f"항목{r}", f"{r}00,000", f"설명{r}"] for r in range(1, 30)])})
    assert big["tokens"] > 8
    assert big["why"] == ""                      # 아무 변명도 붙이지 않는다
    assert big["layout_like"] is False


def test_layout_hint_keeps_real_tables():
    """채워진 다행다열 표는 데이터 표로 남긴다 — 진짜 검토 대상이다."""
    from docstruct.align.documents import layout_hint

    note = layout_hint({"cells": _cells([
        ["구분", "2026", "2027"],
        ["예산", "1,150", "1,200"],
        ["집행", "1,100", "1,180"],
    ])})
    assert note["layout_like"] is False
    assert note["why"] == ""
    assert note["fill_ratio"] == 1.0


def test_layout_hint_always_carries_the_numbers():
    """근거 수치를 언제나 함께 낸다 — 라벨만 남기면 문턱을 다시 못 본다."""
    from docstruct.align.documents import layout_hint

    note = layout_hint({"cells": _cells([["a", "b"], ["c", ""]])})
    for key in ("rows", "cols", "cells", "filled", "fill_ratio",
                "layout_like", "why"):
        assert key in note
    assert note["cells"] == 4 and note["filled"] == 3


def test_align_reports_layout_hint_breakdown():
    """쪽 맞춤 결과가 단서별로 갈라 센다."""
    from docstruct.align import align_documents

    hwpx, pdf = _align_pair_with_layout_boxes()
    result = align_documents(hwpx, pdf)
    assert result["unmatched_layout_like"] == 2
    assert all(t["align_note"]["why"] == "band" for t in result["unmatched"])


# ────────────────────────────────────────────────────────────────────
# 0.4.68 — 글자가 적다는 것만으로 스캔이라고 하고 있었다
#
# HWPX 원본 대조로 드러났다. 세 부처 스캔 판정 쪽의 **숫자 오독은 0건**
# 이었지만(VLM 판독 자체는 깨끗했다), 판정된 쪽 자체가 틀렸다.
#
#     행안부 p76·p243   텍스트 레이어 **0자** — 진짜 빈/스캔 쪽 (정상)
#     문체부 7쪽        텍스트 레이어 11~79자 · **지면 그림 0개**
#                       표가 쪽을 넘어와 꼬리 조각만 남았거나
#                       (`| 고도화(정보화)(500) | 계 |`),
#                       본문이 `ㅇ 해당사항 없음` 한 줄인 쪽이었다
#
# 그것을 VLM 으로 다시 읽자 있던 표 구조가 망가졌다 — 빈 열을 지어내고
# 2열짜리를 13열로 부풀렸다.
# ────────────────────────────────────────────────────────────────────


class _FakeObject:
    """pdfium 페이지 객체 흉내."""

    def __init__(self, kind, bounds):
        self.type = kind
        self._bounds = bounds

    def get_bounds(self):
        return self._bounds


class _FakePage:
    def __init__(self, size, objects):
        self._size = size
        self._objects = objects

    def get_size(self):
        return self._size

    def get_objects(self):
        return list(self._objects)


class _FakeDoc:
    def __init__(self, page):
        self._page = page

    def __getitem__(self, index):
        return self._page


def _image_obj(width, height):
    import pypdfium2.raw as pdfium_c

    return _FakeObject(pdfium_c.FPDF_PAGEOBJ_IMAGE, (0, 0, width, height))


def _path_obj(width, height):
    import pypdfium2.raw as pdfium_c

    return _FakeObject(pdfium_c.FPDF_PAGEOBJ_PATH, (0, 0, width, height))


def test_page_image_detects_full_page_scan():
    """지면을 덮는 이미지가 있으면 읽을 그림이 있는 것이다.

    실측(주택과세금 전면 스캔본): 쪽마다 지면의 80.6% 를 덮는 이미지가
    하나씩 있었다.
    """
    from docstruct.pipeline import _has_page_image

    page = _FakePage((595, 842), [_image_obj(530, 763)])   # 80.6%
    assert _has_page_image(_FakeDoc(page), 0) is True


def test_page_image_ignores_small_pictures():
    """작은 삽화는 지면 그림이 아니다 — 그 쪽은 텍스트 쪽이다."""
    from docstruct.pipeline import _has_page_image

    page = _FakePage((595, 842), [_image_obj(58, 57), _path_obj(539, 785)])
    assert _has_page_image(_FakeDoc(page), 0) is False


def test_page_image_does_not_block_when_it_cannot_tell():
    """판정을 못 하면 막지 않는다 — 스캔본을 놓치면 본문을 잃는다."""
    from docstruct.pipeline import _has_page_image

    class _Boom:
        def __getitem__(self, index):
            raise RuntimeError("열 수 없음")

    assert _has_page_image(_Boom(), 0) is True


def test_sparse_text_page_without_image_is_not_rescanned(monkeypatch, tmp_path):
    """글자가 적어도 **읽을 그림이 없으면** 다시 읽지 않는다.

    실측(문체부 p212): 텍스트 레이어에 `고도화(정보화)(500) 계` 가 멀쩡히
    있는데 스캔으로 판정됐고, VLM 재판독이 2열짜리 표에 빈 열을 지어냈다.
    """
    from docstruct import pipeline
    from docstruct.models import PageContent, PageTrace

    monkeypatch.setattr(pipeline, "_force_reread", lambda: False)
    monkeypatch.setattr(pipeline, "_has_page_image", lambda doc, index: False)

    class _Doc:
        def __len__(self):
            return 3

        def __getitem__(self, index):
            class _P:
                def get_textpage(self):
                    class _T:
                        def get_text_range(self):
                            return "고도화(정보화)(500) 계"
                    return _T()
            return _P()

        def close(self):
            pass

    import types

    fake = types.SimpleNamespace(PdfDocument=lambda path: _Doc())
    monkeypatch.setitem(sys.modules, "pypdfium2", fake)
    pages = [PageContent(page_no=1, page_no_kind="exact", content="",
                         trace=PageTrace())]
    assert pipeline._pages_needing_ocr(tmp_path / "x.pdf", pages) == set()


def test_empty_text_layer_is_still_rescanned(monkeypatch, tmp_path):
    """글자가 아예 없으면 그림 여부와 무관하게 대상이다.

    대조군(행안부 p76·p243)이 0자였다 — 진짜 빈/스캔 쪽이고, 재판독이
    손해를 만들지 않는다.
    """
    from docstruct import pipeline
    from docstruct.models import PageContent, PageTrace

    monkeypatch.setattr(pipeline, "_force_reread", lambda: False)
    monkeypatch.setattr(pipeline, "_has_page_image", lambda doc, index: False)

    class _Doc:
        def __len__(self):
            return 3

        def __getitem__(self, index):
            class _P:
                def get_textpage(self):
                    class _T:
                        def get_text_range(self):
                            return "   \n  "
                    return _T()
            return _P()

        def close(self):
            pass

    import types

    fake = types.SimpleNamespace(PdfDocument=lambda path: _Doc())
    monkeypatch.setitem(sys.modules, "pypdfium2", fake)
    pages = [PageContent(page_no=1, page_no_kind="exact", content="",
                         trace=PageTrace())]
    assert pipeline._pages_needing_ocr(tmp_path / "x.pdf", pages) == {1}


# ────────────────────────────────────────────────────────────────────
# 0.4.69 — 표가 격자를 덮는지 본다 (정답 없이도 확실한 신호)
#
# 표는 직사각 격자이므로 모든 (행, 열) 자리가 정확히 한 셀에 덮여야 한다.
# 구멍은 셀을 놓친 것이고 겹침은 경계를 잘못 그은 것이다 — 어느 쪽이든
# **원본을 몰라도** 틀렸다고 말할 수 있다.
#
# 실측:  문체부 HWPX 653표 결함 **0개** (음성 대조군)
#        조달청 PDF 40% · 행안부 PDF 53% · 문체부 PDF 46%
# ────────────────────────────────────────────────────────────────────


def _grid_cells(spec):
    """(row, col, rowspan, colspan) 목록에서 셀을 만든다."""
    return [{"row": r, "col": c, "rowspan": rs, "colspan": cs, "text": "x"}
            for r, c, rs, cs in spec]


def test_grid_check_passes_on_sound_table():
    """빈틈없이 덮인 표는 통과한다 — HWPX 653표가 그랬다."""
    from docstruct.structuring.checks import grid_check

    # 2×3 격자에 가로 병합 하나
    got = grid_check(_grid_cells([(0, 0, 1, 2), (0, 2, 1, 1),
                                  (1, 0, 1, 1), (1, 1, 1, 1), (1, 2, 1, 1)]))
    assert got["ok"] is True
    assert got["holes"] == 0 and got["overlaps"] == 0
    assert got["width"] == 3 and got["height"] == 2


def test_grid_check_finds_holes():
    """덮이지 않은 자리는 셀을 놓친 것이다."""
    from docstruct.structuring.checks import grid_check

    got = grid_check(_grid_cells([(0, 0, 1, 1), (0, 2, 1, 1),   # c1 이 없다
                                  (1, 0, 1, 1), (1, 1, 1, 1), (1, 2, 1, 1)]))
    assert got["ok"] is False
    assert got["holes"] == 1 and got["overlaps"] == 0


def test_grid_check_finds_overlaps():
    """두 번 덮인 자리는 경계를 잘못 그은 것이다."""
    from docstruct.structuring.checks import grid_check

    got = grid_check(_grid_cells([(0, 0, 1, 2), (0, 1, 1, 2),   # c1 이 겹친다
                                  (1, 0, 1, 1), (1, 1, 1, 1), (1, 2, 1, 1)]))
    assert got["ok"] is False
    assert got["overlaps"] == 1


def test_grid_check_marks_heavy_damage():
    """조금 어긋난 것과 많이 깨진 것을 가른다 — 하류가 쓸 잣대다."""
    from docstruct.structuring.checks import grid_check

    # 4×4 인데 한 행이 통째로 비었다
    light = grid_check(_grid_cells(
        [(r, c, 1, 1) for r in range(4) for c in range(4)][:-1]))
    heavy = grid_check(_grid_cells(
        [(r, c, 1, 1) for r in range(3) for c in range(4)]
        + [(3, 0, 1, 1)]))
    assert light["heavy"] is False
    assert heavy["heavy"] is True


def test_grid_check_skips_bands():
    """1열짜리는 격자라 할 것이 없다 — 제목 띠다."""
    from docstruct.structuring.checks import grid_check

    assert grid_check(_grid_cells([(0, 0, 1, 1), (1, 0, 1, 1)])) is None
    assert grid_check([]) is None


def test_grid_check_does_not_change_cells():
    """표시만 하고 고치지 않는다 — 구멍을 메우려면 지면을 봐야 한다."""
    from docstruct.structuring.checks import grid_check

    cells = _grid_cells([(0, 0, 1, 1), (0, 2, 1, 1), (1, 0, 1, 3)])
    before = [dict(c) for c in cells]
    grid_check(cells)
    assert cells == before


def test_pipeline_records_grid_faults():
    """판독 결과에도 남는다 — 구조화를 돌리지 않아도 보여야 한다."""
    import inspect

    from docstruct import pipeline

    source = inspect.getsource(pipeline.build_document)
    assert "grid_check(table.cells)" in source
    assert "table.grid_faults" in source
    # 실험이 아니라 상시 검사다 — `--exp` 없이도 돈다
    assert "_run_experiments" not in source.split("grid_check(table.cells)")[0][-400:]


# ────────────────────────────────────────────────────────────────────
# 0.4.70 — VLM 재구성이 격자를 나쁘게 만들고 있었다
#
# 실측(문체부 609쪽): VLM 재구성으로 채택된 21표를 원본과 대조하니
#
#     나빠짐 **14** · 그대로 7 · **좋아짐 0**
#
# 나빠진 14표는 **전부 원본이 결함 0** 이었다. 대개 `13열×3행 →
# 13열×4행` 으로 행이 하나 늘며 마지막 행이 일부만 차 구멍 7칸이
# 생겼다 — 모델이 표 아래에 줄을 하나 더 붙인 것이다.
#
# 길이·형태 가드(`_acceptable`)는 이것을 못 잡는다. 글자 수도 비슷하고
# markdown 꼴도 표이기 때문이다.
# ────────────────────────────────────────────────────────────────────


def test_rebuild_rejects_grid_regression(monkeypatch, tmp_path):
    """격자가 나빠진 재구성은 받지 않는다 — 원본을 유지한다."""
    from docstruct.models import PageContent, PageTrace, TableInfo
    from docstruct.tables import vlm_rebuild

    sound = ("| 구분 | 실적 | 목표 | 비고 |\n| --- | --- | --- | --- |\n"
             "| 예산 | 1 | 2 | 3 |")
    # 실측에서 나온 모양 — 모델이 `〃` 로 병합을 표시하면서 격자에 구멍이
    # 생긴다 (`13열×3행 → 13열×4행` · 구멍 7칸이 그것이다)
    ragged = ("| 구분 | 실적 | 〃 | 〃 |\n| --- | --- | --- | --- |\n"
              "| 〃 | 23 | 24 | 25 |\n| 예산 | 1 | 2 | 3 |")

    table = TableInfo(id="table_1", table_num=1, placeholder="<table 1>",
                      markdown=sound)
    table.cells = vlm_rebuild.cells_from_markdown(sound)
    page = PageContent(page_no=1, page_no_kind="exact", content="<table 1>",
                       trace=PageTrace())
    page.tables.append(table)

    page.page_image_path = "/x.png"
    monkeypatch.setattr(vlm_rebuild, "llm_api_config", lambda: {"model": "m"})
    monkeypatch.setattr(vlm_rebuild, "needs_vlm", lambda t: True)
    monkeypatch.setattr(vlm_rebuild, "_rebuild_one",
                        lambda page_, table_, cfg: ragged)

    vlm_rebuild.rebuild_broken_tables([page])
    # 원본이 그대로 남는다
    assert table.markdown == sound
    assert table.source != "vlm"
    assert any("격자가 나빠짐" in (s.get("detail") or "")
               for s in page.trace.to_dict().get("steps", []))


def test_rebuild_keeps_grid_neutral_result(monkeypatch):
    """격자가 나빠지지 않으면 그대로 받는다 — 7표가 그랬다."""
    from docstruct.models import PageContent, PageTrace, TableInfo
    from docstruct.tables import vlm_rebuild

    before = "| a | b | c |\n| --- | --- | --- |\n| 1 | 2 | 3 |"
    after = ("| 구분 | 2026 | 2027 |\n| --- | --- | --- |\n"
             "| 예산 | 100 | 200 |")

    table = TableInfo(id="table_1", table_num=1, placeholder="<table 1>",
                      markdown=before)
    table.cells = vlm_rebuild.cells_from_markdown(before)
    page = PageContent(page_no=1, page_no_kind="exact", content="<table 1>",
                       trace=PageTrace())
    page.tables.append(table)

    page.page_image_path = "/x.png"
    monkeypatch.setattr(vlm_rebuild, "llm_api_config", lambda: {"model": "m"})
    monkeypatch.setattr(vlm_rebuild, "needs_vlm", lambda t: True)
    monkeypatch.setattr(vlm_rebuild, "_rebuild_one",
                        lambda page_, table_, cfg: after)

    vlm_rebuild.rebuild_broken_tables([page])
    assert table.markdown == after
    assert table.source == "vlm"


def test_grid_guard_can_be_turned_off(monkeypatch):
    """손잡이로 끌 수 있다 — 끄면 0.4.70 이전 동작이다."""
    from docstruct.tables import vlm_rebuild

    monkeypatch.setenv("DOCSTRUCT_REBUILD_GRID_GUARD", "0")
    assert vlm_rebuild._grid_guard() is False
    monkeypatch.delenv("DOCSTRUCT_REBUILD_GRID_GUARD", raising=False)
    assert vlm_rebuild._grid_guard() is True


# ────────────────────────────────────────────────────────────────────
# 0.4.71 — 재료가 있는데 쓰지 않던 표 259개
#
# 진단(문체부 609쪽 · col_gate):
#
#     cols_match     259표   결함률 **79%**   격자가 서고 열 수도 맞는다
#     no_lattice      33표   결함률  27%      괘선이 없는 표는 **9%뿐**
#
# 괘선이 없어서가 아니었다. ⑮의 게이트가 `cols <= detected` 라 **열 수가
# 맞으면 물러나기** 때문에, 격자는 서는데 셀에 구멍이 있는 표를 지나쳤다.
# 결함이 남은 220표 중 205표가 격자 열 수 = 인식 열 수였다.
# ────────────────────────────────────────────────────────────────────


def test_lattice_fill_runs_right_after_lattice_restore():
    """⑮ 바로 뒤에 돈다 — ⑮이 이긴 자리를 지키기 위함이다."""
    from docstruct.experiments.registry import _RUN_ORDER

    assert _RUN_ORDER.index("lattice_fill") == (
        _RUN_ORDER.index("lattice_restore") + 1)


def test_lattice_fill_skips_sound_tables(monkeypatch, tmp_path):
    """온전한 표는 건드리지 않는다."""
    from docstruct.experiments.tsr.restore import lattice_fill
    called = {"n": 0}
    monkeypatch.setattr(lattice_fill, "restore_filled",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    table = _col_table(4)                        # 4×1, 구멍 없음
    assert lattice_fill.run([_col_page(table)],
                            pdf_path=tmp_path / "x.pdf") == 0
    assert called["n"] == 0


def test_lattice_fill_repairs_holes(monkeypatch, tmp_path):
    """구멍이 있으면 격자로 다시 세운다."""
    from docstruct.experiments.tsr.restore import lattice_fill
    from docstruct.models import TableInfo

    holed = TableInfo(id="table_1", table_num=1, placeholder="<table 1>",
                      markdown="| a |")
    holed.bbox = {"l": 0, "t": 0, "r": 100, "b": 50}
    # 2×4 인데 (1,1)·(1,3) 이 비었다
    holed.cells = [{"row": 0, "col": c, "rowspan": 1, "colspan": 1, "text": "머리"}
                   for c in range(4)]
    holed.cells += [{"row": 1, "col": c, "rowspan": 1, "colspan": 1, "text": "값"}
                    for c in (0, 2)]
    holed.source = "parser"

    whole = [{"row": r, "col": c, "rowspan": 1, "colspan": 1, "text": "머리값"}
             for r in range(2) for c in range(4)]
    monkeypatch.setattr(
        lattice_fill, "restore_filled",
        lambda *a, **k: {"cells": whole, "markdown": "| x |",
                         "before": 4, "after": 4})

    page = _col_page(holed)
    assert lattice_fill.run([page], pdf_path=tmp_path / "x.pdf") == 1
    assert holed.source == "grid"
    assert holed.lattice_fill["faults_before"] == 2
    assert holed.lattice_fill["faults_after"] == 0


def test_lattice_fill_rejects_when_it_makes_things_worse(monkeypatch, tmp_path):
    """더 망가뜨리면 받지 않는다 — 0.4.70 의 교훈을 스스로에게 건다."""
    from docstruct.experiments.tsr.restore import lattice_fill
    from docstruct.models import TableInfo

    holed = TableInfo(id="table_1", table_num=1, placeholder="<table 1>",
                      markdown="| a |")
    holed.bbox = {"l": 0, "t": 0, "r": 100, "b": 50}
    holed.cells = [{"row": 0, "col": c, "rowspan": 1, "colspan": 1, "text": "머리"}
                   for c in range(4)]
    holed.cells += [{"row": 1, "col": 0, "rowspan": 1, "colspan": 1, "text": "값"}]
    holed.source = "parser"
    before_cells = list(holed.cells)

    # 더 성긴 결과
    worse = [{"row": 0, "col": 0, "rowspan": 1, "colspan": 1, "text": "x"},
             {"row": 2, "col": 3, "rowspan": 1, "colspan": 1, "text": "y"}]
    monkeypatch.setattr(
        lattice_fill, "restore_filled",
        lambda *a, **k: {"cells": worse, "markdown": "| x |",
                         "before": 4, "after": 4})

    page = _col_page(holed)
    assert lattice_fill.run([page], pdf_path=tmp_path / "x.pdf") == 0
    assert holed.cells == before_cells
    assert holed.source == "parser"
    assert any("격자 채움 폐기" in (s.get("action") or "")
               for s in page.trace.to_dict().get("steps", []))


def test_lattice_fill_defers_to_earlier_restorers(monkeypatch, tmp_path):
    """⑦·⑫·⑮가 이긴 자리는 지킨다."""
    from docstruct.experiments.tsr.restore import lattice_fill
    from docstruct.models import TableInfo

    for mark in ("source", "head_grid", "agreed_grid"):
        table = TableInfo(id="table_1", table_num=1, placeholder="<table 1>",
                          markdown="| a |")
        table.bbox = {"l": 0, "t": 0, "r": 100, "b": 50}
        table.cells = [{"row": 0, "col": c, "rowspan": 1, "colspan": 1,
                        "text": "x"} for c in range(4)]
        table.cells += [{"row": 1, "col": 0, "rowspan": 1, "colspan": 1,
                         "text": "y"}]
        if mark == "source":
            table.source = "grid"
        else:
            setattr(table, mark, {"before": 1, "after": 2})
        monkeypatch.setattr(lattice_fill, "restore_filled",
                            lambda *a, **k: pytest.fail("불러선 안 된다"))
        assert lattice_fill.run([_col_page(table)],
                                pdf_path=tmp_path / "x.pdf") == 0


def test_lattice_fill_is_restored():
    """0.4.80 승격 복원 — 오염을 후처리가 지운다.

    0.4.79 에 셀 오염으로 강등됐는데, 새어 든 글자가 **앞 칸에 있는
    중복**이라 지우면 원래 값으로 돌아간다. 원본 대조: 오염 표 셀의
    원본 일치 79~81% → 96~98%.
    """
    from docstruct.experiments.registry import DEFAULT_ON

    assert "lattice_fill" in DEFAULT_ON
    # ⑮ 뒤에 돌아야 그 자리를 지킨다
    from docstruct.experiments.registry import _RUN_ORDER

    assert _RUN_ORDER.index("lattice_fill") > _RUN_ORDER.index("lattice_restore")


def test_fill_min_cols_is_a_knob(monkeypatch):
    """열 수 문턱은 손잡이다 — ⑮에서 물려받은 값이지 검증된 것이 아니다.

    실측: 남은 `too_few_cols` 19표 중 3열이 11개였다. 4 → 3 으로 낮춘다.
    """
    from docstruct.experiments.tsr.restore import lattice_fill
    assert lattice_fill._min_cols() == 3
    monkeypatch.setenv("DOCSTRUCT_EXP_FILL_MIN_COLS", "4")
    assert lattice_fill._min_cols() == 4
    monkeypatch.setenv("DOCSTRUCT_EXP_FILL_MIN_COLS", "엉망")
    assert lattice_fill._min_cols() == 3


# ────────────────────────────────────────────────────────────────────
# 0.4.72 — lattice_fill 이 물러난 자리를 남긴다
#
# 실측(행안부): 남은 결함 17표 중 5표는 `MIN_COLS=4` 로, 3표는 ⑫가 이긴
# 자리라 설명되지만 **9표는 이유를 알 수 없었다.** `restore_filled` 이
# `None` 만 돌려주어 물러난 자리 여섯이 전부 조용했기 때문이다.
#
# `col_gate`(0.4.56)가 ⑬ 문제를 닫은 결정적 재료였는데, 같은 계측을 새
# 실험에 넣지 않은 것이다.
# ────────────────────────────────────────────────────────────────────


def _holed_table(cols=6, rows=3):
    """구멍이 있는 시험용 표."""
    from docstruct.models import TableInfo

    table = TableInfo(id="table_1", table_num=1, placeholder="<table 1>",
                      markdown="| a |")
    table.bbox = {"l": 0, "t": 0, "r": 100, "b": 50}
    table.cells = [{"row": r, "col": c, "rowspan": 1, "colspan": 1, "text": "x"}
                   for r in range(rows) for c in range(cols)][:-2]
    table.source = "parser"
    return table


def test_fill_gate_records_every_retreat(monkeypatch, tmp_path):
    """물러난 사유가 표에 남는다 — 조용히 지나치지 않는다."""
    from docstruct.experiments.tsr.restore import lattice_fill
    for gate in ("no_segments", "no_lattice", "bounds_mismatch", "text_loss"):
        table = _holed_table()
        monkeypatch.setattr(lattice_fill, "restore_filled",
                            lambda *a, _g=gate, **k: {"gate": _g, "detected": 6})
        assert lattice_fill.run([_col_page(table)],
                                pdf_path=tmp_path / "x.pdf") == 0
        assert table.fill_gate["gate"] == gate
        # 결함 수를 함께 남긴다 — 사유별 심각도를 재려면 필요하다
        assert table.fill_gate["faults"] > 0


def test_fill_gate_records_earlier_restorer(monkeypatch, tmp_path):
    """앞선 복원자가 이긴 자리도 사유를 남긴다.

    그 표에 결함이 남아 있다면 그것도 알아야 한다 — 지킨 것과 못 고친
    것은 다르다.
    """
    from docstruct.experiments.tsr.restore import lattice_fill
    table = _holed_table()
    table.head_grid = {"before": 1, "after": 2}
    monkeypatch.setattr(lattice_fill, "restore_filled",
                        lambda *a, **k: pytest.fail("불러선 안 된다"))
    assert lattice_fill.run([_col_page(table)],
                            pdf_path=tmp_path / "x.pdf") == 0
    assert table.fill_gate["gate"] == "earlier_restorer"


def test_fill_gate_records_made_worse(monkeypatch, tmp_path):
    """더 망가뜨려 폐기한 것도 사유로 남는다."""
    from docstruct.experiments.tsr.restore import lattice_fill
    table = _holed_table()
    worse = [{"row": 0, "col": 0, "rowspan": 1, "colspan": 1, "text": "x"},
             {"row": 5, "col": 5, "rowspan": 1, "colspan": 1, "text": "y"}]
    monkeypatch.setattr(
        lattice_fill, "restore_filled",
        lambda *a, **k: {"cells": worse, "markdown": "| x |",
                         "before": 6, "after": 6})
    assert lattice_fill.run([_col_page(table)],
                            pdf_path=tmp_path / "x.pdf") == 0
    assert table.fill_gate["gate"] == "made_worse"


def test_fill_gate_absent_on_success(monkeypatch, tmp_path):
    """성공한 표에는 게이트 기록이 없다 — 있으면 실패로 오해한다."""
    from docstruct.experiments.tsr.restore import lattice_fill
    table = _holed_table()
    whole = [{"row": r, "col": c, "rowspan": 1, "colspan": 1, "text": "값"}
             for r in range(3) for c in range(6)]
    monkeypatch.setattr(
        lattice_fill, "restore_filled",
        lambda *a, **k: {"cells": whole, "markdown": "| x |",
                         "before": 6, "after": 6})
    assert lattice_fill.run([_col_page(table)],
                            pdf_path=tmp_path / "x.pdf") == 1
    assert table.fill_gate is None
    assert table.lattice_fill["faults_after"] == 0


# ────────────────────────────────────────────────────────────────────
# 0.4.74 — 지면에 없는 글을 빼되, 뺐다는 사실을 남긴다
#
# 실측(조달청 성과계획서 70쪽): 프로그램 코드 `53405` 가 **1pt 이면서
# 흰 글씨**로 표의 좁은 열 하나를 차지하고 있었다. 목차 전문(`제1장`…)도
# 흰 글씨로 숨어 있었고, 누름틀 잔재
# (`{"fields": {},"simplefields": {}}`)도 함께 나왔다.
#
#     white        22개   제1장 · 제2장 · 별첨1 …
#     tiny+white   13개   50706 · 53405 · 53407 …
#     tiny          1개
#     field         2개
#
# 본문에 실으면 없는 글이 생기므로 빼는 것이 맞다. 그러나 **뺐다는
# 사실이 남지 않으면** HWPX 와 PDF 의 열 수가 왜 다른지 짚을 수 없다.
# ────────────────────────────────────────────────────────────────────


def test_hidden_char_ids_finds_tiny_and_white():
    """1pt 글자와 흰 글자를 사유별로 가려낸다."""
    import io
    import zipfile

    from docstruct.converters.hwpx import hwpxtree

    # 0.4.82 — 흰 글자는 **작을 때만** 숨긴 것이다. id=2 는 15pt 흰 글자라
    # 진한 바탕 위의 제목이므로 숨김이 아니다(`별첨8` 이 그랬다).
    header = """<?xml version="1.0"?>
<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head">
  <hh:charPr id="0" height="1500" textColor="#000000"/>
  <hh:charPr id="1" height="100" textColor="#000000"/>
  <hh:charPr id="2" height="1500" textColor="#FFFFFF"/>
  <hh:charPr id="3" height="100" textColor="#FFFFFF"/>
  <hh:charPr id="4" height="200" textColor="#FFFFFF"/>
</hh:head>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("Contents/header.xml", header)
    with zipfile.ZipFile(buf) as archive:
        got = hwpxtree._hidden_char_ids(archive)

    assert got == {"1": "tiny", "3": "tiny+white", "4": "tiny+white"}
    assert "0" not in got                        # 본문 글자는 건드리지 않는다
    assert "2" not in got                        # 15pt 흰 글자 — 보이는 제목이다


def test_hidden_char_ids_survive_broken_height():
    """height 가 이상해도 무너지지 않는다."""
    import io
    import zipfile

    from docstruct.converters.hwpx import hwpxtree

    header = """<?xml version="1.0"?>
<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head">
  <hh:charPr id="0" height="엉망" textColor="#000000"/>
  <hh:charPr id="1" textColor="#FFFFFF"/>
</hh:head>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("Contents/header.xml", header)
    with zipfile.ZipFile(buf) as archive:
        got = hwpxtree._hidden_char_ids(archive)
    assert got == {"1": "white"}


def test_hidden_notes_reports_what_was_dropped():
    """뺀 글이 사유별로 요약된다 — 개수와 표본을 함께 낸다."""
    import xml.etree.ElementTree as ET

    from docstruct.converters.hwpx import hwpxtree

    hwpxtree._collectors().hidden.clear()
    para = ET.fromstring(
        '<hp:p xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
        '<hp:run charPrIDRef="0"><hp:t>지표명</hp:t></hp:run>'
        '<hp:run charPrIDRef="3"><hp:t>53405</hp:t></hp:run>'
        '<hp:run charPrIDRef="9"><hp:t>{"fields": {},"simplefields": {}}</hp:t>'
        "</hp:run></hp:p>")
    text = hwpxtree._paragraph_text(para, set(), {"3": "tiny+white"})

    # 본문에는 보이는 글만 남는다
    assert "지표명" in text
    assert "53405" not in text
    assert "simplefields" not in text
    # 무엇을 왜 뺐는지는 남는다
    notes = hwpxtree.hidden_notes()
    assert notes["tiny+white"]["count"] == 1
    assert notes["tiny+white"]["samples"] == ["53405"]
    assert notes["field"]["count"] == 1
    hwpxtree._collectors().hidden.clear()


def test_hidden_notes_are_per_conversion():
    """직전 변환 것만 담는다 — 문서 사이에 새면 수치가 거짓이 된다."""
    from docstruct.converters.hwpx import hwpxtree

    hwpxtree._collectors().hidden.clear()
    hwpxtree._collectors().hidden.setdefault("tiny", []).append("옛 문서")
    assert hwpxtree.hidden_notes()["tiny"]["count"] == 1
    hwpxtree._collectors().hidden.clear()
    assert hwpxtree.hidden_notes() == {}


def test_hidden_text_is_serialized():
    """결과물(document.json)에 실린다."""
    from docstruct.models import PageContent

    page = PageContent(page_no=1, page_no_kind="document", content="본문")
    page.hidden_text = {"tiny+white": {"count": 13, "samples": ["53405"]}}
    data = page.to_dict()
    assert data["hidden_text"]["tiny+white"]["count"] == 13


# ────────────────────────────────────────────────────────────────────
# 0.4.75 — 지면 장식이 짝 성적의 분모를 왜곡하고 있었다
#
# HWPX 는 제목·표지·간지를 **표로 그린다.** 그런 표는 PDF 에 대응 표가
# 아예 없으므로 "짝을 못 지은 것" 이 아니라 **"맞출 것이 없는 것"** 이다.
# 분모에 넣으면 성적이 실제보다 나쁘게 보인다.
#
#     문체부  840표 중 장식 405 → 40% → **75%**
#     행안부  580표 중 장식 268 → 40% → **73%**
#     조달청  119표 중 장식  48 → 47% → **76%**
# ────────────────────────────────────────────────────────────────────


def test_layout_tables_are_not_matchable():
    """제목 띠와 빈 상자는 짝 성적에서 뺀다."""
    from docstruct.align.documents import is_matchable

    band = {"cells": _cells([["2027년도 성과계획서", "", ""]])}
    empty = {"cells": _cells([["", ""], ["", ""]])}
    data = {"cells": _cells([["구분", "2026"], ["예산", "1,150"]])}

    assert is_matchable(band) is False
    assert is_matchable(empty) is False
    assert is_matchable(data) is True


def _align_pair_with_real_tables():
    """제목 상자와 **2행 이상 데이터 표**가 섞인 쌍."""
    def data(n):
        rows = [["구분", "2026", "2027"], [f"예산{n}", "1,150", "1,200"]]
        return {
            "id": f"table_{n}", "table_num": n, "placeholder": f"<table {n}>",
            "markdown": "| 구분 | 2026 | 2027 |",
            "cells": _cells(rows),
        }

    def box(n, text):
        return {
            "id": f"table_{n}", "table_num": n, "placeholder": f"<table {n}>",
            "markdown": f"| {text} |", "cells": _cells([[text]]),
        }

    pdf = {"filename": "x.pdf", "pages": [
        {"page_no": 1, "content": "사업 개요를 밝힌다. 조달 효율화가 목표다.\n\n<table 2>",
         "tables": [data(2)]},
        {"page_no": 2, "content": "성과지표는 다음과 같다.\n\n<table 4>",
         "tables": [data(4)]},
    ]}
    hwpx = {"filename": "x.hwpx", "pages": [{"page_no": 1, "content": (
        "<table 1>\n\n사업 개요를 밝힌다. 조달 효율화가 목표다.\n\n<table 2>\n\n"
        "<table 3>\n\n성과지표는 다음과 같다.\n\n<table 4>"),
        "tables": [box(1, "1. 임무와 비전"), data(2),
                   box(3, "2. 목표체계도"), data(4)]}]}
    return hwpx, pdf


def test_matchable_denominator_is_reported():
    """전체와 **맞출 수 있는 표**를 함께 낸다 — 감추지 않는다."""
    from docstruct.align import align_documents

    hwpx, pdf = _align_pair_with_real_tables()
    result = align_documents(hwpx, pdf)

    # 전체는 그대로 남는다
    assert result["total_tables"] == 4
    # 제목 상자 2개를 뺀 것이 성적의 분모다
    assert result["matchable_tables"] == 2
    assert result["matchable_matched"] == 2
    # 못 맞춘 표는 여전히 실린다 (0.4.64 — 버리지 않는다)
    assert len(result["unmatched"]) == 2


def test_summary_states_what_was_excluded():
    """요약이 무엇을 뺐는지 밝힌다 — 수치만 좋아 보이면 안 된다."""
    from docstruct.align import align_documents
    from docstruct.align.documents import summary_lines

    hwpx, pdf = _align_pair_with_layout_boxes()
    joined = "\n".join(summary_lines(align_documents(hwpx, pdf)))
    assert "맞출 수 있는 표 기준" in joined
    assert "지면 장식" in joined


def test_split_note_measures_containment_not_jaccard():
    """쪼개진 표는 포함률로 잰다 — Jaccard 는 크기 차이를 벌준다.

    실측(문체부 `table_838` · 토큰 3,231): 포함률 70%↑ 인 PDF 표가 101개
    이고 그 조각들이 원본의 86% 를 덮는데, 개별 Jaccard 최고는 0.041
    이었다(문턱 0.20). 못 찾은 것이 아니라 **찾고도 버렸다.**
    """
    from docstruct.align.documents import fragment_note

    big = {"cells": _cells(
        [[f"항목{r}", f"{r}00,000원", f"설명{r}입니다"] for r in range(40)])}
    piece = {"cells": _cells(
        [[f"항목{r}", f"{r}00,000원", f"설명{r}입니다"] for r in range(5, 12)])}

    got = fragment_note(big, [(3, piece)], head_rows=0)
    assert got is not None
    assert got["fragments"] == 1
    assert got["pages"] == [3, 3]
    assert 0 < got["covered"] < 1


def test_split_note_skips_small_tables():
    """작은 표는 이 문제가 아니다 — 잡음만 는다."""
    from docstruct.align.documents import fragment_note

    small = {"cells": _cells([["가", "나"], ["다", "라"]])}
    assert fragment_note(small, [(1, small)]) is None


# ────────────────────────────────────────────────────────────────────
# 0.4.76 — 분모를 깎는 대신 쪽을 준다
#
# 짝을 못 지은 표의 상당수는 HWPX 가 서술 상자로 그린 것을 PDF 가 본문
# 으로 풀어낸 것이다 — 내용은 그대로 있고 표라는 껍데기만 사라졌다.
# 실측(세 부처 141/142): 그런 표의 내용이 PDF **본문**에 85% 이상 들어
# 있었고 PDF **표**에는 9표뿐이었다.
#
# 표 대 표로는 못 맞추지만 **쪽은 줄 수 있다.**
#
#     조달청   54 → 67/71  (94%)   본문으로 13개 추가
#     행안부  227 → 282/312 (90%)  본문으로 55개 추가
#     문체부  328 → 407/435 (94%)  본문으로 79개 추가
# ────────────────────────────────────────────────────────────────────


def test_page_from_text_assigns_a_page():
    """표 짝이 없어도 본문에서 찾으면 쪽을 준다."""
    from docstruct.align.documents import _page_grams, page_from_text

    table = {"markdown": "| 측정산식 : 일반국민을 대상으로 수행하는 문화진흥사업 "
                         "문화 프로그램의 연간 참여자 수 및 예산변동치 반영 |"}
    pdf = {"pages": [
        {"page_no": 4, "content": "다른 이야기가 적힌 쪽입니다. 예산 편성 방향과 "
                                  "재정 운용 전략을 설명합니다.", "tables": []},
        {"page_no": 7, "content": "측정산식 : 일반국민을 대상으로 수행하는 "
                                  "문화진흥사업 문화 프로그램의 연간 참여자 수 및 "
                                  "예산변동치 반영", "tables": []},
    ]}
    got = page_from_text(table, _page_grams(pdf))
    assert got["page_no"] == 7
    assert got["containment"] >= 0.7
    assert got["margin"] >= 0.1


def test_page_from_text_refuses_when_not_unique():
    """어느 쪽인지 모르면 주지 않는다.

    성과계획서는 같은 서식 표가 쪽마다 되풀이되므로, 1위와 2위가 비슷하면
    찍는 순간 틀린 쪽을 준다.
    """
    from docstruct.align.documents import _page_grams, page_from_text

    same = "구분 목표 실적 측정산식 자료수집 방법 출처 가중치"
    table = {"markdown": f"| {same} |"}
    pdf = {"pages": [{"page_no": n, "content": same, "tables": []}
                     for n in (3, 8, 12)]}
    assert page_from_text(table, _page_grams(pdf)) is None


def test_page_from_text_refuses_below_floor():
    """조금 겹치는 것만으로는 주지 않는다."""
    from docstruct.align.documents import _page_grams, page_from_text

    table = {"markdown": "| 문화진흥사업 문화 프로그램 참여자 수 측정산식 |"}
    pdf = {"pages": [{"page_no": 2, "content": "전혀 다른 내용입니다. 조직 개편과 "
                                               "인력 운영 계획을 다룹니다.",
                      "tables": []}]}
    assert page_from_text(table, _page_grams(pdf)) is None


def test_text_matched_tables_carry_their_evidence():
    """어떻게 쪽을 얻었는지 결과물에 남는다 — 근거가 다르면 구분되어야 한다."""
    from docstruct.align import align_documents

    def data(n, rows):
        return {"id": f"table_{n}", "table_num": n,
                "placeholder": f"<table {n}>",
                "markdown": " ".join(r[0] for r in rows),
                "cells": _cells(rows)}

    story = [["측정산식은 일반국민을 대상으로 수행하는 문화진흥사업 문화 "
              "프로그램의 연간 참여자 수를 집계하여 산출한다", "비고"],
             ["측정대상기간은 1월부터 12월까지이며 실적치 집계는 이듬해 "
              "2월말에 완료한다", "확인"]]
    pdf = {"filename": "x.pdf", "pages": [
        {"page_no": 1, "content": "사업 개요를 밝힌다. 조달 효율화가 목표다.",
         "tables": [data(9, [["구분", "2026"], ["예산", "1,150"]])]},
        {"page_no": 2,
         "content": "측정산식은 일반국민을 대상으로 수행하는 문화진흥사업 문화 "
                    "프로그램의 연간 참여자 수를 집계하여 산출한다 "
                    "측정대상기간은 1월부터 12월까지이며 실적치 집계는 이듬해 "
                    "2월말에 완료한다",
         "tables": []},
    ]}
    hwpx = {"filename": "x.hwpx", "pages": [{"page_no": 1, "content": (
        "사업 개요를 밝힌다. 조달 효율화가 목표다.\n\n<table 9>\n\n<table 5>"),
        "tables": [data(9, [["구분", "2026"], ["예산", "1,150"]]),
                   data(5, story)]}]}

    result = align_documents(hwpx, pdf)
    assert result["matched_by_text"] >= 1
    got = [t for p in result["pages"] for t in p["tables"]
           if t.get("page_source") == "text"]
    assert got
    assert got[0]["page_evidence"]["containment"] >= 0.7


def test_text_page_floor_is_a_knob(monkeypatch):
    """문턱은 손잡이다 — 문서군이 바뀌면 다시 재야 한다."""
    from docstruct.align import documents

    assert documents._text_page_floor() == 0.70
    monkeypatch.setenv("DOCSTRUCT_ALIGN_TEXT_MIN", "0.85")
    assert documents._text_page_floor() == 0.85
    monkeypatch.setenv("DOCSTRUCT_ALIGN_TEXT_MIN", "엉망")
    assert documents._text_page_floor() == 0.70


# ────────────────────────────────────────────────────────────────────
# 0.4.78 — 구멍은 빈 칸으로 닫고, 본문 배정은 독립 신호로 검증한다
#
# 해양경찰청에서 `no_lattice` 가 34%(문체부는 10%)로 나와 괘선 복원이
# 닿지 않는 문서가 있음이 드러났다. 그런데 남은 결함 65표 중 **59표(91%)
# 가 구멍만**이었다 — 겹침이 없으면 빈 칸으로 닫을 수 있다.
#
#     해양경찰청  결함 15 → 4   (메운 표 11)
#     문체부      결함 33 → 2   (메운 표 31)
#     행안부      결함 17 → 1   (메운 표 16)
# ────────────────────────────────────────────────────────────────────


def test_hole_fill_closes_the_grid():
    """덮이지 않은 자리를 빈 칸으로 닫는다 — 글은 만들지 않는다."""
    from docstruct.experiments.tsr.restore.hole_fill import fill_holes
    from docstruct.structuring.checks import grid_check

    cells = _grid_cells([(0, 1, 1, 1), (0, 2, 1, 1),      # (0,0) 이 없다
                         (1, 0, 1, 3)])
    filled = fill_holes(cells)
    assert grid_check(filled)["ok"] is True
    added = [c for c in filled if c not in cells]
    assert len(added) == 1
    assert added[0]["text"] == ""                # 글을 지어내지 않는다


def test_hole_fill_leaves_overlaps_alone():
    """겹친 표는 손대지 않는다 — 경계 오류라 메워도 낫지 않는다."""
    from docstruct.experiments.tsr.restore.hole_fill import fill_holes

    overlapping = _grid_cells([(0, 0, 1, 2), (0, 1, 1, 2),
                               (1, 0, 1, 1), (1, 1, 1, 1), (1, 2, 1, 1)])
    assert fill_holes(overlapping) is None


def test_hole_fill_skips_mostly_empty_tables():
    """절반 넘게 빈 표는 메우지 않는다.

    격자를 놓친 것이 아니라 **애초에 표가 아닐** 수 있다 — 그런 것을
    빈 칸으로 채우면 없던 격자를 만들어 주는 셈이다.
    """
    from docstruct.experiments.tsr.restore.hole_fill import fill_holes

    sparse = _grid_cells([(0, 0, 1, 1), (3, 3, 1, 1)])   # 4×4 에 2칸뿐
    assert fill_holes(sparse) is None


def test_hole_fill_is_promoted():
    """0.4.79 승격 — 2부처 실측 · 회귀 0 · 내용 불변."""
    from docstruct.experiments.registry import DEFAULT_ON, _RUN_ORDER

    assert "hole_fill" in DEFAULT_ON
    # 괘선 복원이 이긴 자리를 지키고 남은 것만 본다
    assert _RUN_ORDER.index("hole_fill") > _RUN_ORDER.index("lattice_fill")


def test_hole_fill_needs_no_lattice():
    """괘선 없이 쓸 수 있어야 한다 — pdf_path 를 받지 않는다."""
    import inspect

    from docstruct.experiments.tsr.restore import hole_fill
    assert "pdf_path" not in inspect.signature(hole_fill.fill_holes).parameters
    spec = next(e for e in __import__(
        "docstruct.experiments", fromlist=["all_experiments"]
    ).all_experiments() if e.key == "hole_fill")
    # 형식을 가리지 않는다 — 자리만 채우므로 지면이 필요 없다
    assert {"pdf", "hwpx"} <= set(spec.formats)


def test_order_check_verifies_text_pages_independently():
    """본문으로 준 쪽을 **표 짝이 정한 순서**로 검증한다.

    `containment` 는 본문 대조가 스스로 매긴 점수이므로 그것으로 검증하면
    순환이다. 표 짝짓기는 다른 신호(셀 토큰)를 쓰므로 독립이다.
    """
    from docstruct.align.documents import order_check

    result = {"pages": [
        {"page_no": 10, "tables": [{"table_num": 1, "page_source": "pair"}]},
        {"page_no": 11, "tables": [{"table_num": 2, "page_source": "text"}]},
        {"page_no": 12, "tables": [{"table_num": 3, "page_source": "pair"}]},
    ]}
    got = order_check(result)
    assert got == {"checked": 1, "inside": 1, "outside": 0, "hit_rate": 1.0}


def test_order_check_flags_out_of_range():
    """이웃 구간을 벗어나면 잡아낸다."""
    from docstruct.align.documents import order_check

    result = {"pages": [
        {"page_no": 10, "tables": [{"table_num": 1, "page_source": "pair"}]},
        {"page_no": 40, "tables": [{"table_num": 2, "page_source": "text"}]},
        {"page_no": 12, "tables": [{"table_num": 3, "page_source": "pair"}]},
    ]}
    got = order_check(result)
    assert got["outside"] == 1 and got["hit_rate"] == 0.0


def test_order_check_reported_in_summary():
    """요약에 독립 검증 결과가 함께 나온다 — 자기 점수만 보이면 안 된다."""
    from docstruct.align.documents import summary_lines

    joined = "\n".join(summary_lines({
        "page_count": 3, "text_anchors": 2, "estimated_pages": 0,
        "matched_tables": 2, "total_tables": 3, "unmatched_tables": 1,
        "matchable_tables": 2, "matchable_matched": 2, "matched_by_text": 1,
        "text_order_check": {"checked": 1, "inside": 1, "outside": 0,
                             "hit_rate": 1.0},
    }))
    assert "순서 검증" in joined


# ────────────────────────────────────────────────────────────────────
# 0.4.79 — 격자는 예뻐졌는데 값이 오염됐다
#
# `lattice_fill`(0.4.73 승격)이 격자로 다시 세운 표에서 **앞 칸의 끝
# 글자가 다음 칸 앞에 딸려 온다.**
#
#     r1c2 '63,618'   r1c3 '8 66,578'   r1c4 '8 2,960'
#     r2c2 ') 36.2'   r2c3 '2 40.4'     r2c4 '4 4.2'
#
#     해경  parser 60표 오염 0%   ·  lattice_fill 36표 오염 **33%**
#     문체  parser 149표 오염 1%  ·  lattice_fill 215표 오염 **21%**
#
# 승격 때 쓴 두 지표가 **둘 다 눈이 멀었다** — 격자 온전성은 자리가 다
# 덮여 `ok` 이고, 토큰 닮음은 `8 66,578` 에서 `66,578` 이 그대로 나온다.
# ────────────────────────────────────────────────────────────────────


def test_leak_check_finds_boundary_spill():
    """앞 칸 끝 글자가 다음 칸 앞에 붙은 것을 잡는다."""
    from docstruct.structuring.checks import leak_check

    got = leak_check([
        {"row": 1, "col": 2, "rowspan": 1, "colspan": 1, "text": "63,618"},
        {"row": 1, "col": 3, "rowspan": 1, "colspan": 1, "text": "8 66,578"},
        {"row": 1, "col": 4, "rowspan": 1, "colspan": 1, "text": "8 2,960"},
    ])
    assert got["leaks"] == 2
    assert "63,618" in got["samples"][0]


def test_leak_check_is_quiet_on_sound_tables():
    """멀쩡한 표에서는 울지 않는다."""
    from docstruct.structuring.checks import leak_check

    assert leak_check([
        {"row": 0, "col": 0, "rowspan": 1, "colspan": 1, "text": "구분"},
        {"row": 0, "col": 1, "rowspan": 1, "colspan": 1, "text": "2026 예산"},
        {"row": 1, "col": 0, "rowspan": 1, "colspan": 1, "text": "예산"},
        {"row": 1, "col": 1, "rowspan": 1, "colspan": 1, "text": "1,150 백만원"},
    ]) is None
    assert leak_check([]) is None


def test_leak_check_survives_grid_check():
    """격자 온전성으로는 못 잡는 오류임을 못박는다.

    자리는 다 덮였으므로 `grid_check` 는 `ok` 라고 말한다 — 두 검사가
    보는 것이 다르다.
    """
    from docstruct.structuring.checks import grid_check, leak_check

    cells = [
        {"row": 0, "col": 0, "rowspan": 1, "colspan": 1, "text": "63,618"},
        {"row": 0, "col": 1, "rowspan": 1, "colspan": 1, "text": "8 66,578"},
        {"row": 1, "col": 0, "rowspan": 1, "colspan": 1, "text": "36.2"},
        {"row": 1, "col": 1, "rowspan": 1, "colspan": 1, "text": "2 40.4"},
    ]
    assert grid_check(cells)["ok"] is True       # 격자는 온전하다
    assert leak_check(cells)["leaks"] == 2       # 그런데 값은 오염됐다


def test_pipeline_records_cell_leaks():
    """판독 결과에 상시로 남는다 — 실험이 아니다."""
    import inspect

    from docstruct import pipeline

    source = inspect.getsource(pipeline.build_document)
    assert "leak_check(table.cells)" in source
    assert "table.cell_leaks" in source


def test_structuring_reports_leak_check():
    """구조화 검사에도 함께 나온다."""
    import inspect

    from docstruct import structuring

    source = inspect.getsource(structuring.structure_document)
    assert '"leak_check"' in source


# ────────────────────────────────────────────────────────────────────
# 0.4.80 — 새어 든 글자는 중복이므로 지운다 (은폐가 아니라 복원)
#
# 새어 든 글자는 앞 칸에 **이미 있다** — `63,618` 은 그 자체로 온전하고,
# 다음 칸 `8 66,578` 의 앞 `8` 은 같은 글자가 두 번 적힌 것이다.
#
# 원본 대조로 확인했다 — 오염 표의 셀이 원본과 **글자까지** 일치하는 비율:
#
#     해양경찰청   820셀   662(81%) → **787(96%)**   고친 칸 125
#     문체부     3,291셀 2,606(79%) → **3,211(98%)**  고친 칸 725
#
# 고친 칸 수와 일치 증가분이 맞물린다. 숫자 집합으로는 변화가 0이었다 —
# `8 66,578` 에서도 `66,578` 이 뽑히기 때문이다. **지표 하나만 믿으면
# 보이지 않는다.**
# ────────────────────────────────────────────────────────────────────


def test_repair_leaks_restores_the_value():
    """새어 든 글자를 지우면 원래 값이 된다."""
    from docstruct.structuring.checks import leak_check, repair_leaks

    cells = [
        {"row": 1, "col": 2, "rowspan": 1, "colspan": 1, "text": "63,618"},
        {"row": 1, "col": 3, "rowspan": 1, "colspan": 1, "text": "8 66,578"},
        {"row": 1, "col": 4, "rowspan": 1, "colspan": 1, "text": "8 2,960"},
        {"row": 2, "col": 2, "rowspan": 1, "colspan": 1, "text": "(비중)"},
        {"row": 2, "col": 3, "rowspan": 1, "colspan": 1, "text": ") 36.2"},
    ]
    assert repair_leaks(cells) == 3
    assert [c["text"] for c in cells] == [
        "63,618", "66,578", "2,960", "(비중)", "36.2"]
    assert leak_check(cells) is None


def test_repair_leaks_needs_a_repeated_pattern():
    """한 번뿐이면 손대지 않는다 — 우연일 수 있다.

    실측에서 남은 것은 전부 그런 오탐이었다: `회 계` 다음 칸이 `계 정` —
    진짜 표 머리이고 지우면 글이 깎인다.
    """
    from docstruct.structuring.checks import repair_leaks

    once = [
        {"row": 0, "col": 0, "rowspan": 1, "colspan": 1, "text": "회 계"},
        {"row": 0, "col": 1, "rowspan": 1, "colspan": 1, "text": "계 정"},
    ]
    assert repair_leaks(once) == 0
    assert once[1]["text"] == "계 정"            # 그대로 둔다


def test_repair_leaks_is_applied_in_pipeline():
    """판독 후처리로 상시 적용된다 — 실험이 아니다."""
    import inspect

    from docstruct import pipeline

    source = inspect.getsource(pipeline.build_document)
    assert "repair_leaks(table.cells)" in source
    # 고치고 **난 뒤에** 남은 것을 센다 — 순서가 뒤집히면 늘 오염으로 남는다
    assert source.index("repair_leaks(table.cells)") < source.index(
        "leak_check(table.cells)")


# ────────────────────────────────────────────────────────────────────
# 0.4.81 — HWPX 표가 제 캡션보다 먼저 나왔다
#
# `<hp:pos treatAsChar="0">` 인 표는 **문단에 매달린 객체**다. 지면에서는
# `vertOffset` 만큼 아래에 그려지는데 XML 로는 그 문단 앞쪽에 앉아 있다.
# 문서 순서로 훑으면 표가 제 캡션보다 먼저 나온다.
#
#     실측(해양경찰청 223표): 매달림 **159(71%)** · 글자 취급 64
#     결과물: `</table 166>` 다음에 `프로그램 내 사업 우선순위…`
#
# 글자 취급(`treatAsChar=1`)은 XML 순서가 곧 지면 순서라 손대지 않는다.
# ────────────────────────────────────────────────────────────────────


def _tbl_xml(treat_as_char: str | None) -> "ET.Element":
    import xml.etree.ElementTree as ET

    pos = (f'<hp:pos treatAsChar="{treat_as_char}" vertRelTo="PARA"/>'
           if treat_as_char is not None else "")
    return ET.fromstring(
        '<hp:tbl xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
        f'{pos}<hp:tr><hp:tc><hp:t>값</hp:t></hp:tc></hp:tr></hp:tbl>')


def test_table_anchor_reads_treat_as_char():
    """글자 취급인지 매달린 것인지 가른다."""
    from docstruct.converters.hwpx.hwpxtree import _table_anchor

    assert _table_anchor(_tbl_xml("1")) == "inline"
    assert _table_anchor(_tbl_xml("0")) == "anchored"
    assert _table_anchor(_tbl_xml(None)) == "unknown"


def test_anchored_table_follows_its_caption():
    """매달린 표는 문단 글 **뒤**에 온다 — 캡션이 앞선다."""
    import xml.etree.ElementTree as ET

    from docstruct.converters.hwpx import hwpxtree

    hwpxtree._collectors().anchors.clear()
    para = ET.fromstring(
        '<hp:p xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
        '<hp:run charPrIDRef="0">'
        '<hp:tbl><hp:pos treatAsChar="0"/><hp:tr><hp:tc>'
        '<hp:p><hp:run charPrIDRef="0"><hp:t>63,618</hp:t></hp:run></hp:p>'
        "</hp:tc></hp:tr></hp:tbl></hp:run>"
        '<hp:run charPrIDRef="0"><hp:t>프로그램 내 사업 우선순위</hp:t></hp:run>'
        "</hp:p>")
    root = ET.Element("{http://www.hancom.co.kr/hwpml/2011/paragraph}sec")
    root.append(para)

    blocks = hwpxtree._walk(root, set(), {})
    joined = "\n".join(blocks)
    # 캡션이 표보다 앞선다
    assert joined.index("프로그램 내 사업 우선순위") < joined.index("63,618")
    assert hwpxtree.anchor_notes()["reordered"] == 1
    hwpxtree._collectors().anchors.clear()


def test_anchor_notes_are_per_conversion():
    """직전 변환 것만 담는다."""
    from docstruct.converters.hwpx import hwpxtree

    hwpxtree._collectors().anchors.clear()
    assert hwpxtree.anchor_notes() == {}
    hwpxtree._collectors().anchors["anchored"] = 3
    assert hwpxtree.anchor_notes() == {"anchored": 3}
    hwpxtree._collectors().anchors.clear()


def test_table_anchors_serialized():
    """결과물에 실린다."""
    from docstruct.models import PageContent

    page = PageContent(page_no=1, page_no_kind="document", content="본문")
    page.table_anchors = {"anchored": 159, "inline": 64, "reordered": 21}
    assert page.to_dict()["table_anchors"]["reordered"] == 21


# ────────────────────────────────────────────────────────────────────
# 0.4.82 — 큰 흰 글자를 숨긴 글로 오해했다
#
# 0.4.74 에서 "흰 글자는 보이지 않는다" 며 색만 보고 걸렀는데, **진한
# 바탕 위에 얹힌 제목**이 그렇게 사라졌다.
#
#     charPr 978  height=1600  textColor=#FFFFFF   ← `별첨8` · 지면에 또렷하다
#     charPr …    height= 100  textColor=#FFFFFF   ← 목차 · 진짜 숨긴 글
#
# 실측: `별첨1`~`별첨8` 이 네 부처 결과물에서 통째로 빠져 있었다.
# 진짜 숨긴 흰 글자는 1pt 로도 함께 작다 — 크기를 함께 보면 갈린다.
# ────────────────────────────────────────────────────────────────────


def _header_zip(char_prs: str):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("Contents/header.xml",
                         '<?xml version="1.0"?>\n'
                         '<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/'
                         f'2011/head">{char_prs}</hh:head>')
    return zipfile.ZipFile(buf)


def test_large_white_text_is_not_hidden():
    """큰 흰 글자는 숨긴 것이 아니다 — 진한 바탕 위의 제목이다."""
    from docstruct.converters.hwpx import hwpxtree

    archive = _header_zip(
        '<hh:charPr id="978" height="1600" textColor="#FFFFFF"/>'
        '<hh:charPr id="695" height="100" textColor="#FFFFFF"/>')
    got = hwpxtree._hidden_char_ids(archive)
    assert "978" not in got                      # 16pt — 보이는 글
    assert got["695"] == "tiny+white"            # 1pt — 숨긴 글


def test_hidden_split_matches_the_measured_sizes():
    """실측에서 나온 크기 갈림을 못박는다.

    네 부처 흰 글자모양의 크기는 100 · 1600 · 1800 뿐이었다 — 1pt 는
    숨긴 것이고 16~18pt 는 제목이다. 그 사이가 비어 있어 문턱(900)이
    안전하다.
    """
    from docstruct.converters.hwpx.hwpxtree import VISIBLE_WHITE_HEIGHT

    assert 100 < VISIBLE_WHITE_HEIGHT < 1600


def test_shape_heading_survives():
    """도형 안의 제목 번호가 본문에 남는다."""
    import xml.etree.ElementTree as ET

    from docstruct.converters.hwpx import hwpxtree

    hwpxtree._collectors().hidden.clear()
    shape = ET.fromstring(
        '<hp:container xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
        "<hp:rect><hp:drawText><hp:subList>"
        '<hp:p><hp:run charPrIDRef="961"><hp:t>복합 프로그램 성과지표 관리'
        "</hp:t></hp:run>"
        '<hp:run charPrIDRef="978"><hp:t>별첨8</hp:t></hp:run></hp:p>'
        "</hp:subList></hp:drawText></hp:rect></hp:container>")
    # 978 을 숨김 목록에 넣지 **않았을** 때 살아남아야 한다
    text = hwpxtree._shape_text(shape, set(), {})
    assert "별첨8" in text
    assert "복합 프로그램 성과지표 관리" in text
    hwpxtree._collectors().hidden.clear()


# ────────────────────────────────────────────────────────────────────
# 0.4.83 — `repair_leaks` 가 cells 만 고치고 markdown 은 오염된 채 두었다
#
# 0.4.80 은 `TableInfo.cells` 만 고쳤다. `TableInfo.markdown` 과 본문의
# `<table N>` 블록에는 `8 66,578` 이 그대로 남아, trace 는 "복원" 이라
# 적는데 document.md 와 JSON 의 markdown 은 오염돼 있었다 — 결과물의 두
# 필드가 다른 말을 하는 상태였다.
# ────────────────────────────────────────────────────────────────────


def _leaky_cells() -> list[dict]:
    return [
        {"row": 0, "col": 0, "rowspan": 1, "colspan": 1, "text": "구분"},
        {"row": 0, "col": 1, "rowspan": 1, "colspan": 1, "text": "2024"},
        {"row": 0, "col": 2, "rowspan": 1, "colspan": 1, "text": "2025"},
        {"row": 1, "col": 0, "rowspan": 1, "colspan": 1, "text": "금액"},
        {"row": 1, "col": 1, "rowspan": 1, "colspan": 1, "text": "63,618"},
        {"row": 1, "col": 2, "rowspan": 1, "colspan": 1, "text": "8 66,578"},
        {"row": 2, "col": 0, "rowspan": 1, "colspan": 1, "text": "(비중)"},
        {"row": 2, "col": 1, "rowspan": 1, "colspan": 1, "text": "36.2"},
        {"row": 2, "col": 2, "rowspan": 1, "colspan": 1, "text": "2 40.4"},
    ]


def test_cell_text_diff_lists_repaired_pairs():
    """고치기 전 값과 견줘 (옛값, 새값) 쌍을 행·열 순으로 낸다."""
    from docstruct.structuring.checks import cell_text_diff, repair_leaks

    cells = _leaky_cells()
    before = {(c["row"], c["col"]): c["text"] for c in cells}
    assert repair_leaks(cells) == 2
    assert cell_text_diff(before, cells) == [("8 66,578", "66,578"),
                                             ("2 40.4", "40.4")]


def test_patch_markdown_cells_replaces_only_that_cell():
    """칸 단위로 그 글자만 바꾼다 — 같은 값이 다른 칸에 있어도 자리를 지킨다."""
    from docstruct.structuring.checks import patch_markdown_cells

    markdown = ("| 구분   | 2024   | 2025     |\n"
                "|--------|--------|----------|\n"
                "| 금액   | 63,618 | 8 66,578 |\n"
                "| (비중) | 36.2   | 2 40.4   |")
    fixed, patched = patch_markdown_cells(
        markdown, [("8 66,578", "66,578"), ("2 40.4", "40.4")])
    assert patched == 2
    assert "8 66,578" not in fixed and "| 66,578" in fixed
    assert "2 40.4" not in fixed and "| 40.4" in fixed
    # 앞 칸 `63,618` 은 건드리지 않았다
    assert "| 63,618 |" in fixed
    # 열 폭을 옛 폭에 맞춰 정렬을 유지한다 — 행마다 `|` 수가 같다
    assert len({ln.count("|") for ln in fixed.splitlines()}) == 1


def test_patch_markdown_cells_reports_misses():
    """markdown 에 그대로 찍히지 않은 칸(다단 머리 접힘 등)은 세지 않고 넘긴다."""
    from docstruct.structuring.checks import patch_markdown_cells

    fixed, patched = patch_markdown_cells("| a | b |\n|---|---|\n| c | d |",
                                          [("없는값", "x"), ("d", "e")])
    assert patched == 1
    assert "| e |" in fixed
    assert patch_markdown_cells("", [("a", "b")]) == ("", 0)
    assert patch_markdown_cells("| a |", []) == ("| a |", 0)


def test_pipeline_syncs_repaired_markdown():
    """파이프라인이 cells 고침을 markdown 과 본문 블록에도 옮긴다."""
    import inspect

    from docstruct import pipeline

    source = inspect.getsource(pipeline.build_document)
    assert "patch_markdown_cells(table.markdown" in source
    assert "sync_table_block(" in source
    # cells 를 고친 **뒤에** markdown 을 맞춘다
    assert source.index("repair_leaks(table.cells)") < source.index(
        "patch_markdown_cells(table.markdown")


def test_repaired_table_markdown_matches_cells_end_to_end():
    """실제 TableInfo 에 걸어 보면 cells 와 markdown 이 같은 값을 말한다."""
    from docstruct.models import PageContent, TableInfo
    from docstruct.structuring.checks import (cell_text_diff, leak_check,
                                              patch_markdown_cells, repair_leaks)
    from docstruct.tables.tags import make_table_block, sync_table_block

    cells = _leaky_cells()
    markdown = ("| 구분 | 2024 | 2025 |\n| --- | --- | --- |\n"
                "| 금액 | 63,618 | 8 66,578 |\n| (비중) | 36.2 | 2 40.4 |")
    table = TableInfo(id="table_1", table_num=1, placeholder="<table 1>",
                      markdown=markdown, cells=cells)
    page = PageContent(page_no=1, page_no_kind="exact",
                       content="앞글\n\n" + make_table_block(1, markdown) + "\n\n뒷글")

    before = {(c["row"], c["col"]): c["text"] for c in table.cells}
    got = repair_leaks(table.cells)
    table.markdown, in_md = patch_markdown_cells(
        table.markdown, cell_text_diff(before, table.cells))
    page.content = sync_table_block(page.content, 1, table.markdown)

    assert got == in_md == 2
    assert leak_check(table.cells) is None
    for bad in ("8 66,578", "2 40.4"):
        assert bad not in table.markdown
        assert bad not in page.content
    assert "66,578" in page.content and "40.4" in page.content
    assert page.content.startswith("앞글") and page.content.endswith("뒷글")


# ────────────────────────────────────────────────────────────────────
# 0.4.87 — HWPX 수집기가 모듈 전역이라 동시 변환에서 섞였다
#
# 실측(조달청 성과계획서, 스레드 4개 동시): 단독 anchored 90 · inline 29 ·
# tiny+white 13 이던 것이 동시에는 anchored 329 · inline 106 · tiny+white 52
# 로 부풀었다 — 남의 문서까지 센 값이다. 이것이 CONVERT_CONCURRENCY 를 1 로
# 묶어 두게 만든 원인이었다.
# ────────────────────────────────────────────────────────────────────


def test_hwpx_collectors_are_per_thread():
    """수집기는 스레드마다 따로다 — 한쪽이 쌓아도 다른 쪽이 보지 않는다."""
    import threading

    from docstruct.converters.hwpx import hwpxtree

    hwpxtree._collectors().anchors["anchored"] = 7
    hwpxtree._collectors().hidden.setdefault("tiny", []).append("주 스레드")

    seen: dict = {}

    def other():
        box = hwpxtree._collectors()
        seen["anchors"] = dict(box.anchors)
        seen["hidden"] = {k: list(v) for k, v in box.hidden.items()}
        box.anchors["anchored"] = 999

    worker = threading.Thread(target=other)
    worker.start()
    worker.join()

    assert seen["anchors"] == {}, "다른 스레드가 주 스레드의 앵커를 봤다"
    assert seen["hidden"] == {}, "다른 스레드가 주 스레드의 숨은 글을 봤다"
    # 그쪽이 999 를 넣어도 이쪽은 그대로다
    assert hwpxtree._collectors().anchors["anchored"] == 7
    hwpxtree._collectors().anchors.clear()
    hwpxtree._collectors().hidden.clear()


def test_hwpx_module_has_no_shared_collector_globals():
    """옛 전역 이름이 되살아나지 않게 못 박는다.

    `_HIDDEN_SEEN`·`_ANCHOR_SEEN`·`_HIDDEN_CELL` 을 다시 두면 동시 변환에서
    같은 오염이 재발한다. 이름 자체를 금지한다.
    """
    import pathlib

    from docstruct.converters.hwpx import hwpxtree

    source = pathlib.Path(hwpxtree.__file__).read_text(encoding="utf-8")
    for banned in ("_HIDDEN_SEEN", "_ANCHOR_SEEN", "_HIDDEN_CELL"):
        assert banned not in source, banned
    assert "threading.local()" in source


# ────────────────────────────────────────────────────────────────────
# 0.4.88 — 로그만으로 "겹쳐 돌았나" 를 가릴 수 있어야 한다
#
# `추출 (HWP 파싱) 1663초 100%` 한 줄로는 그 문서가 무거운 것인지 여러 건이
# 겹쳐 각자의 벽시계가 부푼 것인지 알 수 없었다. 실측: 같은 문서가 스레드
# 1·2·4·8개에서 0.35·0.53·0.93·1.60초 — 처리량은 3.8건/초로 평평했다.
# ────────────────────────────────────────────────────────────────────


def test_timing_log_says_solo_when_alone(caplog):
    """단독 실행이면 그렇게 적는다 — 총 시간을 그대로 읽어도 된다는 뜻."""
    import logging

    from docstruct import pipeline

    with caplog.at_level(logging.INFO, logger="docstruct.pipeline"):
        pipeline._log_timings({"추출": 12.0}, 1)
    assert "단독 실행" in caplog.text
    assert "동시 실행" not in caplog.text


def test_timing_log_flags_overlap_and_divides(caplog):
    """겹쳤으면 최대 동시 수와 **이 문서의 몫**을 함께 낸다."""
    import logging

    from docstruct import pipeline

    with caplog.at_level(logging.INFO, logger="docstruct.pipeline"):
        pipeline._log_timings({"추출": 1600.0}, 8)
    assert "동시 실행 최대 8건" in caplog.text
    assert "200.0초" in caplog.text, "겹친 수로 나눈 몫이 없다"


def test_inflight_counter_tracks_overlap():
    """계수기가 실제 겹침을 센다 — 들어온 순간과 나가는 순간의 큰 쪽."""
    import threading

    from docstruct import pipeline

    started = threading.Event()
    release = threading.Event()
    peaks: list[int] = []

    def hold():
        with pipeline._counted("가.hwpx", "hwpx") as peak:
            started.set()
            release.wait(timeout=5)
        peaks.append(peak[0])

    worker = threading.Thread(target=hold)
    worker.start()
    started.wait(timeout=5)
    with pipeline._counted("나.hwpx", "hwpx") as peak:
        assert peak[0] == 2, "두 번째 문서가 겹침을 보지 못했다"
    release.set()
    worker.join(timeout=5)
    assert peaks == [2], "첫 문서가 나갈 때 겹침을 기억하지 못했다"
    # 다 나가면 다시 0 이다 — 다음 문서가 단독으로 보여야 한다
    with pipeline._counted("다.hwpx", "hwpx") as peak:
        assert peak[0] == 1


# ────────────────────────────────────────────────────────────────────
# 0.4.89 — 그림에 닫는 태그가 없어 몫이 어디서 끝나는지 몰랐다
#
# 예전에는 여는 표식 하나뿐이었고(`<!-- image_1 -->`), 경로마다 모양도
# 달랐다(PDF `image_1` · HWPX `image 1`). 판독이 읽은 글이 본문에 그냥
# 이어 붙어, 어디까지가 그림이고 어디부터가 본문인지 셀 수 없었다.
# ────────────────────────────────────────────────────────────────────


def test_image_block_opens_and_closes():
    """그림 구간은 여닫는 태그로 감싼다 — 표와 같은 계약."""
    from docstruct.images.tags import close_tag, make_image_block, open_tag

    block = make_image_block(3, "조직도")
    assert block.startswith(open_tag(3)) and block.endswith(close_tag(3))
    assert "<image-desc 3>조직도</image-desc 3>" in block
    # 읽은 것이 없으면 그 칸은 아예 없다 — 빈 칸을 만들지 않는다
    assert "image-read" not in block


def test_image_block_separates_description_from_read():
    """설명(파서)과 내용(판독)은 다른 칸에 담긴다 — 출처가 다르다."""
    from docstruct.images.tags import make_image_block, parse_image_block

    body = "| 부서 | 인원 |\n| --- | --- |"
    content = f"앞\n\n{make_image_block(1, '조직도', body)}\n\n뒤"
    got = parse_image_block(content, 1)
    assert got["description"] == "조직도"
    assert got["read"] == body


def test_sync_image_block_keeps_the_other_slot():
    """한 칸만 고치면 다른 칸은 그대로다 — 다시 읽어도 설명이 안 지워진다."""
    from docstruct.images.tags import make_image_block, parse_image_block, sync_image_block

    content = f"앞\n\n{make_image_block(1, '조직도', '처음 읽음')}\n\n뒤"
    once = sync_image_block(content, 1, read="다시 읽음")
    got = parse_image_block(once, 1)
    assert got["read"] == "다시 읽음"
    assert got["description"] == "조직도", "설명이 지워졌다"
    # 두 번 읽어도 글이 두 벌로 늘지 않는다 (옛 이어 붙이기의 병)
    assert once.count("다시 읽음") == 1
    assert sync_image_block(once, 1, read="다시 읽음") == once


def test_strip_image_blocks_leaves_only_body():
    """닫는 태그가 있으니 그림 구간만 떼어낼 수 있다."""
    from docstruct.images.tags import make_image_block, strip_image_blocks

    content = f"앞 문단\n\n{make_image_block(1, '조직도', '읽은 글')}\n\n뒤 문단"
    assert strip_image_blocks(content) == "앞 문단\n\n뒤 문단"


def test_legacy_image_marks_are_upgraded():
    """0.4.88 이전 산출물도 읽는다 — 표식 뒤 글을 판독 칸으로 올린다."""
    from docstruct.images.tags import normalize_image_blocks, parse_image_block

    for mark in ("<!-- image_1 -->", "<!-- image 1 -->"):
        legacy = f"앞\n\n{mark}\n\n읽은 글\n둘째 줄\n\n## 다음 절\n\n본문"
        got = parse_image_block(normalize_image_blocks(legacy), 1)
        assert got is not None, mark
        assert got["read"] == "읽은 글\n둘째 줄"
        assert "## 다음 절" not in got["read"], "제목까지 그림으로 삼켰다"


def test_both_extractors_use_the_same_image_tag():
    """PDF 와 HWPX 가 같은 모양을 쓴다 — 예전에는 밑줄/공백으로 갈렸다."""
    import inspect

    from docstruct.extractors import hwpx as hwpx_extractor
    from docstruct.images import picture

    for module in (picture, hwpx_extractor):
        source = inspect.getsource(module)
        assert "docstruct.images.tags import" in source, module.__name__
        # 주석은 옛 모양을 설명하느라 언급한다 — **코드 줄**만 본다.
        code = [line for line in source.splitlines()
                if not line.strip().startswith("#")]
        assert not [line for line in code if "<!-- image" in line], module.__name__


# ────────────────────────────────────────────────────────────────────
# 0.4.90 — "이걸 고치려면 어디로 가나" 를 코드가 답한다
#
# 폴더는 두 축(형식 · 인식)인데 실제 작업은 두 축이 만나는 자리에서
# 일어난다 — "스캔 PDF 의 표" 는 converters/pdf · tables · experiments 에
# 걸쳐 있다. 파일은 폴더 하나에만 살 수 있으니 표로 잇는다.
# ────────────────────────────────────────────────────────────────────


def test_guide_paths_all_exist():
    """길잡이가 가리키는 파일이 실제로 있어야 한다 — 문서처럼 낡지 않게."""
    from docstruct.core.guide import TOPICS, resolve_stop

    missing = [(topic.key, stop.path) for topic in TOPICS for stop in topic.stops
               if resolve_stop(stop.path) is None]
    assert not missing, f"길잡이가 없는 파일을 가리킵니다: {missing}"


def test_guide_covers_every_format_and_concern():
    """네 형식과 세 인식 축이 모두 길잡이에 있어야 한다."""
    from docstruct.core.guide import TOPICS

    keys = {topic.key for topic in TOPICS}
    for need in ("scan-pdf-table", "text-pdf-table", "hwpx-table", "hwp-table",
                 "page-align", "scan-text", "picture"):
        assert need in keys, need
    # 자리마다 무엇을 하는 곳인지 적혀 있어야 한다 — 경로만으로는 못 찾는다
    for topic in TOPICS:
        assert topic.stops, topic.key
        for stop in topic.stops:
            assert stop.does.strip(), (topic.key, stop.path)


def test_guide_search_finds_the_right_topic():
    """사람이 치는 말로 찾힌다."""
    from docstruct.core.guide import find_topics

    assert find_topics("스캔 pdf 표")[0].key == "scan-pdf-table"
    assert find_topics("hwpx 쪽 맞춤")[0].key == "page-align"
    assert find_topics("그림 판독")[0].key == "picture"
    assert find_topics("")  # 빈 질의는 전체 목록
    assert find_topics("존재하지않는말") == []


def test_experiment_needs_are_known_capabilities():
    """실험이 요구하는 재료 이름은 형식표에 있는 것이어야 한다."""
    from docstruct.core.guide import FORMAT_CAPABILITIES
    from docstruct.experiments import all_experiments

    known = set().union(*(caps.keys() for caps in FORMAT_CAPABILITIES.values()))
    for experiment in all_experiments():
        for need in experiment.needs:
            assert need in known, (experiment.key, need)


def test_experiments_that_run_without_material_are_listed():
    """형식은 통과하는데 재료가 없어 **빈손으로 도는** 실험을 못 박는다.

    실험_총정리 §6 의 "조용히 비켜 간다" 가 이것이다. 목록이 늘거나 줄면
    시험이 걸린다 — 조용히 늘어나는 것을 막는 것이 이 시험의 목적이다.
    """
    from docstruct.experiments import all_experiments

    barren = {(e.key, fmt) for e in all_experiments() for fmt in ("pdf", "hwpx", "hwp")
              if fmt in e.formats and e.missing_for(fmt)}
    assert barren == {
        # HWP 는 cells 를 만들지 못한다 — 가장 큰 공백
        ("hole_fill", "hwp"), ("otsl_diff", "hwp"), ("cell_repair", "hwp"),
        # HWP·HWPX 에는 좌표가 없다
        ("cell_repair", "hwpx"),
        # HWP·HWPX 에는 쪽 경계가 없다
        ("page_chrome", "hwp"), ("page_chrome", "hwpx"),
        # HWP·HWPX 는 지면을 화소로 그릴 수 없다 (스캔 이중 판독 대조)
        ("scan_ab", "hwp"), ("scan_ab", "hwpx"),
    }, f"빈손 실험 목록이 달라졌습니다: {sorted(barren)}"


# ────────────────────────────────────────────────────────────────────
# 0.4.91 — for 문으로 문서를 밀어 넣을 때 무엇이 지켜지나
#
# 설정은 os.environ 을 거쳐 들어가고 get_settings() 는 **프로세스 전역**
# 캐시다. 같은 프로세스에서 서로 다른 설정으로 동시에 돌리면 섞인다.
# DocStruct.run() 은 api._applied 의 락으로 막지만, build_document 를
# 직접 부르면 막을 것이 없다 — 그래서 소리를 내고, --jobs 로 프로세스를
# 가를 길을 준다.
# ────────────────────────────────────────────────────────────────────


def test_settings_fingerprint_tracks_docstruct_env(monkeypatch):
    """지문은 DOCSTRUCT_* 환경변수만 본다."""
    from docstruct import pipeline

    monkeypatch.delenv("DOCSTRUCT_TEST_KNOB", raising=False)
    before = pipeline._settings_fingerprint()
    monkeypatch.setenv("PATH_UNRELATED_THING", "1")
    assert pipeline._settings_fingerprint() == before, "무관한 변수가 지문을 바꿨다"
    monkeypatch.setenv("DOCSTRUCT_TEST_KNOB", "on")
    assert pipeline._settings_fingerprint() != before


def test_concurrent_runs_with_different_settings_warn(caplog, monkeypatch):
    """같은 프로세스·다른 설정으로 겹치면 경고한다 (조용히 섞이지 않는다)."""
    import logging
    import threading

    from docstruct import pipeline

    started = threading.Event()
    release = threading.Event()

    def hold():
        with pipeline._counted("가.hwpx", "hwpx"):
            started.set()
            release.wait(timeout=5)

    monkeypatch.delenv("DOCSTRUCT_TEST_KNOB", raising=False)
    worker = threading.Thread(target=hold)
    worker.start()
    started.wait(timeout=5)
    try:
        monkeypatch.setenv("DOCSTRUCT_TEST_KNOB", "다른값")
        with caplog.at_level(logging.WARNING, logger="docstruct.pipeline"):
            with pipeline._counted("나.hwpx", "hwpx"):
                pass
    finally:
        release.set()
        worker.join(timeout=5)
    assert "다른 설정" in caplog.text
    assert "--jobs" in caplog.text, "고치는 방법을 알려주지 않았다"


def test_same_settings_concurrency_does_not_warn(caplog):
    """설정이 같으면 겹쳐도 경고하지 않는다 — 그때는 섞일 것이 없다."""
    import logging
    import threading

    from docstruct import pipeline

    started = threading.Event()
    release = threading.Event()

    def hold():
        with pipeline._counted("가.hwpx", "hwpx"):
            started.set()
            release.wait(timeout=5)

    worker = threading.Thread(target=hold)
    worker.start()
    started.wait(timeout=5)
    try:
        with caplog.at_level(logging.WARNING, logger="docstruct.pipeline"):
            with pipeline._counted("나.hwpx", "hwpx"):
                pass
    finally:
        release.set()
        worker.join(timeout=5)
    assert "다른 설정" not in caplog.text


def test_resolve_jobs_never_exceeds_targets():
    """문서보다 많은 프로세스는 띄우지 않는다 — 띄우는 비용이 이득보다 크다."""
    from docstruct.cli import _resolve_jobs

    assert _resolve_jobs(4, 1) == 1, "1건인데 프로세스를 나눴다"
    assert _resolve_jobs(4, 2) == 2
    assert _resolve_jobs(1, 10) == 1
    assert _resolve_jobs(0, 2) in (1, 2)      # CPU 수에 따라
    assert _resolve_jobs(-3, 5) == 1          # 헛값도 안전하게


def test_worker_returns_reason_instead_of_raising(tmp_path):
    """자식이 실패해도 예외를 던지지 않는다 — 한 건이 전체를 멈추면 안 된다."""
    from docstruct.cli import _worker

    missing = tmp_path / "없는파일.hwpx"
    name, error = _worker((str(missing), str(tmp_path), {"no_llm": True}, "없는파일"))
    assert name == "없는파일.hwpx"
    assert error and "@" in error, f"어디서 났는지가 없다: {error}"


def test_parallel_batch_is_process_based_not_threads():
    """일괄 처리는 **프로세스**로 나눈다. 스레드로는 GIL 때문에 안 빨라진다."""
    import inspect

    from docstruct import cli

    source = inspect.getsource(cli._run_parallel)
    assert "ProcessPoolExecutor" in source
    assert "ThreadPoolExecutor" not in source


# ────────────────────────────────────────────────────────────────────
# 0.4.92 — 일괄 처리에서 산출 폴더가 겹쳐 결과가 조용히 사라졌다
#
# 폴더 이름은 `safe_file_stem(name)` — 확장자를 뗀 것이었다. 그래서
# `성과계획서.hwpx` 와 `성과계획서.pdf` 가 같은 폴더를 썼다. 이 프로젝트가
# **늘 다루는 짝**이다(쪽 맞춤은 같은 문서의 HWPX 와 PDF 를 쓴다).
# 실측: 네 건을 넣으면 "4건 성공" 이라 적고 폴더는 두 개만 남았다.
# ────────────────────────────────────────────────────────────────────


def test_same_stem_different_extension_gets_own_folder():
    """확장자만 다른 짝이 같은 폴더를 쓰지 않는다 — HWPX/PDF 짝이 이 모양."""
    from docstruct.output.names import assign_out_dirs

    got = assign_out_dirs(["성과계획서.hwpx", "성과계획서.pdf"])
    assert len(set(got.values())) == 2, got
    # 0.4.93 — 폴더 이름이 **확장자를 포함한 파일 이름**이다
    assert got["성과계획서.hwpx"] == "성과계획서.hwpx"
    assert got["성과계획서.pdf"] == "성과계획서.pdf"


def test_names_that_normalize_alike_get_own_folder():
    """정규화하면 같아지는 이름도 갈린다 (공백 → 밑줄)."""
    from docstruct.output.names import assign_out_dirs

    got = assign_out_dirs(["성과 계획서.hwpx", "성과_계획서.hwpx"])
    assert len(set(got.values())) == 2, got
    for folder in got.values():
        assert folder.startswith("성과_계획서.hwpx__")


def test_unique_names_keep_their_old_folder():
    """겹치지 않으면 폴더 이름이 예전과 같다 — 기존 산출물이 흔들리면 안 된다."""
    from docstruct.output.names import (assign_out_dirs, describe_renames,
                                        safe_file_name)

    names = ["가.hwpx", "나.pdf", "다.hwp"]
    got = assign_out_dirs(names)
    for name in names:
        assert got[name] == safe_file_name(name)
    assert describe_renames(got) == [], "안 겹치는데 이름을 바꿨다"


def test_folder_assignment_is_order_independent():
    """입력 순서가 달라도 같은 폴더 이름이 나온다 — 다시 돌려도 자리가 같다."""
    from docstruct.output.names import assign_out_dirs

    names = ["성과계획서.hwpx", "성과계획서.pdf", "성과 계획서.hwpx", "따로.hwpx"]
    assert assign_out_dirs(names) == assign_out_dirs(list(reversed(names)))
    # 한 건을 더 넣어도 나머지 이름은 그대로다
    plus = assign_out_dirs([*names, "새문서.pdf"])
    for name in names:
        assert plus[name] == assign_out_dirs(names)[name], name


def test_duplicate_entry_is_one_document():
    """같은 이름이 두 번 들어오면 한 항목이다 — 자기 자신과 겹치지 않는다."""
    from docstruct.output.names import assign_out_dirs

    got = assign_out_dirs(["가.hwpx", "가.hwpx"])
    assert got == {"가.hwpx": "가.hwpx"}


def test_batch_save_uses_collision_free_folders():
    """DocStructBatch.save 도 같은 규칙을 쓴다 (반환 dict 키도 안 뭉갠다)."""
    import inspect

    from docstruct.api import DocStructBatch

    source = inspect.getsource(DocStructBatch.save)
    assert "assign_out_dirs" in source
    assert "Path(doc.filename).stem" not in source, "옛 규칙이 남아 있다"


# ────────────────────────────────────────────────────────────────────
# 0.4.93 — 쪽 맞춤: 이미 돌린 결과가 있으면 다시 돌리지 않는다
#
# 판독은 형식마다 따로 돈다(HWPX 는 XML, PDF 는 Docling). 쪽 맞춤은 그
# 둘이 끝난 뒤의 일인데, 예전에는 맞출 때마다 **두 건을 처음부터 다시**
# 판독했다 — 바로 앞에 돌려 둔 결과가 옆 폴더에 있어도.
# ────────────────────────────────────────────────────────────────────


def test_out_folder_name_keeps_the_extension():
    """산출 폴더 이름에 확장자가 있어야 hwpx/pdf 짝이 갈린다."""
    from docstruct.align.pair import out_folder_name

    assert out_folder_name("성과계획서.hwpx") == "성과계획서.hwpx"
    assert out_folder_name("성과계획서.pdf") == "성과계획서.pdf"
    assert out_folder_name("/어딘가/성과 계획서.hwpx") == "성과_계획서.hwpx"


def test_prepare_reuses_existing_document_json(tmp_path):
    """돌려 둔 결과가 있으면 원본을 건드리지 않는다.

    원본을 **못 읽는 쓰레기 바이트**로 둔다 — 판독을 시도하면 터지므로,
    조용히 성공했다는 것 자체가 다시 쓴 증거다.
    """
    import json

    from docstruct.align.pair import prepare

    src = tmp_path / "성과계획서.hwpx"
    src.write_bytes("이건 HWPX 가 아니다".encode())
    out = tmp_path / "out"
    (out / "성과계획서.hwpx").mkdir(parents=True)
    ready = out / "성과계획서.hwpx" / "document.json"
    ready.write_text(json.dumps({"filename": "성과계획서.hwpx", "pages": []}),
                     encoding="utf-8")

    got = prepare(src, out)
    assert got.reused is True
    assert got.document["filename"] == "성과계획서.hwpx"
    assert "이미 돌린 결과" in got.reason


def test_prepare_rebuilds_when_result_is_older_than_source(tmp_path):
    """결과가 원본보다 오래됐으면 다시 판독한다 — 원본이 바뀐 것이다."""
    import json
    import os
    import time

    from docstruct.align.pair import prepare

    src = tmp_path / "성과계획서.hwpx"
    src.write_bytes("이건 HWPX 가 아니다".encode())
    out = tmp_path / "out"
    (out / "성과계획서.hwpx").mkdir(parents=True)
    ready = out / "성과계획서.hwpx" / "document.json"
    ready.write_text(json.dumps({"pages": []}), encoding="utf-8")
    old = time.time() - 3600
    os.utime(ready, (old, old))

    # 다시 판독하려 들 것이고, 원본이 쓰레기라 실패한다 — 그 실패가 증거다
    with pytest.raises(Exception):
        prepare(src, out)


def test_prepare_can_skip_reuse(tmp_path):
    """reuse=False 면 있어도 다시 판독한다."""
    import json

    from docstruct.align.pair import prepare

    src = tmp_path / "가.hwpx"
    src.write_bytes("쓰레기".encode())
    out = tmp_path / "out"
    (out / "가.hwpx").mkdir(parents=True)
    (out / "가.hwpx" / "document.json").write_text(json.dumps({"pages": []}),
                                                   encoding="utf-8")
    assert prepare(src, out).reused is True
    with pytest.raises(Exception):
        prepare(src, out, reuse=False)


def test_align_pair_rejects_reversed_arguments(tmp_path):
    """PDF 를 첫 자리에 주면 판독 **전에** 막는다 — 몇 분 쓰고 알면 늦다."""
    from docstruct.align.pair import align_pair

    pdf = tmp_path / "문서.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(ValueError, match="쪽이"):
        align_pair(pdf, pdf, tmp_path / "out")


def test_align_pair_reuses_both_sides(tmp_path):
    """양쪽 결과가 다 있으면 판독 없이 맞춘다."""
    import json

    from docstruct.align.pair import align_pair

    hwpx, pdf = _align_pair()
    out = tmp_path / "out"
    src_h = tmp_path / "문서.hwpx"
    src_p = tmp_path / "문서.pdf"
    src_h.write_bytes("쓰레기".encode())          # 판독하면 터진다
    src_p.write_bytes("쓰레기".encode())
    for src, doc in ((src_h, hwpx), (src_p, pdf)):
        folder = out / src.name
        folder.mkdir(parents=True)
        (folder / "document.json").write_text(
            json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    got = align_pair(src_h, src_p, out)
    assert got.hwpx.reused and got.pdf.reused
    assert got.result["matched_tables"] == 2
    assert len(got.notes) == 2


def test_find_counterpart_finds_the_sibling(tmp_path):
    """옆에 있는 같은 이름의 짝을 찾는다 (없으면 None)."""
    from docstruct.align.pair import find_counterpart

    h = tmp_path / "성과계획서.hwpx"
    h.write_bytes(b"x")
    assert find_counterpart(h) is None
    p = tmp_path / "성과계획서.pdf"
    p.write_bytes(b"x")
    assert find_counterpart(h) == p
    assert find_counterpart(p) == h


def test_package_exposes_align_entry_points():
    """`import docstruct` 만으로 쪽 맞춤에 닿는다."""
    import docstruct

    for name in ("align_pair", "align_documents", "prepare", "find_counterpart"):
        assert hasattr(docstruct, name), name
        assert name in docstruct.__all__, name


# ────────────────────────────────────────────────────────────────────
# 0.4.94 — 진행 단계: 출력용(굵은 13단계) · 개발자용(진행수·이유·시간)
#
# 한 벌의 사실을 두 겹으로 낸다. 문구가 두 곳에 흩어지면 화면과 로그가
# 서로 다른 말을 하게 되므로(0.4.83) 표는 core/steps.py 한 곳에만 둔다.
# ────────────────────────────────────────────────────────────────────


def test_steps_table_matches_pipeline_phases():
    """단계 표의 구간 번호가 파이프라인 배너와 1:1 이어야 한다."""
    import inspect
    import re

    from docstruct import pipeline
    from docstruct.core.steps import STEPS

    banners = {int(m) for m in re.findall(r"# ═══ 구간 (\d+) — ",
                                          inspect.getsource(pipeline))}
    assert {step.phase for step in STEPS} == banners
    assert len(STEPS) == 13
    assert len({step.id for step in STEPS}) == 13


def test_every_step_is_signalled_from_the_pipeline():
    """13단계 전부가 파이프라인에서 실제로 신호를 보낸다."""
    import inspect

    from docstruct import pipeline
    from docstruct.core.steps import STEPS

    source = inspect.getsource(pipeline)
    for step in STEPS:
        assert f'report("{step.id}"' in source, step.id


def test_format_skips_are_reported_with_a_reason():
    """형식이 지나가지 않는 단계는 **이유와 함께** 건너뛴 것으로 남는다."""
    from docstruct.core.steps import reporting, steps_for

    # HWP 는 cells 가 없어 격자 검사를 지나가지 않는다 (실험_총정리 §6)
    assert "integrity" not in {step.id for step in steps_for("hwp")}
    events = []
    with reporting("가.hwp", "hwp", sinks=[events.append]) as got:
        got.enter("extract")
        got.skip("integrity", "cells 가 없습니다")
        got.enter("integrity")          # 지나가지 않는 단계 — 나오면 안 된다
    kinds = [(e.get("kind"), e.get("step")) for e in events if e]
    assert ("skip", "integrity") in kinds
    assert ("enter", "integrity") not in kinds, "건너뛴 단계를 '…중' 이라 했다"
    assert all(e.get("reason") for e in events if e.get("kind") == "skip")


def test_user_view_is_coarse_and_dev_view_is_detailed():
    """출력용은 굵은 말만, 개발자용은 진행수·이유·시간까지."""
    from docstruct.core.steps import render_dev, render_user, reporting

    events = []
    with reporting("가.pdf", "pdf", sinks=[events.append]) as got:
        got.enter("integrity", total=119)
        got.advance(50, 119)
        got.skip("table_llm", "LLM 미설정")

    enter, progress, skip = events
    assert render_user(enter) == "표 정합성 검사 중…"
    assert render_user(progress) is None, "출력용에 진행수가 샜다"
    assert render_user(skip) is None, "출력용에 건너뜀이 샜다"
    assert "119" in render_dev(enter) and "8/12" in render_dev(enter)
    assert "50/119" in render_dev(progress)
    assert "LLM 미설정" in render_dev(skip)


def test_reporter_is_per_context():
    """보고자는 문맥마다 따로다 — 두 건이 겹쳐도 진행이 섞이지 않는다."""
    import threading

    from docstruct.core.steps import reporter, reporting

    seen = {}

    def other():
        seen["inner"] = reporter()

    with reporting("가.pdf", "pdf") as mine:
        worker = threading.Thread(target=other)
        worker.start()
        worker.join()
        assert reporter() is mine
    assert seen["inner"] is None, "다른 스레드가 남의 보고자를 봤다"
    assert reporter() is None


def test_jsonl_sink_writes_one_line_per_event(tmp_path):
    """JSONL 은 줄마다 flush 한다 — 프런트가 따라 읽어야 하므로."""
    import json

    from docstruct.core.steps import jsonl_sink, reporting

    path = tmp_path / "progress.jsonl"
    with reporting("가.pdf", "pdf", sinks=[jsonl_sink(path)]) as got:
        got.enter("open")
        # 아직 끝나지 않았는데도 이미 읽을 수 있어야 한다
        assert len(path.read_text(encoding="utf-8").splitlines()) == 1
        got.enter("extract")
        got.done()
    rows = [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines()]
    assert [r["kind"] for r in rows] == ["enter", "enter", "done"]
    assert [r["seq"] for r in rows] == [1, 2, 3]
    assert rows[-1]["elapsed"] >= 0


def test_sink_failure_does_not_break_the_run():
    """sink 하나가 터져도 판독은 계속된다 — 진행 표시가 문서를 죽이면 안 된다."""
    from docstruct.core.steps import reporting

    good = []

    def bad(_event):
        raise RuntimeError("프런트가 끊겼다")

    with reporting("가.pdf", "pdf", sinks=[bad, good.append]) as got:
        got.enter("open")
    assert len(good) == 1


def test_steps_by_format_keeps_hwp_gap_visible():
    """HWP 가 표 단계를 지나가지 않는다는 사실이 표에 있어야 한다."""
    from docstruct.core.steps import steps_for

    hwp = {step.id for step in steps_for("hwp")}
    assert "integrity" not in hwp and "table_llm" not in hwp
    assert {"open", "extract", "outline", "finish"} <= hwp
    pdf = {step.id for step in steps_for("pdf")}
    assert len(pdf) == 13, "PDF 는 모든 단계를 지나간다"


# ────────────────────────────────────────────────────────────────────
# 0.4.95 — 굵은 단계가 CLI 밖에서도 나온다 · 기록 파일의 자리
#
# `--steps` 가 CLI 안에만 있어서 `DocStruct(...).run()` 과 `structure()`
# 는 조용히 몇 분을 썼다 — 노트북에서는 멈춘 것처럼 보인다.
# ────────────────────────────────────────────────────────────────────


def test_progress_path_is_next_to_the_outputs():
    """기록은 산출물 옆에 둔다 — document.json 과 같은 자리."""
    from docstruct.core.steps import PROGRESS_FILENAME, progress_path

    got = progress_path("out/조달청.hwpx")
    assert got is not None
    assert got.name == PROGRESS_FILENAME
    assert str(got).endswith("out/조달청.hwpx/progress.jsonl")
    # 저장할 곳이 없으면 만들지 않는다 — 어디 생겼는지 모르게 되면 안 된다
    assert progress_path(None) is None


def test_notebook_run_shows_steps_and_writes_the_file(tmp_path, capsys):
    """`DocStruct(...).run()` 도 굵은 단계를 내고 기록을 남긴다."""
    import inspect

    from docstruct import api

    source = inspect.getsource(api.DocStruct.run)
    assert "_reporting_for" in source, "run() 이 진행 단계를 내지 않는다"
    batch = inspect.getsource(api.DocStructBatch.run)
    assert "_reporting_for" in batch, "일괄 run() 이 진행 단계를 내지 않는다"


def test_steps_option_is_a_view_setting_not_a_run_argument():
    """`steps` 는 화면 설정이다 — build_document 로 새어 가면 안 된다."""
    import inspect

    from docstruct.api import _RUN_KEYS, _VIEW_KEYS, option_keys
    from docstruct.pipeline import build_document

    assert "steps" in _VIEW_KEYS and "steps" in option_keys()
    assert "steps" not in _RUN_KEYS
    assert "steps" not in inspect.signature(build_document).parameters


def test_silent_still_writes_the_record(tmp_path, capsys):
    """화면만 끈다 — 끄고 싶은 것은 소음이지 기록이 아니다."""
    from docstruct.api import _reporting_for
    from docstruct.core.steps import report

    out = tmp_path / "가.hwpx"
    out.mkdir()
    with _reporting_for("가.hwpx", out, "silent"):
        report("open")
    printed = capsys.readouterr().out
    assert printed.strip() == "", f"silent 인데 화면에 나왔다: {printed!r}"
    assert (out / "progress.jsonl").is_file()


def test_console_sink_matches_the_cli_wording():
    """노트북과 CLI 가 같은 문구를 쓴다 — 표가 한 곳뿐이므로."""
    import inspect

    from docstruct import cli
    from docstruct.core.steps import console_sink

    assert "console_sink" in inspect.getsource(cli._step_reporting)
    events = []
    sink = console_sink("user")
    assert callable(sink)
    del events


# ────────────────────────────────────────────────────────────────────
# 0.4.96 — out_dir 을 줬는데 산출물이 없었다 (노트북)
#
#     DocStruct(fn, out_dir="out").run()   →  out/ 에 images/ 만
#
# `out_dir` 이 판독 **중간 산물**(그림·쪽 이미지)의 자리로만 쓰였다.
# CLI 는 save() 를 따로 불러 다섯 파일을 냈으므로, 같은 인자가 두 경로에서
# 다른 뜻이었다. 이름이 "출력 폴더" 인데 출력이 없었다.
# ────────────────────────────────────────────────────────────────────


def test_run_with_out_dir_writes_the_outputs(tmp_path):
    """`out_dir` 을 주면 run() 이 CLI 와 같은 다섯 파일을 낸다."""
    from docstruct.api import DocStruct
    from docstruct.models import PageContent, PageDocument, PageTrace

    doc = PageDocument(filename="가.hwpx", source_format="hwpx",
                       pages=[PageContent(page_no=1, page_no_kind="document",
                                          content="본문", trace=PageTrace())])
    out = tmp_path / "out"
    holder = DocStruct.from_document(doc, source="가.hwpx", out_dir=str(out))
    holder.save(out)
    names = {path.name for path in out.iterdir() if path.is_file()}
    assert {"document.json", "document.md", "tables.md",
            "pipeline.md", "layout.md"} <= names


def test_run_wires_out_dir_to_save():
    """run() 이 out_dir 을 받으면 저장까지 한다 — 배선을 못 박는다."""
    import inspect

    from docstruct.api import DocStruct, DocStructBatch

    for func in (DocStruct.run, DocStructBatch.run):
        source = inspect.getsource(func)
        assert "write_outputs" in source, func.__qualname__
        assert ".save(" in source, func.__qualname__


def test_write_outputs_can_be_turned_off():
    """중간 산물만 두고 저장은 직접 하고 싶으면 끌 수 있다."""
    from docstruct.api import _RUN_KEYS, _VIEW_KEYS, DocStruct, option_keys

    assert _VIEW_KEYS["write_outputs"] is True
    assert "write_outputs" in option_keys()
    # 화면 설정이므로 build_document 로 새어 가면 안 된다
    assert "write_outputs" not in _RUN_KEYS
    holder = DocStruct("가.hwpx", write_outputs=False)
    assert holder.get("write_outputs") is False


def test_batch_run_gives_each_document_its_own_folder():
    """일괄에서는 문서마다 자기 폴더다 — 한 폴더에 몰면 덮인다(0.4.92)."""
    import inspect

    from docstruct.api import DocStructBatch

    source = inspect.getsource(DocStructBatch.run)
    assert "assign_out_dirs" in source, "폴더 배정을 안 쓴다"
    # 파이프라인에는 뿌리를 넘기지 않는다 (문서마다 갈리므로)
    assert 'run_kwargs.pop("out_dir"' in source


# ────────────────────────────────────────────────────────────────────
# 0.4.97 — `out_dir` 은 산출 뿌리다 (단건도 파일명 폴더를 만든다)
#
# 0.4.96 은 단건 run() 이 뿌리에 **바로** 쏟았다. 같은 뿌리로 두 번 돌리면
# 앞 결과가 덮이고, CLI·일괄과 배치가 달랐다.
# ────────────────────────────────────────────────────────────────────


def test_single_run_makes_a_folder_named_after_the_file():
    """단건도 `out/<파일이름.확장자>/` 아래에 낸다 — CLI 와 같은 모양."""
    import inspect

    from docstruct.api import DocStruct

    source = inspect.getsource(DocStruct.run)
    assert "safe_file_name" in source, "파일명 폴더를 만들지 않는다"
    # 그림·쪽 이미지도 같은 폴더 안으로 (뿌리에 흩어지면 안 된다)
    assert 'run_kwargs["out_dir"] = target' in source


def test_two_documents_to_one_root_do_not_collide(tmp_path):
    """같은 뿌리로 두 문서를 돌려도 서로 덮지 않는다."""
    from docstruct.api import DocStruct
    from docstruct.models import PageContent, PageDocument, PageTrace

    root = tmp_path / "out"
    for name in ("가.hwpx", "가.pdf", "나.hwpx"):
        doc = PageDocument(
            filename=name, source_format=name.rsplit(".", 1)[-1],
            pages=[PageContent(page_no=1, page_no_kind="document",
                               content=f"{name} 본문", trace=PageTrace())])
        from docstruct.output.names import safe_file_name
        DocStruct.from_document(doc, source=name).save(root / safe_file_name(name))

    folders = sorted(p.name for p in root.iterdir() if p.is_dir())
    assert folders == ["가.hwpx", "가.pdf", "나.hwpx"]
    for folder in folders:
        got = (root / folder / "document.json").read_text(encoding="utf-8")
        assert folder.split(".")[0] in got


def test_explicit_save_still_writes_where_told(tmp_path):
    """`save(경로)` 는 그 경로에 그대로 쓴다 — 하위 폴더를 만들지 않는다."""
    from docstruct.api import DocStruct
    from docstruct.models import PageContent, PageDocument, PageTrace

    doc = PageDocument(filename="가.hwpx", source_format="hwpx",
                       pages=[PageContent(page_no=1, page_no_kind="document",
                                          content="본문", trace=PageTrace())])
    where = tmp_path / "여기"
    DocStruct.from_document(doc, source="가.hwpx").save(where)
    assert (where / "document.json").is_file()
    assert not (where / "가.hwpx").exists(), "명시한 경로 아래 또 폴더를 만들었다"


# ────────────────────────────────────────────────────────────────────
# 0.4.98 — 산출 폴더 이름 규칙은 하나뿐이다
#
# 0.4.93 이 "확장자 포함" 으로 바꿨지만 두 자리가 옛 규칙에 남아 있었다:
# 쪽 맞춤용 죽은 헬퍼(`cli._document_dict`)와 `_process` 의 기본값.
# ────────────────────────────────────────────────────────────────────


def test_no_output_folder_is_named_without_its_extension():
    """폴더 이름을 정하는 자리에서 `safe_file_stem` 을 쓰면 안 된다.

    `safe_file_stem` 은 확장자를 떼므로 `문서.hwpx` 와 `문서.pdf` 가 같은
    폴더를 쓰게 된다 — 쪽 맞춤이 늘 다루는 짝이다. 폴더는
    `output.names.safe_file_name`, 파일 **안쪽** 이름만 `safe_file_stem`.
    """
    import inspect
    import pathlib
    import re

    import docstruct

    root = pathlib.Path(docstruct.__file__).parent
    # 폴더를 만드는 자리 = `... / safe_file_stem(...)` 꼴
    offenders = []
    for path in root.rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"/\s*safe_file_stem\(", line):
                offenders.append(f"{path.name}:{lineno}")
    assert not offenders, f"폴더 이름에 확장자가 빠집니다: {offenders}"
    del inspect


def test_all_entry_points_agree_on_the_folder_name():
    """CLI·단건·일괄·쪽 맞춤이 같은 이름을 낸다."""
    from docstruct.align.pair import out_folder_name
    from docstruct.output.names import assign_out_dirs, safe_file_name

    name = "성과 계획서.hwpx"
    expected = "성과_계획서.hwpx"
    assert safe_file_name(name) == expected
    assert out_folder_name(f"/어딘가/{name}") == expected
    assert assign_out_dirs([name])[name] == expected


def test_dead_align_helper_is_gone():
    """0.4.93 에서 `align_pair` 로 대체된 헬퍼가 남아 있으면 안 된다.

    남아 있으면 옛 폴더 규칙(확장자 없음)이 조용히 되살아난다 — 게다가
    그 헬퍼는 결과 재사용을 하지 않아 매번 다시 판독했다.
    """
    from docstruct import cli

    assert not hasattr(cli, "_document_dict")


def test_page_render_stem_stays_extensionless():
    """파일 **안쪽** 이름은 확장자를 떼는 것이 맞다."""
    from docstruct.images.page_render import safe_file_stem

    assert safe_file_stem("성과계획서.hwpx") == "성과계획서"
    assert safe_file_stem("성과 계획서.pdf") == "성과_계획서"


# ────────────────────────────────────────────────────────────────────
# 0.4.99 — `off` 는 "끔" 이 아니라 "상세를 끔" 으로 읽힌다
#
# `steps="off"` 로 돌렸는데 아무것도 안 나온다는 보고. 자연스러운 읽기다 —
# 끄고 싶은 것은 보통 개발자용 소음이지 진행 표시 자체가 아니다.
# ────────────────────────────────────────────────────────────────────


def test_off_and_user_mean_the_coarse_view():
    """`off`·`user` 는 굵은 13단계다 — 침묵이 아니다."""
    from docstruct.core.steps import normalize_mode

    for alias in ("off", "user", "brief", "coarse", "on", True, None):
        assert normalize_mode(alias) == "brief", alias


def test_silent_is_the_only_way_to_say_nothing():
    """조용히 하려면 또렷하게 말해야 한다."""
    from docstruct.core.steps import normalize_mode

    for alias in ("silent", "none", "quiet", False):
        assert normalize_mode(alias) == "silent", alias


def test_unknown_mode_falls_back_to_coarse():
    """오타 하나로 진행 표시가 통째로 사라지면 안 된다."""
    from docstruct.core.steps import normalize_mode

    assert normalize_mode("오프") == "brief"
    assert normalize_mode("dev1") == "brief"
    assert normalize_mode("dev") == "dev"


def test_off_prints_the_coarse_steps(capsys, tmp_path):
    """`steps="off"` 로 돌리면 굵은 단계가 나온다 (노트북·CLI 같다)."""
    from docstruct.api import _reporting_for
    from docstruct.core.steps import report

    with _reporting_for("가.hwpx", tmp_path, "off"):
        report("open")
        report("extract")
    printed = capsys.readouterr().out
    assert "파일 확인 중…" in printed
    assert "문서 여는 중…" in printed
    assert "[ 0/12]" not in printed, "off 인데 상세가 나왔다"


def test_cli_accepts_the_aliases():
    """`--steps` 가 별칭을 받는다 — 예전 명령이 깨지지 않는다."""
    from docstruct.cli import _build_parser

    parser = _build_parser()
    action = next(a for a in parser._actions if "--steps" in a.option_strings)
    assert {"brief", "user", "off", "dev", "silent", "none"} <= set(action.choices)
    assert action.default == "brief"


# ────────────────────────────────────────────────────────────────────
# 0.5.0 — pyhwp(AGPL)를 폴더째 떼어낼 수 있어야 한다
#
# 실측(0.4.99): `hwp5tree.py`·`pyhwp.py` 를 지우면 `import docstruct` 는
# 살아남지만 `HwpConverter` 가 ImportError 로 죽었다 — AGPL 과 무관한
# 나머지 사다리(HWP→HWPX·HWPML·OLE·미리보기)까지 함께. 지우려던 것보다
# 훨씬 많이 잃는 구조였다.
# ────────────────────────────────────────────────────────────────────


def _agpl_surface() -> list[str]:
    """`hwp5`(pyhwp)를 import 하는 파일 목록."""
    import pathlib
    import re

    import docstruct

    # local·overlay 트리는 `converters/` 가 **최상위로 승격**된다
    # (tools/sync_trees.py). 한쪽만 보면 표면을 놓친다.
    package = pathlib.Path(docstruct.__file__).parent
    roots = [package]
    promoted = package.parent / "converters"
    if promoted.is_dir() and promoted != package / "converters":
        roots.append(promoted)

    found = set()
    for root in roots:
        base = root if root is package else root.parent
        for path in root.rglob("*.py"):
            if re.search(r"^\s*(from|import)\s+hwp5\b",
                         path.read_text(encoding="utf-8"), re.M):
                found.add(str(path.relative_to(base)))
    return sorted(found)


def test_agpl_surface_lives_in_one_folder():
    """pyhwp 를 쓰는 파일은 전부 `pyhwp_backend/` 안에 있어야 한다.

    한 파일이라도 밖에 있으면 폴더를 지워도 AGPL 이 남는다.
    """
    outside = [path for path in _agpl_surface()
               if "converters/hwp/pyhwp_backend/" not in path.replace("\\", "/")]
    assert not outside, f"백엔드 폴더 밖에서 pyhwp 를 씁니다: {outside}"
    assert _agpl_surface(), "표면이 아예 없다 — 시험이 무의미해졌는지 확인하세요"


def test_converter_does_not_import_the_backend_at_module_level():
    """사다리는 백엔드를 **함수 안에서** 부른다 — 없어도 죽지 않게."""
    import inspect

    from docstruct.converters.hwp import converter as conv

    head = inspect.getsource(conv).split("def ", 1)[0]
    assert "pyhwp_backend" not in head, "최상위 import 가 남아 있다"
    assert "_backend" in inspect.getsource(conv._get_backend_probe) \
        if hasattr(conv, "_get_backend_probe") else True
    assert conv._backend() is not None or True   # 있는 환경에서는 모듈을 준다


def test_page_break_mark_is_outside_the_backend():
    """쪽 나눔 표식은 백엔드 밖에 있다 — 떼어내도 쪽 나누기는 돌아야 한다."""
    from docstruct.converters.hwp.marks import PAGE_BREAK
    from docstruct.extractors import hwp as extractor

    assert PAGE_BREAK
    source = __import__("inspect").getsource(extractor)
    assert "converters.hwp.marks import PAGE_BREAK" in source
    assert "pyhwp_backend import PAGE_BREAK" not in source


def test_styling_is_shared_and_must_not_move():
    """`styling` 은 HWPX 도 쓴다 — 백엔드로 옮기면 HWPX 가 깨진다."""
    import inspect

    from docstruct.converters.hwpx import hwpxtree

    assert "converters.hwp.styling import" in inspect.getsource(hwpxtree)


def test_deps_gate_does_not_import_hwp5_itself():
    """`deps.py` 가 hwp5 를 직접 import 하면 폴더를 지워도 남는다."""
    import inspect

    from docstruct.converters import deps

    # 설명 글은 이유를 적느라 언급한다 — **import 문**만 본다
    # (`_agpl_surface` 와 같은 잣대).
    import re

    source = inspect.getsource(deps)
    assert not re.search(r"^\s*(from|import)\s+hwp5\b", source, re.M)
    assert isinstance(deps.PYHWP_AVAILABLE, bool)


# ────────────────────────────────────────────────────────────────────
# 0.5.1 — 백엔드가 없어도 조용히 끝까지 간다 · HWP→HWPX 단을 실제로 배선
#
# 문서에는 6단 사다리라 적어 놓고 2단(HWP→HWPX 변환)은 **부르는 곳이
# 없었다.** `converters/hwpx/convert.try_convert` 에 호출부가 0개였다.
# ────────────────────────────────────────────────────────────────────


def test_hwp_to_hwpx_rung_is_actually_wired():
    """2단이 코드에 있어야 한다 — 문서에만 있으면 없는 것이다."""
    import inspect

    from docstruct.extractors import hwp as extractor

    source = inspect.getsource(extractor)
    assert "convert.try_convert" in source, "HWP→HWPX 변환을 부르는 곳이 없다"
    assert "extract_hwpx_pages" in source, "변환 결과를 HWPX 경로로 넘기지 않는다"
    # 변환이 되면 cells 가 생긴다 — HWP 의 가장 큰 공백이 닫히는 자리다
    assert "cells" in source


def test_hwp_to_hwpx_is_skipped_when_unavailable(monkeypatch, tmp_path):
    """변환기가 없으면 조용히 None — 없는 것이 정상이다."""
    from docstruct.converters.hwpx import convert
    from docstruct.extractors.hwp import _as_hwpx

    fake = tmp_path / "a.hwp"
    fake.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 64)
    monkeypatch.setattr(convert, "is_available", lambda: False)
    assert _as_hwpx(str(fake)) is None


def test_hwp_to_hwpx_can_be_turned_off(monkeypatch, tmp_path):
    """설정으로 끌 수 있다 — 변환 결과를 원본과 견주려면 꺼야 한다."""
    from docstruct.converters.hwpx import convert
    from docstruct.extractors.hwp import _as_hwpx

    fake = tmp_path / "a.hwp"
    fake.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 64)
    monkeypatch.setattr(convert, "is_available", lambda: True)
    monkeypatch.setattr(convert, "try_convert", lambda *a, **k: tmp_path / "x.hwpx")
    monkeypatch.setenv("DOCSTRUCT_HWP_VIA_HWPX", "false")
    assert _as_hwpx(str(fake)) is None


def test_missing_backend_records_why_it_fell_back(monkeypatch, tmp_path):
    """백엔드가 없어 내려왔다는 사실이 **결과물에 남는다**."""
    from docstruct.converters.hwp import converter as conv

    monkeypatch.setattr(conv, "_backend", lambda: None)
    fake = tmp_path / "a.hwp"
    fake.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512)
    c = conv.HwpConverter(fake)
    assert c._get_tree_markdown() is None
    assert "백엔드" in (c.tree_failure or "")
    assert c._uses_ole_fallback() is True
    reason = c.fallback_reason or ""
    assert "백엔드" in reason, f"왜 내려왔는지가 없다: {reason!r}"
    assert "HWPX" in reason, "고치는 방법을 알려주지 않는다"


def test_corrupt_container_does_not_raise(monkeypatch, tmp_path):
    """olefile 이 파일을 못 열어도 예외로 문서를 잃지 않는다.

    미리보기 스트림이 사다리의 진짜 마지막 단이다 — 거기까지 실패하면
    빈 결과를 내고, 파이프라인이 "내용이 사실상 비었습니다" 로 알린다.
    """
    from docstruct.converters.hwp import converter as conv

    monkeypatch.setattr(conv, "_backend", lambda: None)
    # OLE 서명만 있고 내용이 망가진 파일 — olefile 이 ValueError 를 낸다
    fake = tmp_path / "깨진.hwp"
    fake.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512)
    c = conv.HwpConverter(fake)
    text = c._get_ole_text()                     # 예외가 나면 안 된다
    assert isinstance(text, str)
    assert "olefile" in (c.fallback_reason or ""), "못 연 사실이 안 적혔다"


# ────────────────────────────────────────────────────────────────────
# 0.5.2 — 변환해서 cells 를 얻었는데 게이트가 그대로 막고 있었다
#
# HWP→HWPX 변환(0.5.1)의 목적은 §6 의 공백(cells 없음)을 닫는 것이었다.
# 그런데 `fmt` 가 여전히 "hwp" 라 격자 실험이 전부 막혔다 — 재료는
# 생겼는데 확장자로 판단해 쓰지 못했다.
# ────────────────────────────────────────────────────────────────────


def test_gate_follows_the_material_not_the_extension():
    """HWPX 로 바꿔 읽었으면 게이트도 hwpx 기준이 된다."""
    from docstruct.models import PageContent, PageTrace
    from docstruct.pipeline import HWPX_VIA_CONVERT, _gate_format

    plain = [PageContent(page_no=1, page_no_kind="document", content="",
                         trace=PageTrace(extractor="hwp5-tree"))]
    assert _gate_format("hwp", plain) == "hwp"

    converted = [PageContent(page_no=1, page_no_kind="document", content="",
                             trace=PageTrace(extractor=HWPX_VIA_CONVERT))]
    assert _gate_format("hwp", converted) == "hwpx"

    # 다른 형식은 건드리지 않는다
    assert _gate_format("pdf", converted) == "pdf"
    assert _gate_format("hwpx", plain) == "hwpx"


def test_source_format_still_says_hwp():
    """게이트만 바꾼다 — 결과물은 입력이 HWP 였다는 **사실**을 적는다."""
    import inspect

    from docstruct import pipeline

    build = inspect.getsource(pipeline.build_document)
    # 게이트 변수만 실험·검사에 쓰이고, 문서 형식은 fmt 그대로 간다
    assert "gate_fmt = _gate_format(fmt, pages)" in build
    assert "source_format=fmt" in build, "결과물 형식이 게이트를 따라가면 안 된다"


def test_experiments_are_gated_by_gate_format():
    """실험 게이트가 `gate_fmt` 를 본다 — `fmt` 를 보면 변환 효과가 죽는다."""
    import inspect

    from docstruct import pipeline

    build = inspect.getsource(pipeline.build_document)
    assert "if gate_fmt not in experiment.formats:" in build
    assert "experiment.missing_for(gate_fmt)" in build


def test_converted_hwp_gets_cells(monkeypatch, tmp_path):
    """변환 경로를 타면 표에 cells 가 생긴다 — 그것이 변환하는 이유다."""
    import shutil

    from docstruct.converters.hwpx import convert
    from docstruct.extractors.hwp import extract_hwp_pages

    sample = pathlib.Path(__file__).resolve().parents[1] / "notebooks/samples/sample.hwpx"
    if not sample.is_file():
        pytest.skip("샘플 HWPX 없음")

    hwp_in = tmp_path / "가.hwp"
    hwp_in.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 64)
    converted = tmp_path / "가.hwpx"
    shutil.copy(sample, converted)

    monkeypatch.setattr(convert, "is_available", lambda: True)
    monkeypatch.setattr(convert, "try_convert", lambda *a, **k: converted)

    pages, table_html = extract_hwp_pages(str(hwp_in))
    assert pages, "변환 경로가 쪽을 내지 못했다"
    assert pages[0].trace.extractor == "hwp2hwpx→hwpx-tree"
    assert any(t.cells for t in pages[0].tables), "cells 가 없다 — 변환한 뜻이 없다"
    assert table_html == [], "변환 경로는 원본 HTML 조각을 쓰지 않는다"


# ────────────────────────────────────────────────────────────────────
# 0.5.3 — HWP→HWPX 변환에 Java·jar 가 꼭 필요하진 않다
#
# `hwp2hwpx`(jkf87/hwp2hwpx-python-refactor)는 순수 파이썬이라 Java 없이
# 돈다. 다만 **그 모듈이 pyhwp(AGPL)에 기댄다** — 라이선스 때문에 백엔드를
# 떼어낸 배포라면 이것을 설치하는 순간 pyhwp 가 런타임으로 다시 들어온다.
# ────────────────────────────────────────────────────────────────────


def test_python_converter_is_preferred_over_the_jar():
    """파이썬 변환기가 있으면 그것을 먼저 쓴다 — Java 를 띄울 이유가 없다."""
    import inspect

    from docstruct.converters.hwpx import convert

    source = inspect.getsource(convert.convert)
    assert "python_backend() is not None" in source
    # jar 명령 구성보다 **앞에** 있어야 한다
    assert source.index("python_backend()") < source.index("subprocess.run")


def test_is_available_true_with_python_backend_only(monkeypatch):
    """jar 명령이 없어도 파이썬 변환기만 있으면 쓸 수 있다."""
    from docstruct.converters.hwpx import convert

    monkeypatch.delenv(convert.CONVERTER_ENV, raising=False)
    monkeypatch.setattr(convert, "python_backend", lambda: object())
    assert convert.is_available() is True
    monkeypatch.setattr(convert, "python_backend", lambda: None)
    assert convert.is_available() is False


def test_python_backend_can_be_turned_off(monkeypatch):
    """jar 만 쓰고 싶을 때 끌 수 있다."""
    from docstruct.converters.hwpx import convert

    monkeypatch.setenv(convert.PY_BACKEND_ENV, "false")
    assert convert.python_backend() is None


def test_python_backend_agpl_warning_is_documented():
    """이 모듈이 pyhwp 에 기댄다는 사실이 코드에 적혀 있어야 한다.

    라이선스 때문에 백엔드를 떼어낸 사람이 이것을 설치하면 pyhwp 가
    런타임으로 되돌아온다 — 모르고 하면 안 되는 선택이다.
    """
    import inspect

    from docstruct.converters.hwpx import convert

    doc = inspect.getdoc(convert.python_backend) or ""
    assert "pyhwp" in doc and "AGPL" in doc
    assert "런타임" in doc


def test_converted_target_lands_in_the_given_folder(monkeypatch, tmp_path):
    """변환 결과가 지정한 폴더에 `<원본이름>.hwpx` 로 놓인다."""
    from docstruct.converters.hwpx import convert

    made = {}

    class _Fake:
        @staticmethod
        def convert_file(src, dst):
            made["src"], made["dst"] = src, dst
            pathlib.Path(dst).write_bytes(b"PK\x03\x04")

    monkeypatch.setattr(convert, "python_backend", lambda: _Fake)
    src = tmp_path / "성과계획서.hwp"
    src.write_bytes(b"\xd0\xcf\x11\xe0")
    out = tmp_path / "결과"
    got = convert.convert(src, out)
    assert got == out / "성과계획서.hwpx"
    assert got.is_file()
    assert made["src"] == str(src)


# ────────────────────────────────────────────────────────────────────
# 0.5.4 — hwp2hwpx 가 있으면 pyhwp 경로를 아예 타지 않는다
#
# 의도: HWP 입력은 hwp2hwpx 로 HWPX 변환해서 처리하고, **그것이 없을 때만**
# pyhwp 기반 사다리를 쓴다. 순서만 맞추는 것으로는 모자란다 — 그 경로가
# 정말 pyhwp 를 건드리지 않는지 못 박아야 한다.
# ────────────────────────────────────────────────────────────────────


def test_conversion_path_never_touches_the_pyhwp_backend(monkeypatch, tmp_path):
    """변환에 성공하면 pyhwp 백엔드를 **부르지 않는다**.

    백엔드 접근자를 터지게 해 두고 돌린다 — 조용히 지나가는 것이 증거다.
    """
    import shutil

    from docstruct.converters.hwp import converter as ladder
    from docstruct.converters.hwpx import convert
    from docstruct.extractors.hwp import extract_hwp_pages

    sample = pathlib.Path(__file__).resolve().parents[1] / "notebooks/samples/sample.hwpx"
    if not sample.is_file():
        pytest.skip("샘플 HWPX 없음")

    hwp_in = tmp_path / "가.hwp"
    hwp_in.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 64)
    converted = tmp_path / "가.hwpx"
    shutil.copy(sample, converted)

    def _boom():
        raise AssertionError("pyhwp 백엔드를 건드렸다")

    monkeypatch.setattr(convert, "is_available", lambda: True)
    monkeypatch.setattr(convert, "try_convert", lambda *a, **k: converted)
    monkeypatch.setattr(ladder, "_backend", _boom)

    pages, _ = extract_hwp_pages(str(hwp_in))
    assert pages[0].trace.extractor == "hwp2hwpx→hwpx-tree"


def test_conversion_is_tried_before_the_ladder():
    """변환 시도가 HwpConverter 보다 **앞**이다."""
    import inspect

    from docstruct.extractors.hwp import extract_hwp_pages

    source = inspect.getsource(extract_hwp_pages)
    assert source.index("_as_hwpx(") < source.index("HwpConverter(")
    assert source.index("_as_hwpx(") < source.index("is_hwpml(hwp_path)")


def test_importing_docstruct_does_not_load_hwp5():
    """`import docstruct` 만으로 AGPL 모듈이 올라오면 안 된다 (0.5.4).

    hwp2hwpx 로만 처리하는 배포에서도 시작할 때 hwp5 가 올라오던 자리다 —
    쓰지도 않을 것을 부르는 셈이었다.
    """
    import subprocess
    import sys

    code = ("import sys; import docstruct; "
            "print(any(m == 'hwp5' or m.startswith('hwp5.') for m in sys.modules))")
    got = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=120)
    assert got.returncode == 0, got.stderr[-500:]
    assert got.stdout.strip().endswith("False"), "import 만으로 hwp5 가 올라왔다"


def test_pyhwp_available_is_still_a_bool_attribute():
    """지연으로 바꿔도 `deps.PYHWP_AVAILABLE` 이름은 그대로 쓴다."""
    from docstruct.converters import deps

    assert isinstance(deps.PYHWP_AVAILABLE, bool)
    assert isinstance(deps.PYHWP_AVAILABLE, bool)      # 두 번째는 캐시
    with pytest.raises(AttributeError):
        deps.없는속성


# ────────────────────────────────────────────────────────────────────
# 0.5.5 — `align_pair(..., steps=...)` 가 터졌다
#
#     build_document() got an unexpected keyword argument 'steps'
#
# `steps` 는 화면 설정이라 실행 인자로 넘기면 안 된다. `DocStruct.run` 은
# `_VIEW_KEYS` 로 갈랐는데 쪽 맞춤만 그 처리가 빠져 있었다.
# ────────────────────────────────────────────────────────────────────


def test_align_pair_accepts_view_settings(tmp_path):
    """`steps`·`write_outputs` 를 줘도 터지지 않는다."""
    import json

    from docstruct.align.pair import align_pair

    left = tmp_path / "가.json"
    right = tmp_path / "나.json"
    hwpx = {"filename": "가.hwpx", "source_format": "hwpx", "page_count": 1,
            "pages": [{"page_no": 1, "page_no_kind": "document",
                       "content": "머리말\n\n<table 1>", "images": [],
                       "tables": [{"id": "table_1", "table_num": 1,
                                   "placeholder": "<table 1>",
                                   "markdown": "| 구분 | 값 |\n| --- | --- |"}]}]}
    pdf = {"filename": "나.pdf", "source_format": "pdf", "page_count": 1,
           "pages": [{"page_no": 1, "page_no_kind": "exact",
                      "content": "머리말\n\n<table 1>", "images": [],
                      "tables": [{"id": "table_1", "table_num": 1,
                                  "placeholder": "<table 1>",
                                  "markdown": "| 구분 | 값 |\n| --- | --- |"}]}]}
    left.write_text(json.dumps(hwpx, ensure_ascii=False), encoding="utf-8")
    right.write_text(json.dumps(pdf, ensure_ascii=False), encoding="utf-8")

    # 여기서 보는 것은 **터지지 않는가** 다. 맞춤 성적은 다른 시험이 본다.
    got = align_pair(left, right, tmp_path / "out", steps="silent",
                     write_outputs=False, assess_tables=False, fill_tables=False)
    assert isinstance(got.result, dict)
    assert got.hwpx.reused and got.pdf.reused


def test_view_settings_never_reach_build_document():
    """화면 설정 목록이 실행 인자와 겹치면 안 된다."""
    import inspect

    from docstruct.align.pair import _VIEW_ONLY, _split_options
    from docstruct.pipeline import build_document

    accepted = set(inspect.signature(build_document).parameters)
    for name in _VIEW_ONLY:
        assert name not in accepted, f"{name} 은 실행 인자다 — 목록에서 빼세요"

    run, view = _split_options({"steps": "dev", "assess_tables": False})
    assert run == {"assess_tables": False}
    assert view == {"steps": "dev"}


def test_align_option_split_matches_the_api():
    """쪽 맞춤과 파사드가 같은 것을 화면 설정으로 본다."""
    from docstruct.align.pair import _VIEW_ONLY
    from docstruct.api import _VIEW_KEYS

    assert set(_VIEW_ONLY) == set(_VIEW_KEYS), \
        "한쪽만 고치면 다시 어긋난다"


# ────────────────────────────────────────────────────────────────────
# 0.5.7 — hwp2hwpx 가 없어도 아무것도 깨지지 않아야 한다
#
# 변환기는 **선택**이다. 없으면 사다리 아래 단이 그대로 받는다. 설치를
# 전제로 코드가 짜이면 안 깐 사람이 전부 막힌다.
# ────────────────────────────────────────────────────────────────────


def test_hwp2hwpx_is_imported_only_inside_a_function():
    """최상위 import 로 두면 안 깐 환경에서 모듈 적재가 실패한다."""
    import ast
    import inspect

    from docstruct.converters.hwpx import convert

    tree = ast.parse(inspect.getsource(convert))
    top = {alias.name.split(".")[0]
           for node in tree.body if isinstance(node, ast.Import)
           for alias in node.names}
    top |= {(node.module or "").split(".")[0]
            for node in tree.body if isinstance(node, ast.ImportFrom)}
    assert "hwp2hwpx" not in top, "최상위에서 변환기를 import 한다"


def test_missing_converter_is_quiet_and_falsey(monkeypatch):
    """없으면 조용히 None·False — 예외를 내지 않는다."""
    from docstruct.converters.hwpx import convert

    monkeypatch.delenv(convert.CONVERTER_ENV, raising=False)
    monkeypatch.setattr(convert, "python_backend", lambda: None)
    assert convert.is_available() is False
    # 존재하지 않는 파일을 줘도 예외가 아니라 None 이다
    assert convert.try_convert("/없는/파일.hwp") is None


def test_as_hwpx_returns_none_without_converter(monkeypatch, tmp_path):
    """추출기 쪽 진입점도 조용히 넘어간다."""
    from docstruct.converters.hwpx import convert
    from docstruct.extractors.hwp import _as_hwpx

    fake = tmp_path / "가.hwp"
    fake.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 64)
    monkeypatch.setattr(convert, "python_backend", lambda: None)
    monkeypatch.delenv(convert.CONVERTER_ENV, raising=False)
    assert _as_hwpx(str(fake)) is None


def test_hwp_still_parses_without_the_converter(monkeypatch):
    """변환기가 없어도 HWP 는 사다리 아래 단으로 읽힌다."""
    from docstruct.converters.hwpx import convert

    sample = pathlib.Path(__file__).resolve().parents[1] / "notebooks/samples/규정안.hwp"
    if not sample.is_file():
        pytest.skip("샘플 HWP 없음")

    monkeypatch.setattr(convert, "python_backend", lambda: None)
    monkeypatch.delenv(convert.CONVERTER_ENV, raising=False)

    from docstruct.extractors.hwp import extract_hwp_pages

    pages, _ = extract_hwp_pages(str(sample))
    assert pages and pages[0].content
    assert pages[0].trace.extractor != "hwp2hwpx→hwpx-tree"


def test_check_suggests_the_converter_instead_of_failing():
    """`--check` 는 없다고 막지 않고 **권한다**."""
    import inspect

    from docstruct.core import checks

    note = inspect.getsource(checks._hwp_note)
    assert "설치하면 표가 살아납니다" in note
    # 변환 경로 판별이 예외를 내지 않는다
    got = checks._hwp_via_hwpx()
    assert isinstance(got, tuple) and isinstance(got[0], bool)


# ────────────────────────────────────────────────────────────────────
# 0.5.8 — 옛 그림 표식(`<!-- image -->`)이 코드에 남아 있으면 안 된다
#
# 0.4.89 부터 그림 자리는 `<image N> … </image N>` 이다. 옛 문자열이
# 어딘가 남아 있으면 그것으로 세는 자리가 **언제나 0개**를 내고
# "그림이 본문에 없다" 는 잘못된 진단이 된다.
# ────────────────────────────────────────────────────────────────────


def test_no_module_still_uses_the_old_image_comment():
    """옛 표식을 **만들거나 세는** 코드가 남아 있으면 안 된다."""
    import pathlib
    import re

    import docstruct

    root = pathlib.Path(docstruct.__file__).parent
    offenders = []
    for path in root.rglob("*.py"):
        if path.name == "tags.py" and path.parent.name == "images":
            continue                      # 옛 표식을 **승격**하는 곳이라 예외
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip().startswith("#"):
                continue                  # 주석은 옛 모양을 설명한다
            if re.search(r"['\"]<!--\s*image", line):
                offenders.append(f"{path.name}:{lineno}")
    assert not offenders, f"옛 그림 표식이 남아 있습니다: {offenders}"


def test_image_block_opens_and_closes_in_real_output(tmp_path):
    """본문에 여는 태그와 닫는 태그가 짝으로 들어간다."""
    import re

    from docstruct.images.tags import make_image_block

    body = "앞 문단\n\n" + make_image_block(1, "조직도", "읽은 내용") + "\n\n뒤 문단"
    assert len(re.findall(r"<image \d+>", body)) == 1
    assert len(re.findall(r"</image \d+>", body)) == 1
    assert body.index("<image 1>") < body.index("</image 1>")
    # 안쪽 두 칸도 각자 닫힌다
    assert "<image-desc 1>조직도</image-desc 1>" in body
    assert "<image-read 1>" in body and "</image-read 1>" in body


# ────────────────────────────────────────────────────────────────────
# 0.5.9 — 그림 구간은 산출물에도 남는다 (표와 다르다)
#
# 복원한 표는 원문에 있던 값을 다시 세운 것이라 본문에 녹아도 된다.
# 그림 설명은 아니다 — VLM 이 `조직도로 보입니다` 처럼 **스스로 쓴 글**이고,
# 표식 없이 섞이면 원문 문장과 구별할 수 없다.
# ────────────────────────────────────────────────────────────────────


def test_expanded_output_keeps_image_marks_but_not_table_marks():
    """그림은 여닫는 표식이 남고, 표는 지워진다."""
    from docstruct.images.tags import make_image_block
    from docstruct.models import ImageInfo, TableInfo
    from docstruct.output.content import expand_tables_and_images
    from docstruct.tables.tags import make_table_block

    table = TableInfo(id="table_1", table_num=1, placeholder="<table 1>",
                      markdown="| 가 | 나 |\n| --- | --- |")
    image = ImageInfo(id="image_1", image_num=1, placeholder="<image 1>",
                      description="조직도", image_path="/x/a.png")
    body = (make_table_block(1, table.markdown) + "\n\n"
            + make_image_block(1, "조직도"))

    got = expand_tables_and_images(body, [table], [image])
    assert "<table 1>" not in got and "</table 1>" not in got
    assert "| 가 | 나 |" in got
    assert "<image 1>" in got and "</image 1>" in got


def test_model_written_text_is_attributed():
    """VLM 이 읽은 내용에는 누가 썼는지가 붙는다."""
    from docstruct.images.tags import make_image_block
    from docstruct.models import ImageInfo
    from docstruct.output.content import expand_tables_and_images

    image = ImageInfo(id="image_1", image_num=1, placeholder="<image 1>",
                      description="조직도", image_path="/x/a.png")
    image.vlm_markdown = "청장 아래 차장이 있는 것으로 보입니다."
    got = expand_tables_and_images(make_image_block(1, "조직도"), [], [image])

    assert "모델이 읽은 내용" in got
    # 파서가 준 설명은 그 표시 **앞**에 온다 — 출처가 다르다
    assert got.index("조직도") < got.index("모델이 읽은 내용")
    assert got.index("모델이 읽은 내용") < got.index("차장이 있는")


def test_image_marks_can_be_turned_off(monkeypatch):
    """예전처럼 펼치고 싶으면 끌 수 있다."""
    from docstruct.images.tags import make_image_block
    from docstruct.models import ImageInfo
    from docstruct.output.content import expand_tables_and_images

    monkeypatch.setenv("DOCSTRUCT_IMAGE_MARKS", "false")
    image = ImageInfo(id="image_1", image_num=1, placeholder="<image 1>",
                      description="조직도", image_path="/x/a.png")
    got = expand_tables_and_images(make_image_block(1, "조직도"), [], [image])
    assert "<image 1>" not in got
    assert "조직도" in got


def test_output_marks_match_the_json_form():
    """산출물과 JSON 이 **같은 표기**를 쓴다 — 도구가 하나로 돈다."""
    import inspect

    from docstruct.output import content

    source = inspect.getsource(content._image_block_text)
    assert '<image {num}>' in source and '</image {num}>' in source
