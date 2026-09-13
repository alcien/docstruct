"""문서 구조화 결과를 담는 데이터 모델.

입력:
    (없음 — 데이터 계약) 파이프라인 각 단계가 채우는 값

역할:
    파싱 결과(본문·표·이미지·처리이력)를 보관하는 순수 데이터 컨테이너.
    로직을 갖지 않으며, 직렬화(to_dict)와 파생 속성만 제공한다.
호출부:
    docstruct.extractors.*   객체 생성
    docstruct.pipeline       조립·상태 갱신
    docstruct.tables.*       표 판정·재추출 결과 기록
    docstruct.output.report/preview 출력
출력:
    PageDocument / PageContent / TableInfo / ImageInfo / PageTrace / TraceStep
    각 클래스의 to_dict() 는 document.json 의 스키마가 된다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# content_type
TABLE = "table"
TEXT = "text"
IMAGE = "image"

# quality
SUFFICIENT = "sufficient"
WRONG = "wrong"
INSUFFICIENT = "insufficient"

#: LLM 재추출이 필요한 품질 등급
NEEDS_FILL = frozenset({WRONG, INSUFFICIENT})


@dataclass
class TableInfo:
    """표 하나의 파싱 결과와 LLM 판정 상태.

    입력(필드):
        id, table_num, placeholder  식별자와 본문 내 `<table N>` 태그
        markdown                    현재 표 내용 (GFM)
        original_markdown           재추출 전 원본 (없으면 None)
        content_type / quality      LLM 판정 결과
        llm_title / reason          판정 부가 정보
        group_image_ids             이미지로 묶일 표 id 목록
    출력(파생):
        needs_fill  재추출 대상 여부
        was_filled  재추출로 내용이 바뀌었는지
    """

    id: str
    table_num: int
    placeholder: str          # `<table N>` 여는 태그
    markdown: str             # 현재 markdown (fill 후에는 LLM 결과)
    bbox: dict[str, float] | None = None      # PDF 페이지 좌표(TOPLEFT, points)
    llm_title: str | None = None
    content_type: str | None = None           # table | text | image
    quality: str | None = None                # sufficient | wrong | insufficient
    original_markdown: str | None = None      # fill 이전 원본 (비교용)
    #: 이 표를 만든/고친 모델 이름 (source 가 llm·vlm 일 때). A/B 를
    #: 결과물만으로 가리기 위한 것 — ImageInfo.vlm_model 과 같은 이유다.
    vlm_model: str | None = None
    group_image_ids: list[str] | None = None
    reason: str | None = None
    #: 그림에서 승격된 표라면 원본 ImageInfo.id. 그림도 함께 남기므로
    #: 같은 영역이 tables 와 images 양쪽에 등록된다 — 이 값으로 짝을 찾는다.
    source_image_id: str | None = None
    #: 격자에서 셀이 빠진 비율 0~1. 표 구조 인식이 열·행을 놓친 표를
    #: 가려내기 위한 값이며, 정상 표에서는 None 이다.
    structure_ratio: float | None = None
    #: (이 표의 열 수, 같은 서식 표 다수의 열 수). 서식이 어긋난 표에만 있다.
    odd_columns: tuple[int, int] | None = None
    #: 셀 격자 (row, col, rowspan, colspan, text). markdown 이 표현하지
    #: 못하는 병합 정보를 담는다 — 구조화 단계가 병합 셀 값을 하위 행에
    #: 전파할 수 있게 하기 위함이다.
    cells: list[dict] | None = None
    #: 평가를 실제로 돌렸는가. False 면 `quality` 는 기본값일 뿐이다.
    #: LLM 미설정으로 건너뛰면 전부 `sufficient` 가 되어 정상처럼 보인다.
    assessed: bool = False
    #: 이 표가 무엇인가 — budget | indicator | program | org | review |
    #: cover | other. 유형마다 다루는 방법이 달라야 한다. 특히 `org`(조직도)
    #: 는 markdown 으로 표현할 수 없어 빈 칸이 많은 것이 정상이다.
    table_kind: str | None = None
    #: 이 표가 어떻게 만들어졌는지. 결과만 보고 출처를 알 수 있어야 한다.
    #:   parser  파서가 뽑은 그대로
    #:   llm     LLM 이 다시 만듦 (fill)
    #:   vlm     VLM 이 지면을 보고 다시 씀 (vlm_fix_tables)
    source: str = "parser"
    #: 재추출에서 원본 대비 무엇이 빠졌는지. LLM 이 다시 만든 표에만 있다.
    #: **맞고 틀림을 판정하지 않는다** — 표는 대조할 기준이 없어 글자가
    #: 바뀌었는지 알 수 없다. 빠짐만 세어 보여 주고 판단은 쓰는 쪽에 맡긴다.
    fill_diff: dict | None = None
    #: ── 실험 단계 표시 (docstruct.experiments) ──────────────────
    #: 검증이 끝나지 않은 기법이 남기는 값이다. 폐기할 때 이 묶음을 지운다.
    #: 셀과 낱말 배정이 양방향에서 어긋난 곳의 수. 값이 옆 칸으로 넘어간
    #: 신호다 — 두 문서에서 검출률 10~11% 로 일관됐다.
    match_disagreements: int | None = None
    #: 뭉친 값을 되돌린 행 수. 표 내용이 바뀌었다는 뜻이므로
    #: `original_markdown` 과 견줄 수 있어야 한다.
    cell_repairs: int | None = None
    #: 표 구조를 OTSL 다섯 토큰으로 적은 것. 지금은 기록만 하며, VLM
    #: 재작성 전후를 견줄 때 쓸 예정이다.
    otsl: str | None = None
    #: 지면 도형으로 복원한 격자와 TableFormer 병합의 **셀 자리** 차이.
    #: {"physical", "detected", "missing": [...], "extra": [...],
    #:  "coverage", "confidence": "high|low", "cells", "rows", "cols"}
    #: 실험 ⑥(vector_grid)이 채운다 — 표시만 하며 표는 바꾸지 않는다.
    grid_merge_gap: dict | None = None
    #: 물리 격자 결정 복원의 전후 기록. 실험 ⑦(grid_restore)이 표를 실제로
    #: 바꿀 때 채운다 — 원본 markdown 은 original_markdown 에 남는다.
    #: {"cells_detected", "cells_restored", "merged_detected",
    #:  "merged_restored", "coverage"}
    grid_restore: dict | None = None
    #: 괘선+사각형 모서리 합성 격자와 인식의 셀 자리 차이 (실험 ⑨ · H2/H3).
    #: 꼴은 grid_merge_gap 과 같다 — 근거만 다르다.
    synth_grid: dict | None = None
    #: 스캔 렌더 선 검출 격자와 인식의 차이 (실험 ⑩ · H8). 같은 꼴.
    scan_grid: dict | None = None
    #: 격자 후보들(tf·rect·lattice)의 서로 일치도 (실험 ⑪ · GriTS 간이판).
    #: {"sizes": {...}, "dice": {"rect~tf": 0.9, …}, "merged_dice": {...}}
    grid_score: dict | None = None
    #: **셀 텍스트가 이웃 칸으로 새어 든 자리** (0.4.79 · 상시 검사).
    #: 앞 칸의 끝 글자가 다음 칸 앞에 딸려 온 것이다
    #: (`63,618` 다음 칸이 `8 66,578`). 격자 온전성으로는 못 잡는다 —
    #: 자리는 다 덮였으므로 `ok` 다. {"leaks","samples"}
    cell_leaks: dict | None = None
    #: 격자의 구멍을 빈 칸으로 메운 기록 (실험 hole_fill · 0.4.78).
    #: 자리만 채우고 **글은 만들지 않는다** — 괘선이 없어도 쓸 수 있다.
    #: {"holes","width","height"}
    hole_fill: dict | None = None
    #: lattice_fill 이 **물러난 사유** (게이트 계측 · 0.4.72). 남은 결함
    #: 표가 왜 안 걸렸는지는 이 기록이 없으면 결과만 보고 가릴 수 없다 —
    #: `col_gate`(0.4.56)가 ⑬ 문제를 닫은 것과 같은 재료다.
    #: {"gate": too_few_cols|no_segments|no_lattice|lattice_fewer|
    #:          bounds_mismatch|text_read_failed|text_loss|made_worse|
    #:          earlier_restorer, "faults", ...}
    fill_gate: dict | None = None
    #: 격자는 서는데 구멍이 남아 다시 세운 기록 (실험 lattice_fill · 0.4.71).
    #: ⑮과 같은 기계를 **격자 결함**으로 발화시킨 것이다.
    #: {"cols","faults_before","faults_after"}
    lattice_fill: dict | None = None
    #: 괘선 격자 전체 복원 기록 (실험 ⑮). {"before","after"} — 열 수
    lattice_restore: dict | None = None
    #: 합의 병합 반영 기록 (실험 ⑭). {"applied","before","after"}
    agreed_grid: dict | None = None
    #: 열 격자 복원 기록 (실험 ⑬). {"before","after"}
    col_grid: dict | None = None
    #: **격자가 온전한가** (0.4.69). 표는 직사각 격자이므로 모든 (행, 열)
    #: 자리가 정확히 한 셀에 덮여야 한다. 구멍은 셀을 놓친 것이고 겹침은
    #: 경계를 잘못 그은 것이다 — **원본을 몰라도 틀렸다고 말할 수 있다.**
    #: 실측: HWPX 653표 결함 0개 / PDF 40~53%.
    #: {"width","height","holes","overlaps","hole_ratio","ok","heavy"}
    #: 표시만 하고 고치지 않는다 — 구멍을 메우려면 지면을 봐야 한다.
    grid_faults: dict | None = None
    #: 괘선보다 열을 더 쪼갠 표 (실험 over_split · 측정 전용 · 0.4.62).
    #: ⑬ 계측에서 나온 **반대 방향** 신호다 — 300표에서 `격자 > 인식` 은
    #: 0건인데 `격자 < 인식` 이 9건이었다. 열을 하나 더 쪼개면 그 뒤 값이
    #: 통째로 한 칸씩 밀린다. {"detected","lattice","gap","trusted"}
    over_split: dict | None = None
    #: 실험 ⑬이 **물러난 사유** (게이트 계측 · 0.4.56). ⑬ 강등(0.4.20)의
    #: 근거가 행안부 한 문서뿐이고, 조달청 열 밀림 7표 중 6표가 어느
    #: 실험에도 안 걸린 채 남아 있어 — 게이트가 좁은 것인지 ⑮이 흡수한
    #: 것인지 가르려면 기각 지점이 기록에 남아야 한다.
    #: {"reason": no_lattice|cols_match|gain_below|remap_failed,
    #:  "detected", "lattice"(격자가 섰을 때만)}
    col_gate: dict | None = None
    #: 머리행 계층 복원 기록 (실험 ⑫). {"before","after","rows"}
    head_grid: dict | None = None
    #: OCR 언어 오판 의심 (한글 지면이 한자로 나온 표). {"han","words","ratio"}
    #: 탐지는 결정론이고, 복구는 VLM 재구성이 맡는다.
    ocr_language_doubt: dict | None = None
    #: H12-b 후보-검증-선택 기록. {"candidates": n, "chosen": 이름,
    #: "score": 점수, "detail": {...}} — best-of-N 이 켜졌고 채택이
    #: 일어났을 때만 채워진다. 전후는 original_markdown 이 담당한다.
    vlm_choice: dict | None = None
    #: 예산표 산술 검산(가설 H5) 결과. 합계 열이 있는 표에서 행마다
    #: 연도열 합 = 합계 를 확인한다. 불일치는 병합 손실로 값이 밀렸다는
    #: 결정적 신호다. {"checked": n, "failed": k, "failures": [...]}
    sum_check: dict | None = None
    #: ────────────────────────────────────────────────────────────
    #: 이 표가 이어지는 앞 표의 id. 쪽을 넘는 표에만 있다.
    continues_from: str | None = None
    #: 그 앞 표의 헤더 행. 구조화 단계가 되짚지 않아도 되도록 담아 둔다.
    inherited_header: list[str] | None = None
    #: 원본 Docling TableItem. 표 셀 텍스트를 나중에 갈아끼울 때 쓴다.
    #: 직렬화 대상이 아니므로 to_dict 에는 넣지 않는다 — JSON 으로 바꿀 수
    #: 없는 객체이고, 결과 파일에 들어갈 정보도 아니다.
    source_item: Any = None

    def to_dict(self) -> dict[str, Any]:
        """JSON 직렬화용 dict.

        입력: 없음
        출력: 직렬화 가능한 필드만 담은 dict
        비고:
            `source_item` 은 Docling 객체라 JSON 으로 바꿀 수 없고 결과
            파일에 들어갈 정보도 아니다. `asdict()` 는 모든 필드를 담으므로
            여기서 빼 준다 — 넣어 두면 to_json 이 통째로 실패한다.
        """
        data = asdict(self)
        data.pop("source_item", None)
        return data

    @property
    def needs_fill(self) -> bool:
        """LLM 재추출 대상인지 여부.

        입력: content_type, quality
        출력: content_type 이 table 이고 quality 가 wrong/insufficient 이면 True
        """
        return self.content_type == TABLE and self.quality in NEEDS_FILL

    @property
    def was_filled(self) -> bool:
        """재추출이 실제로 내용을 바꿨는지 여부.

        입력: original_markdown, markdown
        출력: 원본이 있고 현재 내용과 다르면 True
        """
        return bool(self.original_markdown) and self.original_markdown != self.markdown


@dataclass
class ImageInfo:
    """그림 하나의 저장 결과와 표 승격 판정 상태.

    입력(필드):
        id, image_num, placeholder
                          식별자·번호와 본문 내 `<image N>` 여는 태그.
                          그림의 몫은 `</image N>` 까지다 (0.4.89 · images.tags)
        description       VLM 그림 설명 (없으면 None)
        image_path        저장된 파일 경로
        bbox              PDF 페이지 좌표(TOPLEFT, points)
        text_chars/lines  영역 안 텍스트 밀도 (표 후보 선별용)
        region_text       영역 안 PDF 텍스트 원문 (재추출 근거)
        table_candidate   표일 가능성이 있어 LLM 판정에 올릴지
        promoted_table_id 표로 승격됐다면 그 TableInfo.id
    """

    id: str
    placeholder: str
    #: 본문 블록 번호 — `<image N>` 의 N. 0.4.89 이전 산출물에는 없다.
    image_num: int | None = None
    description: str | None = None
    image_path: str | None = None   # 저장된 이미지 파일 경로
    mime_type: str | None = None
    bbox: dict[str, float] | None = None      # PDF 페이지 좌표(TOPLEFT, points)
    text_chars: int | None = None
    text_lines: int | None = None
    #: 영역 안의 PDF 텍스트 레이어 원문. 표로 승격되면 재추출 근거로 쓴다.
    #: 이미지로는 ➊➋➌·가운뎃점 같은 글자를 잘못 읽기 쉬운데, 원문이 있으면
    #: 글자는 여기서 가져오고 구조만 이미지로 판단하면 된다.
    region_text: str | None = None
    table_candidate: bool = False
    #: 좌표 기반 판정 결과 — "table" | "text" | "image".
    #: text 는 조직도·흐름도처럼 글자는 많지만 격자가 아닌 것으로,
    #: 표로 만들면 의미가 망가지므로 본문 텍스트로 뽑는다.
    region_kind: str | None = None
    region_kind_reason: str | None = None
    #: 이 설명이 어떻게 만들어졌는지 (`parser` | `vlm`).
    source: str = "parser"
    #: 그래프에서 읽은 값 중 본문에서 확인된 비율 0~1.
    #: None 이면 대조하지 않았다는 뜻이며, 낮다고 값이 틀린 것은 아니다 —
    #: **확인하지 못했다**는 뜻이다.
    chart_verified: float | None = None
    #: VLM 이 그림에서 읽어낸 내용 (캡처 이미지 표·도표 복원).
    #: description 은 한 문장 캡션이고, 이것은 내용 자체다.
    vlm_markdown: str | None = None
    #: **어느 모델이 이 결과를 냈는가.** 결과물만 보고 A/B 를 가리려면
    #: 필요하다 — 사내 엔드포인트로 읽은 것과 OpenAI 로 읽은 것을
    #: `vlm_markdown` 만으로는 구별할 수 없다. trace 에 단계는 찍히지만
    #: 모델명은 남지 않는다.
    vlm_model: str | None = None
    #: 이 그림의 실제 해상도(dpi). **형식과 무관하게** 잰다 — 한글 문서에도
    #: 스캔 이미지가 들어가고, 그때 원본이 흐리면 VLM 이 무엇으로 읽든
    #: 한계가 있다. PDF 는 지면 크기로, HWPX 는 `hp:curSz` 로 잰다.
    #: 낮으면(<150) 판독 실패의 원인 후보다 — 결과만 보고 알 수 있어야 한다.
    dpi: float | None = None
    #: 보내기 전에 무엇을 적용했나 (`{"preprocess": "basic",
    #: "upscale": "lanczos x2.46"}`). 비어 있으면 원본 그대로 보냈다.
    #: **A/B 를 결과물에서 가리려면 필요하다** — 어느 결과가 어느
    #: 설정이었는지 남지 않으면 비교가 서지 않는다.
    image_prep: dict | None = None
    #: 판독 가능성 — `{"glyph_px": 8, "verdict": "fair", ...}`.
    #: **dpi 가 아니라 글자 획 높이로 잰다** — 80dpi 가 102dpi 보다 잘
    #: 읽히는 일이 있다. 판독이 부실할 때 원인을 여기서 짚는다.
    legibility: dict | None = None
    #: **이 그림을 지면으로 보고 전사했는가** (0.4.58). `is_page_like` 가
    #: 참이면 그림 설명이 아니라 스캔 지면과 **같은 전사 지시문**으로
    #: 읽는다 — 한글 문서에 붙은 스캔본이 그렇다(0.4.25). 그때 이 결과는
    #: PDF 스캔 쪽 판독과 물리적으로 같은 행위인데, 그 사실이 결과물에
    #: 남지 않아 검증(verify_ocr)과 이중 판독(scan_ab) 대상에서 통째로
    #: 빠져 있었다. 남겨야 같은 대접을 받는다.
    transcribed: bool = False
    #: 전사된 그림의 이중 판독 대조 (실험 scan_ab · 측정 전용).
    #: 꼴은 PageContent.scan_ab 와 같다 — 대상이 쪽이 아니라 그림일 뿐이다.
    scan_ab: dict | None = None
    #: 판독 경로와 그것을 정한 신호 (실험 chart_gate · 측정 전용 · 0.4.62).
    #: 같은 조직도가 HWPX 에서는 전사, PDF 에서는 도해로 갈린 일이 있다 —
    #: 문턱 하나가 결과의 성격을 가르는데 그 사실이 남지 않았다.
    chart_gate: dict | None = None

    promoted_table_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON 직렬화용 dict.

        입력: 없음
        출력: 모든 필드를 담은 dict
        """
        return asdict(self)


#: summary() 에 표시할 출처 (unmeasured/n/a 는 정보가 없으므로 생략)
_SHOWN_SOURCES = frozenset({"text_layer", "ocr", "mixed", "empty"})

#: 화면·리포트용 한국어 라벨
SOURCE_LABELS = {
    "text_layer": "텍스트 레이어",
    "ocr": "OCR",
    "mixed": "혼합",
    "empty": "추출 실패",
    "unmeasured": "미측정",
    "n/a": "해당 없음",
    "unknown": "미상",
}


def source_label(source: str, ratio: float | None = None) -> str:
    """텍스트 출처 코드를 화면용 한국어 라벨로 바꾼다.

    입력:
        source  text_layer | ocr | mixed | empty | unmeasured | n/a
        ratio   OCR 비율 (ocr/mixed 일 때만 표시에 반영)
    출력: `OCR 92%` / `혼합 34%` / `미측정` 형태 문자열
    """
    label = SOURCE_LABELS.get(source, source)
    if ratio is not None and source in ("ocr", "mixed"):
        label += f" {ratio:.0%}"
    return label


#: 단계 라벨. ``GPU_ACCELERATED`` 에 속한 단계만 GPU 로 빨라집니다.
#: (부분 문자열 매칭은 "재추출"이 "추출"에 걸리므로 쓰지 않습니다.)
#:
#: 추출 단계는 형식마다 하는 일이 달라 라벨이 갈립니다. HWP/HWPX 에는
#: TableFormer 도 OCR 도 없으므로 PDF 라벨을 그대로 쓰면 리포트가 거짓말을
#: 합니다. stage_extract() 로 형식에 맞는 라벨을 얻습니다.
STAGE_EXTRACT = "추출 (백엔드+레이아웃+TableFormer+OCR)"
STAGE_EXTRACT_MARKUP = "추출 (HWP 파싱)"
STAGE_RENDER = "페이지 렌더 (pypdfium2)"
#: 스캔 쪽을 VLM 으로 전사하는 단계. **rapidocr 와 칸을 나눠 쓴다** —
#: 0.4.56 에서 쪽별 폴백이 되면서 두 판독이 함께 도는 일이 생겼고, 한 칸에
#: 쓰면 뒤엣것이 앞엣것을 덮어 **가장 오래 걸리는 단계가 계측에서 사라진다**
#: (실측: 주택과세금 377쪽에서 VLM 353쪽 시간이 통째로 없어졌다).
STAGE_SCAN_VLM = "스캔 쪽 판독 VLM"
STAGE_KOREAN_OCR = "한국어 OCR 재판독 (rapidocr)"
#: OCR 이 잘못 읽은 곳을 LLM 이 짚는 단계.
STAGE_VERIFY_OCR = "OCR 결과 검증 LLM"
#: 의심 자리를 지면 보고 다시 읽는 단계.
STAGE_REREAD_OCR = "의심 자리 재판독 VLM"
STAGE_TABLE_REBUILD = "표 재구성 (VLM)"
STAGE_GRID_REBUILD = "표 격자 재구성 (좌표)"
STAGE_ASSESS = "표 평가 LLM"
STAGE_FILL = "표 재추출 LLM"
STAGE_PICTURE_READ = "그림 내용 읽기 VLM"

#: GPU 로 빨라지는 단계. 추출은 PDF 경로(Docling)만 해당합니다.
GPU_ACCELERATED = frozenset({STAGE_EXTRACT})


def stage_extract(source_format: str) -> str:
    """형식에 맞는 추출 단계 라벨.

    입력: source_format — 'pdf' | 'hwp' | 'hwpx'
    출력: 단계 라벨 문자열
    """
    return STAGE_EXTRACT if source_format == "pdf" else STAGE_EXTRACT_MARKUP


@dataclass
class TraceStep:
    """파이프라인 단계 하나의 실행 기록.

    입력(필드):
        module       수행 모듈 경로 (예: docstruct.tables.assess)
        action       수행 동작
        detail       결과 요약
        status       ok | skip | warn | fail
        duration_ms  소요 시간 (측정한 경우)
    출력:
        line(index)  로그 한 줄 문자열
    """

    module: str                      # 실제 수행 모듈 (converters.pdf.docling 등)
    action: str                      # 무엇을 했는지
    detail: str = ""                 # 결과 요약
    status: str = "ok"               # ok | skip | warn | fail
    duration_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON 직렬화용 dict.

        입력: 없음
        출력: 모든 필드를 담은 dict
        """
        return asdict(self)

    def line(self, index: int) -> str:
        """실행 로그 한 줄을 만든다.

        입력: index — 표시할 순번 (1-based)
        출력: `  3. docstruct.tables.assess  LLM 표 판정 — ... (2.1s)` 형태 문자열
        """
        mark = {"ok": " ", "skip": "-", "warn": "!", "fail": "x"}.get(self.status, " ")
        text = f"{mark} {index}. {self.module:<28} {self.action}"
        if self.detail:
            text += f" — {self.detail}"
        if self.duration_ms is not None:
            text += f"  ({self.duration_ms / 1000:.1f}s)"
        return text


@dataclass
class PageTrace:
    """페이지 하나가 거친 처리 경로.

    입력(필드):
        extractor     docling | hwpml-xml | pyhwp-html | olefile-text | python-hwpx
        text_source   text_layer | ocr | mixed | empty | unmeasured | n/a
        ocr_ratio     OCR 로 만들어진 셀 비율 (PDF 에서만)
        cell_count    텍스트 셀 수
        table_count / picture_count
        rendered / assessed / refilled   단계별 수행 여부
        failed / notes / steps
    출력:
        summary()  한 줄 요약
        log()      순차 실행 로그 전문
        to_dict()  document.json 의 page.trace
    """

    #: 본문을 뽑아낸 주체
    #: docling | pyhwp-html | hwpml-xml | olefile-text | python-hwpx
    extractor: str = "unknown"

    #: 텍스트 출처.
    #:   text_layer  PDF 내장 텍스트 레이어를 읽음
    #:   ocr         이미지 인식으로 얻음
    #:   mixed       둘이 섞임
    #:   empty       본문이 실제로 비어 있음 (진짜 문제)
    #:   unmeasured  구분 불가 — Docling 이 셀 데이터를 보관하지 않음
    #:               (파싱 자체는 정상. generate_parsed_pages 로 측정 가능)
    #:   n/a         PDF 가 아님 (HWP/HWPX)
    text_source: str = "unmeasured"

    #: OCR 로 만들어진 텍스트 셀 비율 (0.0~1.0). PDF 에서만 의미 있음.
    ocr_ratio: float | None = None

    #: 원소 개수 (파싱 결과 규모)
    cell_count: int | None = None
    table_count: int = 0
    picture_count: int = 0

    #: 페이지 PNG 렌더 여부 (표 평가/재추출의 시각 근거)
    rendered: bool = False

    #: LLM 단계 수행 여부
    assessed: bool = False
    #: LLM 으로 다시 뽑은 표 ID
    refilled: list[str] = field(default_factory=list)

    #: 파싱 실패로 내용이 비었는지
    failed: bool = False

    #: 사람이 읽을 부가 설명 (경고·특이사항)
    notes: list[str] = field(default_factory=list)

    #: 이 페이지에 대해 실행된 단계들 (실행 순서대로)
    steps: list[TraceStep] = field(default_factory=list)

    def add(
        self,
        module: str,
        action: str,
        detail: str = "",
        *,
        status: str = "ok",
        duration_ms: float | None = None,
    ) -> None:
        """실행 단계를 순서대로 추가한다.

        입력: module, action, detail, status, duration_ms
        출력: 없음 (steps 에 TraceStep 추가)
        """
        self.steps.append(
            TraceStep(
                module=module, action=action, detail=detail,
                status=status, duration_ms=duration_ms,
            )
        )

    def log(self) -> str:
        """이 페이지의 순차 실행 로그 전문.

        입력: steps
        출력: 줄바꿈으로 이어진 로그 문자열 (단계가 없으면 안내 문구)
        """
        if not self.steps:
            return "(기록된 단계 없음)"
        return "\n".join(step.line(i) for i, step in enumerate(self.steps, 1))

    def to_dict(self) -> dict[str, Any]:
        """JSON 직렬화용 dict.

        입력: 없음
        출력: 모든 필드(steps 포함)를 담은 dict
        """
        return asdict(self)

    def summary(self) -> str:
        """처리 경로 한 줄 요약.

        입력: extractor, text_source, 각 단계 수행 여부
        출력: `docling · OCR 92% · 표3 · 렌더 · 평가 · 재추출2` 형태 문자열
        """
        parts = [self.extractor]
        if self.text_source in _SHOWN_SOURCES:
            parts.append(source_label(self.text_source, self.ocr_ratio))
        if self.table_count:
            parts.append(f"표{self.table_count}")
        if self.picture_count:
            parts.append(f"그림{self.picture_count}")
        if self.rendered:
            parts.append("렌더")
        if self.assessed:
            parts.append("평가")
        if self.refilled:
            parts.append(f"재추출{len(self.refilled)}")
        if self.failed:
            parts.append("실패")
        return " · ".join(parts)


@dataclass
class PageContent:
    """페이지 하나의 구조화 결과.

    입력(필드):
        page_no / page_no_kind   페이지 번호와 그 성격 (exact | document)
        content                  본문 markdown (표는 `<table N>` 블록으로 치환)
        tables / images          페이지에 속한 표·이미지 메타
        page_image_path          렌더된 페이지 PNG 경로 (PDF 만)
        trace                    처리 경로 기록
        layout                   레이아웃 모델 인식 영역 목록 (PDF 만)
    출력:
        to_dict()  document.json 의 pages[] 원소
    """
    page_no: int | str
    page_no_kind: str                # exact | document
    content: str
    tables: list[TableInfo] = field(default_factory=list)
    images: list[ImageInfo] = field(default_factory=list)
    page_image_path: str | None = None   # 렌더된 페이지 PNG (PDF 전용)
    trace: PageTrace = field(default_factory=PageTrace)
    #: 레이아웃 모델이 인식한 영역 목록 (PDF 만). docstruct.output.layout.LayoutItem
    layout: list[Any] = field(default_factory=list)
    #: OCR 이 잘못 읽었을 만한 곳 [{index, text, reason, source_text}].
    #: **본문을 고치지 않는다** — 어디가 이상한지만 남긴다. 값을 정하는
    #: 것은 지면을 보는 쪽(VLM)의 몫이다.
    ocr_doubts: list[dict] = field(default_factory=list)
    #: 재판독으로 본문을 고쳤다면 그전 것. 되돌릴 수 있어야 한다.
    ocr_original: str | None = None
    #: 스캔 쪽을 다시 읽은 주체 — 모델 이름 또는 "rapidocr".
    #: `ocr_original` 과 짝이다: 무엇이 무엇으로 바뀌었는지가
    #: 결과물에 남아야 OCR 과 VLM 을 견줄 수 있다.
    ocr_engine: str | None = None
    #: ── 실험 단계 표시 — 쪽 단위 (docstruct.experiments) ─────────
    #: VLM↔OCR 이중 판독 대조 — **쪽 전체를 다시 읽은 경우**
    #: (실험 scan_ab · 측정 전용). 그림 하나만 전사된 경우는
    #: ImageInfo.scan_ab 에 남는다. 본문은 바꾸지
    #: 않고 두 판독의 숫자 집합 불일치만 남긴다 — 수치 오독(연 1천분의
    #: 29 → 2.9)은 어느 판독기든 텍스트만으로 못 잡고, 둘의 불일치가
    #: 가장 싼 결정론 지목이다. {"engine","alt_engine","digit_jaccard",
    #:  "digits_only_main","digits_only_alt","hangul","len","repeat"}
    scan_ab: dict | None = None
    #: 스캔 렌더 배율 A/B (실험 scan_scale_ab · 측정 전용). 같은 쪽을
    #: 다른 배율로 그려 VLM 에 다시 읽힌 대조 판. 기본 300dpi(4.17)는
    #: "OCR 권장값이라는 일반론"으로 들어왔고 이 문서군에서 잰 적이
    #: 없다 — VLM 확대는 그림 경로에서 -11% 실측이 있다.
    #: {"scale_main","scale_alt","len","digit_jaccard","repeat_alt",...}
    scan_scale_ab: dict | None = None
    #: HWPX 표의 앵커 분포 (0.4.81). `{"inline","anchored","reordered"}`.
    #: `treatAsChar=0` 인 표는 문단에 매달린 객체라 지면에서는 문단 글
    #: 아래에 그려지는데 XML 로는 문단 앞쪽에 앉아 있다 — 그대로 훑으면
    #: 표가 제 캡션보다 먼저 나온다. `reordered` 는 바로잡은 수다.
    table_anchors: dict | None = None
    #: **지면에 보이지 않아 본문에서 뺀 글** (HWPX · 0.4.74).
    #: {사유: {"count", "samples"}} — 사유는 tiny(1pt) · white(흰 글자) ·
    #: tiny+white · field(누름틀 잔재).
    #:
    #: 빼는 것이 맞지만 **뺐다는 사실이 남아야** 한다. 실측(조달청 70쪽):
    #: 프로그램 코드 `53405` 가 1pt 이면서 흰 글씨로 표의 좁은 열 하나를
    #: 차지했다 — PDF 에는 그 열이 없어 쪽 맞춤이 어긋나는데, 이 기록이
    #: 없으면 원인을 짚을 수 없다.
    hidden_text: dict | None = None
    #: 쪽마다 되풀이되는 머리말·꼬리말 (실험 page_chrome · 측정 전용).
    #: 판독이 **맞는데도** 내용이 아닌 글이다 — 웹 이북을 인쇄한 PDF 는
    #: 모든 쪽에 시각·주소·브라우저 쪽번호가 박힌다. `share` 가 1.0 에
    #: 가까우면 그 쪽에는 본문이 없다는 뜻이다.
    page_chrome: dict | None = None
    #: ────────────────────────────────────────────────────────────

    def to_dict(self, *, slim: bool = False) -> dict[str, Any]:
        """JSON 직렬화용 dict.

        입력: slim — True 면 실행 기록(trace·layout)을 빼고 내용만 남긴다
        출력: 페이지 필드 + tables/images/trace/layout 을 각자의 to_dict 로
              푼 dict
        비고:
            slim 은 **읽을 사람**을 위한 것이다. 72쪽 문서에서 trace 가
            파일의 85%를 차지해 본문을 찾기 어려웠다. 진단이 필요하면
            slim 없이 뽑으면 된다 — 정보를 지우는 게 아니라 가리는 것이다.
        """
        if slim:
            return {
                "page_no": self.page_no,
                "content": self.content,
                "tables": [
                    {"id": t.id, "table_num": t.table_num,
                     "title": t.llm_title, "markdown": t.markdown}
                    for t in self.tables
                ],
                "images": [
                    {"id": i.id, "description": i.description,
                     "text": i.vlm_markdown}
                    for i in self.images
                ],
                "extraction": self.trace.summary(),
            }
        return {
            "page_no": self.page_no,
            "page_no_kind": self.page_no_kind,
            "page_image_path": self.page_image_path,
            "trace": self.trace.to_dict(),
            "layout": [i.to_dict() for i in self.layout],
            "ocr_doubts": self.ocr_doubts,
            "ocr_original": self.ocr_original,
            "ocr_engine": self.ocr_engine,
            "scan_ab": self.scan_ab,
            "scan_scale_ab": self.scan_scale_ab,
            "page_chrome": self.page_chrome,
            "hidden_text": self.hidden_text,
            "table_anchors": self.table_anchors,
            "content": self.content,
            "tables": [t.to_dict() for t in self.tables],
            "images": [i.to_dict() for i in self.images],
        }


@dataclass
class PageDocument:
    """문서 하나의 구조화 결과 (파이프라인 최종 산출물).

    입력(필드):
        filename / source_format  원본 파일명과 형식 (pdf | hwp | hwpx)
        pages                     페이지 목록
        failed_pages              파싱 실패로 빠진 페이지 번호
        failure_reasons           그 실패의 사유 (모듈·메시지)
        pipeline                  이 실행에 적용된 설정 스냅샷
        timings                   단계별 소요 시간(초)
    출력:
        to_dict()  document.json 전체
    """
    filename: str
    source_format: str
    pages: list[PageContent] = field(default_factory=list)
    #: Docling 이 파싱에 실패해 결과에서 빠진 페이지 번호.
    #: 예외가 아니라 로그로만 남는 부분 실패라 명시적으로 들고 다닙니다.
    failed_pages: list[int] = field(default_factory=list)
    #: 그 실패의 사유 (모듈·메시지). 번호만 남기면 원인을 알 수 없다 —
    #: 실측(조달청 78쪽): 61쪽이 빠졌는데 결과에는 번호 목록뿐이라
    #: 로그를 따로 보지 않으면 무엇을 고칠지 알 수 없었다.
    failure_reasons: list[str] = field(default_factory=list)
    #: 이 실행에 적용된 파이프라인 설정 (백엔드·OCR·LLM 등)
    #: 규칙으로 찾은 목차 [{title, page, source_page}].
    #: `page` 는 문서에 인쇄된 쪽번호라 PDF 쪽과 다를 수 있다.
    toc: list[dict] = field(default_factory=list)
    #: 인쇄 쪽번호와 PDF 쪽번호의 차이 (PDF − 인쇄).
    toc_offset: int | None = None
    pipeline: dict[str, Any] = field(default_factory=dict)
    #: 단계별 소요 시간(초). 어디에 시간이 쓰였는지 판단하는 근거.
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def page_count(self) -> int:
        """페이지 수.

        입력: 없음
        출력: pages 의 길이
        비고: to_dict() 의 page_count 와 같은 값. 객체에서도 바로 읽게 둔다.
        """
        return len(self.pages)

    def to_dict(self, *, slim: bool = False) -> dict[str, Any]:
        """JSON 직렬화용 dict (document.json 의 최상위 구조).

        입력: slim — True 면 실행 기록을 빼고 본문·표 중심으로 담는다
        출력: 문서 메타 + page_count + pages 목록을 담은 dict
        """
        if slim:
            return {
                "filename": self.filename,
                "source_format": self.source_format,
                "page_count": len(self.pages),
                "failed_pages": self.failed_pages,
                "failure_reasons": self.failure_reasons,
                "pages": [p.to_dict(slim=True) for p in self.pages],
            }
        return {
            "filename": self.filename,
            "source_format": self.source_format,
            "page_count": len(self.pages),
            "failed_pages": self.failed_pages,
            "failure_reasons": self.failure_reasons,
            "toc": self.toc,
            "toc_offset": self.toc_offset,
            "pipeline": self.pipeline,
            "timings": self.timings,
            "pages": [p.to_dict() for p in self.pages],
        }

    # -- 집계 헬퍼 (report에서 사용) -------------------------------------

    def all_tables(self) -> list[tuple[PageContent, TableInfo]]:
        """문서의 모든 표를 페이지와 짝지어 낸다.

        입력: 없음
        출력: [(PageContent, TableInfo)] — 문서 순서
        """
        return [(p, t) for p in self.pages for t in p.tables]

    def all_images(self) -> list[tuple[PageContent, ImageInfo]]:
        """문서의 모든 그림을 페이지와 짝지어 낸다.

        입력: 없음
        출력: [(PageContent, ImageInfo)] — 문서 순서
        """
        return [(p, i) for p in self.pages for i in p.images]

    def char_count(self) -> int:
        """본문 총 글자 수.

        입력: 없음
        출력: 페이지 content 길이의 합
        """
        return sum(len(p.content or "") for p in self.pages)
