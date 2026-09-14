from __future__ import annotations

import time

from .base import UnitBase
from .basler_stream import BaslerStream, MODEL
from ..core.state import time_text


class VisionCamera(UnitBase):
    def __init__(self, state, config_store, simulation=True):
        super().__init__(state, "P02_CAMERA", "P02", "VISION_CAMERA", f"Basler {MODEL}", simulation,
                         result="WAIT", score=0, connected=False, stream_status="SIMULATION" if simulation else "SEARCHING",
                         stream_message="SIM 모드 · 실제 카메라 수신 안 함" if simulation else "카메라 연결 대기",
                         camera_model=MODEL, camera_serial="", width=2592, height=1944)
        self.config_store = config_store
        self.stream = BaslerStream(config_store, self._stream_status)

    def _stream_status(self, values):
        if values.get("camera_model"):
            values = dict(values, label=f"Basler {values['camera_model']}")
        self.update(**values)
        self.log("VISION", f"{values['stream_status']} · {values['stream_message']}")

    def connect(self):
        if not self.simulated:
            self.stream.start()

    def set_simulation(self, simulation):
        self.stream.close()
        self.simulated = bool(simulation)
        self.update(simulated=self.simulated, connected=False, result="WAIT", score=0, state="IDLE", error="",
                    stream_status="SIMULATION" if simulation else "SEARCHING",
                    stream_message="SIM 모드 · 실제 카메라 수신 안 함" if simulation else "카메라 검색 중")
        self.state_store.update_line(vision={"visible": False, "result": "WAIT", "reason": "", "time": ""})
        self.connect()

    def close(self):
        self.stream.close()

    def inspect(self, force_ng=False):
        if not self.simulated:
            frame = self.stream.latest()
            camera_check = self.config_store.snapshot()["p02"]["inspection_mode"] == "CAMERA_CHECK"
            if camera_check and frame and self.snapshot.get("connected"):
                result = "NG" if force_ng else "CAMERA_OK"
                reason = "시험용 강제 NG · AI 미검사" if force_ng else "카메라 영상 수신 확인 · AI 미검사 · 임시 사이클 시험 통과"
                self.update(enabled=True, state="ERROR" if force_ng else "DONE", result=result,
                            score=None, error=reason if force_ng else "", checked_frame=frame[1])
                self.state_store.update_line(vision={"visible": True, "result": result, "time": time_text(), "reason": reason})
                self.log("VISION", f"{result} · {reason}")
                return not force_ng
            reason = "실제 검사 알고리즘 미설정 · 영상 수신만 지원합니다." if frame and not camera_check else "카메라 영상 미수신 · 전원/LAN/IP를 확인하세요."
            self.update(state="ERROR", result="NOT_READY", error=reason, score=0)
            self.state_store.update_line(vision={"visible": True, "result": "NOT_READY", "time": time_text(), "reason": reason})
            raise RuntimeError(reason)
        self.update(enabled=True, state="INSPECTING", result="LIVE", error="")
        time.sleep(self.config_store.snapshot()["p02"]["inspection_time"])
        result = "NG" if force_ng else "OK"
        score = 0.31 if force_ng else 0.98
        reason = "베어링 방향 불일치" if force_ng else "자재 형상·방향 정상"
        self.update(state="DONE" if result == "OK" else "ERROR", result=result, score=score)
        self.state_store.update_line(vision={"visible": True, "result": result, "time": time_text(), "reason": reason})
        self.log("VISION", f"{result} · score {score:.2f} · {reason}")
        return result == "OK"
