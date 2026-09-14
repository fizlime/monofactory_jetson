"""Auto-connect/preview tests with mocked pylon; no camera or motor commands."""
import io
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mono_press.application import Application
from mono_press.core.config import ConfigStore
from mono_press.core.state import PlantState
from mono_press.http_server import make_server
from mono_press.units.basler_stream import BaslerStream, MODEL
from mono_press.units.vision_camera import VisionCamera


class BaslerStreamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = ConfigStore(Path(self.tmp.name) / 'config.json')
        self.status = Mock()
        self.stream = BaslerStream(self.config, self.status)

    @staticmethod
    def device(serial='123', model=MODEL, device_class='BaslerGigE'):
        return SimpleNamespace(GetModelName=lambda: model, GetSerialNumber=lambda: serial,
                               GetDeviceClass=lambda: device_class)

    def sdk(self, devices=None):
        device = self.device()
        factory = Mock()
        factory.EnumerateDevices.return_value = [device] if devices is None else devices
        camera = Mock()
        camera.IsGrabbing.side_effect = [True, False, False]
        camera.IsOpen.return_value = True
        nodes = {}
        def get_node(name):
            if name not in nodes:
                nodes[name] = Mock()
            return nodes[name]
        camera.GetNodeMap.return_value.GetNode.side_effect = get_node
        get_node('TriggerSelector').GetSymbolics.return_value = ['FrameStart']
        get_node('PixelFormat').GetSymbolics.return_value = ['Mono8', 'Mono12']
        rate = get_node('AcquisitionFrameRateAbs')
        rate.GetMin.return_value, rate.GetMax.return_value = 1.0, 14.0
        delay = get_node('GevSCPD')
        delay.GetMin.return_value, delay.GetMax.return_value = 0, 100000
        delay.GetInc.return_value, delay.GetValue.return_value = 1, 0
        get_node('GevTimestampTickFrequency').GetValue.return_value = 125000000
        grab = Mock()
        grab.GrabSucceeded.return_value = True
        camera.RetrieveResult.return_value = grab
        pylon = SimpleNamespace(TlFactory=SimpleNamespace(GetInstance=lambda: factory),
                                InstantCamera=Mock(return_value=camera),
                                GrabStrategy_LatestImageOnly=1, TimeoutHandling_ThrowException=2,
                                ImageFormatConverter=Mock(return_value=Mock()), PixelType_RGB8packed=3,
                                PixelType_Mono8=4, OutputBitAlignment_MsbAligned=5)
        image = Mock(size=(2592, 1944))
        image.save.side_effect = lambda buffer, **kw: buffer.write(b'jpeg-test-frame')
        Image = Mock()
        Image.fromarray.return_value = image
        genicam = SimpleNamespace(IsWritable=lambda n: True, IsAvailable=lambda n: True, IsReadable=lambda n: True)
        return (pylon, genicam, Image), camera, grab, nodes

    def test_basler_mono_color_selection_and_multiple_cameras_require_serial(self):
        first, second, wrong = self.device('1'), self.device('2', 'acA2500-14gc'), self.device('3', 'OTHER', 'Other')
        self.assertIs(self.stream.select_device([wrong, first], ''), first)
        self.assertIsNone(self.stream.select_device([wrong], ''))
        with self.assertRaisesRegex(RuntimeError, '여러 대'):
            self.stream.select_device([first, second], '')
        self.assertIs(self.stream.select_device([first, second], '2'), second)
        self.assertIsNone(self.stream.select_device([first], '999'))

    def test_no_camera_publishes_searching_without_opening_another_model(self):
        sdk, camera, _, _ = self.sdk([self.device(model='OTHER', device_class='Other')])
        with patch.object(self.stream, '_sdk', return_value=sdk):
            self.stream._capture_session()
        camera.Open.assert_not_called()
        self.assertEqual(self.status.call_args.args[0]['stream_status'], 'SEARCHING')
        self.assertIsNone(self.stream.latest())

    def test_capture_encodes_jpeg_releases_result_and_closes_device(self):
        sdk, camera, grab, nodes = self.sdk()
        with patch.object(self.stream, '_sdk', return_value=sdk):
            self.stream._capture_session()
        self.assertEqual(self.stream.latest()[0], b'jpeg-test-frame')
        nodes['PixelFormat'].SetValue.assert_called_with('Mono8')
        nodes['TriggerMode'].SetValue.assert_called_with('Off')
        nodes['GevSCPSPacketSize'].SetValue.assert_called_with(1500)
        nodes['GevSCPD'].SetValue.assert_called_with(2500)
        nodes['AcquisitionFrameRateEnable'].SetValue.assert_called_with(True)
        nodes['AcquisitionFrameRateAbs'].SetValue.assert_called_with(8.0)
        self.assertEqual(sdk[0].ImageFormatConverter.return_value.OutputPixelFormat, sdk[0].PixelType_Mono8)
        sdk[0].ImageFormatConverter.return_value.Convert.return_value.Release.assert_called_once()
        grab.Release.assert_called_once()
        camera.Close.assert_called_once()
        self.assertEqual(self.status.call_args.args[0]['width'], 2592)
        self.assertEqual(self.status.call_args.args[0]['stream_status'], 'LIVE')

    def test_color_prefers_bayer_even_when_camera_also_supports_mono(self):
        sdk, _, _, nodes = self.sdk([self.device(model='acA2500-14gc')])
        nodes['PixelFormat'].GetSymbolics.return_value = ['Mono8', 'BayerBG8', 'RGB8Packed']
        with patch.object(self.stream, '_sdk', return_value=sdk):
            self.stream._capture_session()
        nodes['PixelFormat'].SetValue.assert_called_with('BayerBG8')
        self.assertEqual(sdk[0].ImageFormatConverter.return_value.OutputPixelFormat, sdk[0].PixelType_RGB8packed)
        self.assertEqual(self.status.call_args.args[0]['color_mode'], 'COLOR')

    def test_unavailable_formats_are_skipped_and_rgb_only_is_supported(self):
        node = Mock()
        node.GetSymbolics.return_value = ['Mono8', 'BayerRG8', 'RGB8Packed']
        node.GetEntryByName.side_effect = lambda name: name
        genicam = SimpleNamespace(IsWritable=lambda node: True, IsAvailable=lambda name: name != 'BayerRG8')
        self.assertEqual(self.stream.preview_format(node, genicam), ('RGB8Packed', True))
        node.GetSymbolics.return_value = ['Coord3D_C16']
        with self.assertRaisesRegex(RuntimeError, 'PixelFormat'):
            self.stream.preview_format(node, genicam)

    def test_mono_camera_yuv_support_does_not_enable_color_or_double_payload(self):
        sdk, _, _, nodes = self.sdk()
        nodes['PixelFormat'].GetSymbolics.return_value = ['Mono8', 'Mono12', 'Mono12Packed', 'YUV422Packed', 'YUV422_YUYV_Packed']
        with patch.object(self.stream, '_sdk', return_value=sdk):
            self.stream._capture_session()
        nodes['PixelFormat'].SetValue.assert_called_with('Mono8')
        self.assertEqual(self.status.call_args.args[0]['color_mode'], 'MONO')

    def test_optional_transport_nodes_can_be_absent(self):
        nodes = Mock()
        nodes.GetNode.side_effect = RuntimeError('Node not existing')
        self.stream.configure_transport(nodes, SimpleNamespace(), 8)

    def test_transport_retains_larger_packet_delay(self):
        sdk, camera, _, nodes = self.sdk()
        nodes['GevSCPD'].GetValue.return_value = 5000
        self.stream.configure_transport(camera.GetNodeMap(), sdk[1], 8)
        nodes['GevSCPD'].SetValue.assert_called_with(5000)

    def test_converted_image_is_released_if_jpeg_encoding_fails(self):
        sdk, _, grab, _ = self.sdk()
        converter = sdk[0].ImageFormatConverter()
        sdk[2].fromarray.side_effect = RuntimeError('image failed')
        with self.assertRaisesRegex(RuntimeError, 'image failed'):
            self.stream.encode_frame(grab, converter, sdk[2])
        converter.Convert.return_value.Release.assert_called_once()

    def test_real_sdk_rgb_and_bayer_conversion_preserves_red_and_mono_stays_gray(self):
        from pypylon import pylon
        from PIL import Image
        for source_type, output_type, color in (
            (pylon.PixelType_RGB8packed, pylon.PixelType_RGB8packed, True),
            (pylon.PixelType_BayerRG8, pylon.PixelType_RGB8packed, True),
            (pylon.PixelType_Mono8, pylon.PixelType_Mono8, False),
        ):
            source = pylon.PylonImage.Create(source_type, 32, 32)
            try:
                array = source.GetArray()
                array[:] = 0
                if source_type == pylon.PixelType_RGB8packed:
                    array[:, :, 0] = 240
                elif color:
                    array[::2, ::2] = 240
                else:
                    array[:] = 120
                # GetArray is a copy in pypylon 4; attach the populated data.
                source.AttachArray(array, source_type)
                converter = pylon.ImageFormatConverter()
                converter.OutputPixelFormat = output_type
                jpeg, width, height = self.stream.encode_frame(source, converter, Image)
                image = Image.open(io.BytesIO(jpeg))
                self.assertEqual((width, height), (32, 32))
                self.assertEqual(image.mode, 'RGB' if color else 'L')
                if color:
                    red, green, blue = image.getpixel((10, 10))
                    self.assertGreater(red, 200)
                    self.assertLess(green, 30)
                    self.assertLess(blue, 30)
                else:
                    self.assertAlmostEqual(image.getpixel((10, 10)), 120, delta=2)
            finally:
                source.Release()

    def test_failed_grab_releases_buffer_and_closes_camera(self):
        sdk, camera, grab, _ = self.sdk()
        camera.IsGrabbing.side_effect = [True] * 6
        grab.GrabSucceeded.return_value = False
        grab.GetErrorDescription.return_value = 'lost packet'
        with patch.object(self.stream, '_sdk', return_value=sdk):
            with self.assertRaisesRegex(RuntimeError, 'lost packet'):
                self.stream._capture_session()
        self.assertEqual(grab.Release.call_count, 5)
        camera.Close.assert_called_once()

    def test_one_incomplete_frame_does_not_reconnect_and_next_good_frame_is_encoded(self):
        sdk, camera, grab, _ = self.sdk()
        camera.IsGrabbing.side_effect = [True, True, False, False]
        grab.GrabSucceeded.side_effect = [False, True]
        with patch.object(self.stream, '_sdk', return_value=sdk):
            self.stream._capture_session()
        self.assertEqual(grab.Release.call_count, 2)
        self.assertEqual(self.stream.latest()[0], b'jpeg-test-frame')
        self.assertEqual(self.status.call_args.args[0]['stream_status'], 'LIVE')

    def test_missing_sdk_and_disconnect_are_retried_without_duplicate_workers(self):
        self.stream.RETRY_SECONDS = 0.005
        calls = []
        def session():
            calls.append(1)
            if len(calls) == 1:
                raise ImportError('not installed')
            if len(calls) == 2:
                self.stream._store_frame(b'old')
                raise RuntimeError('disconnected')
            self.assertIsNone(self.stream.latest())
            self.stream._status('LIVE', 'reconnected')
            self.stream.stop_event.set()
        with patch.object(self.stream, '_capture_session', side_effect=session):
            self.stream.start()
            thread = self.stream.thread
            self.stream.start()
            self.assertIs(thread, self.stream.thread)
            thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertEqual([c.args[0]['stream_status'] for c in self.status.call_args_list], ['SDK_MISSING', 'RETRYING', 'LIVE'])
        self.assertIsNone(self.stream.latest())

    def test_stale_and_stopped_frames_are_never_served(self):
        self.stream._store_frame(b'frame')
        self.assertIsNotNone(self.stream.latest())
        self.stream.frame_at = time.monotonic() - 3
        self.assertIsNone(self.stream.latest())
        self.stream._store_frame(b'new')
        self.stream.close()
        self.assertIsNone(self.stream.latest())
        self.assertIsNone(self.stream.frame)

    def test_simulation_never_imports_sdk_or_starts_acquisition(self):
        state = PlantState('/dev/no', 38400, True, self.config)
        camera = VisionCamera(state, self.config, True)
        with patch.object(camera.stream, 'start') as start:
            camera.connect()
            camera.set_simulation(True)
            start.assert_not_called()
        self.assertTrue(camera.inspect())
        self.assertEqual(camera.snapshot['result'], 'OK')

    def test_real_inspection_never_returns_simulated_ok_even_with_live_frame(self):
        state = PlantState('/dev/no', 38400, False, self.config)
        camera = VisionCamera(state, self.config, False)
        for live in (False, True):
            if live:
                camera.stream._store_frame(b'frame')
            with self.assertRaises(RuntimeError):
                camera.inspect(force_ng=False)
            self.assertEqual(camera.snapshot['result'], 'NOT_READY')
            self.assertEqual(state.line['vision']['result'], 'NOT_READY')

    def test_application_lifecycle_starts_camera_without_waiting_for_uart(self):
        app = Application(Path(self.tmp.name), '/dev/no', 38400, True)
        with patch.object(app.units.camera, 'connect') as connect, \
             patch.object(app.bus, 'connect', return_value=False), \
             patch.object(app.units.press, 'connect', return_value=False):
            self.assertFalse(app.connect())
            connect.assert_called_once()
        with patch.object(app.units.camera, 'close') as close, \
             patch.object(app.runtime, 'stop'), patch.object(app.units.press, 'close'), patch.object(app.bus, 'close'):
            app.shutdown()
            close.assert_called_once()

    def test_jpeg_http_endpoint_no_frame_and_live_frame(self):
        app = Application(Path(self.tmp.name), '/dev/no', 38400, True)
        with patch('mono_press.http_server.ThreadingHTTPServer', side_effect=lambda addr, handler: handler):
            Handler = make_server(app)
        handler = Handler.__new__(Handler)
        handler.path = '/api/vision/frame.jpg'
        handler.send_json = Mock()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.wfile = io.BytesIO()
        handler.do_GET()
        self.assertEqual(handler.send_json.call_args.args[0], 503)
        app.units.camera.stream._store_frame(b'jpeg')
        handler.do_GET()
        handler.send_response.assert_called_with(200)
        handler.send_header.assert_any_call('Content-Type', 'image/jpeg')
        handler.send_header.assert_any_call('Cache-Control', 'no-store')
        self.assertEqual(handler.wfile.getvalue(), b'jpeg')
        headers = dict(c.args for c in handler.send_header.call_args_list)
        self.assertIn('blob:', headers['Content-Security-Policy'])


if __name__ == '__main__':
    unittest.main()
