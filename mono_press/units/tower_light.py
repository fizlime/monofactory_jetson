from .base import UnitBase


class TowerLight:
    """P06 D8 green, D9 red, D10 buzzer; P00 owns the shared Uno port."""
    def __init__(self, state, config_store=None, material=None):
        self.state = state
        self.config_store = config_store
        self.material = material
        self.desired_mask = 0
        self.previous_simulation = self.simulation
        self.green = UnitBase(state, 'P06_LIGHT_GREEN', 'P06', 'DIGITAL_OUTPUT', '초록 경광등', self.simulation, pin=8, output=False, state='OFF')
        self.red = UnitBase(state, 'P06_LIGHT_RED', 'P06', 'DIGITAL_OUTPUT', '빨강 경광등', self.simulation, pin=9, output=False, state='OFF')
        self.buzzer = UnitBase(state, 'P06_BUZZER', 'P06', 'DIGITAL_OUTPUT', '부저', self.simulation, pin=10, output=False, state='OFF')
        self.channels = (self.green, self.red, self.buzzer)
        if material: material.relay_observer = self.refresh
        self.refresh()

    @property
    def simulation(self):
        return self.material is None or self.material.simulated

    @property
    def level(self):
        return self.config_store.snapshot()['p06']['relay_active_level'] if self.config_store else 'UNSET'

    def refresh(self):
        changed_mode = self.previous_simulation != self.simulation
        self.previous_simulation = self.simulation
        if self.simulation:
            for unit in self.channels:
                if changed_mode: unit.update(output=False, state='OFF', enabled=False)
                unit.update(simulated=True, connected=True, configured=True, error='')
            if changed_mode: self.desired_mask = 0
            return
        relay = self.material.arduino.relays
        configured = self.level in ('LOW', 'HIGH')
        connected = self.material.arduino.device is not None and relay.ready
        compatible = relay.level is None or relay.level == self.level
        known = configured and connected and relay.mask is not None
        error = ('릴레이 LOW/HIGH 작동 방식을 먼저 설정하세요.' if not configured else
                 'Uno P06 통합 펌웨어 또는 USB 연결을 확인하세요.' if not connected else
                 f'Uno 펌웨어 극성은 {relay.level}입니다. 설정을 맞추세요.' if not compatible else
                 '릴레이 출력 확인 대기' if not known else '')
        for i, unit in enumerate(self.channels):
            output = bool(relay.mask & (1 << i)) if known else None
            values = dict(simulated=False, connected=connected, configured=configured and compatible, enabled=known,
                          output=output, active_level=self.level, error=error,
                          state=('ON' if output else 'OFF') if known else 'UNCONFIGURED' if not configured else 'OFFLINE' if not connected else 'UNKNOWN')
            if any(unit.snapshot.get(k) != v for k,v in values.items()): unit.update(**values)
        lamp = ('RED+GREEN' if relay.mask & 3 == 3 else 'RED' if relay.mask & 2 else 'GREEN' if relay.mask & 1 else 'OFF') if known else 'UNKNOWN'
        buzzer = bool(relay.mask & 4) if known else None
        if self.state.line.get('lamp') != lamp or self.state.line.get('buzzer') != buzzer:
            self.state.update_line(lamp=lamp, buzzer=buzzer)

    def _apply(self, mask):
        if self.material:
            with self.material.guard:
                return self._apply_locked(mask)
        return self._apply_locked(mask)

    def _apply_locked(self, mask):
        if not self.simulation:
            level = self.material.arduino.relays.level if mask == 0 else self.level
            if level not in ('LOW', 'HIGH'):
                self.refresh()
                return (True, '릴레이 미설정 · 출력 명령 없음') if mask == 0 else (False, 'P06에서 릴레이 LOW/HIGH 작동 방식을 먼저 설정하세요.')
            ok, message = self.material.set_relays(mask, level)
            self.refresh()
            if not ok: return False, message
        else:
            for i, unit in enumerate(self.channels):
                on = bool(mask & (1 << i))
                unit.update(enabled=True, simulated=True, connected=True, output=on, state='ON' if on else 'OFF', error='')
        self.desired_mask = mask
        self.state.update_line(lamp='RED+GREEN' if mask & 3 == 3 else 'RED' if mask & 2 else 'GREEN' if mask & 1 else 'OFF', buzzer=bool(mask & 4))
        self.state.record('LIGHT', f'D8 초록={bool(mask & 1)} · D9 빨강={bool(mask & 2)} · D10 부저={bool(mask & 4)}', 'P06')
        return True, '경광등·부저 출력 적용'

    def set(self, color):
        color = str(color).upper()
        if color not in {'RED', 'GREEN', 'OFF'}: return False, '경광등은 RED/GREEN/OFF만 가능합니다.'
        return self._apply({'RED': 2, 'GREEN': 1, 'OFF': 0}[color])

    def set_channel(self, unit_id, on):
        unit = next((u for u in self.channels if u.unit_id == unit_id), None)
        if unit is None: return False, 'P06 출력 채널을 찾을 수 없습니다.'
        if self.material:
            with self.material.guard:
                mask = self.desired_mask if self.simulation else (self.material.arduino.relays.mask or 0)
                bit = 1 << self.channels.index(unit)
                return self._apply(mask | bit if on else mask & ~bit)
        bit = 1 << self.channels.index(unit)
        return self._apply(self.desired_mask | bit if on else self.desired_mask & ~bit)
