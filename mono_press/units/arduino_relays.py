"""P06 replies multiplexed with P00 sensor lines on the same Uno serial port."""
import re
import time


class ArduinoRelays:
    def __init__(self):
        self.sequence = 0
        self.reset()

    def reset(self):
        self.ready = False
        self.mask = None
        self.level = None
        self.pending = None
        self.ack = None
        self.last_seen = 0
        self.last_keep = 0
        self.keepalive = False

    def consume(self, line):
        if not line.startswith('P06 '):
            return False
        if line == 'P06 READY 1':
            self.ready = True
            self.last_seen = time.monotonic()
            return True
        match = re.fullmatch(r'P06 (ACK|STATE) ([0-9]{1,10}) (LOW|HIGH) ([0-7])', line)
        if match:
            kind, seq, level, mask = match.groups()
            reply = (int(seq), level, int(mask))
            self.ready = True
            self.last_seen = time.monotonic()
            if kind == 'STATE' or self.pending == reply:
                self.mask, self.level = reply[2], level
            if kind == 'ACK' and self.pending == reply:
                self.ack = reply
                self.keepalive = bool(self.mask)
            elif kind == 'STATE' and self.mask == 0:
                self.keepalive = False
        # Relay errors must not be treated as corrupt P00 sensor packets.
        return True

    def request(self, device, mask, level):
        if level not in ('LOW', 'HIGH') or not isinstance(mask, int) or not 0 <= mask <= 7:
            raise ValueError('릴레이 극성과 출력 값 오류')
        if not self.ready:
            raise RuntimeError('Uno에 P06 릴레이 통합 펌웨어를 먼저 적용하세요.')
        if self.level is not None and self.level != level:
            raise RuntimeError(f'Uno 펌웨어는 {self.level} 동작입니다. P06 극성 설정을 맞추세요.')
        self.sequence = self.sequence % 2147483647 + 1
        self.pending = (self.sequence, level, mask)
        self.ack = None
        self.keepalive = False
        device.write(f'P06 SET {self.sequence} {level} {mask}\n'.encode('ascii'))
        return self.pending

    def tick(self, device):
        now = time.monotonic()
        if self.ready and now - self.last_seen > 3:
            self.reset()
        if device is not None and self.keepalive and self.ack and now - self.last_keep >= .5:
            device.write(f'P06 KEEP {self.ack[0]}\n'.encode('ascii'))
            self.last_keep = now

    def abandon(self, device):
        self.keepalive = False
        self.pending = None
        self.ack = None
        self.mask = None
        if device is not None:
            try:
                device.write(b'P06 OFF\n')
            except Exception:
                pass
