---
name: "browser-llm-integration"
description: "Add OpenAI-compatible LLM API calls to vanilla HTML/JS browser applications. Covers fetch-based chat completions, URL path construction, parameter handling, settings persistence, and UI patterns for e"
priority: 0.5
imported-from: "agents"
source-path: "C:\\Users\\leohu\\.agents\\skills\\software-development\\browser-llm-integration\\SKILL.md"
---
# Browser LLM Integration

Integrate OpenAI-compatible LLM APIs into custom HTML/JS applications running in the browser.

## Quick Start

```js
// Minimal working call
var url = baseUrl.replace(/\/+$/,'') + '/v1/chat/completions';
fetch(url, {
  method: 'POST',
  headers: {'Authorization': 'Bearer '+apiKey, 'Content-Type': 'application/json'},
  body: JSON.stringify({model: modelName, messages: [{role:'user', content:msg}]})
})
.then(r => r.json())
.then(data => {
  var reply = data.choices[0].message.content;
});
```

## URL Path Construction

Users enter a "base URL" — handle multiple input formats:

```js
var base = llmConfig.baseUrl.replace(/\/+$/, '');
var url = base;
if (/\/chat\/completions$/i.test(base)) url = base;
else if (/\/v1$/i.test(base)) url = base + '/chat/completions';
else url = base + '/v1/chat/completions';
```

| User Input | Result |
|---|---|
| `https://api.openai.com` | `.../v1/chat/completions` |
| `https://api.deepseek.com/v1` | `.../v1/chat/completions` |
| `http://localhost:11434` | `.../v1/chat/completions` |
| `https://api.xxx.com/v1/chat/completions` | Used as-is |

## max_tokens Pitfall

Some models (e.g. DeepSeek `deepseek-v4-flash`) reject `max_tokens` entirely. Implement conditional inclusion + retry:

```js
var body = {model, messages, temperature};
var mt = parseInt(maxTokens);
if (!isNaN(mt) && mt > 0 && mt < 999999) body.max_tokens = mt;

// Retry on 400 with max_tokens error
fetch(url, {method:'POST', headers, body: JSON.stringify(body)})
.then(r => {
  if (!r.ok) return r.text().then(txt => {
    if (body.max_tokens && txt.indexOf('max_tokens') >= 0) {
      delete body.max_tokens;
      // retry without max_tokens
      return fetch(url, {method:'POST', headers, body: JSON.stringify(body)});
    }
    throw new Error('HTTP ' + r.status + ' - ' + txt);
  });
  return r.json();
})
```

## Paste Event Interception

Canvas apps often intercept `paste` globally with `e.preventDefault()`. This breaks pasting into config modals' input fields. Fix:

```js
document.addEventListener('paste', function(e) {
  var tag = document.activeElement && document.activeElement.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA') return;  // let native paste through
  var el = document.activeElement;
  while (el) { if (el.id === 'modalId') return; el = el.parentElement; }  // modal check
  e.preventDefault();
  // ... canvas paste handling ...
});
```

## Settings Panel Pattern

- Store API config in a separate `localStorage` key (e.g. `ic_llm`) — NOT mixed with app state
- Settings modal: overlay with form fields for baseUrl, apiKey, model, systemPrompt, temperature, maxTokens
- On open: load from localStorage and populate fields
- On save: read fields, validate, write to localStorage, close modal
- Include LLM config in export/import (embed in exported JSON)

## Flexbox Node Layout

When building custom node UIs with a textarea + action footer:

```css
.node-body { flex: 1; display: flex; flex-direction: column; }
.node-body textarea { flex: 1; }
.node-foot { flex-shrink: 0; display: flex; align-items: center; }
.node-foot .status { flex: 1; }   /* pushes button to right */
```

The wrapping container (`.ndb` or equivalent) must use `flex-direction: column` or wrap body+foot in a sub-container that does. Default is `row` which causes body and foot to appear side by side.

**Wrapper pattern (most reliable):**
```html
<div class="ndb">  <!-- parent: display:flex (default row) -->
  <div class="node-wrap" style="flex:1;display:flex;flex-direction:column;overflow:hidden">
    <div class="node-body" style="flex:1"><textarea ...></textarea></div>
    <div class="node-foot" style="flex-shrink:0;display:flex;align-items:center">
      <span class="status" style="flex:1">● 就绪</span>
      <button>▶ 发送</button>
    </div>
  </div>
</div>
```

The `.node-wrap` inner div creates a column flow INSIDE the row-oriented parent, so the textarea fills vertical space and the footer sits at the bottom.

## Response Node Overwrite Pattern (Canvas/Node UIs)

When a user can click "send" multiple times on the same LLM node, avoid stacking duplicate response nodes. Instead, overwrite the existing one:

**Store `respId` on the LLM node object:**

```js
// First send: create node + store reference
var respId = id++;
var rn2 = {id: respId, x: llmNode.x + llmNode.w + 40, y: llmNode.y, ...};
N.push(rn2);
// ... render node ...
C.push({id: 'c_'+(id++), from: llmNode.id, to: respId});
llmNode.respId = respId;  // ← critical: direct reference on source node

// Subsequent sends: check and overwrite
if (llmNode.respId) {
  var existingNode = gi(llmNode.respId);
  if (existingNode) {
    existingNode.content = reply;
    // Update DOM: textarea value and title
    var el = document.getElementById('n' + existingNode.id);
    if (el) {
      var ta = el.querySelector('.ndb textarea');
      if (ta) ta.value = reply;
    }
    sl(existingNode.id); rd(); sv2();
    return;  // done — no new node created
  }
}
// If no existing node (deleted, or first time), create new one
```

**Persistence:** Custom properties like `respId` must be explicitly included in all serialization paths:
- `saveState()` (undo stack)
- `sv2()` (localStorage auto-save)
- `svf()` (HTML export/embed)

## Error Display

Show errors directly in the node UI rather than alerts:

```js
var st = document.getElementById('status_' + nid);
st.textContent = '错误: ' + err.message;
st.className = 'status err';  // red coloring via CSS
```

Include the actual request body in error messages for debugging:
```js
throw new Error('HTTP ' + status + ' - ' + responseText + ' (sent: ' + JSON.stringify(body).slice(0,200) + ')');
```

## File Upload in LLM Nodes

Support uploading `.txt`, `.md`, `.docx`, `.pdf` into the LLM node. Two UX patterns:

### Pattern A: File content into textarea (simple)

Put file content directly into the LLM node's textarea so the user can edit before sending.

### Pattern B: File as link (node apps, recommended for canvas UIs)

Store file content on the node object, show a filename bar, and **prepend to the message on send** — keeps the textarea clean.

**Node data storage:**
```js
// On the LLM node object:
n.fileName = file.name;      // original filename
n.fileContent = extractedText;  // parsed text content
```

**Display: show a file bar between textarea and footer:**
```html
<div class="file-bar">
  <span>📄 filename.txt</span>
  <button onclick="removeFile(nodeId)">✕</button>
</div>
```

Render conditionally based on `n.fileName`:
```js
var fileHtml = n.fileName
  ? '<div class="file-bar" id="fb_'+n.id+'"><span>📄 '+es(n.fileName)+'</span>'+
    '<button onclick="rmLLMFile('+n.id+')">✕</button></div>'
  : '';
```

**Prepend on send:**
```js
var sendMsg = msg;  // user's textarea content
if (n.fileName && n.fileContent) {
  sendMsg = '以下是文件「' + n.fileName + '」的内容：\n---\n'
          + n.fileContent + '\n---\n\n' + msg;
}
```

**Remove file function:**
```js
function rmLLMFile(nid) {
  var n = gi(nid);
  if (!n) return;
  n.fileName = null;
  n.fileContent = null;
  rn(n);  // re-render to hide file bar
}
```

**Persistence:** Include `fileName` and `fileContent` in all serialization paths (saveState, sv2, export).

### Hidden file input

```html
<input type="file" id="llm_file_input" accept=".txt,.md,.docx,.pdf" style="display:none"
       onchange="handleLLMFile(this.files)">
```

### Trigger + handler

```js
var _llmUploadTarget = null;

function pickLLMFile(nid) {
  _llmUploadTarget = nid;
  var inp = document.getElementById('llm_file_input');
  inp.value = '';  // allow re-selecting same file
  inp.click();
}
```

Use a shared `done(content)` callback that stores on the node:

```js
function handleLLMFile(files) {
  if (!files || !files[0] || !_llmUploadTarget) return;
  var file = files[0], name = file.name.toLowerCase(), nid = _llmUploadTarget;
  var n = gi(nid); if (!n) return;
  var st = document.getElementById('status_' + nid);
  if (st) { st.textContent = '读取中...'; st.className = 'status busy'; }

  function done(content) {
    n.fileName = file.name;
    n.fileContent = content;
    rn(n); rd(); sv2();  // re-render node to show file bar
    if (st) { st.textContent = '就绪'; st.className = 'status'; }
    tm('文件已载入: ' + file.name);
  }

  if (name.endsWith('.txt') || name.endsWith('.md')) {
    var r = new FileReader();
    r.onload = function(e) { done(e.target.result); };
    r.readAsText(file);

  } else if (name.endsWith('.docx')) {
    loadScript('https://cdnjs.cloudflare.com/ajax/libs/mammoth/1.6.0/mammoth.browser.min.js', function() {
      var r = new FileReader();
      r.onload = function(e) {
        mammoth.extractRawText({ arrayBuffer: r.result })
          .then(function(res) { done(res.value); })
          .catch(function() { /* show error */ });
      };
      r.readAsArrayBuffer(file);
    }, function() { /* CDN load failed */ });

  } else if (name.endsWith('.pdf')) {
    loadScript('https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js', function() {
      pdfjsLib.GlobalWorkerOptions.workerSrc =
        'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js';
      var r = new FileReader();
      r.onload = function(e) {
        pdfjsLib.getDocument({ data: r.result }).promise.then(function(pdf) {
          var texts = [];
          (function lp(i) {
            if (i > pdf.numPages) { done(texts.join('\n\n')); return; }
            pdf.getPage(i).then(function(p) { return p.getTextContent(); })
              .then(function(tc) {
                texts.push(tc.items.map(function(it) { return it.str; }).join(' '));
                lp(i + 1);
              });
          })(1);
        }).catch(function() { /* show error */ });
      };
      r.readAsArrayBuffer(file);
    }, function() { /* CDN load failed */ });
  }
}
```

### Dynamic script loader

```js
function loadScript(url, ok, fail) {
  var s = document.createElement('script');
  s.src = url;
  s.onload = ok;
  s.onerror = fail || function() {};
  document.head.appendChild(s);
}
```

### Upload button styling

For canvas node UIs, match the upload button style to the send button:

```css
.nd-llm-upbtn {
  padding: 8px 14px; background: #000; color: #fff; border: none;
  border-radius: 6px; font-size: 13px; cursor: pointer; font-family: inherit;
  flex-shrink: 0;
}
.nd-llm-upbtn:hover { background: #333; }
```

## Markdown Response Rendering

When LLM responses contain structured content (tables, code, formatting), render as HTML instead of a plain textarea. Create a dedicated node type (e.g. `llm-resp`) that displays formatted content.

### Node type registration in `rn()`

```js
} else if (n.type === 'llm-resp') {
  b = '<div class="nd-md">' + mdToHtml(n.content || '') + '</div>';
}
```

### Markdown-to-HTML with Placeholder Protection

The key technique: extract tables (and code blocks) into placeholders FIRST, process text formatting (bold, italic, line breaks) on the remaining content, then restore the placeholders LAST. This prevents `<br>` and `<p>` tags from corrupting table HTML.

```js
function mdToHtml(text) {
  // 1. HTML-escape
  text = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  // 2. Extract code blocks into placeholders
  var codes = [];
  text = text.replace(/```([\s\S]*?)```/g, function(m, c) {
    var i = codes.length; codes.push(c); return '\x00CODE' + i + '\x00';
  });

  // 3. Extract tables into placeholders
  var tables = [];
  text = text.replace(/((?:^|\n)[ \t]*\|[ \t]*[^\n|]+[ \t]*\|[^\n]*(?:\n[ \t]*\|[ \t]*[^\n|]+[ \t]*\|[^\n]*)*)/g, function(m) {
    var idx = tables.length;
    var html = parseTable(m);  // see below
    tables.push(html);
    return '\x00TABLE' + idx + '\x00';
  });

  // 4. Restore code blocks
  for (var i = 0; i < codes.length; i++)
    text = text.replace('\x00CODE' + i + '\x00', '<pre><code>' + codes[i] + '</code></pre>');

  // 5. Inline formatting (safe — no tables to corrupt)
  text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
  text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  text = text.replace(/\*([^*]+)\*/g, '<em>$1</em>');

  // 6. Line breaks
  text = text.replace(/\n\n/g, '</p><p>');
  text = text.replace(/\n/g, '<br>');

  // 7. Restore tables LAST
  for (var i = 0; i < tables.length; i++)
    text = text.replace('\x00TABLE' + i + '\x00', tables[i]);

  return '<p>' + text + '</p>';
}
```

### Table parser: handle multiple formats

```js
function parseTable(block) {
  var rows = block.split('\n');
  // Pre-scan: find separator line (---|---|---)
  var sepRow = -1;
  for (var ri = 0; ri < rows.length; ri++) {
    var ln = cleanPipeLine(rows[ri]);
    var cells = ln.split('|').map(t => t.trim()).filter(c => c !== '');
    if (cells.length && cells.every(c => /^[-:\s]+$/.test(c))) { sepRow = ri; break; }
  }
  var html = '<table>';
  for (var ri2 = 0; ri2 < rows.length; ri2++) {
    if (ri2 === sepRow) continue;
    var ln2 = cleanPipeLine(rows[ri2]);
    var cells2 = ln2.split('|').map(t => t.trim()).filter(c => c !== '');
    if (!cells2.length) continue;
    var isHd = sepRow < 0 ? ri2 === 0 : ri2 < sepRow;
    html += '<tr>';
    for (var ci = 0; ci < cells2.length; ci++) {
      var cc = cells2[ci]
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        .replace(/\*([^*]+)\*/g, '<em>$1</em>');
      html += '<' + (isHd ? 'th' : 'td') + '>' + cc + '</' + (isHd ? 'th' : 'td') + '>';
    }
    html += '</tr>';
  }
  return html + '</table>';
}

function cleanPipeLine(line) {
  var l = line.trim();
  if (l.charAt(0) === '|') l = l.substring(1);
  if (l.charAt(l.length - 1) === '|') l = l.substring(0, l.length - 1);
  return l;
}
```

### Table CSS (Excel-like grid)

```css
.nd-md table { border-collapse: collapse; margin: 6px 0; width: 100%; font-size: 12px; }
.nd-md td, .nd-md th { border: 1px solid #999; padding: 4px 8px; text-align: left; white-space: nowrap; }
.nd-md th { background: #e8e8e8; font-weight: 600; border-color: #888; }
.nd-md tr:nth-child(even) { background: #f8f8f8; }
.nd-md code { background: #f0f0f0; padding: 1px 5px; border-radius: 3px; font-size: 12px; }
.nd-md pre { background: #f5f5f5; padding: 8px; border-radius: 4px; overflow-x: auto; }
```

### Table formats handled

| Input format | Support | Example |
|---|---|---|
| `\| H1 \| H2 \|` (standard) | ✅ | Standard markdown |
| `  \| H1 \| H2 \|` (leading spaces) | ✅ | Regex allows `[ \t]*` |
| `H1 \| H2` (no outer pipes) | ✅ | Cleaned by `cleanPipeLine` |
| `\|1\| \|2\|` (individual pipe-wrapped cells) | ✅ | `filter(c => c !== '')` removes empties |
| No `---|---` separator | ✅ | First row = `<th>`, rest = `<td>` |
| Multiple tables in one response | ✅ | Each extracted independently via placeholders |

### Response node overwrite for repeated sends

When re-sending from the same LLM node, overwrite the existing response node instead of stacking new ones. Store `respId` on the source node:

```js
// Create: store reference
n.respId = respId;

// Overwrite: use reference
if (n.respId) {
  var existing = gi(n.respId);
  if (existing) {
    existing.content = reply;
    rn(existing);  // full re-render (handles both textarea and HTML types)
    sl(existing.id); rd(); sv2();
  }
}
```

For HTML response nodes (`llm-resp`), `rn()` must be used for overwrites — updating a textarea's `.value` won't work since the node uses a `.nd-md` div with `innerHTML`.

## Footer Layout Variants

### Right-aligned button (flex: 1 on status)
```css
.foot { display: flex; align-items: center; padding: 6px 10px; }
.status { flex: 1; }  /* pushes button to right */
```
Result: `[● 就绪]                            [▶ 发送]`

### Centered layout (justify-content: center)
```css
.foot { display: flex; align-items: center; justify-content: center; gap: 6px; }
```
Result: `      [● 就绪] [📎] [▶ 发送] model-name      `

The centered variant works well when adding extra elements (upload button, model label) alongside the send button.

## Model Name Label

After the send button, show the configured model name:

```html
<span class="model-label" id="model_${nodeId}">${llmConfig.model}</span>
```

```css
.model-label {
  font-size: 11px; color: #aaa; max-width: 120px;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
```

Refresh all labels when settings are saved:

```js
function saveLLMSettings() {
  // ... save to localStorage ...
  for (var i = 0; i < N.length; i++) {
    if (N[i].type === 'llm') {
      var el = document.getElementById('model_' + N[i].id);
      if (el) el.textContent = llmConfig.model;
    }
  }
}
```

## Status classes
- `.busy` (orange) — request in progress
- `.done` (green) — completed
- `.err` (red) — error

## "OpenAI-Compatible" ≠ Standard Format (Critical Pitfall)

When a user says "use OpenAI-compatible API" or "OpenAI protocol", DO NOT assume the standard `/v1/chat/completions` request shape. Providers labeled "OpenAI-compatible" vary significantly:

- **Request format**: Some use `messages[]`, others use `content[]` with `role` fields, others use flat `prompt` + `image_urls[]`
- **Image handling**: Some accept base64 data URIs directly; others require uploading to an asset library first and referencing by ID (`asset://xxx`)
- **Polling paths**: `/v1/video/generations/:id` vs `/v1/tasks/:id` vs `/v1/videos/generations/:id` — guessing wastes time
- **Response nesting**: `content.video_url` vs `data.video_url` vs `output.video_url` vs `url`
- **Status values**: `queued/running/succeeded/failed` vs `pending/processing/completed/error`

**Rule**: Always fetch and read the actual API docs before writing integration code. If the user provides docs, verify your implementation against them line by line. See `references/uptoken-media-api.md` for a worked example.

## Flask Backend Proxy Pattern (API Key Security)

For browser-based tools that call paid APIs, route through a Flask backend instead of calling from JS — keeps API keys server-side:

```
Browser → POST /api/generate → Flask backend → POST to external API
                         ← poll /api/poll/:id ← GET /v1/.../:task_id
```

Server-side `config.json` stores `api_base`, `api_key`, `model`. Frontend reads non-sensitive config via `GET /api/config` (omits key, sends `has_key: bool`).

## Async Media Generation Pattern

Video/image generation APIs are async — submit task, poll for result:

1. **Upload**: POST asset to library → get `asset://id` URL
2. **Submit**: POST generation request with asset URL → get `task_id`
3. **Poll**: GET status every 5s → check `status` field → on `succeeded`, extract media URL
4. **Timeout**: Set max poll time (e.g. 2h for video generation)

Frontend shows spinner + elapsed time during poll. Log each poll cycle for debugging.

## Startup Ordering: Load Config Before Rendering

When using `loadLLMConfig()` to restore saved settings (model name, API key, etc.), call it **before** rendering any nodes. Otherwise node content (model labels, file bars) will render with stale defaults:

```js
loadLLMConfig();       // ← first: populate llmConfig from localStorage
// then: load canvas data and render nodes
```

If embedded HTML export data contains a `d.llm` config, apply it before `loadLLMConfig()`:

```js
if (d.llm) { for (var k in d.llm) llmConfig[k] = d.llm[k]; }
loadLLMConfig();
for (var i = 0; i < N.length; i++) rn(N[i]);  // render with correct config
```

## The IIFE Rendering Trap (Critical)

In canvas/node UIs, `rn()` handles ALL node rendering. But when creating a new response node from an API callback, the code often uses an **IIFE** that duplicates `rn()`'s DOM construction:

```js
// In API callback:
var rn2 = {id: respId, type: 'llm-resp', ...};
N.push(rn2);
(function(rnd) {
  var el = document.createElement('div');
  el.innerHTML = '...<div class="ndb"><textarea>...</textarea></div>...';
  w.appendChild(el);
})(rn2);
```

The IIFE's hardcoded innerHTML silently diverges from `rn()`. Every feature added to `rn()` (tables, file bars, model labels) breaks new-node creation.

**Fix:** Call `rn()` directly instead of duplicating logic:

```js
N.push(rn2);
rn(rn2);  // ← single source of truth
sl(rn2.id); rd(); uh(); sv2();
```

Or, if IIFE is unavoidable, paste a comment block and duplicate the logic verbatim. Verify both paths after every `rn()` change.

## `.ndb overflow:hidden` Pitfall

The node body container (`.ndb`) often has `overflow: hidden` for drag-connect behavior. This clips child overflow, including wide tables and scrollbars.

**Fix:** Inner content containers should use `overflow: auto` for both axes, and tables should use `width: auto` (not `100%`) to expand to natural width:

```css
.nd-md { flex: 1; overflow: auto; }
.nd-md table { width: auto; }
```

## Simpler Inline Table Detection (Alternative)

For script/storyboard content with individual `|X|` wrapped cells, try a simpler line-by-line scan directly in `rn()`:

```js
// In rn(), for llm-resp type:
var lines = content.split('\n'), pipeCount = 0;
for (var i = 0; i < lines.length; i++)
  if (lines[i].trim().indexOf('|') >= 0) pipeCount++;

if (pipeCount >= 2) {
  var html = '<table>', isHeader = false;
  for (var ri = 0; ri < lines.length; ri++) {
    var rw = lines[ri].trim();
    if (rw.indexOf('|') < 0) continue;
    if (rw.charAt(0) === '|') rw = rw.substring(1);
    if (rw.charAt(rw.length - 1) === '|') rw = rw.substring(0, rw.length - 1);
    var cells = rw.split('|').map(s => s.trim()).filter(s => s !== '');
    if (!cells.length) continue;
    if (cells.every(s => /^[-:\s]+$/.test(s))) continue;
    html += '<tr>';
    for (var cj = 0; cj < cells.length; cj++)
      html += '<' + (isHeader ? 'td' : 'th') + '>' + cells[cj] + '</' + (isHeader ? 'td' : 'th') + '>';
    html += '</tr>';
    isHeader = true;
  }
  b = html + '</table>';
} else {
  b = '<p>' + es(content).replace(/\n/g, '<br>') + '</p>';
}
```

Trade-off: no inline bold/italic inside cells. Use the placeholder approach when formatting inside cells is needed.
