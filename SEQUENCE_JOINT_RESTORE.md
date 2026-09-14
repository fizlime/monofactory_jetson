# Sequence 구조 및 Dobot Joint 복원

기준: 사용자 첨부 `sequence_service.zip`의 `app/cell/sequence.py`와
`mainSequence.py`. 첨부 서비스의 DB, 프린터, 세척·경화 공정은 가져오지 않고
monofactory의 장치 구성에 단계 실행 구조를 적용했다.

## 실행 구조

- `mono_press/core/mainSequence.py`: P00 → P01 → P02 → P03/P04/P05 → P06 구성.
- `core/sequence.py`: Sequence의 now_step/before_step, 단계 타이머,
  sequence_run/sequence_run_void, 초기화·정지·일시정지 훅.
- 단계 0에서 설정을 읽고 초기화한다. 10, 20, 30… 단계별로 공정 동작을 실행한다.
  센서/모터 완료 대기는 같은 단계에서 반복 확인한다.
- 완료된 단계 뒤에만 다음 단계로 진행한다. STOP 시 남은 동작을 폐기하고
  다음 실행은 단계 0부터 시작한다. 일시정지는 현재 장치 동작 후 경계에서 적용한다.
- 상태 API의 공정 항목에 now_step, before_step, step_complete를 게시한다.
- 장치의 이동 완료·타임아웃·STOP 확인은 기존 드라이버가 담당한다.
  실제 이동 호출 동안 해당 tick은 완료를 기다린다.
- P03/P04/P05는 자재 투입 사이에 프레스가 동작하는 한 개의 순서를 유지한다.
  P03과 P05는 동일한 Dobot USB 연결과 잠금을 공유한다.

## 복원한 27개 동작

1. 자재 1 위치 → 집기 → PRESS_LOAD → 놓기 → 프레스 하강/상승.
2. 자재 2 위치 → 집기 → PRESS_LOAD → 놓기 → 프레스 하강/상승.
3. 자재 3 위치 → 집기 → PRESS_LOAD → 놓기 → 프레스 하강/상승.
4. 자재 4 위치 → 집기 → PRESS_LOAD → 놓기.
5. P05 PRESS_UNLOAD → 집기 → OUTPUT → 놓기 → 저장 HOME 위치.

임시 Dobot HOME/+1° 시험 및 호밍만 하는 순서를 제거했다.
프레스의 기존 자동 HOME 준비는 유지한다. 마지막 저장 HOME 이동은
Dobot의 원점복귀 명령과 구분된다.

## Joint 위치 저장과 실행

- 수동 JOG, 직접 이동, 저장 위치, 자동 순서 모두 JOINT J1~J4 각도(°) 사용.
- 현재 위치 저장 시 장치 pose 응답의 J1~J4를 읽는다. XYZ는 화면 참고값이다.
- 이동 완료도 실제 J1~J4 피드백과 목표 각도를 비교한다.
- 기존 XYZ 저장값을 각도로 재해석하지 않는다. 해당 위치에서 Joint로 다시 저장한다.
- P03/P05 저장 위치 이름은 각각 유지하며 동일한 실제 로봇을 제어한다.
- 실장비에서 저장한 Joint 위치가 없거나 USB 설정이 달라지면 자동 투입 시작 전
  차단한다. P03 단독 실행도 프레스 준비나 로봇 이동 전에 전체 위치를 확인한다.

현재 설정의 아래 8개 위치는 실장비 Joint 저장 기록이 없어 다시 저장해야 한다.
로봇을 각 지점에 놓은 뒤 MANUAL 또는 SETTING의 현재 Joint 위치 저장을 사용한다.

- P03: BUFFER_PICK_1, BUFFER_PICK_2, BUFFER_PICK_3, BUFFER_PICK_4, PRESS_LOAD
- P05: PRESS_UNLOAD, OUTPUT, HOME

기존 설정값은 보존했으며 위 위치를 임의의 관절각으로 덮어쓰지 않았다.

## 확인

Python 회귀, 웹 편집 및 Chromium 클릭 회귀를 가상 장치/API로 검증했다.
Jetson USB에서 실제 조회한 Joint 값: [0.00, 44.60, 81.30, 0.00]°.
실제 이동이나 자동 사이클은 실행하지 않았다. 배포 시 서버는 정지 상태를 유지한다.
