import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mono_press.application import Application
from mono_press.core.config import ConfigStore
from mono_press.http_server import make_server


class DobotRecipeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = Application(Path(self.tmp.name), '/dev/not-used', 38400, True)
        with patch('mono_press.http_server.ThreadingHTTPServer', side_effect=lambda addr, handler: handler):
            self.handler = make_server(self.app)
        self.command = {'type': 'DOBOT_MOVE', 'robot': 'P03', 'mode': 'JOINT', 'values': [12.5, 20, 30, 0], 'speed': 15}

    def post(self, body):
        return self.handler.route_post(None, '/api/recipe', body)

    def test_http_save_reload_preserves_all_command_fields_and_does_not_move(self):
        original = self.app.config.snapshot()
        body = {'version': 3, 'actions': [self.command]}
        with patch.object(self.app.units.scara, 'move_position') as move:
            self.assertTrue(self.post(body)[0])
            move.assert_not_called()
        saved = ConfigStore(self.app.config.path).snapshot()
        self.assertEqual(saved['press_recipe'], body)
        self.assertEqual(saved['p04'], original['p04'])
        self.assertEqual(saved['scara'], original['scara'])

    def test_invalid_command_does_not_replace_existing_recipe(self):
        original = self.app.config.snapshot()
        cases = [('mode', 'UNKNOWN'), ('robot', 'P04'), ('values', [None]*4), ('values', [float('nan')]*4), ('values', [1]*3), ('speed', 0), ('speed', 101), ('speed', float('inf'))]
        for field, value in cases:
            command = {**self.command, field: value}
            with self.assertRaises(ValueError):
                self.post({'version': 3, 'actions': [command]})
            self.assertEqual(self.app.config.snapshot(), original)
        with patch.object(self.app.runtime, 'busy', return_value=True):
            self.assertFalse(self.post({'version': 3, 'actions': [self.command]})[0])

    def test_mixed_sequence_dispatches_values_mode_speed_in_order(self):
        actions = [self.command, {'type': 'GRIPPER', 'robot': 'P03', 'target': 'CLOSE'},
                   {'type': 'WAIT', 'target': .1},
                   {**self.command, 'robot': 'P05', 'mode': 'JOINT', 'values': [20, 20, 30, 10], 'speed': 7},
                   {'type': 'SCARA_MOVE', 'robot': 'P05', 'target': 'HOME'}]
        self.post({'version': 3, 'actions': actions})
        events = []
        ctx = self.app.runtime._context()
        with patch.object(self.app.units.scara, 'move_position', side_effect=lambda values, label, mode, speed: events.append(('P03', mode, values, speed))), \
             patch.object(self.app.units.scara, 'set_gripper', side_effect=lambda closed: (events.append(('grip', closed)) or (True, 'ok'))), \
             patch.object(ctx, 'wait', side_effect=lambda seconds: events.append(('wait', seconds))), \
             patch.object(self.app.units.scara_p05, 'move_position', side_effect=lambda values, label, mode, speed: events.append(('P05', mode, values, speed))), \
             patch.object(self.app.units.scara_p05, 'move_saved', side_effect=lambda name: (events.append(('saved', name)) or (True, 'ok'))):
            self.app.runtime.by_code['P03'].execute(ctx)
        self.assertEqual(events, [('P03', 'JOINT', [12.5, 20, 30, 0], 15), ('grip', True), ('wait', .1),
                                  ('P05', 'JOINT', [20, 20, 30, 10], 7), ('saved', 'HOME')])

    def test_failed_move_prevents_following_actions(self):
        self.post({'version': 3, 'actions': [self.command, {'type': 'GRIPPER', 'robot': 'P03', 'target': 'CLOSE'}]})
        with patch.object(self.app.units.scara, 'move_position', side_effect=RuntimeError('alarm')), \
             patch.object(self.app.units.scara, 'set_gripper') as grip:
            with self.assertRaisesRegex(RuntimeError, 'alarm'):
                self.app.runtime.by_code['P03'].execute(self.app.runtime._context())
            grip.assert_not_called()

    def test_http_recipe_executes_through_existing_process_route_in_simulation(self):
        self.assertTrue(self.app.runtime.home()[0])
        self.app.runtime.thread.join(3)
        self.assertIsNone(self.app.state.line["fault"])
        self.post({'version': 3, 'actions': [{**self.command, 'values': [1, 2, 3, 4]}]})
        self.assertTrue(self.handler.route_post(None, '/api/process/P03/run', {})[0])
        self.app.runtime.thread.join(3)
        self.assertFalse(self.app.runtime.busy())
        self.assertIsNone(self.app.state.line['fault'])
        self.assertEqual(self.app.units.scara.current_position(), [1, 2, 3, 4])
        self.assertEqual(self.app.units.scara_p05.current_position(), [0]*4)


if __name__ == '__main__':
    unittest.main()
