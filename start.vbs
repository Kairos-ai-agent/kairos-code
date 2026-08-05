Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

' Clear Python cache before starting
WshShell.Run "cmd /c cd /d """ & scriptDir & """ && del /s /q /f __pycache__ >nul 2>&1 && for /d /r %%d in (__pycache__) do rd /s /q ""%%d"" >nul 2>&1", 0, True

' Start Backend (hidden)
backendCmd = "cmd /c cd /d """ & scriptDir & """ && .venv\Scripts\activate.bat && python -m kairos.main"
WshShell.Run backendCmd, 0, False

WScript.Sleep 4000

' Start Frontend (hidden)
frontendCmd = "cmd /c cd /d """ & scriptDir & "\web"" && node node_modules\vite\bin\vite.js --host"
WshShell.Run frontendCmd, 0, False

WScript.Sleep 2000

' Start Watchdog (hidden)
watchdogCmd = "cmd /c cd /d """ & scriptDir & """ && watchdog.bat"
WshShell.Run watchdogCmd, 0, False

' Open browser
WshShell.Run "http://localhost:3000", 1, False
