"""Single-owner Basler acquisition worker; JPEG frames stay out of status/SSE."""
from __future__ import annotations

import io
import threading
import time


MODEL = "acA2500-14gm"


class BaslerStream:
    RETRY_SECONDS = 2.0
    MAX_FRAME_AGE = 2.0

    def __init__(self, config_store, publish):
        self.config_store = config_store
        self.publish = publish
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.lifecycle = threading.Lock()
        self.thread = None
        self.frame = None
        self.frame_at = 0.0
        self.sequence = 0
        self.last_status = None

    def _status(self, status, message="", **details):
        value = dict(stream_status=status, connected=status == "LIVE", stream_message=message, **details)
        if value != self.last_status:
            self.last_status = value
            self.publish(value)

    def start(self):
        with self.lifecycle:
            if self.thread and self.thread.is_alive():
                return
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._run, name="basler-acquisition", daemon=True)
            self.thread.start()

    def close(self):
        with self.lifecycle:
            self.stop_event.set()
            self.clear_frame()
            if self.thread:
                self.thread.join(3)
            if self.thread and self.thread.is_alive():
                raise RuntimeError("카메라 연결 종료 대기 중입니다. 잠시 후 다시 시도하세요.")
            self.thread = None
            self._status("CLOSED", "영상 수신 중지")

    def clear_frame(self):
        with self.lock:
            self.frame = None
            self.frame_at = 0.0

    def latest(self):
        with self.lock:
            if self.stop_event.is_set() or not self.frame or time.monotonic() - self.frame_at > self.MAX_FRAME_AGE:
                return None
            return self.frame, self.sequence

    def _store_frame(self, jpeg):
        with self.lock:
            if self.stop_event.is_set():
                return
            self.frame = jpeg
            self.frame_at = time.monotonic()
            self.sequence += 1

    @staticmethod
    def _sdk():
        from pypylon import pylon, genicam
        from PIL import Image
        return pylon, genicam, Image

    @staticmethod
    def select_device(devices, serial):
        matches = [d for d in devices if d.GetDeviceClass() in {"BaslerGigE", "BaslerUsb"}
                   and (not serial or d.GetSerialNumber() == serial)]
        if len(matches) > 1:
            raise RuntimeError("Basler 카메라가 여러 대입니다. P02 설정에 카메라 시리얼 번호를 입력하세요.")
        return matches[0] if matches else None

    @staticmethod
    def preview_format(node, genicam, model=""):
        """Prefer native Bayer to retain color without tripling LAN traffic."""
        if node is None or not genicam.IsWritable(node):
            raise RuntimeError("카메라 PixelFormat 설정 불가")
        available = [name for name in node.GetSymbolics()
                     if genicam.IsAvailable(node.GetEntryByName(name))]
        # Mono sensors may also expose YUV containers with neutral chroma.
        # YUV support alone is not evidence that the sensor captures color.
        mono = [name for name in available if name.startswith("Mono")]
        color = [name for name in available if name.startswith(("Bayer", "RGB", "BGR"))]
        if not color and not mono:
            color = [name for name in available if name.startswith(("YUV", "YCbCr"))]
        if model == MODEL:
            color = []
        preferred = ["BayerRG8", "BayerBG8", "BayerGR8", "BayerGB8", "RGB8", "RGB8Packed", "BGR8", "BGR8Packed"]
        if color:
            selected = next((name for name in preferred if name in color), color[0])
        else:
            selected = "Mono8" if "Mono8" in available else next((name for name in available if name.startswith("Mono")), None)
        if selected is None:
            raise RuntimeError("지원하는 컬러/흑백 PixelFormat이 없습니다.")
        node.SetValue(selected)
        return selected, bool(color)

    @staticmethod
    def optional_node(nodes, name):
        try:
            return nodes.GetNode(name)
        except Exception:
            # pypylon raises for absent model-specific nodes rather than None.
            return None

    @classmethod
    def configure_transport(cls, nodes, genicam, fps):
        enable = cls.optional_node(nodes, "AcquisitionFrameRateEnable")
        if enable is not None and genicam.IsWritable(enable):
            enable.SetValue(True)
        for name in ("AcquisitionFrameRateAbs", "AcquisitionFrameRate"):
            rate = cls.optional_node(nodes, name)
            if rate is not None and genicam.IsWritable(rate):
                rate.SetValue(max(rate.GetMin(), min(float(fps), rate.GetMax())))
                break
        delay = cls.optional_node(nodes, "GevSCPD")
        frequency = cls.optional_node(nodes, "GevTimestampTickFrequency")
        if (delay is not None and frequency is not None and genicam.IsWritable(delay)
                and genicam.IsReadable(frequency)):
            # Spread each burst with at least 20 us between packets; retain any
            # larger delay configured by the operator. No OS/network changes.
            minimum, increment = delay.GetMin(), max(1, delay.GetInc())
            target = max(delay.GetValue(), round(frequency.GetValue() * 0.00002))
            target = minimum + ((max(minimum, target) - minimum + increment - 1) // increment) * increment
            delay.SetValue(min(delay.GetMax(), target))

    @staticmethod
    def encode_frame(grab, converter, Image):
        converted = converter.Convert(grab)
        try:
            frame = Image.fromarray(converted.GetArray())
            width, height = frame.size
            frame.thumbnail((960, 720))
            buffer = io.BytesIO()
            frame.save(buffer, format="JPEG", quality=80)
            return buffer.getvalue(), width, height
        finally:
            converted.Release()

    def _run(self):
        while not self.stop_event.is_set():
            try:
                self._capture_session()
            except ImportError:
                self._status("SDK_MISSING", "pypylon/Pillow 설치 필요 · requirements.txt를 설치하고 서버를 재시작하세요.")
            except Exception as exc:
                if not self.stop_event.is_set():
                    self._status("RETRYING", f"{str(exc)[:250]} · 전원/LAN/IP 또는 다른 프로그램의 카메라 점유를 확인하세요.")
            finally:
                self.clear_frame()
            if self.stop_event.wait(self.RETRY_SECONDS):
                break

    def _capture_session(self):
        pylon, genicam, Image = self._sdk()
        config = self.config_store.snapshot()["p02"]
        serial = config["camera_serial"]
        factory = pylon.TlFactory.GetInstance()
        device = self.select_device(factory.EnumerateDevices(), serial)
        if device is None:
            self._status("SEARCHING", f"Basler{' · ' + serial if serial else ''} 검색 중 · 전원/LAN/IP 확인")
            return
        camera = pylon.InstantCamera(factory.CreateDevice(device))
        try:
            self._status("CONNECTING", "카메라 연결 중")
            camera.Open()
            nodes = camera.GetNodeMap()

            def set_value(name, value, required=False):
                node = self.optional_node(nodes, name)
                if node is not None and genicam.IsWritable(node):
                    node.SetValue(value)
                elif required:
                    raise RuntimeError(f"카메라 {name} 설정 불가")

            # Free-running preview; no persistent user-set or network-IP writes.
            selector = nodes.GetNode("TriggerSelector")
            if selector is not None and genicam.IsWritable(selector):
                for name in selector.GetSymbolics():
                    if not genicam.IsAvailable(selector.GetEntryByName(name)):
                        continue
                    selector.SetValue(name)
                    set_value("TriggerMode", "Off", required=True)
            else:
                set_value("TriggerMode", "Off")
            set_value("AcquisitionMode", "Continuous", required=True)
            pixel_format, color = self.preview_format(nodes.GetNode("PixelFormat"), genicam, device.GetModelName())
            converter = pylon.ImageFormatConverter()
            converter.OutputPixelFormat = pylon.PixelType_RGB8packed if color else pylon.PixelType_Mono8
            converter.OutputBitAlignment = pylon.OutputBitAlignment_MsbAligned
            set_value("GevSCPSPacketSize", 1500)
            self.configure_transport(nodes, genicam, config["preview_fps"])
            camera.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)
            last_encoded = 0.0
            bad_frames = 0
            while not self.stop_event.is_set() and camera.IsGrabbing():
                current = self.config_store.snapshot()["p02"]
                if current["camera_serial"] != serial or current["preview_fps"] != config["preview_fps"]:
                    break
                grab = camera.RetrieveResult(1000, pylon.TimeoutHandling_ThrowException)
                try:
                    if not grab.GrabSucceeded():
                        bad_frames += 1
                        if bad_frames >= 5:
                            raise RuntimeError(grab.GetErrorDescription())
                        # Discard incomplete images. One lost frame should not
                        # tear down the camera and blank the screen for 2 s.
                        continue
                    bad_frames = 0
                    now = time.monotonic()
                    if now - last_encoded < 1 / current["preview_fps"]:
                        continue
                    jpeg, width, height = self.encode_frame(grab, converter, Image)
                    self._store_frame(jpeg)
                    if self.stop_event.is_set():
                        break
                    self._status("LIVE", "실시간 영상 수신 중", camera_model=device.GetModelName(),
                                 camera_serial=device.GetSerialNumber(), width=width, height=height,
                                 pixel_format=pixel_format, color_mode="COLOR" if color else "MONO")
                    last_encoded = now
                finally:
                    grab.Release()
        finally:
            try:
                if camera.IsGrabbing():
                    camera.StopGrabbing()
            finally:
                if camera.IsOpen():
                    camera.Close()
