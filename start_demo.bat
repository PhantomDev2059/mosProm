@echo off
rem Podnimaet VPN hakatona i demo-sajt, otkryvaet brauzer. Dvojnoj klik.
cd /d %~dp0
echo [1/3] VPN connect...
start "" "C:\Program Files\OpenVPN\bin\openvpn-gui.exe" --connect Hakathon.ovpn
timeout /t 15 /nobreak >nul
echo [2/3] opening http://127.0.0.1:8000
start "" http://127.0.0.1:8000
echo [3/3] server is running - do not close this window
.venv\Scripts\python -m uvicorn app.server:app --host 127.0.0.1 --port 8000
pause
