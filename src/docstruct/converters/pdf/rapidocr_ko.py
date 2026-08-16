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
#:
#: 실측(2025 주택과 세금, 26쪽)에서 0.5 → 0.7 로 올리니 `Y늦`·`운은-`·`Y`
#: 같은 잡음이 사라지고 글자는 7% 만 줄었다(714 → 662자). 오히려
#: `-취득 후3년 내 신축` 처럼 잘려 있던 줄이 온전해졌다.
SCORE_ENV = "DOCSTRUCT_RAPIDOCR_MIN_SCORE"
DEFAULT_MIN_SCORE = 0.7

#: 잡음 조각 제거를 끄는 스위치 (`DOCSTRUCT_OCR_KEEP_NOISE=true`).
KEEP_NOISE_ENV = "DOCSTRUCT_OCR_KEEP_NOISE"

#: 미등록어 비율이 이 값 이상이면 잡음으로 본다.
_UNKNOWN_RATIO = 0.4

#: 형태소 분석기가 "정상 낱말이 아니다" 로 붙이는 태그.
#:   SL 외국어 · SH 한자 · SW 기호 · UN 미등록어 · NA 분석 불능
_UNKNOWN_TAGS = frozenset({"SL", "SH", "SW", "UN", "NA"})

_kiwi: Any = None
_kiwi_lock = threading.Lock()


def _get_kiwi() -> Any:
    """형태소 분석기를 준비한다 (한 번만 만들고 재사용).

    입력: 없음
    출력: Kiwi 인스턴스. kiwipiepy 가 없으면 None
    비고:
        선택 의존성이다. 없으면 잡음 제거를 건너뛰고 OCR 결과를 그대로
        쓴다 — 설치 여부가 파이프라인을 깨뜨리면 안 된다.
    """
    global _kiwi
    if _kiwi is not None:
        return _kiwi
    with _kiwi_lock:
        if _kiwi is not None:
            return _kiwi
        try:
            from kiwipiepy import Kiwi
        except ImportError:
            _log.debug("kiwipiepy 가 없어 OCR 잡음 제거를 건너뜁니다")
            return None
        _kiwi = Kiwi()
        return _kiwi


def is_noise(text: str) -> bool:
    """OCR 이 장식·로고를 글자로 잘못 읽은 조각인지 판별한다.

    입력: text — 인식된 한 줄
    출력: 잡음으로 보이면 True
    비고:
        이 문서에는 색상 블록과 아이콘이 많아 `YoHIYL`, `OSUMMM`,
        `C168zs운道lYR IIllY IY올` 같은 조각이 섞인다. 원본에 대응하는
        글자가 없으므로 **고칠 대상이 아니라 지울 대상**이다.

        판별은 형태소 태그로 한다. 라틴·한자·기호·미등록어가 40% 이상이면
        잡음으로 본다. 실측에서 잡음 10개 중 7개를 잡고 정상 문장
        16개에는 오탐이 없었다. 정상 문서 1,200줄에서도 오탐은 0.1%(1건,
        그마저 HTML 주석)였다.

        **1음절 명사 연속 비율은 쓰지 않는다.** 신호로 유망해 보였으나
        `개 정`(1.00), `-취득 후3년 내 신축`(0.50) 같은 정상 문구가 걸려
        쓸 수 없었다.

        `을글`·`운은-` 처럼 순수 한글로 이뤄진 잡음은 걸러지지 않는다.
        그런 조각을 잡으려면 실제 낱말인지 판단해야 하는데, 사전에 있는
        글자들의 조합이라 형태소 분석으로는 구분되지 않는다.
    """
    stripped = text.strip()
    if not stripped:
        return True
    kiwi = _get_kiwi()
    if kiwi is None:
        return False
    tokens = kiwi.tokenize(stripped)
    if not tokens:
        return False
    unknown = sum(1 for token in tokens if token.tag in _UNKNOWN_TAGS)
    return unknown / len(tokens) >= _UNKNOWN_RATIO


def _drop_noise() -> bool:
    """잡음 조각을 버릴지.

    입력: 없음 (`DOCSTRUCT_OCR_KEEP_NOISE`)
    출력: 버리면 True (기본), 보존 설정이면 False
    """
    raw = os.environ.get(KEEP_NOISE_ENV, "").strip().lower()
    return raw not in ("1", "true", "on", "yes")


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


def _as_list(value: Any) -> list:
    """결과 필드를 목록으로 바꾼다.

    입력: value — rapidocr 결과의 한 필드 (numpy 배열·목록·None)
    출력: 파이썬 목록. 값이 없으면 빈 목록
    비고:
        `value or []` 를 쓰면 안 된다. rapidocr 은 `boxes` 를 **numpy 배열**로
        주는데, 배열에 `or` 를 걸면 진리값을 물어 이렇게 터진다.

            ValueError: The truth value of an array with more than one
                        element is ambiguous.

        `is None` 으로만 판별하고, 길이가 0인 배열도 목록으로 바꾸면
        자연스럽게 빈 목록이 된다.
    """
    if value is None:
        return []
    return list(value)


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

    texts = _as_list(getattr(result, "txts", None))
    scores = _as_list(getattr(result, "scores", None))
    boxes = _as_list(getattr(result, "boxes", None))

    threshold = _min_score()
    skip_noise = _drop_noise()
    lines: list[OcrLine] = []
    dropped = 0
    for index, text in enumerate(texts):
        text = str(text).strip()
        if not text:
            continue
        score = float(scores[index]) if index < len(scores) else 1.0
        if score < threshold:
            continue
        if skip_noise and is_noise(text):
            dropped += 1
            continue
        box = None
        if index < len(boxes) and boxes[index] is not None:
            # 꼭짓점도 numpy 배열일 수 있다 — 파이썬 실수로 바꿔 둔다.
            box = [(float(point[0]), float(point[1])) for point in boxes[index]]
        lines.append(OcrLine(text=text, score=score, box=box))
    if dropped:
        _log.debug("잡음 조각 %d개를 버렸습니다 (%s)", dropped, image)
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
    import tempfile

    import pypdfium2 as pdfium

    source = Path(pdf_path).expanduser()
    # `/tmp` 는 Windows 에 없다. OS 가 알려 주는 임시 폴더를 쓴다.
    render_path = Path(tempfile.gettempdir()) / f"_ocr_compare_p{page_no}.png"
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
        text = " ".join(str(x) for x in _as_list(getattr(plain, "txts", None)))
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
