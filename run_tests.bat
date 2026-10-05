@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

set "REPORT=%~dp0_pytest_report.txt"

echo === Kairos tests ===
echo interpreter: %PY%
echo.

rem --- clean up stray MCP servers left by an aborted run (node-based) ---
taskkill /F /IM node.exe >nul 2>&1

rem --- verbose + a per-test timeout if pytest-timeout is installed, so a hung
rem     test is named and killed instead of freezing the whole run ---
set "ARGS=-v --color=no -p no:cacheprovider"
"%PY%" -c "import pytest_timeout" >nul 2>&1 && set "ARGS=%ARGS% --timeout=90"

echo report file: %REPORT%
echo.

"%PY%" -m pytest %ARGS% %* > "%REPORT%" 2>&1
set "RC=%ERRORLEVEL%"

echo.
echo ---- last 40 lines ----
powershell -NoProfile -Command "Get-Content -Tail 40 '%REPORT%'"
echo.
echo Full log: %REPORT%   (pytest exit code %RC%)

rem --- clean up again so nothing is left running ---
taskkill /F /IM node.exe >nul 2>&1
