from __future__ import annotations

import threading
import time
import math

from .base import UnitBase
from .dobot_serial import DobotSerial


class ScaraRobot:
    """Separate P03/P05 Dobot interfaces; legacy IDs remain recipe-compatible."""

    def __init__(self, state, config_store, process="P03"):
        if process not in {"P03", "P05"}:
            raise ValueError("SCARA process must be P03 or P05")
        self.process = process
        self.config_key = "scara" if process == "P03" else "scara_p05"
        self.state = state
        self.config_store = config_store
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.command_guard = threading.RLock()
        self.simulation = bool(state.serial["simulation"])
        self.device = None
        self.poll_stop = threading.Event()
        self.poll_thread = None
        self.xyzr = [0.0] * 4
        self.home_joints = None
        self.axes = []
        for index in range(4):
            unit = UnitBase(
                state, f"{process}_SCARA_J{index + 1}", process, "SCARA_AXIS", f"{process} SCARA J{index + 1}", True,
                axis=index + 1, position=0.0, state="READY",
            )
            self.axes.append(unit)
        self.gripper = UnitBase(
            state, f"{process}_SCARA_GRIPPER", process, "GRIPPER", f"{process} SCARA 그리퍼", True, state="OPEN", output=False, drive_enabled=None,
        )
        self._status(connected=self.simulation, simulated=self.simulation,
                     xyzr=list(self.xyzr), pose_valid=self.simulation, alarms=[], homed=False)

    def _status(self, **values):
        for axis in self.axes:
            axis.update(**values)
        gripper_values = {k: v for k, v in values.items() if k not in {"xyzr", "position", "last_target"}}
        if gripper_values.get("state") in {"READY", "MOVING", "JOGGING", "HOMING"}:
            gripper_values.pop("state")
        self.gripper.update(**gripper_values)

    def connect(self):
        if self.simulation:
            return True
        if self.device is not None:
            return bool(self.axes[0].snapshot.get("connected"))
        try:
            cfg = self.config_store.snapshot()[self.config_key]
            if cfg.get("driver") != "dobot_serial":
                raise RuntimeError("Dobot USB 장치 미설정")
            self.device = DobotSerial(cfg.get("port", ""))
            for attempt in range(3):
                try:
                    self.refresh_pose(timeout=4.0)
                    break
                except TimeoutError:
                    if attempt == 2:
                        raise
            self._status(connected=True, simulated=False, state="DISABLED", enabled=False, error="")
            self.gripper.update(drive_enabled=None)
            self.refresh_alarms()
            self.poll_stop.clear()
            self.poll_thread = threading.Thread(target=self._poll, name=f"dobot-{self.process}", daemon=True)
            self.poll_thread.start()
            return True
        except Exception as exc:
            if self.device:
                self.device.close()
            self.device = None
            self._status(connected=False, simulated=False, pose_valid=False, enabled=False, state="OFFLINE", error=str(exc))
            return False

    def _poll(self):
        while not self.poll_stop.wait(0.4):
            if not self.lock.acquire(blocking=False):
                continue
            try:
                self.refresh_pose()
                self.refresh_alarms()
            except Exception as exc:
                self._status(connected=False, pose_valid=False, enabled=False, state="OFFLINE", error=str(exc))
            finally:
                self.lock.release()

    def close(self):
        self.home_joints = None
        self._status(homed=False, home=False)
        self.poll_stop.set()
        if self.poll_thread:
            self.poll_thread.join(timeout=2)
        if self.device:
            self.device.close()
            self.device = None

    def set_simulation(self, simulation):
        self.close()
        self.simulation = bool(simulation)
        self.gripper.update(drive_enabled=None)
        self.xyzr = [0.0] * 4
        self._status(simulated=self.simulation, connected=self.simulation, enabled=False,
                     pose_valid=self.simulation, xyzr=list(self.xyzr), position=0, state="DISABLED", error="", alarms=[], homed=False)
        return self.connect()

    def refresh_pose(self, timeout=1.0):
        if self.simulation:
            return
        if self.device is None:
            raise RuntimeError("Dobot USB 연결이 필요합니다.")
        xyzr, joints = self.device.pose(timeout=timeout)
        self.xyzr = xyzr
        # Publish both coordinate systems atomically in one SSE snapshot.
        with self.state.changed:
            for axis, value in zip(self.axes, joints):
                axis.snapshot.update(position=value, xyzr=list(xyzr), connected=True, pose_valid=True, last_pose_at=time.time())
            self.state._notify()

    def refresh_alarms(self):
        alarms = [] if self.simulation else self.device.alarms()
        previous = self.axes[0].snapshot.get("state")
        values = {"alarms": alarms}
        if alarms:
            values.update(enabled=False, state="ALARM", error=f"Dobot 알람: {alarms}")
        elif previous in {"ALARM", "OFFLINE"}:
            values.update(enabled=False, state="DISABLED", error="")
        self._status(**values)
        return alarms

    def home(self, cancel=None):
        """Run controller homing once; saved position named HOME is unrelated."""
        if not self.lock.acquire(blocking=False):
            return False, "Dobot 이동 중입니다."
        sent = False
        self.home_joints = None
        was_enabled = all(axis.snapshot.get("enabled") for axis in self.axes)
        try:
            with self.command_guard:
                if cancel is not None and cancel.is_set():
                    raise RuntimeError('Dobot HOME 정지')
                self.stop_event.clear()
            if not self.simulation:
                if not self.connect():
                    raise RuntimeError(self.axes[0].snapshot.get("error", "Dobot 연결 필요"))
                self.refresh_pose()
                alarms = self.refresh_alarms()
                if alarms:
                    raise RuntimeError(f"HOME 실행 전 Dobot 알람을 확인하세요: {alarms}")
                target = self.device.home_params()
                initial = list(self.xyzr)
            self._status(state="HOMING", homed=False, home=False, error="")
            self.state.record("SCARA", "HOME 시작", self.process)
            if self.simulation:
                if self.stop_event.wait(0.3):
                    raise RuntimeError("Dobot HOME 정지")
                self.xyzr = [0.0] * 4
                for axis in self.axes:
                    axis.update(position=0.0, xyzr=list(self.xyzr))
            else:
                with self.command_guard:
                    if self.stop_event.is_set() or (cancel is not None and cancel.is_set()):
                        raise RuntimeError("Dobot HOME 정지")
                    sent = True  # A lost ACK must still trigger STOP, never a retry.
                    self.device.start_home()
                self._wait_home(target, initial)
            with self.command_guard:
                if self.stop_event.is_set() or (cancel is not None and cancel.is_set()):
                    raise RuntimeError("Dobot HOME 정지")
                self.home_joints = self.current_position()
                self._status(state="READY" if was_enabled else "DISABLED", homed=True, home=True, error="")
            self.state.record("SCARA", "HOME 완료 · 원점복귀 및 위치 안정 확인", self.process)
            return True, f"{self.process} Dobot HOME 완료"
        except Exception as exc:
            stopped = self.stop_event.is_set()
            if sent:
                self.stop()
            status = "STOPPED" if stopped else "ALARM" if self.axes[0].snapshot.get("alarms") else "ERROR"
            self._status(enabled=False, homed=False, home=False, state=status, error=str(exc))
            self.state.record("ERROR", f"HOME · {exc}", self.process)
            return False, str(exc)
        finally:
            self.lock.release()

    def _wait_home(self, target, initial, timeout=90.0):
        started = time.monotonic()
        deadline = started + timeout
        moved = False
        stable = 0
        while time.monotonic() < deadline:
            if self.stop_event.wait(0.25):
                raise RuntimeError("Dobot HOME 정지")
            self.refresh_pose()
            alarms = self.device.alarms()
            if alarms:
                self._status(alarms=alarms)
                raise RuntimeError(f"Dobot HOME 알람: {alarms}")
            moved = moved or max(abs(a - b) for a, b in zip(self.xyzr, initial)) > 0.5
            at_home = max(abs(a - b) for a, b in zip(self.xyzr, target)) < 0.5
            stable = stable + 1 if moved and at_home and time.monotonic() - started >= 2 else 0
            if stable >= 4:
                return
        raise TimeoutError("Dobot HOME 완료 확인 시간 초과")

    @staticmethod
    def validate_target(values, mode):
        if mode != "JOINT":
            raise ValueError("Dobot 이동과 위치 저장은 JOINT J1~J4 각도를 사용합니다.")
        if not isinstance(values, (list, tuple)) or len(values) != 4:
            raise ValueError("목표값은 4개가 필요합니다.")
        values = [float(v) for v in values]
        if not all(math.isfinite(v) and abs(v) <= 10000 for v in values):
            raise ValueError("목표값은 유한한 숫자여야 하며 ±10000 이내입니다.")
        return values

    def pose_values(self, mode="JOINT"):
        self.validate_target([0] * 4, mode)
        self.refresh_pose()
        return self.current_position() if mode == "JOINT" else list(self.xyzr)

    def manual_move(self, values, mode="JOINT", speed=10):
        try:
            self.move_position(values, "MANUAL", mode, speed)
            return True, f"{self.process} {mode} 이동 완료"
        except Exception as exc:
            return False, str(exc)

    def move_home_offset(self, offsets, speed=10):
        offsets = self.validate_target(offsets, 'JOINT')
        if any(abs(value) > 10 for value in offsets):
            raise ValueError('호밍 기준 관절각 증분은 ±10° 이내입니다.')
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('Dobot 이동 중입니다.')
        try:
            if self.home_joints is None or not self.axes[0].snapshot.get('homed'):
                raise RuntimeError('호밍 기준 이동 전에 HOME 완료가 필요합니다.')
            target = [home + offset for home, offset in zip(self.home_joints, offsets)]
            self.move_position(target, f'HOME OFFSET {offsets}', 'JOINT', speed)
        finally:
            self.lock.release()

    def unit_ids(self):
        return [axis.unit_id for axis in self.axes] + [self.gripper.unit_id]

    def current_position(self):
        return [float(axis.snapshot.get("position", 0)) for axis in self.axes]

    def enable(self):
        if not self.simulation:
            if not self.connect():
                return False, self.axes[0].snapshot.get("error", "Dobot 연결 실패")
            try:
                self.refresh_pose()
                alarms = self.refresh_alarms()
                if alarms:
                    return False, f"Dobot 알람 확인 필요: {alarms}"
            except Exception as exc:
                self._status(enabled=False, state="ERROR", error=str(exc))
                return False, str(exc)
        for axis in self.axes:
            axis.update(enabled=True, state="READY", error="")
        self.gripper.update(enabled=True)
        self.state.record("SCARA", "4축 + 그리퍼 ENABLE", self.process)
        return True, "SCARA ENABLED"

    def disable(self):
        ok, message = self.stop()
        for axis in self.axes:
            axis.update(enabled=False, state="DISABLED" if ok else "ERROR")
        self.gripper.update(enabled=False, state="DISABLED")
        return ok, "SCARA DISABLED" if ok else message

    def stop(self):
        self.stop_event.set()
        if self.device:
            try:
                with self.command_guard:
                    self.device.stop()
            except Exception as exc:
                self._status(enabled=False, state="ERROR", error=f"STOP 응답 확인 실패: {exc}")
                return False, str(exc)
        for axis in self.axes:
            axis.update(state="STOPPED")
        self.state.record("SCARA", "STOP", self.process)
        return True, "SCARA STOP"

    def jog(self, axis_number, delta, mode="JOINT", speed=10):
        try:
            axis_number = int(axis_number)
            delta = float(delta)
            if axis_number not in range(1, 5) or not math.isfinite(delta) or not 0 < abs(delta) <= 10:
                raise ValueError("축은 1~4, JOG 간격은 0 초과 10 이하입니다.")
            if not self.lock.acquire(blocking=False):
                return False, "Dobot 이동 중입니다."
            try:
                values = self.pose_values(mode)
                values[axis_number - 1] += delta
                return self.manual_move(values, mode, speed)
            finally:
                self.lock.release()
        except Exception as exc:
            return False, str(exc)

    def set_gripper(self, closed):
        with self.command_guard:
            # Check inside the same lock as STOP so a waiting command cannot
            # restart the drive after STOP has disabled manual permission.
            if not self.gripper.snapshot.get("enabled"):
                return False, "그리퍼 ALL ENABLE이 필요합니다."
            try:
                if not self.simulation:
                    self.device.gripper(closed)
            except Exception as exc:
                self.gripper.update(state="ERROR", drive_enabled=None, error=str(exc))
                return False, str(exc)
            self.gripper.update(state="CLOSE" if closed else "OPEN", output=bool(closed), drive_enabled=True, error="")
        self.state.record("SCARA", f"GRIPPER {'CLOSE' if closed else 'OPEN'}", self.process, self.gripper.unit_id)
        return True, "GRIPPER 명령 완료"

    def stop_gripper(self):
        # Independent of arm movement and logical ENABLE/alarm state.
        with self.command_guard:
            self.gripper.update(enabled=False)
            try:
                if not self.simulation:
                    if self.device is None:
                        raise RuntimeError("Dobot USB 연결이 필요합니다.")
                    self.device.stop_gripper()
            except Exception as exc:
                message = f"그리퍼 STOP 확인 실패: {exc}"
                self.gripper.update(state="ERROR", drive_enabled=None, error=message)
                self.state.record("ERROR", message, self.process, self.gripper.unit_id)
                return False, message
            # output records the last OPEN/CLOSE direction, not jaw feedback.
            self.gripper.update(state="STOPPED", drive_enabled=False, error="")
        self.state.record("SCARA", "GRIPPER STOP · 구동 OFF", self.process, self.gripper.unit_id)
        return True, f"{self.process} 그리퍼 구동 OFF · 재사용하려면 ALL ENABLE"

    def move_position(self, values, label="CURRENT", mode="JOINT", speed=None):
        values = self.validate_target(values, mode)
        speed = float(self.config_store.snapshot()[self.config_key]["speed"] if speed is None else speed)
        if not math.isfinite(speed) or not 1 <= speed <= 100:
            raise ValueError("속도는 1~100%입니다.")
        if not all(axis.snapshot.get("enabled") for axis in self.axes):
            raise RuntimeError("SCARA ENABLE이 필요합니다.")
        if len(values) != 4:
            raise ValueError("SCARA 위치는 4축 값이 필요합니다.")
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("Dobot 이동 중입니다.")
        try:
            with self.command_guard:
                self.stop_event.clear()
            for axis in self.axes:
                axis.update(state="MOVING")
            if not self.simulation:
                try:
                    self.refresh_pose()
                    alarms = self.device.alarms()
                    if alarms:
                        raise RuntimeError(f"Dobot 알람 확인 필요: {alarms}")
                    with self.command_guard:
                        if self.stop_event.is_set():
                            raise RuntimeError("Dobot STOPPED")
                        self.device.move(mode, values, speed)
                    deadline = time.monotonic() + 30
                    stable = 0
                    while time.monotonic() < deadline:
                        if self.stop_event.wait(0.1):
                            raise RuntimeError("Dobot STOPPED")
                        self.refresh_pose()
                        alarms = self.device.alarms()
                        if alarms:
                            raise RuntimeError(f"Dobot 알람: {alarms}")
                        current = self.current_position() if mode == "JOINT" else self.xyzr
                        stable = stable + 1 if max(abs(a - b) for a, b in zip(current, values)) < 0.2 else 0
                        if stable >= 3:
                            self._status(state="READY", error="")
                            self.state.record("SCARA", f"MOVE {mode} {label} · {values}", self.process)
                            return
                    raise TimeoutError("Dobot 이동 완료 확인 시간 초과")
                except Exception as exc:
                    self.stop()
                    self._status(enabled=False, state="ERROR", error=str(exc))
                    raise
            current = self.current_position() if mode == "JOINT" else self.xyzr
            distance = max(abs(float(target) - current[index]) for index, target in enumerate(values))
            if self.stop_event.wait(min(1.0, max(0.12, distance / max(1, speed) * 0.15))):
                raise RuntimeError("SCARA STOPPED")
            if mode == "XYZR":
                self.xyzr = list(values)
                self._status(state="READY", xyzr=list(values), last_target=label)
            else:
                for index, axis in enumerate(self.axes):
                    axis.update(state="READY", position=float(values[index]), last_target=label)
            self.state.record("SCARA", f"MOVE {label} · {values}", self.process)
        finally:
            self.lock.release()

    def saved_joint_target(self, name):
        config = self.config_store.snapshot()[self.config_key]
        key = str(name).upper()
        if key not in config['positions']:
            raise ValueError(f'저장 위치 {key}가 없습니다.')
        meta = config.get('position_meta', {}).get(key, {})
        if meta.get('mode', 'JOINT') != 'JOINT':
            raise ValueError('기존 XYZ 위치입니다. 해당 위치에서 현재 Joint 값을 다시 저장하세요.')
        if not self.simulation and (meta.get('source') != 'REAL' or meta.get('mode') != 'JOINT'):
            raise RuntimeError('실장비에서 현재 Joint 위치를 다시 저장한 뒤 사용하세요.')
        if not self.simulation and meta.get('port') != config.get('port'):
            raise RuntimeError('USB 장치 설정이 바뀌었습니다. 현재 장치에서 위치를 다시 저장하세요.')
        return self.validate_target(config['positions'][key], 'JOINT')

    def move_saved(self, name):
        key = str(name).upper()
        try:
            values = self.saved_joint_target(key)
            self.move_position(values, key, 'JOINT')
            return True, f'Dobot → {key} · JOINT'
        except Exception as exc:
            return False, str(exc)

    def execute_actions(self, actions, context=None):
        for action in actions:
            if context:
                context.checkpoint()
            action_type = str(action.get("type", "")).upper()
            target = action.get("target", "")
            if action_type in {"MOVE", "SCARA_MOVE"}:
                ok, message = self.move_saved(target)
                if not ok:
                    raise RuntimeError(message)
            elif action_type == "GRIPPER":
                ok, message = self.set_gripper(str(target).upper() == "CLOSE")
                if not ok:
                    raise RuntimeError(message)
            elif action_type == "WAIT":
                (context.wait if context else time.sleep)(float(target or 0.2))
            else:
                raise RuntimeError(f"지원하지 않는 SCARA 동작: {action_type}")
