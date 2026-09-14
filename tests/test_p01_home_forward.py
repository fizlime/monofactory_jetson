import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from mono_press.application import Application


class HomeForwardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.app=Application(Path(self.tmp.name),'/dev/not-used',38400,True)
        self.axis=self.app.units.p01_axes[0]
        self.axis.update(enabled=True)
        self.app.config.update(lambda c:c['p01']['axes']['E0'].update(home_forward_mm=9.625,home_search_mm=9.625,home_step_mm=4.8125))

    def test_active_sensor_still_advances_and_only_completion_marks_homed(self):
        observed=[]
        def move(pulses):
            observed.append((pulses,self.axis.snapshot['position'],self.axis.snapshot['homed']))
            self.axis.update(position=pulses)
            self.axis.home_sensor.set_simulated(False)
            return True
        with patch.object(self.axis,'_move',side_effect=move):
            self.assertTrue(self.axis.home()[0]);self.axis.worker.join(1)
        self.assertEqual(observed,[(800,0,False)])
        self.assertTrue(self.axis.snapshot['homed'])
        self.assertEqual(self.axis.snapshot['position'],800)
        self.assertFalse(self.axis.home_sensor.read())

    def test_missing_distance_or_sensor_does_not_move(self):
        for missing in ('distance','sensor'):
            with self.subTest(missing=missing),patch.object(self.axis,'_move') as move:
                if missing=='distance':self.app.config.update(lambda c:c['p01']['axes']['E0'].update(home_forward_mm=None))
                else:self.axis.home_sensor.set_mode(False)
                self.assertFalse(self.axis.home()[0]);move.assert_not_called()
                self.assertFalse(self.axis.snapshot['homed'])

    def test_no_sensor_detection_never_advances(self):
        with patch.object(self.axis.home_sensor,'read',return_value=False),patch.object(self.axis,'_move',return_value=True) as move:
            self.assertFalse(self.axis.home_blocking())
        self.assertEqual([c.args for c in move.call_args_list],[(-400,),(-400,)])
        self.assertFalse(self.axis.snapshot['homed'])

    def test_failed_or_cancelled_forward_does_not_complete(self):
        for outcome in ('fail','stop'):
            with self.subTest(outcome=outcome):
                cancel=threading.Event()
                def move(_):
                    if outcome=='stop':cancel.set()
                    return outcome!='fail'
                with patch.object(self.axis,'_move',side_effect=move):self.assertFalse(self.axis.home_blocking(cancel))
                self.assertFalse(self.axis.snapshot['homed'])

    def test_stop_at_origin_prevents_forward(self):
        def detected():self.axis.cancel.set();return True
        with patch.object(self.axis.home_sensor,'read',side_effect=detected),patch.object(self.axis,'_move') as move:
            self.assertFalse(self.axis.home_blocking());move.assert_not_called()

    def test_offset_validation_rejects_invalid_motion_values(self):
        for v in (0,-1,float('nan'),float('inf'),True,.000001):
            with self.subTest(v=v),self.assertRaises(ValueError):
                self.app.config.update(lambda c:c['p01']['axes']['E0'].update(home_forward_mm=v))

    def test_main_home_skips_unconfigured_offset_without_enable(self):
        self.app.config.update(lambda c:[a.update(home_forward_mm=None) for a in c['p01']['axes'].values()])
        with patch.object(self.axis,'enable') as enable:
            self.assertTrue(self.app.runtime.home('P01')[0]);self.app.runtime.thread.join(1)
            enable.assert_not_called()
        self.assertEqual(self.app.state.processes['P01']['status'],'WAITING')
        self.assertIn('전진 거리',self.app.state.processes['P01']['message'])

if __name__=='__main__':unittest.main()
