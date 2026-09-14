"""Two physical SCARA identities, tested with simulation and mocked HTTP only."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mono_press.application import Application
from mono_press.core.config import ConfigStore, DEFAULT_CONFIG, validate_config
from mono_press.http_server import make_server


class TwoScaraTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = Application(Path(self.tmp.name), '/dev/not-used', 38400, True)
        self.load, self.unload = self.app.units.scaras.values()
        with patch('mono_press.http_server.ThreadingHTTPServer', side_effect=lambda addr, handler: handler):
            self.handler = make_server(self.app)

    def post(self, path, body=None):
        return self.handler.route_post(None, path, body or {})

    def test_v2_migration_assigns_legacy_tail_and_is_idempotent(self):
        raw = copy.deepcopy(DEFAULT_CONFIG)
        raw['press_recipe']['version'] = 2
        raw['press_recipe']['actions'].extend(raw['scara'].pop('p05_actions'))
        config = validate_config(raw)
        self.assertEqual(len(config['press_recipe']['actions']), 27)
        self.assertTrue(all(a.get('robot') in {None, 'P03'} for a in config['press_recipe']['actions'][:-5]))
        self.assertTrue(all(a['robot'] == 'P05' for a in config['press_recipe']['actions'][-5:]))
        self.assertEqual(validate_config(config), config)
        config['scara_p05']['positions']['HOME'][0] = 123
        self.assertEqual(config['scara']['positions']['HOME'][0], 0)

    def test_ambiguous_old_recipe_and_missing_new_robot_are_rejected(self):
        raw = {'press_recipe': {'version': 2, 'actions': [{'type': 'SCARA_MOVE', 'target': 'HOME'}]}}
        with self.assertRaisesRegex(ValueError, 'SCARA 구분'):
            validate_config(raw)
        with patch.object(Path, 'read_text', return_value=json.dumps(raw)):
            with self.assertRaises(ValueError):
                ConfigStore(Path(self.tmp.name) / 'ambiguous.json')
        new = self.app.config.snapshot()
        new['press_recipe']['actions'][0].pop('robot')
        with self.assertRaisesRegex(ValueError, 'robot'):
            validate_config(new)
        new['press_recipe']['actions'][0]['robot'] = 'P99'
        with self.assertRaisesRegex(ValueError, 'P03 또는 P05'):
            validate_config(new)

    def test_units_enable_jog_gripper_and_stop_are_independent(self):
        ids3 = self.app.units.unit_ids_for_process('P03')
        ids5 = self.app.units.unit_ids_for_process('P05')
        self.assertEqual(len(ids3), 5)
        self.assertEqual(len(ids5), 5)
        self.assertFalse(set(ids3) & set(ids5))
        command = self.app.units.manual_command
        self.assertFalse(command('SCARA_J1', 'ENABLE')[0])
        self.assertTrue(command('P05_SCARA_J1', 'ENABLE')[0])
        self.assertFalse(self.load.axes[0].snapshot['enabled'])
        self.assertTrue(command('P05_SCARA_J2', 'JOG_PLUS', {'delta': 2})[0])
        self.assertEqual(self.unload.current_position(), [0, 2, 0, 0])
        self.assertEqual(self.load.current_position(), [0, 0, 0, 0])
        self.assertTrue(command('P05_SCARA_GRIPPER', 'CLOSE')[0])
        self.assertFalse(self.load.gripper.snapshot['output'])
        self.assertTrue(self.unload.gripper.snapshot['output'])
        self.assertTrue(command('P03_SCARA_J1', 'ENABLE')[0])
        self.assertTrue(command('P05_SCARA_J1', 'STOP')[0])
        self.assertFalse(self.load.stop_event.is_set())
        self.assertTrue(self.unload.stop_event.is_set())
        self.app.units.stop_all()
        self.assertTrue(self.load.stop_event.is_set())

    def test_save_same_name_keeps_distinct_coordinates_and_moves_correct_robot(self):
        for i, axis in enumerate(self.load.axes):
            axis.update(position=i + 1)
        for i, axis in enumerate(self.unload.axes):
            axis.update(position=i + 11)
        for code in ('P03', 'P05'):
            self.assertTrue(self.post(f'/api/scara/{code}/save-current', {'name': 'CUSTOM'})[0])
        config = self.app.config.snapshot()
        self.assertEqual(config['scara']['positions']['CUSTOM'], [1, 2, 3, 4])
        self.assertEqual(config['scara_p05']['positions']['CUSTOM'], [11, 12, 13, 14])
        self.load.enable()
        self.unload.enable()
        for axis in self.unload.axes:
            axis.update(position=0)
        self.assertTrue(self.post('/api/scara/P05/move-saved', {'name': 'CUSTOM'})[0])
        self.assertEqual(self.unload.current_position(), [11, 12, 13, 14])
        self.assertEqual(self.load.current_position(), [1, 2, 3, 4])
        self.assertFalse(self.post('/api/scara/move-saved', {'name': 'CUSTOM'})[0])

    def test_position_deletion_checks_only_own_robot_and_persists(self):
        self.app.config.update(lambda c: c['press_recipe'].update(actions=[
            {'type': 'SCARA_MOVE', 'target': 'HOME', 'robot': 'P03'}]))
        self.assertFalse(self.post('/api/scara/P03/delete-position', {'name': 'HOME'})[0])
        self.assertTrue(self.post('/api/scara/P05/delete-position', {'name': 'HOME'})[0])
        reloaded = ConfigStore(self.app.config.path).snapshot()
        self.assertIn('HOME', reloaded['scara']['positions'])
        self.assertNotIn('HOME', reloaded['scara_p05']['positions'])
        self.assertTrue(self.post('/api/scara/P03/delete-position', {'name': 'OUTPUT'})[0])
        self.assertNotIn('OUTPUT', ConfigStore(self.app.config.path).snapshot()['scara']['positions'])

    def test_speeds_and_saved_positions_survive_independent_edits(self):
        draft = self.app.config.snapshot()
        draft['scara']['speed'] = 12
        draft['scara_p05']['speed'] = 81
        draft['scara_p05']['positions']['HOME'] = [9, 8, 7, 6]
        self.app.config.save(draft)
        config = ConfigStore(self.app.config.path).snapshot()
        self.assertEqual(config['scara']['speed'], 12)
        self.assertEqual(config['scara_p05']['speed'], 81)
        self.assertEqual(config['scara']['positions']['HOME'], [0, 0, 0, 0])

    def test_interleaved_recipe_routes_by_robot_not_list_position(self):
        actions = [{'type': 'SCARA_MOVE', 'target': 'HOME', 'robot': 'P05'},
                   {'type': 'GRIPPER', 'target': 'CLOSE', 'robot': 'P03'},
                   {'type': 'SCARA_MOVE', 'target': 'HOME', 'robot': 'P03'},
                   {'type': 'GRIPPER', 'target': 'OPEN', 'robot': 'P05'}]
        self.app.config.update(lambda c: c['press_recipe'].update(actions=actions))
        events = []
        def event(code, action):
            def perform(target):
                events.append((code, action, target))
                return True, 'ok'
            return perform
        with patch.object(self.load, 'move_saved', side_effect=event('P03', 'move')), \
             patch.object(self.unload, 'move_saved', side_effect=event('P05', 'move')), \
             patch.object(self.load, 'set_gripper', side_effect=event('P03', 'grip')), \
             patch.object(self.unload, 'set_gripper', side_effect=event('P05', 'grip')):
            self.app.runtime.by_code['P03'].run(self.app.runtime._context())
        self.assertEqual(events, [('P05', 'move', 'HOME'), ('P03', 'grip', True),
                                  ('P03', 'move', 'HOME'), ('P05', 'grip', False)])

    def test_stop_from_either_robot_cancels_running_sequence(self):
        with patch.object(self.app.runtime, 'busy', return_value=True), \
             patch.object(self.app.runtime, 'stop', return_value=(True, 'stopped')) as stop:
            for code in ('P03', 'P05'):
                self.assertTrue(self.post(f'/api/unit/{code}_SCARA_J1/command', {'action': 'STOP'})[0])
                self.assertFalse(self.post(f'/api/scara/{code}/save-current', {'name': 'NO'})[0])
            self.assertEqual(stop.call_count, 2)

    def test_press_home_is_completed_before_either_robot_is_enabled(self):
        self.assertTrue(self.app.units.press.enable()[0])
        self.assertTrue(self.app.units.press.home()[0])
        self.app.config.update(lambda c: c['press_recipe'].update(actions=[
            {'type': 'SCARA_MOVE', 'target': 'HOME', 'robot': 'P05'},
            {'type': 'PRESS_DOWN', 'target': 'DOWN_STEPS'},
            {'type': 'SCARA_MOVE', 'target': 'HOME', 'robot': 'P03'}]))
        enabled = []
        def enable(code):
            self.assertTrue(self.app.units.press.axis.snapshot['homed'])
            enabled.append(code)
            return True, 'ok'
        with patch.object(self.load, 'enable', side_effect=lambda: enable('P03')), \
             patch.object(self.unload, 'enable', side_effect=lambda: enable('P05')), \
             patch.object(self.load, 'move_saved', return_value=(True, 'ok')), \
             patch.object(self.unload, 'move_saved', return_value=(True, 'ok')), \
             patch.object(self.app.units.press, '_move_steps'):
            self.app.runtime.by_code['P03'].execute(self.app.runtime._context())
        self.assertEqual(enabled, ['P05', 'P03'])

    def test_old_client_cannot_overwrite_two_robot_config(self):
        before = self.app.config.snapshot()
        old = copy.deepcopy(before)
        old['press_recipe']['version'] = 2
        self.assertFalse(self.post('/api/config', old)[0])
        self.assertEqual(self.app.config.snapshot(), before)

    def test_mode_switch_disables_both_robots(self):
        self.load.enable()
        self.unload.enable()
        self.assertTrue(self.app.switch_mode('SIMULATION')[0])
        for robot in self.app.units.scaras.values():
            self.assertFalse(any(axis.snapshot['enabled'] for axis in robot.axes))


if __name__ == '__main__':
    unittest.main()
