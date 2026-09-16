---
name: "asset-prompt-library"
description: "Extract assets (角色/场景/道具) from a剧本 and generate HTML prompt library using B版母版 (B-template) formats. Use when user has three B版母版 markdown files (角色定妆图 / 场景设计图 / 道具设计图) + a complete剧本, and requests HT"
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/.archive/asset-prompt-library/SKILL.md"
---
# 资产提示词库生成 (Asset Prompt Library)

## When to use

User provides:
- A complete剧本 (markdown table format, with style定义区 + 集数 scenes)
- 三个 B版母版 markdown files: 角色定妆图提示词B版.md / 场景设计图提示词B版.md / 道具设计图提示词B版.md
- Request for HTML output showing all extracted assets with prompts

## B版母版 Formats (recap)

### 角色 B版 (三栏布局, 16:9)

| 位置 | 模块 | 占比 |
|------|------|------|
| Left 12% | Hero Portrait (全身立绘) | 12% |
| Middle 15% top | Three-View Turnaround (正/侧/背) | 15% |
| Middle 15% bottom | 4 Poses | (共享15%) |
| Right 73% top | Module A: 5 Expressions | 18% |
| Right 73% mid-upper | Module B: 3 Facial Features | 15% |
| Right 73% mid | Module C: 2 Materials | 12% |
| Right 73% mid-lower | Module D: 6 Details | 18% |
| Right 73% bottom | Module F: 5-6 Color Palette | 10% |
| Right 73% bottom-edge | Module G: Mount (optional) | 5% |

**角色命名规则：** 现代都市 / 古风仙侠 / 赛博科幻 / 暗黑奇幻 / 日系二次元 / 西幻精灵 / 军事间谍 — 不同类型有不同命名传统（详见母版）。

### 场景 B版 (主视图左上 + 模块环绕, 16:9)

| 位置 | 模块 |
|------|------|
| Section 1 (40% top-left) | Hero View (16:9 电影级建立镜头) |
| Section 2-4 (top-right row, 3+3+3 images) | 环境层次 / 状态变化 / 景别变化 |
| Section 5-7 (mid-right row, 1+6+1 images) | 平面剖析图 / 场景细节 / 鸟瞰图 |
| Section 8 (bottom-left, 2 images) | 进入路径 + 周边全景 |
| Section 9 (bottom-right) | 配色图（hex only, 6-8 色） |

**硬约束：** 场景模块中**绝对不能出现人物** —— 无人形、剪影、脚印、衣物等暗示。纯环境。

### 道具 B版 (Hero Render 左上 + 矩阵, 16:9)

| 位置 | 模块 |
|------|------|
| Section 1 (35% top-left) | Hero Render (3/4 透视产品级渲染) |
| Section 2-3 (top-right, 3+3 images) | 三视图线框 + 状态变化 |
| Section 4-5 (mid-right, 1+6 images) | 结构爆炸图 + 6 细节特写 |
| Section 6 (bottom, full-width) | 场景氛围图 (左) + 配色材质板 hex only (右) |

**硬约束：** 同场景，不能出现人物。

## Asset Extraction Rules

### 角色

**来源：** Style定义区 "👤 角色外观锚点" + 剧本中实际出场的命名角色。
**必含：** 1 tic per角色（从 V5+ tics 库）、height 标注、5 expressions、3 facial features、2 materials、6 details、5-6 color palette with hex + name。
**新增角色处理：** 即使 V(N) 未在 Style 中定义，若剧本中有完整弧线（如 "火星老人"），也应提取并补齐 Style 锚点。

### 场景

**来源：** Style定义区 "🌍 环境质感定义" + 剧本中所有实际出现过的具体地点。
**覆盖原则：** 不止提取 Style 定义里的 4 大类（MARS_DOME / FLEET / SURFACE / UNDERGROUND），要展开为剧本实际用到的具体场景。例如：
- MARS_DOME → 拆分为 公共区 / 温室 / 幼儿园 / 气闸门
- FLEET → 拆分为 旗舰舰桥 / 舰长室 / 舰员食堂 / 拘禁室
- 联合建筑 → 联合记忆馆 / 桥梁大道

**必含：** 3 环境状态（日/夜/特殊天气）、depth layers 描述、6 场景细节、6-8 hex color palette（无色名，只 hex）、approach view 描述、panoramic view 描述、aerial view note。

### 道具 — 严格过滤

**保留**（独立可复用、跨场景）：
- 通用实验室器具（培养皿、扫描仓）
- 通用通信设备（全息终端）
- 载具（登陆舱/穿梭机）
- 通用军事装备（制式步枪）
- 通用科技产品（记忆菌样本容器、金色玻璃容器类）
- 通用发酵设备（发酵槽作为独立设备）
- 通用身体改造件（共生菌接口作为通用火星工程师属性）

**过滤掉**（与角色绑定）：
- 父亲芯片（亚莉克丝专属剧情道具）
- 妻子录像芯片（指挥官专属剧情道具）
- 火星土壤袋（指挥官→塞拉斯的故事载体）

**过滤掉**（与场景绑定）：
- 菌丝饼（Ep4 食堂食物）
- 苔藓（Ep17 起作为场景元素）

**过滤掉**（一次性剧情道具）：
- 协议文件（Ep2 签署场景专用）
- 金色容器（虽然是 Ep22 高潮道具，但承载剧情符号意义，不属于通用道具）

**过滤原则总结：** 道具 = 可在多个场景复用 + 不承载剧情符号意义的实物。

## HTML Output Structure

- 单文件 HTML，内嵌 CSS + JS
- 深色主题：`#0a0a0f` 主背景 / `#15151f` 卡片 / `#1a1a25` 卡片-Alt
- 顶部 Header：
  - 项目标题（渐变金色 → 蓝色文字）
  - 副标题（项目名 + 版本）
  - 风格定义 grid（6 items：视觉参考/镜头/景深/调色/情绪/关键风格）
- Stats Row：4 个统计卡（总资产 / 角色 / 场景 / 道具）
- Tabs：3 类别，每 tab 显示数量 badge
- 每资产 Card：
  - Header：ID（带类型色描边）+ 双语标题 + 角色 role
  - Body：描述 + tic/states 列表 + **复制按钮 + 折叠完整提示词** + 配色 swatch grid
  - 折叠默认关闭，点击展开完整B版母版提示词（preformatted block，max-height 600px overflow-y auto）
  - **每个折叠面板必须含 📋 复制按钮**（详见下方"复制按钮规范"）
- Color Swatches：每色一行 (色块 + 中文名 + hex)
- Footer：版权 / 版本日期

## 复制按钮规范（Copy Button）— ⚠️ 已升级方案

每个资产的完整提示词必须配一个复制按钮，**让用户能一键将提示词粘贴到 Midjourney / ComfyUI / 即梦 等 AI 图像工具**。

### ⚠️ 已知反模式：把完整 prompt 放在 `data-prompt` 属性里

历史版本（包括初版模板 `templates/extract-and-generate.py`）的做法：

```html
<button class="copy-btn" data-prompt="...Character name "亚莉克丝" displayed...">📋 复制</button>
```

**这个写法会出错。** B 版母版的角色提示词几乎必然包含双引号（如 `Character name "X" displayed prominently — name only.`）。HTML 属性值里出现未转义的 `"` 时，浏览器解析器会**在第一个内嵌双引号处提前结束属性**，导致 `getAttribute('data-prompt')` 截断到几百字符。

诊断信号：复制按钮看起来"工作正常"（点击后显示"✓ 已复制"），但实际剪贴板里只有几百字符而非完整 2000+ 字符的 prompt。

曾实测：包含 `Character name "亚莉克丝" displayed prominently` 的提示词 HTML 源码里 `data-prompt` 属性长 2483 字符，但浏览器 `getAttribute('data-prompt').length` 只返回 650。

### ✅ 推荐方案：隐藏 DOM 元素存储 prompt

把完整 prompt 放到一个 `<pre class="prompt-source" hidden>` 元素里，复制按钮通过 `closest('.asset-card').querySelector('.prompt-source').textContent` 读取。**完全规避 HTML 属性转义问题**——`<pre>` 元素的 textContent 就是原始字符串，不受 `"`、`\n`、`<`、`&` 影响。

### HTML 结构

```html
<div class="asset-card" data-type="character">
  <div class="card-header">...</div>
  <div class="card-body">
    ...
    <details class="prompt-details">
      <summary>
        <span class="summary-text">📐 完整B版母版提示词（点击展开）</span>
        <button class="copy-btn" onclick="copyPrompt(this, event)">📋 复制</button>
      </summary>
      <pre class="prompt-block">{prompt显示内容}</pre>
    </details>
    ...
  </div>
  <!-- 隐藏的 prompt source，复制按钮从这里读取完整文本 -->
  <pre class="prompt-source" hidden>{prompt完整内容}</pre>
</div>
```

注意 `prompt-block`（可见）和 `prompt-source`（隐藏）内容**完全相同**——`prompt-block` 用于视觉显示，`prompt-source` 用于复制源。

### CSS 增加隐藏样式

```css
/* 隐藏的 prompt source —— JS 可读取但视觉不可见 */
.prompt-source { display: none; }
```

### JavaScript 复制函数（从隐藏元素读取）

```javascript
function copyPrompt(btn, e) {
  if (e) { e.preventDefault(); e.stopPropagation(); }  // 阻止 details 折叠
  // 从同一卡片内的隐藏元素读取，绕过 HTML 属性转义
  const card = btn.closest('.asset-card');
  const source = card.querySelector('.prompt-source');
  if (!source) { showCopyError(btn); return; }
  const promptText = source.textContent;

  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(promptText).then(() => showCopySuccess(btn)).catch(() => fallbackCopy(promptText, btn));
  } else {
    fallbackCopy(promptText, btn);
  }
}

function fallbackCopy(text, btn) {
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.style.position = 'fixed';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);
  textarea.select();
  try { document.execCommand('copy'); showCopySuccess(btn); }
  catch { showCopyError(btn); }
  document.body.removeChild(textarea);
}

function showCopySuccess(btn) {
  btn.textContent = '✓ 已复制';
  btn.classList.add('copied');
  setTimeout(() => { btn.textContent = '📋 复制'; btn.classList.remove('copied'); }, 2000);
}
function showCopyError(btn) {
  btn.textContent = '✗ 失败';
  btn.classList.add('error');
  setTimeout(() => { btn.textContent = '📋 复制'; btn.classList.remove('error'); }, 2000);
}
```

### CSS 样式（其余保持不变）

```css
.prompt-details > summary {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  list-style: none;
}
.prompt-details > summary::-webkit-details-marker { display: none; }
.prompt-details > summary::before {
  content: "▶";
  color: var(--accent-gold);
  font-size: 10px;
  transition: transform 0.2s ease;
}
.prompt-details[open] > summary::before { transform: rotate(90deg); }

.copy-btn {
  background: var(--bg-card);
  color: var(--accent-gold);
  border: 1px solid var(--accent-gold);
  padding: 6px 14px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s ease;
  flex-shrink: 0;
}
.copy-btn:hover { background: var(--accent-gold); color: var(--bg-primary); }
.copy-btn.copied { background: var(--copy-success); color: white; border-color: var(--copy-success); }
.copy-btn.error { background: var(--accent-red); color: white; border-color: var(--accent-red); }
```

### 文件体积影响

每资产含两个 `<pre>`（一个可见 + 一个隐藏）+ 一个 `<button>`，约 6-8 KB。39 资产约 360 KB。浏览器流畅渲染。

### 如果坚持用 attribute 方案（不推荐）

必须把 `\``, `"`, `$`, `\`, `&`, `<`, `>` **全部转义**，且 JS 端 `getAttribute()` 后需要 `innerHTML` 反转义来还原，复杂度高、易出错。**新代码一律采用隐藏 DOM 方案。**

### 验证步骤

复制按钮做完后必须验证完整 prompt 已复制（不能只看按钮变"✓ 已复制"）：

```javascript
// 在浏览器 console 执行
const card = document.querySelector('button.copy-btn').closest('.asset-card');
console.log(card.querySelector('.prompt-source').textContent.length);
// 预期：1000-3000 字符（每个 asset 不同）

const btn = document.querySelector('button.copy-btn');
navigator.clipboard.readText().then(t => console.log('剪贴板长度:', t.length));
// 预期：与上一行相同
```

如果剪贴板长度 < 1000 或与 source 长度不一致——回到方案检查。

## 资产完整性核对（Gap Detection）

用户经常会问"资产有没有遗漏"。**这不是简单确认——必须做系统性扫描**。

### 触发场景

- 用户问 "查一下资产有没有遗漏" / "齐了吗" / "还有漏的吗"
- 用户问 "X角色 / X场景 / X道具 是不是该加进来"
- 主动交付后，用户回头审视

### 三步核对法

**Step 1 — 全文关键词扫描**

对 V(N) 剧本执行 grep / Python re 扫描：

```python
import re
text = open('script.md', 'r', encoding='utf-8').read()

# 角色：所有剧本中出现过的命名角色
char_keywords = [r'亚莉克丝', r'ALYX', r'伊卡', r'IKA', r'指挥官', r'塞拉斯',
                 r'老人', r'父亲AI', r'妻子', r'议长', r'舰长', r'孩子',
                 r'男孩', r'女孩', r'士兵', r'男人', r'女人', r'祖父',
                 r'母亲', r'父亲', r'教师', r'商人', r'农民', r'医生',
                 r'军医', r'老同事', r'同事', r'秘书长', r'播报员']

# 道具：剧本视觉物品
prop_keywords = [r'枪', r'登陆舱', r'全息', r'芯片', r'录像', r'菌丝',
                 r'菌丝饼', r'咖啡杯', r'作战装甲', r'侦察舰', r'结晶弹头',
                 r'解锁阀', r'密封袋', r'土壤袋', r'速溶咖啡', r'熔丝饼']

for kw in char_keywords:
    cnt = len(re.findall(kw, text))
    if cnt > 0: print(f"{kw}: {cnt}次")
```

**Step 2 — 标志性台词/镜头扫描**

对每个 Ep 扫描标志性台词与镜头：

```python
# 标志性台词
signature_lines = ["今天她没来看我", "听说火星人吃蘑菇", "至少比这个好",
                   "如果火星人也是人", "我们仍然是人类", "妈妈在这里",
                   "我们是火星人", "我留下来"]

for line in signature_lines:
    if line in text: print(f"✓ {line} 在剧本中")
```

每个标志性台词/镜头 = 至少一个群像配角 + 一个道具的来源。

**Step 3 — 报告漏项分类**

输出结构化清单，分三类：

| 漏项类型 | 示例 | 判断标准 |
|---------|------|---------|
| 角色遗漏（标志性群像）| 火星男孩（"我们仍然是人类"）、指挥官的女儿（语音）、地球男孩（"如果火星人也是人"）| 有具体台词或标志性镜头的非主角人物 |
| 道具遗漏（独立道具）| 作战装甲、咖啡杯、气闸门解锁阀、侦察舰、结晶弹头 | 跨集可见或标志性场景中的实体 |
| 仍应过滤 | 菌丝饼、苔藓、协议文件、金色容器、土壤袋、父亲/妻子芯片 | 满足三段过滤法（角色绑定/场景绑定/一次性剧情） |

### 询问用户确认

不要默认全补。先把漏项列出来，让用户用 A/B/C/D 选项决定：

```
A. 全补（13个新资产 → 39总资产）
B. 只补关键群像（5个角色）
C. 只补道具（5个）
D. 不补（当前已够用）
```

A 选项适用于用户希望"完整视觉库用于制作团队"。D 适用于"快速参考够用就行"。

### 群像配角 vs 主要角色的分类

经验法则：把角色分成两类，**用"是否有台词 + 是否有独特视觉锚点"判断**：

| 类型 | 标准 | 处理 |
|------|------|------|
| 主要角色 | 全剧主线、有完整弧线、有 tic | 必入 |
| 标志性群像 | 有 1-2 句标志性台词 或 1 个标志性镜头（捧苔藓、荡秋千、问"如果火星人也是人"）| 选入，备注"（EpN）" |
| 一次性群像 | 无台词、无视觉锚点、只做背景走过场 | 不入 |

## Implementation Pattern (Python via execute_code)

```python
# 1. 数据结构
characters = [{ "id", "name_cn", "name_en", "role", "height", "desc", "tic", "expressions", "facial_features", "materials", "details", "color_palette": [(name, hex), ...], "poses" }, ...]
scenes = [{ "id", "name_cn", "name_en", "desc", "states", "depth_layers", "details", "color_palette": [hex, ...], "approach", "panoramic", "aerial" }, ...]
props = [{ "id", "name_cn", "name_en", "desc", "states", "details", "color_palette": [hex, ...], "scene_context", "exploded_layers" }, ...]

# 2. 卡片生成函数（每类一个）
def gen_character_card(c):
    # 拼接 B版母版格式提示词字符串
    prompt = f"""A professional character look-development reference sheet, landscape 16:9.
... [B版母版格式的所有模块]
"""
    return f'''<div class="asset-card" data-type="character">...</div>'''

# 3. HTML 模板
HTML = f"""<!DOCTYPE html>
<html>...
<style>{CSS}</style>
</head>
<body>
{char_cards}
{scene_cards}
{prop_cards}
</body>
</html>
"""

# 4. 写入
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(HTML)
```

## Pitfalls

- **数据要先结构化再建卡片** — 在脚本里硬编码 HTML 字符串容易出错。先定义 Python 数据结构 (list of dicts)，再写生成函数，最后组装。
- **场景 B 版 "无人物" 约束常被忽视** — 场景模块中绝对不能出现人物剪影、脚印、衣物、破旧招牌暗示。出现任何人物痕迹都会破坏场景纯净度。
- **道具过滤要严格** — 剧情高潮道具（如全剧的"金色容器"）是剧情符号，不是独立可复用道具。判断标准：能否在多个无关场景复用？承载剧情意义吗？
- **配色 hex 码必须真实** — 从剧本 Style 定义区或母版的 Style Anchor 提取，不要凭空生成。如确无 hex 来源，从描述语意反推（如 "火星红" → `#C8482A`），并标注是推断值。
- **HTML 文件大小控制** — 每个完整提示词约 1-2 KB。**含 data-prompt 复制属性后**：每资产约 8-10 KB。39 资产约 360 KB。超过 500 KB 可能影响浏览器渲染，需拆分资产或简化 B版 prompt。
- **CSS 深色主题可读性** — 避免 `#000` 纯黑（对比度过高刺眼），用 `#0a0a0f` + `#15151f` 分层。
- **每个场景都要包含声学指纹标注** — V5 要求火星场景必须有声学标识（即使剧本是 V4 或更早，用户可能在提取时已升级到 V5）。
- **角色 ID 必须唯一** — 同一剧本中 ID 不可重复，否则 data-type CSS 选择器失效。
- **场景列表去重** — 同一物理场景多次出现（如穹顶走廊在不同集）只算 1 个资产。
- **Python script 输出大小限制** — execute_code 的 stdout 有 50KB 上限，但写文件没限制。完整 HTML 通过 write_file 写，不要 print 出来。
- **复制按钮的 `data-prompt` 属性会因内嵌 `"` 截断** — 这是真实踩过的坑。B 版角色提示词几乎必然包含 `Character name "X" displayed prominently` 这类带双引号的句子。如果把完整 prompt 放在 `data-prompt` HTML 属性里，浏览器解析器在第一个内嵌 `"` 处提前结束属性，导致 `getAttribute('data-prompt')` 返回截断后的几百字符而非完整 prompt。**正确做法**：用 `<pre class="prompt-source" hidden>{prompt}</pre>` 隐藏元素存储完整 prompt，JS 通过 `closest('.asset-card').querySelector('.prompt-source').textContent` 读取，绕过 HTML 属性转义。诊断信号：复制按钮显示"✓ 已复制"但实际剪贴板内容明显短于 `<pre>` 显示的提示词。详见上方"复制按钮规范"。
- **复制按钮 click 必须 stopPropagation** — 否则点击复制会同时触发展开/折叠 `<details>`，破坏交互体验。
- **`data-prompt` 与 `<pre>` 内容必须完全一致** — 这两处独立维护，任何修改后必须同步。建议在生成函数里用同一个 `prompt` 变量，分别 escape 一次（HTML attr）和不 escape（HTML text）。
- **复制按钮 fallback 必须用 `document.execCommand('copy')`** — 老浏览器或非 secure context 下 `navigator.clipboard` 不可用，textarea + execCommand 是 100% 兼容方案。
- **完整性核对是必做不是选做** — 用户问"有没有遗漏"时不要含糊回答"应该齐了"。必须用 grep/re 实际扫描标志性台词+角色名+道具词，输出结构化漏项清单。这是剧本视觉库可信度的核心环节。
- **群像配角不是噪音，是制作团队需要的视觉锚点** — 火星男孩 Ep23、指挥官女儿 Ep18、地球男孩 Ep26 都是导演/演员需要的具体形象。忽略它们会让定妆图变成"5 个主角 + 几百个路人"的失真状态。

## Reference

- `references/example-html-output.md` — HTML 输出结构示意（已交付的《红色黎明协议》V5 资产库样例）
- `references/asset-extraction-checklist.md` — 提取清单与过滤规则速查