"""실험 chart_gate — 그림 판독 경로가 어떻게 갈렸는지 계측 (측정 전용).

입력:
    그림 ImageInfo 목록 + region_kind·legibility 값
출력:
    없음 — ImageInfo.chart_gate 계측 기록

역할:
    그림마다 **어느 지시문으로 읽혔는지**와 그것을 정한 신호값을 남긴다.
    판독 결과는 바꾸지 않는다.
호출부:
    pipeline (실험이 켜졌을 때) — 사다리 밖, 검증 계열
설정:
    DOCSTRUCT_EXP_CHART_GATE=1 (기본 꺼짐)

왜 있는가 — 같은 그림이 형식에 따라 다르게 읽혔다
----------------------------------------------
조달청 조직도 하나를 두 형식으로 돌린 실측이다.

    HWPX  text_rows 52 · per_row 19.0  →  **전사**(scan_vlm 지시문)
    PDF   text_rows 61 · per_row 15.0  →  **도해**(계층 목록 지시문)

같은 문서의 같은 그림인데 판독 경로가 갈렸다. `per_row` 가
`CHART_MAX_PER_ROW`(16.0) 를 사이에 두고 19.0 과 15.0 으로 놓였기
때문이다 — HWPX 는 원본 이미지를 그대로 쓰고 PDF 는 지면을 렌더하므로
같은 그림도 화소가 달라지고, 그 차이가 문턱을 넘나든다.

경로가 갈리면 결과의 성격이 갈린다. 전사는 지면 글을 그대로 옮기고,
도해는 계층 목록으로 요약한다. 나아가 **검증 대상 여부까지 갈린다** —
`scan_ab`(이중 판독)는 전사된 것만 본다(0.4.58).

그런데 지금은 **어느 경로로 읽혔는지가 결과물에 남지 않는다.** 위
사실도 손으로 코드를 따라가며 알아냈다. 문턱 하나가 결과를 가르는데
그 문턱 근처에 실물이 얼마나 몰려 있는지 모르는 상태다.

무엇을 재는가
-----------
    kind        legibility 가 본 종류 (page / figure / decoration)
    verdict     판독 가능성 (good / fair / poor)
    text_rows   글자 줄 수          ┐ 도해 분기를 정하는 두 신호
    per_row     줄당 글자 수        ┘ (둘 다 만족해야 도해다)
    route       실제로 고른 경로 (transcribe / chart / describe / skip)
    margin      **두 문턱 각각**까지 얼마나 남았나 — 이것이 핵심 수치다.
                0 에 가까운 그림이 많으면 그 문턱은 문서 사정에 따라
                결과를 뒤집는다는 뜻이다

**두 축을 다 잰다** (0.4.65). 처음에는 `per_row` 쪽만 쟀는데, 실측에서
`image_3`(줄 17 · 문턱 20)이 **줄 수 축에서 3 차이**로 갈렸는데도 경계로
잡히지 않았다. 결정은 두 조건의 논리곱이므로 어느 한쪽만 재면 절반을
놓친다.

경계에 놓인 것은 **양쪽으로 읽어 본다** (0.4.67)
----------------------------------------------
분포만으로는 문턱을 옮길지 정할 수 없다. **어느 경로가 옳은 판독이었는지**
를 한 번도 확인한 적이 없기 때문이다 — 조달청 조직도를 전사로 읽은 것이
나은지 도해로 읽은 것이 나은지 모른 채, 문턱 근처에 몰려 있다는 사실만
알고 있었다.

경계로 잡힌 그림만 **반대 지시문으로 한 번 더** 읽어 `alt` 에 담는다.
사람이 둘을 나란히 보고 어느 쪽이 지면에 가까운지 찍으면 그것이 정답이
된다. 실측에서 경계는 두 부처 합쳐 셋뿐이었으므로 비용은 세 번이다.

    DOCSTRUCT_EXP_CHART_GATE_AB=0 으로 끌 수 있다 (기본 켬 — 경계일 때만)

무엇을 하고 무엇을 안 하나
------------------------
    한다     경로와 신호값을 그림마다 남긴다
             경계인 그림은 반대 지시문으로도 읽어 함께 남긴다
    안 한다  경로를 바꾸는 일 (측정 전용 — `alt` 는 기록일 뿐 본문에
             들어가지 않는다)
             둘 중 어느 쪽이 옳은지 판정 (지면을 봐야 안다)
             문턱 조정 (정답이 모이기 전에는 옮기지 않는다)
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from docstruct.experiments.registry import Experiment, register

if TYPE_CHECKING:
    from docstruct.models import PageContent

_log = logging.getLogger(__name__)

#: 문턱까지 이만큼 안쪽이면 "경계에 놓였다" 고 본다.
#: 실측(조달청 조직도)이 두 형식에서 +1.0 과 -3.0 으로 갈렸으므로 3.0
#: 이면 양쪽을 모두 잡는다.
NEAR_PER_ROW = 3.0
#: 줄 수 축의 근접 폭. 실측 `image_3` 이 17줄(문턱 20)로 3 차이였다.
NEAR_ROWS = 3.0


def route_of(info, legible: dict) -> str:
    """이 그림이 어느 지시문으로 읽혔는지 되짚는다.

    입력: info — ImageInfo, legible — legibility 결과
    출력: transcribe | chart | describe | skip
    비고:
        `vlm_read._read_one` 의 분기를 **그대로** 되짚는다. 판독할 때
        기록해 두는 편이 더 정확하지만, 그러려면 판독 경로를 건드려야
        한다 — 측정 실험이 본체를 고치게 할 수는 없다. 대신 분기 조건이
        바뀌면 여기도 바뀌어야 하므로, 시험으로 둘을 묶어 둔다.
    """
    from docstruct.images.vlm_read import (CHART_MAX_PER_ROW, CHART_MIN_ROWS,
                                          is_page_like)

    verdict = legible.get("verdict")
    if verdict == "decoration":
        return "skip"
    if verdict == "poor":
        # 흐린 그림은 옮기라고 하지 않고 설명하라고 한다 — 마지막에
        # 덮어쓰는 분기라 가장 먼저 본다.
        return "describe"
    rows = legible.get("text_rows") or 0
    per_row = legible.get("per_row") or 0
    if rows >= CHART_MIN_ROWS and per_row < CHART_MAX_PER_ROW:
        return "chart"
    return "transcribe" if is_page_like(info, legible) else "picture"


def margins(legible: dict) -> dict[str, float | None]:
    """두 문턱까지의 거리를 잰다.

    입력: legible — legibility 결과
    출력: {"per_row": …, "rows": …} — 잴 수 없으면 None
    비고:
        **부호를 살린다.** 각 값이 양수면 그 조건이 도해 쪽을 만족한다는
        뜻이다.

            per_row  = CHART_MAX_PER_ROW - per_row   (per_row < 문턱이면 통과)
            rows     = text_rows - CHART_MIN_ROWS    (rows >= 문턱이면 통과)

        실측: 조달청 조직도가 PDF 에서 per_row 15.0 → +1.0(도해),
        HWPX 에서 19.0 → -3.0(전사)으로 갈렸다. `image_3` 은 per_row 는
        +11.0 으로 여유가 있는데 rows 가 -3.0 이라 도해가 되지 못했다.
    """
    from docstruct.images.vlm_read import CHART_MAX_PER_ROW, CHART_MIN_ROWS

    per_row = legible.get("per_row")
    rows = legible.get("text_rows")
    return {
        "per_row": (None if per_row is None
                    else round(CHART_MAX_PER_ROW - float(per_row), 2)),
        "rows": None if rows is None else float(rows) - CHART_MIN_ROWS,
    }


def is_borderline(margins_: dict, near_per_row: float = NEAR_PER_ROW,
                  near_rows: float = NEAR_ROWS) -> bool:
    """문턱 하나만 조금 달라져도 경로가 뒤집히는 자리인가.

    입력: margins_ — margins() 결과, near_* — 각 축의 근접 폭
    출력: 뒤집힐 수 있으면 True
    비고:
        도해 판정은 **두 조건의 논리곱**이다. 그러므로

            도해인 경우      어느 한쪽이든 조금 모자라면 도해가 아니게 된다
            도해가 아닌 경우 **막고 있는 쪽**이 조금만 넘으면 도해가 된다.
                             다른 쪽은 이미 통과해 있어야 한다 — 둘 다
                             모자라면 문턱 하나로 뒤집히지 않는다

        이 구분이 없으면 `image_2`(줄 4 · 한참 모자람)까지 경계로 세게
        되어 수치가 무의미해진다.
    """
    per_row, rows = margins_.get("per_row"), margins_.get("rows")
    if per_row is None or rows is None:
        return False
    passes_per_row, passes_rows = per_row > 0, rows >= 0
    if passes_per_row and passes_rows:                # 지금 도해다
        return per_row <= near_per_row or rows <= near_rows
    if passes_rows and not passes_per_row:            # per_row 가 막고 있다
        return abs(per_row) <= near_per_row
    if passes_per_row and not passes_rows:            # rows 가 막고 있다
        return abs(rows) <= near_rows
    return False                                      # 둘 다 모자라다


def _alt_read(page, info, route: str):
    """경계에 놓인 그림을 **반대 지시문으로** 한 번 더 읽는다.

    입력: page — PageContent (trace 기록용), info — ImageInfo, route — 고른 경로
    출력: (반대 판독 본문, 쓴 지시문 이름). 못 읽으면 None
    비고:
        도해로 갔으면 전사 지시문으로, 전사·그림으로 갔으면 도해 지시문으로
        읽는다. 실패는 조용히 물러난다 — 측정이 본체를 멈추게 할 수 없다.
    """
    if os.getenv("DOCSTRUCT_EXP_CHART_GATE_AB", "1").strip() in ("0", "false"):
        return None
    image = getattr(info, "image_path", None)
    if not image:
        return None

    from docstruct.infrastructure.llm.client import invoke_llm, llm_api_config

    cfg = llm_api_config()
    if cfg is None:
        return None
    try:
        from docstruct.images.encode import encode_image_file
        from docstruct.images.vlm_read import _CHART_PROMPT, _strip_fence
        from docstruct.text.scan_vlm import _PROMPT as PAGE_PROMPT

        other = "transcribe" if route == "chart" else "chart"
        prompt = (PAGE_PROMPT if other == "transcribe" else _CHART_PROMPT)
        encoded = encode_image_file(image)
        if not encoded:
            return None
        mime, b64 = encoded
        raw = invoke_llm(
            prompt.format(context=(page.content or "")[:400] or "(없음)"),
            span_name="chart_gate_ab",
            image_urls=[f"data:{mime};base64,{b64}"],
            cfg=cfg,
        )
    except Exception as exc:                     # noqa: BLE001
        page.trace.add("docstruct.experiments.image.chart_gate", "반대 판독 생략",
                       f"{info.id} · {str(exc)[:70]}", status="warn")
        return None
    text = (raw or "").strip()
    if text.startswith("```"):
        text = _strip_fence(text)
    return (text, other) if text else None


def run(pages: list[PageContent], **kwargs) -> int:
    """그림마다 판독 경로와 그것을 정한 신호를 남긴다.

    입력: pages — 페이지 목록 (chart_gate 필드만 채운다)
    출력: 경계에 놓인 그림 수
    """
    from docstruct.images.vlm_read import CHART_MAX_PER_ROW, CHART_MIN_ROWS

    near = 0
    for page in pages:
        for info in page.images or []:
            legible = getattr(info, "legibility", None)
            if not legible:
                # 판독 대상이 아니었다 — 잰 적이 없으므로 남길 것도 없다.
                continue
            route = route_of(info, legible)
            gaps = margins(legible)
            # **도해 문턱이 실제로 결정한 경우에만** 경계를 따진다.
            # 흐린 그림(describe)·장식(skip)은 그 앞 분기에서 갈리므로
            # 문턱을 아무리 넘나들어도 결과가 바뀌지 않는다.
            decided_by_thresholds = route in ("chart", "transcribe", "picture")
            borderline = decided_by_thresholds and is_borderline(gaps)
            info.chart_gate = {
                "route": route,
                "kind": legible.get("kind"),
                "verdict": legible.get("verdict"),
                "text_rows": legible.get("text_rows"),
                "per_row": legible.get("per_row"),
                # 두 문턱까지의 거리. 양수면 그 조건이 도해 쪽을 만족한다.
                "per_row_margin": gaps["per_row"],
                "rows_margin": gaps["rows"],
                "per_row_threshold": CHART_MAX_PER_ROW,
                "rows_threshold": CHART_MIN_ROWS,
                "borderline": borderline,
            }
            if borderline:
                near += 1
                got = _alt_read(page, info, route)
                if got:
                    alt_text, alt_route = got
                    # **본문에 넣지 않는다.** 사람이 나란히 보고 고르라고
                    # 두는 기록이다.
                    info.chart_gate["alt"] = {
                        "route": alt_route,
                        "text": alt_text[:4000],
                        "len": len(alt_text),
                        "main_len": len(info.vlm_markdown or ""),
                    }
                page.trace.add(
                    "docstruct.experiments.image.chart_gate", "판독 경로가 경계에",
                    f"{info.id} · {route} · 줄 {legible.get('text_rows')}"
                    f"(문턱 {CHART_MIN_ROWS}, 차 {gaps['rows']}) · 줄당 "
                    f"{legible.get('per_row')}자(문턱 {CHART_MAX_PER_ROW}, "
                    f"차 {gaps['per_row']}) — 문서 사정이 조금만 달라도 "
                    "다른 지시문으로 읽힙니다", status="warn")
            else:
                page.trace.add(
                    "docstruct.experiments.image.chart_gate", "판독 경로",
                    f"{info.id} · {route} · {legible.get('text_rows')}줄 · "
                    f"줄당 {legible.get('per_row')}자")
    return near


register(Experiment(
    key="chart_gate",
    title="그림 판독 경로 계측 (전사 / 도해 / 설명)",
    purpose="문턱 하나가 결과의 성격을 가르는데 그 사실이 결과물에 남지 "
            "않는다 — 경계에 얼마나 몰려 있는지 본다",
    origin="조달청 조직도가 HWPX 에서는 전사(per_row 19.0), PDF 에서는 "
           "도해(15.0)로 갈렸다 — 문턱은 16.0 이다",
    formats=("pdf", "hwpx", "hwp"),
    needs=("image",),
    status="testing",
    stage="images",
    note="측정 전용 · 판독 불변. 경로는 `vlm_read._read_one` 의 분기를 "
         "되짚어 재현하므로, 그 분기가 바뀌면 여기도 바뀌어야 한다"
         "(시험으로 묶어 두었다). 0.4.62 신설.",
    run=run,
))
