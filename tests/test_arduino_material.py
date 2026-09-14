import itertools
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mono_press.application import Application
from mono_press.core.config import DEFAULT_CONFIG, validate_config
from mono_press.units.arduino_material import ArduinoMaterialInput, parse_sensor_line


def packet(values):
    return (' | '.join(f"센서 {i+1}: {'감지' if value else '미감지'}" for i, value in enumerate(values)) + '\r\n').encode('utf-8')


class ArduinoInputTests(unittest.TestCase):
    def setUp(self):
        self.reader = ArduinoMaterialInput()
        self.addCleanup(self.reader.close)
        self.cfg = DEFAULT_CONFIG['p00']
        self.port = Mock(in_waiting=4096)
        self.clock = 100.
        self.serial_patch = patch('mono_press.units.arduino_material.serial.Serial', return_value=self.port)
        self.factory = self.serial_patch.start()
        self.addCleanup(self.serial_patch.stop)
        clock_patch = patch('mono_press.units.arduino_material.time.monotonic', side_effect=lambda: self.clock)
        clock_patch.start()
        self.addCleanup(clock_patch.stop)

    def test_parser_requires_four_exact_channels(self):
        for values in itertools.product((False, True), repeat=4):
            self.assertEqual(parse_sensor_line(packet(values).decode().strip()), list(values))
        self.assertIsNone(parse_sensor_line('센서 4개 확인 시작'))
        for bad in ('센서 1: 감지', '감지', '센서 1: 감지 | 센서 1: 감지 | 센서 3: 감지 | 센서 4: 감지'):
            with self.assertRaises(ValueError): parse_sensor_line(bad)

    def test_fragmented_utf8_startup_and_cached_read(self):
        data = packet([True, False, True, False])
        self.port.read.side_effect = ['센서 4개 확인 시작\r\n'.encode(), data[:2], data[2:], b'']
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, '대기'): self.reader.read(self.cfg)
        self.assertEqual(self.reader.read(self.cfg), ([True, False, True, False], True))
        self.assertEqual(self.reader.read(self.cfg), ([True, False, True, False], False))
        self.factory.assert_called_once_with(self.cfg['serial_port'], 115200, timeout=0, write_timeout=.5, exclusive=True)
        self.port.write.assert_not_called()

    def test_stale_data_clears_values_and_reconnects(self):
        self.port.read.side_effect = [packet([True]*4), b'', packet([False]*4)]
        self.reader.read(self.cfg)
        self.clock += 3.1
        with self.assertRaisesRegex(RuntimeError, '시간 초과'): self.reader.read(self.cfg)
        self.assertIsNone(self.reader.values)
        self.assertIsNone(self.reader.device)
        self.clock += 1.1
        self.assertEqual(self.reader.read(self.cfg), ([False]*4, True))
        self.assertEqual(self.factory.call_count, 2)

    def test_corruption_and_unplug_do_not_retain_permission(self):
        self.port.read.side_effect = [packet([True]*4), b'broken\n']
        self.reader.read(self.cfg)
        with self.assertRaises(ValueError): self.reader.read(self.cfg)
        self.assertIsNone(self.reader.values)
        self.clock += 2
        self.port.read.side_effect = OSError('USB unplugged')
        with self.assertRaises(OSError): self.reader.read(self.cfg)
        self.assertIsNone(self.reader.device)

    def test_config_rejects_invalid_port_and_timeout(self):
        for change in ({'serial_port':'/etc/passwd'}, {'serial_port':'/dev/ttyUSB0;bad'}, {'stale_timeout':0}, {'serial_baud':12345}):
            with self.assertRaises(ValueError): validate_config({'p00':change})

    def test_bad_line_revokes_permission_then_recovers_without_resetting_uno(self):
        self.port.read.side_effect = [packet([True]*4), b'\xffpartial\n'+packet([False,True,False,False]), b'']
        self.reader.read(self.cfg)
        with self.assertRaises(ValueError): self.reader.read(self.cfg)
        self.assertIsNone(self.reader.values)
        self.assertIs(self.reader.device,self.port)
        self.port.close.assert_not_called()
        self.assertEqual(self.reader.read(self.cfg),([False,True,False,False],True))
        self.assertEqual(self.factory.call_count,1)
        self.assertEqual(self.reader.invalid_frames,1)

    def test_reboot_banner_revokes_cached_permission(self):
        self.port.read.side_effect = [packet([True]*4),'센서 4개 확인 시작\n'.encode(),b'',packet([False]*4)]
        self.reader.read(self.cfg)
        generation=self.reader.generation
        with self.assertRaisesRegex(RuntimeError,'대기'): self.reader.read(self.cfg)
        self.assertIsNone(self.reader.values)
        self.assertGreater(self.reader.generation,generation)
        with self.assertRaisesRegex(RuntimeError,'대기'): self.reader.read(self.cfg)
        self.assertEqual(self.reader.read(self.cfg),([False]*4,True))
        self.factory.assert_called_once()

    def test_open_port_without_first_frame_times_out(self):
        self.port.read.return_value=b''
        with self.assertRaisesRegex(RuntimeError,'대기'): self.reader.read(self.cfg)
        self.clock+=3.1
        with self.assertRaisesRegex(RuntimeError,'시간 초과'): self.reader.read(self.cfg)
        self.assertIsNone(self.reader.device)

    def test_cached_poll_does_not_change_real_receive_timestamp(self):
        self.port.read.side_effect=[packet([False]*4),b'']
        with patch('mono_press.units.arduino_material.time.time',return_value=123):
            self.reader.read(self.cfg)
        with patch('mono_press.units.arduino_material.time.time',return_value=124):
            self.reader.read(self.cfg)
        self.assertEqual(self.reader.received_at,123)

    def test_spacing_changes_keep_channel_order_strict(self):
        self.assertEqual(parse_sensor_line('센서1:감지|센서 2 : 미감지 | 센서3:감지 |센서4:미감지'),[True,False,True,False])


class FourMaterialTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.app = Application(Path(tmp.name), '/dev/not-used', 38400, True)
        self.sensor = self.app.units.material
        self.addCleanup(self.sensor.close)
        self.app.config.update(lambda cfg: cfg['p00'].update(driver='arduino_serial'))
        self.app.config.update(lambda cfg: cfg['p01']['axes']['E3'].update(installed=False))
        self.sensor.simulated = False

    def test_all_four_required_even_when_fourth_motor_excluded(self):
        for values in itertools.product((False, True), repeat=4):
            self.sensor.config_key = None
            with patch.object(self.sensor.arduino, 'read', return_value=(list(values), True)), patch('mono_press.units.material_sensor.time.monotonic', side_effect=[100, 101]):
                self.assertFalse(self.sensor.refresh())
                self.assertEqual(self.sensor.refresh(), all(values))
                self.assertEqual(self.sensor.snapshot['detected_count'], sum(values))
                self.assertEqual([c['pin'] for c in self.sensor.snapshot['channels']], [2,3,4,5])

    def test_absence_and_connection_loss_block_forward_before_command(self):
        axis = self.app.units.p01_axes[0]
        axis.update(enabled=True)
        with patch.object(self.sensor.arduino, 'read', return_value=([True]*4, True)), patch('mono_press.units.material_sensor.time.monotonic', side_effect=[100,101]):
            self.sensor.refresh()
            self.assertTrue(self.sensor.refresh())
        with patch.object(self.sensor.arduino, 'read', return_value=([True,True,True,False], True)):
            self.assertFalse(axis.start_move(10)[0])
            self.assertFalse(axis.start_cycle(10)[0])
        with patch.object(self.sensor.arduino, 'read', side_effect=RuntimeError('USB disconnected')):
            self.assertFalse(self.sensor.require_material()[0])
            self.assertEqual(self.sensor.snapshot['detected_count'], 0)
            self.assertFalse(self.sensor.snapshot['connected'])

    def test_reboot_with_new_frame_restarts_debounce(self):
        with patch.object(self.sensor.arduino,'read',return_value=([True]*4,True)), patch('mono_press.units.material_sensor.time.monotonic',side_effect=[100,101,102,103]):
            self.sensor.refresh()
            self.assertTrue(self.sensor.refresh())
            self.sensor.arduino.generation+=1
            self.assertFalse(self.sensor.refresh())
            self.assertTrue(self.sensor.refresh())

    def test_usb_open_waiting_for_data_is_distinct_from_unplugged(self):
        self.sensor.config_key=tuple(sorted(self.app.config.snapshot()['p00'].items()))
        self.sensor.arduino.device=Mock()
        with patch.object(self.sensor.arduino,'read',side_effect=RuntimeError('데이터 대기')):
            self.assertFalse(self.sensor.refresh())
            self.assertEqual(self.sensor.snapshot['state'],'WAITING_DATA')
            self.assertTrue(self.sensor.snapshot['transport_connected'])
            self.assertFalse(self.sensor.snapshot['connected'])
        self.sensor.arduino.device=None
        with patch.object(self.sensor.arduino,'read',side_effect=RuntimeError('USB 미연결')):
            self.assertFalse(self.sensor.refresh())
            self.assertEqual(self.sensor.snapshot['state'],'OFFLINE')
            self.assertFalse(self.sensor.snapshot['transport_connected'])


if __name__ == '__main__':
    unittest.main()
