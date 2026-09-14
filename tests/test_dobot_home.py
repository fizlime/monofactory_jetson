import itertools
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mono_press.application import Application
from mono_press.http_server import make_server
from mono_press.units.dobot_serial import DobotSerial


class DobotHomeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = Application(Path(self.tmp.name), '/dev/not-used', 38400, True)
        self.robot = self.app.units.scara
        with patch('mono_press.http_server.ThreadingHTTPServer', side_effect=lambda addr, handler: handler):
            self.handler = make_server(self.app)

    def post(self, path, body=None):
        return self.handler.route_post(None, path, body or {})

    def real_device(self):
        self.robot.simulation = False
        device = self.robot.device = Mock()
        device.alarms.return_value = []
        device.home_params.return_value = [194, 68, 126, 19]
        device.pose.return_value = ([200, 0, 100, 0], [0, 1, 12, 0])
        return device

    def test_wire_home_params_and_one_nonqueued_command(self):
        device = DobotSerial.__new__(DobotSerial)
        device.request = Mock(return_value=struct.pack('<4f', 194, 68, 126, 19))
        self.assertEqual(device.home_params(), [194, 68, 126, 19])
        device.request.assert_called_once_with(30)
        device.request.reset_mock()
        device.start_home()
        device.request.assert_called_once_with(31, write=True)

    def test_home_simulation_available_without_enable_and_independent(self):
        self.robot.axes[0].update(position=17)
        self.app.units.scara_p05.axes[0].update(position=23)
        self.assertTrue(self.post('/api/unit/P03_SCARA_J1/command', {'action': 'HOME'})[0])
        self.assertEqual(self.robot.current_position(), [0]*4)
        self.assertTrue(self.robot.axes[0].snapshot['homed'])
        self.assertFalse(self.robot.axes[0].snapshot['enabled'])
        self.assertEqual(self.app.units.scara_p05.current_position()[0], 23)
        self.assertTrue(self.post('/api/scara/P05/home')[0])
        self.assertTrue(self.app.units.scara_p05.axes[0].snapshot['homed'])

    def test_real_home_uses_device_home_not_saved_zero_and_verifies_movement(self):
        device = self.real_device()
        poses = itertools.chain([([200, 0, 100, 0], [0]*4)], itertools.repeat(([194, 68, 126, 19], [19, 0, 3, 0])))
        device.pose.side_effect = poses
        with patch('mono_press.units.scara_robot.time.monotonic', side_effect=itertools.count(0, .5)), \
             patch.object(self.robot.stop_event, 'wait', return_value=False):
            self.assertTrue(self.robot.home()[0])
        device.start_home.assert_called_once()
        self.assertGreaterEqual(device.pose.call_count, 5)
        device.move.assert_not_called()
        self.assertEqual(self.robot.xyzr, [194, 68, 126, 19])
        self.assertTrue(self.robot.axes[0].snapshot['homed'])

    def test_ack_alone_or_unchanged_pose_is_not_home_completion(self):
        device = self.real_device()
        device.pose.return_value = ([194, 68, 126, 19], [0]*4)
        with patch('mono_press.units.scara_robot.time.monotonic', side_effect=itertools.count(0, 10)), \
             patch.object(self.robot.stop_event, 'wait', return_value=False):
            self.assertFalse(self.robot.home()[0])
        device.start_home.assert_called_once()
        device.stop.assert_called_once()
        self.assertFalse(self.robot.axes[0].snapshot['homed'])

    def test_preexisting_alarm_blocks_home_without_writing_or_clearing(self):
        device = self.real_device()
        device.alarms.return_value = [73]
        self.assertFalse(self.robot.home()[0])
        device.start_home.assert_not_called()
        device.stop.assert_not_called()
        self.assertEqual(self.robot.axes[0].snapshot['alarms'], [73])
        self.assertEqual(self.robot.axes[0].snapshot['state'], 'ALARM')

    def test_lost_home_ack_is_not_retried_and_attempts_stop(self):
        device = self.real_device()
        device.start_home.side_effect = TimeoutError('lost ACK')
        self.assertFalse(self.robot.home()[0])
        device.start_home.assert_called_once()
        device.stop.assert_called_once()
        self.assertFalse(self.robot.axes[0].snapshot['homed'])

    def test_stop_and_manual_lock_during_homing(self):
        results = []
        worker = threading.Thread(target=lambda: results.append(self.post('/api/scara/P03/home')))
        worker.start()
        deadline = time.monotonic()+1
        while self.robot.axes[0].snapshot['state']!='HOMING' and time.monotonic()<deadline:time.sleep(.001)
        self.assertFalse(self.post('/api/scara/P05/home')[0])
        self.assertFalse(self.app.runtime.start()[0])
        with patch.object(self.app.units.press, 'stop', return_value=(False, 'CAN offline')) as press_stop:
            self.assertTrue(self.post('/api/unit/P03_SCARA_J1/command', {'action':'STOP'})[0])
            press_stop.assert_not_called()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(results[0][0])
        self.assertFalse(self.robot.axes[0].snapshot['homed'])

    def test_alarm_and_transient_offline_recovery_are_visible(self):
        device=self.real_device()
        device.alarms.return_value=[73]
        self.robot.refresh_alarms()
        self.assertEqual(self.robot.axes[0].snapshot['state'],'ALARM')
        device.alarms.return_value=[]
        self.robot.refresh_alarms()
        self.assertEqual(self.robot.axes[0].snapshot['state'],'DISABLED')
        self.robot._status(state='OFFLINE',connected=False,error='timeout')
        self.robot.refresh_pose();self.robot.refresh_alarms()
        self.assertTrue(self.robot.axes[0].snapshot['connected'])
        self.assertEqual(self.robot.axes[0].snapshot['error'],'')

    def test_legacy_home_recipe_is_preserved_but_never_homes_in_cycle(self):
        body={'version':3,'actions':[{'type':'DOBOT_HOME','robot':'P05'}]}
        self.assertTrue(self.post('/api/recipe',body)[0])
        self.assertEqual(self.app.config.snapshot()['press_recipe'],body)
        with patch.object(self.app.units.scara_p05,'home',return_value=(True,'done')) as home:
            self.app.runtime.by_code['P03'].execute(self.app.runtime._context())
            home.assert_not_called()
        self.assertFalse(self.robot.axes[0].snapshot['homed'])


if __name__=='__main__':unittest.main()
