"""그림 영역을 **원본 화소 그대로** 뽑는다 (해상도 손실 방지).

입력:
    PDF 쪽 + bbox
출력:
    원본 화소 그대로의 그림 파일

역할:
    Docling 이 내보내는 그림 이미지는 `images_scale` 배율로 **다시 렌더**한
    것이라, 원본에 심어진 비트맵보다 해상도가 낮을 수 있다. 여기서는 그
    영역이 통짜 래스터 하나면 **그 비트맵을 그대로** 꺼내고, 아니면
    (벡터 도해 등) 충분한 배율로 렌더해 잘라낸다.
호출부:
    media.picture.picture_to_block (source_path 가 있을 때)
설정:
    DOCSTRUCT_REGION_MIN_PX (기본 1600) — 렌더 경로에서 목표로 하는 긴 변 화소

왜 필요한가 (실측)
----------------
조달청 성과계획서 59쪽 그래프:

    원본 심긴 비트맵   3139 × 947 px (600 DPI)
    Docling 기본 내보내기(images_scale=1.0)  ≈ 377 × 114 px (72 DPI)

**8배를 버리고 저장하고 있었다.** 축 눈금·범례 글자는 이 배율에서 뭉개져
VLM 이 읽을 수 없다. 초해상(SR)으로 되살릴 대상이 아니라 **애초에 버리지
않으면 되는** 정보다 — SR 은 없는 정보를 지어내지만, 원본 추출은 있는
정보를 그대로 옮긴다.

한계: 원본 비트맵 자체가 저해상인 스캔 문서에는 이 경로가 도움이 되지
않는다. 그때는 렌더 경로도 같은 화소를 볼 뿐이다 (없는 것을 만들지
않는다는 원칙 그대로).
"""
from __future__ import annotations

import ctypes
import io
import logging
import os

_log = logging.getLogger(__name__)

#: 렌더 경로에서 목표로 하는 긴 변 화소 수.
DEFAULT_MIN_PX = 1600
#: 렌더 배율 상한 — 이보다 키우면 메모리만 는다.
MAX_SCALE = 8.0
#: 원본 비트맵 위에 글자·선이 겹쳐 그려진 경우, 비트맵만 꺼내면 그 겹침을
#: 잃는다. 영역 안에 이만큼 넘는 겹침 객체가 있으면 렌더 경로로 간다.
MAX_OVERLAY = 0


def _min_px() -> int:
    """렌더 목표 화소 (환경변수로 조정 가능)."""
    try:
        return max(400, int(os.getenv("DOCSTRUCT_REGION_MIN_PX", DEFAULT_MIN_PX)))
    except ValueError:
        return DEFAULT_MIN_PX


def _overlap_ratio(inner, outer) -> float:
    """inner 가 outer 안에 든 비율 (겹침 면적 ÷ inner 면적)."""
    left = max(inner[0], outer[0]); top = max(inner[1], outer[1])
    right = min(inner[2], outer[2]); bottom = min(inner[3], outer[3])
    if right <= left or bottom <= top:
        return 0.0
    area = (inner[2] - inner[0]) * (inner[3] - inner[1])
    return ((right - left) * (bottom - top) / area) if area > 0 else 0.0


def extract_region_png(source_path, page_no: int, bbox: dict) -> tuple[bytes, str] | None:
    """영역 이미지를 PNG 로 낸다 — 원본 비트맵 우선, 없으면 고배율 렌더.

    입력: source_path — 원본 PDF, page_no — 1부터, bbox — TOPLEFT {l,t,r,b}
    출력: (PNG 바이트, 출처 "native"|"render"). 못 만들면 None
    비고:
        영역의 90% 이상을 덮는 래스터 객체가 하나면 그 비트맵을 그대로
        쓴다. 그런 객체가 없거나(벡터 도해) 원본이 렌더보다 작으면 렌더
        경로로 간다 — 둘 중 **화소가 많은 쪽**을 고르는 것이지, 어느
        경로든 없는 정보를 만들지 않는다.
    """
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw as raw
    except ImportError:
        return None

    try:
        document = pdfium.PdfDocument(str(source_path))
    except Exception as exc:                     # noqa: BLE001 - 보조 경로다
        _log.debug("원본을 열지 못했습니다: %s", exc)
        return None
    try:
        index = page_no - 1
        if not 0 <= index < len(document):
            return None
        page = document[index]
        width_pt, height_pt = page.get_size()
        region = (bbox["l"], bbox["t"], bbox["r"], bbox["b"])
        region_w = max(1.0, region[2] - region[0])
        region_h = max(1.0, region[3] - region[1])

        # 렌더 경로에서 쓸 배율 — 영역의 긴 변이 목표 화소가 되게.
        target = _min_px()
        scale = min(MAX_SCALE, max(2.0, target / max(region_w, region_h)))

        # ① 원본 비트맵 — 영역을 거의 다 덮는 래스터 하나를 찾는다.
        #    같은 걸음에 겹침 객체(글자·선)도 센다.
        best = None
        overlays = 0
        for obj in page.get_objects():
            if obj.type != raw.FPDF_PAGEOBJ_IMAGE:
                left = ctypes.c_float(); bottom = ctypes.c_float()
                right = ctypes.c_float(); top = ctypes.c_float()
                if raw.FPDFPageObj_GetBounds(
                        obj.raw, ctypes.byref(left), ctypes.byref(bottom),
                        ctypes.byref(right), ctypes.byref(top)):
                    cx = (left.value + right.value) / 2.0
                    cy = height_pt - (top.value + bottom.value) / 2.0
                    if (region[0] <= cx <= region[2]
                            and region[1] <= cy <= region[3]):
                        overlays += 1
                continue
            left = ctypes.c_float(); bottom = ctypes.c_float()
            right = ctypes.c_float(); top = ctypes.c_float()
            if not raw.FPDFPageObj_GetBounds(
                    obj.raw, ctypes.byref(left), ctypes.byref(bottom),
                    ctypes.byref(right), ctypes.byref(top)):
                continue
            box = (left.value, height_pt - top.value,
                   right.value, height_pt - bottom.value)
            if _overlap_ratio(box, region) < 0.9:
                continue
            try:
                pil = obj.get_bitmap(render=False).to_pil()
            except Exception:                    # noqa: BLE001 - 형식에 따라 실패한다
                continue
            if best is None or pil.width * pil.height > best.width * best.height:
                best = pil

        # 원본이 있으면 원본이다. **화소 수로 견주지 않는다** — 렌더는
        # 그 비트맵을 확대할 뿐이라 수치는 커져도 정보는 늘지 않는다
        # (그 가짜 이득을 고르는 것이 곧 초해상을 흉내 내는 일이다).
        if best is not None and overlays <= MAX_OVERLAY:
            buffer = io.BytesIO()
            best.save(buffer, format="PNG")
            return buffer.getvalue(), "native"

        # ② 렌더 경로 — 목표 화소가 되도록 배율을 올려 잘라낸다
        image = page.render(scale=scale).to_pil()
        crop = image.crop((int(region[0] * scale), int(region[1] * scale),
                           int(region[2] * scale), int(region[3] * scale)))
        buffer = io.BytesIO()
        crop.save(buffer, format="PNG")
        return buffer.getvalue(), "render"
    except Exception as exc:                     # noqa: BLE001
        _log.debug("%s쪽 영역 이미지 추출 실패: %s", page_no, exc)
        return None
    finally:
        document.close()
