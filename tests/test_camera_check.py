import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from mono_press.core.config import ConfigStore
from mono_press.core.state import PlantState
from mono_press.sequences.p02_vision import P02VisionSequence
from mono_press.units.vision_camera import VisionCamera


class CameraCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = ConfigStore(Path(self.tmp.name) / 'config.json')
        self.config.update(lambda c: c['p02'].update(inspection_mode='CAMERA_CHECK'))
        self.state = PlantState('unused', 38400, False, self.config)
        self.camera = VisionCamera(self.state, self.config, False)

    def live(self):
        self.camera.update(connected=True, stream_status='LIVE')
        self.camera.stream._store_frame(b'jpeg')

    def test_live_camera_allows_p02_to_complete_without_ai_quality_result(self):
        self.live()
        ctx = SimpleNamespace(state=self.state, scenario='normal', checkpoint=Mock())
        P02VisionSequence(self.camera).run(ctx)
        self.assertEqual(self.state.snapshot()['processes']['P02']['status'], 'DONE')
        self.assertEqual(self.camera.snapshot['result'], 'CAMERA_OK')
        self.assertIsNone(self.camera.snapshot['score'])
        self.assertEqual(self.state.line['vision']['result'], 'CAMERA_OK')
        self.assertIn('AI 미검사', self.state.line['vision']['reason'])
        self.assertGreaterEqual(ctx.checkpoint.call_count, 2)  # Every step checks STOP.

    def test_missing_stale_and_disconnected_video_cannot_pass(self):
        for case in ('missing', 'stale', 'disconnected'):
            with self.subTest(case=case):
                self.live()
                if case == 'missing':
                    self.camera.stream.clear_frame()
                elif case == 'stale':
                    self.camera.stream.frame_at = time.monotonic() - 3
                else:
                    self.camera.update(connected=False)
                with self.assertRaisesRegex(RuntimeError, '영상 미수신'):
                    self.camera.inspect()
                self.assertEqual(self.camera.snapshot['result'], 'NOT_READY')

    def test_disabling_temporary_mode_restores_ai_requirement(self):
        self.live()
        self.assertTrue(self.camera.inspect())
        self.config.update(lambda c: c['p02'].update(inspection_mode='AI'))
        with self.assertRaisesRegex(RuntimeError, '알고리즘 미설정'):
            self.camera.inspect()
        self.assertEqual(self.camera.snapshot['result'], 'NOT_READY')

    def test_forced_ng_test_still_blocks_next_process(self):
        self.live()
        ctx = SimpleNamespace(state=self.state, scenario='vision_ng', checkpoint=Mock())
        with self.assertRaisesRegex(RuntimeError, '시험용 강제 NG'):
            P02VisionSequence(self.camera).run(ctx)
        self.assertEqual(self.state.snapshot()['processes']['P02']['status'], 'ERROR')
        ctx.checkpoint.assert_called()

    def test_invalid_inspection_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            self.config.update(lambda c: c['p02'].update(inspection_mode='IGNORE_ALL'))


if __name__ == '__main__':
    unittest.main()
