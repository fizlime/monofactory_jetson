# MONO POC Press Line

P01~P06 공정을 웹 UI에서 자동운전, 수동시험, 레시피 설정하는 Jetson용 제어 프로젝트입니다. 화면은 Apple 계열의 밝은 Parchment 캔버스, SF 시스템 글꼴, 파란색 액션 버튼과 평면 카드 체계로 구성했습니다.

## 구조

```text
mono_press_web/
├─ control_server.py              # 실행 진입점
├─ requirements.txt
├─ settings/
│  └─ poc_config.json             # 전체 라인 설정과 레시피
├─ web/
│  ├─ index.html                  # MAIN / MANUAL / SETTING / LOG
│  ├─ styles.css
│  ├─ app.js
│  └─ assets/mono-logo.png
└─ mono_press/
   ├─ application.py              # 전체 객체 조립
   ├─ http_server.py              # 웹/API/SSE
   ├─ core/                        # 상태, 설정, 시퀀스 기반 클래스, 런타임
   ├─ sequences/                   # P01~P06 프로세스 클래스
   └─ units/                       # 모터·센서·카메라·SCARA·프레스·MES 유닛
```

각 `sequences/pXX_*.py`가 한 공정을 담당하고 그 안에서 `units/*.py`의 장치 유닛을 호출합니다. P01은 Servo42C UART를 사용하고 P04는 candleLight USB CAN을 통해 MKS SERVO57D를 제어합니다. Vision은 Basler 실제 영상 수신을 지원하며 검사 알고리즘은 미구현입니다. SCARA, MES, P06은 동일 인터페이스의 시뮬레이션 어댑터로 준비되어 있습니다.

## Jetson 실제 실행

```bash
cd /home/tracelab/monofactory
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x setup_can_usb.sh
./setup_can_usb.sh
python3 control_server.py --port /dev/ttyTHS1 --baud 38400 --host 0.0.0.0 --http-port 8080
```

같은 네트워크의 PC 또는 태블릿에서 `http://JETSON_IP:8080`을 엽니다.

UART 권한이 없을 때:

```bash
sudo usermod -aG dialout $USER
sudo reboot
```

## 하드웨어 없이 실행

Jetson/Ubuntu:

```bash
python3 control_server.py --simulation --http-port 8080
```

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python control_server.py --simulation --host 127.0.0.1 --http-port 8080
```

브라우저에서 `http://127.0.0.1:8080`을 엽니다. Windows에서는 UI, API, P01~P06 시퀀스 시뮬레이션이 동작합니다. `/dev/ttyTHS1` 실제 장비 제어는 Jetson에서 실행합니다.

Windows에 동일한 UART 장치를 COM 포트로 연결했다면 `--port COM3`처럼 실행 포트를 지정할 수 있습니다. 우측 상단 SIM/REAL 전환은 운전 정지 상태에서만 가능하며, REAL 전환 시 지정된 포트를 다시 연결합니다.

## 공정 정의

- P01: E0~E3 펄스 모터와 홈 센서 4개로 자재 4개 전진·복귀. 자동 왕복은 SETTING에서, 수동 전진·후진 이동량은 MANUAL에서 축별로 입력하며, HOME 명령만 센서를 찾을 때까지 홈 방향으로 이동
- P02: Basler 실시간 영상, SIM 모드의 OK/NG 검사 결과 표시. REAL 검사는 알고리즘 구현 전까지 NOT_READY로 중단
- P03·P04·P05: SCARA 투입, 그리퍼, 프레스 이동/하강/상승, 완성품 이송, 대기를 하나의 순서로 실행하는 통합 레시피
- P06: 배출 · 경광등. P05 통합 순서에서 통에 그리퍼 OPEN 후 MES 실적 처리와 완료 점등을 실행합니다. 별도 배출 장비는 없습니다.

LOG 메뉴는 서버가 보관하는 최근 이벤트를 실시간으로 표시하며, 메시지 검색과 공정·유형 필터 및 화면 업데이트 일시정지를 제공합니다.

MAIN 하단은 최근 이벤트·알람·Basler acA2500-14gm 비전 패널을 왼쪽부터 가로로 배치하고 각각 같은 너비(1/3)를 사용합니다. 영상 영역은 2592×1944 사양에 맞춘 4:3 비율이며, 좁은 화면에서는 세 영역을 위아래로 배치합니다. 기존 비전 팝업과 닫기 버튼은 제거했습니다. REAL 모드에서 카메라를 자동 검색하고 연결되면 영상을 표시합니다. 연결이 끊기면 이전 영상을 숨기고 재연결하며, 검사 상태·사유·시각은 패널 아래에 표시합니다.

MAIN의 START는 STOP 또는 알람이 발생할 때까지 P01~P06을 계속 반복합니다. 자동운전 중에는 MANUAL 화면이 잠기며 서버도 수동 명령을 거부합니다. 우측 상단 SIM/REAL 스위치는 운전 정지 상태에서만 변경할 수 있습니다.

## Basler 카메라 자동 연결

`requirements.txt`의 pypylon, Pillow, NumPy를 설치한 Python 환경으로 서버를 실행해야 합니다. 이 작업에서는 프로젝트 `.venv`에 설치했습니다. 이미 실행 중인 서버에는 Python 변경 사항이 자동 반영되지 않으므로, 장비를 안전하게 정지하고 기존 서버를 종료한 후 다음과 같이 다시 실행합니다.

```bash
cd /home/tracelab/monofactory
.venv/bin/python control_server.py --port /dev/ttyTHS1 --baud 38400 --host 0.0.0.0 --http-port 8080
```

- 카메라 전원을 공급하고 LAN으로 연결합니다. 카메라와 Jetson의 IP 대역은 통신 가능하도록 미리 설정해야 합니다. 서버는 IP 주소, 방화벽, 네트워크 설정을 자동 변경하지 않습니다.
- REAL 모드에서는 Basler GigE/USB 카메라를 검색하며 실패 시 약 2초 간격으로 다시 시도합니다. SIM 모드에서는 실제 카메라를 열지 않습니다. 다른 제조사 카메라는 이 연동 범위에 포함하지 않습니다.
- SETTING → P02의 카메라 시리얼 번호가 비어 있으면 Basler 카메라 한 대를 자동 선택합니다. 여러 대이면 시리얼 번호를 지정해야 합니다. 카메라 교체 시에는 기존 시리얼을 새 번호로 변경하거나 비워야 합니다. 다른 프로그램(pylon Viewer 등)이 카메라를 점유하고 있다면 먼저 해제합니다.
- 미리보기 FPS는 1~14, 기본 8입니다. 프레임은 별도 스레드에서 받고 최대 960×720의 JPEG로 비율을 유지해 전달합니다. 프레임이 2초 이상 갱신되지 않으면 제공하지 않습니다. MAIN과 MANUAL에서 동일한 카메라 프레임을 공유하며, 활성 화면만 영상을 요청합니다. SETTING/LOG나 백그라운드 탭에서는 브라우저 영상 요청을 멈춥니다.
- MANUAL은 P02 Vision 선택 시에만 제어 영역 오른쪽에 비전 패널을 표시하며 좁은 화면에서는 아래로 배치합니다. 다른 공정에서는 패널을 숨기고 제어 영역을 전체 폭으로 복원하며 브라우저 영상 요청을 멈춥니다. MAIN의 비전 패널은 그대로 유지합니다. 공정 선택이나 상태 업데이트로 영상 요소를 다시 만들지 않습니다. 기존 자동운전 중 수동 제어 잠금은 유지합니다.
- 연결 시 연속 촬영, 트리거 OFF, 지원 시 패킷 크기 1500을 적용합니다. 지원 PixelFormat에 Bayer/RGB 등의 컬러 형식이 있으면 컬러를 선택하고 SDK로 RGB JPEG로 변환합니다(Bayer 8-bit 우선). 흑백 카메라는 Mono8을 우선 선택해 흑백 JPEG로 변환합니다. MAIN/MANUAL의 모델명과 COLOR/MONO 표시는 실제 연결 정보로 갱신됩니다. 현재 acA2500-14gm은 흑백 카메라입니다. 카메라의 영구 사용자 설정에는 저장하지 않습니다. 향후 하드웨어 트리거 검사를 구현할 때는 촬영 방식을 별도로 조정해야 합니다.
- 흑백 카메라도 YUV 출력 형식을 제공할 수 있으므로 YUV 지원만으로 컬러 센서라고 판단하지 않습니다. acA2500-14gm은 Mono8로 유지합니다. 지원되는 경우 카메라 실제 촬영 FPS도 미리보기 FPS에 맞추고, GigE 패킷 간격은 최소 20μs(이 카메라에서는 2500 ticks)로 설정합니다. 이미 설정된 더 큰 간격은 유지합니다. OS 수신 버퍼·MTU는 자동 변경하지 않습니다.
- 일시적으로 불완전한 프레임은 버리되 바로 연결을 끊지 않습니다. 5회 연속 실패 또는 SDK 수신 제한 시간 초과 시 재연결합니다. 갱신되지 않은 영상은 기존과 같이 2초 후 제공하지 않습니다. FPS 변경 시에는 카메라를 재연결해 촬영 속도에도 반영합니다.
- **현재 연동 범위는 실제 영상 표시입니다. REAL 모드의 P02 검사는 판정 알고리즘 미설정으로 NOT_READY 오류를 내고 공정을 중단합니다. 영상 연결만으로 OK를 생성하거나 다음 공정으로 넘기지 않습니다.** SIM 모드의 기존 OK/NG 시나리오는 유지됩니다.

SDK 에뮬레이터의 영상 수신·브라우저 표시와 자동 재연결 단위 테스트를 확인했습니다. 2026-09-11에는 실카메라 시리얼 `24260480`의 2592×1944 영상 수신과 MAIN 표시도 확인했습니다. 초기 프레임 손실 후 자동 재연결이 확인됐으며, 장시간 안정성과 검사에 필요한 초점·노출 조정은 추가 검증이 필요합니다.

현재 Jetson에는 `monofactory-camera` 유선 연결 프로필을 추가했습니다(UUID `3f27d5e3-0fa9-4812-85f5-b965743c1478`). `eno1`에서 IPv4 링크 로컬 주소를 자동 할당하며, 자동 연결 우선순위는 50, 경로 metric은 100, 기본 경로 사용은 꺼져 있습니다. 기존 Wi-Fi 인터넷과 기존 유선 프로필은 유지했습니다. 연결 당시 Jetson은 `169.254.246.20/16`, 카메라는 `169.254.129.2/16`이며 자동 할당 주소는 바뀔 수 있으므로 앱은 IP가 아닌 저장된 시리얼로 장치를 선택합니다. 카메라의 영구 IP 설정은 변경하지 않았습니다. 카메라 전용 LAN을 다른 용도로 바꾸려면 이 프로필의 자동 연결을 해제하고 해당 네트워크에 맞는 프로필을 사용해야 합니다.

## P04 프레스 CAN 제어

SETTING의 `P03 · P04 · P05` 탭에서 **P03 투입용 SCARA와 P05 배출용 SCARA 2대**를 따로 관리합니다. 왼쪽 위치 영역에 P03 현재 위치·속도·저장, P05 현재 위치·속도·저장, 프레스 현재위치를 각각 표시합니다. 아래 영역은 좌측 `통합 저장 위치`, 우측 `P03 · P04 · P05 통합 동작 순서`를 같은 폭으로 나누고 각 영역을 세로로 스크롤합니다. 저장 위치 목록도 P03/P05/PRESS로 구분합니다. 좁은 화면에서는 두 영역을 위아래로 배치합니다.

동작 순서는 기존 P05 방식의 목록 편집기로, 위에서 아래로 실행됩니다. 각 행에서 동작 종류와 저장 위치·그리퍼 동작·대기 시간을 선택하고 ↑·↓ 버튼으로 순서를 변경합니다. 동작별 배경색, 이동 후 초점 추적과 짧은 블러 피드백을 유지합니다. 프레스 하강·상승은 공통 step 설정을 사용합니다. 변경 사항은 `설정 저장` 후 적용되며 편집만으로 장비가 움직이지 않습니다.

레시피는 `press_recipe.version: 3`의 단일 `actions` 목록입니다. SCARA 이동·그리퍼에는 `robot: P03` 또는 `robot: P05`를 저장합니다. 선택란에서 `P03 SCARA 이동`, `P03 그리퍼`, `P05 SCARA 이동`, `P05 그리퍼`를 선택하며, 저장 위치는 해당 로봇의 목록만 표시합니다. 순서를 옮겨도 동작의 로봇 지정이 유지됩니다. 자동운전에서는 통합 순서를 한 번만 실행하며, P03/P04/P05 공정 시험도 같은 통합 순서를 실행합니다(프레스 유닛의 CYCLE 버튼은 기존 하강·상승 시험 유지). LINE의 P03/P04/P05 공정 대기시간은 합산하여 통합 순서 완료 후 적용합니다.

P03 설정은 `scara`, P05 설정은 `scara_p05`에 독립 저장합니다. 유닛 ID도 `P03_SCARA_J1`~`J4`/`P03_SCARA_GRIPPER`, `P05_SCARA_J1`~`J4`/`P05_SCARA_GRIPPER`로 분리합니다. MANUAL의 ENABLE/DISABLE/JOG/그리퍼/STOP은 선택한 로봇에만 적용합니다. 단, 통합 운전 도중 로봇 STOP 요청은 전체 순서를 취소하고 두 로봇과 프레스를 정지합니다. 위치 API는 `/api/scara/P03/...`, `/api/scara/P05/...`처럼 로봇을 명시해야 하며, 기존 공용 API는 실행하지 않습니다.

이전 분리 레시피(v1)는 원래 P03/P05 목록을 기준으로 변환합니다. 통합 레시피(v2)는 끝의 기존 배출 5개 동작(`PRESS_UNLOAD → CLOSE → OUTPUT → OPEN → HOME`)이 일치할 때만 자동 구분합니다. 구분할 수 없는 수정된 순서는 로봇 지정을 요구하는 오류로 중단하며 기본 순서로 덮어쓰지 않습니다. 기존 좌표와 속도는 초기값으로 두 로봇에 복사하되 이후에는 서로 영향을 주지 않습니다. **서로 다른 로봇의 좌표계이므로 실제 운전 전에 각각 위치를 재확인·재등록해야 합니다. 현재 SCARA 드라이버는 2대 모두 시뮬레이션이며 실제 통신은 미구현입니다.** 이 변경은 제어 서버 재시작과 브라우저 새로고침이 필요하며, 이전 버전 화면의 설정 저장은 거부합니다.

SETTING의 `P03 · P04 · P05`에는 CAN 속도, CAN ID, bitrate, 가감속 설정이 있습니다. MANUAL P04에서 ENABLE → HOME 후 내리기·올리기 이동량을 step으로 각각 입력합니다. 환산 기준은 600 step/mm이며 입력 옆에 mm를 표시합니다.

P04 수동 DOWN/UP API에는 양의 정수 `steps`가 필수입니다. P01 수동 FORWARD/REVERSE API에는 양의 정수 `pulses`가 필수입니다. 누락·0·음수·소수는 거부하며 이전 설정값으로 대체하지 않습니다. 수동 입력은 브라우저 화면 동안 유지하며 설정 파일이나 자동 레시피의 down_steps/up_steps 및 왕복 거리를 바꾸지 않습니다.

P04 수동 내리기·올리기와 저장 위치 이동은 ENABLE → HOME 센서 찾기를 완료해야 실행할 수 있습니다. CYCLE 및 프레스가 포함된 자동 레시피는 시작 시 호밍 완료 여부를 확인하고, 미완료이면 자동 ENABLE → HOME → 공정 순서로 실행합니다. 자동 레시피의 HOME은 로봇 동작 전에 완료하며, 이미 호밍된 상태에서는 반복하지 않습니다. 센서가 ON이라는 이유만으로 호밍 완료로 취급하지 않습니다. 호밍 실패·중단 또는 STOP 시에는 다음 공정 이동을 실행하지 않습니다. DISABLE, 연결 재설정, 모드 전환, 이동 오류 또는 정지 확인 실패 시에는 다시 HOME이 필요합니다. 정상 호밍 후에는 센서 OFF 상태에서도 일반 이동이 가능하며, 정상적으로 확인된 STOP은 호밍 완료 상태를 유지합니다.

P01 SETTING에는 축별 자동 왕복 거리와 HOME 탐색 설정만 있습니다. MANUAL 전진·후진 값은 각 축 카드에서 입력합니다. 일반 이동은 HOME 센서를 정지 조건으로 사용하지 않으며, HOME만 물리 센서를 탐색합니다.

REAL 모드에서는 서버가 USB VID/PID `1d50:606f`인 candleLight 장치 1개를 열고 SERVO57D의 SR 모드, CAN 응답, EN, 보호 상태를 확인합니다. 이동은 `0xF5` 절대 위치 명령을 짧은 목표로 계속 갱신하고, 정지는 `0xF7`, 상태 확인은 `0xF1`, 홈 센서는 IN_1 active LOW를 사용합니다. HOME은 PC가 센서를 읽으며 탐색하고 현재 엔코더 좌표를 UI의 0 step 기준으로 잡습니다. P04의 USB 처리는 전용 작업 스레드 하나에서 실행됩니다.

Windows에서 `--simulation`으로 실행하면 USB CAN 라이브러리를 열지 않으므로 하드웨어 없이 전체 화면과 시퀀스를 시험할 수 있습니다.

## 실제 장치 연결 위치

- SCARA 통신: `mono_press/units/scara_robot.py`
- 프레스 공통 인터페이스: `mono_press/units/press_unit.py`
- 프레스 USB CAN 어댑터: `mono_press/units/can_press/driver.py`
- SERVO57D CAN 프레임/운동 로직: `mono_press/units/can_press/can_link.py`, `control_logic.py`
- Vision SDK 영상 수신: `mono_press/units/basler_stream.py`
- Vision 검사 인터페이스: `mono_press/units/vision_camera.py`
- MES 전송: `mono_press/units/mes_client.py`
- P06 배출 I/O: `mono_press/units/discharge_unit.py`

SCARA, MES, P06의 실제 프로토콜이 확정되면 해당 유닛 클래스만 교체합니다. Vision은 실제 검사 알고리즘을 추가해야 합니다. 시퀀스, API, UI는 같은 구조로 유지됩니다.

기존 P07 완료 처리는 P06 배출·경광등으로 통합되었습니다. p07 설정은 p06으로 이전하고 P06/P07 완료 대기시간은 한 번 합산합니다. 경광등과 MES 유닛 ID는 P06_LIGHT_RED, P06_LIGHT_GREEN, P06_MES입니다. 로봇 배출 위치와 OPEN 명령은 기존 P03·P04·P05 통합 순서에 유지하며 P06에서 중복 실행하지 않습니다.
