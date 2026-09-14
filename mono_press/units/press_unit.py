from __future__ import annotations

from ..core.distance_units import press_steps, press_mm

import threading
from contextlib import contextmanager

from .base import UnitBase
from .can_press import CanPressDriver
from .can_press.control_logic import Cancelled
from .home_sensor import HomeSensor


class StepPress:
    """P04 facade: simulation keeps the UI runnable; REAL uses USB CAN."""

    def __init__(self, state, config_store):
        self.state = state
        self.config_store = config_store
        self.lock = threading.RLock()
        self.control_guard = threading.RLock()
        self.operation_depth = 0
        self.stopping = False
        self.stop_error = ""
        self.stop_event = threading.Event()
        self.simulation = bool(state.serial["simulation"])
        self.axis = UnitBase(
            state, "P04_PRESS_AXIS", "P04", "CAN_PRESS", "프레스 CAN 모터", self.simulation,
            position=0, state="READY", speed=config_store.snapshot()["p04"]["speed"], connected=self.simulation,
            transport="SIMULATION" if self.simulation else "USB CAN", command_rpm=0, encoder=0, detail="", homed=False,
        )
        self.home_sensor = HomeSensor(state, "P04_HOME_1", "P04", "프레스 홈 센서", self.simulation)
        self.can = CanPressDriver(state, self.axis, self.home_sensor, config_store)

    def set_simulation(self, simulation):
        simulation = bool(simulation)
        if simulation:
            self.can.close()
        self.simulation = simulation
        self.axis.simulated = simulation
        self.home_sensor.simulated = simulation
        self.axis.update(
            simulated=simulation, connected=simulation, enabled=False, homed=False,
            state="READY" if simulation else "OFFLINE", error="",
            transport="SIMULATION" if simulation else "USB CAN",
        )
        self.home_sensor.update(simulated=simulation, connected=simulation, home=simulation, state="ON" if simulation else "OFFLINE")

    def connect(self):
        if self.simulation or self.config_store.snapshot()["p04"].get("driver") == "simulation":
            self.axis.update(connected=True, simulated=True, transport="SIMULATION", state="READY", error="")
            return True
        return self.can.connect()

    def close(self):
        self.axis.update(homed=False)
        self.can.close()
        if not self.simulation:
            self.axis.update(connected=False, enabled=False, state="OFFLINE")

    def enable(self):
        if not self.lock.acquire(False):
            return False, "PRESS 동작 중입니다. STOP 후 다시 시도하세요."
        try:
            if self.stopping:
                return False, "PRESS 정지 확인 중입니다."
            return self._enable()
        finally:
            self.lock.release()

    def _enable(self):
        if not self.simulation:
            if not self.can.connected and not self.can.connect():
                return False, self.axis.snapshot.get("error") or "P04 CAN 연결 실패"
            try:
                self.can.ready()
            except Exception as exc:
                self.axis.update(enabled=False, homed=False, state="ERROR", error=str(exc))
                return False, str(exc)
        self.axis.update(enabled=True, state="READY", error="")
        return True, "PRESS ENABLED"

    def disable(self):
        ok, message = self.stop()
        if not ok:
            return ok, message
        self.axis.update(enabled=False, homed=False, state="DISABLED")
        return True, "PRESS DISABLED"

    def stop(self):
        with self.control_guard:
            self.stop_event.set()
            self.stopping = True
            self.axis.update(state="STOPPING", detail="정지 요청")
            self.state.record("PRESS", "STOP 요청", "P04", self.axis.unit_id)
        try:
            if not self.simulation:
                self.can.stop()
            with self.control_guard:
                self.stop_error = ""
                self.axis.update(state="STOPPED", command_rpm=0, error="", detail="정지 확인")
                self.state.record("PRESS", "STOP 확인 완료", "P04", self.axis.unit_id)
            return True, "PRESS STOP 확인 완료"
        except Exception as exc:
            with self.control_guard:
                self.stop_error = str(exc)
                self.axis.update(state="ERROR", homed=False, error=self.stop_error, detail="정지 확인 실패")
            return False, f"PRESS 정지 확인 실패: {exc}"
        finally:
            with self.control_guard:
                self.stopping = False

    @contextmanager
    def _operation(self):
        # Reject overlapping button presses instead of replaying them after STOP.
        if not self.lock.acquire(False):
            raise RuntimeError("PRESS 동작 중입니다. STOP 후 다시 시도하세요.")
        entered = False
        try:
            with self.control_guard:
                if self.stopping:
                    raise Cancelled("PRESS STOPPING")
                if self.operation_depth == 0:
                    self.stop_event.clear()
                    self.stop_error = ""
                self.operation_depth += 1
                entered = True
                if self.stop_event.is_set():
                    raise Cancelled("PRESS STOPPED")
            yield
            if self.stop_event.is_set():
                raise Cancelled("PRESS STOPPED")
        except Cancelled:
            with self.control_guard:
                if not self.stop_error:
                    self.axis.update(state="STOPPING" if self.stopping else "STOPPED", error="")
            raise
        finally:
            if entered:
                self.operation_depth -= 1
            self.lock.release()

    def move_steps(self, signed_steps, speed=None):
        with self._operation():
            return self._move_steps(signed_steps, speed)

    def _move_steps(self, signed_steps, speed=None):
        if not self.axis.snapshot.get("enabled"):
            raise RuntimeError("PRESS ENABLE이 필요합니다.")
        self.require_homed()
        steps = int(signed_steps)
        selected_speed = int(speed or self.config_store.snapshot()["p04"]["speed"])
        with self.lock:
            if self.simulation:
                self.home_sensor.set_simulated(False)
            self.axis.update(state="DOWN" if steps > 0 else "UP", speed=selected_speed, last_command=f"{press_mm(steps):+.6f} mm")
            if not self.simulation:
                try:
                    self.can.move_steps(steps, selected_speed, cancel=self.stop_event)
                    if self.stop_event.is_set():
                        raise Cancelled("PRESS STOPPED")
                    self.axis.update(state="READY", command_rpm=0)
                    self.state.record("PRESS", f"CAN MOVE {press_mm(steps):+.6f} mm · {selected_speed} RPM", "P04", self.axis.unit_id)
                    return
                except Cancelled:
                    raise
                except Exception as exc:
                    self.axis.update(state="ERROR", homed=False, command_rpm=0, error=str(exc))
                    raise
            duration = min(1.0, max(0.12, abs(steps) / max(1, selected_speed) * 0.04))
            if self.stop_event.wait(duration):
                raise Cancelled("PRESS STOPPED")
            position = int(self.axis.snapshot.get("position", 0)) + steps
            self.axis.update(state="READY", position=position)
            self.home_sensor.set_simulated(position <= 0)
            self.state.record("PRESS", f"MOVE {press_mm(steps):+.6f} mm · SPEED {selected_speed}", "P04", self.axis.unit_id)

    def home(self, cancel=None):
        try:
            with self._operation():
                if cancel is not None and cancel.is_set():
                    raise Cancelled('PRESS STOPPED')
                return self._home()
        except Cancelled as exc:
            self.axis.update(homed=False)
            return False, str(exc)
        except Exception as exc:
            return False, str(exc)

    def _home(self):
        if not self.axis.snapshot.get("enabled"):
            return False, "PRESS ENABLE이 필요합니다."
        self.axis.update(homed=False, state="HOMING", detail="원점 센서 탐색")
        try:
            if not self.simulation:
                self.can.home(cancel=self.stop_event)
                if self.stop_event.is_set():
                    raise Cancelled("PRESS STOPPED")
                sensor = self.home_sensor.snapshot
                if not sensor.get("connected") or not sensor.get("home"):
                    raise RuntimeError("PRESS HOME 센서가 확인되지 않았습니다. 다시 HOME을 실행하세요.")
            else:
                # Homing is the only travel allowed before the origin is known.
                # Do not route it through the normal movement interlock.
                if self.stop_event.wait(0.12):
                    raise Cancelled("PRESS STOPPED")
                self.home_sensor.set_simulated(True)
            with self.control_guard:
                if self.stop_event.is_set():
                    raise Cancelled("PRESS STOPPED")
                self.axis.update(position=0, homed=True, state="READY", error="", detail="원점 설정 완료")
            return True, "PRESS CAN HOME 완료" if not self.simulation else "PRESS HOME 완료"
        except Cancelled:
            raise
        except Exception as exc:
            self.axis.update(homed=False, state="ERROR", error=str(exc), detail="원점 설정 실패")
            return False, str(exc)

    def require_homed(self):
        if not self.axis.snapshot.get("homed"):
            raise RuntimeError("PRESS HOME이 필요합니다. 먼저 HOME 센서 찾기를 완료하세요.")

    def _cycle_checkpoint(self, context=None):
        if context is not None:
            context.checkpoint()
        if self.stop_event.is_set():
            raise Cancelled("PRESS STOPPED")

    @contextmanager
    def prepared_cycle(self, context=None):
        # Explicit HOME must finish first. This context never initiates homing.
        with self._operation():
            self._cycle_checkpoint(context)
            ok, message = self.enable()
            if not ok:
                raise RuntimeError(message)
            self._cycle_checkpoint(context)
            self.require_homed()
            self._cycle_checkpoint(context)
            yield

    def run_cycle(self, context=None):
        with self.prepared_cycle(context):
            return self._run_cycle(context)

    def _run_cycle(self, context=None):
        config = self.config_store.snapshot()["p04"]
        self.move_steps(press_steps(config["down_mm"]), config["speed"])
        if context:
            context.wait(0.1)
        else:
            if self.stop_event.wait(0.1):
                raise Cancelled("PRESS STOPPED")
        self.move_steps(-press_steps(config["up_mm"]), config["speed"])

    def current_position(self):
        return int(self.axis.snapshot.get("position", 0))

    def move_saved(self, name):
        positions = self.config_store.snapshot()["p04"]["saved_positions_mm"]
        key = str(name).upper()
        if key not in positions:
            return False, f"프레스 저장 위치 {key}가 없습니다."
        try:
            self.require_homed()
            delta = press_steps(positions[key], signed=True, allow_zero=True) - self.current_position()
            if delta:
                self.move_steps(delta)
            return True, f"PRESS → {key}"
        except Exception as exc:
            return False, str(exc)
