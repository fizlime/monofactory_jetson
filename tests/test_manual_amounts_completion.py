import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch

from mono_press.application import Application
from mono_press.core.config import validate_config
from mono_press.http_server import make_server


class ManualAmountsCompletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.app=Application(Path(self.tmp.name),'/dev/not-used',38400,True)
        with patch('mono_press.http_server.ThreadingHTTPServer',side_effect=lambda addr,handler:handler):self.handler=make_server(self.app)

    def post(self,unit,action,**kw):return self.handler.route_post(None,f'/api/unit/{unit}/command',dict(action=action,**kw))

    def test_each_manual_axis_uses_entered_distance_and_direction(self):
        before=self.app.config.snapshot()
        for axis in self.app.units.p01_axes:
            with patch.object(axis,'start_move',return_value=(True,'ok')) as move:
                self.assertTrue(self.post(axis.unit_id,'FORWARD',pulses=1234)[0]);move.assert_called_with(1234,manual=True)
                self.assertTrue(self.post(axis.unit_id,'REVERSE',pulses='567')[0]);move.assert_called_with(-567,manual=True)
        with patch.object(self.app.units.press,'move_steps') as move:
            self.assertTrue(self.post('P04_PRESS_AXIS','DOWN',steps=1200)[0]);move.assert_called_with(1200,None)
            self.assertTrue(self.post('P04_PRESS_AXIS','UP',steps=600)[0]);move.assert_called_with(-600,None)
        self.assertEqual(self.app.config.snapshot(),before)

    def test_invalid_or_missing_amount_never_moves_or_falls_back(self):
        for unit,actions,field in [('P01_E0',['FORWARD','REVERSE'],'pulses'),('P04_PRESS_AXIS',['DOWN','UP'],'steps')]:
            with patch.object(self.app.units.p01_axes[0],'start_move') as axis,patch.object(self.app.units.press,'move_steps') as press:
                for action in actions:
                    for value in [None,'',0,-1,1.5,'2.5',True,float('nan'),float('inf'),2**32,{},'1e3']:
                        self.assertFalse(self.post(unit,action,**{field:value})[0],(unit,action,value))
                    self.assertFalse(self.post(unit,action)[0])
                axis.assert_not_called();press.assert_not_called()

    def test_press_enable_and_home_interlocks_are_retained(self):
        self.assertFalse(self.post('P04_PRESS_AXIS','DOWN',steps=120)[0])
        self.app.units.press.enable()
        self.assertFalse(self.post('P04_PRESS_AXIS','DOWN',steps=120)[0])
        self.assertEqual(self.app.units.press.current_position(),0)
        self.app.units.press.home()
        self.assertTrue(self.post('P04_PRESS_AXIS','DOWN',steps=120)[0])
        self.assertEqual(self.app.units.press.current_position(),120)
        self.assertTrue(self.post('P04_PRESS_AXIS','UP',steps=60)[0])
        self.assertEqual(self.app.units.press.current_position(),60)

    def test_merge_migrates_settings_and_delays_exactly_once(self):
        old=self.app.config.snapshot()
        old['p07']={'green_hold_seconds':3.4,'line_code':'MY-LINE','mes_endpoint':'http://example.test'}
        old['p06']={'reserved_note':'old'}
        old['line']['process_delays'].update(P06=.5,P07=.8)
        new=validate_config(old)
        self.assertEqual(new['p06'],dict(old['p07'],relay_active_level='UNSET'))
        self.assertNotIn('p07',new);self.assertNotIn('P07',new['line']['process_delays'])
        self.assertEqual(new['line']['process_delays']['P06'],1.3)
        self.assertEqual(new['press_recipe'],old['press_recipe'])
        self.assertEqual(validate_config(new),new)

    def test_single_completion_process_no_reserved_unit_or_extra_release(self):
        state=self.app.state.snapshot()
        self.assertEqual(state['process_order'],['P00','P01','P02','P03','P04','P05','P06'])
        self.assertNotIn('P06_RESERVED',state['units'])
        self.assertFalse(any(k.startswith('P07') for k in state['units']))
        self.assertEqual([s.code for s in self.app.sequences],['P00','P01','P02','P03','P06'])
        self.assertEqual(set(self.app.units.unit_ids_for_process('P06')),{'P06_MES','P06_LIGHT_RED','P06_LIGHT_GREEN','P06_BUZZER'})
        ctx=self.app.runtime._context();ctx.wait=Mock()
        with patch.object(self.app.units.mes,'submit',return_value=(True,'ok')) as mes,patch.object(self.app.units.scara_p05,'set_gripper') as release:
            self.app.runtime.by_code['P06'].execute(ctx)
            mes.assert_called_once();release.assert_not_called()
        self.assertTrue(self.app.units.light.green.snapshot['output'])
        self.assertTrue(self.post('P06_LIGHT_RED','RED')[0])
        self.assertTrue(self.app.units.light.red.snapshot['output'])


if __name__=='__main__':unittest.main()
