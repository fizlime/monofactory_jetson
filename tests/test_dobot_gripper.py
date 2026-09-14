"""Gripper drive disable, independent routing, and sequence cancellation."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch, call

from mono_press.application import Application
from mono_press.http_server import make_server
from mono_press.units.dobot_serial import DobotSerial


class DobotGripperTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.app = Application(Path(tmp.name), '/dev/not-used', 38400, True)
        self.robot = self.app.units.scara
        with patch('mono_press.http_server.ThreadingHTTPServer', side_effect=lambda addr, handler: handler):
            self.handler = make_server(self.app)

    def stop(self, code='P03'):
        return self.handler.route_post(None, f'/api/unit/{code}_SCARA_GRIPPER/command', {'action':'STOP'})

    def test_wire_disables_output_and_requires_readback(self):
        device = DobotSerial.__new__(DobotSerial)
        device.request = Mock(side_effect=[b'', b'\0\0'])
        device.stop_gripper()
        self.assertEqual(device.request.call_args_list, [call(63, b'\0\0', write=True), call(63)])
        for reply in [b'', b'\1\0', b'\0']:
            device.request = Mock(side_effect=[b'', reply])
            with self.assertRaises(RuntimeError): device.stop_gripper()
        device.request = Mock(side_effect=TimeoutError('lost ACK'))
        with self.assertRaises(TimeoutError): device.stop_gripper()
        device.request.assert_called_once_with(63, b'\0\0', write=True)
        device.request = Mock()
        device.gripper(False)
        device.request.assert_called_once_with(63, b'\1\0', write=True)

    def test_both_robots_stop_independently_and_require_reenable(self):
        for robot in self.app.units.scaras.values():
            robot.enable(); robot.set_gripper(True)
        for code, robot in self.app.units.scaras.items():
            self.assertTrue(self.stop(code)[0])
            g = robot.gripper.snapshot
            self.assertFalse(g['drive_enabled'])
            self.assertFalse(g['enabled'])
            self.assertEqual(g['state'], 'STOPPED')
            self.assertTrue(g['output'])  # Do not claim an OPEN jaw position.
            self.assertEqual(robot.axes[0].snapshot['state'], 'READY')
            self.assertFalse(robot.set_gripper(False)[0])
            if code == 'P03': self.assertTrue(self.app.units.scara_p05.gripper.snapshot['drive_enabled'])
            robot.enable()
            self.assertTrue(robot.set_gripper(False)[0])

    def test_stop_bypasses_manual_busy_and_enable_and_never_stops_arm(self):
        self.robot.simulation = False
        device = self.robot.device = Mock()
        self.app.runtime.manual_active = True
        self.robot.axes[0].update(state='HOMING')
        self.assertTrue(self.stop()[0])
        device.stop_gripper.assert_called_once()
        device.stop.assert_not_called()
        self.assertEqual(self.robot.axes[0].snapshot['state'], 'HOMING')

    def test_disconnected_or_failed_stop_never_claims_output_off(self):
        self.robot.simulation = False
        self.assertFalse(self.stop()[0])
        self.assertIsNone(self.robot.gripper.snapshot['drive_enabled'])
        self.robot.device = Mock()
        self.robot.device.stop_gripper.side_effect = TimeoutError('USB')
        self.assertFalse(self.stop()[0])
        self.assertEqual(self.robot.gripper.snapshot['state'], 'ERROR')
        self.assertFalse(self.robot.gripper.snapshot['enabled'])

    def test_output_off_before_other_units_stop_even_when_can_fails(self):
        self.robot.enable(); self.robot.set_gripper(True)
        self.app.runtime.thread = Mock()
        self.app.runtime.thread.is_alive.return_value = True
        def fail_stop():
            self.assertTrue(self.app.runtime.stop_event.is_set())
            self.assertFalse(self.robot.gripper.snapshot['drive_enabled'])
            return False, 'CAN offline'
        with patch.object(self.app.units, 'stop_all', side_effect=fail_stop):
            ok, message = self.stop()
        self.assertFalse(ok)
        self.assertIn('구동 OFF', message)
        self.assertIn('CAN offline', message)

    def test_running_recipe_cannot_restart_gripper_after_stop(self):
        self.assertTrue(self.app.runtime.home()[0])
        self.app.runtime.thread.join(3)
        self.assertIsNone(self.app.state.line["fault"])
        entered, release = threading.Event(), threading.Event()
        recipe={'version':3,'actions':[{'type':'GRIPPER','robot':'P03','target':'CLOSE'},
                                    {'type':'GRIPPER','robot':'P03','target':'OPEN'}]}
        self.app.config.update(lambda c:c.__setitem__('press_recipe',recipe))
        original = self.robot.set_gripper
        calls=[]
        def gripper(closed):
            calls.append(closed)
            result=original(closed)
            entered.set()
            release.wait(3)
            return result
        with patch.object(self.robot,'set_gripper',side_effect=gripper):
            self.assertTrue(self.app.runtime.run_process('P03')[0])
            try:
                self.assertTrue(entered.wait(3))
                self.assertTrue(self.stop()[0])
            finally:
                release.set()
                self.app.runtime.thread.join(3)
        self.assertFalse(self.app.runtime.thread.is_alive())
        self.assertEqual(calls,[True])
        self.assertFalse(self.robot.gripper.snapshot['drive_enabled'])

    def test_waiting_open_is_rejected_after_stop_under_same_lock(self):
        self.robot.enable()
        started=threading.Event()
        results=[]
        def pending():
            started.set(); results.append(self.robot.set_gripper(False))
        with self.robot.command_guard:
            worker=threading.Thread(target=pending)
            worker.start()
            self.assertTrue(started.wait(1))
            self.assertTrue(self.stop()[0])
        worker.join(2)
        self.assertFalse(results[0][0])
        self.assertFalse(self.robot.gripper.snapshot['drive_enabled'])

    def test_registry_gripper_stop_is_not_robot_stop(self):
        with patch.object(self.robot,'stop_gripper',return_value=(True,'off')) as stop, patch.object(self.robot,'stop') as arm:
            self.assertTrue(self.app.units.manual_command(self.robot.gripper.unit_id,'STOP')[0])
            stop.assert_called_once(); arm.assert_not_called()


if __name__=='__main__': unittest.main()
