"""Single serial owner for Uno P00 sensor input and P06 relay replies."""
import re
import time

import serial
from .arduino_relays import ArduinoRelays


def parse_sensor_line(line):
    if line == '센서 4개 확인 시작':
        return None
    match = re.fullmatch(r'센서\s*1\s*:\s*(감지|미감지)\s*\|\s*센서\s*2\s*:\s*(감지|미감지)\s*\|\s*센서\s*3\s*:\s*(감지|미감지)\s*\|\s*센서\s*4\s*:\s*(감지|미감지)', line)
    if not match:
        raise ValueError('Arduino 센서 4채널 응답 형식 오류')
    return [value == '감지' for value in match.groups()]


class ArduinoMaterialInput:
    def __init__(self):
        self.device = None
        self.key = None
        self.buffer = bytearray()
        self.values = None
        self.last_frame = None
        self.next_connect = 0
        self.generation = 0
        self.received_at = 0
        self.last_line = ''
        self.frame_count = 0
        self.invalid_frames = 0
        self.data_deadline = 0
        self.relays = ArduinoRelays()

    def invalidate(self):
        self.values = None
        self.last_frame = None
        self.received_at = 0
        self.generation += 1

    def close(self):
        if self.relays.keepalive:
            self.relays.abandon(self.device)
        self.relays.reset()
        if self.device is not None:
            try:
                self.device.close()
            except Exception:
                pass
        self.device = None
        self.buffer.clear()
        self.invalidate()

    def read(self, cfg):
        now = time.monotonic()
        key = (cfg['serial_port'], cfg['serial_baud'])
        if key != self.key:
            self.close()
            self.key = key
            self.next_connect = 0
        if self.device is None:
            if now < self.next_connect:
                raise RuntimeError('Arduino 재연결 대기')
            self.next_connect = now + 1
            try:
                self.device = serial.Serial(*key, timeout=0, write_timeout=.5, exclusive=True)
            except serial.SerialException as exc:
                raise RuntimeError(f'Arduino USB 연결 실패 · {key[0]} · {exc}') from exc
            self.data_deadline = now + max(3, cfg['stale_timeout'])
        fresh = False
        try:
            self.buffer.extend(self.device.read(min(4096, self.device.in_waiting)))
        except Exception:
            self.close()
            self.next_connect = now + 1
            raise
        try:
            if len(self.buffer) > 4096:
                self.buffer.clear()
                raise ValueError('Arduino 수신 버퍼 초과')
            while b'\n' in self.buffer:
                raw, _, remaining = self.buffer.partition(b'\n')
                self.buffer = bytearray(remaining)
                line = raw.decode('utf-8').strip()
                if self.relays.consume(line):
                    continue
                values = parse_sensor_line(line)
                if values is None:
                    # A reboot must revoke old permission, even if the same read
                    # also contains the first complete frame after startup.
                    self.invalidate()
                    self.relays.reset()
                    fresh = False
                    self.data_deadline = now + max(3, cfg['stale_timeout'])
                else:
                    self.values = values
                    self.last_frame = now
                    self.received_at = time.time()
                    self.last_line = line
                    self.frame_count += 1
                    self.data_deadline = now + cfg['stale_timeout']
                    fresh = True
        except (ValueError, UnicodeError):
            # Do not reopen/reset the Uno for a partial startup or bad line.
            # Revoke permission and recover from the next complete valid frame.
            self.invalid_frames += 1
            self.invalidate()
            if now > self.data_deadline:
                self.close()
                self.next_connect = now + 1
            raise
        try:
            self.relays.tick(self.device)
        except Exception:
            self.close()
            self.next_connect = now + 1
            raise
        if now > self.data_deadline:
            self.close()
            self.next_connect = now + 1
            raise RuntimeError('Arduino 센서 데이터 시간 초과 · 투입 차단')
        if self.last_frame is None:
            raise RuntimeError('Arduino USB 연결됨 · 센서 데이터 대기')
        return list(self.values), fresh
