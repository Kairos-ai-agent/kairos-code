---
name: "ai-storyboard-artist"
description: "AI驱动的分镜与故事板创作工作流。将文字剧本转化为可视化分镜、生成关键帧prompt、设计镜头语言。适用于短剧、漫剧、TVC、电影故事板。与ComfyUI/Flux 2 Klein 4B集成生成关键帧图像。"
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/creative/ai-storyboard-artist/SKILL.md"
---
# AI分镜师 · AI Storyboard Artist

## 角色定位
将文字剧本转换为可视化分镜图，生成可用于AI图像生成工具（ComfyUI/Flux/Midjourney）的关键帧提示词。

## 核心流程
```
文字剧本 → 镜头拆解 → 画面描述 → Prompt生成 → 图像生成 → 分镜板组装
```

## 第一步：镜头拆解

### 剧本→镜头转换规则
| 剧本元素 | 转换为 |
|---------|--------|
| 动作描述 | 对应的视觉镜头 |
| 对话 | 过肩/近景/特写 + 说话者 |
| 情绪变化 | 特写 + 面部表情描述 |
| 环境描写 | 全景/交代镜头 |
| 时间变化 | 转场镜头（叠化/黑场/闪白） |
| 关键信息 | 插入特写（手机/文件/物品） |

### 一集短剧的典型镜头分布（90秒/约15-20个镜头）
```
第1-2镜：钩子（特写/倒叙闪回）
第3-5镜：场景建立（中景+环境）
第6-10镜：对话推进（过肩/中近景交替）
第11-13镜：冲突爆发（快切/特写）
第14-16镜：小高潮（慢动作/推镜）
第17-18镜：过渡/铺垫
第19-20镜：结尾钩子（悬念特写+黑场）
```

## 第二步：画面描述写作

### 画面描述规范
```
【镜头号】 【景别】 【运镜】 | 【时长】

画面内容：人物位置、动作、表情、环境细节、道具
光线氛围：自然光/戏剧光/逆光/剪影/霓虹
色彩调性：暖色调/冷色调/高饱和/低饱和/黑白
构图参考：居中/三分法/对角线/框架构图

参考风格：[艺术家/电影/风格关键词]
```

### 示例
```
【镜05】 中近景 → 过肩 | 快速推 | 6s
画面：主角（左侧背景虚化）面对反派（右前方），主角眼神锐利
手中文件缓缓放在桌上。窗外阴天，室内冷调。
光线：侧逆光打在主角侧脸，形成明暗对比。
构图：对角线构图，主角占画面1/3，反派背影占2/3。
色彩：低饱和，青灰调。
```

## 第三步：AI图像生成Prompt

### Flux 2 Klein 4B Prompt语法
```
[主体], [动作/姿态], [环境/背景], [光线], [色彩], [风格], [画质]
```

### 景别Prompt对照表
| 景别 | Prompt关键词 | 效果 |
|------|-------------|------|
| 远景/全景 | wide shot, full body, environmental portrait | 展现环境与人物关系 |
| 中景 | medium shot, waist up | 对话/动作标准镜头 |
| 中近景 | medium close-up, chest up | 专注人物表情与动作 |
| 特写 | close-up, extreme close-up, macro shot | 面部微表情/物品细节 |
| 过肩 | over-the-shoulder shot, OTS | 对话场景标准 |
| 俯拍 | high angle shot, bird's eye view | 弱势/压制感 |
| 仰拍 | low angle shot, worm's eye view | 强势/威严感 |

### 风格Prompt库
```
# 甜宠/偶像剧
"cinematic lighting, soft warm tones, shallow depth of field, romantic atmosphere, glowing skin, pastel color palette"

# 赘婿/逆袭
"dramatic chiaroscuro lighting, high contrast, cool blue shadows, warm key light, cinematic composition, gritty texture"

# 战神/打斗
"dynamic action shot, motion blur, dramatic lighting, cinematic color grading, anamorphic lens flare, high contrast"

# 古装穿越
"epic cinematic shot, warm golden hour light, atmospheric haze, traditional Chinese architecture, rich colors, textured fabrics"

# 现代都市
"clean modern aesthetic, natural lighting, realistic skin texture, urban setting, shallow depth of field, neutral color palette"

# 漫剧/2D动画风
"2D anime style, cel shading, clean line art, vibrant colors, flat lighting, anime aesthetic, key visual style"

# 3D国漫风
"3D CG animation style, Chinese donghua style, realistic rendering, subsurface scattering, volumetric lighting, epic composition"
```

### 角色一致性技巧
```
每张图嵌入角色描述保持一致性:
[角色名], [性别], [年龄], [外貌描述], [服装描述], [发型], [特征]
```

## 第四步：分镜板组装

### 输出文件结构
```
storyboard/{project-name}/
├── script/                     # 原始剧本
│   └── script_s01.md
├── breakdown/                  # 镜头拆解
│   └── scene_01_breakdown.md
├── prompts/                    # AI生成prompt
│   ├── prompts_01_keyframes.md
│   └── prompts_01_styles.md
├── frames/                     # 生成的关键帧
│   └── s01-e01-{NN}-{shot}.png
└── storyboard.md               # 完整分镜板文档
```

## 常见镜头语言速查
| 目的 | 推荐的镜头 |
|------|-----------|
| 建立场景 | 全景/远景 + 横移/航拍 |
| 展示角色 | 中景 + 缓慢推近 |
| 情感表达 | 特写 + 浅景深 |
| 紧张感 | 快速推镜 + 倾斜构图 |
| 力量对比 | 俯仰对切 |
| 悬念揭晓 | 慢速拉远 + 广角 |
| 动作场面 | 快切 + 运动模糊 + 低角度 |
| 浪漫时刻 | 柔焦 + 逆光 + 慢动作 |

## ComfyUI/Flux工作流集成

### 关键帧生成流程
1. 使用`short-drama-scriptwriting` skill产出剧本
2. 用本skill拆解镜头、写画面描述
3. 生成ComfyUI API格式prompt
4. 使用`comfyui` skill + Flux 2 Klein 4B生成图像
5. 需要风格LoRA自动加载 → 参考comfyui skill的`references/flux-klein-lora-guide.md`
6. 组装分镜板

### Prompt注入参数模板
```json
{
  "prompt": "[画面描述 + 风格prompt + 角色描述]",
  "negative_prompt": "ugly, deformed, blurry, low quality, bad anatomy, watermark, text",
  "seed": -1,
  "steps": 30,
  "cfg": 3.5,
  "width": 1080,
  "height": 1920
}
```

## 分镜设计图提示词母版库

> 在 OpenClaw agent `agent-k1skt8` 中积累了 40+ 个实战母版文件。
> 详见 `references/storyboard-design-templates-library.md`

包含 6 套母版布局（深色电影感/白底商业/情景喜剧/竞速/数据/二次元）、
角色/场景/道具 3 套资产模板、以及 20+ 实战案例。

**核心起手公式（深色底）：**
```
A cinematic production design board for a [PROJECT TYPE] project,
presented as a complex multi-panel infographic pitch deck.
Background: deep charcoal #0A0A0C with 2-3% film grain noise.
Accent color: antique gold gradient. Double-line gold frame with corner filigree.
```

**核心起手公式（白底商业）：**
```
A professional storyboard master production sheet, landscape 16:9,
clean white background. 3-4 section vertical layout.
Thin borders, sans-serif typography, generous whitespace.
```

选择母版：详见 `references/storyboard-design-templates-library.md`

## 用户偏好（古武行者项目）
- **交付速度第一** — 尽快给出完整结果，不要解释步骤或分期交付
- **一次给全** — 不要"先做一个看看效果"，一次完成所有资产
- **真功能** — 工具必须真实可用（调用 API 生图），不能用占位图/模拟器
- **写实仿真人 + 5%赛博** — 所有资产统一风格，竖屏 9:16

## 参考
- `short-drama-scriptwriting` skill → 剧本来源
- `comfyui` skill → 图像生成执行
- `tvc-director` skill → TVC镜头语言
- `grok-imagine-prompt` skill → 分镜图→Grok视频提示词转换
- `references/storyboard-design-templates-library.md` → 完整母版库
- `references/asset-design-templates-a版-quickref.md` → 资产设计图A版母版快速参考（角色定妆图/场景设计图/道具设计图结构+四变体选择+检查清单）
- `references/character-design-sheet-generation.md` → 角色定妆图生成器架构（母版模板 → API调用 → Pillow拼版 → PNG输出）
