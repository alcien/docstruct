"""experiments.tsr.restore — 격자로 표 고치기 (바꾼다).

축: 인식 ① 표.
역할:
    measure/ 가 남긴 근거로 표를 다시 세운다. 공통 규칙:
        · original_markdown 에 이전 표를 남긴다 — 되짚을 수 있어야 한다
        · 고친 뒤가 나쁘면 되돌린다 — 글자 5%↑ 손실(⑮), 격자 결함 증가(lattice_fill)
        · 왜 물러났는지 남긴다 — fill_gate · col_gate
    파이프라인 구간 8 의 상시 검사(grid_check · repair_leaks)가 이 폴더의
    결과를 다시 잰다.
호출부:
    docstruct.pipeline 구간 5

모듈 (입력 → 출력 · 역할) — 등록 순서:
    grid_restore.py       배경 사각형 격자(덮개≥1.0) + 지면 텍스트 → 표 재작성 (⑦)
                          T1(배경 사각형이 표 전체를 덮은 표). 기본 켬.
    lattice_restore.py    괘선 격자 → 열 밀림 교정 (⑮)
                          인식이 중간 열을 잃어 값이 밀린 표. 글자 5%↑ 줄면 되돌림. 기본 켬.
    lattice_fill.py       격자 결함(grid_check)이 있는 표 + 괘선 → 다시 세움
                          결함이 실제로 줄 때만 채택. 사유는 fill_gate. 기본 켬.
    hole_fill.py          격자 구멍 → 빈 칸으로 메움 (글은 만들지 않음)
                          괘선이 없어도 된다. 겹친 표·절반 넘게 빈 표는 제외. 기본 켬.
    col_grid.py           격자 열 수 → 마지막 열 colspan 조정 (⑬ · 강등 · 기본 꺼짐)
    head_grid.py          격자 → 머리행 계층 복원 (⑫). 기본 켬.
    agreed_grid.py        두 근거(vector·line)가 합의한 병합만 반영 (⑭)
    cell_repair.py        한 칸에 뭉친 값 + 지면 좌표 → 칸 되돌리기 (⑦ 계열)

읽는 순서: grid_restore → lattice_restore → lattice_fill → hole_fill (이 넷이 승격된 사다리).
"""
