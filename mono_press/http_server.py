from __future__ import annotations

import json
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from .core.distance_units import press_mm


def json_bytes(value): return json.dumps(value, ensure_ascii=False).encode("utf-8")


def make_server(application, host="0.0.0.0", port=8080):
    web_root = application.root / "web"

    class Handler(BaseHTTPRequestHandler):
        server_version = "MONO-POC/4"
        def log_message(self, fmt, *args): return

        def handle(self):
            try:
                super().handle()
            except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, OSError):
                return

        def headers_common(self, content_type, length=None):
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-src 'self'; object-src 'none'")
            if length is not None: self.send_header("Content-Length", str(length))

        def send_json(self, status, data):
            body = json_bytes(data); self.send_response(status); self.headers_common("application/json; charset=utf-8", len(body)); self.end_headers(); self.wfile.write(body)

        def read_json(self):
            try:
                length = min(int(self.headers.get("Content-Length", "0")), 1024 * 1024)
                return json.loads(self.rfile.read(length) or b"{}")
            except Exception: raise ValueError("요청 본문이 올바른 JSON이 아닙니다.")

        def same_origin(self):
            origin = self.headers.get("Origin")
            return not origin or urlparse(origin).netloc == self.headers.get("Host")

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/status": return self.send_json(200, application.state.snapshot())
            if path == "/api/vision/frame.jpg":
                frame = application.units.camera.stream.latest()
                if frame is None:
                    return self.send_json(503, {"ok": False, "message": "카메라 영상 수신 대기"})
                body, sequence = frame
                self.send_response(200)
                self.headers_common("image/jpeg", len(body))
                self.send_header("X-Frame-Id", str(sequence))
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/api/events": return self.events()
            relative = "index.html" if path in {"", "/"} else path.lstrip("/")
            target = (web_root / relative).resolve()
            if web_root.resolve() not in target.parents and target != web_root.resolve(): return self.send_error(403)
            if not target.is_file(): return self.send_error(404)
            body = target.read_bytes(); content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}: content_type += "; charset=utf-8"
            self.send_response(200); self.headers_common(content_type, len(body)); self.end_headers(); self.wfile.write(body)

        def events(self):
            self.send_response(200); self.headers_common("text/event-stream; charset=utf-8"); self.send_header("Connection", "keep-alive"); self.end_headers()
            last_version = -1
            try:
                while True:
                    with application.state.changed:
                        if application.state.version == last_version: application.state.changed.wait(12)
                        snapshot = application.state.snapshot()
                    last_version = snapshot["version"]
                    self.wfile.write(b"data: " + json_bytes(snapshot) + b"\n\n"); self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError): return

        def do_POST(self):
            if not self.same_origin(): return self.send_json(403, {"ok": False, "message": "Origin 불일치"})
            path = urlparse(self.path).path
            try:
                ok, message = self.route_post(path, self.read_json())
                return self.send_json(200 if ok else 409, {"ok": ok, "message": message, "state": application.state.snapshot()})
            except ValueError as exc: return self.send_json(400, {"ok": False, "message": str(exc)})
            except Exception as exc:
                application.state.record("ERROR", f"API {path} · {exc}")
                return self.send_json(500, {"ok": False, "message": str(exc)})

        def route_post(self, path, body):
            runtime = application.runtime
            if path in {"/api/line/start", "/api/sequence/start"}: return runtime.start(body.get("scenario", "normal"))
            if path == '/api/line/home': return runtime.home()
            home_match = re.fullmatch(r'/api/process/(P0[0-6])/home',path)
            if home_match: return runtime.home(home_match.group(1))
            if path in {"/api/line/pause", "/api/sequence/pause"}: return runtime.pause()
            if path in {"/api/line/resume", "/api/sequence/resume"}: return runtime.resume()
            if path in {"/api/line/stop", "/api/sequence/stop", "/api/all-stop"}: return runtime.stop()
            if path == "/api/line/ack": return runtime.acknowledge()
            if path == "/api/line/reset": return runtime.reset()
            if path == "/api/p00/simulate":
                if runtime.busy(): return False, "운전 중에는 감지 시험값을 바꿀 수 없습니다."
                if not isinstance(body.get("detected"), bool): return False, "detected bool 필요"
                return application.units.material.set_simulated(body["detected"])
            if path == "/api/p01/check":
                if any(axis.busy() for axis in application.units.p01_axes): return False, "P01 이동 중입니다."
                return runtime.manual_action(application.check_p01)
            if path == "/api/reconnect":
                ok = application.connect(); return ok, application.connection_summary()
            if path == "/api/mode":
                return application.switch_mode(body.get("mode", ""))
            if path == "/api/recipe":
                with runtime.guard:
                    if runtime.busy(): return False, "운전 중에는 통합 순서를 저장할 수 없습니다."
                    if not isinstance(body, dict) or body.get("version") != 3 or not isinstance(body.get("actions"), list):
                        raise ValueError("version: 3과 actions 배열을 입력하세요.")
                    application.config.update(lambda cfg: cfg.__setitem__("press_recipe", body))
                    application.state.touch()
                    application.state.record("SETTING", "HTTP 통합 동작 순서 저장")
                    return True, "통합 순서를 저장했습니다. 공정 실행 버튼으로 실행하세요."
            process_match = re.fullmatch(r"/api/process/(P0[0-6])/run", path)
            if process_match: return runtime.run_process(process_match.group(1), body.get("scenario", "normal"))
            unit_match = re.fullmatch(r"/api/unit/([A-Z0-9_]+)/command", path)
            if unit_match:
                if unit_match.group(1) in {axis.unit_id for axis in application.units.p01_axes} and str(body.get('action', '')).upper() == 'STOP':
                    if runtime.thread and runtime.thread.is_alive():
                        return runtime.stop()
                    return application.units.manual_command(unit_match.group(1), 'STOP', body)
                gripper = next((r for r in application.units.scaras.values() if unit_match.group(1) == r.gripper.unit_id), None)
                if gripper and str(body.get("action", "")).upper() == "STOP":
                    return runtime.stop_gripper(gripper)
                if unit_match.group(1) in {uid for robot in application.units.scaras.values() for uid in robot.unit_ids()} and str(body.get("action", "")).upper() == "STOP" and runtime.busy():
                    if runtime.manual_active and not (runtime.thread and runtime.thread.is_alive()):
                        robot = next(r for r in application.units.scaras.values() if unit_match.group(1) in r.unit_ids())
                        return robot.stop()
                    return runtime.stop()
                if unit_match.group(1) == "P04_PRESS_AXIS" and str(body.get("action", "")).upper() == "STOP":
                    # A process STOP also cancels the sequence so it cannot
                    # issue the next press move after this one is stopped.
                    if runtime.busy():
                        return runtime.stop()
                    return application.units.press.stop()
                if runtime.busy(): return False, "자동/단독 시퀀스 중에는 수동 장치 제어가 잠깁니다."
                if unit_match.group(1) in {uid for robot in application.units.scaras.values() for uid in robot.unit_ids()}:
                    return runtime.manual_action(lambda: application.units.manual_command(unit_match.group(1), body.get("action", ""), body))
                return runtime.manual_action(lambda: application.units.manual_command(unit_match.group(1), body.get("action", ""), body))
            if path == "/api/config":
                if runtime.busy(): return False, "운전 중에는 설정을 저장할 수 없습니다."
                if body.get('scara_p05', {}).get('shared_with', '') != application.config.snapshot()['scara_p05'].get('shared_with', ''):
                    return False, 'Dobot 장치 공유 방식 변경은 서버를 종료한 뒤 설정 파일에서 적용하세요.'
                if body.get("press_recipe", {}).get("version") != 3:
                    return False, "SCARA 2대 분리 설정을 불러오려면 새로고침하세요."
                if any(axis.busy() for axis in application.units.p01_axes): return False, "P01 동작 중에는 설정을 저장할 수 없습니다."
                application.config.save(body); application.sync_feed_config(); application.state.touch(); application.state.record("SETTING", "P00-P06 설정 저장")
                return True, "설정을 저장했습니다."
            scara_match = re.fullmatch(r"/api/scara/(P03|P05)/(save-current|move-saved|delete-position|move|jog|connect|home)", path)
            if scara_match:
                if runtime.busy(): return False, "운전 중에는 SCARA 위치를 변경할 수 없습니다."
                code, command = scara_match.groups()
                robot = application.units.scaras[code]
                config_key = robot.config_key
                if command == "home":
                    return runtime.manual_action(robot.home)
                if command == "connect":
                    if any(axis.snapshot.get("enabled") for axis in robot.axes):
                        return False, "ALL DISABLE 후 USB를 다시 연결하세요."
                    def reconnect():
                        robot.close()
                        ok = robot.connect()
                        status = robot.axes[0].snapshot
                        message = f"{code} USB 연결 완료 · {status.get('error') or 'ALL ENABLE 후 이동 가능'}" if ok else f"{code} USB 연결 실패 · {status.get('error', '')}"
                        return ok, message
                    return runtime.manual_action(reconnect)
                mode = str(body.get("mode", "JOINT")).upper()
                if command == "move":
                    return runtime.manual_action(lambda: robot.manual_move(body.get("values"), mode, body.get("speed", 10)))
                if command == "jog":
                    return runtime.manual_action(lambda: robot.jog(body.get("axis"), body.get("delta"), mode, body.get("speed", 10)))
                name = str(body.get("name", "")).strip().upper()[:32]
                if not name: return False, "위치 이름을 입력하세요."
                if command == "move-saved":
                    return runtime.manual_action(lambda: robot.move_saved(name))
                if command == "save-current":
                    if mode != "JOINT":
                        return False, "현재 위치 저장은 JOINT J1~J4 각도를 사용하세요."
                    def save_current():
                        values = robot.pose_values("JOINT")
                        application.config.update(lambda cfg: save_pose(cfg, values))
                        application.state.touch()
                        return True, f"{code} 현재 Joint 위치를 {name}으로 저장했습니다."
                    def save_pose(cfg, values):
                        cfg[config_key]["positions"][name] = values
                        cfg[config_key].setdefault("position_meta", {})[name] = {
                            "mode": mode, "source": "SIMULATION" if robot.simulation else "REAL"}
                        if not robot.simulation:
                            cfg[config_key]["position_meta"][name]["port"] = cfg[config_key].get("port", "")
                    return runtime.manual_action(save_current)
                positions = application.config.snapshot()[config_key]["positions"]
                if name not in positions: return False, "저장 위치가 없습니다."
                if any(item.get("type") == "SCARA_MOVE" and item.get("robot") == code and str(item.get("target", "")).upper() == name for item in application.config.snapshot()["press_recipe"]["actions"]):
                    return False, "동작 순서에서 사용 중인 위치는 삭제할 수 없습니다."
                application.config.update(lambda cfg: cfg[config_key]["positions"].pop(name, None))
                application.state.touch(); return True, f"{code} SCARA 위치 {name} 삭제"
            if path == "/api/press/save-current":
                if runtime.busy(): return False, "운전 중에는 위치를 저장할 수 없습니다."
                name = str(body.get("name", "")).strip().upper()[:32]
                if not name: return False, "위치 이름을 입력하세요."
                application.config.update(lambda cfg: cfg["p04"]["saved_positions_mm"].__setitem__(name, press_mm(application.units.press.current_position())))
                application.state.touch(); return True, f"프레스 현재 위치를 {name}으로 저장했습니다."
            if path == "/api/press/delete-position":
                if runtime.busy(): return False, "운전 중에는 위치를 삭제할 수 없습니다."
                name = str(body.get("name", "")).strip().upper()
                positions = application.config.snapshot()["p04"]["saved_positions_mm"]
                if name not in positions: return False, "저장 위치가 없습니다."
                if any(item.get("type") == "PRESS_MOVE" and str(item.get("target", "")).upper() == name for item in application.config.snapshot()["press_recipe"]["actions"]):
                    return False, "동작 순서에서 사용 중인 위치는 삭제할 수 없습니다."
                application.config.update(lambda cfg: cfg["p04"]["saved_positions_mm"].pop(name, None))
                application.state.touch(); return True, f"프레스 위치 {name} 삭제"
            if path == "/api/vision/dismiss":
                vision = dict(application.state.line.get("vision", {})); vision["visible"] = False; application.state.update_line(vision=vision)
                return True, "Vision 창 닫기"
            return False, "알 수 없는 API입니다."

    return ThreadingHTTPServer((host, port), Handler)
