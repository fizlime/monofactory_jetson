"""Explicit homing workflow. Simulation and mocks only; no real I/O."""
from contextlib import ExitStack
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from mono_press.application import Application
from mono_press.core.config import ConfigStore
from mono_press.http_server import make_server


class LineHomingTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        store = ConfigStore(root / 'settings/poc_config.json')
        cfg = store.snapshot()
        cfg['scara_p05']['shared_with'] = 'P03'
        cfg['p01']['axes']['E3']['installed'] = False
        store.save(cfg)
        self.app = Application(root, '/dev/not-used', 38400, True)
        self.runtime = self.app.runtime
        self.units = self.app.units
        with patch('mono_press.http_server.ThreadingHTTPServer', side_effect=lambda addr, handler: handler):
            self.handler = make_server(self.app)

    def home(self, code=None):
        path = '/api/line/home' if code is None else f'/api/process/{code}/home'
        self.assertTrue(self.handler.route_post(None, path, {})[0])
        self.runtime.thread.join(3)
        self.assertFalse(self.runtime.busy())
        self.assertIsNone(self.app.state.line['fault'])

    def test_main_homes_installed_axes_and_each_physical_device_once(self):
        with ExitStack() as stack:
            calls = [stack.enter_context(patch.object(a, 'home', wraps=a.home)) for a in self.units.p01_axes]
            robot = stack.enter_context(patch.object(self.units.scara, 'home', wraps=self.units.scara.home))
            press = stack.enter_context(patch.object(self.units.press, 'home', wraps=self.units.press.home))
            grip = stack.enter_context(patch.object(self.units.scara, 'set_gripper'))
            move = stack.enter_context(patch.object(self.units.scara, 'move_position'))
            cycle = stack.enter_context(patch.object(self.units.press, 'run_cycle'))
            self.home()
            self.assertEqual([c.call_count for c in calls], [1, 1, 1, 0])
            robot.assert_called_once()
            press.assert_called_once()
            for c in (grip, move, cycle): c.assert_not_called()
        self.runtime.homing.require_ready()
        self.assertEqual(self.app.state.line['mode'], 'IDLE')
        for code in ('P01', 'P03', 'P04', 'P05'):
            self.assertEqual(self.app.state.processes[code]['status'], 'DONE')

    def test_pending_ee_sx_is_never_enabled_or_marked_homed(self):
        with ExitStack() as stack:
            enables = []
            for axis in self.units.p01_axes:
                axis.home_sensor.set_mode(False)
                enables.append(stack.enter_context(patch.object(axis, 'enable')))
            self.home()
            for enable in enables: enable.assert_not_called()
        self.assertEqual(self.app.state.processes['P01']['status'], 'WAITING')
        self.assertIn('EE-SX', self.app.state.line['message'])
        self.assertFalse(any(a.snapshot['homed'] for a in self.units.p01_axes))
        self.runtime.homing.require_ready()  # Pending inputs retain relative-cycle behavior.
        # Once origin inputs exist, each installed axis requires a verified HOME.
        for axis in self.units.p01_axes: axis.home_sensor.set_mode(True)
        with self.assertRaisesRegex(RuntimeError, 'P01'): self.runtime.homing.require_ready()
        self.home('P01')
        self.runtime.homing.require_ready()

    def test_selected_process_home_does_not_home_other_equipment(self):
        with patch.object(self.units.scara, 'home', wraps=self.units.scara.home) as robot, \
             patch.object(self.units.press, 'home', wraps=self.units.press.home) as press:
            self.home('P05')
            robot.assert_called_once()
            press.assert_not_called()
            self.assertTrue(all(a.snapshot['homed'] for a in self.units.scara.axes))
            self.home('P04')
            press.assert_called_once()
            self.assertEqual(robot.call_count, 1)
        for code in ('P00', 'P02', 'P06'):
            self.assertFalse(self.handler.route_post(None, f'/api/process/{code}/home', {})[0])

    def test_unprepared_cycle_is_rejected_without_dispatch(self):
        with patch.object(self.units.scara, 'home') as home, patch.object(self.units.press, 'enable') as enable:
            self.assertFalse(self.runtime.start()[0])
            for code in ('P01', 'P03', 'P04', 'P05'):
                self.assertFalse(self.runtime.run_process(code)[0])
            home.assert_not_called()
            enable.assert_not_called()

    def test_stop_during_home_cancels_remaining_devices_and_rejects_duplicates(self):
        entered = threading.Event()
        def robot_home(cancel=None):
            entered.set()
            self.assertTrue(cancel.wait(2))
            return False, 'stopped'
        with patch.object(self.units.scara, 'home', side_effect=robot_home) as home, \
             patch.object(self.units.press, 'home') as press:
            self.assertTrue(self.runtime.home()[0])
            try:
                self.assertTrue(entered.wait(1))
                self.assertEqual(self.app.state.line['mode'], 'HOMING')
                self.assertFalse(self.runtime.home()[0])
                self.assertFalse(self.runtime.run_process('P03')[0])
                self.assertFalse(self.runtime.manual_action(lambda: (True, 'must not run'))[0])
                self.assertTrue(self.runtime.stop()[0])
            finally:
                self.runtime.stop_event.set()
                self.runtime.thread.join(2)
            home.assert_called_once()
            press.assert_not_called()
        self.assertEqual(self.app.state.line['status'], 'IDLE')
        self.assertFalse(self.units.press.axis.snapshot['homed'])

    def test_stop_during_enable_never_sends_following_home(self):
        def stopped_enable():
            self.runtime.stop()
            return True, 'ok'
        with patch.object(self.units.press, 'enable', side_effect=stopped_enable), \
             patch.object(self.units.press, 'home') as home:
            self.assertTrue(self.runtime.home('P04')[0])
            self.runtime.thread.join(2)
            home.assert_not_called()
            self.assertEqual(self.app.state.line['status'], 'IDLE')

    def test_home_failure_blocks_remaining_devices(self):
        with patch.object(self.units.scara, 'home', return_value=(False, 'HOME timeout')), \
             patch.object(self.units.press, 'home') as press:
            self.assertTrue(self.runtime.home()[0])
            self.runtime.thread.join(2)
            self.assertEqual(self.app.state.line['status'], 'ALARM')
            self.assertEqual(self.app.state.processes['P03']['status'], 'ERROR')
            press.assert_not_called()
            self.assertFalse(self.runtime.start()[0])

    def test_manual_axis_activity_blocks_home_and_start(self):
        with patch.object(self.units.p01_axes[0], 'busy', return_value=True):
            self.assertFalse(self.runtime.home()[0])
            self.assertFalse(self.runtime.home('P04')[0])
            self.assertFalse(self.runtime.start()[0])
            self.assertFalse(self.runtime.run_process('P03')[0])

    def test_two_complete_recipes_keep_all_actions_and_never_rehome(self):
        self.home()
        actions = self.app.config.snapshot()['press_recipe']['actions']
        self.assertEqual(len(actions), 27)
        events = []
        with patch.object(self.units.scara, 'home') as robot_home, \
             patch.object(self.units.press, 'home') as press_home, \
             patch.object(self.units.scara, 'move_position') as move, \
             patch.object(self.units.scara, 'move_saved', side_effect=lambda name: (events.append(name) or (True, 'ok'))), \
             patch.object(self.units.scara_p05, 'move_saved', side_effect=lambda name: (events.append(name) or (True, 'ok'))), \
             patch.object(self.units.scara, 'set_gripper', side_effect=lambda closed: (events.append('CLOSE' if closed else 'OPEN') or (True, 'ok'))), \
             patch.object(self.units.press, '_move_steps', side_effect=lambda n, *a: events.append('DOWN' if n > 0 else 'UP')):
            for _ in range(2):
                self.assertTrue(self.runtime.run_process('P03')[0])
                self.runtime.thread.join(3)
                self.assertFalse(self.runtime.busy())
                self.assertIsNone(self.app.state.line['fault'])
            robot_home.assert_not_called()
            press_home.assert_not_called()
            move.assert_not_called()
        expected = [a['target'] if a['type'] not in ('PRESS_DOWN','PRESS_UP') else ('DOWN' if a['type']=='PRESS_DOWN' else 'UP') for a in actions]
        self.assertEqual(events, expected * 2)

    def test_p01_origin_is_invalidated_on_disable_and_disconnect(self):
        self.home('P01')
        axis = self.units.p01_axes[0]
        self.assertTrue(axis.snapshot['homed'])
        axis.disable()
        self.assertFalse(axis.snapshot['homed'])
        self.home('P01')
        self.app.bus.close()
        self.assertFalse(any(a.snapshot['homed'] for a in self.units.p01_axes))


if __name__ == '__main__': unittest.main()
