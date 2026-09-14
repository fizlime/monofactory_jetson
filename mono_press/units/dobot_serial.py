"""Dobot Magician protocol 1.1.5. All movement commands are non-queued.

Reference: https://download.dobot.cc/product-manual/dobot-magician/pdf/en/Dobot-Communication-Protocol-V1.1.5.pdf
"""
import math
import struct
import threading
import time


class DobotSerial:
    def __init__(self, port, serial_factory=None):
        if not port:
            raise ValueError("Dobot USB 포트를 지정하세요.")
        if serial_factory is None:
            import serial
            serial_factory = serial.Serial
        self.lock = threading.RLock()
        self.serial = serial_factory(port=None, baudrate=115200, timeout=0.05,
                                     write_timeout=0.5, exclusive=True)
        self.serial.dtr = False
        self.serial.rts = False
        self.serial.port = port
        self.serial.open()

    def close(self):
        with self.lock:
            self.serial.close()

    def request(self, command, params=b"", write=False, timeout=1.0):
        # Never retry a write: a lost acknowledgement may still mean it moved.
        payload = bytes((command, int(write))) + params
        frame = b"\xaa\xaa" + bytes((len(payload),)) + payload + bytes((-sum(payload) & 255,))
        with self.lock:
            self.serial.reset_input_buffer()
            self.serial.write(frame)
            deadline = time.monotonic() + timeout
            buffer = bytearray()
            while time.monotonic() < deadline:
                buffer.extend(self.serial.read(max(1, self.serial.in_waiting)))
                while len(buffer) >= 3:
                    if buffer[:2] != b"\xaa\xaa" or buffer[2] < 2:
                        del buffer[0]
                        continue
                    length = buffer[2]
                    if len(buffer) < length + 4:
                        break
                    packet = bytes(buffer[3:3 + length])
                    checksum = buffer[3 + length]
                    del buffer[:length + 4]
                    if (sum(packet) + checksum) & 255:
                        continue
                    if packet[:2] == payload[:2]:
                        return packet[2:]
            raise TimeoutError(f"Dobot 응답 없음 (명령 {command})")

    def pose(self, timeout=1.0):
        data = self.request(10, timeout=timeout)
        if len(data) != 32:
            raise RuntimeError("Dobot 위치 응답 길이 오류")
        values = list(struct.unpack("<8f", data))
        if not all(math.isfinite(v) for v in values):
            raise RuntimeError("Dobot 위치 응답 오류")
        return values[:4], values[4:]

    def alarms(self):
        data = self.request(20)
        if len(data) != 16:
            raise RuntimeError("Dobot 알람 응답 길이 오류")
        return [i * 8 + bit for i, byte in enumerate(data) for bit in range(8) if byte & (1 << bit)]

    def move(self, mode, values, speed):
        if mode != "JOINT":
            raise ValueError("Dobot 이동은 JOINT J1~J4 각도가 필요합니다.")
        self.request(83, struct.pack("<2f", speed, speed), write=True)
        self.request(84, struct.pack("<B4f", 4, *values), write=True)

    def stop(self):
        self.request(242, write=True)

    def gripper(self, closed):
        self.request(63, bytes((1, int(closed))), write=True)

    def stop_gripper(self):
        # OPEN still powers the pump. Disable its output, then read it back.
        self.request(63, b"\x00\x00", write=True)
        data = self.request(63)
        if len(data) != 2 or data[0] != 0:
            raise RuntimeError("그리퍼 구동 OFF 확인 실패")

    def home_params(self):
        data = self.request(30)
        if len(data) != 16:
            raise RuntimeError("Dobot HOME 위치 응답 길이 오류")
        values = list(struct.unpack("<4f", data))
        if not all(math.isfinite(v) for v in values):
            raise RuntimeError("Dobot HOME 위치 응답 오류")
        return values

    def start_home(self):
        # Official DobotLink encodes command 31 without the obsolete reserved
        # field. This form was confirmed on the installed Magician firmware.
        self.request(31, write=True)
