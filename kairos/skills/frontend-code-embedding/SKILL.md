---
name: "frontend-code-embedding"
description: "Embed single-page frontend code inside a Node.js server as base64 to prevent source file theft, and extract it back when needed."
priority: 0.5
imported-from: "agents"
source-path: "agents/skills/software-development/frontend-code-embedding/SKILL.md"
---
# Frontend Code Embedding (base64 in Node.js)

Prevent frontend source code theft by embedding the entire HTML/JS/CSS file as a base64 string inside the Node.js server. No source file exists on disk at runtime.

Also covers the **reverse operation**: extracting the embedded HTML back to disk when you need to edit it.

## When to use

- Confidential single-page app (canvas, dashboard, internal tool)
- Served by a Node.js HTTP server (Express, plain http, etc.)
- Goal: prevent copying the HTML file from disk or via static routes

---

## Part A: Embedding (encode into server.js)

### 1. Encode the file to base64

```bash
node -e "
const fs = require('fs');
const content = fs.readFileSync('public/app.html', 'utf8');
const b64 = Buffer.from(content).toString('base64');
console.log('Base64 length:', b64.length);
"
```

### 2. Append as a constant in server.js

```bash
node -e "
const fs = require('fs');
const content = fs.readFileSync('public/app.html', 'utf8');
const b64 = Buffer.from(content).toString('base64');
const js = '\n// === EMBEDDED APP (base64) ===\nconst _APP_B64 = \"' + b64 + '\";\n';
fs.writeFileSync('/tmp/embed.js', js);
"
cat /tmp/embed.js >> server.js
```

### 3. Update the route to decode at runtime

Before (static file serving):
```js
if (req.method === "GET" && pathname === "/app") {
  return serveStaticFile(req, res, PUBLIC_DIR, "/app.html");
}
```

After (inline decode):
```js
if (req.method === "GET" && pathname === "/app") {
  res.setHeader("Cache-Control", "no-cache, no-store, must-revalidate");
  res.setHeader("Pragma", "no-cache");
  res.setHeader("Expires", "0");
  res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
  res.end(Buffer.from(_APP_B64, "base64").toString("utf8"));
  return;
}
```

### 4. Remove the source file from public/

```bash
rm public/app.html
```

---

## Part B: Extraction (decode back to disk)

When you need to edit the embedded HTML, extract it back to a writable file:

### 1. Identify the base64 constant

In `server.js`, look for a line like:

```js
const _CANVAS_HTML_B64 = "77u/PCFET0NUWVBFIGh0bWw...";
```

The variable name usually hints at what it is (e.g. `_CANVAS_HTML_B64`, `_APP_B64`, `_HTML_B64`).

### 2. Extract and decode in one command

```bash
node -e "
const fs = require('fs');
const path = require('path');
const content = fs.readFileSync('app/server.js', 'utf-8');
const match = content.match(/const _CANVAS_HTML_B64 = \"([^\"]+)\"/);
if (!match) { console.log('NOT FOUND'); process.exit(1); }
const b64 = match[1];
const decoded = Buffer.from(b64, 'base64').toString('utf-8');
console.log('Decoded length:', decoded.length);

// Write to canvas/ directory
const canvasDir = path.resolve('canvas');
if (!fs.existsSync(canvasDir)) fs.mkdirSync(canvasDir, {recursive:true});
fs.writeFileSync(path.join(canvasDir, 'canvas.html'), decoded, 'utf-8');
console.log('Saved to canvas/canvas.html');

// Also write to app/public/
const publicDir = path.resolve('app', 'public');
fs.writeFileSync(path.join(publicDir, 'canvas.html'), decoded, 'utf-8');
console.log('Saved to app/public/canvas.html');
"
```

### 3. Verify extraction

```bash
# Check file size matches original
ls -la canvas/canvas.html app/public/canvas.html

# The server still serves via /canvas route (embedded) — the extracted
# file is now also accessible via static routes like /canvas.html.
# Route ordering matters: if the dynamic /canvas route is checked before
# the static fallback, /canvas still serves the embedded version.
```

### 4. (Optional) Update server to serve static file instead

If you want the server to serve the extracted file instead of the embedded base64:

```js
// Remove or comment out the embedded route:
// if (req.method === "GET" && pathname === "/canvas") { ... }

// The static fallback at the bottom of the route handler will serve
// the file from public/ directory automatically:
// if (req.method === "GET") return serveStaticFile(req, res, PUBLIC_DIR, pathname);
```

### 5. Server restart requirement

After changing server.js (embedding or switching routes), restart the Node.js process for changes to take effect.

---

## Pitfalls

- **Large files**: base64 expands size ~1.37x. A 300KB HTML becomes ~410KB. Acceptable for single-file apps.
- **Cache headers**: Always set `Cache-Control: no-cache` so browser doesn't serve stale copies.
- **Restart required**: Any HTML change requires re-encode + server restart. Keep a dev copy outside public/ and re-embed at deploy time.
- **Not encrypted**: Base64 is encoding, not encryption. Anyone who reads server.js can decode. For true encryption, encrypt with a key from env var.
- **DevTools still shows code**: Once served, browser has the full HTML in memory. This prevents FILE THEFT (disk copy), not runtime inspection.
- **Two-file sync**: If you keep both an embedded version and a static fallback (for development), always sync edits to both files. The embedded static constant and the file on disk can diverge silently.
- **Extraction regex**: The `match(/const _CANVAS_HTML_B64 = \"([^\"]+)\"/)` regex assumes the base64 string uses double quotes (not template literals) and no escaped quotes inside. If the variable declaration format differs, adjust the regex accordingly.

## Defense layers

| Layer | Prevents | Weakness |
|-------|----------|----------|
| Remove from public/ | Direct URL access | Requires server restart on change |
| Embed in server.js | Disk copy | DevTools still shows runtime code |
| Server binds 127.0.0.1 | External network access | Same-machine access works |
| JS obfuscation | Readable DevTools code | Deobfuscation possible |

## Verification

```bash
# Verify embedded route works
curl -s -o /dev/null -w "%{http_code}" http://localhost:PORT/canvas
# expected: 200

# Verify static file route is dead (after removal)
curl -s -o /dev/null -w "%{http_code}" http://localhost:PORT/canvas.html
# expected: 404

# After extraction back to disk, static route returns 200 again
curl -s -o /dev/null -w "%{http_code}" http://localhost:PORT/canvas.html
# expected: 200 (after restoring canvas.html to public/)
```

## Part C: Hybrid mode (static file with embedded fallback)

For development workflows, combine both approaches — the server tries the static file first, then falls back to the embedded base64:

```js
if (req.method === "GET" && pathname === "/canvas") {
  res.setHeader("Cache-Control", "no-cache, no-store, must-revalidate");
  res.setHeader("Pragma", "no-cache");
  res.setHeader("Expires", "0");
  res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
  const canvasPath = path.join(PUBLIC_DIR, "canvas.html");
  if (fs.existsSync(canvasPath)) {
    res.end(fs.readFileSync(canvasPath, "utf-8"));
  } else {
    res.end(Buffer.from(_CANVAS_HTML_B64, "base64").toString("utf8"));
  }
  return;
}
```

### When to use hybrid mode

- **Development**: The static file exists in `public/`. Edit it directly — no re-encode step needed.
- **Deployment**: Remove the static file from `public/`. The embedded fallback kicks in automatically.
- **Both routes work simultaneously**: `/canvas` (dynamic route) serves the file; `/canvas.html` (static route) also works if the file exists in `public/`.

### Reverting to fully static

When you want full editability:

1. Extract the embedded HTML using the extraction method in Part B
2. Save it to `public/` directory
3. Update the route to serve the static file (with fallback as above — or just use the static fallback at the end of your route handler)

### Server restart

Any change to `server.js` (embedding, route switching, or modifying the fallback logic) requires a Node.js restart:

```bash
# Find and kill the old server
netstat -ano | grep PORT
taskkill -f -pid PROCESS_ID

# Start the new one
cd /path/to/app && node server.js
```

## Verification

```bash
# Verify embedded route works
curl -s -o /dev/null -w "%{http_code}" http://localhost:PORT/canvas
# expected: 200

# Verify static file route (only if file exists in public/)
curl -s -o /dev/null -w "%{http_code}" http://localhost:PORT/canvas.html
# expected: 200 (if file exists) or 404 (if removed)

# After extraction back to disk, static route returns 200 again
curl -s -o /dev/null -w "%{http_code}" http://localhost:PORT/canvas.html
# expected: 200 (after restoring canvas.html to public/)
```

## Related

- **kairos-canvas-development** skill — Kairos Canvas node development, rendering, and canvas data persistence
