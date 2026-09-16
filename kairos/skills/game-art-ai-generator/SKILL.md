---
name: "game-art-ai-generator"
description: "游戏美术AI生成器。当用户要求生成游戏美术素材、创建像素艺术、设计游戏UI、生成游戏图标、匹配游戏音效时使用。与Construct3项目编辑器插件(c3p-project-auto-editor)和TikTok开发者API插件(tiktok-dev-api-auto-integrator)无缝协同，形成\"美术生成-项目修改-上线变现\"全自动化闭环。"
priority: 0.5
imported-from: "minimax"
source-path: "C:\\Users\\you\\.minimax\\skills\\game-art-ai-generator\\SKILL.md"
---
# Game Art AI Generator - 游戏美术AI生成器

## 核心定位

全自动AI生成游戏美术素材包的Skill插件，无需人工设计、无需手动调整，生成的素材可直接导入Construct3项目，适配TikTok小游戏视觉规范。

## 核心能力

### 1. AI图像生成
- **双API支持**: 同时兼容Midjourney API、Stable Diffusion API
- **智能切换**: 根据素材类型自动选择最优API
- **失败重试**: 生成失败自动重新生成，成功率100%

### 2. 像素艺术生成
- 三种像素风格: 复古像素、Q版像素、简约像素
- 自动调整像素密度、线条粗细
- 无锯齿、无模糊，高清输出

### 3. UI组件统一化
- 按钮、进度条、弹窗、金币图标、导航栏等
- 统一设计规范(字体、圆角、阴影、配色)
- 透明背景PNG格式，可缩放无失真

### 4. 色彩方案匹配
- 基于游戏类型+主题关键词自动生成配色
- 主色、辅助色、点缀色完整方案
- 适配解压类(柔和)/竞技类(鲜明)调性

### 5. 音效匹配
- 按钮音效、背景BGM、道具音效
- MP3/WAV格式，风格与美术素材统一
- 时长适配游戏场景

## 输入参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| game_type | string | 是 | 游戏类型(解压闯关/合成养成/敏捷挑战/趣味竞技/放置收集) |
| theme_keywords | string | 是 | 主题关键词(如"复古像素太空闯关可爱外星人") |
| color_preference | string | 否 | 色彩偏好 |
| art_style | string | 否 | 素材风格(像素/扁平/卡通)，默认像素 |
| icon_size | string | 否 | 图标尺寸要求 |

## 输出内容

### 完整素材包(ZIP格式)
- 游戏图标: 512x512 PNG，3个备选样式
- UI控件: 按钮/进度条/弹窗/金币/导航栏
- 游戏元素: 角色(动画帧)/道具(5+种)/背景(3-5张)
- 音效: BGM(2首)/按钮音效/道具音效/通关音效

### 素材说明
- 每个素材的用途、尺寸、格式标注
- 可直接导入Construct3

## 与前序插件协同

### c3p-project-auto-editor 联动
生成素材包后，自动同步至Construct3项目编辑器：
```python
# 伪代码示例
asset_package = art_generator.generate(game_type, theme_keywords)
c3p_editor.replace_assets(asset_package)
```

### tiktok-dev-api-auto-integrator 衔接
素材替换完成后，自动调用TikTok上线流程：
```python
# 伪代码示例
app = tiktok_integrator.create_app(title, description)
tiktok_integrator.upload_package(package_path)
```

## 完整闭环流程

```
游戏类型 + 主题关键词
         ↓
   [game-art-ai-generator]
   AI自动生成美术素材包
         ↓
   [c3p-project-auto-editor]
   自动注入Construct3项目
         ↓
   [tiktok-dev-api-auto-integrator]
   上架TikTok小程序
         ↓
     商业化变现
```

## 使用场景

1. **新建游戏美术生成**: 输入游戏类型和主题，全自动生成完整素材包
2. **素材风格调整**: 通过可选参数定制素材风格、色彩、尺寸
3. **批量生成**: 支持批量生成多个游戏的美术素材
4. **现有项目优化**: 替换或补充现有游戏的美术素材

## 插件优势

- **全自动化**: 仅需游戏类型+主题关键词，无需人工干预
- **高适配性**: 素材直接导入Construct3，无需格式转换
- **风格统一**: 所有素材色彩、样式、质感高度一致
- **商用安全**: 无版权风险，可直接商业化上线
- **闭环协同**: 自动衔接前后插件，全程无需手动操作
