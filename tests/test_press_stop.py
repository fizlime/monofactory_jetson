"""STOP regressions; fake CAN only, never opens USB or moves real equipment."""
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from mono_press.application import Application
from mono_press.http_server import make_server
from mono_press.units.can_press.can_link import CanLink
from mono_press.units.can_press.control_logic import Cancelled

ROOT = Path(__file__).resolve().parents[1]


class FakeLink:
    def __init__(self, config):
        self.config = config
        self.cancel_requested = lambda: False
        self.moving = threading.Event()
        self.stopped = threading.Event()
        self.stop_failure = False
        self.sent = []
        self.threads = []
        self.position = 0

    def ready(self):
        pass

    def sample(self):
        return {'io': 1, 'position': self.position}

    def begin_motion(self):
        pass

    def read(self, command):
        return 0 if command in (0x3E, 0xF1) else 1

    def move_target(self, target, rpm, acceleration=None):
        self.sent.append(0xF5)
        self.threads.append(threading.get_ident())
        self.moving.set()
        # Leave position fixed: the real Controller keeps moving until STOP.

    def stop(self):
        self.sent.append(0xF7)
        self.threads.append(threading.get_ident())
        if self.stop_failure:
            raise RuntimeError('no F7 response')
        self.stopped.set()

    def close(self):
        pass


class StopTests(unittest.TestCase):
    def setUp(self):
        self.app = Application(ROOT, '/dev/not-used', 38400, True)
        self.press = self.app.units.press
        self.link = FakeLink(self.press.can._config())
        self.patch = patch('mono_press.units.can_press.driver.CanLink', return_value=self.link)
        self.patch.start()
        self.press.set_simulation(False)
        self.assertTrue(self.press.connect())
        self.assertTrue(self.press.enable()[0])
        # STOP tests start after homing; all CAN interactions remain fake.
        with patch.object(self.press.can, 'home', side_effect=lambda **kw:
                          self.press.home_sensor.update(home=True, connected=True)):
            self.assertTrue(self.press.home()[0])
        self.workers = []

    def tearDown(self):
        self.link.stop_failure = False
        self.press.stop()
        for thread in self.workers:
            thread.join(2)
        self.press.close()
        self.patch.stop()

    def background(self, callback):
        result = []
        def run():
            try:
                result.append(callback())
            except Exception as exc:
                result.append(exc)
        thread = threading.Thread(target=run)
        self.workers.append(thread)
        thread.start()
        return thread, result

    def check_moving_stop(self, callback):
        thread, result = self.background(callback)
        self.assertTrue(self.link.moving.wait(1))
        before = time.monotonic()
        self.assertTrue(self.press.stop()[0])
        self.assertTrue(self.link.stopped.is_set())
        self.assertLess(time.monotonic() - before, 0.5)
        thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.press.axis.snapshot['state'], 'STOPPED')
        first_stop = self.link.sent.index(0xF7)
        self.assertNotIn(0xF5, self.link.sent[first_stop:])
        self.assertEqual(len(set(self.link.threads)), 1)
        return result

    def test_down_stop(self):
        self.check_moving_stop(lambda: self.press.move_steps(6000, 100))

    def test_up_stop(self):
        self.check_moving_stop(lambda: self.press.move_steps(-6000, 100))

    def test_home_stop_sends_f7(self):
        result = self.check_moving_stop(self.press.home)
        self.assertFalse(result[0][0])

    def test_stop_while_idle_still_sends_f7(self):
        self.assertTrue(self.press.stop()[0])
        self.assertIn(0xF7, self.link.sent)

    def test_stop_failure_is_not_reported_as_stopped(self):
        self.link.stop_failure = True
        ok, message = self.press.stop()
        self.assertFalse(ok)
        self.assertIn('no F7 response', message)
        self.assertEqual(self.press.axis.snapshot['state'], 'ERROR')

    def test_overlapping_movement_is_rejected(self):
        self.background(lambda: self.press.move_steps(6000, 100))
        self.assertTrue(self.link.moving.wait(1))
        with self.assertRaisesRegex(RuntimeError, '동작 중'):
            self.press.move_steps(-6000, 100)
        self.assertTrue(self.press.stop()[0])

    def test_new_explicit_movement_after_stop_can_be_stopped_again(self):
        self.check_moving_stop(lambda: self.press.move_steps(6000, 100))
        self.link.sent.clear()
        self.link.moving.clear()
        self.link.stopped.clear()
        self.check_moving_stop(lambda: self.press.move_steps(-6000, 100))

    def test_motion_queued_before_stop_cannot_clear_cancellation(self):
        driver = self.press.can
        started, release = threading.Event(), threading.Event()
        original_ready = self.link.ready
        def waiting_ready():
            started.set()
            release.wait(1)
        self.link.ready = waiting_ready
        self.background(driver.ready)
        self.assertTrue(started.wait(1))
        motion, result = self.background(lambda: driver.move_steps(6000, 100))
        deadline = time.monotonic() + 1
        while driver.commands.empty() and time.monotonic() < deadline:
            time.sleep(0.001)
        self.assertFalse(driver.commands.empty())
        stopped, _ = self.background(self.press.stop)
        self.assertTrue(driver.cancel.wait(1))
        release.set()
        stopped.join(1)
        motion.join(1)
        self.link.ready = original_ready
        self.assertFalse(motion.is_alive())
        self.assertIsInstance(result[0], Cancelled)
        self.assertNotIn(0xF5, self.link.sent)

    def test_stop_during_cycle_dwell_never_starts_up(self):
        down_done = threading.Event()
        continue_cycle = threading.Event()
        moves = []
        class Context:
            def checkpoint(inner):
                pass

            def wait(inner, seconds):
                down_done.set()
                continue_cycle.wait(1)
        with patch.object(self.press, '_move_steps', side_effect=lambda steps, speed: moves.append(steps)):
            thread, _ = self.background(lambda: self.press.run_cycle(Context()))
            self.assertTrue(down_done.wait(1))
            self.assertTrue(self.press.stop()[0])
            continue_cycle.set()
            thread.join(1)
        self.assertEqual(len(moves), 1)

    def test_api_stop_bypasses_running_sequence_lock(self):
        # Obtain the actual handler without binding a socket.
        with patch('mono_press.http_server.ThreadingHTTPServer', side_effect=lambda addr, handler: handler):
            handler = make_server(self.app)
        with patch.object(self.app.runtime, 'busy', return_value=True), \
             patch.object(self.app.runtime, 'stop', return_value=(True, 'stopped')) as stop:
            result = handler.route_post(None, '/api/unit/P04_PRESS_AXIS/command', {'action': 'STOP'})
            self.assertTrue(result[0])
            stop.assert_called_once()

    def test_usb_response_wait_is_interruptible_but_stop_is_allowed(self):
        link = CanLink.__new__(CanLink)
        cancel = threading.Event()
        link.cancel_requested = cancel.is_set
        link.stopping = False
        link.config = self.link.config
        sent = []
        link._send = lambda payload: sent.append(payload[0])
        def receive(timeout):
            cancel.set()
            link._check_cancel()
        link._receive = receive
        with self.assertRaises(Cancelled):
            link.request([0x31])
        link._receive = lambda timeout: bytes([0xF7, 1, 0xF9])
        link.stationary = lambda: True
        link.stop()
        self.assertEqual(sent, [0x31, 0xF7])


if __name__ == '__main__':
    unittest.main()
