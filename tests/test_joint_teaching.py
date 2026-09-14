import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from mono_press.application import Application
from mono_press.core.robot_command import validate_dobot_move
from mono_press.http_server import make_server
from mono_press.units.dobot_serial import DobotSerial
from test_dobot_manual import FakeSerial


class JointTeachingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.app = Application(Path(temp.name), '/dev/not-used', 38400, True)
        self.app.config.update(lambda cfg: cfg['scara_p05'].update(shared_with='P03'))
        # Construct the shared facade using the saved shared configuration.
        self.app = Application(Path(temp.name), '/dev/not-used', 38400, True)
        with patch('mono_press.http_server.ThreadingHTTPServer', side_effect=lambda addr, handler: handler):
            self.handler = make_server(self.app)

    def test_real_joint_feedback_saved_and_replayed_in_p05_namespace(self):
        robot = self.app.units.scara
        robot.simulation = False
        robot.device = Mock()
        robot.device.pose.return_value = ([210, 50, 90, 12], [15, 25, 35, 45])
        result = self.handler.route_post(None, '/api/scara/P05/save-current', {'name':'TAUGHT'})
        self.assertTrue(result[0])
        cfg = self.app.config.snapshot()
        self.assertEqual(cfg['scara_p05']['positions']['TAUGHT'], [15,25,35,45])
        self.assertEqual(cfg['scara_p05']['position_meta']['TAUGHT']['mode'], 'JOINT')
        self.assertNotIn('TAUGHT', cfg['scara']['positions'])
        with patch.object(robot, 'move_position') as move:
            self.assertTrue(self.app.units.scara_p05.move_saved('TAUGHT')[0])
            self.assertEqual(move.call_args.args[0], [15,25,35,45])
            self.assertEqual(move.call_args.args[2], 'JOINT')

    def test_xyz_values_never_relabelled_or_sent_as_angles(self):
        self.app.config.update(lambda cfg: cfg['scara'].setdefault('position_meta', {}).update(
            HOME={'mode':'XYZR','source':'SIMULATION'}))
        with patch.object(self.app.units.scara,'move_position') as move:
            self.assertFalse(self.app.units.scara.move_saved('HOME')[0])
            move.assert_not_called()
        before = self.app.config.snapshot()
        self.assertFalse(self.handler.route_post(None,'/api/scara/P03/save-current',{'name':'XYZ','mode':'XYZR'})[0])
        self.assertEqual(self.app.config.snapshot(),before)
        with self.assertRaises(ValueError):
            validate_dobot_move({'robot':'P03','mode':'XYZR','values':[200,0,50,0],'speed':10})
        driver=DobotSerial('/dev/fake',FakeSerial)
        with self.assertRaises(ValueError):driver.move('XYZR',[200,0,50,0],10)
        self.assertEqual(driver.serial.frames,[])

    def test_teach_holds_manual_reservation_and_failed_read_does_not_save(self):
        robot=self.app.units.scara
        def fail():
            self.assertTrue(self.app.runtime.busy())
            raise TimeoutError('pose unavailable')
        with patch.object(robot,'pose_values',side_effect=lambda mode:fail()):
            with self.assertRaises(TimeoutError):
                self.handler.route_post(None,'/api/scara/P03/save-current',{'name':'FAILED'})
        self.assertFalse(self.app.runtime.busy())
        self.assertNotIn('FAILED',self.app.config.snapshot()['scara']['positions'])


if __name__ == '__main__':unittest.main()
