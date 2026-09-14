@echo off
REM Stop Video Grabber starting automatically at sign-in.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0autostart.ps1" -Remove
echo.
pause
