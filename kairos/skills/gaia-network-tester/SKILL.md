---
name: "gaia-network-tester"
description: "为 GaiaNetworkTester（D:\\AI_work\\GaiaNetworkTester\\）开发和维护前端页面。Node.js 服务端、多页面前端、图像/视频生成 API 测试工具。覆盖新增页面、修改配置、模型列表分类、常见坑。Consolidated umbrella for gaia-network-tester-frontend and gaia-video-factory-home"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\.archive\\gaia-network-tester\\SKILL.md"
---
# GaiaNetworkTester 前端开发指南

## 项目结构

```
D:/AI_work/GaiaNetworkTester/
├── app/
│   ├── server.js          # Node.js 服务端（端口: $PORT 或 5788）
│   ├── public/
│   │   ├── index.html     # 原主页（生图+图生视频 Tab，已被 home.html 取代）
│   │   ├── home.html      # 新主页（4功能模式） ← Node `/` 实际指向这里
│   │   ├── Kairos_canvas_A2_master.html  # 当前默认画布（被 server.js 硬编码引用）
│   │   ├── Kairos_canvas_*.html          # 大量画布快照/备份文件
│   │   ├── canvas.html / canvas_bak.html # 旧画布文件（保留备份，新版用 Kairos_canvas_*）
│   │   ├── styles.css     # 共用样式
│   │   ├── home.js / home_styles.js / home_assets.js # 新主页逻辑（按职责拆分）
│   │   └── app.js         # 原主页逻辑
│   └── node_modules/
├── serve_canvas_5788.py   # Python 静态服务器，端口 5788，`/` → 画布 HTML（无 home 入口）
├── start-windows.bat      # 默认启 Node（start "" http://...:5788 + node app/server.js）
├── config.json            # 服务端配置
├── config.example.json    # 配置模板
├── outputs/               # 生成结果输出
│   ├── image/
│   └── video/
├── logs/run.log           # 运行日志
└── runtime/node.exe       # 打包的 Node 运行时（避免依赖系统 Node）
```

## 服务端

- 位置: `app/server.js`（生产，Node）/ `serve_canvas_5788.py`（Python 简易版，画布直出）
- 端口: `process.env.PORT` 或 5788（默认）—— **两者都监听 5788，互斥，不能同时跑**
- ⚠️ **重要**: bash 环境里 `$PORT` 可能已设为 8648（Hermes Web UI），启动时必须显式指定 `PORT=5788`
- ⚠️ **重要（双 launcher）**: 5788 有两个入口，任何"5788 主页/画布"类修改必须同时改两边：

  | Launcher | 入口文件 | 主页规则 | 实际画布文件 |
  |---|---|---|---|
  | Node `start-windows.bat` / `node app/server.js` | `app/server.js` | `/` → `home.html`（4 模式卡片页） | `/canvas` 路由硬编码指向 `Kairos_canvas_<某快照>.html`（grep `PUBLIC_DIR, "Kairos_canvas` 找当前指向） |
  | Python `python serve_canvas_5788.py` | `serve_canvas_5788.py` | `/` **直接就是画布**（无 home 入口） | `do_GET` 里把 `self.path` 改写到 `Kairos_canvas_<某快照>.html` |

  例：用户说"把 5788 端口主页的画布指向 X.html" → 必须改 `serve_canvas_5788.py` 的 `do_GET` 和 `app/server.js` 的 `/canvas` 路由两处；只改 Node 的话用 Python 起的实例不变。

- 路由规则（Node server.js，2025-07 实际状态）:
  - `GET /` → `home.html`（不是 `index.html`；index.html 已废弃）
  - `GET /home` → `home.html`
  - `GET /canvas` → `Kairos_canvas_<硬编码文件名>.html`（grep `PUBLIC_DIR, "Kairos_canvas` 看当前指向哪个快照）
  - `GET /api/config` → 共享配置
  - `GET /api/models` → 模型列表
  - `POST /api/image/generate` → 图片生成
  - `POST /api/video/generate` → 视频生成
  - 其他 GET → 从 `public/` 目录静态文件服务

## 配置结构 (config.json)

```json
{
  "baseUrl": "https://api.gaiavideofactory.com/v1",
  "apiKey": "...",
  "image": {
    "model": "gpt-image-2",
    "size": "1280x720",
    "quality": "auto",
    "mode": "async"
  },
  "video": {
    "model": "grok-imagine-video-1.5-preview",
    "seconds": 6,
    "size": "1280x720",
    "resolutionName": "720p",
    "preset": "normal"
  }
}
```

## API 端点

### 图片生成 (`POST /api/image/generate`)
- 请求体: `{ prompt, negativePrompt?, images?: string[], config: { model, size, quality, mode } }`
- `images` 是 base64 data URL 数组（用于图生图）
- 响应: `{ outputUrl, outputPath, jobId? }`

### 视频生成 (`POST /api/video/generate`)
- 请求体: `{ prompt, negativePrompt?, imageDataUrl?, imageName?, images?: [{dataUrl, name}], config: { model, seconds, size, resolutionName } }`
- ⚠️ `images` 为可空数组 — 空数组表示文生视频（需在 server.js 中注释掉长度检查）
- 响应: `{ outputUrl, outputPath, taskId? }`

### 模型列表 (`GET /api/models`)
- 响应: `{ data: [{ id: string, ... }] }`
- 可能返回所有 API 支持的模型（包含图片和视频模型）

## 模型分类逻辑

从 /api/models 获取的模型列表需要按类型过滤：

```javascript
const imgKw = ["image", "dall-e", "gpt-image", "seedream", "flux", "sd", "stable-diffusion", "sdxl"];
const vidKw = ["video", "kling", "grok-imagine-video", "seedance", "sora", "pika", "runway", "luma", "haiper", "mochi", "minimax"];
// seedream → 图片模型, seedance → 视频模型
```

如果分类失败（没有匹配任何关键词），则所有模型归入两个列表。

## 前端开发约定

### 新增页面步骤
1. 在 `app/public/` 下创建 `.html` 和 `.js` 文件
2. 在 `server.js` 中添加路由: `if (req.method==='GET' && pathname==='/xxx') return serveStaticFile(req, res, PUBLIC_DIR, '/xxx.html');`
3. 重启服务

### 设置项自动切换
根据模式显示不同设置：
- **图片模式**（文生图/图生图）: 显示「质量」「模式（同步/异步）」
- **视频模式**（文生视频/图生视频）: 显示「时长」「清晰度」，隐藏「模式」
- **图生模式**（图生图/图生视频）: 显示参考图片上传区

### 比例 → Size 映射
```javascript
const SIZE_BY_RATIO = {
  "16:9": "1280x720",
  "9:16": "720x1280",
  "1:1": "720x720",
};
```

### 图片压缩
1600px 长边，JPEG 82% 质量，自动缩放：
```javascript
const longEdge = 1600;
const scale = Math.min(1, longEdge / Math.max(width, height));
// 用 canvas 缩放后 toDataURL("image/jpeg", 0.82)
```

## Gaia API 视频提交格式注意

### 必须用 multipart form data（不是 JSON body）

Gaia API 的 `POST /v1/videos` 端**只支持 multipart form data** 格式，不要用 JSON body。关键字段：

```
form.append("model", model)              // 模型 ID
form.append("prompt", prompt)             // 文本提示词
form.append("seconds", String(seconds))   // ⚠️ 必须是字符串，不能传数字！
form.append("size", "1280x720")          // 尺寸
form.append("resolution_name", "720p")    // 清晰度
form.append("preset", "normal")           // 预设（使用 input_reference[] 时用 normal）
form.append("input_reference[]", blob, fileName)  // 参考图（多张可重复 append）
```

**`seconds` 字段必须是字符串**，否则上游（火山引擎）会返回：
```
json: cannot unmarshal number into Go struct field .Alias.seconds of type string
```

### 图片参考 vs 文生视频

- **图生视频**: 传 `input_reference[]` 表单字段，每张图片一个 blob
- **文生视频**: 不传 `input_reference[]` 即可
- 不要用 JSON 的 `content` 数组格式（那是火山引擎直连 API 的格式，Gaia API 不支持）

### 轮询 + 下载容错

```javascript
// pollVideoJob 中：
// - 网络异常（catch）→ 跳过继续轮询
// - HTTP 5xx → 跳过继续轮询（不是立即抛异常）
// - 任务 completed → 优先用 job.result_url 下载

// downloadVideo 中：
// - 如果有 completedJob.result_url，用它拼接下载 URL
// - result_url 以 "/v1/" 开头，baseUrl 以 "/v1" 结尾
// - 需要去掉 baseUrl 末尾的 /v1 再拼接，避免双 /v1/v1/
```

## 每模块独立状态管理模式

GaiaNetworkTester 有 4 个功能模块（文生图/图生图/文生视频/图生视频），每个模块需要独立保存以下状态：

| 状态 | 缓存对象 | 说明 |
|------|---------|------|
| 参考图片 | `refImagesByMode` | 各模块上传的参考图 |
| 风格 | `perModeStyle` | 各模块选择的风格 |
| 音频 | `perModeAudio` | 各模块上传的音频文件 |
| 输出 | `perModeOutput` | 各模块的生成结果（URL、类型） |

### 切换模式的通用模式

在 `applyMode()` 中，先保存旧模块状态，再恢复新模块状态：

```javascript
// 保存
perModeAudio[prevMode] = [...audioFiles];
perModeOutput[prevMode] = { lastOutputUrl, outputType, imageSrc, videoSrc, emptyHidden };

// 恢复
const savedAudio = perModeAudio[mode];
audioFiles = savedAudio ? [...savedAudio] : [];
renderAudioPreview();
```

新模块若无保存记录，重置为默认值（如风格 → "无风格"，输出 → 默认提示文字）。

### 生成完成但用户已切走模块的处理

```javascript
generatingForMode = currentMode;
if (generatingForMode !== currentMode) {
  perModeOutput[generatingForMode] = { lastOutputUrl, outputType, imageSrc, emptyHidden: true };
}
```

### 输出区重置

切换模块时重置输出区为默认状态，而非"生成中..."：

```javascript
emptyOutput.innerHTML = DEFAULT_EMPTY_HTML;
```

## 视频生成流程（避免双重轮询）

`POST /api/video/generate` 已在服务端完成 submit → poll → download → return。客户端直接用 `result.outputUrl`，无需再轮询 status 端点。

## 用户界面偏好

### 4个功能图标
- **透明底色**：PNG 白色（≥248）转 alpha=0
- **紧密裁剪**：按 alpha>200 边界裁剪后铺满画布
- **去文字**：`.label` `.desc` → `display: none`
- **居中排列**：flex `justify-content: center`，gap 2px
- **大小**：125×125px

### 模型选择
- 不要自动切换，用户选哪个用哪个
- grok-imagine 检查只应在 `needsRef` 为 true 时拦截

### 时长选择
- `<select>` 下拉框逐秒 3-15，不接受 `<input type="number">`

### 比例选项
- `1:1 方形` → `1:1`

### 生成按钮位置
- `margin-left: auto` 推至最右

### 输出区样式
- 深色背景 `#0b1424`，所有文字白色
- mode-tag 移除，资产库按钮无 📁

## PNG 图标处理

```python
img = Image.open(path).convert('RGBA')
# 白色≥248 → alpha=0
# 裁剪 alpha>200 边界
# resize 至 1024x1024 铺满
```

## ⚠️ 关键坑点

### 1. CSS Grid 中 `overflow: hidden` 导致行高坍缩

当 Grid item 设置了 `overflow: hidden`，浏览器将其视作 scroll container，Grid 轨道最小内容尺寸会变为 0，导致整行被压缩到极矮（如 14px）。

**症状**: Grid 网格看起来完全被压扁，所有格子只有几像素高。

**修复**: 不要在 Grid item 上使用 `overflow: hidden`。改为移除它，并将 `border-radius` 直接加在子元素（如 img/video）上：
```css
.asset-item { overflow: visible; }
.asset-item img { border-radius: 7px; } /* 略小于父容器 border-radius 8px */
```

### 2. 前端 JS 与 CSS class 名不同步

JS 动态生成的 HTML 使用 `className = "asset-item"`，但 CSS 中只定义了 `.modal-grid-item` 的样式。这会导致资产库网格中的图片完全没有任何布局约束，以原始尺寸渲染出现「有大有小」的现象。

**原则**: 每次新增 JS 动态元素时，确认 CSS 中 class 名与之匹配，或者用逗号选择器同时覆盖两个 class：
```css
.modal-grid-item, .asset-item { ... }
```

### 3. 首尾帧模式父容器 visibility 未同步

`setVidRefMode()` 只切换了 `refSectionStartEnd` 子元素的 `hidden` class，但父容器 `frameUploadBox` 在 HTML 中有 `style="display:none"`，且 JS 没有控制它的显示。

**原则**: 当 HTML 中有嵌套的 `display:none` 容器 + JS 控制子元素显示时，**必须也同步控制父容器的 display**，否则子元素永远不可见。

**修复示例**:
```javascript
function setVidRefMode(mode) {
  // ...
  frameUploadBox.style.display = mode === "startend" ? "block" : "none";
  refSectionStartEnd.classList.toggle("hidden", mode !== "startend");
}
```

### 4. 下拉框 vs 数字输入框

用户偏好：时长选择使用下拉框（`<select>`）逐秒列出所有选项，不接受 `<input type="number">`。原因在于 number 输入框缺少直观的可选范围展示，而下拉框能一目了然地看到所有可选值。

### 4a. Kairos Canvas 节点命名映射

`Kairos_canvas_A2_master.html` 节点 UI 的中文显示名在三处映射，**改名三处必须全改**：

| 位置 | 作用 |
|---|---|
| `TNAMES[type]` (1343 行附近) | 节点 header 里的 badge |
| `dT(type)` (1345 行附近) | `addNode` 默认 title |
| 右键菜单 `<div class="mi" data-t="character"><span class="lb">数字人</span></div>` | 右键菜单 label |

**data-type 内部标识保持英文**（`character` / `scene` / `script` / `asset`），不要因为 UI 改名就改它——会连带破坏 extract / review assets / asset 创建脚本等所有 `n.type==="character"` 的判断。

**当前映射（2025-07-16）**：`character` → "数字人"（不再是"角色卡"），其它节点按 dT 字面值。

### 4b. 节点 UI 极简原则（inspector 已经包了执行入口）

`#a2InspectorRun`（836-846 行）已经处理：
- manual + image/video → "打开生成设置"
- 其他节点 → "执行当前节点"

**节点 body 内不要再加"生成"按钮**。底部加"生成 X"按钮会被用户判定为 UI 噪音立即删掉（实测）。只放非生成类的"操作"按钮：清空参考 / 删除 / 重置 / 切换显示模式。

### 4c. 节点底部操作栏贴底（margin-top:auto 套路）

节点 body 内含"可变高度内容 + 固定底部操作"时，footer 浮在中间不贴底。修法：

```css
.<node>-fields { display: flex; flex-direction: column; flex: 1; min-height: 0; }
.<node>-fields .<node>-footer { margin-top: auto; padding-top: 8px; display: flex; gap: 4px; }
```

完整模板和数字人节点 ref 上传（face/voice/digital human）模式见 `references/kairos-canvas-architecture.md` 第 1-4 节。

### 5. HTML 元素删除后必须同步移除 JS 引用
- 如果从 HTML 中删除一个元素（如 `id="refUploadBtn"`），必须从 JS 中移除对应的 `document.getElementById` 和所有 `addEventListener`
- 否则 JS 脚本会在执行到那行时抛出 `TypeError`，**导致整个脚本 silent crash** — 后续所有代码（包括 `loadConfig()`）都不执行
- 症状：模型下拉为空、页面没有配置日志、但 HTML 结构正常

### 2. 浏览器缓存
- 修改 `home.js` 文件后浏览器会缓存旧版本
- 解决办法：在 HTML 的 `<script src="/home.js?nocache=N">` 中使用递增的查询参数
- 同时 URL 上也加随机参数: `http://.../home?t=xxx`

### 3. 不要随意重启运行中的服务
- 用户的生产服务可能正在运行
- 需要重启时先确认用户同意
- 用 `powershell Stop-Process -Id PID -Force` 代替 `taskkill`

### 4. 端口冲突
- 默认端口 5788（GaiaNetworkTester）与 8648（Hermes Web UI）可能同时存在
- Git bash 中 `$PORT=8648` 可能导致服务器启动到错误端口
- 启动时强制指定: `PORT=5788 node app/server.js`

### 6. `app/public/` 里有大量 `Kairos_canvas_*.html` 快照文件
`Kairos_canvas.html`、`Kairos_canvas_A2_master.html`、`Kairos_canvas_master_20260713.html`、`Kairos_canvas_current_20260713_v2.html`、`Kairos_canvas_merged_review_*.html` 等等都是历史快照/备份。`server.js` 的 `/canvas` 路由和 `serve_canvas_5788.py` 的 `do_GET` 都硬编码指向其中**某一个**。修改"当前画布"就是改这两个硬编码字符串；不要去删旧快照，它们是版本历史。

### 7. 双 launcher 互斥
见上方"服务端 → 双 launcher"表格。两个入口都监听 5788，**同时跑会端口冲突**。如果 `netstat -ano | grep 5788` 显示有进程在占着，先 kill 再换 launcher 验证修改是否生效（参见坑点 3）。

### 8. Kairos Canvas A2_master.html（手搓画布）扩展
`app/public/Kairos_canvas_A2_master.html` 是节点画布编辑器，单文件 6800+ 行。扩展节点类型 / 加端口 / 改右键菜单之前**先读 `references/kairos-canvas-architecture.md`**——它覆盖了：renderNode + ENHANCEMENTS V2 二次包装、cvm/cnm 模式分组、浮动面板（igFloatingPanel/vgFloatingPanel）模式、4-port 共用系统、seedVgRefsFromConnectedNodes 自动 ref 抓取、TTS→audio-gen 多 tab 范式、AI Gateway 调用约定、以及 patch 误删/JS 校验/双 launcher 端口冲突等具体坑点。

---

## Absorbed Skills

### Frontend Patterns
See `references/frontend-patterns.md` for content absorbed from `gaia-network-tester-frontend` — per-mode independent state management, video ref mode toggle with parent/child visibility, @mention in prompts, audio multi-upload, and navigation link placement.

### Homepage Development
See `references/homepage-development.md` for content absorbed from `gaia-video-factory-homepage` — layout rules, 4K detection, video generation flow avoiding double polling, start/end frame mode, icon PNG processing, and CSS Grid overflow pitfalls.
