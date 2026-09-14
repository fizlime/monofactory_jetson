from __future__ import annotations

import queue
import threading
import itertools
from dataclasses import fields

from ...core.press_calibration import (
    PRESS_INTERNAL_MM_PER_REV, PRESS_INTERNAL_STEPS_PER_REV, PRESS_STEPS_PER_MM,
)
from .can_link import CanLink
from ...core.distance_units import press_steps
from .control_logic import ENCODER_COUNTS_PER_REV, Cancelled, Config, Controller, home_active


class CanPressDriver:
    """Run every PyUSB/CAN operation on one dedicated worker thread.

    The public methods are synchronous so the existing P04 sequence, REST API,
    and UI can keep their current interfaces. STOP only raises the controller's
    cancellation flag; the worker then sends F7 from the same USB thread.
    """

    def __init__(self, state, axis, home_sensor, config_store):
        self.state = state
        self.axis = axis
        self.home_sensor = home_sensor
        self.config_store = config_store
        self.cancel = threading.Event()
        self.commands = queue.PriorityQueue()
        self.guard = threading.RLock()
        self.order = itertools.count()
        self.generation = 0
        self.stop_pending = False
        self.thread = None
        self.connected = False
        self._origin_encoder = None

    def _config(self, speed=None):
        p04 = self.config_store.snapshot()["p04"]
        raw = dict(p04.get("can", {}))
        raw["mm_per_rev"] = PRESS_INTERNAL_MM_PER_REV
        raw["steps_per_rev_original"] = PRESS_INTERNAL_STEPS_PER_REV
        raw["fast_rpm"] = max(1, min(100, int(speed or p04.get("speed", 100))))
        raw["target_distance_mm"] = p04["down_mm"]
        raw["deceleration_advance_steps"] = press_steps(raw["deceleration_advance_mm"], allow_zero=True)
        names = {item.name for item in fields(Config)}
        return Config(**{key: value for key, value in raw.items() if key in names})

    def _emit(self, kind, value):
        if kind == "log":
            self.state.record("CAN", value, "P04", self.axis.unit_id)
        elif kind == "rpm":
            self.axis.update(command_rpm=int(value))
        elif kind == "phase":
            self.axis.update(detail=str(value))
        elif kind == "home":
            self._origin_encoder = int(value)
        elif kind == "sample":
            config = self._active_config
            active = home_active(value["io"], config)
            self.home_sensor.update(home=active, state="ON" if active else "OFF", connected=True, simulated=False)
            encoder = int(value["position"])
            if self._origin_encoder is None:
                self._origin_encoder = encoder
            down_counts = (self._origin_encoder - encoder) * config.up_sign
            update = {
                "encoder": encoder,
                "connected": True,
                "position": round(down_counts / ENCODER_COUNTS_PER_REV * config.steps_per_rev_original),
            }
            self.axis.update(**update)

    def _worker(self):
        link = None
        controller = None
        try:
            while True:
                _, _, generation, action, args, answer = self.commands.get()
                if action == "close":
                    try:
                        if link is not None:
                            link.stop()
                    except Exception:
                        pass
                    try:
                        if link is not None:
                            link.close()
                    finally:
                        link = controller = None
                        self.connected = False
                        answer.put((True, None))
                    return
                try:
                    if action == "stop":
                        if link is not None:
                            link.stop()
                        with self.guard:
                            if generation == self.generation:
                                self.stop_pending = False
                        answer.put((True, None))
                        continue
                    if action in {"home", "move", "connect", "ready"}:
                        with self.guard:
                            if generation != self.generation or self.stop_pending:
                                raise Cancelled("PRESS STOPPED")
                            self.cancel.clear()
                    if action == "connect":
                        if link is not None:
                            link.close()
                            link = controller = None
                        self.connected = False
                        self._active_config = self._config()
                        link = CanLink(self._active_config)
                        link.cancel_requested = self.cancel.is_set
                        controller = Controller(link, self._active_config, self.cancel, self._emit)
                        link.ready()
                        result = link.sample()
                        self._emit("sample", result)
                        self.connected = True
                    else:
                        if link is None or controller is None:
                            raise RuntimeError("P04 USB CAN이 연결되지 않았습니다.")
                        if action == "ready":
                            link.ready()
                            result = link.sample()
                            self._emit("sample", result)
                        elif action == "home":
                            controller.homing()
                            result = controller.sample()
                        elif action == "move":
                            signed_steps, speed = args
                            self._active_config = self._config(speed)
                            controller.c = self._active_config
                            distance = (
                                abs(int(signed_steps)) / PRESS_STEPS_PER_MM
                            )
                            controller.jog(distance, -1 if signed_steps > 0 else 1)
                            result = controller.sample()
                        elif action == "sample":
                            result = controller.sample()
                        else:
                            raise RuntimeError(f"지원하지 않는 CAN 작업: {action}")
                    answer.put((True, result))
                except BaseException as exc:
                    if action == 'connect':
                        self.connected = False
                        if link is not None:
                            try:
                                link.close()  # Release USB claim after failed handshake.
                            except Exception:
                                pass
                        link = controller = None
                    # HOME has no jog() finally block. Always send F7 when an
                    # active motion fails or is cancelled, on this USB thread.
                    if action in {"home", "move"} and link is not None:
                        try:
                            link.stop()
                        except Exception as stop_exc:
                            exc = RuntimeError(f"PRESS 정지 확인 실패: {stop_exc}")
                    answer.put((False, exc))
        finally:
            self.connected = False
            self.axis.update(homed=False)
            if link is not None:
                try:
                    link.close()
                except Exception:
                    pass

    def _submit(self, action, *args, timeout=180, cancel=None):
        answer = queue.Queue(maxsize=1)
        with self.guard:
            if action in {"home", "move"} and (self.stop_pending or (cancel is not None and cancel.is_set())):
                raise Cancelled("PRESS STOPPED")
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._worker, name="p04-can-usb", daemon=True)
                self.thread.start()
            self.commands.put((0 if action in {"stop", "close"} else 1,
                               next(self.order), self.generation, action, args, answer))
        try:
            ok, value = answer.get(timeout=timeout)
        except queue.Empty:
            self.cancel.set()
            raise RuntimeError("P04 CAN 작업 제한 시간을 초과했습니다.")
        if not ok:
            raise value
        return value

    def connect(self):
        if self.connected:
            return True
        self.axis.update(homed=False)
        self._origin_encoder = None
        try:
            self._submit("connect", timeout=5)
            self.axis.update(connected=True, simulated=False, transport="USB CAN", error="", state="DISABLED")
            self.home_sensor.update(connected=True, simulated=False)
            self.state.record("CAN", "P04 candleLight · SERVO57D 연결 완료", "P04", self.axis.unit_id)
            return True
        except Exception as exc:
            self.connected = False
            self.axis.update(connected=False, simulated=False, enabled=False, state="OFFLINE", error=str(exc))
            self.home_sensor.update(connected=False, simulated=False, state="OFFLINE")
            self.state.record("ERROR", f"P04 CAN 연결 실패 · {exc}", "P04", self.axis.unit_id)
            return False

    def ready(self):
        self._submit("ready", timeout=5)

    def home(self, cancel=None):
        return self._submit("home", cancel=cancel)

    def move_steps(self, signed_steps, speed, cancel=None):
        return self._submit("move", int(signed_steps), int(speed), cancel=cancel)

    def sample(self):
        return self._submit("sample", timeout=5)

    def stop(self):
        with self.guard:
            self.cancel.set()
            self.generation += 1
            self.stop_pending = True
            if self.thread is None or not self.thread.is_alive():
                self.stop_pending = False
                return
        self._submit("stop", timeout=5)

    def close(self):
        self.axis.update(homed=False)
        with self.guard:
            self.cancel.set()
            self.generation += 1
        if self.thread is None or not self.thread.is_alive():
            self.connected = False
            return
        try:
            self._submit("close", timeout=5)
        except Exception:
            pass
        self.thread.join(timeout=1)
        self.connected = False
