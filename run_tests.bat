@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

set "REPORT=%~dp0_pytest_report.txt"

echo === Kairos tests (per-file, hang-isolated) ===
echo interpreter: %PY%
echo.

rem --- kill stray MCP servers from an aborted run ---
taskkill /F /IM node.exe >nul 2>&1

rem --- per-test timeout so a single hung test is named and killed, not frozen ---
set "ARGS=-q --color=no -p no:cacheprovider"
"%PY%" -c "import pytest_timeout" >nul 2>&1 && set "ARGS=%ARGS% --timeout=120"

echo Kairos per-file run -- %DATE% %TIME% > "%REPORT%"
echo args: %ARGS% >> "%REPORT%"

for /r "%~dp0tests" %%F in (test_*.py) do (
  echo. >> "%REPORT%"
  echo ==== %%F ==== >> "%REPORT%"
  "%PY%" -m pytest %ARGS% "%%F" >> "%REPORT%" 2>&1
  echo [exit !ERRORLEVEL!] %%F
)

taskkill /F /IM node.exe >nul 2>&1

echo.
echo ---- overall summary (passed/failed lines) ----
powershell -NoProfile -Command "Select-String -Path '%REPORT%' -Pattern 'passed','failed','error' | Select-Object -Last 15 | ForEach-Object { $_.Line }"
echo.
echo Full log: %REPORT%
