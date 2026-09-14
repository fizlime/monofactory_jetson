@echo off
cd /d "%~dp0"
if not exist .venv py -m venv .venv
call .venv\Scripts\activate.bat
pip install -r requirements.txt
python control_server.py --simulation --host 127.0.0.1 --http-port 8080
