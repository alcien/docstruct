"""실험 ① — 격자 경계를 좌표로 미세 조정한다.

무엇을 보완하는가
--------------
표 구조 인식이 이미지를 축소해 넣으면서 **좁은 열의 경계가 뭉개진다.**
성과계획서의 연도 열(`'23`~`'29`)처럼 폭이 작은 열이 특히 그렇다.

어디서 빌린 발상인가
-----------------
SEMv3 의 KOR(Keypoint Offset Regression) — 분리선을 절대 좌표로 예측하지
않고 **제안점 대비 오프셋만 회귀**한다. 위치 사전정보를 살리는 방식이다.

우리는 파서 격자를 제안으로 삼고, 조각 좌표로 **경계만 조금 옮긴다.**
격자를 새로 세우지 않으므로 0.3.4 의 실패(13회 시도 13회 폐기)를 피한다.

한계
----
- **조정 폭이 좁다.** 크게 어긋난 격자는 못 고친다 — 그것은 다른 문제다.
- 스캔본은 좌표가 OCR 결과라 조정이 오히려 해로울 수 있다. 미검증.
"""
from __future__ import annotations

import logging
import os
import statistics

from docstruct.experiments.registry import Experiment, register
from docstruct.models import PageContent

_log = logging.getLogger(__name__)

#: 이보다 작은 어긋남은 셀 안쪽 여백으로 본다.
#: 실측: 정상 표들이 0.9~4.0pt 로 나왔다 — 전부 여백이다.
MIN_MEANINGFUL_DRIFT = 4.0

#: 경계를 옮길 최대 거리 (포인트). 이보다 멀면 다른 열이다.
NUDGE_ENV = "DOCSTRUCT_EXP_REFINE_NUDGE"
DEFAULT_NUDGE = 4.0


def _nudge_limit() -> float:
    """조정 한도.

    입력: 없음 (`DOCSTRUCT_EXP_REFINE_NUDGE`)
    출력: 포인트 단위 실수
    """
    raw = os.environ.get(NUDGE_ENV, "").strip()
    if not raw:
        return DEFAULT_NUDGE
    try:
        value = float(raw)
    except ValueError:
        _log.warning("%s 값이 숫자가 아닙니다: %r", NUDGE_ENV, raw)
        return DEFAULT_NUDGE
    return value if value > 0 else DEFAULT_NUDGE


def refine_edges(edges: list[float], observed: list[float]) -> list[float]:
    """제안 경계를 관측값 쪽으로 조금 옮긴다.

    입력: edges — 파서가 준 경계, observed — 조각에서 관측된 경계 후보
    출력: 조정된 경계 목록
    비고:
        **제안 대비 오프셋만** 준다. 관측값이 한도 안에 없으면 그대로 둔다 —
        먼 값은 다른 열의 경계다.
    """
    limit = _nudge_limit()
    out: list[float] = []
    for edge in edges:
        near = [o for o in observed if abs(o - edge) <= limit]
        out.append(statistics.median(near) if near else edge)
    return out


def run(pages: list[PageContent], *, scale: float = 2.0, **_kwargs) -> int:
    """열 경계가 조정될 만한 표를 표시한다.

    입력: pages — 페이지 목록, scale — 렌더 배율
    출력: 표시한 표 수
    비고:
        **격자를 바꾸지 않는다.** 얼마나 어긋났는지만 기록한다 — 실제 조정은
        검증 뒤에 붙인다.
    """
    from docstruct.converters.pdf.text_runs import read_text_runs

    source = _kwargs.get("pdf_path")
    marked = 0
    for page in pages:
        if not page.tables or not isinstance(page.page_no, int):
            continue
        # **PDF 텍스트 좌표를 직접 읽는다.** 렌더 + OCR 을 거치면 79쪽
        # 전체를 그려야 하고 오차만 더해진다 — 원본보다 정확할 수 없다.
        runs = read_text_runs(source, page.page_no) if source else []
        if not runs:
            continue
        observed = sorted({round(r.left, 1) for r in runs})

        for table in page.tables:
            item = getattr(table, "source_item", None)
            cells = [c for c in (getattr(getattr(item, "data", None),
                                         "table_cells", None) or [])
                     if getattr(c, "bbox", None) is not None]
            if not cells or not observed:
                continue
            edges = sorted({round(float(c.bbox.l), 1) for c in cells})
            moved = refine_edges(edges, observed)
            # **셀 경계와 글자 시작점은 원래 다르다** — 안쪽 여백 때문이다.
            # 실측(국세청 성과보고서)에서 61개 표가 전부 0.9~4.0pt 어긋난
            # 것으로 나왔는데, 그것이 정상 여백이었다.
            #
            # 여백을 넘어서는 어긋남만 본다.
            drift = [abs(a - b) for a, b in zip(edges, moved)
                     if abs(a - b) > MIN_MEANINGFUL_DRIFT]
            if not drift:
                continue
            table.edge_drift = round(max(drift), 1)
            marked += 1
            page.trace.add(
                "experiments.grid_refine", "열 경계 어긋남",
                f"{table.id} · 최대 {max(drift):.1f}pt · {len(drift)}곳")
    return marked


register(Experiment(
    key="grid_refine",
    title="열 경계를 좌표로 미세 조정",
    purpose="좁은 열이 리사이즈에서 뭉개지는 문제",
    origin="SEMv3 KOR — 제안 대비 오프셋만 회귀한다는 발상",
    formats=("pdf",),
    status="proposed",
    note="지금은 어긋난 정도만 기록한다(격자 변경 없음). "
         "스캔본은 좌표가 OCR 결과라 미검증.",
    run=run,
    knobs={NUDGE_ENV: f"경계를 옮길 최대 거리 (기본 {DEFAULT_NUDGE}pt)"},
))
