@echo off
setlocal
cd /d "%~dp0"
title Local Screen Mirror
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 goto fail
)
".venv\Scripts\python.exe" -c "import mss, PIL, qrcode, aiortc, dxcam; import winrt.windows.graphics.capture" >nul 2>&1
if errorlevel 1 (
  echo Installing screen capture libraries. Internet is needed only for this first setup.
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto fail
)
".venv\Scripts\python.exe" server.py
pause
exit /b
:fail
echo Setup failed. Install Python 3.10 or newer with Python added to PATH, then try again.
pause
