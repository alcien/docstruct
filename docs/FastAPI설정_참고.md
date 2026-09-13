# FastAPI 에서 docstruct 설정 받기

사내 `server.py` 에 붙여 쓰는 참고 코드입니다. `docstruct.configure()` 가
받는 키를 그대로 요청 본문으로 노출합니다.

---

## 지금 상황

`verify_ocr` 을 켜고 싶은데 요청에 그 칸이 없어, CLI 로만 켤 수 있습니다.

    docstruct 문서.pdf -o out --set verify_ocr=true     되는 것
    POST /convert  {"verify_ocr": true}                  안 되는 것

---

## 설정 목록은 코드에서 가져옵니다

손으로 적으면 설정이 늘 때마다 어긋납니다.

```python
import docstruct

ALLOWED = set(docstruct.option_keys())   # 51개
```

`--set` 으로 되는 것은 모두 여기 있습니다. 새 설정이 생겨도 자동으로
따라옵니다.

---

## 요청 모델

```python
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator


class ConvertOptions(BaseModel):
    """문서 변환 요청에 실을 docstruct 설정.

    입력: docstruct.option_keys() 에 있는 키
    출력: configure() 에 넘길 dict
    비고:
        **모르는 키는 거부한다.** 조용히 무시하면 사용자가 설정을 켰다고
        믿는데 실제로는 안 켜진다 — 결과만 보고는 알 수 없다.
    """

    settings: dict[str, Any] = Field(
        default_factory=dict,
        description="docstruct 설정. 예: {\"verify_ocr\": true}",
        examples=[{"verify_ocr": True, "scanned_skip_docling_ocr": True}],
    )

    @field_validator("settings")
    @classmethod
    def _known_keys_only(cls, value: dict[str, Any]) -> dict[str, Any]:
        import docstruct

        allowed = set(docstruct.option_keys())
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(
                f"모르는 설정: {', '.join(unknown)}. "
                f"쓸 수 있는 것: {', '.join(sorted(allowed))}"
            )
        return value
```

---

## 적용

```python
import docstruct


def apply_options(options: ConvertOptions) -> dict:
    """요청에 실린 설정을 적용한다.

    입력: options — ConvertOptions
    출력: 실제 적용된 설정 (비밀값은 가려짐)
    비고:
        `configure()` 는 **프로세스 전역**에 남는다. 요청마다 다른 설정을
        쓰려면 처리 뒤에 되돌려야 한다 — 아래 `scoped_options` 를 쓴다.
    """
    if not options.settings:
        return {}
    return docstruct.configure(**options.settings)
```

### 요청 단위로 격리하기

전역이라 그대로 두면 **다음 요청까지 남습니다.** 동시에 여러 요청이 오면
서로 간섭하고요.

```python
import contextlib
import threading

_SETTINGS_LOCK = threading.Lock()


@contextlib.contextmanager
def scoped_options(settings: dict):
    """이 요청 동안만 설정을 적용한다.

    입력: settings — docstruct 설정
    출력: 없음 (컨텍스트)
    비고:
        `configure()` 가 전역이므로 **락으로 감싼다.** 그러지 않으면 동시
        요청이 서로의 설정을 덮어쓴다.

        요청마다 설정이 다르면 처리가 직렬화된다. 설정이 늘 같다면 기동
        시 한 번만 `configure()` 하고 이것을 쓰지 않는 편이 빠르다.
    """
    if not settings:
        yield
        return

    import docstruct
    from docstruct.core.config import get_settings

    with _SETTINGS_LOCK:
        before = {k: getattr(get_settings(), k, None) for k in settings}
        docstruct.configure(**settings)
        try:
            yield
        finally:
            restore = {k: v for k, v in before.items() if v is not None}
            if restore:
                docstruct.configure(**restore)
```

---

## 엔드포인트

```python
from fastapi import File, Form, UploadFile


@app.post("/convert")
async def convert(
    file: UploadFile = File(...),
    settings: str = Form("{}", description='docstruct 설정 (JSON)'),
):
    """문서를 변환한다.

    입력: file — 문서, settings — docstruct 설정 JSON
    출력: document.json
    """
    import json

    try:
        parsed = ConvertOptions(settings=json.loads(settings or "{}"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    path = await _save_upload(file)
    with scoped_options(parsed.settings):
        doc = docstruct.DocStruct().run(path)
    return doc.to_dict()
```

`multipart/form-data` 라 설정을 문자열로 받습니다. JSON 본문이면 중첩 모델을
그대로 쓸 수 있습니다.

---

## 쓰는 쪽

```bash
curl -X POST http://서버/convert \
  -F "file=@주택과세금_검증11쪽.pdf" \
  -F 'settings={"verify_ocr": true}'
```

```python
import requests

requests.post(
    "http://서버/convert",
    files={"file": open("문서.pdf", "rb")},
    data={"settings": json.dumps({
        "verify_ocr": True,
        "scanned_skip_docling_ocr": True,
    })},
)
```

---

## 설정 목록을 알려 주는 엔드포인트

무엇을 쓸 수 있는지 물어볼 수 있어야 합니다.

```python
@app.get("/options")
def options():
    """쓸 수 있는 설정 목록.

    출력: {키: 현재값}
    """
    import docstruct
    from docstruct.core.config import get_settings

    current = get_settings()
    return {k: getattr(current, k, None) for k in docstruct.option_keys()}
```

---

## 실수하기 쉬운 것

### 모르는 키를 조용히 무시하지 마세요

    {"verify_ocr": true}   →  오타로 {"verfy_ocr": true} 를 보냈다면

무시하면 **켰다고 믿는데 안 켜집니다.** 결과만 보고는 알 수 없어, 앞서
`scanned_skip_docling_ocr` 이 안 먹은 것을 한참 뒤에야 알아차렸습니다.

### 무엇이 적용됐는지 결과에 남기세요

`document.json` 의 `pipeline` 에 주요 설정이 실립니다. 요청이 실제로
반영됐는지 그것으로 확인합니다.

```json
"pipeline": {"verify_ocr": true, "korean_ocr": true, ...}
```

### LLM 설정도 같은 방법으로

`verify_ocr` 은 LLM 이 없으면 조용히 건너뜁니다.

```json
{"llm_url": "http://내부주소:포트/v1/chat/completions",
 "llm_model": "모델명",
 "verify_ocr": true}
```

기동 시 한 번 `configure()` 해 두는 편이 낫습니다 — 요청마다 보내면
느리고 키가 로그에 남을 수 있습니다.
