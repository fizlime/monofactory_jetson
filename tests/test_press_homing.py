"""P04 homing interlock regressions; simulation/mocks only, no hardware I/O."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from mono_press.application import Application
from mono_press.core.sequence import SequenceStopped
from mono_press.units.can_press.control_logic import Cancelled


class HomingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = Application(Path(self.tmp.name), '/dev/not-used', 38400, True)
        self.press = self.app.units.press
        self.assertTrue(self.press.enable()[0])

    def test_startup_blocks_manual_and_saved_positions_even_with_sensor_on(self):
        self.press.home_sensor.set_simulated(True)
        for action in ('DOWN', 'UP'):
            with self.subTest(action=action):
                ok, message = self.app.units.manual_command('P04_PRESS_AXIS', action, {'steps': 120})
                self.assertFalse(ok)
                self.assertIn('HOME', message)
        for name in ('PRESS_HOME', 'PRESS_BOTTOM'):
            self.assertFalse(self.press.move_saved(name)[0])
        self.assertEqual(self.press.current_position(), 0)
        self.assertFalse(self.press.axis.snapshot['homed'])

    def test_simulated_home_allows_travel_with_sensor_off(self):
        self.press.axis.update(position=600)
        self.assertTrue(self.press.home()[0])
        self.assertTrue(self.press.axis.snapshot['homed'])
        self.assertEqual(self.press.current_position(), 0)
        self.press.move_steps(120)
        self.assertFalse(self.press.home_sensor.snapshot['home'])
        self.press.move_steps(-60)
        self.assertEqual(self.press.current_position(), 60)
        self.assertTrue(self.press.axis.snapshot['homed'])

    def mock_real(self):
        self.press.set_simulation(False)
        self.press.axis.update(enabled=True, connected=True)

    def finish_home(self, **kwargs):
        self.press.home_sensor.update(connected=True, home=True)

    def test_real_move_never_reaches_driver_until_verified_home(self):
        self.mock_real()
        with patch.object(self.press.can, 'move_steps') as move:
            for steps in (1200, -1200):
                with self.assertRaisesRegex(RuntimeError, 'HOME'):
                    self.press.move_steps(steps)
            move.assert_not_called()
            with patch.object(self.press.can, 'home', side_effect=self.finish_home):
                self.assertTrue(self.press.home()[0])
            self.press.home_sensor.update(home=False)
            self.press.move_steps(1200)
            self.press.move_steps(-1200)
            self.assertEqual(move.call_args_list, [
                call(1200, 100, cancel=self.press.stop_event),
                call(-1200, 100, cancel=self.press.stop_event),
            ])

    def test_missing_sensor_or_home_failure_does_not_unlock(self):
        self.mock_real()
        for failure in (None, RuntimeError('sensor timeout'), Cancelled('PRESS STOPPED')):
            with self.subTest(failure=failure):
                self.press.axis.update(homed=True)
                self.press.home_sensor.update(home=False, connected=True)
                with patch.object(self.press.can, 'home', side_effect=failure):
                    self.assertFalse(self.press.home()[0])
                self.assertFalse(self.press.axis.snapshot['homed'])
                with self.assertRaisesRegex(RuntimeError, 'HOME'):
                    self.press.move_steps(1200)

    def test_stop_during_home_does_not_unlock(self):
        self.mock_real()
        started = threading.Event()
        result = []
        def homing(**kwargs):
            started.set()
            if self.press.stop_event.wait(1):
                raise Cancelled('PRESS STOPPED')
        with patch.object(self.press.can, 'home', side_effect=homing), \
             patch.object(self.press.can, 'stop'):
            worker = threading.Thread(target=lambda: result.append(self.press.home()))
            worker.start()
            try:
                self.assertTrue(started.wait(1))
                self.assertTrue(self.press.stop()[0])
            finally:
                self.press.stop_event.set()
                worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(result[0][0])
        self.assertFalse(self.press.axis.snapshot['homed'])
        self.assertEqual(self.press.axis.snapshot['state'], 'STOPPED')

    def test_disable_mode_switch_and_close_require_new_home(self):
        for reset in (self.press.disable, lambda: self.press.set_simulation(True), self.press.close):
            self.assertTrue(self.press.enable()[0])
            self.assertTrue(self.press.home()[0])
            reset()
            self.assertFalse(self.press.axis.snapshot['homed'])
            self.assertTrue(self.press.enable()[0])
            with self.assertRaisesRegex(RuntimeError, 'HOME'):
                self.press.move_steps(1200)

    def test_reconnect_invalidates_home_but_repeated_enable_preserves_it(self):
        self.mock_real()
        self.press.can.connected = True
        self.press.axis.update(homed=True)
        with patch.object(self.press.can, 'ready'):
            self.assertTrue(self.press.enable()[0])
        self.assertTrue(self.press.axis.snapshot['homed'])
        self.press.can.connected = False
        with patch.object(self.press.can, '_submit'):
            self.assertTrue(self.press.connect())
        self.assertFalse(self.press.axis.snapshot['homed'])

    def test_move_fault_and_unconfirmed_stop_invalidate_home(self):
        self.mock_real()
        self.press.axis.update(homed=True)
        with patch.object(self.press.can, 'move_steps', side_effect=RuntimeError('CAN timeout')):
            with self.assertRaisesRegex(RuntimeError, 'CAN timeout'):
                self.press.move_steps(1200)
        self.assertFalse(self.press.axis.snapshot['homed'])
        self.press.axis.update(homed=True)
        with patch.object(self.press.can, 'stop', side_effect=RuntimeError('no stop ACK')):
            self.assertFalse(self.press.stop()[0])
        self.assertFalse(self.press.axis.snapshot['homed'])

    def test_confirmed_stop_preserves_completed_home(self):
        self.assertTrue(self.press.home()[0])
        self.assertTrue(self.press.stop()[0])
        self.assertTrue(self.press.axis.snapshot['homed'])
        self.press.move_steps(120)

    def test_automatic_recipe_uses_explicit_home_without_rehoming(self):
        self.assertTrue(self.press.home()[0])
        self.app.config.update(lambda c: c['press_recipe'].update(actions=[
            {'type': 'PRESS_DOWN', 'target': 'DOWN_STEPS'},
            {'type': 'PRESS_UP', 'target': 'UP_STEPS'},
        ]))
        def check_homed():
            self.assertTrue(self.press.axis.snapshot['homed'])
        with patch.object(self.app.units.scara, 'enable', side_effect=check_homed), \
             patch.object(self.press, '_home', wraps=self.press._home) as home, \
             patch.object(self.press, '_move_steps') as move:
            self.app.runtime.by_code['P03'].execute(Mock())
            home.assert_not_called()
            self.assertEqual(move.call_count, 2)

    def test_explicit_home_then_two_manual_cycles_never_rehome(self):
        self.mock_real()
        self.press.can.connected = True
        events = []
        def home(**kwargs):
            events.append('HOME')
            self.finish_home()
        def move(steps, speed, **kwargs):
            events.append('DOWN' if steps > 0 else 'UP')
        with patch.object(self.press.can, 'ready'), \
             patch.object(self.press.can, 'home', side_effect=home), \
             patch.object(self.press.can, 'move_steps', side_effect=move):
            self.assertTrue(self.press.home()[0])
            for _ in range(2):
                self.assertTrue(self.app.units.manual_command('P04_PRESS_AXIS', 'CYCLE')[0])
        self.assertEqual(events, ['HOME', 'DOWN', 'UP', 'DOWN', 'UP'])

    def cycle_entries(self):
        self.app.config.update(lambda c: c['press_recipe'].update(actions=[
            {'type': 'PRESS_DOWN', 'target': 'DOWN_STEPS'},
            {'type': 'PRESS_UP', 'target': 'UP_STEPS'},
        ]))
        return (self.press.run_cycle, lambda: self.app.runtime.by_code['P04'].execute(Mock()))

    def test_unprepared_cycle_blocks_without_implicit_home(self):
        for run in self.cycle_entries():
            self.mock_real()
            self.press.can.connected = True
            with patch.object(self.press.can, 'ready'), \
                 patch.object(self.press.can, 'home') as home, \
                 patch.object(self.press.can, 'move_steps') as move, \
                 patch.object(self.app.units.scara, 'enable') as robot:
                with self.assertRaisesRegex(RuntimeError, 'HOME'):
                    run()
                home.assert_not_called()
                move.assert_not_called()
                robot.assert_not_called()
                self.assertFalse(self.press.axis.snapshot['homed'])

    def test_cycle_enable_failure_never_homes_or_moves(self):
        for run in self.cycle_entries():
            with patch.object(self.press, 'enable', return_value=(False, 'enable failed')), \
                 patch.object(self.press, '_home') as home, \
                 patch.object(self.press, '_move_steps') as move:
                with self.assertRaisesRegex(RuntimeError, 'enable failed'):
                    run()
                home.assert_not_called()
                move.assert_not_called()

    def test_stop_during_cycle_enable_blocks_all_following_motion(self):
        for run in self.cycle_entries():
            self.press.axis.update(homed=True)
            def enable():
                self.press.stop()
                return True, 'ok'
            with patch.object(self.press, 'enable', side_effect=enable), \
                 patch.object(self.press, '_home') as home, \
                 patch.object(self.press, '_move_steps') as move, \
                 patch.object(self.app.units.scara, 'enable') as robot:
                with self.assertRaises(Cancelled): run()
                home.assert_not_called()
                move.assert_not_called()
                robot.assert_not_called()

    def test_stop_between_ready_check_and_cycle_does_not_get_cleared(self):
        for run in self.cycle_entries():
            self.press.axis.update(homed=True)
            original = self.press.require_homed
            def ready():
                original()
                self.press.stop()
            with patch.object(self.press, 'require_homed', side_effect=ready), \
                 patch.object(self.press, '_home') as home, \
                 patch.object(self.press, '_move_steps') as move:
                with self.assertRaises(Cancelled): run()
                home.assert_not_called()
                move.assert_not_called()
                self.assertTrue(self.press.stop_event.is_set())

    def test_stopped_automatic_context_never_starts_home(self):
        self.cycle_entries()
        ctx = self.app.runtime._context()
        self.app.runtime.stop_event.set()
        with patch.object(self.press, 'enable') as enable, \
             patch.object(self.press, '_home') as home:
            with self.assertRaises(SequenceStopped):
                self.app.runtime.by_code['P03'].execute(ctx)
            enable.assert_not_called()
            home.assert_not_called()


if __name__ == '__main__':
    unittest.main()
