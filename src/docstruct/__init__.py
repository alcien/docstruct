"""문서 구조화 라이브러리.

역할:
    HWP/HWPX/PDF 를 페이지 단위로 구조화해 JSON 으로 내보낸다.
    공개 API 는 DocStruct 클래스이며, get()/set() 으로 설정하고
    run() 으로 실행한 뒤 to_json() 으로 저장한다.
호출부:
    외부 사용자 코드, notebooks/*, docstruct.cli
출력:
    DocStruct (파사드), structure / structure_to_json (한 줄 사용),
    모델 클래스, build_document (하위 수준 함수)

사용 예::

    from docstruct import DocStruct

    ds = DocStruct("보고서.pdf")
    ds.set(device="cuda", llm_concurrency=8, assess_tables=True)
    ds.run()
    ds.to_json("결과.json")

    print(ds.get("device"))        # 'cuda'
    print(len(ds.tables))          # 표 개수
    ds.save("out/")                # 모든 산출물

    # 키를 소스에 두지 않고 실행 시점에 지정
    import docstruct, getpass
    docstruct.set_api_key(getpass.getpass("OpenAI 키: "))

    # 처리 경로 확인
    doc = ds.document
    print(doc.pages[0].trace.summary())     # 한 줄 요약
    print(doc.pages[0].trace.log())         # 순차 실행 로그
    docstruct.preview.show_pipeline(doc)    # 노트북에서 표로
    docstruct.preview.show_page(doc.pages[0])

    # 여러 문서를 한 번에 (진행 막대 포함)
    from docstruct import DocStructBatch

    batch = DocStructBatch("문서모음/", pattern="*.pdf", progress=True)
    batch.run()
    batch.to_json("결과/")
    print(batch.summary())

    # 한 줄로
    from docstruct import structure_to_json
    structure_to_json("보고서.pdf", "결과.json", assess_tables=False)
"""
from docstruct.api import (
    DocStruct,
    DocStructBatch,
    DocStructError,
    configure,
    defaults,
    mask,
    option_keys,
    set_api_key,
    structure,
    structure_to_json,
)
from docstruct.models import (
    ImageInfo,
    PageContent,
    PageDocument,
    PageTrace,
    TableInfo,
    TraceStep,
)
from docstruct.pipeline import SUPPORTED_SUFFIXES, build_document

# 노트북에서 `docstruct.preview.show_page(...)` 처럼 바로 쓸 수 있게
# 서브모듈을 미리 붙여 둡니다 (import docstruct 만으로 접근 가능).
from docstruct import preview, report  # noqa: E402,F401

__all__ = [
    # 공개 API
    "DocStruct",
    "DocStructBatch",
    "DocStructError",
    "structure",
    "structure_to_json",
    "option_keys",
    "defaults",
    "configure",
    "set_api_key",
    "mask",
    # 모델
    "PageDocument",
    "PageContent",
    "TableInfo",
    "ImageInfo",
    "PageTrace",
    "TraceStep",
    # 하위 수준
    "build_document",
    "SUPPORTED_SUFFIXES",
    # 서브모듈
    "preview",
    "report",
]

# SUPPORTED_SUFFIXES 상수와 추출기 레지스트리가 어긋나면 즉시 드러나게 합니다.
from docstruct.extractors.registry import supported_suffixes as _reg_suffixes

assert tuple(sorted(SUPPORTED_SUFFIXES)) == _reg_suffixes(), (
    "SUPPORTED_SUFFIXES 와 extractors/registry 등록이 불일치합니다: "
    f"{sorted(SUPPORTED_SUFFIXES)} != {list(_reg_suffixes())}"
)
