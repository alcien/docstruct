"""그림 **안에** 있는 표를 그림과 잇는다 (라우팅 교정).

입력:
    표 목록 + 그림 목록
출력:
    양쪽에 관계 표시

역할:
    표 영역이 그림 영역 안에 통째로 들어 있으면, 그 표의 글자는 텍스트
    레이어가 아니라 **래스터를 OCR 한 결과**다. 그 사실을 표와 그림
    양쪽에 남겨, VLM 경로가 그 자리를 받게 한다.
호출부:
    pipeline (표 표시 단계)
설정:
    없음 — 순수 기하 판정이다.

왜 필요한가 (조달청 9쪽 실측)
--------------------------
PPT 로 만든 조직도를 그림으로 붙인 쪽이다. 그 그림 안에 정원표가 그려져
있고, docling 이 그 영역을 표로 잡아 **내장 OCR(중국어 모델)** 로 읽었다:

    image_2  bbox (87,178)-(528,727) · 지면의 48% · region_kind="chart"
    table_4  bbox (111,617)-(526,725)  ← image_2 **안**
             | 7是 | 77 | 歪 |   ("구 분 / 기 구 / 기준정원")

두 가지가 어긋나 있었다.

    ① 조직도가 "chart" 로 판정됐다 — 판정 근거가 "글자 0자 · 래스터" 라
       59쪽의 진짜 막대그래프와 구분이 안 된다. chart 로 두면 값 읽기
       (chart_read)로 가는데, 조직도에는 읽을 값이 없다.
    ② 표가 그림에서 나왔다는 사실이 어디에도 남지 않았다 — 그래서 품질
       판정(LLM)은 그 표를 `sufficient` 로 통과시켰다.

**표가 그림 안에 있다**는 것은 순수 기하로 확인되는 사실이고, "이 글자는
OCR 산물" 이라는 뜻이다. 언어 검사(한자 비율)가 못 잡는 경우 — OCR 이
그럴듯한 한글을 지어낸 경우 — 까지 받는 더 일반적인 신호다.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docstruct.models import PageContent

_log = logging.getLogger(__name__)

#: 포함 판정 여유(pt). 레이아웃 모델의 경계가 몇 pt 어긋난다.
PAD = 3.0


def _inside(inner: dict, outer: dict) -> bool:
    """inner 가 outer 안에 통째로 드는가.

    입력: inner/outer — TOPLEFT {l,t,r,b}
    출력: 들면 True
    """
    if not inner or not outer:
        return False
    return (outer["l"] - PAD <= inner["l"] and outer["t"] - PAD <= inner["t"]
            and inner["r"] <= outer["r"] + PAD and inner["b"] <= outer["b"] + PAD)


def link_tables_in_pictures(pages: list[PageContent]) -> int:
    """그림 안에 든 표를 그림과 잇고, 그림의 종류를 바로잡는다.

    입력: pages — 페이지 목록 (제자리 갱신)
    출력: 이어붙인 표 수
    비고:
        표에는 `source_image_id` 를, 그림에는 `table_candidate` 를 남긴다.
        그리고 그 그림이 `chart` 로 판정돼 있으면 `image` 로 내린다 —
        **표가 그려진 그림은 값 읽기 대상이 아니라 통째로 다시 읽을
        대상**이다 (조직도·PPT 캡처가 이 꼴이다).
    """
    linked = 0
    for page in pages:
        images = [im for im in (page.images or []) if im.bbox]
        if not images:
            continue
        for table in (page.tables or []):
            if not table.bbox or table.source_image_id:
                continue
            host = next((im for im in images if _inside(table.bbox, im.bbox)), None)
            if host is None:
                continue
            table.source_image_id = host.id
            host.table_candidate = True
            linked += 1
            note = ""
            if host.region_kind == "chart":
                host.region_kind = "image"
                host.region_kind_reason = (
                    f"{host.region_kind_reason or ''} · 표({table.id})가 안에 "
                    "있어 그래프가 아니라 그림으로 봅니다").strip(" ·")
                note = " · 그림 종류를 chart→image 로 바로잡음"
            page.trace.add(
                "docstruct.images.picture_tables", "그림 안의 표",
                f"{table.id} 는 {host.id} 안에 있습니다 — 글자가 래스터 OCR "
                f"산물입니다{note}",
                status="warn")
    return linked
