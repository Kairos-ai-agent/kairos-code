@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

:: Watch both the frontend (vite) and the backend (kairos.main).
:: If either dies, kill the other and exit. Prevents the watchdog
:: from running forever on a dead backend (the old script only watched
:: vite, so a crashed Python loop went unnoticed). Updated to also
:: cover backend-dies, frontend-alive case.

:loop
timeout /t 5 /nobreak >nul

:: --- Check backend (python kairos.main) ---
set "backend="
for /f "tokens=*" %%a in ('powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*kairos.main*' } | Select-Object -ExpandProperty ProcessId" 2^>nul') do (
    set "backend=%%a"
)

:: --- Check frontend (node vite) ---
set "frontend="
for /f "tokens=*" %%a in ('powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*vite.js*' -and $_.Name -eq 'node.exe' } | Select-Object -ExpandProperty ProcessId" 2^>nul') do (
    set "frontend=%%a"
)

:: If backend died, kill frontend and exit.
if not defined backend (
    if defined frontend (
        for /f "tokens=*" %%a in ('powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*vite.js*' -and $_.Name -eq 'node.exe' } | Select-Object -ExpandProperty ProcessId" 2^>nul') do (
            taskkill /PID %%a /F >nul 2>&1
        )
    )
    exit
)

:: If frontend died, kill backend and exit.
if not defined frontend (
    if defined backend (
        taskkill /PID %backend% /F >nul 2>&1
    )
    exit
)

goto loop