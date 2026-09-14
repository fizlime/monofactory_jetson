from collections import deque
from types import SimpleNamespace
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mono_press.application import Application
from mono_press.units.can_press.can_link import CanLink, CanError


class Timeout(Exception):
    pass


class Frame:
    @staticmethod
    def unpack_into(frame, raw, timestamp):
        frame.__dict__.update(raw)


def received(can_id, data, echo=0xffffffff):
    return dict(can_id=can_id, data=data, can_dlc=len(data), echo_id=echo,
                arbitration_id=can_id & 0x7ff, is_error_frame=bool(can_id & 0x20000000),
                is_extended_id=False, is_remote_frame=False)


class CanControllerStatusTests(unittest.TestCase):
    def link(self, frames):
        link=CanLink.__new__(CanLink)
        pending=deque(frames)
        def read(*args, **kwargs):
            if pending:return pending.popleft()
            raise Timeout()
        link.raw=SimpleNamespace(read=read)
        link.Frame=Frame
        link.usb=SimpleNamespace(core=SimpleNamespace(USBTimeoutError=Timeout))
        link.node=1;link.stopping=False;link.cancel_requested=lambda:False
        link.config=SimpleNamespace(can_timeout_seconds=.01)
        link._send=Mock();link.controller_notices=0
        return link

    def test_zero_controller_notice_then_valid_motor_reply(self):
        link=self.link([received(0x20000004,bytes(8)), received(1,b'\x3a\x3b',echo=1),received(1,b'\x3a\x01\x3c')])
        self.assertEqual(link.read(0x3a),1)
        self.assertEqual(link.controller_notices,1)
        link._send.assert_called_once_with([0x3a])

    def test_recovery_notice_does_not_count_as_a_motor_ack(self):
        link=self.link([received(0x20000004,b'\0\x40'+bytes(6))])
        with self.assertRaisesRegex(CanError,'시간 초과'):link.read(0x3a)

    def test_repeated_empty_notices_cannot_make_motion_succeed_or_retry(self):
        link=self.link([received(0x20000004,bytes(8)) for _ in range(8)])
        with self.assertRaisesRegex(CanError,'시간 초과'):link.request([0xf5,0,1,1,0,0,1])
        self.assertEqual(link._send.call_count,1)

    def test_real_faults_and_malformed_notices_still_abort(self):
        faults=[(0x20000040,bytes(8)),(0x20000020,bytes(8)),(0x20000008,bytes(8)),
                (0x20000004,b'\0\x10'+bytes(6)),(0x20000004,b'\0\x01'+bytes(6)),
                (0x20000004,bytes(7)),(0x20000004,bytes(6)+b'\x01\0'),
                (0x20000044,b'\0\x40'+bytes(6))]
        for can_id,data in faults:
            with self.subTest(can_id=can_id,data=data):
                link=self.link([received(can_id,data),received(1,b'\x3a\x01\x3c')])
                with self.assertRaisesRegex(CanError,'CAN 오류 프레임 ID='):link.read(0x3a)

    def test_notice_does_not_bypass_motor_checksum_or_expected_command(self):
        link=self.link([received(0x20000004,bytes(8)),received(1,b'\x3a\x01\0')])
        with self.assertRaisesRegex(CanError,'체크섬'):link.read(0x3a)
        link=self.link([received(0x20000004,bytes(8)),received(1,b'\x3e\0\x3f')])
        with self.assertRaisesRegex(CanError,'시간 초과'):link.read(0x3a)

    def test_failed_handshake_releases_usb_before_next_connect(self):
        with tempfile.TemporaryDirectory() as tmp:
            app=Application(Path(tmp),'/dev/not-used',38400,True)
            driver=app.units.press.can
            bad,good=Mock(),Mock()
            bad.ready.side_effect=CanError('handshake failed')
            good.sample.return_value={'io':1,'position':0}
            with patch('mono_press.units.can_press.driver.CanLink',side_effect=[bad,good]):
                try:
                    self.assertFalse(driver.connect())
                    bad.close.assert_called_once();bad.stop.assert_not_called()
                    self.assertTrue(driver.connect())
                    self.assertTrue(driver.connected)
                finally:driver.close()
            good.close.assert_called_once()


if __name__=='__main__':unittest.main()
