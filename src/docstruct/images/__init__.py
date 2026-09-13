"""images — 그림 구조 인식 [인식 축 ③].

축: 인식 — 형식을 모른다. 입력은 ImageInfo(bbox·image_path) 와 쪽 이미지.
역할:
    파서가 "그림" 이라 한 것의 정체를 가려(표·글·그래프·장식) 그에 맞게
    읽는다. 읽기 전에 읽을 만한지(legibility) 재고, 필요하면 다듬고
    (image_prep·super_resolution), 읽은 결과를 본문과 대조한다(chart_verify).
    쪽 렌더(page_render)도 여기 — 표 판정·VLM 판독의 시각 근거를 만든다.
호출부:
    docstruct.pipeline 구간 2(렌더) · 4(그래프 읽기) · 7(그림 판독) ·
    docstruct.tables.assess / fill (encode) · docstruct.extractors.pdf (picture)

모듈 (입력 → 출력 · 역할):
  자리 잡기
    picture.py            Docling PictureItem → (본문 주석, ImageInfo)
                          그림을 파일로 떨어뜨리고 본문에 자리를 남긴다.
    native_image.py       PDF 쪽 + bbox → 원본 화소 그대로의 그림 파일
                          렌더 배율에 묶이지 않게 원본에서 잘라 낸다.
    picture_tables.py     표 목록 + 그림 목록 → 양쪽에 관계 표시
                          그림 **안**에 있는 표는 래스터 OCR 결과다 — 그
                          사실을 남겨 VLM 경로가 받게 한다.
    page_render.py        PDF 경로 + 쪽 번호 → {쪽: PNG 경로}
                          목적별 배율(표 판정·스캔 판독)로 나눠 그린다.
    encode.py             이미지 파일 → (MIME, base64)
                          LLM 에 첨부할 data URI 재료.
  읽을 만한가
    legibility.py         그림 파일 → {glyph_px, verdict, sharpness…}
                          dpi 가 아니라 글자 획 높이로 판독 가능성을 잰다.
    image_prep.py         그림 파일 → (보낼 파일, 적용 기록)
                          basic·full·lanczos·model 단계로 다듬는다.
    super_resolution.py   그림 파일 → (결과 파일, 기록) — GPU 있을 때만
                          없는 획을 지어낼 위험이 있어 기본 꺼짐.
  읽기·대조
    vlm_read.py           그림 + 지시문 → content·ImageInfo.vlm_markdown 갱신
                          텍스트 레이어가 없는 그림을 VLM 으로 읽는다.
                          짧게 읽혀도 쓴다(표지에서는 짧은 것이 정답).
    chart_read.py         그래프 그림 → 읽어낸 그래프 수 (chart 필드)
                          VLM 으로 값을 읽고 본문 숫자와 대조.
    chart_verify.py       막대그래프 화소 + 표 값 → 교차 검산 결과
                          화소 길이를 재서 옮겨적기 오류를 잡는다.

읽는 순서: picture → picture_tables → legibility → image_prep → vlm_read / chart_read → chart_verify.
"""
