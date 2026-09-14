#!/usr/bin/env bash
set -eu
cd "$(dirname "$0")"
# Prefer the Linux environment used to verify this project on the Jetson.
# A copied Windows .venv directory is not a usable Linux virtual environment.
if [ -x .venv-jetson/bin/python ]; then
    mono_python=.venv-jetson/bin/python
elif [ -x .venv/bin/python ]; then
    mono_python=.venv/bin/python
else
    python3 -m venv .venv-jetson
    mono_python=.venv-jetson/bin/python
    "$mono_python" -m pip install -r requirements.txt
fi
exec "$mono_python" control_server.py --port /dev/ttyTHS1 --baud 38400 --host 0.0.0.0 --http-port 8080 "$@"
