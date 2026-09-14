import tempfile
import json
from contextlib import ExitStack, nullcontext
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from mono_press.application import Application
from mono_press.core.sequence import ProcessSequence, SequenceStopped


class ProbeSequence(ProcessSequence):
    code = 'P00'

    def __init__(self, events):
        super().__init__()
        self.events = events
        self.allow = False

    def steps(self, ctx):
        self.events.append('first')
        yield True
        while not self.allow:
            yield False
        self.events.append('second')
        yield True


class SequenceStepTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.app = Application(Path(temp.name), '/dev/not-used', 38400, True)
        self.ctx = self.app.runtime._context()

    def test_one_tick_one_step_wait_keeps_step_and_stop_restarts_at_zero(self):
        events = []
        seq = ProbeSequence(events)
        seq.ctx = self.ctx
        seq.sequence_run_void()
        self.assertEqual(seq.now_step, 10)
        self.assertEqual(events, [])
        seq.sequence_run_void()
        self.assertEqual((seq.before_step, seq.now_step), (10, 20))
        for _ in range(4): seq.sequence_run_void()
        self.assertEqual(events, ['first'])
        self.assertEqual(seq.now_step, 20)
        seq.machine_pause()
        self.assertTrue(self.ctx.pause_event.is_set())
        self.assertEqual(seq.now_step, 20)
        self.ctx.pause_event.clear()
        seq.allow = True
        seq.sequence_run_void()
        seq.sequence_run_void()
        self.assertTrue(seq.completed)
        self.assertEqual(events, ['first', 'second'])
        seq.machine_stop()
        self.assertEqual((seq.now_step, seq.before_step), (0, 0))
        self.ctx.stop_event.clear()
        seq.execute(self.ctx)
        self.assertEqual(events, ['first', 'second', 'first', 'second'])

    def test_reentrant_tick_cannot_duplicate_action(self):
        entered, release = threading.Event(), threading.Event()
        events = []
        class Blocking(ProbeSequence):
            def steps(inner, ctx):
                entered.set()
                release.wait(2)
                events.append('one')
                yield True
        seq = Blocking(events)
        seq.ctx = self.ctx
        seq.sequence_run_void()
        thread = threading.Thread(target=seq.sequence_run_void)
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            seq.sequence_run_void()
            self.assertTrue(seq.sequence_is_working)
        finally:
            release.set()
            thread.join(2)
        self.assertEqual(events, ['one'])
        seq.machine_stop()

    def test_stop_between_recipe_steps_releases_press_and_sends_no_next_action(self):
        self.assertTrue(self.app.units.press.enable()[0])
        self.assertTrue(self.app.units.press.home()[0])
        seq = self.app.runtime.by_code['P03']
        self.app.config.update(lambda cfg: cfg['press_recipe'].update(actions=[
            {'type': 'PRESS_DOWN', 'target': 'DOWN_STEPS'},
            {'type': 'GRIPPER', 'robot': 'P05', 'target': 'OPEN'}]))
        with patch.object(self.app.units.press, 'move_steps', side_effect=lambda *a: self.ctx.stop_event.set()), \
             patch.object(self.app.units.scara_p05, 'set_gripper') as grip:
            with self.assertRaises(SequenceStopped): seq.execute(self.ctx)
            grip.assert_not_called()
        self.assertIsNone(seq._steps)
        self.assertEqual(seq.now_step, 0)
        # A later run is a new plan, never a continuation after the stopped step.
        self.ctx.stop_event.clear()
        self.app.config.update(lambda cfg: cfg['press_recipe'].update(actions=[{'type':'WAIT','target':0}]))
        seq.execute(self.ctx)
        self.assertTrue(seq.completed)

    def test_real_untaught_position_blocks_before_any_press_or_robot_motion(self):
        seq = self.app.runtime.by_code['P03']
        self.app.units.scara.simulation = False
        with patch.object(self.app.units.press, 'prepared_cycle') as press, \
             patch.object(self.app.units.scara, 'enable') as enable:
            with self.assertRaisesRegex(RuntimeError, 'Joint'):
                seq.execute(self.ctx)
            press.assert_not_called()
            enable.assert_not_called()
        self.assertFalse(self.app.runtime.start()[0])

    def test_recipe_failure_does_not_continue_and_publishes_step(self):
        seq = self.app.runtime.by_code['P03']
        self.app.config.update(lambda cfg: cfg['press_recipe'].update(actions=[
            {'type':'DOBOT_MOVE','robot':'P03','mode':'JOINT','values':[1,2,3,4],'speed':10},
            {'type':'GRIPPER','robot':'P03','target':'CLOSE'}]))
        with patch.object(self.app.units.scara,'move_position',side_effect=RuntimeError('drive fault')), \
             patch.object(self.app.units.scara,'set_gripper') as grip:
            with self.assertRaisesRegex(RuntimeError,'drive fault'):seq.run(self.ctx)
            grip.assert_not_called()
        self.assertEqual(self.app.state.processes['P03']['status'], 'ERROR')
        self.assertGreater(self.app.state.processes['P03']['now_step'], 0)

    def test_restored_27_actions_keep_load_press_unload_order(self):
        restored = json.loads((Path(__file__).resolve().parents[1]/'settings/poc_config.json').read_text(encoding='utf-8'))
        actions = restored['press_recipe']['actions']
        self.assertEqual(len(actions), 27)
        self.app.config.update(lambda cfg: cfg.update(press_recipe=restored['press_recipe']))
        events=[]
        with ExitStack() as stack:
            stack.enter_context(patch.object(self.app.units.press,'prepared_cycle',return_value=nullcontext()))
            stack.enter_context(patch.object(self.app.units.press,'move_steps',side_effect=lambda n,*a:events.append('DOWN' if n>0 else 'UP')))
            for code,robot in self.app.units.scaras.items():
                stack.enter_context(patch.object(robot,'enable',return_value=(True,'ok')))
                stack.enter_context(patch.object(robot,'move_saved',side_effect=lambda name,c=code:(events.append((c,name)) or (True,'ok'))))
                stack.enter_context(patch.object(robot,'set_gripper',side_effect=lambda closed,c=code:(events.append((c,'CLOSE' if closed else 'OPEN')) or (True,'ok'))))
            self.app.runtime.by_code['P03'].run(self.ctx)
        expected=[]
        for i in range(1,5):
            expected.extend([('P03',f'BUFFER_PICK_{i}'),('P03','CLOSE'),('P03','PRESS_LOAD'),('P03','OPEN')])
            if i<4:expected.extend(['DOWN','UP'])
        expected.extend([('P05','PRESS_UNLOAD'),('P05','CLOSE'),('P05','OUTPUT'),('P05','OPEN'),('P05','HOME')])
        self.assertEqual(events,expected)


if __name__ == '__main__': unittest.main()
