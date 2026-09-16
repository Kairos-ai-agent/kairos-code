---
name: "ai-media-gen-ui"
description: "Build frontend UIs and backend proxies for AI image/video generation tools. Covers 4-mode pattern (txt2img/img2img/txt2vid/img2vid), client-side async polling, adaptive settings, model classification,"
priority: 0.5
imported-from: "agents"
source-path: "C:\\Users\\leohu\\.agents\\skills\\software-development\\ai-media-gen-ui\\SKILL.md"
---
# AI Media Generation UI

Build a frontend UI for async AI image/video generation tools connected to an OpenAI-compatible backend.

## Architecture: Client-Side Polling

Never block the UI waiting for generation. Use this pattern:

```
Browser                              Server                          External API
  │                                    │                                │
  │── POST /api/image/generate ──────► │                                │
  │                                    │── POST (submit job) ─────────► │
  │◄── { jobId, status:"running" } ── │◄── { id } ──────────────────── │
  │                                    │                                │
  │── GET /api/image/generate/status/ │                                │
  │        :jobId (every 2s) ────────►│                                │
  │                                    │── GET /status ───────────────► │
  │◄── { status:"completed",           │◄── { image data } ──────────── │
  │       outputUrl } ──────────────── │                                │
```

### Server Changes

1. Split `handleImageGenerate`/`handleVideoGenerate` into **submit-only** (POST returns jobId immediately)
2. Add status endpoints: `GET /api/image/generate/status/:jobId` and `GET /api/video/generate/status/:taskId`
3. Status endpoints do a **single poll** to external API — no server-side polling loop
4. On completion, download/save the result and return `outputUrl`

### Client Polling

```javascript
const result = await requestJson(apiUrl, { method: "POST", body: JSON.stringify(payload) });
const jobId = result.jobId;
// Animate progress
let dots = "";
const pollInterval = setInterval(() => {
  dots = dots.length >= 3 ? "" : dots + ".";
  updateUI(`生成中${dots}`);
}, 500);
// Poll until done
const completed = await pollUntilDone(`/api/image/generate/status/${jobId}`, 300);
clearInterval(pollInterval);
showResult(completed.outputUrl);
```

```javascript
async function pollUntilDone(url, maxAttempts) {
  for (let i = 0; i < maxAttempts; i++) {
    await new Promise(r => setTimeout(r, 2000));
    const res = await requestJson(url);
    if (res.status === "completed") return res;
    if (res.status === "failed") throw new Error(res.error);
  }
  throw new Error("Job timed out");
}
```

### Pitfall: Canvas Nodes Using Async Mode Without Polling

The most common bug when adding image generation to canvas node UIs (as opposed to the main home page form) is **sending `mode: "async"` but not implementing client-side polling after receiving the jobId**.

**Symptoms**: Server log shows job submitted successfully with jobId; client logs show `{success: true, jobId: "imgjob_...", status: "running"}` but then throws `"未知错误: {success: true, jobId: ...}"`. The `gaiaImgUrl()` helper returns `null` because it checks `d.outputUrl` — which doesn't exist in async mode responses.

**Root cause**: The server's async path (POST to `/images/generations/async`) returns `{success: true, jobId, status: "running", submitted}` — no `outputUrl`. The client immediately calls `gaiaImgUrl(d, apiUrl)` which looks for `d.outputUrl` and returns null, falling into the error handler.

**Fix pattern** — after receiving the POST response, check if async mode was used and polling is needed:

```javascript
var d = JSON.parse(txt);
var imgUrl = null;
if (isT && d.success && d.jobId && !d.outputUrl) {
  // Async mode: poll the status endpoint
  var jid = d.jobId;
  var stUrl = apiUrl.replace(/\/api\/image\/generate$/, "/api/image/generate/status/");
  for (var att = 0; att < 300; att++) {
    await new Promise(r => setTimeout(r, 2000));
    updateProgress(`生成中... (${(att+1)*2}s)`);
    var sr = await fetch(stUrl + jid);
    var sj = await sr.json();
    if (sj.status === "completed" && sj.outputUrl) { imgUrl = sj.outputUrl; break; }
    if (sj.status === "failed") throw new Error(sj.error || "生成失败");
    if (sj.status === "cancelled") throw new Error("任务已取消");
  }
  if (!imgUrl) throw new Error("轮询超时");
  // Handle relative outputUrl
  if (imgUrl.startsWith("/")) imgUrl = window.location.origin + imgUrl;
} else {
  // Sync mode or direct response — use existing gaiaImgUrl
  imgUrl = isT ? gaiaImgUrl(d, apiUrl) : (d.data?.[0]?.url || d.data?.[0]?.b64_json);
}
if (imgUrl) {
  displayResult(imgUrl);
} else {
  throw new Error(d.error?.message || "未知错误: " + JSON.stringify(d).slice(0, 200));
}
```

**All canvas generation functions need this fix**: `exImg`, `exGenAsset`, and any `exVid` equivalent. The home page form (`generate()` in home.js) already handles polling via `pollUntilDone` — the bug only appears in **canvas node** generation functions that were written independently.

### Pitfall: Connection Chain Depth in Canvas Node Traversal

When one canvas node auto-creates another (e.g. sboard → prompt → image-gen → prompt-video → video-gen), searching for upstream nodes by connection requires **iterating through multiple hops**. A single `S.conns.some()` only finds direct connections.

**Bad** — only checks 1-2 hops, misses auto-created chains 3+ hops deep:
```javascript
var sbNode = S.nodes.find(function(n) {
  return n.type === "sboard" && S.conns.some(function(c) {
    return c.from === n.id && (c.to === nd.id || S.nodes.some(function(m) {
      return m.id === c.to && S.conns.some(function(cc) {
        return cc.from === m.id && cc.to === nd.id;
      });
    }));
  });
});  // BUG: deeply nested → hard to debug, easy to miss parens
```

**Good** — iterative traversal, 5 hops max, clear and debuggable:
```javascript
var targetNode = null, tmp = nd;
for (var hop = 0; hop < 5; hop++) {
  var up = S.nodes.find(function(n) {
    return S.conns.some(function(c) {
      return c.from === n.id && c.to === tmp.id;
    });
  });
  if (!up) break;
  if (up.type === "sboard") { targetNode = up; break; }
  tmp = up;
}
```

**WARNING**: Deeply nested `find` + `some` + `some` chains are syntax-error-prone. A single missing `)` paren causes the entire page to fail to render (blank page with 0 nodes). Always prefer iterative loops over nested expressions.

## Per-Node Style in Canvas Workflows

When adding a style dropdown to canvas nodes (scriptwriter, script, prompt nodes), **do not use the global `S.cfg.style`** for an individual node's choice.

### Correct Pattern

1. **Save to `nd.meta.style`** (per-node, persisted with canvas state):
```javascript
// In event handler:
var styleSel = el.querySelector(".sw-sty");
if (styleSel) {
  // Initialize from saved meta on load
  var saved = nd.meta && nd.meta.style;
  if (saved) styleSel.value = saved;
  // Save on change
  styleSel.addEventListener("change", function() {
    nd.meta = nd.meta || {};
    nd.meta.style = this.value;
  });
}
```

2. **Trace style through connection chain** when generating downstream content, rather than reading the global `S.cfg.style`:
```javascript
// Trace from sboard → episode → script/scriptwriter → meta.style
var tracedStyle = "";
var epNode = S.nodes.find(function(n) {
  return S.conns.some(function(c) { return c.from === n.id && c.to === sbNode.id; });
});
if (epNode) {
  var scriptNode = S.nodes.find(function(n) {
    return S.conns.some(function(c) { return c.from === n.id && c.to === epNode.id; });
  });
  if (scriptNode && scriptNode.meta && scriptNode.meta.style) {
    tracedStyle = scriptNode.meta.style;
  }
}
```

3. **The global fallback** should only be used when no connected script/scriptwriter node is found.

## GaiaVideoFactory API: Multipart Form for Reference Images

Seedance 2.0 全能参考模式 requires **multipart form data** with `input_reference[]`. JSON body with `images[]` does NOT set the `role: "reference_image"`.

### Correct Server-Side Submission
```javascript
const form = new FormData();
form.append("model", "seedance-2.0-fast");
form.append("prompt", "@图片1 您的视频描述");
form.append("seconds", String(seconds));
form.append("size", "1280x720");
form.append("resolution_name", "720p");
for (const image of images) {
  // MUST use input_reference[] for 全能参考模式
  form.append("input_reference[]",
    new Blob([new Uint8Array(image.buffer)], { type: image.mime }),
    image.fileName);
}
// Avoid doubao-seedance-2-0-fast-260128 — its upstream internal path
// /api/v3/contents/generations/tasks returns 404 (Gaia upstream issue).
// Use seedance-2.0-fast instead (supports both text-to-video and
// image-to-video with 全能参考模式).
```

## Toolbar Layout: Icon/Text Alignment

When building a vertical toolbar with emoji icons + text labels:

```css
.tbar-left {
  display: flex; flex-direction: column; align-items: center;
  padding: 14px 16px; gap: 8px;
}
.tb-left {
  display: flex; align-items: center; gap: 8px; white-space: nowrap;
  padding: 10px 18px; font-size: 15px;
}
/* Icons and text same size */
.tb-left .tb-ico,
.tb-left .tb-lbl { font-size: 15px; }
/* Fixed-width icon container for vertical alignment */
.tb-left .tb-ico { width: 20px; text-align: center; flex-shrink: 0; }
```

Emoji characters have different visual widths. **Always give icons a fixed width** + `text-align:center` to ensure all toolbar items appear vertically aligned.

## Model Selection Persistence

Save user's model choice across page refreshes using localStorage:

```javascript
let userSelectedModel = {};
try {
  const saved = JSON.parse(localStorage.getItem("gaia_selected_models") || "{}");
  if (saved.image) userSelectedModel.image = saved.image;
  if (saved.video) userSelectedModel.video = saved.video;
} catch {}

// On model change:
modelSelect.addEventListener("change", () => {
  const modelType = isVideoMode ? "video" : "image";
  userSelectedModel[modelType] = modelSelect.value;
  try { localStorage.setItem("gaia_selected_models", JSON.stringify(userSelectedModel)); } catch {}
});

// In populateModelSelect, prefer user's saved choice over defaults:
const value = prevUserChoice || configDefault || models[0];
```

## Model-Aware Size Computation

Different models have different minimum pixel requirements. Always validate and auto-scale.

```javascript
// Doubao models (seedream/seedance) require min 3,686,400 pixels
if (/seedream|seedance/i.test(model)) {
  const minPixels = 3686400;
  const [w, h] = baseSize.split("x").map(Number);
  if (w * h < minPixels) {
    const scale = Math.ceil(Math.sqrt(minPixels / (w * h)));
    const newW = Math.ceil(w * scale / 64) * 64;
    const newH = Math.ceil(h * scale / 64) * 64;
    size = `${newW}x${newH}`;
  }
}
```

## Video Generation: Reference Image Modes

图生视频 (img2vid) supports two modes:
- **全能参考** (all-purpose): Multiple reference images, all with `role: "reference_image"`
- **首尾帧** (start/end frame): Exactly 1 start frame (`role: "first_frame"`) + 1 end frame (`role: "last_frame"`)

### UI Structure

```
[Prompt textarea]
[＋ 负面提示词] (click to expand)
[Reference upload area]
  ├── [全能参考] [首尾帧] (mode toggle, img2vid only)
  ├── [📎 选择参考图片] (multiple, for 全能参考)
  └── [🎬 首帧图] [🏁 尾帧图] (single each, for 首尾帧)
[Model | Ratio | 清晰度/时长 | Generate▶]
```

## Audio Upload + @Mention Pattern

See `references/audio-upload-and-mention.md` for:
- Multi-file audio upload with per-item delete
- Categorized @mention dropdown (upload images → audio → asset library)
- Asset library preloading for instant @mention
- Simplify-first approach: single file → confirm → multi-file

## User Preferences (This Project)

- **Clean light design**: `#f0f2f5` background, white cards, `#e4e7ec` borders, `#4f6ef7` blue accent
- **No dark themes, no glassmorphism, no flashy effects**
- Simple rounded corners (12px), subtle shadows
- Mode buttons in a horizontal row, 4 columns
- Generate button uses blue gradient (`#4f6ef7 → #6366f1`)
- All settings in one row at the bottom with generate button
- SVG icons with `#4f6ef7` stroke, clean line art style

---

## Absorbed Skills

This umbrella skill consolidates content from the following previously-separate skills:

### Dark Theme Patterns
See `references/dark-theme-patterns.md` for the teal/dark gradient theme variant with trapezoid mode buttons (`clip-path: polygon(8% 0%, 92% 0%, 100% 100%, 0% 100%)`). Previously `ai-gen-ui`.

### Video API Backend Proxy (Flask)
See `references/video-api-backend-proxy.md` for Flask backends that proxy external video generation APIs (UpToken, GaiaVideoFactory). Covers the upload→poll→generate→poll pattern, asset library handling, and provider-specific quirks. Previously `ai-video-api-integration`.

### Test/Mock UI Patterns
See `references/test-ui-patterns.md` for Node.js+Express project structure, video ratio filtering, recurring user preference patterns. Previously `ai-tool-test-ui`.

### Client-Side Async Polling (standalone reference)
The polling pattern described in the Architecture section above (`pollUntilDone`, server split) also covers what was previously the standalone `client-side-polling` skill. Key pitfall: if the POST endpoint already does server-side polling, the client must NOT re-poll the status endpoint.

### Async Submit Architecture
The server-split pattern (POST returns immediately, GET /status polls) documented above also covers what was previously `async-ai-web-ui`. Both server endpoints and client polling code are in the Architecture section.
