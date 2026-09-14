from pathlib import Path

from .core.config import ConfigStore
from .core.runtime import LineRuntime
from .core.state import PlantState
from .sequences.registry import build_sequences
from .units.registry import UnitRegistry
from .units.servo_bus import ServoBusController
from .units.teensy_bus import TeensyBusController


class Application:
    def __init__(self, root: Path, port: str, baud: int, simulation: bool):
        self.root = Path(root)
        self.config = ConfigStore(self.root / "settings" / "poc_config.json")
        self.state = PlantState(port, baud, simulation, self.config)
        p01=self.config.snapshot()['p01']
        if p01.get('transport')=='teensy_usb':
            self.bus=TeensyBusController(self.state,p01['teensy_port'],115200,simulation,config_store=self.config)
        else:
            self.bus = ServoBusController(self.state, port, baud, simulation)
        self.units = UnitRegistry(self.state, self.config, self.bus)
        self.sequences = build_sequences(self.units, self.config)
        self.runtime = LineRuntime(self.state, self.config, self.units, self.sequences)
        self.sync_feed_config()

    def sync_feed_config(self):
        active = []
        for axis in self.units.p01_axes:
            installed = axis.installed()
            axis.update(installed=installed)
            if not installed: axis.update(enabled=False, connected=False, state='EXCLUDED', error='')
            elif axis.snapshot['state'] == 'EXCLUDED': axis.update(state='DISABLED', enabled=False)
            if installed: active.append(axis.name)
        self.state.update_process('P01', summary=f"사용 {len(active)}/4축 · {', '.join(active)} · 자재 감지 후 투입")

    def connect(self):
        self.units.material.connect()
        self.units.camera.connect()
        uart_ok = self.bus.connect()
        press_ok = self.units.press.connect()
        robot_results = [robot.connect() for robot in self.units.physical_robots]
        return uart_ok and press_ok and all(robot_results)

    def connection_summary(self):
        active = [a for a in self.units.p01_axes if a.installed()]
        responding = sum(bool(a.snapshot.get('connected')) for a in active)
        transport='Teensy USB' if isinstance(self.bus,TeensyBusController) else 'UART'
        parts = [f"P01 {transport} {'열림' if self.state.serial['connected'] else '미연결'} · 모터 응답 {responding}/{len(active)}"]
        for code, robot in self.units.scaras.items():
            item = robot.axes[0].snapshot
            parts.append(f"{code} Dobot {'USB 연결' if item.get('connected') else '미연결'}" + (f" ({item['error']})" if item.get('error') else ""))
        parts.append(f"P04 CAN {'연결' if self.units.press.axis.snapshot.get('connected') else '미연결'}")
        return ' / '.join(parts)

    def check_p01(self):
        results = []
        for axis in self.units.p01_axes:
            if not axis.installed():
                axis.update(enabled=False, connected=False, state='EXCLUDED', error='')
                results.append(f'{axis.name} 제외')
                continue
            ok, message = self.bus.probe(axis.address)
            enabled = self.bus.probe_status.get(axis.address, {}).get('enabled', axis.snapshot.get('enabled', False)) if ok else False
            axis.update(connected=ok, enabled=enabled, state='READY' if ok and enabled else 'DISABLED' if ok else 'NO_RESPONSE',
                        error='' if ok else message, diagnostic=message)
            if not ok: axis.update(enabled=False)
            results.append(f'{axis.name}: {message}')
        self.bus.publish_diagnostics(force=True)
        self.state.record('SERIAL', ' / '.join(results), 'P01')
        active = [a for a in self.units.p01_axes if a.installed()]
        return all(a.snapshot['connected'] for a in active), ' / '.join(results)

    def switch_mode(self, mode):
        if self.runtime.busy():
            return False, "운전 중에는 SIMULATION/REAL 모드를 변경할 수 없습니다."
        selected = str(mode).upper()
        if selected not in {"SIMULATION", "REAL"}:
            return False, "모드는 SIMULATION 또는 REAL입니다."
        self.units.stop_all()
        for robot in self.units.physical_robots:
            robot.disable()
        self.units.press.disable()
        self.bus.close()
        simulation = selected == "SIMULATION"
        self.bus.simulation = simulation
        self.state.update_serial(simulation=simulation, connected=False, last_error="")
        for unit in self.units.p01_axes:
            unit.update(simulated=simulation, connected=False, enabled=False, homed=False, state="DISABLED")
        for sensor in self.units.p01_homes:
            sensor.set_mode(simulation)
        self.units.press.set_simulation(simulation)
        self.units.material.set_mode(simulation)
        self.units.camera.set_simulation(simulation)
        for robot in self.units.physical_robots:
            robot.set_simulation(simulation)
        uart_connected = self.bus.connect()
        press_connected = self.units.press.connect()
        self.state.record("MODE", f"{selected} 모드 전환")
        if simulation:
            return True, "SIMULATION 모드로 전환했습니다."
        connected = uart_connected and press_connected and all(r.axes[0].snapshot.get('connected') for r in self.units.scaras.values())
        return connected, "REAL 모드 · " + self.connection_summary()

    def shutdown(self):
        self.runtime.stop()
        self.units.material.close()
        try:
            self.units.camera.close()
        finally:
            for robot in self.units.physical_robots:
                robot.close()
            self.units.press.close()
            self.bus.close()
