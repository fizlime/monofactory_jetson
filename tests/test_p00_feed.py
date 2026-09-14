import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mono_press.application import Application
from mono_press.core.config import validate_config
from mono_press.core.sequence import SequenceStopped
from mono_press.http_server import make_server


class FeedTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.app=Application(Path(tmp.name),'/dev/not-used',38400,True)
        self.sensor=self.app.units.material
        self.addCleanup(self.sensor.close)
        with patch('mono_press.http_server.ThreadingHTTPServer',side_effect=lambda addr,handler:handler):self.handler=make_server(self.app)

    def test_no_material_blocks_forward_and_cycle_but_allows_reverse_stop(self):
        axis=self.app.units.p01_axes[0];axis.enable()
        self.sensor.set_simulated(False)
        with patch.object(axis,'start',return_value=(True,'started')) as start:
            self.assertFalse(axis.start_move(123)[0]);self.assertFalse(axis.start_cycle(123)[0]);start.assert_not_called()
            self.assertTrue(axis.start_move(-123)[0]);start.assert_called_once()
        self.assertTrue(axis.stop()[0])
        with patch.object(axis,'enable') as enable:
            with self.assertRaisesRegex(RuntimeError,'P00'):self.app.runtime.by_code['P01'].execute(self.app.runtime._context())
            enable.assert_not_called()

    def test_real_unconfigured_and_gpio_failure_are_fail_closed(self):
        self.sensor.set_mode(False)
        self.assertFalse(self.sensor.require_material()[0])
        self.assertFalse(self.sensor.set_simulated(True)[0])
        self.app.config.update(lambda c:c['p00'].update(driver='gpio'))
        with patch('mono_press.units.material_sensor.subprocess.run',side_effect=OSError('unplugged')):
            self.assertFalse(self.sensor.require_material()[0])
        self.assertFalse(self.sensor.snapshot['connected']);self.assertFalse(self.sensor.snapshot['detected'])

    def test_manual_api_ignores_sensor_but_retains_enable_installed_and_busy_checks(self):
        self.sensor.set_simulated(False)
        axis=self.app.units.p01_axes[0]
        def post(action):
            return self.handler.route_post(None,'/api/unit/P01_E0/command',{'action':action,'pulses':800})
        with patch.object(self.sensor,'require_material',side_effect=AssertionError('Manual must not query material')):
            self.assertFalse(post('FORWARD')[0])
            axis.update(enabled=True)
            with patch.object(axis,'start',return_value=(True,'started')) as start:
                for action in ['FORWARD','REVERSE','CYCLE']: self.assertTrue(post(action)[0])
                self.assertEqual(start.call_count,3)
            with patch.object(axis,'busy',return_value=True): self.assertFalse(post('FORWARD')[0])
            self.app.config.update(lambda c:c['p01']['axes']['E0'].update(installed=False))
            self.assertFalse(post('FORWARD')[0])
            self.assertFalse(post('CYCLE')[0])

    def test_manual_does_not_allow_automatic_feed_when_sensor_is_offline(self):
        axis=self.app.units.p01_axes[0]
        axis.update(enabled=True)
        with patch.object(self.sensor,'require_material',return_value=(False,'P00 USB 미연결')):
            with patch.object(axis,'start',return_value=(True,'started')):
                self.assertTrue(self.handler.route_post(None,'/api/unit/P01_E0/command',{'action':'FORWARD','pulses':800})[0])
            with patch.object(axis,'enable') as enable:
                with self.assertRaisesRegex(RuntimeError,'P00'):
                    self.app.runtime.by_code['P01'].execute(self.app.runtime._context())
                enable.assert_not_called()

    def test_real_input_debounce_and_absence_reset(self):
        self.app.config.update(lambda c:c['p00'].update(driver='gpio',debounce_ms=100))
        self.sensor.simulated=False
        with patch('mono_press.units.material_sensor.subprocess.run',return_value=Mock(stdout='0')) as read,patch('mono_press.units.material_sensor.time.monotonic',side_effect=[1,1.05,1.11]):
            self.assertFalse(self.sensor.refresh());self.assertFalse(self.sensor.refresh());self.assertTrue(self.sensor.refresh())
            self.assertEqual(read.call_args.args[0],['gpioget','gpiochip0','105'])
        with patch('mono_press.units.material_sensor.subprocess.run',return_value=Mock(stdout='1')):
            self.assertFalse(self.sensor.refresh())
        self.assertIsNone(self.sensor.since)

    def test_p00_precedes_p01_and_wait_can_be_cancelled(self):
        self.assertEqual([s.code for s in self.app.sequences][:2],['P00','P01'])
        self.sensor.set_simulated(False)
        ctx=self.app.runtime._context();ctx.stop_event.set()
        with self.assertRaises(SequenceStopped):self.app.runtime.by_code['P00'].execute(ctx)

    def test_three_axes_skip_excluded_motor_and_can_restore_fourth(self):
        self.app.config.update(lambda c:c['p01']['axes']['E3'].update(installed=False))
        self.app.sync_feed_config()
        fourth=self.app.units.p01_axes[3]
        self.assertFalse(fourth.enable()[0]);self.assertFalse(fourth.start_move(1)[0])
        with patch.object(fourth,'enable',wraps=fourth.enable) as enable:
            self.app.runtime.by_code['P01'].execute(self.app.runtime._context());enable.assert_not_called()
        self.assertEqual(self.app.state.processes['P01']['repeat_total'],3)
        self.assertEqual(fourth.snapshot['state'],'EXCLUDED')
        self.app.config.update(lambda c:c['p01']['axes']['E3'].update(installed=True))
        self.app.sync_feed_config();self.assertTrue(fourth.enable()[0])

    def test_explicit_axis_exception_does_not_hide_failed_installed_axis(self):
        self.app.config.update(lambda c:c['p01']['axes']['E3'].update(installed=False))
        with patch.object(self.app.units.p01_axes[1],'enable',return_value=(False,'NO_RESPONSE')):
            with self.assertRaisesRegex(RuntimeError,'E1'):self.app.runtime.by_code['P01'].execute(self.app.runtime._context())

    def test_config_rejects_all_axes_excluded_and_gpio_command_injection(self):
        cfg=self.app.config.snapshot()
        for a in cfg['p01']['axes'].values():a['installed']=False
        with self.assertRaises(ValueError):validate_config(cfg)
        with self.assertRaises(ValueError):validate_config({'p00':{'gpio_chip':'gpiochip0; echo unsafe'}})

    def test_probe_is_read_only_and_skips_excluded_axis(self):
        self.app.config.update(lambda c:c['p01']['axes']['E3'].update(installed=False))
        with patch.object(self.app.bus,'probe',return_value=(True,'read')) as probe:
            self.assertTrue(self.app.check_p01()[0])
            self.assertEqual([c.args[0] for c in probe.call_args_list],[0xe0,0xe1,0xe2])
        self.assertFalse(any(a.snapshot['enabled'] for a in self.app.units.p01_axes))

    def test_serial_noise_is_not_a_connection_and_real_status_is_accepted(self):
        bus=self.app.bus
        # Invalid status with matching checksum, unsolicited ACK, then a solicited ACK.
        chunks=[bytes([0xe0,0xf8,0xd8]),bytes([0xe1,1,0xe2]),bytes([0xe0,1,0xe1])]
        def read(_):
            if chunks:return chunks.pop(0)
            bus.reader_stop.set();return b''
        bus.device=Mock();bus.device.read.side_effect=read
        bus.waiting_commands.add(0xe0)
        bus._reader_loop()
        self.assertTrue(self.app.units.p01_axes[0].snapshot['connected'])
        self.assertFalse(self.app.units.p01_axes[1].snapshot['connected'])
        self.assertEqual(len(bus.events[0xe0]),1)
        self.assertGreater(bus.discarded_bytes,0)

    def test_failed_command_clears_enabled_and_query_parses_six_byte_reply(self):
        bus=self.app.bus;bus.simulation=False
        with patch.object(bus,'write',return_value=(True,0,'')),patch.object(bus,'wait_event',return_value=None):
            self.app.units.p01_axes[0].update(enabled=True)
            self.assertFalse(bus.command(0xe0,[0xf3,1],'ENABLE',.01)[0])
        self.assertFalse(self.app.units.p01_axes[0].snapshot['enabled'])
        bus.reader_stop.clear();query={'data':None};bus.pending_queries[0xe0]=query
        data=bytes([0xe0,0,0,1,0,0xe1]);chunks=[data[:2],data[2:]]
        def read(_):
            if chunks:return chunks.pop(0)
            bus.reader_stop.set();return b''
        bus.device=Mock();bus.device.read.side_effect=read;bus._reader_loop()
        self.assertEqual(query['data'],b'\0\0\1\0');self.assertEqual(len(bus.events[0xe0]),0)


if __name__=='__main__':unittest.main()
