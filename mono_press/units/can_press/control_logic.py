"""PC sensor homing and rolling targets, with linear ramps for round trips."""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, fields
from pathlib import Path
from threading import Event

from ...core.press_calibration import PRESS_INTERNAL_MM_PER_REV, PRESS_INTERNAL_STEPS_PER_REV

ENCODER_COUNTS_PER_REV = 16384


class ControlError(RuntimeError):
    pass


class Cancelled(ControlError):
    pass


@dataclass(frozen=True)
class Config:
    can_id: int = 1
    bitrate: int = 500000
    mm_per_rev: float = PRESS_INTERNAL_MM_PER_REV
    steps_per_rev_original: int = PRESS_INTERNAL_STEPS_PER_REV
    up_sign: int = 1
    home_input_bit: int = 0
    home_active_low: bool = True
    target_distance_mm: float = 160.0
    slow_zone_mm: float = 10.0
    home_approach_mm: float = 5.0
    repeat_count: int = 3
    fast_rpm: int = 100
    ramp_rpm_per_second: float = 75.0
    deceleration_advance_steps: int = 4000
    fast_delay_us: int = 200
    slow_delay_us: int = 800
    home_start_delay_us: int = 1200
    home_seek_delay_us: int = 400
    home_fine_delay_us: int = 1800
    home_start_mm: float = 3.0
    home_backoff_mm: float = 2.0
    max_home_search_mm: float = 350.0
    home_fine_search_mm: float = 5.0
    home_escape_mm: float = 10.0
    extra_home_search_mm: float = 5.0
    lookahead_mm: float = 1.0
    poll_seconds: float = 0.02
    can_timeout_seconds: float = 0.15
    acceleration: int = 1

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name == 'home_active_low':
                if type(value) is not bool:
                    raise ValueError('home_active_low는 true/false여야 합니다.')
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                raise ValueError(f'{field.name}: 유효한 숫자가 필요합니다.')
            if isinstance(field.default, int) and type(value) is not int:
                raise ValueError(f'{field.name}: 정수가 필요합니다.')
            if field.name not in ('up_sign', 'home_input_bit', 'acceleration', 'deceleration_advance_steps') and value <= 0:
                raise ValueError(f'{field.name}: 0보다 커야 합니다.')
        if self.up_sign not in (-1, 1) or self.home_input_bit != 0:
            raise ValueError('up_sign은 ±1이며 이 버전의 센서 입력은 IN_1(bit 0)입니다.')
        if not 1 <= self.can_id <= 2047 or self.bitrate not in (125000, 250000, 500000, 1000000):
            raise ValueError('잘못된 CAN ID/속도')
        if not 1 <= self.repeat_count <= 100 or not 0 <= self.acceleration <= 255:
            raise ValueError('잘못된 반복 횟수/가속도')
        if not 0.005 <= self.poll_seconds <= 0.05 or not 0.05 <= self.can_timeout_seconds <= 0.3:
            raise ValueError('통신 주기/시간 제한 범위를 확인하세요.')
        if not 0.1 <= self.lookahead_mm <= 2:
            raise ValueError('lookahead_mm은 0.1~2 mm 범위여야 합니다.')
        for delay in (self.fast_delay_us, self.slow_delay_us, self.home_start_delay_us,
                      self.home_seek_delay_us, self.home_fine_delay_us):
            if not 1 <= self.rpm(delay) <= 100:
                raise ValueError('변환된 속도는 1~100 RPM이어야 합니다.')
        if self.fast_delay_us > self.slow_delay_us:
            raise ValueError('fast_delay_us는 slow_delay_us보다 작아야 합니다.')
        if not max(self.rpm(self.slow_delay_us), self.rpm(self.home_start_delay_us)) <= self.fast_rpm <= 100:
            raise ValueError('fast_rpm은 저속 이상, 100 RPM 이하여야 합니다.')
        if not 1 <= self.ramp_rpm_per_second <= 1000:
            raise ValueError('ramp_rpm_per_second는 1~1000 범위여야 합니다.')
        if self.deceleration_advance_steps < 0:
            raise ValueError('감속 선행 거리는 0 steps 이상이어야 합니다.')
        if not 1 <= self.counts(self.target_distance_mm) < 2**23:
            raise ValueError('이동 거리는 엔코더 1카운트 이상, CAN 좌표 범위 이내여야 합니다.')

    @classmethod
    def load(cls, path: Path):
        return cls(**json.loads(path.read_text(encoding='utf-8-sig')))

    def rpm(self, delay):
        # Original stepOnce(): LOW delay + HIGH delay, not just one delay.
        return round(60_000_000 / (2 * delay * self.steps_per_rev_original))

    def steps(self, mm):
        return round(mm / self.mm_per_rev * self.steps_per_rev_original)

    def counts(self, mm):
        return round(mm / self.mm_per_rev * ENCODER_COUNTS_PER_REV)

    def mm(self, counts):
        return counts * self.mm_per_rev / ENCODER_COUNTS_PER_REV

    def down_rpm(self, distance):
        slow = self.rpm(self.slow_delay_us)
        # Start early enough even if the user selects a gentle ramp rate.
        zone = max(self.slow_zone_mm, self.ramp_distance(self.fast_rpm, slow)) + self.deceleration_advance_mm()
        return slow if distance >= self.target_distance_mm - zone else self.fast_rpm

    def deceleration_advance_mm(self):
        return self.deceleration_advance_steps / self.steps_per_rev_original * self.mm_per_rev

    def ramp_distance(self, high, low):
        # Integral of a linear-in-time RPM ramp, converted to millimetres.
        return max(0, high * high - low * low) / (2 * self.ramp_rpm_per_second) * self.mm_per_rev / 60


class RpmRamp:
    """Symmetric linear-in-time slew; CAN output is rounded to integer RPM."""
    def __init__(self, rate, now):
        self.rate, self.last_time, self.value = rate, now, 1.0

    def update(self, target, now):
        change = self.rate * max(0, now - self.last_time)
        self.value += max(-change, min(change, target - self.value))
        self.last_time = now
        return max(1, round(self.value))


class SensorProof:
    """Require both sustained physical levels while stationary in this connection."""
    def __init__(self):
        self.level = None
        self.since = 0.0
        self.seen = set()

    def observe(self, active, now):
        if active != self.level:
            self.level, self.since = active, now
        if now - self.since >= 0.15:
            self.seen.add(active)

    @property
    def verified(self):
        return self.seen == {False, True}


def home_active(io, config):
    high = bool(io & (1 << config.home_input_bit))
    return not high if config.home_active_low else high


def parse_jog_distance(config, value):
    try:
        mm = float(value)
    except (TypeError, ValueError):
        raise ValueError('조그 거리는 mm 단위 숫자로 입력하세요.') from None
    if not math.isfinite(mm) or mm <= 0:
        raise ValueError('조그 거리는 0보다 큰 유한한 숫자여야 합니다.')
    if not 1 <= config.counts(mm) < 2**23:
        raise ValueError('조그 거리가 CAN 이동 범위를 벗어났습니다.')
    return mm


class Controller:
    def __init__(self, motor, config, stop_event=None, emit=None,
                 clock=time.monotonic, sleep=time.sleep):
        self.motor = motor
        self.c = config
        self.cancel = stop_event if stop_event is not None else Event()
        self.emit = emit or (lambda kind, data: None)
        self.clock, self.sleep = clock, sleep
        self.home_position = None
        self.completed = 0
        self.phase = '대기'

    def announce(self, text):
        self.phase = text
        self.emit('log', text)
        self.emit('phase', text)

    def check_cancel(self):
        if self.cancel.is_set():
            raise Cancelled('사용자가 정지했습니다. 다시 시작하면 처음부터 원점을 찾습니다.')

    def pause(self, duration):
        deadline = self.clock() + duration
        while self.clock() < deadline:
            self.check_cancel()
            self.sleep(min(0.02, deadline - self.clock()))

    def sample(self):
        self.check_cancel()
        sample = self.motor.sample()
        sample['home_active'] = home_active(sample['io'], self.c)
        if self.home_position is not None:
            sample['distance_mm'] = self.c.mm((self.home_position - sample['position']) * self.c.up_sign)
        self.emit('sample', sample)
        return sample

    def stop_motion(self):
        self.motor.stop()
        self.emit('rpm', 0)

    def move(self, distance_mm, direction, speed, sensor_target=None, linear=False):
        """Move up/down with a rolling, <=lookahead_mm absolute target.

        A lost PC connection cannot leave a 350 mm search commanded. A normal
        Python shutdown stops the drive; a hard crash leaves at most the most
        recent short target, plus tracking/braking error. Not a hardware interlock.
        """
        if direction not in (-1, 1) or distance_mm < 0:
            raise ValueError('잘못된 이동 방향/거리')
        first = self.sample()
        if sensor_target is not None and first['home_active'] == sensor_target:
            self.stop_motion()
            return True
        start = first['position']
        sign = self.c.up_sign * direction
        total = self.c.counts(distance_mm)
        end = start + sign * total
        if not -(2**23) < min(start, end) or not max(start, end) < 2**23:
            raise ControlError('이동 목표가 CAN 절대좌표 범위를 벗어났습니다.')
        if total == 0:
            return False
        min_rpm = min(self.c.rpm(d) for d in (self.c.fast_delay_us, self.c.slow_delay_us,
                      self.c.home_start_delay_us, self.c.home_seek_delay_us, self.c.home_fine_delay_us))
        timeout = distance_mm / (min_rpm * self.c.mm_per_rev / 60) * 2 + 5
        begin = self.clock()
        last_target = None
        last_speed = None
        ramp = RpmRamp(self.c.ramp_rpm_per_second, begin) if linear else None
        lookahead = self.c.counts(self.c.lookahead_mm)
        tolerance = max(12, self.c.counts(0.03))
        next_health = begin
        self.motor.begin_motion()
        while True:
            now = self.clock()
            sample = self.sample()
            progress = (sample['position'] - start) * sign
            if sensor_target is not None and sample['home_active'] == sensor_target:
                self.stop_motion()
                return True
            if now - begin > timeout:
                raise ControlError('이동 제한 시간을 초과했습니다.')
            if now >= next_health:
                if self.motor.read(0x3E):
                    raise ControlError('모터 보호 상태가 감지됐습니다.')
                next_health = now + 0.2
            # Only consider the segment finished after the exact final target was sent.
            if last_target == end and abs(total - progress) <= tolerance and self.motor.read(0xF1) == 1:
                self.stop_motion()
                return sensor_target is not None and self.sample()['home_active'] == sensor_target
            wanted = speed(self.c.mm(max(0, progress))) if callable(speed) else speed
            rpm = ramp.update(wanted, self.clock()) if ramp else wanted
            remaining_to_sent = ((last_target - sample['position']) * sign) if last_target is not None else 0
            # Stream each poll during a ramped move. Waiting for half of a short
            # target to be consumed reduces the time available for the next update.
            if last_target is None or rpm != last_speed or (last_target != end and (linear or remaining_to_sent <= lookahead / 2)):
                ahead = min(total, max(0, progress) + lookahead)
                target = start + sign * int(ahead)
                self.check_cancel()
                if linear:
                    # PC supplies the time-based speed ramp. Acc=0 avoids a
                    # second driver ramp toward every short intermediate target.
                    self.motor.move_target(target, rpm, acceleration=0)
                else:
                    self.motor.move_target(target, rpm)
                self.emit('rpm', rpm)
                last_target, last_speed = target, rpm
            self.pause(self.c.poll_seconds)

    def jog(self, distance_mm, direction):
        """One exact relative move. Only the dedicated HOME action uses the sensor."""
        c = self.c
        distance_mm = parse_jog_distance(c, distance_mm)
        if direction not in (-1, 1):
            raise ValueError('잘못된 조그 방향')
        self.check_cancel()
        try:
            self.motor.ready()
            label = '상승' if direction == 1 else '하강'
            self.announce(f'조그 {label} {distance_mm:g} mm ({c.steps(distance_mm):,} steps) · 일반 {c.fast_rpm} RPM')
            # Short moves use a triangular profile; longer moves reach general RPM.
            peak = min(c.fast_rpm, math.sqrt(1 + distance_mm * c.ramp_rpm_per_second * 60 / c.mm_per_rev))
            braking = c.ramp_distance(peak, 1) + peak * c.mm_per_rev / 60 * c.poll_seconds
            self.move(distance_mm, direction,
                      lambda progress: 1 if distance_mm - progress <= braking else peak,
                      sensor_target=None, linear=True)
            self.announce('조그 이동 완료')
        finally:
            self.stop_motion()

    def homing(self):
        c = self.c
        self.home_position = None
        self.announce('초기 정밀 원점 찾기 — 이전 센서 탐색 방식')
        if self.sample()['home_active']:
            self.announce(f'센서 해제: 최대 {c.steps(c.home_escape_mm):,} steps 하강')
            if not self.move(c.home_escape_mm, -1, c.rpm(c.home_fine_delay_us), sensor_target=False):
                raise ControlError('최대 해제 거리 내에서 원점 센서가 해제되지 않았습니다.')
            self.pause(0.3)
        self.announce(f'처음 {c.steps(c.home_start_mm):,} steps 저속 상승 탐색')
        detected = self.move(c.home_start_mm, 1, c.rpm(c.home_start_delay_us), sensor_target=True)
        if not detected:
            self.announce(f'원점 일반 탐색: 최대 {c.steps(c.max_home_search_mm):,} steps 추가 상승')
            detected = self.move(c.max_home_search_mm, 1, c.rpm(c.home_seek_delay_us), sensor_target=True)
        if not detected:
            raise ControlError('탐색 거리 내에서 원점을 찾지 못했습니다.')
        self.pause(0.3)
        self.announce(f'{c.steps(c.home_backoff_mm):,} steps 하강 후퇴')
        self.move(c.home_backoff_mm, -1, c.rpm(c.home_fine_delay_us))
        self.pause(0.3)
        if self.sample()['home_active']:
            raise ControlError('후퇴 후에도 센서가 감지 상태입니다.')
        self.announce(f'정밀 원점 접근: 최대 {c.steps(c.home_fine_search_mm):,} steps 상승')
        if not self.move(c.home_fine_search_mm, 1, c.rpm(c.home_fine_delay_us), sensor_target=True):
            raise ControlError('정밀 원점 탐색에 실패했습니다.')
        self.home_position = self.sample()['position']
        self.emit('home', self.home_position)
        self.announce('원점 설정 완료 — 반복 횟수 0')
        self.pause(0.5)

    def move_down(self):
        c = self.c
        self.announce(f'{c.steps(c.target_distance_mm):,} steps 하강 — PC 목표 갱신 / 선형 가감속')
        self.move(c.target_distance_mm, -1, c.down_rpm, linear=True)
        self.pause(0.5)

    def move_up(self):
        c = self.c
        slow = c.rpm(c.home_start_delay_us)
        approach = min(c.home_approach_mm, c.target_distance_mm)
        braking_start = approach + c.ramp_distance(c.fast_rpm, slow) + c.deceleration_advance_mm()
        self.announce(f'{c.steps(c.target_distance_mm):,} steps 상승 — 센서 미사용 / 선형 가감속')
        self.move(c.target_distance_mm, 1,
                  lambda progress: slow if c.target_distance_mm-progress <= braking_start else c.fast_rpm,
                  sensor_target=None, linear=True)

    def run(self):
        self.check_cancel()
        self.motor.ready()
        self.completed = 0
        self.emit('count', 0)
        try:
            self.pause(1)
            self.homing()
            self.pause(1)
            for index in range(self.c.repeat_count):
                self.emit('cycle', index + 1)
                self.move_down()
                self.pause(1)
                self.move_up()
                self.completed += 1
                self.emit('count', self.completed)
                self.announce(f'{self.completed}회 완료')
                self.pause(1)
            self.announce(f'전체 {self.completed}회 완료 — 정지 및 위치 유지')
        finally:
            self.stop_motion()
