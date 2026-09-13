"""experiments.tsr — 표 구조 인식(TSR) 실험.

축: 인식 ① 표.
역할:
    TableFormer 가 낸 표를 **지면의 물리 근거**(벡터 도형·괘선·스캔 선·
    OCR 좌표)와 견주고, 근거가 설 때만 고친다. 두 하위 폴더는 원칙이 다르다.

    measure/    근거를 만들고 잰다. TableInfo 에 계측 필드를 남길 뿐
                markdown·cells 를 바꾸지 않는다.
    restore/    근거로 표를 다시 세운다. 반드시 original_markdown 을 남기고,
                고친 뒤가 더 나쁘면(글자 손실·격자 결함 증가) 되돌린다.

    실행 순서는 registry 의 등록 순서(복원 사다리)다 — measure 가 먼저
    근거를 남기고 restore 가 그것을 읽는다. 유형(T1~T5)을 따로 판정하는
    오케스트레이터는 없다. 각 실험이 "내 근거가 서는가" 를 스스로 보고
    아니면 물러난다 — **물러남의 연쇄가 곧 라우팅이다.**
호출부:
    docstruct.pipeline 구간 5 (`_run_experiments("tables")`)
"""
