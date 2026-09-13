"""격자에 셀이 빠진 표를 VLM 으로 다시 만든다.

역할:
    표 구조 인식이 열이나 행을 통째로 놓친 표만 골라, 그 영역 이미지를
    VLM 에 보여 주고 markdown 표를 새로 받는다.
호출부:
    docstruct.pipeline (`vlm_fix_tables` 가 켜졌을 때)
입력: 구조 결함이 표시된 PageContent 목록
출력: 다시 만든 표 수

왜 필요한가
----------
좌표 매칭은 **격자가 원본과 같을 때만** 동작한다. 스캔본에서 13행 2열 표가
7행으로 인식된 사례가 있었다. OCR 은 `지방세법`·`종합부동산세법` 을 제대로
읽었는데 넣을 행이 없었다.

이런 표는 알고리즘으로 고칠 수 없다 — 없는 칸에 값을 넣을 수는 없다.
지면을 보고 격자를 다시 세우는 수밖에 없다.

대상 선정
--------
**서식이 어긋난 표(`odd_columns`)만 고른다.** 같은 서식 표 다수와 열 수가
다른 표이며, 문서 안에서 서로 견주어 찾으므로 근거가 분명하다.

    table_10 · 7열 — 같은 서식 표 다수는 8열입니다

빈 칸 비율(`structure_ratio`)은 쓰지 않는다. 그 값은 "값이 없는 칸" 을
세는 것이지 구조 결함이 아니어서, 텍스트 PDF 에서 정상 표 17개 중 14개가
그 표시를 받았다. 그것으로 고르면 멀쩡한 표를 추측으로 바꾸게 된다.

**스캔본에는 대상이 잡히지 않는다.** 같은 서식 표가 셋 이상 있어야 비교가
되는데 스캔 문서는 표 서식이 제각각이다. 스캔본의 격자 크기 오류
(13행이 7행으로 인식)는 아직 자동 판정 방법이 없다.

왜 전부에 쓰지 않는가
------------------
VLM 은 못 읽은 것을 지어낸다. 좌표 매칭은 "이 텍스트는 (194, 473) 셀에서
왔다" 가 증명되지만 VLM 출력은 그렇지 않다. **구조가 깨진 표에만** 쓰고,
결과가 원본보다 나쁘면 되돌린다.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import logging
import re
from typing import Any

from docstruct.infrastructure.llm.client import invoke_llm, llm_api_config
from docstruct.images.vlm_read import encode_image_file
from docstruct.core.config import get_settings
from docstruct.models import PageContent
from docstruct.structuring.checks import grid_check
from docstruct.tables.grade import cells_from_markdown

_log = logging.getLogger(__name__)

#: 다시 만든 표가 이보다 짧으면 실패로 본다.
MIN_TABLE_CHARS = 20

#: 표를 다시 만들 때 쓰는 지시문 (단문판 — 기본).
_PROMPT = """이 이미지에서 표 하나를 찾아 GFM markdown 표로 옮기세요.

규칙:
- 보이는 대로만 옮깁니다. 읽을 수 없는 칸은 빈 칸으로 두세요.
- 내용을 추측하거나 채워 넣지 마세요.
- 병합된 칸은 맨 왼쪽 위에만 값을 넣고, 그 병합이 덮는 **아래쪽 칸에는
  `〃` 를 적으세요** (빈 칸이 아니라 `〃` 입니다). 가로로 덮인 칸도 같습니다.
- 원래부터 값이 없는 칸은 빈 칸으로 둡니다 — `〃` 와 구분해 주세요.
- 표가 없거나 읽을 수 없으면 정확히 `없음` 이라고만 답하세요.
- 설명 없이 표만 출력하세요.
{target}{hint}
참고 (이 표 주변 본문):
{context}"""

#: 단계별 지시판 (가설 H10). 근거 — TaDA 헤더 벤치(VLDB 2025): 중형
#: 모델에는 짧은 지시보다 **단계별 지시 + 탐색 기준**이 낫다. 사내
#: Gemma 26B 가 그 구간이다. DOCSTRUCT_VLM_PROMPT=steps 로 켠다.
_PROMPT_STEPS = """이 이미지의 표를 GFM markdown 표로 옮기세요. 다음 순서대로 하세요.

1단계 — 행 수를 세세요. 가로 괘선(또는 배경 색 경계)을 위에서 아래로
   따라가며 셉니다. 글줄 수가 아니라 **칸의 행 수**입니다.
2단계 — 열 수를 세세요. 세로 경계를 왼쪽에서 오른쪽으로 셉니다.
   모든 행이 같은 열 수를 가져야 합니다.
3단계 — 병합을 확인하세요. 이웃 칸 사이에 경계선이 없으면 병합입니다.
   병합 칸은 맨 왼쪽 위 자리에만 값을 넣고, **덮인 자리에는 `〃`** 를
   적습니다. 원래 비어 있는 칸(값이 없는 칸)은 빈 칸으로 두어 구분합니다.
4단계 — 1~3단계의 행·열 수 그대로 표를 작성하세요.

규칙:
- 보이는 대로만 옮깁니다. 읽을 수 없는 칸은 빈 칸, 추측 금지.
- 표가 없거나 읽을 수 없으면 정확히 `없음` 이라고만 답하세요.
- 설명·단계 풀이 없이 최종 표만 출력하세요.
{target}{hint}
참고 (이 표 주변 본문):
{context}"""

#: missing 자리 제시 문단 (가설 H10). 근거 — NGTR(IJCAI-25): 이웃/근거를
#: 짚어 주면 VLM 병합 인식이 오른다. 우리는 ⑥·⑨가 남긴 `missing`
#: (지면 도형에는 있는데 인식에 없는 병합 자리)을 공짜로 갖고 있다.
#: DOCSTRUCT_VLM_HINT_MISSING=1 로 켠다.
_HINT_HEADER = (
    "\n지면 도형 분석 결과, 아래 자리에 병합이 있을 가능성이 높습니다. "
    "각 자리를 확인하고, 병합이 맞으면 덮인 칸에 `〃` 를 적으세요:\n")
_HINT_LIMIT = 8

_NO_TABLE = "없음"


def _prompt_template() -> str:
    """지시문 판 선택 — 단문(기본) 또는 단계별.

    입력: 없음 (DOCSTRUCT_VLM_PROMPT 환경 변수)
    출력: 프롬프트 틀
    """
    import os

    if os.getenv("DOCSTRUCT_VLM_PROMPT", "").strip().lower() == "steps":
        return _PROMPT_STEPS
    return _PROMPT


def _missing_hint(table) -> str:
    """⑥·⑨가 남긴 missing 자리를 지시문 문단으로.

    입력: table — TableInfo (grid_merge_gap · synth_grid)
    출력: 힌트 문단. 끌 때·자료 없을 때·신뢰 낮을 때는 빈 문자열
    비고:
        confidence:high 인 근거만 쓴다 — 낮은 덮개의 자리는 행·열 번호가
        어긋날 수 있어(실측 83%), 틀린 자리를 짚어 주면 역효과다.
    """
    import os

    # **기본 켬 (0.4.3 승격).** A/B 4판 중 최고(48.3% 대 대조군 44.3%)이고,
    # ⑫와 함께 쓰면 정밀도까지 오른다(조달청 98.5%) — 결정론 보호 덕에 VLM
    # 이 결정론이 못 푼 표만 받게 되기 때문이다. 끄려면 이 환경변수를
    # 거짓으로 두거나 `--exp no_vlm_hint` 를 쓴다.
    if os.getenv("DOCSTRUCT_VLM_HINT_MISSING", "").strip().lower() in (
            "0", "false", "off", "no"):
        return ""
    # 근거 위계: **합의 병합**(사각형∩괘선)이 가장 정확하다 — 정답 대조
    # 실측 94.9~98.0% 대 단일 근거 69.6~91.9%. 있으면 이것부터 쓴다.
    score = getattr(table, "grid_score", None)
    if score and score.get("agreed_missing"):
        return _hint_lines(score["agreed_missing"])
    for source in (getattr(table, "grid_merge_gap", None),
                   getattr(table, "synth_grid", None)):
        if not source or source.get("confidence") != "high":
            continue
        missing = source.get("missing") or []
        if not missing:
            continue
        return _hint_lines(missing)
    return ""


def _hint_lines(missing) -> str:
    """missing 자리 목록을 지시문 문단으로.

    입력: missing — (row, col, rowspan, colspan) 목록
    출력: 힌트 문단
    """
    lines = []
    for row, col, rowspan, colspan in missing[:_HINT_LIMIT]:
        what = []
        if rowspan > 1:
            what.append(f"세로 {rowspan}칸")
        if colspan > 1:
            what.append(f"가로 {colspan}칸")
        lines.append(f"- {row + 1}행 {col + 1}열에서 {'·'.join(what) or '병합'}")
    return _HINT_HEADER + "\n".join(lines) + "\n"


#: 지목 문단 머리. 쪽 이미지를 통째로 보내므로 **어느 표인지 반드시 짚어야**
#: 한다 — 실측(조달청 8쪽): 지목이 없어 VLM 이 같은 쪽의 다른 표를 옮겨
#: 적었고, 그 결과가 원본을 덮어 회귀가 났다.
_TARGET_HEADER = "\n옮길 표를 다음으로 지목합니다. 이 표만 옮기세요:\n"


def _table_target(page, table) -> str:
    """어느 표를 옮길지 짚어 주는 문단.

    입력: page — 대상 페이지, table — 대상 TableInfo
    출력: 지목 문단. 짚을 근거가 없으면 빈 문자열
    비고:
        근거는 둘 다 결정론이다 — **머리행 낱말**(원본 markdown 첫 줄)과
        **쪽 안 순번**(bbox 위→아래). 자르지 않고 통째로 보내는 이유는
        bbox 가 실제보다 좁게 잡히는 것이 이 문제의 원인이기 때문이다
        (자르면 잘린 표를 보여 준다). 그래서 자르는 대신 **말로 짚는다.**
    """
    lines = [line for line in (table.markdown or "").splitlines()
             if line.strip().startswith("|")]
    head = ""
    if lines:
        cells = [c.strip().replace("*", "") for c in lines[0].strip("|").split("|")]
        cells = [c for c in cells if c][:4]
        if cells:
            head = " · ".join(cells)

    order = ""
    with_bbox = [t for t in (page.tables or []) if t.bbox]
    if table.bbox and len(with_bbox) > 1:
        with_bbox.sort(key=lambda t: t.bbox["t"])
        index = next((i for i, t in enumerate(with_bbox) if t.id == table.id), None)
        if index is not None:
            order = f"이 쪽의 표 {len(with_bbox)}개 중 위에서 {index + 1}번째 표"

    parts = [part for part in (order, f"머리가 「{head}」 인 표" if head else "") if part]
    if not parts:
        return ""
    return _TARGET_HEADER + "".join(f"- {part}\n" for part in parts)


def _formula_line() -> str:
    """산식 보존 규칙 줄 (가설 C2). DOCSTRUCT_VLM_KEEP_FORMULA=1 로 켠다.

    입력: 없음 (환경 변수)
    출력: 규칙 한 줄. 꺼져 있으면 빈 문자열
    비고:
        수식이 든 표에서 VLM 이 산식을 계산해 버리는(=12×3 → 36) 손상을
        막는다. 산식과 결과가 함께 적힌 표는 ⑧ 검산이 판독 검증이 된다 —
        산식을 지우면 그 검증 근거도 사라진다.
    """
    import os

    if os.getenv("DOCSTRUCT_VLM_KEEP_FORMULA", "").strip() != "1":
        return ""
    return ("\n- 산식·수식은 계산하거나 풀어 쓰지 말고 보이는 기호 그대로 "
            "옮기세요 (분수는 분자/분모 로).\n")


def _best_of() -> int:
    """후보 수 (가설 H12-b). DOCSTRUCT_VLM_BEST_OF, 기본 1 = 현행.

    입력: 없음 (환경 변수)
    출력: 1~4
    """
    import os

    try:
        n = int(os.getenv("DOCSTRUCT_VLM_BEST_OF", "1"))
    except ValueError:
        n = 1
    return max(1, min(n, 4))


def _candidate_prompts(table) -> list[tuple[str, str, str]]:
    """후보 지시문 목록 — (이름, 틀, 힌트) 보수적 차례.

    입력: table — 대상 TableInfo
    출력: 후보 목록. best_of=1 이면 현행 손잡이 그대로 하나
    비고:
        차례가 곧 동점 규칙이다 — grade.pick_best 는 동점에서 먼저 온
        것을 고르므로, 단문(현행)을 앞에 두면 이득이 분명할 때만 판이
        바뀐다. 힌트 판은 missing 이 실제로 있을 때만 후보가 된다.
    """
    formula = _formula_line()
    hint = _missing_hint(table)
    if _best_of() == 1:
        return [("현행", _prompt_template(), formula + hint)]
    out = [("단문", _PROMPT, formula),
           ("단계별", _PROMPT_STEPS, formula)]
    if hint:
        out.insert(1, ("단문+힌트", _PROMPT, formula + hint))
        out.append(("단계별+힌트", _PROMPT_STEPS, formula + hint))
    return out[:_best_of()]


def _strip_fence(text: str) -> str:
    """```markdown 울타리를 벗긴다.

    입력: text — 모델 응답
    출력: 울타리를 제거한 본문
    """
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _looks_like_table(text: str) -> bool:
    """GFM 표 형태인지.

    입력: text — 모델 응답
    출력: 표로 보이면 True
    비고:
        구분선(`|---|`)이 있고 데이터 줄이 하나 이상 있어야 한다. 모델이
        설명문을 돌려주는 일이 있어 형태를 확인한다.
    """
    rows = [line for line in text.splitlines() if line.strip().startswith("|")]
    if len(rows) < 3:
        return False
    return any(set(row.strip()) <= set("|-: ") for row in rows)


def _grid_guard() -> bool:
    """격자 심사를 켤 것인가 (DOCSTRUCT_REBUILD_GRID_GUARD).

    입력: 없음
    출력: 켜져 있으면 True (기본 켬)
    비고:
        끄면 0.4.70 이전처럼 길이·형태만 보고 받는다. 실측에서 그 경우
        격자가 나빠진 표 14개가 그대로 채택된다.
    """
    import os

    return os.getenv("DOCSTRUCT_REBUILD_GRID_GUARD", "1").strip() not in (
        "0", "false", "False")


def _rebuild_one(page: PageContent, table, cfg: dict[str, Any]) -> str | None:
    """표 하나를 VLM 에 보내 다시 만든다.

    입력: page — 맥락용 페이지, table — 대상 TableInfo, cfg — LLM 설정
    출력: markdown 표. 실패하면 None
    비고:
        페이지 전체 이미지를 보낸다. 표 영역만 잘라 보내면 더 정확하겠지만,
        표 bbox 가 실제보다 좁게 잡히는 것이 바로 이 문제의 원인이므로
        그 좌표를 믿고 자르면 잘린 표를 보여 주게 된다.
    """
    encoded = encode_image_file(page.page_image_path)
    if not encoded:
        return None
    mime, b64 = encoded

    context = re.sub(r"<table \d+>", "", page.content or "")[:400]

    target = _table_target(page, table)

    def _ask(template: str, hint: str) -> str | None:
        """지시문 하나로 후보 하나를 받는다."""
        raw = invoke_llm(
            template.format(context=context.strip() or "(없음)", hint=hint,
                            target=target),
            span_name="table_rebuild",
            image_urls=[f"data:{mime};base64,{b64}"],
            cfg=cfg,
        )
        if not raw:
            return None
        text = raw.strip()
        if text.startswith("```"):
            text = _strip_fence(text)
        if not text or text.replace(" ", "") == _NO_TABLE:
            return None
        if len(text) < MIN_TABLE_CHARS or not _looks_like_table(text):
            return None
        return text

    prompts = _candidate_prompts(table)
    if len(prompts) == 1:                        # 현행 경로 — 채점 없이 그대로
        name, template, hint = prompts[0]
        text = _ask(template, hint)
        if text is None:
            _log.debug("%s 재구성 결과가 표 형태가 아닙니다", table.id)
        return text

    # H12-b: 후보-검증-선택. 탐색은 VLM, 보상은 결정론(grade), 학습 없음.
    from docstruct.tables.grade import pick_best

    candidates: dict[str, str] = {}
    for name, template, hint in prompts:
        text = _ask(template, hint)
        if text is not None:
            candidates[name] = text
    if not candidates:
        return None
    best = pick_best(table, candidates)
    if best is None:
        # 모든 후보가 결정론 문턱 미달 — 원본 유지가 폴백의 폴백이다.
        _log.info("%s 후보 %d개 전부 문턱 미달 — 원본 유지", table.id, len(candidates))
        return None
    name, graded = best
    table.vlm_choice = {"candidates": len(candidates), "chosen": name,
                        "score": graded["score"], "detail": graded["detail"]}
    _log.info("%s 후보 %d개 중 '%s' 채택 (점수 %d)",
              table.id, len(candidates), name, graded["score"])
    return candidates[name]


#: 후보가 지켜야 할 행·열 비율. 1.0 이 아닌 이유: 머리행 처리가 한 줄
#: 달라지는 일이 있다.
MIN_SHAPE_KEEP = 0.9
#: 자리 수를 지켰더라도 글이 이만큼 미만으로 줄면 내용이 비었다고 본다.
#: 병합 복원의 정상 축소는 실측 0.8 배쯤이고, 잘린 후보는 자리 수에서
#: 먼저 걸리므로 이 문턱은 "칸만 남기고 값을 비운" 경우를 잡는 몫이다.
MIN_LENGTH_KEEP = 0.3


def _acceptable(original: str | None, candidate: str) -> bool:
    """후보를 받아들일 수 있는가 — 자리 수와 내용량을 함께 본다.

    입력: original — 원본 markdown, candidate — VLM 후보
    출력: 받아들일 수 있으면 True
    비고:
        대상별로 가드를 나누지 않는다. 길이만 보면 **병합 복원을 되돌리고**
        (실측: 13표 폐기 — `〃`·빈 칸으로 바뀌어 짧아진다), 자리 수만 보면
        **칸만 남기고 값을 비운 후보**를 받는다. 두 조건을 함께 걸면
        둘 다 걸러진다.

        그림 안 표·한자 표도 같은 기준을 쓴다 — OCR 산물인 원본은 잡음이
        많아 되레 길 수 있어(반복·오독 문자), 정확한 판독이 짧아지는 일이
        있다 (실측: 조달청 9쪽 정원표).
    """
    if not _keeps_shape(original, candidate):
        return False
    return len(candidate) >= len(original or "") * MIN_LENGTH_KEEP


def _reject_reason(original: str | None, candidate: str) -> str:
    """왜 받아들이지 않았는지 한 줄 (기록용).

    입력: original — 원본, candidate — 후보
    출력: 사유
    """
    if not _keeps_shape(original, candidate):
        return "결과의 행·열 수가 원본보다 적어"
    return "결과가 원본보다 크게 짧아"


def _keeps_shape(original: str | None, candidate: str) -> bool:
    """후보가 원본의 **행·열 수**를 지켰는가.

    입력: original — 원본 markdown, candidate — VLM 후보
    출력: 행·열이 모두 MIN_SHAPE_KEEP 이상 남았으면 True
    비고:
        병합 복원은 **글자 수는 줄지만 자리 수는 그대로다** — 반복 기재된
        값이 `〃`·빈 칸으로 바뀔 뿐이다. 그래서 글자 길이로 재면 고친 것을
        되돌리고(실측: 13표 폐기), 형태 검사만 하면 잘린 표를 받는다
        (실측: 35% 로 잘린 후보도 문턱 통과). 자리 수로 재면 둘을 가른다.
    """
    from docstruct.tables.grade import cells_from_markdown

    before = cells_from_markdown(original or "")
    after = cells_from_markdown(candidate)
    if not before or not after:
        return False
    def _shape(cells):
        return (max(c["row"] + c["rowspan"] for c in cells),
                max(c["col"] + c["colspan"] for c in cells))
    rows_before, cols_before = _shape(before)
    rows_after, cols_after = _shape(after)
    return (rows_after >= rows_before * MIN_SHAPE_KEEP
            and cols_after >= cols_before * MIN_SHAPE_KEEP)


def _still_has_han(table) -> bool:
    """한자 표시가 **지금도** 맞는가 (묵은 신호 걸러내기).

    입력: table — TableInfo
    출력: 현재 markdown 에도 한자가 많으면 True
    비고:
        `ocr_language_doubt` 는 표시 단계에서 **그때의** markdown 으로
        재어 둔 값이다. 그 뒤 재추출(fill)이 표를 한글로 고쳐 놓으면 표시는
        묵은 값이 된다 — 실측(조달청 9쪽 table_4): fill 이 한글 표를 만든
        뒤에도 표시가 남아 VLM 을 한 번 더 불렀고, 그 결과가 fill 판보다
        짧아 폐기됐다. 헛걸음이다.

        고친 뒤에도 대상으로 남을지는 **지금 글**이 답한다.
    """
    from docstruct.converters.pdf.ocr_language import wrong_language

    return wrong_language(table.markdown) is not None


def _reason_of(table) -> str:
    """이 표가 VLM 으로 간 까닭을 사람 말로 (기록용).

    입력: table — TableInfo
    출력: 한 줄 설명
    비고:
        `needs_vlm` 의 네 조건과 짝을 이룬다. 0.3.82 에서 대상을 넓힐 때
        이 기록이 `odd_columns` 만 가정한 채 남아 있었고, 새 대상(합의·
        한자·그림 안 표)이 들어오자 언팩에서 죽었다 — **대상을 넓히면
        그 대상을 설명하는 자리도 함께 넓혀야 한다.**
    """
    if table.odd_columns:
        width, majority = table.odd_columns
        return f"{width}열 → 다수인 {majority}열"
    if table.ocr_language_doubt:
        ratio = table.ocr_language_doubt.get("ratio")
        return f"한자로 나온 표 (비율 {ratio})"
    if table.source_image_id:
        return f"그림({table.source_image_id}) 안에 있는 표"
    for name in ("grid_merge_gap", "synth_grid", "scan_grid"):
        report = getattr(table, name, None)
        if (report and report.get("confidence") == "high"
                and report.get("missing")):
            return f"지면 격자에 있으나 인식에 없는 병합 {len(report['missing'])}개"
    return "결함 표시"


def needs_vlm(table) -> bool:
    """이 표를 VLM 이 받아야 하는가 — 결정론이 물러난 자리인가.

    입력: table — TableInfo
    출력: 대상이면 True
    비고:
        사다리(가설 문서 §17)의 낙하 지점을 그대로 옮긴 것이다. 셋 중
        하나면 받는다.

            odd_columns          같은 서식 표들과 열 수가 다르다
            ocr_language_doubt   한글 지면이 한자로 나왔다 (0.3.81)
            source_image_id      표가 그림 안에 있다 — OCR 산물이다 (0.3.84)
            표시됐으나 미복원      ⑥⑨⑩이 **신뢰 high** 로 병합을 짚었는데
                                 ⑦이 복원하지 못한 표 (source != "grid")

        마지막 조건이 핵심이다: 지면에는 병합이 그려져 있는데 인식에는
        없고 결정론 복원도 못 한 표 — 남은 경로가 VLM 뿐인 자리다.
        **신뢰 high 만** 받는다 (낮은 덮개는 자리 번호가 어긋나 실측
        정밀도 83%). 실측(조달청 59표): 이 조건으로 13표(22%)가 잡힌다.
    """
    if table.odd_columns:
        return True
    if table.ocr_language_doubt and _still_has_han(table):
        return True
    if table.source_image_id:
        # 그림 안에 있는 표 — 글자가 래스터 OCR 산물이다 (조달청 9쪽
        # 조직도 실측). 언어 검사가 못 잡는 경우(OCR 이 그럴듯한 한글을
        # 지어낸 경우)까지 받는 더 일반적인 신호다.
        return True
    if getattr(table, "source", None) == "grid":
        return False                             # ⑦이 이미 복원했다
    if getattr(table, "head_grid", None):
        # ⑫가 머리 계층을 이미 복원했다. 그 위에 VLM 이 표를 다시 쓰면
        # **복원한 병합이 지워진다** — 실측(행안부 112쪽 table_57):
        # ⑫가 세운 병합 3개를 VLM 이 0개로 만들었다(프로그램명을 두 줄로
        # 쪼개고 `〃` 를 쓰지 않았다). 결정론이 이긴 자리는 지킨다.
        return False
    if getattr(table, "agreed_grid", None):
        # ⑭이 합의 병합(두 기하 근거 일치 — 행안부 정답 대조 100.0%)을
        # 이미 심었다. ⑫와 같은 이유로 지킨다 — 이 보호가 없으면 ⑥⑨의
        # 잔여 missing 표시(high) 때문에 같은 표가 VLM 대상으로 다시
        # 잡혀, 심어 둔 병합을 VLM 재작성이 지울 수 있다 (0.4.2).
        return False
    for name in ("grid_merge_gap", "synth_grid", "scan_grid"):
        report = getattr(table, name, None)
        if (report and report.get("confidence") == "high"
                and report.get("missing")):
            return True
    return False


def rebuild_broken_tables(pages: list[PageContent], *, progress: bool = False) -> int:
    """구조가 깨진 표를 VLM 으로 다시 만든다.

    입력: pages — 대상 페이지 목록 (제자리 갱신), progress — 진행 표시 여부
    출력: 다시 만든 표 수
    비고:
        `TableInfo.odd_columns` 또는 `ocr_language_doubt`(한글 지면이
        한자로 나온 표 — 중국어 모델 OCR 산물)가 표시된 표만 고른다. 같은 서식 표 다수와
        열 수가 다른 표이며, `flag_odd_tables` 단계가 채운다.

        **원본을 보관한다.** 다시 만든 표가 원본보다 짧으면 되돌린다 —
        VLM 이 표를 일부만 옮기는 일이 있고, 그때 원본을 잃으면 손해다.
    """

    targets = [
        (page, table)
        for page in pages
        for table in page.tables
        if needs_vlm(table) and page.page_image_path
    ]
    if not targets:
        return 0

    cfg = llm_api_config()
    if not cfg:
        _log.warning("LLM 이 설정되지 않아 표 재구성을 건너뜁니다 (%d개)", len(targets))
        return 0

    # **동시 실행.** 표마다 한 번씩 부르므로 순차로 두면 대기가 그대로
    # 쌓인다 — 실측(행안부 429쪽): 대상 178표 · 776.9초 (표당 4.4초).
    # assess·fill 과 같은 방식으로 llm_concurrency 를 따른다. 결과 반영은
    # 순서에 무관하다(표마다 독립) — 그래서 병렬로 안전하다.
    workers = min(get_settings().llm_concurrency, len(targets))
    results: dict[str, str | None] = {}
    if workers <= 1:
        for page, table in targets:
            try:
                results[table.id] = _rebuild_one(page, table, cfg)
            except Exception as exc:             # noqa: BLE001 - 한 표 실패로 멈추지 않는다
                _log.warning("%s 재구성 실패: %s", table.id, exc)
    else:
        _log.info("표 재구성 %d개 · 동시 %d개", len(targets), workers)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_rebuild_one, page, table, cfg): table
                       for page, table in targets}
            for future in as_completed(futures):
                table = futures[future]
                try:
                    results[table.id] = future.result()
                except Exception as exc:         # noqa: BLE001
                    _log.warning("%s 재구성 실패: %s", table.id, exc)

    def _grid_regressed(before_markdown: str, after_cells) -> str:
        """재구성이 격자를 나쁘게 만들었는가.

        입력: before_markdown — 원본 표, after_cells — 재구성 결과의 셀
        출력: 나빠졌으면 사유 문자열, 아니면 빈 문자열
        비고:
            **표는 직사각 격자다.** 모든 자리가 정확히 한 셀에 덮여야
            하고, 그것은 원본을 몰라도 확인된다(0.4.69). 재구성 결과가
            그 점에서 원본보다 나쁘면 받을 이유가 없다.

            실측(문체부 609쪽)이 이 가드를 요구했다. VLM 재구성으로
            채택된 21표를 원본과 대조하니:

                나빠짐 **14** · 그대로 7 · **좋아짐 0**

            나빠진 14표는 **전부 원본이 결함 0** 이었다. 대개
            `13열×3행 → 13열×4행` 으로 행이 하나 늘면서 마지막 행이
            일부만 차 구멍 7칸이 생겼다 — 모델이 표 아래에 줄을 하나
            더 붙인 것이다.

            길이·형태 가드(`_acceptable`)는 이것을 못 잡는다. 글자 수도
            비슷하고 markdown 꼴도 표이기 때문이다.
        """
        if not _grid_guard():
            return ""
        after = grid_check(after_cells)
        if after is None or after["ok"]:
            return ""
        before = grid_check(cells_from_markdown(before_markdown))
        if before is None:
            return ""
        gap_before = before["holes"] + before["overlaps"]
        gap_after = after["holes"] + after["overlaps"]
        if gap_after <= gap_before:
            return ""
        return (f"격자가 나빠짐 (결함 {gap_before} → {gap_after}칸 · "
                f"{before['width']}×{before['height']} → "
                f"{after['width']}×{after['height']})")

    rebuilt = 0
    for page, table in targets:
        markdown = results.get(table.id)
        if not markdown:
            continue
        # 원본보다 짧아지면 되돌린다 — VLM 이 표를 일부만 옮기는 일이 있다.
        #
        # 단, **병합 복원은 짧아지는 것이 정상이다.** 인식이 병합을 놓쳐
        # 값을 행마다 반복해 적었다면, 바르게 복원한 표는 `〃`·빈 칸이
        # 되어 글자 수가 줄어든다. 실측(조달청 첫 VLM 실행): 대상 14표 중
        # **13표가 이 가드에 걸려 폐기**됐는데 그 대부분이 병합 표시
        # (⑨ synth_grid) 로 온 표였다 — 고치려던 것을 가드가 되돌린 것이다.
        #
        # 그래서 병합 복원이 목적인 표는 길이 대신 **결정론 채점**으로 본다:
        # 형태가 표이고 ⑧ 검산이 깨지지 않으면 짧아도 받는다.
        if not _acceptable(table.markdown, markdown):
            page.trace.add(
                "docstruct.tables.vlm_rebuild", "재구성 폐기",
                f"{table.id} · {_reject_reason(table.markdown, markdown)} — "
                f"원본을 유지합니다",
                status="warn",
            )
            continue
        # **격자로도 심사한다** (0.4.70). 셀을 먼저 만들어 보고, 격자가
        # 원본보다 나빠졌으면 받지 않는다.
        rebuilt_cells = cells_from_markdown(markdown)
        regressed = _grid_regressed(table.markdown, rebuilt_cells)
        if regressed:
            page.trace.add(
                "docstruct.tables.vlm_rebuild", "재구성 폐기",
                f"{table.id} · {regressed} — 원본을 유지합니다",
                status="warn",
            )
            continue
        table.original_markdown = table.markdown
        table.markdown = markdown
        table.source = "vlm"
        # **어느 모델이 냈는지 남긴다.** `source="vlm"` 만으로는 사내
        # 엔드포인트로 읽은 것과 OpenAI 로 읽은 것을 결과물에서 구별할 수
        # 없다 — A/B 를 결과만 보고 가리려면 필요하다. ImageInfo.vlm_model
        # · PageContent.ocr_engine 과 같은 이유다.
        table.vlm_model = str((cfg or {}).get("model") or "vlm")
        # **cells 도 함께 갱신한다.** markdown 만 바꾸면 병합이 그림의 떡이
        # 된다 — 채점기·구조화 전개·⑧ 검산은 전부 `cells` 를 읽는다.
        # ⑦(grid_restore)은 이미 둘 다 갱신하는데 VLM 경로만 빠져 있었다.
        if rebuilt_cells:
            table.cells = rebuilt_cells
        rebuilt += 1
        page.trace.add(
            "docstruct.tables.vlm_rebuild", "표 재구성",
            f"{table.id} · {_reason_of(table)} — VLM 재작성",
        )
    return rebuilt
