---
name: "storyboard-previs"
description: "|"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\storyboard-previs\\SKILL.md"
---
# 白模视频（Storyboard Previs）

## 1. 工作流定位

```
剧本 → v6 分镜剧本（storyboard-gen） → [白模视频] → AI 视频模型精模生成
                                          ↑
                                    你在这里做的
```

**为什么插这一步**：白模 0 GPU / 0 钱 / 5 分钟改完；AI 视频贵 + 慢 + 改不动。客户 review 也比 markdown 直观 10 倍。

## 2. 4 步工艺

### 步骤 1：解析 v6 分镜
提取 SEG 头 + 每个镜头的 8 要素（【镜头级资产】【摄影机 4 字段】【画面】【基线】【表演】【对白】【声音】【意图】）+ SEG 级调度 4 字段（演员走位/灯光调度/道具调度/转场规划）。

**关键字段映射**：
- 【摄影机】4 项 → camera: `{focal(数字mm), height, distance(米), motion}`
- 【SEG 级调度·灯光调度】→ seg.lighting（"开 SEG 暖黄3000K → 收 SEG 暮色蓝紫8000K"）
- 【SEG 级调度·道具调度】→ seg.props（["推车", "书摊", "小王子"]）
- 【SEG 级调度·转场规划】→ seg.transition（黑场/声音桥/匹配剪辑/视觉引导/直切）

### 步骤 2：几何体建模（白模风格）
- **角色**：用 Group 拼**8 件套人形**（capsule 太简陋，团队 review 看不出角色关系）
  - 头：`SphereGeometry(0.13)`（小孩 0.11）+ 脖子短圆柱
  - 躯干：`BoxGeometry`（女性胯宽肩窄，男性肩宽胯窄）
  - 髋部：`BoxGeometry`（女性更宽）
  - 双腿：2×`CylinderGeometry`（左右腿）
  - 双臂：2×`CylinderGeometry`（左右臂，略外展）
  - 头顶名字标签：`Sprite + Canvas` 绘制（黑底白字圆角矩形）
- **性别自动判断**：name 含"女/母/姐/妹/她/娘/姑/婆" → 女性体型
- **年龄自动判断**：startY < 0.5 → 小孩，整体 scale 0.7
- **地面**：`PlaneGeometry(40, 40)` + `MeshStandardMaterial({color: 0x3a3a3a})`
- **网格**：`GridHelper(40, 40, 0x5a5a5a, 0x2a2a2a)` 帮观察镜头运动
- **道具**：字典映射（推车→box, 书摊→box, 杯子→cylinder, 路灯→cylinder 等），见 `templates/prop-library.json`
- **材质**：统一灰色 `MeshStandardMaterial`，cast/receive shadow

### 步骤 3：关键帧动画
- **摄像机运动**：根据 v6 摄影机·运动 字段分 5 种
  - 固定机位：position 不变
  - 横移：camera.position.x 沿 X 方向 lerp（offset = (t-0.5) * 4）
  - 推：distance 从 2×distance → distance lerp
  - 拉：distance 从 0.5×distance → distance lerp
  - 跟：camera 跟随 actor.startPos → actor.endPos lerp
- **演员走位**：`actor.position.lerpVectors(startPos, endPos, localT)`
- **灯光**：从 SEG 级调度·灯光调度 文本提取色温，**每 SEG 切换时重建灯光**（不是平滑插值，简化处理）
- **转场**：黑场用 fullscreen overlay div，控制 opacity 淡入淡出；其他（声音桥/匹配剪辑/视觉引导）不做视觉处理

### 步骤 4：渲染输出
- **浏览器内播放**：Three.js WebGLRenderer 实时渲染到 canvas
- **播放完成停止**：必须显式判断 `isFinal = isLastSeg && isLastShot` → 播完自动停止，按钮变回 `▶ 播放`，状态栏显示 `✓ 播放完成 X.Xs / Y.Ys`。**不要默认循环**（团队 review 时循环播放容易让人忘记看到哪；2026-08-14 用户明确反馈"不要循环"）
- **录屏导出**：MediaRecorder + `canvas.captureStream(30)`，按 SEG 切分保存为独立 webm
- **生产级批量**：Blender Python headless（见 `references/blender-headless.md`）

## 3. 关键算法

### 3.1 35mm 焦距 → Three.js fov

```javascript
// 35mm 胶片高度 = 24mm（简化 sensor height = 24）
function focalToFov(focalMm) {
  return THREE.MathUtils.radToDeg(2 * Math.atan(12 / focalMm));
}
// 24mm 广角 → 73°, 35mm → 54°, 50mm → 40°, 85mm → 24°, 100mm → 20°, 135mm → 15°
```

### 3.2 色温 → RGB（黑体辐射简化算法）

```javascript
// 适用范围 1000K-40000K
function kelvinToRGB(k) {
  k = k / 100;
  let r, g, b;
  if (k <= 66) {
    r = 255;
    g = 99.4708025861 * Math.log(k) - 161.1195681661;
    if (k <= 19) b = 0;
    else b = 138.5177312231 * Math.log(k - 10) - 305.0447927307;
  } else {
    r = 329.698727446 * Math.pow(k - 60, -0.1332047592);
    g = 288.1221695283 * Math.pow(k - 60, -0.0755148492);
    b = 255;
  }
  return [Math.max(0, Math.min(255, Math.round(r))),
          Math.max(0, Math.min(255, Math.round(g))),
          Math.max(0, Math.min(255, Math.round(b)))];
}
```

### 3.3 机位高度文字 → 数字（米）

```javascript
function heightToY(heightStr) {
  if (typeof heightStr === 'number') return heightStr;
  const s = String(heightStr);
  if (s.includes('俯视') || s.includes('高位')) return 3.0;
  if (s.includes('仰') || s.includes('低机位') || s.includes('低')) return 0.3;
  if (s.includes('胸口') || s.includes('平视')) return 1.6;
  return 1.6;  // 默认胸口高度
}
```

### 3.4 道具字典（精简版）
详见 `templates/prop-library.json`。常见物件：
- 推车/车 → `box[1.2, 0.9, 1.5]` 灰
- 书摊/桌子 → `box[2, 0.9, 0.8]` 棕
- 书/小王子 → `box[0.2, 0.3, 0.05]` 白/奶白
- 杯子/茶 → `cylinder[r, h, r]` 白/棕
- 椅子 → `box[0.5, 1, 0.5]` 棕
- 路灯 → `cylinder[0.08, 4, 0.08]` 灰
- 墙/巷口 → `box[0.3, 3, 4]` 暗棕

### 3.5 v6 markdown → JSON（LLM 解析）
完整 system prompt 见 `templates/llm-system-prompt.md`。

**输入兼容性**：单 SEG / 单镜头 / 多 SEG / 非标准笔记都要支持。LLM 自行识别并解析成 `segs` 数组（即使只有 1 个 SEG）。

**默认值**：缺失字段用 focal=50, height="胸口高度", distance=3, motion="固定机位"。

**调用 LLM API — 必须按 base_url 自动检测风格**：详见 `llm-browser-gateway` skill（已独立成完整 skill，含 5+ 个 templates/references）。核心代码：

```javascript
const isAnthropic = /\/anthropic/i.test(baseUrl);
let url, headers, body;
if (isAnthropic) {
  // MiniMax、Azure Foundry、Anthropic 官方
  url = `${baseUrl}/v1/messages`;
  headers = { 'Content-Type': 'application/json', 'x-api-key': apiKey, 'anthropic-version': '2023-06-01' };
  body = JSON.stringify({
    model, max_tokens: 4096,
    messages: [{ role: 'user', content: SYSTEM_PROMPT + '\n\n' + userText }]  // 没有 system role
  });
} else {
  // Agnes、OpenAI、OpenRouter 等
  url = `${baseUrl}/chat/completions`;
  headers = { 'Content-Type': 'application/json', 'Authorization': `Bearer ${apiKey}` };
  body = JSON.stringify({
    model,
    messages: [
      { role: 'system', content: SYSTEM_PROMPT },
      { role: 'user', content: userText }
    ],
    temperature: 0.1
  });
}

const resp = await fetch(url, { method: 'POST', headers, body });
if (!resp.ok) {
  const body = await resp.text();
  throw new Error(`HTTP ${resp.status}\nURL: ${url}\n风格: ${isAnthropic ? 'Anthropic' : 'OpenAI'}\n响应: ${body.slice(0, 400)}`);
}
const data = await resp.json();
const content = isAnthropic
  ? (data.content?.map(c => c.text || '').join('') || '')
  : (data.choices?.[0]?.message?.content || '');
```

⚠️ **常见错（2026-08-14 实际踩坑）**：把 MiniMax 当 OpenAI-compatible，调用 `/v1/chat/completions` → **404 page not found**。MiniMax 实际是 Anthropic Messages 兼容，路径 `/anthropic/v1/messages` + `x-api-key` header（也支持 `Authorization: Bearer`）。

**CORS 解决**：浏览器调公共 LLM SaaS（如 MiniMax / OpenAI / Anthropic 官方）会被 CORS 拦截 → `Failed to fetch`。
1. **curl 通 ≠ 浏览器通**（curl 不做 preflight OPTIONS）
2. 起本地 FastAPI proxy（10 行代码）转发到上游 endpoint，浏览器 base_url 改成 `http://localhost:8765/anthropic`
3. 完整 proxy 模板见 `llm-browser-gateway/templates/fastapi_cors_proxy.py`

**提取 JSON 防 LLM 输出 markdown 代码块**：
```javascript
const jsonMatch = content.match(/\{[\s\S]*\}/);
if (!jsonMatch) throw new Error('LLM 未返回 JSON 格式');
return JSON.parse(jsonMatch[0]);
```

### 3.6 MediaRecorder 录屏按 SEG 切分

```javascript
const stream = renderer.domElement.captureStream(30);
const mimeType = ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm']
  .find(t => MediaRecorder.isTypeSupported(t)) || 'video/webm';
const recorder = new MediaRecorder(stream, { mimeType });
const chunks = [];
recorder.ondataavailable = e => { if (e.data.size > 0) chunks.push(e.data); };
recorder.onstop = () => {
  if (chunks.length === 0) return;
  const blob = new Blob(chunks, { type: 'video/webm' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `previs-seg${String(idx + 1).padStart(2, '0')}.webm`;
  a.click();
  URL.revokeObjectURL(url);
  chunks = [];
};
```

## 4. 工具方案对比

| 方案 | 安装 | 3D 能力 | 批渲染 | 适合阶段 |
|------|------|--------|--------|---------|
| **Three.js + HTML 单文件** | 零（CDN importmap） | 基础 | 浏览器录屏 | 早期验证 / 客户 demo / 快速验证分镜 |
| **Blender Python headless** | 一次装 | 专业 | 批量 mp4 | 生产级 / 大量 SEG / 集成到 AIGC pipeline |
| **Unreal / Unity** | 巨大 | 电影级 | 批量 mp4 | 重武器，不推荐起步 |

**推荐路径**：起步 Three.js 验证工艺 → 跑通后迁 Blender headless 批生产。

## 5. 与 AIGC_agent 集成

白模视频是 AIGC_agent 的 storyboard stage 的**可选前序阶段**：
- 项目路径：`D:\AI_work\AIGC_agent\kairos_aigc\aigc\stages\storyboard.py`
- LLM endpoint：复用 settings.json 的 `base_url` (`https://apihub.agnes-ai.com/v1`) + `llm_model` (`agnes-2.5-flash`)。**注意**：用户当前 default model 已切到 MiniMax-M3（hermes config.yaml 的 `default: MiniMax-M3`），不是 Agnes。集成时确认实际 default model。
- 白模验证可作为新增 stage `previs_validate`，在原 storyboard 完成后、video_gen 前插入
- 输入：storyboard stage 输出的 JSON
- 输出：mp4 文件路径或浏览器预览 URL
- 价值：自动验证分镜 → 不合理的分镜不进入 video_gen 烧钱

## 6. 关键踩坑 / Pitfall

1. **MiniMax 是 Anthropic Messages 兼容路径**——base_url 含 `/anthropic` 走 `/v1/messages` + `x-api-key`；否则走 `/chat/completions` + `Authorization: Bearer`。混搭 `https://api.minimaxi.com/anthropic/v1/chat/completions` 是 404。完整诊断见 `gen-api-integration/references/minimax-anthropic-api.md`
2. **Three.js over file:// — use UMD not importmap** — `<script type="importmap">` + `<script type="module">` is unreliable over `file://` protocol. Headless browsers (and some Chrome configs) block ES module loading as CORS even when the file is local. **Fix**: download `three.min.js` once to a vendor/ directory and load via plain `<script src="./vendor/three.min.js">`. UMD exposes `window.THREE` globally. ~655KB, works everywhere. If you need ES module style, run a local HTTP server (`python -m http.server`) and use http:// instead of file://.
3. **MediaRecorder 必须用户交互触发**——不能在 onload 里自动开始录制，需要用户点按钮（浏览器安全策略）
4. **浏览器 fetch LLM API 受 CORS 限制**——公共 SaaS 不一定支持浏览器跨域；`Failed to fetch` 错误是 CORS 拦截不是网络问题。解法：起本地 FastAPI proxy（10 行代码）转发，或确认目标 endpoint 支持 CORS header。详细诊断模式（base_url 漏 /v1 自动补、max_tokens=5 测试连接、错误响应 body 头 400 字符）见 `references/browser-fetch-llm-pitfalls.md`
5. **色温换算在 < 1000K 或 > 40000K 时不准**——超出范围用描述性 fallback（暖黄→3000K 暖色、蓝紫→8000K 冷色、暗夜→6500K 中性）
6. **Three.js fov 是垂直 fov**——v6 焦距是水平 35mm 胶片焦距；sensor 高度 24mm 简化是合理近似
7. **背景 + 雾色要跟灯光色温走**——否则色温变化只影响灯光强度而不影响整体氛围（关键：scene.background = color.clone().multiplyScalar(0.25)）
8. **每 SEG 切换时必须重建场景**——演员/道具/灯光都在 buildStage 里 add；老 mesh 要 clearStage 删除避免叠加
9. **v6 SEG 总时长 vs 镜头时长之和**——允许 ±0.5 秒误差（声音桥重叠），不强制精确
11. **MediaRecorder 输出是 webm 不是 mp4**——Chrome/Firefox 写 webm-vp9/vp8，需要真 mp4 用 ffmpeg 重混：`ffmpeg -i input.webm -c:v libx264 output.mp4`。Safari 不支持 webm
12. **角色位置默认 y 值**——站立人 y=0.8，小孩 y=0.4；其他物件按 size[1]/2 落地
13. **UX 三大坑**（2026-08-14 用户反馈触发）：
    - **不要默认循环播放**——必须显式检测 `(isLastSeg && isLastShot)` 触发停止，按钮变回 `▶ 播放`，status 显示 `✓ 播放完成 X.Xs / Y.Ys`。team review 时循环播放让人忘记看到哪。
    - **不要 static "等待加载" status**——让用户以为页面卡了。要 step-by-step transition: `加载引擎...` → `就绪` → `初始化场景...` → `加载示例剧本...` → `▶ 播放中`。任何未捕获异常更新 status 为红底错误。
    - **LLM-driven 场景不要加 file picker 按钮**——多此一举。页面打开自动加载示例 + 600ms 后自动播放，用户立即看到效果不需要点任何按钮。
14. **Always curl-test the LLM endpoint before writing fetch code** — 30 秒的 curl 能省 30 分钟 fetch 调试。`curl -X POST <guessed-path> -d '{...}' -H 'x-api-key: test'` 返回 401 = endpoint 存在；返回 404 HTML = 路径错。完整诊断模式见 `references/browser-fetch-llm-pitfalls.md` Pitfall 6。
15. **Brace-aware JSON extractor** — 不要用 `/\{[\s\S]*\}/` regex 抓 LLM JSON，会抓穿字符串里的 `{...}`、多个对象、前后散文。详见 `references/browser-fetch-llm-pitfalls.md` Pitfall 5 的 brace counter 实现。

## 7. 立即可跑 demo

完整可工作的 Three.js 单文件 demo 已落到：
- `D:\AI_work\previs\storyboard-previs.html`

包含：
- LLM API 配置 modal（base_url / api_key / model 存 localStorage）
- LLM 解析 modal（粘贴 v6 markdown 自动解析，含 API 风格自动检测）
- 文件加载 / 示例剧本 / 播放 / SEG 切换 / 镜头列表
- 完整灯光 / 道具 / 转场 / 录屏实现
- 默认 base_url = `https://api.minimaxi.com/anthropic`，model = `MiniMax-M3`（hermes config.yaml 当前 default model）；切换到 Agnes/Apihub 也兼容（自动检测）
- 测试连接按钮（不消耗 token 的连通性验证 + 显示真实 URL）

打开方式：双击 HTML 文件或浏览器拖入即可。**注意**：浏览器直连公共 LLM SaaS 受 CORS 限制（`Failed to fetch`），如报错走 FastAPI 10 行 proxy。

## 8. 扩展方向

1. **Blender headless 批生产**——Python 脚本读 v6 JSON + bpy API 渲染 mp4（脚本模板见 `templates/blender_render.py`）
2. **集成 AIGC_agent pipeline**——新增 `previs_validate` stage，自动化验证分镜
3. **多角色 actor system**——现在只能简单 lerp；支持 walk cycle / 关节动画
4. **声音设计可视化**——v6 声音字段现在未处理；可加声场定位（speaker 位置图标 + 时间轴波形）
5. **白模 + 实拍 hybrid**——前 80% 用白模预演，最后 20% 接实拍/AI 视频拼接
6. **导出 FBX/glTF 给下游 AI 视频模型**——用白模场景几何体作为 i2v 的 reference image 或 keyframe

## 9. 三档渲染实现（3D / 2D / Ken Burns）

The skill umbrella subsumes three tiers of browser-based storyboard preview, all driven by the same shot-script format:

| Tier | Library | Look | When |
|------|---------|------|------|
| **3D** | Three.js | Low-poly 3D scene with real camera FOV | Default. Concept validation, camera-move testing |
| **2D** | Pixi.js 7.4+ (~300 KB) | Parallax sprite layers | Mobile-friendly, quick pacing check |
| **Ken Burns** | Pure CSS + SVG (~50 KB) | Pan/zoom on procedural gradient backgrounds | Voice-over/documentary style, fastest load |

All three are **single HTML files, no build step, CDN-only deps, MediaRecorder → WebM export**. The Three.js path is the default and covered above; the 2D / Ken Burns tiers share the same `Script | Action | Camera | Seconds` (or free-form) format and the same 6-move camera vocabulary (`wide / medium / closeup / over / dutch / reveal`). See:
- `references/3tier-canvas-init-timing.md` — the 0×0 canvas bug diagnosis + reproducer (applies to Pixi *and* Three)
- `references/3tier-free-form-parser.md` — complete free-form script parser with word-boundary-disciplined regex (the OTS-in-footsteps bug fix)
- `templates/3tier-sample-scripts.md` — 5 ready-to-paste sample scripts covering each scene type

**Always deliver all three tiers** as independent files so the user can compare — never pick one for them.

## 10. Procedural GLB character library (Blender headless)

When the storyboard has >2 distinct character archetypes (adult / child / elderly, or different body types), the gray-mesh `human.glb` plus its Group fallback isn't enough. Generate a **character library** ahead of time via headless Blender:

```
vendor/characters/
├── adult_male.glb
├── adult_female.glb
├── child_boy.glb
├── child_girl.glb
├── old_man.glb
└── ...
```

Parameterize `make_human.py` with `--gender`, `--age`, `--body`, `--muscular` flags, then run `templates/3d-build-character-lib.sh` to batch-generate the full library. At runtime auto-select via `pickCharacterModel(name, actor)` (rules in `references/3d-character-library.md`).

Crucial limitation to surface to users: **all procedural GLBs render as the same gray-mesh look**. If the user complains "still looks childish / cartoon", do NOT download more gray-mesh GLBs — jump to a textured source (Mixamo / Meshy / commercial). See `references/3d-open-source-glbs.md` for the realistic-character path.

Sibling references for the GLB character path: `references/3d-glb-parser.md` (minimal 80-line built-in parser), `references/3d-js-pitfalls.md` (Three.js quirks: `!numericIndex` falsy-zero bug, Float32Array byte-vs-element count), `references/3d-blender-headless-glb-gen.md` (full Windows install via Tsinghua mirror + MPFB headless limitations + Y-up→Z-up rotation matrix).

## 相关 skill

- `storyboard-gen` — v6 分镜剧本的源头（必加载）
- `short-drama-pipeline` — AIGC_agent 全流程（如果要集成到 pipeline）
- `comfyui` — 备选的 3D/视频生成路径
- `gen-api-integration` — 第三方生成 API 集成模式（含 MiniMax Anthropic 风格适配 reference）

## Absorbed skills

This umbrella subsumes the following previously-separate skills (now in `.archive/`):

- **`3d-storyboard-previz`** — procedural GLB character library, headless-Blender generation pipeline, v6-schema-bridge mapping, built-in 80-line GLB parser, walking animation via Group traversal. All references in `references/3d-*.md`, all templates in `templates/3d-*.{sh,py,html}`. Live demo: `D:\AI_work\previs\storyboard-previs.html`.
- **`storyboard-preview-tool`** — 3-tier browser rendering (3D / 2D / Ken Burns), free-form script parser, 6-move camera vocabulary. References in `references/3tier-*.md`, samples in `templates/3tier-sample-scripts.md`. Live demos at `C:\Users\leohu\D\ImageGen\storyboard-3d.html`, `storyboard-2d.html`, `storyboard-kenburns.html`.
- **`threejs-frontend-tools`** — narrow Three.js quick-start recipe (UMD loading, character composition, FOV conversion, walk-cycle amplitude, LLM provider adapter). All of its content already lives here under the umbrella's own body and the absorbed `references/3d-*.md` + `llm-browser-gateway/references/demo-*.md`. The standalone `templates/storyboard-previs-skeleton.html` is a smaller variant of `templates/3d-storyboard-previs-skeleton.html`.