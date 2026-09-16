---
name: "ai-client-model-injection"
description: ">-"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\you\\AppData\\Local\\hermes\\skills\\.archive\\ai-client-model-injection\\SKILL.md"
---
# AI Client Model Injection

Class: **redirect a closed AI client's hard-coded API endpoint to a local proxy that serves your chosen model**, while keeping the native client UI / OAuth / tool loops intact. The user does NOT want to abandon their existing client — they want it to talk to a different upstream.

## When to load this skill

- "把 ChatGPT 桌面 app 改成用 X 模型" / "I want model X in the ChatGPT desktop app"
- "让 Cursor / Claude Code / Codex 用我的本地模型"
- "用 OpenAI 账号登录但调 DeepSeek / MiniMax / GLM"
- "客户端是闭源的，没法改源码，想换 provider"
- User keeps their ChatGPT subscription / OAuth but wants to test a different model without paying for a second client
- User asks for a tool that "doesn't require patching the app"

## When NOT to use

- User is fine with **switching to a different client entirely** (Cherry Studio, ChatBox, Open WebUI) — just install the new client, don't bother with proxy redirection
- User wants to **train or fine-tune** models — different class (MLOps)
- User wants a **central key vault** for many providers but uses clients that already accept custom base URLs — that's plain `ai-gateway`, no injection needed
- The "client" is just a curl/HTTP call you control — point it at the new URL directly

## The core technique: openai_base_url redirection

Many closed AI clients ship a config key that overrides their hard-coded API endpoint:

| Client | Config file / flag | Field |
|---|---|---|
| ChatGPT desktop app / Codex (post-July 2026 merge) | `~/.codex/config.toml` | `openai_base_url = "http://127.0.0.1:10110/<token>/v1"` |
| Codex CLI | same file | same field |
| Claude Code | settings or `--api-base` flag | `apiBase` / env `ANTHROPIC_BASE_URL` |
| Cursor | Cursor settings → Models → "OpenAI API Base URL" override | per-model override |
| Cline / Continue.dev | `cline_settings.json` / `config.json` | `apiBase` / `baseUrl` |

Set it to a local proxy URL, run the proxy, the client transparently calls your upstream instead.

**Why this works**: the client thinks it's talking to its own vendor; the proxy impersonates the vendor's API surface (or close enough) and routes to the real upstream.

## Reference implementations

### OmniRoute — the production answer (38k stars, 2026-08-03)

**github.com/diegosouzapw/OmniRoute** — MIT, TypeScript, Electron + Docker + npm.

What it gives you:
- **One local endpoint**, 290+ providers wired in (Kimi, Claude, GPT, OpenAI, Gemini, GLM, **DeepSeek, MiniMax**, plus 90+ free tiers)
- Native compatibility with **Codex / ChatGPT desktop / Claude Code / Cursor / OpenCode / Cline / Copilot** — confirmed in README
- Quota-aware auto-fallback (use next provider when one runs out)
- Token compression (RTK + Caveman, 15-95% savings)
- Built-in admin dashboard + Electron desktop wrapper

When to use: any time the user wants "X model in Y client" with minimal setup. It's the easy button.

Configuration:
1. Install: `npm i -g omniroute` or grab the Electron release
2. Add MiniMax as provider in dashboard
3. Point ChatGPT desktop `openai_base_url` at OmniRoute's local URL
4. The model picker shows MiniMax alongside native options

### DSCodex — the focused pattern (6 stars, 2026-08-03)

**github.com/fish2lab/DSCodex** — MIT, Node.js, zero deps, ~40KB.

What it gives you:
- Local router on `http://127.0.0.1:10110/<token>/v1` (256-bit token in `openai_base_url`)
- Routes **by model name**: V4 Flash → DeepSeek Responses API; everything else → chatgpt.com OAuth passthrough
- Injects the third-party model into Codex's native model menu via `model_catalog_json`
- macOS / Linux / **Windows native** (CLI + IDE; app-server bridge is macOS-only — Windows skips it without losing functionality)
- Vision fallback: V4 Flash can't see images → router borrows GPT-OAuth to describe them, caches by sha256, injects as text

Architecture (read this before forking):

```
Codex App / CLI / IDE
        │  HTTP/SSE  (zstd-compressed request, OAuth headers)
        ▼
http://127.0.0.1:10110/<router-token>/v1     ← DSCodex local router
        │
        ├── model == "deepseek/deepseek-v4-flash"
        │       ▼
        │   https://api.deepseek.com/responses   (DeepSeek native SSE)
        │
        └── any other model (gpt-5.6-*, codex-auto-review, ...)
                ▼
        https://chatgpt.com/backend-api/codex   (OAuth passthrough, unmodified)
```

Source layout (read these three files to understand the technique):
- `src/constants.mjs` — endpoint URL + wire-model constants (the only file you change to fork for a new provider)
- `src/proxy.mjs` — the routing logic, header forwarding, decompression
- `src/catalog.mjs` — model menu injection via `model_catalog_json`

**Why it's worth knowing even if you use OmniRoute**: it's the simplest possible reference for the technique. If OmniRoute is too heavy or doesn't support a niche provider, DSCodex is the fork-from template.

## Architecture choice

```
                        ┌────────────────────────────┐
                        │  Closed client (Codex etc) │
                        │  openai_base_url = 127.0.0.1│
                        └─────────────┬──────────────┘
                                      │
                         ┌────────────▼─────────────┐
                         │   Local proxy/router      │
                         │   - token auth            │
                         │   - model-name routing    │
                         │   - protocol translation  │
                         └─┬───────────────┬────────┘
                           │               │
              ┌────────────▼──┐    ┌───────▼──────────┐
              │ Third-party   │    │ Native vendor    │
              │ upstream      │    │ (chatgpt.com etc)│
              │ (MiniMax etc) │    │ OAuth passthrough│
              └───────────────┘    └──────────────────┘
```

## Protocol selection: Responses API vs Chat Completions

This is the #1 pitfall. Before writing or picking a proxy, **check what protocol the closed client uses**:

| Client | Protocol it speaks to upstream |
|---|---|
| ChatGPT desktop app / Codex (post-merge) | **OpenAI Responses API** (`/responses`, SSE, tool-call loops) |
| Codex CLI / IDE | Responses API |
| Claude Code | Anthropic Messages API (`/v1/messages`, `x-api-key` + `anthropic-version` headers) |
| Cursor | Chat Completions (`/v1/chat/completions`) — older clients; newer agents use Responses |
| Cline / Continue.dev | Chat Completions |
| Generic OpenAI SDK | Chat Completions |

What the upstream provider actually exposes:

| Provider | Chat Completions | Responses API | Notes |
|---|---|---|---|
| OpenAI | ✓ | ✓ | Both work |
| xAI Grok | ✓ | partial | Mostly Chat Completions |
| DeepSeek | ✓ | ✓ | Responses API supports full agentic tools |
| MiniMax (minimaxi.com / minimax.io) | ✓ | ✗ (as of 2026-08) | **Chat Completions only** — wire DSCodex-style fork needs adaptation |
| Anthropic | n/a | n/a | Native Messages API, not OpenAI-shaped |
| Google Gemini | ✓ (newer) | ✗ | Older clients use `:generateContent` |

**If the client uses Responses API but your provider only has Chat Completions**: you need a protocol translator layer, not a passthrough. Either pick a provider that has Responses (DeepSeek, OpenAI), or pick a client that uses Chat Completions (Cline, plain OpenAI SDK calls).

## Provider endpoint catalog (as of 2026-08)

Reference for common upstreams. Model name is **strict** in most APIs — copy-paste, don't paraphrase.

| Provider | baseUrl | Model field | Auth | Notes |
|---|---|---|---|---|
| MiniMax (CN) | `https://api.minimaxi.com/v1` | `MiniMax-M3` | Bearer API key | `platform.minimaxi.com` → 账户管理 → 接口密钥 |
| MiniMax (intl) | `https://api.minimax.io/v1` | `MiniMax-M3` | Bearer API key | Same model name |
| DeepSeek | `https://api.deepseek.com` | `deepseek-v4-flash` (or `deepseek-chat` for V3) | Bearer API key | Responses API at `/responses` |
| OpenAI | `https://api.openai.com/v1` | `gpt-5`, `gpt-5-mini`, `o3`, etc. | Bearer | Both protocols |
| Anthropic | `https://api.anthropic.com` | `claude-sonnet-4-5`, etc. | `x-api-key` + `anthropic-version: 2023-06-01` | Native Messages API |
| Gemini (OpenAI-compatible) | `https://generativelanguage.googleapis.com/v1beta/openai` | `gemini-2.5-pro`, etc. | Bearer | OpenAI-compatible shim |
| Doubao / Volcengine Ark | `https://ark.cn-beijing.volces.com/api/v3` | `doubao-seedream-3-0-t2i-250415` etc. | Bearer | `/api/v3` suffix required |
| GLM (Z.AI) | `https://api.z.ai/api/paas/v4` | `glm-4.6`, `glm-4.5` | Bearer | |
| Kimi (Moonshot) | `https://api.moonshot.cn/v1` | `kimi-k2-0711-preview` etc. | Bearer | |
| Qwen (DashScope, OpenAI-compat) | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-max`, `qwen-plus` | Bearer | |

Get the latest entries by searching `api.<provider>.com` or `<provider> openai compatible api base_url` — endpoint URLs change without notice.

## Configuration recipes

### MiniMax-M3 into ChatGPT desktop app (Windows, via OmniRoute)

1. Install OmniRoute (npm global or Docker or Electron)
2. In OmniRoute dashboard, add a provider:
   - baseUrl: `https://api.minimaxi.com/v1`
   - apiKey: `<your MiniMax key>`
   - model: `MiniMax-M3`
3. Note OmniRoute's local URL (default `http://127.0.0.1:<port>/v1`)
4. In `%USERPROFILE%\.codex\config.toml`, add or set:
   ```toml
   openai_base_url = "http://127.0.0.1:<port>/v1"
   ```
5. Restart ChatGPT desktop app — model picker shows MiniMax-M3 alongside native OpenAI models

If `config.toml` already has its own `openai_base_url` and you don't want to overwrite, OmniRoute's `install` step will refuse and tell you — back up first.

### MiniMax-M3 into a plain OpenAI-SDK script (no injection needed)

If "the client" is a script you control, skip the whole proxy dance. Just point at MiniMax directly:

```python
from openai import OpenAI
client = OpenAI(
    api_key="<MiniMax key>",
    base_url="https://api.minimaxi.com/v1",
)
resp = client.chat.completions.create(
    model="MiniMax-M3",
    messages=[{"role": "user", "content": "..."}],
)
```

Only need the proxy when the client is closed and won't accept a `base_url` parameter.

## Pitfalls

### Wrong protocol (Responses vs Chat Completions)

If you point a Codex client at a provider that only speaks Chat Completions, you'll see cryptic errors like `404 Not Found` on `/v1/responses` or tool loops that never start. Either pick a provider with Responses (DeepSeek, OpenAI) or pick a client that uses Chat Completions (Cline, plain SDK).

### Model name strictness

Most providers (especially MiniMax, DeepSeek, Volcengine) reject model names that don't exactly match. `minimax-m3` vs `MiniMax-M3` vs `MiniMax/M3` — wrong case or wrong separator = silent 404. Copy from the provider's docs, don't guess.

### Token leakage via openai_base_url

`openai_base_url` is stored in plaintext on disk. If you set it to a third-party endpoint, your client might still send OAuth / session headers in some calls — make sure the proxy strips them before forwarding to the third-party provider. DSCodex does this with an explicit allowlist (`FORWARDED_REQUEST_HEADERS` set in `proxy.mjs`).

### Overwriting user's existing openai_base_url

Many users already have a custom `openai_base_url` set (e.g., to a different proxy). Before `install` overwrites it, back it up. Both OmniRoute and DSCodex auto-backup to `config.toml.pre-<tool>.bak`.

### Windows-specific: app-server bridge is macOS-only

DSCodex's "model menu state persistence" bridge uses `CODEX_CLI_PATH` shim that doesn't work on Windows (CreateProcess needs `.exe`, not `.sh`). Windows gets all features except this one — README says it's auto-disabled and `doctor` reports `ok`. Don't try to debug the Windows shim; it isn't supposed to work there.

### Free-tier providers' model lists change weekly

If you wire OmniRoute or any gateway to "free" providers (SiliconFlow, GLM Flash, OpenCode Zen, etc.), expect the model name to drift. Pin the version or add a watchdog check that fails fast if a model 404s.

### Some clients ignore base URL override

Cursor's "OpenAI API Base URL" override is per-model and may not propagate to all surfaces (chat panel vs composer). Claude Code's `ANTHROPIC_BASE_URL` env only takes effect if set before launch. Test the specific surface you care about before assuming it works.

## Verification

After configuring, prove it actually works — don't trust "I changed the config":

1. **Pick a probe prompt** that requires a tool call (e.g., "list files in current directory") — proves the protocol and tool loop work, not just chat
2. **Check the proxy logs** — you should see the request come in and the upstream response go out
3. **For OAuth-passthrough**: confirm the native models still work after the override (the proxy must not break the OAuth path, just add a new model)
4. **For injected providers**: ask the model something it shouldn't know (e.g., "what's today's date") and verify it answers from the new upstream, not from cached native responses

If using DSCodex: `node src/cli.mjs doctor` runs six checks (route token, app-server bridge, etc.) — all must report `ok`.

## Files to create

When setting up a custom injection project:

| File | Lines | Purpose |
|---|---|---|
| `proxy.mjs` | ~200 | HTTP listener, header forwarding, decompression, routing logic |
| `constants.mjs` | ~50 | Provider endpoint + model name constants |
| `catalog.mjs` | ~100 | `model_catalog_json` builder for injecting into Codex menu |
| `config.toml` snippet | ~5 | The actual `openai_base_url` line + provider entries |

References for the actual implementations:
- **`references/provider-endpoints.md`** — extended endpoint catalog with regional variants, free-tier limits, and gotchas per provider
- **`references/responses-vs-chat-completions.md`** — deep dive on protocol differences, request body shapes, SSE event names, tool-call format

## Bundled resources

- **`references/responses-vs-chat-completions.md`** — protocol deep dive: request shapes, SSE event names, provider matrix, Responses↔Chat-Completions translation pseudocode. **Read this first** if the closed client and upstream provider don't share a protocol.

## Related skills

- `ai-gateway` — for building a FastAPI **management backend** for many providers; complementary when the user needs both proxy + admin UI
- `cloudflare-deployment` — for hosting the proxy on Cloudflare Workers instead of local; not a class fit but same shape
- `mcporter` — for MCP-style tool routing, different protocol layer
- `xiaomi-mimo-tts` — MiniMax sibling provider with its own quirks (chat-completion-format audio synthesis, not `/v1/audio/speech`)
- `agent-reach` — when the injection target is a web-scraping / external-API agent rather than a chat client

## End of Skill