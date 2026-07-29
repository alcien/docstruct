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
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text.strip())
    return text.strip()


def parse_json_object(text: str) -> dict[str, Any]:
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


def parse_json_list_or_object_map(text: str) -> list[dict[str, Any]]:
    """응답에서 JSON 을 뽑아 dict 목록으로 만든다.

    입력: raw — LLM 응답 원문
    출력: dict 목록. 객체 하나면 1개짜리 목록, 파싱 실패 시 빈 목록
    """
    text = strip_code_fences(text)
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [{"id": k, **v} for k, v in data.items()]
    except json.JSONDecodeError:
        pass
    match = re.search(r"\[[\s\S]*\]", text)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return [{"id": k, **v} for k, v in data.items()]
        except json.JSONDecodeError:
            pass
    return []
