"""노트북 파일 선택 위젯.

역할:
    노트북에서 처리할 문서를 고르는 네 가지 경로를 하나로 묶는다 —
    업로드, samples 디렉터리, 직접 입력, Colab 업로드.
    ipywidgets 가 없으면 안내 후 set_path() 로 대체할 수 있다.
호출부:
    notebooks/preview.ipynb, notebooks/preview_colab.ipynb
출력:
    선택된 파일의 Path
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable

_log = logging.getLogger(__name__)

_MANUAL = "manual"
_UPLOAD = "upload"
_SAMPLE = "sample"
_COLAB = "colab"


def in_colab() -> bool:
    """Colab 런타임인지 판별합니다.

    ``importlib.util.find_spec`` 만 쓰면 ``__spec__`` 이 없는 모듈에 대해
    ``ValueError`` 를 던지므로, 이미 로드된 모듈을 먼저 확인합니다.
    """
    import importlib.util
    import sys

    if "google.colab" in sys.modules:
        return True
    try:
        return importlib.util.find_spec("google.colab") is not None
    except (ImportError, ValueError, AttributeError):
        return False


def _upload_entries(value: Any) -> list[dict[str, Any]]:
    """FileUpload.value 를 버전 차이 없이 [{name, content}] 로 정규화합니다.

    ipywidgets 7 은 ``{filename: {metadata: {...}, content: bytes}}`` (dict),
    8 은 ``({name: ..., content: memoryview}, ...)`` (tuple) 을 돌려줍니다.
    """
    if not value:
        return []
    items = list(value.values()) if isinstance(value, dict) else list(value)

    entries: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("metadata", {}).get("name")
        content = item.get("content")
        if content is None:
            continue
        entries.append({"name": name or "uploaded.bin", "content": bytes(content)})
    return entries


class FilePicker:
    """문서 선택 위젯.

    입력(생성자):
        work_dir    업로드 파일을 저장할 위치
        sample_dir  샘플 문서 디렉터리
        suffixes    허용 확장자
    출력:
        path      현재 선택된 경로 (없으면 None)
        resolve() 선택을 확정하고 경로 반환
    """

    def __init__(
        self,
        *,
        work_dir: Path,
        sample_dir: Path,
        suffixes: Iterable[str],
    ) -> None:
        self.work_dir = Path(work_dir)
        self.sample_dir = Path(sample_dir)
        self.suffixes = tuple(s.lower() for s in suffixes)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.sample_dir.mkdir(parents=True, exist_ok=True)

        self._last_source: str | None = None
        self._written: dict[tuple[str, int], Path] = {}
        self._colab_file: Path | None = None
        #: 위젯을 못 쓰는 환경에서 set_path() 로 지정한 경로
        self._fallback_path: Path | None = None
        self._widgets_ok = False
        self._uploader = None
        self._dropdown = None
        self._manual = None
        self._label = None
        self._build()

    # ── 위젯 구성 ---------------------------------------------------------

    def scan_samples(self) -> list[Path]:
        """샘플 디렉터리의 문서를 찾는다.

        입력: 없음
        출력: 허용 확장자에 해당하는 파일 목록
        """
        return sorted(
            p
            for p in self.sample_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in self.suffixes
        )

    def _build(self) -> None:
        try:
            import ipywidgets as W
        except ImportError:
            _log.info("ipywidgets 미설치 — 경로 직접 입력만 사용합니다.")
            return

        self._W = W
        self._uploader = W.FileUpload(
            accept=",".join(self.suffixes),
            multiple=False,
            description="파일 첨부",
            layout=W.Layout(width="200px"),
        )
        self._dropdown = W.Dropdown(
            options=self._dropdown_options(),
            description="samples/",
            layout=W.Layout(width="420px"),
            style={"description_width": "70px"},
        )
        self._manual = W.Text(
            value="",
            placeholder="또는 경로를 직접 입력 (위 두 개보다 우선)",
            description="경로",
            layout=W.Layout(width="620px"),
            style={"description_width": "70px"},
        )
        self._refresh = W.Button(description="samples 새로고침", layout=W.Layout(width="150px"))
        self._label = W.HTML()

        # 콜백은 라벨 갱신용일 뿐입니다 — 발화하지 않아도 .path 는 정상 동작합니다.
        self._uploader.observe(lambda _c: self._touch(_UPLOAD), names="value")
        self._dropdown.observe(lambda _c: self._touch(_SAMPLE), names="value")
        self._manual.observe(lambda _c: self._touch(_MANUAL), names="value")
        self._refresh.on_click(lambda _b: self._on_refresh())

        self._widgets_ok = True
        self._update_label()

    def _dropdown_options(self) -> list[tuple[str, str | None]]:
        samples = self.scan_samples()
        head = (
            f"— {len(samples)}건 —" if samples else "— samples/ 폴더가 비어 있음 —"
        )
        return [(head, None)] + [(p.name, str(p)) for p in samples]

    def _on_refresh(self) -> None:
        self._dropdown.options = self._dropdown_options()
        self._update_label()

    def _touch(self, source: str) -> None:
        self._last_source = source
        self._update_label()

    # ── 상태 표시 ---------------------------------------------------------

    def _update_label(self) -> None:
        if self._label is None:
            return
        path = self.path
        if path is None:
            self._label.value = (
                '<div style="padding:6px 0;color:#d97706;font-size:13px;">'
                "선택된 파일 없음 — 위에서 하나 고르세요.</div>"
            )
            return
        exists = path.is_file()
        color = "#16a34a" if exists else "#dc2626"
        note = f"{path.stat().st_size:,} bytes" if exists else "⚠ 파일이 존재하지 않습니다"
        self._label.value = (
            f'<div style="padding:6px 0;font-size:13px;">선택됨 &nbsp;'
            f'<code style="color:{color};font-weight:600;">{path.name}</code>'
            f'<span style="color:#64748b;"> &nbsp;{note}</span><br>'
            f'<span style="color:#94a3b8;font-size:11px;">{path}</span></div>'
        )

    # ── 핵심: 호출 시점 해석 ----------------------------------------------

    def _manual_path(self) -> Path | None:
        if self._manual is not None:
            raw = (self._manual.value or "").strip().strip("'\"")
            if raw:
                return Path(raw).expanduser()
        return self._fallback_path

    def _upload_path(self) -> Path | None:
        """업로드된 내용을 디스크에 쓰고 경로를 반환합니다 (동일 파일은 재사용)."""
        if self._uploader is None:
            return None
        entries = _upload_entries(self._uploader.value)
        if not entries:
            return None

        entry = entries[0]
        name = Path(entry["name"]).name or "uploaded.bin"
        content: bytes = entry["content"]
        key = (name, len(content))
        cached = self._written.get(key)
        if cached is not None and cached.is_file():
            return cached

        dest = self.work_dir / name
        dest.write_bytes(content)
        self._written[key] = dest
        return dest

    def _colab_path(self) -> Path | None:
        return self._colab_file if (self._colab_file and self._colab_file.is_file()) else None

    def colab_upload(self) -> Path:
        """Colab 업로드 대화상자를 연다.

        입력: 없음
        출력: 업로드된 파일 경로. 취소하면 None
        """
        from google.colab import files

        uploaded = files.upload()
        if not uploaded:
            raise ValueError("업로드가 취소되었거나 파일이 없습니다.")

        name, content = next(iter(uploaded.items()))
        dest = self.work_dir / Path(name).name
        dest.write_bytes(content)

        self._colab_file = dest
        self._last_source = _COLAB
        self._update_label()
        print(f"업로드됨: {dest.name} ({len(content):,} bytes)")
        return self.resolve()

    def _sample_path(self) -> Path | None:
        if self._dropdown is None:
            return None
        value = self._dropdown.value
        return Path(value) if value else None

    @property
    def path(self) -> Path | None:
        """현재 선택된 경로.

        입력: 없음
        출력: Path. 아직 고르지 않았으면 None
        비고: 업로드·직접입력·샘플 순으로 확인한다
        """
        resolvers = {
            _MANUAL: self._manual_path,
            _COLAB: self._colab_path,
            _UPLOAD: self._upload_path,
            _SAMPLE: self._sample_path,
        }
        if self._last_source:
            found = resolvers[self._last_source]()
            if found is not None:
                return found
        # 우선순위 폴백: 직접 입력 > Colab 업로드 > 위젯 업로드 > samples
        for source in (_MANUAL, _COLAB, _UPLOAD, _SAMPLE):
            found = resolvers[source]()
            if found is not None:
                return found
        return None

    def resolve(self) -> Path:
        """선택을 확정한다.

        입력: 없음
        출력: 선택된 Path
        예외: 아무것도 고르지 않았으면 FileNotFoundError
        """
        path = self.path
        if path is None:
            raise ValueError(
                "파일이 선택되지 않았습니다.\n"
                "  · 위 셀에서 [파일 첨부] 버튼을 쓰거나\n"
                f"  · {self.sample_dir} 에 문서를 넣고 드롭다운에서 고르거나\n"
                "  · [경로] 칸에 전체 경로를 직접 입력하세요.\n"
                + (
                    "  · Colab: picker.colab_upload() 을 실행하세요."
                    if in_colab()
                    else "  · 위젯이 안 보이면: pip install ipywidgets 후 커널 재시작"
                )
            )
        path = path.expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"파일이 존재하지 않습니다: {path}")
        if path.suffix.lower() not in self.suffixes:
            raise ValueError(
                f"지원하지 않는 형식: {path.suffix!r} "
                f"(지원: {', '.join(self.suffixes)})"
            )
        return path

    # ── 표시 --------------------------------------------------------------

    def display(self) -> None:
        """위젯을 화면에 표시한다.

        입력: 없음
        출력: 없음. ipywidgets 가 없으면 대체 사용법을 안내한다
        """
        from IPython.display import display

        if in_colab():
            print("Colab 감지 — 업로드는 picker.colab_upload() 가 가장 확실합니다.")

        if not self._widgets_ok:
            print("ipywidgets 를 사용할 수 없습니다 — 아래처럼 경로를 직접 지정하세요.")
            print('    picker.set_path("/경로/문서.pdf")')
            for p in self.scan_samples():
                print(f"    samples/ : {p.name}")
            return

        W = self._W
        display(
            W.VBox(
                [
                    W.HBox([self._uploader, self._dropdown, self._refresh]),
                    self._manual,
                    self._label,
                ]
            )
        )

    def set_path(self, path: str | Path) -> Path:
        """위젯 없이 경로를 지정한다.

        입력: path — 문서 경로
        출력: 확정된 Path
        """
        if self._manual is not None:
            self._manual.value = str(path)
        else:
            self._fallback_path = Path(path).expanduser().resolve()
        self._last_source = _MANUAL
        self._update_label()
        return self.resolve()

    def refresh(self) -> None:
        """샘플 목록을 다시 읽는다.

        입력: 없음
        출력: 없음
        """
        self._update_label()
