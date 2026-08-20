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

import logging
import re
from typing import Any

from docstruct.infrastructure.llm.client import invoke_llm, llm_api_config
from docstruct.media.vlm_read import encode_image_file
from docstruct.models import PageContent

_log = logging.getLogger(__name__)

#: 다시 만든 표가 이보다 짧으면 실패로 본다.
MIN_TABLE_CHARS = 20

#: 표를 다시 만들 때 쓰는 지시문.
_PROMPT = """이 이미지에서 표 하나를 찾아 GFM markdown 표로 옮기세요.

규칙:
- 보이는 대로만 옮깁니다. 읽을 수 없는 칸은 빈 칸으로 두세요.
- 내용을 추측하거나 채워 넣지 마세요.
- 병합된 칸은 맨 왼쪽 위에만 값을 넣고 나머지는 빈 칸으로 둡니다.
- 표가 없거나 읽을 수 없으면 정확히 `없음` 이라고만 답하세요.
- 설명 없이 표만 출력하세요.

참고 (이 표 주변 본문):
{context}"""

_NO_TABLE = "없음"


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
    raw = invoke_llm(
        _PROMPT.format(context=context.strip() or "(없음)"),
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
        _log.debug("%s 재구성 결과가 표 형태가 아닙니다", table.id)
        return None
    return text


def rebuild_broken_tables(pages: list[PageContent], *, progress: bool = False) -> int:
    """구조가 깨진 표를 VLM 으로 다시 만든다.

    입력: pages — 대상 페이지 목록 (제자리 갱신), progress — 진행 표시 여부
    출력: 다시 만든 표 수
    비고:
        `TableInfo.odd_columns` 가 표시된 표만 고른다. 같은 서식 표 다수와
        열 수가 다른 표이며, `flag_odd_tables` 단계가 채운다.

        **원본을 보관한다.** 다시 만든 표가 원본보다 짧으면 되돌린다 —
        VLM 이 표를 일부만 옮기는 일이 있고, 그때 원본을 잃으면 손해다.
    """

    targets = [
        (page, table)
        for page in pages
        for table in page.tables
        if table.odd_columns and page.page_image_path
    ]
    if not targets:
        return 0

    cfg = llm_api_config()
    if not cfg:
        _log.warning("LLM 이 설정되지 않아 표 재구성을 건너뜁니다 (%d개)", len(targets))
        return 0

    rebuilt = 0
    for page, table in targets:
        try:
            markdown = _rebuild_one(page, table, cfg)
        except Exception as exc:                 # noqa: BLE001 - 한 표 실패로 멈추지 않는다
            _log.warning("%s 재구성 실패: %s", table.id, exc)
            continue
        if not markdown:
            continue
        # 원본보다 짧아지면 되돌린다. VLM 이 표를 일부만 옮기는 일이 있다.
        if len(markdown) < len(table.markdown or "") * 0.6:
            page.trace.add(
                "docstruct.tables.vlm_rebuild", "재구성 폐기",
                f"{table.id} · 결과가 원본보다 짧아 원본을 유지합니다",
                status="warn",
            )
            continue
        table.original_markdown = table.markdown
        table.markdown = markdown
        table.source = "vlm"
        rebuilt += 1
        width, majority = table.odd_columns
        page.trace.add(
            "docstruct.tables.vlm_rebuild", "표 재구성",
            f"{table.id} · {width}열 → 다수인 {majority}열에 맞춰 VLM 재작성",
        )
    return rebuilt
