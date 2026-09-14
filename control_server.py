#!/usr/bin/env python3
"""MONO POC line web UI and sequence control server."""

import argparse
import errno
import logging
import signal
import threading
from pathlib import Path

from mono_press.application import Application
from mono_press.http_server import make_server


def main():
    parser = argparse.ArgumentParser(description="MONO P01-P07 control server")
    parser.add_argument("--port", default="/dev/ttyTHS1", help="Servo42C UART")
    parser.add_argument("--baud", type=int, default=38400)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--http-port", type=int, default=8080)
    parser.add_argument("--simulation", action="store_true", help="하드웨어 없이 UI/시퀀스 시험")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(root / "mono_press_control.log", encoding="utf-8"), logging.StreamHandler()],
    )
    app = Application(root, args.port, args.baud, args.simulation)
    # Reserve the HTTP port before touching UART, CAN or the camera. A second
    # instance must not compete for hardware owned by the running server.
    try:
        server = make_server(app, args.host, args.http_port)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            logging.error(
                "HTTP 포트 %s가 이미 사용 중입니다. 기존 MONO 서버를 사용하거나 "
                "기존 서버를 종료한 후 다시 실행하세요. 장치는 연결하지 않았습니다.",
                args.http_port,
            )
        else:
            logging.error("HTTP 서버 시작 실패 (%s:%s): %s", args.host, args.http_port, exc)
        raise SystemExit(1) from None

    def shutdown(*_):
        threading.Thread(target=server.shutdown, name="server-shutdown", daemon=True).start()

    try:
        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)
        app.connect()
        print(f"MONO POC UI: http://127.0.0.1:{args.http_port}")
        print("MODE:", "SIMULATION" if args.simulation else f"HARDWARE {args.port} @ {args.baud}")
        server.serve_forever()
    finally:
        try:
            app.shutdown()
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
