---
name: "china-hf-mirror"
description: ">-"
priority: 0.5
version: "1.0.0"
imported-from: "hermes"
source-path: "hermes/skills/mlops/china-hf-mirror/SKILL.md"
---
# China HF Mirror Configuration

Many ML tools (ComfyUI, Wan2GP, diffusers, transformers, Open WebUI) download models from HuggingFace Hub at runtime. In China, `huggingface.co` is blocked — you must use `hf-mirror.com` as a proxy.

## Quick Fix: Set `HF_ENDPOINT`

The `huggingface_hub` Python library natively respects the `HF_ENDPOINT` environment variable. Set it before launching any Python-based app:

**Windows (user env, persistent):**
```powershell
[Environment]::SetEnvironmentVariable("HF_ENDPOINT", "https://hf-mirror.com", "User")
```

**Windows (batch file — add before app launch):**
```batch
set "HF_ENDPOINT=https://hf-mirror.com"
```

**Linux/macOS:**
```bash
export HF_ENDPOINT=https://hf-mirror.com
```

**Docker:**
```bash
docker run -e HF_ENDPOINT=https://hf-mirror.com ...
```

This affects: `hf_hub_download()`, `snapshot_download()`, `from_pretrained()`, `AutoModel.from_pretrained()` — anything that uses the `huggingface_hub` library.

## What `HF_ENDPOINT` Does NOT Fix

Some apps construct their own download URLs by hardcoding `https://huggingface.co/` — they do NOT use `huggingface_hub` internally. These apps fail even with `HF_ENDPOINT` set.

### Detection

Search the app's Python source for hardcoded HF URLs:

```bash
grep -r "huggingface\.co" /path/to/app --include="*.py" -l
```

Common patterns:
```python
# ❌ Hardcoded — will fail in China
return f"https://huggingface.co/{repo}/resolve/main/{path}"

# ✅ Respects HF_ENDPOINT — will use mirror
import os
endpoint = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
return f"{endpoint}/{repo}/resolve/main/{path}"
```

### Fix Pattern

Patch any function that builds HF URLs to read `HF_ENDPOINT` from the environment:

```python
# BEFORE
def build_hf_url(repo_id, *path_parts):
    return f"https://huggingface.co/{repo_id}/resolve/main/{path}"

# AFTER
import os
def build_hf_url(repo_id, *path_parts):
    endpoint = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
    return f"{endpoint}/{repo_id}/resolve/main/{path}"
```

## App-Specific Fixes

### Wan2GP / Gaia Video Factory

**Two approaches — pick one based on user preference:**

#### Approach A: Mirror (if user agrees)

- **File:** `engine/Wan2GP/shared/utils/hf.py`
- **Issue:** `build_hf_url()` hardcodes `https://huggingface.co`
- **Fix:** Patch to respect `HF_ENDPOINT` (see pattern above)
- **Env setup:** Add `HF_ENDPOINT` to `start-local.bat` (inherited by Node.js → Python child process)

#### Approach B: Local-only (no mirror, when user rejects mirror)

For users in China who want models loaded strictly from local `ckpts/` with no network dependency:

1. **`process_files_def()`** in `wgp.py` — add `timeout=5` + try-except to every `hf_hub_download()` and `snapshot_download()` call so shared model downloads fail fast instead of hanging 30s.
2. **`download_file()`** in `wgp.py` — add `timeout=5` to the two `hf_hub_download()` calls.
3. **`preload_URLs` loop** in `download_models()` — change `raise` to `continue` when a preload URL download fails (the correct quantization-matched file is loaded later by `get_text_encoder_name()`).
4. If the app spawns the runtime Python (no torch) instead of the venv Python (has torch), create `runtime/python/Lib/site-packages/sitecustomize.py` that adds the Wan2GP venv's site-packages to `sys.path`.
5. Kill all stuck Python processes (they may be hanging on TCP `SynSent` to HF). The user must regenerate the image.

See `references/wan2gp-gaia-video-factory-debug.md` for the full debug story and exact fix locations.

### Open WebUI

- **Config:** Set `HF_ENDPOINT` in the Open WebUI startup script or service env
- See `open-webui` skill for details

## GitHub Release Downloads in China (GFW)

GitHub Releases (`github.com/<owner>/<repo>/releases`) are often blocked or extremely slow in China. Unlike HuggingFace (which has `hf-mirror.com`), GitHub has no single official mirror, so you must use proxies or alternative methods.

### Diagnostic Checklist

Before trying workarounds, confirm the problem:

```bash
# Check if github.com is reachable at all
curl -sI --connect-timeout 5 --max-time 10 "https://github.com" -o /dev/null -w "%{http_code}"
# 000 = blocked entirely, 200 = reachable

# Check download speed independently
curl -fsSL --max-time 15 \
  "https://github.com/Owner/Repo/releases/download/v1.0.0/asset.zip" \
  -o /dev/null -w "speed: %{speed_download}B/s\nhttp: %{http_code}\n"
```

### Finding Latest Version (When GitHub Pages Are Blocked)

GitHub's **REST API** often works even when the web UI and CDN are blocked:

```bash
# Get latest release info
curl -sL "https://api.github.com/repos/Owner/Repo/releases/latest" | \
  python3 -c "import json,sys; d=json.load(sys.stdin); print('Tag:', d.get('tag_name')); [print(f'  {a[\"name\"]}') for a in d.get('assets',[])]"
```

### GitHub Proxy/Mirror Services

These proxy GitHub releases. **Availability varies by region and ISP** — try several:

| Proxy URL | Notes |
|-----------|-------|
| `https://gh-proxy.com/github.com/...` | Often works but slow |
| `https://githubfast.com/...` | Intermittent 403 |
| `https://ghproxy.com/github.com/...` | Often blocked |
| `https://mirror.ghproxy.com/github.com/...` | Often blocked |
| `https://gitclone.com/github.com/...` | May return 500 |
| `https://hub.nuaa.cf/...` | Academic mirror, often timed out |
| `https://ghproxy.net/https://github.com/...` | **Verified working 2025-06** — `git clone` and `curl` both work. Used for `embed-ai-tool` install |

Usage:
```bash
curl -fsSL -o /tmp/asset.zip \
  "https://gh-proxy.com/github.com/Owner/Repo/releases/download/v1.0.0/asset.zip"
```

| `https://ghproxy.net/https://github.com/...` | **Verified working 2025-06** — `git clone` and `curl` both work. Used for `embed-ai-tool` install |

### `git clone` Full Repository via Proxy

Standard `git clone https://github.com/...` fails in China. Prepend a proxy URL:

```bash
git clone --depth 1 https://ghproxy.net/https://github.com/Owner/Repo.git
```

**Note:** Not all proxies support `git clone` (only those proxying full HTTPS transport). `ghproxy.net` and `gh-proxy.com` do; simple file proxies may not.

### `npx` / NPM Package Install via Proxy

When `npx` tries to clone a GitHub repo (e.g., `npx skills add owner/repo`), it uses raw GitHub access and fails. Workaround: clone manually via proxy, then use local path:

```bash
# Step 1: Clone via proxy
git clone --depth 1 https://ghproxy.net/https://github.com/Owner/repo.git /tmp/repo

# Step 2: Install from local path
npx skills add /tmp/repo -g -y
# or if skills CLI fails, copy skills/ directly:
cp -r /tmp/repo/skills/* ~/.claude/skills/
```

### Terminal Proxy Detection and Configuration

Many users have a VPN/proxy client (Clash, V2Ray, sing-box) that works in the browser but NOT in the terminal. This is because terminal sessions don't inherit system proxy settings on Windows.

**Step 1: Check current env vars**
```bash
echo "https_proxy=$https_proxy" "http_proxy=$http_proxy" "all_proxy=$all_proxy"
```

**Step 2: Scan common proxy ports**
```bash
for port in 7890 7891 1080 10809 1081 1088 9090 8080 3128 8888; do
  curl -fsSL --connect-timeout 3 --max-time 5 \
    -x "http://127.0.0.1:$port" "https://www.google.com" \
    -o /dev/null -w "Port $port: %{http_code}" 2>/dev/null && echo " OK" \
    || echo "Port $port: fail"
done
```

**Step 3: Set for the session**
```bash
export https_proxy=http://127.0.0.1:PORT
export http_proxy=http://127.0.0.1:PORT
```

**Common ports by client:** Clash Verge/Meta `7890`, v2rayN `10809`, NekoRay `2080`.

### aria2c for Unstable Connections

aria2c supports multi-connection downloads and resume — better than curl for slow/spotty connections:

```bash
aria2c -x 4 -s 4 --connect-timeout=15 --timeout=60 \
  --max-tries=5 --retry-wait=5 \
  -d /tmp -o asset.zip \
  "https://github.com/Owner/Repo/releases/download/v1.0.0/asset.zip"
```

If even aria2c shows `0B/s` and `Timeout` across all connections, the terminal has NO route to GitHub — the user must configure proxy or use a VPN with TUN mode.

### Fallback: Manual Download via Browser + Local Install

When all terminal methods fail, the user can download via browser (which may have proxy access):

```bash
# If the CLI has an install script that supports --binary:
curl -fsSL https://example.com/install | bash -s -- --binary /path/to/downloaded/binary

# Or manual install:
mkdir -p ~/.toolname/bin
cp /path/to/downloaded/binary ~/.toolname/bin/
chmod +x ~/.toolname/bin/binary
# Add to PATH in .bashrc: export PATH=$HOME/.toolname/bin:$PATH
```

## App-Specific Fixes

**Huawei Cloud (recommended — verified working):**
```bash
pnpm config set registry https://mirrors.huaweicloud.com/repository/npm/
```

**Tencent Cloud:**
```bash
pnpm config set registry https://mirrors.cloud.tencent.com/npm/
```

**npmmirror.com (formerly taobao) — may NOT work for some users.** Try Huawei Cloud first.

Verify:
```bash
pnpm config get registry
```

### npm 主仓在 GFW 下同样极慢 — 必须切镜像

**Symptom (verified 2026-08-14, deepseek-harness install):** `npm install -g @deepseek-ai/dsh` 拉 528 个包，每个 cache miss 35–40s，sharp/shikijs 大文件 60s+，verbose 日志停在 reify 阶段后再 20 分钟无新输出（实际在 symlink+postinstall，但 verbose 不打印），进程不死锁但用户体感"卡死"。

**Fix (1 minute vs 20+ minutes):**
```bash
npm config set registry https://registry.npmmirror.com
npm config get registry  # 确认
```

切到 npmmirror 后 528 个包 **1 分钟装完**。这个 registry 持久化到 `~/.npmrc`，**会影响之后所有 npm install**。

**Why npmmirror wins here over Huawei/Tencent mirrors:** npmmirror 是淘宝完整镜像，所有 `@deepseek-ai/*` 这种 scoped 包都能解析；某些云镜像对 npm 协议覆盖不全，可能找不到 scoped 包。

**Worked example:** `references/deepseek-harness-install.md` documents the full install of `@deepseek-ai/dsh` (528 packages) using npmmirror — 1 minute vs 20+ minute hang on the default registry.

**Same rule applies:** If `node_modules` was partially installed (incomplete due to interrupted npm install), delete and reinstall. For pnpm:
```bash
rm -rf node_modules
pnpm install
```

**Symptoms of incomplete node_modules:** `ERR_MODULE_NOT_FOUND` for core files (e.g. `next-dev.js`), missing `.modules.yaml` in `node_modules/`.

**pnpm `onlyBuiltDependencies` warning:** The `pnpm.onlyBuiltDependencies` field in `package.json` is no longer read by newer pnpm versions (v11+). This warning is benign and does not block installation.

**pnpm v11+ build script approval:** Packages with native build scripts (better-sqlite3, ffmpeg-static, sharp, esbuild, @swc/core, etc.) are now blocked by default. After `pnpm install`, if you see `[ERR_PNPM_IGNORED_BUILDS]`, run:
```bash
pnpm approve-builds
```
Approve required packages (at minimum: `better-sqlite3`, `ffmpeg-static`, `sharp` for video/DB apps), then re-run `pnpm install`.

## exFAT Drives and pnpm Symlinks

**pnpm default node-linker uses symlinks, which require NTFS.** exFAT (common on USB drives, SD cards, external drives) does NOT support symlinks — pnpm install will fail with `[ERR_PNPM_EISDIR] symlinkAllModules EISDIR`.

### Detection
```bash
# Check filesystem type (Git Bash / MSYS)
mount | grep "E:"   # look for "type ntfs" or "type exfat"
df -T /e/ | tail -1
```

### Fix
If the project lives on an exFAT drive, set pnpm to hoisted mode (no symlinks):
```bash
pnpm config set node-linker hoisted
```
Then delete and reinstall:
```bash
rm -rf node_modules
pnpm install
```

**User's drive layout:** D: is exFAT (needs hoisted), E: is NTFS (works natively). Prefer installing Node.js projects on NTFS drives.

## Native Module Compilation on Windows (VS Build Tools)

Packages with native C++ addons (better-sqlite3, sharp, @swc/core, esbuild, ffmpeg-static) need Visual Studio Build Tools to compile. In China, `prebuild-install` often times out downloading prebuilt binaries from GitHub, so `node-gyp` fallback kicks in — which requires the full MSVC toolchain.

### Detection
If `pnpm rebuild` or `pnpm install` fails with:
```
gyp ERR! find VS You need to install the latest version of Visual Studio
gyp ERR! find VS including the "Desktop development with C++" workload.
```

### Install Chain (3 steps, must be in order)

**Step 1: VS Build Tools**
```bash
winget install Microsoft.VisualStudio.2022.BuildTools --override "--add Microsoft.VisualStudio.Workload.VCTools --includeRecommended --passive"
```

**Step 2: VC++ Toolset + Windows SDK** (if Step 1 alone doesn't work)
```bash
# Kill any running VS Installer first
taskkill /F /IM setup.exe 2>/dev/null

# Add VC++ tools + Windows SDK via VS Installer
"C:\Program Files (x86)\Microsoft Visual Studio\Installer\setup.exe" modify --installPath "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools" --add Microsoft.VisualStudio.Component.VC.Tools.x86.x64 --add Microsoft.VisualStudio.Component.Windows11SDK.22621 --quiet --norestart
```

**Step 3: Rebuild native packages**
```bash
cd /e/your-project
pnpm rebuild better-sqlite3
# or rebuild all native deps:
pnpm rebuild
```

### Pitfalls
- **VS Installer singleton lock:** Only one instance of `setup.exe` can run at a time. If you see "Another instance is running", kill all `setup.exe` processes first.
- **`--wait` is not a valid flag** for VS Installer CLI (causes exit code 87). Use `--quiet` or `--passive` instead.
- **`prebuild-install` timeout in China:** Even with npm mirror set, `prebuild-install` downloads prebuilt binaries from GitHub releases (not npm registry). These often timeout behind GFW. The `node-gyp` fallback is the reliable path — just needs VS Build Tools.
- **Better approach: install on NTFS drive.** Native module compilation is faster and more reliable on NTFS. exFAT drives also have symlink issues (see above).

## Pitfalls

- **`HF_ENDPOINT` only works for `huggingface_hub` library — not for `requests.get()` or `urlretrieve()` calls to hardcoded URLs.** Always search for hardcoded `huggingface.co` strings.
- **Gated repos still require auth even with mirror.** If a model (e.g. FLUX.2-dev) is gated, `hf-mirror.com` returns 403. The mirror proxies the API but cannot bypass access control. Workaround: load weights manually with `safetensors.torch.load_file()` and use a public repo's config (see `local-ai-gui` skill Strategy 3).
- **`from_single_file()` ignores `HF_ENDPOINT` for config downloads in some diffusers versions.** It may construct URLs internally. If configs fail to download, fall back to `from_pretrained()` with a public repo + manual weight loading.
- **`hf-mirror.com` is community-maintained** — it lags behind the official hub. Rare or recently-updated repos may be missing.
- **Setting `HF_ENDPOINT` mid-session won't affect already-imported modules.** The environment variable must be set before Python starts, or before `huggingface_hub` is imported.
- **On Windows, environment variables set via `set` in a .bat file are inherited by child processes** (CMD → Node.js → Python). This is the right place to set `HF_ENDPOINT` for app launchers.
- **Model files in custom directories (e.g., `ckpts/`) are NOT in the HF cache** (`~/.cache/huggingface/hub/`). `hf_hub_download()` won't find them there. The app's own file locator (checking `ckpts/`) runs first and only falls back to download if the file is missing entirely.
- **Some apps iterate ALL `URLs` in a model profile, not just the one matching the current quantization setting.** If any URL in the list points to a file not present locally, the download attempt will fail.
- **MSYS bash (Git Bash on Windows) requires `//` to escape `/` flags for native Windows tools.** When killing stuck `npm install` / `pnpm install` processes, `taskkill /F /IM node.exe` fails with "无效选项 /F:/" because MSYS rewrites `/F` as a path. Use `taskkill //F //IM node.exe` (double slash). Affects: `taskkill`, `icacls`, `reg`, and other CMD-native tools invoked from Git Bash.
