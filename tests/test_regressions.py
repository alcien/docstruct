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
import json
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

    from docstruct import checks

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
    from docstruct import checks

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
    import pathlib

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
    from docstruct.converters.hwp.pyhwp import pyhwp_html_verdict

    insufficient, reason = pyhwp_html_verdict(
        _rich_html(), "unmatched field end", 626_176
    )
    assert insufficient is False
    assert "표가 살아 있음" in reason


def test_field_warning_with_empty_body_triggers_fallback():
    """필드 경고 + 빈 결과는 그대로 폴백한다 (원래 잡으려던 케이스)."""
    from docstruct.converters.hwp.pyhwp import pyhwp_html_verdict

    insufficient, reason = pyhwp_html_verdict(
        "<html><body></body></html>", "unmatched field end", 626_176
    )
    assert insufficient is True
    assert reason


def test_empty_result_without_warning_still_falls_back():
    """경고가 없어도 결과가 비면 폴백한다."""
    from docstruct.converters.hwp.pyhwp import pyhwp_html_verdict

    insufficient, _ = pyhwp_html_verdict("<html><body></body></html>", "", 626_176)
    assert insufficient is True


def test_mostly_empty_cells_trigger_fallback():
    """셀이 대부분 비면 폴백한다."""
    from docstruct.converters.hwp.pyhwp import pyhwp_html_verdict

    rows = "<tr>" + "<td></td>" * 20 + "</tr>"
    html = f"<html><body><p>{'가' * 600}</p><table>{rows}</table></body></html>"
    insufficient, reason = pyhwp_html_verdict(html, "", 626_176)
    assert insufficient is True
    assert "내용 있는 셀" in reason


def test_small_file_short_body_is_not_fallback():
    """작은 파일은 본문이 짧아도 정상으로 본다."""
    from docstruct.converters.hwp.pyhwp import pyhwp_html_verdict

    insufficient, _ = pyhwp_html_verdict("<html><body>짧음</body></html>", "", 5_000)
    assert insufficient is False


def test_verdict_always_gives_reason():
    """어느 경로로 가든 사유 문구가 비지 않는다 (진단용)."""
    from docstruct.converters.hwp.pyhwp import pyhwp_html_verdict

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

    from docstruct import colab

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
    from docstruct.converters.korean_text import collapse_even_spacing

    assert collapse_even_spacing(src) == want


def test_even_spacing_needs_three_tokens():
    """토큰이 셋 미만이면 균등배분으로 보지 않는다."""
    from docstruct.converters.korean_text import collapse_even_spacing

    assert collapse_even_spacing("가 나") == "가 나"
    assert collapse_even_spacing("가 나 다") == "가나다"


def test_pua_mapping():
    """한컴 PUA 글머리표가 표준 유니코드로 바뀐다."""
    from docstruct.converters.korean_text import map_pua

    assert map_pua("\uf06f 항목") == "□ 항목"
    assert map_pua("\uf0a2 하위") == "○ 하위"
    assert map_pua("\uf0fc 완료") == "✔ 완료"
    assert map_pua("\U000f0854인용\U000f0855") == "《인용》"


def test_pua_keeps_unmapped_characters():
    """매핑에 없는 PUA 는 지우지 않는다 (옛한글 보호)."""
    from docstruct.converters.korean_text import map_pua

    assert map_pua("\ue000옛한글") == "\ue000옛한글"
    assert map_pua("\uf001x") == "\uf001x"


def test_normalize_applies_per_line():
    """균등배분은 줄 단위로 판단한다."""
    from docstruct.converters.korean_text import normalize_korean_text

    out = normalize_korean_text("대 한 민 국 정 부\n중동 사태 대응")
    assert out.splitlines() == ["대한민국정부", "중동 사태 대응"]


def test_normalize_can_skip_collapse():
    """짧은 표 셀에는 균등배분 복원을 끌 수 있다."""
    from docstruct.converters.korean_text import normalize_korean_text

    assert normalize_korean_text("가 나 다", collapse=False) == "가 나 다"


# ────────────────────────────────────────────────────────────────────
# HWP 파서 트리 경로 (hwp5.xmlmodel)
# ────────────────────────────────────────────────────────────────────

def _ok_diagnosis():
    """진단을 통과시키는 결과 (가짜 파일로 경로 선택만 시험할 때)."""
    from docstruct.converters.hwp.diagnose import HwpDiagnosis

    return HwpDiagnosis(True, "")


def test_render_table_merges_are_not_duplicated():
    """병합 셀은 왼쪽 위에만 값을 넣고 나머지는 비운다."""
    from docstruct.converters.hwp.hwp5tree import _Cell, _Table, _render_table

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
    from docstruct.converters.hwp.hwp5tree import _Cell, _Table, _render_table

    md = _render_table(_Table(cols=1, cells=[_Cell(col=0, row=0, blocks=["a|b"])]))
    assert r"a\|b" in md


def test_nested_table_uses_marker_not_inline():
    """중첩 표는 표식만 셀에 남기고 본체는 부모 표 뒤에 둔다.

    GFM 은 셀 안에 표를 담지 못한다. 그대로 넣으면 한 줄로 눕고 `|` 가
    이스케이프되어 사람도 LLM 도 읽을 수 없다.
    """
    from docstruct.converters.hwp.hwp5tree import NESTED_MARKER

    assert "{n}" in NESTED_MARKER
    assert NESTED_MARKER.format(n=1) == "[중첩표 1]"


def test_hwp5tree_availability_probe():
    """pyhwp 파서 모듈 유무를 안전하게 확인한다."""
    from docstruct.converters.hwp.hwp5tree import is_available

    assert isinstance(is_available(), bool)


def test_converter_prefers_tree_path(monkeypatch, tmp_path):
    """파서 트리가 결과를 내면 그 경로를 쓴다."""
    from docstruct.converters.hwp import converter as conv

    fake = tmp_path / "a.hwp"
    fake.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 64)

    monkeypatch.setattr(conv, "is_hwpml", lambda _p: False)
    monkeypatch.setattr(conv, "diagnose", lambda _p: _ok_diagnosis())
    monkeypatch.setattr(conv.hwp5tree, "is_available", lambda: True)
    monkeypatch.setattr(conv.hwp5tree, "to_markdown", lambda _p: "가" * 500)

    c = conv.HwpConverter(fake)
    assert c.extraction_path() == "hwp5-tree"
    assert c.to_markdown() == "가" * 500
    assert c.table_html_fragments() == []        # 트리 경로엔 원본 HTML 이 없다


def test_converter_falls_back_when_tree_is_empty(monkeypatch, tmp_path):
    """파서 트리 결과가 빈약하면 기존 경로로 넘어간다."""
    from docstruct.converters.hwp import converter as conv

    fake = tmp_path / "b.hwp"
    fake.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 64)

    monkeypatch.setattr(conv, "is_hwpml", lambda _p: False)
    monkeypatch.setattr(conv, "diagnose", lambda _p: _ok_diagnosis())
    monkeypatch.setattr(conv.hwp5tree, "is_available", lambda: True)
    monkeypatch.setattr(conv.hwp5tree, "to_markdown", lambda _p: "짧음")
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
    monkeypatch.setattr(conv.hwp5tree, "is_available", lambda: True)
    monkeypatch.setattr(conv.hwp5tree, "to_markdown", boom)
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
    from docstruct.converters.korean_text import collapse_vertical_text

    assert collapse_vertical_text(src) == want


def test_normalize_handles_vertical_then_even_spacing():
    """세로쓰기와 균등배분이 한 번에 처리된다."""
    from docstruct.converters.korean_text import normalize_korean_text

    out = normalize_korean_text("프\n로\n그\n램\n대 한 민 국 정 부")
    assert out == "프로그램\n대한민국정부"


def test_vertical_collapse_survives_markdown_tables():
    """markdown 표 행은 낱글자 줄로 오해하지 않는다."""
    from docstruct.converters.korean_text import collapse_vertical_text

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
    from PIL import Image

    from docstruct.models import ImageInfo

    path = tmp_path / f"{kwargs.get('id', 'image_1')}.png"
    Image.new("RGB", (400, 300), "white").save(path)
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
    from docstruct.media.vlm_read import _should_read

    assert _should_read(_picture(tmp_path)) is True
    assert _should_read(_picture(tmp_path, id="i2", region_kind="table")) is False
    assert _should_read(_picture(tmp_path, id="i3", region_kind="text")) is False
    assert _should_read(_picture(tmp_path, id="i4",
                                 bbox={"l": 60, "t": 100, "r": 110, "b": 130})) is False
    assert _should_read(_picture(tmp_path, id="i5", image_path=None)) is False
    assert _should_read(_picture(tmp_path, id="i6", vlm_markdown="이미 읽음")) is False


def test_vlm_read_inserts_after_placeholder(tmp_path, monkeypatch):
    """복원한 내용이 그림 placeholder 바로 뒤에 들어가고 그림은 남는다."""
    from docstruct.media import vlm_read
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
    from docstruct.media import vlm_read
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
    from docstruct.media import vlm_read
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
    from docstruct.media import vlm_read
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
    from docstruct.converters.hwp.hwp5tree import _Counter

    counter = _Counter()
    assert [counter.next() for _ in range(3)] == [1, 2, 3]


def test_split_by_page_break_keeps_table_numbers():
    """쪽으로 갈라도 표 번호는 통번호를 유지한다."""
    from docstruct.converters.hwp.hwp5tree import PAGE_BREAK
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
    from docstruct.converters.hwp.hwp5tree import PAGE_BREAK

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
    from docstruct.extractors.hwpx import _rich_markdown

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
    from docstruct.extractors.hwpx import _rich_markdown

    class _Doc:
        def export_rich_markdown(self):
            return "OLD"

    assert _rich_markdown(_Doc()) == "OLD"


# ────────────────────────────────────────────────────────────────────
# 0.1.62 — HWP 표 열 수 과소 선언
# ────────────────────────────────────────────────────────────────────

def test_render_table_keeps_cells_beyond_declared_cols():
    """TableBody.cols 가 실제보다 작아도 범위 밖 셀이 버려지지 않는다."""
    from docstruct.converters.hwp.hwp5tree import _Cell, _Table, _render_table

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
    from docstruct.converters.hwp.hwp5tree import _close_quietly

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
    ("docstruct.colab", "check_llm_reachable"),
    ("docstruct.report", "IMAGE"),
    ("docstruct.report", "TABLE"),
    ("docstruct.report", "TEXT"),
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
    from docstruct import checks, colab

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
        "colab": "docstruct.colab",
        "checks": "docstruct.checks",
        "preview": "docstruct.preview",
        "nbui": "docstruct.nbui",
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
_DOC_SKIP_FILES = {"BUGFIXES.md", "RESTRUCTURE.md"}


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
        """모듈이거나 부모 모듈의 속성이면 True."""
        try:
            importlib.import_module(path)
            return True
        except ImportError:
            pass
        parent, _, attr = path.rpartition(".")
        if not parent:
            return False
        try:
            return hasattr(importlib.import_module(parent), attr)
        except ImportError:
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

    src = root / "src"
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
    from docstruct.converters.korean_text import map_pua

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
    from docstruct.converters.hwp.hwp5tree import _quiet_warnings

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

    from docstruct.converters.hwp.hwp5tree import _NoiseCounter

    counter = _NoiseCounter()
    record = logging.LogRecord(
        "hwp5.xmlmodel", logging.WARNING, "", 0,
        "섹션을 읽지 못했습니다", None, None)
    assert counter.filter(record) is True
    assert counter.total == 0


def test_enum_dump_line_is_dropped_without_counting():
    """`defined name/values:` 덤프는 앞 줄에 딸린 것이라 세지 않는다."""
    import logging

    from docstruct.converters.hwp.hwp5tree import _NoiseCounter

    counter = _NoiseCounter()
    record = logging.LogRecord(
        "hwp5.dataio", logging.WARNING, "", 0,
        "defined name/values: {'SOLID': 0, 'DASHED': 1}", None, None)
    assert counter.filter(record) is False
    assert counter.total == 0


def test_verbose_env_disables_suppression():
    """DOCSTRUCT_PYHWP_VERBOSE=true 면 계수기를 달지 않는다."""
    import logging

    from docstruct.converters.hwp.hwp5tree import _quiet_warnings

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

    from docstruct.converters.hwp import hwp5tree

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


def test_hwp5html_failure_falls_back_to_olefile():
    """hwp5html 이 실패하면 예외를 내지 않고 olefile 폴백으로 내려간다."""
    from docstruct.converters.hwp import converter as conv

    c = _bare_converter()
    orig_html, orig_ishwpml = conv.hwp_to_html_str, conv.is_hwpml

    def _boom(_path):
        raise RuntimeError(f"hwp5html 실패 (종료코드 1):\n{_PYHWP_NOISE}")

    conv.hwp_to_html_str = _boom
    conv.is_hwpml = lambda _p: False
    try:
        assert c._uses_ole_fallback() is True
        assert "hwp5html" in (c.fallback_reason or "")
    finally:
        conv.hwp_to_html_str, conv.is_hwpml = orig_html, orig_ishwpml


def test_error_message_filters_pyhwp_noise():
    """오류 메시지에 pyhwp 상시 경고가 실패 사유로 실리지 않는다."""
    from docstruct.converters.hwp.pyhwp import real_error_lines

    assert real_error_lines(_PYHWP_NOISE) == []

    mixed = _PYHWP_NOISE + "\nKeyError: 42"
    assert real_error_lines(mixed) == ["KeyError: 42"]


def test_error_message_says_so_when_only_noise():
    """경고밖에 없으면 원인을 모른다는 사실을 밝힌다.

    경고를 원인인 양 보여주면 `undefined UnderlineStyle value: 15` 를
    실패 사유로 읽게 된다 — 실제로 그렇게 읽혔다.
    """
    from docstruct.converters.hwp.pyhwp import _describe_failure

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

def test_tree_failure_is_recorded(caplog):
    """기본 경로 실패가 WARNING 으로 남고 사유가 보존된다."""
    import logging

    from docstruct.converters.hwp import converter as conv
    from docstruct.converters.hwp import hwp5tree

    c = _bare_converter()
    c._tree_failure = None
    original = hwp5tree.to_markdown

    def _boom(_path):
        raise KeyError("HWPTAG_LIST_HEADER: 알 수 없는 레코드")

    hwp5tree.to_markdown = _boom
    try:
        with caplog.at_level(logging.WARNING, logger=conv.__name__):
            assert c._get_tree_markdown() is None
        assert "HWPTAG_LIST_HEADER" in (c.tree_failure or "")
        assert any("hwp5-tree" in r.message for r in caplog.records)
    finally:
        hwp5tree.to_markdown = original


def test_short_tree_result_records_reason():
    """파싱은 됐으나 내용이 없는 경우도 사유가 남는다."""
    from docstruct.converters.hwp import hwp5tree

    c = _bare_converter()
    c._tree_failure = None
    original = hwp5tree.to_markdown
    hwp5tree.to_markdown = lambda _p: "짧음"
    try:
        assert c._get_tree_markdown() is None
        assert "자뿐" in (c.tree_failure or "")
    finally:
        hwp5tree.to_markdown = original


def test_fallback_reason_includes_first_failure():
    """폴백 사유에 먼저 죽은 기본 경로의 사유가 함께 실린다."""
    from docstruct.converters.hwp import converter as conv
    from docstruct.converters.hwp import hwp5tree

    c = _bare_converter()
    c._tree_failure = None
    o1, o2, o3 = hwp5tree.to_markdown, conv.hwp_to_html_str, conv.is_hwpml

    hwp5tree.to_markdown = lambda _p: (_ for _ in ()).throw(
        KeyError("HWPTAG_LIST_HEADER"))
    conv.hwp_to_html_str = lambda _p: (_ for _ in ()).throw(
        RuntimeError(f"hwp5html 실패:\n{_PYHWP_NOISE}"))
    conv.is_hwpml = lambda _p: False
    try:
        c._get_tree_markdown()
        assert c._uses_ole_fallback() is True
        reason = c.fallback_reason or ""
        assert "HWPTAG_LIST_HEADER" in reason, "첫 실패가 최종 사유에 없습니다"
    finally:
        hwp5tree.to_markdown, conv.hwp_to_html_str, conv.is_hwpml = o1, o2, o3


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
    from docstruct.converters.hwp.hwp5tree import PAGE_BREAK
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
    trace.add("converters.hwp.hwp5tree", "파싱", "공통 기록")
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
        assert any(s.module == "converters.hwp.hwp5tree" for s in page.trace.steps)


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
    from docstruct.converters.hwp.hwp5tree import _Cell, _Table, _render_table

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
    from docstruct.converters.hwp.hwp5tree import _Cell, _Table, _render_table

    table = _Table(cols=2)
    table.cells = [
        _Cell(col=0, row=0, blocks=[]), _Cell(col=1, row=0, blocks=["합계"]),
        _Cell(col=0, row=1, blocks=["인건비"]), _Cell(col=1, row=1, blocks=["100"]),
    ]
    header = _render_table(table).splitlines()[0]
    assert "합계" in header


def test_render_table_keeps_fully_empty_table():
    """표 전체가 비어 있으면 그대로 둔다 (원본이 장식용 빈 상자)."""
    from docstruct.converters.hwp.hwp5tree import _Cell, _Table, _render_table

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
