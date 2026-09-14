from __future__ import annotations

import threading
import time
from collections import deque
from .uart_framing import MarkedInputDecoder, mark_input_errors, frame_error_count
from .servo42c_protocol import checksum, packet as command_packet

try:
    import serial
except ImportError:
    serial = None


class ServoBusController:
    """Single owner of /dev/ttyTHS1. TX is serialized; RX is continuously parsed."""

    TX_GAP_SECONDS = 0.050

    def __init__(self, state, port, baud, simulation=False, *, mark_errors=False):
        self.state = state
        self.port = port
        self.baud = baud
        self.simulation = simulation
        self.device = None
        self.tx_lock = threading.Lock()
        self.next_tx_at = 0.0
        self.connect_lock = threading.Lock()
        self.event_condition = threading.Condition()
        self.event_id = 0
        self.events = {address: deque(maxlen=80) for address in range(0xE0, 0xE4)}
        self.unit_by_address = {}
        self.reader_stop = threading.Event()
        self.reader_thread = None
        self.query_lock = threading.Lock()
        self.pending_queries = {}
        self.waiting_commands = set()
        self.moving_addresses = set()
        self.discarded_bytes = 0
        self.command_lock = threading.RLock()
        self.marked_input = False
        self.mark_errors = mark_errors  # Opt-in diagnostic; v7 raw RX is the default.
        self.kernel_frame_count = None
        self.framing_errors = 0
        self.last_diagnostics = time.monotonic()
        self.last_error_count = 0
        self.error_rate = 0.0
        self.probe_status = {}
        self.stop_generation = 0

    def bind(self, address, unit_id):
        self.unit_by_address[address] = unit_id

    def connect(self):
        if self.simulation:
            self.state.update_serial(connected=True, last_error="")
            return True
        if serial is None:
            self.state.update_serial(connected=False, last_error="pyserial이 설치되지 않았습니다.")
            return False
        with self.connect_lock:
            if self.device is not None and self.device.is_open and self.reader_thread and self.reader_thread.is_alive():
                return True
            try:
                for unit_id in self.unit_by_address.values():
                    self.state.update_unit(unit_id, homed=False)
                if self.device is not None: self.device.close()
                self.device = serial.Serial(
                    self.port, self.baud, bytesize=8, parity=serial.PARITY_NONE,
                    stopbits=1, timeout=0.05, write_timeout=0.5, exclusive=True,
                )
                self.marked_input = mark_input_errors(self.device) if self.mark_errors else False
                self.kernel_frame_count = frame_error_count(self.device)
                self.device.reset_input_buffer()  # Startup only; never during operation.
                self.reader_stop.clear()
                self.reader_thread = threading.Thread(target=self._reader_loop, name="servo-rx", daemon=True)
                self.reader_thread.start()
                self.state.update_serial(connected=True, last_error="", rx_mode='marked' if self.marked_input else 'v7_raw')
                self.state.record("SERIAL", f"{self.port} @ {self.baud} 연결")
                return True
            except Exception as exc:
                if self.device is not None:
                    try: self.device.close()
                    except Exception: pass
                self.device = None
                self.state.update_serial(connected=False, last_error=str(exc))
                self.state.record("ERROR", f"UART 연결 실패 · {exc}")
                return False

    def close(self):
        for unit_id in self.unit_by_address.values():
            self.state.update_unit(unit_id, homed=False)
        self.reader_stop.set()
        with self.connect_lock:
            device, self.device = self.device, None
            if device:
                try:
                    device.close()
                except Exception:
                    pass
        if self.reader_thread and self.reader_thread is not threading.current_thread():
            self.reader_thread.join(timeout=1)
        self.waiting_commands.clear()
        self.moving_addresses.clear()
        with self.event_condition:
            self.pending_queries.clear()
            self.event_condition.notify_all()
        self.state.update_serial(connected=self.simulation)

    def publish_diagnostics(self, force=False):
        now = time.monotonic()
        elapsed = now - self.last_diagnostics
        if (elapsed >= 1 or force) and not self.marked_input and self.device is not None:
            count = frame_error_count(self.device)
            if count is not None:
                if self.kernel_frame_count is not None:
                    self.framing_errors += max(0, count - self.kernel_frame_count)
                self.kernel_frame_count = count
        if elapsed >= 1:
            self.error_rate = (self.framing_errors - self.last_error_count) / elapsed
            self.last_diagnostics = now
            self.last_error_count = self.framing_errors
        if elapsed >= 1 or force:
            self.state.update_serial(rx_discarded=self.discarded_bytes, rx_frame_errors=self.framing_errors,
                                     rx_error_rate=round(self.error_rate, 1), error_marking=self.marked_input)

    def _parse_buffer(self, buffer):
        while len(buffer) >= 3:
            if buffer[0] not in self.events:
                del buffer[0]
                self.discarded_bytes += 1
                continue
            address = buffer[0]
            with self.event_condition:
                query = self.pending_queries.get(address)
            if query is not None:
                size = query.get('size', 4) + 2
                if len(buffer) < size: break
                packet = bytes(buffer[:size])
                if packet[-1] != checksum(packet[:-1]):
                    del buffer[0]; self.discarded_bytes += 1; continue
                if size == 3 and packet[1] not in (0, 1, 2):
                    del buffer[0]; self.discarded_bytes += 1; continue
                del buffer[:size]
                with self.event_condition:
                    # A later coincidental checksum must not overwrite the reply.
                    if query['data'] is None:
                        query['data'] = packet[1:-1]
                        self.event_condition.notify_all()
                continue
            frame = bytes(buffer[:3])
            if frame[2] != checksum(frame[:2]) or frame[1] not in (0, 1, 2):
                del buffer[0]; self.discarded_bytes += 1; continue
            del buffer[:3]
            if address not in self.waiting_commands and address not in self.moving_addresses:
                self.discarded_bytes += 3
                continue
            self._dispatch(frame[0], frame[1])
            if frame[1] == 2: self.moving_addresses.discard(address)

    def _reader_loop(self):
        buffer = bytearray()
        decoder = MarkedInputDecoder()
        while not self.reader_stop.is_set():
            try:
                if self.device is None:
                    return
                chunk = self.device.read(32)
                self.publish_diagnostics()
                if not chunk:
                    continue
                for value in decoder.decode(chunk) if self.marked_input else chunk:
                    if value is None:
                        self.framing_errors += 1
                        self.discarded_bytes += len(buffer) + 1
                        buffer.clear()
                    else:
                        buffer.append(value)
                        self._parse_buffer(buffer)
            except Exception as exc:
                if not self.reader_stop.is_set():
                    self.state.update_serial(connected=False, last_error=str(exc))
                    self.state.record("ERROR", f"UART RX 오류 · {exc}")
                return

    def _dispatch(self, address, status):
        with self.event_condition:
            self.event_id += 1
            event = {"id": self.event_id, "status": status, "at": time.monotonic()}
            self.events[address].append(event)
            self.event_condition.notify_all()
        label = "START" if status == 1 else "COMPLETE" if status == 2 else f"FAIL 0x{status:02X}"
        unit_id = self.unit_by_address.get(address)
        if unit_id:
            self.state.update_unit(unit_id, connected=status in (1, 2), last_event=label, last_ack=time.time())
        self.state.record("RX", f"E{address & 0x0F} {label}", "P01", unit_id)

    def wait_event(self, address, expected, after, timeout, cancel=None):
        deadline = time.monotonic() + timeout
        expected_values = set(expected) if isinstance(expected, (tuple, list, set)) else {expected}
        with self.event_condition:
            while True:
                if cancel is not None and cancel.is_set():
                    return None
                for event in self.events[address]:
                    if event["id"] > after and (event["status"] in expected_values or (1 in expected_values and event["status"] == 0)):
                        return event
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self.event_condition.wait(min(0.1, remaining))

    def write(self, address, body, label):
        if not self.connect():
            return False, self.event_id, self.state.serial["last_error"]
        packet = command_packet(address, body)
        text = " ".join(f"{value:02X}" for value in packet)
        with self.tx_lock:
            if not self.simulation:
                # One shared deadline across every axis, query and command.
                # Measure idle time from the previous flush, not its start.
                remaining = self.next_tx_at - time.monotonic()
                if remaining > 0:
                    time.sleep(remaining)
            with self.event_condition:
                watermark = self.event_id
            try:
                self.state.record("TX", f"E{address & 0x0F} {label} · {text}", "P01", self.unit_by_address.get(address))
                if self.simulation:
                    self._dispatch(address, 1)
                    if body and body[0] == 0xFD:
                        pulses = int.from_bytes(bytes(body[2:6]), "big")
                        delay = min(1.0, max(0.12, pulses / 80000.0))
                        threading.Timer(delay, self._dispatch, args=(address, 2)).start()
                else:
                    try:
                        self.device.write(packet)
                        self.device.flush()
                    finally:
                        # A failed write may have transmitted part of a packet.
                        self.next_tx_at = time.monotonic() + self.TX_GAP_SECONDS
                return True, watermark, text
            except Exception as exc:
                self.state.update_serial(connected=False, last_error=str(exc))
                return False, watermark, str(exc)

    def command(self, address, body, label, timeout, cancel=None):
        with self.command_lock:
            # Only state-setting commands are idempotent. Never retry relative moves.
            attempts = 2 if body and body[0] in (0xF3, 0xF7) else 1
            for attempt in range(attempts):
                if cancel is not None and cancel.is_set():
                    return False, 'CANCELLED', self.event_id
                result = self._command_once(address, body, label, timeout, cancel)
                if result[0] or result[1] != 'NO_RESPONSE': return result
            return result

    def _command_once(self, address, body, label, timeout, cancel=None):
        if body and body[0] == 0xF7: self.moving_addresses.discard(address)
        self.waiting_commands.add(address)
        if body and body[0] == 0xFD: self.moving_addresses.add(address)
        sent, watermark, detail = self.write(address, body, label)
        if not sent:
            self.waiting_commands.discard(address)
            self.moving_addresses.discard(address)
            return False, detail, watermark
        expected = (1, 2) if body and body[0] == 0xFD else 1
        event = self.wait_event(address, expected, watermark, timeout, cancel)
        self.waiting_commands.discard(address)
        if cancel is not None and cancel.is_set():
            self.moving_addresses.discard(address)
            return False, 'CANCELLED', watermark
        if event is None or event['status'] == 0:
            self.moving_addresses.discard(address)
            unit_id = self.unit_by_address.get(address)
            error = 'NO_RESPONSE' if event is None else 'COMMAND_REJECTED'
            if unit_id:
                self.state.update_unit(unit_id, connected=False, enabled=False, homed=False, state=error, error=error)
            return False, error, watermark
        return True, "COMPLETE" if event['status'] == 2 else "ACK", event["id"]

    def _query(self, address, command, size, timeout):
        query = {'data': None, 'size': size}
        with self.event_condition: self.pending_queries[address] = query
        try:
            sent, _, detail = self.write(address, [command], f'READ 0x{command:02X}')
            if not sent: return None
            deadline = time.monotonic() + timeout
            with self.event_condition:
                while query['data'] is None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or self.reader_stop.is_set() or query.get('cancelled'): return None
                    self.event_condition.wait(min(.1, remaining))
            return query['data']
        finally:
            with self.event_condition: self.pending_queries.pop(address, None)

    def probe(self, address, timeout=.7):
        """Confirm EN twice without making optional angle telemetry a prerequisite."""
        if self.simulation: return True, 'SIM 응답'
        with self.command_lock, self.query_lock:
            stop_generation = self.stop_generation
            self.probe_status.pop(address, None)
            if not self.connect(): return False, self.state.serial.get('last_error', 'UART 연결 실패')
            if self.waiting_commands or self.moving_addresses: return False, 'P01 명령 처리 중'
            statuses = []
            for _ in range(4):
                raw = self._query(address, 0x3A, 1, timeout)
                if self.stop_generation != stop_generation: return False, 'STOP 요청으로 통신 확인 중단'
                if raw is None or raw[0] not in (1, 2): continue
                if raw[0] in statuses:
                    enabled = raw[0] == 1
                    self.probe_status[address] = {'enabled': enabled}
                    self.publish_diagnostics(force=True)
                    return True, f'EN 반복 응답 확인 · {"활성" if enabled else "비활성"}'
                statuses.append(raw[0])
            return False, 'NO_RESPONSE · EN 상태 응답 불안정'

    def emergency_stop(self, address):
        with self.event_condition:
            self.stop_generation += 1
            for query in self.pending_queries.values(): query['cancelled'] = True
            self.event_condition.notify_all()
        self.moving_addresses.discard(address)
        return self.write(address, [0xF7], "STOP")[0]
