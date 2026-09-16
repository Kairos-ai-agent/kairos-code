---
name: "windows-proxy-config"
description: "Configure Clash for Windows (CFW) and similar proxy clients on Windows 10/11. Covers config directory structure, YAML formatting, subscription vs inline proxies, API usage, and troubleshooting."
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\.archive\\windows-proxy-config\\SKILL.md"
---
# Windows 代理客户端配置

## Scope

This skill covers two sub-domains:

1. **Proxy Clients** (Clash for Windows) — configuration, troubleshooting, YAML formatting, subscription handling, API operations. See `references/cfw-guide.md` for the full guide.
2. **Local AI Proxy Services** — deploying Node.js proxy gateways that wrap CLI-based AI backends as OpenAI-compatible APIs. See `references/local-ai-proxy.md` for deployment patterns.

---

## Part 1: Clash for Windows (CFW) Configuration

## Trigger
Use when the user asks you to:
- Set up, configure, or troubleshoot Clash for Windows
- Configure any proxy/VPN client on Windows
- Import subscription URLs, test proxy nodes
- Debug "连不上" / proxy not connecting issues
- Diagnose "Edge/Chrome 上不了网" / browser can't reach internet symptoms

## Diagnosing "browser can't access internet" symptoms

When a user says "Edge/Chrome can't go online" or "browser 上不了网", run these in parallel **before** assuming config issues. The dominant failure mode on Windows is **orphan system proxy** — CFW uninstalled/deleted but the IE/system proxy still points to `127.0.0.1:7897`, so the browser dials a dead port and never connects.

### Diagnostic sequence (parallel batch)

```bash
# 1. Direct connectivity (bypasses proxy entirely)
curl -sI --max-time 5 https://www.google.com      # expect: timeout/empty
curl -sI --max-time 5 https://cn.bing.com         # expect: 200/302 (CN works direct)
curl -sI --max-time 5 https://github.com          # expect: empty (proxy needed)

# 2. Through the proxy port — does it actually listen?
curl -sI --max-time 5 -x http://127.0.0.1:7897 https://www.google.com   # expect: empty if CFW down

# 3. Who's listening on the proxy port?
netstat -ano | grep ":7897"                       # expect: nothing if CFW down
tasklist | grep -iE "clash|mihomo|verge"          # expect: no rows
```

**Interpretation matrix:**

| cn.bing | github.com | :7897 listens | Verdict |
|---------|------------|---------------|---------|
| 200/302 | empty      | no row        | **Orphan system proxy** (most common) — CFW uninstalled, proxy setting stuck |
| 200/302 | 200        | yes           | CFW running fine, no problem |
| empty   | empty      | no row        | No proxy set, full network outage (DNS/router/firewall) |
| 200/302 | empty      | yes           | CFW running but nodes dead — fall through to Part 1 §3 |

### Reading the system proxy from git-bash

`reg query` produces garbled Chinese in git-bash (codepage mismatch). **Always use powershell.exe for registry reads:**

```bash
powershell.exe -NoProfile -Command "(Get-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings').ProxyEnable; (Get-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings').ProxyServer"
# Expect: 1, 127.0.0.1:7897 (or similar)
```

Note: `netsh winhttp show proxy` reports **WinHTTP** proxy (used by some CLI tools) — **different** from the IE/system proxy that Edge uses. Don't confuse the two.

### Orphan system proxy — tell-tale signs

When CFW was uninstalled/deleted but the system proxy was never cleared:

1. Registry: `ProxyEnable=1, ProxyServer=127.0.0.1:7897`
2. `%APPDATA%\clash_win\` directory still exists (Electron-style subdirs: `Cache`, `Code Cache`, `DawnCache`, `Local State`, `Local Storage`, `GPUCache`, `blob_storage`, `Session Storage`, `Network`) — leftover from the old install
3. No `clash.exe` / `mihomo.exe` anywhere on disk (`powershell.exe` recursive search under `C:\` and `D:\`)
4. No shortcuts in `Desktop` or `Start Menu\Programs`

### Two recovery paths

**A. Quick (kill the proxy, browser goes direct — only domestic works):**
- Edge: `edge://settings/system` → turn off "使用系统代理设置" / "Use system proxy settings"
- Or system-wide: `Internet Options` → Connections → LAN settings → uncheck "Use a proxy server..."

**A2. Programmatic cleanup (same effect, no GUI) — when the user wants Edge reset and you want to do it from terminal:**

```bash
# 1. Clear the IE/system proxy in the registry (Edge/Chrome/IE all read this)
powershell.exe -NoProfile -Command "
Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -Name ProxyEnable -Value 0 -Type DWord -Force
Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -Name ProxyServer     -ErrorAction SilentlyContinue
Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -Name AutoConfigURL   -ErrorAction SilentlyContinue
Remove-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -Name ProxyOverride   -ErrorAction SilentlyContinue
"

# 2. Clear WinHTTP proxy (separate store, affects CLI tools)
netsh winhttp reset proxy     # may show "拒绝访问" without admin — usually OK because nothing was set

# 3. Verify
powershell.exe -NoProfile -Command "(Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings') | Select-Object ProxyEnable,ProxyServer,AutoConfigURL | Format-List"
# Expect: ProxyEnable=0, all others blank
```

Then for a full Edge reset to defaults:

```bash
# 1. Kill Edge so the Preferences file isn't locked
powershell.exe -NoProfile -Command "Get-Process | Where-Object { $_.ProcessName -match '^msedge' } | Stop-Process -Force; Start-Sleep 2; Get-Process | Where-Object { $_.ProcessName -match '^msedgewebview2' } | Stop-Process -Force -ErrorAction SilentlyContinue"

# 2. Back up + strip custom fields from Preferences (Chromium stores per-user settings here)
# Path: %LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Preferences
# Backup first, then remove proxy/homepage/session/default_search_provider/profile/browser keys
# (only the ones that exist — most are already absent on a default install)

# 3. Open the in-Edge reset page for the user to confirm (one-click: bookmarks/history/passwords preserved)
Start-Process 'msedge.exe' 'edge://settings/reset'
```

**B. Proper (reinstall CFW):**
- Recommend a current fork (Clash Verge Rev / Mihomo Party) over the abandoned upstream CFW
- Re-import subscription URL after install

### "Invalid credentials" / login errors after cleanup

After clearing the proxy, the user may immediately try to log into a service that was previously failing due to the proxy and now shows a fresh auth error. **Don't conflate these** — orphan-proxy fix is network-layer, the login error is application-layer. Ask for the URL/domain to disambiguate which backend's `Invalid credentials` they're seeing.

### Don't do this
- **Don't** try to "fix" the proxy setting by editing `ProxyEnable` directly in the registry while CFW is also running — race condition, CFW will rewrite it on next launch
- **Don't** restart Edge hoping it auto-discovers the dead proxy is gone — it won't, it'll keep dialing 7897
- **Don't** blame the network/router/DNS first — `cn.bing.com` returning 200 proves the network is fine, the proxy is the only variable

## Key Paths (Windows 10, git-bash)

| 项目 | 路径 |
|------|------|
| CFW 数据目录 | `~/.config/clash/` |
| 主配置 | `~/.config/clash/config.yaml` |
| CFW 配置文件 | `~/.config/clash/cfw-settings.yaml` |
| 日志 | `~/.config/clash/logs/` |
| 配置测试 | `clash-win64.exe -d ~/.config/clash -t` |
| clash 核心 | 在 CFW 安装目录下 `resources/static/files/win/x64/clash-win64.exe` |
| API 地址 | `http://127.0.0.1:9090`（从 config 中读取 external-controller） |

## Workflow

### 1. 确定核心版本
**关键区别**：Clash Premium（旧版CFW自带）≠ Clash Meta / Mihomo（新版）

| 特性 | Clash Premium (CFW 0.20.x) | Clash Meta/Mihomo |
|------|:--------------------------:|:-----------------:|
| VLESS | ✗ | ✓ |
| Hysteria2 | ✗ | ✓ |
| RULE-SET | ✗ | ✓ |
| proxy-providers (file) | 支持但有限 | 完全支持 |

### 2. 订阅处理

Free subscription sites (yoyapai.com, clashgithub.com, mibei77.com, freeclashnode.com) typically release full configs designed for Clash Meta. When using with CFW Premium:

**Method A — Inline proxies (recommended for CFW Premium):**
```yaml
proxies:
  - name: Example
    type: ss
    server: x.x.x.x
    port: 443
    cipher: chacha20-ietf-poly1305
    password: xxx

proxy-groups:
  - name: GLOBAL
    type: select
    proxies:
      - Example
      - DIRECT
```

**Method B — Proxy providers (Mihomo only):**
```yaml
proxy-providers:
  FreeNode:
    type: http  # or file
    path: ./freenode.yaml
    url: "https://..."
```

### 3. 常见配置错误与修复

| 错误 | 原因 | 修复 |
|------|------|------|
| `cannot unmarshal !!seq into string` | nameserver-policy 用列表格式 | 改成单行 `key: value` |
| `cannot unmarshal !!seq into provider.ProxySchema` | provider 文件缺 proxies: 头 | 加 `proxies:` |
| `unsupport proxy type: hysteria2` | CFW Premium 不支持的协议 | 过滤掉 type: hysteria2/hy2/vless |
| `yaml: mapping values are not allowed` | YAML 缩进不对 | 确保 2空格/4空格层级一致 |
| `GLOBAL → REJECT` | CFW 默认安全策略 | 通过 API 手动切换 `PUT /proxies/GLOBAL {"name":"节点名"}` |
| `proxy group[X]: 'Name' not found` | 分组引用的节点名不存在 | 检查名称完全匹配（含特殊字符） |

### 4. API 操作（核心运行时）

```bash
# 查看代理
curl -s http://127.0.0.1:9090/proxies/GLOBAL

# 切换节点
curl -X PUT http://127.0.0.1:9090/proxies/GLOBAL \
  -H "Content-Type: application/json" \
  -d '{"name":"节点名"}'

# 查看提供者
curl -s http://127.0.0.1:9090/providers/proxies

# 切换模式
curl -X PATCH http://127.0.0.1:9090/configs \
  -H "Content-Type: application/json" \
  -d '{"mode":"rule"}'
```

> 注意：节点名含特殊字符时 curl shell 会出错 — 用 Python 的 urllib 替代。

### 5. YAML 缩进规范

```yaml
proxies:
  - name: Foo         # 2 空格缩进
    type: ss          # 4 空格缩进（在 - name: 下面）
    server: x.x       # 4 空格

proxy-groups:
  - name: GLOBAL
    type: select
    proxies:
      - Foo           # 6 空格缩进（在 proxies: 下面）

rules:
  - MATCH,GLOBAL      # 2 空格
```

### 6. 测试流程

```bash
# 1. 验证 YAML
clash-win64.exe -d ~/.config/clash -t

# 2. 启动核心
clash-win64.exe -d ~/.config/clash -f ~/.config/clash/config.yaml

# 3. 验证 API 响应
curl http://127.0.0.1:9090/proxies/GLOBAL

# 4. 测试代理端口
curl -x http://127.0.0.1:7890 -o /dev/null -w "%{http_code}" \
  --max-time 10 https://www.bing.com

# 5. 切换节点并验证
curl -X PUT http://127.0.0.1:9090/proxies/GLOBAL \
  -H "Content-Type: application/json" \
  -d '{"name":"节点名"}'
```

### 7. 多进程冲突

多个 clash 进程会竞争同一端口。彻底清理：
```bash
kill $(ps aux | grep clash | grep -v grep | awk '{print $1}')
```
然后只启动一个。

## 8. 让命令行工具走代理（git / curl / npm / pip）

CFW 启动后只暴露本地端口（默认混合端口 7890，HTTP 端口 7891），但 git/curl 等命令行工具默认**不会**自动读取 Windows 系统代理，需要手动配置或设环境变量。

**先确认端口**：
```bash
# CFW 默认端口（可能因版本不同）
# 混合端口（HTTP + SOCKS）：7890
# HTTP only：7891
# SOCKS only：7892
# 也可以从配置里读：grep -E "mixed-port|^ - port" ~/.config/clash/config.yaml
# 实测当前端口：netstat -ano | grep LISTENING | grep -E ":789[0-9]"
```

**一次性（当前 shell）**：
```bash
export http_proxy="http://127.0.0.1:7890"
export https_proxy="http://127.0.0.1:7890"
git clone https://github.com/foo/bar.git
curl -fsSL https://example.com
```

**永久（git）**：
```bash
git config --global http.proxy http://127.0.0.1:7890
git config --global https.proxy http://127.0.0.1:7890
# 取消：
git config --global --unset http.proxy
git config --global --unset https.proxy
```

**永久（npm）**：
```bash
npm config set proxy http://127.0.0.1:7890
npm config set https-proxy http://127.0.0.1:7890
```

**永久（pip）**：`~/.pip/pip.conf` 加：
```ini
[global]
proxy = http://127.0.0.1:7890
```

**PowerShell 全局代理**（让所有 PS 进程走代理，包括 winget）：
```powershell
netsh winhttp set proxy proxy-server="127.0.0.1:7890"
# 关闭：
netsh winhttp reset proxy
```
注意：这只影响走 WinHTTP 的程序，git/curl 走 WinHTTP 不一定。

**典型调试场景**：HTTPS clone 超时或 ECONNRESET，但 `curl -x http://127.0.0.1:7890 https://github.com` 成功 → git 没走代理 → `git config --global http.proxy` 救场。

## Pitfalls

- **进程残留**：API 返回的 GLOBAL→REJECT 很可能来自旧进程，不是当前配置的问题
- **免费节点全死的概率很高**：yoyapai 当天发布的节点实测 20/20 全失效（"远程主机强制关闭连接"、"SSL handshake failed"）。这不是配置问题，是免费节点宿命
- **`&` 后台启动在 foreground terminal 中不可用**：必须用 terminal(background=true)
- **Python execute_code 的 /tmp 与 git-bash 的 /tmp 不互通**：跨 sandbox 文件传递必须通过 Windows 绝对路径
- **config.yaml 中不要写 `enhanced-mode: redir-host`**：CFW Premium 旧版不支持
- **`randomControllerPort: true`（cfw-settings.yaml 中的设置）会使 CFW 改写 config.yaml 的端口**：如果要直接使用 clash 核心独立运行，把这行关掉
- **HTTPS 证书校验失败**：CFW Premium 的 `skip-cert-verify: true` 让上游自由，但 git 仍可能因企业 CA/自签证书炸 — 临时绕开：`git -c http.sslVerify=false clone ...`；永久绕开：`git config --global http.sslVerify false`（仅限个人机器）
- **`reg query` in git-bash outputs garbled Chinese** (codepage mismatch on stdout) — for any HKCU/HKLM read that touches the system proxy, WinHTTP, or uninstall registry, wrap with `powershell.exe -NoProfile -Command "(Get-ItemProperty -Path 'HKCU:\...')..."` instead. Same gotcha applies to `netsh`, `sc query`, `wmic` when run via git-bash
- **`netsh winhttp show proxy` ≠ system proxy for Edge/Chrome** — WinHTTP proxy only affects WinHTTP-based clients (some CLI tools, older Windows components). Edge/Chrome/system-wide browsers use the IE proxy in `HKCU\\...\\Internet Settings`. Always check both when diagnosing "browser can't reach internet"
- **Orphan-proxy cleanup sequence**: when CFW is uninstalled but `ProxyEnable=1` is stuck, edit the registry to `0` and remove `ProxyServer`/`AutoConfigURL`/`ProxyOverride` — but **kill all `msedge` and `msedgewebview2` processes first** so Edge doesn't lock the Preferences file and re-write the setting on exit. Then verify with both `reg` read AND a fresh `curl -sI https://github.com` (direct, no `-x`) to confirm the browser's next session won't dial 7897 again.

---

## Part 2: Local AI Proxy Services (Node.js Gateway)

Deploy a Node.js proxy that wraps a CLI-based AI backend as a standard OpenAI-compatible API, then connect it to Hermes as a custom provider.

See `references/local-ai-proxy.md` for the full deployment guide including:
- Architecture pattern (Client → Node.js Proxy → CLI Backend)
- Step-by-step deployment (clone, install, .env, start backend, start proxy, register Hermes provider)
- Service lifecycle (starting, stopping, health checks)
- Windows-specific pitfalls (background process lifecycle, port conflicts, Node.js version, npm install failures, silent process exit, .env special characters, API_KEY security)
- Debugging checklist (symptom → check mapping)

---

## Absorbed Skills

### Windows Proxy Configuration (from `windows-proxy-configuration`)
Core compatibility matrix: CFW Premium (Clash) ≠ Clash Meta/Mihomo. Premium supports ss/trojan/vmess/http/socks5; rejects vless/hysteria2/wireguard/RULE-SET. Config location: `~/.config/clash/`. YAML indentation strict (2-space top, 4-space proxy fields, 6-space group proxies). GLOBAL mode defaults to REJECT in some CFW versions. Free subscriptions (yoyapai, clashgithub, mibei77): expect 90%+ nodes dead within hours. Strategy: strip incompatible types, add ALL remaining to url-test group. Pitfalls: `enhanced-mode: fake-ip` breaks old Premium; `proxy-providers: type: file` buggy in Premium; CFW port randomization overrides config.

### Local AI Proxy (from `local-ai-proxy-windows`)
Architecture: OpenAI-format client → Node.js proxy → CLI backend. Key env vars: PROXY_PORT (default 10000), BACKEND_PORT (default 10001). Hermes custom provider registration: `hermes config set providers.custom.<name>.base_url "http://127.0.0.1:10000/v1"`. Windows pitfalls: background processes don't survive reboots; port conflicts via `netstat -ano`; Node.js v18+ required; npm registry blocked → use `npm config set registry https://registry.npmmirror.com`; .env with special chars → use Python write_file instead of echo; `notify_on_complete=true` for silent exit detection.
