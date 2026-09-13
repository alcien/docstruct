"""experiments.text — 텍스트(스캔) 인식 실험 (측정 전용).

축: 인식 ② 텍스트.
역할:
    스캔 쪽을 읽는 두 주체(VLM · rapidocr)와 두 배율이 얼마나 다른 답을
    내는지, 쪽마다 되풀이되는 껍데기(머리말·꼬리말)가 본문의 몇 %인지
    잰다. 본문을 바꾸지 않는다.
호출부:
    docstruct.pipeline 구간 5 (formats 에 따라)

모듈 (입력 → 출력 · 역할):
    scan_ab.py            스캔 쪽 이미지 → VLM 판독 vs OCR 판독 대조 (page.scan_ab · ImageInfo.scan_ab)
                          숫자 Jaccard 로 불일치를 센다.
    scan_scale_ab.py      스캔 쪽 이미지 (두 배율) → 판독 차이 (page.scan_scale_ab)
                          DOCSTRUCT_EXP_SCAN_SCALE_PAGES 로 쪽을 고른다.
    page_chrome.py        쪽 목록 content → 되풀이 줄 · 껍데기 비중 (page.page_chrome)
"""
