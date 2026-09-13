"""structuring — 구조화 층: 판독(document.json) → 레코드·계층·관계 (구조화_단계_설계.md).

축: 인식 ① 표의 윗단 — 표를 **의미 단위**로 편다.
역할:
    표를 레코드·계층·관계로 전개한다. 의미가 만들어지는 유일한 자리다.
    전부 dict → dict 순수 함수라 판독 실행 없이 json 만으로 돈다.
    `checks.py` 의 격자·오염 검사만은 파이프라인이 매번 부르는 **상시 검사**
    (구간 8)다 — 정답 없이 잴 수 있는 것은 실험이 아니라 검사다.
호출부:
    overlay structure 단계 · 시험 CLI (`python -m docstruct.structuring`) ·
    docstruct.pipeline (checks 만)
설정:
    없음.

모듈 (입력 → 출력 · 역할):
    schema.py             표 하나의 구조화 산출 → structured.json 계약 조립·검증
    expand.py             cells → 행 단위 레코드 (병합 전개, §5-1)
    hierarchy.py          cells → 부모>자식 삼항 (계층, §5-2)
    joins.py              continues_from 사슬 → 이어 붙인 레코드 (§5-3)
    checks.py             cells → grid_check(결함) · leak_check(오염) · repair_leaks(복원)
                          · patch_markdown_cells(markdown 반영) · count_check · recheck_sums
    __main__.py           document.json → structured.json (시험 CLI)

사다리(실험)와의 관계: **비접촉** — 실험이 남긴 필드를 읽기만 한다.
"""
from __future__ import annotations

from docstruct.structuring.checks import (count_check, grid_check,
                                           leak_check, recheck_sums)
from docstruct.structuring.expand import column_names, expand_merges
from docstruct.structuring.hierarchy import extract_hierarchy
from docstruct.structuring.joins import join_chains
from docstruct.structuring.schema import build_table_entry, validate

__all__ = ["structure_document", "expand_merges", "column_names",
           "extract_hierarchy", "join_chains", "count_check", "grid_check", "leak_check",
           "recheck_sums", "build_table_entry", "validate"]


def structure_document(document: dict, *, pdf_path=None) -> dict:
    """document.json(dict) → structured(dict).

    입력: document — 판독 산출, pdf_path — 열 배치 검산용 원본 (선택)
    출력: {"source_document","tables":[계약 항목…],"problems":[…]}
    비고:
        경고를 지우지 않는다 — 판독의 자신 없음(odd_columns 등)은
        provenance.warnings 로 그대로 넘어간다. 개수 검산은 이어붙임
        사슬에 물린 조각에서는 건너뛰고 **이어진 레코드에서만** 돈다
        (조각에서 재면 개수가 잘려 오탐이 난다 — checks.py 계약).
    """
    flat: list[dict] = []
    for page in document.get("pages", []):
        for table in (page.get("tables") or []):
            cells = table.get("cells") or []
            names = column_names(cells) if cells else []
            records = expand_merges(cells) if cells else []
            warnings = [key for key in
                        ("odd_columns", "match_disagreements", "fill_diff")
                        if table.get(key)]
            flat.append({
                "table_id": table.get("id"),
                "page": page.get("page_no"),
                "bbox": table.get("bbox"),
                "continues_from": table.get("continues_from"),
                "kind": table.get("table_kind"),
                "names": names,
                "records": records,
                "cells": cells,
                "source": table.get("source"),
                "warnings": warnings,
            })

    chains = join_chains(flat, pdf_path=pdf_path)
    chained_ids = {part for chain in chains for part in chain["parts"]}
    chain_of = {chain["head"]: chain for chain in chains}

    tables_out: list[dict] = []
    for item in flat:
        chain = chain_of.get(item["table_id"])
        joined = chain and chain["records"] is not None
        records = chain["records"] if joined else item["records"]
        in_unjoined_chain = (item["table_id"] in chained_ids
                            and not joined and chain is None)
        checks: dict = {}
        # **격자가 온전한지 먼저 본다** (0.4.69). 표는 직사각 격자이므로
        # 모든 자리가 정확히 한 셀에 덮여야 한다 — 구멍이나 겹침은 **원본을
        # 몰라도** 틀렸다고 말할 수 있는 신호다. 실측: HWPX 653표는 0개,
        # PDF 는 40~53% 의 표에 결함이 있다.
        #
        # 고치지 않고 표시만 한다. 구멍을 어느 셀로 메울지는 지면을 봐야
        # 알 수 있고(판독의 몫), 여기서 지어내면 없는 값을 만든다.
        got = grid_check(item["cells"]) if item["cells"] else None
        if got:
            checks["grid_check"] = got
        # 셀 텍스트가 이웃 칸으로 새어 들었는지 (0.4.79) — 격자 온전성으로는
        # 못 잡는 오류다.
        got = leak_check(item["cells"]) if item["cells"] else None
        if got:
            checks["leak_check"] = got
        got = recheck_sums(item["cells"]) if item["cells"] else None
        if got:
            checks["sum_check"] = got
        # 개수 검산 — 완전성이 보장된 곳에서만: 사슬 밖 단독 표, 또는
        # **열 배치 검산 ok 로 이어진 사슬**. unchecked 사슬은 건너뛴다
        # (실측: 판독기 사슬이 조각을 빠뜨리면 개수가 잘려 발화한다 —
        # ok 사슬에서의 발화는 "사슬에 누락 조각이 있다" 는 신호로 읽는다).
        complete = (item["table_id"] not in chained_ids
                    or (joined and chain["column_check"] == "ok"))
        if item["names"] and records and complete:
            got = count_check(records, item["names"])
            if got:
                checks["count_check"] = got
        tables_out.append(build_table_entry(
            item["table_id"],
            kind=item["kind"],
            records=records,
            hierarchy=(extract_hierarchy(item["cells"], item["names"])
                       if item["cells"] and item["names"] else []),
            joins=({"parts": chain["parts"],
                    "column_check": chain["column_check"]} if chain else
                   {"continues_from": item["continues_from"]}
                   if item["continues_from"] else {}),
            checks=checks,
            provenance={"page": item["page"], "bbox": item["bbox"],
                        "source": item["source"],
                        "warnings": item["warnings"]},
        ))
        del in_unjoined_chain

    structured = {"source_document": document.get("source"),
                  "tables": tables_out}
    structured["problems"] = validate(structured)
    return structured
