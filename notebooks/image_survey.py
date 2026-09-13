"""문서 뭉치의 그림 상태를 훑는다 — 어느 문서에 손이 필요한지 고른다.

역할:
    HWPX·PDF 를 훑어 그림의 실제 해상도와 지면 점유율을 재고, 처방이
    필요한 문서를 골라낸다. **판독을 돌리지 않으므로 빠르다** — 180개
    문서를 몇 분에 훑는다.
호출부:
    시험 꾸러미.

        python image_survey.py                    # CONFIG 의 TARGETS
        python image_survey.py 성과계획서            # 폴더를 인자로
        python image_survey.py 성과계획서 -o 결과.txt # 파일로도 남긴다
        python image_survey.py 성과계획서 --dump 그림  # 판정별로 뽑는다
        python image_survey.py a.hwpx b.pdf        # 파일을 직접

    윈도우에서도 그대로 돈다 — 셸 스크립트가 필요 없다.
출력:
    표준출력 표 셋 — 문서별 요약 · 처방별 분류 · 손이 급한 문서
    (`-o` 를 주면 같은 내용을 파일에도 남긴다)

왜 있는가
--------
문서가 180개면 하나씩 열어 볼 수 없다. 그런데 처방은 문서마다 다르다.

    해상도가 충분한 그림   그대로 둔다
    102~167dpi 짜리 그림   확대(lanczos)가 필요하다
    지면을 채운 이미지     "그림 설명" 이 아니라 **전사** 로 읽어야 한다
    그림이 아예 없는 문서  손댈 것이 없다

실측(조달청·행안부 HWPX): 그림 다섯 개가 전부 102~167dpi 였고 조직도도
그 안에 있었다. 이것이 전체 뭉치에서 어느 정도 비율인지 알아야 처방의
규모가 정해진다.
"""
from __future__ import annotations

import io
import sys
from collections import Counter
from pathlib import Path

# ── CONFIG ────────────────────────────────────────────────────────────
#: 훑을 곳. 폴더를 주면 그 아래 *.hwpx·*.pdf 를 모두 본다.
TARGETS = [
    "/mnt/user-data/uploads",
]
#: 판정 기준은 **글자 획 높이**다 — dpi 는 지면 배치에 좌우돼 판독과
#: 어긋난다. 실측: 국방부 막대그래프가 80dpi·획 9px 로 다 읽히는 반면
#: 원그래프는 102dpi·획 6px 로 글자가 이미 깨져 있다(`100%` → `IDD%`).
#: dpi 는 참고용으로만 낸다.
LOW_DPI = 150.0
#: 지면 점유율이 이 이상이면 전사 대상(스캔본을 붙인 것).
PAGE_LIKE_RATIO = 0.55
#: 못 읽는 그림이 이 비율 이상이면 문서 전체를 "손댈 수 없음" 으로 본다.
#: 하나뿐이면 "일부" 다 — 나머지가 멀쩡한데 포기로 읽히면 안 된다.
POOR_MAJORITY = 0.5
#: 장식으로 보고 무시할 최소 크기(pt).
MIN_IMAGE_PT = 72.0
#: docstruct 소스 경로 (설치본이면 빈 문자열).
DOCSTRUCT_SRC = ""
# ──────────────────────────────────────────────────────────────────────

if DOCSTRUCT_SRC:
    sys.path.insert(0, DOCSTRUCT_SRC)


def hwpx_images(path: Path) -> list[dict]:
    """HWPX 그림의 해상도와 지면 점유율.

    입력: path — .hwpx
    출력: [{ref, dpi, width_pt, height_pt, ratio}]
    """
    try:
        from docstruct.converters.hwpx.hwpxtree import (
            image_display_sizes, image_parts,
        )
    except ImportError:
        from converters.hwpx.hwpxtree import (          # type: ignore
            image_display_sizes, image_parts,
        )
    from PIL import Image

    sizes = image_display_sizes(path)
    parts = image_parts(path)
    out = []
    for ref, (width, height) in sizes.items():
        data = parts.get(ref)
        if not data or width <= 0:
            continue
        try:
            with Image.open(io.BytesIO(data[0])) as image:
                pixels = image.width
                legible = _legibility(image)
        except Exception as exc:                 # noqa: BLE001
            # **조용히 건너뛰지 않는다.** 이 자리에서 예외를 삼켜 61건이
            # 전부 "그림 0개" 로 나왔고, 결과만 보고는 알 수 없었다.
            _SKIPPED.append(f"{path.name}:{ref} — {exc}")
            continue
        out.append({
            "ref": ref,
            "dpi": pixels / (width / 72.0),
            "width_pt": width,
            "height_pt": height,
            "glyph_px": legible.get("glyph_px"),
            "verdict": legible.get("verdict", "unknown"),
            "kind": legible.get("kind"),
            "rows": legible.get("text_rows"),
            "per_row": legible.get("per_row"),
            "data": data[0],
            # A4 기준 점유율
            "ratio": min(1.0, (width * height) / (595.0 * 842.0)),
        })
    return out


def _legibility(image) -> dict:
    """PIL 이미지의 판독 가능성 — 라이브러리 규칙을 그대로 쓴다.

    입력: image — PIL 이미지
    출력: measure 의 결과. 못 재면 {}
    비고:
        **NamedTemporaryFile 을 열어 둔 채 그 경로에 쓰지 않는다.**
        윈도우는 열려 있는 파일을 다시 열지 못해 저장이 실패하고, 그
        예외를 호출부가 삼켜 **그림이 통째로 사라졌다** — 실측: 61건
        전부 "그림 0개" 로 나왔다. 임시 폴더에 경로만 만들어 쓴다.
    """
    import tempfile
    import uuid

    try:
        from docstruct.images.legibility import measure
    except ImportError:
        try:
            from media.legibility import measure            # type: ignore
        except ImportError:
            return {}

    target = Path(tempfile.gettempdir()) / f"docstruct_survey_{uuid.uuid4().hex}.png"
    try:
        image.convert("RGB").save(target)
        return measure(target)
    finally:
        target.unlink(missing_ok=True)


def pdf_images(path: Path) -> list[dict]:
    """PDF 그림의 해상도와 지면 점유율."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return []
    out = []
    try:
        document = pdfium.PdfDocument(str(path))
    except Exception:                            # noqa: BLE001
        return []
    try:
        for index in range(len(document)):
            page = document[index]
            for obj in page.get_objects():
                if getattr(obj, "type", None) != 3:
                    continue
                try:
                    left, bottom, right, top = obj.get_bounds()
                    meta = obj.get_metadata()
                except Exception:                # noqa: BLE001
                    continue
                width, height = right - left, top - bottom
                if width < MIN_IMAGE_PT or height < MIN_IMAGE_PT:
                    continue                     # 글머리 아이콘 등
                legible = {}
                try:
                    from PIL import Image        # noqa: F401

                    legible = _legibility(obj.get_bitmap().to_pil())
                except Exception as exc:         # noqa: BLE001
                    _SKIPPED.append(f"{path.name}:p{index + 1} — {exc}")
                out.append({
                    "ref": f"p{index + 1}",
                    "dpi": meta.width / (width / 72.0),
                    "width_pt": width,
                    "height_pt": height,
                    "glyph_px": legible.get("glyph_px"),
                    "verdict": legible.get("verdict", "unknown"),
                    "kind": legible.get("kind"),
                    "rows": legible.get("text_rows"),
                    "per_row": legible.get("per_row"),
                    "ratio": min(1.0, (width * height) / (595.0 * 842.0)),
                })
    finally:
        document.close()
    return out


def prescribe(images: list[dict]) -> str:
    """이 문서에 무엇이 필요한가 — **판독 가능성으로 가른다.**

    입력: images — 그림 목록
    출력: 처방 이름
    비고:
        장식은 세지 않는다(글자가 없다). `poor` 는 원본에 정보가 없어
        **손댈 수 없다** — 확대해도 `IDD%` 가 `100%` 로 돌아오지 않는다.
        고칠 수 있는 것은 `fair` 뿐이다.
    """
    if any(i.get("verdict") == "unknown" for i in images):
        # **판정을 못 한 것을 "그대로" 로 읽으면 안 된다.** cv2 가 없으면
        # 모든 그림이 unknown 이 되는데, 그것이 "문제 없음" 으로 보이면
        # 조사가 통째로 무의미해진다 — 실측: 61건 전부 "그대로" 로 나왔다.
        return "판정 불가(opencv 없음)"
    real = [i for i in images if i.get("verdict") != "decoration"]
    if not real:
        return "손댈 것 없음"
    page_like = [i for i in real if i.get("kind") == "page"]
    poor = [i for i in real if i.get("verdict") == "poor"]
    fair = [i for i in real if i.get("verdict") == "fair"]

    if page_like:
        return "전사 + 보정" if fair else "전사"
    if fair:
        return "보정"
    if not poor:
        return "그대로"

    # **가장 나쁜 그림 하나가 문서를 대표하지 않게 한다.** 실측: 국세청은
    # 그림 6개 중 못읽음이 **1개**뿐이고 나머지 5개가 멀쩡한데
    # "손댈 수 없음" 으로 분류됐다 — 사람이 그 목록을 보면 "이 문서는
    # 포기" 로 읽는다. 비율로 가른다.
    return ("손댈 수 없음" if len(poor) >= len(real) * POOR_MAJORITY
            else "일부 손댈 수 없음")


def survey(path: Path) -> dict:
    """문서 하나를 훑는다."""
    images = (hwpx_images(path) if path.suffix.lower() in (".hwpx", ".hwtx")
              else pdf_images(path))
    real = [i for i in images if i.get("verdict") != "decoration"]
    glyphs = [i["glyph_px"] for i in real if i.get("glyph_px")]
    return {
        "name": path.name,
        "kind": path.suffix.lower().lstrip("."),
        "images": len(images),
        "decor": len(images) - len(real),
        "poor": sum(1 for i in real if i.get("verdict") == "poor"),
        "fair": sum(1 for i in real if i.get("verdict") == "fair"),
        "page_like": sum(1 for i in real if i.get("kind") == "page"),
        "min_glyph": min(glyphs) if glyphs else None,
        "prescription": prescribe(images),
        "_images": images,                       # 분포·뽑기에 쓴다
    }


#: 훑을 확장자.
SUFFIXES = (".hwpx", ".hwtx", ".pdf")

#: (유형, 판정) → 파이프라인이 그 그림에 무엇을 하는가. 분포를 볼 때
#: **호출 계획**이 함께 보이게 한다.
_ROUTE = {
    ("page", "good"): "전사",
    ("page", "fair"): "보정 후 전사",
    ("page", "poor"): "설명",
    ("figure", "good"): "복원",
    ("figure", "fair"): "보정 후 복원",
    ("figure", "poor"): "설명",
    ("-", "decoration"): "VLM 호출 안 함",
}

#: 읽다 실패한 그림. 끝에 함께 낸다 — 조용히 사라지면 결과가 거짓이 된다.
_SKIPPED: list[str] = []


def collect(targets: list[str]) -> list[Path]:
    """폴더·파일 목록에서 훑을 문서를 모은다.

    입력: targets — 폴더 또는 파일 경로 목록
    출력: 문서 경로 목록 (중복 제거·정렬)
    비고:
        폴더는 **재귀로** 훑는다. 부처별 하위 폴더로 나뉜 뭉치를 그대로
        받기 위함이다.
    """
    found: list[Path] = []
    for target in targets:
        path = Path(target).expanduser()
        if path.is_dir():
            for item in sorted(path.rglob("*")):
                if item.is_file() and item.suffix.lower() in SUFFIXES:
                    found.append(item)
        elif path.is_file():
            found.append(path)
        else:
            print(f"  ! 없는 경로: {target}")
    seen, out = set(), []
    for item in found:
        key = str(item.resolve())
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _dump(rows: list[dict], dump_dir: str) -> int:
    """그림을 판정별 폴더로 뽑는다 — 눈으로 확인하라고.

    입력: rows — survey 결과, dump_dir — 저장 위치
    출력: 저장한 개수
    비고:
        수치만으로는 판정이 맞는지 알 수 없다. 실제로 눈으로 보고서야
        "지면급이 스캔본이 아니라 지도·논리모형이었다" 를 알았다.
        파일 이름에 판정을 적어 두어 목록만 봐도 갈리게 한다.
    """
    from PIL import Image

    base = Path(dump_dir)
    saved = 0
    for row in rows:
        for item in row.get("_images", []):
            data = item.get("data")
            if not data:
                continue                         # PDF 경로는 바이트를 담지 않는다
            folder = base / (item.get("verdict") or "unknown")
            folder.mkdir(parents=True, exist_ok=True)
            stem = Path(row["name"]).stem[:28]
            name = (f"{stem}_{item['ref']}_{item.get('kind') or '-'}"
                    f"_{item.get('glyph_px') or 0}px.png")
            try:
                with Image.open(io.BytesIO(data)) as image:
                    image.convert("RGB").save(folder / name)
                saved += 1
            except Exception:                    # noqa: BLE001 - 한 장 실패는 넘긴다
                continue
    return saved


def main(argv: list[str] | None = None) -> int:
    """훑고 표 셋을 낸다.

    입력: argv — 명령행 인자 (없으면 sys.argv)
    출력: 종료 코드
    비고:
        `-o 파일` 을 주면 화면과 파일에 함께 남긴다. argparse 를 쓰지
        않는 것은 이 꾸러미의 관례를 따른 것이다 — CONFIG 를 소스 위에
        두고, 인자는 있으면 그것이 이긴다.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    dump_dir: str | None = None
    if "--dump" in args:
        index = args.index("--dump")
        if index + 1 >= len(args):
            print("사용: python image_survey.py [폴더] [-o 결과.txt] "
                  "[--dump 그림폴더]")
            return 1
        dump_dir = args[index + 1]
        del args[index:index + 2]
    out_path: Path | None = None
    if "-o" in args:
        index = args.index("-o")
        if index + 1 >= len(args):
            print("사용: python image_survey.py [폴더|파일 ...] [-o 결과.txt]")
            return 1
        out_path = Path(args[index + 1])
        del args[index:index + 2]

    try:
        import cv2                               # noqa: F401
    except ImportError:
        print("! opencv 가 없어 판독 가능성을 잴 수 없습니다.")
        print("  pip install opencv-python-headless")
        print("  (설치 전에는 모든 그림이 '판정 불가' 로 나옵니다)\n")

    files = collect(args or TARGETS)
    if not files:
        where = ", ".join(args or TARGETS)
        print(f"훑을 문서가 없습니다 ({', '.join(SUFFIXES)}): {where}")
        print("사용: python image_survey.py [폴더|파일 ...] [-o 결과.txt]")
        return 1

    lines: list[str] = []

    def out(text: str = "") -> None:
        """화면과 (요청 시) 파일에 함께 낸다."""
        print(text)
        lines.append(text)

    out(f"대상 {len(files)}건")

    rows = []
    for index, path in enumerate(files, 1):
        if len(files) > 20 and index % 20 == 0:
            print(f"  … {index}/{len(files)}", flush=True)
        try:
            rows.append(survey(path))
        except Exception as exc:                 # noqa: BLE001 - 한 건 실패로 멈추지 않는다
            out(f"  ! {path.name}: {exc}")
    if not rows:
        out("읽을 수 있는 문서가 없었습니다")
        return 1

    out(f"\n① 문서별 ({len(rows)}건)")
    out(f"{'문서':40}{'그림':>5}{'장식':>5}{'못읽음':>7}{'보정가능':>9}"
        f"{'지면급':>7}{'최소획':>7}  처방")
    for r in sorted(rows, key=lambda r: (r["prescription"], r["name"])):
        glyph = f"{r['min_glyph']}px" if r["min_glyph"] else "-"
        out(f"{r['name'][:38]:40}{r['images']:5}{r['decor']:5}{r['poor']:7}"
            f"{r['fair']:9}{r['page_like']:7}{glyph:>7}  {r['prescription']}")

    if _SKIPPED:
        out(f"\n! 읽지 못한 그림 {len(_SKIPPED)}개 — 아래 수치는 그만큼 빠져 있다")
        for item in _SKIPPED[:10]:
            out(f"   {item}")

    out("\n② 처방별")
    counts = Counter(r["prescription"] for r in rows)
    for name, n in counts.most_common():
        out(f"   {name:14} {n:4}건 ({n / len(rows):5.1%})")

    fixable = [r for r in rows if r["fair"]]
    out(f"\n③ 보정이 듣는 문서 ({len(fixable)}건) — `fair` 그림이 있다")
    for r in sorted(fixable, key=lambda r: -r["fair"])[:30]:
        out(f"   {r['name'][:46]:48} 보정가능 {r['fair']}/{r['images']} "
            f"· 최소획 {r['min_glyph']}px")

    hopeless = [r for r in rows if r["prescription"] == "손댈 수 없음"]
    out(f"\n④ 손댈 수 없는 문서 ({len(hopeless)}건) — 그림 과반이 못 읽는다")
    for r in sorted(hopeless, key=lambda r: -r["poor"])[:20]:
        out(f"   {r['name'][:46]:48} 못읽음 {r['poor']}/{r['images']}")

    partial = [r for r in rows if r["prescription"] == "일부 손댈 수 없음"]
    out(f"\n⑤ 일부만 못 읽는 문서 ({len(partial)}건) — 나머지는 멀쩡하다")
    for r in sorted(partial, key=lambda r: -r["images"])[:15]:
        good = r["images"] - r["decor"] - r["poor"] - r["fair"]
        out(f"   {r['name'][:46]:48} 못읽음 {r['poor']}/{r['images']} "
            f"· 쓸만함 {good}")
    out()

    out("\n⑥ 유형·판정 분포 (그림 단위)")
    pairs = Counter()
    for r in rows:
        for item in r.get("_images", []):
            pairs[(item.get("kind") or "-", item.get("verdict"))] += 1
    total = sum(pairs.values()) or 1
    for (kind, verdict), n in pairs.most_common():
        note = _ROUTE.get((kind, verdict), "")
        out(f"   {kind:9} {verdict:11} {n:5}개 ({n / total:5.1%})  {note}")

    if dump_dir:
        saved = _dump(rows, dump_dir)
        out(f"\n⑦ 그림을 뽑았습니다: {dump_dir} ({saved}개)")
        out("   판정별 폴더로 나뉘어 있습니다 — 눈으로 확인하세요.")

    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"저장했습니다: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
