@echo off
REM Make Video Grabber start hidden every time you sign in to Windows.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0autostart.ps1"
echo.
pause
