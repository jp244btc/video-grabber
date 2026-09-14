@echo off
setlocal
REM Video Grabber launcher.
REM First run: creates the Python environment and installs everything (a minute
REM or two). After that it starts instantly.
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto :run

echo ============================================================
echo  First run - setting up Video Grabber (one time only)...
echo ============================================================
echo.

where py >nul 2>&1
if %errorlevel%==0 (
    set "PYLAUNCH=py"
) else (
    where python >nul 2>&1
    if %errorlevel%==0 (
        set "PYLAUNCH=python"
    ) else (
        echo ERROR: Python was not found on this PC.
        echo.
        echo   Install it from https://www.python.org/downloads/
        echo   IMPORTANT: tick "Add python.exe to PATH" in the installer,
        echo   then double-click this file again.
        echo.
        pause
        exit /b 1
    )
)

echo [1/2] Creating the Python environment...
%PYLAUNCH% -m venv .venv
if not exist ".venv\Scripts\python.exe" (
    echo ERROR: could not create the virtual environment.
    pause
    exit /b 1
)

echo [2/2] Installing dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: dependency install failed - check your internet connection,
    echo then double-click this file again.
    pause
    exit /b 1
)

where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo.
    echo NOTE: ffmpeg was not on PATH. The app looks in the winget folder too,
    echo       so this may be fine. If downloads fail to merge, run:
    echo           winget install Gyan.FFmpeg
)

echo.
echo Setup complete - starting Video Grabber...
echo.

:run
".venv\Scripts\python.exe" app.py
pause
