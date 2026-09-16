---
name: "flask-api-proxy-tool"
description: "|"
priority: 0.5
imported-from: "agents"
source-path: "C:\\Users\\leohu\\.agents\\skills\\software-development\\flask-api-proxy-tool\\SKILL.md"
---
# Flask API Proxy Tool

Build custom web tools: Flask backend proxies external APIs, HTML/JS frontend provides controls.

## Architecture Pattern

```
Browser → Flask Backend → External API
              ↓
        config.json (persists settings)
```

Backend handles: API key security, CORS bypass, polling logic, rate limiting.
Frontend handles: UI controls, file upload, result display, status feedback.

## File Structure

```
project/
├── server.py          # Flask backend
├── index.html         # Frontend (served by Flask)
├── config.json        # Persisted settings (auto-created)
├── requirements.txt   # flask, flask-cors, requests
└── assets/            # Generated media (optional)
```

## Backend Template (server.py)

```python
import os, json, time, requests
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder=".", static_url_path="")
CORS(app)

CONFIG_FILE = Path(__file__).parent / "config.json"
DEFAULT_CONFIG = {"api_base": "https://api.example.com/v1", "api_key": "", "model": ""}

def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return {**DEFAULT_CONFIG, **json.load(f)}
    return dict(DEFAULT_CONFIG)

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

config = load_config()

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

@app.route("/api/generate", methods=["POST"])
def generate():
    data = request.json
    # Build payload, call external API, return task_id or result
    api_key = data.get("api_key") or config["api_key"]
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": config["model"], ...}
    
    resp = requests.post(f"{config['api_base']}/generations", headers=headers, json=payload, timeout=30)
    if resp.status_code == 200:
        result = resp.json()
        task_id = result.get("id") or result.get("task_id")
        if task_id:
            return jsonify({"status": "submitted", "task_id": task_id})
        return jsonify({"status": "completed", "result": result})
    return jsonify({"error": resp.text[:500]}), resp.status_code

@app.route("/api/poll/<task_id>")
def poll(task_id):
    # Poll multiple endpoint paths (OpenAI APIs vary)
    headers = {"Authorization": f"Bearer {config['api_key']}"}
    for path in [f"/generations/{task_id}", f"/tasks/{task_id}"]:
        resp = requests.get(f"{config['api_base']}{path}", headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            status = data.get("status", "unknown")
            url = data.get("video_url") or data.get("url") or ""
            return jsonify({"status": status, "url": url})
    return jsonify({"status": "unknown"}), 404

@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "GET":
        safe = {k: v for k, v in config.items() if k != "api_key"}
        safe["has_key"] = bool(config.get("api_key"))
        return jsonify(safe)
    else:
        for k in ["api_base", "api_key", "model"]:
            if k in request.json:
                config[k] = request.json[k]
        save_config(config)
        return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8765, debug=True)
```

## Frontend Patterns

### Dark Theme (Apple Industrial Style)
```css
:root{--bg:#0a0a0a;--card:#141414;--border:#222;--text:#e5e5e5;--text2:#888;--blue:#007AFF;--radius:12px}
body{font-family:-apple-system,BlinkMacSystemFont,"SF Pro",sans-serif;background:var(--bg);color:var(--text)}
```

### File Upload (drag + click)
```javascript
const zone = document.getElementById('uploadZone');
zone.addEventListener('dragover', e => e.preventDefault());
zone.addEventListener('drop', e => {
  const file = e.dataTransfer.files[0];
  const reader = new FileReader();
  reader.onload = e => { const dataUri = e.target.result; };
  reader.readAsDataURL(file);
});
```

### Async Polling
```javascript
async function pollTask(taskId, interval=5000, maxTime=600000) {
  const start = Date.now();
  while (Date.now() - start < maxTime) {
    const resp = await fetch(`/api/poll/${taskId}`);
    const data = await resp.json();
    if (data.status === 'succeeded' || data.status === 'completed') {
      if (data.url) showResult(data.url);
      return;
    }
    if (data.status === 'failed') throw new Error(data.error);
    await new Promise(r => setTimeout(r, interval));
  }
  throw new Error('Timeout');
}
```

### Config Modal
Settings persisted via backend `/api/config` endpoint, modal UI for editing.

## Key Pitfalls

1. **API key in frontend = exposed**: Always proxy through backend, never send API key to browser
2. **CORS**: Flask-CORS handles this, but ensure `CORS(app)` is called before routes
3. **"OpenAI-compatible" ≠ identical format**: DO NOT assume payload shape. UpToken, Kling, Luma all claim "OpenAI-compatible" but use completely different payload formats. ALWAYS read the actual API docs first. Key differences:
   - UpToken: `content` array with `type: "image_url"` + `role: "reference_image"` (NOT `image` field)
   - UpToken: requires Asset Library upload first (POST /assets → asset:// URL)
   - Kling: `image_url` field (string URL, not base64)
   - Some use `prompt` string, others use `content` array with `type: "text"`
4. **Asset Library pattern**: Some APIs (UpToken) require uploading media to an asset store first, getting a reference URL (e.g. `asset://ut-asset-xxx`), then using that URL in the generation request. Response field may be `asset_url` not `id` — always check actual response shape.
5. **Frontend error detail**: When backend returns `{error: "...", detail: "..."}`, show BOTH in the frontend. Users can't debug if they only see "API 返回 400" without the detail. Log `detail` with `|` separator for single-line display.
6. **Polling path varies**: OpenAI-compatible APIs use different paths (`/generations/`, `/tasks/`, `/videos/`). Check docs first; fall back to trying multiple paths.
7. **Base64 images**: Frontend sends `data:image/jpeg;base64,...` URI. Backend may need to strip prefix or send as-is depending on API.
8. **Config security**: Never log or return API key in GET responses. Return `has_key: true/false` instead.
9. **Background process**: Use `python server.py &` or Python `subprocess` for daemon mode. Verify with `curl localhost:PORT/api/config`.
10. **Windows paths**: Use `pathlib.Path` not string concatenation. Forward slashes work in Python on Windows.
11. **Response field names vary**: UpToken returns `asset_url` not `id`/`asset_id`. Always inspect actual response or test with curl before coding assumptions.

## Reference Image Handling in Image Gen Proxies

When proxying image generation with reference images, the field name varies by model/provider. Common findings:

| Attempt | Field | Result |
|---------|-------|--------|
| `images` (array) | Silently ignored |
| `image` (single string) | Single ref works ✓ |
| `image` (array) | HTTP 400: Go unmarshal error |
| `image_urls` (array) | Accepted but refs ignored |
| `reference_images` (array) | Accepted but refs ignored |

**Best approach**: Combine all reference images into ONE before sending:
```javascript
const sharp = require('sharp');
async function combineImages(images) {
  if (!images || images.length <= 1) return images;
  const buffers = await Promise.all(images.map(img => {
    const b64 = img.replace(/^data:image\/\w+;base64,/, '');
    return Buffer.from(b64, 'base64');
  }));
  const metas = await Promise.all(buffers.map(b => sharp(b).metadata()));
  const maxH = Math.max(...metas.map(m => m.height));
  const totalW = metas.reduce((s, m, i) => s + m.width + (i > 0 ? 8 : 0), 0);
  const composite = await sharp({
    create: { width: totalW, height: maxH, channels: 4, background: { r: 0, g: 0, b: 0, alpha: 0 } }
  }).composite(buffers.map((b, i) => ({
    input: b, top: 0,
    left: metas.slice(0, i).reduce((s, m) => s + m.width, 0) + i * 8
  }))).png().toBuffer();
  return [`data:image/png;base64,${composite.toString('base64')}`];
}
```
Call before `imagePayload()`: `images = await combineImages(images);`

### Common Pitfalls for Image Gen Proxies
- **Data URL size**: 5MB PNG → ~7MB base64. 2+ images duplicated in payload → `TypeError: terminated`. Always deduplicate.
- **Localhost URLs**: Fetch and convert to data URLs before forwarding upstream.
- **Video handler also needs conversion**: `handleVideoGenerate` often skips localhost-to-dataURL unlike image handlers.
- **Image size limit**: `MAX_SINGLE_IMAGE_BYTES` default 4MB → increase to ≥10MB for generated images.
- **Model ignores `size`**: Some models always output 16:9 regardless of `size` param.
- **Debug logging**: Include `hasImages`, `imagesCount`, `promptLen` in submission log.

## Verification

After building, test with:
1. `pip install flask flask-cors requests`
2. `python server.py` (starts on :8765)
3. Open `http://localhost:8765` in browser
4. Test config save/load via modal
5. Test upload + generate flow (needs real API key)
