import threading
import unittest
from unittest.mock import Mock, patch

from mono_press.units.servo_bus import ServoBusController


class Clock:
    def __init__(self):
        self.now = 10.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Port:
    def __init__(self, clock):
        self.clock = clock
        self.sent = []
        self.finished = []
        self.fail = False

    def write(self, packet):
        self.sent.append((self.clock.now, packet))
        if self.fail:
            self.fail = False
            raise OSError('partial write')

    def flush(self):
        self.clock.now += len(self.sent[-1][1]) * 10 / 38400
        self.finished.append(self.clock.now)


class TxGapTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.bus = ServoBusController(Mock(), 'unused', 38400)
        self.bus.connect = Mock(return_value=True)
        self.port = Port(self.clock)
        self.bus.device = self.port
        self.patch_clock = patch('mono_press.units.servo_bus.time.monotonic', self.clock.monotonic)
        self.patch_sleep = patch('mono_press.units.servo_bus.time.sleep', self.clock.sleep)
        self.patch_clock.start()
        self.patch_sleep.start()
        self.addCleanup(self.patch_clock.stop)
        self.addCleanup(self.patch_sleep.stop)

    def test_concurrent_axes_share_gap_after_full_packet_transmission(self):
        barrier = threading.Barrier(3)
        outcomes = []

        def axis(address):
            barrier.wait()
            for body in ([0x3a], [0xf3, 1], [0xfd, 16, 0, 0, 0, 10], [0xf7]):
                outcomes.append(self.bus.write(address, body, 'test')[0])

        threads = [threading.Thread(target=axis, args=(address,)) for address in (0xe0, 0xe1, 0xe2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(outcomes, [True] * 12)
        self.assertEqual(len(self.port.sent), 12)
        self.assertEqual(self.port.sent[0][0], 10.0)
        for index in range(1, 12):
            self.assertGreaterEqual(self.port.sent[index][0] - self.port.finished[index - 1], .050 - 1e-9)
        for _, packet in self.port.sent:
            self.assertEqual(sum(packet[:-1]) & 255, packet[-1])

    def test_idle_bus_does_not_add_unnecessary_delay(self):
        self.bus.write(0xe0, [0x3a], 'read')
        self.clock.now += .1
        self.bus.write(0xe1, [0x3a], 'read')
        self.assertEqual(self.clock.sleeps, [])

    def test_partial_failure_still_spaces_next_packet(self):
        self.port.fail = True
        self.assertFalse(self.bus.write(0xe0, [0x3a], 'read')[0])
        self.assertTrue(self.bus.write(0xe1, [0xf7], 'stop')[0])
        self.assertAlmostEqual(self.port.sent[1][0] - self.port.sent[0][0], .050)


if __name__ == '__main__':
    unittest.main()
