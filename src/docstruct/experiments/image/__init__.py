"""experiments.image — 그림 인식 실험 (측정 전용).

축: 인식 ③ 그림.
역할:
    images/ 층이 그림마다 고른 판독 경로(표·글·그래프)가 **어떻게 갈렸는지**
    잰다. 경로를 바꾸지 않는다 — 문턱 ±3 안에 든 경계 사례를 세어 문턱을
    정할 근거를 모은다(지면형 그림 세 개뿐이라 아직 아무것도 정하지 못했다).
호출부:
    docstruct.pipeline 구간 8 뒤 (`_run_experiments("images")`)

모듈 (입력 → 출력 · 역할):
    chart_gate.py         ImageInfo(region_kind · legibility · text_density) → chart_gate 필드
                          경로 분포 · 경계 비율 · 두 축의 문턱까지 거리.
                          경계에 든 그림은 반대 경로로도 읽어 alt 로 남긴다.
"""
