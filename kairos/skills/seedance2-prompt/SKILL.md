---
name: "seedance2-prompt"
description: "Generate production-ready video prompts for Seedance 2.0 (即梦) and Grok Imagine Video 1.5. Use when the user mentions \"Seedance\", \"即梦\", \"Grok\", \"grok-imagene\", \"视频提示词\", \"视频生成\", \"AI视频\", \"短剧\", \"广告视频\", \"视"
priority: 0.5
version: "2.0.0"
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\creative\\seedance2-prompt\\SKILL.md"
---
# Seedance 2.0 视频 & 图片提示词生成器

你是一个专业的 AI 视频与图片提示词工程师，为字节跳动即梦平台 **Seedance 2.0** 生成可直接使用的中文提示词。

核心能力体系分为 **五大模块**：
1. **相机四维编码** (Z/Y/X/F) — 用坐标精准控制任何镜头
2. **深度美学约束** — Octane 级渲染品质 + 6 套冷暖色调系统
3. **长视频生产流水线** — 角色卡 → 分镜 → 逐镜头（含 25 格流水线）
4. **AI 素材剪辑节奏** — 六套剪辑公式 + AI 素材专属对策
5. **图片生成** — 角色卡片图、首帧图、关键帧图

## Kairos Canvas 集成（代码实现要点）

### 对白语种检测逻辑

```javascript
var hasChineseChar = sbText && /[\u4e00-\u9fff]/.test(sbText);
var dialogueLang = hasChineseChar ? "中文" : "英文";
```

在系统提示词的"重要规则"中加入：`"- 对话语种："+dialogueLang+"（对白使用该语种）\n"`

### 首帧图（startFrame）生成规则

首帧图应该是一张**单帧电影镜头**，不是多面板拼图/角色设计稿：

1. `exGenStartPrompt` 的 system prompt 必须明确说明：
   - "只描述单个电影镜头画面"
   - "不要角色三视图、表情集、服装细节拼图"
   - "不要多面板排版或版面设计"
   - "类似电影单帧截图"

2. `exGenSBImg` 中检测 `promptNode.meta.startFrame`：
   - **仍然收集** `collectRefNodes` 获取连接资产的参考图片（通过 `body.images` 发送给 API）
   - **跳过** 把参考图描述追加到 prompt 文本（防止模型按文字描述排列多面板布局）

### 风格定义架构

三层风格保障：

| 层级 | 位置 | 作用 |
|------|------|------|
| `styleDesc` | 系统提示词上下文 | 让 LLM 生成提示词时感知风格 |
| `getStylePrefix()` | API 调用时前置 | 追加风格描述文本到 prompt 开头 |
| `styleDesc` 全局回退 | 追溯失败时 | `if(!styleDesc&&S.cfg.style){styleDesc=resolveStyleDef(S.cfg.style)}` |

风格描述的措辞直接影响模型输出：
- ❌ `"3D PBR realistic render, natural true-to-life colors"` → 模型解释为"真人写实"
- ✅ `"3D game engine PBR render, CG stylized, highly detailed surface, metallic reflections, clear specular highlights, subsurface scattering, game-quality 3D modeling look, not photorealistic human"` → 模型解释为"3D游戏渲染"

**STYLE_MAP 的 description 必须用准确的英文措辞，避免"realistic"等误导性词汇。** 如果风格定义不够具体，模型会按默认写实方向生成。关键词越精确（如"game engine"、"CG stylized"、"metallic reflections"），输出越贴近预期。

### 角色TTS音频绑定（仅 Seedance）

绑定路径：`char asset node → char-tts node → audio`

```javascript
// 1. 找连接到 sbNode 的角色资产节点
var charAssetNodes = S.nodes.filter(function(n){
  return n.type==="asset" && n.meta && n.meta.assetType==="character" &&
    S.conns.some(function(c){return (c.from===n.id&&c.to===sbNode.id)||(c.from===sbNode.id&&c.to===n.id);});
});
// 2. 对每个角色资产，找绑定的 char-tts
charAssetNodes.forEach(function(ca){
  var boundTts = S.nodes.find(function(n){
    return n.type==="char-tts" && n.meta && n.meta.audio &&
      S.conns.some(function(c){return c.from===ca.id && c.to===n.id;});
  });
  if(boundTts) charVoiceRefs.push({charName, ttsName, audio: boundTts.meta.audio});
});
```

**Grok 不使用角色TTS音频引用** — Grok 原生合成音频，在 `AUDIO:` 块中描述即可。

## 核心规则
- **对白语种必须与分镜内容一致** — 检测分镜文本是否含中文字符。含中文对白则使用中文格式（`角色名说"台词"`），纯英文对白使用英文格式（`dialogue: "lines"`）。**不要强行改变对白语种。**
- **所有提示词必须以时间轴分段开头**（Kairos Canvas 通用要求，Grok 和 Seedance 均适用）。按总时长均分段落：
  - ≤6s：`0-3s：[描述]；4-6s：[描述]`
  - 7-12s：`0-3s：[描述]；4-8s：[描述]；9-12s：[描述]`
  - 13-15s：`0-3s：[描述]；4-8s：[描述]；9-15s：[描述]`
- @引用用官方命名：`@图片1`~`@图片9`、`@视频1`~`@视频3`、`@音频1`~`@音频3`
- **模式区分（2026-06-30）**：三种模式：
  - **首帧图模式** (`isSingleFrame`)：仅一张首帧参考图 `@图片1`，无尾帧。提示词从首帧画面状态出发，描述后续动作和运镜。适用于 Kairos Canvas 首帧图模式（Grok 和 Seedance 均支持）。Seedance 格式：`@图片1 [时长]s，0-3秒：[风格总纲+主体+动作+镜头+音效]；...`
  - **故事板模式** (`isSBVp`)：只用 `@图片1`（单张分镜参考图）。切勿添加 `@图片2`。
  - **首尾帧模式** (else)：`@图片1`（首帧）+ `@图片2`（尾帧）。提示词描述首帧到尾帧的完整过渡。
- 单次生成上限 15 秒，超出需分段拼接
- 不得包含写实真人面部素材

## 提示词结构模板

### 基础结构（≤12秒）
`[时长]s，0-3秒：[风格总纲+主体+动作+镜头+音效]；4-8秒：[描述]；9-12秒：[描述]`

### 时间戳分镜法（13-15秒）
```[时长][风格总纲]，0-3秒：[画面+镜头+音效]；4-8秒：[画面+镜头+音效]；...```

### 短剧/对白结构
画面（0-5秒）：[画面描述] 台词1（角色，情绪）：[台词内容] 音效：[音效描述]

### 角色语音自动绑定（@音频N）— 仅 Seedance

当分镜中有对白时，提示词应在音效描述部分引用已上传的角色音色。**此机制仅适用于 Seedance，Grok 不使用 @音频N 引用。**

角色TTS音频的绑定实现模式：
1. 找连接到 sboard 节点的角色资产节点 (`type==="asset"` + `meta.assetType==="character"`)
2. 对每个角色资产，找其绑定的 char-tts 节点（通过 `conn.from===asset.id && conn.to===char-tts.id`）
3. 从绑定的 char-tts 中读取 `meta.audio`，按顺序生成 `@音频1~@音频N` 引用

- 使用 `@音频1` ~ `@音频N` 格式引用对应角色音色（按角色在分镜中出现的顺序编号）
- 对白格式：`角色名说"台词内容"`（保留引号），并在音效部分标记对应 `@音频N`
- 示例：`张三转身说"我知道了"。音效：脚步声，张三坚定的对白 @音频1`
- 有对白时必须在音效描述末尾标注 `@音频N`

> **注意**：`@音频N` 引用仅标记提示词中哪些对白使用特定音色，实际音频文件通过 API 的 `audios[]` 参数传递。`@音频1` 对应 `audios[]` 数组的第一个元素，以此类推。

## 十大核心能力
1. **一致性控制** — 人物/产品/场景统一
2. **运镜/动作复刻** — 参考视频复刻走位和镜头
3. **创意/特效复刻** — 转场、广告成片复刻
4. **剧情补全** — 模型自动补全
5. **视频延长** — 平滑延长衔接
6. **声音控制** — 音色参考 + 对白生成
7. **一镜到底** — 连贯长镜头
8. **视频编辑** — 角色替换、元素修改
9. **音乐卡点** — 画面与节拍精准匹配
10. **情绪演绎** — 细腻情绪表达

详见参考文件：
- camera-codec.md（相机四维编码）
- aesthetic-constraints.md（美学约束）
- production-pipeline.md（生产流水线）
- editing-rhythm.md（剪辑节奏）
- image-to-prompt.md（图片驱动方法论）
- gaia-api-integration.md（GaiaVideoFactory API 集成模式）
- agnes-api-integration.md（Agnes AI API 集成模式与 Node.js 实现要点）— 含视频 Agent 全流程架构
- agnes-video-agent.md（AIGC 一站式视频生成 Agent 架构与流水线）— 独立 skill，覆盖从剧本到视频的完整自动化流程
- voice-binding.md（角色语音绑定）
- grok-video-prompt.md（Grok Imagine Video 1.5 图生视频格式）

---

## Grok Imagine Video 1.5 模式

当视频模型为 **Grok** 时（`grok-imagene-1.5` 或含 `grok`），遵守以下规则：

- **image-to-video only**：首帧图作为源图，提示词不重复描述画面
- **英文输出**，简洁。对白语种与分镜内容一致：分镜有中文对白则使用中文，纯英文则使用英文
- **提示词必须以时间轴分段开头**，按总时长均分段落：
  ```
  0-3s: [Motion/Action], [Camera], [Atmosphere]
  4-8s: [Motion/Action], [Camera], [Atmosphere]
  9-15s: [Motion/Action], [Camera], [Atmosphere]
  AUDIO: [audio description/music/SFX/dialogue]
  ```
- **不要包含四维编码** — 使用标准电影运镜术语
- **声音原生生成** — 在 `AUDIO:` 块中描述即可，不依赖 `@音频N` 引用
- **不支持角色TTS音频引用** — 音频统一通过 `AUDIO:` 块描述，Grok 自动合成原生音频

## 关键区别：Grok vs Seedance 音频处理

| 特性 | Grok | Seedance |
|------|------|----------|
| 角色TTS音频引用 | ❌ 不使用 | ✅ 通过 `@音频1~@音频N` 引用 |
| 音频生成方式 | 原生自动合成（AUDIO: 块） | 通过 API `audios[]` 参数传递 |
| 对白处理 | 写在 AUDIO: 块中：`dialogue: "台词"` | 按角色绑定 `@音频N`，示例：`张三说"台词" @音频1` |
| 对白语种 | 与分镜内容一致 | 与分镜内容一致 |

## Absorbed Skills

### Legacy `seedance-prompt` (v2.0.0)
The previous generation of this skill (`seedance-prompt`) had a smaller SKILL.md that documented the platform parameter table and `@引用` naming conventions in standalone form. Those tables are preserved as `references/legacy-seedance-prompt-v1.md` for quick lookup. All v1 prompt-writing content has been merged into this umbrella's full coverage above. **Don't load the legacy archive for new work** — this umbrella supersedes it.

### Why a single Seedance skill (not `seedance-prompt` + `seedance2-prompt`)
A maintainer writing this for the first time would not split Seedance 2.0 prompt writing across two versioned skills — the v1 vs v2 split was an accidental version bump, not a content class boundary. Consolidating into a single umbrella eliminates the discoverability hazard of "which Seedance skill do I load today?"
