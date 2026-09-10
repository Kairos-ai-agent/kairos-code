Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

' Clear Python cache before starting
WshShell.Run "cmd /c cd /d """ & scriptDir & """ && del /s /q /f __pycache__ >nul 2>&1 && for /d /r %%d in (__pycache__) do rd /s /q ""%%d"" >nul 2>&1", 0, True

' Start Backend (hidden)
' KAIROS_SKIP_WORKTREES=1: on this machine git worktree checkout of the
' repo takes ~100s per worktree (antivirus scanning), which made backend
' startup hang for minutes and the launcher pile up stuck processes.
' Agents then run on the main checkout instead of isolated worktrees.
' Remove the var if worktree isolation is wanted (and add a Defender
' exclusion for the repo to make checkout fast again).
' R38.6.4: backend launch lives in start_backend.bat (single source of
' truth, shared with watchdog.bat). It calls the venv interpreter by
' ABSOLUTE PATH — the venv's activate.bat still hard-codes the OLD repo
' location (D:\software_bak\Kairos_code; the repo now lives on E:), which
' prepended a non-existent Scripts dir to PATH, made `python` resolve to a
' foreign interpreter without pydantic_settings, and the backend died in
' <1s → every /api call 500 → "LLM 设置连接不上". Output goes to
' logs\backend_out.log so the next crash is diagnosable instead of silent.
backendCmd = "cmd /c cd /d """ & scriptDir & """ && start_backend.bat"
WshShell.Run backendCmd, 0, False

WScript.Sleep 4000

' Start Frontend (hidden)
' vite.config.ts re-resolves the backend port on every /api request
' (8900/9527/8964/8966/8909/8000, 1.5s cache), so no KAIROS_PORT needed here.
frontendCmd = "cmd /c cd /d """ & scriptDir & "\web"" && node node_modules\vite\bin\vite.js --host"
WshShell.Run frontendCmd, 0, False

WScript.Sleep 2000

' Start Watchdog (hidden)
watchdogCmd = "cmd /c cd /d """ & scriptDir & """ && watchdog.bat"
WshShell.Run watchdogCmd, 0, False

' Open browser
WshShell.Run "http://localhost:3000", 1, False
