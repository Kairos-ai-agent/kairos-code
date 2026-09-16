---
name: "debug-pro-1.0.0"
description: "Systematic debugging methodology and language-specific debugging commands, including browser event debugging for Chromium-specific issues."
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\debug-pro-1.0.0\\SKILL.md"
---
# debug-pro

Systematic debugging methodology and language-specific debugging commands.

## The 7-Step Debugging Protocol

1. **Reproduce** — Get it to fail consistently. Document exact steps, inputs, and environment.
2. **Isolate** — Narrow scope. Comment out code, use binary search, check recent commits with `git bisect`.
3. **Hypothesize** — Form a specific, testable theory about the root cause.
4. **Instrument** — Add targeted logging, breakpoints, or assertions.
5. **Verify** — Confirm root cause. If hypothesis was wrong, return to step 3.
6. **Fix** — Apply the minimal correct fix. Resist the urge to refactor while debugging.
7. **Regression Test** — Write a test that catches this bug. Verify it passes.

## Language-Specific Debugging

### JavaScript / TypeScript

**Pitfall — Removed HTML element but kept JS references:**
When you delete an HTML element (or change its `id`), `document.getElementById('id')` returns `null`. If any subsequent line calls `.addEventListener()` or `.classList.toggle()` on that null reference, it throws a `TypeError` that **silently halts the entire script** at the point of failure — no error visible in the UI, all code after that line (including `loadConfig()`, model population, etc.) never runs.

Symptoms: page renders but app logic never initializes; model dropdowns are empty; console shows no app log output. Debugging steps:
1. Check the browser console for `TypeError: Cannot read properties of null`.
2. Search for `document.getElementById("id")` calls that match recently-removed elements.
3. Verify every call chain from the DOM lookup: `getElementById` → `addEventListener` → methods that use the reference.
4. Remove or null-guard all references to deprecated elements.

Fix pattern:
```js
// Before: references element that may be removed
const refUploadBtn = document.getElementById("refUploadBtn");
// Later: TypeError if element is gone
refUploadBtn.addEventListener("click", () => {});

// After: null-guard the reference
const refUploadBtn = document.getElementById("refUploadBtn");
if (refUploadBtn) refUploadBtn.addEventListener("click", () => {});
```

This is particularly dangerous because top-level `const` declarations followed by method calls run synchronously during script parsing — any failure there prevents ALL subsequent code from loading, including async initialization.

**Pitfall — Cross-script `let` variable isolation:** When data is loaded into a script via `let files = []` (script-scoped), it is NOT accessible from other script tags or other modules. To share data across separate `<script>` tags on the same page, expose it globally:

```js
// Script A (loads first):
let files = [];
window.files = files; // reference to same array
async function loadFiles() {
  const res = await fetch("/api/data");
  files = await res.json();
  window.files = files; // update shared reference after reassignment
}

// Script B (loads after A, accesses shared data):
function showDropdown() {
  // window.files is now accessible across scripts
  for (const f of window.files) { /* ... */ }
}
```

The initial `window.files = files` sets the global to the same array reference, but after `files = await res.json()` (reassignment), the global still points to the old empty array. Always re-assign the global after the local variable changes.

**Pitfall — Function hoisting in strict mode HTML scripts:** In a single `<script>` tag with `"use strict"`, function declarations defined much later in the file may NOT be hoisted for use by code that runs during page load (inside IIFEs, event handlers, or immediately-invoked code paths). This is especially true when the function is called from code that runs synchronously during DOM parsing (e.g., inside `renderNode` called from `loadCvs` at init time).

```js
// Defined at line 2198 — called during page init
function updateSboardPanel(nd, el) {
  var html = "<div>" + _escHtml(text) + "</div>";  // ❌ ReferenceError: _escHtml is not defined
}

// Defined at line 3733 — much later in the same script
function _escHtml(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
```

Symptoms: `ReferenceError: X is not defined` at runtime during canvas/node rendering even though the function IS defined in the same file. Stack trace shows `at functionName (file:line:col)` with the failed call.

**✅ Fix — inline the helper as a local function variable:**

```js
function updateSboardPanel(nd, el) {
  var esc = function(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  };
  var html = "<div>" + esc(text) + "</div>";  // ✓ local reference always works
}
```

Alternative: use `textContent` instead of `innerHTML` + escaping when possible.

**Pitfall — Async data loading for @mention dropdowns:** If a feature needs data that's loaded asynchronously (e.g. asset library from API), preload it on page init, not only on user interaction. Otherwise the feature will appear broken until the user happens to trigger the load action.

**Pitfall — localStorage staleness when save functions write different keys than the auto-save interval:**\nWhen a manual save function (`saveCvs`, `saveSettings`, etc.) saves a subset of state keys but an auto-save interval (`setInterval`) writes additional keys, there is a freshness window between the manual save and the next interval tick where a page refresh will see stale data.\n\nDiagnosis:\n1. List ALL localStorage writes (`localStorage.setItem` calls) across the codebase — manual saves AND intervals.\n2. Compare the keys each writes. If any key is written ONLY by the interval and not by manual save, a manual-save-then-refresh sequence will read the interval's last-written (potentially stale) value from a previous page load.\n3. Check the init code: which localStorage keys does it read on page load? If it reads a key that only the interval writes, it's exposed to the staleness bug.\n\nFix:\nMake the manual save function write ALL the same keys the interval writes:\n```js\nfunction saveCvs() {\n  var key = \"kc-cvs-\" + canvasMode;\n  localStorage.setItem(key, JSON.stringify(data));\n  localStorage.setItem(\"kc-style\", S.cfg.style || '');\n  localStorage.setItem(\"kc-mode\", canvasMode);  // ← was only in interval, added here\n  toast(\"画布已保存\");\n}\n```\n\nAlso, the init code should write the derived state back to localStorage immediately, not wait for the first interval tick. Add `localStorage.setItem` right after reading the saved value:\n```js\nvar saved = localStorage.getItem(\"kc-mode\");\nif (saved === \"frame\" || saved === \"story\") { canvasMode = saved; }\nlocalStorage.setItem(\"kc-mode\", canvasMode);  // ← persist immediately\n```\n\nWhen a save function saves SOME but NOT ALL state keys: the unsaved keys become stale on refresh until the next interval fires. The safest pattern is a single `saveState()` helper that writes EVERYTHING and is called by both manual save and auto-save.\n\nSee `references/spa-js-syntax-debugging.md` for SPA white-screen debugging — template literal backtick escaping, `split('\\n')` vs `split('\n')` mismatches after template literals, and HTML escaping for innerHTML data interpolation.

**Pitfall — Browser cache poisoning during iterative HTML/CSS/JS development:**
When iterating fast on static HTML files with `<script src="/file.js">`, the browser caches the JS aggressively even with `Cache-Control: no-store`. Changes to the `.js` file don't take effect on refresh. Workarounds:
1. Append a cache-busting query param: `<script src="/file.js?nocache=N">` — increment `N` each edit.
2. Use inline `<script>...</script>` blocks for the iteration phase and move back to external files when stable.
3. Restart the dev server to clear any server-side caching layer.
4. Use the browser's dev tools → Network → Disable cache checkbox.

**Pitfall — Incomplete `node_modules` after interrupted install:**
`pnpm install` / `npm install` can leave `node_modules` in a half-installed state when interrupted (Ctrl+C, network drop, timeout). The directory exists but packages are missing internal files, causing `ERR_MODULE_NOT_FOUND` for core modules like Next.js's `next-dev.js`. Diagnosis steps:
1. Check for `node_modules/.modules.yaml` — if missing, pnpm never completed.
2. Spot-check key packages: `ls node_modules/next/dist/cli/next-dev.js` (or the failing module's expected path).
3. If incomplete: delete `node_modules` and re-run the package manager. Do NOT try to fix by installing individual packages.

The pnpm warning about `onlyBuiltDependencies` in `package.json` is a deprecation notice (pnpm v10+) and is harmless — it does not block installation.

```bash
# Node.js debugger
node --inspect-brk app.js
# Chrome DevTools: chrome://inspect

# Console debugging
console.log(JSON.stringify(obj, null, 2))
console.trace('Call stack here')
console.time('perf'); /* code */ console.timeEnd('perf')

# Memory leaks
node --expose-gc --max-old-space-size=4096 app.js
```

### Python
```bash
# Built-in debugger
python -m pdb script.py

# Breakpoint in code
breakpoint()  # Python 3.7+

# Verbose tracing
python -X tracemalloc script.py

# Profile
python -m cProfile -s cumulative script.py
```

**Pitfall — sqlite3 `execute()` rejects multi-statement strings:** Python's `sqlite3` module runs exactly ONE statement per `execute()` call. Chaining with `;` raises `ProgrammingError: You can only execute one statement at a time.` This bites silently in init / migration code because tests using `:memory:` SQLite often pass via different code paths and never surface it — the failure only shows up at production startup when real-disk WAL mode kicks in.

Symptom chain (typical):
1. `Persistence.__init__()` calls `_init_schema()` which runs `conn.execute("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;")`.
2. `ProgrammingError` propagates up to module-level code (e.g. `orchestrator = Orchestrator(...)` at module import).
3. uvicorn / FastAPI `app.py` import fails → server crashes silently.
4. Any startup-script health check that polls an unrelated endpoint (or doesn't poll at all) reports "started" while the backend is actually dead. Symptom is "frontend loads but no data / no API responses / service appears broken with no logs visible".

Fix — split into one PRAGMA per `execute()`:
```python
# BAD — single execute with two statements
conn.execute("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;")

# GOOD — two execute calls
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
```

Same rule applies to any SQL string with multiple `;`-separated statements (DDL batches, multi-row INSERTs not using `executemany`). For multi-statement DDL, use `executescript()` (intentionally has no parameter binding). For multi-row parameterized inserts, use `executemany()`.

Diagnostic when "the app silently doesn't start":
1. Run the entry point directly (not via a backgrounded launcher) — Python tracebacks are visible on stderr.
2. If uvicorn says "Application startup failed", scroll up — the real error is in the FastAPI lifespan / import chain, NOT uvicorn.
3. Check for module-level side effects (singletons, `Orchestrator()` instantiation, `Persistence()` instantiation) — these run during import and explode on first init error.
4. Verify the health endpoint actually exists; polling a 404 endpoint always returns "not ready" and masks the real failure.

### Swift
```bash
# LLDB debugging
lldb ./MyApp
(lldb) breakpoint set --name main
(lldb) run
(lldb) po myVariable

# Xcode: Product → Profile (Instruments)
```

### CSS / Layout
```css
/* Outline all elements */
* { outline: 1px solid red !important; }

/* Debug specific element */
.debug { background: rgba(255,0,0,0.1) !important; }
```

### Network
```bash
# HTTP debugging
curl -v https://api.example.com/endpoint
curl -w "@curl-format.txt" -o /dev/null -s https://example.com

# DNS
dig example.com
nslookup example.com

# Ports
lsof -i :3000
netstat -tlnp
```

### Git Bisect
```bash
git bisect start
git bisect bad              # Current commit is broken
git bisect good abc1234     # Known good commit
# Git checks out middle commit — test it, then:
git bisect good  # or  git bisect bad
# Repeat until root cause commit is found
git bisect reset
```

## HTTP API Error Debugging

See `references/http-api-errors.md` for common patterns.

### The fault-injection ladder for invisible HTTP 500s

When a user reports `POST /some/endpoint → 500 Internal Server Error` and your happy-path tests all pass, **the bug is in a branch you never tested**. Standard unhandled-exception paths are: (a) `result["status"]` KeyError when the subprocess returns `None` or `{}`, (b) `cancelled` misinterpreted as failure, (c) genuine runtime exceptions (network timeout, OSError) that the route's outer try/except doesn't know how to format as a meaningful detail. Walk this ladder to surface them:

1. **Verify the happy path is still happy.** Run the exact production code path three ways: (i) direct in-process function call (`await _generate(req, request)`), (ii) FastAPI `TestClient` (`client.post("/api/...", json=...)`), (iii) live HTTP via `urllib.request` or `curl` against the running backend. All three must succeed. If happy path passes — the bug is in an unguarded error branch.

2. **Mock-inject every possible failure mode into the subprocess.** `monkeypatch.setattr(service, "method", AsyncMock(return_value=X))` for each of:
   - `{"status": "failed", "error": "<real agnes error message>"}` → must surface the error string in the 500 detail
   - `{"status": "rate_limited", "retry_after_s": 60}` → must return 429 with Retry-After header, NOT 500
   - `{"status": "cancelled"}` → must return 200 (normal control flow), NOT 500
   - `{"status": "weird_unknown_value"}` → 500 with both status and result in detail
   - `None` → must not KeyError; must produce a meaningful 500 detail
   - `{}` → must not KeyError; must produce a meaningful 500 detail
   - `{"error": "no status key from upstream"}` → must not KeyError; must use the error string as detail
   - `raise RuntimeError("network timeout")` → must propagate so Starlette converts to 500, AND traceback must land on disk
   
   Each failure mode should produce the right HTTP status (200 / 429 / 500) AND the right detail string. The ones that produce bare `Internal Server Error` or wrong status are the real bugs.

3. **Inspect the route handler's `except Exception` block.** It must (a) write the traceback to a persistent file the user can read, (b) log to stderr/stdout so dev-mode debugging works, (c) re-raise so Starlette converts it to a 500 response. If any of these is missing, add it.

4. **Add unit tests for each branch.** One test per failure mode is the right granularity — 7 tests is normal. Without them, the next refactor will silently re-break the defensive access.

Why this works: traditional endpoint tests only cover `status=done` and skip every other branch because the user reports a 500 with no reproduction details. The fault-injection ladder forces the test author to enumerate every reachable result shape, and most production bugs show up at branch number 4 or 5 in the list.

Caveat: starlette `TestClient` re-raises unhandled exceptions in test mode (it does NOT return a 500 response). For testing the unhandled-exception path (`raise RuntimeError`), invoke the inner function directly (`asyncio.run(mv_module._generate(req, request=None))`) and assert the exception propagates + the persistent log was written. Use the live HTTP path (`urllib.request` against the running uvicorn) for end-to-end verification that Starlette converts unhandled exceptions to 500 correctly.

See `references/proxy-api-debugging.md` for multi-layer proxy architecture debugging (client → proxy → upstream). Covers:

- Identifying layers and each layer's expected format
- Multipart form data vs JSON body gotchas
- Parameter type mismatches (number vs string)
- Polling resilience for long-running async jobs
- Download URL construction with relative result_url paths
- Native upstream vs proxy format differences (with concrete Gaia API / Volcano Engine examples)

**Critical rule: Test the known-working format first.** When debugging an API integration, always start by verifying the format that previously worked (or the documented native format) before trying alternatives. Do NOT switch fundamental approaches (e.g. image-to-video → text-to-video) as a debugging step — that changes too many variables at once. Isolate one parameter at a time.

**Critical rule: Check if the server handler mode changed.** A server may have evolved from synchronous (submit → poll → download → return URL) to asynchronous client-poll (submit → return taskId, client polls via separate endpoint). If the canvas/frontend expects a direct URL response but the server returns `{taskId}`, the frontend will fail silently. Verify which handler is mapped to the route in server.js.

**Critical rule: Kill ALL node processes, not just the one you think.** Stale node processes accumulate and serve old code. A single `taskkill //F //IM node.exe` kills ALL running Node.js instances. Verify with `powershell "netstat -ano | findstr LISTENING"` that the port is actually free before restarting.

Key rule: **On HTTP 4xx/5xx, always read the response body** before throwing. The status code alone rarely tells you what's wrong — the body carries the actionable error (invalid param name, wrong model, auth issue, etc.).

```js
fetch(url, options)
  .then(function(r){
    if(!r.ok){
      return r.text().then(function(body){
        throw new Error('HTTP '+r.status+' - '+body.slice(0,400));
      });
    }
    return r.json();
  })
```

Common API 400 causes and their body signals:
| Body keyword | Likely cause |
|-------------|-------------|
| `invalid_*_value` | Parameter value out of range / wrong type |
| `model_not_found` | Model name doesn't exist on this provider |
| `invalid_api_key` / `auth` | Missing or wrong API key |
| `bad_request` | Malformed JSON or wrong Content-Type |
| `content_policy` | Input flagged by content filter |
| `rate_limit` | Too many requests per minute |

**OpenAI-compatible URL construction**: Don't blindly append `/v1/chat/completions`. Smart join:
```js
var base = baseUrl.replace(/\/+$/, '');
var url = /\/chat\/completions$/i.test(base) ? base
       : /\/v1$/i.test(base)               ? base + '/chat/completions'
       :                                     base + '/v1/chat/completions';
```

**Pitfall — `TypeError: Failed to fetch` on POST to local backend is CORS preflight failure, not a network error:**

When the browser console shows `TypeError: Failed to fetch` on any POST/PUT/DELETE to a local HTTP API, the network is fine and the backend is fine — the browser is refusing to even send the request because of CORS preflight. This is the #1 "everything looks broken but nothing is wrong" debugging trap for custom local backends (dsh, FastAPI dev servers, hand-rolled Node servers, Python http.server with custom routes, etc.).

**The mechanism in one line:** Modern browsers auto-send an `OPTIONS` request before any POST with `Content-Type: application/json` (even same-origin). If the server returns anything other than 2xx + `Access-Control-Allow-*` headers, the browser aborts the real POST and `fetch()` throws `TypeError: Failed to fetch`. Network panel will show the OPTIONS as 404 and no further request.

**Reproduction recipe (run from bash, not browser):**

```bash
# 1. Plain GET to confirm the endpoint exists at all
curl -i http://127.0.0.1:PORT/api/endpoint

# 2. OPTIONS preflight exactly as the browser sends it
curl -i -X OPTIONS http://127.0.0.1:PORT/api/endpoint \
  -H "Origin: http://127.0.0.1:PORT" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type"

# GOOD (preflight succeeds):
#   HTTP/1.1 204 No Content
#   access-control-allow-origin: http://127.0.0.1:PORT
#   access-control-allow-methods: POST, GET, OPTIONS
#
# BAD (this is what's causing "Failed to fetch"):
#   HTTP/1.1 404 Not Found
#   content-type: text/plain;charset=UTF-8
#   ...not found
```

A backend that "doesn't handle OPTIONS" (returns 404 + no CORS headers) is the canonical broken case. Confirmed against `dsh web` (deepseek-harness) backend on 2026-08-14 — every RPC endpoint registered, but no CORS middleware, so browser POST + JSON to `/api/*` always failed with `TypeError: Failed to fetch`.

**Why curl alone won't catch this**: `curl` doesn't do preflight. Plain `curl -i` shows the server works. The browser-only failure is invisible without sending the OPTIONS request yourself.

**Three workarounds for local dev** (pick by environment):

1. **Chrome/Edge `--disable-web-security`** — fastest, dirtiest. Disables CORS for one browser profile:
   ```bat
   :: chrome.bat — only use this Chrome for dsh; never for normal browsing
   start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --disable-web-security --user-data-dir="%LOCALAPPDATA%\ChromeDev" http://127.0.0.1:3080
   ```
   Caveat: every site loses CORS protection. Acceptable only for a dedicated dev profile pointed at one trusted local server.

2. **Caddy reverse proxy in front** — clean, single Caddyfile line:
   ```caddyfile
   :3080 {
       reverse_proxy 127.0.0.1:3081
       @options { method OPTIONS }
       respond @options 204
       header Access-Control-Allow-Origin "*"
       header Access-Control-Allow-Methods "GET, POST, OPTIONS"
       header Access-Control-Allow-Headers "Content-Type, Authorization"
   }
   ```
   Run the backend on 3081, Caddy on 3080. No source change to backend.

3. **Custom Node.js CORS proxy (~30 lines)** — no external dependencies. If neither Chrome flag nor Caddy is acceptable (e.g. headless / CI / packaged app), write a tiny Node `http.createServer` that intercepts OPTIONS with the right headers and proxies everything else to the real backend. Copy-paste starter at `templates/cors-preflight-proxy.js` — run with `node cors-preflight-proxy.js [LISTEN_PORT] [UPSTREAM_PORT]` and point your browser at the LISTEN_PORT.

**Source-level fix** (long-term, if you own the backend): add CORS middleware. For Express: `app.use(cors())`. For FastAPI: `app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:PORT"], allow_methods=["*"], allow_headers=["*"])`. For raw `http.createServer`: handle OPTIONS manually returning 204 + CORS headers before any other routing.

**Rule:** when debugging a local web app and "Failed to fetch" is the only signal, never trust the absence of errors in the backend log — the request may have been blocked before reaching it. Always reproduce with curl OPTIONS as the first diagnostic step.

## Browser Drag & Event Debugging

See `references/print-orientation-webview2.md` for WebView2 print orientation debugging — dynamically modifying `@page` CSS in an iframe before `contentWindow.print()` is unreliable; rebuild the iframe with correct CSS baked in instead.

See `references/browser-drag-events.md` for known Chromium issues:

- **Right-click drag**: Chrome on Windows swallows `mouseup` (button 2). Fix: detect release via `e.buttons` in `mousemove`.
- **contextmenu crash**: Calling `preventDefault` + DOM manipulation in a `contextmenu` handler can crash the Chromium renderer. Keep the handler minimal.
- **Overscroll**: `overscroll-behavior:none` prevents back/forward navigation during horizontal drags.
- **Box selection math**: Coordinate conversion for pan/zoom canvases, multi-node drag with snapshot positions.
- **Paste event hijacking**: A global `document.addEventListener('paste')` with `e.preventDefault()` intercepts ALL paste events, including those in input fields and settings modals. **Always check focus first:**
  ```js
  document.addEventListener('paste', function(e){
    var tag = document.activeElement && document.activeElement.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA') return;
    var el = document.activeElement;
    while (el) { if (el.id === 'modalId') return; el = el.parentElement; }
    e.preventDefault();
    // ... handle canvas paste ...
  });
  ```
  Without this guard, users cannot paste API keys, URLs, or any text into your app's input fields.

## Common Error Patterns

| Error | Likely Cause | Fix |
|-------|-------------|-----|
| `Cannot read property of undefined` / `Cannot read properties of null` | Missing null check, DOM element doesn't exist, or wrong data shape | Add optional chaining (`?.`) or guard check. For DOM: ensure element with that `id` exists in the HTML before referencing it in JS |
| `ENOENT` | File/directory doesn't exist | Check path, create directory, use `existsSync` |
| `CORS error` | Backend missing CORS headers | Add CORS middleware with correct origins |
| `TypeError: Failed to fetch` (on POST) | Backend doesn't handle OPTIONS preflight (returns 404 + no CORS headers) | See pitfall below — diagnose with `curl -X OPTIONS`, then add CORS proxy or `--disable-web-security` for local dev |
| `Module not found` | Missing dependency or wrong import path | `npm install`, check tsconfig paths |
| `Hydration mismatch` (React) | Server/client render different HTML | Ensure consistent rendering, use `useEffect` for client-only |
| `Segmentation fault` | Memory corruption, null pointer | Check array bounds, pointer validity |
| `Connection refused` | Service not running on expected port | Check if service is up, verify port/host |
| `Permission denied` | File/network permission issue | Check chmod, firewall, sudo |

## Windows Startup CMD Window Diagnostics

See `references/windows-startup-cmd-diagnostics.md` for a systematic checklist to investigate what CMD windows flash on boot.

Key technique — check PE subsystem type to distinguish GUI apps (no CMD) from CONSOLE apps (will open CMD):

```python
import struct
with open(path, 'rb') as f:
    f.seek(0x3C); pe_off = struct.unpack('<I', f.read(4))[0]
    f.seek(pe_off + 0x5C)
    subsys = struct.unpack('<H', f.read(2))[0]
# 2=GUI, 3=CONSOLE
```

Also verify the file actually exists — orphaned registry entries cause no window.

## Quick Diagnostic Commands

```bash
# What's using this port?
lsof -i :PORT

# What's this process doing?
ps aux | grep PROCESS

# Watch file changes
fswatch -r ./src

# Disk space
df -h

# System resource usage
top -l 1 | head -10
```
