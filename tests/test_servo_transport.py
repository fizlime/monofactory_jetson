import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mono_press.application import Application
from mono_press.units.uart_framing import MarkedInputDecoder


class ServoTransportTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.app = Application(Path(tmp.name), '/dev/not-used', 38400, True)
        self.bus = self.app.bus
        self.axis = self.app.units.p01_axes[0]
        self.addCleanup(self.app.units.material.close)

    def read_chunks(self, chunks):
        chunks = list(chunks)
        def read(_):
            if chunks: return chunks.pop(0)
            self.bus.reader_stop.set()
            return b''
        self.bus.reader_stop.clear()
        self.bus.device = Mock()
        self.bus.device.read.side_effect = read
        self.bus._reader_loop()

    def test_error_marker_and_literal_ff_across_every_fragment_boundary(self):
        data = b'\xe0\xff\xff\xff\x00\x00\x01\xe1'
        for split in range(len(data)+1):
            decoder = MarkedInputDecoder()
            got = list(decoder.decode(data[:split])) + list(decoder.decode(data[split:]))
            self.assertEqual(got, [0xe0, 0xff, None, 1, 0xe1])

    def test_corrupt_character_breaks_a_possible_ack_frame(self):
        self.bus.marked_input = True
        self.bus.waiting_commands.add(0xe0)
        # Without the boundary, E0 01 E1 would falsely acknowledge a command.
        self.read_chunks([b'\xe0\xff', b'\x00\x99\x01\xe1', b'\xe0\x01\xe1'])
        self.assertEqual(len(self.bus.events[0xe0]), 1)
        self.assertEqual(self.bus.framing_errors, 1)

    def test_first_query_reply_survives_later_checksum_coincidence(self):
        query = {'size':4, 'data':None}
        self.bus.pending_queries[0xe0] = query
        self.read_chunks([bytes.fromhex('e0 00 00 01 00 e1 e0 00 00 02 00 e2')])
        self.assertEqual(query['data'], bytes.fromhex('00 00 01 00'))

    def test_signed_angle_with_escaped_ff_is_preserved(self):
        self.bus.marked_input = True
        query = {'size':4, 'data':None}
        self.bus.pending_queries[0xe0] = query
        frame = bytes.fromhex('e0 ff ff ff f8 d5').replace(b'\xff', b'\xff\xff')
        self.read_chunks([frame[:3], frame[3:7], frame[7:]])
        self.assertEqual(int.from_bytes(query['data'], 'big', signed=True), -8)

    def test_completion_without_start_is_accepted_without_resending_move(self):
        self.axis.update(enabled=True)
        def send(address, body, label):
            self.bus._dispatch(address, 2)
            return True, 0, 'TX'
        with patch.object(self.bus, 'write', side_effect=send) as write:
            self.assertTrue(self.axis.move_blocking(10))
            write.assert_called_once()
        self.assertEqual(self.axis.snapshot['position'], 10)
        self.assertEqual(self.axis.snapshot['state'], 'READY')

    def test_timeout_retries_enable_but_never_relative_move(self):
        with patch.object(self.bus, 'write', return_value=(True,0,'')) as write, patch.object(self.bus, 'wait_event', return_value=None):
            self.assertFalse(self.bus.command(0xe0,[0xf3,1],'ENABLE',.01)[0])
            self.assertEqual(write.call_count, 2)
            write.reset_mock()
            self.assertFalse(self.bus.command(0xe0,[0xfd,16,0,0,0,10],'MOVE',.01)[0])
            self.assertEqual(write.call_count, 1)

    def test_explicit_rejection_is_not_retried(self):
        with patch.object(self.bus, 'write', return_value=(True,0,'')) as write, patch.object(self.bus, 'wait_event', return_value={'id':1,'status':0}):
            self.assertFalse(self.bus.command(0xe0,[0xf3,1],'ENABLE',.01)[0])
            write.assert_called_once()

    def test_cancel_wins_over_already_received_completion(self):
        self.bus._dispatch(0xe0, 2)
        self.axis.cancel.set()
        self.assertIsNone(self.bus.wait_event(0xe0,2,0,1,self.axis.cancel))
        with patch.object(self.bus,'write') as write:
            self.assertEqual(self.bus.command(0xe0,[0xfd,1,0,0,0,1],'MOVE',1,self.axis.cancel)[1], 'CANCELLED')
            write.assert_not_called()

    def test_longer_entered_distance_gets_sufficient_completion_timeout(self):
        self.axis.update(enabled=True)
        with patch.object(self.bus,'command',return_value=(True,'ACK',1)), patch.object(self.bus,'wait_event',return_value={'at':time.monotonic()}) as wait:
            self.assertTrue(self.axis.move_blocking(80000))
            self.assertGreaterEqual(wait.call_args.args[3],16)

    def test_probe_confirms_en_without_requiring_angle_response(self):
        self.bus.simulation = False
        values = [None, b'\x00', b'\x01', b'\x01']
        with patch.object(self.bus,'connect',return_value=True), patch.object(self.bus,'_query',side_effect=values) as query:
            self.assertTrue(self.bus.probe(0xe0)[0])
            self.assertEqual([c.args[1] for c in query.call_args_list], [0x3a]*4)
            self.assertEqual(self.bus.probe_status[0xe0], {'enabled':True})

    def test_probe_confirms_disabled_and_rejects_unconfirmed_en(self):
        self.bus.simulation = False
        with patch.object(self.bus,'connect',return_value=True), patch.object(self.bus,'_query',side_effect=[b'\x02',b'\x02']):
            self.assertTrue(self.bus.probe(0xe0)[0])
            self.assertFalse(self.bus.probe_status[0xe0]['enabled'])
        with patch.object(self.bus,'connect',return_value=True), patch.object(self.bus,'_query',side_effect=[None,b'\x01',None,b'\x02']):
            self.assertFalse(self.bus.probe(0xe0)[0])
            self.assertNotIn(0xe0,self.bus.probe_status)

    def test_all_four_axes_send_the_v7_enable_and_relative_move_frames(self):
        # Literal bytes from the v7 driver: 800 pulses, speed gear 16.
        expected = [
            ('e0 f3 01 d4', 'e0 fd 10 00 00 03 20 10', 'e0 fd 90 00 00 03 20 90'),
            ('e1 f3 01 d5', 'e1 fd 10 00 00 03 20 11', 'e1 fd 90 00 00 03 20 91'),
            ('e2 f3 01 d6', 'e2 fd 10 00 00 03 20 12', 'e2 fd 90 00 00 03 20 92'),
            ('e3 f3 01 d7', 'e3 fd 10 00 00 03 20 13', 'e3 fd 90 00 00 03 20 93'),
        ]
        self.bus.simulation=False
        self.bus.device=Mock()
        self.app.config.update(lambda cfg: cfg['p01'].update(speed_gear=16))
        with patch.object(self.bus,'connect',return_value=True), patch.object(self.bus,'wait_event',return_value={'id':1,'status':1,'at':time.monotonic()}):
            for axis, frames in zip(self.app.units.p01_axes,expected):
                self.bus.device.write.reset_mock()
                self.assertTrue(axis.enable()[0])
                self.assertTrue(axis.move_blocking(800))
                self.assertTrue(axis.move_blocking(-800))
                self.assertEqual([c.args[0] for c in self.bus.device.write.call_args_list],[bytes.fromhex(f) for f in frames])

    def test_check_updates_display_from_verified_physical_en(self):
        self.bus.probe_status[0xe0] = {'enabled':True,'angle_raw':0}
        with patch.object(self.bus,'probe',return_value=(True,'read')):
            self.app.check_p01()
        self.assertTrue(self.axis.snapshot['enabled'])
        self.assertEqual(self.axis.snapshot['state'],'READY')

    def test_command_response_phases_are_serialized(self):
        entered=threading.Event();release=threading.Event();seen=[]
        def command_once(address,*args):
            seen.append(address)
            if address==0xe0:
                entered.set()
                release.wait(1)
            return True,'ACK',1
        with patch.object(self.bus,'_command_once',side_effect=command_once):
            first=threading.Thread(target=self.bus.command,args=(0xe0,[0xf3,1],'ENABLE',1))
            second=threading.Thread(target=self.bus.command,args=(0xe1,[0xf3,1],'ENABLE',1))
            first.start();self.assertTrue(entered.wait(1));second.start()
            self.assertEqual(seen,[0xe0])
            release.set();first.join(1);second.join(1)
        self.assertEqual(seen,[0xe0,0xe1])

    def test_forced_diagnostics_do_not_clear_recent_error_rate(self):
        self.bus.last_diagnostics=100
        self.bus.framing_errors=400
        with patch('mono_press.units.servo_bus.time.monotonic',side_effect=[101,101.01]):
            self.bus.publish_diagnostics()
            self.bus.publish_diagnostics(force=True)
        self.assertEqual(self.app.state.serial['rx_error_rate'],400)

    def test_v7_raw_input_is_default_and_still_reports_kernel_errors(self):
        self.assertFalse(self.bus.mark_errors)
        self.bus.device=Mock()
        self.bus.kernel_frame_count=100
        self.bus.last_diagnostics=100
        with patch('mono_press.units.servo_bus.frame_error_count',return_value=120), patch('mono_press.units.servo_bus.time.monotonic',return_value=101):
            self.bus.publish_diagnostics()
        self.assertEqual(self.app.state.serial['rx_frame_errors'],20)
        self.assertEqual(self.app.state.serial['rx_error_rate'],20)

    def test_stop_is_sent_before_waiting_for_confirmation(self):
        calls=[]
        with patch.object(self.bus,'emergency_stop',side_effect=lambda address:calls.append('immediate') or True), patch.object(self.bus,'command',side_effect=lambda *args:calls.append('confirm') or (True,'ACK',1)):
            self.assertTrue(self.axis.stop()[0])
        self.assertEqual(calls,['immediate','confirm'])


if __name__=='__main__': unittest.main()
