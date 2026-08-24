@echo off
rem ---------------------------------------------------------------
rem  InstRef - one-time setup: creates .venv and installs deps
rem ---------------------------------------------------------------
setlocal
cd /d "%~dp0"

set "BOOT="
py -3 --version >nul 2>&1
if not errorlevel 1 set "BOOT=py -3"
if defined BOOT goto :haspy
python --version >nul 2>&1
if not errorlevel 1 set "BOOT=python"
if defined BOOT goto :haspy
goto :nopy

:haspy
echo.
echo [1/3] Creating virtual environment (.venv) ...
if exist ".venv\Scripts\python.exe" goto :hasvenv
%BOOT% -m venv ".venv"
if errorlevel 1 goto :fail

:hasvenv
echo [2/3] Upgrading pip ...
".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet

echo [3/3] Installing dependencies (this may take a few minutes) ...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo.
echo Setup complete. Launch the app with InstRef.bat
echo.
pause
exit /b 0

:nopy
echo.
echo Python 3.10+ was not found.
echo Install it from https://www.python.org/downloads/ and tick
echo "Add python.exe to PATH" during setup, then run this file again.
echo.
pause
exit /b 1

:fail
echo.
echo Setup failed. Scroll up to see the error.
echo.
pause
exit /b 1
