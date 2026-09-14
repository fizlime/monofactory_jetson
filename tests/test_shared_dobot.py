import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mono_press.application import Application
from mono_press.core.config import ConfigStore, validate_config
from mono_press.core.robot_command import validate_dobot_move
from mono_press.core.sequence import SequenceStopped
from mono_press.http_server import make_server


class SharedDobotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        store = ConfigStore(root / 'settings/poc_config.json')
        cfg = store.snapshot()
        cfg['scara_p05']['shared_with'] = 'P03'
        store.save(cfg)
        self.app = Application(root, '/dev/not-used', 38400, True)
        self.robot = self.app.units.scara
        self.p05 = self.app.units.scara_p05
        self.move = {'type': 'DOBOT_MOVE', 'robot': 'P03', 'mode': 'JOINT',
                     'reference': 'HOME', 'values': [1, 0, 0, 0], 'speed': 10}
        with patch('mono_press.http_server.ThreadingHTTPServer', side_effect=lambda addr, handler: handler):
            self.handler = make_server(self.app)

    def test_one_transport_owner_and_live_state_aliases(self):
        self.assertEqual(self.app.units.physical_robots, [self.robot])
        self.assertIs(self.p05.lock, self.robot.lock)
        self.assertIs(self.p05.stop_event, self.robot.stop_event)
        device = self.robot.device = Mock()
        self.assertIs(self.p05.device, device)
        self.robot.axes[1].update(position=42, state='ALARM', alarms=[73], enabled=False)
        snapshot = self.app.state.snapshot()['units']['P05_SCARA_J2']
        self.assertEqual(snapshot['id'], 'P05_SCARA_J2')
        self.assertEqual(snapshot['process'], 'P05')
        self.assertEqual(snapshot['position'], 42)
        self.assertEqual(snapshot['alarms'], [73])
        self.assertTrue(self.p05.stop()[0])
        device.stop.assert_called_once()

    def test_shared_ports_normalized_and_independent_duplicate_rejected(self):
        cfg = self.app.config.snapshot()
        cfg['scara'].update(driver='dobot_serial', port='/dev/ttyUSB1')
        checked = validate_config(cfg)
        self.assertEqual(checked['scara_p05']['port'], '/dev/ttyUSB1')
        checked['scara_p05'].pop('shared_with')
        with self.assertRaisesRegex(ValueError, '같은 Dobot'):
            validate_config(checked)

    def test_process_views_share_enable_gripper_and_home_but_keep_saved_namespaces(self):
        self.p05.enable()
        self.assertTrue(self.robot.axes[0].snapshot['enabled'])
        self.robot.set_gripper(True)
        self.assertTrue(self.p05.gripper.snapshot['output'])
        self.assertTrue(self.p05.home()[0])
        self.assertTrue(self.p05.axes[0].snapshot['homed'])
        self.p05.move_home_offset([1, 0, 0, 0], 10)
        self.assertEqual(self.robot.current_position(), [1, 0, 0, 0])
        self.assertEqual(self.p05.axes[0].snapshot['position'], 1)
        self.assertTrue(self.handler.route_post(None, '/api/scara/P05/save-current', {'name':'P05_ONLY'})[0])
        cfg = self.app.config.snapshot()
        self.assertEqual(cfg['scara_p05']['positions']['P05_ONLY'], [1, 0, 0, 0])
        self.assertNotIn('P05_ONLY', cfg['scara']['positions'])
        self.p05.stop_gripper()
        self.assertFalse(self.robot.gripper.snapshot['drive_enabled'])
        self.assertFalse(self.p05.gripper.snapshot['enabled'])

    def test_offset_uses_measured_home_joints_without_accumulation(self):
        self.robot.enable()
        self.robot.home_joints = [12, 23, 34, 45]
        self.robot._status(homed=True)
        self.robot.move_home_offset([1, 0, 0, 0], 10)
        self.assertEqual(self.robot.current_position(), [13, 23, 34, 45])
        self.p05.move_home_offset([1, 0, 0, 0], 10)
        self.assertEqual(self.robot.current_position(), [13, 23, 34, 45])
        self.robot.close()
        with self.assertRaisesRegex(RuntimeError, 'HOME 완료'):
            self.robot.move_home_offset([1, 0, 0, 0], 10)

    def test_home_then_offset_then_original_actions_and_one_enable(self):
        actions = [{'type':'DOBOT_HOME', 'robot':'P03'}, self.move,
                   {'type':'GRIPPER', 'robot':'P05', 'target':'OPEN'}]
        self.app.config.update(lambda cfg: cfg['press_recipe'].update(actions=actions))
        events = []
        with patch.object(self.robot, 'home', side_effect=lambda:(events.append('home') or (True,'ok'))), \
             patch.object(self.robot, 'enable', side_effect=lambda:(events.append('enable') or (True,'ok'))), \
             patch.object(self.robot, 'move_home_offset', side_effect=lambda *args:events.append('offset')), \
             patch.object(self.robot, 'set_gripper', side_effect=lambda *args:(events.append('p05-gripper') or (True,'ok'))):
            self.assertTrue(self.robot.home()[0])
            self.app.runtime.by_code['P03'].execute(self.app.runtime._context())
        self.assertEqual(events, ['home', 'enable', 'offset', 'p05-gripper'])

    def test_failed_explicit_home_or_stop_does_not_send_offset(self):
        self.app.config.update(lambda cfg: cfg['press_recipe'].update(actions=[{'type':'DOBOT_HOME','robot':'P03'},self.move]))
        with patch.object(self.robot, 'home', return_value=(False,'home alarm')), \
             patch.object(self.robot, 'move_home_offset') as move:
            self.assertTrue(self.app.runtime.home('P03')[0])
            self.app.runtime.thread.join(2)
            self.assertIn('home alarm', self.app.state.line['fault']['message'])
            self.assertFalse(self.app.runtime.run_process('P03')[0])
            move.assert_not_called()
        self.app.runtime.stop_event.set()
        with patch.object(self.robot, 'home') as home, patch.object(self.robot, 'move_home_offset') as move:
            with self.assertRaises(SequenceStopped):
                self.app.runtime.by_code['P03'].execute(self.app.runtime._context())
            home.assert_not_called()
            move.assert_not_called()

    def test_stop_all_and_lifecycle_only_once_per_physical_robot(self):
        with patch.object(self.robot, 'stop', return_value=(True,'ok')) as stop:
            self.app.units.stop_all()
            stop.assert_called_once()
        with patch.object(self.app.bus,'connect',return_value=True), patch.object(self.app.units.press,'connect',return_value=True), \
             patch.object(self.app.units.material,'connect'), patch.object(self.app.units.camera,'connect'), \
             patch.object(self.robot,'connect',return_value=True) as connect:
            self.app.connect()
            connect.assert_called_once()

    def test_offset_config_validation_and_persistence(self):
        self.assertEqual(validate_dobot_move(self.move), self.move)
        for edits in ({'values':[11,0,0,0]}, {'values':[None,0,0,0]}, {'mode':'XYZR'}, {'reference':'UNKNOWN'}):
            with self.assertRaises(ValueError):
                validate_dobot_move({**self.move, **edits})
        recipe = {'version':3,'actions':[{'type':'DOBOT_HOME','robot':'P03'}, self.move]}
        self.assertTrue(self.handler.route_post(None, '/api/recipe', recipe)[0])
        self.assertEqual(ConfigStore(self.app.config.path).snapshot()['press_recipe'], recipe)
        changed = copy.deepcopy(self.app.config.snapshot())
        changed['scara_p05'].pop('shared_with')
        self.assertFalse(self.handler.route_post(None, '/api/config', changed)[0])


if __name__ == '__main__':
    unittest.main()
