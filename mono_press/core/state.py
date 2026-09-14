from __future__ import annotations

import copy
import logging
import threading
import time
from collections import deque

from .config import PROCESS_META
from .distance_units import counts_to_mm, press_mm


def time_text():
    stamp = time.time()
    return time.strftime("%H:%M:%S", time.localtime(stamp)) + f".{int(stamp % 1 * 1000):03d}"


class PlantState:
    def __init__(self, port, baud, simulation, config_store):
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.version = 0
        self.config_store = config_store
        self.serial = {"port": port, "baud": baud, "connected": simulation, "simulation": simulation, "last_error": ""}
        self.line = {
            "status": "IDLE", "mode": "IDLE", "active_process": None, "cycle_count": 0, "message": "운전 준비",
            "fault": None, "scenario": "normal", "vision": {"visible": False, "result": "WAIT", "time": "", "reason": ""},
        }
        self.processes = {
            item["code"]: {
                **item, "status": "IDLE", "progress": 0, "message": "대기", "repeat_current": 0, "repeat_total": 1, "now_step": 0, "before_step": 0, "step_complete": False,
            }
            for item in PROCESS_META
        }
        self.units = {}
        self.unit_aliases = {}
        self.logs = deque(maxlen=300)
        self.record("SYSTEM", "시뮬레이션 모드" if simulation else "실제 장비 모드")

    def _notify(self):
        for alias, source in self.unit_aliases.items():
            identity = {key: self.units[alias][key] for key in ('id', 'process', 'type', 'label')}
            self.units[alias].update(self.units[source])
            self.units[alias].update(identity, shared_with=source)
        self.version += 1
        self.changed.notify_all()

    def touch(self):
        with self.changed:
            self._notify()

    def record(self, category, message, process=None, unit=None):
        entry = {"time": time_text(), "type": category, "process": process or "", "unit": unit or "", "message": str(message)}
        with self.changed:
            self.logs.appendleft(entry)
            self._notify()
        logging.getLogger("mono.control").info("%s | %s | %s", entry["time"], category, message)

    def register_unit(self, unit_id, process, unit_type, label, simulated=False, **extra):
        with self.changed:
            self.units[unit_id] = {
                "id": unit_id, "process": process, "type": unit_type, "label": label, "simulated": bool(simulated),
                "enabled": False, "state": "IDLE", "position": 0, "home": False, "error": "", **extra,
            }
            self._notify()

    def update_unit(self, unit_id, **values):
        with self.changed:
            self.units[unit_id].update(values)
            self._notify()

    def alias_unit(self, alias, source):
        with self.changed:
            if source not in self.units or alias not in self.units or source in self.unit_aliases:
                raise ValueError('장치 공유 대상이 올바르지 않습니다.')
            self.unit_aliases[alias] = source
            self._notify()

    def update_serial(self, **values):
        with self.changed:
            self.serial.update(values)
            self._notify()

    def update_line(self, **values):
        with self.changed:
            self.line.update(values)
            self._notify()

    def update_process(self, code, **values):
        with self.changed:
            self.processes[code].update(values)
            self._notify()

    def reset_processes(self):
        with self.changed:
            for item in self.processes.values():
                item.update(status="IDLE", progress=0, message="대기", repeat_current=0, now_step=0, before_step=0, step_complete=False)
            self._notify()

    def snapshot(self):
        with self.lock:
            config = self.config_store.snapshot()
            units = copy.deepcopy(self.units)
            for unit_id, unit in units.items():
                if unit_id in {f'P01_E{i}' for i in range(4)}:
                    unit['position_mm'] = counts_to_mm(unit.get('position', 0), config['p01']['pulse_per_rev'])
                elif unit_id == 'P04_PRESS_AXIS':
                    unit['position_mm'] = press_mm(unit.get('position', 0))
            return {
                "version": self.version,
                "serial": copy.deepcopy(self.serial),
                "line": copy.deepcopy(self.line),
                "processes": copy.deepcopy(self.processes),
                "process_order": [item["code"] for item in PROCESS_META],
                "units": units,
                "config": config,
                "logs": list(self.logs)[:120],
            }
