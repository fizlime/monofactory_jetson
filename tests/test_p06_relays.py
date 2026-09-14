import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mono_press.application import Application
from mono_press.core.config import validate_config
from mono_press.units.arduino_relays import ArduinoRelays


SENSORS = '센서 1: 미감지 | 센서 2: 미감지 | 센서 3: 미감지 | 센서 4: 미감지\n'.encode()


class FakeUno:
    def __init__(self):
        self.buffer = bytearray(b'P06 READY 1\nP06 STATE 0 LOW 0\n'+SENSORS)
        self.writes = []
        self.is_open = True
        self.reply = True

    @property
    def in_waiting(self): return len(self.buffer)

    def read(self, size):
        data = bytes(self.buffer[:size]);del self.buffer[:size];return data

    def write(self, data):
        self.writes.append(data)
        if data.startswith(b'P06 SET ') and self.reply:
            self.buffer.extend(data.replace(b'P06 SET ',b'P06 ACK ')+SENSORS)
        return len(data)

    def close(self): self.is_open = False


class RelayProtocolTests(unittest.TestCase):
    def test_only_matching_ack_confirms_requested_output(self):
        relay=ArduinoRelays();device=Mock()
        self.assertFalse(relay.consume(SENSORS.decode().strip()))
        relay.consume('P06 READY 1');relay.consume('P06 STATE 0 LOW 0')
        wanted=relay.request(device,5,'LOW')
        self.assertEqual(device.write.call_args.args[0],b'P06 SET 1 LOW 5\n')
        relay.consume('P06 ACK 2 LOW 5');self.assertIsNone(relay.ack)
        relay.consume('P06 ACK 1 HIGH 5');self.assertIsNone(relay.ack)
        relay.consume('P06 ACK 1 LOW 7');self.assertIsNone(relay.ack)
        relay.consume('P06 ACK 1 LOW 5');self.assertEqual(relay.ack,wanted)
        self.assertEqual(relay.mask,5)

    def test_missing_firmware_or_wrong_polarity_never_sends_on(self):
        relay=ArduinoRelays();device=Mock()
        with self.assertRaisesRegex(RuntimeError,'펌웨어'):relay.request(device,1,'LOW')
        relay.consume('P06 STATE 0 HIGH 0')
        with self.assertRaisesRegex(RuntimeError,'HIGH'):relay.request(device,1,'LOW')
        device.write.assert_not_called()

    def test_watchdog_off_report_cancels_keepalive_and_never_restarts_output(self):
        relay=ArduinoRelays();device=Mock()
        relay.consume('P06 STATE 0 LOW 0');relay.request(device,7,'LOW');relay.consume('P06 ACK 1 LOW 7')
        device.write.reset_mock();relay.tick(device)
        self.assertEqual(device.write.call_args.args[0],b'P06 KEEP 1\n')
        relay.consume('P06 STATE 1 LOW 0');device.write.reset_mock();relay.tick(device)
        device.write.assert_not_called();self.assertFalse(relay.keepalive)

    def test_late_ack_after_timeout_cannot_restart_keepalive(self):
        relay=ArduinoRelays();device=Mock()
        relay.consume('P06 READY 1');relay.request(device,7,'LOW');relay.abandon(device)
        relay.consume('P06 ACK 1 LOW 7')
        self.assertIsNone(relay.ack);self.assertFalse(relay.keepalive)
        self.assertEqual(device.write.call_args.args[0],b'P06 OFF\n')


class P06Tests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.app=Application(Path(tmp.name),'/dev/not-used',38400,True)
        self.light=self.app.units.light;self.material=self.app.units.material
        self.addCleanup(self.material.close)

    def test_simulation_independent_channels_and_all_off(self):
        for unit in self.light.channels:
            self.assertTrue(self.app.units.manual_command(unit.unit_id,'ON')[0])
        self.assertEqual([u.snapshot['output'] for u in self.light.channels],[True]*3)
        self.app.units.manual_command(self.light.red.unit_id,'CHANNEL_OFF')
        self.assertEqual([u.snapshot['output'] for u in self.light.channels],[True,False,True])
        self.assertTrue(self.light.set('OFF')[0])
        self.assertEqual([u.snapshot['output'] for u in self.light.channels],[False]*3)
        self.assertEqual([u.snapshot['pin'] for u in self.light.channels],[8,9,10])

    def test_unconfigured_real_outputs_are_blocked_without_touching_usb(self):
        self.material.simulated=False
        with patch.object(self.material,'set_relays') as send:
            self.assertFalse(self.light.set('GREEN')[0])
            self.assertFalse(self.light.set_channel(self.light.buzzer.unit_id,True)[0])
            self.light.set('OFF');send.assert_not_called()
        self.assertFalse(self.light.green.snapshot['simulated'])
        self.assertEqual(self.light.green.snapshot['state'],'UNCONFIGURED')

    def test_sensor_and_relay_share_one_port_and_zero_material_does_not_block_lights(self):
        self.app.config.update(lambda c:(c['p00'].update(driver='arduino_serial'),c['p06'].update(relay_active_level='LOW')))
        self.material.simulated=False
        uno=FakeUno()
        with patch('mono_press.units.arduino_material.serial.Serial',return_value=uno) as factory:
            self.material.refresh()
            self.assertTrue(self.light.set_channel(self.light.green.unit_id,True)[0])
            self.assertTrue(self.light.set_channel(self.light.buzzer.unit_id,True)[0])
            self.assertEqual([u.snapshot['output'] for u in self.light.channels],[True,False,True])
            self.assertTrue(self.material.snapshot['connected'])
            self.assertFalse(self.material.snapshot['detected'])
            self.assertEqual(self.material.snapshot['invalid_frames'],0)
            factory.assert_called_once()
            self.assertEqual([x for x in uno.writes if x.startswith(b'P06 SET')],[b'P06 SET 1 LOW 1\n',b'P06 SET 2 LOW 5\n'])
            self.light.set('OFF')

    def test_watchdog_status_updates_units_and_line_without_faking_ack(self):
        self.app.config.update(lambda c:c['p06'].update(relay_active_level='LOW'))
        self.material.simulated=False;self.material.arduino.device=Mock()
        relay=self.material.arduino.relays
        relay.consume('P06 STATE 1 LOW 5');self.light.refresh()
        self.assertEqual(self.app.state.line['lamp'],'GREEN');self.assertTrue(self.app.state.line['buzzer'])
        relay.consume('P06 STATE 1 LOW 0');self.light.refresh()
        self.assertEqual(self.app.state.line['lamp'],'OFF');self.assertFalse(self.app.state.line['buzzer'])
        relay.reset();self.light.refresh();self.assertIsNone(self.light.green.snapshot['output'])

    def test_missing_ack_does_not_claim_on_and_requests_off(self):
        self.app.config.update(lambda c:(c['p00'].update(driver='arduino_serial'),c['p06'].update(relay_active_level='LOW')))
        self.material.simulated=False
        uno=FakeUno();uno.reply=False
        with patch('mono_press.units.arduino_material.serial.Serial',return_value=uno):
            self.material.refresh()
            self.assertFalse(self.light.set('GREEN')[0])
            self.assertIsNone(self.light.green.snapshot['output'])
            self.assertFalse(self.material.arduino.relays.keepalive)
            self.assertEqual(uno.writes[-1],b'P06 OFF\n')

    def test_all_off_uses_firmware_level_even_if_ui_level_is_unset(self):
        self.app.config.update(lambda c:c['p00'].update(driver='arduino_serial'))
        self.material.simulated=False
        uno=FakeUno()
        with patch('mono_press.units.arduino_material.serial.Serial',return_value=uno):
            self.material.refresh()
            self.assertTrue(self.light.set('OFF')[0])
            self.assertIn(b'P06 SET 1 LOW 0\n',uno.writes)

    def test_p06_sequence_propagates_output_failure(self):
        with patch.object(self.light,'set',return_value=(False,'Uno no response')), patch.object(self.app.units.mes,'submit') as submit:
            with self.assertRaisesRegex(RuntimeError,'Uno no response'):
                self.app.runtime.by_code['P06'].execute(self.app.runtime._context())
            submit.assert_not_called()

    def test_unknown_level_is_default_and_invalid_values_are_rejected(self):
        self.assertEqual(validate_config({})['p06']['relay_active_level'],'UNSET')
        for level in ['LOW','HIGH','UNSET']:
            self.assertEqual(validate_config({'p06':{'relay_active_level':level}})['p06']['relay_active_level'],level)
        with self.assertRaises(ValueError):validate_config({'p06':{'relay_active_level':'OTHER'}})
