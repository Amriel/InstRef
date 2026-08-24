@echo off
rem ---------------------------------------------------------------
rem  InstRef - launcher
rem ---------------------------------------------------------------
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo First run detected - preparing the environment...
    call "%~dp0install.bat"
)

if not exist ".venv\Scripts\pythonw.exe" (
    echo Environment is missing. Run install.bat first.
    pause
    exit /b 1
)

start "InstRef" ".venv\Scripts\pythonw.exe" -m igsaved
exit /b 0
