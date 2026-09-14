"""Fixed P04 distance scale; no USB access or real equipment movement."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from mono_press.application import Application
from mono_press.core.config import ConfigStore, validate_config
from mono_press.units.can_press.control_logic import Config


class CalibrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = Application(Path(self.tmp.name), '/dev/not-used', 38400, True)

    def test_legacy_fields_are_removed_and_scale_cannot_be_overridden(self):
        for raw in ({}, {'mm_per_rev': 9, 'steps_per_rev_original': 1234},
                    {'steps_per_mm': 900}, {'mm_per_rev': None, 'steps_per_rev_original': ''}):
            with self.subTest(raw=raw):
                p04 = validate_config({'p04': {'down_steps': 1200, 'up_steps': 600, 'can': raw}})['p04']
                self.assertEqual(p04['can']['steps_per_mm'], 600)
                self.assertNotIn('mm_per_rev', p04['can'])
                self.assertNotIn('steps_per_rev_original', p04['can'])
                self.assertEqual((p04['down_mm'], p04['up_mm']), (2, 1))

    def test_saved_config_uses_only_fixed_scale(self):
        self.app.config.update(lambda c: c['p04']['can'].update(
            mm_per_rev=3, steps_per_rev_original=1600, steps_per_mm=100))
        can = ConfigStore(self.app.config.path).snapshot()['p04']['can']
        self.assertEqual(can['steps_per_mm'], 600)
        self.assertNotIn('mm_per_rev', can)
        self.assertNotIn('steps_per_rev_original', can)

    def test_controller_preserves_existing_encoder_and_speed_conversion(self):
        c = Config()
        self.assertEqual(c.steps(1), 600)
        self.assertEqual(c.steps(2), 1200)
        self.assertEqual(c.counts(1), 3072)
        self.assertEqual(c.counts(2), 6144)
        self.assertEqual(c.mm(6144), 2)
        self.assertEqual(c.rpm(200), 47)

    def test_driver_uses_fixed_scale_even_for_unvalidated_legacy_input(self):
        config = self.app.config.snapshot()
        config['p04']['down_mm'] = 2
        config['p04']['can'].update(mm_per_rev=9, steps_per_rev_original=1234, steps_per_mm=100)
        with patch.object(self.app.config, 'snapshot', return_value=config):
            c = self.app.units.press.can._config()
        self.assertEqual(c.target_distance_mm, 2)
        self.assertEqual(c.steps(1), 600)
        self.assertEqual(c.counts(2), 6144)

    def test_move_command_and_position_readback_use_same_scale(self):
        driver = self.app.units.press.can
        link = Mock()
        link.sample.return_value = {'io': 1, 'position': 0}
        controller = Mock()
        controller.sample.return_value = link.sample.return_value
        with patch('mono_press.units.can_press.driver.CanLink', return_value=link), \
             patch('mono_press.units.can_press.driver.Controller', return_value=controller):
            try:
                self.assertTrue(driver.connect())
                driver.move_steps(1200, 100)
                driver.move_steps(-600, 100)
                self.assertEqual(controller.jog.call_args_list, [call(2, -1), call(1, 1)])
                driver._emit('sample', {'io': 1, 'position': -6144})
                self.assertEqual(self.app.units.press.current_position(), 1200)
            finally:
                driver.close()


if __name__ == '__main__':
    unittest.main()
