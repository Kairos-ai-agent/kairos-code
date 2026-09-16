---
name: "hermes-web-ui"
description: "Install, configure, run, troubleshoot, and fully remove Hermes Web UI — a full-featured web dashboard for Hermes Agent. Covers npm-based install on Windows, CLI commands, batch file launcher, desktop"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\autonomous-ai-agents\\hermes-web-ui\\SKILL.md"
---
# Hermes Web UI

Web dashboard for Hermes Agent — AI chat, session management, usage analytics, platform channel config, cron jobs, skills browser, log viewer, web terminal, and settings.

**URL:** http://localhost:8648 (default port)
**Repo:** https://github.com/EKKOLearnAI/hermes-web-ui
**Stack:** Vue 3 + TypeScript + Koa 2 (BFF) + node-pty

## Trigger

User asks to install, configure, start, stop, or troubleshoot Hermes Web UI; or to create a desktop shortcut or launcher for it.

## 1. Install

### npm global install

```bash
npm install -g hermes-web-ui
```

On this system, npm global root is at `~/AppData/Local/hermes/node/` (a custom node install managed by Hermes). The package lands at `~/AppData/Local/hermes/node/node_modules/hermes-web-ui/`.

After install, verify:
```bash
ls ~/AppData/Local/hermes/node/node_modules/hermes-web-ui/bin/
# Should show: hermes-web-ui.mjs
```

**If bin/ or package.json are missing** (installation corruption), uninstall and reinstall:

```bash
npm uninstall -g hermes-web-ui
npm install -g hermes-web-ui
```

### 1.5 Node.js Management

**Hermes bundles its own Node.js.** On this Windows system, it lives at:
```
~/AppData/Local/hermes/node/
    ├── node.exe          # Node.js binary
    ├── npm, npm.cmd      # npm CLI shims
    ├── npm.ps1
    └── node_modules/
        └── npm/          # npm package (must be updated with node)
```

The `node` in PATH points to this Hermes-bundled copy, NOT a system-wide install. There is no `C:\Program Files\nodejs\` or nvm. Upgrading must be done by replacing the binary directly (MSI/winget installers will not affect it).

#### Upgrading Node.js

1. **Check current version:**
   ```bash
   node --version
   ```

2. **Find latest LTS:**
   ```bash
   curl -s https://nodejs.org/dist/index.json | python3 -c \
     "import json,sys; data=json.load(sys.stdin); lts=[d for d in data if d['lts']]; print('Latest LTS:', lts[0]['version'], '|', lts[0]['date'])"
   ```

3. **Download Windows binary from mirror** (recommended in China — npmmirror.com is faster than nodejs.org):
   ```bash
   curl -L -o /tmp/node-v24.16.0-win-x64.zip \
     https://npmmirror.com/mirrors/node/v24.16.0/node-v24.16.0-win-x64.zip
   ```

4. **Extract and replace:**
   ```bash
   unzip -o /tmp/node-v24.16.0-win-x64.zip -d /tmp/node-v24.16.0/
   
   # Backup old node.exe
   cp /c/Users/leohu/AppData/Local/hermes/node/node.exe \
      /c/Users/leohu/AppData/Local/hermes/node/node.exe.bak
   
   # Replace node.exe
   cp /tmp/node-v24.16.0/node-v24.16.0-win-x64/node.exe \
      /c/Users/leohu/AppData/Local/hermes/node/node.exe
   
   # Update npm scripts
   for f in npm npm.cmd npm.ps1 npx npx.cmd npx.ps1; do
     cp "/tmp/node-v24.16.0/node-v24.16.0-win-x64/$f" \
        "/c/Users/leohu/AppData/Local/hermes/node/$f"
   done
   
   # Replace npm module (this is large, use cp -r)
   rm -rf /c/Users/leohu/AppData/Local/hermes/node/node_modules/npm
   cp -r /tmp/node-v24.16.0/node-v24.16.0-win-x64/node_modules/npm \
      /c/Users/leohu/AppData/Local/hermes/node/node_modules/npm
   ```

5. **Restart Web UI and gateway** to pick up the new Node.js:
   ```bash
   # Kill running node processes
   powershell.exe -Command "Get-Process -Name node | ForEach-Object { Stop-Process -Id \$_.Id -Force }"
   
   # Wait for port release
   sleep 3
   
   # Restart Web UI via batch file
   cmd //c "C:\Users\leohu\hermes-web-ui.bat"
   ```

6. **Verify:**
   ```bash
   node --version
   npm --version
   curl -s http://127.0.0.1:8648/health  # Should show new node_version
   ```

**Caveat:** After killing and restarting the Web UI process, the browser session is invalidated. Open http://localhost:8648 and re-enter the auth token. The token file persists across restarts — read it from `~/.hermes-web-ui/.token`.

## 3. Auth Token

On first start, hermes-web-ui enables auth by default. The token is:

- Printed to server log: `~/.hermes-web-ui/server.log` — look for `"Auth enabled — token: <hex>"`
- Stored in: `~/.hermes-web-ui/.token` (v0.6.0+; older versions stored it under `<pkg_dir>/dist/server/data/.token`)

To find the token from a running instance:
```bash
cat ~/.hermes-web-ui/.token
# or
cat ~/.hermes-web-ui/server.log | grep "token"
```

**Important:** The auth token may be **rotated** when the Web UI process restarts (e.g. after a Node.js upgrade or crash). The old token from the server log will no longer work. Always read the current token:
```bash
cat ~/.hermes-web-ui/.token
```
When you kill and restart the Web UI process, the browser session is also invalidated. You must re-enter the auth token at the login page before using the Web UI again.

## 4. Gateway — Prerequisite

**v0.4.0 and earlier:** The GatewayManager would fail to auto-start the gateway on Windows native (non-WSL). Workaround was to start gateway manually first.

**v0.6.0+ (fixed):** The GatewayManager now properly detects `hermes gateway run --replace` already running on port 8642 and skips starting a duplicate. No manual gateway start needed.

However, the batch file launcher still checks gateway health before launching as a safety measure.

### 4a. Batch file launcher (with gateway health check)

Create `~/hermes-web-ui.bat`:

```bat
@echo off
title Hermes Web UI
cd /d "%~dp0"

set NODE_JS=%USERPROFILE%\AppData\Local\hermes\node\node.exe
set WEB_UI=%USERPROFILE%\AppData\Local\hermes\node\node_modules\hermes-web-ui\bin\hermes-web-ui.mjs
set LOG_DIR=%USERPROFILE%\.hermes-web-ui

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ========================================
echo     Hermes Web UI - Quick Launch
echo ========================================
echo.

:: Step 1: Check / start Hermes Gateway (backend on port 8642)
echo [..] Checking Hermes Gateway...
"%NODE_JS%" -e "http=require('http');http.get('http://127.0.0.1:8642/health',r=>{r.resume();process.exit(r.statusCode==200?0:1)}).on('error',()=>process.exit(1))" >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Hermes Gateway is already running
) else (
    echo [..] Starting Hermes Gateway...
    start "Hermes Gateway" /B hermes gateway run --replace > "%LOG_DIR%\gateway.log" 2>&1
    echo [..] Waiting for gateway (up to 15s)...
    timeout /t 12 /nobreak >nul
    echo [OK] Gateway launch attempted
)

echo.

:: Step 2: Start Hermes Web UI
"%NODE_JS%" "%WEB_UI%" status >nul 2>&1
if %errorlevel% equ 0 (
    echo [OK] Hermes Web UI is already running
) else (
    echo [..] Starting Hermes Web UI...
    "%NODE_JS%" "%WEB_UI%" start
    if %errorlevel% neq 0 (
        echo [FAIL] Web UI startup failed, check logs
        pause
        exit /b 1
    )
    echo [OK] Web UI started
)

echo.
echo [..] Opening browser...
start http://localhost:8648

echo.
echo ========================================
echo     URL: http://localhost:8648
echo     Token: see "%LOG_DIR%\server.log"
echo     Stop:  hermes-web-ui.bat stop
echo ========================================
echo.
echo Press any key to close this window (server keeps running in background)...
pause >nul
```

**IMPORTANT: Keep REM/echo lines ASCII-only** — Chinese/CJK characters in batch files cause encoding errors on Windows Chinese locale (CMD reads UTF-8 as GBK).

### 4b. Desktop shortcut (.lnk)

Use PowerShell to create the shortcut:

```powershell
$userHome = $env:USERPROFILE
$shell = New-Object -ComObject WScript.Shell
$shortcutPath = Join-Path $userHome "Desktop"
$shortcutPath = Join-Path $shortcutPath "Hermes Web UI.lnk"
$shortcut = $shell.CreateShortcut($shortcutPath)
$targetPath = Join-Path $userHome "hermes-web-ui.bat"
$shortcut.TargetPath = $targetPath
$shortcut.Arguments = "start"
$shortcut.WorkingDirectory = $userHome
$shortcut.Description = "Hermes Web UI Dashboard - http://localhost:8648"
$shortcut.WindowStyle = 7
# Use node.exe icon for visual recognizability
$nodeDir = Join-Path $userHome "AppData"
$nodeDir = Join-Path $nodeDir "Local"
$nodeDir = Join-Path $nodeDir "hermes"
$nodeDir = Join-Path $nodeDir "node"
$nodeExe = Join-Path $nodeDir "node.exe"
if (Test-Path $nodeExe) {
    $shortcut.IconLocation = $nodeExe + ", 0"
}
$shortcut.Save()
```

## 5. First-Run Behavior

On first `start`, the BFF server automatically:

1. Validates `~/.hermes/config.yaml` and fills missing `api_server` fields
2. Backs up original config to `config.yaml.bak`
3. **Attempts** to start the Hermes Gateway for profile "default" (port 8642)
4. Resolves port conflicts (kills stale processes)
5. Opens browser on successful startup

First startup may take 10–30 seconds while the gateway initializes.

### v0.6.0+ — GatewayManager is reliable on Windows

v0.6.0 fixed the GatewayManager issues on native Windows. It now:

- Correctly detects an already-running gateway at port 8642
- Skips starting a duplicate gateway
- Does NOT reassign ports in config.yaml
- Auto-detects Hermes home at `AppData\Local\hermes\` on Windows

## Platform Channel Configuration

The web UI's **Platform Channels** page lets you configure 8 messaging platforms for Hermes Gateway: Telegram, Discord, Slack, WhatsApp, Matrix, Feishu (Lark), WeChat (微信), and WeCom (企业微信).

### WeChat (微信)

Configured entirely through the Web UI:

1. Open http://localhost:8648 and enter the auth token
2. Navigate to **Platform Channels** → **WeChat**
3. Click configure — a QR code is displayed
4. Scan the QR code with WeChat on your phone
5. After scanning, the Hermes Agent bot (listed as "clawbot" — legacy name from the OpenClaw era) appears in your WeChat contacts

**Requirements:**
- The Hermes Gateway must be running (port 8642) and reachable
- WeChat integration is built into the gateway (`weixin.py` platform adapter) — no separate plugin needed
- The web UI's BFF server handles the QR code login flow and credential storage

**Troubleshooting:**
- If the Platform Channels page shows WeChat as unavailable, check that the gateway is running: `curl http://127.0.0.1:8642/health`
- QR code login may fail if the gateway restarts mid-flow — retry from the platform config page
- Credentials are auto-saved on successful login via `POST /api/hermes/weixin/save`
- **⚠️ Dual .env files on Windows**: The Web UI's save endpoint writes to `~/.hermes/.env`, but the gateway reads from `AppData\Local\hermes/.env` (where `get_hermes_home()` points on this system). If WeChat messages aren't processed after saving, check BOTH files — the gateway is likely reading stale config from AppData. See `references/wechat-ilink-debugging.md` for the full fix.
- **Messages scan/add works but no response**: Two required env vars are often missing:
  ```bash
  WEIXIN_DM_POLICY=open          # Allow DMs
  GATEWAY_ALLOW_ALL_USERS=true   # Skip allowlist check
  ```
  These must be in the `.env` file that the gateway reads. Add them and restart the gateway.
- **Token is truncated in .env (contains `...`)**: After scanning and confirming, the Web UI's BFF calls `POST /api/hermes/weixin/save` to write credentials to `~/.hermes/.env`. If the saved `WEIXIN_TOKEN` contains literal `...` (e.g. `13e9ee...aa7a`), the token is incomplete and the gateway's weixin adapter will fail to authenticate. **Fix**: Clear the bad token from `.env`, click "Reconnect" in the Web UI, re-scan the QR code, and this time verify the full token is saved by checking:
  ```bash
  grep WEIXIN_TOKEN ~/.hermes/.env
  # Should NOT contain '...' — it should be a full hex/base64 string
  ```
  If the token keeps getting truncated, it's a frontend issue (likely text-overflow CSS clipping the input value). Workaround: after the Web UI saves the token, manually append the correct full token value by running the QR login flow from the gateway instead:
  ```bash
  cd /c/Users/leohu/AppData/Local/hermes/hermes-agent
  ./venv/Scripts/python.exe -c "
  import asyncio
  from gateway.platforms.weixin import qr_login
  result = asyncio.run(qr_login('C:/Users/leohu/.hermes', timeout_seconds=120))
  print(result)
  "
  ```
- **Bot added but no response to messages**: The gateway's weixin adapter polls the iLink API for new messages. If it's not responding, check:
  1. `grep WEIXIN_TOKEN ~/.hermes/.env` — token must be complete (no `...`)
  2. `grep WEIXIN_ACCOUNT_ID ~/.hermes/.env` — account ID must be present
  3. Gateway must be running (check `/health` on port 8642)
  4. The adapter reads `.env` at startup — if you fix the token, restart the gateway: `hermes gateway run --replace`

- **Re-running QR login from terminal for debugging**: When the Web UI's save flow produces a truncated token, bypass it by running the gateway's QR login directly. This gives you the raw credentials from iLink, which you can then manually write to `.env`:
  ```bash
  cd /c/Users/leohu/AppData/Local/hermes/hermes-agent
  ./venv/Scripts/python.exe -c "
  import asyncio
  from gateway.platforms.weixin import qr_login
  result = asyncio.run(qr_login('C:/Users/leohu/.hermes', timeout_seconds=120))
  print(result)
  "
  ```
  This prints a QR code to the terminal. After the user scans and confirms on their phone, the function returns `{account_id, token, base_url, user_id}` with the FULL token. Save it:
  ```bash
  # Write to .env
  echo 'WEIXIN_ACCOUNT_ID=<id>' >> ~/.hermes/.env
  echo 'WEIXIN_TOKEN=<full_token>' >> ~/.hermes/.env
  echo 'WEIXIN_BASE_URL=https://ilinkai.weixin.qq.com' >> ~/.hermes/.env
  # Restart gateway
  hermes gateway run --replace
  ```
  **Note**: The iLink QR login has a timeout of about 60 seconds per QR code. If the QR expires, a new one is auto-fetched (up to 3 refreshes). If the user is slow to scan, the whole function may return `None`. Increase `timeout_seconds` to 180 for more patience.

- **Display QR code as image for easier scanning**: When debugging via terminal, the ASCII QR code can be hard to scan. Generate a PNG image:
  ```bash
  pip install qrcode[pil]
  python3 -c "
  import qrcode
  qr = qrcode.make('https://liteapp.weixin.qq.com/q/7GiQu1?qrcode=...')
  qr.save('/tmp/wechat_qr.png')
  "
  ```
  Then reference the image path in your response using markdown: `![QR](<C:/tmp/wechat_qr.png>)`
- **iLink API returns `application/octet-stream` instead of `application/json`**: Tencent's iLink API delivers JSON payloads with MIME type `application/octet-stream`. The `aiohttp` Python client's `response.json()` raises `ContentTypeError`. The gateway's `weixin.py` correctly uses `response.text()` + `json.loads()` — if you write custom code, do the same.
- **Web UI WeChat API endpoints** (BFF routes, all require auth token):
  - `GET /api/hermes/weixin/qrcode` — Get QR code from iLink
  - `GET /api/hermes/weixin/qrcode/status` — Poll scan status
  - `POST /api/hermes/weixin/save` — Save credentials to .env + restart gateway

### Other Platforms

Configured similarly through the Web UI's Platform Channels page. Each platform has its own settings (bot token, mention control, allow/ignore lists, etc.).

- **Shortcut flash-close**: Batch file runs `hermes-web-ui start` (daemon mode) and exits. Window appears and disappears instantly. **Fix**: Add `pause >nul` at end of batch so window stays open until user dismisses it.
- **Chinese text in batch files**: On Chinese Windows, CMD reads .bat files using the system code page (GBK). UTF-8 Chinese characters become garbled ("������"), and CMD may interpret them as commands. **Fix**: Use only ASCII characters in REM/echo lines.
- **Missing bin/ directory (corrupted install)**: The `bin/` folder or `package.json` may go missing after a failed `stop`/`update` cycle. **Fix**: `npm uninstall -g hermes-web-ui && npm install -g hermes-web-ui`.
- **Auth token unknown on first use**: After install, the token is only in the server log and `.token` file. Don't close the terminal until noting it, or look it up post-start from `~/.hermes-web-ui/server.log`.
- **Port conflict**: Default port is 8648. If occupied, use `hermes-web-ui start --port <N>`.
- **Gateway autostart fails (v0.4.0 and earlier)**: The GatewayManager could not auto-start the Hermes Gateway on native Windows, requiring manual `hermes gateway run --replace` first. **Fixed in v0.6.0+** — GatewayManager now detects an already-running gateway on port 8642 and skips starting a duplicate.
- **GatewayManager port reassignment (v0.4.0 only)**: Would write `api_server.extra.port = 8643` to config.yaml on each start. **Fixed in v0.6.0+**.
- **Hermes home mismatch**: The BFF server assumes Hermes home is `~/.hermes/` (resolved from `os.homedir()`). On Windows, Hermes may install to `AppData\Local\hermes\` instead. This causes:
  - **state.db not found**: The sessions-db service looks for `~/.hermes/state.db` but it's at `AppData\Local/hermes/state.db`. The log shows `"unable to open database file"`. The web UI falls back to CLI mode for session listing, which is slower but works.
- **Config/env not in sync**: `API_SERVER_KEY` may be in `~/.hermes/.env` but the gateway (started from `AppData\\\\Local/hermes`) may read from a different env or config file, causing `Invalid API key` errors on proxy requests. **Fix**: Set the API key in the Hermes home config: `hermes config set API_SERVER_KEY <your-key>`, then restart gateway.

- **Model/provider changes don't take effect**: Running `hermes config set model.provider xiaomi` (or `hermes model` interactive picker) writes to `AppData\\Local\\hermes/config.yaml` (where `get_hermes_home()` points on Windows). Meanwhile, the user may be editing `~/.hermes/config.yaml` manually and wondering why changes don't work. **Always check which config was written**:
  ```bash
  # After running hermes config set, verify:
  cat ~/AppData/Local/hermes/config.yaml  # This is what Hermes actually reads
  cat ~/.hermes/config.yaml               # User-facing config (may be stale)
  ```
  The gateway reads from `AppData\Local\hermes\` at startup. If you change the model, you must restart the gateway (`hermes gateway run --replace`) or start a new CLI session (`hermes` or `/reset`) for the change to take effect. `hermes config set` does NOT automatically restart the gateway.

- **node-pty `AttachConsole` error on Node.js v24+**: After upgrading to Node.js v24+, the Web UI server log may show:
  ```
  Error: AttachConsole failed
      at Object.<anonymous> (...node-pty/lib/conpty_console_list_agent.js:13:26)
  ```
  This is a known compatibility issue between `node-pty` v1.1.0 and Node.js v24 on Windows. **Impact**: The Web Terminal (node-pty) page won't work, but the Web UI dashboard, chat, and all other features function normally. No fix available until node-pty is updated. The server auto-restarts after this error and continues without terminal support.

- **Web UI /health shows old node_version after upgrade**: Even after replacing `node.exe`, the running Web UI process keeps using the old Node.js in memory. `curl -s http://127.0.0.1:8648/health` shows `node_version: "22.22.3"`. **Fix**: Kill the Node process(es) and restart the Web UI.

- **WeChat scan fails after Web UI restart**: When the Web UI process is killed and restarted (e.g. after Node.js upgrade), the browser's login session is invalidated. Open http://localhost:8648 and re-enter the auth token from `~/.hermes-web-ui/.token`.

- **Silent process exit — no crash log**: The Web UI process can silently disappear without logging any error. The `~/.hermes-web-ui/server.log` and `~/.hermes-web-ui/logs/server.log` just stop mid-operation — no uncaughtException, no SIGTERM handler output, no error stack. The PID no longer exists in `tasklist`. This is NOT the same as the node-pty AttachConsole crash (which does leave a stack trace). Possible causes:
  - Windows power management (sleep/hibernate kills the process)
  - Memory pressure (Windows evicts the process)
  - Node.js v24 runtime instability with long-running async operations
  - SQLite WAL checkpoint contention (large `.db-wal` files)
  - **No definitive fix known** — the cron monitor workaround is the most reliable mitigation.
  - See `references/silent-crash-monitor.md` for the cron job setup recipe.
### Gateway Zombie Process — Forced Restart

Sometimes the gateway process cannot be killed with normal means (`taskkill /F`, `Stop-Process -Force`). This manifests as:

- `curl http://127.0.0.1:8642/health` still responds after killing the process
- `taskkill /PID <pid> /F` returns "success" but the port is still held by the same PID
- `Stop-Process -Id <pid> -Force` returns "Access Denied"
- The gateway PID file (`~/.hermes/gateway.pid`) points to a different PID than the one holding port 8642

**`taskkill.exe //PID //F` (double-slash syntax for git-bash):** When running `taskkill` from git-bash (MSYS), the `/PID` and `/F` flags must use double-slash `//` to prevent MSYS from converting them to Windows paths. Single-slash `taskkill /PID 1234 /F` silently fails (no error, no kill). Always use:

```bash
taskkill.exe //PID 1234 //F
```

**Force kill via WMI** (works when taskkill/powershell both fail):

```powershell
$proc = Get-WmiObject Win32_Process -Filter 'ProcessId = <stuck_pid>'
if ($proc) { $proc.Terminate() }
```

Or kill ALL pythonw instances at once:

```powershell
Get-WmiObject Win32_Process -Filter "Name='pythonw.exe'" | ForEach-Object { $_.Terminate() }
```

Then (re)start the gateway:

```bash
hermes gateway run --replace
```

**Verify the new gateway loaded the correct .env**: Check that `grep WEIXIN_TOKEN ~/.hermes/.env` shows the complete token (no `...`), then confirm the gateway responds on port 8642 and the weixin adapter automatically starts (it reads `.env` at startup).

**Note**: The Web UI's GatewayManager auto-restarts the gateway when it detects the port is free. If the zombie keeps coming back, it may be a SYSTEM-level process. In that case, a full Windows reboot is the nuclear option.

### Verifying WeChat Gateway Adapter After Restart

After updating WEIXIN_TOKEN in `.env` and restarting the gateway:

1. **Check gateway health**: `curl http://127.0.0.1:8642/health`
2. **Pull messages directly from iLink** to verify the token works:
   ```bash
   curl -s -X POST "https://ilinkai.weixin.qq.com/ilink/bot/getupdates" \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer $(grep WEIXIN_TOKEN ~/.hermes/.env | cut -d= -f2)" \
     -H "AuthorizationType: ilink_bot_token" \
     -d '{"get_updates_buf":"","base_info":{"channel_version":"2.2.0"}}'
   ```
4. **Send a test message from WeChat** and check via the same iLink pull that it arrives
5. **Check gateway logs** (if log files exist at `~/.hermes/logs/` or `$HERMES_HOME/logs/`):
   ```bash
   grep -i "weixin\\|wechat" ~/.hermes/logs/*.log
   # Or if HERMES_HOME = AppData:
   grep -i "weixin\\|wechat" ~/AppData/Local/hermes/logs/*.log
   ```
6. **Verify weixin adapter connected** (from gateway startup log):
   ```bash
   grep "weixin connected\\|weixin.*Connect" ~/AppData/Local/hermes/logs/gateway.log
   # Expected output includes: "✓ weixin connected"
   # If missing, the adapter didn't start — check token and restart gateway
   ```

If the gateway is healthy but messages are not being processed, the most common cause is an **incomplete WEIXIN_TOKEN** (contains `...`). See `references/wechat-ilink-debugging.md` for full debugging guide.

## 6. Uninstall / Full Cleanup

Complete removal requires cleaning four layers — npm package is only one of them:

```bash
# 1. Remove npm package
npm uninstall -g hermes-web-ui

# 2. Kill the running process (may have a different PID each time)
netstat -ano | grep ":8648 "
# Find the LISTENING PID, then:
taskkill //F //PID <PID>

# 3. Remove the cron watchdog (if one was set up via references/silent-crash-monitor.md)
# This cron job runs every 1m and auto-restarts the Web UI when it detects it's down.
# Uninstalling the npm package does NOT remove this cron job.
hermes cron list | grep -i "web.ui\|8648\|watchdog"
hermes cron remove --job-id <job_id>

# 4. Delete batch file, shortcut, and config
rm -f ~/hermes-web-ui.bat
rm -f ~/Desktop/"Hermes Web UI.lnk"
rm -rf ~/.hermes-web-ui
```

**Critical pitfall — cron watchdog survives npm uninstall:** The `silent-crash-monitor` cron job checks `localhost:8648` every 60s. If the Web UI is not running, it (re)starts it by running `npm start -g hermes-web-ui` — which reinstalls the package if it's gone! This means:
  - `npm uninstall` alone is ineffective — the cron job un-does it within a minute
  - The Web UI keeps popping up on port 8648 even after you think you removed it
  - **Always remove the cron job first**, then uninstall the package, then kill the process

## Related Skills

- **open-webui** (mlops/) — covers generic Windows batch file and shortcut patterns for web-based tools, git-bash MSYS interop quirks, and port management.
- **hermes-agent** (autonomous-ai-agents/) — protected/bundled skill for Hermes Agent itself.

## Reference Files

| File | Contents |
|------|----------|
| `references/wechat-ilink-debugging.md` | Complete iLink API reference, curl commands, token format, common issues, gateway restart procedures, dual .env file fix |
| `references/silent-crash-monitor.md` | Cron-based watchdog for Web UI silent process exit |
| `references/batch-file-windows-tips.md` | Windows batch file patterns: port detection without findstr, PowerShell in CMD pitfalls, git-bash vs CMD differences, background process patterns, CRLF line endings |
| `references/setup-session-notes.md` | Session-specific setup notes (legacy)
