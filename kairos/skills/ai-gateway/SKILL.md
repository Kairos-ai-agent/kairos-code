---
name: "ai-gateway"
description: ">-"
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/ai-gateway/SKILL.md"
---
# AI Model Gateway

Class: **local unified AI model management backend** — a single FastAPI service (or Flask) that wraps multiple AI providers behind one HTTP API. The user adds/configures providers in a web admin UI; client apps call the gateway's proxy endpoints and never deal with vendor-specific APIs.

## When to load this skill

- "做一个后台服务，使得自己能够设置各种模型" / "build a backend so I can configure various AI models"
- User has multiple AI providers (OpenAI, Claude, Grok, MiMo, Doubao, etc.) and wants ONE URL per model type instead of one URL per vendor
- Existing canvas / workflow / test app calls `cfg.llm.url`, `cfg.img.url`, `cfg.vid.url`, `cfg.tts.url` directly — wants a unified place to swap providers
- "Add a config UI for my AI scripts" / "I want to test which model is best without changing 20 files"
- "前端只关心一个地址，后端路由到对应服务商"

## When NOT to use

- User only has one AI provider and no plans to add more → don't add the gateway layer, call the API directly.
- User wants to **train** models (not invoke them) → different class (MLOps).
- User wants per-user API key management with auth/quotas → needs a real backend with database and auth, not the JSON-file pattern below.
- Distributed multi-user production → needs proper DB, secret manager, rate limiting, etc. This skill covers the **single-user local** case (the "I run it on my own machine" case).

## Architecture

```
┌─────────────────┐    ┌──────────────────────────────────────┐
│  Client App     │───▶│  AI Gateway (this skill)              │
│  (Kairos, web,  │    │  FastAPI single-file server          │
│   scripts, etc) │    │                                      │
└─────────────────┘    │  /admin  → admin.html (CRUD UI)      │
                       │  /api/models → list/create/update    │
                       │  /api/{type}/{id}/{action} → proxy   │
                       │                                      │
                       │  config.json ← all models + keys    │
                       └──────────────┬───────────────────────┘
                                      │ transparent proxy
              ┌───────────────────────┼─────────────────────────┐
              ▼                       ▼                         ▼
       OpenAI /v1/chat         Doubao /v1/images         Edge TTS (direct)
       Claude /v1/messages     Grok /v1/images          MiMo TTS
       Gemini /v1/chat         Seedance /v1/video       ...
```

**Three files are enough for a working gateway:**

| File | Purpose | Approx size |
|------|---------|-------------|
| `server.py` | FastAPI: admin API + proxy endpoints + persistence | ~700 lines |
| `admin.html` | Single-file SPA: list / add / edit / test / delete models | ~700 lines |
| `config.json` | Models persistence (created on first run) | grows with use |
| `start.bat` | Optional Windows launcher with auto-install | 30 lines |

## Core implementation patterns

### 1. Data model — single models array in config.json

```python
DEFAULT_CONFIG = {
    "version": 1,
    "models": [
        {
            "id": "preset-openai-gpt4o",     # unique, used in URL path
            "name": "OpenAI · GPT-4o",        # display name
            "type": "llm",                     # llm | image | video | tts
            "enabled": True,
            "baseUrl": "https://api.openai.com",
            "apiKey": "sk-...",
            "model": "gpt-4o",
            "extra": {"temperature": 0.7, "maxTokens": 4096},
            "preset": "openai",                 # which preset was used to create this
            "note": "OpenAI 官方 — 最通用 LLM"
        },
        # ... more
    ]
}

# Lock for concurrent writes (FastAPI is async, use asyncio.Lock)
CONFIG_LOCK = asyncio.Lock()

async def load_config():
    if not CONFIG_PATH.exists():
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)

async def save_config(cfg):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
```

**Why JSON file not SQLite:** the user runs this locally, on one machine, with no concurrent writers. JSON is human-readable, easy to inspect, easy to back up. SQLite adds machinery for a problem that doesn't exist here.

**Why no auth:** local single-user. If you need to expose it on a LAN, add basic auth middleware (see `references/lan-auth.md` for a snippet).

### 2. Pydantic model for input validation

```python
from pydantic import BaseModel, Field

class ModelIn(BaseModel):
    id: Optional[str] = None    # auto-generated if missing
    name: str
    type: str = Field(..., pattern="^(llm|image|video|tts)$")
    enabled: bool = True
    baseUrl: str = ""
    apiKey: str = ""
    model: str = ""
    extra: dict = {}
    preset: str = ""
    note: str = ""
```

Pattern validates `type` against the four allowed values. Anything else returns 422 with a clear error.

### 3. CRUD endpoints — always async-lock-wrapped

```python
@app.post("/api/models")
async def create_model(body: ModelIn):
    if body.type not in {"llm", "image", "video", "tts"}:
        raise HTTPException(400, f"type 必须是 {{llm, image, video, tts}}")
    
    mid = body.id or f"custom-{body.type}-{uuid.uuid4().hex[:8]}"
    if any(m["id"] == mid for m in CONFIG["models"]):
        raise HTTPException(400, f"ID 已存在: {mid}")
    
    new_model = body.model_dump()
    new_model["id"] = mid
    new_model["createdAt"] = datetime.now().isoformat()
    
    async with CONFIG_LOCK:
        CONFIG["models"].append(new_model)
        save_config(CONFIG)
    
    return {"ok": True, "model": new_model}

@app.put("/api/models/{model_id}")
async def update_model(model_id: str, body: ModelIn):
    async with CONFIG_LOCK:
        for i, m in enumerate(CONFIG["models"]):
            if m["id"] == model_id:
                updated = body.model_dump()
                updated["id"] = model_id
                updated["createdAt"] = m.get("createdAt")
                updated["updatedAt"] = datetime.now().isoformat()
                CONFIG["models"][i] = updated
                save_config(CONFIG)
                return {"ok": True, "model": updated}
    raise HTTPException(404, f"模型不存在: {model_id}")
```

The `async with CONFIG_LOCK` is critical — without it, two parallel admin requests can corrupt the file.

### 4. API Key masking in list responses

The full `apiKey` should NEVER be returned by `GET /api/models` (it's used in the browser UI — anyone with DevTools can read it). Mask it on the way out:

```python
@app.get("/api/models")
async def list_models(type: Optional[str] = None, enabled: Optional[bool] = None):
    items = CONFIG.get("models", [])
    if type: items = [m for m in items if m.get("type") == type]
    if enabled is not None: items = [m for m in items if m.get("enabled", True) == enabled]
    
    safe = []
    for m in items:
        m2 = dict(m)
        k = m2.pop("apiKey", "")
        if k and len(k) > 8:
            m2["apiKeyMasked"] = "***" + k[-4:]
        else:
            m2["apiKeyMasked"] = "***" if k else ""
        safe.append(m2)
    return {"models": safe, "total": len(safe)}
```

The original `apiKey` field stays in the dict for PUT/POST flows but is stripped from list responses.

### 5. Connection test endpoint — calls upstream /v1/models

```python
@app.post("/api/test/{model_id}", response_model=TestResult)
async def test_model(model_id: str):
    model = next((m for m in CONFIG["models"] if m["id"] == model_id), None)
    if not model:
        raise HTTPException(404, f"模型不存在: {model_id}")
    if not model.get("enabled"):
        return TestResult(ok=False, message="模型已禁用")
    
    base = (model.get("baseUrl") or "").rstrip("/")
    api_key = model.get("apiKey", "")
    
    start = time.time()
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            # Most OpenAI-compatible providers expose GET /v1/models
            headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
            r = await client.get(base + "/v1/models", headers=headers)
            latency = int((time.time() - start) * 1000)
            if r.status_code == 200:
                return TestResult(ok=True, message=f"✓ 连通正常 (HTTP {r.status_code})",
                                  latencyMs=latency, detail={"url": base + "/v1/models"})
            return TestResult(ok=False, message=f"✗ HTTP {r.status_code}: {r.text[:200]}", latencyMs=latency)
        except httpx.ConnectError as e:
            return TestResult(ok=False, message=f"✗ 连接失败: {e}")
        except httpx.TimeoutException:
            return TestResult(ok=False, message="✗ 连接超时 (15s)")
```

Catches `ConnectError` (DNS or TCP failure), `TimeoutException` (slow upstream), and HTTP errors separately so the admin UI can show a useful message.

### 6. Transparent proxy — one endpoint per type

The whole point of the gateway: the client app calls `/api/llm/{id}/chat` and the gateway translates to whatever upstream format the chosen provider wants.

```python
@app.post("/api/llm/{model_id}/chat")
async def proxy_llm(model_id: str, request: Request):
    model = next((m for m in CONFIG["models"]
                  if m["id"] == model_id and m["type"] == "llm"), None)
    if not model:
        raise HTTPException(404, f"LLM 模型不存在: {model_id}")
    if not model.get("enabled"):
        raise HTTPException(503, f"模型已禁用: {model_id}")
    
    body = await request.json()
    base = model["baseUrl"].rstrip("/")
    api_key = model["apiKey"]
    
    upstream_body = {
        "model": model["model"],
        "messages": body.get("messages", []),
        "temperature": body.get("temperature", model["extra"].get("temperature", 0.7)),
        "stream": False
    }
    if "max_tokens" in body:
        upstream_body["max_tokens"] = body["max_tokens"]
    elif "maxTokens" in model["extra"]:
        upstream_body["max_tokens"] = model["extra"]["maxTokens"]
    
    url = base + "/v1/chat/completions"
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {api_key}"}
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            r = await client.post(url, json=upstream_body, headers=headers)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as e:
            raise HTTPException(e.response.status_code,
                                f"上游 {e.response.status_code}: {e.response.text[:500]}")
```

**Key choices:**
- 120s timeout for LLM (slow reasoning models need it)
- 180s timeout for image (generation + download)
- 600s timeout for video (long-running)
- On upstream HTTP error: pass the status through, but truncate the body to 500 chars so the admin UI doesn't blow up
- Return `r.json()` directly — don't try to remap the response shape (most providers use OpenAI's shape already)

### 7. Edge TTS — return a special directive instead of proxying

Edge TTS is browser-side (Microsoft's free speech synthesis). The server can't proxy it usefully — just tell the client to call Edge TTS directly:

```python
if model.get("preset") == "edge":
    return {
        "type": "edge-direct",
        "voice": model["extra"].get("voice", "zh-CN-XiaoxiaoNeural"),
        "speed": model["extra"].get("speed", 1.0),
        "text": body.get("text", body.get("input", ""))
    }
```

The client checks `response.type === "edge-direct"` and switches to a frontend-only speech call.

### 8. Presets — one-click add from common providers

Users will want to add "OpenAI GPT-4o" without filling in the URL/model fields every time. Pre-define presets:

```python
PRESETS = {
    "openai": {"name": "OpenAI 通用", "type": "llm",
               "baseUrl": "https://api.openai.com", "model": "gpt-4o",
               "extra": {"temperature": 0.7, "maxTokens": 4096}},
    "claude": {"name": "Anthropic Claude", "type": "llm",
               "baseUrl": "https://api.anthropic.com", "model": "claude-sonnet-4-5",
               "extra": {"temperature": 0.7, "maxTokens": 8192}},
    "doubao": {"name": "豆包 Seedream", "type": "image",
               "baseUrl": "https://ark.cn-beijing.volces.com/api/v3",
               "model": "doubao-seedream-3-0-t2i-250415",
               "extra": {"size": "1280x720"}},
    "edge": {"name": "Edge TTS", "type": "tts", "baseUrl": "", "model": "edge-tts",
             "extra": {"voice": "zh-CN-XiaoxiaoNeural", "speed": 1.0}},
    # ... 10+ more
}

@app.post("/api/presets/{preset_id}/add")
async def add_preset(preset_id: str, body: dict = {}):
    if preset_id not in PRESETS:
        raise HTTPException(404, f"预设不存在: {preset_id}")
    p = dict(PRESETS[preset_id])
    api_key = body.get("apiKey", "")
    name = body.get("name", p["name"])
    new_model = {
        "id": f"preset-{preset_id}-{uuid.uuid4().hex[:6]}",
        "name": name, "type": p["type"], "enabled": True,
        "baseUrl": p["baseUrl"], "apiKey": api_key,
        "model": p["model"], "extra": p.get("extra", {}),
        "preset": preset_id, "note": p.get("note", ""),
        "createdAt": datetime.now().isoformat()
    }
    async with CONFIG_LOCK:
        CONFIG["models"].append(new_model)
        save_config(CONFIG)
    return {"ok": True, "model": new_model}
```

Aim for **at least 10 presets** covering the user's main providers — anything less and the admin UI feels empty.

## Admin UI patterns

### Layout — three-column responsive

```
┌────────────────────────────────────────────────┐
│ Header: title, primary action button           │
├──────────┬─────────────────────────────────────┤
│ Sidebar  │ Main area:                          │
│ - tabs   │   - 4 stat cards (counts per type)  │
│ - filter │   - model grid (cards)              │
│          │   - or doc panel for "接入文档" tab  │
└──────────┴─────────────────────────────────────┘
```

Dark theme (`#0d1117` background, `#e6edf3` text) matches dev-tool conventions and shows code-like prompts better than light themes.

### Model card — actions inline

Each card shows: type icon, name, preset tag, id, baseUrl, model, masked key, note. Buttons row at bottom: 测试连接 | 复制URL | 编辑 | 复制 | 删除.

### Edit modal — flat form, no tabs

```html
<div class="modal">
  <input id="ed-name" placeholder="OpenAI · GPT-4o">
  <select id="ed-type"><option>llm</option>...</select>
  <input id="ed-baseUrl" placeholder="https://api.openai.com">
  <input id="ed-apiKey" type="password">  <!-- leave blank = keep existing -->
  <input id="ed-model" placeholder="gpt-4o">
  <textarea id="ed-extra">{}</textarea>   <!-- JSON for parameters -->
</div>
```

The "leave blank to keep existing" trick for API keys: when editing, send `apiKey: ""` if the field is blank, and the server only overwrites if the new value is non-empty. Otherwise updating would nuke the key.

### Toggle, duplicate — one-click UX

```html
<div class="toggle-switch on" data-id="..." data-action="toggle"></div>
```

Toggle just flips `enabled` field — no modal, no confirmation. Same for duplicate: hits `/api/models/{id}/duplicate` which appends a copy with `(副本)` suffix and new id.

### Stat cards — count enabled models per type

```javascript
document.getElementById('stat-llm').textContent =
    MODELS.filter(m => m.type==='llm' && m.enabled).length;
```

Count enabled (not just configured) — that matches what the client can actually call.

### Toast for feedback

```javascript
function toast(msg, type='info') {
  const wrap = document.getElementById('toast-wrap');
  const div = document.createElement('div');
  div.className = `toast ${type}`;
  div.textContent = msg;
  wrap.appendChild(div);
  setTimeout(() => div.remove(), 3500);
}
```

Always show success/error after every CRUD action — silent successes are confusing.

## Pitfalls

### Server timing out on slow providers

Default `httpx` timeout is 5s. LLM providers (especially Claude/Gemini reasoning) can take 30-60s. Use explicit timeouts:
- LLM: 120s
- Image: 180s  
- Video: 600s
- Connection test: 15s

`httpx.AsyncClient(timeout=N)` per endpoint, not global.

### Forgetting asyncio.Lock for config writes

Without `CONFIG_LOCK`, two parallel admin actions (e.g., user editing while another tab toggles) can race on `CONFIG["models"].append(...)` and one write wins, losing the other. Even though FastAPI is async, JSON file IO is sync — wrap every `save_config` in the lock.

### Returning full API key in list endpoints

If `GET /api/models` returns `{"apiKey": "sk-xxx..."}`, the key is visible in browser DevTools and in any logging/proxy layer. Always mask on list endpoints. The full key only flows through the proxy endpoint, which doesn't echo it back.

### Adding same preset twice

User clicks "Add preset openai" twice by accident → two models with the same preset but different ids. Not actually a bug, but consider deduplicating by `(preset, baseUrl, model)` if it becomes a problem.

### Edge TTS proxy doesn't work

Edge TTS runs in the user's browser via `speechSynthesis`, not via a network call to a server. If you try to proxy it, you'll fail. The pattern above (return `{"type": "edge-direct", ...}`) tells the client to use the browser API instead.

### Forgetting presets for non-OpenAI providers

Anthropic, Gemini, and some Chinese providers use slightly different URL paths or auth headers. Document each preset's quirks in its `note` field so the user knows what to expect. Test each preset's `/v1/models` GET at least once before shipping.

### Editing wipes the API key

When the user clicks "Edit" on an existing model, the form's API key field is empty (you didn't pre-fill it for security). If they save without re-entering it, the key gets overwritten with `""`. Either:
- Pre-fill with the masked version `***xxxx` and let them know
- Or: in the server's update handler, only overwrite `apiKey` if the new value is non-empty

The second option is cleaner — empty input means "keep what's there".

## Files to create

When starting a new gateway project, copy these files:

| File | Lines | Purpose | Where |
|------|-------|---------|-------|
| `server.py` | ~600 | FastAPI: admin API + 4 proxy endpoints | `templates/server.py` in this skill |
| `admin.html` | ~700 | Single-file SPA (CSS + JS inline) | Reference: `D:/AI_work/canvas/ai-gateway/admin.html` (copy & customize) |
| `start.bat` | ~30 | Windows launcher with auto-install | Use the pattern in existing `D:/AI_work/canvas/ai-gateway/start.bat` |
| `README.md` | ~80 | Document the API endpoints | Pattern in `D:/AI_work/canvas/ai-gateway/README.md` |

config.json is created on first run with DEFAULT_CONFIG.

See `templates/server.py` for a complete, runnable starting template with placeholder PRESETS. Reference the live `D:/AI_work/canvas/ai-gateway/server.py` (which has 15+ real presets) for the full feature set including:
- Edit modal with JSON extra params
- Duplicate model button
- Toggle enable/disable inline
- Toast notifications
- Pre-set defaults on first run

## Bundled resources

- **`templates/server.py`** — runnable FastAPI backend with placeholder PRESETS. Copy, edit DEFAULT_CONFIG and PRESETS, run.
- **`references/architecture.md`** — component diagram, request flow, concurrency model, failure modes. Read this before extending the gateway.
- **`references/kairos-canvas-integration.md`** — concrete integration recipe for replacing `cfg.llm.url` etc. in the Kairos Canvas HTML. Includes Edge TTS special case and LAN-deployment auth snippet.
- **`references/preset-catalog.md`** — 15+ preset templates covering OpenAI, Claude, Gemini, Grok, MiMo, DeepSeek, Qwen, Doubao Seedream/Seedance, Wanx, Kling, Edge TTS, MiMo TTS. Copy-paste into PRESETS dict.

## Extension points

When the user asks for new capabilities:

| User asks | Add |
|----------|-----|
| "I want to use it from another machine on LAN" | `--host 0.0.0.0` flag, add basic-auth middleware |
| "Add Claude/Gemini-specific request format" | Per-type upstream adapters — call OpenAI format by default, special-case Anthropic (`x-api-key` header, `anthropic-version` header, `/v1/messages` endpoint) |
| "I want version history / undo" | Move config to SQLite, add `version` field + audit log |
| "Multiple users with their own keys" | Add User model, scope configs to user_id |
| "TTS streaming" | Switch TTS proxy to `StreamingResponse` returning audio chunks |
| "I want to see request logs" | Add a simple in-memory ring buffer exposed at `/api/logs?tail=100` |
| "Webhook for completed video jobs" | Add a callback URL field per model, fire on async completion |

## Alternative architecture A: Dual routing (no frontend code change)

When the frontend already has its own URL/Key/Model inputs and you can't change the call site, support **two ways** to identify the model on the same handler:

- **URL path**: `POST /api/llm/{model_id}/chat`
- **Authorization header**: `Authorization: Bearer <model_id>` — frontend stuffs the model_id into the Bearer token

```python
def resolve_model_id(model_id, request, mtype) -> dict:
    mid = model_id
    if not mid:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            mid = auth[7:].strip()
    if not mid:
        available = [m["id"] for m in CONFIG["models"] if m["type"] == mtype]
        raise HTTPException(400, detail={
            "error": "缺少模型ID",
            "available_ids": available,
        })
    model = next((m for m in CONFIG["models"] if m["id"] == mid and m["type"] == mtype), None)
    if not model:
        available = [m["id"] for m in CONFIG["models"] if m["type"] == mtype and m.get("enabled")]
        raise HTTPException(404, detail={
            "error": f"模型不存在: {mid}",
            "available_ids": available,
        })
    return model

@app.post("/api/llm/chat")                  # static path FIRST
@app.post("/api/llm/{model_id}/chat")      # parameterized path SECOND
async def proxy_llm(request: Request, model_id: Optional[str] = None):
    model = resolve_model_id(model_id, request, "llm")
    # ... same proxy logic as above ...
```

**Why this works**: The frontend thinks it's calling any standard OpenAI-compatible endpoint. User fills the API Key field with a model_id from the gateway; the gateway extracts it from Bearer, looks up the model, and routes accordingly. Zero frontend code change.

### CRITICAL PITFALL — Static path MUST register before parameterized path

FastAPI matches multi-route decorators in declaration order. If `/api/llm/{model_id}/chat` registers first, a request to `POST /api/llm/chat` matches with `model_id="chat"` and fails with 404 — never reaching `/api/llm/chat`.

```python
# WRONG — "chat" gets captured as model_id
@app.post("/api/llm/{model_id}/chat")
@app.post("/api/llm/chat")

# RIGHT — static path first
@app.post("/api/llm/chat")
@app.post("/api/llm/{model_id}/chat")
```

Applies to all 4 types: llm/image/video/tts.

## Alternative architecture B: Minimal CORS proxy (no model management)

⚠️ **Diagnostic question before building the managed gateway**: does the user actually need model management, or do they just have a CORS problem?

| User signal | Real need | Use this skill's pattern |
|---|---|---|
| "Settings live in client UI", user mentions CORS / 跨域 | Just needs CORS bypass | **Architecture B** (below) — 100 lines, no config |
| "I want to manage models centrally", "all keys in one place" | Real gateway | Main pattern above |
| User says "URL/Key/Model 在前端配" / "frontend has settings" | Frontend already has config UI | **Architecture B** |
| User pushes back when you propose features they didn't ask for | Likely Architecture B | Simpler is better |

### The 100-line FastAPI CORS proxy template

No config file, no admin UI, no model list. Frontend fills `URL = http://127.0.0.1:5790/proxy/<full-upstream-url>` and the body is forwarded verbatim.

```python
import urllib.parse, httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse

app = FastAPI()

@app.api_route("/proxy/{target_path:path}",
               methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy(target_path: str, request: Request):
    target = urllib.parse.unquote(target_path)
    if request.method == "OPTIONS":
        return StreamingResponse(iter([]), status_code=204, headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, PATCH, OPTIONS",
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Max-Age": "86400",
        })
    skip = {"host", "content-length", "connection",
            "x-forwarded-for", "x-forwarded-proto", "x-forwarded-host"}
    fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in skip}
    body = await request.body()
    query = dict(request.query_params)
    timeout = httpx.Timeout(connect=10.0, read=600.0, write=600.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        try:
            r = await client.stream(method=request.method, url=target,
                                    headers=fwd_headers, params=query or None,
                                    content=body or None)
        except httpx.HTTPError as e:
            raise HTTPException(502, f"upstream: {e}")
        resp_headers = {k: v for k, v in r.headers.items()
                        if k.lower() not in {"content-encoding", "transfer-encoding", "connection"}}
        if "access-control-allow-origin" not in {k.lower() for k in resp_headers}:
            resp_headers["Access-Control-Allow-Origin"] = "*"
        return StreamingResponse(r.aiter_bytes(), status_code=r.status_code,
                                  headers=resp_headers)
```

Key points:
- `target_path:path` captures everything after `/proxy/`, including slashes
- `urllib.parse.unquote` recovers the `://` that browsers don't encode but httpx does
- Don't parse the body — pass it through
- Add `Access-Control-Allow-Origin: *` to responses if upstream didn't
- 600s read/write timeout for video/LLM calls

**When to escalate from proxy to managed gateway**: Only when the frontend has no URL/Key/Model UI (model config must live server-side), or user explicitly asks for central key rotation / usage accounting / rate limiting.

## Provider quirks (cross-vendor reference)

When adding a preset or debugging a "provider doesn't work" report, check these:

| Provider | Endpoint / quirk |
|----------|------------------|
| **OpenAI / xAI Grok** | OpenAI-compatible `/v1/chat/completions` and `/v1/images/generations` |
| **Anthropic Claude** | `/v1/messages` with `x-api-key` + `anthropic-version` headers — NOT OpenAI format |
| **Google Gemini** | `/v1/chat/completions` (newer) or `/v1beta/models/{model}:generateContent` (legacy) |
| **MiMo TTS** | Endpoint is `https://api.xiaomimimo.com/v1/chat/completions` (NOT `/v1/audio/speech`) — uses chat-completion format with `audio: {voice, format}` and `assistant` role for text-to-speak. See `references/xiaomi-mimo-tts/` under the `gen-api-integration` skill for full details. |
| **Edge TTS (Microsoft)** | Free, no API key. Return `{type: "edge-direct", voice, speed, text}` to let frontend call browser-native Web Speech API directly — DO NOT try to proxy it |
| **Doubao / Volcengine Ark** | Base URL has `/api/v3` suffix: `https://ark.cn-beijing.volces.com/api/v3` |
| **HuggingFace Inference** | `/models/{owner}/{model}` for inference, `/pipeline/tag/{tag}` for older API |
| **Replicate** | `POST /v1/predictions` (sync mode returns immediately; otherwise poll `/v1/predictions/{id}`) |

**Time per type** (always override httpx default of 30s):
- LLM: 120s
- Image: 180s
- Video: 600s
- TTS: 120s

## Related skills

- `frontend-patching` — for surgical edits to admin.html after the initial build
- `pyinstaller-desktop-packaging` — for distributing the gateway as a single .exe to non-technical users
- `api-design` — for REST endpoint conventions (status codes, error shapes, pagination)
- `flask-erp-development` — covers a similar single-file Flask backend pattern, applicable if the user wants Flask instead of FastAPI
- `gen-api-integration` — full integration knowledge for third-party gen APIs, incl. MiMo TTS (the 3 model variants + role convention), Agnes, MiniMax H3 under `references/`

## Absorbed sub-skills (consolidated umbrella)

This is the umbrella for the **unified AI model gateway / model-routing** class. The narrow siblings below were consolidated here in a prior pass; their full bodies live in `references/<name>/` for the depth you need on a specific approach:

- **Build your own gateway** — the core content above (FastAPI service + admin UI + proxy endpoints + provider presets).
- **Operate existing gateway tools** — OmniRoute, LiteLLM, OpenRouter self-host, Portkey, mimocode2api; routing coding CLIs (Claude Code, Codex, Cursor, Cline, OpenCode) across providers & free-tier pools; installer selection, launch, wiring, startup troubleshooting. → `references/llm-gateway-setup/`
- **Inject models into closed clients** — redirect a closed AI client's hard-coded `openai_base_url` to your proxy (ChatGPT desktop, Codex CLI, Claude Code, Cursor, Cline); provider endpoint catalog (MiniMax, DeepSeek…); Responses API vs Chat Completions protocol selection. → `references/ai-client-model-injection/`
- **Browser frontend → LLM API (CORS)** — cross-provider API-style adaptation (OpenAI vs Anthropic vs MiniMax), the minimal FastAPI CORS proxy, browser fetch template, and the `Failed to fetch` debugging flow (URL + status + body). → `references/llm-browser-gateway/`

## End of Skill