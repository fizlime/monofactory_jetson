from __future__ import annotations

import threading

from .sequence import SequenceContext, SequenceStopped
from .homing import HomingCoordinator


class LineRuntime:
    def __init__(self, state, config_store, units, sequences):
        self.state = state
        self.config_store = config_store
        self.units = units
        self.sequences = sequences
        self.by_code = {sequence.code: sequence for sequence in sequences}
        if "P03" in self.by_code:
            self.by_code["P04"] = self.by_code["P03"]
            self.by_code["P05"] = self.by_code["P03"]
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.guard = threading.RLock()
        self.thread = None
        self.manual_active = False
        self.homing = HomingCoordinator(units, state)

    def home(self, code=None):
        codes = self.homing.processes if code is None else (str(code).upper(),)
        if any(item not in self.homing.processes for item in codes):
            return False, '호밍 대상은 P01/P03/P04/P05입니다.'
        with self.guard:
            if self.busy() or self.homing.motion_busy():
                return False, '장치 동작 중에는 호밍을 시작할 수 없습니다. STOP 후 다시 시도하세요.'
            self.stop_event.clear()
            self.pause_event.clear()
            self.state.update_line(status='RUNNING', mode='HOMING', message='전체 HOMING 시작' if code is None else f'{code} HOMING 시작')
            def work():
                try:
                    pending = self.homing.run(self._context(), codes)
                    self._context().checkpoint()
                    message = ('호밍 가능한 장비 완료 · ' + ' / '.join(pending)) if pending else 'HOMING 완료 · 사이클 준비'
                    self.state.update_line(status='IDLE',mode='IDLE',active_process=None,message=message)
                except SequenceStopped:
                    self.state.update_line(status='IDLE',mode='IDLE',active_process=None,message='HOMING 정지')
                except Exception as exc:
                    if self.stop_event.is_set():return
                    active=self.state.line.get('active_process') or 'HOME'
                    self.units.stop_all()
                    self.state.update_process(active,status='ERROR',message=str(exc))
                    self.state.update_line(status='ALARM',mode='IDLE',fault={'code':f'{active}-HOME','title':'호밍 실패','message':str(exc),'ack':False},message=str(exc))
            self.thread=threading.Thread(target=work,name='line-homing',daemon=True)
            self.thread.start()
            return True, 'HOMING 시작'

    def busy(self):
        return self.manual_active or (self.thread is not None and self.thread.is_alive())

    def manual_action(self, callback):
        # Reserve the line before releasing the guard; AUTO cannot race a move.
        with self.guard:
            if self.busy():
                return False, "운전 중에는 수동 장치 제어가 잠깁니다."
            self.manual_active = True
        try:
            return callback()
        finally:
            with self.guard:
                self.manual_active = False

    def _context(self, scenario="normal"):
        return SequenceContext(
            self.state, self.config_store.snapshot(), self.units,
            self.stop_event, self.pause_event, scenario,
        )

    def start(self, scenario="normal"):
        with self.guard:
            if self.busy() or self.homing.motion_busy():
                return False, "라인이 이미 운전 중입니다."
            if self.state.line.get("fault"):
                return False, "알람을 확인하고 리셋하세요."
            try:
                self.homing.require_ready()
            except RuntimeError as exc:
                return False, str(exc)
            if 'P03' in self.by_code:
                try:
                    self.by_code['P03'].validate_recipe(self.config_store.snapshot()['press_recipe']['actions'])
                except (RuntimeError, ValueError) as exc:
                    return False, str(exc)
            self.stop_event.clear()
            self.pause_event.clear()
            self.state.reset_processes()
            self.state.update_line(status="RUNNING", mode="AUTO", scenario=scenario, message="무한 반복 자동운전 시작", vision={"visible": False, "result": "WAIT", "time": "", "reason": ""})

            def work():
                ctx = self._context(scenario)
                try:
                    while True:
                        ctx.checkpoint()
                        self.state.reset_processes()
                        for sequence in self.sequences:
                            ctx.checkpoint()
                            sequence.run(ctx)
                            delays = self.config_store.snapshot()["line"]["process_delays"]
                            wait_seconds = delays.get(sequence.code, 0)
                            if sequence.code == "P03":
                                wait_seconds += delays.get("P04", 0) + delays.get("P05", 0)
                            ctx.wait(wait_seconds)
                        count = self.state.line["cycle_count"] + 1
                        self.state.update_line(status="RUNNING", mode="AUTO", active_process="P06", cycle_count=count, message=f"제품 #{count} 완료 · 다음 사이클 준비")
                        self.state.record("PRODUCTION", f"제품 #{count} 완료 · 다음 사이클 자동 시작")
                except SequenceStopped:
                    self.state.update_line(status="IDLE", mode="IDLE", active_process=None, message="라인 정지")
                except Exception as exc:
                    if self.stop_event.is_set():
                        return
                    code = self.state.line.get("active_process") or "LINE"
                    fault = {"code": f"{code}-ERR", "title": f"{code} 공정 오류", "message": str(exc), "ack": False}
                    self.units.light.set("RED")
                    self.state.update_line(status="ALARM", mode="IDLE", fault=fault, message=str(exc))

            self.thread = threading.Thread(target=work, name="line-runtime", daemon=True)
            self.thread.start()
            return True, "P00-P06 자동 운전 시작"

    def run_process(self, code, scenario="normal"):
        with self.guard:
            if self.busy() or self.homing.motion_busy():
                return False, "장치 동작 중에는 개별 공정을 시험할 수 없습니다."
            sequence = self.by_code.get(str(code).upper())
            if not sequence:
                return False, "공정 코드는 P00-P06입니다."
            if str(code).upper() in self.homing.processes:
                try:
                    self.homing.require_ready(('P03','P04','P05') if str(code).upper() in ('P03','P04','P05') else ('P01',))
                except RuntimeError as exc:
                    return False,str(exc)
            self.stop_event.clear()
            self.pause_event.clear()
            self.state.update_line(mode="MANUAL", status="RUNNING", message=f"{sequence.code} 단독 시험")

            def work():
                try:
                    sequence.run(self._context(scenario))
                    self.state.update_line(status="IDLE", mode="IDLE", active_process=None, message=f"{sequence.code} 단독 시험 완료")
                except SequenceStopped:
                    self.state.update_line(status="IDLE", mode="IDLE", active_process=None, message="단독 시험 정지")
                except Exception as exc:
                    if self.stop_event.is_set():
                        return
                    fault = {"code": f"{sequence.code}-ERR", "title": f"{sequence.code} 단독 시험 오류", "message": str(exc), "ack": False}
                    self.state.update_line(status="ALARM", mode="IDLE", fault=fault, message=str(exc))

            self.thread = threading.Thread(target=work, name=f"manual-{sequence.code}", daemon=True)
            self.thread.start()
            return True, f"{sequence.code} 단독 시험 시작"

    def pause(self):
        if not self.busy():
            return False, "운전 중인 시퀀스가 없습니다."
        self.pause_event.set()
        for sequence in self.sequences:
            sequence.machine_pause()
        self.state.update_line(status="PAUSE_REQUESTED", message="현재 유닛 동작 후 일시정지")
        return True, "일시정지 요청"

    def resume(self):
        if not self.pause_event.is_set():
            return False, "일시정지 상태가 아닙니다."
        self.pause_event.clear()
        self.state.update_line(status="RUNNING", message="자동 운전 재개")
        return True, "운전 재개"

    def stop_gripper(self, robot):
        with self.guard:
            running = self.thread is not None and self.thread.is_alive()
            if running:
                self.stop_event.set()
                self.pause_event.clear()
            # Switch off first, even if another unit cannot acknowledge STOP.
            ok, message = robot.stop_gripper()
            if running:
                line_ok, line_message = self.stop()
                if not line_ok:
                    return False, f"{message} / 통합 순서 정지 확인: {line_message}"
            return ok, message

    def stop(self):
        self.stop_event.set()
        self.pause_event.clear()
        for sequence in self.sequences:
            sequence.machine_stop()
        ok, message = self.units.stop_all()
        if not ok:
            fault = {"code": "P04-STOP", "title": "프레스 정지 확인 실패", "message": message, "ack": False}
            self.state.update_line(status="ALARM", mode="IDLE", fault=fault, message=message)
            return False, message
        self.state.update_line(status="IDLE", mode="IDLE", active_process=None, message="전체 정지")
        return True, "STOP ALL 완료"

    def acknowledge(self):
        fault = self.state.line.get("fault")
        if not fault:
            return False, "활성 알람이 없습니다."
        updated = dict(fault)
        updated["ack"] = True
        self.state.update_line(fault=updated)
        return True, "알람 확인"

    def reset(self):
        if self.busy():
            return False, "운전 중에는 리셋할 수 없습니다."
        self.units.light.set("OFF")
        self.state.update_line(status="IDLE", mode="IDLE", active_process=None, fault=None, message="알람 리셋")
        self.state.reset_processes()
        return True, "알람 리셋 완료"
