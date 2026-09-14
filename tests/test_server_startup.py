"""Startup failures must not connect to or compete for real hardware."""
import errno
import unittest
from unittest.mock import Mock, patch

import control_server


class ServerStartupTests(unittest.TestCase):
    def setUp(self):
        self.app = Mock()
        self.server = Mock()
        for name, replacement in (
            ('Application', Mock(return_value=self.app)),
            ('make_server', Mock(return_value=self.server)),
            ('logging.FileHandler', Mock()),
            ('logging.basicConfig', Mock()),
            ('signal.signal', Mock()),
        ):
            patcher = patch('control_server.' + name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch('sys.argv', ['control_server.py', '--simulation'])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_busy_port_exits_without_connecting_or_stopping_devices(self):
        control_server.make_server.side_effect = OSError(errno.EADDRINUSE, 'busy')
        with self.assertLogs(level='ERROR') as logs, self.assertRaises(SystemExit) as error:
            control_server.main()
        self.assertEqual(error.exception.code, 1)
        self.assertIn('이미 사용 중', logs.output[0])
        self.app.connect.assert_not_called()
        self.app.shutdown.assert_not_called()

    def test_bind_precedes_connect_and_normal_exit_closes_resources(self):
        order = Mock()
        order.attach_mock(control_server.make_server, 'bind')
        order.attach_mock(self.app.connect, 'connect')
        order.attach_mock(self.server.serve_forever, 'serve')
        control_server.main()
        self.assertEqual([call[0] for call in order.mock_calls], ['bind', 'connect', 'serve'])
        self.app.shutdown.assert_called_once()
        self.server.server_close.assert_called_once()

    def test_connect_failure_closes_devices_and_http_socket(self):
        self.app.connect.side_effect = RuntimeError('connect failed')
        with self.assertRaisesRegex(RuntimeError, 'connect failed'):
            control_server.main()
        self.app.shutdown.assert_called_once()
        self.server.server_close.assert_called_once()
        self.server.serve_forever.assert_not_called()

    def test_shutdown_failure_still_releases_http_socket(self):
        self.app.shutdown.side_effect = RuntimeError('close failed')
        with self.assertRaisesRegex(RuntimeError, 'close failed'):
            control_server.main()
        self.server.server_close.assert_called_once()
