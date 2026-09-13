"""쪽 넘김 이어붙임 — continues_from 사슬을 레코드로 통합 (구조화 §5-3, T6).

입력:
    continues_from 사슬
출력:
    이어 붙인 레코드 (§5-3)

역할:
    판독이 남긴 continues_from 사슬을 따라 표 조각의 **레코드**를 잇는다.
    열 배치 검산(0.3.71 실증: 너비 기준, 홀짝 여백 불변)은 pdf 가 있을
    때만 돌고, 어긋나면 잇지 않고 표시로 강등한다.
호출부:
    structuring.structure_document
설정:
    없음 — 순수 함수 (pdf_path 는 선택).
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


def _column_ok(pdf_path, page_a: int, bbox_a: dict,
               page_b: int, bbox_b: dict) -> bool | None:
    """두 조각의 열 배치가 같은가 (지면 검산 — pdf 없으면 None).

    입력: pdf_path — 원본(없으면 None), 조각 두 개의 쪽·bbox
    출력: True/False, 검산 불가면 None
    """
    if not pdf_path or not bbox_a or not bbox_b:
        return None
    try:
        from docstruct.experiments.tsr.measure.vector_grid import (
            _inside,
            _page_rects,
            same_column_layout,
        )

        rects_a = [r for r in _page_rects(pdf_path, page_a) if _inside(r, bbox_a)]
        rects_b = [r for r in _page_rects(pdf_path, page_b) if _inside(r, bbox_b)]
        if not rects_a or not rects_b:
            return None
        return same_column_layout(rects_a, rects_b)
    except Exception as exc:                     # noqa: BLE001 - 검산 보조다
        _log.debug("열 배치 검산 실패: %s", exc)
        return None


def join_chains(tables: list[dict], *, pdf_path=None) -> list[dict]:
    """continues_from 사슬별로 잇기 계획을 세운다.

    입력: tables — [{"table_id","page","bbox","continues_from","records",…}]
    출력: [{"head": id, "parts": [id…], "column_check": "ok|mismatch|unchecked",
           "records": 이어진 레코드}]
    비고:
        mismatch 면 레코드를 **잇지 않는다** — 조각 그대로 두고 표시만
        남긴다 (없는 결함을 만드는 것보다 안 내는 편이 낫다).
    """
    by_id = {t["table_id"]: t for t in tables}
    next_of = {t["continues_from"]: t["table_id"]
               for t in tables if t.get("continues_from") in by_id}

    out: list[dict] = []
    for head in tables:
        if head.get("continues_from") in by_id:
            continue                              # 사슬 중간·끝은 머리가 아니다
        if head["table_id"] not in next_of:
            continue                              # 사슬 없음
        parts = [head["table_id"]]
        while parts[-1] in next_of:
            parts.append(next_of[parts[-1]])
        verdicts = []
        records = list(by_id[parts[0]].get("records") or [])
        joined = True
        for a, b in zip(parts, parts[1:]):
            ta, tb = by_id[a], by_id[b]
            ok = _column_ok(pdf_path, ta.get("page"), ta.get("bbox"),
                            tb.get("page"), tb.get("bbox"))
            verdicts.append(ok)
            if ok is False:
                joined = False
                break
            records.extend(tb.get("records") or [])
        check = ("mismatch" if False in verdicts
                 else "ok" if verdicts and all(v is True for v in verdicts)
                 else "unchecked")
        out.append({"head": parts[0], "parts": parts,
                    "column_check": check,
                    "records": records if joined else None})
    return out
