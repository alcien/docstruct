"""structured.json 계약 — 만들고 검증한다 (설계 문서 §3).

입력:
    표 하나의 구조화 산출
출력:
    structured.json 계약 조립·검증

역할:
    표 하나의 구조화 산출을 계약된 꼴로 조립하고, 산출 전체가 계약을
    지키는지 검증한다. 계약이 코드에 있어야 소비자(RAG·RDB)가 흔들리지
    않는다.
호출부:
    structuring.structure_document · overlay structure 단계
설정:
    없음.
"""
from __future__ import annotations

#: 표 항목의 필수 열쇠. 값이 없어도 열쇠는 있다 — 소비자가 분기하지 않게.
TABLE_KEYS = ("table_id", "kind", "records", "hierarchy",
              "joins", "checks", "provenance")


def build_table_entry(table_id: str, *, kind=None, records=None,
                      hierarchy=None, joins=None, checks=None,
                      provenance=None) -> dict:
    """표 하나의 구조화 항목을 계약 꼴로.

    입력: table_id 필수, 나머지는 산출물 (없으면 빈 값)
    출력: TABLE_KEYS 를 전부 가진 dict
    """
    return {
        "table_id": table_id,
        "kind": kind,
        "records": records or [],
        "hierarchy": hierarchy or [],
        "joins": joins or {},
        "checks": checks or {},
        "provenance": provenance or {},
    }


def validate(structured: dict) -> list[str]:
    """계약 위반 목록 (비어 있으면 통과).

    입력: structured — {"source_document","tables":[…]}
    출력: 위반 설명 목록
    비고:
        예외를 던지지 않고 목록을 낸다 — 검증 실패가 파이프라인을 멈출지
        는 호출부의 결정이다 (실험실은 멈추고, 운영은 기록 후 진행 등).
    """
    problems: list[str] = []
    if "tables" not in structured:
        return ["tables 열쇠가 없습니다"]
    for index, table in enumerate(structured["tables"]):
        for key in TABLE_KEYS:
            if key not in table:
                problems.append(f"tables[{index}] 에 {key} 가 없습니다")
        for record in table.get("records") or []:
            if "_row" not in record:
                problems.append(
                    f"{table.get('table_id')} 레코드에 _row 가 없습니다")
                break
    return problems
