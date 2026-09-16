---
name: "kairos-canvas-development"
description: "Develop and maintain Kairos Canvas (canvas.html) — an infinite canvas SPA with node-based workflow. Use when adding new node types, modifying existing node rendering, fixing layout issues, or adding c"
priority: 0.5
imported-from: "agents"
source-path: "C:\\Users\\you\\.agents\\skills\\software-development\\kairos-canvas-development\\SKILL.md"
---
# Kairos Canvas 节点开发指南

Kairos Canvas (`app/public/canvas.html`) 是一个单 HTML 文件的无尽画布 SPA。所有节点类型、渲染、事件处理都在一个文件中。

## 连接线系统

连接线已重构为**直线**，颜色跟随**选中节点**类型色（非源节点）。端口点从 `::after` 伪元素改为 `<span class='port-dot'>` 真实 DOM 元素以实现 JS 动态着色。选中节点时端口点颜色 = 节点类型色；取消选中时恢复默认灰色。详见 `references/connectivity.md`。新增 `references/editor-node.md` 记录视频剪辑节点开发文档。新增 `references/rendering-failure.md` 记录页面渲染失败的调试方法。

## 行为准则

- 严格按用户字面要求执行，不自行扩展修改范围。用户说"只改 X"就不碰 Y。
- 用户反复强调过：没让改的不要自作主张改回来。**修改前先看现有实现，复制现有代码再改，不要凭空重写。**
- 用户极度厌恶反复试错不改根因。CSS/布局试3次以上没解决→彻底重建。**没让用户改的样式（线宽、虚线、端口大小等）绝对不要自作主张修改。**
- **部署陷阱**：`server.js` 的 `/canvas` 路由直接从 `public/Kairos_canvas.html` 文件读取。修改文件后即时生效，无需更新 base64（2026-06-20 已彻底移除 `_CANVAS_HTML_B64`）。如果文件读取失败，返回 500 错误。
- **Base64 缓存陷阱**：修改 `Kairos_canvas.html` 后如果画布表现异常（如连接线颜色不对、功能缺失），首先检查 `server.js` 末尾是否还残留 `_CANVAS_HTML_B64` 常量。如果有，必须同步更新 base64 值或删除 fallback 逻辑。旧版代码中 fallback 会被优先使用，导致文件修改"看似不生效"。
- **页面渲染失败陷阱**：修改 `Kairos_canvas.html` 后如果画布空白、右键菜单不弹、节点不显示，**第一步检查 DOM 是否渲染**：`document.querySelectorAll('div').length` 应为 > 0。如果为 0 或 `document.body.innerHTML` 为空，说明 JS 语法错误导致脚本中断（常见于 patch 引入的多余 `};` 破坏 IIFE 闭合）。用 `document.querySelector('script').textContent` 查看原始 JS，尝试 `eval()` 捕获具体语法错误。详见 `references/rendering-failure.md`。

## 节点类型注册矩阵

添加新节点需修改以下 **6 个位置**（全要改，缺一不可）：

| # | 位置 | 说明 | 示例 |
|---|------|------|------|
| 1 | **CSS** — `<style>` 区 | 节点容器 + 子元素 | `.node[data-type='xxx'] .nbd{padding:0}` |
| 2 | **ICONS/TNAMES/dW/dH/dT** | 图标、中文名、默认宽高、默认标题 | `scriptwriter:\"📝\"`, `dH:300` |
| 3 | **Context menu** — HTML `<div class="ctx" id="cvm">` | 右键菜单条目 | `data-t="scriptwriter"` |
| 4 | **renderNode()** — body 生成 | 拼接该节点类型的 HTML | `else if(nd.type==="xxx"){body=...}` |
| 5 | **foot 变量** — nft 排除列表 | 无 nft 的节点加进排除列表 | `&&nd.type!=="scriptwriter"` |
| 6 | **事件绑定** — renderNode() 后半部分 | 输入同步 + 按钮点击 | `if(nd.type==="xxx"){...}` |

## 按钮定位：body 内 footer div（唯一正确模式）

**节点自带的底部按钮必须放在 `nbd` 内部的 div 中，而不是 `.nft` 元素。** nft 是用于没有自定义按钮的节点类型（text、image 等）。

```html
<div class='xxx-footer' style='display:flex;align-items:center;justify-content:flex-end;padding:4px 8px;border-top:1px solid rgba(0,0,0,0.08);flex-shrink:0;margin-top:auto'>
  <button class='nbtn' data-action='xxx'>按钮文字</button>
</div>
```

**参考**：`episode` (.ep-footer)、`sboard` (.sboard-footer)、`scriptwriter` (.sw-foot)、`script` (.script-btns)

### 关键规则

1. **nft 排除** — 新节点类型必须加进 foot 变量排除列表。**漏加 = 底部多出一个默认「生成」按钮**（本会话被用户多次点名批评）。

2. **nbd padding** — 带自定义按钮的节点 `.nbd` 应设 `padding:0;display:flex;flex-direction:column`

3. **按钮不重复** — 检查 foot 变量 + renderNode body，两种不能同时存在按钮

4. **margin-top:auto** — 推到底部，比 `position:absolute` 更可靠

### 调试决策树（按钮下方留白）

用户反复说「按钮下方有留白」时：
1. 检查 foot 变量 — 类型在排除列表中吗？不在 → 默认 nft 按钮导致空间错乱
2. 检查两个按钮（nft"生成" + 自定义按钮）？→ 最常见的根因
3. 确认只有一个按钮且用 body div 方案：检查 nbd padding（应设0）和按钮父 div padding-bottom
4. 不要连续微调 bottom/padding — 停下来对比正常节点（ep-footer, sboard-footer）

## 节点类型：控制栏 + 输入区 + 底部按钮（三栏式）

```css
.sw-ctrl{display:flex;flex-wrap:wrap;gap:4px;padding:6px 8px;border-bottom:1px solid rgba(0,0,0,0.08);flex-shrink:0}
.sw-body{flex:1;display:flex;flex-direction:column;min-height:0}
.sw-body textarea{flex:1;border:none;resize:none;padding:8px;margin:0;line-height:1.5;font-size:12px}
.sw-foot{display:flex;align-items:center;justify-content:flex-end;padding:4px 8px;border-top:1px solid rgba(0,0,0,0.08);flex-shrink:0}
```

结构：`sw-ctrl`（控制栏）→ `sw-body`（textarea 占满）→ `sw-foot`（按钮右对齐）

## 生成剧本节点模式

`exGenScript()` 创建的输出节点用 `meta.generated=true` 标记，在 `renderNode()` 中为 `script` 类型加分支实现简化版 UI（无导入图标、无优化按钮、无集数/风格/LLM 显示，只保留 textarea + 一键分集 + 提取资产）。

## 左侧工具栏

工具栏从顶部移到左侧，`position:fixed` 竖排居中。

```css
.tbar-left{position:fixed;left:0;top:50%;transform:translateY(-50%);padding:14px 16px;border-radius:0 16px 16px 0;display:flex;flex-direction:column;align-items:center;gap:8px;z-index:999;border:1px solid rgba(0,0,0,0.1);border-left:none}
.tb-left{display:flex;align-items:center;gap:8px;padding:10px 18px;font-size:15px;white-space:nowrap}
.tb-left .tb-ico{font-size:22px}
.tb-left .tb-lbl{font-size:14px}
```

**本会话改为统一 15px + 固定图标宽度 20px 实现竖向对齐**：

```css
.tb-left .tb-ico,.tb-left .tb-lbl{font-size:15px}
.tb-left .tb-ico{width:20px;text-align:center;flex-shrink:0}
.mode-dropdown-trigger .tb-ico{width:20px;text-align:center;flex-shrink:0}
```

不同 emoji 的天然视觉宽度不同（如 🎨 窄、🧠 宽、⚙ 居中），固定宽度 + 居中对齐使所有图标在竖排时左边缘对齐。

**关键约定**（被用户两次纠正）：
- 文字必须横向：`flex-direction:row`（默认）+ `white-space:nowrap`
- **禁止** `writing-mode:vertical-lr`
- 图标和文字 gap:8px，字号至少 15px

## 画布模式切换（下拉子菜单）

用下拉菜单代替两个按钮并排。

```html
<div class="mode-dropdown-trigger" id="modeTrigger" style="position:relative">
  🎨 画布模式 <span class="md-arrow">▶</span>
</div>
<div class="mode-dropdown" id="modeDropdown" style="position:absolute;left:100%;top:-4px;display:none">
  <button class="mode-btn" data-mode="frame">🖼 首尾帧</button>
  <button class="mode-btn" data-mode="story">🎬 故事板</button>
</div>
```

**关键定位**（被用户多次纠正的坑）：
- ✅ **`position:absolute;left:100%`**（CSS 自动定位，相对于 trigger）
- ❌ 不要用 `position:fixed` + JS 计算坐标（用户说「位置不对，检查清楚」）
- `top:-4px` — 使第一个选项与触发器文字对齐
- 箭头用 `▶`（向右），不是 `▼`（向下）
- 触发按钮文字初始为「画布模式」，选择模式后变为模式名（本会话改为「首尾帧」或「故事板」，见 `switchCanvasMode` 中的 `childNodes[1].textContent` 更新）
- 切换只更新子菜单内的高亮

JS 模式切换：

```javascript
var canvasMode="frame";
function switchCanvasMode(mode){
  if(mode===canvasMode)return;
  saveCvs(); canvasMode=mode;
  document.querySelectorAll(".mode-btn").forEach(b=>b.classList.toggle("active",b.dataset.mode===mode));
  document.getElementById("modeTrigger").childNodes[1].textContent=mode==="frame"?" 首尾帧":" 故事板"; // ← 模式名显示在按钮上
  document.getElementById("modeDropdown").classList.remove("open");
  loadCvs();
}
```

**缓存机制**：
- `kc-cvs-frame` / `kc-cvs-story` — 独立画布数据
- `kc-mode` — 持久化模式
- 资产库共用
- 自动保存和手动保存都用 `kc-cvs-${canvasMode}` 键

### ⚠️ 模式持久化陷阱：kc-mode 必须同步写（本会话修复）

**症状**：用户在当前模式下操作、保存、然后刷新页面 → 加载了另一个模式的画布数据（故事板数据而非首尾帧数据）。

**根因**：`saveCvs()` 只保存了画布数据（`kc-cvs-frame`）但没有同时更新 `kc-mode`。只有 30 秒间隔的自动保存（`setInterval`）会写 `kc-mode`。用户手动点"保存"或切换模式后立即刷新 → `kc-mode` 还是旧值 → 加载了另一个模式的数据。

**修复**（两处都必须加）：

```javascript
function saveCvs(){
  // ... 原有保存逻辑 ...
  localStorage.setItem("kc-cvs-"+canvasMode, JSON.stringify(data));
  localStorage.setItem("kc-mode", canvasMode);           // ← 新增！
}

// Init IIFE — 页面加载时也立即写，不依赖 30 秒的 interval
(function(){
  var savedMode = localStorage.getItem("kc-mode");
  if(savedMode==="frame"||savedMode==="story") canvasMode = savedMode;
  localStorage.setItem("kc-mode", canvasMode);           // ← 新增！
  // ... 更新 UI ...
})();
```

**通用原则**：所有涉及 localStorage 持久化模式/状态的函数（手动保存 `saveCvs()`、模式切换 `switchCanvasMode()`、页面初始化 IIFE），**必须各自独立写所有相关的 key**，不能假设「30 秒一次的 auto-save interval 会帮我补」。模式切换的 `switchCanvasMode()` 调用 `saveCvs()` 已包含修复，无需额外处理。

**同步修改两份文件**：`app/public/canvas.html` 和 `canvas/canvas.html`。

## 画布模式裁剪检查清单（禁用某一模式时）

当需要从双模式画布（首尾帧 + 故事板）裁剪为单模式版本时（如本会话创建 `canvas/canvas.html`），按以下顺序操作：

### 1. 移除 UI — 删除模式下拉菜单 HTML

删除 `mode-dropdown-trigger` 和 `mode-dropdown` 整个 div 块（约 5 行）。

### 2. 修改默认模式变量

```diff
- var canvasMode="frame";
+ var canvasMode="story";    // 改为保留的模式
```

### 3. 移除模式切换函数及事件绑定

删除 `switchCanvasMode()`、`modeTrigger` 和 `modeDropdown` 的所有 `addEventListener`，以及关闭菜单的 `document.addEventListener("click"`。

### 4. 简化存储 key（去掉模式后缀）

改 3 处：`saveCvs()`、`loadCvs()`、`setInterval` 自动保存。同时删掉 `localStorage.setItem("kc-mode",...)`。

```diff
- var key="kc-cvs-"+canvasMode;
+ var key="kc-cvs";
```

### 5. 简化导出文件名

```diff
- a.download="kairos-canvas-"+(canvasMode==="frame"?"frame":"story")+"-"+date+".json";
+ a.download="kairos-canvas-story-"+date+".json";
```

### 6. 简化页面初始化 IIFE

删除模式读取/回写的 IIFE，直接 `loadCfg();loadCvs();`。

### 7. 必查：检查剩余模式的按钮事件绑定！

这是最容易被忽略的坑（也是本会话发现的预存 bug）：

原始画布中，首尾帧模式的 `genstartprompt`/`genendprompt` 按钮在 `sboard` 分支有事件绑定，但故事板模式的 `genprompt` 按钮**从来就没有事件绑定**——在双模式画布中切换到故事板模式时点击无反应。

**修复**：在 `sboard` 分支加 `genprompt` 事件绑定：

```javascript
if(nd.type==="sboard"){
  var gpBtn=el.querySelector('[data-action="genprompt"]');
  if(gpBtn)gpBtn.addEventListener("click",function(e){
    e.stopPropagation();exGenPrompt(nd,el);
  });
}
```

### 8. 清理模式相关的 CSS（可选）

移除 `.mode-btn`、`.mode-dropdown*`、`.mode-dropdown-trigger*` 相关 CSS 规则（不删也不影响功能）。

### 9. JS 语法验证

每次 patch 后验证语法：
```bash
node -e "const fs=require('fs');const c=fs.readFileSync('.../canvas.html','utf8');const js=c.slice(c.indexOf('<script>')+8,c.indexOf('</script>')).replace(/\r/g,'');new Function(js);console.log('JS OK');"
```

## 首页集成（home.html → canvas.html）

主页 `home.html` 是应用的着陆页。当用户点击 `canvas.html` 上的功能按钮（如从文生图/文生视频主页跳转）时，需要通过 URL 参数传递状态：

```
/app/public/canvas.html?mode=image-gen&prompt=xxx
```

首页布局为四个功能卡片（文生图/图生图/文生视频/图生视频），各自独立记忆状态（参考图、风格、音频、输出结果），切换模块后自动保存/恢复。

**添加首页链接到画布的步骤**：
1. 在 `home.html` 中找到要加链接的位置（header 区或功能卡片区）
2. 使用 `<a>` 标签或 JS `location.href` 指向 `canvas.html`
3. 如需保持页面内导航体验，用 `window.open()` 新标签打开或 AJAX 加载

## 画布性能优化：自动保存频率 + 显式保存

Canvas SPA 中 `JSON.stringify(S.nodes)` 序列化所有节点数据是开销操作。大画布（50+ 节点）每次序列化可能阻塞主线程数十毫秒。当用户尝试拖动/缩放时，这种阻塞会导致明显的界面卡顿。

### 降低自动保存频率

```javascript
// ❌ 太频繁 — 大画布下每5秒阻塞一次主线程
setInterval(function(){ saveCvs(); }, 5000);

// ✅ 30秒一次 — 减少90%的周期性序列化开销
setInterval(function(){ saveCvs(); }, 30000);
```

**原理**：`localStorage.setItem` 本身快，但 `JSON.stringify(S.nodes)` 在节点多时慢。降低频率减少总阻塞时间。

### 关键操作完成后显式保存

降频后，用户可能在两次自动保存之间做了重要操作（如图片生成、节点调整），需要立即持久化：

```javascript
// 在生成成功回调末尾显式保存
function exGenStartImg(nd, el) {
  // ... 创建节点、连线 ...
  saveCvs();  // ← 确保新创建的节点和连线立即保存
}

function exGenSBImg(nd, el) {
  // ... 提交图片生成 ...
  // 轮询完成后设置 meta.img 并保存
  nd.meta.img = imgUrl;
  saveCvs();  // ← 确保生成的图片引用不丢失
}
```

**使用场景**：图片生成完成、节点创建/删除、连线修改等「丢失后难以恢复」的操作后，立即调用 `saveCvs()`。

### 避免不必要的序列化

不要在 UI 更新循环中执行 `saveCvs()`。例如：

```javascript
// ❌ 错误：每2秒轮询时也保存，浪费性能
for (var att = 0; att < 300; att++) {
  await new Promise(r => setTimeout(r, 2000));
  saveCvs();  // ← 不需要，轮询期间没改变画布数据
  var sr = await fetch(stUrl + jid);
  // ...
}

// ✅ 正确：只在操作完成后保存
// ... 轮询完成后
if (sj.status === "completed" && sj.outputUrl) {
  nd.meta.img = sj.outputUrl;
  saveCvs();  // ← 只在关键数据变化后保存
}
```

### 性能基线

以 50 个节点的大画布为例：

| 方案 | 自动保存频率 | 显式保存 | 主线程阻塞时间/分钟 |
|------|------------|---------|-------------------|
| 旧 | 5秒 | 无 | ~100ms × 12次 = 1.2s |
| 新 | 30秒 | 关键操作后 | ~100ms × 2次 + 显式调用 |

## 画布导出/导入

导出：`exportJSON()` → 下载 `kairos-canvas-{mode}-{date}.json`
导入：选择 JSON 文件 → 解析 → 替换当前画布 → `saveCvs()`

数据格式：`{nodes, conns, nid, zoom, px, py, cfg}`

## 分镜节点纵向堆叠

`exStoryboard()` 中每个分镜从 `startY=nd.y`（分集节点 Y）开始排列，不再全局扫描其他集的分镜。`gap` 变量控制间距，**默认 80px**。首尾帧模式共用同一函数。

### 分镜时长规则（LLM 提示词 + traceStoryboardDuration 联动，2026-06-19 更新）

分镜时长由视频模型决定，`exStoryboard()` 的 LLM system prompt 动态生成 `durRule`：

```javascript
var durRule = (S.cfg.vid.model||'').toLowerCase().indexOf('grok') >= 0
  ? "重要规则：每个镜头的duration字段固定为15秒"
  : "重要规则：每个镜头的duration字段在8-15秒内自由选择，尽量用较长时长（10秒以上），减少短时长镜头。根据镜头复杂度决定具体时长。";
```

然后在 API 请求的 `content` 字符串中用 `+durRule+` 拼接替换原来的硬编码文本。

#### `traceStoryboardDuration(nd)` — 统一时长追溯函数（2026-06-19 新增）

```javascript
function traceStoryboardDuration(nd) {
  var model = (S.cfg.vid && S.cfg.vid.model || '').toLowerCase();
  // Grok 模型强制 15s
  if (model.indexOf('grok') >= 0) return 15;
  // 非 Grok：先取节点自身的 duration 元数据
  if (nd.meta && nd.meta.duration) return nd.meta.duration;
  // BFS 沿连接链向上追溯
  var visited = {}, queue = [nd.id];
  visited[nd.id] = true;
  while (queue.length) {
    var curId = queue.shift();
    var curNode = S.nodes.find(n => n.id === curId);
    if (curNode && curNode.meta && curNode.meta.duration) return curNode.meta.duration;
    if (curNode && curNode.type === 'sboard') {
      var dm = (curNode.content || '').match(/^(\d+)s/);
      if (dm) return Math.min(Math.max(parseInt(dm[1]), 8), 15); // 钳位 8-15s
    }
    S.conns.forEach(c => { if (c.to === curId && !visited[c.from]) { visited[c.from] = true; queue.push(c.from); } });
  }
  return 12; // 全链路无数据时默认 12s
}
```

**追溯逻辑**：nd自身 → 上游节点.meta.duration → sboard内容头部 `(\d+)s` → 钳位8-15s → 12s

**替换了4处旧代码**：mkvideo创建video-gen、video-gen渲染显示时长、exGenVideo API请求seconds、exGenVideoPrompt获取时长。改时长规则只需改这一个函数。

必须同步修改 `Kairos_canvas.html` 和 `canvas.html` 两份文件。

```javascript
var startX=nd.x+nd.w+30,startY=nd.y,nw=280,nh=450,gap=80;
var epTitle=nd.title||"\u5206\u955C";
for(var i=0;i<shots.length;i++){
  var nn=addNode("sboard",null,null,{title:epTitle+"-"+(i+1),content:shotDesc,w:nw,h:nh},true);
  nn.x=startX;nn.y=startY+i*(nh+gap);
  var nel=document.getElementById(nn.id);if(nel){nel.style.left=nn.x+"px";nel.style.top=nn.y+"px";}
  S.conns.push({from:nd.id,to:nn.id});
}
```

**重要（2026-06-16 修复）**：旧代码用全局 `S.nodes.filter(n=>n.type==="sboard")` 扫描所有集的分镜，导致第2集的分镜在第1集分镜后面堆叠，而不是和第2集节点对齐。**已完全移除**。现在每集一键分镜时，第一个分镜节点一定和该集节点在同一水平线。

**关键约定**：
- `startY=nd.y` — 不分镜属于哪一集，永远从当前分集节点的 Y 开始
- 旧的分镜由连接查找 `S.conns.filter(c=>c.from===nd.id)` 删除，不会残留
- 不同集的分镜不会互相干扰位置
- 每集分镜内部通过 `startY+i*(nh+gap)` 纵向排列

## 画布图像生成异步轮询（exImg / exGenAsset / exGenSBImg）

画布中 `exImg()`、`exGenAsset()` 和 `exGenSBImg()` 三个图像生成函数都使用 `mode: "async"` 发送请求给本地服务端。服务端提交任务到 Gaia API 后立即返回 `{success, jobId, status: "running"}`（不含 outputUrl）。

**关键陷阱**：旧代码直接用 `gaiaImgUrl(d, apiUrl)` 检查 `d.outputUrl`，异步响应没这个字段 → 返回 null → throw "未知错误"。本会话连续修复了三个函数。

**注意 `exGenSBImg` 特别容易漏掉**：它绑定在 image-gen 节点的「生成图片」按钮上（`data-action="genimg"`），跟普通图像节点 (`exImg`) 是不同的渲染入口。每次添加新图片生成入口时，必须检查是否也需要加轮询逻辑。

**修复方案**：检测到 `isT && d.success && d.jobId && !d.outputUrl` 时，启动客户端轮询：

```javascript
var jid=d.jobId, stUrl=apiUrl.replace(/\/api\/image\/generate$/, "/api/image/generate/status/");
for(var att=0; att<300; att++){
  await new Promise(function(r){setTimeout(r,2000);});
  var sr=await fetch(stUrl+jid); var stxt=await sr.text();
  var sj=JSON.parse(stxt);
  if(sj.status==="completed"&&sj.outputUrl){imgUrl=sj.outputUrl; break;}
  if(sj.status==="failed") throw new Error(sj.error||"生成失败");
  if(sj.status==="cancelled") throw new Error("任务已取消");
}
// outputUrl 以 "/" 开头 → 加 window.location.origin
```

**必须同步修改两份文件**：`app/public/canvas.html` 和 `canvas/canvas.html`（两个都 serve 给不同路由）。

## 分镜节点双提示词生成（exGenStartPrompt / exGenEndPrompt）— 首帧/尾帧 + 弹出节点框

本会话将 sboard 节点从单个「生成提示词」按钮改为**两个按钮**，点击后**只弹出独立的 prompt 节点框**（不显示 sboard-panel 浮框，已被用户要求移除）。Y 坐标对齐**分镜节点框自身**（不追溯 episode），被用户多次纠正后最终确定。

### Y 定位规则（被多次纠正后确定的最终规则）

```
首帧提示词 Y = sboard Y（与该分镜节点框顶部同一水平线）
尾帧提示词 Y = sboard Y + 180（160 高度 + 20 间距）
X = sboard.x + sboard.w + 30
所有节点同一列对齐（相同 X）
```

### 参考图传递链

sboard → startPrompt → startImage，sboard → endPrompt → endImage → videoPrompt。视频提示词节点**连接到尾帧图**，内容是空字符串（`content:""`），由 LLM 根据分镜内容生成首尾帧模式的 Seedance 2.0 提示词。

```html
<div class='sboard-footer'>
  <button class='nbtn' data-action='genstartprompt'>🎬 首帧提示词</button>
  <button class='nbtn' data-action='genendprompt'>🏁 尾帧提示词</button>
</div>
```

### 提示词弹出方式：仅 prompt 节点框（sboard-panel 已移除）

点击「首帧提示词」或「尾帧提示词」后**只弹出独立的 prompt 节点框**（type="prompt"）。不显示浮层面板——本会话中用户明确要求「去掉分镜提示词按钮弹出的浮框，只弹出提示词节点框」。所有相关的 `.sboard-panel` CSS、`updateSboardPanel()` 函数、和 `has-prompt` 类已被清除。

这简化了布局：sboard 节点保持 320px 固定宽度（无右侧面板），prompt 节点框通过 `addNode("prompt",...)` 创建并连线到 sboard。

### 数据存储 + 节点框弹出

生成的提示词存储在 `nd.meta.startPrompt` / `nd.meta.endPrompt` 中（用于持久化），同时创建独立的 prompt 节点框：

```javascript
// 生成首帧后
nd.meta.startPrompt = resp;
// 生成尾帧后
nd.meta.endPrompt = resp;
// 创建 prompt 节点框（自动连线到 sboard）
var nn = addNode("prompt", null, null, { title: ..., content: resp, meta: { startFrame: true, ... } }, false);
nn.x = nd.x + nd.w + 30; nn.y = alignY;
S.conns.push({ from: nd.id, to: nn.id });
```

自动保存（每 5s 的 `localStorage.setItem`）会序列化整个 `S.nodes`，包括 meta，所以提示词自动持久化。

### LLM 提示词 — 首帧 vs 尾帧

两个函数使用不同的 system prompt 焦点：

| 按钮 | 焦点 | 描述要求 |
|------|------|----------|
| 首帧提示词 | OPENING SHOT / FIRST FRAME | 场景开场构图、角色入场、初始画面 |
| 尾帧提示词 | CLOSING SHOT / FINAL FRAME | 场景收尾构图、退场、最终画面 |

其余部分（背景色、边框、风格追溯、关联资产）与旧 `exGenPrompt` 一致。

### sboard 默认宽度 320px — 绝对不要改大

```javascript
sboard: 320  // 必须保持 320px！
```

**本会话血泪教训**：曾改为 580，导致故事板模式纵向堆叠布局被破坏：分镜节点框变宽后 X 方向偏移、Y 方向从 episode 对齐变为错位。被用户截图指出后批评「越来越笨」。

### ⚠️ renderNode 中的错误会打断 loadCvs（画布显示空白但数据完好）

**症状**：用户保存后刷新，画布显示「节点：0」（空白），但 localStorage 数据完好。手动 `saveCvs()` 控制台输出正常。

**根因**：`renderNode()` 在 `loadCvs()` 的 `d.nodes.forEach(function(n){S.nodes.push(n);renderNode(n);})` 内部被调用。如果 `renderNode` 中某个节点类型的处理代码抛出异常（如 `_escHtml is not defined`），**整个循环中断**，剩余节点无法渲染。

**修复顺序**：
1. 检查控制台是否有 `ReferenceError` / `TypeError` — 通常是新加的代码引用了未定义变量
2. 检查 `renderNode` 中新增的分支（如 `if(nd.type==="sboard"){...}` 内调用了新函数）
3. 如果新函数依赖文件底部的辅助函数（如 `_escHtml`），改为函数内局部定义

### ⚠️ `overflow:hidden/auto` 裁剪绝对定位子元素（通用 CSS 坑）

当在 `.nbd`（或任何带 `overflow` 的容器）内部放置 `position:absolute` 的子元素时，容器的 `overflow` 属性会**裁剪**它。

```css
/* 全局 .nbd 默认 overflow:auto — 会裁剪绝对定位子元素 */
.nbd { overflow: auto; }

/* 如果绝对定位元素需要出现在容器外部（如 sboard-panel），
   必须显式覆盖为 overflow:visible */
.node[data-type='sboard'] .nbd { overflow: visible; }
```

**本会话的教训**：sboard-panel 用 `position:absolute;left:100%;top:0` 贴在节点框右侧，但因 `.nbd` 的 `overflow:hidden` 而完全不可见。用户两次截图指出后才被发现。

⚠️ **sboard-panel 已在会话中被移除**——用户要求「去掉分镜提示词按钮弹出的浮框，只弹出提示词节点框」。所有 `.sboard-panel` CSS、`updateSboardPanel()` 函数、`has-prompt` 类已清除。sboard 节点保持 320px 固定宽度。

**本会话被用户两次截图指出面板不可见后才发现的坑**。

## 节点默认尺寸（2026-06-15 更新）

故事板模式下分镜节点的默认尺寸已调整：

| 节点类型 | 旧尺寸 | 新尺寸 | 修改位置 |
|----------|--------|--------|---------|
| 分镜提示词节点（prompt/storyboard） | 320×160 | **720×450** | `exGenPrompt()` 中 `addNode` 的 `w:720,h:450` |
| 分镜图节点（image-gen 默认） | 365×200 | **530×450** | `dW()` 中 `"image-gen":530`，`dH()` 中 `"image-gen":450` |

### ⚠️ 已移除（多次反复添加又删除）：Init 时第一个分镜节点 Y 对齐 episode

**用户明确要求「保存画布后，刷新画布不可修改所有节点框位置」。** 对齐代码在 `loadCvs()` 后强制拉齐第一个分镜到 episode 的 Y 坐标，导致第2集第一个分镜刷新后跳动。

**⚠️ 这个代码已被反复添加了四次**——每次在文件因重构/合并/复制而重建时，开发者会出于"好意"重新加上「对齐第一个分镜到分集 Y」的逻辑，认为能改善布局。如果在新文件中看到类似 `// Align first sboard of each episode to episode node Y` 的代码，必须立即删除。

**本次（2026-06-19，第4次）出现在 `app/public/Kairos_canvas.html`**（带下划线 `_` 的变体文件名）。`exStoryboard` 创建分镜时已用 `startY=nd.y` 保证第一分镜与分集对齐，保存/加载也能正确恢复位置。对齐代码会破坏保存的位置，且 `updConns()` 在 forEach 循环内被多次调用。

**搜索/排查**：如果刷新后某个节点跳动位置，检查 `loadCvs()` 之后是否有任何 IIFE 修改了 `n.y`、`n.x`、`style.top`、或 `style.left`。关键词：`Align first sboard`、`n.y=`、`ep.y`、`style.top=`。涵盖的文件名包括：`canvas.html`、`Kairos_canvas.html`、`Kairos_canvas.html`（带下划线变体）。

**根因**：以下 IIFE 会在每次页面加载时修改 `n.y` 并重设 DOM `style.top`，覆盖了用户手动拖拽后的保存位置：

```javascript
// 已删除的代码（位于 app/public/canvas.html 和 canvas/canvas.html 的 ~line 3096）
loadCfg();loadCvs();
(function(){
  S.nodes.filter(function(n){return n.type==="sboard";}).forEach(function(n){
    var ep=S.nodes.find(function(e){return e.type==="episode"&&S.conns.some(function(c){return c.from===e.id&&c.to===n.id;});});
    if(!ep)return;
    var firstSboard=S.nodes.filter(function(s){return s.type==="sboard"&&S.conns.some(function(c){return c.from===ep.id&&c.to===s.id;});})
      .sort(function(a,b){return a.y-b.y;})[0];
    if(firstSboard&&firstSboard.id===n.id&&Math.abs(n.y-ep.y)>20){
      n.y=ep.y;
      var el=document.getElementById(n.id);if(el)el.style.top=n.y+"px";
      updConns();
    }
  });
})();
```

**硬规则**：保存画布 = 位置固定。`loadCvs()` 之后不得有任何代码修改节点 `.x` / `.y` / `style.left` / `style.top`。新分镜创建时的 Y 坐标应该使用 `exStoryboard()` 中的堆叠逻辑（gap 机制），而不是在 init 中做后处理对齐。

> 参考文件：`references/sboard-overlap-on-refresh.md` — 本节诊断的完整调试轨迹、症状、根因和修复方法。

**搜索/排查**：如果刷新后某个节点跳动位置，检查 `loadCvs()` 之后是否有任何 IIFE 修改了 `n.y`、`n.x`、`style.top`、或 `style.left`。关键词：`Align first sboard`、`n.y=`、`ep.y`、`style.top=`。

`addNode()` 的第四参数 `data` 中，`data.w` 和 `data.h` 分别控制节点宽度和高度。如果放在 `data.meta` 内，`addNode` 会回退到 `dW(type)` / `dH(type)` 的默认值。

**本会话教训**：用户反馈「生成的是 320×200 不是 320×160」，排查发现 `w:320, h:160` 误放在 `meta` 对象中。

### 事件绑定（替换旧 genprompt）

点击按钮后**创建独立的 prompt 节点框**（sboard-panel 浮框已移除，只弹节点框）：

- **Y 坐标对齐规则**（被用户多次纠正，最终规则！）：**直接使用 sboard 节点自身的 Y**，不追溯 episode：
  1. 首帧提示词 Y = sboard Y（与该分镜节点框顶部同一水平线）
  2. 尾帧提示词 Y = sboard Y + 180（160 高度 + 20 间距）
  3. X 坐标：`nd.x + nd.w + 30`（sboard 右侧 30px）
  4. 所有节点同一列对齐（相同 X）
- **节点框默认尺寸**：`w:720, h:450`（2026-06-15 从 320×160 改为 720×450）

```javascript
// 多跳追溯 + 回退（通用模板，start 和 end 都用这个）
var alignNode=null,tmpN=nd;
for(var hop=0;hop<5;hop++){
  var up=S.nodes.find(function(n){return S.conns.some(function(c){return c.from===n.id&&c.to===tmpN.id;});});
  if(!up)break;
  if(up.type==="episode"){alignNode=up;break;}
  tmpN=up;
}
// Fallback: first episode by Y position
if(!alignNode){
  var eps=S.nodes.filter(function(n){return n.type==="episode";}).sort(function(a,b){return a.y-b.y;});
  if(eps.length)alignNode=eps[0];
}
nn.x=nd.x+nd.w+30;
nn.y=alignNode?alignNode.y:nd.y;  // 首帧
// 尾帧：nn.y=(alignNode?alignNode.y:nd.y)+180;  // 160高度 + 20间距

## 首帧/尾帧提示词节点 → 生成首帧图/尾帧图按钮

**新增渲染分支**：prompt 节点若 `meta.startFrame=true` 或 `meta.endFrame=true`，底部加"生成首帧图"/"生成尾帧图"按钮：

```javascript
if(nd.type==="prompt"&&nd.meta&&nd.meta.startFrame){
  body="<textarea>"+nd.content+"</textarea>"+
    "<div class='sbprompt-footer' style='justify-content:flex-end'><button class='nbtn' data-action='genstartimg'>🎬 生成首帧图</button></div>";
}else if(nd.type==="prompt"&&nd.meta&&nd.meta.endFrame){
  body="<textarea>"+nd.content+"</textarea>"+
    "<div class='sbprompt-footer' style='justify-content:flex-end'><button class='nbtn' data-action='genendimg'>🏁 生成尾帧图</button></div>";
}
```

**事件绑定**（在 renderNode 中）：

```javascript
if(nd.type==="prompt"&&nd.meta&&nd.meta.startFrame){
  var ta=el.querySelector("textarea");
  if(ta){ta.addEventListener("input",function(){nd.content=ta.value;});...}
  var siBtn=el.querySelector('[data-action="genstartimg"]');
  if(siBtn)siBtn.addEventListener("click",function(e){...exGenStartImg(nd,el);});
}
// endFrame 同理，genendimg → exGenEndImg
```

**处理函数**（与 exGenSBImage 同模式）：

```javascript
function exGenStartImg(nd,el){
  var ta=el.querySelector("textarea"),text=ta?ta.value.trim():(nd.content||"");
  var sbName=nd.title.replace(/ 首帧提示词$/,'').replace(/ 提示词$/,'');
  var nn=addNode("image-gen",null,null,{title:sbName+" 首帧图",content:text},false);
  nn.x=nd.x+nd.w+30;nn.y=nd.y;
  S.conns.push({from:nd.id,to:nn.id});updConns();updUI();
}
// exGenEndImg 同理，替换 首帧→尾帧
```

标题去除规则：先尝试去掉" 首帧提示词"后缀，再尝试去掉" 提示词"后缀（兼容两种命名）。

### 生成首帧图/尾帧图（exGenStartImg / exGenEndImg）— 含自动视频提示词节点

提示词节点框右下角有「生成首帧图」/「生成尾帧图」按钮，点击创建 image-gen 节点。

**⚠️ 视频提示词节点在 `exGenStartImg` 中创建**（本会话最新决策），`exGenEndImg` 只查找已有节点并连接，不再重复创建：

```
exGenStartImg:  首帧提示词 → 首帧图 → [创建] 视频提示词节点
                                        ↑ 连接（如有）
                                       尾帧图 ← exGenEndImg: 查找已有视频节点
```

**`exGenEndImg` 查找逻辑**（不创建新节点）：
```javascript
// 从 sboard 找 startPrompt → startImage → 已有 video prompt
var sbNode=S.nodes.find(n=>S.conns.some(c=>c.to===nd.id&&c.from===n.id));
var vpn=null;
if(sbNode){
  var stPrompt=S.nodes.find(n=>n.type==="prompt"&&n.meta&&n.meta.startFrame&&
    S.conns.some(c=>c.from===sbNode.id&&c.to===n.id));
  if(stPrompt){
    var stImg=S.nodes.find(n=>n.type==="image-gen"&&
      S.conns.some(c=>c.from===stPrompt.id&&c.to===n.id));
    if(stImg)vpn=S.nodes.find(n=>n.type==="prompt"&&n.meta&&n.meta.video&&
      S.conns.some(c=>c.from===stImg.id&&c.to===n.id));
  }
}
if(!vpn){ /* 创建新视频节点 */ }
if(!S.conns.some(c=>c.from===nn.id&&c.to===vpn.id))
  S.conns.push({from:nn.id,to:vpn.id});
```



**渲染分支**（`renderNode` 中 `prompt` 类型分支）：

```javascript
if(nd.type==="prompt"&&nd.meta&&nd.meta.startFrame){
  body="<textarea>"+nd.content+"</textarea>"+
    "<div class='sbprompt-footer' style='justify-content:flex-end'><button class='nbtn' data-action='genstartimg'>🎬 生成首帧图</button></div>";
}else if(nd.type==="prompt"&&nd.meta&&nd.meta.endFrame){
  body="<textarea>"+nd.content+"</textarea>"+
    "<div class='sbprompt-footer' style='justify-content:flex-end'><button class='nbtn' data-action='genendimg'>🏁 生成尾帧图</button></div>";
}
```

**事件绑定**和 **`startFrame`/`endFrame` prompt 的 textarea 输入同步**（两个一起做）：

```javascript
if(nd.type==="prompt"&&nd.meta&&nd.meta.startFrame){
  var ta=el.querySelector("textarea");
  if(ta){ta.addEventListener("input",function(){nd.content=ta.value;});ta.addEventListener("mousedown",function(e){e.stopPropagation();});}
  var siBtn=el.querySelector('[data-action="genstartimg"]');
  if(siBtn)siBtn.addEventListener("click",function(e){e.stopPropagation();exGenStartImg(nd,el);});
}
if(nd.type==="prompt"&&nd.meta&&nd.meta.endFrame){
  var ta=el.querySelector("textarea");
  if(ta){ta.addEventListener("input",function(){nd.content=ta.value;});ta.addEventListener("mousedown",function(e){e.stopPropagation();});}
  var eiBtn=el.querySelector('[data-action="genendimg"]');
  if(eiBtn)eiBtn.addEventListener("click",function(e){e.stopPropagation();exGenEndImg(nd,el);});
}
```

**处理函数**（与 `exGenSBImage` 同模式）：

```javascript
function exGenStartImg(nd,el){
  var ta=el.querySelector("textarea"),text=ta?ta.value.trim():(nd.content||"");
  var sbName=nd.title.replace(/ 首帧提示词$/,'').replace(/ 提示词$/,'');  // 优先去" 首帧提示词"，回退去" 提示词"
  var nn=addNode("image-gen",null,null,{title:sbName+" 首帧图",content:text,w:360,h:160},false);
  nn.x=nd.x+nd.w+30;nn.y=nd.y;  // 与提示词节点同一行
  S.conns.push({from:nd.id,to:nn.id});updConns();updUI();
}
function exGenEndImg(nd,el){
  var ta=el.querySelector("textarea"),text=ta?ta.value.trim():(nd.content||"");
  var sbName=nd.title.replace(/ 尾帧提示词$/,'').replace(/ 提示词$/,'');
  var nn=addNode("image-gen",null,null,{title:sbName+" 尾帧图",content:text,w:360,h:160},false);
  nn.x=nd.x+nd.w+30;nn.y=nd.y;  // 与尾帧提示词节点同一水平线
  S.conns.push({from:nd.id,to:nn.id});updConns();updUI();
}
```

**定位规则**：
- 首帧图 Y = 首帧提示词 Y（同一水平线）
`addNode()` 的第四参数 `data` 中，`data.w` 和 `data.h` 分别控制节点宽度和高度。如果放在 `data.meta` 内，`addNode` 会回退到 `dW(type)` / `dH(type)` 的默认值。

**本会话教训**：用户反馈「生成的是 320×200 不是 320×160」，排查发现 `w:320, h:160` 误放在 `meta` 对象中。

### 事件绑定（替换旧 genprompt）

```javascript
if (nd.type === "sboard") {
  var ta = el.querySelector("textarea");
  if (ta) { ta.addEventListener("input", function() { nd.content = ta.value; }); ... }
  var spBtn = el.querySelector('[data-action="genstartprompt"]');
  if (spBtn) spBtn.addEventListener("click", function(e) { e.stopPropagation(); exGenStartPrompt(nd, el); });
  var epBtn = el.querySelector('[data-action="genendprompt"]');
  if (epBtn) epBtn.addEventListener("click", function(e) { e.stopPropagation(); exGenEndPrompt(nd, el); });
}
```

### 风格追溯 + 关联资产（与旧 exGenPrompt 一致）

两个函数沿用相同的上游追溯逻辑（sboard → episode → script/scriptwriter → meta.style）和关联资产收集方式。代码相同，不再重复。

### 视频提示词生成（exGenVideoPrompt）— 模式自适应（2026-06-15 新增）

`exGenVideoPrompt()` 的系统提示词不仅要包含规则（首尾帧模式、相机四维编码、时间），**必须拼接 `context` 和已有 `text`**。本会话曾不小心删掉了关键的两行 `+\"\\n分镜内容：\\n\"+context+\"\\n\"`，导致 LLM 收不到剧本内容，只能返回「由于您未提供具体的分镜内容，以下是一个示例...」。

**正确模式**：

```javascript
var sysPrompt = \"...规则部分...\" +
  \"- 时长：\" + vnDur + \"秒\\n\" +
  \"\\n分镜内容：\\n\" + context + \"\\n\" +  // ← 必须加！
  (text ? \"已有提示词（可优化）：\\n\" + text + \"\\n\" : \"\");  // ← 可选但推荐
```

### ⚠️ sbNode 搜索必须有多条路径（本会话修复的 bugs）

`exGenVideoPrompt` 需要从视频提示词节点追溯到 sboard 节点来获取分镜文本。旧代码只有一个 traceback 路径，如果连接链中某个节点类型不匹配或 `find()` 返回了错误的中间节点，sbNode 为 null → 所有验证跳过 → 弹出"请先生成首帧图和尾帧图"（假阴性）。

**修复方案**：两条路径搜索：

```javascript
// 路径1：traceback（视频提示词 → image-gen → prompt → sboard）
function findSboardFromVp(vpId){
  for(var hop=0;hop<5;hop++){
    var up=S.nodes.find(function(n){
      return S.conns.some(function(c){return c.from===n.id&&c.to===vpId;});
    });
    if(!up)return null;
    if(up.type==="sboard")return up;
    vpId=up.id;
  }
  return null;
}
var sbNode=findSboardFromVp(nd.id);

// 路径2（fallback）：从所有连接到 vp 的 image-gen 节点反向找 sboard
if(!sbNode){
  var allImgs=S.nodes.filter(function(n){
    return n.type==="image-gen"&&S.conns.some(function(c){return c.from===n.id&&c.to===nd.id;});
  });
  for(var i=0;i<allImgs.length;i++){
    var upPrompt=S.nodes.find(function(n){
      return n.type==="prompt"&&S.conns.some(function(c){return c.from===n.id&&c.to===allImgs[i].id;});
    });
    if(upPrompt){
      var upSboard=S.nodes.find(function(n){
        return n.type==="sboard"&&S.conns.some(function(c){return c.from===n.id&&c.to===upPrompt.id;});
      });
      if(upSboard){sbNode=upSboard;break;}
    }
  }
}
```

**通用原则**：任何需要向上追溯节点链的函数，当主 traceback 可能因连接顺序或意外类型失败时，加备用搜索路径从已知连接点（如 image-gen→vp）开始反向搜索。

### 调试 console.log 模式（本会话新增）

在 `exGenVideoPrompt` 等复杂搜索函数中，在关键决策点加 console.log：

| 决策点 | 日志内容 | 预期成功输出 |
|--------|---------|------------|
| 函数入口 | `nd.id`, `nd.title` | 确认按钮绑定正确 |
| sbNode 搜索结果 | `found id=N` 或 `null` | 应找到 sboard |
| 图片节点检测 | `stPrompt/enPrompt/stImg/enImg`, `stImgReady/enImgReady` | 4 个 true |

当用户反馈"功能不正常"时，日志能快速判断是搜索失败还是数据未保存。

### ⚠️ 图片/视频生成后立即 `saveCvs()`（清单模式）

**每次你添加新的图片/视频生成函数，或修改现有函数中设置 `nd.meta.img=` / `nd.meta.vid=` 的路径时，必须确认该路径末尾有 `saveCvs()`。**

具体检查点：

| 函数 | 设置 meta 的位置 | 有无 saveCvs？ |
|------|-----------------|---------------|
| `exImg()` | 异步轮询路径 (line ~1759) | ✅ 本会话修复 |
| `exImg()` | 标准路径 (line ~1765) | ✅ 本会话修复 |
| `exGenSBImg()` | 异步轮询路径 (line ~2598) | ✅ 已有 |
| `exGenSBImg()` | 标准路径 (line ~2604) | ✅ 已有 |
| `exGenAsset()` | 异步轮询路径 (line ~2662) | 需检查 |
| `exGenAsset()` | 标准路径 (line ~2668) | 需检查 |
| `exGenVideoPrompt()` | LLM 响应设置 `nd.content` (line ~2509) | ✅ 本会话新增 |
| `exGenVideo()` | 视频 URL 设置 `nd.meta.vid` (line ~1838) | 无 → 非必要（视频生成慢，30s 自动保存够） |

**本会话教训**：`exImg()` 两个路径都设置了 `nd.meta.img=imgUrl` 但缺少 `saveCvs()`。如果自动保存间隔 30 秒，用户在此期间刷新页面 → `meta.img` 丢失 → 下次验证图片存在性失败 → 假阴性「请先生成首帧图和尾帧图」。

### ⚠️ 按钮 disable 必须在 try 内部（不要在 try 外预 disable）

```javascript
// ✅ 正确：disable 在 try 内，re-enable 在 catch 后
var btn=el.querySelector('[data-action="xxx"]');
try{
  if(btn){btn.disabled=true;btn.innerHTML="<span class='spinner'></span> 生成中...";}
  // ... API 调用 ...
}catch(err){...}
if(btn){btn.disabled=false;btn.innerHTML="✨ 原文字";}

// ❌ 错误：disable 在 try 前，如果 try 前的验证失败 return，按钮永久卡住
btn.disabled=true;  // ← 如果下面某行 return 了，按钮不会恢复
if(!ready){toast("未就绪");return;}  // ← 按钮永久 spinner！
try{
  ...
}catch(err){...}
btn.disabled=false;
```

**原则**：按钮的 disable 必须和对应的 API 调用在同一个 try/catch 块内。验证检查必须发生在 disable 之前。

### 删除冗余的 try 块内外重复代码（本会话修复）

**旧代码的反模式**：`exGenVideoPrompt` 的 sbNode 搜索、sbText 取值、图片节点查找在 try 块外做一次，在 try 块内又做一次（不同变量名、不同搜索方向）。这不仅浪费行数，还可能导致变量不一致——内外部 `stImgReady` 指向不同搜索。

**修复后**：所有搜索和验证放在 try 块外，只有 API 调用放在 try 内。单一真实来源：

```javascript
// ✅ 正确：搜索在外，API 在内
var sbNode=findBoardFromVp(nd.id);
// ... 所有搜索、验证 ...
if(!stImgReady||!enImgReady){return;}
// 按钮 disable 在 API 调用前一刻
try{
  btn.disabled=true;
  // API call...
}catch(err){...}
btn.disabled=false;
```

### ⚠️ `@图片1` 必须指向尾帧图（本会话修复）

`exGenVideoPrompt` 中 `sbImg` 的 `igNode` 之前用 `find` 随便取第一个 image-gen 节点，但因为视频提示词同时连接着首帧图和尾帧图，可能取到首帧图。必须明确找到**上游有 endFrame prompt 的 image-gen**：

```javascript
// ✅ 正确：优先找连接了尾帧提示词的 image-gen
var igNode = S.nodes.find(function(n) {
  return n.type === "image-gen"
    && S.conns.some(function(c) { return c.from === n.id && c.to === nd.id; })
    && S.nodes.some(function(p) {
      return p.type === "prompt"
        && p.meta && p.meta.endFrame
        && S.conns.some(function(c2) { return c2.from === p.id && c2.to === n.id; });
    });
});
```

确保 `@图片1` 始终指向尾帧图，与系统提示词一致。

### ⚠️ 视频提示词内容 = 分镜节点 text，不含首尾帧提示词

`exGenVideoPrompt` 当前只从 sboard 节点读取 `sbText`，不拼接 `stPromptText`/`enPromptText`（本会话用户明确要求「不分参考首尾帧提示词」）：

```javascript
var context = sbText ? "分镜内容：" + sbText : "";
// 不包含：
// (stPromptText?"首帧提示词："+stPromptText+"\n":"")+
// (enPromptText?"尾帧提示词："+enPromptText+"\n":"")
```

### 完整布局参照表（故事板模式）— 每个 sboard 的节点簇

对同一个 sboard 节点点击各按钮后，所有生成节点的位置关系：

| 按钮 | 生成的节点类型 | 标题 | 尺寸 | Y 坐标 | X 坐标 |
|------|---------------|------|------|--------|--------|
| ✨ 生成提示词 | `prompt(meta.storyboard)` | `"分镜 提示词"` | **720×450** | sboard Y | sboard.x + 310 |
| 🎬 分镜图 | `image-gen` | `"分镜 分镜图"` | **530×450** | prompt Y | prompt.x + 750 |
| ✨ 生成视频提示词（image-gen 上） | `prompt(meta.video)` | `"分镜 分镜视频提示词"` | 360×340 | image-gen Y | image-gen.x + 560 |

对同一个 sboard 节点点击各按钮后，所有生成节点的位置关系：

| 按钮 | 生成的节点类型 | 标题 | 尺寸 | Y 坐标 | X 坐标 |
|------|---------------|------|------|--------|--------|
| 🎬 首帧提示词 | `prompt(meta.startFrame)` | `"分镜 首帧提示词"` | 320×160 | sboard Y | sboard.x + 310 |
| 🏁 尾帧提示词 | `prompt(meta.endFrame)` | `"分镜 尾帧提示词"` | 320×160 | sboard Y + 180 | sboard.x + 310 |
| 🎬 生成首帧图 | `image-gen` | `"分镜 首帧图"` | **360×160** | prompt Y (=sboard Y) | prompt.x + 350 |
| 🏁 生成尾帧图 | `image-gen` | `"分镜 尾帧图"` | **360×160** | prompt Y (=sboard Y+180) | prompt.x + 350 |
| （自动创建） | `prompt(meta.video)` | `"分镜 视频提示词"` | **360×340** | startImage Y (=sboard Y) | startImage.x + 390 |

> **连线拓扑：** 首帧图 → 视频提示词 ← 尾帧图（**视频提示词由 exGenStartImg 自动创建**，exGenEndImg 只补连到尾帧图）
> 间距：首尾帧提示词之间 gap=20（160高度+20间距=180顶部偏移）
> 图片节点与对应 prompt 节点**同一水平线**（`nn.y=nd.y`，nd 为对应 prompt 节点）
> 所有节点同一列对齐（相同 X = `nd.x + nd.w + 30`）
> **Y 基于 sboard 自身 Y，不追溯 episode**（本会话最终确定）

## 故事板提示词生成（exGenPrompt — 在双模式画布中原本预存bug，2026-06-15 修复）

`exGenPrompt()` 在原始双模式画布中是**预存bug**：故事板模式（`story`）的「生成提示词」按钮 (`data-action="genprompt"`) **在 `sboard` 类型的事件绑定分支中没有对应的事件处理**。旧代码仅绑定了 `genstartprompt`（首帧提示词）和 `genendprompt`（尾帧提示词）。故事板模式下虽然按钮 HTML 存在（`renderNode` 条件渲染），但没有任何事件绑定，点击无反应。

### 2026-06-15 修复（见本会话）

创建独立的故事板模式画布 `canvas/canvas.html` 时暴露了此问题：

```javascript
// renderNode → sboard 分支：替换旧双按钮为单按钮 + 绑定新函数
if(nd.type==="sboard"){
  var ta=el.querySelector("textarea");
  if(ta){ta.addEventListener("input",function(){nd.content=ta.value;});ta.addEventListener("mousedown",function(e){e.stopPropagation();});}
  var gpBtn=el.querySelector('[data-action="genprompt"]');
  if(gpBtn)gpBtn.addEventListener("click",function(e){e.stopPropagation();exGenPrompt(nd,el);});
}
```

`exGenPrompt` 函数与 `exGenStartPrompt` 逻辑相同，但：
- System prompt 不强调「首帧/尾帧」，改为通用画面描述
- 生成的 prompt 节点 `meta:{storyboard:true}`（非 startFrame/endFrame）
- 创建的节点标题为「分镜 提示词」而非「分镜 首帧/尾帧提示词」

```javascript
async function exGenPrompt(nd,el){
  var s=S.cfg.llm;if(!s.url||!s.key){toast("请先在设置中配置LLM API");return;}
  var ta=el.querySelector("textarea"),text=ta?ta.value.trim():(nd.content||"");
  if(!text){toast("分镜无内容");return;}
  var btn=el.querySelector('[data-action="genprompt"]');if(btn){btn.disabled=true;btn.innerHTML="<span class='spinner'></span> 生成中...";}
  try{
    // ... 追溯风格、关联资产（同 exGenStartPrompt）...
    var sysPrompt="You are a professional cinematographer. Generate a concise English prompt describing this scene for AI image generation.\n\n"+
      "Describe:\n"+
      "- Scene setting and atmosphere\n"+
      "- Subject/character position, appearance, and action\n"+
      "- Camera angle and framing\n"+
      "- Lighting mood and color palette\n"+
      "- Key visual elements\n\n"+
      (styleDesc?"Style reference: "+styleDesc+"\n\n":"")+
      "Storyboard content:\n"+desc+"\n\n"+
      assetSection+
      "\n\nOutput only the prompt, English, one concise paragraph.";
    var r=await fetch(fixLLMUrl(s.url),{method:"POST",headers:{"Content-Type":"application/json","Authorization":"Bearer "+s.key},body:JSON.stringify({model:s.model,messages:[{role:"system",content:sysPrompt},{role:"user",content:"请根据分镜内容生成画面提示词"}],temperature:0.5})});
    // ... 创建 prompt 节点 ...
  }catch(err){toast("生成失败: "+err.message);console.error(err);}
  if(btn){btn.disabled=false;btn.innerHTML="✨ 生成提示词";}
}
```

### M版母版提示词（2026-06-15 替换）

`exGenPrompt` 的 sysPrompt 已替换为用户提供的分镜设计图母版模板：
- 角色升级为 `production designer and cinematographer`
- 包含完整视觉规范（#0A0A0C 深炭灰背景、双线金色边框、古金强调色、胶片颗粒噪点）
- 布局：3区横版（HEADER 35% / MIDDLE 30% / FOOTER 35%）+ 3×3 Grid 变体
- 运行注入：STYLE REFERENCE（风格追溯）、REFERENCE ASSETS（资产链接）、STORYBOARD CONTENT（分镜内容）
- 去掉了"one concise paragraph"限制，改为"detailed English prompt that fully describes every zone, label, and visual element"
- 去掉了"No text generation"限制（用户明确要求移除，保留文字/标注生成）

**风格追溯**：沿用 3 跳链路 sboard → episode → script/scriptwriter → `meta.style` → `STYLE_MAP`，不追溯时 `styleDesc` 为空。

**其他生成函数**（exGenStartPrompt、exGenEndPrompt、exGenVideoPrompt）的 sysPrompt **未替换**，保留原有简短模板。详情见 `references/storyboard-m-template.md`。

当前 sysPrompt 全部硬编码，无用户可编辑 UI。如需增加可编辑字段，可在 sboard 节点或设置面板加 textarea。

`exGenPrompt` 生成单个 prompt 并创建独立的 prompt 节点（`addNode("prompt",...,{meta:{storyboard:true}})`）。

## Seedance 2.0 视频提示词生成（exGenVideoPrompt）— 双模式自适应（2026-06-15 重构）

`exGenVideoPrompt()` 被 prompt 节点的「✨ 生成视频提示词」按钮调用。2026-06-15 重构为**双模式自适应**：自动检测当前是故事板模式还是首尾帧模式，使用不同的 sysPrompt 和验证逻辑。

`exGenVideoPrompt()` 被 prompt 节点的「✨ 生成视频提示词」按钮调用。本会话改为 **Seedance 2.0 首尾帧模式**，提示词节点**由首帧图创建时自动生成**（`exGenStartImg` 中 `addNode`），**连接到首帧图和尾帧图两个节点**。

### 连接方式

视频提示词节点在 `exGenStartImg`（生成首帧图）中自动创建并连线到首帧图。当用户后续点击「生成尾帧图」时，`exGenEndImg` 查找已有视频提示词节点并补连到尾帧图。如果用户先点「生成尾帧图」（首帧图不存在），`exGenEndImg` 也会创建视频提示词节点作为兜底：

```javascript
// exGenStartImg 中创建
var vpn=addNode("prompt",...,{title:sbName+" 分镜视频提示词", content:"", meta:{video:true}, w:360, h:340});
vpn.x=nn.x+nn.w+30; vpn.y=nn.y;  // 首帧图右侧
S.conns.push({from:startImage.id, to:vpn.id});
// 如果尾帧图已存在，也连接
if(endImage) S.conns.push({from:endImage.id, to:vpn.id});

// exGenEndImg 中查找并补连（不创建新节点）
var vpn = findExistingVideoPrompt(startImage);  // sboard → startImg → 已有 video prompt
if(!vpn) createNewVideoPrompt();  // 兜底：首帧图不存在时自建
if(!alreadyConnected) S.conns.push({from:endImage.id, to:vpn.id});
```

最终连接拓扑：
```
sboard
  ├─ startPrompt → startImage ──┐
  ├─ endPrompt   → endImage   ──┤
  └─ 两个都连到 ──→ prompt(video) [空内容，360×340]
```

### 内容来源：仅分镜节点 text，不含首尾帧提示词

`exGenVideoPrompt` 只使用 sboard 节点的 `sbText` 作为上下文，不拼接首尾帧提示词（`stPromptText`/`enPromptText`）。用户明确要求：

```javascript
var context = sbText ? "分镜内容：" + sbText : "";
// 不含首帧提示词 / 尾帧提示词
```

### ⚠️ 必须加 context 和 text 到 system prompt（本会话血泪教训）

系统提示词末尾必须拼接分镜内容和已有文本，否则 LLM 在空上下文中只能生成示例：

`exGenVideoPrompt()` 从视频提示词节点追溯上游找到：
1. 连接的 image-gen 节点（通过 `c.from===n.id && c.to===nd.id`）
2. 检测是否有 startFrame/endFrame prompt → 区分模式
3. 根据模式找到对应的参考图片

### 双模式检测逻辑（2026-06-15 新增）

```javascript
// 检测 sboard 是否连接了 startFrame/endFrame prompt
var stPrompt=sbNode?S.nodes.find(function(n){
  return n.type==="prompt"&&n.meta&&n.meta.startFrame
    &&S.conns.some(function(c){return c.from===sbNode.id&&c.to===n.id;});
}):null;
var enPrompt=sbNode?S.nodes.find(function(n){
  return n.type==="prompt"&&n.meta&&n.meta.endFrame
    &&S.conns.some(function(c){return c.from===sbNode.id&&c.to===n.id;});
}):null;
var isSBVp=!stPrompt&&!enPrompt;  // 故事板模式
```

**故事板模式**（isSBVp=true）：只有一个分镜图（image-gen），直接连接到 vp，不需要首尾帧区分。
```javascript
// 找直接连接到 vp 的 image-gen 节点
var sbImgNode=S.nodes.find(function(n){
  return n.type==="image-gen"
    &&S.conns.some(function(c){return c.from===n.id&&c.to===nd.id;});
});
// 只需检查这一个节点是否有图
if(!sbImgNode||!sbImgNode.meta||!sbImgNode.meta.img)
  {toast("请先生成分镜图");return;}
```

**首尾帧模式**（isSBVp=false）：通过 sboard 找 startFrame/endFrame prompt，再找对应的 image-gen，需要两张图都存在。

### 触发节点

只有 `meta.video=true` 的 prompt 节点（视频提示词节点）有这个按钮。故事板 prompt 节点（`meta.storyboard=true`）没有此按钮——视频提示词节点由 `exGenSBImg` 的成功回调自动创建（见「分镜图生成后的自动创建链」）。

### 自动创建链与追踪跳数

`exGenSBImg`（分镜图生成成功）后自动创建控制链：

```
sboard → prompt(storyboard) → image-gen (生图完成) → prompt(video) [← exGenVideoPrompt 在这] → video-gen
```

**关键陷阱：连接搜索深度**。`exGenVideoPrompt` 需要从 prompt(video) 向上追溯找到 sboard 节点获取故事板描述和时长。自动创建链的路径为：

- prompt(video) ← image-gen ← prompt(storyboard) ← sboard（3 跳）

旧代码只支持 2 跳搜索（`c.to===nd.id` 或 `m→nd`），无法从 prompt(video) 找到 sboard。**本会话已修复**，采用循环回溯代替嵌套搜索：

```javascript
var sbNode=null,tmpN=nd;
for(var hop=0;hop<5;hop++){
  var up=S.nodes.find(n=>S.conns.some(c=>c.from===n.id&&c.to===tmpN.id));
  if(!up)break;
  if(up.type==="sboard"){sbNode=up;break;}
  tmpN=up;
}
```

**通用原则**：任何需要向上追溯节点链的函数，必须考虑自动创建链路的跳数。如果链路超过 2 跳，用循环替代嵌套 find/some。

### mkvideo 时长追溯：必须处理 startFrame/endFrame 提示词（本会话修复）

`mkvideo` 按钮（「分镜视频」）需要从视频提示词节点向上追溯找到 sboard，解析时长。旧代码硬编码搜索 `n.meta.storyboard` 的 prompt，但新流程的提示词节点用的是 `meta.startFrame/endFrame`。

**修复**：改为搜索任意类型的 prompt 节点（不限制 `meta.storyboard`），然后继续向上找到 sboard：

```javascript
// ✅ 正确：不限制 meta 类型
var mkPsb = S.nodes.find(function(n) {
  return n.type === "prompt"
    && S.conns.some(function(c) { return c.from === n.id && c.to === mkIgNode.id; });
});

// ❌ 旧：只找 meta.storyboard
var mkPsb = S.nodes.find(function(n) {
  return n.type === "prompt" && n.meta && n.meta.storyboard
    && S.conns.some(function(c) { return c.from === n.id && c.to === mkIgNode.id; });
});
```

之后从 mkPsb → sboard 解析 `parseInt((mkSb.content||'').match(/^(\d+)s\s*\|/)?.[1])` 取时长。

### mkvideo 时长追溯增强：直接回溯兜底（2026-06-16 新增）

即使 image-gen → prompt 链路不全（如 mkIgNode 为 null 或 mkPsb 为空），也能从 video-prompt 节点直接向上追溯找到 sboard：

```javascript
// 第一路径：image-gen → prompt → sboard （保留原有）
var mkIgNode=S.nodes.find(...);
if(mkIgNode){
  var mkPsb=S.nodes.find(...);
  if(mkPsb&&mkPsb.meta&&mkPsb.meta.duration){
    mkDur=mkPsb.meta.duration;
  }else if(mkPsb){
    // 从 prompt 上游找 sboard 解析 content 中的时长
    var mkSb=S.nodes.find(...);
    if(mkSb){var mkm=(mkSb.content||'').match(/^(\d+)s\s*\|/);if(mkm)mkDur=parseInt(mkm[1]);}
  }
}
// 第二路径（兜底）：从 video-prompt 直接向上回溯
if(!mkDur){
  var mkFbId=nd.id;
  for(var fb=0;fb<6;fb++){
    var mkUp=S.nodes.find(function(n){
      return S.conns.some(function(c){return c.from===n.id&&c.to===mkFbId;});
    });
    if(!mkUp)break;
    if(mkUp.type==="sboard"){/* parse duration from content */;break;}
    if(mkUp.meta&&mkUp.meta.duration){mkDur=mkUp.meta.duration;break;}
    mkFbId=mkUp.id;
  }
}
```

**同时修复了空指针问题**：原 `else { mkPsb.id }` 在 mkPsb 为 null 时报错，改为 `else if(mkPsb)`。

自动创建时，`exGenSBImg` 已经将时长写入 `prompt(video).meta.duration`。`exGenVideoPrompt` 应优先读取这个值，而不是从头解析 sboard content：

```javascript
var vnDur = nd.meta && nd.meta.duration ||
  (sbNode ? parseInt((sbNode.content||"").match(/^(\d+)s/)?.[1]) || 6 : 6);
```

### 输出格式

```
@图片2 [风格/色调总纲]，[主体描述]，[动作序列]，[环境/光影]，[镜头语言]，[音效/对白描述]
```

- 中文，一段话，不超过 200 字
- 必须包含 `@图片2` 引用尾帧参考图
- 前置引用：`@图片1`（首帧）、`@图片2`（尾帧）

### 相机四维编码（镜头语言部分）

| 轴 | 含义 | 取值 |
|----|------|------|
| Z | 景别 | Z1大特写/Z2特写/Z3中近景/Z4中景/Z5中全景/Z6全景/Z7远景/Z8大远景/Z9极远景 |
| Y | 高度 | Y1虫视/Y3低角度/Y4平视/Y5俯拍/Y7顶视 |
| X | 方位 | X1正面/X2四分之三侧/X3侧面/X4背面 |
| F | 焦段 | 24mm广角/35mm环境/50mm人眼/85mm人像/135mm压缩 |

### 分镜图节点新增「生成视频提示词」按钮（2026-06-15 注意：第一次误改到分镜提示词节点被用户纠正）

**分镜图节点**（`image-gen` 类型，标题含"分镜图"）右下角在「📷 生成图片」按钮右边新增「✨ 生成视频提示词」按钮（`data-action="igenvprompt"`），点击调用 `exGenMkVPrompt()` 在右侧创建 `prompt(video)` 节点。

**⚠️ 用户纠正**：第一次我错误地加到了分镜提示词节点（prompt/storyboard），用户明确说「让你改分镜图节点框，不是分镜提示词节点框」。分镜图节点 = `image-gen` 类型，分镜提示词节点 = `prompt` 类型带 `storyboard` meta。这两个节点容易混淆——分镜图节点是图片节点，分镜提示词节点是文字提示词节点。

**渲染位置**（image-gen 节点的 `ig-ctrl` div 中）：
```html
<button class='nbtn' data-action='linkassets'>🔗 关联资产</button>
<button class='nbtn' data-action='genimg'>📷 生成图片</button>
<button class='nbtn' data-action='igenvprompt'>✨ 生成视频提示词</button>
```

**事件绑定**（在 renderNode 的 image-gen 分支添加）：
```javascript
if(nd.type==="image-gen"){
  var genBtn=el.querySelector('[data-action="genimg"]');
  if(genBtn)genBtn.addEventListener("click",function(e){e.stopPropagation();exGenSBImg(nd,el);});
  var linkBtn=el.querySelector('[data-action="linkassets"]');
  if(linkBtn)linkBtn.addEventListener("click",function(e){e.stopPropagation();exLinkAssets(nd,el);});
  var ivpBtn=el.querySelector('[data-action="igenvprompt"]');
  if(ivpBtn)ivpBtn.addEventListener("click",function(e){e.stopPropagation();exGenMkVPrompt(nd,el);});
}
```

**事件绑定**（在 renderNode 的 storyboard 分支添加）：
```javascript
var vpBtn=el.querySelector('[data-action="mkvprompt"]');
if(vpBtn)vpBtn.addEventListener("click",function(e){e.stopPropagation();exGenMkVPrompt(nd,el);});
```

**处理函数**（`exGenMkVPrompt`，注意 title 优先去除" 分镜图"后缀，兼容 prompt 节点调用）：
\`\`\`javascript
function exGenMkVPrompt(nd,el){
  var sbName=nd.title.replace(/ 分镜图$/,'').replace(/ 提示词$/,'');
  // 检查右侧是否已有从该节点出发的视频提示词节点
  var vpn=S.nodes.find(function(n){
    return n.type==="prompt"&&n.meta&&n.meta.video&&
      S.conns.some(function(c){return c.from===nd.id&&c.to===n.id;});
  });
  if(vpn){toast("视频提示词节点已存在");return;}
  vpn=addNode("prompt",null,null,{
    title:sbName+" 分镜视频提示词",content:"",
    meta:{video:true},w:360,h:340
  },false);
  vpn.x=nd.x+nd.w+30;vpn.y=nd.y;
  var vnel=document.getElementById(vpn.id);
  if(vnel){vnel.style.left=vpn.x+"px";vnel.style.top=vpn.y+"px";}
  S.conns.push({from:nd.id,to:vpn.id});updConns();updUI();
  toast("视频提示词节点已创建");
}
\`\`\`

> 与首尾帧模式的自动创建不同，这里只连线到当前节点（不自动连线到其他图片节点）——视频提示词节点内容为空，由用户后续填写或通过 prompt(video) 自己的「✨ 生成视频提示词」按钮调用 LLM 生成。

### 风格追溯

与 `exGenPrompt` 相同的风格追溯链路（已取代旧代码中的全局 `getStylePrefix()`）：

```javascript
// sboard → episode → script/scriptwriter → meta.style
var tracedStyle="";
if(sbNode){
  var epNode=S.nodes.find(n=>S.conns.some(c=>c.from===n.id&&c.to===sbNode.id));
  if(epNode){
    var scriptNode=S.nodes.find(n=>S.conns.some(c=>c.from===n.id&&c.to===epNode.id));
    if(scriptNode&&scriptNode.meta&&scriptNode.meta.style)
      tracedStyle=scriptNode.meta.style;
  }
}
```

### 分镜图片检测

函数会检测上游 image-gen 节点的 `meta.img`（即「分镜图」按钮生成的 storyboard design board 图片），若有则 `userMsg` 提示 LLM 参考图已生成（用节点标题作 `@` 引用，如 `@第1集-1 尾帧图`），否则回退到纯文字描述。

## 关联资产（exLinkAssets）按钮位置：sboard → image-gen

本会话将「🔗 关联资产」按钮从 sboard 节点移到了 image-gen 节点（点击分镜图后创建的图像生成节点），放在「📷 生成图片」按钮之前。

### 渲染位置

image-gen 节点渲染（`renderNode` → `nd.type==="image-gen"`）：

```javascript
body = ... +
  "<button class='nbtn' data-action='linkassets'>🔗 关联资产</button>" +
  "<button class='nbtn' data-action='genimg'>📷 生成图片</button>" +
  ...
```

sboard 节点不再有 linkassets 按钮。

### 事件绑定

```javascript
if (nd.type === "image-gen") {
  var genBtn = el.querySelector('[data-action="genimg"]');
  if (genBtn) genBtn.addEventListener("click", function(e) {
    e.stopPropagation(); exGenSBImg(nd, el);
  });
  var linkBtn = el.querySelector('[data-action="linkassets"]');
  if (linkBtn) linkBtn.addEventListener("click", function(e) {
    e.stopPropagation(); exLinkAssets(nd, el);
  });
}
```

### exLinkAssets 适配 image-gen

当从 image-gen 节点调用时，`exLinkAssets` 不从节点自身的 textarea 读取（image-gen 没有 textarea），而是从上游连接的 prompt(storyboard) 节点读取：

```javascript
if (nd.type === "image-gen") {
  var upNode = S.nodes.find(function(n) {
    return S.conns.some(function(c) { return c.from === n.id && c.to === nd.id; }) && n.type === "prompt";
  });
  if (upNode) text = upNode.content || "";
} else {
  var ta = el.querySelector("textarea");
  text = ta ? ta.value.trim() : (nd.content || "");
}
```

AI 匹配后将 asset 节点直接连接到 image-gen。后续 `exGenSBImg` 生成图片时，`collectRefNodes()` 自动找到连接的 asset 节点，将其图片作为参考图发送。

## 复查（遗漏检测）— 本会话新增

首次提取完成后，自动发起第二次 LLM 调用，传入已提取资产清单，要求找出遗漏项：

```javascript
// 对比已提取的资产名，用第二个 LLM 请求检查遗漏
var reviewPrompt="你是专业美术指导。请复查以下剧本，对比已提取的资产清单，找出遗漏的视觉资产。\n\n"+
  "已提取的角色："+(existingNames.characters.join("、")||"无")+"\n"+
  ...
  "如果无遗漏，输出空JSON：{\"characters\":[],\"scenes\":[],\"props\":[]}";
var r2=await fetch(fixLLMUrl(s.url),{...});
```

复查发现的遗漏资产通过名称去重（`existingNames.indexOf(c.name)<0`），自动创建 asset 节点。
复查出错不影响已有提取结果（`catch` 内只 console.log）。

#### LLM 提示词必须明确指定 JSON 格式（2026-06-20 修复）

**教训**：初始版提示词只说「只输出遗漏资产JSON」，没有指定具体字段名和结构，LLM 返回的 JSON 缺少 `name` 字段 → 资产节点创建时无标题。

**正确格式**：必须逐字段指定 JSON 结构，明确要求 `name` 和 `prompt` 字段：

```javascript
var sysPrompt="你是一个专业的美术指导。请从以下剧本中提取遗漏的视觉资产，严格按JSON格式输出。\n\n"+
  "已有角色："+...+"\n"+
  "已有场景："+...+"\n"+
  "已有道具："+...+"\n\n"+
  "仅输出遗漏的视觉资产，严格按以下JSON格式（不要其他文字）：\n"+
  "{\"characters\":[{\"name\":\"角色名称\",\"prompt\":\"英文文生图提示词\"}],"+
  "\"scenes\":[{\"name\":\"场景名称\",\"prompt\":\"英文场景提示词\"}],"+
  "\"props\":[{\"name\":\"道具名称\",\"prompt\":\"英文道具提示词\"}]}\n"+
  "如果无遗漏，输出：{\"characters\":[],\"scenes\":[],\"props\":[]}\n"+
  "角色prompt必须是英文定妆图描述。场景prompt是纯环境英文描述。"+
  "道具prompt是英文独立物品描述。每个name必须是中文角色/场景/道具名称。";
```

**关键要求**：`name` 字段必须明确指出是中文名称，`prompt` 必须明确指出是英文提示词，且必须提供完整的 JSON 示例模板。

## 节点按钮事件绑定：优先用 onclick 属性，避免 setInterval 绑定

**原则**：对于 `renderNode()` 中通过字符串拼接 `body` 创建的按钮，**优先在 `body` 字符串中使用 `onclick` 属性**，而不是依赖独立的 `setInterval` 或后期事件绑定。

### ✅ 推荐方式：onclick 属性（已验证可靠）

```javascript
// renderNode body 字符串中直接写 onclick
body="<button class='xxx-btn' onclick='handleXxx(this)'>按钮文字</button>";

// 全局函数处理
window.handleXxx=function(btn){
  var node=btn.closest(".node");
  var nd=find(node.id);
  // ... 业务逻辑 ...
};
```

**优点**：事件绑定与 DOM 创建在同一时间点，无竞态条件。适用于 TTS、char-tts 等所有节点类型。

**参考实现**：`pickAssetAudio`（普通TTS节点）和 `pickAssetAudioForCharTTS`（角色TTS节点）都使用此模式：
```javascript
// 在 renderNode body 中：
"<button class='nbtn' onclick='pickAssetAudioForCharTTS(this)'>🎵 资产库</button>"

// 全局函数（定义在资产库 IIFE 的 window 暴露区）：
window.pickAssetAudioForCharTTS=function(btn){
  getAllAssets(function(assets){
    var audios=assets.filter(function(a){return a.type==='audio';});
    if(!audios.length){toast('资产库中暂无音频');return;}
    // 构建浮动下拉面板...
  });
};
```

### ❌ 避免方式：setInterval 轮询绑定

原始 char-tts 节点的"资产库"按钮使用 `setInterval(fn, 500)` 每500ms扫描 `.ct-lib-btn:not(.ct-lib-bound)` 按钮绑定事件。这种方式在 `renderNode` 创建新DOM后可能产生竞态：用户快速点击时，setInterval 还没来得及绑定事件。本会话中已全部替换为 onclick 方式。

```javascript
// ❌ 不再推荐
setInterval(function(){
  var btns=document.querySelectorAll(".xxx-btn:not(.xxx-bound)");
  btns.forEach(function(btn){
    btn.classList.add("xxx-bound");
    btn.addEventListener("click",function(e){...}); // 可能被 renderNode 覆盖
  });
}, 500);
```

### ⚠️ 普通TTS节点角色TTS节点共用同一个浮动面板id

`pickAssetAudio` 和 `pickAssetAudioForCharTTS` 都使用 `document.getElementById('alAudioPicker')` 检测/销毁旧的浮动面板。两者共用一个选择器id，不可同时打开两个面板。这是预期行为——一次只能选一个音频。

### 浮动下拉面板音频名称支持重命名（双击名称或点击✏️按钮）

两个音频选择函数（`pickAssetAudio` 和 `pickAssetAudioForCharTTS`）的浮动下拉面板中，每个音频项目支持重命名：

**交互方式**：
- **双击名称**（`dblclick`）→ 替换为 `<input>` 编辑框
- **点击 ✏️ 按钮** → 同样触发重命名
- 编辑框行为：Enter → 保存；Escape → 还原原名称；失焦（`blur`）→ 保存
- 保存后更新 IndexedDB 并调用 `renderAssets()` 刷新侧边栏

**实现要点**：

```javascript
// 用 document.createElement 构建项目，不用 innerHTML
var nameSpan=document.createElement('span');
nameSpan.textContent=a.name;
nameSpan.title='双击重命名';

var renameBtn=document.createElement('span');
renameBtn.textContent='✏️';
renameBtn.style.cssText='font-size:10px;cursor:pointer;flex-shrink:0;opacity:0.4';

// 点击项目选择音频（排除 renameBtn、audio 控件、INPUT 编辑框）
item.addEventListener('click',function(ev){
  if(ev.target.tagName==='AUDIO'||ev.target.closest('audio')||ev.target===renameBtn||ev.target.tagName==='INPUT')return;
  // 选择音频逻辑...
});

// 重命名用 stopPropagation 防止触发选择
nameSpan.addEventListener('dblclick',function(ev){ev.stopPropagation();startRename();});
renameBtn.addEventListener('click',function(ev){ev.stopPropagation();startRename();});
```

**关键注意点**：
- 必须用 `ev.stopPropagation()` 防止 rename action 触发 item 的 click（选择音频并关闭面板）
- 必须用 `ev.target===renameBtn` 和 `ev.target.tagName==='INPUT'` 在 click 处理中排除重命名操作
- 保存后创建新的 span 替换 input，新 span 需重新绑定 dblclick
- 名字保存到 IndexedDB 后需调用 `renderAssets()` 刷新侧边栏同步显示
- 两个函数（普通TTS和角色TTS）的 dropdown 都支持重命名，实现代码相同

### 资产库侧边栏：音频和视频禁止拖入画布

侧边栏资产库（`renderAssets()`）中，所有资产默认 `draggable=true`。视频和音频类型在 `dragstart` 事件中 `e.preventDefault()` 阻止拖拽：

```javascript
// renderAssets 中的 dragstart 事件
div.addEventListener('dragstart',function(e){
  if(a.type==='video'||a.type==='audio'){e.preventDefault();return;}
  e.dataTransfer.setData('text/asset-id',a.id);
  // ...
});
```

**规则**：只有图片和文本资产可以拖入画布创建 asset 节点。音频和视频资产只能通过按钮选择（如 TTS 节点的资产库按钮），不能通过拖放创建。

### char-tts 节点资产库按钮实现参考

该节点的资产库按钮完整实现由三部分构成：

1. **renderNode body**（第1180行）：按钮 HTML 加上 `onclick='pickAssetAudioForCharTTS(this)'`
2. **全局函数**（资产库IIFE末尾，约第3689行）：`window.pickAssetAudioForCharTTS` 创建浮动下拉面板
3. **setInterval 跳过**（第1719行）：旧 setInterval 检测 `onclick` 属性后跳过该按钮，避免双重绑定

按钮选择音频后更新：
- `nd.meta.audio` ← 选中音频的 data URL
- `nd.meta.fileName` ← 选中音频的文件名
- DOM: `.char-tts-audio` (插入 `<audio>` 播放器) + `.ct-name` (文件名显示)

### ⚠️ 参考图抓取方式：collectRefNodes maxHops=0（直连模式，2026-06-20 最终定稿）

`exGenSBImg`（分镜图生成）中 `collectRefNodes` 的追溯层数经多次来回后**最终定为 0 层**——只取直接连接到 image-gen 节点的资产：

```javascript
var refNodes = collectRefNodes(nd.id, 0);  // 只取直连资产，不向上追溯
```

**演变历史**：
- 原始 `canvas/canvas.html`：10 跳（BFS 全链追溯）→ 会误抓不相关资产
- 2026-06-19 `app/public` 改为 1 跳 → ❌ 找不到 sboard 上的资产（需 3+ 跳）
- 2026-06-19 改为 5 跳 → ✅ 能找到但用户批评「不应该向上追溯」
- **2026-06-20 最终：0 跳** → 用户要求只取直连资产，通过「关联资产」按钮手动连接

**工作流**：用户点击 image-gen 节点上的「关联资产」按钮 → LLM 分析分镜内容 → 匹配的 asset 直接连接到 image-gen → 生成图片时只取这些直连 asset 的图片作为 reference images。

### 语种感知节点命名（hasChinese + localLabel，2026-06-20 新增）

```javascript
function hasChinese(t){return/[\\u4e00-\\u9fff]/.test(t);}
function localLabel(text,zh,en){return hasChinese(text||"")?zh:en;}
```

资产节点和 char-tts 节点标题通过 `localLabel(text, 中文, 英文)` 包裹，根据剧本内容中的 CJK 字符自动切换：

| 位置 | 中文剧本 | 英文剧本 |
|------|---------|---------|
| asset 角色 | `角色1 张三` | `Character 1 Zhang San` |
| asset 场景 | `场景1 办公室` | `Scene 1 Office` |
| asset 道具 | `道具1 手机` | `Prop 1 Phone` |
| char-tts | `张三-语音` | `Zhang San-TTS` |

**同步两份文件**：`app/public/Kairos_canvas.html` 和 `canvas/canvas.html`。

**⚠️ LLM 的 name 字段也需提示词约束**：`"每个name必须使用剧本原文中的名称（剧本是中文则name用中文，剧本是英文则name用英文）"`。仅靠 `localLabel` 包裹前缀不够，LLM 返回的 `c.name` 也需与剧本语种一致。

### 资产复查（exReviewAssets）— 本会话新增/反复修正

剧本节点（`script`）名称栏（nhdr）中的「复查资产」按钮，调用 LLM 核查遗漏资产，只补不删。

#### ⚠️ 编号公式（2026-06-20 最终确定）

```javascript
// ✅ 正确：existingCount + counter + 1（counter 每类型独立，不加 i 索引）
"角色" + (existingChars.length + cc + 1) + " " + c.name

// ❌ 错误：cc 和 i 双重累加导致跳号
"角色" + (existingChars.length + cc + i + 1)  // 15→17→19
```

#### ⚠️ 位置计算（2026-06-20 最终确定——纯右扩展，不改变行高）

用户确认复查时**必定存在同类已有资产**，无需兜底逻辑：

```javascript
// 角色：取已有角色最右 X + 已有角色 Y，纯右向延伸
var cx = nd.x + 40, cy = nd.y, cc = 0;
existingChars.forEach(function(e) {
  if (e.node) { var r = e.node.x + e.node.w + 20; if (r > cx) cx = r; cy = e.node.y; }
});
// 新角色：nn.x = cx + cc * 340; nn.y = cy;
// 场景、道具同理（各自独立计数器 sc/pc、独立起始位置 sx/px）

// 🔑 关键：每类型用自己独立的计数器（cc/sc/pc），绝对不共享 addCount
```

#### LLM 提示词规则（与 exExtract 保持一致）

复查提示词必须包含：
- 角色定妆图规范（单人、三视图、材质等）
- 场景纯环境约束（不含人物/动物/生物）
- 道具三个不提取规则（绑定角色/场景的不提取）
- **不提取已有资产** + **不提取一次性资产**（仅出现一次的场景/道具）
- **严格依据剧本原文，不得自行遐想添加不存在的资产**
- 名称与剧本语种同步
- 输出 JSON 模板（含 `name` + `prompt` 字段）
- Style 回退链：`nd.meta.style` → `S.cfg.style`

**⚠️ 2026-06-20 补丁**：初期提示词只说"只输出JSON"，LLM 返回的 JSON 缺少 `name` 字段。必须逐字段指定 JSON 结构。

#### 新角色自动创建 char-tts 节点

```javascript
var charName = c.name || (c.prompt || "").split(/[.!?，。！？]/)[0];
var stxt = nd.content || "";
var hd = stxt.indexOf(charName) >= 0 && (
  stxt.indexOf("「" + charName) >= 0 || stxt.indexOf('"' + charName) >= 0 ||
  stxt.indexOf(charName + "：") >= 0 || stxt.indexOf(charName + ":") >= 0 ||
  stxt.indexOf("(" + charName + ")") >= 0 || stxt.indexOf("（" + charName + "）") >= 0);
if (!hd) { var ne = charName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  hd = new RegExp("[「\"]\\s*" + ne + "|^" + ne + "[：:]|\\(\\s*" + ne + "\\s*\\)|[（]\\s*" + ne + "[）]|" + ne + "台词").test(stxt); }
if (hd) {
  var tn = addNode("char-tts", null, null, {
    title: localLabel(text, charName + "-语音", charName + "-TTS"),
    meta: { charName: charName, charNodeId: nn.id }
  });
  tn.x = nn.x; tn.y = nn.y + nn.h + 20;  // 角色节点正下方
  S.conns.push({ from: nn.id, to: tn.id });
}
```

### nhdr 内联按钮通用模式（2026-06-20 新增/多次修复） 

1. **CSS**：按钮默认 `display:none`，父节点类型选择器控制显隐
2. **渲染**：在 `el.innerHTML` 的 badge 和 sz 之间三元条件插入
3. **事件绑定**：在 `renderNode` 对应类型的 `addEventListener` 块中添加（⚠️ 不能只加在类似类型如 `scriptwriter` 的块中——`script` 也要加）
4. **拖拽冲突**：nhdr mousedown 必须排除所有可交互元素：

```javascript
// 必须显式排除：ttl（标题输入）、cls（关闭按钮）、rv-btn（复查资产按钮）
if (e.target.closest(".nhdr") && !e.target.classList.contains("ttl")
    && !e.target.classList.contains("cls") && !e.target.classList.contains("rv-btn")) {
  startDrag(e, nd.id); e.stopPropagation();
}
```

**通用原则**：nhdr 内所有可交互元素都必须在拖拽启动条件中显式排除，否则点击触发节点拖拽而非按钮点击。

### 提示词全部改为中文（2026-06-20 批量转换）

所有提供给 LLM 或图像生成 API 的提示词模板从英文改为中文：

| 函数/位置 | 原语言 | 现语言 | 说明 |
|-----------|--------|--------|------|
| `charVariantPrompt` | 英文 | 中文 | 角色定妆图 layout 描述 |
| `sceneVariantPrompt` | 英文 | 中文 | 场景设计图 layout 描述 |
| `propVariantPrompt` | 英文 | 中文 | 道具设计图 layout 描述 |
| `exGenStartPrompt` | 英文 | 中文 | 首帧提示词生成 system prompt |
| `exGenEndPrompt` | 英文 | 中文 | 尾帧提示词生成 system prompt |
| `exGenPrompt` | 英文 | 中文 | 分镜设计图 M 版模板 |
| `exGenSBImg` 参考图文字 | 英文 | 中文 | REFERENCE IMAGES → 参考图 |
| `LINKED REFERENCE ASSETS` | 英文 | 关联参考资产 | 资产 section 标题 |
| `Description:` | 英文 | 描述： | charVariant 前缀 |

**⚠️ 禁止在生成提示词中包含模型特定参数**（如 `--ar 16:9 --v 6.0`、`--s`、`--style` 等 Midjourney/SD 参数）。

**⚠️ 覆盖写回文件**：Python 脚本执行 `html.replace()` 后必须在脚本末尾调用 `with open(path, 'w')` 写回。本会话 3 次忘记保存，只有 `print` 没有 `write`。

## 画布数据持久化：save/load key 架构

| 键名 | 写入位置 | 读取位置 | 用途 |
|------|---------|---------|------|
| `kc-cvs` | `saveCvs()`, 30s auto-save, Ctrl+S | `loadCvs()` 初始化 | 主画布数据（单模式版本唯一键） |
| `kc-cvs-story` | `copyToStory()` 函数 | **从不被 loadCvs 读取** | 故事板模式备份（仅双模式画布） |
| `kc-mode` | 30s auto-save interval | init IIFE 读取 | 当前模式（frame/story），单模式版本无需 |
| `kc-cfg` | `saveSet()` | `loadCfg()` 初始化 | 模型配置 |
| `kc-style` | `saveCvs()`, auto-save | init IIFE fallback | 全局风格（向后兼容） |

### ⚠️ 数据丢失排查（本会话）

当用户报「画布内容丢失」时：

1. **检查 `kc-cvs`**（主键）是否有数据
2. 如果空，检查 `kc-cvs-story`（可能之前保存到故事板备份了）
3. 如果 `kc-cvs-story` 有数据但 `kc-cvs` 空，导致 `loadCvs()` 加载空白
4. 如果两个都空 → 数据确实丢失（localStorage 被清空或从未保存过）

### 单模式 vs 双模式画布键差异

单模式画布（`canvas/canvas.html`，仅故事板模式）：
- `saveCvs()` 写 `kc-cvs`
- `loadCvs()` 读 `kc-cvs`
- 30s auto-save 写 `kc-cvs`
- 无 `kc-mode` 持久化

双模式画布（`app/public/canvas.html`，首尾帧+故事板）：
- `saveCvs()` 写 `kc-cvs-${canvasMode}`（如 `kc-cvs-frame`, `kc-cvs-story`）
- `loadCvs()` 读 `kc-cvs`
- 30s auto-save 写 `kc-cvs`
- 必须写 `kc-mode`（见"模式持久化陷阱"章节）

## 母版提示词（分镜设计图提示词母版 M 版）

分镜设计图的生成使用一个固定的 system prompt 模板，定义在 `exGenPrompt()` 函数中（硬编码 `sysPrompt` 变量，line ~2349）。

### 当前架构

- **参考文档**：`references/storyboard-m-template.md` 记录了模板结构和 LLM 提示词指令
- **代码位置**：`exGenPrompt()` 中的 `sysPrompt` 字符串（硬编码，无 UI）
- **无用户可编辑的"母版提示词"字段** — sysPrompt 无法通过设置面板或节点 UI 修改

### 模板内容

该模板生成带有特定布局的分镜设计图（production design board）：
- 暗色背景（#0A0A0C）+ 金色边框（#D4AF37）
- 三层布局：HEADER(35%)、MIDDLE(30%)、FOOTER(35%)
- 包含类型/简介、主图、角色设计、分镜 strip、色卡、灯光参考、道具参考等
- 输出 16:9 landscape (2560×1440)

其他生成函数也有各自的硬编码 sysPrompt：
- `exGenStartPrompt()` — 首帧画面提示词（line ~2244）
- `exGenEndPrompt()` — 尾帧画面提示词（line ~2296）
- `exGenVideoPrompt()` — Seedance 2.0 视频提示词（line ~2551）

### 未来扩展方向

如需让用户可编辑母版提示词，可在设置面板或 sboard 节点 UI 中添加 textarea 字段：

```javascript
// 大致思路
var masterPrompt = nd.meta && nd.meta.masterPrompt || DEFAULT_MASTER_PROMPT;
// 在 renderNode 的 sboard 分支添加 textarea 供用户编辑
// 保存在 nd.meta.masterPrompt 中，随 canvas 数据持久化
排查发现 `w:320, h:160` 误放在 `meta` 对象中。

### 资产复查按钮（nhdr 内联按钮模式，2026-06-20 新增）\n\n剧本（`script`）和剧本创作（`scriptwriter`）节点框的名称栏（nhdr）中间有一个「复查资产」按钮，功能为调用 LLM 核查遗漏资产，只补不删。

#### CSS（独立样式，通过 display 控制显隐）

```css
.nhdr .rv-btn{display:none;font-size:10px;padding:1px 6px;border:none;border-radius:4px;cursor:pointer;background:#f59e0b;color:#fff;line-height:1.6;white-space:nowrap}
.node[data-type='script'] .nhdr .rv-btn{display:inline-block}
```

**模式**：按钮默认隐藏（`display:none`），仅特定节点类型通过类型选择器显示。通用 nhdr 模板无需分支判断。

#### nhdr 渲染（badge 和 sz 之间条件插入）

```javascript
"<span class='badge'>"+TNAMES[nd.type]+"</span>"+
(nd.type==="script"?"<button class='rv-btn' data-action='reviewassets' title='资产复查'>复查资产</button>":"")+
"<span class='sz'>..."+
```

#### 事件绑定

**⚠️ 关键陷阱**：监听器必须在 `script` 类型块中添加。按钮目前仅对导入剧本（`script`）节点显示，剧本创作（`scriptwriter`）已去除。

```javascript
// script 分支中：
var rb=el.querySelector('[data-action="reviewassets"]');
if(rb)rb.addEventListener("click",function(e){e.stopPropagation();exReviewAssets(nd,el);});
```

#### nhdr 拖拽冲突

nhdr 的 `mousedown` 处理器必须排除 `rv-btn`，否则点击按钮触发节点拖拽而非按钮点击：

```javascript
if(e.target.closest(".nhdr")&&!e.target.classList.contains("ttl")&&!e.target.classList.contains("cls")&&!e.target.classList.contains("rv-btn")){
```

**通用原则**：nhdr 内所有可交互元素都必须在拖拽启动条件中显式排除。

#### 功能逻辑（exReviewAssets）

1. 读取节点 textarea 的剧本内容
2. 查找**直接连接**到该节点的所有 `asset` 节点，按角色/场景/道具分类
3. 调用 LLM 复查，传入已提取清单，要求找出遗漏视觉资产
4. 解析 JSON 响应，按名称去重（`cn.indexOf(c.name)<0`）只新增不重复
5. 新资产**按类型排序**：角色排在已有角色右侧，场景排在已有场景右侧，道具排在已有道具右侧（各自独立计算起始位置）
6. 不删除/修改任何已有资产

**与 exExtract 内联复查的区别**：exExtract 复查基于刚刚提取的 JSON 结果创建节点；exReviewAssets 基于已存在于画布中并连接到该节点的 asset 节点进行去重。

#### 复查新角色自动创建 char-tts 节点（2026-06-20 新增）

当 `exReviewAssets` 发现新角色资产时，自动检测该角色在剧本中是否有对白，有则创建 char-tts 节点：

```javascript
var charName=c.name||(c.prompt||"").split(/[.!?，。！？]/)[0];
var scriptText=nd.content||"";
var hasDialogue=scriptText.indexOf(charName)>=0&&(
  scriptText.indexOf("「"+charName)>=0||scriptText.indexOf('"'+charName)>=0||
  scriptText.indexOf(charName+"：")>=0||scriptText.indexOf(charName+":")>=0||
  scriptText.indexOf("("+charName+")")>=0||scriptText.indexOf("（"+charName+"）")>=0);
if(!hasDialogue){var nameEsc=...;hasDialogue=new RegExp(...).test(scriptText);}
if(hasDialogue){
  var ttsNode=addNode("char-tts",null,null,{
    title:localLabel(text,charName+"-语音",charName+"-TTS"),...});
  ttsNode.x=nn.x;ttsNode.y=nn.y+nn.h+20;  // 角色节点正下方
  S.conns.push({from:nn.id,to:ttsNode.id});
}
```

**位置**：TTS 节点在角色资产节点正下方（`nn.y+nn.h+20`）。

#### ⚠️ 修正历史：exReviewAssets 经历 3 轮迭代最终确定（2026-06-20）

| 版本 | 编号公式 | 位置计算 | 问题 |
|------|---------|---------|------|
| 初始 | `existingCount + addCount + i + 1`（`addCount` 跨类型共享） | `nd.x+40+(addCount%4)*340` 统一网格 | 场景/道具位置错乱，编号跳跃 |
| 修复 | `typeStartX/typeStartY/ny` 三层辅助函数 | 每类型独立起始位置 | 无同类时放在 `ny()` 上方 400px |
| **最终** | **`existingCount + counter + 1`（`counter` 每类型独立 `cc/sc/pc`，不加 `i`）** | **inline 计算：取同类资产最右 X + 同类资产 Y，纯右扩展** | ✅ 用户确认 |

### 复查资产提示词必须与提取资产提示词一致（2026-06-20 用户纠正）

初期复查提示词太简略（只说"只输出JSON"），LLM 返回的 JSON 缺少 `name` 字段。必须包含：
- 角色定妆图规范（单人、三视图、材质等）
- 场景纯环境约束（不含人物/动物）
- 道具三个不提取规则（绑定角色/场景的不提取）
- 只提取遗漏资产 + 不提取重复资产 + 不提取一次性资产（仅出现一次的场景/道具）
- 输出 JSON 模板（含 `name` 和 `prompt` 字段）

Style 回退：`nd.meta.style`（节点自身）→ `S.cfg.style`（全局），与 `exExtract` 一致。

参考文件：`references/asset-review-exreviewassets.md`（完整实现代码）

#### 资产复查按钮（nhdr 内联按钮模式，2026-06-20 新增）

剧本（`script`）节点框名称栏（nhdr）中间的「复查资产」按钮，调用 LLM 核查遗漏资产，只补不删。

**CSS**（默认隐藏，类型选择器控制显隐）：
```css
.nhdr .rv-btn{display:none;font-size:10px;padding:1px 6px;border:none;border-radius:4px;cursor:pointer;background:#f59e0b;color:#fff;line-height:1.6;white-space:nowrap}
.node[data-type='script'] .nhdr .rv-btn{display:inline-block}
```

**nhdr 渲染**（badge 和 sz 之间条件插入 + 拖拽冲突排除）：
```javascript
// 渲染
(nd.type==="script"?"<button class='rv-btn' data-action='reviewassets'>复查资产</button>":"")+
// 拖拽冲突 — nhdr mousedown 必须排除 rv-btn
if(/*...*/&&!e.target.classList.contains("rv-btn")){startDrag(...)}
```

**事件绑定** — 必须在 `script` 类型块中添加（不只在 `scriptwriter` 块）：
```javascript
var rb=el.querySelector('[data-action="reviewassets"]');
if(rb)rb.addEventListener("click",function(e){e.stopPropagation();exReviewAssets(nd,el);});
```

**功能逻辑**：
1. 读取剧本内容 → 查找直接连接的 asset 节点（角色/场景/道具分类）
2. LLM 复查（提示词与 exExtract 一致）+ 不提取重复资产 + 不提取一次性资产
3. 新资产**按类型独立编号**：`existingChars.length + cc + 1`（`cc` 每类型独立，不加 `i`）
4. **位置**：取同类已有资产的 Y 坐标，从最右侧向右扩展（`nn.x=cx+cc*340; nn.y=cy`）
5. 新角色有对白 → 自动在正下方创建 char-tts 节点
6. 不删除/修改任何已有资产

**⚠️ 编号公式陷阱**：`existingChars.length + cc + i + 1` 中 `cc` 和 `i` 双重累加导致跳号。**正确公式**：`existingChars.length + cc + 1`（不加 `i`）。

**⚠️ addCount 不能跨类型共享**：角色/场景/道具各自用独立计数器（`cc/sc/pc`），否则后处理的类型从很远的 X 开始排列。

#### ⚠️ 新资产计数陷阱：跨类型共享 addCount 导致位置错乱（已修复，最终版使用独立计数器 + 纯右扩展定位）\n\n**症状**：复查后角色资产的 X 位置正确，但场景/道具资产从很远的右侧开始排列。\n\n**根因**（已修复）：角色、场景、道具三种资产共用同一个 `addCount` 变量。如果复查补充了 3 个角色，场景新资产就从 `sx + 3*340` 开始而不是从 `sx + 0*340` 开始。\n\n**编号公式陷阱（2026-06-20 修复）**：早期版本使用 `existingChars.length + cc + i + 1`。`cc`（已加数）和 `i`（循环索引）双重累加导致跳号（15→17→19）。**正确公式**：`existingChars.length + cc + 1`（不加 `i`）。\n\n**位置计算最终版（2026-06-20）**：经多次迭代后确定最简单可靠的方式——同类型资产必定存在，直接用其 Y 坐标，纯右扩展：\n\n```javascript\n// 角色：从已有角色最右侧往后排，同一行 Y\nvar cx=nd.x+40,cy=nd.y,cc=0;\nexistingChars.forEach(function(e){if(e.node){var r=e.node.x+e.node.w+20;if(r>cx)cx=r;cy=e.node.y;}});\n(missed.characters||[]).forEach(function(c,i){if(cn.indexOf(c.name)<0){\n  var nn=addNode(\"asset\",null,null,{title:localLabel(text,\"角色\"+(existingChars.length+cc+1)+\" \"+c.name,...)});\n  nn.x=cx+cc*340;nn.y=cy; // 纯右向扩展，不改变行高\n  ...cc++;\n}});\n\n// 场景、道具同理（各自独立计数器 sc/pc）\n```\n\n**演变历史**：\n1. 初始版：共用 `addCount` + `nd.x+40+(addCount%4)*340` 统一网格 → 场景/道具位置错乱\n2. 修复版：`typeStartX/typeStartY/ny` 三层辅助函数 → 无同类时放在所有资产上方\n3. **最终版（当前）**：用户确认不存在无同类资产情况 → 直接取同类资产 Y，纯右扩展，三行独立 inline 计算，无需辅助函数\n\n**原则**：画布中同时创建多个类型的节点时，每个类型的计数/编号/位置必须独立。编号公式中 `cc` 和 `i` 不能同时出现。对于复查补缺场景，取同类资产的 Y 坐标是最可靠的方式。

#### ⚠️ 新资产位置遮挡修复（typeStartX/typeStartY/ny，2026-06-20 修复）

**症状**：复查补充的新道具/场景节点与现有场景/角色节点框重叠。

**根因**：`startY` 使用 `nd.y+nd.h+80`（剧本节点下方）作为默认 Y。如果该类型无已有资产，新资产被放置在剧本节点下方，可能正好与其他类型的已有资产行重叠。

**修复方案（三层辅助函数）**：

```javascript
// 起始X：放在已有同类资产最右侧；无同类则从 nd.x+40 开始
function typeStartX(nodes){
  var mx=nd.x+40;
  nodes.forEach(function(e){if(e.node&&e.node.x+e.node.w+20>mx)mx=e.node.x+e.node.w+20;});
  return mx;
}
// 起始Y：沿用同类资产第一个节点的Y；无同类则调用 ny()
function typeStartY(nodes){
  for(var i=0;i<nodes.length;i++)if(nodes[i].node)return nodes[i].node.y;
  return ny();
}
// 无同类时：取所有已有资产（角色+场景+道具）的最小Y（最上方），再减去400px（行高+间距），放在所有资产上方
function ny(){
  var my=nd.y+nd.h+80;
  existingChars.concat(existingScenes,existingProps).forEach(function(e){
    if(e.node&&e.node.y<my)my=e.node.y;
  });
  return my-400;
}
```

**三层逻辑**：
1. 有同类资产 → 沿用该类型的 Y（与已有资产同一行）
2. 无同类但有其他类型资产 → 放在所有资产最上方（`ny()` 取最小 Y - 400px）
3. 完全无资产 → `nd.y+nd.h+80`（剧本节点下方）

**适用于 `exReviewAssets` 中的所有 3 类资产创建**（角色/场景/道具）。
```

**原则**：画布中同时创建多个类型的节点时，每个类型的计数/编号/位置必须独立。

#### nhdr 内联按钮通用模式

1. **CSS**：按钮默认 `display:none`，通过父节点类型选择器控制显隐
2. **渲染**：在 `el.innerHTML` 拼接中三元条件插入
3. **事件**：在 renderNode 事件绑定块中添加 `addEventListener`
4. **适合**：频率低、不占 body 空间的辅助操作
5. **不适合**：高频操作或需要复杂控件的情况（应放 body footer）

## 节点框名称条渐变色（2026-06-20 统一更新）

所有节点类型的 `.nhdr`（名称条）背景改为从线框颜色到白色的渐变色：

```css
/* 基础 nhdr：移除旧 background:rgba(0,0,0,0.02)，由各类型覆盖 */
.nhdr{display:flex;align-items:center;padding:6px 8px;border-bottom:1px solid rgba(0,0,0,0.1);cursor:move;gap:4px;flex-shrink:0}

/* 使用 CSS 变量的类型（从 :root 取值） */
.node[data-type="storyboard"] .nhdr{background:linear-gradient(to right, var(--node-storyboard), #fff)}
.node[data-type="character"] .nhdr{background:linear-gradient(to right, var(--node-character), #fff)}
.node[data-type="scene"] .nhdr{background:linear-gradient(to right, var(--node-scene), #fff)}
.node[data-type="scriptwriter"] .nhdr{background:linear-gradient(to right, var(--node-scriptwriter), #fff)}
.node[data-type="episode"] .nhdr{background:linear-gradient(to right, var(--node-episode), #fff)}
.node[data-type="text"] .nhdr{background:linear-gradient(to right, var(--node-text), #fff)}
.node[data-type="prompt"] .nhdr{background:linear-gradient(to right, var(--node-prompt), #fff)}
.node[data-type="llm"] .nhdr{background:linear-gradient(to right, var(--node-llm), #fff)}
.node[data-type="image"] .nhdr{background:linear-gradient(to right, var(--node-image), #fff)}
.node[data-type="video"] .nhdr{background:linear-gradient(to right, var(--node-video), #fff)}
.node[data-type="tts"] .nhdr{background:linear-gradient(to right, var(--node-tts), #fff)}
.node[data-type="script"] .nhdr{background:linear-gradient(to right, var(--node-script), #fff)}
.node[data-type="image-gen"] .nhdr{background:linear-gradient(to right, var(--node-image-gen), #fff)}
.node[data-type="video-gen"] .nhdr{background:linear-gradient(to right, var(--node-video-gen), #fff)}

/* 硬编码颜色的类型 */
.node[data-type='asset'] .nhdr{background:linear-gradient(to right, #6366f1, #fff)}
.node[data-type='sboard'] .nhdr{background:linear-gradient(to right, #ec4899, #fff)}
.node[data-type='char-tts'] .nhdr{background:linear-gradient(to right, #8b5cf6, #fff)}
```

**渐变方向**：`to right`（从左到右），左边=线框颜色，右边=白色。

**类型全覆盖检查清单**：storyboard, character, scene, scriptwriter, episode, asset, sboard, char-tts, text, prompt, llm, image, video, tts, script, image-gen, video-gen。每个类型必须有一条 `.nhdr` 渐变规则。

## 视频生成节点默认大小

video-gen 节点的默认尺寸已从 450×200 改为 **580×290**（`dW("video-gen")` / `dH("video-gen")`）。通过 `addNode("video-gen", ...)` 创建（如 mkvideo 函数）或从右键菜单创建时均使用此默认值。

## 剧本资产提取（exExtract）— 道具过滤规则

`exExtract()` 调用 LLM 从剧本内容中提取 characters、scenes、props 三类资产并创建 asset 节点。

### LLM 提示词要点

系统提示词要求 LLM 输出严格 JSON：`{characters: [{name, prompt}], scenes: [{name, prompt}], props: [{name, prompt}]}`

角色 prompt 使用 `charVariantPrompt()`（look-dev reference sheet 格式），场景用 `sceneVariantPrompt()`，道具用 `propVariantPrompt()`。

### 语种感知节点命名（hasChinese + localLabel，2026-06-20 新增）

资产节点和角色 TTS 节点的标题应根据剧本内容语种自动切换中文/英文标签。

#### 辅助函数（紧贴 `exExtract` 定义）

```javascript
// 检测文本语种（是否含中文字符）
function hasChinese(t){return/[\u4e00-\u9fff]/.test(t);}
// 根据剧本语种返回本地化标签
function localLabel(text,zh,en){return hasChinese(text||"")?zh:en;}
```

#### 应用位置（exExtract 中所有 8 处创建节点）

| 位置 | 行（Kairos_canvas） | 中文剧本 | 英文剧本 |
|------|---------------------|---------|---------|
| asset - 角色 | 首批 + 复查 | `"角色"+(i+1)+" "+c.name` | `"Character "+(i+1)+" "+c.name` |
| asset - 场景 | 首批 + 复查 | `"场景"+(i+1)+" "+sc.name` | `"Scene "+(i+1)+" "+sc.name` |
| asset - 道具 | 首批 + 复查 | `"道具"+(i+1)+" "+p.name` | `"Prop "+(i+1)+" "+p.name` |
| char-tts | 首批 | `charName+"-语音"`（`localLabel` 决定中文） | `charName+"-TTS"`（`localLabel` 决定英文） |

**注意**：`char-tts` 在 `canvas/canvas.html`（故事板单模式版）中不存在这个创建逻辑——它只在双模式画布 `Kairos_canvas.html` 中有。

#### 判断逻辑

用 CJK unicode 范围 `[\u4e00-\u9fff]` 检测剧本文本。只要包含中文字符就按中文处理。纯英文/拼音文本按英文处理。

#### 同步两份文件

`app/public/Kairos_canvas.html` 和 `canvas/canvas.html` 都要改。`canvas/canvas.html` 没有 `char-tts` 节点创建逻辑（storyboard 单模式），所以只改 6 处 asset 标题。

### 道具过滤规则（本会话添加）

LLM 提示词末尾追加了三条规则，防止过度提取：

```
道具提取规则（重要）：
- 不提取绑定角色的固定道具（如角色的专属武器、眼镜、帽子、背包、配饰等——这些属于角色设计的一部分）
- 不提取绑定场景的固定道具（如场景中的家具、灯具、装饰物、树木、车辆等——这些属于场景设计的一部分）
- 只提取独立于角色和场景的独立道具（如剧情关键物品、可由多个角色使用的通用物品、故事核心道具）
```

### 场景资产节点提示词规则（双层约束）

#### 第一层：LLM 提取提示词（exExtract system prompt，2026-06-16 新增）

在 exExtract 的 LLM 系统提示词中，角色指令之后、道具提取规则之前，新增了场景约束规则，**让 LLM 在提取阶段就不生成含角色的场景描述**：

```
场景prompt必须是纯环境描述，禁止包含任何角色、人物、动物、生物。只能描述场景本身的建筑、光线、植被、天气、材质等环境要素，不能出现人物或角色。
```

#### 第二层：场景提示词生成函数（sceneVariantPrompt）

`sceneVariantPrompt()` 中已明确禁止生成任何人物和动物：

```
CRITICAL: NO humans, NO people, NO characters, NO animals, NO creatures, NO figures,
NO silhouettes, NO body parts whatsoever -- scene must be completely empty of any
living beings, pure environment only, no living creatures of any kind.
```

**修改历史**：原始版本只有 `NO humans/people/characters`，2026-06-15 用户要求补充 `NO animals, NO creatures` 以及 `no living creatures of any kind`，确保场景参考图不含任何活物。

### 风格传递

`exExtract()` 中所有 6 处资产创建（首批 3 类 + 复查 3 类）优先从 `nd.meta.style` 追溯（nd 为 script 或 scriptwriter 节点），fallback 到全局 `S.cfg.style`：

```javascript
// 优先追溯脚本节点本身的 meta.style
var sp = nd.meta && nd.meta.style
  ? (STYLE_MAP[nd.meta.style] || nd.meta.style) + ", "
  : "";
if (!nd.meta || !nd.meta.style) {
  var gs = S.cfg.style;
  if (gs) sp = (STYLE_MAP[gs] || gs) + ", ";
}
```

`charVariantPrompt()`、`sceneVariantPrompt()`、`propVariantPrompt()` 三个函数都通过 `styleP` 参数接收该风格字符串，拼接在所有 sections 末尾。

⚠️ **修改一致性**：`exExtract` 中有 6 处风格调用（首批角色/场景/道具 + 复查遗漏角色/场景/道具），必须全部统一追溯逻辑，漏改一处会导致同一个提取操作中部分资产使用节点风格、部分使用全局风格。

## video-gen 节点：无 textarea，提示词从上游节点读取

`mkvideo`（「分镜视频」按钮）创建 video-gen 节点时，**不再复制提示词文本**到 `nd.content`：

```javascript
// ❌ 旧：复制文本到节点
var vgn=addNode("video-gen",null,null,{title:sbName2+" 分镜视频",content:text,meta:{duration:mkDur}},false);
// ✅ 新：content 为空，提示词从上游读取
var vgn=addNode("video-gen",null,null,{title:sbName2+" 分镜视频",content:"",meta:{duration:mkDur}},false);
```

渲染时 textarea 替换为只读标签：

```javascript
// ❌ 旧：textarea 可编辑
body="<textarea placeholder='视频提示词...' style='flex-shrink:0;max-height:80px'>"+nd.content+"</textarea>"+
// ✅ 新：只读提示
body="<div class='vg-src' style='padding:6px 8px;font-size:11px;color:#888;border-bottom:1px solid rgba(0,0,0,0.06);flex-shrink:0'>提示词来自上游节点</div>"+
```

事件处理中移除 textarea 监听：

```javascript
// ❌ 旧
var ta=el.querySelector("textarea");
if(ta){ta.addEventListener("input",function(){nd.content=ta.value;});ta.addEventListener("mousedown",function(e){e.stopPropagation();});}
// ✅ 新：直接删除这两行
```

`exGenVideo` 生成视频时从连接的 prompt 节点读取（已有逻辑，未改动）：

```javascript
var vpNode=S.nodes.find(function(n){
  return n.type==="prompt"&&n.meta&&n.meta.video&&S.conns.some(function(c){return c.from===n.id&&c.to===nd.id;});
});
var prompt = vpNode ? (vpNode.content||"") : (nd.content||"");
```

### Seedance 2.0 @引用命名约定（2026-06-20 更新：统一为 @图片1/@图片2/@音频1~@音频N）

**本会话（2026-06-20）统一为 Seedance 2.0 标准格式**：

| 引用类型 | 故事板模式 | 首尾帧模式 | 映射关系 |
|---------|-----------|-----------|---------|
| 参考图 | `@图片1` | `@图片1`（首帧）+ `@图片2`（尾帧） | image-gen 节点 |
| 角色音色 | `@音频1` ~ `@音频N` | `@音频1` ~ `@音频N` | char-tts 节点（按角色在分镜中出现的顺序） |

**重要区分**：
- **故事板模式**（`isSBVp=true`）：只有一张参考图（分镜图），只用 `@图片1`，**不生成 `@图片2`**
- **首尾帧模式**（`isSBVp=false`）：有两张图，`@图片1`=首帧 `@图片2`=尾帧

**历史**：旧版使用节点标题作 @引用名（如 `@第1集-1 尾帧图`），2026-06-20 改为 Seedance 2.0 标准 `@图片1`/`@图片2` 格式以符合上游 API 规范。

#### exGenVideoPrompt — 固定使用 @图片1/@图片2（System Prompt 硬编码，不依赖节点标题）

```javascript
sysPrompt="你是一个专业的视频提示词工程师。为Seedance 2.0（即梦）生成中文视频提示词，严格遵循首尾帧模式。\n\n"+
  "提示词结构（一段话，中文，不超过200字）：\n"+
  "@图片2 [风格/色调总纲]，[主体描述]，[动作序列]，[环境/光影]，[镜头语言]，[音效/对白描述]\n\n"+
  "注意：首尾帧模式下，@图片2 为尾帧参考图（最终画面），@图片1 为首帧参考图（起始画面）。\n\n"+
  audioRefStr+
  "重要规则：\n"+
  "- @图片2 引用尾帧参考图（最终画面）\n"+
  "- @图片1 引用首帧参考图（起始画面）\n"+
  ...
```

#### 角色音色引用格式（@音频1~@音频N）

**自动绑定条件**（2026-06-20 新增：角色对白检测 + 连接关系验证）：

```
收集 char-tts 节点的条件：
  1. ct.meta.audio 存在（已上传音频）
  2. char-tts 节点通过任一方式连接到 sboard：
     a. charNode（asset）→ sboard 直接连线
     b. charNode → episode → sboard（间接连线）
     c. char-tts → sboard 直接连线
     d. char-tts → 其他 sboard 节点（遍历兼容）
  3. sbText.indexOf(charName) >= 0（分镜内容中有该角色名称）
```

满足所有条件的角色按检测顺序被分配 `@音频1` ~ `@音频N`，顺序与 `exGenVideo` 中 `refAudios` 数组一致。

#### 视频 API 请求体音频传参（2026-06-20 新增）

`exGenVideo` 中收集 `refAudios` 数组，同时传入请求体 `audios` 字段和 `refLabels.audios`：

```javascript
refAudios.push({name: ct.title || (charName2+"-TTS"), dataUrl: ct.meta.audio, charName: charName2});
// ...
refLabels.audios = refAudios.map(function(a,i){
  return "@音频"+(i+1)+"="+a.name;
}).join(";");
```

`audios` 数组告诉 API 哪些音频文件可用，`refLabels.audios` 字符串映射 `@音频N` 到角色音色名称。两份文件中的顺序必须一致（都按 `allCharTts2.forEach` 的遍历顺序排列）。

```javascript
// Seedance 2.0 格式
audioRefStr="\n\n角色音色引用（分镜中有对白的角色）：\n"+
  charVoiceRefs.map(function(r,i){return "- "+r.charName+" -> @音频"+(i+1)+"（"+r.ttsName+"）";}).join("\n")+
  "\n\n音频引用规则（严格遵循 Seedance 2.0）：\n"+
  "- @音频"+(charVoiceRefs.length>1?"1~@音频"+charVoiceRefs.length:"1")+" 已上传对应角色音色\n"+
  "- 对白格式：角色名说\"台词内容\"（保留引号），并在音效部分标记对应 @音频N\n"+
  "- 示例：张三转身说\"我知道了\"。音效：脚步声，张三坚定的对白 @音频1\n"+
  "- 有对白时必须在音效描述末尾标注 @音频N\n";
```

#### exGenVideo — API 请求体传音视频引用映射（2026-06-20：image2 仅在有第二张参考图时设置；`@图片1` 默认名为"分镜图"非"首帧图"）

```javascript
var refImg1Name=refImgs.length>0?refImgs[0].name:"分镜图";
var refLabels={image1:refImg1Name,audios:refAudios.map(function(a,i){
  return "@音频"+(i+1)+"="+a.name;
}).join(";")};
if(refImgs.length>1)refLabels.image2=refImgs[1].name;  // 故事板模式只有1张图，不设置 image2
body={prompt:getStylePrefix()+prompt,imageDataUrl:...,images:refImgs,audios:refAudios,
  config:{model:s.model,seconds:seconds,size:size,
    refLabels:refLabels,...}};
```

> 参考图文件名用节点标题（如 `第1集-1 首帧图`），服务端 `normalizeInputImages` 接受 `{dataUrl, name}` 对象。
> 角色音色自动绑定流程（char-tts → exGenVideoPrompt → exGenVideo 的 `@音频N` 调用链）：`references/char-tts-video-autobinding.md`（2026-06-20 新增）
> Seedance 2.0 模式对应的 @图片1/@图片2 引用规则：`references/seedance-image-naming.md`（2026-06-20 更新：条件性 @图片2）

### 参考图抓取方式（2026-06-19 修复：沿链收集，不再只取 sboard 下的直连 image-gen）

参考图从 video-gen 节点逆向追溯，**沿路所有节点**只要是 `image-gen` 且有 `meta.img` 就收集，而不仅限于 `sboard` 下的直连子节点：

**实际节点链拓扑**：`sboard → sbPrompt(storyboard) → image-gen(分镜图) → vp(视频提示词) → video-gen`

旧代码只 `hop<2` 且只查 `sboard` 的直连 `image-gen` 子节点，在 4 层链路中永远找不到参考图。

```javascript
var refImgs = [];
var traceId = nd.id;
for (var hop = 0; hop < 5; hop++) {
  var up = S.nodes.find(function(n) {
    return S.conns.some(function(c) { return c.from === n.id && c.to === traceId; });
  });
  if (!up) break;
  // 沿路收集 image-gen 节点中的图片
  if (up.type === "image-gen" && up.meta && up.meta.img) {
    refImgs.push({ dataUrl: up.meta.img, name: up.title || "分镜图" });
  }
  // 也兼容旧结构：sboard 下的直连 image-gen
  if (up.type === "sboard") {
    S.nodes.forEach(function(n) {
      if (n.type === "image-gen" && S.conns.some(c => c.from === up.id && c.to === n.id) && n.meta && n.meta.img) {
        refImgs.push({ dataUrl: n.meta.img, name: n.title || "分镜图" });
      }
    });
    break;
  }
  traceId = up.id;
}
```

### 空参考图前置检查（模型相关）

视频模型分为两类：
- **图生视频**（如 `grok-imagine-video-1.5-preview`）：必须有参考图
- **文生视频**（如 `seedance-2.0-fast`、`seedance-2.0-pro`）：无需参考图，纯文本提示词即可

**Grok 模型固定要求至少一张参考图**（2026-06-18 起 Gaia API 强制）。`exGenVideo()` 中检测到 Grok 模型无参考图时**直接禁止生成视频**，toast 提示用户先生成参考图：

```javascript
var modelLc = (s.model || '').toLowerCase();
if (modelLc.indexOf('grok') >= 0 && refImgs.length === 0) {
  toast("Grok 视频需要至少一张参考图，请先生成参考图");
  return;
}
```

**其它模型**（Seedance 等）无此限制，`refImgs` 为空时按文生视频处理。

```javascript
if(vpNode2){
  var igNode=S.nodes.find(...);
  if(igNode&&igNode.meta&&igNode.meta.img)refImgs.push(igNode.meta.img);
}
// 发请求时 refImgs 为空 → API 按文生视频处理
//                  → 有图则作为 input_reference[] 传入
```

### 完整视频生成链路

```
scriptwriter/script(meta.style) ── 风格
    ↓
episode
    ↓
sboard(文字内容 + 时长)
    ↓ "✨ 生成提示词" → exGenPrompt → M版模板
prompt(storyboard) (meta.storyboard=true)
    ↓ "🎬 分镜图" → exGenSBImage → image-gen
image-gen (meta.img=分镜设计图)
    ↓ 生图成功后自动创建 (exGenSBImg 成功回调)
prompt(video) (meta.video=true, meta.duration=时长)
    ↓ "✨ 生成视频提示词" → exGenVideoPrompt → Seedance 2.0 格式
    ↓ "🎬 分镜视频" → mkvideo → content=""
video-gen (content="", 提示词从上游读取)
    ↓ "生成视频" → exGenVideo
GaiaVideoFactory API (prompt + @图片1 参考图)
```

> 视频模型速查表、错误排查流程、已知故障模式见 `references/gaia-video-models.md`（2026-06-14 更新：`doubao-*` 确认永久故障，`seedance-2.0-fast` 图生视频/文生视频已验证可用）
> 异步图片 API payload 格式、多图参考字段名、轮询流程见 `references/gaia-image-api.md`（2026-06-14 新增：`payload.images` 复数修复）
> Seedance 2.0 首尾帧模式参考图命名约定（用节点标题作 @引用名，不用 @图片N）见 `references/seedance-image-naming.md`（2026-06-15 更新：用户纠正为动态命名）
> 双模式画布按钮事件绑定矩阵、工具栏结构（顶栏 display:none 只有左侧栏可用）见 `references/dual-mode-button-matrix.md`
> 服务端 LLM 代理路由配置、401/405 排查、`ERR_HTTP_HEADERS_SENT` 崩溃修复见 `references/gaia-server-llm-proxy.md`

## Canvas 嵌入/提取工作流（server.js base64 模式）

当需要将 canvas.html 嵌入 server.js 以保护源码时，采用以下模式：

### 嵌入（encode → 写入 server.js）

```javascript
// 在 server.js 末尾
// === EMBEDDED CANVAS HTML (base64) ===
const _CANVAS_HTML_B64 = "base64string...";
```

生成命令：
```bash
node -e "const fs=require('fs');const c=fs.readFileSync('public/canvas.html','utf8');console.log('const _CANVAS_HTML_B64=\"'+Buffer.from(c).toString('base64')+'\";')"
```

### 提取（decode → 写回文件）

从 server.js 解码并恢复：
```bash
node -e "
const fs=require('fs');
const content = fs.readFileSync('server.js', 'utf-8');
const match = content.match(/const _CANVAS_HTML_B64 = \"([^\"]+)\"/);
const b64 = match[1];
const decoded = Buffer.from(b64, 'base64').toString('utf-8');
fs.writeFileSync('canvas/canvas.html', decoded, 'utf-8');
fs.writeFileSync('app/public/canvas.html', decoded, 'utf-8');
"
```

⚠️ 提取后需要同步 server.js 的路由：改为先读静态文件，失败时 fallback 到 base64：

```javascript
// /canvas 路由
const canvasPath = path.join(PUBLIC_DIR, "canvas.html");
if (fs.existsSync(canvasPath)) {
  res.end(fs.readFileSync(canvasPath, "utf-8"));
} else {
  res.end(Buffer.from(_CANVAS_HTML_B64, "base64").toString("utf8"));
}
```

这样开发时改静态文件即可，部署时仍可用嵌入版本。

## LLM API URL 配置与 `fixLLMUrl` 函数（2026-06-19 发现，同会话修复三次）

`fixLLMUrl()`（第 537 行）将画布设置面板中的 LLM API 地址补全为符合 OpenAI `/chat/completions` 格式的 URL。**该函数有 3 层保护，按优先级执行：**

```javascript
function fixLLMUrl(u){
  if(!u)return u;
  u=u.replace(/\/+$/,"");
  // 第一层：URL 中已有 /chat/completions（防止重复追加）
  var ci=u.indexOf("/chat/completions");
  if(ci!==-1)return u.substring(0,ci+"/chat/completions".length);
  // 第二层：本地路径（/开头、无 http 协议）强制指向服务端代理端点
  if(!u.match(/^https?:\/\//)&&u.startsWith("/"))return "/v1/chat/completions";
  // 第三层：外部 URL，正常补全路径
  if(u.endsWith("/v1"))u+="/chat/completions";
  else u+="/v1/chat/completions";
  return u;
}
```

### 三层保护逻辑

| 层 | 条件 | 行为 | 示例 |
|----|------|------|------|
| 1 | URL 中包含 `/chat/completions` | 截取到该位置（去重） | `/api/v1/chat/generates/v1/chat/completions` → `/api/v1/chat/generates/v1/chat/completions` |
| 2 | 本地路径（`/` 开头，无 http） | **强制替换为 `/v1/chat/completions`** | `/api/gaia-kairos/llm` → `/v1/chat/completions` |
| 3 | 外部 URL（http/https） | 补全 `/v1/chat/completions` | `https://api.xxx.com/v1` → `https://api.xxx.com/v1/chat/completions` |

**配置值 vs 实际请求 URL：**

| 设置面板填的值 | fixLLMUrl 后 | 服务端路由 |
|---------------|-------------|-----------|
| `/api/gaia-kairos/llm` | `/v1/chat/completions` | ✅ 本地代理（第 2 层强制） |
| `api/chat/generate` | `api/chat/generate/v1/chat/completions` | ❌（无首 `/`，第 2 层不触发） |
| **`/v1/chat/completions`** | `/v1/chat/completions` | **✅ 命中 server.js 第 976 行** |
| `https://token-plan-cn.xiaomimimo.com` | `https://token-plan-cn.xiaomimimo.com/v1/chat/completions` | ✅ 直接走上游 |

### ⚠️ 常见陷阱

1. **URL 必须加首 `/`**——`api/chat/generate` 是相对 URL（无首 `/`），第 2 层的 `startsWith("/")` 不触发，导致服务端 405。
2. **`fixLLMUrl` 只修复路径后缀，不验证路径正确性**——如果用户填了 `/some/wrong/path`，第 2 层会强制成 `/v1/chat/completions`。这是有意的兜底行为。
3. **触发三次修复的教训**——第一次只修 `endsWith` 检查（不全 → 重复），第二次加 `indexOf` 检测（能去重但基址错误），第三次加本地路径强制（最终解决全部错误基址）。

### 错误症状

控制台显示 `Failed to load .../v1/chat/completions:1 resource: the server responded with a status of 405 (Method Not Allowed)` 及 `SyntaxError: Unexpected token 'M', "Method not allowed" is not valid JSON`。这是服务端无法匹配路由时的默认响应（server.js 第 1072 行）。刷新页面让前端重新 fetch 即可，**无需重启服务端**。

## Gaia API 故障排查

### 图生图/文生图 `fetch failed` 排查

**症状**：设置中能拉到模型列表（`/api/models` 返回 200），但生成图片时报 `fetch failed`。

**根因分析**：
- 拉模型：`GET {baseUrl}/models` ✅
- 生图：`POST {baseUrl}/images/generations/async` ❌

两个端点不同，模型列表能拉不代表图片生成端点正常。`fetch failed` 是网络级错误（DNS/连接被拒绝/超时），不是 4xx/5xx 响应。

**排查步骤**：
1. 检查 config.json 中的 `baseUrl` 和 `apiKey`
2. 检查图片 model 名称是否在可用模型中
3. 用 curl 测试图片生成端点：`curl -X POST {baseUrl}/images/generations/async -H "Authorization: Bearer {key}" -d '{"model":"xxx","prompt":"test","size":"1280x720"}'`
4. 如果是 Gaia API，可能是该模型或端点临时故障，切换模型重试
5. 检查防火墙/代理是否阻止了特定路径的请求

### 配置文件位置

`D:\\AI视频号\\GaiaNetworkTester\\config.json`

```json
{
  "baseUrl": "https://api.gaiavideofactory.com/v1",
  "apiKey": "sk-...",
  "image": { "model": "gpt-image-2", "size": "1280x720", "mode": "async" }
}
```

图片模型名在日志中可以看到不同版本：`gpt-image-2`（默认）、`gpt-image2-2k-2`（常用替代）、`doubao-seedream-4-5-251128`（已知问题）。

### 生图fetch failed但拉模型正常 — 端点级排查

**2026-06-15 会话已验证的故障模式**。用户报「设置中能拉到模型列表，但生图卡住不生成」，日志显示 `Request failed {"path":"/api/image/generate","error":"TypeError: fetch failed"}`。

**根因**：拉模型和生图走不同的 HTTP 路径，前者能通不代表后者正常：

| 操作 | 端点（baseUrl=https://api.gaiavideofactory.com/v1） | 结果 |
|------|------------------------------------------------------|------|
| 拉模型列表 | `GET {baseUrl}/models` | ✅ 200 |
| 生图（async） | `POST {baseUrl}/images/generations/async` | ❌ `fetch failed` |

`fetch failed` 是网络级错误（DNS/连接拒绝/超时），不是 HTTP 4xx/5xx。排除步骤：

1. **提交测试** — 用 curl 直接测生图端点（见上方命令）
2. **等 10 秒再轮询** — 如果提交成功但状态卡在 `queued`/`running`，说明 Gaia API 侧处理队列积压
3. **切换图片模型** — 日志中 `gpt-image2-2k-2` 比 `gpt-image-2` 更稳定
4. **临时故障** — 提交下一分钟重试就能通（本会话 13:58 报错，14:17 重试成功）
5. **检查 config.json 的 `baseUrl`** — 确保末尾不带额外路径

**关键判断**：如果 `/models` 能拉但 `/images/generations/async` 不通，且重试后恢复，说明是 Gaia API 异步队列的临时故障，不是配置错误。

### 任务提交成功但永不完成（队列积压）

**2026-06-15 会话观察**：多个 async 图片任务提交成功（`{success:true, status:"queued", jobId:"imgjob_xxx"}`），但轮询 `/status` 一直返回 `{status:"running"}`，超时后仍未完成。当天所有提交（13:58、14:15、14:17）均无对应的 `Image saved` 日志记录。

| 提交时间 | jobId | 结果 |
|---------|-------|------|
| 13:58 | `imgjob_Mh3SY_H9StJr4Skq` | 提交后 9 秒 `fetch failed` |
| 13:58 | `imgjob_6ZcLHKYlS0p_So6d` | 提交成功，此后无记录 |
| 14:15 | `imgjob_ZcYhkZWMN9PKHpE-` | 提交成功，轮询超时 |
| 14:17 | `imgjob_QUYoQM-jLRoEV5vO` | 提交成功，轮询超时 |

**结论**：上传成功后任务卡在 Gaia API 队列中不处理。这是上游服务端的问题，非客户端配置错误。排查建议：

1. 查看 outputs/image/ 目录是否有新文件产生
2. 用 curl 直接轮询 job 状态确认后端是否响应
3. 如果长时间无响应，联系 Gaia API 支持或切换图片模型

### ⚠️ 空画布保护（2026-06-19 新增）

`saveCvs()` 开头检查：如果 `S.nodes` 为空且本地已有画布数据（`getLocalCvsData()` 返回非空），跳过保存，防止自动保存覆盖已有画布：

```javascript
function saveCvs() {
  if (!S.nodes.length) {
    var existing = getLocalCvsData();
    if (existing && existing.nodes && existing.nodes.length) {
      console.log('[saveCvs] skip: empty canvas would overwrite existing data');
      return;
    }
  }
  // ... 正常保存 ...
}
```

### 模型设置事件 + 缓存（2026-06-19 新增）

`saveSet()` 末尾触发 `gaia-kairos-config-saved` CustomEvent + 写 `kc-gaia-model-overrides`：

```javascript
localStorage.setItem("kc-cfg", JSON.stringify(S.cfg));
closeSet(); toast("设置已保存");
window.dispatchEvent(new CustomEvent('gaia-kairos-config-saved', {
  detail: { cfg: S.cfg, mode: canvasMode }
}));
localStorage.setItem('kc-gaia-model-overrides', JSON.stringify({
  llm: S.cfg.llm.model, img: S.cfg.img.model, vid: S.cfg.vid.model
}));
```

### 媒体控制区 flex-wrap（2026-06-19 新增）

`.ig-ctrl` 和 `.vg-ctrl` 加 `flex-wrap:wrap`，防止按钮多时溢出：

```css
.ig-ctrl { flex-wrap: wrap; }
.vg-ctrl { flex-wrap: wrap; }
```

### sceneEnvironmentOnlyDescription（2026-06-19 新增）

场景资产纯环境约束函数，过滤角色/人物关键词：

```javascript
function sceneEnvironmentOnlyDescription(desc) {
  var clean = (desc || "").replace(/角色|人物|主角|配角|人物剪影|身体|脸|手|动作|表情|对话|对白/gi, '');
  return "纯环境场景，不包含任何角色、人物、动物、剪影或生命体。仅描述场景本身。" + clean;
}
```

### collectRefNodes 追溯层数 — 模式相关（2026-06-19 改为 1 → 2026-06-20 改回 5）

**背景**：`exGenSBImg`（分镜图生成）和 `exGenVideo`（视频生成）都需要追溯上游关联的资产节点（asset），获取其生成的图片作为参考图。

**节点链**：`image-gen → prompt(storyboard) → sboard → episode → script`，资产节点连接在 sboard 或 episode 上。从 image-gen 到资产需要 **3+ 跳**。

#### 历史变更

| 日期 | 文件 | 跳数 | 原因 | 结果 |
|------|------|------|------|------|
| 原始 | `canvas/canvas.html` | 10 | 保证能找到所有关联资产 | ✅ 工作正常 |
| 2026-06-19 | `app/public/Kairos_canvas.html` | **1** | 改为只取直接连接的资产节点，认为向上追溯会误抓到不相关资产 | ❌ 找不到 sboard 连接的资产（3+跳） |
| 2026-06-20 | `app/public/Kairos_canvas.html` | **5** | 1跳找不到资产（asset连sboard，image-gen离sboard有2+跳） | ✅ 能追溯到所有关联资产 |

**⚠️ 关键教训**：1 跳只够找到直接连接 image-gen 的节点（prompt），但资产节点连接在 **sboard** 上。从 image-gen → prompt → sboard → asset 需要 3 跳。**至少需要 5 跳才能可靠覆盖**。

#### 当前代码（两份文件不同）

```javascript
// app/public/Kairos_canvas.html（双模式画布）：
var refNodes = collectRefNodes(nd.id, 5);

// canvas/canvas.html（故事板单模式画布）：
var refNodes = collectRefNodes(nd.id, 10);  // 原始值，从未改过
```

**`exGenVideo`** 中的参考图收集使用自行实现的 5 跳循环（非 `collectRefNodes`），与 `exGenSBImg` 的追溯深度保持一致。

#### 空资产行为

无关联资产时，两个函数的处理不同：

| 函数 | 无资产时行为 |
|------|-------------|
| `exGenSBImg`（app/public） | 仅提示，继续生成（纯提示词模式） |
| `exGenSBImg`（canvas/canvas.html） | ❌ **阻止生成**（`if(!refNodes.length){toast("⚠️ ...");return;}`） |
| `exGenVideo` | 仅在 Grok 模型时阻止（`refImgs.length===0` → `return`） |

### exExtract 运行锁（2026-06-19 新增）

`exExtract` 入口加 `extractRunning` 检查，防止双击触发两次请求：

```javascript
nd.meta = nd.meta || {};
if (nd.meta.extractRunning) { toast("提取中，请稍候"); return; }
nd.meta.extractRunning = true;
// ... 提取逻辑 ...
nd.meta.extractRunning = false;
```

### ⚠️ 函数声明提升陷阱 — `_escHtml is not defined`

**症状**：`loadCvs()` 中 `renderNode()` 调用某个函数时（如 `updateSboardPanel()`），该函数内部调用 `_escHtml()`，控制台报 `ReferenceError: _escHtml is not defined`。画布加载到一半中断，显示空白（但 localStorage 中数据完好）。用户误以为「保存按钮正常但刷新后内容丢失」，其实是加载时报错导致渲染中断。

**根因**：虽然 `_escHtml` 是函数声明（理论上提升），在 HTML `<script>` 标签内的严格模式下，定义在文件底部的函数可能在早期执行上下文中不可见。本会话此 bug 导致整个页面初始化链断裂（auto-save interval 都没设上）。

**修复方案**：任何需要 HTML 转义的函数，**不要依赖外部的 `_escHtml`**，在调用函数内部定义局部 escape：

```javascript
function myFunction(nd, el) {
  var _e = function(s) {
    return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  };
  // 用 _e() 代替 _escHtml()
}
```

**通用原则**：在大型单文件 HTML 脚本中，尽量将辅助函数定义在文件头部或函数内部，避免依赖底部定义的函数的「提升」行为。

### 资产复查按钮（nhdr 内联按钮模式，本会话新增/修复多个陷阱）

剧本（`script`）节点框名称栏（nhdr）中间的「复查资产」按钮，调用 LLM 核查遗漏资产，只补不删。

#### CSS（默认隐藏，类型选择器控制显隐）

```css
.nhdr .rv-btn{display:none;font-size:10px;padding:1px 6px;border:none;border-radius:4px;cursor:pointer;background:#f59e0b;color:#fff;line-height:1.6;white-space:nowrap}
.node[data-type='script'] .nhdr .rv-btn{display:inline-block}
```

**注意**：只对 `script` 类型显示。`scriptwriter`（剧本创作）已去除。

#### nhdr 渲染（badge 和 sz 之间条件插入）

```javascript
"<span class='badge'>"+TNAMES[nd.type]+"</span>"+
(nd.type==="script"?"<button class='rv-btn' data-action='reviewassets' title='资产复查'>复查资产</button>":"")+
"<span class='sz'>..."+
```

#### 拖拽冲突 — nhdr mousedown 必须排除 rv-btn

```javascript
if(e.target.closest(".nhdr")&&!e.target.classList.contains("ttl")&&!e.target.classList.contains("cls")&&!e.target.classList.contains("rv-btn")){
```

**通用原则**：nhdr 内所有可交互元素都必须在拖拽启动条件中显式排除。

#### 事件绑定 — 必须在 script 类型块中添加

```javascript
// script 分支中（不是仅 scriptwriter）：
var rb=el.querySelector('[data-action="reviewassets"]');
if(rb)rb.addEventListener("click",function(e){e.stopPropagation();exReviewAssets(nd,el);});
```

**⚠️ 历史 Bug**：最初只加在 `scriptwriter` 分支，`script`（导入剧本）节点的按钮点不动。

### 复查资产功能（exReviewAssets）

```javascript
async function exReviewAssets(nd,el){
  // 1. 读取剧本内容
  // 2. 查找直接连接到该节点的 asset 节点，按角色/场景/道具分类
  // 3. LLM 复查（提示词与 exExtract 一致）
  // 4. 新资产按类型独立编号 + 纯右扩展定位
  // 5. 新角色有对白 → 自动创建 char-tts 节点
}
```

#### ⚠️ 编号公式陷阱

`existingChars.length + cc + i + 1` 中 `cc`（已加数）和 `i`（循环索引）双重累加导致跳号（15→17→19）。

**正确公式**：`existingChars.length + cc + 1`（不加 `i`）。

**历史**：第 1 版用跨类型共享 `addCount` + grid 定位 → 第 2 版用 `typeStartX/typeStartY/ny` 三层辅助函数 → **最终版**：用户确认不存在无同类资产情况，直接用同类资产 Y，纯右扩展，每类型独立计数器（`cc/sc/pc`）。

#### 位置计算（最终版）

```javascript
var cx=nd.x+40,cy=nd.y,cc=0;
existingChars.forEach(function(e){if(e.node){var r=e.node.x+e.node.w+20;if(r>cx)cx=r;cy=e.node.y;}});
// 新角色：cx+cc*340, cy（与已有角色同一行）
// 场景、道具同理（各自独立计数器 sc/pc、独立起始位置 px/sx）
```

#### LLM 提示词规则

复查提示词必须与 `exExtract` 一致：
- 角色定妆图规范（单人、三视图、材质等）
- 场景纯环境约束
- 道具三个不提取规则（绑定角色/场景的不提取）
- **不提取重复资产**（已有清单的不会输出）
- **不提取一次性资产**（仅出现一次的场景/道具不提取）
- 名称与剧本语种同步
- 输出 JSON 模板（含 `name` + `prompt` 字段）

Style 回退链：`nd.meta.style` → `S.cfg.style`（全局），与 `exExtract` 一致。

#### 新角色自动创建 char-tts 节点

```javascript
var charName=c.name;
var scriptText=nd.content||"";
var hasDialogue=scriptText.indexOf(charName)>=0 && (
  scriptText.indexOf("「"+charName)>=0 || /* ... 多种对白格式检测 */);
if(hasDialogue){
  var tn=addNode("char-tts",...,{title:localLabel(text,charName+"-语音",charName+"-TTS"),...});
  tn.x=nn.x; tn.y=nn.y+nn.h+20;  // 角色节点正下方
}
```

### 语种感知命名（hasChinese + localLabel）

```javascript
function hasChinese(t){return/[\u4e00-\u9fff]/.test(t);}
function localLabel(text,zh,en){return hasChinese(text||"")?zh:en;}
```

所有资产节点和 TTS 节点标题外层包裹 `localLabel(text, ...)`：
- 中文剧本 → `角色1 张三`、`场景1 办公室`、`张三-语音`
- 英文剧本 → `Character 1 Zhang San`、`Scene 1 Office`、`Zhang San-TTS`

**注意**：LLM 的 `name` 字段也需通过提示词约束与剧本语种一致（`"每个name必须使用剧本原文中的名称"`）。

### 节点框名称条渐变色（全类型覆盖）

所有 17 种节点类型的 `.nhdr` 改为从线框颜色到白色的渐变：

```css
.node[data-type="xxx"] .nhdr{background:linear-gradient(to right, var(--node-xxx), #fff)}
```

**全覆盖检查清单**：storyboard, character, scene, scriptwriter, episode, asset, sboard, char-tts, text, prompt, llm, image, video, tts, script, image-gen, video-gen。缺一不可。

### 所有提示词改为中文（本会话批量转换）

| 函数 | 原语言 | 现语言 |
|------|--------|--------|
| `charVariantPrompt` | 英文 | 中文 |
| `sceneVariantPrompt` | 英文 | 中文 |
| `propVariantPrompt` | 英文 | 中文 |
| `exGenStartPrompt` | 英文 | 中文 |
| `exGenEndPrompt` | 英文 | 中文 |
| `exGenPrompt`（M版模板） | 英文 | 中文 |
| `exGenSBImg` 参考图文字 | 英文 | 中文 |
| `LINKED REFERENCE ASSETS` | 英文 | 关联参考资产 |
| `Description: ` 前缀 | 英文 | 描述：|

### ⚠️ 覆盖写回文件（本会话 3 次忘记保存）

Python 脚本中替换文件内容但在脚本末尾忘记 `with open(path, 'w')`：
1. 替换了 `charVariantPrompt/sceneVariantPrompt/propVariantPrompt` 的 base 字符串但没写回
2. 用 `html.replace()` 做了替换但变量没保存

**修复**：写 Python 脚本时先写完整流程（read → replace → write）再执行，不要在脚本中间加 print 调试中断流程。

### ⚠️ exGenSBImg collectRefNodes 追溯层数

**2026-06-19→20 来回改**：
- 原始 1 跳 → ❌ 找不到资产（asset连sboard，image-gen离sboard需3跳）
- 改为 5 跳 → ✅ 能追溯到所有关联资产

节点链：`image-gen → prompt(storyboard) → sboard → episode → script`，asset 连在 sboard 上需 3+ 跳。**至少 5 跳**。

### ⚠️ 复杂修改前先停下来规划

用户明确说了：同一布局/逻辑问题尝试 3 次以上不成功时，**停下来**，分析所有已改代码，识别根因，一次性修好。

**本会话标志性案例**：exReviewAssets 的位置计算从统一 grid → typeStartX/typeStartY/ny → 纯右扩展，经历了 3 轮迭代。第 3 轮是一次性重写的最终版本。

### 复制现有节点优先
用户首选方式：先复制一个已有且显示正确的节点，再在上面改。不要从零搭建。

### ⚠️ 手术刀式修改（被用户多次严厉批评）
- **只改目标元素** — 用 `patch()` 精准替换，绝不用 `write_file` 重写整个文件
- **不动其他** — 不要顺手改不相关的样式、布局、或代码。用户说「不要动其他东西」
- **改前先看已有实现** — 同一布局问题尝试 3+ 次不成功时，停下来看其他节点怎么实现的（如 `ep-footer`、`script-btns`），不要继续微调
- **验证再交** — 复杂修改后必须确认无误再告知用户

### ⚠️ 任务完成前检查清单
每次 patch 后必须 read_file 检查周围 10 行：
1. 残留旧代码（orphan lines）
2. 缺失闭合括号
3. **重复按钮**（foot 变量 + 渲染 body 不能同时有）
4. **foot 排除** — 新类型必须加进排除列表
5. search_files 确认无残留旧引用
6. wc -l 确认行数变化合理

### ⚠️ 从零重建流程
用户说「重新从0开始做」时：
1. search_files 搜所有引用 → 2. 逐一删除 → 3. 验证无残留 → 4. 从干净状态重建

### ⚠️ 用户偏好
- 像素级精确：按钮/对齐必须和现有节点一致
- 命名准确：显示功能名不显示选中值
- 先看别人怎么做的再动手

### ⚠️ select 下拉框：初始化 value 必须在事件绑定前设置

**本会话修复的 bug**：scriptwriter 的 `.sw-sty` 和 script 的 `.style-sel` 两个下拉框都只在 `change` 事件中保存到 `nd.meta.style`，但 **没有在渲染时从 `nd.meta.style` 恢复初始值**。刷新页面后，虽然 `nd.meta.style` 存在于缓存中，但下拉框始终显示第一个 option。

```javascript
// ❌ 错误：只注册 change 事件
var sy=el.querySelector(".sw-sty");
if(sy)sy.addEventListener("change",function(){nd.meta=nd.meta||{};nd.meta.style=this.value;});

// ✅ 正确：设置初始值 + 注册 change
var sy=el.querySelector(".sw-sty");
if(sy){
  var sv=nd.meta&&nd.meta.style;
  if(sv)sy.value=sv;  // ← 关键！从 meta 恢复
  sy.addEventListener("change",function(){nd.meta=nd.meta||{};nd.meta.style=this.value;});
}
```

**script 节点（导入剧本）的 style-sel 优先级链**：`nd.meta.style` > `localStorage("kc-style")` > `S.cfg.style`。保存时同时写入 `nd.meta.style`（per-node）和 `S.cfg.style`（全局向后兼容）。

**适用所有带 `<select>` 的节点**：episode 的 `ep-sec`/`ep-cnt`、scriptwriter 的 `sw-ep`/`sw-dur`/`sw-anc`/`sw-sty`、script 的 `style-sel` 等。任何需要持久化的 input/select，都要在事件绑定前从 `nd.meta.*` 初始化其 `.value`。

### ⚠️ 复杂 JS 嵌套 → 拆成循环

超过 3 层的 find/some 嵌套回调必须拆成循环或分步赋值。本会话中一个 4 层嵌套的表达式少了一个 `)`，导致整页 JS 语法错误，画布空白，用户数据丢失。

**每次修改后必须做括号自检**——运行脚本统计 `(` vs `)` 和 `{` vs `}` 数量，两边必须相等：

```javascript
// 用 execute_code 快速验证
opens = content.count('(');
closes = content.count(')');
// opens === closes 必须成立
```

### 双文件同步原则

修改 canvas 时 **必须同步修改两个文件**：
- `app/public/canvas.html`（主路由，Node.js server serve）
- `canvas/canvas.html`（/canvas 路由的副本）

漏改一个会导致同一功能两个路由不一致。修改后需搜索确认两处都已同步。

### ⚠️ JS 字符串拼接：删除最后一段时必须去掉前面行的尾随 `+`

当从 JS 字符串拼接链中删除最后一段时，前面行的 `\\n"+` 尾随 `+` 会导致语法错误：

```javascript
// ❌ 错误：删了下一行但没去掉 `+`
"...Texture overlay\\n"+
// ← 下一行被删了，但 + 还在！
var r=await fetch(...)  // SyntaxError!

// ✅ 正确：把 `+` 改成 `;`
"...Texture overlay\\n";
var r=await fetch(...)
```

**本会话教训**：删除 exGenPrompt sysPrompt 的 "No text generation" 最后一行 Requirements 时，上一行的 `\\n"+` 没改成 `\\n"`，导致页面 JS 语法错误。

**检查方法**：每次行级删除后，read_file 确认被删行前后的语法连续性，或运行 `node --check` 验证。对于函数/变量内嵌的拼接，直接在 `node --check` 或 `node -e` 中执行完整文件。

### ⚠️ JS 语法验证流程（2026-06-15 新增）

每次对 canvas.html 中的 `<script>` 内容做 patch 后，必须验证 JS 语法：

```bash
# 提取 script 内容到临时文件并验证
node -e "
const fs=require('fs');
const c=fs.readFileSync('app/public/canvas.html','utf-8');
const start=c.indexOf('<script>')+8;
const end=c.lastIndexOf('</script>');
const script=c.substring(start,end).replace(/\r/g,'');
fs.writeFileSync('/tmp/_check_canvas.js',script,'utf-8');
"
node --check /tmp/_check_canvas.js
```

⚠️ **Windows 路径问题**：`/tmp` 不存在时改用用户目录：
```bash
fs.writeFileSync('C:/Users/you/_check_canvas.js',script,'utf-8');
node --check "C:\Users\you\_check_canvas.js"
```

**找不到语法错误位置时用二分搜索**：
```javascript
// 在 node 中执行
const lines = script.split('\n');
let lo = 0, hi = lines.length;
while (lo < hi) {
  const mid = Math.floor((lo + hi) / 2);
  try {
    new Function(lines.slice(0, mid).join('\n'));
    lo = mid + 1;
  } catch(e) { hi = mid; }
}
// 错误在 lines[lo] 附近
console.log('Error near line:', lo + 1);
console.log(lines[lo - 1]);
console.log(lines[lo]);
```

**快速括号平衡检查**（避免多余 `}` 或缺失 `)`）：
```bash
node -e "
const fs=require('fs');
const c=fs.readFileSync('app/public/canvas.html','utf-8');
const s=c.substring(c.indexOf('<script>')+8,c.lastIndexOf('</script>'));
console.log('(:', s.split('(').length-1, ') :', s.split(')').length-1);
console.log('{:  ', s.split('{').length-1, '}  :', s.split('}').length-1);
"
```

**本会话教训**：多了一个多余的 `}` 导致整个 script 语法错误，`loadCvs()` 不执行，画布显示空白。用户报「画布内容丢失」，实际数据在 localStorage 完好，只是 JS 引擎拒绝执行。两步排查：(1) `node --check` 发现语法错误 (2) 二分定位到第 1001 行多余的 `}`。

### buildAssetPrompt 风格追溯（2026-06-15 修复）

`buildAssetPrompt(nd, desc)` 在 `exGenAsset()`（资产节点「生成图片」按钮）中调用。原代码使用 `getStylePrefix()`（全局 `S.cfg.style`），无法追溯到上游 script/scriptwriter 节点的风格。

**修复**：从 asset 节点向上追溯 2 跳查找 script/scriptwriter 节点：

```javascript
function buildAssetPrompt(nd, desc){
  var at=getAssetType(nd);
  // Trace style from upstream script/scriptwriter node
  var tracedStyle="";
  var upNode=S.nodes.find(function(n){
    return S.conns.some(function(c){return c.from===n.id&&c.to===nd.id;});
  });
  if(upNode&&upNode.meta&&upNode.meta.style)tracedStyle=upNode.meta.style;
  if(!tracedStyle&&upNode){
    var up2=S.nodes.find(function(n){
      return S.conns.some(function(c){return c.from===n.id&&c.to===upNode.id;});
    });
    if(up2&&up2.meta&&up2.meta.style)tracedStyle=up2.meta.style;
  }
  var sp=tracedStyle?(STYLE_MAP[tracedStyle]||tracedStyle)+", ":"";
  if(!sp){var gs=S.cfg.style;if(gs)sp=(STYLE_MAP[gs]||gs)+", ";}
  if(at==="character") return charVariantPrompt(desc, sp);
  if(at==="scene") return sceneVariantPrompt(desc, sp);
  if(at==="prop") return propVariantPrompt(desc, sp);
  return sp+desc;
}
```

**追溯路径**：asset → 上游节点（可能是 script 或 sboard） → 再上游 → script/scriptwriter。如果 2 跳都没找到，fallback 到全局 `S.cfg.style`。

⚠️ **必须与 `exExtract` 中的 6 处风格调用保持统一**：`exExtract` 直接读 `nd.meta.style`（nd=script 节点），`buildAssetPrompt` 靠连接搜索往上追溯。两种方式结果应一致，但覆盖了不同触达路径（提取 vs 重新生图）。

### ⚠️ JS 全角字符陷阱（2026-06-15 发现）\n\n**症状**：`node --check` 报 `SyntaxError: Invalid or unexpected token`，但肉眼检查代码似乎正确。\n\n**根因**：中文字符集中的全角 `：`（U+FF1A）被意外用在 JS 三元运算符位置。全角冒号在 JS 中不是合法 token，三元运算符必须用半角 `:`（U+003A）。\n\n```javascript\n// ❌ 错误：全角冒号（U+FF1A）在 ?: 运算符中\nvar userMsg = isSBVp ? "A" \uff1a (cond ? "B" : "C");\n\n// ✅ 正确：半角冒号（U+003A）\nvar userMsg = isSBVp ? "A" : (cond ? "B" : "C");\n```\n\n**高发场景**：在 Python 脚本中批量替换 JS 代码时，从 markdown/模板文件复制过来的字符串可能混入全角符号。用 Python 检测：\n\n```python\nif '\\uff1a' in code:\n    print(\"WARN: full-width colon found!\")\n```\n\n**预防**：Python 脚本中处理 JS 字符串时，避免从外部 markdown 文件直接粘贴代码到 f-string 或模板中。优先在脚本内组装代码字符串，或使用 `replace('\\uff1a', ':')` 清洗。\n\n### patch 工具逃逸陷阱（2026-06-15 发现）

当用 `patch` 工具修改包含转义字符的 JS 字符串时，`\n` 可能被转义为字面量 `\n`（两个字符：反斜杠+字母n），`\"` 可能被转义为 `\"`。症状是 patch 报告 success，但文件出现字面量 `\n` 和 `\"`，JS 引擎无法解析。

**修复方法**：用 Python 脚本做字节级替换：
```python
# 通过 terminal 执行 python3
with open('file.html', 'rb') as f:
    data = f.read()
# 将字面量反斜杠-n 替换为真正换行符
data = data.replace(b'exGenSBImage(nd,el);});\\\\n    var', b'exGenSBImage(nd,el);});\\n    var')
with open('file.html', 'wb') as f:
    f.write(data)
```
先用 `repr()` 或 hex dump 确认实际字节序列再写替换代码。
