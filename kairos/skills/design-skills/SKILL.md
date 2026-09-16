---
name: "design-skills"
description: "External GitHub design skill landscape — curated map of UI/UX, anti-slop, motion, design-system, and critique skills for AI agents. Use when user asks to find design skills, wants anti-AI-slop UI qual"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\design-skills\\SKILL.md"
---
# Design Skills — GitHub Landscape Map

Curated reference of the best external design skills on GitHub. Organized by category.

## How to Use

When the user asks about design skills, UI quality, anti-slop, or external design resources:
1. Load this skill to get the landscape overview
2. Recommend based on user's specific need (see selection guide below)
3. Provide install commands

## Installation Pattern

Most of these use the `npx skills add <owner/repo>@<skill-name>` pattern.
For Claude Code / Cursor: clone the repo and copy the `.claude/skills/` or `SKILL.md` into your project.

## Network Constraint (2026-07-09)

**GitHub is unreachable from this machine** — all connections to github.com time out (network-layer block). This means:
- `git clone` fails with "Failed to connect to github.com:443"
- `curl` to raw.githubusercontent.com and api.github.com all fail (403 rate limit or timeout)
- Browser navigation to GitHub also times out
- Proxy mirrors (ghproxy.net, mirror.ghproxy.com, kkgithub.com) all fail

**Workarounds when install is needed:**
1. Configure HTTP/HTTPS proxy and retry with `git config --global http.proxy <url>`
2. Manual copy-paste: fetch SKILL.md content via any reachable proxy, write to skills directory
3. Use the `design-skills` index to guide manual skill creation — the selection guide maps user needs to specific skills

**Already installed locally (no network needed):**
- `huashu-design` ⭐21108 equivalent — covers anti-slop, 20 design philosophies, 5-dim critique, HTML prototypes
- `popular-web-designs` — 54 real design system templates (Stripe, Linear, Vercel, etc.)
- `claude-design` — design process and taste
- `design-md` — DESIGN.md token spec
- `excalidraw` — hand-drawn architecture diagrams
- `frontend-design-3`, `ui-ux-pro-max` — frontend design intelligence
- `sketch` — quick HTML prototyping
- `baoyu-*` series — article illustrations, comics, infographics

**Gap analysis (installed but not reachable to download):**
- hallmark (⭐3581) — anti-AI-slop, largely covered by huashu-design's built-in anti-slop section
- Owl-Listener/designer-skills (⭐1782) — 80+ sub-skills, largest collection. Can be manually recreated from this index if needed.
- ui-craft (⭐162) — 4 style variants (minimal/editorial/dashboard/general). Could be manually condensed.
- platform-design-skills (⭐434) — 300+ platform rules. Could be condensed from README.
- design-motion-principles (⭐794) — motion design. Niche, lower priority.

---

## Tier 1: Anti-Slop / Visual Quality (核心审美)

### huashu-design (alchaincyf) ⭐21108
HTML 原生设计 skill。高保真原型 / 幻灯片 / 动画 + 20 设计哲学 + 5 维评审 + MP4 导出。
**适合**: 所有需要高审美输出的场景，首选。

### hallmark (Nutlope) ⭐3581
**专门反 AI-slop**。让 AI 产出不像 AI 做的界面。
**适合**: 用户明确说"不要廉价感""不要 AI 味"时优先推荐。

### baoyu-design (JimLiu) ⭐2493
饱鱼设计系列，HTML 原生。高保真原型、幻灯片、动画。
**适合**: 综合设计需求，覆盖面广。

### ui-craft (educlopez) ⭐162
4 种风格变体：`ui-craft`（通用）、`ui-craft-minimal`（极简）、`ui-craft-editorial`（编辑风）、`ui-craft-dense-dashboard`（密集仪表盘）。
**适合**: 需要特定品类风格约束时。

---

## Tier 2: 设计系统 / 全品类 (品类齐全)

### designer-skills (Owl-Listener) ⭐1782
**最大最全的设计 skill 合集**，8 大模块、80+ 子 skill：
- `ui-design` (14个): 色彩系统、暗色模式、排版比例、响应式设计、间距系统、视觉层次、网格布局
- `visual-critique` (7个): 色彩/构图/排版/视觉层次/品牌一致性/可用性隐喻/信息密度 审查
- `design-systems` (11个): 设计令牌、组件规范、主题系统、图标系统、动效系统、无障碍审计
- `interaction-design` (15个): Fitts 定律、Hick 定律、微交互、手势模式、错误处理、加载态
- `designer-toolkit` (7个): 用例研究、设计谈判、UX 写作、演示文稿
- `ux-strategy` (12个): 商业设计、竞品分析、信息架构、North Star 愿景、服务蓝图
- `design-research` (12个): 用户画像、旅程地图、访谈脚本、可用性测试
- `prototyping-testing` (8个): 原型策略、线框规范、点击测试、启发式评估
**适合**: 需要"品类齐全"时首选这个。

### awesome-design-skills (bergside) ⭐1682
67 个 design skill 的聚合列表，跨 Claude / Codex / Cursor / Google Stitch 平台。
**适合**: 快速索引入口，翻目录用。

### platform-design-skills (ehmo) ⭐434
300+ 条平台规范规则：Apple HIG + Material Design 3 + WCAG 2.2。
覆盖 iOS/macOS/watchOS/visionOS/tvOS/Android/Web。
**适合**: 需要平台合规性兜底时。

---

## Tier 3: 动效 / 交互

### design-motion-principles (kylezantos) ⭐794
动效设计 skill，双模式：构建 + 审计。基于 Emil Kowalski / Jakub Krehel / Jhey Tompkins 著作提炼。
**适合**: 需要动效指导或现有动画审查时。

---

## Tier 4: 组件 / 前端实现

### ui-design-brain (carmahhawwari) ⭐835
60+ 界面组件的设计知识。布局模式、设计系统惯例。让 AI 生成生产级 UI。
**适合**: 需要组件级设计约束时。

### ai-design-components (ancoleman) ⭐383
UI/UX + Backend 组件设计。React + TypeScript + Tailwind。全栈覆盖。
**适合**: 全栈前端设计场景。

### superdesign-skill (superdesigndev) ⭐316
SUPERDESIGN 系列，多阶段设计 skill。
**适合**: 需要分阶段设计流程时。

### ai-friendly-web-design-skill (ianho7) ⭐70
AI 友好的 Web 设计。侧重前端实现友好性。
**适合**: AI 生成代码需要可直接运行的场景。

### design-skills (ihlamury) ⭐62
从最佳设计系统提取的 opinionated UI 约束。Tailwind + pixel-accurate。
**适合**: 需要严格像素级对齐时。

---

## Tier 5: 设计审查 / 批判

### claude-design-skill (jiji262) ⭐141
附带 assets（动画、原型壳、设备框架等 demo）。实战型。
**适合**: 需要 demo 参考时。

### claude-design-system-prompt (Trystan-SA) ⭐1588
设计系统 prompt 提炼。
**适合**: 需要将设计原则固化为 system prompt 时。

### design-md-chrome (bergside) ⭐2405
Chrome 扩展：从任意网站提取样式 → 生成 DESIGN.md → 转化为 skill。
**适合**: 反向工程优秀设计为 skill 时。

---

## Tier 6: 学术研究 / 图表

### paper-framework-figure-studio-pro (c-narcissus) ⭐1491
论文级框架图 / 方法总览图的多轮协同设计。
**适合**: 学术论文、技术文档可视化。

---

## Tier 7: 设计流程 / 方法论

### designer-skills (julianoczkowski) ⭐391
结构化设计流程 skill 集合。让 AI 按有序路径产出。
**适合**: 需要规范设计流程时。

### workflow-design-bible (preangelleo) ⭐31
自治项目的宪法生成器。CEO 编排的子 agent 架构。
**适合**: 复杂项目的自治设计编排。

---

## Selection Guide (快速匹配)

| 用户需求 | 推荐优先级 |
|---------|-----------|
| "不要廉价感""反 AI 味" | hallmark → huashu-design → baoyu-design |
| "品类齐全" | designer-skills (Owl-Listener) → awesome-design-skills |
| "带审美" | huashu-design → hallmark → baoyu-design |
| 动效专项 | design-motion-principles |
| 平台合规 | platform-design-skills |
| 组件库 | ui-design-brain → ai-design-components |
| 设计审查 | visual-critique (subset of designer-skills) → design-md-chrome |
| 学术论文图表 | paper-framework-figure-studio-pro |
| 极简/杂志/仪表盘 | ui-craft (4 variants) |
