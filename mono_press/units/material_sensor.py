import subprocess
import threading
import time

from .base import UnitBase
from .arduino_material import ArduinoMaterialInput


class MaterialSensor(UnitBase):
    """Process permission input, never a replacement for an emergency stop."""
    def __init__(self, state, config_store, simulation):
        self.config_store = config_store
        self.guard = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.sim_present = True
        self.since = None
        self.config_key = None
        self.arduino = ArduinoMaterialInput()
        self.arduino_generation = None
        self.relay_observer = None
        self.channel_since = [None] * 4
        self.channel_detected = [False] * 4
        super().__init__(state, 'P00_MATERIAL', 'P00', 'MATERIAL_SENSOR',
                         'E-MSMLS61N-2M 자재 감지', simulation,
                         detected=bool(simulation), connected=bool(simulation),
                         state='DETECTED' if simulation else 'UNCONFIGURED', last_sample=0,
                         channels=[], detected_count=4 if simulation else 0, required_count=4)

    def connect(self):
        self.refresh()
        if not self.thread or not self.thread.is_alive():
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._poll, name='material-input', daemon=True)
            self.thread.start()

    def _poll(self):
        while not self.stop_event.wait(.1): self.refresh()

    def close(self):
        self.stop_event.set()
        if self.thread: self.thread.join(1)
        with self.guard: self.arduino.close()

    def set_mode(self, simulation):
        with self.guard:
            self.simulated = bool(simulation)
            self.since = None
            self.channel_since = [None] * 4
            self.channel_detected = [False] * 4
            self.arduino.close()
            self.refresh()

    def set_simulated(self, present):
        if not self.simulated: return False, 'REAL 센서 값은 소프트웨어로 변경할 수 없습니다.'
        self.sim_present = bool(present)
        self.refresh()
        return True, 'SIM 자재 감지 변경'

    def refresh(self):
        with self.guard:
            cfg = self.config_store.snapshot()['p00']
            key = tuple(sorted(cfg.items()))
            if key != self.config_key:
                self.since = None
                self.channel_since = [None] * 4
                self.channel_detected = [False] * 4
                self.arduino.close()
                self.config_key = key
            now = time.monotonic()
            try:
                if self.simulated:
                    present = self.sim_present
                    detected = present
                    values = [detected] * 4
                elif cfg['driver'] == 'arduino_serial':
                    raw_values, fresh = self.arduino.read(cfg)
                    if self.arduino_generation != self.arduino.generation:
                        self.channel_since = [None] * 4
                        self.channel_detected = [False] * 4
                        self.arduino_generation = self.arduino.generation
                    if fresh:
                        for i, present in enumerate(raw_values):
                            self.channel_since[i] = (self.channel_since[i] if self.channel_since[i] is not None else now) if present else None
                            self.channel_detected[i] = present and now - self.channel_since[i] >= cfg['debounce_ms'] / 1000
                    values = list(self.channel_detected)
                    detected = all(values)
                else:
                    if cfg['driver'] != 'gpio': raise RuntimeError('센서 입력 미설정 · 배선 후 GPIO 입력을 선택하세요.')
                    # Existing libgpiod v1 CLI; no shell, output direction or arbitrary command.
                    raw = subprocess.run(['gpioget', cfg['gpio_chip'], str(cfg['gpio_line'])],
                                         capture_output=True, text=True, timeout=.5, check=True).stdout.strip()
                    if raw not in ('0', '1'): raise RuntimeError('GPIO 입력 응답 오류')
                    present = (raw == '0') if cfg['active_low'] else (raw == '1')
                    self.since = (self.since if self.since is not None else now) if present else None
                    detected = present and now - self.since >= cfg['debounce_ms'] / 1000
                    values = [detected] * 4
                channels = [dict(sensor=i+1, pin=i+2, detected=value) for i, value in enumerate(values)]
                arduino_real = not self.simulated and cfg['driver'] == 'arduino_serial'
                self.update(connected=True, detected=detected, simulated=self.simulated,
                            state='DETECTED' if detected else 'WAITING', error='',
                            last_sample=self.arduino.received_at if arduino_real else time.time(),
                            transport_connected=arduino_real and self.arduino.device is not None,
                            serial_port=cfg['serial_port'] if arduino_real else '',
                            serial_baud=cfg['serial_baud'] if arduino_real else 0,
                            received_frames=self.arduino.frame_count if arduino_real else 0,
                            invalid_frames=self.arduino.invalid_frames if arduino_real else 0,
                            channels=channels, detected_count=sum(values), required_count=4)
            except Exception as exc:
                self.since = None
                self.channel_since = [None] * 4
                self.channel_detected = [False] * 4
                transport = cfg['driver'] == 'arduino_serial' and self.arduino.device is not None
                self.update(connected=False, detected=False, simulated=False, transport_connected=transport,
                            serial_port=cfg['serial_port'] if cfg['driver'] == 'arduino_serial' else '',
                            serial_baud=cfg['serial_baud'] if cfg['driver'] == 'arduino_serial' else 0,
                            received_frames=self.arduino.frame_count, invalid_frames=self.arduino.invalid_frames,
                            state='UNCONFIGURED' if cfg['driver'] == 'unconfigured' else 'WAITING_DATA' if transport else 'OFFLINE', error=str(exc), last_sample=0,
                            channels=[dict(sensor=i+1, pin=i+2, detected=False) for i in range(4)], detected_count=0)
            if self.relay_observer:
                self.relay_observer()
            return bool(self.snapshot['connected'] and self.snapshot['detected'])

    def set_relays(self, mask, level):
        """Use the same reader/lock as P00; never open the Uno a second time."""
        with self.guard:
            cfg = self.config_store.snapshot()['p00']
            if cfg['driver'] != 'arduino_serial':
                return False, 'P00 입력을 Arduino Uno 직렬 통신으로 설정하세요.'
            self.refresh()
            relay = self.arduino.relays
            if self.arduino.device is None:
                return False, self.snapshot.get('error') or 'Arduino USB 미연결'
            try:
                pending = relay.request(self.arduino.device, mask, level)
                deadline = time.monotonic() + 1.5
                while time.monotonic() < deadline:
                    self.refresh()
                    if relay.ack == pending:
                        return True, 'Uno 릴레이 출력 응답 확인'
                    if self.arduino.device is None:
                        break
                    time.sleep(.01)
            except Exception as exc:
                relay.abandon(self.arduino.device)
                return False, str(exc)
            relay.abandon(self.arduino.device)
            return False, 'Uno 릴레이 응답 시간 초과 · OFF 요청'

    def require_material(self):
        if not self.refresh():
            return False, 'P00 자재 4개 감지 필요 · ' + (self.snapshot.get('error') or f"{self.snapshot.get('detected_count', 0)}/4 감지 · 미감지 또는 안정화 대기")
        return True, 'P00 자재 4/4 감지 · 투입 허용'
