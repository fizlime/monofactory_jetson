"""P03/P04/P05 migration and execution regressions; no hardware access."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mono_press.application import Application
from mono_press.core.config import ConfigStore, DEFAULT_CONFIG, validate_config
from mono_press.core.sequence import SequenceStopped


class UnifiedRecipeTests(unittest.TestCase):
    def test_legacy_tail_is_appended_once_and_all_other_values_preserved(self):
        legacy = copy.deepcopy(DEFAULT_CONFIG)
        legacy['scara']['speed'] = 17
        head = legacy['press_recipe']['actions']
        tail = legacy['scara']['p05_actions']
        checked = validate_config(legacy)
        expected = [{**a, **({'robot': code} if a['type'] in {'SCARA_MOVE', 'GRIPPER'} else {})}
                    for code, group in [('P03', head), ('P05', tail)] for a in group]
        for action in expected:
            if action['type'] == 'PRESS_DOWN': action['target'] = 'DOWN_MM'
            if action['type'] == 'PRESS_UP': action['target'] = 'UP_MM'
        self.assertEqual(checked['press_recipe']['actions'], expected)
        self.assertEqual(checked['press_recipe']['version'], 3)
        self.assertNotIn('p05_actions', checked['scara'])
        self.assertEqual(checked['scara']['speed'], 17)
        self.assertEqual(validate_config(checked), checked)
        self.assertEqual(len(checked['press_recipe']['actions']), 27)

    def test_custom_legacy_move_alias_and_order_are_preserved(self):
        raw = {'press_recipe': {'actions': [{'type': 'WAIT', 'target': 1}]},
               'scara': {'p05_actions': [{'type': 'MOVE', 'target': 'OUTPUT'},
                                         {'type': 'GRIPPER', 'target': 'OPEN', 'robot': 'P05'}]}}
        checked = validate_config(raw)
        self.assertEqual(checked['press_recipe']['actions'], [
            {'type': 'WAIT', 'target': 1}, {'type': 'SCARA_MOVE', 'target': 'OUTPUT', 'robot': 'P05'},
            {'type': 'GRIPPER', 'target': 'OPEN', 'robot': 'P05'}])

    def test_save_reload_and_tail_edit_do_not_restore_default_unloading(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            store = ConfigStore(path)
            original = store.snapshot()
            for _ in range(3):
                self.assertEqual(store.save(store.snapshot()), original)
                store = ConfigStore(path)
                self.assertEqual(store.snapshot(), original)
            edited = store.snapshot()
            edited['press_recipe']['actions'] = [{'type': 'WAIT', 'target': 1}]
            store.save(edited)
            self.assertEqual(ConfigStore(path).snapshot(), edited)

    def test_canonical_empty_recipe_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '한 개 이상'):
            validate_config({'press_recipe': {'version': 2, 'actions': []}})

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = Application(Path(self.tmp.name), '/dev/not-used', 38400, True)

    def test_runtime_registers_combined_sequence_only_once(self):
        runtime = self.app.runtime
        self.assertEqual([s.code for s in runtime.sequences], ['P00', 'P01', 'P02', 'P03', 'P06'])
        self.assertIs(runtime.by_code['P03'], runtime.by_code['P04'])
        self.assertIs(runtime.by_code['P03'], runtime.by_code['P05'])

    def configure_small_recipe(self):
        self.app.config.update(lambda c: c['press_recipe'].update(actions=[
            {'type': 'PRESS_DOWN', 'target': 'DOWN_STEPS'},
            {'type': 'PRESS_UP', 'target': 'UP_STEPS'},
            {'type': 'SCARA_MOVE', 'target': 'PRESS_UNLOAD', 'robot': 'P05'},
            {'type': 'GRIPPER', 'target': 'CLOSE', 'robot': 'P05'},
            {'type': 'SCARA_MOVE', 'target': 'OUTPUT', 'robot': 'P05'},
            {'type': 'GRIPPER', 'target': 'OPEN', 'robot': 'P05'},
        ]))

    def test_unloading_runs_once_after_press_and_requires_home(self):
        self.configure_small_recipe()
        press, robot = self.app.units.press, self.app.units.scara_p05
        self.assertTrue(press.enable()[0])
        self.assertTrue(press.home()[0])
        events = []
        def move(steps, *args, **kwargs):
            self.assertTrue(press.axis.snapshot['homed'])
            events.append('DOWN' if steps > 0 else 'UP')
        def robot_move(name):
            events.append(name)
            return True, 'ok'
        def grip(close):
            events.append('CLOSE' if close else 'OPEN')
            return True, 'ok'
        ctx = Mock()
        with patch.object(press, '_move_steps', side_effect=move), \
             patch.object(robot, 'enable', return_value=(True, 'ok')), \
             patch.object(robot, 'move_saved', side_effect=robot_move), \
             patch.object(robot, 'set_gripper', side_effect=grip):
            self.app.runtime.by_code['P03'].execute(ctx)
        self.assertEqual(events, ['DOWN', 'UP', 'PRESS_UNLOAD', 'CLOSE', 'OUTPUT', 'OPEN'])
        ctx.state.update_process.assert_any_call('P05', status='DONE', progress=100,
                                                repeat_current=6, message='통합 완성품 이송 완료')

    def test_stop_after_press_prevents_unloading(self):
        self.configure_small_recipe()
        press, robot = self.app.units.press, self.app.units.scara_p05
        self.assertTrue(press.enable()[0])
        self.assertTrue(press.home()[0])
        def move(steps, *args, **kwargs):
            self.app.runtime.stop_event.set()
        with patch.object(press, '_move_steps', side_effect=move), \
             patch.object(robot, 'enable', return_value=(True, 'ok')), \
             patch.object(robot, 'move_saved') as unload:
            with self.assertRaises(SequenceStopped):
                self.app.runtime.by_code['P03'].execute(self.app.runtime._context())
            unload.assert_not_called()


if __name__ == '__main__':
    unittest.main()
