from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod


class SequenceStopped(Exception):
    pass


class SequenceContext:
    def __init__(self, state, config, units, stop_event, pause_event, scenario="normal"):
        self.state = state
        self.config = config
        self.units = units
        self.stop_event = stop_event
        self.pause_event = pause_event
        self.scenario = scenario

    def wait(self, seconds):
        deadline = time.monotonic() + max(0, seconds)
        while time.monotonic() < deadline:
            self.checkpoint()
            time.sleep(max(0, min(0.04, deadline - time.monotonic())))

    def checkpoint(self):
        if self.stop_event.is_set():
            raise SequenceStopped()
        while self.pause_event.is_set():
            self.state.update_line(status="PAUSED", message="공정 경계에서 일시정지")
            if self.stop_event.wait(0.05):
                raise SequenceStopped()


class Sequence(ABC):
    """Step/tick lifecycle from sequence_service, adapted to local equipment.

    A tick executes one step. Hardware adapters retain their own completion,
    timeout and STOP checks. No database or printer services are required.
    """

    def __init__(self, sequence_name):
        self.sequence_name = sequence_name
        self._sequence_is_working = False
        self._tick_lock = threading.Lock()
        self.before_step = self._now_step = 0
        self._origin_step = 0
        self.is_origin = False
        self.enable_sequence = True
        self._step_started_at = time.monotonic()
        self._initialized = False

    @property
    def sequence_is_working(self):
        return self._sequence_is_working

    @property
    def now_step(self):
        return self._now_step

    @now_step.setter
    def now_step(self, value):
        self.before_step, self._now_step = self._now_step, int(value)
        self._step_started_at = time.monotonic()

    def elapsed_ms(self):
        return int((time.monotonic() - self._step_started_at) * 1000)

    def sequence_run(self):
        if not self.enable_sequence or not self._tick_lock.acquire(False):
            return self.sequence_is_working
        self._sequence_is_working = True
        try:
            if not self._initialized:
                self.get_db_info()
                self.init()
                self._initialized = True
            self.sequence_logic(self.now_step)
        finally:
            self._sequence_is_working = False
            self._tick_lock.release()
        return False

    def sequence_run_void(self):
        self.sequence_run()

    def machine_stop(self):
        self.machine_stop_logic()
        # A running hardware call unwinds on its own thread after STOP.
        if getattr(self, '_owner_thread', threading.get_ident()) != threading.get_ident():
            return
        if self._tick_lock.acquire(False):
            try:
                self._reset_steps()
            finally:
                self._tick_lock.release()

    def _reset_steps(self):
        self.now_step = self.before_step = 0
        self._initialized = False

    def machine_pause(self):
        self.machine_pause_logic()

    def origin(self):
        return self.origin_logic()

    @abstractmethod
    def sequence_logic(self, step): ...
    @abstractmethod
    def machine_stop_logic(self): ...
    @abstractmethod
    def machine_pause_logic(self): ...
    @abstractmethod
    def origin_logic(self): ...
    @abstractmethod
    def get_db_info(self): ...
    @abstractmethod
    def save_data(self): ...
    @abstractmethod
    def init(self): ...
    @abstractmethod
    def step_update_to_db(self, cmd_id, now_step, is_complete, remark): ...


class ProcessSequence(Sequence):
    code = "P00"
    name = "Process"
    unit_ids = ()

    def __init__(self):
        super().__init__(self.code)
        self.run_lock = threading.Lock()
        self.ctx = None
        self._steps = None
        self.completed = False

    def get_db_info(self):
        # The original service reads a repository here; monofactory uses JSON.
        store = getattr(self, 'config_store', None)
        self.settings = store.snapshot() if store is not None else getattr(self.ctx, 'config', {})

    def init(self):
        self.completed = False
        self._steps = iter(self.steps(self.ctx))

    def sequence_logic(self, step):
        self.ctx.checkpoint()
        if step == 0:
            self.now_step = 10
        else:
            try:
                # False means keep polling the current step; True advances.
                advance = next(self._steps)
            except StopIteration:
                self.completed = True
                self.save_data()
                self.now_step = 0
            else:
                if advance is not False:
                    self.now_step += 10
        self.step_update_to_db('', self.now_step, self.completed, '')

    def step_update_to_db(self, cmd_id, now_step, is_complete, remark):
        self.ctx.state.update_process(self.code, now_step=now_step,
                                      before_step=self.before_step, step_complete=is_complete)

    def save_data(self):
        pass  # Process/MES reporting remains with the existing runtime and P06.

    def machine_stop_logic(self):
        if self.ctx is not None:
            self.ctx.stop_event.set()

    def machine_pause_logic(self):
        if self.ctx is not None:
            self.ctx.pause_event.set()

    def origin_logic(self):
        # Origin is an explicit equipment command, never a step-counter reset.
        raise RuntimeError('MANUAL에서 해당 장치 HOME을 실행하세요.')

    def _reset_steps(self):
        if self._steps is not None:
            self._steps.close()
            self._steps = None
        super()._reset_steps()

    def execute(self, ctx):
        self._owner_thread = threading.get_ident()
        self.ctx = ctx
        self._reset_steps()
        try:
            while not self.completed or not self._initialized:
                ctx.checkpoint()
                self.sequence_run_void()
                if not self.enable_sequence:
                    raise RuntimeError(f'{self.code} 시퀀스가 비활성 상태입니다.')
        finally:
            self._reset_steps()

    def run(self, ctx: SequenceContext):
        if not self.run_lock.acquire(False):
            raise RuntimeError(f"{self.code} 시퀀스가 이미 실행 중입니다.")
        try:
            ctx.state.update_line(active_process=self.code, status="RUNNING", message=f"{self.code} {self.name}")
            ctx.state.update_process(self.code, status="RUNNING", progress=0, message="시작")
            ctx.state.record("PROCESS", f"{self.code} START · {self.name}", self.code)
            self.execute(ctx)
            ctx.checkpoint()
            ctx.state.update_process(self.code, status="DONE", progress=100, message="완료")
            ctx.state.record("PROCESS", f"{self.code} COMPLETE", self.code)
        except SequenceStopped:
            ctx.state.update_process(self.code, status="STOPPED", message="정지")
            raise
        except Exception as exc:
            ctx.state.update_process(self.code, status="ERROR", message=str(exc))
            ctx.state.record("ERROR", f"{self.code} · {exc}", self.code)
            raise
        finally:
            self.run_lock.release()

    def repeat_progress(self, ctx, current, total, message):
        progress = int((current - 1) / max(1, total) * 100)
        ctx.state.update_process(
            self.code, repeat_current=current, repeat_total=total, progress=progress, message=message
        )

    @abstractmethod
    def steps(self, ctx: SequenceContext):
        ...
