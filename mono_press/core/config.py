from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from .press_calibration import PRESS_STEPS_PER_MM
from .robot_command import validate_dobot_move
from .distance_units import legacy_distance_input, mm_distance_output, p01_pulses

PROCESS_META = [
    {"code": "P00", "name": "자재 감지", "summary": "Arduino D2·D3·D4·D5 자재 4/4 감지 시 투입 허용", "unit_count": 4},
    {"code": "P01", "name": "자재 투입", "summary": "4축 리니어 모터가 자재 4개를 전진·복귀", "unit_count": 8},
    {"code": "P02", "name": "Vision 검사", "summary": "Basler 카메라로 원자재 OK/NG 판정", "unit_count": 1},
    {"code": "P03", "name": "SCARA 투입", "summary": "저장 위치 기반 SCARA·그리퍼 통합 레시피", "unit_count": 5},
    {"code": "P04", "name": "프레스", "summary": "USB CAN 위치명령과 홈 센서로 압입", "unit_count": 2},
    {"code": "P05", "name": "SCARA 배출 이송", "summary": "완성품을 잡아 다음 공정으로 이송", "unit_count": 5},
    {"code": "P06", "name": "배출 · 경광등", "summary": "실적 등록 · 초록등·빨강등·부저 제어", "unit_count": 4},
]


DEFAULT_CONFIG = {
    "line": {
        "process_delays": {code: value for code, value in zip(
            [item["code"] for item in PROCESS_META], [0, 0.4, 1.0, 0.5, 0.5, 0.5, 1.3]
        )},
    },
    "p00": {"driver": "unconfigured", "gpio_chip": "gpiochip0", "gpio_line": 105, "active_low": True, "debounce_ms": 100, "wait_timeout": 30,
            "serial_port": "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0", "serial_baud": 115200, "stale_timeout": 3},
    "p01": {
        "transport": "uart",
        "teensy_port": "/dev/ttyACM0",
        "pulse_per_rev": 3200,
        "speed_gear": 16,
        "start_gap": 0.05,
        "reverse_dwell": 0.2,
        "command_timeout": 1.0,
        "move_timeout": 6.0,
        "axes": {
            f"E{i}": {
                "address": 0xE0 + i,
                "installed": True,
                "pulses": 16000,
                "manual_forward_pulses": 16000,
                "manual_reverse_pulses": 16000,
                "home_search_pulses": 64000,
                "home_step_pulses": 400,
                "home_forward_mm": None,
                "home_sensor": f"P01_HOME_{i + 1}",
            }
            for i in range(4)
        },
    },
    "p02": {
        "inspection_mode": "AI",
        "camera_name": "Basler",
        "camera_serial": "",
        "preview_fps": 8,
        "inspection_time": 0.6,
        "score_threshold": 0.85,
    },
    "scara": {
        "driver": "simulation",
        "speed": 40,
        "positions": {
            "HOME": [0.0, 0.0, 0.0, 0.0],
            "BUFFER_PICK": [12.0, 22.0, -8.0, 0.0],
            "BUFFER_PICK_1": [12.0, 22.0, -8.0, 0.0],
            "BUFFER_PICK_2": [12.0, 17.0, -8.0, 0.0],
            "BUFFER_PICK_3": [12.0, 12.0, -8.0, 0.0],
            "BUFFER_PICK_4": [12.0, 7.0, -8.0, 0.0],
            "PRESS_LOAD": [35.0, 10.0, -15.0, 90.0],
            "PRESS_UNLOAD": [35.0, 10.0, -10.0, 90.0],
            "OUTPUT": [5.0, -28.0, -6.0, 180.0],
        },
        "p05_actions": [
            {"type": "SCARA_MOVE", "target": "PRESS_UNLOAD"},
            {"type": "GRIPPER", "target": "CLOSE"},
            {"type": "SCARA_MOVE", "target": "OUTPUT"},
            {"type": "GRIPPER", "target": "OPEN"},
            {"type": "SCARA_MOVE", "target": "HOME"},
        ],
    },
    "p04": {
        "driver": "can_usb",
        "down_steps": 64000,
        "up_steps": 64000,
        "speed": 100,
        "home_sensor": "P04_HOME_1",
        "saved_positions": {"PRESS_HOME": 0, "PRESS_BOTTOM": 64000},
        "can": {
            "can_id": 1,
            "bitrate": 500000,
            "steps_per_mm": PRESS_STEPS_PER_MM,
            "up_sign": 1,
            "home_input_bit": 0,
            "home_active_low": True,
            "slow_zone_mm": 10.0,
            "home_approach_mm": 5.0,
            "repeat_count": 3,
            "ramp_rpm_per_second": 75.0,
            "deceleration_advance_steps": 4000,
            "fast_delay_us": 200,
            "slow_delay_us": 800,
            "home_start_delay_us": 1200,
            "home_seek_delay_us": 400,
            "home_fine_delay_us": 1800,
            "home_start_mm": 3.0,
            "home_backoff_mm": 2.0,
            "max_home_search_mm": 350.0,
            "home_fine_search_mm": 5.0,
            "home_escape_mm": 10.0,
            "extra_home_search_mm": 5.0,
            "lookahead_mm": 1.0,
            "poll_seconds": 0.02,
            "can_timeout_seconds": 0.15,
            "acceleration": 1,
        },
    },
    "press_recipe": {
        "actions": [
            {"type": "SCARA_MOVE", "target": "BUFFER_PICK_1"},
            {"type": "GRIPPER", "target": "CLOSE"},
            {"type": "SCARA_MOVE", "target": "PRESS_LOAD"},
            {"type": "GRIPPER", "target": "OPEN"},
            {"type": "PRESS_DOWN", "target": "DOWN_STEPS"},
            {"type": "PRESS_UP", "target": "UP_STEPS"},
            {"type": "SCARA_MOVE", "target": "BUFFER_PICK_2"},
            {"type": "GRIPPER", "target": "CLOSE"},
            {"type": "SCARA_MOVE", "target": "PRESS_LOAD"},
            {"type": "GRIPPER", "target": "OPEN"},
            {"type": "PRESS_DOWN", "target": "DOWN_STEPS"},
            {"type": "PRESS_UP", "target": "UP_STEPS"},
            {"type": "SCARA_MOVE", "target": "BUFFER_PICK_3"},
            {"type": "GRIPPER", "target": "CLOSE"},
            {"type": "SCARA_MOVE", "target": "PRESS_LOAD"},
            {"type": "GRIPPER", "target": "OPEN"},
            {"type": "PRESS_DOWN", "target": "DOWN_STEPS"},
            {"type": "PRESS_UP", "target": "UP_STEPS"},
            {"type": "SCARA_MOVE", "target": "BUFFER_PICK_4"},
            {"type": "GRIPPER", "target": "CLOSE"},
            {"type": "SCARA_MOVE", "target": "PRESS_LOAD"},
            {"type": "GRIPPER", "target": "OPEN"},
        ]
    },
    "p06": {
        "mes_endpoint": "http://127.0.0.1:9000/api/result",
        "line_code": "POC-LINE-01",
        "green_hold_seconds": 2.0,
        "relay_active_level": "UNSET",
    },
}


def _number(value, minimum, maximum, label, integer=False):
    converted = int(value) if integer else float(value)
    if not minimum <= converted <= maximum:
        raise ValueError(f"{label}: {minimum}~{maximum} 범위입니다.")
    return converted


def validate_config(raw: dict) -> dict:
    raw = legacy_distance_input(raw)
    base = json.loads(json.dumps(DEFAULT_CONFIG))

    def merge(target, source):
        for key, value in source.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                merge(target[key], value)
            else:
                target[key] = value

    if isinstance(raw, dict):
        merge(base, raw)
        # Saved positions are a complete per-robot list, not default overrides.
        if "positions" in raw.get("scara", {}):
            base["scara"]["positions"] = json.loads(json.dumps(raw["scara"]["positions"]))

    if "saved_positions" in raw.get("p04", {}):
        base["p04"]["saved_positions"] = dict(raw["p04"]["saved_positions"])

    # Merge legacy discharge and completion settings once; recipe order stays intact.
    legacy = raw if isinstance(raw, dict) else {}
    if "p07" in legacy:
        base["p06"].update(legacy["p07"])
        base["p06"].update({k: v for k, v in legacy.get("p06", {}).items() if k != "reserved_note"})
    base.pop("p07", None)
    base["p06"].pop("reserved_note", None)
    delays = base["line"]["process_delays"]
    if "P07" in legacy.get("line", {}).get("process_delays", {}):
        delays["P06"] = float(delays.get("P06", 0)) + float(delays.pop("P07"))
    delays.pop("P07", None)
    line = base["line"]
    for code in [item["code"] for item in PROCESS_META]:
        line["process_delays"][code] = _number(line["process_delays"].get(code, 0.5), 0, 60 if code == "P06" else 30, f"{code} 대기시간")
    line.pop("manual_timeout", None)
    line.pop("auto_repeat", None)

    p00 = base["p00"]
    if p00["driver"] not in {"unconfigured", "gpio", "arduino_serial"}: raise ValueError("P00 입력은 unconfigured/gpio/arduino_serial입니다.")
    if not re.fullmatch(r"/dev/(?:serial/by-id/[A-Za-z0-9_.:-]+|tty(?:USB|ACM)[0-9]+)", str(p00['serial_port'])): raise ValueError('Arduino 직렬 포트 경로 오류')
    p00['serial_baud'] = _number(p00['serial_baud'], 9600, 115200, 'Arduino 통신 속도', True)
    if p00['serial_baud'] not in (9600, 19200, 38400, 57600, 115200): raise ValueError('Arduino 통신 속도 오류')
    p00['stale_timeout'] = _number(p00['stale_timeout'], 1.5, 10, 'Arduino 수신 제한')
    if not re.fullmatch(r"gpiochip[0-9]+", str(p00["gpio_chip"])): raise ValueError("GPIO 칩 이름 오류")
    p00["gpio_line"] = _number(p00["gpio_line"], 0, 511, "GPIO line", True)
    if not isinstance(p00["active_low"], bool): raise ValueError("P00 감지 극성은 bool입니다.")
    p00["debounce_ms"] = _number(p00["debounce_ms"], 50, 2000, "감지 안정 시간", True)
    p00["wait_timeout"] = _number(p00["wait_timeout"], 1, 300, "감지 대기 제한")
    p01 = base["p01"]
    if p01['transport'] not in ('uart','teensy_usb'):raise ValueError('P01 통신 방식 오류')
    if not re.fullmatch(r'/dev/(?:serial/by-id/[A-Za-z0-9_.:-]+|ttyACM[0-9]+)|COM[0-9]+',str(p01['teensy_port'])):raise ValueError('Teensy USB 포트 오류')
    p01["pulse_per_rev"] = _number(p01["pulse_per_rev"], 1, 100000, "회전당 펄스", True)
    p01["speed_gear"] = _number(p01["speed_gear"], 1, 127, "P01 속도", True)
    p01["start_gap"] = _number(p01["start_gap"], 0, 1, "축 시작 간격")
    p01["reverse_dwell"] = _number(p01["reverse_dwell"], 0, 10, "복귀 전 대기")
    p01["command_timeout"] = _number(p01["command_timeout"], 0.1, 10, "명령 제한시간")
    p01["move_timeout"] = _number(p01["move_timeout"], 0.5, 120, "이동 제한시간")
    for index in range(4):
        name = f"E{index}"
        axis = p01["axes"][name]
        raw_axis = raw.get("p01", {}).get("axes", {}).get(name, {}) if isinstance(raw, dict) else {}
        legacy_pulses = raw_axis.get("pulses", axis.get("pulses", 16000))
        axis["address"] = 0xE0 + index
        if not isinstance(axis.get("installed"), bool): raise ValueError("P01 사용 여부는 bool입니다.")
        axis["pulses"] = _number(axis["pulses"], 1, 0xFFFFFFFF, f"{name} 펄스", True)
        axis["manual_forward_pulses"] = _number(
            raw_axis.get("manual_forward_pulses", legacy_pulses), 1, 0xFFFFFFFF,
            f"{name} 수동 전진 펄스", True,
        )
        axis["manual_reverse_pulses"] = _number(
            raw_axis.get("manual_reverse_pulses", legacy_pulses), 1, 0xFFFFFFFF,
            f"{name} 수동 후진 펄스", True,
        )
        axis["home_search_pulses"] = _number(
            axis.get("home_search_pulses", 64000), 1, 0xFFFFFFFF,
            f"{name} HOME 최대 탐색 펄스", True,
        )
        axis["home_step_pulses"] = _number(
            axis.get("home_step_pulses", 400), 1, axis["home_search_pulses"],
            f"{name} HOME 확인 간격", True,
        )
        axis["home_sensor"] = f"P01_HOME_{index + 1}"
        forward = axis.get('home_forward_mm')
        if forward is not None:
            p01_pulses(forward, p01)  # Positive, finite, representable motor distance.
            axis['home_forward_mm'] = float(forward)

    if not any(a["installed"] for a in p01["axes"].values()): raise ValueError("P01 사용 축을 하나 이상 선택하세요.")

    p02 = base["p02"]
    if p02["inspection_mode"] not in ("AI", "CAMERA_CHECK"):
        raise ValueError("P02 검사 방식은 AI 또는 CAMERA_CHECK여야 합니다.")
    p02["camera_serial"] = str(p02.get("camera_serial", "")).strip()[:64]
    p02["preview_fps"] = _number(p02.get("preview_fps", 8), 1, 14, "카메라 미리보기 FPS", True)
    p02["inspection_time"] = _number(p02["inspection_time"], 0.1, 30, "Vision 검사시간")
    p02["score_threshold"] = _number(p02["score_threshold"], 0, 1, "Vision 판정 기준")

    scara = base["scara"]
    scara["speed"] = _number(scara["speed"], 1, 100, "SCARA 속도", True)
    scara.pop("jog_step", None)
    scara.pop("p03_repeat", None)
    scara.pop("p03_actions", None)
    positions = {}
    for name, values in scara.get("positions", {}).items():
        clean_name = str(name).strip().upper()[:32]
        if not clean_name or not isinstance(values, list) or len(values) != 4:
            raise ValueError("SCARA 위치는 이름과 4개 축 좌표가 필요합니다.")
        positions[clean_name] = [float(value) for value in values]
    if not positions:
        raise ValueError("SCARA 저장 위치가 한 개 이상 필요합니다.")
    scara["positions"] = positions
    recipe_version = base.get("press_recipe", {}).get("version", 1)
    if recipe_version not in {1, 2, 3}:
        raise ValueError("지원하지 않는 통합 레시피 버전입니다.")
    if recipe_version == 3 and "scara_p05" not in base:
        raise ValueError("P05 SCARA 설정이 필요합니다.")
    p05_actions = []
    # Versions 2+ already include unloading; do not append it again on save.
    legacy_p05 = scara.get("p05_actions", []) if recipe_version == 1 else []
    for item in legacy_p05:
        action_type = str(item.get("type", "")).upper()
        if action_type == "MOVE":
            action_type = "SCARA_MOVE"
        if action_type not in {"SCARA_MOVE", "GRIPPER", "WAIT"}:
            raise ValueError(f"지원하지 않는 P05 동작: {action_type}")
        action = {"type": action_type, "target": item.get("target", "")}
        if action_type in {"SCARA_MOVE", "GRIPPER"}:
            action["robot"] = "P05"
        p05_actions.append(action)
    scara.pop("p05_actions", None)

    # Preserve the old shared coordinates as an initial copy, never shared state.
    # These must be taught/verified separately on each physical robot.
    unload = base.setdefault("scara_p05", json.loads(json.dumps(scara)))
    unload["speed"] = _number(unload.get("speed", 40), 1, 100, "P05 SCARA 속도", True)
    for robot in (scara, unload):
        robot["driver"] = str(robot.get("driver", "simulation")).lower()
        if robot["driver"] not in {"simulation", "dobot_serial"}:
            raise ValueError("Dobot 드라이버는 simulation 또는 dobot_serial입니다.")
        robot["port"] = str(robot.get("port", "")).strip()
        meta = robot.setdefault("position_meta", {})
        for item in meta.values():
            if item.get("mode") not in {"JOINT", "XYZR"} or item.get("source") not in {"REAL", "SIMULATION"}:
                raise ValueError("Dobot 저장 위치의 좌표 모드/출처가 올바르지 않습니다.")
    shared = unload.get('shared_with', '')
    if shared not in ('', 'P03'):
        raise ValueError('P05 공유 장치는 P03 또는 미공유여야 합니다.')
    if shared == 'P03':
        unload['driver'], unload['port'] = scara['driver'], scara['port']
    if not shared and scara["port"] and scara["port"] == unload["port"] and all(r["driver"] == "dobot_serial" for r in (scara, unload)):
        raise ValueError("P03과 P05에 같은 Dobot USB 포트를 지정할 수 없습니다.")
    unload_positions = {}
    for name, values in unload.get("positions", {}).items():
        clean_name = str(name).strip().upper()[:32]
        if not clean_name or not isinstance(values, list) or len(values) != 4:
            raise ValueError("P05 SCARA 위치는 이름과 4개 축 좌표가 필요합니다.")
        unload_positions[clean_name] = [float(value) for value in values]
    if not unload_positions:
        raise ValueError("P05 SCARA 저장 위치가 한 개 이상 필요합니다.")
    unload["positions"] = unload_positions

    p04 = base["p04"]
    p04["driver"] = str(p04.get("driver", "can_usb")).lower()
    if p04["driver"] not in {"can_usb", "simulation"}:
        raise ValueError("P04 드라이버는 can_usb 또는 simulation입니다.")
    p04["down_steps"] = _number(p04["down_steps"], 1, 0x7FFFFFFF, "프레스 하강 스텝", True)
    p04["up_steps"] = _number(p04["up_steps"], 1, 0x7FFFFFFF, "프레스 상승 스텝", True)
    p04["speed"] = _number(p04["speed"], 1, 100 if p04["driver"] == "can_usb" else 100000, "프레스 속도", True)
    can = p04["can"]
    can["can_id"] = _number(can["can_id"], 1, 2047, "P04 CAN ID", True)
    can["bitrate"] = _number(can["bitrate"], 125000, 1000000, "P04 CAN bitrate", True)
    if can["bitrate"] not in {125000, 250000, 500000, 1000000}:
        raise ValueError("P04 CAN bitrate는 125000/250000/500000/1000000 중 하나입니다.")
    # Old recipe/API fields must not override the fixed distance scale.
    can.pop("mm_per_rev", None)
    can.pop("steps_per_rev_original", None)
    can["steps_per_mm"] = PRESS_STEPS_PER_MM
    can["ramp_rpm_per_second"] = _number(can["ramp_rpm_per_second"], 1, 1000, "P04 가감속")
    can["deceleration_advance_steps"] = _number(can["deceleration_advance_steps"], 0, 1000000, "P04 감속 선행 스텝", True)
    can["home_active_low"] = bool(can["home_active_low"])
    can["up_sign"] = _number(can["up_sign"], -1, 1, "P04 상승 방향", True)
    if can["up_sign"] == 0:
        raise ValueError("P04 상승 방향은 -1 또는 1입니다.")
    can["lookahead_mm"] = _number(can["lookahead_mm"], 0.1, 2, "P04 CAN 선행 거리")
    can["poll_seconds"] = _number(can["poll_seconds"], 0.005, 0.05, "P04 CAN 확인 주기")
    can["can_timeout_seconds"] = _number(can["can_timeout_seconds"], 0.05, 0.3, "P04 CAN 응답 제한")
    p04.pop("repeat", None)
    p04["saved_positions"] = {str(key).strip().upper()[:32]: int(value) for key, value in p04.get("saved_positions", {}).items()}
    p04.pop("actions", None)

    allowed = {"SCARA_MOVE", "DOBOT_MOVE", "DOBOT_HOME", "GRIPPER", "PRESS_MOVE", "PRESS_DOWN", "PRESS_UP", "WAIT"}
    actions = []
    source_actions = base.get("press_recipe", {}).get("actions", [])
    unload_start = len(source_actions)
    if recipe_version == 2:
        # v2 lost phase metadata. Only migrate the recognizable legacy tail;
        # never guess a robot from a position name elsewhere in the recipe.
        tail = DEFAULT_CONFIG["scara"]["p05_actions"]
        plain = [{"type": a.get("type"), "target": a.get("target")} for a in source_actions]
        if plain[-len(tail):] == tail:
            unload_start -= len(tail)
        elif any(a.get("type") in {"SCARA_MOVE", "GRIPPER"} and "robot" not in a for a in source_actions):
            raise ValueError("기존 통합 순서의 SCARA 구분이 필요합니다. 각 동작에 robot P03/P05를 지정하세요.")
    for index, item in enumerate(source_actions):
        action_type = str(item.get("type", "")).upper()
        if action_type not in allowed:
            raise ValueError(f"지원하지 않는 통합 레시피 동작: {action_type}")
        if action_type == "DOBOT_MOVE":
            actions.append(validate_dobot_move(item))
            continue
        if action_type == "DOBOT_HOME":
            robot = str(item.get("robot", "")).upper()
            if robot not in {"P03", "P05"}:
                raise ValueError("Dobot HOME 대상은 P03 또는 P05입니다.")
            actions.append({"type": "DOBOT_HOME", "robot": robot})
            continue
        action = {"type": action_type, "target": item.get("target", "")}
        if action_type in {"SCARA_MOVE", "GRIPPER"}:
            if recipe_version == 3 and "robot" not in item:
                raise ValueError("SCARA 동작에 robot P03/P05를 지정하세요.")
            robot = str(item.get("robot", "P05" if index >= unload_start else "P03")).upper()
            if robot not in {"P03", "P05"}:
                raise ValueError("SCARA 동작 대상은 P03 또는 P05입니다.")
            action["robot"] = robot
        actions.append(action)
    actions.extend(p05_actions)
    if not actions:
        raise ValueError("P03·P04·P05 통합 동작이 한 개 이상 필요합니다.")
    base["press_recipe"] = {"version": 3, "actions": actions}

    p07 = base["p06"]
    p07["mes_endpoint"] = str(p07.get("mes_endpoint", "")).strip()[:300]
    p07["line_code"] = str(p07.get("line_code", "POC-LINE-01")).strip()[:64]
    p07["green_hold_seconds"] = _number(p07["green_hold_seconds"], 0, 60, "초록 경광등 유지시간")
    if p07['relay_active_level'] not in ('UNSET', 'LOW', 'HIGH'):
        raise ValueError('릴레이 작동 신호는 미설정/LOW/HIGH 중 선택하세요.')
    return mm_distance_output(base)


class ConfigStore:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.data = self._load()

    def _load(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return validate_config({})
        # Invalid or ambiguous hardware recipes must not silently become defaults.
        return validate_config(raw)

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.data))

    def save(self, data):
        checked = validate_config(data)
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(checked, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(self.path)
            self.data = checked
        return self.snapshot()

    def update(self, updater):
        with self.lock:
            draft = self.snapshot()
            updater(draft)
            return self.save(draft)
