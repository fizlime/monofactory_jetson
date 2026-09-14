"""No hardware: fake serial frames, simulated API, and cancellation races."""
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


class FakeSerial:
    def __init__(self, **kwargs):
        self.frames = []
        self.pending = bytearray()
    def open(self): pass
    def close(self): pass
    def reset_input_buffer(self): self.pending.clear()
    @property
    def in_waiting(self): return len(self.pending)
    def write(self, frame):
        self.frames.append(frame)
        payload = frame[3:5]
        if payload[0] == 10:
            payload += struct.pack('<8f', 200, 20, 120, 19, 19, 1, 3, 0)
        elif payload[0] == 20:
            payload += bytes([0, 0, 4] + [0] * 13)
        # Fragmented frames and leading noise exercise synchronization.
        self.pending.extend(b'noise' + b'\xaa\xaa' + bytes([len(payload)]) + payload + bytes([-sum(payload) & 255]))
    def read(self, count):
        result = self.pending[:min(count, 3)]
        del self.pending[:len(result)]
        return bytes(result)


class DobotManualTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.app = Application(Path(tmp.name), '/dev/not-used', 38400, True)
        with patch('mono_press.http_server.ThreadingHTTPServer', side_effect=lambda addr, handler: handler):
            self.handler = make_server(self.app)
        self.robot = self.app.units.scara

    def post(self, path, **body):
        return self.handler.route_post(None, path, body)

    def test_official_wire_modes_pose_and_checksum(self):
        driver = DobotSerial('/dev/fake', FakeSerial)
        self.assertEqual(driver.pose(), ([200, 20, 120, 19], [19, 1, 3, 0]))
        self.assertEqual(driver.alarms(), [18])
        for mode, number in [('JOINT', 4)]:
            driver.move(mode, [1, 2, 3, 4], 10)
            frame = driver.serial.frames[-1]
            self.assertEqual(frame[2:6], bytes([19, 84, 1, number]))
            self.assertEqual(struct.unpack('<4f', frame[6:-1]), (1, 2, 3, 4))
            self.assertEqual(sum(frame[3:]) & 255, 0)
        driver.stop()
        self.assertEqual(driver.serial.frames[-1], b'\xaa\xaa\x02\xf2\x01\x0d')

    def test_lost_write_ack_is_never_retried(self):
        driver = DobotSerial('/dev/fake', FakeSerial)
        driver.serial.read = lambda count: b''
        with patch('mono_press.units.dobot_serial.time.monotonic', side_effect=[0, 0, 2]):
            with self.assertRaises(TimeoutError):
                driver.request(84, write=True)
        self.assertEqual(len(driver.serial.frames), 1)

    def test_joint_movement_and_jog_reject_xyz_without_changing_pose(self):
        self.robot.enable()
        self.assertTrue(self.post('/api/scara/P03/move', mode='JOINT', values=[1, 2, 3, 4])[0])
        self.assertFalse(self.post('/api/scara/P03/move', mode='XYZR', values=[20, 30, 40, 5])[0])
        self.assertFalse(self.post('/api/scara/P03/jog', mode='XYZR', axis=2, delta=-1)[0])
        self.assertEqual(self.robot.current_position(), [1, 2, 3, 4])
        self.assertEqual(self.app.units.scara_p05.current_position(), [0] * 4)
        self.assertTrue(self.post('/api/scara/P03/jog', mode='JOINT', axis=2, delta=-1)[0])
        self.assertEqual(self.robot.current_position(), [1, 1, 3, 4])

    def test_invalid_targets_do_not_move(self):
        self.robot.enable()
        for values, mode in [([float('nan')] * 4, 'JOINT'), ([float('inf')] * 4, 'XYZR'), ([1], 'JOINT'), ([1] * 4, 'INVALID')]:
            self.assertFalse(self.post('/api/scara/P03/move', mode=mode, values=values)[0])
        for axis, delta in [(0, 1), (5, 1), (1, float('nan')), (1, 0), (1, 11)]:
            self.assertFalse(self.post('/api/scara/P03/jog', axis=axis, delta=delta)[0])
        self.assertEqual(self.robot.current_position(), [0] * 4)

    def test_saved_pose_records_mode_and_simulation_source(self):
        self.robot.enable()
        self.robot.manual_move([1, 2, 3, 4], 'JOINT')
        self.assertTrue(self.post('/api/scara/P03/save-current', mode='JOINT', name='CART')[0])
        meta = self.app.config.snapshot()['scara']['position_meta']['CART']
        self.assertEqual(meta, {'mode': 'JOINT', 'source': 'SIMULATION'})
        self.robot.manual_move([0] * 4, 'JOINT')
        self.assertTrue(self.robot.move_saved('CART')[0])
        self.assertEqual(self.robot.current_position(), [1, 2, 3, 4])
        self.robot.simulation = False
        with patch.object(self.robot, 'move_position') as move:
            self.assertFalse(self.robot.move_saved('CART')[0])
            self.assertFalse(self.robot.move_saved('HOME')[0])
            move.assert_not_called()

    def test_missing_real_device_never_reports_simulated_success(self):
        self.assertFalse(self.robot.set_simulation(False))
        self.assertFalse(self.robot.enable()[0])
        self.assertFalse(self.robot.axes[0].snapshot['simulated'])
        self.assertFalse(self.robot.axes[0].snapshot['pose_valid'])
        self.assertFalse(self.robot.manual_move([1] * 4)[0])

    def test_stop_cancels_manual_move_and_auto_cannot_race(self):
        self.robot.enable()
        results = []
        worker = threading.Thread(target=lambda: results.append(self.post('/api/scara/P03/move', mode='JOINT', values=[100] * 4, speed=1)))
        worker.start()
        deadline = time.monotonic() + 1
        while self.robot.axes[0].snapshot['state'] != 'MOVING' and time.monotonic() < deadline:
            time.sleep(0.001)
        self.assertTrue(self.app.runtime.busy())
        self.assertFalse(self.app.runtime.start()[0])
        self.assertFalse(self.post('/api/scara/P05/move', mode='JOINT', values=[1] * 4)[0])
        self.assertTrue(self.post('/api/unit/P03_SCARA_J1/command', action='STOP')[0])
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(results[0][0])
        self.assertEqual(self.robot.current_position(), [0] * 4)

    def test_real_motion_checks_alarm_and_completion_not_just_ack(self):
        self.robot.enable()
        self.robot.simulation = False
        device = self.robot.device = Mock()
        device.pose.return_value = ([100, 20, 30, 4], [1, 2, 3, 4])
        device.alarms.return_value = [68]
        self.assertFalse(self.robot.manual_move([1, 2, 3, 4])[0])
        device.move.assert_not_called()
        device.alarms.return_value = []
        self.robot._status(enabled=True)
        self.assertTrue(self.robot.manual_move([1, 2, 3, 4])[0])
        device.move.assert_called_once_with('JOINT', [1, 2, 3, 4], 10)
        self.assertGreaterEqual(device.pose.call_count, 4)


if __name__ == '__main__':
    unittest.main()
