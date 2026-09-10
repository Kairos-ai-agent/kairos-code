Set WshShell = CreateObject("WScript.Shell")

REM ---------------------------------------------------------------------------
REM R38.6.4: the old script only killed :8900 and :3000, but start_silent.bat
REM pins KAIROS_PORT=9527, so "stop" left the backend and the watchdog
REM running and the next start ran into a stale process / port. Now:
REM   1) kill the watchdog first, so it cannot restart the backend we are
REM      about to stop - watchdog.bat auto-heals a missing backend now;
REM   2) kill every port the stack can listen on, plus vite's :3000.
REM ---------------------------------------------------------------------------

REM 1) Watchdog. Exclude this PowerShell's own PID because its command line
REM    also contains the string watchdog.bat.
WshShell.Run "powershell -NoProfile -Command ""Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*watchdog.bat*' -and $_.ProcessId -ne $PID } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }""", 0, True

REM 2) Kill by port
ports = Array("8900", "9527", "8964", "8966", "8909", "8000", "3000")
For Each p In ports
  WshShell.Run "cmd /c for /f ""tokens=5"" %a in ('netstat -aon ^| findstr "":" & p & ".*LISTENING""') do taskkill /PID %a /F >nul 2>&1", 0, True
Next

MsgBox "Kairos Code stopped.", vbInformation, "Kairos Code"
