"""rapidocr 직접 호출 — 한국어 인식 모델을 강제한다.

역할:
    docling 을 거치지 않고 rapidocr 을 직접 불러 페이지 이미지를 읽는다.
    docling 의 `RapidOcrOptions.lang` 만으로는 **모델 버전을 정할 수 없어**
    한국어 문서가 통째로 중국어로 읽히는 문제가 있다.
호출부:
    docstruct.extractors.pdf (ocr_backend='rapidocr-ko' 일 때)
입력: 페이지 이미지 경로 또는 numpy 배열
출력: OcrLine 목록 (텍스트·신뢰도·좌표)

왜 직접 부르는가
--------------
rapidocr 3.x 는 기본 인식 모델을 **PP-OCRv6 small** 로 바꿨는데, 그 모델에
한국어가 없다. `lang=["korean"]` 을 넘겨도 이렇게 거부되고 중국어 모델로
되돌아간다.

    ValueError: Unsupported rec.lang_type='korean' for PP-OCRv6 small model.

그 결과 한글 지면이 한자·가나로 나온다. 실제 문서(2025 주택과 세금,
380쪽)에서 **한글 0%** 였고 본문이 `气····吾·咎今`, `ヤ君居 |0号` 같은
문자열이 됐다. `force_full_page_ocr=True` 로 전면 OCR 을 켜도 같았다 —
엔진이 도는데 언어가 틀린 것이라 켜고 끄는 문제가 아니었다.

한국어 모델은 존재한다. 다만 세 값을 함께 지정해야 선택된다.

    Rec.lang_type   = KOREAN
    Rec.model_type  = MOBILE      (server 조합은 없음)
    Rec.ocr_version = PPOCRV5     (v4 도 있으나 v5 가 최신)

docling 의 옵션 객체에는 이 세 값을 넘길 자리가 없다. 그래서 우회한다.

모델 내려받기
------------
처음 실행할 때 rapidocr 이 알아서 받아 패키지 폴더에 캐시한다. 우리가
파일을 챙길 필요는 없다. 다만 사내망에서 modelscope.cn 이 막혀 있으면
실패하므로, 그때는 밖에서 받아 `~/.rapidocr` 아래 같은 경로에 두거나
`DOCSTRUCT_RAPIDOCR_MODEL_DIR` 로 폴더를 지정한다.
"""
from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

#: 미리 받아 둔 모델을 둘 폴더 (사내망용).
MODEL_DIR_ENV = "DOCSTRUCT_RAPIDOCR_MODEL_DIR"

#: 인식 모델 버전. v5 가 최신이며 한국어 mobile 모델이 있다.
VERSION_ENV = "DOCSTRUCT_RAPIDOCR_VERSION"

#: 이 값 미만인 조각은 버린다. 낮은 신뢰도 조각은 대개 장식·괘선이다.
SCORE_ENV = "DOCSTRUCT_RAPIDOCR_MIN_SCORE"
DEFAULT_MIN_SCORE = 0.5


@dataclass
class OcrLine:
    """인식된 텍스트 한 줄.

    입력(필드):
        text   인식 결과
        score  신뢰도 0~1
        box    [(x, y), ...] 네 꼭짓점. 없으면 None
    """

    text: str
    score: float
    box: list[tuple[float, float]] | None = None

    @property
    def top(self) -> float:
        """상단 y 좌표 (읽기 순서 정렬용).

        입력: 없음
        출력: 꼭짓점 y 최솟값. box 가 없으면 0.0
        """
        return min(p[1] for p in self.box) if self.box else 0.0

    @property
    def left(self) -> float:
        """좌측 x 좌표.

        입력: 없음
        출력: 꼭짓점 x 최솟값. box 가 없으면 0.0
        """
        return min(p[0] for p in self.box) if self.box else 0.0


_engine: Any = None
_engine_lock = threading.Lock()


def _min_score() -> float:
    """조각을 버릴 신뢰도 하한.

    입력: 없음 (`DOCSTRUCT_RAPIDOCR_MIN_SCORE`)
    출력: 0~1 실수. 잘못된 값이면 기본값
    """
    raw = os.environ.get(SCORE_ENV, "").strip()
    try:
        value = float(raw) if raw else DEFAULT_MIN_SCORE
    except ValueError:
        _log.warning("%s 값이 숫자가 아닙니다: %r", SCORE_ENV, raw)
        return DEFAULT_MIN_SCORE
    return value if 0.0 <= value <= 1.0 else DEFAULT_MIN_SCORE


def _build_params() -> dict[str, Any]:
    """한국어 모델을 고르는 rapidocr 파라미터.

    입력: 없음 (`DOCSTRUCT_RAPIDOCR_VERSION`, `DOCSTRUCT_RAPIDOCR_MODEL_DIR`)
    출력: RapidOCR(params=...) 에 넘길 dict
    예외: rapidocr 미설치 시 ImportError
    비고:
        세 값을 **함께** 줘야 한다. lang_type 만 주면 기본 v6 small 이
        선택되고, 그 모델에 한국어가 없어 중국어로 되돌아간다.
    """
    from rapidocr import LangRec, ModelType, OCRVersion

    versions = {"v4": OCRVersion.PPOCRV4, "v5": OCRVersion.PPOCRV5}
    wanted = os.environ.get(VERSION_ENV, "v5").strip().lower()
    version = versions.get(wanted)
    if version is None:
        _log.warning("%s=%r 는 알 수 없는 값입니다 — v5 를 씁니다", VERSION_ENV, wanted)
        version = OCRVersion.PPOCRV5

    params: dict[str, Any] = {
        "Rec.lang_type": LangRec.KOREAN,
        # server 조합은 한국어 모델이 없다. mobile 만 쓸 수 있다.
        "Rec.model_type": ModelType.MOBILE,
        "Rec.ocr_version": version,
    }

    model_dir = os.environ.get(MODEL_DIR_ENV, "").strip()
    if model_dir:
        # 사내망에서 modelscope.cn 이 막힌 경우, 미리 받아 둔 파일을 쓴다.
        folder = Path(model_dir).expanduser()
        found = sorted(folder.glob("korean_PP-OCR*_rec_mobile.onnx"))
        if found:
            params["Rec.model_path"] = str(found[0])
            _log.info("미리 받아 둔 인식 모델을 씁니다: %s", found[0].name)
        else:
            _log.warning(
                "%s=%s 에 korean_PP-OCR*_rec_mobile.onnx 가 없습니다 — "
                "자동 내려받기로 진행합니다", MODEL_DIR_ENV, folder,
            )
    return params


def get_engine():
    """한국어 rapidocr 엔진을 만든다 (한 번만 만들고 재사용).

    입력: 없음
    출력: RapidOCR 인스턴스
    예외:
        ImportError   rapidocr 미설치
        RuntimeError  모델을 내려받지 못했거나 조합이 유효하지 않음
    비고:
        모델 로딩이 수 초 걸리므로 캐시한다. 여러 스레드가 동시에 만들지
        않도록 락으로 감싼다 — 배치에서 페이지를 병렬 처리할 때 필요하다.
    """
    global _engine
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        try:
            from rapidocr import RapidOCR
        except ImportError as exc:
            raise ImportError(
                "rapidocr 이 설치되어 있지 않습니다: pip install rapidocr"
            ) from exc

        params = _build_params()
        try:
            _engine = RapidOCR(params=params)
        except Exception as exc:                 # noqa: BLE001 - 원인을 그대로 전한다
            raise RuntimeError(
                f"한국어 OCR 모델을 준비하지 못했습니다: {exc}\n"
                "사내망이라면 modelscope.cn 이 막혀 있을 수 있습니다. "
                f"모델을 미리 받아 {MODEL_DIR_ENV} 로 폴더를 지정하세요."
            ) from exc
        _log.info("한국어 OCR 엔진 준비 완료 (%s)", params["Rec.ocr_version"])
        return _engine


def read_image(image: str | Path) -> list[OcrLine]:
    """이미지 하나를 읽어 텍스트 줄 목록을 낸다.

    입력: image — 이미지 경로
    출력: OcrLine 목록. 아무것도 못 읽으면 빈 목록
    비고:
        신뢰도가 하한 미만인 조각은 버린다. 표 괘선이나 장식이 글자로
        잡히는 일이 잦은데, 그런 조각은 대개 신뢰도가 낮다.
    """
    engine = get_engine()
    try:
        result = engine(str(image))
    except Exception as exc:                     # noqa: BLE001 - 한 장 실패로 전체를 멈추지 않는다
        _log.warning("OCR 실패 (%s): %s", image, exc)
        return []

    texts = list(getattr(result, "txts", None) or [])
    scores = list(getattr(result, "scores", None) or [])
    boxes = list(getattr(result, "boxes", None) or [])

    threshold = _min_score()
    lines: list[OcrLine] = []
    for index, text in enumerate(texts):
        if not (text or "").strip():
            continue
        score = float(scores[index]) if index < len(scores) else 1.0
        if score < threshold:
            continue
        box = None
        if index < len(boxes) and boxes[index] is not None:
            box = [(float(x), float(y)) for x, y in boxes[index]]
        lines.append(OcrLine(text=text.strip(), score=score, box=box))
    return lines


def read_page_text(image: str | Path) -> str:
    """이미지 하나를 읽어 본문 문자열로 만든다.

    입력: image — 이미지 경로
    출력: 읽기 순서로 이어 붙인 텍스트
    비고:
        위에서 아래, 같은 높이면 왼쪽에서 오른쪽으로 잇는다. 좌표가 없는
        조각은 원래 순서를 지킨다 — 임의로 옮기면 더 나빠진다.

        단순한 규칙이지만 단 段 구성(2단 편집 등)에서는 어긋난다. 그런
        지면은 레이아웃 분석이 따로 필요하며, 여기서 하려는 것은 **글자를
        올바른 언어로 읽는 것**이지 읽기 순서 복원이 아니다.
    """
    lines = read_image(image)
    if not lines:
        return ""
    if all(line.box for line in lines):
        # 같은 줄로 볼 세로 오차. 글자 높이의 절반쯤으로 잡는다.
        tolerance = 12.0
        lines.sort(key=lambda ln: (round(ln.top / tolerance), ln.left))
    return "\n".join(line.text for line in lines)


def compare(pdf_path: str | Path, page_no: int = 1, *, scale: float = 2.0) -> None:
    """한 쪽을 기본 설정과 한국어 모델로 각각 읽어 비교한다 (진단용).

    입력: pdf_path — PDF 경로, page_no — 1부터, scale — 렌더 배율
    출력: 없음 (stdout)
    비고:
        모델 교체가 실제로 효과가 있는지 **한 쪽만으로 30초 안에** 확인하기
        위한 도구다. 380쪽을 다 돌려 보고 판단할 이유가 없다.

        판정은 한글 비율로 한다. 지금 기본 설정은 한국어 문서에서 0% 가
        나오므로, 개선 여부가 즉시 드러난다.
    """
    import re

    import pypdfium2 as pdfium

    source = Path(pdf_path).expanduser()
    render_path = Path(f"/tmp/_ocr_compare_p{page_no}.png")
    document = pdfium.PdfDocument(str(source))
    try:
        if not 1 <= page_no <= len(document):
            print(f"쪽 번호가 범위를 벗어납니다 (1~{len(document)})")
            return
        document[page_no - 1].render(scale=scale).to_pil().save(render_path)
    finally:
        document.close()

    def ratio(text: str) -> float:
        """한글 비율."""
        chars = len(re.sub(r"\s", "", text))
        return len(re.findall(r"[가-힣]", text)) / max(chars, 1)

    from rapidocr import RapidOCR

    print(f"{source.name} · {page_no}쪽\n")

    print("── 기본 설정 (현재 파이프라인)")
    try:
        plain = RapidOCR()(str(render_path))
        text = " ".join(getattr(plain, "txts", None) or [])
        print(f"   한글 {ratio(text):.1%} · {len(text)}자")
        print(f"   {text[:150]}")
    except Exception as exc:                     # noqa: BLE001 - 비교 도구다
        print(f"   실패: {exc}")

    print("\n── 한국어 모델")
    try:
        text = read_page_text(render_path)
        print(f"   한글 {ratio(text):.1%} · {len(text)}자")
        print(f"   {text[:150]}")
    except Exception as exc:                     # noqa: BLE001
        print(f"   실패: {exc}")


def main() -> None:
    """CLI 진입점.

    입력: 없음 (argv: <pdf경로> [쪽번호=1])
    출력: 없음 (stdout)
    """
    import sys

    if len(sys.argv) < 2:
        print("사용법: python -m docstruct.converters.pdf.rapidocr_ko <pdf> [쪽번호]")
        raise SystemExit(1)
    compare(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 1)


if __name__ == "__main__":
    main()
