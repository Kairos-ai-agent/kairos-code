@echo off
rem ===========================================================================
rem  Runs the Kairos test suite and writes the FULL output to _pytest_report.txt
rem  next to this file. The AI agent reads that file directly from the mounted
rem  folder -- no copy/paste of logs needed.
rem
rem  Usage:
rem    * double-click            -> runs the whole suite
rem    * from a terminal:        run_tests.bat tests/test_x.py -k something
rem ===========================================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

set "REPORT=%~dp0_pytest_report.txt"
echo === Kairos tests ===
echo interpreter : %PY%
echo writing to  : %REPORT%
echo.

"%PY%" -m pytest -q --color=no -p no:cacheprovider %* > "%REPORT%" 2>&1
set "RC=%ERRORLEVEL%"

echo ---- last 40 lines of the report ----
powershell -NoProfile -Command "Get-Content -Tail 40 '%REPORT%'"
echo.
echo Full log written to: %REPORT%   (pytest exit code %RC%)
echo Now tell the agent: 跑完了，读 _pytest_report.txt
endlocal
