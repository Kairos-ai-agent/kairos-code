@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

REM ---------------------------------------------------------------------------
REM Watch frontend vite on 3000 and backend kairos.main on one of PORTS.
REM
REM   backend down, frontend alive  -> restart the backend, keep watching
REM   frontend down, backend alive  -> kill the backend and exit
REM   both down                     -> exit
REM
REM Backend liveness is detected by LISTENING port, not by command line: a
REM half-started process can match the cmdline while nothing is listening.
REM
REM R38.6.5 fixes for the "kairos-backend window spam" report:
REM   * timeout /t needs an input handle and fails in a hidden console, so the
REM     loop spun freely and spawned 13 backends in 10 seconds. Use ping now.
REM   * start "title" /min batch.bat opens cmd /K, which keeps the console open
REM     forever after the batch ends, so dead attempts piled up as
REM     kairos-backend windows. Use start "" /b cmd /c batch.bat instead.
REM   * after a restart, wait for the port before looping so a slow boot
REM     cannot trigger a second spawn.
REM ---------------------------------------------------------------------------
set "PORTS=8900 9527 8964 8966 8909 8000"
set "NETFILE=%TEMP%\kairos_netstat.txt"

:loop
call :sleep 5
call :refresh
if defined BACKUP if defined FRONTUP goto loop
if defined BACKUP if not defined FRONTUP goto stop_backend
if not defined BACKUP if defined FRONTUP goto restart_backend
exit /b 0

:restart_backend
echo [watchdog] backend down - restarting
start "" /b cmd /c ""%~dp0start_backend.bat""
set /a WAITS=0
:wait_loop
call :sleep 3
call :refresh
if defined BACKUP goto loop
set /a WAITS+=1
if %WAITS% lss 8 goto wait_loop
echo [watchdog] backend still down after 24s - retrying
goto loop

:stop_backend
echo [watchdog] frontend gone - stopping backend
for %%p in (%PORTS%) do (
    for /f "tokens=5" %%a in ('findstr /c:":%%p " "%NETFILE%"') do taskkill /PID %%a /F >nul 2>&1
)
exit /b 0

:refresh
netstat -aon | findstr /i "LISTENING" > "%NETFILE%" 2>nul
set "BACKUP="
for %%p in (%PORTS%) do findstr /c:":%%p " "%NETFILE%" >nul 2>&1 && set "BACKUP=1"
set "FRONTUP="
findstr /c:":3000 " "%NETFILE%" >nul 2>&1 && set "FRONTUP=1"
exit /b 0

:sleep
ping -n %1 127.0.0.1 >nul 2>&1
exit /b 0
