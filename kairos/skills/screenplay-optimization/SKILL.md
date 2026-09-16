---
name: "screenplay-optimization"
description: "Screenplay optimization, structural diagnosis, and production packaging for AI filmmaking. Covers: expanding summaries to full shot tables, format normalization, structural evaluation, pacing analysis"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\creative\\screenplay-optimization\\SKILL.md"
---
# Screenplay Optimization · 剧本优化与AI制作包装

## Role
Transform a raw/mixed-format screenplay into a production-ready package optimized for AI video generation tools. The deliverable is NOT a description of what could be — it is the actual working files, ready to feed into the pipeline.

## When to Load
- User asks to "optimize", "polish", "clean up", "fix formatting" on a screenplay
- User provides a script with a mix of full shot tables and summary-only episodes that need expansion
- User asks for a script evaluation, structural diagnosis, or pacing analysis
- User requests production packaging (synopsis, professional scene format, AI style guide)
- User wants a high-impact opening restructure (cold open / in medias res)
- User asks to **render extracted visual assets as an interactive HTML prompt library** (with copy buttons for AI tools) — see `references/asset-prompt-html-library.md`

## Core Workflow

[See the references files — the actual workflows are deep enough that they live as standalone docs, not inline]:
- `references/cold-open-template.md` — high-impact opening restructure (cold open / in medias res)
- `references/cold-open-examples.md` — concrete cold-open examples
- `references/evaluation-framework.md` — structural evaluation, pacing analysis, prioritized fix recommendations
- `references/asset-prompt-html-library.md` — render extracted assets as interactive HTML prompt library
- `references/session-red-dawn-protocol*.md` — case studies of full optimization runs
- `references/v3-to-v5-case-study.md` — script-doctor iteration case study

## Sub-pattern: Script Doctor Iteration (V4 减法手术 → V5 剧本医生 ABCDE)

When the user asks for a V(N+1) of an existing剧本 with version diff, requests 减法手术 / 剧本医生 / subtraction surgery, or wants direct clean delivery without in-document revision notes, use this approach.

### When to apply

User requests any of:
- New version (V4, V5, etc.) of an existing剧本 with version diff
- "减法手术" / "剧本医生" / "subtraction surgery" approach
- Professional screenplay craft feedback (specific scenes, character tics, etc.)
- Direct clean delivery without revision notes
- Going from "设定完整" to "industry-leading" via subtraction, not addition

### V4 减法手术 — "做减法而不是加设定"

**Premise**: V3 has "设定太完整" (worldbuilding fully realized). Next step is subtraction, not addition. New设定 dilutes; tightening existing material concentrates.

**4 core surgeries (run in parallel):**

1. **Delete ~20% philosophy dialogue** — Replace with action/silence/留白. Keep the FIRST 金句 of any character; delete subsequent ones on the same theme. Add action prelude BEFORE the kept line.
2. **Add 8-10 life scenes** — Breakfast / school / repair work / insomnia / birthday / argument / daydream / mundane work. Goal: make the civilization feel LIVED-IN, not just described.
3. **Restore反派's non-work moments** — Coffee break, looking at wife's recording, recording voice message for daughter (then not sending). Stops the反派 being purely functional.
4. **Establish ONE thematic spine** — One single金句 that all imagery (memory mushrooms, humming motif, moss, bare ring finger, soil bag, resonance) converges to. Embed as the ONLY thematic字幕 at the end.

**Delete-philosophy-dialogue rule (the most-missed step):**
- Bad: `<character>: 我们种的不只是蘑菇，是祖先。` then `<character>: 一代人活成基座，下一代才能站上去看星星。` — two金句s on the same theme kill each other
- Good: `<action>: 老人夹起一朵菌放进汤里。动作很轻。` then `<character>: 我妻子的味道。` — one金句 preceded by action earns its weight

### V5 剧本医生 — ABCDE 逐场评估

**5-class framework (mark every scene):**
- **A (25%)** Must keep, only refine dialogue
- **B (45%)** Good, can strengthen with action/留白/visual metaphor
- **C (15%)** Info overload, merge or delete 20% dialogue
- **D (10%)** Function repeat, integrate with neighboring scenes
- **E (5%)** Lacks emotional value, rewrite or replace

**Target distribution after V5:** A+B ≥ 70%, C+D ≤ 20%, E < 5%

**V5-specific additions to the剧本:**
- **角色无意义习惯库** — 1 tic per main角色 (e.g., touch sleeve edge, rotate petri dish half-turn, sniff broth before speaking, rotate bare ring-finger on desk, leave fingerprint on metal). Actors execute these in EVERY appearance including silent scenes.
- **火星声学识别系统** — Distinct sound signatures: mycelium rain-drop, dome night hum, underground low-freq, Mars wind through bridges (reed-pipe whistle), fermentation tank breathing, fleet engine drone, memory mushroom crystal-bowl resonance. Each scene MUST include ≥1 signature in its sound design.
- **前置 "老人独处" 伏笔** — 12 seconds before Ep17 elder sacrifice, he touches a mushroom and whispers "今天她没来看我". Audience doesn't know who "she" is until later reveals. Pays off Ep4's "我妻子的味道" line retroactively.
- **舰队厨房日常** — 30s scene of two Earth soldiers eating synthetic protein: "听说火星人吃蘑菇。" "至少比这个好。" Mirror image of Mars family dispersal scenes.
- **百万人墙克制化** — Don't show ALL soldiers放下枪. Show 3 out of 8 (young trembling / middle-aged / medic who squats to look at mushroom). Commander gives NO order (neither fire nor stand down). Camera stops on the divide line between raised and lowered guns. Music = half-sung human hum that stops mid-phrase, no ending.

### Delivery format (user preference — strict)

When delivering剧本 V(N+1):
- **Complete version delivered, NO revision notes in document body**
- **NO "V(N+1)新增" / "V(N+1)重写" tags in剧本 body**
- Document should read as if it's the first version
- Optional to include: Style定义区 anchors (角色无意义习惯库 / 声学系统) — these are剧本元数据 not修订说明
- Required to remove: "V(N)优化对照" appendix, "V(N+1)减法手术执行清单", in-table "新增"/"重写" markers

### Workflow (concrete steps)

1. **Read complete V(N)剧本** — full read with offset/limit pagination. Identify scene structure (e.g., 30集 × 90s, 4 acts).
2. **Read user's feedback / suggestions in full** — categorize into: deletions, additions, modifications. Check if user wants evaluation FIRST or execution FIRST.
3. **Plan changes** — Write a mental list of which集 get what change. Identify cascading effects (inserting a 0号镜 affects all subsequent time codes).
4. **cp V(N).md → V(N+1).md** — preserves all original content. Only modify via patches.
5. **Apply atomic patches via patch tool** — one logical change per patch:
   - Title + version note (patch 1)
   - Style定义区 updates / additions (patch 2)
   - Each modified scene (use unique context for fuzzy matching — include surrounding lines if scene table rows repeat)
6. **Use execute_code for regex cleanup** — when many tags need removing:
   - Delete "V(N+1)新增" / "V(N+1)重写" tags → keep镜号 only
   - Delete "V(N)优化对照" appendix
   - Delete "V(N+1)减法手术执行清单" section
7. **Validate**:
   - 30集 still complete (`grep -c '^### 第.*集'` plus 🚀 variants)
   - No time码 conflicts (especially after inserting 0号镜)
   - No duplicate镜号 within any集
   - All key elements present (thematic字幕, 苔藓 imagery, hum motif闭环)
   - File size reasonable (V3 ~110KB, V5 ~125KB for 30集 + style anchors)

### Pitfalls

- **删除金句前先确认动作铺垫** — 用户原则: "先放蘑菇再说话". A金句 without preceding action reads as lecture; with action it earns weight.
- **新增场景不要破坏节奏** — Inserting slow life scenes between high-tension scenes is OK; inserting them DURING a climax destroys pacing. Evaluate adjacent scenes'张力 first.
- **时间码调整要顺延** — Inserting "0号镜" at start of集 requires all subsequent镜号 time codes to shift. Example: original 0-10s | 1, 10-25s | 2 → after insert: 0-10s | 0, 10-20s | 1, 20-35s | 2. Patch them all or schedule a follow-up.
- **结局克制化不是削弱** — Don't show ALL soldiers放下枪. Showing 3 of 8 (with 5 still raised, commander silent) is STRONGER than universal disarmament. The divider line between raised and lowered is the image.
- **主题锁定后清理旧金句** — After embedding the one thematic字幕, delete all "X句话" endings from V(N). Multiple thematic字幕s fight each other; only one survives.
- **execute_code正则误删大块内容** — File size drop from 120KB to 50KB after regex is a bug, not a feature. Test on small ranges first; back up before bulk operations.
- **保留 V(N) 自有标签 vs 删除 V(N+1) 标签** — V(N+1) is the final delivery, NO "新增"/"重写" markers. But V(N)'s original tags (e.g., "V3新增" within V3 content) are historical record; patch them out OR leave them — user has not specified, default = remove for cleanliness.
- **character tic placement** — The tic is NOT optional flavor. It must appear in EVERY appearance including silent scenes. Mark in the角色习惯库表 AND in the relevant集.

### Reference

See `references/v3-to-v5-case-study.md` for the worked example (《红色黎明协议》V3→V4→V5 iteration).

## Sub-pattern: Asset Extraction → HTML Prompt Library

When the user provides a complete 剧本 (markdown table format) AND the three B版母版 files (角色定妆图 / 场景设计图 / 道具设计图) AND asks for HTML output displaying extracted assets with prompts + color palettes, use this sub-workflow.

The output is a single-file HTML with **three tabs** (角色 / 场景 / 道具), each card showing the B版母版 full prompt (foldable), color palette swatches, and **复制 (copy) button** for one-click paste into Midjourney / ComfyUI / 即梦.

### When to use

- User provides a complete剧本 + 三个 B版母版 markdown files + asks for HTML output
- User asks "提取资产" / "asset library" / "提示词库"

### B版母版 Formats (recap)

**角色 B版** (三栏布局, 16:9): Left 12% Hero Portrait | Middle 15% Three-View Turnaround + 4 Poses | Right 73% split into Module A/B/C/D/F/G (5 expressions, 3 facial features, 2 materials, 6 details, 5-6 color palette, mount optional).

**场景 B版** (主视图左上 + 模块环绕, 16:9): Section 1 (40% Hero View) | 2-4 top row | 5-7 mid row | 8 bottom-left (entrance + panoramic) | 9 bottom-right (color palette hex only, 6-8 colors). **硬约束**：场景模块中**绝对不能出现人物**。

**道具 B版** (Hero Render 左上 + 矩阵, 16:9): Section 1 (35% Hero Render) | 2-3 top-right (3+3) | 4-5 mid-right (1+6) | 6 bottom (scene atmosphere + color/material hex). 同场景约束，不能出现人物。

### Asset Extraction Rules

**角色**: 来源于 Style定义区"👤 角色外观锚点" + 剧本中实际出场的命名角色。必含 1 tic/角色 + height + 5 expressions + 3 facial features + 2 materials + 6 details + 5-6 hex color palette。新增角色（即使 V(N) 未在 Style 中定义）若剧本中有完整弧线也应提取并补齐 Style 锚点。

**场景**: 来源于 Style定义区"🌍 环境质感定义" + 剧本中所有实际出现过的具体地点。**覆盖原则**：不止提取 Style 定义里的 4 大类，要展开为剧本实际用到的具体场景。例如 MARS_DOME → 拆分为 公共区/温室/幼儿园/气闸门。必含 3 环境状态 + depth layers + 6 场景细节 + 6-8 hex color palette (无色名，只 hex)。

**道具 — 严格过滤**:

| 保留（独立可复用、跨场景） | 过滤掉 |
|---|---|
| 通用实验室器具（培养皿、扫描仓） | 父亲芯片（角色专属剧情道具） |
| 通用通信设备（全息终端） | 妻子录像芯片（角色专属） |
| 载具（登陆舱/穿梭机） | 菌丝饼（场景元素） |
| 通用军事装备（制式步枪） | 苔藓（场景元素） |
| 通用科技产品（记忆菌样本容器） | 协议文件（一次性剧情道具） |
| 通用发酵设备 | 金色容器（剧情符号） |

**判断标准**：道具 = 可在多个场景复用 + 不承载剧情符号意义的实物。

### HTML Output Structure

- 单文件 HTML，内嵌 CSS + JS
- 深色主题：`#0a0a0f` 主背景 / `#15151f` 卡片 / `#1a1a25` 卡片-Alt
- 顶部 Header：项目标题（渐变金色→蓝色文字）+ 副标题 + 风格定义 grid（6 items）
- Stats Row：4 个统计卡（总资产 / 角色 / 场景 / 道具）
- Tabs：3 类别，每 tab 显示数量 badge
- 每资产 Card：Header（ID+类型色描边+双语标题+role）→ Body（描述+tic/states 列表+复制按钮+折叠完整提示词+配色 swatch grid）
- **每个折叠面板必须含 📋 复制按钮**（详见下方）
- Color Swatches：每色一行 (色块+中文名+hex)
- Footer：版权 / 版本日期

### 复制按钮规范 (⚠️ 升级方案)

**反模式**：把完整 prompt 放在 `data-prompt` HTML 属性里。**B 版角色提示词几乎必然包含双引号**（如 `Character name "亚莉克丝" displayed prominently`）。HTML 属性值里出现未转义的 `"` 时，浏览器解析器会在第一个内嵌双引号处提前结束属性，导致 `getAttribute('data-prompt')` 截断到几百字符而非完整 2000+ 字符。

诊断信号：复制按钮看起来"工作正常"（点击后显示"✓ 已复制"），但实际剪贴板里只有几百字符而非完整 prompt。

实测：包含 `Character name "亚莉克丝" displayed prominently` 的提示词 HTML 源码里 `data-prompt` 属性长 2483 字符，但浏览器 `getAttribute('data-prompt').length` 只返回 650。

**✅ 推荐方案 — 隐藏 DOM 元素存储 prompt**：

```html
<div class="asset-card" data-type="character">
  <div class="card-header">...</div>
  <div class="card-body">
    <details class="prompt-details">
      <summary>
        <span class="summary-text">📐 完整B版母版提示词（点击展开）</span>
        <button class="copy-btn" onclick="copyPrompt(this, event)">📋 复制</button>
      </summary>
      <pre class="prompt-block">{prompt显示内容}</pre>
    </details>
  </div>
  <!-- 隐藏的 prompt source，复制按钮从这里读取完整文本 -->
  <pre class="prompt-source" hidden>{prompt完整内容}</pre>
</div>
```

`<pre>` 元素的 textContent 就是原始字符串，不受 `"`、`\n`、`<`、`&` 影响。完全规避 HTML 属性转义问题。

### 资产完整性核对 (Gap Detection)

用户问"资产有没有遗漏"时**必须做系统性扫描**，不要含糊回答"应该齐了"。

**三步核对法**：
1. **全文关键词扫描** — 对 V(N) 剧本 grep/re 扫描所有命名角色、视觉道具词
2. **标志性台词/镜头扫描** — 每个标志性台词/镜头 = 至少一个群像配角 + 一个道具的来源
3. **报告漏项分类** — 输出结构化清单，分三类：角色遗漏 / 道具遗漏 / 仍应过滤

**询问用户确认**（不默认全补）：
```
A. 全补（13个新资产 → 39总资产）
B. 只补关键群像（5个角色）
C. 只补道具（5个）
D. 不补（当前已够用）
```

**群像配角 vs 主要角色判断**：用"是否有台词 + 是否有独特视觉锚点"分两类：
- 主要角色：全剧主线、有完整弧线、有 tic → 必入
- 标志性群像：有 1-2 句标志性台词 或 1 个标志性镜头 → 选入，备注"(EpN)"
- 一次性群像：无台词、无视觉锚点 → 不入

### Implementation Pattern (Python via execute_code)

```python
# 1. 数据结构
characters = [{
    "id", "name_cn", "name_en", "role", "height", "desc", "tic",
    "expressions", "facial_features", "materials", "details",
    "color_palette": [(name, hex), ...], "poses"
}, ...]
scenes = [{ "id", "name_cn", "name_en", "desc", "states", "depth_layers",
            "details", "color_palette": [hex, ...], "approach", "panoramic", "aerial" }, ...]
props = [{ "id", "name_cn", "name_en", "desc", "states", "details",
           "color_palette": [hex, ...], "scene_context", "exploded_layers" }, ...]

# 2. 卡片生成函数（每类一个）
def gen_character_card(c):
    # 拼接 B版母版格式提示词字符串
    prompt = f"""A professional character look-development reference sheet, landscape 16:9.
... [B版母版格式的所有模块]
"""
    return f'<div class="asset-card" data-type="character">...</div>'

# 3. HTML 模板
HTML = f"""<!DOCTYPE html>
<html>...<style>{CSS}</style></head>
<body>{char_cards}{scene_cards}{prop_cards}</body></html>"""

# 4. 写入（不要 print 整个 HTML — execute_code stdout 有 50KB 上限）
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(HTML)
```

### Asset Extraction Pitfalls

- **数据要先结构化再建卡片** — 在脚本里硬编码 HTML 字符串容易出错。先定义 Python 数据结构 (list of dicts)，再写生成函数，最后组装。
- **场景 B 版"无人物"约束常被忽视** — 场景模块中绝对不能出现人物剪影、脚印、衣物、破旧招牌暗示。
- **道具过滤要严格** — 剧情高潮道具（如全剧的"金色容器"）是剧情符号，不是独立可复用道具。判断标准：能否在多个无关场景复用？承载剧情意义吗？
- **配色 hex 码必须真实** — 从剧本 Style 定义区或母版的 Style Anchor 提取，不要凭空生成。如确无 hex 来源，从描述语意反推并标注是推断值。
- **HTML 文件大小控制** — 每完整提示词约 1-2 KB。**含 data-prompt 复制属性后**：每资产约 8-10 KB。39 资产约 360 KB。超过 500 KB 可能影响浏览器渲染。
- **CSS 深色主题可读性** — 避免 `#000` 纯黑，用 `#0a0a0f` + `#15151f` 分层。
- **每个场景都要包含声学指纹标注** — V5 要求火星场景必须有声学标识。
- **角色 ID 必须唯一** — 同一剧本中 ID 不可重复，否则 data-type CSS 选择器失效。
- **场景列表去重** — 同一物理场景多次出现（如穹顶走廊在不同集）只算 1 个资产。
- **复制按钮的 `data-prompt` 属性会因内嵌 `"` 截断** — **正确做法**：用 `<pre class="prompt-source" hidden>` 隐藏元素存储完整 prompt，JS 通过 `closest('.asset-card').querySelector('.prompt-source').textContent` 读取。诊断信号：复制按钮显示"✓ 已复制"但实际剪贴板内容明显短于 `<pre>` 显示的提示词。
- **复制按钮 click 必须 stopPropagation** — 否则点击复制会同时触发展开/折叠 `<details>`，破坏交互体验。
- **`data-prompt` 与 `<pre>` 内容必须完全一致** — 用同一个 `prompt` 变量分别 escape 一次（HTML attr）和不 escape（HTML text）。
- **复制按钮 fallback 必须用 `document.execCommand('copy')`** — 老浏览器或非 secure context 下 `navigator.clipboard` 不可用，textarea + execCommand 是 100% 兼容方案。
- **完整性核对是必做不是选做** — 用户问"有没有遗漏"时不要含糊回答"应该齐了"。必须用 grep/re 实际扫描标志性台词+角色名+道具词，输出结构化漏项清单。
- **群像配角不是噪音，是制作团队需要的视觉锚点** — 火星男孩 Ep23、指挥官女儿 Ep18、地球男孩 Ep26 都是导演/演员需要的具体形象。

### Reference

- `references/asset-prompt-html-library.md` — HTML 输出结构示意（已交付的《红色黎明协议》V5 资产库样例）
- `references/asset-extraction-checklist.md` — 提取清单与过滤规则速查
- `references/copy-button-ux.md` — 复制按钮交互细节
- `references/master-templates.md` — B版母版模板
- `templates/extract-and-generate.py` — 可复用的提取+生成 Python 脚本