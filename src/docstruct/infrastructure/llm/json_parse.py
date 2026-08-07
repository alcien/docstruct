"""LLM 응답에서 JSON 뽑아내기.

역할:
    코드펜스나 설명이 섞여 오는 응답에서 JSON 배열/객체를 찾아 파싱한다.
호출부:
    docstruct.tables.assess, docstruct.outline.builder
출력:
    dict 목록. 파싱 실패 시 빈 목록
"""
from __future__ import annotations

import json
import re
from typing import Any


def strip_code_fences(text: str) -> str:
    """응답 양끝의 ``` 코드펜스를 벗긴다.

    입력: text — LLM 응답 원문
    출력: 펜스와 양끝 공백이 제거된 문자열
    """
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text.strip())
    return text.strip()


def parse_json_object(text: str) -> dict[str, Any]:
    """응답에서 JSON 객체 하나를 뽑는다.

    입력: text — LLM 응답 원문
    출력: dict. 파싱 실패 시 빈 dict
    동작: 전체 파싱을 먼저 시도하고, 실패하면 `{…}` 조각을 찾아 다시 시도한다.
    """
    text = strip_code_fences(text)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return {}


def parse_json_array(text: str) -> list[dict[str, Any]]:
    """응답에서 JSON 배열을 뽑는다.

    입력: text — LLM 응답 원문
    출력: dict 목록 (dict 아닌 항목은 버림). 파싱 실패 시 빈 목록
    동작: 전체 파싱을 먼저 시도하고, 실패하면 `[…]` 조각을 찾아 다시 시도한다.
    """
    text = strip_code_fences(text)
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    except json.JSONDecodeError:
        pass
    match = re.search(r"\[[\s\S]*\]", text)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
        except json.JSONDecodeError:
            pass
    return []


def _dicts_only(data: list[Any]) -> list[dict[str, Any]]:
    """목록에서 dict 항목만 남긴다.

    입력: data — json.loads 결과 목록
    출력: dict 인 항목만 담은 목록
    비고:
        약한 LLM 은 `["table_1 은 문제없음", {...}]` 처럼 문자열을 섞어
        보낸다. 그대로 넘기면 호출부의 `.get()` 에서 AttributeError 가 난다.
    """
    return [item for item in data if isinstance(item, dict)]


def _map_to_list(data: dict[str, Any]) -> list[dict[str, Any]]:
    """`{id: {…}}` 형태의 객체 맵을 dict 목록으로 푼다.

    입력: data — json.loads 결과 객체
    출력: 각 값에 `id` 키를 넣은 dict 목록
    비고:
        값이 dict 가 아닌 항목(`{"table_1": "sufficient"}` 류)은 버린다.
        `**v` 로 풀 수 없어 TypeError 가 나던 자리다 — 형식을 추측해
        복원하기보다 결정적으로 걸러내는 편이 안전하다.
    """
    return [{"id": k, **v} for k, v in data.items() if isinstance(v, dict)]


def parse_json_list_or_object_map(text: str) -> list[dict[str, Any]]:
    """응답에서 JSON 을 뽑아 dict 목록으로 만든다.

    입력: text — LLM 응답 원문
    출력: dict 목록. 객체 맵이면 `id` 키를 넣어 풀고, 파싱 실패 시 빈 목록
    동작: 코드펜스 제거 → 전체 파싱 → 실패 시 배열 조각 → 객체 조각 순으로
          시도한다. 어느 경로든 dict 가 아닌 항목은 조용히 버린다.
    """
    text = strip_code_fences(text)
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return _dicts_only(data)
        if isinstance(data, dict):
            return _map_to_list(data)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\[[\s\S]*\]", text)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, list):
                return _dicts_only(data)
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return _map_to_list(data)
        except json.JSONDecodeError:
            pass
    return []
