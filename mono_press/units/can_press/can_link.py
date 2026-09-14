"""candleLight / MKS SERVO57D, classic CAN; all USB I/O stays on one thread."""
from __future__ import annotations

import logging
import struct
import sys
import time
from pathlib import Path

from .control_logic import Cancelled

DEPS = Path(__file__).resolve().parent / 'deps'
if DEPS.is_dir():
    sys.path.insert(0, str(DEPS))


class CanError(RuntimeError):
    pass


def packet(node: int, payload: list[int]) -> bytes:
    if not 1 <= node <= 2047 or not 1 <= len(payload) <= 7:
        raise ValueError('잘못된 CAN ID 또는 데이터 길이')
    return bytes([*payload, (node + sum(payload)) & 255])


def position_payload(target: int, rpm: int, acceleration: int) -> list[int]:
    if not -(2**23) < target < 2**23:
        raise CanError('모터 좌표가 CAN 위치 명령의 24비트 범위를 벗어났습니다.')
    if not 1 <= rpm <= 100 or not 0 <= acceleration <= 255:
        raise CanError('이 프로그램은 1~100 RPM 위치 이동만 허용합니다.')
    return [0xF5, *rpm.to_bytes(2, 'big'), acceleration,
            *target.to_bytes(3, 'big', signed=True)]


class CanLink:
    def __init__(self, config):
        import usb.core
        import usb.util
        import libusb_package
        from gs_usb.gs_usb import GsUsb
        from gs_usb.gs_usb_frame import GsUsbFrame

        self.usb = usb
        self.Frame = GsUsbFrame
        self.config = config
        self.node = config.can_id
        self.raw = None
        self.adapter = None
        self.kernel_detached = False
        self.motion_error = None
        self.home_status = None
        self.tx_sequence = 0
        self.cancel_requested = lambda: False
        self.stopping = False
        self.controller_notices = 0
        devices = list(libusb_package.find(find_all=True, idVendor=0x1D50, idProduct=0x606F))
        if len(devices) != 1:
            raise CanError(f'candleLight USB CAN을 1개 연결하세요. 현재 {len(devices)}개입니다.')
        self.raw = devices[0]
        try:
            # Linux normally binds candleLight to gs_usb. Own the interface
            # only while this application is connected, then restore it.
            if sys.platform.startswith('linux') and self.raw.is_kernel_driver_active(0):
                self.raw.detach_kernel_driver(0)
                self.kernel_detached = True
            usb.util.claim_interface(self.raw, 0)
            self.adapter = GsUsb(self.raw)
            if not self.adapter.device_capability.feature & 8:
                raise CanError('USB CAN의 one-shot 전송 기능이 필요합니다.')
            self.raw.ctrl_transfer(0x41, 0, 0, 0, struct.pack('<I', 0xBEEF))
            self.adapter.stop()
            if not self.adapter.set_bitrate(config.bitrate):
                raise CanError('USB CAN이 요청한 통신 속도를 지원하지 않습니다.')
            self.raw.ctrl_transfer(0x41, 2, 0, 0, struct.pack('<II', 1, 8))
        except BaseException:
            self.close()
            raise

    def _send(self, payload):
        self.tx_sequence = (self.tx_sequence + 1) & 0x7FFFFFFF
        data = packet(self.node, payload)
        frame = self.Frame(can_id=self.node, data=list(data))
        frame.echo_id = self.tx_sequence
        self.raw.write(0x02, frame.pack(False), timeout=150)
        if payload[0] in (0x90, 0x91, 0x92, 0xF5, 0xF7):
            logging.info('TX id=%s %s', self.node, data.hex(' '))

    def _receive(self, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._check_cancel()
            milliseconds = max(1, min(20, int((deadline - time.monotonic()) * 1000)))
            try:
                raw = self.raw.read(0x81, 20, timeout=milliseconds)
            except self.usb.core.USBTimeoutError:
                continue
            frame = self.Frame()
            self.Frame.unpack_into(frame, raw, False)
            if frame.echo_id != 0xFFFFFFFF:
                continue  # Adapter echo is not a motor response.
            if frame.is_error_frame:
                detail = bytes(frame.data[:frame.can_dlc])
                # This candleLight emits CRTL/UNSPEC with eight zero bytes even
                # while healthy replies follow. It is not a motor ACK. Keep
                # waiting within the original deadline; never retry a motion.
                # Explicit ACTIVE recovery is likewise only a status notice.
                if (frame.can_id == 0x20000004 and len(detail) == 8
                        and detail in (bytes(8), b'\x00\x40' + bytes(6))):
                    self.controller_notices = getattr(self, 'controller_notices', 0) + 1
                    if self.controller_notices == 1:
                        logging.info('CAN 컨트롤러 상태 알림 · 모터의 유효 응답을 별도로 확인합니다.')
                    continue
                raise CanError(f'CAN 오류 프레임 ID=0x{frame.can_id:08X} DATA={detail.hex(" ")} · 배선/종단/전원을 확인하세요.')
            if frame.arbitration_id != self.node or frame.is_extended_id or frame.is_remote_frame:
                continue
            body = bytes(frame.data[:frame.can_dlc])
            if len(body) < 3 or (self.node + sum(body[:-1])) & 255 != body[-1]:
                raise CanError('모터 응답의 체크섬 또는 길이가 올바르지 않습니다.')
            if body[0] in (0x90, 0x91, 0x92, 0xF5, 0xF7):
                logging.info('RX id=%s %s', self.node, body.hex(' '))
            self._record_response(body)
            return body
        return None

    def _record_response(self, body):
        # Completion may arrive while request() is waiting for an IO/position
        # reply. Retain it so it cannot disappear between controller polls.
        if body[0] == 0x91:
            if len(body) != 3 or body[1] not in (0, 1, 2):
                raise CanError('잘못된 드라이버 호밍 응답')
            self.home_status = body[1]
        if body[0] == 0xF5:
            if len(body) != 3:
                raise CanError('잘못된 위치 명령 응답')
            if body[1] in (0, 3):
                self.motion_error = ('위치 명령 거부' if body[1] == 0 else '드라이버 리미트 정지')

    def _check_cancel(self):
        if not self.stopping and self.cancel_requested():
            raise Cancelled('PRESS STOPPED')

    def request(self, payload, expected=None):
        self._check_cancel()
        expected = payload[0] if expected is None else expected
        self._send(payload)
        deadline = time.monotonic() + self.config.can_timeout_seconds
        while time.monotonic() < deadline:
            body = self._receive(min(0.02, deadline - time.monotonic()))
            if body is not None and body[0] == expected:
                return body
        raise CanError(f'CAN 응답 시간 초과: ID {self.node}, 명령 {payload[0]:02X}')

    def read(self, command):
        sizes = {0x31: 8, 0x32: 4, 0x34: 3, 0x3A: 3, 0x3E: 3, 0xF1: 3}
        if command not in sizes:
            raise ValueError('지원하지 않는 조회 명령')
        body = self.request([command])
        if len(body) != sizes[command]:
            raise CanError(f'응답 길이 오류: {command:02X}')
        return int.from_bytes(body[1:-1], 'big', signed=command in (0x31, 0x32))

    def param(self, code):
        body = self.request([0, code], expected=code)
        if body[1:-1] == b'\xff\xff':
            raise CanError(f'모터 펌웨어가 설정 조회 {code:02X}를 지원하지 않습니다.')
        return list(body[1:-1])

    def sample(self):
        # Sensor first: do not let a slow position query postpone its observation.
        io = self.read(0x34)
        position = self.read(0x31)
        if self.motion_error:
            raise CanError(self.motion_error)
        return {'io': io, 'position': position}

    def begin_motion(self):
        # Consume any late completion from the previous (stopped) segment first.
        while self._receive(0.002) is not None:
            pass
        self.motion_error = None

    def configure_home(self):
        c = self.config
        # 90 uses 0=CW, 1=CCW (opposite to F6's direction bit).
        # Positive axis counts are CCW; this installation's positive axis is UP.
        rpm = c.rpm(c.home_start_delay_us)
        values = [0 if c.home_active_low else 1, 1 if c.up_sign == 1 else 0,
                  *rpm.to_bytes(2, 'big'), 0, 0]
        # EndLimit=0 retains shaft holding after GoHome. hmMode=0 uses IN_1,
        # never the sensorless/stall mode. Only write changed settings.
        for code, wanted in ((0x8C, [1, 1]), (0x90, values)):
            current = self.param(code)
            if len(current) != len(wanted):
                raise CanError(f'지원하지 않는 호밍 설정 형식: {code:02X} {current}')
            if current != wanted:
                body = self.request([code, *wanted])
                if len(body) != 3 or body[1] != 1 or self.param(code) != wanted:
                    raise CanError(f'드라이버 설정 적용 실패: {code:02X}')

    def start_homing(self):
        self.begin_motion()
        self.home_status = None
        body = self.request([0x91])
        self._record_response(body)
        if self.home_status == 0:
            raise CanError('드라이버가 센서 호밍을 거부했습니다.')

    def zero_axis(self):
        body = self.request([0x92])
        if len(body) != 3 or body[1] != 1:
            raise CanError('드라이버 원점 좌표 설정 실패')

    def move_target(self, target, rpm, acceleration=None):
        if self.motion_error:
            raise CanError(self.motion_error)
        acc = self.config.acceleration if acceleration is None else acceleration
        body = self.request(position_payload(target, rpm, acc))
        if body[1] not in (1, 2):
            raise CanError('모터가 위치 이동을 거부했거나 리미트에 도달했습니다.')

    def stop(self):
        # Cancellation interrupts ordinary CAN response waits, but F7/F1 must
        # still be allowed to run to completion while cancellation is set.
        self.stopping = True
        try:
            return self._stop()
        finally:
            self.stopping = False

    def _stop(self):
        # Keep torque enabled on this vertical axis; never automatically release EN.
        body = self.request([0xF7])
        if len(body) != 3 or body[1] not in (0, 1):
            raise CanError(f'알 수 없는 정지 명령 응답: {body.hex(" ")}')
        # F7=00 occurs when this drive is already stopped. Use its stopped
        # state, not tiny closed-loop encoder corrections, to decide completion.
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if self.stationary():
                logging.info('STOP: response=%02x, motor state=STOPPED', body[1])
                return
            time.sleep(0.025)
        raise CanError(f'정지 응답 {body.hex(" ")}: 모터가 정지 상태로 전환되지 않았습니다.')

    def stationary(self, sleep=time.sleep):
        # F1=1 is the documented stopped state. Holding-position corrections
        # and a lagging RPM estimate must not abort a successful sensor stop.
        return self.read(0xF1) == 1

    def ready(self):
        if self.param(0x82) not in ([3], [4], [5]):
            raise CanError('모터의 Mode를 SR_vFOC 등 SR 모드로 설정하세요.')
        if self.param(0x8C)[0] != 1:
            raise CanError('모터의 CanRSP 응답 기능을 Enable로 설정하세요.')
        if self.param(0x9E) != [0]:
            raise CanError('입력 리매핑이 켜져 있습니다. 이 프로그램은 실제 IN_1을 사용합니다.')
        if self.read(0x3A) != 1:
            raise CanError('모터가 비활성 상태입니다. 모터의 EN 설정을 확인하세요.')
        if self.read(0x3E):
            raise CanError('모터 보호 상태입니다. 원인을 먼저 확인하세요.')
        if not self.stationary():
            raise CanError('모터가 정지한 상태에서 시작하세요.')

    def close(self):
        try:
            if self.adapter is not None:
                self.adapter.stop()  # Stops USB interface, not the motor.
        finally:
            if self.raw is not None:
                try:
                    self.usb.util.release_interface(self.raw, 0)
                    if self.kernel_detached:
                        self.raw.attach_kernel_driver(0)
                        self.kernel_detached = False
                finally:
                    self.usb.util.dispose_resources(self.raw)
                    self.raw = None
