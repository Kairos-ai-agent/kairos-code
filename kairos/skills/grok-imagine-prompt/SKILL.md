---
name: "grok-imagine-prompt"
description: "Grok Imagine 图生视频/文生视频提示词写作工作流。涵盖五要素提示词结构、图生视频克制原则、角色一致性、产品/海报动画、短剧/漫剧场景的实用模板和限制须知。"
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/creative/grok-imagine-prompt/SKILL.md"
---
# Grok Imagine 视频提示词写作指南

> 基于 PixVerse 2026年5月 Grok Imagine 官方指南整理。

## 核心原则：克制

**图生视频的关键是克制。** 图片已承载主体、构图和风格，提示词应聚焦于：
- **运动**（动作变化）
- **镜头行为**（推拉摇移）
- **氛围**（光线、天气、背景运动）
- **保持不变的内容**（约束条件）

**文生视频** 则在提示词中描述完整的主体+环境+动作。

## 提示词五要素公式

每个提示词都应回答这五个问题：

| 要素 | 英文关键词 | 说明 |
|------|-----------|------|
| **主体 (Subject)** | character / product / object | 必须保持清晰的人物、产品、场景 |
| **动作 (Action)** | walks, turns, light sweeps, rain falls | 片段中发生了什么变化 |
| **镜头 (Camera)** | push-in, pull-back, track, crane-up, handheld, macro | 镜头运动方式 |
| **环境 (Environment)** | location, time, light, weather | 地点、时间、光线、氛围、背景运动 |
| **约束 (Constraints)** | no text, preserve label, keep identity, no extra characters | 必须保持不变的内容 |

## 图生视频提示词模板

### 通用结构
```
Animate this [image type] as a [style] [format]. 
Keep the [subject], [composition], [colors], [key elements] unchanged.
Add [motion 1], [motion 2], [camera movement], [atmospheric effect].
[Constraints]. [Aspect ratio].
```

### 示例模板

**产品预告:**
```
Animate the uploaded product image into a premium launch teaser.
Keep the product shape, color, label, and camera angle consistent.
Add a slow push-in, a subtle light sweep across the surface,
tiny particles floating in the background, and a clean studio shadow shift.
No extra text, no extra objects, vertical 9:16.
```

**海报动画:**
```
Animate this movie poster as a short atmospheric teaser.
Keep the main character, composition, title placement, and color palette unchanged.
Add drifting fog, a slow camera push toward the character's face,
faint background light movement, and subtle fabric motion.
Cinematic suspense mood, no new text.
```

**角色一致性（参考图）:**
```
Use the reference images to preserve the character's face,
hairstyle, jacket, and color palette.
Generate a new shot where the character walks through [scene],
[action details].
Smooth tracking shot, realistic reflections, [lighting mood],
no extra characters with the same face.
```

## 短剧/漫剧专用场景提示词

### 场景一：角色出场
```
Animate this character image as a dramatic entrance shot.
Keep the character's face, outfit, hairstyle, and expression consistent.
The camera slowly pulls back as the character steps forward,
turning their head slightly to reveal the full outfit.
Cinematic lighting, subtle fabric movement, shallow depth of field,
vertical 9:16, no text, no extra characters.
```

### 场景二：对话反应镜头
```
Animate this character portrait as a reaction shot.
Keep the face, expression baseline, and background unchanged.
The character's eyes shift slightly, a micro-expression crosses their face,
a soft breath visible in the cool air.
Tight close-up, realistic skin detail, subtle natural motion,
no text, no extra objects.
```

### 场景三：意境/环境转场
```
Animate this scene image into an atmospheric transition shot.
Keep the composition, colors, and main elements unchanged.
Add drifting mist, soft light change from day to golden hour,
a few leaves or particles floating through the frame.
Slow dreamy camera push-in, cinematic mood, no text, no characters.
```

### 场景四：动作/行走镜头
```
Animate this character into a walking shot.
Keep the character's face, hairstyle, outfit, and body proportions unchanged.
The character walks forward as the camera tracks alongside.
Natural arm swing, slight hair movement from the motion,
background blurs slightly with parallax.
Realistic movement, no text, no extra characters, vertical 9:16.
```

## 文生视频提示词（无输入图时使用）

### 短剧风格
```
A stylish young creator in a [outfit] stands in a [setting].
The lights flicker once, then [scene transition].
The camera tracks backward as [action details].
Fast social hook, cinematic but playful, crisp facial detail,
smooth motion, vertical 9:16, no captions.
```

### 电影感环境
```
A lone [character] [action] in a [dramatic setting].
The sky is [colors], [atmospheric effects].
Each [action detail] sends a ripple through the environment.
The camera starts [position], then slowly cranes upward to reveal [vista].
Epic cinematic mood, realistic reflections, slow graceful motion,
widescreen 16:9, no text.
```

## ⚠️ 已知限制

| 项目 | 参数 |
|------|------|
| 最长视频时长 | 15 秒 |
| 视频编辑输入 | ≤ 8.7 秒 |
| 延展输出 | 2-10 秒 |
| 支持分辨率 | 480p / 720p |
| 输出 URL | 临时链接，需下载保存 |
| 内容审核 | 通过后可见 |

## 💡 最佳实践

1. **图生视频 > 文生视频** 当需要角色/产品一致性时
2. **约束条件写在最后**，用 `no text`、`preserve X` 句式
3. **动作要具体**："walks forward" 比 "moves" 好，"slow camera push-in" 比 "camera moves" 好
4. **多片段拼接**：每个 Grok 视频最多 15 秒，长内容需多个片段 + 延展 + 后期编辑
5. **生产前下载**：输出 URL 临时有效
6. **文字检查**：AI 可能生成看似可读但错误的文字，发布前检查

## 📖 故事板/分镜图提示词（多格合成 → 连续视频）

用于上传一张**多格分镜图**（如 3×3 九宫格、4格漫画、故事板），用单条提示词生成连续视频。

### ⚠️ 核心坑点：9宫格整图输入的真相

Grok Imagine 的**图生视频**模式把整张上传图视为**单一画面的起始帧**。9宫格在它眼里是一幅拼贴画/设计稿，不是9个独立场景。

**常见失败表现：**
- 模型把格子间的空隙当成画面内容（如白边/分割线被动画化）
- 角色和场景在格子之间"乱窜"，不按 Frame 1→2→3 的顺序
- 画面只动了其中一格，其他格子静止或模糊
- 分镜被重新构图，变成一幅"画面"而不是分格叙事

**解决思路：降低模型的自由度，提高每格描述的精确度。**

### 三种方案对比

| 方案 | 适用场景 | 效果可控度 | 工作量 |
|------|---------|-----------|-------|
| **🅰 裁剪单格 → 逐格生成 → 剪辑拼接** | 需要每格精确对应 | ⭐⭐⭐⭐⭐ | 每格一条提示词 + 后期剪辑 |
| **🅱 严格约束提示词** | 整图输入，强控约束 | ⭐⭐⭐ | 一条提示词 + 可能需多次调优 |
| **🅲 Reference-to-Video（多参考图）** | 有每格原图，风格引导 | ⭐⭐⭐⭐ | 裁7张图 + 一条提示词 |

### 🅰 逐格生成（最推荐）

把多格图裁剪成单张，每张分别做图生视频（1-2s），最后剪辑拼接。

**每格提示词示例（猫鼠分镜）：**
```text
Frame 1 — Tabby cat crouches on wooden floor, eyes fixed on small mouse standing alert.
Subtle tail tip flick, soft window light shifts across fur.
Keep cat's tabby stripes, white paws, warm wood grain. No text. 9:16.
```

### 🅱 严格约束提示词（整图输入）

用极详细的约束告诉模型"别动格子布局，只做微动画"。

#### 变体1：详细行/列描述版

**公式：**
```text
This image is a [N]x[M] storyboard — [N×M] independent scenes arranged in a grid.
DO NOT re-compose or merge the frames. Treat each cell as a fixed, separate shot.

Animate them in reading order ([left-to-right, top-to-bottom]):

Row 1 (top):
  Cell 1 — [精确描述格子1的内容：角色+姿势+场景].
  Cell 2 — [精确描述格子2的内容].
  ...

Row 2 (middle):
  ...

Row 3 (bottom):
  ...

PRESERVE AT ALL COSTS — do not change:
- [角色1]: [关键视觉特征]
- [角色2]: [关键视觉特征]
- [环境元素列表]
- [颜色/光线风格]

Add only subtle motion within each cell: [允许的微动作].
No new objects, no extra characters, no text, no captions.
[Aspect ratio], total [N] seconds.
```

**区别（对比上面的普通故事板提示词）：**
- `DO NOT re-compose or merge the frames` — 强制锁定格子布局
- `Row 1 / Row 2 / Row 3` 明确分区，不只靠 `Frame N —`
- `PRESERVE AT ALL COSTS` 列表式约束，每条都显式列出要保护的元素
- `Add only subtle motion within each cell` 限制动画幅度
- 适合 Grok 容易跑偏的场景

#### 变体2：网格位置锁定 + 简洁时间轴（用户偏好格式）

> ⚠️ **用户偏好规则：每次写分镜提示词必须优先使用此格式。** 不要用长篇段落描述每格内容。每行严格控制在 12 个词以内。运镜只需 1-3 个词。用户明确要求「简洁明了」，长篇描述会被要求重写。

用户偏好的格式：**时间轴 | 网格位置 | 主体+动作 | 运镜**，要求语言极简，避免任何啰嗦描述。

```text
CRITICAL: [N]×[M] storyboard grid. N fixed panels. Do not re-compose or merge.
Each time segment maps to its grid cell position:

Row 1: Panel 1 (left 0-33%, top 0-50%) | Panel 2 (center 33-66%, top 0-50%) | Panel 3 (right 66-100%, top 0-50%)
Row 2: Panel 4 (left 0-33%, bottom 50-100%) | Panel 5 (center 33-66%, bottom 50-100%) | Panel 6 (right 66-100%, bottom 50-100%)

--- TIMELINE ---

T=0s→1s | Panel 1 | Cat crouches on left, mouse stands alert. Tail flick. | Static.
T=1s→2s | Panel 2 | Cat reaches paw, mouse sprints right. | Micro-jitter.
T=2s→3s | Panel 3 | Cat at baseboard, mouse peeks from hole. Ear twitch. | Static.
T=3s→4s | Panel 4 | Cat walks, mouse pauses looking back. | Low angle track.
T=4s→5s | Panel 5 | Cat under tablecloth, mouse passes. | Medium shot.
T=5s→6s | Panel 6 | Cat leaps, mouse escapes below. | Freeze at apex.

--- RULES ---
[Subjects] consistent throughout. [Environment details]. Grid borders stay fixed. No text. [Aspect ratio], [N]s total.
```

**关键格式要素：**
- `T=Ns→Ns | Panel N | 主体 + 动作 | 运镜` — 每行只写三个信息块
- 运镜只需 1-3 个词（Static / Low angle track / Medium shot / Freeze at apex）
- 动作描述精简到 1 个短句
- 结尾统一写约束规则，不要在每个时间行重复

### ⚡ 用户偏好：提示词必须简洁

> 用户明确要求「太复杂」「简洁一些」「简洁明了」。每次写提示词优先使用**极简格式**，拒绝长篇描述。

**必须遵守的原则：**
- 时间轴 | 网格位置 | 主体+动作 | 运镜 — 每行只三个信息块
- 运镜只用 1-3 个词（Static / Low angle track / Medium shot / Freeze）
- 动作描述精简到 1 个短句（Cat reaches, mouse sprints right）
- 不要在每个时间行重复约束，统一写在末尾 RULES 区
- 不要加解释性文字、不要评论提示词写得好不好、不要分析可行性
- 直接输出提示词正文，不要加「这是我写的…」「你可以尝试…」之类的废话

### 🅲 Reference-to-Video（多参考图）

当你有**多张独立图片**（非拼贴图）时，xAI 提供 Reference-to-Video API，通过 `@Image1`-`@Image7` 引用最多**7张参考图**。

#### 核心语法（基于 Venice API Docs 官方指南）

**公式：**
```
[主体 with @Image tag] + [动作] + [环境 with @Image tag] + [运镜] + [光线/风格]
```

**官方示例：**
```text
@Image1 and @Image2 walking together through a sunlit park, camera slowly tracking alongside them, warm afternoon light.
```
```text
A @Image1 running through a sunlit meadow, cinematic slow motion.
```

**多图序列示例（猫鼠6格）：**
```text
@Image1 Tabby cat crouches on left, mouse stands alert.
@Image2 Cat extends paw, mouse sprints right.
@Image3 Cat at baseboard corner, mouse peeks from round hole.
@Image4 Cat walks near green cabinet, mouse pauses.
@Image5 Cat under checkered cloth, mouse passes by.
@Image6 Cat leaps mid-air, mouse escapes below.

Smooth continuous sequence. Gentle cross-dissolve between each scene.
Keep tabby cat (white paws, gray stripes), warm wood floor, soft window light.
No text, 9:16, 6 seconds.
```

#### ⚠️ 重要限制

| 特性 | 支持情况 |
|------|---------|
| 引用图片数 | 1-7 张 |
| Multi-Shot（多镜头分场景） | ❌ **不支持** — 仅 Kling O3 R2V 支持 |
| 音频生成 | ❌ 不支持 |
| Elements（角色身份锚点） | ❌ 不支持 |
| 过渡效果 | ✅ 自动平滑混合（无精确转场控制） |

> **注意：** Grok R2V 不支持多镜头分场景模式。它生成 **1 段连续视频**，`@Image1→@Image6` 的过渡是模型自己决定的平滑动画，不保证按帧精确对齐。需要精确时间控制 → 方案🅰（剪单格逐条生成）。

#### 与图生视频的关键区别

| 模式 | 传图方式 | 图片作用 | 帧锁定 |
|------|---------|---------|-------|
| Image-to-Video | 传1张图 | 图片 = 起始帧 | ✅ 完全锁定 |
| Reference-to-Video | 传1-7张图 | 图片 = 风格/内容参考 | ❌ 不锁定起帧 |

**使用要点：**
- 在 Prompt 中用 `@Image1`、`@Image2` 引用各参考图
- 图片影响风格和主体身份，但不保证精确帧对齐
- API 端点：`xai/grok-imagine-video/reference-to-video`
- 仅 API 调用（Grok 网页端可能有不同的入口）

**提示词示例（裁成7张单图上传）：**
```text
@Image1 cat crouches on wooden floor as mouse stands alert.
Transition to @Image2 cat extends paw toward running mouse.
Transition to @Image3 cat at corner, mouse peeks from hole.
Transition to @Image4 cat walks near green cabinet, mouse pauses.
Transition to @Image5 cat hides under checkered tablecloth.
Transition to @Image6 cat leaps in mid-air toward escaping mouse.
Transition to @Image7 cat gently holds mouse in embrace.
Keep tabby cat with white paws, gray-brown mouse, warm wooden floor consistent.
Soft natural lighting, no text, 9:16, 10 seconds total with smooth transitions.
```

### 🅲 多镜头序列语法（Multi-Shot）

> ⚠️ **重要区分：** Grok Imagine 网页端支持 Multi-Shot，但 Grok R2V API 不支持。以下模板适用于 **Text-to-Video 模式**（无参考图时）。

Grok 支持在一个提示词内用 `Scene + camera switch` 描述多段镜头切换，适合**单一场景内的镜头变化**（不适合跨越不同场景的分镜）。

```text
Scene 1: Wide shot of city skyline at dusk;
camera switch: close-up on protagonist's face with neon reflections;
camera switch: tracking shot following the protagonist walking through crowd.
Keep the cyberpunk aesthetic, blue-purple color palette, vertical 9:16, 8 seconds, no text.
```

**适用边界：**
- ✅ 同一场景的不同景别/角度切换
- ❌ 不同时间/地点的分镜跳转（这种情况用方案🅰或🅱）
- ❌ 9宫格分镜（模型不理解格子布局，用🅰或🅱）

### 🅴 Extend from Frame（链式拼接 — 官方推荐长视频方案）

Grok Imagine 的 **Extend from Frame** 功能允许将多个视频片段拼接为长序列。选取一段生成视频的**最后一帧**作为下一段的起始帧，模型会承载角色位置、光线、动作进入下一段。

**适用场景：** 多段故事板逐段生成、分镜逐格推进、需要精确控制的叙事短片。

**工作流：**
1. 生成第 1 段视频（单格 Image-to-Video，1-2s）
2. 取该段最后一帧作为起始帧，用 `EXTEND FROM FRAME` 模式
3. 写第 2 段的动作+运镜提示词
4. 重复至所有分段完成
5. 后期拼接合并

**提示词示例（第 1 段 → 延展至第 2 段）：**
```text
# 第1段（单格图生视频）
Tabby cat crouches on floor, tail tip flicks. Soft window light, no text, 9:16.

# 使用 Extend from Frame，第2段提示词：
Cat extends paw forward, mouse darts to the right. Continue same lighting, same composition.
Keep cat's white paws and gray-brown stripes. No text, 9:16.
```

**优势：**
- 每段视频独立生成，精确可控
- 角色/光线/构图在段间保持连续
- 适合教学、产品多角度、分镜叙事

**限制：**
- 每段 ≤ 15 秒
- 延展后总时长仍受平台限制

### 🅵 时间戳式多场景提示词（Scene + 时间戳）

针对**产品广告、多段故事情节**（单一场景内多段内容变换），community 验证最有效的格式是显式写时间戳：

```text
Scene 1 (0–3s): Device rotating on desk with neon reflections, text overlay.
Scene 2 (3–7s): User interacts with holographic UI, real-time smart responses.
Scene 3 (7–11s): Fast montage of travel, productivity, and lifestyle scenarios.
Scene 4 (11–15s): Hero shot — product glowing in dark environment, logo reveal.

Style: cinematic, luxury tech ad, futuristic, viral TikTok ad, realistic lighting, smooth transitions, high contrast, modern minimal aesthetic. 9:16, no text on final hero frame.
```

**关键格式要素：**
- `Scene N (X–Ys)` — 精确到秒的时间区间
- 每段只描述该时间段内发生的事
- 结尾定义统一的风格基调
- 适合**文生视频**（无输入图时），或**图生视频+风格参考图**

**与普通 Multi-Shot 对比：**

| 特性 | 普通 Multi-Shot | Scene+时间戳 |
|------|----------------|-------------|
| 时间控制 | 模糊（"then"、"after"） | 精确到秒 |
| 段数限制 | 2-3段 | 4-5段 |
| 内容密度 | 每个场景一句话 | 每段可加细节 |
| 适用场景 | 单一连续动作 | 多段故事/广告 |

## 🎬 真实社区提示词风格指南

> 基于 [awesome-grok-imagine-prompts](https://github.com/YouMind-OpenLab/awesome-grok-imagine-prompts) 社区 1638 个实测提示词总结。完整画廊: [YouMind Gallery](https://youmind.com/grok-imagine-prompts)

### 两大风格流派

**1. 简短直接派（1-2 句话）**
最常见的风格。直接告诉模型要做什么，`@image1` 指代上传的图片。

```
@image1 walks slowly. She stops and can observe how Starship V3 rises into the sky.
```
```
@image1 riding, dust rises, a small Starship V3 rocket ascends behind her.
```
```
@image1 drinking coffee. Look at the camera and say second coffee of Saturday.
```
```
The young dark haired woman sits inside her Time Machine.
```

> ✅ 适合: 图生视频、简单动作、日常场景
> 要点: 动作动词（walks/rides/drinks）比形容词更关键

**2. 长篇叙事派（3-5 句详细描述）**
完整描绘故事线、人物、环境、时间线。

```
Realistic cartoon style video of Hachiko's true 1920s Tokyo life:
the Akita puppy born in 1923 in Odate, adopted by Professor Ueno in 1924,
their daily walks to Shibuya Station, Ueno's sudden death in 1925 from
cerebral haemorrhage, and Hachiko's real, faithful wait every day for
nearly 10 years until his passing in 1935 amid crowds and seasons.
```
```
A mystical and noble celestial Valkyrie, a dignified and beautiful woman
with long silver hair and shining armor, holding a holy spear, descending
to Earth in the moment, with rain pouring down, radiating a solemn and
sacred aura as she is drawn down from Valhalla in a fantastical appearance.
```

> ✅ 适合: 文生视频、故事性强的场景、需要氛围渲染
> 要点: 时间线+动作链+感官细节（雨水、光晕、声音）

### @image1 引用语法

上传图片的图生视频场景下，用 `@image1`（或多个 `@image1`, `@image2`）指代上传的参考图。提示词专注于动作而不用再描述主体外貌。

### 常见场景分类

| 场景 | 典型长度 | 风格特点 |
|------|---------|---------|
| 产品广告 | 1-2 句 | 保持产品外形+颜色+标签 |
| 角色短片 | 1-3 句 | @image1 + 动作 + 环境 |
| 电影场景 | 2-4 句 | 人物+氛围+运镜 |
| 特效/灾难 | 1-2 句 | 动态描述+镜头视角 |
| 风格转换 | 1-2 句 | Turn into [风格]+保持主体 |
| 故事叙事 | 3-5 句 | 时间线+人物+情感细节 |

### 精选提示词案例（YouMind Gallery Top）

详见 `references/awesome-grok-prompts.md` 完整收录。

## 🎬 agent-k1skt8 实战 Grok 提示词库

> 用户此前在 OpenClaw (`agent-k1skt8`) 中积累了 11 组生产级 Grok 提示词。
> 详见 `references/grok-prompts-from-agents.md`

涵盖场景：
- 写实武打（屋顶对决、地下拳赛）
- 3D日漫（打斗、追逐）
- 赛博城市（机器人觉醒、街头追逐）
- 喜剧（兄弟争糖、出租车斗嘴）
- 产品广告（鼠标8s、吐槽种草45s）
- 奇幻（美女与野兽、夏日穿梭）

**文件命名惯例：**
- `{项目名}-grok-提示词.md` → 纯 Grok 视频生成提示词
- `{项目名}-分镜故事板.md` → 文字分镜脚本
- `{项目名}-{版本}-分镜设计图提示词.md` → 分镜设计图 AI 生成提示词

## 🔬 各模型对故事板/宫格图的识别能力对比

> 2026年5月实测 + 社区报告汇总。用于在 Grok 效果不佳时快速建议用户切换模型。

### 宫格图（多格拼贴）支持一览

| 模型 | 支持宫格图 | 方式 | 开源 | 国内可访问 |
|:----|:--------:|:-----|:---:|:---------:|
| **Grok Imagine** (Image-to-Video) | ❌ | 整图当一幅画，不分格 | ❌ | ❌ GFW |
| **Grok R2V** (@Image1-7) | ⚠️ 多图风格参考 | 1-7张图平滑混合，不逐帧对齐 | ❌ | ❌ GFW |
| **Wan 2.7** | ✅ **3×3 Grid Storyboard** | 原生 multi-grid reference-video | ❌ API only | ✅ DashScope |
| **Seedance 2.0** | ✅ **`@[storyboard_image]` 语法** | 明确面板 = 顺序关键帧 | ❌ | ✅ 字节跳动 |
| **Veo 3.1** | ⚠️ 首尾帧控制 | 1-3张参考图 + 开始/结束帧 | ❌ | ❌ |
| **Kling O3 R2V** | ✅ **原生 Multi-Shot** | `Shot 1 (5s): @Element1...` | ❌ | ⚠️ 部分 |
| **Wan 2.2** (开源) | ❌ | 仅单图I2V/T2V/首尾帧 | ✅ | ✅ |

### 用户选择指南

当用户问"Grok 不支持宫格图怎么办"时依次建议：

1. **裁单格 → 逐条 Image-to-Video → 剪辑**（最稳，通用方案）
2. **换 Wan 2.7 reference-video API**（如果用户有 DashScope 额度）
3. **换 Seedance 2.0 的 `@[storyboard_image]` 语法**
4. **Grok R2V @Image1-7**（接受转场不可控的风险）

### Seedance 2.0 故事板提示词语法

来源：[awesomevideoprompts.com](https://awesomevideoprompts.com/prompts/2026-05/2050607320950829551-sketch-storyboard-seedance/)

```text
Use the provided previs storyboard page @[storyboard_image] as the main reference.
Do not treat the page as one single image.
Treat the panels as sequential shot keyframes and expand them into a coherent short scene with clear continuity.
Use the @[character_sheet_image] as characters reference.
Match the storyboard's spatial variety and emotional pacing.
```

**关键点：**
- `@[storyboard_image]` 引用故事板图
- `@[character_sheet_image]` 引用角色设定图
- `Do not treat the page as one single image` — 明确告诉模型"这是多格图"
- `Treat the panels as sequential shot keyframes` — 面板 = 关键帧序列

### Wan 2.7 Multi-Grid Storyboard API

来源：[Evolink Wan 2.7 API Guide](https://evolink.ai/blog/wan-2-7-api-guide)

```json
{
  "model": "wan2.7-reference-video",
  "prompt": "Reference image. 3D cartoon style. 1. Wide shot of fantasy forest..."
}
```

- 支持 3×3 grid 参考图 → 单一连续视频
- 国内通过阿里云 DashScope 调用
- 闭源，API only

### PopcornAI Smart Multi-Frame Engine

另一款支持多图故事板的工具：

> "Allows you to upload up to 9 reference images as guideposts on a timeline."
> "Unlike standard Image-to-Video tools that guess the next frame..."

### Extend from Frame 链式拼接（Grok 官方推荐的长视频方案）

来源：[romptn.com 日文实测文章](https://romptn.com/article/105422)、[Puppetry Guide](https://www.puppetry.com/posts/how-to-create-grok-imagine-videos-an-ultimate-beginner-s-guide-2026)

Grok Imagine 网页端的功能。选取一段视频的最后一帧作为下一段的起始帧。

**3点模板（来自日本社区）：**

① **最终帧选择** — 选"动作中途"的帧（不要完全静止），人物在帧内完整、光源方向明确
② **下一段提示词** — 必须写明三个继承：**主体维持**（same character） + **运镜继承**（camera continues） + **光源色温**（keep lighting）
③ **跳跃检查** — 连接完检查三类跳跃：场景跳跃（背景突变）/ 主体跳跃（角色变了）/ 光影跳跃（颜色偏了）

**提示词格式（第2段以后）：**
```text
[Subject description] continues the motion from previous clip.
[Action] happening next.
Same character, same outfit, same hairstyle.
Camera continues [direction/movement].
Keep [lighting type] lighting, [color temperature] color temperature.
[New action details].
No text, 9:16.
```

**注意事项：**
- 1段 ≤ 15秒
- 3-5段链式拼接最稳定
- 每段提示词必须重复角色特征描述（Grok 不记忆前一段的内容）
- 同一项目的提示词措辞要保持一致

## 🔗 相关资源

- xAI Imagine Overview: https://grok.com/imagine
- xAI Pricing: https://console.x.ai
- PixVerse Grok Guide: https://pixverse.ai/en/blog/grok-imagine-now-available-on-pixverse
- **awesome-grok-imagine-prompts (1638 prompts)**: https://github.com/YouMind-OpenLab/awesome-grok-imagine-prompts
- **YouMind Gallery (带视频预览)**: https://youmind.com/grok-imagine-prompts
- **YouMind 中文版**: https://youmind.com/zh-CN/grok-imagine-prompts
- **xAI Video Generation API**: https://docs.x.ai/developers/model-capabilities/video/generation
- **Reference-to-Video API (fal.ai)**: https://fal.ai/models/xai/grok-imagine-video/reference-to-video
- **Grok Video Prompt Guide**: https://grokvideo.ai/blog/top-grok-video-prompts-2026
- **Awesome Video Prompts (229 Grok prompts)**: https://awesomevideoprompts.com/?model=Grok
- **Grok Multi-Shot Template**: https://awesomevideoprompts.com/prompts/2026-02/2019612775660576768-grok-multi-shot-video-prompt-template/
- GaiaVideoFactory (本地): 参照 `video-director-workflow` skill 中 Gaia 相关配置
