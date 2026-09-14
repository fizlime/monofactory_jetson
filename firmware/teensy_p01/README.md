# P01 Teensy 4.1 USB bridge

사용자가 제공한 독립 UART Servo42C 스케치를 기반으로 한다. 원본의 UART 배선, 38400 baud, 명령 패킷, 체크섬 및 E3 전용 응답 패딩 규칙을 유지한다. 부팅 시 자동 ENABLE/왕복은 제거하고, 기존 Jetson Sequence가 USB로 개별 명령을 전달한다.

| 축 | UART | Teensy RX | Teensy TX | 현재 사용 |
|---|---|---|---|---|
| E0 | Serial3 | 15 | 14 | 사용 |
| E1 | Serial4 | 16 | 17 | 사용 |
| E2 | Serial2 | 7 | 8 | 사용 |
| E3 | Serial5 | 21 | 20 | 사용, 응답 확인 대기 |

각 모터와 Teensy UART의 TX/RX 및 공통 GND 연결은 원본 스케치 기준이다. Uno의 자재 감지 D2–D5 및 경광등 D8–D10 기능은 기존 Uno에서 유지한다.

## Jetson 설정

`settings/poc_config.json`의 `p01.transport`를 `teensy_usb`, `p01.teensy_port`를 `/dev/ttyACM0`으로 지정한다. E0–E3 모두 `installed: true`다. E3는 추가 연결되어 사용 설정을 켰으며 실제 응답은 아직 확인 대기 상태다. 기존 `uart` 경로도 호환용으로 유지한다. 통신 방식 변경은 서버 재시작 시 적용된다.

기존 mm 이동량, 속도, 복귀 대기, P00 4/4 자재 감지 조건, P02 이후 공정과 Dobot 레시피는 그대로 사용한다. 수동 이동은 센서 조건 없이 가능하다. P01은 활성 축의 ENABLE 확인 후 축별 왕복을 독립 UART로 실행하고, 모두 COMPLETE가 되어야 다음 공정으로 진행한다. EE-SX 원점 센서가 아직 연결되지 않았으므로 P01 HOME의 기존 미연결 처리를 유지한다.

## USB protocol v1

USB Serial 115200 baud, ASCII newline framing. 부팅 시 모터 명령을 보내지 않는다.

- `HELLO <nonce>` → `HELLO <nonce> 1`
- `PING` → `PONG` (호스트가 400 ms 간격으로 전송)
- `CMD <request_id> <axis 0..3> <hex_body> <timeout_ms>`
- 응답 `R <request_id> <axis> STATUS <0|1|2>`, `PROBE <0|1>`, 또는 `ERROR <reason>`

허용 body는 F3 enable/disable, FD 방향·속도·32비트 펄스 이동, F7 stop, 3A EN 상태 읽기다. 호스트는 이동을 자동 재전송하지 않는다. START 없이 COMPLETE가 먼저 와도 해당 요청의 완료로 처리한다. STOP은 진행 중 요청을 취소하고 즉시 모터에 전송하며, 일반 STOP 확인은 실제 응답을 기다린다. USB heartbeat가 2초 끊기거나 요청 시간이 초과되면 진행 중 축에 STOP을 전송한다. UART가 물리적으로 끊기면 STOP 전달도 보장할 수 없다.

## Build / verification

공식 [PJRC Arduino 지원](https://www.pjrc.com/teensy/td_download.html)의 `teensy:avr:teensy41:usb=serial`로 빌드한다. 업로드는 [공식 Teensy Loader CLI](https://www.pjrc.com/teensy/loader_cli.html)의 `--mcu=TEENSY41`을 사용한다.

Python: `python -m unittest discover -s tests -p 'test_*.py'`

펌웨어 모의 테스트 (실제 USB/모터에 접근하지 않음):

```sh
g++ -std=c++17 -Wall -Itests/teensy tests/teensy/test_bridge.cpp -o /tmp/test_bridge
/tmp/test_bridge
```

실제 연결 확인은 MANUAL P01의 **연결 · EN 상태 확인**을 사용한다. 이는 3A 상태 읽기이며 이동이나 ENABLE을 보내지 않는다. MAIN START는 기존 전체 사이클을 실행한다.
