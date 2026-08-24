@echo off
rem ---------------------------------------------------------------
rem  InstRef - headless run (used by Windows Task Scheduler)
rem  Writes to logs\sync_YYYY-MM.log
rem ---------------------------------------------------------------
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" exit /b 1
".venv\Scripts\pythonw.exe" -m igsaved.cli --sync --quiet
exit /b %errorlevel%
