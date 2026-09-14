import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mono_press.application import Application
from mono_press.core.config import ConfigStore, validate_config
from mono_press.core.distance_units import counts_to_mm, mm_to_counts, p01_pulses, press_mm, press_steps
from mono_press.http_server import make_server


class MillimetreTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.app=Application(Path(tmp.name),'/dev/not-used',38400,True)
        with patch('mono_press.http_server.ThreadingHTTPServer',side_effect=lambda addr,handler:handler):
            self.handler=make_server(self.app)

    def post(self,unit,action,**values):
        return self.handler.route_post(None,f'/api/unit/{unit}/command',dict(action=action,**values))

    def test_one_turn_and_fractional_mm_produce_exact_motor_counts(self):
        self.assertEqual(mm_to_counts(38.5),3200)
        self.assertEqual(mm_to_counts(19.25),1600)
        self.assertEqual(mm_to_counts('0.01203125'),1)
        self.assertEqual(press_steps('1.25'),750)
        self.assertEqual(press_steps(2),1200)
        self.assertAlmostEqual(press_mm(3200),5.333333333333333)
        for counts in [1,3,400,1234,64000,0xFFFFFFFF]:
            self.assertEqual(mm_to_counts(counts_to_mm(counts)),counts)
        for counts in [1,3,600,1200,4000,64000,0x7FFFFFFF]:
            self.assertEqual(press_steps(press_mm(counts)),counts)

    def test_legacy_config_migrates_once_and_saves_mm_only(self):
        old={'p01':{'pulse_per_rev':3200,'axes':{'E0':{'pulses':64000,'home_search_pulses':128000,'home_step_pulses':400}}},
             'p04':{'down_steps':1200,'up_steps':600,'saved_positions':{'ZERO':0,'A':1500,'NEG':-750},
                    'can':{'deceleration_advance_steps':4000}}}
        cfg=validate_config(old)
        self.assertEqual(cfg['p01']['axes']['E0']['distance_mm'],770)
        self.assertEqual(cfg['p01']['axes']['E0']['home_search_mm'],1540)
        self.assertEqual(cfg['p01']['axes']['E0']['home_step_mm'],4.8125)
        self.assertEqual(cfg['p04']['saved_positions_mm'],{'ZERO':0,'A':2.5,'NEG':-1.25})
        self.assertEqual((cfg['p04']['down_mm'],cfg['p04']['up_mm']),(2,1))
        for _ in range(5):
            self.assertEqual(validate_config(cfg),cfg)
            self.app.config.save(cfg)
            self.assertEqual(ConfigStore(self.app.config.path).snapshot(),cfg)
        stored=json.loads(self.app.config.path.read_text(encoding='utf-8'))
        self.assertNotIn('pulses',stored['p01']['axes']['E0'])
        self.assertNotIn('down_steps',stored['p04'])
        self.assertNotIn('saved_positions',stored['p04'])
        self.assertNotIn('deceleration_advance_steps',stored['p04']['can'])

    def test_http_mm_uses_each_axis_scale_and_direction(self):
        before=self.app.config.snapshot()
        for axis in self.app.units.p01_axes:
            with patch.object(axis,'start_move',return_value=(True,'ok')) as move:
                self.assertTrue(self.post(axis.unit_id,'FORWARD',distance_mm=38.5)[0]);move.assert_called_with(3200,manual=True)
                self.assertTrue(self.post(axis.unit_id,'REVERSE',distance_mm='19.25')[0]);move.assert_called_with(-1600,manual=True)
        with patch.object(self.app.units.press,'move_steps') as move:
            self.assertTrue(self.post('P04_PRESS_AXIS','DOWN',distance_mm=1.25)[0]);move.assert_called_with(750,None)
            self.assertTrue(self.post('P04_PRESS_AXIS','UP',distance_mm=.5)[0]);move.assert_called_with(-300,None)
        self.assertEqual(self.app.config.snapshot(),before)

    def test_invalid_mm_and_mixed_units_never_send_motion(self):
        for unit,action in [('P01_E0','FORWARD'),('P04_PRESS_AXIS','DOWN')]:
            with patch.object(self.app.units.p01_axes[0],'start_move') as feed,patch.object(self.app.units.press,'move_steps') as press:
                for value in [None,'',True,0,-1,float('nan'),float('inf'),{},'0.000000001','1e1000000']:
                    self.assertFalse(self.post(unit,action,distance_mm=value)[0],value)
                self.assertFalse(self.post(unit,action,distance_mm=1,steps=600)[0])
                self.assertFalse(self.post(unit,action,distance_mm=1,pulses=3200)[0])
                feed.assert_not_called();press.assert_not_called()

    def test_saved_press_position_and_status_report_mm_without_reinterpreting_counts(self):
        self.app.units.press.axis.update(position=1500)
        self.app.units.p01_axes[0].update(position=3200)
        self.assertTrue(self.handler.route_post(None,'/api/press/save-current',{'name':'TAUGHT'})[0])
        self.assertEqual(self.app.config.snapshot()['p04']['saved_positions_mm']['TAUGHT'],2.5)
        status=self.app.state.snapshot()['units']
        self.assertEqual(status['P04_PRESS_AXIS']['position_mm'],2.5)
        self.assertEqual(status['P01_E0']['position_mm'],38.5)
        self.app.units.press.axis.update(position=300,homed=True)
        with patch.object(self.app.units.press,'move_steps') as move:
            self.assertTrue(self.app.units.press.move_saved('TAUGHT')[0])
            move.assert_called_once_with(1200)

    def test_automatic_and_home_use_mm_and_preserve_exclusion(self):
        self.app.config.update(lambda cfg:cfg['p01']['axes']['E0'].update(distance_mm=38.5,home_step_mm=4.8125,home_forward_mm=9.625))
        for axis in self.app.units.p01_axes[1:]:
            self.app.config.update(lambda cfg,n=axis.name:cfg['p01']['axes'][n].update(installed=False))
        axis=self.app.units.p01_axes[0]
        def cycle(counts,**kw):
            axis.update(state='DONE')
            return True,'done'
        with patch.object(axis,'enable',return_value=(True,'ok')),patch.object(axis,'start_cycle',side_effect=cycle) as start:
            self.app.runtime.by_code['P01'].execute(self.app.runtime._context())
            start.assert_called_once_with(3200,material_authorized=True)
        axis.update(enabled=True)
        with patch.object(axis.home_sensor,'read',side_effect=[False,True]),patch.object(axis,'_move',return_value=True) as move:
            self.assertTrue(axis.home_blocking())
            self.assertEqual([c.args for c in move.call_args_list],[(-400,),(800,)])

    def test_can_adapter_converts_only_at_boundary_and_conflicting_schema_rejected(self):
        self.app.config.update(lambda cfg:cfg['p04'].update(down_mm=2,up_mm=1))
        config=self.app.units.press.can._config()
        self.assertEqual(config.target_distance_mm,2)
        self.assertEqual(config.deceleration_advance_steps,4000)
        cfg=self.app.config.snapshot()
        cfg['p04']['down_steps']=1200
        with self.assertRaises(ValueError):validate_config(cfg)
        cfg=self.app.config.snapshot()
        cfg['p01']['mm_per_rev']=99
        self.assertEqual(validate_config(cfg)['p01']['mm_per_rev'],38.5)

    def test_user_mm_edits_persist_and_control_automatic_press(self):
        from contextlib import nullcontext
        cfg=self.app.config.snapshot()
        cfg['p01']['axes']['E0']['distance_mm']=19.25
        cfg['p04'].update(down_mm=3.25,up_mm=1.5)
        cfg['press_recipe']['actions']=[{'type':'PRESS_DOWN'},{'type':'PRESS_UP'}]
        self.assertTrue(self.handler.route_post(None,'/api/config',cfg)[0])
        saved=ConfigStore(self.app.config.path).snapshot()
        self.assertEqual(saved['p01']['axes']['E0']['distance_mm'],19.25)
        self.assertEqual((saved['p04']['down_mm'],saved['p04']['up_mm']),(3.25,1.5))
        with patch.object(self.app.units.press,'prepared_cycle',return_value=nullcontext()),patch.object(self.app.units.press,'move_steps') as move:
            self.app.runtime.by_code['P03'].execute(self.app.runtime._context())
        self.assertEqual([call.args[0] for call in move.call_args_list],[1950,-900])


if __name__=='__main__':unittest.main()
