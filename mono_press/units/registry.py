from __future__ import annotations

from .home_sensor import HomeSensor
from .material_sensor import MaterialSensor
from .mes_client import MesClient
from .press_unit import StepPress
from .pulse_axis import PulseAxis
from .scara_robot import ScaraRobot
from .shared_scara_robot import SharedScaraRobot
from .tower_light import TowerLight
from .vision_camera import VisionCamera
from ..core.manual_amount import positive_integer
from ..core.distance_units import p01_pulses, press_steps


class UnitRegistry:
    """Owns every physical/logical unit and exposes one manual command surface."""

    def __init__(self, state, config_store, servo_bus):
        self.state = state
        self.config_store = config_store
        self.bus = servo_bus
        p01 = config_store.snapshot()["p01"]
        self.material = MaterialSensor(state, config_store, servo_bus.simulation)
        self.p01_homes = [
            HomeSensor(
                state, f"P01_HOME_{i + 1}", "P01", f"자재 {i + 1} 홈 센서",
                servo_bus.simulation,
            )
            for i in range(4)
        ]
        self.p01_axes = []
        for index in range(4):
            name = f"E{index}"
            item = p01["axes"][name]
            self.p01_axes.append(PulseAxis(state, config_store, servo_bus, name, item["address"], self.p01_homes[index], self.material))
        self.camera = VisionCamera(state, config_store, servo_bus.simulation)
        load = ScaraRobot(state, config_store, 'P03')
        shared = config_store.snapshot()['scara_p05'].get('shared_with') == 'P03'
        unload = SharedScaraRobot(state, config_store, load) if shared else ScaraRobot(state, config_store, 'P05')
        self.scaras = {'P03': load, 'P05': unload}
        self.scara = self.scaras["P03"]  # Internal compatibility: always the loading robot.
        self.scara_p05 = self.scaras["P05"]
        self.press = StepPress(state, config_store)
        self.light = TowerLight(state, config_store, self.material)
        self.mes = MesClient(state, config_store)

    @property
    def physical_robots(self):
        return list(dict.fromkeys(getattr(r, 'physical_robot', r) for r in self.scaras.values()))

    def unit_ids_for_process(self, code):
        code = str(code).upper()
        if code == "P00": return [self.material.unit_id]
        if code == "P01": return [u.unit_id for u in self.p01_axes + self.p01_homes]
        if code == "P02": return [self.camera.unit_id]
        if code in self.scaras: return self.scaras[code].unit_ids()
        if code == "P04": return [self.press.axis.unit_id, self.press.home_sensor.unit_id]
        if code == "P06": return [unit.unit_id for unit in self.light.channels] + [self.mes.unit_id]
        return []

    def stop_all(self):
        press_result = self.press.stop()
        errors = [] if press_result[0] else [press_result[1]]
        for axis in self.p01_axes:
            try: axis.stop(emergency=True)
            except Exception: pass
        for robot in self.physical_robots:
            ok, message = robot.stop()
            if not ok:
                errors.append(f"{robot.process} Dobot STOP: {message}")
        self.light.set("OFF")
        return (False, " / ".join(errors)) if errors else press_result

    @staticmethod
    def _manual_distance(payload, legacy_key, convert, maximum):
        if 'distance_mm' in payload:
            if 'pulses' in payload or 'steps' in payload:
                raise ValueError('mm와 펄스/스텝을 함께 입력할 수 없습니다.')
            return convert(payload['distance_mm'])
        # Explicit legacy units remain compatible with existing clients.
        return positive_integer(payload.get(legacy_key), maximum, '이동량')

    def manual_command(self, unit_id, action, payload=None):
        payload = payload or {}
        action = str(action).upper()
        config = self.config_store.snapshot()
        if unit_id.startswith("P01_E"):
            axis = next((item for item in self.p01_axes if item.unit_id == unit_id), None)
            if axis is None: return False, "P01 축을 찾을 수 없습니다."
            axis_config = config["p01"]["axes"][axis.name]
            if action == "ENABLE": return axis.enable()
            if action == "DISABLE": return axis.disable()
            if action == "STOP": return axis.stop()
            if action == "HOME": return axis.home()
            if action in {"FORWARD", "REVERSE"}:
                try:
                    pulses = self._manual_distance(payload, "pulses", lambda mm: p01_pulses(mm, config["p01"]), 0xFFFFFFFF)
                except ValueError as exc:
                    return False, str(exc)
                return axis.start_move(pulses if action == "FORWARD" else -pulses, manual=True)
            if action == "CYCLE": return axis.start_cycle(p01_pulses(axis_config["distance_mm"], config["p01"]), manual=True)
        if unit_id == self.camera.unit_id and action in {"INSPECT", "INSPECT_OK", "INSPECT_NG"}:
            ok = self.camera.inspect(force_ng=action == "INSPECT_NG" or bool(payload.get("force_ng")))
            return ok, "Vision OK" if ok else "Vision NG"
        for robot in self.scaras.values():
            if unit_id not in robot.unit_ids():
                continue
            if unit_id == robot.gripper.unit_id and action == "STOP": return robot.stop_gripper()
            if action == "ENABLE": return robot.enable()
            if action == "HOME": return robot.home()
            if action == "DISABLE": return robot.disable()
            if action == "STOP": return robot.stop()
            if unit_id == robot.gripper.unit_id:
                if action == "OPEN": return robot.set_gripper(False)
                if action == "CLOSE": return robot.set_gripper(True)
            elif action in {"JOG_PLUS", "JOG_MINUS"}:
                axis_number = int(unit_id[-1])
                delta = float(payload.get("delta") or 0.5)
                return robot.jog(axis_number, abs(delta) if action == "JOG_PLUS" else -abs(delta),
                                 str(payload.get("mode", "JOINT")).upper(), payload.get("speed", 10))
        if unit_id == self.press.axis.unit_id:
            if action == "ENABLE": return self.press.enable()
            if action == "DISABLE": return self.press.disable()
            if action == "STOP": return self.press.stop()
            if action == "HOME": return self.press.home()
            try:
                if action == "DOWN":
                    self.press.move_steps(self._manual_distance(payload, "steps", press_steps, 0x7FFFFFFF), payload.get("speed")); return True, "PRESS DOWN 완료"
                if action == "UP":
                    self.press.move_steps(-self._manual_distance(payload, "steps", press_steps, 0x7FFFFFFF), payload.get("speed")); return True, "PRESS UP 완료"
                if action == "CYCLE":
                    self.press.run_cycle(); return True, "PRESS CYCLE 완료"
            except Exception as exc: return False, str(exc)
        if unit_id in {unit.unit_id for unit in self.light.channels}:
            if action in {'ON', 'CHANNEL_OFF'}: return self.light.set_channel(unit_id, action == 'ON')
            if action in {'RED', 'GREEN', 'OFF'}: return self.light.set(action)
        if unit_id == self.mes.unit_id and action in {"SUBMIT", "TEST"}:
            return self.mes.submit(force_fail=bool(payload.get("force_fail")))
        return False, f"지원하지 않는 명령입니다: {unit_id} / {action}"
