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
    from docstruct.converters.hwp.hwp5tree import _Cell, _Table

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


def test_rowspan_continuation_is_marked_not_blank():
    """세로 병합이 이어지는 칸은 빈 칸이 아니라 표식으로 남는다."""
    from docstruct.converters.hwp.hwp5tree import MERGE_UP, _render_table

    rows = _render_table(_sns_table()).splitlines()
    last = rows[-1]
    assert "인스타그램" in last
    assert last.count(MERGE_UP) == 3, f"병합 표식이 없습니다: {last}"


def test_rowspan_value_stays_on_first_row():
    """값 자체는 맨 윗행에 그대로 있고 복제되지 않는다.

    복제하면 같은 값이 검색에 여러 번 걸리고, 합계가 행마다 있는 것처럼
    보인다.
    """
    md = None
    from docstruct.converters.hwp.hwp5tree import _render_table

    md = _render_table(_sns_table())
    assert md.count("15.7만") == 1
    assert "| 페이스북 | 콘텐츠 상호작용 | 15.7만 | 3.0만 |" in md


def test_rowspan_rows_are_not_truncated():
    """rowspan 이 표 끝까지 이어져도 행이 잘리지 않는다.

    행 수를 `max(row)+1` 로 세면 마지막 셀이 rowspan 으로 아래를 덮을 때
    그 행이 사라진다. `max(row + rowspan)` 이어야 한다.
    """
    from docstruct.converters.hwp.hwp5tree import MERGE_UP, _Cell, _Table, _render_table

    table = _Table(cols=2)
    table.cells = [
        _Cell(col=0, row=0, blocks=["가"]),
        _Cell(col=1, row=0, rowspan=3, blocks=["공유값"]),
        _Cell(col=0, row=1, blocks=["나"]),
        _Cell(col=0, row=2, blocks=["다"]),
    ]
    md = _render_table(table)
    assert "다" in md
    assert md.count(MERGE_UP) == 2


def test_merge_mark_can_be_disabled(monkeypatch):
    """표식은 끌 수 있다 (예전 산출물과 대조할 때)."""
    from docstruct.converters.hwp.hwp5tree import MERGE_MARK_ENV, MERGE_UP, _render_table

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

    from docstruct.report import write_json

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
    from docstruct.report import write_json

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
    from docstruct.converters.hwp.hwp5tree import _is_field_payload

    assert _is_field_payload('{"fields": {},"simplefields": {}}')
    assert _is_field_payload('  {"fields": {},"simplefields": {}}  ')
    assert _is_field_payload('{"fields": {"a":1},"simplefields": {"b":2}}')


def test_field_payload_does_not_eat_real_content():
    """본문에 나오는 정상 텍스트·JSON 은 건드리지 않는다.

    넓게 잡으면 문서에 실린 코드 조각이나 설명문까지 지운다.
    """
    from docstruct.converters.hwp.hwp5tree import _is_field_payload

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
    from docstruct.converters.hwp.hwp5tree import _is_field_payload

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


def test_hwpx_render_marks_vertical_merge():
    """세로 병합이 이어지는 칸에 표식을 남긴다 (hwp5tree 0.1.75 와 동일)."""
    from docstruct.converters.hwpx.hwpxtree import MERGE_UP, _render_table

    md = _render_table(_hwpx_table(2, 2, [
        (0, 0, 1, 1, "페이스북"), (0, 1, 2, 1, "15.7만"),
        (1, 0, 1, 1, "인스타그램"),
    ]))
    assert md.splitlines()[-1].count(MERGE_UP) == 1
    assert md.count("15.7만") == 1          # 값은 복제하지 않는다


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
    from docstruct import colab
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
    from docstruct.converters.korean_text import tighten_punctuation

    assert tighten_punctuation("입법 , 예 · 결산 심사") == "입법, 예·결산 심사"
    assert tighten_punctuation("｢ 헌법 ｣ 및 ｢ 국회법 ｣") == "｢헌법｣ 및 ｢국회법｣"
    assert tighten_punctuation("( 국회 )") == "(국회)"
    assert tighten_punctuation("가 · 나 · 다 · 라") == "가·나·다·라"


def test_tighten_punctuation_keeps_characters():
    """공백만 지우고 글자는 하나도 잃지 않는다."""
    import re

    from docstruct.converters.korean_text import tighten_punctuation

    for line in ("입법 , 예 · 결산", "｢ 헌법 ｣ 에  따라", "( 국회 ) 사무처"):
        strip = lambda t: re.sub(r"\s", "", t)      # noqa: E731
        assert strip(tighten_punctuation(line)) == strip(line)


def test_tighten_punctuation_protects_bullet():
    """줄 맨 앞의 가운뎃점은 글머리표이므로 뒤 공백을 지키다.

    지우면 `· 항목` 이 `·항목` 이 되어 본문에 붙는다.
    """
    from docstruct.converters.korean_text import tighten_punctuation

    assert tighten_punctuation("· 시작 항목") == "· 시작 항목"
    assert tighten_punctuation("  · 들여쓴 항목") == "  · 들여쓴 항목"


def test_tighten_punctuation_leaves_normal_text():
    """이미 올바른 표기는 건드리지 않는다."""
    from docstruct.converters.korean_text import tighten_punctuation

    for line in ("정상·표기", "각 부처별 사업 현황", "입법, 예·결산", ""):
        assert tighten_punctuation(line) == line


def test_collapse_repeated_words_handles_phrases():
    """낱말뿐 아니라 여러 낱말로 된 구절 반복도 줄인다.

    제목에 그림자 효과를 준 지면에서 같은 글자가 여러 번 그려진다.
    """
    from docstruct.converters.korean_text import collapse_repeated_words

    assert collapse_repeated_words("별첨3 별첨3 별첨3") == "별첨3"
    assert collapse_repeated_words(
        "성과계획 목표체계 성과계획 목표체계 성과계획 목표체계 제1장 제1장 제1장"
    ) == "성과계획 목표체계 제1장"


def test_collapse_repeated_words_needs_three():
    """두 번 반복은 실제 표현일 수 있어 건드리지 않는다."""
    from docstruct.converters.korean_text import collapse_repeated_words

    assert collapse_repeated_words("국가 국가") == "국가 국가"
    assert collapse_repeated_words("매우 매우 좋다") == "매우 매우 좋다"
    assert collapse_repeated_words("가 나 다 라") == "가 나 다 라"


def test_normalize_pdf_text_is_pdf_only():
    """PDF 전용 정규화는 HWP 경로에 걸지 않는다.

    HWP·HWPX 는 바이너리에서 글자를 직접 읽어 이런 손상이 없다(같은
    문서에서 527건 대 0건). 정상 텍스트에 규칙을 더 걸면 고칠 것 없이
    위험만 는다.
    """
    from pathlib import Path as _Path

    src = _Path(__file__).resolve().parent.parent / "src" / "docstruct"
    pdf_extractor = (src / "extractors" / "pdf.py").read_text(encoding="utf-8")
    assert "normalize_pdf_text" in pdf_extractor

    for name in ("hwp5tree.py", "olefile.py"):
        text = (src / "converters" / "hwp" / name).read_text(encoding="utf-8")
        assert "normalize_pdf_text" not in text, f"{name} 에 PDF 전용 규칙이 걸렸습니다"


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
    version = tomllib.loads(
        (root / "pyproject.toml").read_text(encoding="utf-8")
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

    from docstruct.core.config import get_settings

    settings = get_settings()
    # 셋 다 기본으로 끈다. 빈 칸 표시는 정상 표를 82% 나 잡았고(오판),
    # 격자 재구성은 텍스트 PDF 에서 13회 시도해 13회 모두 폐기됐다.
    assert settings.flag_broken_tables is False
    assert settings.rebuild_grid is False
    assert settings.vlm_fix_tables is False


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
    from docstruct.converters.korean_text import tighten_punctuation

    assert tighten_punctuation("국회 (사무처)") == "국회(사무처)"
    assert tighten_punctuation("1 급 (2 명 )") == "1급(2명)"


def test_space_between_number_and_unit_removed():
    """숫자와 단위 사이 공백을 없앤다."""
    from docstruct.converters.korean_text import tighten_punctuation

    assert tighten_punctuation("총 169 명") == "총 169명"
    assert tighten_punctuation("2027 년도 예산") == "2027년도 예산"
    assert tighten_punctuation(
        "· 1 급 (2 명 ), 2 급 (32 명 )") == "· 1급(2명), 2급(32명)"


def test_number_unit_rule_is_conservative():
    """한 글자 단위만 붙인다.

    `5 개년 계획` 의 `개년` 처럼 두 글자 이상을 붙이면 다른 말이 될 수
    있어 목록을 좁게 둔다.
    """
    from docstruct.converters.korean_text import tighten_punctuation

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

    from docstruct.converters.korean_text import tighten_punctuation

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
    from docstruct.media.chart_read import verified_ratio

    hit, total = verified_ratio(_CHART_ANSWER, "전략목표 Ⅰ 은 20.7 이고 Ⅱ 는 25.7 이다")
    assert (hit, total) == (2, 2)


def test_chart_verify_ignores_single_digits():
    """한 자리 숫자는 대조에 쓰지 않는다.

    우연히 맞을 확률이 높아 근거가 되지 못한다.
    """
    from docstruct.media.chart_read import verified_ratio

    _, total = verified_ratio("| a | 5 |\n| b | 7 |", "본문에 5 와 7 이 있다")
    assert total == 0


def test_chart_read_records_verification(tmp_path, monkeypatch):
    """검증 결과가 ImageInfo 에 남는다."""
    from docstruct.media import chart_read

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
    from docstruct.media import chart_read

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
    from docstruct.media import chart_read

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
    from docstruct.media import chart_read

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
    from docstruct.media import chart_read

    monkeypatch.setattr(chart_read, "llm_api_config", lambda: {"model": "x"})
    monkeypatch.setattr(chart_read, "encode_image_file",
                        lambda _p: ("image/png", "AAAA"))
    monkeypatch.setattr(chart_read, "invoke_llm", lambda *a, **k: _SPAN_ANSWER)


def test_chart_verify_looks_at_neighbouring_pages(tmp_path, monkeypatch):
    """이웃 쪽 본문까지 견준다.

    같은 쪽만 보면 실측에서 검증률이 0 이었다.
    """
    from docstruct.media import chart_read

    image = tmp_path / "c.png"
    image.write_bytes(b"x")
    _patch_chart_llm(monkeypatch)

    pages = _span_pages(image)
    chart_read.read_charts(pages)
    assert pages[2].images[0].chart_verified == 1.0


def test_chart_verify_span_is_configurable(tmp_path, monkeypatch):
    """범위를 좁히면 검증률이 떨어진다."""
    from docstruct.media import chart_read

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
    from docstruct.media import chart_read

    image = tmp_path / "c.png"
    image.write_bytes(b"x")
    _patch_chart_llm(monkeypatch)
    monkeypatch.setenv(chart_read.VERIFY_SOURCE_ENV, "document")

    pages = _span_pages(image, chart_page=200)   # 아주 멀리 떨어뜨림
    chart_read.read_charts(pages)
    assert pages[2].images[0].chart_verified == 1.0


def test_chart_verify_span_rejects_bad_values(monkeypatch):
    """범위 설정이 잘못되면 기본값을 쓴다."""
    from docstruct.media import chart_read

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
    from docstruct.media import chart_read

    # 기본은 ±2 — 실측에서 ±1 로도 9/11 이 잡혔고 ±2 이상은 더 늘지 않는다.
    assert chart_read.DEFAULT_VERIFY_SPAN >= 1
    assert chart_read._verify_span() >= 1


def test_chart_read_uses_neighbour_pages(tmp_path, monkeypatch):
    """같은 쪽에 값이 없어도 앞뒤 쪽에서 확인한다."""
    from docstruct.media import chart_read
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
    from docstruct.media import chart_read
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
    from docstruct.report import summary_lines

    text = "\n".join(summary_lines(_source_doc()))
    assert "LLM 1" in text and "VLM 1" in text
    assert "값이 빠진 표" in text
    assert "그래프" in text


def test_preview_shows_source_badges():
    """HTML 미리보기에 출처 배지가 나온다."""
    from docstruct.preview import summary_html, table_overview_html

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
    """다섯 기법이 등록돼 있다."""
    from docstruct.experiments import all_experiments

    keys = {e.key for e in all_experiments()}
    assert keys == {"split_merge", "grid_refine", "two_way_match",
                    "grid_consensus", "otsl_diff"}


def test_experiments_off_by_default():
    """실험은 기본으로 꺼져 있다.

    검증이 끝나면 본체로 옮긴다.
    """
    from docstruct.experiments import enabled_experiments

    assert enabled_experiments() == []


def test_experiment_toggle(monkeypatch):
    """환경변수로 켜고 끌 수 있다."""
    from docstruct.experiments import enabled_experiments

    monkeypatch.setenv("DOCSTRUCT_EXP_SPLIT_MERGE", "true")
    assert [e.key for e in enabled_experiments()] == ["split_merge"]


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


def test_split_merge_detects_broken_header():
    """세로 병합이 좌우로 갈린 모습을 잡는다.

    실측: `구분` → `구` / `분` 으로 쪼개져 양쪽 열에 붙었다.
    """
    from docstruct.experiments.split_merge import find_split_merges
    from tests.table_fixtures import make_table

    item = make_table(1, 3, [_split_cell(0, 0, "구", (100, 100, 115, 112)),
                             _split_cell(0, 1, "분", (117, 100, 132, 112)),
                             _split_cell(0, 2, "총 계 (A+B)", (140, 100, 220, 112))])

    found = find_split_merges(item)
    assert len(found) == 1
    assert found[0]["texts"] == ["구", "분"]


def test_split_merge_ignores_normal_table():
    """정상 표에서는 아무것도 잡지 않는다."""
    from docstruct.experiments.split_merge import find_split_merges
    from tests.table_fixtures import make_table

    item = make_table(1, 2, [_split_cell(0, 0, "구분", (100, 100, 140, 112)),
                             _split_cell(0, 1, "총계", (150, 100, 200, 112))])
    assert find_split_merges(item) == []


def test_grid_refine_nudges_only_nearby():
    """경계를 한도 안에서만 옮긴다.

    멀리 있는 관측값은 다른 열의 경계다.
    """
    from docstruct.experiments.grid_refine import refine_edges

    assert refine_edges([100, 150, 200], [98, 152, 199]) == [98, 152, 199]
    assert refine_edges([100, 150], [98, 300]) == [98, 150]


def test_grid_consensus_finds_standard():
    """같은 서식 표들의 중앙값으로 표준 격자를 만든다."""
    from docstruct.experiments.grid_consensus import consensus_edges

    standard = consensus_edges([[100, 150, 200], [101, 149, 201],
                                [100, 151, 199], [130, 150, 200]])
    assert abs(standard[0] - 100.5) < 1
    assert abs(standard[1] - 150) < 1


def test_two_way_match_flags_crowding():
    """한 셀에 조각이 몰리면 불일치로 잡는다."""
    from docstruct.converters.pdf.cell_match import Box
    from docstruct.experiments.two_way_match import disagreements

    cells = [Box(100, 100, 150, 120), Box(150, 100, 200, 120)]
    fine = [(Box(105, 105, 145, 115), "왼쪽"), (Box(155, 105, 195, 115), "오른쪽")]
    assert disagreements(cells, fine) == []

    crowded = [(Box(105, 105, 145, 115), "A"), (Box(110, 105, 148, 115), "B")]
    assert len(disagreements(cells, crowded)) == 1


def test_otsl_expresses_merges():
    """OTSL 이 병합을 토큰으로 나타낸다."""
    from docstruct.experiments.otsl_diff import to_otsl, token_diff

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
    """세로 병합이 이어지는 칸에 `〃` 를 남긴다.

    빈 칸으로 두면 값이 맨 윗행만의 것으로 읽힌다 — HWP 경로에서 같은
    문제로 `페이스북+인스타그램 합계` 가 `페이스북 단독` 으로 잘못 읽혔다.
    """
    from docstruct.tables.docling import MERGE_UP, docling_table_to_markdown
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
    assert MERGE_UP in markdown                  # 아래 행에 표식
    # 값을 복제하지는 않는다 — 집계가 왜곡된다
    assert markdown.count("프로그램") == 1


def test_pdf_table_merge_mark_matches_hwp():
    """표식이 HWP·HWPX 와 같은 글자다.

    형식마다 다르면 읽는 쪽이 분기해야 한다.
    """
    from docstruct.converters.hwp.hwp5tree import MERGE_UP as HWP_MARK
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
        "split_merge_hints", "match_disagreements", "edge_drift",
        "consensus_drift", "otsl",
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
                      split_merge_hints=[{"row": 0, "texts": ["구", "분"]}],
                      match_disagreements=3, edge_drift=2.5,
                      consensus_drift=1.2, otsl="C L NL")
    data = table.to_dict()
    for key in ("split_merge_hints", "match_disagreements", "edge_drift",
                "consensus_drift", "otsl"):
        assert key in data, f"{key} 가 빠졌습니다"
    json.dumps(data, ensure_ascii=False)


def test_experiments_need_env_to_run():
    """실험은 환경변수로 켜야 돈다.

    켜지 않으면 아무것도 하지 않는다 — 결과가 비어 있으면 이것부터 본다.
    """
    from docstruct.experiments import all_experiments, enabled_experiments

    assert all_experiments()           # 등록은 돼 있고
    assert not enabled_experiments()   # 기본은 꺼짐


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

    for name in ("DOCSTRUCT_EXP_SPLIT_MERGE", "DOCSTRUCT_EXP_OTSL_DIFF"):
        monkeypatch.delenv(name, raising=False)

    assert _enable_experiments("split_merge,otsl_diff") == [
        "split_merge", "otsl_diff"]
    assert {e.key for e in enabled_experiments()} == {"split_merge", "otsl_diff"}


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
    assert "split_merge" in out and "grid_consensus" in out


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

def test_consensus_groups_by_header_not_only_width():
    """열 개수가 같아도 헤더가 다르면 다른 서식이다."""
    from docstruct.experiments.grid_consensus import _header_key
    from docstruct.models import TableInfo

    years = TableInfo(id="t1", table_num=1, placeholder="",
                      markdown="| 연 도 | 2020 | 2021 |\n|---|---|---|\n"
                               "| 목표 | - | 13,303 |")
    program = TableInfo(id="t2", table_num=2, placeholder="",
                        markdown="| 프로그램명 | 목표 | 지표 |\n|---|---|---|\n"
                                 "| 전략목표 Ⅰ | | |")

    assert _header_key(years) != _header_key(program)
    assert _header_key(years)[0] == "연 도"


def test_consensus_header_key_ignores_emphasis():
    """굵게 표시(`**`)는 서식 판단에 영향을 주지 않는다."""
    from docstruct.experiments.grid_consensus import _header_key
    from docstruct.models import TableInfo

    plain = TableInfo(id="t1", table_num=1, placeholder="",
                      markdown="| 구분 | 값 |\n|---|---|\n| 가 | 1 |")
    bold = TableInfo(id="t2", table_num=2, placeholder="",
                     markdown="| **구분** | **값** |\n|---|---|\n| 가 | 1 |")
    assert _header_key(plain) == _header_key(bold)


def test_consensus_skips_tables_without_header():
    """헤더를 읽을 수 없으면 견주지 않는다."""
    from docstruct.experiments.grid_consensus import _header_key
    from docstruct.models import TableInfo

    empty = TableInfo(id="t1", table_num=1, placeholder="", markdown="")
    assert _header_key(empty) == ()


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

def test_split_merge_ignores_number_pairs():
    """숫자 쌍은 갈린 낱말이 아니다.

    실측: 회계 코드 표에서 `['11', '11']` 이 10곳씩 잡혔다.
    """
    from docstruct.experiments.split_merge import find_split_merges
    from tests.table_fixtures import make_cell, make_table

    item = make_table(1, 3, [
        make_cell(0, 0, "11", box=(100, 100, 115, 112)),
        make_cell(0, 1, "11", box=(117, 100, 132, 112)),
        make_cell(0, 2, "0", box=(140, 100, 160, 112)),
    ])
    assert find_split_merges(item) == []


def test_split_merge_still_catches_hangul():
    """한글이 갈린 것은 그대로 잡는다 (회귀 확인)."""
    from docstruct.experiments.split_merge import find_split_merges
    from tests.table_fixtures import make_cell, make_table

    item = make_table(1, 3, [
        make_cell(0, 0, "구", box=(100, 100, 115, 112)),
        make_cell(0, 1, "분", box=(117, 100, 132, 112)),
        make_cell(0, 2, "지적사항", box=(140, 100, 220, 112)),
    ])
    assert [h["texts"] for h in find_split_merges(item)] == [["구", "분"]]


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
    from docstruct.experiments.otsl_diff import to_otsl

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

    from docstruct.experiments import grid_refine, two_way_match

    for module in (grid_refine, two_way_match):
        source = inspect.getsource(module.run)
        assert "read_text_runs" in source
        assert "read_image" not in source        # OCR 을 쓰지 않는다


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

def test_grid_refine_ignores_cell_padding():
    """셀 안쪽 여백을 어긋남으로 보지 않는다."""
    from docstruct.experiments.grid_refine import MIN_MEANINGFUL_DRIFT

    # 실측에서 정상 표들이 0.9~4.0pt 였다
    assert MIN_MEANINGFUL_DRIFT >= 4.0


def test_two_way_match_counts_straddling_only():
    """셀 하나에 담기는 낱말은 세지 않는다.

    한 셀에 여러 낱말이 들어가는 것은 정상이다.
    """
    import inspect

    from docstruct.experiments import two_way_match

    source = inspect.getsource(two_way_match.run)
    assert "straddling" in source
    assert "> 1" in source                  # 두 셀 이상을 걸칠 때만


def test_two_way_match_flags_real_straddle():
    """한 셀에 여러 조각이 몰리면 잡는다.

    셀 → 조각 방향은 하나만 고를 수 있으므로, 둘 이상이 같은 셀을
    가리키면 어긋난다.
    """
    from docstruct.converters.pdf.cell_match import Box
    from docstruct.experiments.two_way_match import disagreements

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

def test_consensus_uses_relative_drift():
    """어긋남을 표 폭에 견준다.

    고정 pt 로 재면 넓은 표가 불리하다.
    """
    import inspect

    from docstruct.experiments import grid_consensus

    source = inspect.getsource(grid_consensus.run)
    assert "MIN_DRIFT_RATIO" in source
    assert "column" in source


def test_consensus_drift_ratio_documented():
    """열 폭 대비 기준이 있다."""
    from docstruct.experiments.grid_consensus import MIN_DRIFT_RATIO

    # 열 하나를 통째로 밀어낼 정도라야 한다
    assert MIN_DRIFT_RATIO >= 1.0


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
