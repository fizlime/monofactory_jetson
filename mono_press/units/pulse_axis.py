from __future__ import annotations

import threading
import time

from .base import UnitBase
from ..core.distance_units import p01_pulses, counts_to_mm
from .servo42c_protocol import enable_body, move_body


class PulseAxis(UnitBase):
    def __init__(self, state, config_store, bus, name, address, home_sensor, material_sensor=None):
        super().__init__(
            state, f"P01_{name}", "P01", "PULSE_MOTOR", f"자재 {int(name[1]) + 1} 이송 모터",
            simulated=bus.simulation, address=address, axis=name, connected=False,
            last_event="NONE", last_command="NONE", direction="-", pulses=0, homed=False,
        )
        self.material_sensor = material_sensor
        self.config_store = config_store
        self.bus = bus
        self.name = name
        self.address = address
        self.home_sensor = home_sensor
        self.cancel = threading.Event()
        self.guard = threading.RLock()
        self.worker = None
        bus.bind(address, self.unit_id)

    def busy(self):
        return self.worker is not None and self.worker.is_alive()

    def installed(self):
        return self.config_store.snapshot()["p01"]["axes"][self.name].get("installed", True)

    def enable(self):
        if not self.installed(): return False, f"{self.name} 임시 제외 · SETTING P01에서 사용으로 변경하세요."
        if self.busy():
            return False, "축 동작 중"
        self.update(state="ENABLING", error="", last_command="ENABLE")
        timeout = self.config_store.snapshot()["p01"]["command_timeout"]
        ok, detail, _ = self.bus.command(self.address, enable_body(True), "ENABLE", timeout)
        self.update(enabled=ok, state="READY" if ok else detail, error="" if ok else detail)
        return ok, f"{self.name} ENABLED" if ok else f"{self.name} {detail}"

    def disable(self):
        if self.busy():
            return False, "축 동작 중에는 DISABLE할 수 없습니다."
        self.update(state="DISABLING", last_command="DISABLE")
        timeout = self.config_store.snapshot()["p01"]["command_timeout"]
        ok, detail, _ = self.bus.command(self.address, enable_body(False), "DISABLE", timeout)
        self.update(enabled=False if ok else self.snapshot["enabled"], homed=False, state="DISABLED" if ok else detail, error="" if ok else detail)
        return ok, f"{self.name} DISABLED" if ok else f"{self.name} {detail}"

    def stop(self, emergency=False):
        if self.busy():self.update(homed=False)
        self.cancel.set()
        self.update(state="STOPPING", last_command="STOP")
        if emergency:
            ok, detail = self.bus.emergency_stop(self.address), "STOP TX"
        else:
            # Send STOP before waiting for any serialized diagnostic/ACK phase.
            self.bus.emergency_stop(self.address)
            timeout = self.config_store.snapshot()["p01"]["command_timeout"]
            ok, detail, _ = self.bus.command(self.address, [0xF7], "STOP", timeout)
        self.update(state="STOPPED" if ok else "ERROR", error="" if ok else detail, direction="-")
        return ok, f"{self.name} STOP"

    def home(self, cancel=None):
        if cancel is not None and cancel.is_set():return False, 'HOME 정지'
        if not self.installed(): return False, f"{self.name} 임시 제외"
        if not self.snapshot.get("enabled"):
            return False, "ENABLE 후 HOME 이동이 가능합니다."
        if self.busy():
            return False, "축 동작 중"
        if not self.home_sensor.available():
            self.update(homed=False)
            return False, f"{self.name} 실제 HOME 센서 입력이 연결되지 않았습니다."
        if self.home_sensor.read():
            self.update(position=0, homed=True, state="READY", error="", last_command="HOME")
            return True, f"{self.name} HOME 센서 감지"
        return self.start(lambda:self.home_blocking(cancel=cancel), "HOME")

    def home_blocking(self, cancel=None):
        """Move only in the home direction until the physical sensor is active."""
        p01 = self.config_store.snapshot()["p01"]
        config = p01["axes"][self.name]
        maximum = p01_pulses(config["home_search_mm"], p01)
        chunk = p01_pulses(config["home_step_mm"], p01)
        travelled = 0
        self.cancel.clear()
        self.update(state="HOMING", homed=False, error="", last_command="HOME SEARCH")
        while travelled < maximum:
            if self.cancel.is_set() or (cancel is not None and cancel.is_set()):
                self.update(state="STOPPED")
                return False
            if self.home_sensor.read():
                self.update(position=0, homed=True, state="READY", error="", direction="-")
                self.log("HOME", f"{self.name} HOME 센서 감지 · {counts_to_mm(travelled, p01['pulse_per_rev']):.6f} mm 탐색")
                return True
            step = min(chunk, maximum - travelled)
            if not self._move(-step):
                return False
            travelled += step
        if self.home_sensor.read():
            if self.cancel.is_set() or (cancel is not None and cancel.is_set()):return False
            self.update(position=0, homed=True, state="READY", error="", direction="-")
            return True
        self.update(state="HOME_NOT_FOUND", error="HOME_NOT_FOUND", direction="-")
        self.log("ERROR", f"{self.name} HOME 센서 미감지 · 최대 {counts_to_mm(maximum, p01['pulse_per_rev']):.6f} mm")
        return False

    def _move(self, signed_pulses):
        config = self.config_store.snapshot()["p01"]
        if not self.snapshot["enabled"]:
            self.update(state="ENABLE_REQUIRED", error="ENABLE_REQUIRED")
            return False
        pulses = abs(int(signed_pulses))
        direction = "FORWARD" if signed_pulses > 0 else "REVERSE"
        body = move_body(signed_pulses, config["speed_gear"])
        if signed_pulses > 0:
            self.home_sensor.set_simulated(False)
        started = time.monotonic()
        self.update(state="STARTING", direction=direction, pulses=pulses, last_command=f"{counts_to_mm(signed_pulses, config['pulse_per_rev']):+.6f} mm", error="")
        ok, detail, start_id = self.bus.command(self.address, body, f"{counts_to_mm(signed_pulses, config['pulse_per_rev']):+.6f} mm", config["command_timeout"], self.cancel)
        if not ok:
            self.update(homed=False)
            self.bus.emergency_stop(self.address)
            self.update(state='STOPPED' if self.cancel.is_set() else detail, error='' if self.cancel.is_set() else detail)
            return False
        if self.cancel.is_set():
            self.update(state='STOPPED', error='', homed=False)
            return False
        self.update(state=direction)
        # Official speed formula gives speed_gear * 500 input pulses per second.
        move_timeout = max(config['move_timeout'], pulses / (config['speed_gear'] * 500) * 1.5 + 1)
        complete = ({'at': time.monotonic()} if detail == 'COMPLETE' else
                    self.bus.wait_event(self.address, 2, start_id, move_timeout, self.cancel))
        if complete is None:
            self.update(homed=False)
            if self.cancel.is_set():
                self.update(state="STOPPED")
                return False
            self.bus.emergency_stop(self.address)
            self.update(state="MOVE_TIMEOUT", error="MOVE_TIMEOUT")
            return False
        if self.cancel.is_set():
            self.update(state='STOPPED', error='', homed=False)
            return False
        next_position = self.snapshot.get("position", 0) + signed_pulses
        self.update(state="READY", position=next_position, direction="-", elapsed=round(complete["at"] - started, 3))
        if self.home_sensor.simulated:
            self.home_sensor.set_simulated(next_position <= 0)
        return True

    def move_blocking(self, signed_pulses):
        with self.guard:
            self.cancel.clear()
            return self._move(signed_pulses)

    def cycle_blocking(self, pulses):
        with self.guard:
            self.cancel.clear()
            start_position = int(self.snapshot.get("position", 0))
            self.update(state="CYCLE_FORWARD")
            if not self._move(abs(pulses)):
                return False
            if self.cancel.wait(self.config_store.snapshot()["p01"]["reverse_dwell"]):
                return False
            self.update(state="CYCLE_REVERSE")
            ok = self._move(-abs(pulses))
            self.update(
                state="DONE" if ok else self.snapshot["state"],
                position=start_position if ok else self.snapshot.get("position", 0),
            )
            return ok

    def start(self, target, label):
        with self.guard:
            if self.busy():
                return False, f"{self.name} 동작 중"
            self.cancel.clear()
            self.worker = threading.Thread(target=target, name=f"{self.unit_id}-{label}", daemon=True)
            self.worker.start()
            return True, f"{self.name} {label} 시작"

    def start_move(self, signed_pulses, *, manual=False):
        if not self.installed(): return False, f"{self.name} 임시 제외"
        if not manual and signed_pulses > 0 and self.material_sensor:
            ok, message = self.material_sensor.require_material()
            if not ok: return False, message
        if not self.snapshot.get("enabled"):
            return False, "ENABLE 후 모터 이동이 가능합니다."
        return self.start(lambda: self.move_blocking(signed_pulses), "MOVE")

    def start_cycle(self, pulses, material_authorized=False, *, manual=False):
        if not self.installed(): return False, f"{self.name} 임시 제외"
        if self.material_sensor and not (material_authorized or manual):
            ok, message = self.material_sensor.require_material()
            if not ok: return False, message
        if not self.snapshot.get("enabled"):
            return False, "ENABLE 후 모터 왕복이 가능합니다."
        return self.start(lambda: self.cycle_blocking(pulses), "CYCLE")
