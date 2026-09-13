"""`python -m docstruct` 진입점.

역할:
    설치본의 `docstruct` 명령과 같은 일을 한다.
호출부:
    사용자 (`python -m docstruct 문서.pdf -o out`)

왜 필요한가
--------
배치마다 부르는 법이 달랐다.

    설치본     docstruct 문서.pdf -o out
    로컬 트리  python -m docstruct.cli 문서.pdf -o out

`pip install` 을 하지 않은 트리(사내 배포·로컬 검토)에서는 `docstruct`
명령이 없어 `-m` 으로 부르는데, 그때 `.cli` 까지 적어야 했다. 이 파일이
있으면 **어디서나 `python -m docstruct`** 로 같다.

    python -m docstruct 문서.pdf -o out --set verify_ocr=true

로컬 트리에서는 `converters`·`core`·`experiments` 가 트리 루트에 있으므로
**그 폴더에서 실행**해야 한다 (또는 그 경로를 `PYTHONPATH` 에 넣는다).
"""
from docstruct.cli import main

raise SystemExit(main())
