"""보정이 VLM 판독을 낫게 하는가 — 같은 그림을 두 번 읽어 견준다.

역할:
    그림마다 **보정 없이 한 번, 보정하고 한 번** VLM 에 보내 결과를
    나란히 저장한다. 어느 쪽이 나은지는 사람이 읽고 판정한다.
호출부:
    시험 꾸러미.

        python image_ab.py 그림/fair                # 폴더 전체
        python image_ab.py 그림/fair -n 10          # 앞 10장만
        python image_ab.py a.png b.png -o ab.md     # 파일 지정

출력:
    ab_결과.md — 그림마다 조합별 결과와 요약 표

왜 필요한가
--------
기계적으로는 이미 확인됐다 — 실측(fair 45개): **41개가 획이 올라가고
내려간 것은 0개**, 변화 중앙값 +4px 로 `good` 구간에 든다.

그러나 **획이 커진 것과 VLM 이 더 잘 읽는 것은 다르다.** lanczos 는
정보를 만들지 않고 늘리기만 하므로, 모델이 이미 읽어내던 것이라면
이득이 없을 수 있다. 그것은 실제로 읽혀 봐야 안다.

읽는 법
------
`vlm_markdown` 두 쪽을 견줄 때 볼 것:

    · 숫자·고유명사가 **더 많이** 나왔는가 (덜 나왔으면 손해다)
    · 없던 값이 생겼는가 — **지어낸 것일 수 있다**. 원본에 없는 정보를
      보정이 만들어내지는 않는다
    · 표 구조가 더 온전한가

`fair` 는 경계선이다. 이득이 뚜렷하지 않으면 켜지 않는 편이 낫다 —
호출이 두 배가 되지는 않지만 처리 시간이 붙고, 되돌릴 근거가 없어진다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ── CONFIG ────────────────────────────────────────────────────────────
#: 기본 대상 (인자로 주면 그쪽이 우선)
TARGETS = ["그림/fair"]
#: 결과 파일
OUT_FILE = "ab_결과.md"
#: 한 번에 볼 최대 장수. LLM 호출은 **장당 조합 수**만큼 든다.
MAX_IMAGES = 10

#: 견줄 조합 — (이름, 전처리, 확대). 무엇이 이득이고 무엇이 해로운지
#: 가르려면 **하나씩 떼어** 봐야 한다. `basic+lanczos` 만 재고 "lanczos
#: 때문" 이라고 단정했던 것이 앞선 실수다(0.4.41) — 전처리 단독으로도
#: 해로울 수 있다는 신호가 이미 있었다(국방부 막대그래프에서 대비
#: 정규화가 획을 9px → 4px 로 떨어뜨렸다).
COMBOS = [
    ("원본",            "off",   "off"),
    ("basic",           "basic", "off"),
    ("lanczos",         "off",   "lanczos"),
    ("basic+lanczos",   "basic", "lanczos"),
    # 초해상도 모델 (GPU 가 있을 때만 실제로 돈다 — 없으면 lanczos 로
    # 물러나고 그 사유가 기록에 남는다)
    ("model",           "off",   "model"),
]
#: docstruct 소스 경로 (설치본이면 빈 문자열)
DOCSTRUCT_SRC = ""

# ── LLM ───────────────────────────────────────────────────────────────
# **여기를 채우거나, `.env` 나 환경변수로 준다.** 셋 다 비어 있으면
# 아무것도 읽지 못하고 "(LLM 미설정)" 만 나온다.
#
# 노트북에서 쓰던 `docstruct.set_api_key()` 는 **이 도구에 통하지
# 않는다** — 그것은 파이썬 세션 안에서만 살고, 이 도구는 별도
# 프로세스로 돈다.
#
# 비워 두면 이미 설정된 환경변수·`.env` 를 그대로 쓴다.
LLM_URL = ""      # 예: "https://api.openai.com/v1/chat/completions"
LLM_MODEL = ""    # 예: "gpt-5.6-luna"
LLM_KEY = ""      # 예: "sk-..."
# ──────────────────────────────────────────────────────────────────────

if LLM_URL:
    os.environ["DOCLING_TABLE_API_URL"] = LLM_URL
if LLM_MODEL:
    os.environ["DOCLING_TABLE_API_MODEL"] = LLM_MODEL
if LLM_KEY:
    os.environ["DOCLING_TABLE_API_KEY"] = LLM_KEY

if DOCSTRUCT_SRC:
    sys.path.insert(0, DOCSTRUCT_SRC)


def _import(name: str, *symbols):
    """pkg 배치와 local/overlay 배치를 모두 받는다.

    입력: name — `docstruct.` 를 뺀 모듈 경로, symbols — 가져올 이름
    출력: 심볼 튜플
    비고:
        배포 트리는 `converters`·`core`·`infrastructure`·`experiments` 를
        **최상위로 승격**한다(`docstruct.infrastructure` 가 없다).
        설치본은 `docstruct.` 아래에 둔다. 도구가 양쪽에서 도는 것이
        맞으므로 둘 다 시도한다.
    """
    import importlib

    for prefix in ("docstruct.", ""):
        try:
            module = importlib.import_module(prefix + name)
        except ImportError:
            continue
        return tuple(getattr(module, s) for s in symbols)
    raise ImportError(f"{name} 을(를) 찾지 못했습니다 — DOCSTRUCT_SRC 를 보세요")


def read_once(path: Path, pre: str, up: str) -> tuple[str, dict]:
    """그림 하나를 정해진 보정으로 한 번 읽는다.

    입력: path — 그림 경로, pre — 전처리 단계, up — 확대 방식
    출력: (읽은 글, 적용 기록)
    비고:
        파이프라인과 **같은 함수**를 쓴다 — 지시문 선택(전사/복원/설명)도
        그대로 따라간다. A/B 가 실제 동작과 어긋나면 의미가 없다.
    """
    (prepare,) = _import("media.image_prep", "prepare")
    (measure,) = _import("media.legibility", "measure")
    (_read_one,) = _import("media.vlm_read", "_read_one")
    ImageInfo, PageContent, PageTrace = _import(
        "models", "ImageInfo", "PageContent", "PageTrace")

    # **`auto` 를 쓰지 않는다.** 0.4.41 부터 auto 는 아무것도 걸지 않으므로
    # 그것으로 A/B 를 하면 두 쪽이 같아진다. 단계를 직접 지정한다.
    os.environ["DOCSTRUCT_IMAGE_PREPROCESS"] = pre
    os.environ["DOCSTRUCT_IMAGE_UPSCALE"] = up

    legible = measure(path)
    info = ImageInfo(id=path.stem[:20], placeholder="<!-- image 1 -->",
                     image_path=str(path))
    info.legibility = legible
    info.dpi = None
    page = PageContent(page_no=1, page_no_kind="exact", content="",
                       trace=PageTrace())

    (llm_api_config,) = _import("infrastructure.llm.client", "llm_api_config")

    cfg = llm_api_config()
    if cfg is None:
        return "(LLM 미설정 — DOCLING_TABLE_API_URL/MODEL/KEY 를 넣으세요)", {}
    try:
        text = _read_one(page, info, cfg) or "(읽지 못함)"
    except Exception as exc:                     # noqa: BLE001
        # **한 장 실패로 멈추지 않는다.** 연결이 끊기거나 한 장이 거부되면
        # 그 자리만 사유를 적고 나머지를 계속 본다 — 20장을 돌리다
        # 15번째에서 멈추면 앞의 14장도 잃는다.
        text = f"(실패: {exc})"
    try:
        _sent, applied = prepare(path, None, legible)
    except Exception as exc:                     # noqa: BLE001
        # 기록용 재실행이 실패해도 읽은 결과는 살린다.
        applied = {"error": str(exc)}
    return text, applied


def collect(targets: list[str]) -> list[Path]:
    """폴더·파일에서 그림을 모은다."""
    found: list[Path] = []
    for target in targets:
        path = Path(target).expanduser()
        if path.is_dir():
            found.extend(sorted(p for p in path.rglob("*")
                                if p.suffix.lower() in (".png", ".jpg", ".jpeg")))
        elif path.is_file():
            found.append(path)
        else:
            print(f"  ! 없는 경로: {target}")
    return found


def main(argv: list[str] | None = None) -> int:
    """두 번 읽어 나란히 적는다."""
    args = list(sys.argv[1:] if argv is None else argv)
    out_file = OUT_FILE
    limit = MAX_IMAGES
    for flag, setter in (("-o", "out"), ("-n", "n")):
        if flag in args:
            index = args.index(flag)
            if index + 1 >= len(args):
                print("사용: python image_ab.py [폴더|파일 ...] "
                      "[-o 결과.md] [-n 장수]")
                return 1
            value = args[index + 1]
            del args[index:index + 2]
            if setter == "out":
                out_file = value
            else:
                limit = int(value)

    files = collect(args or TARGETS)[:limit]
    if not files:
        print("그림을 찾지 못했습니다. 사용: python image_ab.py [폴더] "
              "[-o 결과.md] [-n 장수]")
        return 1

    # **무엇으로 읽는지 먼저 보여 준다.** 실행해 보고 "(LLM 미설정)" 이
    # 나와서야 아는 것은 늦다.
    (llm_api_config,) = _import("infrastructure.llm.client", "llm_api_config")

    cfg = llm_api_config()
    if cfg is None:
        print("! LLM 이 설정되지 않았습니다 — 아무것도 읽지 못합니다.\n")
        print("  이 파일 위 CONFIG 의 LLM_URL·LLM_MODEL·LLM_KEY 를 채우거나,")
        print("  환경변수(또는 .env)로 주세요:")
        print("     DOCLING_TABLE_API_URL / _MODEL / _KEY")
        print("  (노트북의 set_api_key() 는 별도 프로세스라 통하지 않습니다)")
        return 1
    key = os.environ.get("DOCLING_TABLE_API_KEY", "")
    print(f"LLM  : {cfg.get('url')}")
    print(f"모델 : {cfg.get('model')} · 키 {'있음' if key else '없음'}")
    print(f"대상 {len(files)}장 · 조합 {len(COMBOS)} · "
          f"LLM 호출 {len(files) * len(COMBOS)}회\n")
    lines = ["# 보정 A/B — 조합별 견주기", "",
             "조합: " + " · ".join(name for name, _p, _u in COMBOS), ""]
    for index, path in enumerate(files, 1):
        print(f"  [{index}/{len(files)}] {path.name}", flush=True)
        lines += [f"## {index}. {path.name}", ""]
        results = []
        for label, pre, up in COMBOS:
            try:
                text, applied = read_once(path, pre, up)
            except Exception as exc:                 # noqa: BLE001
                text, applied = f"(실패: {exc})", {}
            results.append((label, text, applied))
            lines += [f"### {label}", "",
                      f"적용: `{applied or '없음'}`", "",
                      text.strip(), ""]
        lines += ["#### 요약", "",
                  "| 조합 | 글자 | 숫자 | 적용 |",
                  "| --- | --- | --- | --- |"]
        for label, text, applied in results:
            digits = sum(character.isdigit() for character in text)
            lines.append(f"| {label} | {len(text.strip())} | {digits} | "
                         f"`{applied or '없음'}` |")
        lines += ["", "---", ""]

    Path(out_file).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n저장했습니다: {out_file}")
    print("조합별 요약 표를 견주세요 — 숫자·고유명사가 더 나왔는지,")
    print("없던 값이 생기지는 않았는지(지어냈을 수 있습니다), 구조가 온전한지.")
    print("\n하나씩 떼어 보는 것이 요점입니다: basic 만 걸어도 해로울 수")
    print("있고(실측: 대비 정규화가 획을 9px → 4px 로 떨어뜨렸다),")
    print("그러면 'lanczos 때문' 이라는 결론이 틀린 것이 됩니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
