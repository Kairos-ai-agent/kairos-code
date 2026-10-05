@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

set "REPORT=%~dp0_pytest_report.txt"
echo Running the Kairos test suite...
echo interpreter: %PY%
echo report file: %REPORT%
echo.

"%PY%" -m pytest -q --color=no -p no:cacheprovider %* > "%REPORT%" 2>&1
set "RC=%ERRORLEVEL%"

echo.
echo ---- last 30 lines ----
powershell -NoProfile -Command "Get-Content -Tail 30 '%REPORT%'"
echo.
echo Full log: %REPORT%   (pytest exit code %RC%)
