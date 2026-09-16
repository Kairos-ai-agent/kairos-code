---
name: "node-app-merging"
description: "Merge two or more similar Node.js web apps into a single unified application with shared config, tab-based UI, and organized output. Use when the user has multiple small standalone tools (e.g. separat"
priority: 0.5
imported-from: "agents"
source-path: "agents/skills/software-development/node-app-merging/SKILL.md"
---
# Node.js App Merging

Merge two or more similar Node.js web tools into a single app with tab-based UI.

## When to Use

- User has multiple standalone web apps with overlapping structure (same server.js pattern, same static-file serving, same config system)
- Apps share the same API base URL, auth key, or output directory
- User wants a single launcher/browser tab instead of multiple ports

## Step-by-Step Workflow

### 1. Read Both Projects

Read all source files of each project before writing anything:
- `server.js` — route structure, API calls, config format, utility functions
- `public/index.html` — DOM element IDs, form fields, layout
- `public/app.js` — frontend logic, event handlers, default prompts
- `public/styles.css` — may differ between projects (colors, grid layouts)
- `config.json` — current user config (API keys, model names)
- `start-windows.bat` — port number, node.exe path, startup sequence
- `README.md` — for understanding purpose and API docs

### 2. Design the Unified Config Structure

**Always use nested sections** for per-module config. Never mix module-specific fields at the top level.

```json
{
  "baseUrl": "https://shared-api.example.com/v1",
  "apiKey": "sk-...",
  "moduleA": {
    "model": "model-a",
    "size": "1280x720"
  },
  "moduleB": {
    "model": "model-b",
    "seconds": 6
  }
}
```

**Config migration**: When loading old flat configs, detect whether nested keys exist. If not, migrate flat fields into the correct nested section based on heuristics (e.g. model name, field presence).

```js
// Detect if old flat format
if (!parsed.moduleA && parsed.model && /* heuristic */) {
  moduleA.model = parsed.model;
}
```

Use `structuredClone(DEFAULT_CONFIG)` for safe deep copy of defaults (Node 17+).

### 3. Design Tab-Based UI

**Prefix all DOM element IDs with the tab name** to avoid collisions:
- Tab A: `imgBaseUrlInput`, `imgModelInput`, `imgGenerateBtn`
- Tab B: `vidModelInput`, `vidSecondsInput`, `vidGenerateBtn`

**Tab switching** — pure CSS + minimal JS:
```html
<nav class="tabs">
  <button class="tab active" data-tab="image">🎨 生图</button>
  <button class="tab" data-tab="video">🎬 图生视频</button>
</nav>
<div id="tabImage" class="tab-content active">...</div>
<div id="tabVideo" class="tab-content">...</div>
```

```js
document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById(`tab${capitalize(btn.dataset.tab)}`).classList.add("active");
  });
});
```

```css
.tab-content { display: none; }
.tab-content.active { display: block; }
```

### 4. Prefix API Routes

Use prefixed routes to separate module handlers:
- `POST /api/image/generate` → `handleImageGenerate()`
- `POST /api/video/generate` → `handleVideoGenerate()`
- `GET /api/config` → shared config endpoint
- `POST /api/config` → shared config save (accepts nested sections)
- `POST /api/open-output` → accepts `{ subdir: "image" }` or `{ subdir: "video" }`

### 5. Split Output Subdirectories

```
outputs/
├── image/    # module A output
└── video/    # module B output
```

Each handler writes to its own subdirectory. The static file route serves from the unified `outputs/` root:
```js
if (pathname.startsWith("/outputs/")) {
  return serveStaticFile(req, res, OUTPUT_DIR, pathname.replace(/^\/outputs\//, "/"));
}
```

Output URLs in API responses include the subdirectory:
```js
outputUrl: `/outputs/image/${encodeURIComponent(fileName)}`
```

### 6. Merge Utility Functions

Identical functions (logging, redact, sendJson, sendText, readBody, static file serving, auth headers, sleep, etc.) appear in both projects — keep only one copy.

### 7. Test Before Declaring Done

Start the merged server as a background process, then test:
```bash
# Config endpoint
curl -s http://127.0.0.1:PORT/api/config
# HTML serving
curl -s http://127.0.0.1:PORT/ | head -c 200
# Both generate routes exist (should return "X is required", not 404)
curl -s -X POST http://127.0.0.1:PORT/api/image/generate -H "Content-Type: application/json" -d '{}'
curl -s -X POST http://127.0.0.1:PORT/api/video/generate -H "Content-Type: application/json" -d '{}'
```

Kill the test server after verification.

## Pitfalls

- **Syntax check server.js** — Node's `--check` or the linter runs automatically on write. Fix any errors before proceeding.
- **Port conflict** — use a new port (not one of the old ports) to avoid stale processes blocking startup.
- **API Key handling** — copy the API key from existing `config.json` to the new one so it's not lost. The UI still shows "留空则保留已保存的 Key".
- **CSS merge** — don't blindly concatenate. Deduplicate shared rules (`.button`, `.panel`, etc.) and keep only module-specific rules (`.image-wrap` vs `.video-wrap`).
- **Element ID collisions** — the #1 bug when merging frontends. Prefix ALL IDs.
- **Shared config save** — when either tab saves config, it should send only its own section's changes plus the shared fields (baseUrl, apiKey). The server merges with the current config.
