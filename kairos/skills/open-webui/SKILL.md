---
name: "open-webui"
description: "Install, configure, and run Open WebUI (open-webui) — a ChatGPT-like web interface for LLMs. Covers pip installation in isolated venv, domestic mirror configuration for China, first-run model download"
priority: 0.5
imported-from: "agents"
source-path: "C:\\Users\\you\\.agents\\skills\\mlops\\open-webui\\SKILL.md"
---
# Open WebUI 安装与运行

## Trigger
User asks to install, set up, or run Open WebUI.

## Steps

### 1. Create an Isolated Virtual Environment

**IMPORTANT**: Open WebUI has heavy dependencies (Torch, Transformers, ChromaDB, sentence-transformers) that conflict with Hermes Agent's venv. **Never install in Hermes's venv.**

```bash
python -m venv ~/openwebui-env
source ~/openwebui-env/Scripts/activate
```

### 2. Install with Domestic Mirror (China)

If the user is in China, use Tsinghua mirror for speed:

```bash
pip install open-webui -i https://pypi.tuna.tsinghua.edu.cn/simple/
```

### 3. Start the Service

Use a separate terminal session (background) — the service is long-running:

```bash
# Set HuggingFace domestic mirror for model downloads
export HF_ENDPOINT=https://hf-mirror.com

# Start on default port 8080
open-webui serve --port 8080
```

### 4. First-Run Behavior

On first launch, Open WebUI:
- Generates a `~/.webui_secret_key` file
- Runs alembic database migrations (SQLite, stored in `~/.open_webui/`)
- Downloads ~30 files (default sentence-transformers embedding model for RAG)
- This can take several minutes depending on network speed

### 5. Verify

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080
# Should return 200
```

## Pitfalls

- **Dependency conflicts**: Open WebUI pins specific versions of fastapi, pydantic, openai, cryptography, and other packages that Hermes Agent also depends on. Installing in the same venv breaks Hermes. **Always use a separate venv.**
- **No output in background mode**: The process may show no stdout for 30+ seconds on first run while downloading models. Use `notify_on_complete=true` and check `process(action='log')` to see progress.
- **Port already in use**: If 8080 is occupied, a stale open-webui process is the most common cause. **Better solution: include auto-kill in the startup batch script** (see Section 6) so the shortcut always works on double-click. Manual debug:
  ```bash
  # Find PID holding port 8080
  netstat -ano | grep 8080 | grep LISTEN | awk '{print $NF}' | sort -u
  # Kill it
  taskkill -f -pid <PID>
  ```
- **HuggingFace blocked (China)**: Without `HF_ENDPOINT=https://hf-mirror.com`, model downloads will fail. Set this env var before starting.
- **Multiple residual processes**: If killed and restarted, old PIDs may linger. Clean up with `taskkill -f -im open-webui.exe` on Windows.
- **CORS warning on startup**: The warning about `CORS_ALLOW_ORIGIN='*'` is normal for local/dev use. Not production-safe without configuration.

### Windows + git-bash/MSYS interop quirks (when testing batch files from the Hermes terminal)

When the terminal runs in git-bash/MSYS (as on this Windows host), `cmd.exe` arguments with single `/` flags get converted to MSYS paths. This causes subtle breakage:

- **Use `cmd //c` not `cmd /c`**: From git-bash, `cmd /c "command"` fails silently (bash eats the `/c` flag). Always use `cmd //c "batch-file.bat"` to pass `/c` through to cmd.exe.
- **`timeout` collision**: The batch command `timeout /t 2 /nobreak` runs bash's `/usr/bin/timeout`, not Windows' `C:\Windows\System32\timeout.exe`. Bash's `timeout` doesn't understand `/t`, producing `invalid time interval '/t'`. This ONLY happens when testing via `cmd //c` from git-bash — the actual batch file runs fine when double-clicked in Windows Explorer. To test, accept the garbled output or run native via PowerShell.
- **Chinese characters in batch REM/comments**: Comments with Unicode/CJK characters become garbled when piped through git-bash's encoding layer. Either keep REM lines ASCII-only or accept garbled test output.
- **Quoting and special chars**: Batch symbols (`^`, `|`, `>`, `%`) are preprocessed by bash shell before reaching cmd.exe. When testing, expect garbled error messages about paths and commands — these are bash artifacts, not real batch syntax errors.

To test a batch file accurately from git-bash:
```bash
# Best: use PowerShell to invoke the batch natively
powershell.exe -Command "Start-Process 'C:\Users\you\start-open-webui.bat' -Wait"

# Or: accept that `cmd //c` test output will have garbled bits from bash, 
# but the exit code and server-start behavior are still valid indicators
cmd //c "C:\Users\you\start-open-webui.bat"
```

### 6. Make the Service Persistent (Survive Bash Session Exit)

Background processes started via `terminal(background=true)` may terminate when the bash/git-bash session ends. To keep Open WebUI running independently on Windows:

**Create a startup batch script:**

Create `start-open-webui.bat` on the user's desktop or home directory:

```bat
@echo off
set "WEBUI_HOME=%USERPROFILE%\openwebui-env"
set "HF_ENDPOINT=https://hf-mirror.com"

REM Auto-kill any stale process holding port 8080
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8080" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a >nul 2>&1
)
timeout /t 2 /nobreak >nul

call "%WEBUI_HOME%\Scripts\activate.bat"
if %errorlevel% neq 0 (
    echo ERROR: Failed to activate venv
    pause
    exit /b 1
)
open-webui serve --port 8080

REM Only reached if server exits (error case)
echo Server exited with code %errorlevel%
pause
```

**Launch it detached from bash (using `start /B`):**

```bash
cmd //c "start /B C:\Users\you\start-open-webui.bat"
```

This uses Windows `start /B` to spin the process independently of the bash session — it survives even if Hermes is closed.

**Debug if the shortcut still flashes closed:**
1. Check if port 8080 is already in use: `netstat -ano | grep 8080 | grep LISTEN`
2. Kill the old process: `taskkill -f -im open-webui.exe`
3. Verify the batch file's for-loop syntax is correct (no Chinese chars in REM, `^|` for pipe escaping)
4. Add `pause` at strategic positions to catch error messages before the window closes

### 7. (Optional) Create a Desktop Shortcut

Use Python with `win32com.client` to create a `.lnk` file on the desktop so the user can double-click to start the service.

Write a standalone `.py` file (not inline in execute_code — bash path escaping issues):

```python
import os, sys
desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
profile = os.path.expanduser('~')
target = os.path.join(profile, 'start-open-webui.bat')
shortcut = os.path.join(desktop, 'Open WebUI.lnk')

import win32com.client
ws = win32com.client.Dispatch('WScript.Shell')
sc = ws.CreateShortcut(shortcut)
sc.TargetPath = target
sc.WorkingDirectory = profile
sc.Description = 'Open WebUI - Chat Interface'
sc.Save()
```

Run the script using MSYS path format (bash on Windows):

```bash
python /c/Users/you/create_shortcut.py
```

## Verification

```bash
curl http://localhost:8080 | head -20
```

If the page returns HTML with "Open WebUI" in the title, the service is running correctly.
