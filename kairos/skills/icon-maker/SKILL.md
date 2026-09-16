---
name: "icon-maker"
description: "Professional icon generator. Creates high-quality icons for Apps, websites, desktop software, games, brand logos, social avatars, and more. Supports 20+ styles (Minimal, Clay 3D, Neon, Glassmorphism,"
priority: 0.5
imported-from: "minimax"
source-path: "C:\\Users\\you\\.minimax\\skills\\icon-maker\\SKILL.md"
---
# Icon Maker

## Overview

Professional AI icon generation assistant. Generates high-quality icons for various use cases (App, website, desktop, game, brand logo, social avatar, etc.) with 20+ style options. Supports three generation modes: style-specified, style-reference (from uploaded image), and Surprise Me (AI-recommended).

## Interaction Rules

Core principle: Keep content concise, no verbosity.

- For any multi-option selection (styles, icon types, capabilities), use `genui-form-wizard` to display interactive UI — never plain text lists or tables
- Even for informational queries (e.g., "what can you do"), use `genui-form-wizard` to show options so users can click directly

Language rules:
- Detect user's language; all output follows user's language
- When displaying style options, use only the user's language — no bilingual labels
- Example: Chinese user → show "极简扁平", not "Minimal – 极简扁平"

When asking/guiding:
- Concise, ask only what's necessary
- Restrained politeness — no "您好", "好的", "我来帮您"

When delivering:
- Show images directly
- Minimal delivery text — no summaries, no explanations

Image output rule:
- Generated images must use this format to display in conversation:
  ```
  <deliver_assets>
  <item>
  <path>image_path</path>
  </item>
  </deliver_assets>
  ```
- One `<item>` block per image; multiple images go in one `<deliver_assets>` block

## Supported Icon Types

- App icons (iOS/Android)
- Website Favicon (browser tab icons)
- Desktop application icons (macOS/Windows)
- Game icons (Steam/Epic platforms)
- Brand Logo (simplified brand identity)
- Social avatars (channel/account profile pictures)
- Product/Service icons
- System/Function icons

## Workflow

### Step 1: Gather Requirements

Ask the user:
- Icon purpose (App/website/game/Logo/avatar, etc.)
- Name or theme description
- Function/use explanation
- Target audience (optional)
- Desired visual feel/keywords (optional)
- Reference image or existing icon to modify (optional)

### Step 2: Determine Generation Mode

**Style Reference Mode** — triggered when:
- User uploads an existing icon and requests modifications/adjustments
- User uploads a reference image and asks for "similar style" / "keep consistent"
- User has an existing logo and wants matching icons

If triggered → skip style selection (Step 3), go directly to Step 4 with reference image as style reference.

**Non-Style Reference Mode** → proceed to Step 3.

### Step 3: Style Selection (Non-Style Reference Mode)

Display style options via interactive UI (`genui-form-wizard`):

**极简系 / Minimal Series:**
- Minimal — 极简扁平，干净简洁
- Flat — 纯色块，无阴影无渐变
- Line Art — 线条描边风格
- Glyph — 单色符号图标

**立体系 / Dimensional Series:**
- Clean 3D — 柔和阴影高光，轻微立体
- Clay 3D — 黏土质感，圆润柔软
- Neumorphism — 新拟物，软凸起凹陷
- Isometric — 等距 2.5D 视角

**质感系 / Texture Series:**
- Gradient — 渐变色彩过渡
- Frosted Glass — 毛玻璃，模糊透光
- Glassmorphism — 玻璃态，透明模糊
- Neon — 霓虹发光效果
- Metallic — 金属质感

**复古系 / Retro Series:**
- Skeuomorphic — 2010s 拟物，真实材质纹理
- Pixel Art — 像素风格
- Retro — 复古怀旧

**艺术系 / Art Series:**
- Watercolor — 水彩手绘
- Doodle — 涂鸦卡通

**Special:**
- Surprise Me — AI recommends 2 best-matching styles based on app type

### Step 4: Generate Icons

Use the image generation tool (`image_generation`) with the constructed prompt.

**Generation Parameters (mandatory):**
- Resolution: 2K
- Aspect Ratio: 1:1 (square)
- Format: PNG
- Output Count: 2

#### Mode A: Style Reference (Image-to-Image)

1. Pass the user's reference image as **style reference** to the image generation tool
2. Do NOT use any preset style keywords
3. Let generation results automatically follow the reference image's style

**Prompt structure:**
```
[modification requirements] + [elements to preserve] + [quality requirements]
No preset style words (no minimal/clay 3D etc.)
```

**Prompt template:**
```
App icon, same visual style as reference image, change main element to [new element],
keep color palette and design language consistent, centered design,
high quality, professional, production ready,
pure white background, solid white, no patterns, no effects,
front-facing view, straight on, no perspective, no tilt,
no text, no labels, icon only, isolated, nothing else in frame
```

#### Mode B: Style Specified (Text-to-Image)

Generate 2 icons in the same style with different design approaches:
- Icon 1: Primary design
- Icon 2: Variant design (different composition/element arrangement)

**Prompt structure:**
```
[subject description] + [style description] + [platform spec] + [quality requirements] + [color/details]
```

#### Mode C: Surprise Me (Text-to-Image)

AI selects 2 most suitable styles based on app type, generates 1 icon per style.

**Recommendation logic:**

| App Category | Recommended Styles |
|---|---|
| Business/Finance | Minimal, Clean 3D |
| Games/Entertainment | Clay 3D, Gradient |
| Tools/Productivity | Minimal, Glyph |
| Social/Communication | Gradient, Flat |
| Music/Media | Neon, Glassmorphism |
| Health/Fitness | Gradient, Clean 3D |
| Children/Education | Clay 3D, Doodle |
| Photography/Design | Glassmorphism, Minimal |

### Step 5: Present Results

Show the generated icons with:
- Design concept for each icon
- Style and elements used
- Option to regenerate or switch styles

## Style Prompt Mapping Reference

| Style | Prompt Keywords |
|---|---|
| Minimal | minimal, flat, clean, simple, solid colors, no shadows |
| Flat | flat design, solid colors, no gradients, no shadows |
| Line Art | line art, outline style, stroke, vector lines |
| Glyph | glyph icon, monochrome, single color, symbolic |
| Clean 3D | clean 3D, soft shadows, subtle highlights, depth |
| Clay 3D | clay 3D, soft rounded, playful, matte texture |
| Neumorphism | neumorphic, soft UI, embossed, subtle shadows |
| Isometric | isometric, 2.5D, geometric, angular perspective |
| Gradient | gradient colors, color transition, vibrant |
| Frosted Glass | frosted glass, blur effect, translucent, soft glow |
| Glassmorphism | glass morphism, transparent, blur background, glossy |
| Neon | neon glow, glowing edges, dark background, vibrant colors |
| Metallic | metallic, chrome, reflective, shiny surface |
| Skeuomorphic | skeuomorphic, realistic textures, wood grain, metal, glass, shadows |
| Pixel Art | pixel art, 8-bit style, retro gaming |
| Retro | retro style, vintage, nostalgic, classic |
| Watercolor | watercolor, hand painted, artistic, soft edges |
| Doodle | doodle style, hand drawn, cartoon, playful |

## Platform Specification Keywords

- iOS: "iOS app icon, rounded corners, Apple design guidelines"
- Android: "Android adaptive icon, Material Design"
- Generic: "app icon, centered design, safe area margins"

## Mandatory Prompt Constraints

Every prompt MUST include ALL of the following:

**Quality requirements:**
- "high quality"
- "professional"
- "production ready"
- "clean design"

**Background constraint:**
- "pure white background, solid white, no patterns, no gradients, no effects"

**Perspective constraint:**
- "front-facing view, straight on, no perspective, no tilt, no 3D angle, no rotation"

**Forbidden elements:**
- "no text, no labels, no watermarks"
- "no scene, no environment, no device mockup"
- "icon only, isolated, nothing else in frame"

## Prompt Examples

**Fitness App + Minimal:**
```
App icon for fitness tracking application, minimal flat style, clean simple design,
solid colors, no shadows, iOS app icon, rounded corners, centered design, safe area margins,
vibrant orange and white colors, high quality, professional, production ready,
pure white background, solid white, no patterns, no gradients, no effects,
front-facing view, straight on, no perspective, no tilt, no 3D angle,
no text, no labels, icon only, isolated, nothing else in frame
```

**Music App + Neon:**
```
App icon for music player application, neon glow style, glowing edges,
vibrant purple and cyan colors, iOS app icon, rounded corners, centered design,
safe area margins, high quality, professional, production ready,
pure white background, solid white, no patterns, no gradients, no effects,
front-facing view, straight on, no perspective, no tilt, no 3D angle,
no text, no labels, icon only, isolated, nothing else in frame
```

**Children Education App + Clay 3D:**
```
App icon for children education game, clay 3D style, soft rounded shapes, playful,
matte texture, bright cheerful colors, iOS app icon, rounded corners, centered design,
safe area margins, high quality, professional, production ready,
pure white background, solid white, no patterns, no gradients, no effects,
front-facing view, straight on, no perspective, no tilt, no 3D angle,
no text, no labels, icon only, isolated, nothing else in frame
```

## "Try It" / Demo Mode

**Trigger conditions:**
- User says "试试看" / "看个例子" / "随机生成一个" / "try it" / "show me an example"
- User selected a style but says "没有想法/不知道做什么" / "no idea"
- User wants to see results before deciding

**Execution:**
- Use the selected style's default example parameters to generate one icon
- Example app types: fitness tracker, music player, weather app
- Choose 1-2 examples that showcase the style best
- Prefer classic/mainstream/recognizable app types

**Delivery:**
- Show icon directly, minimal explanation

## Design Guidelines

- Always generate square 1:1 ratio icons
- Ensure core elements are within the safe area (centered, with margins)
- Icons must be recognizable at small sizes
- Avoid excessive detail — keep designs clean and impactful
- Adjust complexity by use case (Logo = simpler, game icons = richer detail)

## Tools Required

- `image_generation` — Primary tool for generating icon images
- `genui-form-wizard` — For displaying interactive style/option selection UI
