@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

REM ---------------------------------------------------------------------------
REM Single source of truth for starting the Kairos backend.
REM Called by start.vbs on first launch and by watchdog.bat for auto-heal.
REM
REM R38.6.4: call the venv interpreter by ABSOLUTE PATH. Do NOT go back to
REM "activate.bat then python -m kairos.main". That activate.bat still
REM hard-codes the OLD repo location D:\software_bak\Kairos_code while the
REM repo now lives on E:, so it prepended a non-existent Scripts dir to PATH
REM and "python" resolved to a foreign interpreter without pydantic_settings.
REM The backend then died in under a second, nothing listened on any port and
REM the UI showed HTTP 500 upstream returned text/plain on every /api call.
REM
REM Output is appended to logs\backend_out.log so a crash is diagnosable.
REM ---------------------------------------------------------------------------

if not exist logs mkdir logs
set "KAIROS_SKIP_WORKTREES=1"
if not defined KAIROS_PORT set "KAIROS_PORT=9527"

".venv\Scripts\python.exe" -m kairos.main >> logs\backend_out.log 2>&1
