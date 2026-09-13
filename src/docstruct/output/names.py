"""문서 여러 건의 산출 폴더 이름 정하기 (충돌 없이).

입력:
    문서 파일 이름 목록
역할:
    문서마다 **서로 다른** 산출 폴더 이름을 정한다. 이름이 겹치면 뒤에 온
    문서가 앞의 산출물을 조용히 덮어쓰기 때문이다.
호출부:
    docstruct.cli (`_process` 의 산출 폴더) · docstruct.api.DocStructBatch.save
출력:
    {원본 파일 이름: 폴더 이름}

왜 필요한가 (0.4.92)
-------------------
폴더 이름은 `safe_file_stem(name)` — 확장자를 떼고 특수문자를 `_` 로 바꾼
것이었다. 그래서 **다른 문서가 같은 폴더를 쓰는 경우가 두 갈래** 있었다.

    ① 확장자만 다른 짝
       성과계획서.hwpx · 성과계획서.pdf   →  둘 다 `성과계획서`

       이 프로젝트가 **늘 다루는 모양**이다. 쪽 맞춤(align)은 같은 문서의
       HWPX 와 PDF 를 짝지어 쓰는데, 그 둘을 한 폴더에 넣고 돌리면 하나가
       사라진다.

    ② 정규화하면 같아지는 이름
       "성과 계획서.hwpx" · "성과_계획서.hwpx"  →  둘 다 `성과_계획서`

실측(0.4.91 기준, 네 건 입력):

    총 4건 중 4건 성공          ← CLI 는 성공이라고 말했다
    산출 폴더 2개               ← 둘은 사라졌다
    성과_계획서/document.json   → 표 1개 (표 119개짜리가 덮였다)

**성공이라고 말하면서 결과를 잃는다** — 가장 나쁜 종류의 실패다. 게다가
`--jobs N`(0.4.91)에서는 두 프로세스가 **같은 파일에 동시에 쓴다.**
document.json 이 찢어질 수 있다.

어떻게 푸는가
------------
겹치는 것만, 겹치는 만큼만 이름을 늘린다. 안 겹치는 문서의 폴더 이름은
예전과 똑같다 — 이미 돌려 둔 산출물이 갑자기 다른 이름이 되면 안 된다.

    1단계  `성과계획서.hwpx` `성과계획서.pdf`   **확장자를 폴더 이름에 넣는다**
    2단계  `성과_계획서.hwpx__a1b2c3`           그래도 겹치면 원본 이름의 해시

    0.4.93 부터 폴더 이름이 **확장자를 포함한 파일 이름**이다. 확장자만
    다른 짝은 이것만으로 갈리고, 남는 충돌은 정규화하면 같아지는 이름
    ("성과 계획서.hwpx" 와 "성과_계획서.hwpx")뿐이다.

순서에 기대지 않는다 — 같은 입력 집합이면 언제나 같은 이름이 나온다.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import re


def safe_file_name(name: str) -> str:
    """폴더 이름으로 쓸 수 있게 정리한 **확장자 포함** 파일 이름.

    입력: name — 원본 파일 이름
    출력: `성과 계획서.hwpx` → `성과_계획서.hwpx`
    비고:
        `safe_file_stem` 은 확장자를 뗀다. 폴더 이름에는 **확장자가 있어야**
        `성과계획서.hwpx` 와 `성과계획서.pdf` 가 갈린다(0.4.93). 쪽 맞춤은
        이 짝을 늘 다루므로 어느 것이 어느 것인지 폴더 이름에서 보여야 한다.
    """
    from pathlib import Path as _Path

    raw = _Path(name).name if name else "document"
    safe = re.sub(r"[^\w\-.]", "_", raw).strip("._")
    return safe or "document"


def _digest(name: str) -> str:
    """원본 이름의 짧은 지문.

    입력: name — 원본 파일 이름
    출력: 6자 16진수
    """
    return hashlib.sha1(name.encode("utf-8", "replace")).hexdigest()[:6]


def assign_out_dirs(names: list[str] | tuple[str, ...]) -> dict[str, str]:
    """문서 이름들에 서로 다른 폴더 이름을 준다.

    입력: names — 원본 파일 이름 목록 (경로여도 된다 — 이름만 쓴다)
    출력: {원본 이름: 폴더 이름}
    비고:
        같은 이름이 두 번 들어오면 한 항목으로 본다 — 같은 문서다.
        폴더 이름은 **입력 순서와 무관**하다. 셋째 단계까지 가도 해시가
        원본 이름에서만 나오므로, 파일을 하나 더 넣어도 나머지 이름은
        그대로다.
    """
    unique = list(dict.fromkeys(names))
    by_base: dict[str, list[str]] = {}
    for name in unique:
        by_base.setdefault(safe_file_name(Path(name).name), []).append(name)

    out: dict[str, str] = {}
    for base, group in by_base.items():
        if len(group) == 1:
            out[group[0]] = base
            continue
        # 2단계 — 정규화하면 같아지는 이름들("성과 계획서.hwpx" 와
        # "성과_계획서.hwpx"). 확장자는 이미 base 에 있으므로 남은 충돌은
        # 이것뿐이다. 원본 이름의 해시로 가른다.
        for name in group:
            out[name] = f"{base}__{_digest(Path(name).name)}"
    return out


def describe_renames(assigned: dict[str, str]) -> list[str]:
    """이름을 늘린 문서만 사람이 읽을 줄로.

    입력: assigned — assign_out_dirs 결과
    출력: 설명 줄 목록 (늘린 것이 없으면 빈 목록)
    비고:
        조용히 바꾸지 않는다. 산출 폴더 이름이 평소와 다르면 그 이유를
        말해야 한다 — 안 그러면 "왜 폴더 이름이 이래?" 로 시간을 쓴다.
    """
    lines = []
    for name, folder in assigned.items():
        if folder != safe_file_name(Path(name).name):
            lines.append(f"{name} → {folder}/")
    return sorted(lines)
