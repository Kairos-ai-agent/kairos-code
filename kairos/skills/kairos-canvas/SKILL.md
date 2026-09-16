---
name: "kairos-canvas"
description: "Build and maintain Kairos Canvas — an infinite canvas SPA (single HTML) with node-based visual workflows for AI content generation (LLM, image, video, TTS, storyboarding). Use when modifying Kairos Ca"
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/creative/kairos-canvas/SKILL.md"
---
# Kairos Canvas — Consolidated Skill

This is the unified skill for all Kairos Canvas development work. It combines:
- **Canvas architecture & state** (from kairos-canvas)
- **Infinite canvas dev guidelines** (from infinite-canvas-dev) — file paths, variant differences, asset library
- **Development workflow** (from infinite-canvas-development) — adding node types, TTS, assistant, debugging patterns
- **Style management** (`skill_view(name='kairos-canvas', file_path='references/style-management.md')`) — adding visual styles to the standalone HTML file

## Kairos Canvas Architecture

Single-file SPA at `D:\\AI视频号\\GaiaNetworkTester\\app\\public\\canvas.html` (~3600 lines, ~250KB). Also mirrored to `D:\\AI视频号\\GaiaNetworkTester\\canvas\\canvas.html`. Light theme. Node sizes: video-gen 450x200, others 320x200.

## Core State

```javascript
var S = {
  nodes: [],      // Array of node objects
  conns: [],      // Array of {from: nodeId, to: nodeId}
  sel: [],        // Selected node IDs
  nid: 1,         // Next node ID counter
  zoom: 1, px: 0, py: 0,  // View transform
  cfg: { llm: {...}, img: {...}, vid: {...}, tts: {...}, style: '' }
};
```

## Node Object Shape

```javascript
{
  id: 'n1', type: 'llm', x: 100, y: 200, w: 320, h: 300,
  title: 'LLM Chat', content: 'prompt text', response: '',
  meta: { img: 'url', vid: 'url', audio: 'data:...', ttsVoice: '冰糖', ... }
}
```

## Key Functions

| Function | Purpose |
|----------|---------|
| `addNode(type, x, y, data, skipOverlap)` | Create + render + position node |
| `renderNode(nd)` | Build node DOM, attach event listeners |
| `execN(id, action)` | Execute node's generation task |
| `delN(id)` | Delete node + connections |
| `find(id)` | Lookup node by ID |
| `s2c(x, y)` | Screen coords → canvas coords |
| `updConns()` | Redraw SVG connection lines |
| `saveCvs()` / `loadCvs()` | localStorage persistence |
| `fitView()` / `resetZ()` | View controls |

## Node Rendering Pattern

In `renderNode(nd)`:
1. Build `body` HTML string based on `nd.type`
2. `foot` = generic execute button (excluded for types with custom buttons)
3. `el.innerHTML = ports + header + nbd(body) + foot`
4. Attach event listeners (textarea input, button clicks, TTS controls)
5. **Always use `e.stopPropagation()`** on interactive elements to prevent canvas pan/drag

## Adding a New Node Type

1. Add to `ICONS`, `TNAMES`, `dW()`, `dH()`, `dT()` maps
2. Add rendering case in `renderNode()` body-building section
3. Add execution case in `execN()` switch
4. Add to context menu HTML (`#cvm`) and/or toolbar
5. If it has custom buttons, exclude from `foot` generation (line ~944)
6. Add CSS for node-specific elements

### Bottom-Button Positioning (CRITICAL)

When a node type needs action buttons at the bottom (like 导入剧本's 优化/一键分集/提取资产, or 剧本创作's 生成剧本):

**DO: Put buttons in the `body` variable as a dedicated footer div inside `nbd`:**
```javascript
body="<div class='my-body'><textarea>...</textarea></div>"+
  "<div class='my-footer'><button class='nbtn'>Action</button></div>";
```
Then set CSS:
```css
.node[data-type='mytype'] .nbd{display:flex;flex-direction:column;padding:0;overflow:hidden;min-height:0;flex:1}
.my-body{flex:1;display:flex;flex-direction:column;min-height:0}
.my-body textarea{flex:1;...}
.my-footer{display:flex;align-items:center;justify-content:flex-end;padding:4px 8px;border-top:1px solid rgba(0,0,0,0.08);flex-shrink:0}
```

**DON'T:**
- ❌ Use `position:absolute` on the button — it doesn't follow node resizing and creates spacing headaches
- ❌ Put buttons in the `foot`/`nft` variable (the default `.nft` has `padding:6px 8px` and `justify-content:center` that needs overriding)
- ❌ Put the button inside the textarea's container with absolute positioning

- 任何面板DOM相关函数都要在 IIFE 中加 null 检查。**面板HTML必须放在 `<script>` 之前**，否则 `getElementById` 返回 null，TypeError 中断整个脚本让工具栏失效。详见 `references/image-node-inline-controls.md` — 图像节点内联控件（手动模式，替代浮动面板方案）。

## 为节点添加手动模式浮动面板（步骤化模式）

当需要为新的节点类型在手动模式下添加浮动面板时（如图片节点 igFloatingPanel、视频节点 vgFloatingPanel），按以下步骤：

### 步骤 1: 面板 HTML
在 `</div>`（资产库面板末尾）和 `<script>` 之间添加面板元素。**面板HTML必须在 `<script>` 之前**，否则 getElementById 返回 null。

```html
<div id="ntFloatingPanel" class="ig-fp-overlay" style="width:420px">
  <div class="ig-fp-header">
    <span style="font-size:13px;font-weight:600;color:#333">🎬 标题</span>
    <button class="ig-fp-close" id="ntFpClose" style="display:none">✕</button>
  </div>
  <textarea class="ig-fp-prompt" id="ntFpPrompt" placeholder="描述..."></textarea>
  <button class="ig-fp-genbtn" id="ntFpGenBtn" style="margin:0;border-radius:0;width:100%">✨ 生成</button>
  <div class="ig-fp-config" style="display:flex;align-items:center;gap:6px;padding:6px 10px;border-top:1px solid #eee;flex-wrap:wrap;flex-shrink:0">
    <!-- 底部紧凑参数栏：模型、尺寸、时长、分辨率等 -->
  </div>
</div>
```

### 步骤 2: 绑定函数
```javascript
var _ntFpNode=null;
function toggleNtFloatingPanel(nd,el){
  var panel=document.getElementById("ntFloatingPanel");
  if(!panel)return;
  if(panel.classList.contains("show")&&_ntFpNode===nd)return;
  _ntFpNode=nd;
  if(panel.parentNode)panel.parentNode.removeChild(panel);
  el.appendChild(panel);
  panel.classList.add("bound","show");
  var pta=panel.querySelector(".ig-fp-prompt");
  if(pta)pta.value=nd.content||"";
  // 恢复保存的 nd.meta._ntParams
}
```

### 步骤 3: renderNode 中绑定点击
```javascript
if(nd.type==="yourtype"){
  if(canvasMode==="manual"){
    setTimeout(function(){toggleNtFloatingPanel(nd,el);},100);
    el.addEventListener("click",function(e){
      if(canvasMode==="manual"&&!e.target.closest(".ig-fp-overlay"))toggleNtFloatingPanel(nd,el);
    });
  }
}
```

### 步骤 4: 节点 body — 手动模式只显示预览（无 textarea、无"点击生成"）
```javascript
else if(nd.type==="yourtype"){
  var ph=nd.meta.result?"<video/img src='...'>":"";
  if(canvasMode==="manual"){
    body="<div class='preview' style='flex:1;min-height:0'>"+ph+"</div>";
  }else{
    body="<textarea>"+escHtml(nd.content)+"</textarea><div class='preview'>"+ph+"</div>";
  }
}
```

### 步骤 5: CSS
```css
.node[data-type='yourtype'] .nbd{display:flex;flex-direction:column;overflow:hidden}
```

### 步骤 6: auto-open 函数
```javascript
function autoOpenYpPanel(){
  if(canvasMode!=="manual")return;
  var panel=document.getElementById("ntFloatingPanel");
  if(!panel||panel.classList.contains("show"))return;
  var node=document.querySelector("#canvas-manual .node[data-type='yourtype']");
  if(!node)return;
  var nd=find(node.id);if(!nd)return;
  toggleNtFloatingPanel(nd,node);
}
```
在 `switchCanvasMode()` 的 `autoOpenIgPanel()` 之后，以及页面加载代码的 `autoOpenIgPanel()` 之后调用。

### 步骤 7: applyModeTheme 关闭
确保故事板分支中已有对应面板的关闭代码（如 igFloatingPanel 和 vgFloatingPanel 的关闭处理）。

### 步骤 8: 生成按钮
保存参数到 `nd.meta._ntParams` 并调用生成函数。

**⚠️ 模型下拉框常见遗漏：** 面板中如有模型 `<select>`，务必：
1. 将 `model: panel.querySelector("#ntFpModel").value` 保存到 `_ntParams`
2. 生成函数中用 `params.model || s.model` 替代硬编码 `s.model`
3. 设置 `ms.value=curModel` 自动选中当前模型（在 populate 函数末尾）
4. 添加 `refreshNtFpModel()` 并在 `saveSet()` 末尾调用，使设置保存后刷新

详见 `references/floating-panel-model-pitfall.md`。

### 已实现的示例
- **Image**: `igFloatingPanel`/`toggleIgFloatingPanel`/`autoOpenIgPanel` (~line 834/2302/5702)
- **Video**: `vgFloatingPanel`/`toggleVgFloatingPanel`/`autoOpenVgPanel` (~line 878/5944/971)

## 视频提示词时间轴格式

`references/frame-mode-video-prompt-architecture.md` — 所有视频提示词（meta.video 节点）遵守以下规则：
- **以时间轴分段开头**，按总时长均分段落
- **三种模式**：首帧图模式（isSingleFrame）、故事板模式（isSBVp）、首尾帧模式（else）
- **Grok** → 英文 Motion+Camera+Atmosphere+Audio，跳过 TTS 引用
- **Seedance** → 中文+四维编码+@图片引用+@音频N 角色TTS引用
- **首帧图单图规则**：exGenSBImg 中跳过资产参考图收集
- **角色TTS绑定**：通过角色资产节点 → 绑定的 char-tts 查找
- Grok 只须首帧单图（不要求尾帧），跳过角色TTS音频引用（原生AUDIO块）
- 代码入口：`exGenVideoPrompt()`

### IIFE Scope — Critical Pattern for New Functions

All floating panel functions (`toggleNtFloatingPanel`, helper functions, event handlers) are defined **inside the ENHANCEMENTS V2 IIFE** (starts at `// ====== ENHANCEMENTS V2 ======`). But `renderNode()` is wrapped/replaced by this IIFE — the ORIGINAL `renderNode` is saved as `origRender` and called first. The original `renderNode` (at global scope) contains the `setTimeout` and click-event calls to `toggleNtFloatingPanel`.

**Problem:** Functions defined inside the IIFE are NOT visible to the setTimeout callback in `origRender` (which runs in the global scope, not the IIFE scope). The callback calls an undefined function silently.

**Solution — Expose on `window`:** Right after the function definition inside the IIFE:
```javascript
// Inside ENHANCEMENTS V2 IIFE:
function toggleVgFloatingPanel(nd,el){ ... }
// MUST expose for renderNode's setTimeout callback:
window.toggleVgFloatingPanel=toggleVgFloatingPanel;
```

This matches the existing pattern for the image panel at line ~5802:
```javascript
window.toggleIgFloatingPanel=toggleIgFloatingPanel;
```

**Pitfall:** Forgetting `window.` exposure is the #1 cause of "面板不弹出" bugs. The function IS in the HTML source, the code IS correct, but it's trapped in the IIFE scope. Always add `window.` exposure immediately after any new toggle function definition.

### Panel Flex Layout — Header/RefBar/Textarea/Config/GenBtn

All floating panels now use a consistent flex column layout:

```css
.ig-fp-overlay{display:flex;flex-direction:column}
/* Fixed sections — do not stretch */
.ig-fp-header{flex-shrink:0}
.ig-fp-toolbar{flex-shrink:0}
.vg-ref-bar{flex-shrink:0}
.ig-fp-ref-bar{flex-shrink:0}
.ig-fp-config{flex-shrink:0}
.ig-fp-genbtn{flex-shrink:0}
/* Stretching section — fills remaining space */
.ig-fp-prompt{flex:1;min-height:60px;resize:none}
```

Order: Header → (Toolbar/RefBar) → Textarea → (Config) → GenBtn → Resize handles

**Generate button at VERY bottom** — Use inline `style="margin:0;border-radius:0;width:100%"` to make it edge-to-edge full width.

### @ Mention Autocomplete in Textarea

When a textarea needs `@`-triggered asset selection:

1. **HTML:** Add a positioned dropdown div after the panel element:
```html
<div class="vg-mention" id="vgMention"></div>
```

2. **Detection logic** — Must handle Chinese characters (not just spaces):
```javascript
// ❌ WRONG — Chinese text before @ makes word start at beginning
while(start>=0&&val[start]!==" "&&val[start]!=="\n"&&val[start]!=="\t"){start--;}
var word=val.substring(start+1,pos); // "小柯基@" — word[0] is Chinese, not @

// ✅ CORRECT — stop at @ as delimiter
while(start>=0&&val[start]!==" "&&val[start]!=="\n"&&val[start]!=="\t"&&val[start]!=="@"){start--;}
var word=val.substring(start,pos); // "@" — word[0] is @
_atStart=start; // position of @ (not start+1)
```

3. **Source of assets** — Can be from `_vgRefs` (current panel's reference bar) or `window.getAllAssets()` (global asset library).

4. **Bind inside toggle function** — Use `panel._atBound` flag to bind once:
```javascript
if(!panel._atBound){
  panel._atBound=true;
  var _ta=panel.querySelector("#vgFpPrompt"),_dd=document.querySelector("#vgMention");
  // bind input/keydown/blur events to _ta, populate _dd with matched assets
}
```

### Panel Resize Handles (Border Dragging)

Add 8 resize handles to any `.ig-fp-overlay` panel:

**CSS:**
```css
.ig-fp-rh{position:absolute;z-index:10;background:transparent}
.ig-fp-rh:hover,.ig-fp-rh.act{background:rgba(99,102,241,0.15)}
.ig-fp-rh.nw{top:-4px;left:-4px;width:12px;height:12px;cursor:nwse-resize}
.ig-fp-rh.n{top:-4px;left:16px;right:16px;height:8px;cursor:ns-resize}
/* repeat for ne, e, se, s, sw, w */
```

**HTML** (inside panel div, as last children):
```html
<div class="ig-fp-rh nw" data-fp-rh="nw"></div>
<div class="ig-fp-rh n" data-fp-rh="n"></div>
<!-- ne, e, se, s, sw, w -->
```

**JS** (shared delegated handler listening on document mousedown):
Updates `panel.style.width`/`height` with direction-aware deltas, clamped at `Math.max(320,newVal)`.

### Reference Asset System (Video Panel)

```javascript
var _vgRefs=[]; // [{type:'image'|'audio', data:'data:...', name:'...'}]

function vgAddRef(type,data,name){
  _vgRefs.push({type,data,name});
  vgRenderRefs();
  if(_vgFpNode){_vgFpNode.meta=_vgFpNode.meta||{};_vgFpNode.meta._vgRefs=_vgRefs.slice();}
}
function vgSyncConnectedRefs(nd){
  // find image nodes connected TO this video node, auto-add their images
}
```

**Save/restore** in `toggleVgFloatingPanel`:
```javascript
_vgRefs=[];
if(nd.meta&&nd.meta._vgRefs){_vgRefs=nd.meta._vgRefs.slice();}
vgRenderRefs();
vgSyncConnectedRefs(nd);
```

### Seedance API — Mode Selection

Three modes with different content array construction. Mode is stored in `nd.meta._vgMode`.

| Mode | Text includes `--duration` flags | Ref images | Seedance 1.5 |
|------|-----------|------------|-------------|
| `text` | ✅ | None | ✅ |
| `ref` | ❌ (plain prompt) | In `content` array | ❌ |
| `frame` | ✅ | In `content` array | ❌ |

**Pitfall:** Seedance 1.5 rejects text+image_url in the same `content` array with "first/last frame content cannot be mixed with reference media content." Use Seedance 2.0 or a different API for ref/frame modes.

### 改动后同步
`app/public/Kairos_canvas.html` → `canvas/canvas.html`

## Canvas Assistant (AI Chat)

- System prompt: `_SYS_PROMPT` — defines all JSON commands
- Commands: `_CMD_HANDLERS` object with sync/async handlers
- Message flow: user → `_sendMsg()` → API → parse commands → execute → `_followUp()` for results
- **Critical**: After command execution, results are fed back to LLM via `_followUp()` so it can answer based on results
- Think filtering: `_stripThink()` removes `</think> blocks (streaming-safe)
- **API URL pitfall**: `_sendMsg()` uses `cfg.url` directly from `S.cfg.llm`. Unlike other LLM call sites, it was NOT using `fixLLMUrl()`. If the user entered a base URL without `/v1/chat/completions`, the assistant silently failed. **Always wrap with `fixLLMUrl(cfg.url)`** in the `_sendMsg` fetch call.

## Asset Library (IndexedDB)

- DB: `KairosAssets`, store: `assets`, key: `id`
- Asset shape: `{ id, name, type, data, created }`
- Types: `text`, `image`, `video`, `audio`
- Data stored as dataURL (NOT blob URL) for API compatibility
- Auto-save: generated images/video/audio automatically saved via `addAssetToLib()`
- Sidebar: toggle with toolbar button, filter by type, drag-to-canvas, click-to-add
- **Pitfall**: blob URLs (`blob:http://...`) don't work with APIs. Always use `FileReader.readAsDataURL()` to get `data:audio/wav;base64,...` format.

## TTS Node Architecture

Four model types with different UIs and API behaviors:

| Model | UI Sections | Audio Tags | Voice Selection | API Target |
|-------|------------|------------|-----------------|------------|
| MiMo Standard TTS | Voice grid + params | ✅ `(方言)` format | Clickable voice grid (9 MiMo voices) | MiMo API (requires key) |
| MiMo Voice Design | Voice description textarea + params | ❌ Natural language only | N/A (generates custom voice) | MiMo API (requires key) |
| MiMo Voice Clone | Ref audio picker + params | ✅ `(方言)` format | Upload/select from asset library | MiMo API (requires key) |
| **Edge-TTS (免费)** | Voice grid + speed/pitch/volume | ❌ N/A | Clickable voice grid (21 Edge voices) | Local proxy (no key needed) |

**TTS node rendering pattern:**
- Model selector toggles visibility of voice/design/clone sections
- Voice grid uses `.tts-voice-btn` class (NOT `.nbtn`)
- Language/dialect selector only shown for MiMo standard TTS
- Style/emotion rows hidden for Edge-TTS (not supported)
- Format locked to mp3 for Edge-TTS (only format supported)
- Each TTS node stores its own model/voice/style in `nd.meta`
- Generate button uses `data-action="tts-gen"` with dedicated event listener
- Model switch handler rebuilds voice grid dynamically (MiMo voices vs Edge voices)

**Edge-TTS integration:** Uses a Flask proxy at `edge_tts_server.py` (port 5050) that wraps `edge-tts` Python library as an OpenAI-compatible `/v1/audio/speech` endpoint. The proxy URL is stored in `S.cfg.tts.edgeUrl`. When Edge-TTS model is selected, `exTTS()` calls the proxy instead of MiMo API — no API key needed. See `references/edge-tts-proxy.md` for proxy details.

**Dialect handling differs by model** — see `references/mimo-tts-api.md` for exact message format per model.

## Asset Library

IndexedDB-based persistent storage for text, images, video, audio.

**Auto-save pattern:** Generated media automatically saved to asset library via `window.addAssetToLib(name, type, dataUrl)` in generation callbacks (exImg, exVid, exGenVideo, exTTS).

**Critical:** Always store media as data URLs (`data:audio/wav;base64,...`), NOT blob URLs (`blob:http://...`). Use `FileReader.readAsDataURL()` not `URL.createObjectURL()` when saving generated content. Blob URLs are ephemeral and break when passed to APIs (e.g., voice clone).

**Rename:** Double-click asset name to edit inline (input field, Enter/blur to save, Escape to cancel).

## Canvas Assistant Command Flow

1. User sends message → `_sendMsg()` → API call with `_buildApiMsgs()`
2. LLM returns text with `` ```json ``` command blocks
3. `_parseAndExec()` extracts commands → `_CMD_HANDLERS` execute them
4. **Results fed back to LLM** via `_followUp()` so assistant can answer based on execution results
5. Think filtering: `_stripThink()` removes `</think>` blocks (streaming-safe)

## Connection Visibility & Z-Index

Connections (SVG paths between nodes) are ONLY drawn when nodes are selected (`S.sel.length > 0`). This is by design — avoids visual clutter. `updConns()` first removes all paths, then draws only connections involving selected nodes. If connections appear missing, click a node to select it. If connections are truly gone (data loss), check `S.conns` length in console — if 0, the connections were never saved or were overwritten.

**Z-Index fix:** SVG connection lines must render BEHIND node boxes. In the CSS, `#svg` has `z-index: 2` and `#viewport` has `z-index: 5`. The `.node` elements inside `#viewport` use `z-index: 10` relative to viewport's stacking context. This stacking context setup ensures SVG lines always stay behind nodes. If lines appear on top of nodes, check that `#viewport` has an explicit `z-index` (higher than `#svg`'s `z-index`).

## Asset Node Prompt Generation (Master Templates)

Asset nodes (类型: 角色/场景/道具) now generate image prompts using master prompt templates instead of simple style + description concatenation. The templates mimic professional character/scene/prop reference sheet layouts.

### Three Asset Types

| Type | Function | Template Source |
|------|----------|----------------|
| `character` | `charVariantPrompt(desc, style)` | 角色定妆图提示词B版 — 三栏布局(12%/15%/73%), 主视觉+三视图+姿态+5表情+3面部特征+2材质+6配件+配色+坐骑 |
| `scene` | `sceneVariantPrompt(desc, style)` | 场景设计图提示词B版 — 主视图40%左上, SEC2-9环绕: 环境层次/状态变化/景别/平面剖析/6细节/鸟瞰/周边/配色 |
| `prop` | `propVariantPrompt(desc, style)` | 道具设计图提示词B版 — 主视图35%+三视图线框+状态变化+爆炸图+6细节特写+场景氛围+配色 |

### Key Functions

- `getAssetType(nd)` — Returns `"character"`, `"scene"`, or `"prop"` from `nd.meta.assetType`. Falls back to title prefix detection (角色/场景/道具 → English).
- `buildAssetPrompt(nd, desc)` — Calls the correct master template function based on asset type + style prefix + description.
- `exGenAsset(nd, el)` — The "生成图片" button handler. Now calls `buildAssetPrompt()` instead of `getStylePrefix() + prompt`.

### Storage Pattern

Asset node `textarea` stores only the raw DESCRIPTION string (English, from LLM extraction or user input). The full master-template prompt is built dynamically at generation time via `buildAssetPrompt()`.

### Style from Script Node

Style is set via the script node's `<select class='style-sel'>` dropdown, which writes to `S.cfg.style`. `getStylePrefix()` reads `S.cfg.style` and maps through `STYLE_MAP` to produce the English style keyword string. The style is embedded as the `tail` parameter in all master template functions.

### Script Node Extraction (exExtract)

When "提取资产" is clicked on a script node:
1. LLM extracts `{characters, scenes, props}` in JSON
2. Each asset node is created with `meta.assetType` set to `"character"` / `"scene"` / `"prop"`
3. Node `content` stores the raw LLM-generated description (NOT the full template prompt)
4. Full prompt is built dynamically when user clicks "生成图片"

### Script Node Textarea (Editable)

The script node's textarea has `readonly` removed — users can paste script content directly or import via file picker. Key details:
- Placeholder: "剧本内容...直接粘贴或导入文件"
- File import (`.doc/.docx/.txt/.md/.pdf`) still works alongside paste
- **Limitation**: `.doc` and `.docx` files are NOT supported. Both are binary formats that produce garbled text when read as plain text. `.txt` and `.md` work correctly. Show a toast: `"不支持 doc 格式，请另存为.txt后导入"` when an unsupported extension is detected.
- Content is read via `ta.value.trim()` in `exSplit`/`exExtract`/`exOptimize` — always from live textarea, not `nd.content`

### Pitfalls

- Always store `nd.meta.assetType` on asset node creation — don't rely on title prefix alone for type detection, as user may rename the title.
- Master template prompts are LONG (300-500 chars per prompt). This is by design for reference-sheet style image generation.
- The charVariantPrompt was migrated from A版 (4 variants A1-A4 with detectVariant keyword matching) to pure B版 (single three-column layout, no radar charts, no attribute data). Remove all references to `CHAR_VARIANTS` and `detectVariant` if updating from old code.

## Canvas Save/Load — Style Persistence

Canvas saves (`saveCvs()`) now include the active style alongside node/connection data:

```javascript
function saveCvs(){
  localStorage.setItem("kc-cvs", JSON.stringify({
    nodes: S.nodes, conns: S.conns, nid: S.nid,
    zoom: S.zoom, px: S.px, py: S.py,
    style: S.cfg.style || ''    // ← save active style
  }));
}
```

On load (`loadCvs()`), the style is restored:
```javascript
if(d.style){ S.cfg.style = d.style; }
```

This ensures that when a user reopens a saved canvas, the global style (selected from the script node's dropdown) is automatically restored. Without this, the style would revert to empty on page refresh, requiring the user to re-select it.

**Pitfall:** Style saved in canvas data (`kc-cvs`) is independent from style saved in settings (`kc-cfg`). If both exist, kc-cvs style takes precedence because `loadCvs()` runs after `loadCfg()`. This is intentional — the canvas carries its own style context.

## Auto-Save (30s interval)

- Auto-save:** Canvas auto-saves every **30 seconds** via `setInterval` (changed from 5s to reduce JSON.stringify lag on large canvases). Plus immediate `saveCvs()` after image/prompt generation to persist `meta.img`/`meta.vid`/`nd.content` before the next auto-save tick. Without these immediate saves, generated media data can be lost if the user refreshes between auto-save intervals.

**Places where saveCvs() is called after success:**
- `exImg()` — both async polling path and standard path (after `nd.meta.img=imgUrl`)
- `exGenSBImg()` — after `nd.meta.img=imgUrl`
- `exGenAsset()` — after `nd.meta.img=imgUrl`
- `exGenVideoPrompt()` — after `nd.content=resp`
- `exVid()` — after `nd.meta.vid=vidUrl`
- `exGenVideo()` — after `nd.meta.vid=vidUrl`
- `exTTS()` — after `nd.meta.audio=audioUrl`

```javascript
setInterval(function(){
  _cleanTtsMeta(S.nodes);
  localStorage.setItem("kc-cvs", JSON.stringify({
    nodes: S.nodes, conns: S.conns, nid: S.nid,
    zoom: S.zoom, px: S.px, py: S.py,
    style: S.cfg.style || ''
  }));
  localStorage.setItem("kc-style", S.cfg.style || '');
}, 5000);
```

- Runs silently (no toast, no user notification)
- Same data shape as `saveCvs()` (manual save)
- `_cleanTtsMeta` runs before persist to strip stale URL data from TTS node meta
- 5s interval balances data safety vs write frequency (localStorage writes are synchronous)

## Optimize Script — Write Back Fix

The `exOptimize()` function had a bug: it called the LLM to optimize the script but only showed a "剧本优化完成！" toast without writing the result back. The fix:

```javascript
// Before (bug): result was thrown away
if(d.choices&&d.choices[0]){ toast("剧本优化完成！"); }

// After: result written back to node content and textarea
if(d.choices&&d.choices[0]){
  var ot = d.choices[0].message.content;
  nd.content = ot;
  if(ta) ta.value = ot;
  toast("剧本优化完成！");
}
```

## Start-End Frame Mode (首尾帧模式)

Frame mode 下的分镜节点(sboard)有独立的 prompt 和 image 生成流程，分为首帧和尾帧两套。

### Node Chain

```
sboard → startPrompt(startFrame) → startImg(image-gen) → vpn(meta.video)
sboard → endPrompt(endFrame) → endImg(image-gen) ───────────────┘
```

**视频提示词(meta.video)自动弹出规则：** 点击首帧提示词节点的"生成首帧图"时，自动创建分镜视频提示词节点到首帧图右侧，同时连线首帧图→视频提示词。如果尾帧图已存在，也连上尾帧图。点击尾帧提示词节点的"生成尾帧图"时，只连线到尾帧图，不新建视频提示词（已由首帧图创建）。

#### `addNode` data — w/h must be top-level, not inside meta

When calling `addNode("prompt", null, null, {..., meta: {...}, w:320, h:160}, false)`, the `w` and `h` properties MUST be at the top level of the data object, NOT inside `meta`. The `addNode` function reads `data.w || dW(type)` and `data.h || dH(type)` — if w/h are inside `meta`, they're never seen and the node defaults to `dW(type)`/`dH(type)` instead.

```javascript
// ❌ WRONG — w/h inside meta, ignored by addNode
addNode("prompt", null, null, {title:"首帧提示词", content:resp, meta:{startFrame:true, w:320, h:160}}, false);

// ✅ CORRECT — w/h at top level
addNode("prompt", null, null, {title:"首帧提示词", content:resp, meta:{startFrame:true}, w:320, h:160}, false);
```

**Pitfall:** This is easy to miss when refactoring or adding new node types — the `w` and `h` look like they belong with other sizing info. Always double-check they're siblings of `meta`, not children.

### `addNode` data — w/h must be top-level, not inside meta

When calling `addNode("prompt", null, null, {..., meta: {...}, w:320, h:160}, false)`, the `w` and `h` properties MUST be at the top level of the data object, NOT inside `meta`. The `addNode` function reads `data.w || dW(type)` and `data.h || dH(type)` — if w/h are inside `meta`, they're never seen and the node defaults to `dW(type)`/`dH(type)` instead.

```javascript
// ❌ WRONG — w/h inside meta, ignored by addNode
addNode("prompt", null, null, {title:"首帧提示词", content:resp, meta:{startFrame:true, w:320, h:160}}, false);

// ✅ CORRECT — w/h at top level
addNode("prompt", null, null, {title:"首帧提示词", content:resp, meta:{startFrame:true}, w:320, h:160}, false);
```

**Pitfall:** This is easy to miss when refactoring or adding new node types — the `w` and `h` look like they belong with other sizing info. Always double-check they're siblings of `meta`, not children.

## Default Sizes

| Node | Width | Height |
|------|-------|--------|
| sboard (分镜) | 280 | 450 |
| startPrompt/endPrompt | 320 | 160 |
| startImg/endImg | 360 | 160 |
| video prompt (prompt.meta.video) | 360 | 340 |
| video-gen (分镜视频) | 560 | 340 |

### Prompt Generation (No Master Template)

`exGenStartPrompt` / `exGenEndPrompt` generate pure scene description prompts. **No master template / board design / frame border.** System prompt is "cinematographer" (not "production designer"). Output is a concise English paragraph describing: scene setting, subject, camera angle, lighting, color palette.

```javascript
// Example sysPrompt structure:
"You are a professional cinematographer. Generate a concise English prompt describing the START FRAME (first shot) of this scene for AI image generation.
Describe the opening shot's:
- Scene setting and atmosphere
- Subject/character position, appearance, and action
- Camera angle and framing
- Lighting mood and color palette
...
Output only the prompt, English, one concise paragraph. Do NOT include any template, board design, or frame border instructions."
```

### Image Generation — Multi-Image Reference

`exImg` on image-gen nodes collects connected asset images as multi-image reference:
```javascript
var linked = S.nodes.filter(function(n){
  return n.type==="asset" && n.meta && n.meta.img &&
    S.conns.some(function(c){ return c.to===nd.id && c.from===n.id; });
});
// For GaiaTester (isT): body.images = refImgs (multi-image array)
// For standard API: body.image = refImgs[0] (single image)
```

### Duration Tracing — Start-End Frame Chain

For video prompt generation and mkvideo, the duration is traced through the start-end frame chain. The old code hardcoded `meta.storyboard` — now it matches ANY prompt type (startFrame/endFrame/storyboard):

```javascript
// mkvideo duration trace:
var mkIgNode = S.nodes.find(n => n.type==="image-gen" && connected to nd);
var mkPsb = S.nodes.find(n => n.type==="prompt" && connected to mkIgNode); // ← no meta filter!
// Then find sboard via mkPsb and parse /^(\d+)s\s*\|/ from content
```

### Video Prompt Context Must Be Passed Through

When modifying `exGenVideoPrompt`'s system prompt, **always ensure the storyboard content (`context`) and existing text (`text`) are appended to the system prompt**:

```javascript
// Correct — context and text are part of sysPrompt:
"- 时长："+vnDur+"秒\n"+
"\n分镜内容：\n"+context+"\n"+
(text?"已有提示词（可优化）：\n"+text+"\n":"");
```

**Pitfall:** If you remove these lines from the system prompt, the LLM receives rules but NO actual storyboard content. It will generate a generic example response like "由于您未提供具体的分镜内容..." instead of the actual video prompt. Always verify that the storyboard content flows into the LLM call.

### `var sbNode` Shadowing in try Block (FIXED — No Shadowing)

`exGenVideoPrompt` had `var sbNode` declared both OUTSIDE and INSIDE the `try` block. Due to `var` hoisting, the inner `sbNode` shadowed the outer one, causing the trace loop to reset `sbNode=null` and lose the outer search result.

`exGenVideoPrompt` has `var sbNode` declared both OUTSIDE and INSIDE the `try` block (lines ~2424 and ~2453). Due to `var` hoisting, the inner `sbNode` shadows the outer one. If the inner trace loop doesn't find a sboard, `sbNode` is `null`, and subsequent access to `sbNode.id` (for finding start/end prompts) throws:

```
Cannot read properties of null (reading 'id')
```

**Fix:** 
1. Guard every `sbNode.id` access with a null check:
```javascript
var stPrompt = sbNode ? S.nodes.find(...) : null;
var enPrompt = sbNode ? S.nodes.find(...) : null;
```

2. **Don't redeclare outer variables inside try.** The inner trace loop should NOT use `var sbNode=null` — this resets the outer value and breaks the fallback. Instead:
```javascript
// OUTSIDE try (correct):
var sbNode = null;
for(...) { ... sbNode = up; ... }

// INSIDE try (WRONG — resets outer sbNode):
try {
  var sbNode = null, tmpN = nd;  // ← BUG: resets outer sbNode
  for(...) { ... sbNode = up; ... }
}

// INSIDE try (CORRECT — only declare tmpN, reuse sbNode):
try {
  var tmpN = nd;  // ← only new variable
  sbNode = null;  // ← assigns to outer sbNode, no shadowing
  for(...) { ... sbNode = up; ... }
}
```

**Prevention:** Use unique variable names for inner-scope variables, or avoid `var` redeclaration by using the outer `sbNode` directly. When shadowing with `var` is unavoidable, null-guard all downstream accesses.

### sbNode Traceback — Alt Search Path

The original `exGenVideoPrompt` had a single traceback loop (`videoPrompt → image-gen → prompt → sboard`) that could fail if the connection order was unexpected. The fix adds an alternative search path:

```javascript
// Primary: trace backward from vp
var sbNode = findSboardFromVp(nd.id);  // reusable function

// Fallback: search directly from connected image-gen nodes
if(!sbNode){
  var allImgs = S.nodes.filter(n => n.type==="image-gen" && connected to nd);
  for(var i=0; i<allImgs.length; i++){
    var upPrompt = find upstream prompt connected to allImgs[i];
    var upSboard = find sboard connected to upPrompt;
    if(upSboard){ sbNode = upSboard; break; }
  }
}
```

Also adding console.log at each key decision point so failures can be diagnosed from browser devtools.

### Old `exImg` Auto-Create Video Prompt

The `exImg` function (~line 2602) had old code that auto-created a "视频提示词" node after any image generation:

```javascript
// REMOVED: This code auto-created a duplicate video prompt node
var vnn = addNode("prompt", null, null, {title: sbName+" 视频提示词", content:"", meta: vnMeta}, false);
```

This was designed for the old storyboard flow (sboard → prompt(storyboard) → image-gen → prompt(video)). In the new start-end frame flow, the video prompt is created by `exGenStartImg`. **The old auto-creation causes duplicate video prompt nodes. If you see a second "视频提示词" node popping up after image generation, search for `addNode.*视频提示词` in exImg and remove it.**

### `@图片1` Reference — Always End Frame Image (OLD format)

```javascript
var igNode = S.nodes.find(n => 
  n.type==="image-gen" && S.conns.some(c => c.from===n.id && c.to===nd.id) &&
  S.nodes.some(p => p.type==="prompt" && p.meta && p.meta.endFrame &&
    S.conns.some(c2 => c2.from===p.id && c2.to===n.id))
);
```

### Image Reference Naming Convention — Use Node Titles (NEW)

Seedance 2.0 first-last frame mode now uses **actual image-gen node titles** as `@` references instead of `@图片1`/`@图片2`:

**Convention:**
- `@<endImg.title>` = end frame reference image (尾帧)
- `@<stImg.title>` = start frame reference image (首帧)

**In `exGenVideoPrompt` (system prompt generation):**
```javascript
var enImgName = enImg ? enImg.title : "尾帧图";
var stImgName = stImg ? stImg.title : "首帧图";
// Then in sysPrompt string:
// "@"+enImgName+" [风格/色调总纲]..."
// "- @"+enImgName+" 引用尾帧参考图..."
// "- @"+stImgName+" 引用首帧参考图..."
```

**In `exGenVideo` (sending images to API):**
```javascript
// Send BOTH start and end frame images as objects with node title as name
if(endImg&&endImg.meta&&endImg.meta.img)
  refImgs.push({dataUrl: endImg.meta.img, name: endImg.title});
if(startImg&&startImg.meta&&startImg.meta.img)
  refImgs.push({dataUrl: startImg.meta.img, name: startImg.title});
// Send as-is — server.js normalizes {dataUrl, name} to {fileName: title}
body = { prompt: ..., images: refImgs, ... };
```

**CRITICAL:** `S.nodes.find()` only returns the FIRST match. Both start and end frame images are connected to the video prompt node, so use `S.nodes.filter()` to get ALL connected image-gen nodes, then differentiate by checking upstream prompt type (`meta.startFrame` vs `meta.endFrame`).

### Seedance 2.0 Video Prompt Format

`exGenVideoPrompt` generates Chinese prompts following Seedance 2.0 conventions. The system prompt must include BOTH rules AND storyboard content:

**System prompt structure (in order):**
1. Role definition: "专业的视频提示词工程师。为Seedance 2.0（即梦）生成中文视频提示词"
2. Output structure: `@图片1 [风格/色调总纲]，[主体描述]，[动作序列]，[环境/光影]，[镜头语言]，[音效描述]`
3. Start-end frame rules: @图片1 = 尾帧参考图，描述首帧→尾帧过渡
4. Camera codec: Z/Y/X/F
5. Constraints: ≤200字, 中文, 角色一致性
6. Duration from sboard content (`/^(\\d+)s/`)
7. **Storyboard content** (`\\n分镜内容：\\n` + context)
8. Existing text for optimization (`已有提示词（可优化）：\\n` + text)

**Pitfall:** The storyboard content (`context`) and existing text must be appended to `sysPrompt`, NOT passed separately in `userMsg`. If they're missing, the LLM generates a generic example instead of the actual video prompt. Verify line 2485-2487 has the `context` and `text` lines.

```javascript
// Verified correct format at end of sysPrompt:
"- 时长："+vnDur+"秒\\n"+
"\\n分镜内容：\\n"+context+"\\n"+
(text?"已有提示词（可优化）：\\n"+text+"\\n":"");
```

```
@图片1 [风格/色调总纲]，[主体描述]，[动作序列]，[环境/光影]，[镜头语言]，[音效描述]
```

Rules:
- 中文，一段话，不超过200字
- 四维编码 Z/Y/X/F 嵌入镜头部分
- @图片1 = 尾帧参考图
- 描述首帧到尾帧的完整过渡
- 时长来自分镜节点内容解析 (`/^(\\d+)s/`)

**Video prompt validation — check images exist (not context/text):**
The video prompt generation does NOT check if the sboard has text content. Instead, it checks if the start AND end frame IMAGES have been generated (have `meta.img`):

```javascript
// Outside try block:
var stImgNode = sbNode ? S.nodes.find(n => n.type==="image-gen" && connected to nd && has upstream endFrame prompt) : null;
var enImgNode = sbNode ? S.nodes.find(n => n.type==="image-gen" && connected to nd && has upstream endFrame prompt) : null;
var stImgReady = stImgNode && stImgNode.meta && stImgNode.meta.img;
var enImgReady = enImgNode && enImgNode.meta && enImgNode.meta.img;
if (!stImgReady || !enImgReady) { toast("请先生成首帧图和尾帧图"); return; }
```

This check runs both OUTSIDE and INSIDE the try block. The `stImgReady`/`enImgReady` variables are declared once outside try and reused inside (no shadowing). If the user hasn't clicked "生成图片" on both the start and end frame image nodes, the video prompt generation blocks with "请先生成首帧图和尾帧图".

**Pitfall:** The OUTSIDE check (line ~2437) and INSIDE check (line ~2482) must use the SAME variables (`stImgReady`/`enImgReady`) declared once outside try. The inner try block should NOT redeclare them — reference the outer values directly. If you redeclare inside try, the inner values may differ from the outer ones due to variable hoisting/shadowing.

**`@图片1` reference — always end frame image:**

```javascript
// igNode finds the image-gen connected to video prompt that has an upstream endFrame prompt:
var igNode = S.nodes.find(n => 
  n.type==="image-gen" && 
  S.conns.some(c => c.from===n.id && c.to===nd.id) &&  // connected to video prompt
  S.nodes.some(p => 
    p.type==="prompt" && p.meta && p.meta.endFrame &&
    S.conns.some(c2 => c2.from===p.id && c2.to===n.id)  // has endFrame prompt upstream
  )
);
var sbImg = igNode && igNode.meta && igNode.meta.img;
```

This ensures `@图片1` is always the end frame image, NOT the first-connected image in the connections array (which might be the start image). The original code used `S.nodes.find(...)` which returned the first match — could be either start or end image.

## Video Duration — Propagation Chain

Duration flows from sboard → prompt(storyboard) → image-gen → prompt(video) → video-gen:

| Step | Function | What happens |
|------|----------|-------------|
| sboard content | `exStoryboard` | Content format: `"6s | 中景固定镜头|..."` |
| Prompt gen | `exGenPrompt` (~line 1885) | Parses `"6s |"` prefix for frame count calc. **Stores `duration:dur` in the prompt(storyboard) node's `meta`** for reliable downstream access |
| Image-gen → Prompt(video) | `exGenSBImg` (~line 1975) | Traces upstream to find sboard, extracts duration via `/^(\d+)s\s*\|/`, stores in `vnMeta.duration` |
| Prompt(video) → Video-gen | `mkvideo` (~line 1249) | **Traces at click time**: prompt(video) → image-gen → prompt(storyboard). Reads `mkPsb.meta.duration` first. Falls back to sboard content regex. |
| Video-gen render | renderNode (~line 1268) | Displays duration as a **plain text `<span class='vg-dur'>`** (NOT a select dropdown). Value = `vgDur+'s'` or `'5s'` default. |
| Video generation | `exGenVideo` (~line 1583) | Reads duration via `parseInt(dur.textContent)||5`, sends `duration:seconds` in API body |

**Duration display:** `<span class='vg-dur'>` replaces the old `<select>` dropdown. Shows `-` initially, gets populated with `{N}s` (e.g. `6s`). Plain text — no user modification possible. Read back for API via `parseInt(dur.textContent)||5`.

**Video quality options:** `480p / 720p / 1080p` via `.vg-quality`.
1. Walk up connection chain (up to 6 hops), find first node with `meta.duration`
2. Fallback: match sboard by title prefix ("第1集-1 分镜视频" → strip " 分镜视频" → find sboard with that title) → regex parse duration
3. If all fail, display `'5s'` as default

**Reference image for video — both start and end frame images with node titles:**
`exGenVideo()` traces upstream from `video-gen → prompt(video)` and finds ALL connected image-gen nodes. It differentiates start vs end frame by checking each image-gen's upstream prompt (`meta.startFrame` vs `meta.endFrame`). **End frame is sent first (@<nodeTitle> in prompt), start frame second:**

```javascript
// Correct pattern — find ALL, not just first:
var allImgNodes = S.nodes.filter(function(n){
  return n.type==="image-gen" && S.conns.some(function(c){return c.from===n.id&&c.to===vpNode2.id;});
});
var endImg=null, startImg=null;
allImgNodes.forEach(function(n){
  var upPrompt = S.nodes.find(p => p.type==="prompt" && connected to n);
  if(upPrompt&&upPrompt.meta&&upPrompt.meta.endFrame) endImg=n;
  else if(upPrompt&&upPrompt.meta&&upPrompt.meta.startFrame) startImg=n;
});
// Send with node title as filename (used as @<title> in prompt):
if(endImg&&endImg.meta&&endImg.meta.img) refImgs.push({dataUrl: endImg.meta.img, name: endImg.title});
if(startImg&&startImg.meta&&startImg.meta.img) refImgs.push({dataUrl: startImg.meta.img, name: startImg.title});
```

**Pitfall:** `find()` returns only the first match. With both start and end images connected to the same video prompt, `find` might return the wrong one. Always use `filter()` + type differentiation.

**Video quality options:** `480p / 720p / 1080p` via `.vg-quality`.
```javascript
var body = { model:s.model, prompt:..., size:..., duration:seconds, images:refImgs };
```
The `duration` field tells Seedance / UpToken API how many seconds the video should be.

### Sboard Vertical Gap (Storyboard Mode)

Both `exStoryboard` and `dH()` define the sboard node spacing:

- Default height (`dH("sboard")`): 450
- Gap between nodes in `exStoryboard`: 20
- Total spacing between node tops: 450 + 20 = 470

```javascript
// In exStoryboard():
var startX = nd.x + nd.w + 30, startY = nd.y, nw = 280, nh = 450, gap = 20;
for(var i = 0; i < shots.length; i++){
  var nn = addNode("sboard", null, null, {title: epTitle+"-"+(i+1), content: shotDesc, w: nw, h: nh}, true);
  nn.x = startX; nn.y = startY + i * (nh + gap);
}
```

### Canvas Init — Sboard Alignment

In the init section (after `loadCvs()`), add a one-time alignment pass to snap the first sboard of each episode to match the episode node's Y. This handles saved canvas data where positions drifted.

```javascript
S.nodes.filter(function(n){return n.type==="sboard";}).forEach(function(n){
  var ep = S.nodes.find(function(e){
    return e.type==="episode" && S.conns.some(function(c){return c.from===e.id&&c.to===n.id;});
  });
  if(!ep) return;
  var firstSboard = S.nodes.filter(function(s){
    return s.type==="sboard" && S.conns.some(function(c){return c.from===ep.id&&c.to===s.id;});
  }).sort(function(a,b){return a.y-b.y;})[0];
  if(firstSboard && firstSboard.id===n.id && Math.abs(n.y-ep.y)>20){
    n.y = ep.y;
    var el = document.getElementById(n.id); if(el) el.style.top = n.y+"px";
    updConns();
  }
});
```

**Pitfall:** This alignment code runs only once at page init. Do NOT call `saveCvs()` inside it — that would overwrite the saved positions. The alignment is visual only; the next auto-save (5s later) will persist the adjusted positions.

## Reference Files

- `references/adding-styles.md` — How to add a new style to the image/video generation style system (5 locations: STYLE_MAP, VG_STYLE_LABELS, inline styleMap, supplemental entries, HTML options)

- **`vlinkassets` button** ("🔗 关联资产") removed from video-gen nodes. The corresponding `exLinkVideoAssets()` function and all event handler code was cleaned up. (关联资产 still exists on prompt(storyboard) nodes via `linkassets` action.)

## Storyboard Duration Constraints

Both `exGenPrompt()` (sboard → prompt) and `exStoryboard()` (episode → storyboard) have updated duration rules:

**exGenPrompt** (frame splitting):
```javascript
var frames = (dur>=3 && dur<=15) ? Math.min(8, Math.max(3, Math.round(dur/2))) : 0;
```
- Replaced old `dur>=10?6:dur>=6?4:0` which only supported 6s and 10s buckets
- Now supports any duration 3-15s with proportional frames (~1 frame per 2 seconds)

**exStoryboard** (system prompt):
```
// Old: "每个镜头的duration字段必须严格为6或10（秒），不能使用其他数值"
// New: "每个镜头的duration字段在3-15秒内自由选择，根据镜头复杂度决定时长"
// Old: "6秒镜头拆分为4个关键帧，10秒镜头拆分为6个关键帧"
// New: "3-15秒镜头自动按round(时长/2)帧拆分为3-8个关键帧"
```

## When adding new fields to node meta, add cleanup logic in `loadCvs()` to strip stale/corrupt values:

```javascript
// In loadCvs(), before renderNode:
if(n.meta){
  ['ttsEmotion','ttsStyle'].forEach(function(k){
    if(n.meta[k] && n.meta[k].indexOf('http')>=0) n.meta[k]='';
  });
}
```

Also run cleanup after `loadCvs()` on in-memory nodes and save if changed. This prevents stale data (like API URLs accidentally stored in style/emotion fields) from persisting.

## Data Cleanup Pattern (`_cleanTtsMeta`)

When node meta fields get corrupted with API URLs (from browser autofill or bugs), use a reusable cleanup function:

```javascript
function _cleanTtsMeta(nodes){
  var changed=false;
  (nodes||[]).forEach(function(n){
    if(!n.meta)return;
    if(n.type!=='tts')return;           // ← ONLY clean TTS nodes
    Object.keys(n.meta).forEach(function(k){
      if(k==='img'||k==='vid')return;   // ← NEVER touch media URLs
      var v=n.meta[k];
      if(typeof v==='string'&&(v.indexOf('http://')>=0||v.indexOf('https://')>=0)){
        n.meta[k]='';changed=true;
      }
    });
  });
  return changed;
}
```

**⚠️ CRITICAL PITFALL**: The naive version of this function (without the `n.type!=='tts'` guard and `k==='img'||k==='vid'` skip) will destroy `meta.img` and `meta.vid` URLs on EVERY save via `saveCvs()`. This causes all generated images/videos to disappear after page refresh. The function is called in `saveCvs()` BEFORE persisting, so it silently corrupts saved data. Always scope cleanup to only the affected node type and skip media URL keys.

Call in `loadCvs()` BEFORE rendering, and in `saveCvs()` BEFORE persisting. Also add a delayed post-render check in `renderNode()` for inputs that browser autofill might corrupt.

## Browser Autofill Prevention

Browsers aggressively autofill input fields that look like URLs or form fields. **All interactive inputs in node renderers MUST have `autocomplete="off"`.** Without this, API URLs from settings panel can leak into TTS style/emotion fields.

Also add a 100ms delayed check after rendering TTS nodes:
```javascript
setTimeout(function(){
  el.querySelectorAll('.tts-ctrl input,.tts-ctrl textarea').forEach(function(inp){
    if(inp.value&&(inp.value.indexOf('http')>=0)){
      inp.value='';
      inp.dispatchEvent(new Event('input'));
    }
  });
},100);
```

## LLM URL Auto-Fix (fixLLMUrl)

A `fixLLMUrl(u)` function auto-appends the OpenAI-compatible chat completions path to partially-specified URLs. Used in ALL LLM fetch calls across the app.

```javascript
function fixLLMUrl(u){
  if(!u)return u;
  u=u.replace(/\/+$/,"");
  if(!u.endsWith("/chat/completions")){
    if(u.endsWith("/v1")) u+="/chat/completions";
    else u+="/v1/chat/completions";
  }
  return u;
}
```

**Usage:** Every LLM fetch call uses `fixLLMUrl(s.url)` instead of raw `s.url`:
```javascript
var r = await fetch(fixLLMUrl(s.url), {method:"POST", headers:{...}, body:...});
```

This allows users to enter base URLs like `https://api.deepseek.com` or `http://localhost:11434` without manually appending `/v1/chat/completions`.

## Selected Node Visual Effect (SDots)

When a node is selected (`.sel` class), four silver dots appear on its four edges and move clockwise via `requestAnimationFrame`:

```javascript
var sdotTimers = {};
function addSDots(e){
  if(!e||e.querySelector(".sdot"))return;
  var id=e.id||"n", sides=["t","r","b","l"], dots={};
  sides.forEach(function(s){
    var d=document.createElement("div"); d.className="sdot"; d.dataset.side=s;
    e.appendChild(d); dots[s]=d;
  });
  var start=performance.now(), dur=2000, cw=e.offsetWidth||200, ch=e.offsetHeight||100;
  function anim(now){
    var t=((now-start)%dur)/dur;
    // Cache dimensions at start — DO NOT read offsetWidth/offsetHeight every frame (causes forced layout)
    var cw=cw||200, ch=ch||100;
    var pos = [
      {x:t*cw, y:0},           // top: left→right
      {x:cw, y:t*ch},           // right: top→bottom
      {x:(1-t)*cw, y:ch},       // bottom: right→left
      {x:0, y:(1-t)*ch}        // left: bottom→top
    ];
    sides.forEach(function(s,i){
      var d=dots[s], p=pos[i];
      d.style.left=p.x+"px"; d.style.top=p.y+"px";
      var blink=((now%500)/500);
      var bright=blink<0.2 ? 1 : 0.3;
      d.style.opacity=bright;
      d.style.transform="scale("+(0.5+bright*0.7)+")";
      d.style.boxShadow="0 0 "+(2+bright*12)+"px silver";
    });
    sdotTimers[id]=requestAnimationFrame(anim);
  }
  sdotTimers[id]=requestAnimationFrame(anim);
}
function rmSDots(e){
  if(!e)return;
  var id=e.id||"n";
  if(sdotTimers[id]){cancelAnimationFrame(sdotTimers[id]);delete sdotTimers[id];}
  e.querySelectorAll(".sdot").forEach(function(d){d.remove();});
}
```

**CSS for dots:**
```css
.sdot{position:absolute;width:6px;height:6px;border-radius:50%;background:silver;
  box-shadow:0 0 8px silver;pointer-events:none;z-index:20;margin:-3px 0 0 -3px;opacity:0}
```

**Triggered from selN/deselAll/toggleSel:** When a node gets `.sel` class, `addSDots(e)` is called. When deselected, `rmSDots(e)` removes dots and cancels the rAF.

## Script Node Style Validation

The `一键分集` button checks that a specific style has been selected before executing. The check is in `exSplit()`:

```javascript
async function exSplit(nd, el){
  var s=S.cfg.llm; if(!s.url||!s.key){toast("请先在设置中配置LLM API");return;}
  var styleSel=el.querySelector(".style-sel");
  if(!styleSel||!styleSel.value){
    toast("请先在剧本节点中选择一个风格（不要选\"全局风格\"）");
    return;
  }
  ...
}
```

The check reads the actual DOM `<select>` value directly (not `S.cfg.style`) because `S.cfg.style` might have stale saved values from previous sessions. The style-sel initialization sets `styleSel.value=S.cfg.style||""` on render, so the DOM value is the ground truth.

**Pitfall:** Don't check `S.cfg.style` alone — it may hold a value saved from a previous session even when the dropdown shows "全局风格". Always read `el.querySelector(".style-sel").value` at execution time.

## Image Click-to-Zoom

All images in the canvas (preview, ig-preview, vg-preview, sb-panel, al-item-preview) support left-click to open a fullscreen dark overlay:

```javascript
document.addEventListener("click", function(e) {
  var img = e.target.closest("#canvas img, .al-item-preview img");
  if (!img || e.target.closest(".img-viewer")) return;
  if (document.querySelector(".img-viewer")) return;
  var src = img.getAttribute("src") || img.src;
  if (!src || src.length < 50) return;  // skip small placeholders
  var ov = document.createElement("div"); ov.className = "img-viewer";
  var big = document.createElement("img"); big.src = src;
  ov.appendChild(big);
  ov.addEventListener("click", function() { ov.remove(); });
  document.body.appendChild(ov);
});
```

**CSS:**
```css
.img-viewer {
  position: fixed; inset: 0; z-index: 100001;
  background: rgba(0,0,0,0.85);
  display: flex; align-items: center; justify-content: center;
  cursor: zoom-out; animation: ivFade .2s;
}
.img-viewer img {
  max-width: 90vw; max-height: 90vh;
  object-fit: contain; border-radius: 8px;
  box-shadow: 0 8px 40px rgba(0,0,0,0.5);
}
@keyframes ivFade { from { opacity: 0; } to { opacity: 1; } }
```

**Close:** Click overlay → `ov.remove()`. Press ESC → removed in keyboard handler alongside other desel/close logic.

The `#canvas img` selector catches all images inside node previews, and `.al-item-preview img` catches asset library thumbnails.

## Export Icon & Canvas Assistant Toolbar Button

### Export Icon Fix

The dynamically-created export button had a broken unicode icon (malformed surrogate pair):

```javascript
// Before (broken — shows as diamonds with question marks):
expBtn.innerHTML = "<span>\uD83D�</span> 导出";

// After (proper ES6 unicode syntax):
expBtn.innerHTML = "<span>\u{1F4E4}</span> 导出";
```

**Always use `\u{XXXXX}`** for emoji beyond BMP (U+10000+). Old-style surrogate pairs (`\uD83D\uDDE3`) can break when the pair is split across string concatenations or truncated by editing tools.

### Canvas Assistant Button

The assistant panel was previously only accessible via the `/` keyboard shortcut. Added a toolbar button for discoverability:

```javascript
var asstBtn = document.createElement("button");
asstBtn.className = "tb"; asstBtn.id = "tAssistant";
asstBtn.innerHTML = "<span>\u{1F9E0}</span> 画布助手";
asstBtn.addEventListener("click", function(e) {
  e.stopPropagation();
  var p = document.getElementById('agentPanel');
  if (!p) return;
  if (typeof _agent === 'undefined' || !_agent.inited) { _initPanel(); }
  p.classList.toggle('visible');
  if (p.classList.contains('visible')) {
    var inp = document.getElementById('agentInput');
    if (inp) setTimeout(function() { inp.focus(); }, 100);
  }
});
toolbar.insertBefore(asstBtn, settingsButton);
```

**IIFE scope pitfall:** The `_toggleAgent()` function is defined in a different IIFE (the last one in the file, ~line 3369) than the toolbar button code (~line 2341). Calling `_toggleAgent()` directly from the button handler will throw "not defined" because it's in a different scope. Solutions:
1. **Inline the logic** — directly toggle the panel class + init in the click handler
2. **Expose via `window`** — add `window.toggleAgent = _toggleAgent;` inside the defining IIFE, then call `toggleAgent()` from the button

## Default Image Sizes

Two node types have their default size selector changed to 16:9:

| Node Type | Selector Class | First Option (default) | Fallback in code |
|-----------|---------------|----------------------|-----------------|
| Asset (资产) | `.as-size` | `value='2560x1440'` → 16:9 | `"2560x1440"` |
| Image-gen (图像) | `.ig-size` | `value='2560x1440'` → 16:9 | `"1280x720"` |

The first `<option>` in the `<select>` is the browser's default selection. Move the 16:9 option (`2560x1440`) to first position. Also update the fallback value in the respective generation function (`exGenAsset` for assets, `exGenSBImg` for image-gen) to match a 16:9 resolution.

## API Test Connection Pattern

Settings panel has a "测试连接" button for TTS that sends a minimal request to verify URL + key:

```javascript
async function testTTSApi(){
  var url=document.getElementById("stu").value.trim();
  var key=document.getElementById("stk").value.trim();
  if(!url||!key){ /* show error */ return; }
  // Send minimal TTS request
  var r=await fetch(url,{method:"POST",
    headers:{"Content-Type":"application/json","api-key":key},
    body:JSON.stringify({model:"mimo-v2.5-tts",
      messages:[{role:"user",content:"温暖自然的语调"},{role:"assistant",content:"你好，测试连接。"}],
      audio:{format:"wav",voice:"冰糖"}
    })});
  if(r.ok){ /* success - decode audio and play */ }
  else{ /* show error response body for debugging */ }
}
```

Pattern: try minimal request, show full error response on failure, play audio on success. Add `autocomplete="off"` to URL/key inputs.

### Canvas Mode Persistence — saveCvs Must Write kc-mode

**Bug:** `saveCvs()` saved canvas data under `kc-cvs-frame` or `kc-cvs-story` but did NOT update `localStorage["kc-mode"]`. Only the 30s auto-save interval wrote `kc-mode`. Result: switching modes and refreshing before the next auto-save tick reloaded the OLD mode (and its stale canvas data).

**Fix — two places:**

1. `saveCvs()` — add `localStorage.setItem("kc-mode", canvasMode)` immediately after saving canvas data.
2. Init IIFE (page load) — add `localStorage.setItem("kc-mode", canvasMode)` right after setting `canvasMode` from saved state, so the first page load also initializes the key.

```javascript
// In saveCvs():
function saveCvs(){
  var key="kc-cvs-"+canvasMode;
  localStorage.setItem(key, JSON.stringify({...}));
  localStorage.setItem("kc-style", S.cfg.style||'');
  localStorage.setItem("kc-mode", canvasMode);  // ← FIX: persist mode immediately
  toast("画布已保存");
}

// In init IIFE:
(function(){
  var savedMode=localStorage.getItem("kc-mode");
  if(savedMode==="frame"||savedMode==="story"){canvasMode=savedMode;}
  localStorage.setItem("kc-mode", canvasMode);  // ← FIX: init immediately
  ...
})();
```

**Apply to both files:** `app/public/canvas.html` and `canvas/canvas.html`.

### sboard genprompt Button in Storyboard Mode — Separate Event Handler Needed

In storyboard mode, the sboard node renders a `✨ 生成提示词` button (`data-action="genprompt"`). Unlike the frame-mode buttons (`genstartprompt`/`genendprompt`) which have dedicated handlers in the sboard event-binding block (line ~1388), the `genprompt` button has NO handler there. It relies on the generic `.nbtn` → `execN()` path (line 1149), but `execN()` doesn't handle `sboard` type — so the button appears but does nothing on click.

**Fix — add a separate handler alongside the frame-mode handlers:**

```javascript
if(nd.type==="sboard"){
  var ta=el.querySelector("textarea");
  if(ta){ta.addEventListener("input",function(){nd.content=ta.value;});...}
  // Frame-mode buttons (首尾帧):
  var spBtn=el.querySelector('[data-action="genstartprompt"]');
  if(spBtn)spBtn.addEventListener("click",function(e){e.stopPropagation();exGenStartPrompt(nd,el);});
  var epBtn=el.querySelector('[data-action="genendprompt"]');
  if(epBtn)epBtn.addEventListener("click",function(e){e.stopPropagation();exGenEndPrompt(nd,el);});
  // Storyboard-mode button (故事板):  ← MUST ADD SEPARATELY
  var gpBtn=el.querySelector('[data-action="genprompt"]');
  if(gpBtn)gpBtn.addEventListener("click",function(e){e.stopPropagation();exGenPrompt(nd,el);});
}
```

**The `exGenPrompt` function** is identical to `exGenStartPrompt` but removes the "start frame" specificity from the system prompt:

```javascript
async function exGenPrompt(nd,el){
  var s=S.cfg.llm;if(!s.url||!s.key){toast("请先在设置中配置LLM API");return;}
  var ta=el.querySelector("textarea"),text=ta?ta.value.trim():(nd.content||"");
  if(!text){toast("分镜无内容");return;}
  var btn=el.querySelector('[data-action="genprompt"]');btn.disabled=true;...
  try{
    // ...style trace, asset collection (same as exGenStartPrompt)...
    var sysPrompt="You are a professional cinematographer. Generate a concise English prompt describing this scene..."+
      // ...(no "START FRAME" language, general scene description)...
    var r=await fetch(fixLLMUrl(s.url),{...});
    var d=await r.json();
    // Create prompt node with meta:{storyboard:true}
    var nn=addNode("prompt",null,null,{title:(nd.title||"分镜")+" 提示词",
      content:resp,meta:{storyboard:true,duration:dur},w:320,h:160},false);
    nn.x=nd.x+nd.w+30;nn.y=nd.y;
    S.conns.push({from:nd.id,to:nn.id});updConns();updUI();
    toast("提示词已生成");
  }catch(err){...}
}
```

The created prompt node (with `meta.storyboard:true`) then connects to the existing `gensbimg` handler which calls `exGenSBImage`.

**When modifying sboard buttons:** check ALL THREE data-action values (`genstartprompt`, `genendprompt`, `genprompt`) and ensure each has its own event handler. Do not assume one handler covers all modes.

### Disabling 首尾帧 Mode (Keeping Button Visible)

To disable frame mode while keeping the button in the mode row:

1. **HTML** — Add `disabled` attribute to the frame button:
   ```html
   <button class="mode-btn" data-mode="frame" disabled>&#x1F5BC; 首尾帧</button>
   ```

2. **CSS** — Dim the button and change cursor:
   ```css
   .mode-btn[data-mode="frame"]{opacity:0.4;cursor:not-allowed}
   .mode-btn[data-mode="frame"]:hover{background:transparent;color:#999}
   ```

3. **switchCanvasMode** — Guard at the top:
   ```javascript
   function switchCanvasMode(mode){
     if(mode==="frame"){toast("首尾帧模式已停用");return;}
     ...
   }
   ```

4. **Init** — Force `canvasMode` to `"story"` on page load regardless of saved `kc-mode`:
   ```javascript
   (function(){
     canvasMode="story";
     localStorage.setItem("kc-mode", canvasMode);
     ...
   })();
   ```<｜end▁of▁thinking｜>

<｜｜DSML｜｜parameter name="file_path" string="true">file_path

The `linkassets` button (🔗 关联资产) is **only** shown on `image-gen` nodes. It was removed from `sboard` nodes.

**CRITICAL — Canvas mode controls which buttons appear on sboard nodes:**
The sboard footer must check `canvasMode` at render time to show the correct buttons for the active mode:

```javascript
body="<textarea>..."+
  (canvasMode===\"frame\"?
    \"<div class='sboard-footer'><button data-action='genstartprompt'>🎬 首帧提示词</button><button data-action='genendprompt'>🏁 尾帧提示词</button></div>\":
    \"<div class='sboard-footer'><button data-action='genprompt'>✨ 生成提示词</button></div>\");
```

- **Frame mode:** shows `🎬 首帧提示词` + `🏁 尾帧提示词`
- **Storyboard mode:** shows `✨ 生成提示词`

**Pitfall:** When modifying sboard footer HTML, you MUST verify BOTH modes work. A change that only works in frame mode will break storyboard mode (and vice versa). The `canvasMode` variable is a global string (`\"frame\"` or `\"story\"`) set via the toolbar dropdown and persisted in `localStorage` key `kc-mode`.

The prompt nodes (startFrame/endFrame) have their own generation buttons:
```html
<!-- prompt.meta.startFrame footer -->
<button class='nbtn' data-action='genstartimg'>🎬 生成首帧图</button>

<!-- prompt.meta.endFrame footer -->
<button class='nbtn' data-action='genendimg'>🏁 生成尾帧图</button>
```

The sboard footer (frame mode) has genprompt replaced with genstartprompt + genendprompt:

```html
<!-- sboard footer (no linkassets) -->
<div class='sboard-footer'>
  <button class='nbtn' data-action='genprompt'>✨ 生成提示词</button>
</div>
```

The `exLinkAssets()` function still works — it's triggered from the image-gen node's button. It traces upstream from the image-gen node to find connected asset nodes and links them as reference images.

**When adding/modifying buttons in node footers:** Check if the same button type already exists on another node type. If a button's purpose (e.g. 关联资产) applies to multiple node types, use a single source of truth and avoid per-node duplication.

### Mode Toolbar — Always-Visible Buttons (No Dropdown)

The mode selector is a flat `.mode-row` of three always-visible buttons (首尾帧/故事板/手搓画布), NOT a dropdown:

```html
<div class="mode-row">
  <button class="mode-btn" data-mode="frame" disabled><span>&#x1F5BC;</span>首尾帧</button>
  <button class="mode-btn" data-mode="story"><span>&#x1F3AC;</span>故事板</button>
  <button class="mode-btn" data-mode="manual"><span>&#x270F;</span>手搓画布</button>
</div>
```

**Unified highlighting** — all three buttons use the same `data-mode` + `.active` toggle, no separate `#tManualCanvas` logic:

```javascript
// In switchCanvasMode() and init:
document.querySelectorAll(".mode-btn").forEach(function(b){
  b.classList.toggle("active", b.dataset.mode === mode);
});
```

**Removed from the sidebar:** The old `#tManualCanvas` button (separate from the dropdown), `#modeTrigger`, `#modeDropdown`, and all dropdown open/close event listeners. The `.mode-row` is at the top of `.tbar-left`.

**CSS:**
```css
.mode-row{display:flex;gap:0;padding:4px 6px;border-bottom:1px solid #eee;background:#fafafa}
.mode-row .mode-btn{flex:1;padding:6px 4px;text-align:center;border:none;background:transparent;border-radius:6px;cursor:pointer;font-family:inherit;font-size:11px;color:#999;transition:all 150ms;line-height:1.2}
.mode-row .mode-btn span{display:block;font-size:14px;margin:0 auto 2px}
.mode-row .mode-btn:hover{color:#e67e22;background:#f5f5f5}
.mode-row .mode-btn.active{color:#e67e22;font-weight:600;background:#fff3e0}
.mode-row .mode-btn[data-mode="frame"]{opacity:0.4;cursor:not-allowed}
```

**Pitfall:** When adding context menu filtering by mode (hiding storyboard nodes in manual mode and vice versa), do NOT hide the mode-toggle button itself (`#tManualCanvas` was hidden by an early attempt — this prevents the user from switching back since the dropdown no longer has a "手搓画布" option). Keep all three `.mode-row` buttons always visible; only hide context menu items and mode-specific sidebar buttons (剧本创作/导入剧本).

### Storyboard Prompt Generation — Master Template

`exGenPrompt()` (triggered by `✨ 生成提示词` on sboard nodes) now generates prompts using a professional cinema production design board template instead of simple frame-by-frame output:

```javascript
var sysPrompt = "你是一个专业的影视分镜设计提示词工程师。请根据分镜描述，生成一段完整的图像生成提示词，用于AI生成一张电影级分镜设计图。\n\n"+
  "提示词结构必须严格遵循以下格式，填入分镜内容：\n\n"+
  "[参考图声明]\n"+
  "I have uploaded reference images. All visual information from these reference images must be maintained. Reference images are the primary visual authority.\n\n"+
  "[视觉风格]\n"+
  "- Background: deep charcoal #0A0A0C with subtle 2-3% film grain noise overlay\n"+
  "- Accent color: antique gold gradient #D4AF37 to dark bronze #8B6508\n"+
  "- Border: double-line gold frame 4-5px with geometric filigree corner brackets\n"+
  "- Inner frame: thin gold line 1px inset 20px\n"+
  "- Section headers: white bold sans-serif with bracket prefix\n"+
  "- Main title: serif font with gold gradient fill\n"+
  "- Body text: light grey #CCCCCC\n"+
  (styleP?"- Style requirement: "+styleP+"\n":"")+
  "- Film grain texture\n\n"+
  "[三区布局]\n"+
  "ZONE 01 - HEADER 35%: Left 30% Genre/Synopsis panel + Right 70% Hero Image 2.39:1\n"+
  "ZONE 02 - MIDDLE LEFT 40%: Character Design panel (turnaround + portrait + leader lines)\n"+
  "ZONE 03 - MIDDLE RIGHT 60%: Storyboard strip - X panels in horizontal row, 2.39:1 each, numbered\n"+
  "ZONE 04 - FOOTER TOP: Camera Movement timeline with arrows\n"+
  "ZONE 05-08 - FOOTER BOTTOM: Color Palette + Lighting Reference + Props Reference + Lens Parameters\n\n"+
  "分镜内容（填入ZONE 03的故事板面板）：\n" + desc + "\n\n"+
  "【OUTPUT FORMAT】Single image, 16:9 landscape. Dark background #0A0A0C with gold accents. Double-line gold border. No text generation.\n\n"+
  "要求：1.只输出最终的提示词本身 2.提示词用英文 3.保持母版结构完整 4.角色/场景/道具必须与参考图一致 5.输出为一个完整的单段提示词";
```

The master template file is at `D:\AI视频号\分镜板研究\￥￥￥分镜设计图-提示词母版.md`. It describes a 3-zone layout with gold/charcoal visual style, filigree corner brackets, and multi-panel storyboard strips.

**Flow change:** The master template is now applied at the PROMPT GENERATION step (exGenPrompt), NOT at the image generation step (exGenSBImg). The prompt node now contains a full master-template-structured prompt. `exGenSBImg` just passes this prompt directly to the image API with reference images.

Reference: The master md file has two variants — Variant A (3-zone horizontal for action) and Variant B (3x3 grid for drama). Current implementation uses Variant A structure.

### Img2Img with Asset Reference Images

`exGenSBImg` sends connected asset images as reference images for image-to-image generation. Reference images come from TWO sources:

1. **`collectRefNodes(nd.id, 0)`** — traverses connections from the image-gen node to find directly-connected `asset`/`image` nodes with `meta.img` (used in storyboard mode along the `sboard → prompt → image-gen` chain)
2. **`nd.meta._igRefs`** — reference images uploaded/dragged via floating panel (used in manual mode)

```javascript
var refNodes = collectRefNodes(nd.id, 0);
var refImages = refNodes.map(n => n.meta.img);
var panelRefs = nd.meta && nd.meta._igRefs || [];
panelRefs.forEach(r => refImages.push(r.data));
```

#### Reference Image Storage

References added via the floating panel go through `igAddRef(data, name)`:
```javascript
function igAddRef(data, name){
  _igRefs.push({data: data, name: name || '图片'});
  if(_igFpNode){
    _igFpNode.meta = _igFpNode.meta || {};
    _igFpNode.meta._igRefs = _igRefs.slice();  // persist to node meta
  }
}
```

Saved per-node in `nd.meta._igRefs`, persisted in localStorage via `saveCvs()`. Restored on panel open in `toggleIgFloatingPanel`:
```javascript
_igRefs = (nd.meta && nd.meta._igRefs) ? nd.meta._igRefs.slice() : [];
```

#### Critical Mode Issue: async vs sync

`exGenSBImg` sends `body.mode = "async"` for all requests. The server (`server.js`) then:

1. Tries `/v1/images/edits` (sync FormData) first → this correctly passes ref images
2. If edits fails → falls back to async `/v1/images/generations/async` with JSON body

**The fallback is broken:** The async generations endpoint `POST /v1/images/generations` does NOT support `image` or `images` fields (it's a text-to-image endpoint). The reference images in `payload.image` / `payload.images` are silently ignored.

**Fix:** When ref images are present, use `mode: "sync"` instead of `"async"` so the server goes through the edits endpoint:

```javascript
body = { prompt: prompt, config: { model: s.model, size: size, quality: "auto",
  mode: refImages.length ? "sync" : "async" } };
if (refImages.length) body.images = refImages;
```

This is now applied in the frontend code at `exGenSBImg` (both `app/public/` and `canvas/` copies). See `references/image-video-api-format.md` for the full payload format.

#### Drag-and-Drop from Asset Library (Replaces 📁📚 Upload)

**Removed (2026-06-28):** The igRefBar (📁 upload, 📚 asset library picker, thumbnail previews), `igRenderRefs()`, `igRemoveRef()`, `igSyncConnectedRefs()`, `showIgFpRef()`, `clearIgFpRef()`, and all related upload/asset-picker event handlers were removed from the floating panel.

**Replacement:** Image nodes in manual mode now support drag-and-drop from the asset library. The asset library items already have `draggable=true` with `text/asset-id` drag data format. On drop, the node looks up the asset by ID and calls `igAddRef(asset.data, asset.name)` to save the ref. The panel auto-switches to 图生图 tab.

#### Server-Side Image Conversion (`imgToDataUrl`)

When `body.images` reaches `handleImageGenerate()` in `server.js`, HTTP URLs are fetched server-side and converted to data URLs. If the fetch fails, the original URL is returned — the upstream API cannot access localhost URLs. Always store refs as data URLs when possible.

### Drag/Pan Performance Optimization

The mousemove handler was bottlenecked by calling `updConns()` and `resolveOverlap()` on EVERY frame during drag and pan:

**Removed from mousemove drag handler:**
- `updConns()` — SVG connection lines don't need to redraw during drag; the SVG moves via CSS transform automatically
- `resolveOverlap(nd, true)` — collision detection loop is expensive; only needs to run once on mouseup

**Removed from `updTf()` (called on every pan/zoom):**
- `updConns()` — SVG paths follow the canvas transform; no need to recalculate on each frame

**Added to mouseup handler:**
- `resolveOverlap(nd2, true)` — resolve conflicts once when drag ends
- `updConns()` — redraw connections once when drag ends

**Result:** Both canvas pan (blank space drag) and node drag are now smooth because no SVG path recalculations or overlap checks happen during frame-by-frame movement. Only position updates (CSS left/top) happen on each mousemove.



### Connection Lines Z-Index Fix

Connection lines (SVG paths) were invisible when a node was selected. Root cause: `#viewport` had `z-index: 5` while `#svg` had `z-index: 2`. The viewport's opaque white background (`background:#fff`) visually covered the SVG paths.

**Fix:** Set `#svg` to `z-index: 6` (higher than `#viewport`'s `z-index: 5`). The SVG container has `pointer-events: none` so it doesn't interfere with node clicks. Individual path elements have `pointer-events: stroke` for click/delete interactions.

Connection visibility logic remains unchanged — `updConns()` only draws paths when `S.sel.length > 0`, and only for connections involving selected nodes.

### Custom Image File Naming

Generated image filenames default to timestamps. To name files after the node title:

**Client side** (canvas.html `exImg`): Send `title: nd.title` in the request body to `/api/image/generate`.

**Server side** (`server.js`):
1. Store title by jobId: `pendingImageNames.set(jobId, payload.title)` in `handleImageGenerate`
2. Retrieve on status poll: `const customName = pendingImageNames.get(jobId)` in `handleImageStatus`
3. Pass to `saveImage(imageData, model, customName)`
4. In `saveImage`, prepend the custom name: `const namePart = customName ? sanitizeFileName(customName, "image") : "image";`

This produces files like: `第1集-1 首帧图-20260614-234523-gpt-image-2.png` instead of `image-20260614-234523-gpt-image-2.png`.

### exGenEndImg Y Position — Must Account for Parent Node Height

In `exGenEndImg`, the end frame image node was placed at `nn.y=nd.y+100` where `nd` is the **end prompt** node. Since end prompt has `h:160`, this created a **60px overlap** (endImg top at y+100 is still inside endPrompt, which spans y to y+160):

```javascript
// ❌ BUG: overlaps with endPrompt by 60px → resolveOverlap pushes it → node "jumps"
nn.y=nd.y+100;

// ✅ FIX: endImg top = endPrompt bottom + 20px gap
nn.y=nd.y+nd.h+20;     // dynamic: 160 + 20 = 180
// OR hardcode the known total:
nn.y=nd.y+180;
```

**Pitfall:** When placing a child node below a parent, NEVER use a small hardcoded offset like `+100` or `+50`. Always use `parent.y + parent.h + gap` so the placement survives height changes. The same bug pattern affected `exGenEndPrompt` (which correctly uses `nd.y+180` for prompt below sboard) vs `exGenEndImg` (which used `nd.y+100` instead of `nd.y+ndt.h+20`).

**Result of the bug:** `addNode` calls `resolveOverlap(nd,true)` internally, which detects the 60px overlap and pushes the endImg out of position → user sees it "jump" to an unexpected location → user drags it back → overlap resolution cascades → canvas becomes laggy.

**Verification:** After fixing, always sync both files (`app/public/canvas.html` and `canvas/canvas.html`).

### Video Prompt Auto-Popup Rule

When clicking "生成首帧图" (from start prompt node), `exGenStartImg` auto-creates a storyboard video prompt node (分镜视频提示词, 360×340) to the right of the start image, and connects start image → video prompt. If the end image already exists (user created end image first), it also connects end image → video prompt.

When clicking "生成尾帧图" (from end prompt node), `exGenEndImg` ONLY creates the end image node and connects it to the existing video prompt (found via the start image's connection). It does NOT create a new video prompt node — the video prompt should only exist once per sboard.

**Old code removed:** `exImg` (~line 2602) had legacy code that auto-created a "视频提示词" node after any image generation. This was for the old storyboard flow (sboard → prompt(storyboard) → image-gen → prompt(video)). In the new start-end frame flow, this creates DUPLICATE video prompt nodes. **If you see a second video prompt node popping up after image generation, search for `addNode.*视频提示词` inside `exImg` and remove it.**

**Image generation does NOT create its own video prompt.** When the user clicks "生成图片" on the start/end image nodes, `exImg` generates the image and stores it in `nd.meta.img` — no additional nodes are created. The video prompt node was already created by `exGenStartImg` when the start image node appeared. This is the fix for the issue where image generation would create duplicate "视频提示词" nodes.

When using the `patch` tool to add event handler code like `classList.contains(\"sel\")`, the quoted string `\"sel\"` (escaped double-quotes inside a double-quoted JS string) can be serialized incorrectly by the tool, producing literal backslash characters `\\"` instead of just `"`.

**Symptoms:** The page loads but no JavaScript works — canvas is blank, no nodes render, no interactive features respond. The browser console shows `SyntaxError: Unexpected token '\\'` or similar at the affected line.

**Fix:** After patching any JavaScript that contains escaped quotes within strings, verify by reading the affected line from the file. Look for `\\"` where there should be `\"`. The pattern `classList.contains(\\"sel\\")` should be `classList.contains(\"sel\")`. If you find extra backslashes, patch again to replace `\\\"` with `\"`.

**Prevention:** When writing patch old_string/new_string that contains `\"` inside JavaScript string literals, use single quotes for the outer string in JavaScript to avoid escaping entirely: `classList.contains('sel')` instead of `classList.contains(\"sel\")`.

### Follow Existing Patterns When Adding UI

When adding new UI elements (especially node footers, buttons, control bars), **always look at how similar existing node types implement the same pattern first**. The canvas has established conventions:

- **Bottom buttons** → use `sboard-footer`/`ep-footer`/`script-btns` pattern (footer div inside `nbd`, NOT in separate `foot`/`nft`)
- **Control bar at top** → use `script-style`/`sw-ctrl` pattern (a div inside `nbd` with `flex-shrink:0`)
- **Textarea filling space** → textarea with `flex:1` inside a flex container

**Pitfall:** Inventing a new layout approach (e.g., `position:absolute` for buttons) instead of copying an existing pattern leads to spacing bugs, resize issues, and user frustration. The existing patterns have been battle-tested. **Copy first, customize second.**

### Cross-Mode Testing — Frame vs Storyboard

The canvas has two independent modes: **首尾帧** (`"frame"`) and **故事板** (`"story"`), selected via the toolbar dropdown and globally accessible as `var canvasMode`. When modifying code shared by both modes:

1. **Always check BOTH modes** after any change to shared code (sboard, prompt, image-gen, video-gen nodes, event handlers, context menus, toolbar)
2. **`renderNode` sboard footer** — The most common cross-mode concern. Sboard nodes show different buttons per mode. Never hardcode one mode's buttons
3. **Mode-specific data** is saved separately (`kc-cvs-frame` vs `kc-cvs-story` in localStorage). `canvasMode` determines which save key is used
4. **Init block** restores `canvasMode` from `kc-mode` localStorage key on page load
5. **Preview panel** (`sb-panel`) is only relevant in storyboard mode — it doesn't appear in frame mode
6. The user considers frame and storyboard modes **independent** — fixing one must not break the other. Test both before marking done

### Syntax Validation — Mandatory After Any JS Edit

After EVERY edit to the inline `<script>` in canvas.html, validate the entire script syntax. A single missing `"`, `)`, or `}` causes the ENTIRE script block to fail parsing — no JavaScript executes, no error appears in the user's workflow (the canvas simply freezes with `S undefined`).

**Validation method:**
```bash
# Extract the script content and check with node
node -e "
const fs = require('fs');
const c = fs.readFileSync('canvas.html', 'utf-8');
const s = c.slice(c.indexOf('<script>') + 8, c.indexOf('</script>')).replace(/\r/g, '');
try { new Function(s); console.log('OK'); } catch(e) { console.log(e.message); }
"
```

Or write to a temp file:
```bash
node -e "
const fs = require('fs');
const c = fs.readFileSync('canvas.html', 'utf-8');
const s = c.slice(c.indexOf('<script>') + 8, c.indexOf('</script>')).replace(/\r/g, '');
fs.writeFileSync('/tmp/_check.js', s, 'utf-8');
" && node --check /tmp/_check.js
```

**Common syntax error patterns found in this codebase:**

1. **Missing closing quote on string concatenation** — `+S.zoom+");` (line 514, `updTf`). The `)` must be INSIDE the string: `+S.zoom+")";`. Compare with the correct pattern on lines 515/517.
   
   **Detection:** The canvas loads, toolbar renders, but `S` is undefined. All elements after the script tag are missing. No red errors in console.

2. **IIFE scope cross-reference** — Code in one IIFE referencing `var`-declared functions/variables from another IIFE. The `repairBrokenImages` function called `openDB()` and `STORE` which were declared inside the asset library IIFE. **Fix:** Either move the code inside the source IIFE, or expose on `window`.

3. **Missing IIFE closing brace** — When restructuring IIFE boundaries (moving code in/out), the closing `})();` can be accidentally dropped. After any IIFE restructuring, count braces: `grep -o '[{}()]' | sort | uniq -c` for the affected section.

4. **`repairBrokenImages` IIFE scope bug** — This function (auto-repair of stale 127.0.0.1:5788 image URLs) was split into its own IIFE outside the asset library IIFE, where `openDB()` and `STORE` were declared. Since `var` declarations are IIFE-scoped (not hoisted across IIFEs), `repairBrokenImages` throws `ReferenceError: openDB is not defined`. **Fix:** Move the repair code INSIDE the asset library IIFE (before `renderAssets()`), or expose `openDB` on `window`.

**Pitfall:** When the entire script fails to parse, the browser reports `Uncaught SyntaxError: Invalid or unexpected token` at some early line of the script (not the actual error line). The `node --check` method reports the EXACT error line in the extracted script. Map back to HTML line by adding `<script>` start line offset.

### patch() Fuzzy Matching — `old_string` Can Eat Partial Line

When using `patch()` with `old_string`, the fuzzy matcher can match a SUBSTRING of a line, eating the rest of the line that wasn't in the old_string.

**Bug scenario:**
```javascript
// old_string ends with:
  if(savedMode!==
// But the REAL line is:
  if(savedMode!=="story"){
// patch matches "if(savedMode!==" and drops the remaining "\"story"){" →
// Result: "  if(savedMode!==   " — SyntaxError, entire script fails
```

**Fix — always include 2-3 surrounding lines in old_string** so the match is unique and the full line is preserved:

```javascript
// ✅ Correct — include enough context that the match captures the full condition
old_string: "  document.getElementById(\"tScriptL\").style.display=\"none\";\n  }\n  if(savedMode!==\"story\"){\n    document.getElementById(\"canvas-\"+savedMode).style.display=\"\";"
new_string: "...replacement with same surrounding lines..."
```

**Prevention rule:** NEVER end `old_string` with a partial line like `if(x!==` or `if(x==` — always include the full condition including the comparison value, the `){`, and enough surrounding context (2+ lines before/after) to make the match unambiguous. This applies to any `old_string` that contains `if(`, `else{`, `function(`, or any control flow keyword where the condition might extend beyond what you wrote.

The canvas has TWO independent modes (首尾帧 `"frame"` and 故事板 `"story"`), determined by `var canvasMode`. They share the same codebase but have separate:
- **localStorage save keys:** `kc-cvs-frame` vs `kc-cvs-story`
- **sboard node footer buttons:** frame mode shows 首帧提示词+尾帧提示词; storyboard shows 生成提示词
- **Preview panel:** `sb-panel` only active in storyboard mode

**Mandatory verification steps after ANY code change:**
1. Test in frame mode — create a sboard → click 首帧提示词 → click 尾帧提示词 → verify both image nodes appear
2. Switch to storyboard mode → create a sboard → verify it shows `✨ 生成提示词` NOT the frame-mode buttons
3. Switch BACK to frame mode → verify the previously created nodes are restored
4. If you modified `renderNode`'s sboard section, CSS, or any shared function, check BOTH modes visually

**Pitfall:** The user will catch a mode-specific breakage immediately and will point it out. Do not assume "this change only affects frame mode" — the sboard render path is shared.

### `updTf` String Concatenation — Missing Quote = Entire Script Fails

The `updTf()` function sets `cv.style.transform` using string concatenation. A missing closing `"` on line 514 (`cv.style.transform`) caused the ENTIRE `<script>` block to fail parsing — no JavaScript executed, canvas was completely frozen with no console errors.

```javascript
// ❌ BUG: +S.zoom+"); — the string "); has no closing "
cv.style.transform="translate("+S.px+"px,"+S.py+"px) scale("+S.zoom+");

// ✅ FIX: compare with lines 515-517 which are correct
svg.style.transform="translate("+S.px+"px,"+S.py+"px) scale("+S.zoom+")";  // correct
grd.style.transform="translate("+(S.px%20)+"px,"+(S.py%20)+"px) scale("+S.zoom+")";  // correct
```

The `+S.zoom+")"` pattern: the closing parenthesis `)` must be INSIDE the string literal `")"`, not outside. `+S.zoom+")";` = string `")"` (contains `)`) + statement terminator `;`. But `+S.zoom+");` = string `");` which has NO closing `"`, causing an "Invalid or unexpected token" at parse time.

**Detection:** If the entire canvas script fails to execute (S undefined, toolbar incomplete, "节点: 0" stuck), use Node.js to validate the script:
```bash
node --check extracted_script.js
```

The error position in the HTML file can be found by extracting the `<script>` content and checking with `new Function(script)`.

**Root cause:** The original code likely had a copy-paste error between line 514 (cv) and line 515 (svg). Line 515 correctly uses `+S.zoom+")";` but line 514 was missing the closing `"` before `)`.

### ENHANCEMENTS V2 IIFE — Double Event Binding Bug

The `ENHANCEMENTS V2` IIFE (line ~2297) wraps `renderNode`:

```javascript
(function(){
var origRender=renderNode;
renderNode=function(nd){
  origRender(nd);   // ← already binds events
  var el=document.getElementById(nd.id);if(!el)return;
  var nbd=el.querySelector(".nbd");if(!nbd)return;
  // ...
  // BUG: re-binds same events as origRender!
  if(nd.type==="script"){
    // sf-left click + file input change — DUPLICATE of ~line 1176-1194
    // optimize/split/extract buttons — DUPLICATE of ~line 1196-1200
  }
};
```

**Bug**: `origRender(nd)` already binds ALL script node events: file input picker (`sf-left` click + `input[type=file]` change at ~line 1176), and data-action buttons (`optimize`, `split`, `extract` at ~line 1196). The V2 wrapper re-binds ALL of these on the same DOM elements. Each click fires TWICE: file picker opens 2 dialogs, split runs 2x, extract runs 2x, optimize runs 2x.

**Affected elements (all inside script node block):**
- `.sf-left` click → `fi.click()` (opens file dialog twice)
- `input[type=file]` change → `FileReader` loads twice
- `[data-action='optimize']` → `execN(nd.id,'optimize')` fires twice
- `[data-action='split']` → `execN(nd.id,'split')` fires twice
- `[data-action='extract']` → `exExtract(nd,el)` fires twice

**Fix**: The V2 wrapper should ONLY ADD V2-specific behaviors (textarea input, style-sel change). Remove ALL duplicate bindings from the script node block in the V2 wrapper. If adding new buttons in the future inside the V2 wrapper, always check `origRender` first to avoid double-binding.

**Prevention**: Before adding any event binding in the V2 wrapper, grep for the selector in `renderNode()` — if it's already bound in `origRender`, skip it. The V2 wrapper pattern should be: `origRender(nd)` handles all standard bindings, then V2 only adds enhancements that don't exist in the original.

**Prevention**: Before adding any event binding in the V2 wrapper, check if `origRender()` already binds it (especially for `data-action` buttons in the footer/nbd). The V2 wrapper should only ADD behaviors, not re-implement existing ones. Same applies to any future `origRender`-style wrapping.

### Duration Must Be Traced Directly at Click Time

When propagating duration from sboard → video-gen, **do NOT rely on `meta.duration` being passed through intermediate nodes** — it can get lost in the chain (especially across save/load cycles). Instead, **trace directly from the sboard at creation time** (`mkvideo` handler):

```
prompt(video) → image-gen(分镜图) → prompt(storyboard) → sboard
```

Parse `"6s | ..."` from sboard content with `/^(\d+)s\s*\|/`.

Fallback to `nd.meta.duration` only if direct trace yields no result. This approach works even for video-gen nodes loaded from saved canvas state (the tracing runs fresh each time).

### Console.Log Debugging for Canvas Bugs

When a canvas feature doesn't visually work (e.g., dropdown value not set, button not showing), add `console.log` statements at the render site inside `renderNode()` and at the creation site (click handler). Common things to log:

```javascript
console.log("[mkvideo] tracing: nd.id=",nd.id,"nd.meta=",JSON.stringify(nd.meta));
console.log("[mkvideo] igNode:",mkIgNode?mkIgNode.id:'null');
console.log("[vg-render] nd.id=",nd.id,"meta.duration=",nd.meta&&nd.meta.duration,"durSel=",durSel);
```

Check the browser console (F12) after re-creating nodes. This is faster than reading code to debug runtime state.

## Node Spacing Defaults

Both horizontal (parent→child) and vertical (sibling→sibling) node gaps default to **30px**. Key locations:

| Function | Direction | Variable | Value |
|----------|-----------|----------|-------|
| `exSplit` (剧本→分集) | Horizontal | `startX = nd.x+nd.w+30` | 30px |
| `exSplit` (分集↕) | Vertical | `nn.y = startY+i*(nh+gap)` where `gap=30` | 30px |
| `exStoryboard` (分集→分镜) | Horizontal | `startX = nd.x+nd.w+30` | 30px |
| `exStoryboard` (分镜↕) | Vertical | `nn.y = startY+i*(nh+gap)` where `gap=30` | 30px |
| `exExtract` (资产↕) | Vertical | `nn.y = startY+row*(nh+gap)` where `gap=30` | 30px |
| `exGenPrompt` (分镜→提示词) | Horizontal | `nn.x = nd.x+nd.w+30` | 30px |
| `exGenStartPrompt` (分镜→首帧提示词) | Horizontal | `nn.x = nd.x+nd.w+30; nn.y = nd.y` | 30px |
| `exGenEndPrompt` (分镜→尾帧提示词) | Horizontal+V| `nn.x = nd.x+nd.w+30; nn.y = nd.y+180` | 30px + 160h+20gap |
| `exGenStartImg` (首帧提示词→首帧图) | Horizontal | `nn.x = nd.x+nd.w+30; nn.y = nd.y` | 30px |
| `exGenEndImg` (尾帧提示词→尾帧图) | Horizontal+V | `nn.x = nd.x+nd.w+30; nn.y = nd.y+nd.h+20` | 30px + 160h+20gap = 180 |
| `exGenSBImage` (提示词→分镜图) | Horizontal | `nn.x = nd.x+nd.w+30` | 30px |
| `exGenSBImg` (分镜图→视频提示词) | Horizontal | `vnn.x = nd.x+nd.w+30` | 30px |
| `mkvideo` (视频提示词→分镜视频) | Horizontal | `vgn.x = nd.x+nd.w+30` | 30px |

Uniform 30px spacing avoids overlapping nodes and keeps the layout predictable.





### Dot Animation Performance: Pause During Drag

Reading `offsetWidth`/`offsetHeight` every frame in a requestAnimationFrame loop triggers forced layout (reflow). On large nodes this causes visible lag during drag. Fixes:

1. **Cache dimensions at animation start** — `var cw=e.offsetWidth, ch=e.offsetHeight;` read once, reuse in anim()
2. **Pause rAF during drag** — cancel the animation loop in the mousemove handler when dragging
3. **Resume on mouseup** — if node still has `.sel` class, restart the animation

Also apply the same pattern during node resize events.

### `_escHtml` Not Defined — Function Hoisting Failure

When adding new functions that reference `_escHtml()` (defined at the bottom of the file), the function hoisting may fail in strict mode depending on the execution context. Instead of relying on hoisting for a function defined later in the script, **inline the escape function locally**:

```javascript
// Don't do this (relies on hoisting):
function updateSboardPanel(nd,el){
  html += "..." + _escHtml(text) + "...";  // ReferenceError: _escHtml not defined
}

// Do this (self-contained):
function updateSboardPanel(nd,el){
  var _e = function(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); };
  html += "..." + _e(text) + "...";
}
```

**Root cause:** In strict mode, function declarations inside blocks are block-scoped. Even top-level function declarations may not be hoisted reliably when the script tag is large and contains complex IIFE structures. Always use local function variables for escape utilities consumed within the same function scope.

2. **CSS visibility** — Use `.visible` class NOT `inline style.display` for show/hide. But for sections within nodes, `style.display` is fine.
4. **Event delegation** — Context menu has delegated click handler at line ~1096. Don't add duplicate individual handlers.
5. **`el.querySelector('.nbtn')`** — Finds FIRST `.nbtn` in node. If node has multiple buttons, use `data-action` attribute and specific selectors.
6. **Canvas saves to localStorage** as `kc-cvs`. Config saves as `kc-cfg`. Both are JSON strings.
7. **API defaults**: LLM=`mimo-v2.5-pro` (Xiaomi API), Image=`gpt-image-2` (OpenAI-compatible), Video=`grok-imagine-video` (OpenAI-compatible), TTS=`mimo-v2.5-tts` (Xiaomi API). Image/Video use user-configured OpenAI-compatible endpoint (currently apihub.agnes-ai.com). LLM/TTS use Xiaomi API directly. **Never hardcode `127.0.0.1:5788` — the Gaia proxy was removed.** See "Image/Video API Format" section below.
8. **Node resizers** — hidden when panel is minimized to prevent click interception.
9. **`var` declarations** — Always declare variables with `var` before using in if/else blocks. `speakText` without `var` causes "not defined" runtime errors. Common pattern: `var speakText=text; if(cond){ speakText='modified'; }` NOT `if(cond){ speakText='modified'; }else{ speakText=text; }` (missing `var`).
10. **Undefined variable in `renderNode()` cascades to break everything** — If ANY variable referenced inside `renderNode()` is undefined (e.g. a removed button variable like `vgSaveBtn`), the ENTIRE `loadCvs()` fails because `renderNode()` throws during `forEach`. This means: no nodes render, no connections draw, the canvas appears empty. **When removing a variable, grep for ALL references to it in `renderNode()` HTML strings.** The error shows as `ReferenceError: X is not defined at renderNode` — check the line number in the HTML template string, not the function definition.
10. **Blob URLs vs Data URLs** — `URL.createObjectURL()` creates ephemeral `blob:` URLs that break when passed to external APIs. Always use `FileReader.readAsDataURL()` for data that needs to be stored or sent to APIs.
## Settings Panel

- Settings only stores URL + API key per service (LLM, Image, Video, TTS)
- Model/voice/parameter selection is done IN the node, not in settings
- TTS settings: URL defaults to `https://api.xiaomimimo.com/v1/chat/completions`, has a "测试连接" button
- After removing fields from settings HTML, also update `saveSet()` and `openSet()` functions to avoid silent failures from missing `getElementById` elements
12. **TTS API endpoint** — MiMo TTS uses `/v1/chat/completions` (same as LLM), NOT `/v1/audio/speech`. Auth header is `api-key`, not `Authorization: Bearer`.
13. **Image/Video API format** — Uses standard OpenAI-compatible format, NOT the old Gaia proxy format. Request: `{model, prompt, size, n:1}` with `Authorization: Bearer <key>` header. Response: `{data: [{url: "..."}]}`. Key functions: `exImg()`, `exGenSBImg()`, `exGenAsset()` for images; `exVid()`, `exGenVideo()` for video. All follow the same pattern. Old format `{prompt, config:{model,size,quality,mode}}` with `{success, outputUrl}` response is OBSOLETE — never use it.
14. **loadCfg() auto-migration pitfall** — `loadCfg()` previously had code that force-overrode any non-5788 image/video URLs back to `http://127.0.0.1:5788/api/image/generate`. This was removed. If you ever see image generation silently using wrong URLs, check if someone re-added auto-migration logic in `loadCfg()`. The user's saved settings in localStorage should be respected as-is.
15. **API connectivity debugging** — When canvas generation fails: (a) check if the service port is listening (`netstat -ano | grep :PORT`), (b) verify request format matches API docs, (c) check response parsing matches actual response shape, (d) look for any code overriding user settings (like the old loadCfg migration). See `references/image-video-api-format.md` for full API format details and debugging steps.
16. **`fixImgUrl()` / `fixVidUrl()` — defensive URL normalization** — Users often enter partial URLs in settings (e.g. just `https://apihub.agnes-ai.com/v1` without `/images/generations`). All image generation functions call `fixImgUrl(s.url)` and video functions call `fixVidUrl(s.url)` to auto-append the correct endpoint path. Never pass `s.url` directly to fetch for image/video generation — always wrap with the appropriate fix function. `fixImgUrl` appends `/images/generations`, `fixVidUrl` appends `/video/generate`.

**⚠️ 必须同步更新 `isGaiaTester`：** 在 `fixVidUrl`/`fixImgUrl` 中新增代理路径识别时，`isGaiaTester()` 也必须同步更新，否则 `isGaiaTester` 返回 false 导致请求不走服务端轮询协议。详见 `references/proxy-url-recognition.md`。 See `references/image-video-api-format.md` for the normalization logic.
17. **Response parsing — always read as text first** — Don't use `r.json()` directly. Read as `r.text()` first, log it to console, then parse. This gives better debugging output when the API returns unexpected formats. Also handle both `data[0].url` and `data[0].b64_json` response shapes.
18. **HTTP status as debugging signal** — When generation fails, the status code tells you what's wrong: **404** = wrong URL path (missing `/images/generations` suffix), **400** = wrong request body (e.g. unsupported `response_format` parameter), **401** = bad API key. Always check the console for the actual request URL and response body. See `references/image-video-api-format.md` for full table.
19. **Always check `r.ok` before `r.json()`** — The "Unexpected end of JSON input" error on `r.json()` means the HTTP response body is empty or non-JSON. This almost always happens when the API returned an error status (4xx/5xx) with an empty body, but the code didn't check `r.ok`. Always guard: `if(!r.ok){var errText=await r.text();throw new Error("HTTP "+r.status+": "+errText);} var d=await r.json();`. This converts cryptic "Unexpected end of JSON input" into a readable error with status code.

---

## Cross-Session SPA Patterns

### Drag-Drop from Asset Library

Asset library items (`ct-lib`) set `text/asset-id` drag data. Drop handlers can be added to any node type:

- **Global `dragover` handler** is REQUIRED: `document.addEventListener("dragover", function(e){e.preventDefault()})` — without it, browser blocks drops for custom drag data types.
- For `image` nodes: save image data to `nd.meta._igRefs` (read by `exGenSBImg` via `panelRefs`)
- For `asset` nodes: save image data to `nd.meta.img` (displayed in `.preview` )
- Always call `saveCvs()` after drop for refresh persistence
- Use `el.querySelector(".nbd .preview, .nbd .ig-preview")` to find the preview element
- Do NOT create separate asset nodes on drop — store directly in target node's meta

### Reference Image Collection in exGenSBImg

`exGenSBImg` collects refs via two paths:
1. `collectRefNodes(nd.id, 0)` — traverses `S.conns` to find connected `asset` nodes, reads `n.meta.img`
2. `nd.meta._igRefs` — directly stored refs from drag-drop or upload

Debug with console messages:
- `[exGenSBImg] collectRefNodes result: N nodes, images: M`
- `[exGenSBImg] panelRefs from node meta: N items`
- `[exGenSBImg] final refImages count: N`

### Style Sync Before Generation

The floating panel gen button handler syncs the selected style to `S.cfg.style` so `getStylePrefix()` reads it:
```javascript
S.cfg.style = document.getElementById("igFpStyle").value;
```

## Absorbed Skills

This umbrella skill consolidates content from previously-separate skills covering the same Kairos Canvas codebase:

### Exhaustive Development Notes
See `references/development-guide.md` for the complete development log from `kairos-canvas-development` — including scriptwriter node iterations (5+ failed approaches to final rebuild), storyboard master template evolution (9 iterations), multi-image reference flow, reference image compression, server management with Chinese dir paths, and patching/sed recovery pitfalls.

### Storyboard Prompt & Video Pipeline
See `references/storyboard-pipeline.md` for the storyboard-to-video pipeline specifics from `canvas-storyboard-engineering` — two-stage image pipeline, video pipeline with Seedance 2.0 reference mode, grid generation rules (no borders/numbers), frame count calculation, and video prompt templates.

### Generic HTML Canvas Patterns
See `references/html-canvas-patterns.md` for generalized HTML canvas node app patterns from `html-canvas-app` — generic renderNode() flow, SVG connection line architecture, node type registry, SDots rAF effect, and triple-layer style persistence.
