from contextlib import nullcontext
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from mono_press.application import Application
from mono_press.core.config import ConfigStore


class HomeOnlyRecipeTests(unittest.TestCase):
    def test_legacy_home_block_is_skipped_and_press_actions_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            store=ConfigStore(root/'settings/poc_config.json')
            cfg=store.snapshot()
            cfg['scara_p05']['shared_with']='P03'
            cfg['press_recipe']['actions']=[{'type':'DOBOT_HOME','robot':'P03'},
                {'type':'PRESS_DOWN','target':'DOWN_STEPS'}, {'type':'PRESS_UP','target':'UP_STEPS'}]
            store.save(cfg)
            app=Application(root,'/dev/not-used',38400,True)
            robot,press=app.units.scara,app.units.press
            with patch.object(robot,'home',return_value=(True,'home complete')) as home, \
                 patch.object(robot,'enable') as enable, patch.object(robot,'move_position') as move, \
                 patch.object(robot,'move_saved') as saved, patch.object(robot,'set_gripper') as grip, \
                 patch.object(press,'prepared_cycle',return_value=nullcontext()), patch.object(press,'move_steps') as steps:
                app.runtime.by_code['P03'].execute(app.runtime._context())
                home.assert_not_called()
                for mock in (enable,move,saved,grip):mock.assert_not_called()
                self.assertEqual(steps.call_count,2)
            self.assertEqual(app.state.processes['P03']['status'],'DONE')
            self.assertEqual(app.state.processes['P05']['status'],'DONE')


if __name__=='__main__':unittest.main()
