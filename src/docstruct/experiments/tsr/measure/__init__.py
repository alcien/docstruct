"""experiments.tsr.measure — 표 격자 근거 · 측정 전용 (바꾸지 않는다).

축: 인식 ① 표.
역할:
    같은 표를 여러 근거로 다시 그려 보고 서로 얼마나 맞는지 잰다. 결과는
    TableInfo 의 필드(vector_grid · line_grid · scan_grid · grid_score …)로
    남고, restore/ 가 그것을 읽는다. 여기 실험은 markdown 을 바꾸지 않는다.
호출부:
    docstruct.pipeline 구간 5 · experiments.tsr.restore.* (근거 소비)

모듈 (입력 → 출력 · 역할):
    vector_grid.py        PDF 도형(사각형·선) → 격자 + confidence
                          한글 내보내기의 셀 배경 사각형에서 격자를 복원해
                          병합 인식과 견준다. 기본 켬.
    line_grid.py          괘선·모서리 → 합성 격자 (가설 H2+H3)
                          선이 끊긴 표를 모서리로 잇는다. 기본 켬.
    scan_grid.py          스캔 지면 선 검출 → 격자 (가설 H8)
                          벡터가 없는 스캔 PDF 에서 line_grid 의 자리를 맡는다.
    grid_score.py         TableFormer·사각형·lattice 격자 → 서로 일치도 (GriTS 간이)
                          정답 없이 근거별 유불리를 양상으로 남긴다. 기본 켬.
    otsl_diff.py          표 구조 → OTSL 토큰 → 두 구조의 차이
    two_way_match.py      표 셀 bbox ↔ OCR 조각 → 양방향 매칭 불일치
    over_split.py         인식 열 수 vs 괘선 열 수 → 더 쪼갠 표 지목 (over_split 필드)
    sum_check.py          예산표 숫자 → 행·열 합 검산 결과 (sum_check 필드)
                          검산 실패는 표시만 — 값을 고치지 않는다. 기본 켬.
                          tables/grade 가 후보 채점에도 쓴다.

읽는 순서: vector_grid → line_grid → scan_grid → grid_score. 나머지는 독립.
"""
