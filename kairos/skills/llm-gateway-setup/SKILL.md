---
name: "llm-gateway-setup"
description: "|"
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/.archive/llm-gateway-setup/SKILL.md"
---
# LLM Gateway / Aggregator Setup

Every gateway in this class follows the same arc. They differ in default
port, install method, and config UI — not in shape.

## Universal flow

1. **Verify available installers** for the user's OS via the authoritative
   source (GitHub Releases API for hosted projects, npm registry for npm
   packages). Do NOT trust partial file lists the user pastes — release
   pages often have more assets than the user shows.
2. **Pick the right package** for the user's OS:
   - Windows: `.exe` (NSIS installer) or portable `.exe`; rarely `.msi`
   - macOS: `.dmg` (Intel vs arm64 are separate)
   - Linux: `.AppImage`, `.deb`, or `.rpm` (often separate x64 / arm64)
3. **Install and launch** — distinguish three install shapes:
   - **Electron desktop wrapper** (OmniRoute): double-click .exe, system
     tray icon, browser auto-opens to dashboard. No terminal needed.
   - **Headless server** (LiteLLM proxy, mimocode2api): runs in a terminal,
     exposes HTTP, no GUI. User must keep the terminal open.
   - **npm CLI** (`npm i -g omniroute`): no GUI by itself, but usually
     starts a bundled web dashboard on first run.
4. **Configure**: set port if non-default, add providers (API keys / OAuth),
   create an API key for downstream tools.
5. **Wire into coding CLI**: point base URL at `http://localhost:PORT/v1`
   with the API key, OR use the gateway's auto-config command
   (`omniroute setup-claude`, `setup-codex`, etc.).

## Pitfalls

### Don't claim a platform isn't supported based on a partial file list

If the user pastes a file list and asks "which one is for Win11?", do NOT
conclude "none of these are for Windows" without checking the release
page. GitHub releases often ship Windows .exe alongside Linux/Mac assets.

Verify with the GitHub API before answering:

    curl -sL "https://api.github.com/repos/OWNER/REPO/releases/tags/vX.Y.Z" \
      | python -c "import sys,json; d=json.load(sys.stdin); \
        [print(a['name'], a.get('size',0)//1024//1024, 'MB', a['browser_download_url']) \
         for a in d.get('assets',[])]"

If the release genuinely lacks a platform build, say so WITH evidence
("v3.8.49 has 14 assets, none are .exe — verified via GitHub API"), not
from inference. This user has been bitten by premature "doesn't exist"
claims before.

### Give evidence, not "yes/no"

This user is direct and verifies claims. When asked "did it install?",
"which file?", "what port?", answer with concrete tool output: file path
from `ls` / `Get-Item`, process from `tasklist` / `Get-Process`, listening
port from `netstat -ano | findstr :PORT` / `ss -ltnp`. Never "yes" or "no"
without showing the command that confirmed it.

### Default ports differ per tool — verify, don't assume

- OmniRoute: 20128 (API + dashboard same port by default)
- mimocode2api: 8788 (current user setup; older configs used 10001+10000)
- LiteLLM proxy: 4000
- OpenRouter: hosted, no local port

Check the tool's own `--help` or SETUP_GUIDE for the canonical port before
telling the user "open localhost:XXXX".

### Electron desktop wrapper ≠ headless server

If the .exe is an Electron wrapper, the user does NOT need to keep a
terminal open. Closing the terminal does not stop the server — quit via
the tray icon. If it's a CLI binary, give terminal-based launch
instructions and warn that closing the terminal stops the server.

### Don't pile on npm/pnpm/Docker variants when user just wants to launch

The user has installed the .exe / .dmg / .AppImage. They want to know
"how do I start it", not "here are 6 install methods". Show the
**launched-the-installer** path first. Mention npm/Docker only as escape
hatches for advanced config (custom port, MCP server, scripting).

### Investigate unfamiliar tool names BEFORE pattern-matching

The user once asked "how to use ChatGPT(beta) with minimax-M3" and the
agent immediately replied with a generic OpenAI-compatible-client config
(base URL + API key + model name) without checking what "ChatGPT(beta)"
actually was on this machine. It turned out to be the **OpenAI Codex
Beta Microsoft Store app** (`OpenAI.CodexBeta_*.exe`), which uses
`~/.codex/config.toml` with a different schema than a generic client —
the answer should have been a Codex config snippet, not generic
instructions. The user called this out: "你的所有设定流程似乎跟
chatgpt(beta)没有任何关系".

**Rule**: when a tool name has a version suffix in parentheses
(`(beta)`, `(preview)`, `(pro)`, etc.) OR sounds like a familiar
product from a different vendor, treat it as a hint to look at the
actual install on disk first:

  - Desktop shortcut on Desktop / Start Menu → resolve `.lnk` to find
    the real target (UTF-16LE strings inside the .lnk binary give the
    AUMID or exe path on Windows)
  - `%LOCALAPPDATA%\Programs\` and `%LOCALAPPDATA%\<Vendor>\` for
    per-user installs
  - `tasklist` / `Get-Process` for what's actually running
  - Vendor's known config paths (`~/.codex/`, `~/.claude/`,
    `~/.cursor/`, etc.)

Only then answer. Generic "use the OpenAI SDK with base_url=..." is
the right answer for ChatGPT-Next-Web / LobeChat / ChatBox; it's
**wrong** for the Codex desktop Store app, which has its own config
file and doesn't expose Base URL / API Key fields in the UI.

## Per-tool reference

- `references/omniroute-windows.md` — OmniRoute on Windows (Electron
  desktop wrapper, port 20128, install location, tray behavior, log paths,
  provider + endpoint setup, CLI integration via auto-config commands,
  built-in provider catalog with MiniMax specifics, OpenAI→Anthropic
  upstream translation behavior, per-CLI config snippets for Codex /
  Claude Code / generic OpenAI-compatible clients, fresh-install
  diagnostic checklist, plus the `omniroute nodes add` global-vs-
  subcommand `--base-url` flag-collision bug and the direct-API /
  direct-DB fallback recipes for when the CLI is unusable)
- `references/omniroute-quirks.md` — Short companion to
  `omniroute-windows.md` covering the `RATE_LIMIT_MAX_WAIT_MS=15000`
  silent-drop streaming bug, the `provider/model` prefix requirement
  to avoid "ambiguous model" 404s, and the Codex config TOML quirks
  (`wire_api = "responses"`, `requires_openai_auth = false`).
- `references/codex-regression-debug.md` — When Codex Desktop "shows error immediately on connect" while OmniRoute appears healthy. Time-anchored diagnostic ladder (ping / uptime / app.log / Codex JSONL rollout) plus three known patterns: tool_use schema-validation 400 (session accumulation), auto-review codex-OAuth 404 (sub-agent missing config), clientApiPolicy warn (cosmetic). Companion to `references/omniroute-windows.md` which covers install + wiring.
- `references/msix-loopback.md` — Windows MSIX AppContainer loopback
  exemption for ChatGPT / Codex desktop Store apps (symptom: terminal
  curl works but the Store app can't reach `127.0.0.1:<port>`; fix via
  `CheckNetIsolation.exe LoopbackExempt -a -n=<PackageFamilyName>`,
  admin-elevated, requires app close+reopen).
- `scripts/setup-omni-provider.sh` — runnable replacement for the
  broken `omniroute nodes add` flow. Computes the auto machine token,
  POSTs to `/api/provider-nodes` with `x-omniroute-cli-token`, attaches
  the API key via either `/api/providers` or direct SQLite insert into
  `provider_connections.api_key`, and pings `/v1/chat/completions`
  to confirm end-to-end. Use this whenever a fresh provider needs to be
  added without going through the dashboard wizard.
- `templates/codex-desktop-omniroute.toml` — Codex desktop `config.toml`
  preset with full comments, for OpenAI Codex Beta MSIX app + OmniRoute
  on Windows (drops in as `~/.codex/config.toml`, edit model + key).
- `templates/codex-model-providers.toml` — Bare `model_providers`
  block template for Codex desktop (smaller than
  `codex-desktop-omniroute.toml`; use when you only need the
  provider section, not the full config).

## Companion patterns

When the gateway needs to be configured for non-trivial providers
(Cloudflare Workers AI, custom OAuth, regional endpoints), the
`cloudflare-deployment` skill covers Workers + D1 + KV/R2 patterns.
For the user-specific Hermes "mimo" provider pointing at mimocode2api,
see the user profile memory entry — that setup is already live and
working, do not rebuild.

When the user's downstream client speaks **OpenAI Chat Completions**
but the target provider only accepts **Anthropic-format** requests
(Claude, MiniMax, GLM-via-Anthropic, etc.), OmniRoute handles the
translation transparently at `/v1` — never tell the user "OpenAI
clients can't reach Anthropic providers" without checking whether a
gateway is in the path first. See the "OpenAI-format in, provider-
native format out" section in `references/omniroute-windows.md`.
