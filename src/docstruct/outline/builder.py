"""문서 목차(의미 경로) 추출.

역할:
    페이지 본문을 LLM 에 보내 섹션 계층을 뽑고, 페이지를 넘어가며
    직전 경로를 이어받아 문서 전체의 목차를 만든다.
호출부:
    (선택 기능) 목차가 필요한 소비자
출력:
    OutlineNode 목록 / markdown 목차 문자열
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from docstruct.content import expand_tables_and_images
from docstruct.models import PageContent, PageDocument
from docstruct.infrastructure.llm.client import invoke_llm, llm_api_config
from docstruct.infrastructure.llm.json_parse import parse_json_array

_log = logging.getLogger(__name__)

#: 프롬프트에 넣을 페이지 본문 최대 길이
MAX_PAGE_CHARS = 12_000
#: 이전 페이지 컨텍스트로 넘길 최대 길이 (프롬프트가 2배로 커지는 것 방지)
MAX_PREV_CHARS = 2_000

_PROMPT = """\
이전 페이지 내용(일부):
{previous_page_text}

이전 페이지의 마지막 경로: {last_path_str}

현재 페이지 내용:
{page_text}

현재 페이지 내용에서 (의미경로, 핵심요약) 쌍을 추출하세요.

규칙:
- 가능한 한 이전 경로를 재사용·확장할 것
- 완전히 다른 주제면 공통 조상에서 새 경로를 만들 것
- 문서에 명확한 제목, 장, 절, 항목, 섹션명이 존재하면 우선 경로로 사용한다.
- 단순 번호(1., ①, 가.)는 제외하고 의미 있는 제목만 사용한다.
- 현재 페이지가 특정 섹션 내부에 속하면 해당 섹션 경로를 유지한다.
- 새로운 의미 경로를 생성하는 것은 적절한 제목이나 섹션명이 존재하지 않을 때만 허용한다.
- 경로는 문서의 구조를 복원하는 것이 우선이며, 내용 요약은 fact에 작성한다.
- 이 페이지에 해당하는 내용이 여러 주제면 여러 쌍을 반환한다.

응답 형식 (JSON 배열만, 다른 텍스트 없음):
[
  {{"path": ["주제", "하위주제"], "fact": "핵심 요약"}},
  {{"path": ["주제", "다른하위"], "fact": "핵심 요약"}}
]
"""


@dataclass
class OutlineNode:
    """목차 항목 하나.

    입력(필드): page_no, path(섹션 경로), preview(본문 앞부분)
    출력: path_str — 경로 문자열, to_dict — 직렬화
    """

    path: list[str]
    fact: str
    page_no: int | str | None = None

    def path_str(self) -> str:
        """섹션 경로 문자열.

        입력: 없음
        출력: '제1장 > 제1절' 형태
        """
        return " > ".join(self.path)

    def to_dict(self) -> dict[str, Any]:
        """직렬화한다.

        입력: 없음
        출력: dict
        """
        return {"path": self.path, "path_str": self.path_str(), "fact": self.fact, "page_no": self.page_no}


def _fmt_path(path: list[str]) -> str:
    """목차 경로를 프롬프트 표기로 만든다.

    입력: path — 상위 제목 목록
    출력: `A > B > C` 형태 문자열. 비어 있으면 "(없음 — 문서 시작)"
    """
    return " > ".join(path) if path else "(없음 — 문서 시작)"


def _fmt_prev(text: str) -> str:
    """직전 본문 조각을 프롬프트 표기로 만든다.

    입력: text — 직전 페이지 끝 텍스트
    출력: 앞 MAX_PREV_CHARS 자. 비어 있으면 "(없음 — 문서 시작)"
    """
    text = text.strip()
    if not text:
        return "(없음 — 문서 시작)"
    return text[:MAX_PREV_CHARS]


def _normalize_path(raw: Any) -> list[str]:
    """LLM 이 준 경로 값을 문자열 목록으로 정리한다.

    입력: raw — LLM 응답의 경로 값
    출력: 문자열 목록
    """
    if not isinstance(raw, list):
        return []
    return [str(p).strip() for p in raw if p and str(p).strip()]


def _extract_page(
    last_path: list[str],
    page_text: str,
    previous_text: str,
    cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    """페이지 하나의 섹션 경로를 LLM 으로 뽑는다.

    입력: page, last_path — 직전 페이지의 경로
    출력: 항목 목록. 실패 시 폴백 결과
    """
    prompt = _PROMPT.format(
        previous_page_text=_fmt_prev(previous_text),
        last_path_str=_fmt_path(last_path),
        page_text=page_text[:MAX_PAGE_CHARS],
    )
    try:
        raw = invoke_llm(prompt, span_name="outline_extract", cfg=cfg)
    except Exception as exc:
        _log.warning("경로 추출 실패: %s", exc)
        return []

    results: list[dict[str, Any]] = []
    for item in parse_json_array(raw):
        path = _normalize_path(item.get("path"))
        fact = (item.get("fact") or "").strip()
        if path and fact:
            results.append({"path": path, "fact": fact})
    return results


def _fallback(last_path: list[str], page_text: str) -> list[dict[str, Any]]:
    """LLM 실패 시 직전 경로를 그대로 잇는다.

    입력: last_path, page_text
    출력: 항목 목록
    """
    snippet = (page_text or "").strip()[:200]
    if not snippet:
        return []
    return [{"path": last_path or ["문서"], "fact": snippet}]


def build_outline(doc: PageDocument) -> list[OutlineNode]:
    """문서 전체의 목차를 만든다.

    입력: doc — PageDocument
    출력: OutlineNode 목록 (페이지 순서)
    """
    cfg = llm_api_config()
    if cfg is None:
        _log.warning("LLM API 미설정 — 목차 추출을 건너뜁니다.")
        return []

    nodes: list[OutlineNode] = []
    last_path: list[str] = []
    previous_text = ""

    _log.info("목차 추출 시작: %s (%d페이지)", doc.filename, len(doc.pages))

    for page in doc.pages:
        expanded = expand_page(page)
        if not expanded:
            continue

        results = _extract_page(last_path, expanded, previous_text, cfg)
        if not results:
            results = _fallback(last_path, expanded)

        for item in results:
            nodes.append(
                OutlineNode(path=item["path"], fact=item["fact"], page_no=page.page_no)
            )

        if results:
            last_path = results[-1]["path"]
        previous_text = expanded

    _log.info("목차 추출 완료: %d개 경로", len(nodes))
    return nodes


def expand_page(page: PageContent) -> str:
    """페이지 본문의 placeholder 를 펼친다.

    입력: page — PageContent
    출력: 표·이미지가 실제 내용으로 치환된 본문
    """
    return expand_tables_and_images(page.content or "", page.tables, page.images)


def outline_to_markdown(nodes: list[OutlineNode]) -> str:
    """목차를 markdown 으로 만든다.

    입력: nodes — OutlineNode 목록
    출력: 들여쓰기된 목록 문자열
    """
    if not nodes:
        return "_추출된 경로가 없습니다._\n"

    lines: list[str] = []
    prev: list[str] = []

    for node in nodes:
        # 이전 경로와 공통 접두사는 다시 출력하지 않습니다.
        common = 0
        for a, b in zip(prev, node.path, strict=False):
            if a != b:
                break
            common += 1

        for depth in range(common, len(node.path)):
            lines.append(f"{'  ' * depth}- **{node.path[depth]}**")

        indent = "  " * len(node.path)
        page = f" _(p.{node.page_no})_" if node.page_no is not None else ""
        lines.append(f"{indent}- {node.fact}{page}")
        prev = node.path

    return "\n".join(lines) + "\n"
