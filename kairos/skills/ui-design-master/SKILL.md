---
name: "ui-design-master"
description: "专业的UI/UX设计专家技能。提供界面设计、配色方案、设计系统构建、动效设计、响应式布局、仪表盘设计、落地页设计、移动应用设计、图标设计等全方位设计指导与代码实现。触发关键词：UI设计、UX设计、界面设计、配色、设计系统、组件库、落地页、仪表盘、移动应用、图标、动效、响应式、Tailwind CSS"
priority: 0.5
imported-from: "minimax"
source-path: "C:\\Users\\you\\.minimax\\skills\\ui-design-master\\SKILL.md"
---
# UI设计大师

## 概述

你是一位顶级的UI/UX设计专家，拥有丰富的界面设计经验和深厚的美学素养。你精通现代设计理念、设计系统构建、交互设计原则，能够为用户提供专业、实用的设计指导，并能直接生成高质量的设计作品。

## 核心能力

### 🎨 视觉设计
- 配色方案设计与色彩理论应用
- 字体排版与视觉层次构建
- 图标设计与插画创作
- 品牌视觉识别系统设计

### 📐 界面设计
- 响应式网页设计
- 移动应用UI设计（iOS/Android）
- 仪表盘与后台管理界面
- 落地页与营销页面

### 🧩 设计系统
- 设计令牌（Design Tokens）定义
- 组件库规范制定
- 样式指南文档编写
- 设计与开发协作规范

### ✨ 交互设计
- 微交互与动效设计
- 用户流程优化
- 可用性与无障碍设计
- 手势交互设计

## 设计原则

1. **用户为中心**：所有设计决策以用户需求为出发点
2. **简洁清晰**：去除冗余，突出核心功能
3. **一致性**：保持视觉和交互的统一
4. **可访问性**：确保设计对所有用户友好
5. **美观实用**：在美学与功能间取得完美平衡

## 工作流程

### 设计咨询模式
当用户寻求设计建议时：
1. 深入理解设计目标和约束条件
2. 分析目标用户群体和使用场景
3. 提供具体、可执行的设计方案
4. 给出详细的设计规范（色值、尺寸、间距等）

### 设计创作模式
当用户需要设计作品时：
1. 确认设计需求和风格偏好
2. 搜索相关设计灵感作为参考
3. 生成高质量的UI设计图或原型
4. 提供配套的代码实现（HTML/CSS/React）

### 设计评审模式
当用户展示现有设计时：
1. 全面评估设计的优缺点
2. 从可用性、美观性、一致性等维度分析
3. 提供具体的改进建议
4. 必要时生成改进后的设计方案

## 输出标准

- **配色方案**：提供完整的HEX/RGB色值
- **间距尺寸**：使用具体的像素值或设计令牌
- **组件规范**：包含所有状态和变体
- **代码实现**：使用Tailwind CSS，注重细节和响应式
- **设计图**：生成高质量、可直接使用的视觉稿

## 设计灵感来源

在设计过程中，参考以下资源：
- **Dribbble / Behance**：设计灵感和趋势
- **Awwwards**：优秀网页设计案例
- **Mobbin**：移动应用UI参考
- **Material Design / Apple HIG**：平台设计规范

---

# 配色系统设计

## 色彩理论基础

### 色彩模式
- **单色（Monochromatic）**：同一色相的不同明度和饱和度
- **互补色（Complementary）**：色轮对面的两种颜色
- **类似色（Analogous）**：色轮相邻的颜色
- **三角色（Triadic）**：色轮等距的三种颜色
- **分裂互补（Split-Complementary）**：一种颜色与其互补色两侧的颜色

### 色彩心理学
| 颜色 | 情感联想 | 适用场景 |
|------|----------|----------|
| 蓝色 | 信任、专业、冷静 | 科技、金融、医疗 |
| 绿色 | 自然、健康、成长 | 环保、健康、教育 |
| 红色 | 激情、紧迫、能量 | 餐饮、促销、娱乐 |
| 橙色 | 活力、友好、创意 | 创意、社交、运动 |
| 紫色 | 奢华、神秘、创新 | 美妆、奢侈品、科技 |
| 黄色 | 乐观、温暖、注意 | 儿童、快消、警示 |
| 黑色 | 高端、专业、力量 | 奢侈品、时尚、科技 |

## 配色方案模板

### SaaS产品配色
```
主色：#2563EB（品牌蓝）
辅助色：#3B82F6（浅蓝）
强调色：#10B981（成功绿）
警告色：#F59E0B（警告橙）
错误色：#EF4444（错误红）
中性色：
  - 文字：#111827
  - 次要文字：#6B7280
  - 边框：#E5E7EB
  - 背景：#F9FAFB
```

### 电商平台配色
```
主色：#FF6B35（活力橙）
辅助色：#004E89（稳重蓝）
强调色：#F72585（促销粉）
成功色：#06D6A0（成功绿）
背景色：#FFFFFF
深色背景：#1A1A2E
```

### 暗色主题配色
```
背景层级：
  - 最深：#0F0F0F
  - 深色：#1A1A1A
  - 中等：#262626
  - 浅色：#404040
文字层级：
  - 主要：#FFFFFF
  - 次要：#A3A3A3
  - 禁用：#525252
强调色：#3B82F6
```

## 无障碍色彩标准（WCAG 2.1）

### 对比度要求
- **AA级**：普通文字 4.5:1，大文字 3:1
- **AAA级**：普通文字 7:1，大文字 4.5:1

### 推荐的高对比度组合
| 背景色 | 文字色 | 对比度 |
|--------|--------|--------|
| #FFFFFF | #1F2937 | 14.7:1 ✓ |
| #F3F4F6 | #374151 | 9.3:1 ✓ |
| #1F2937 | #FFFFFF | 14.7:1 ✓ |
| #3B82F6 | #FFFFFF | 4.5:1 ✓ |

## 渐变色方案

### 现代渐变预设
```css
/* 日出渐变 */
background: linear-gradient(135deg, #FF6B6B 0%, #FFE66D 100%);

/* 海洋渐变 */
background: linear-gradient(135deg, #667EEA 0%, #764BA2 100%);

/* 清新渐变 */
background: linear-gradient(135deg, #11998E 0%, #38EF7D 100%);

/* 极光渐变 */
background: linear-gradient(135deg, #4FACFE 0%, #00F2FE 100%);

/* 暖阳渐变 */
background: linear-gradient(135deg, #FA709A 0%, #FEE140 100%);
```

## 配色使用指南

1. 分析品牌定位和目标用户群体
2. 选择符合情感诉求的主色调
3. 基于色彩理论构建完整配色方案
4. 验证无障碍对比度要求
5. 在实际界面中测试并微调

---

# 设计系统构建指南

## 设计令牌（Design Tokens）

### 间距系统（8px基准）
```
spacing-0: 0px
spacing-1: 4px
spacing-2: 8px
spacing-3: 12px
spacing-4: 16px
spacing-5: 20px
spacing-6: 24px
spacing-8: 32px
spacing-10: 40px
spacing-12: 48px
spacing-16: 64px
spacing-20: 80px
spacing-24: 96px
```

### 字体系统
```
字体家族：
  - 主要：Inter, -apple-system, BlinkMacSystemFont, sans-serif
  - 代码：JetBrains Mono, Consolas, monospace
  - 中文：PingFang SC, Microsoft YaHei, sans-serif

字号：
  - xs: 12px / 16px
  - sm: 14px / 20px
  - base: 16px / 24px
  - lg: 18px / 28px
  - xl: 20px / 28px
  - 2xl: 24px / 32px
  - 3xl: 30px / 36px
  - 4xl: 36px / 40px
  - 5xl: 48px / 48px

字重：
  - normal: 400
  - medium: 500
  - semibold: 600
  - bold: 700
```

### 圆角系统
```
rounded-none: 0px
rounded-sm: 2px
rounded: 4px
rounded-md: 6px
rounded-lg: 8px
rounded-xl: 12px
rounded-2xl: 16px
rounded-3xl: 24px
rounded-full: 9999px
```

### 阴影系统
```css
shadow-sm: 0 1px 2px 0 rgb(0 0 0 / 0.05);
shadow: 0 1px 3px 0 rgb(0 0 0 / 0.1), 0 1px 2px -1px rgb(0 0 0 / 0.1);
shadow-md: 0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1);
shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1);
shadow-xl: 0 20px 25px -5px rgb(0 0 0 / 0.1), 0 8px 10px -6px rgb(0 0 0 / 0.1);
shadow-2xl: 0 25px 50px -12px rgb(0 0 0 / 0.25);
```

## 核心组件规范

### 按钮（Button）
```
尺寸：
  - xs: h-7 px-2 text-xs
  - sm: h-8 px-3 text-sm
  - md: h-10 px-4 text-sm
  - lg: h-11 px-6 text-base
  - xl: h-12 px-8 text-base

变体：
  - primary: 主要操作，品牌色填充
  - secondary: 次要操作，灰色填充
  - outline: 边框按钮，透明背景
  - ghost: 幽灵按钮，无边框无背景
  - destructive: 危险操作，红色系

状态：
  - default: 默认状态
  - hover: 悬停（亮度调整5-10%）
  - active: 按下（亮度调整10-15%）
  - disabled: 禁用（opacity: 0.5）
  - loading: 加载中（显示spinner）
```

### 输入框（Input）
```
高度：h-10 (40px)
内边距：px-3 py-2
边框：1px solid border-color
圆角：rounded-md (6px)
字号：text-sm (14px)

状态样式：
  - default: border-gray-300
  - focus: border-primary ring-2 ring-primary/20
  - error: border-red-500 ring-2 ring-red-500/20
  - disabled: bg-gray-100 cursor-not-allowed
```

### 卡片（Card）
```
内边距：p-6 (24px)
圆角：rounded-lg (8px)
背景：bg-white
边框：border border-gray-200
阴影：shadow-sm

变体：
  - default: 标准卡片
  - elevated: 带阴影的浮起效果
  - outline: 仅边框无阴影
  - interactive: 可点击，带hover效果
```

### 模态框（Modal）
```
最大宽度：
  - sm: max-w-sm (384px)
  - md: max-w-md (448px)
  - lg: max-w-lg (512px)
  - xl: max-w-xl (576px)
  - 2xl: max-w-2xl (672px)

结构：
  - overlay: 遮罩层 bg-black/50
  - container: 内容容器
  - header: 标题区 pb-4
  - body: 内容区 py-4
  - footer: 操作区 pt-4
```

## 布局系统

### 网格系统
```
容器宽度：
  - sm: 640px
  - md: 768px
  - lg: 1024px
  - xl: 1280px
  - 2xl: 1536px

栅格：12列布局
间距：gap-4 (16px) 或 gap-6 (24px)
```

### 常用布局模式
```
侧边栏布局：
  - sidebar: w-64 (256px) 或 w-72 (288px)
  - main: flex-1

顶部导航布局：
  - header: h-16 (64px)
  - main: min-h-[calc(100vh-64px)]
```

## 组件文档模板

每个组件需包含：
1. **概述**：组件用途和使用场景
2. **属性**：所有可配置的props
3. **变体**：不同样式变体展示
4. **状态**：各种交互状态
5. **示例**：代码示例和预览
6. **最佳实践**：使用建议和注意事项
7. **无障碍**：a11y相关说明

---

# 响应式设计指南

## 断点系统

### 标准断点（Mobile First）
```css
/* 移动设备 - 默认样式 */
/* < 640px */

/* 小平板 */
@media (min-width: 640px) { /* sm */ }

/* 平板 */
@media (min-width: 768px) { /* md */ }

/* 小桌面 */
@media (min-width: 1024px) { /* lg */ }

/* 桌面 */
@media (min-width: 1280px) { /* xl */ }

/* 大桌面 */
@media (min-width: 1536px) { /* 2xl */ }
```

### Tailwind 断点速查
```
sm:  >= 640px   小平板横屏
md:  >= 768px   平板竖屏
lg:  >= 1024px  平板横屏/小笔记本
xl:  >= 1280px  桌面显示器
2xl: >= 1536px  大屏显示器
```

## 响应式布局模式

### 1. 流式布局（Fluid Layout）
```html
<div class="w-full max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
  <!-- 内容随屏幕宽度自适应，但有最大宽度限制 -->
</div>
```

### 2. 列数变化布局
```html
<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
  <!-- 卡片列表：移动端1列，逐渐增加到4列 -->
</div>
```

### 3. 侧边栏响应式
```html
<!-- 移动端隐藏侧边栏，桌面端显示 -->
<div class="flex">
  <aside class="hidden lg:block w-64 shrink-0">
    <!-- 侧边栏 -->
  </aside>
  <main class="flex-1">
    <!-- 主内容 -->
  </main>
</div>
```

### 4. 堆叠到并排
```html
<div class="flex flex-col md:flex-row gap-6">
  <div class="w-full md:w-1/3">左侧内容</div>
  <div class="w-full md:w-2/3">右侧内容</div>
</div>
```

## 响应式字体

### 流体字体（Clamp）
```css
/* 标题：24px -> 48px */
font-size: clamp(1.5rem, 4vw, 3rem);

/* 正文：14px -> 18px */
font-size: clamp(0.875rem, 1.5vw, 1.125rem);
```

### 断点字体
```html
<h1 class="text-2xl sm:text-3xl md:text-4xl lg:text-5xl">
  响应式标题
</h1>
```

## 响应式间距

```html
<!-- 页面内边距 -->
<div class="p-4 sm:p-6 lg:p-8">

<!-- 组件间距 -->
<div class="space-y-4 sm:space-y-6 lg:space-y-8">

<!-- 网格间距 -->
<div class="gap-4 sm:gap-6 lg:gap-8">
```

## 响应式图片

### 1. 自适应图片
```html
<img
  src="image.jpg"
  class="w-full h-auto object-cover"
  alt="描述"
/>
```

### 2. 不同尺寸图片源
```html
<picture>
  <source media="(min-width: 1024px)" srcset="large.jpg">
  <source media="(min-width: 640px)" srcset="medium.jpg">
  <img src="small.jpg" alt="响应式图片">
</picture>
```

### 3. 宽高比容器
```html
<div class="aspect-video">
  <img src="video-thumb.jpg" class="w-full h-full object-cover">
</div>

<div class="aspect-square">
  <img src="avatar.jpg" class="w-full h-full object-cover rounded-full">
</div>
```

## 响应式导航

### 移动端汉堡菜单
```html
<nav>
  <!-- 移动端：汉堡按钮 -->
  <button class="lg:hidden">
    <MenuIcon />
  </button>

  <!-- 桌面端：水平导航 -->
  <ul class="hidden lg:flex space-x-8">
    <li><a href="#">首页</a></li>
    <li><a href="#">产品</a></li>
    <li><a href="#">关于</a></li>
  </ul>
</nav>
```

## 响应式表格

### 横向滚动表格
```html
<div class="overflow-x-auto">
  <table class="min-w-full">
    <!-- 表格内容 -->
  </table>
</div>
```

### 卡片化表格（移动端）
```html
<div class="hidden md:block">
  <!-- 桌面端：标准表格 -->
</div>
<div class="md:hidden space-y-4">
  <!-- 移动端：卡片列表 -->
</div>
```

## 响应式隐藏/显示

```html
<!-- 仅移动端显示 -->
<div class="block sm:hidden">

<!-- 仅桌面端显示 -->
<div class="hidden lg:block">

<!-- 平板及以上显示 -->
<div class="hidden md:block">
```

## 响应式测试清单

- [ ] iPhone SE (375px)
- [ ] iPhone 14 (390px)
- [ ] iPad Mini (768px)
- [ ] iPad Pro (1024px)
- [ ] MacBook (1440px)
- [ ] Desktop (1920px)

---

# UI动效设计指南

## 动效原则

### 1. 目的性
每个动效都应该有明确目的：
- **引导注意力**：引导用户关注重要内容
- **反馈操作**：确认用户的操作
- **状态过渡**：平滑地展示状态变化
- **空间关系**：表达元素之间的层级关系

### 2. 时长建议
```
即时反馈：100-150ms（按钮点击、开关切换）
简单过渡：150-200ms（悬停效果、小型弹窗）
中等过渡：200-300ms（侧边栏、下拉菜单）
复杂过渡：300-500ms（模态框、页面切换）
```

### 3. 缓动函数
```css
/* 常用缓动 */
ease-out: cubic-bezier(0, 0, 0.2, 1);      /* 进入动画 */
ease-in: cubic-bezier(0.4, 0, 1, 1);       /* 退出动画 */
ease-in-out: cubic-bezier(0.4, 0, 0.2, 1); /* 状态切换 */

/* 弹性效果 */
bounce: cubic-bezier(0.68, -0.55, 0.265, 1.55);
```

## Tailwind 动效类

### 过渡
```html
<!-- 基础过渡 -->
<div class="transition-all duration-200 ease-out">

<!-- 特定属性过渡 -->
<div class="transition-colors duration-150">
<div class="transition-opacity duration-200">
<div class="transition-transform duration-300">
```

### 悬停效果
```html
<!-- 颜色变化 -->
<button class="bg-blue-500 hover:bg-blue-600 transition-colors">

<!-- 缩放效果 -->
<div class="hover:scale-105 transition-transform">

<!-- 阴影效果 -->
<div class="hover:shadow-lg transition-shadow">

<!-- 上移效果 -->
<div class="hover:-translate-y-1 transition-transform">
```

### 内置动画
```html
<!-- 旋转 -->
<svg class="animate-spin">

<!-- 脉冲 -->
<span class="animate-pulse">

<!-- 弹跳 -->
<div class="animate-bounce">

<!-- 渐显 -->
<div class="animate-fade-in">
```

## CSS动画代码

### 淡入淡出
```css
@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}

@keyframes fadeOut {
  from { opacity: 1; }
  to { opacity: 0; }
}

.fade-in { animation: fadeIn 0.2s ease-out; }
.fade-out { animation: fadeOut 0.2s ease-in; }
```

### 滑入动画
```css
@keyframes slideInUp {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes slideInDown {
  from {
    opacity: 0;
    transform: translateY(-20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.slide-in-up { animation: slideInUp 0.3s ease-out; }
.slide-in-down { animation: slideInDown 0.3s ease-out; }
```

### 缩放动画
```css
@keyframes scaleIn {
  from {
    opacity: 0;
    transform: scale(0.95);
  }
  to {
    opacity: 1;
    transform: scale(1);
  }
}

@keyframes scaleOut {
  from {
    opacity: 1;
    transform: scale(1);
  }
  to {
    opacity: 0;
    transform: scale(0.95);
  }
}
```

### 骨架屏动画
```css
@keyframes shimmer {
  0% { background-position: -200% 0; }
  100% { background-position: 200% 0; }
}

.skeleton {
  background: linear-gradient(
    90deg,
    #f0f0f0 25%,
    #e0e0e0 50%,
    #f0f0f0 75%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
}
```

### 加载动画
```css
/* 旋转加载 */
@keyframes spin {
  to { transform: rotate(360deg); }
}
.spinner {
  animation: spin 1s linear infinite;
}

/* 脉冲点 */
@keyframes pulse-dot {
  0%, 80%, 100% { transform: scale(0); }
  40% { transform: scale(1); }
}
.loading-dots span {
  animation: pulse-dot 1.4s infinite ease-in-out both;
}
.loading-dots span:nth-child(1) { animation-delay: -0.32s; }
.loading-dots span:nth-child(2) { animation-delay: -0.16s; }
```

## 组件动效示例

### 按钮反馈
```html
<button class="
  transform
  transition-all
  duration-150
  active:scale-95
  hover:shadow-md
">
  点击我
</button>
```

### 卡片悬停
```html
<div class="
  transition-all
  duration-300
  hover:shadow-xl
  hover:-translate-y-2
">
  卡片内容
</div>
```

### 模态框进入
```html
<!-- 遮罩层 -->
<div class="
  fixed inset-0 bg-black/50
  transition-opacity duration-200
  data-[state=open]:opacity-100
  data-[state=closed]:opacity-0
">
  <!-- 内容 -->
  <div class="
    transition-all duration-300
    data-[state=open]:opacity-100 data-[state=open]:scale-100
    data-[state=closed]:opacity-0 data-[state=closed]:scale-95
  ">
  </div>
</div>
```

### 下拉菜单
```html
<div class="
  origin-top
  transition-all duration-200
  data-[state=open]:opacity-100 data-[state=open]:scale-y-100
  data-[state=closed]:opacity-0 data-[state=closed]:scale-y-95
">
  菜单内容
</div>
```

### 侧边栏滑入
```html
<aside class="
  fixed inset-y-0 left-0 w-64
  transform transition-transform duration-300
  data-[state=open]:translate-x-0
  data-[state=closed]:-translate-x-full
">
  侧边栏内容
</aside>
```

## 微交互示例

### 复选框
```css
.checkbox:checked + .checkmark {
  animation: checkmark 0.2s ease-out forwards;
}

@keyframes checkmark {
  0% { stroke-dashoffset: 24; }
  100% { stroke-dashoffset: 0; }
}
```

### 点赞动画
```css
.like-button.liked {
  animation: like-pop 0.3s ease-out;
}

@keyframes like-pop {
  0% { transform: scale(1); }
  50% { transform: scale(1.3); }
  100% { transform: scale(1); }
}
```

### 输入框聚焦
```html
<div class="relative">
  <input class="peer border-b-2 border-gray-300 focus:border-blue-500 transition-colors" />
  <span class="
    absolute bottom-0 left-0 h-0.5 w-0 bg-blue-500
    peer-focus:w-full transition-all duration-300
  "></span>
</div>
```

## 动效性能优化

1. **使用transform和opacity**：这两个属性不触发重排
2. **使用will-change**：提示浏览器优化
3. **避免同时动画过多元素**：控制在10个以内
4. **使用GPU加速**：`transform: translateZ(0)`
5. **尊重用户偏好**：`prefers-reduced-motion`

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}
```

---

# 仪表盘设计指南

## 仪表盘布局

### 标准布局结构
```
┌──────────────────────────────────────────┐
│  顶部导航栏（Header）h-16               │
├────────┬─────────────────────────────────┤
│        │  面包屑导航                     │
│  侧    ├─────────────────────────────────┤
│  边    │                                 │
│  栏    │  主要内容区                     │
│        │  （数据卡片、图表、表格）       │
│  w-64  │                                 │
│        │                                 │
└────────┴─────────────────────────────────┘
```

### 基础HTML结构
```html
<div class="min-h-screen bg-gray-100">
  <!-- 顶部导航 -->
  <header class="h-16 bg-white border-b fixed top-0 left-0 right-0 z-50">
    <div class="flex items-center justify-between h-full px-6">
      <div class="flex items-center gap-4">
        <img src="logo.svg" class="h-8" />
        <span class="font-semibold">Dashboard</span>
      </div>
      <div class="flex items-center gap-4">
        <button>🔔</button>
        <img src="avatar.jpg" class="w-8 h-8 rounded-full" />
      </div>
    </div>
  </header>

  <!-- 侧边栏 -->
  <aside class="w-64 bg-white border-r fixed top-16 left-0 bottom-0">
    <nav class="p-4 space-y-2">
      <a href="#" class="flex items-center gap-3 px-4 py-2 rounded-lg bg-blue-50 text-blue-600">
        <svg>📊</svg> 概览
      </a>
      <a href="#" class="flex items-center gap-3 px-4 py-2 rounded-lg hover:bg-gray-50">
        <svg>👥</svg> 用户管理
      </a>
      <!-- 更多菜单项 -->
    </nav>
  </aside>

  <!-- 主内容区 -->
  <main class="ml-64 pt-16 p-6">
    <!-- 内容 -->
  </main>
</div>
```

## 数据卡片设计

### 统计卡片
```html
<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
  <div class="bg-white rounded-xl p-6 shadow-sm">
    <div class="flex items-center justify-between">
      <div>
        <p class="text-sm text-gray-500">总用户数</p>
        <p class="text-2xl font-bold mt-1">12,345</p>
        <p class="text-sm text-green-500 mt-2">↑ 12% 较上月</p>
      </div>
      <div class="w-12 h-12 bg-blue-100 rounded-lg flex items-center justify-center">
        <svg class="w-6 h-6 text-blue-600">👥</svg>
      </div>
    </div>
  </div>

  <div class="bg-white rounded-xl p-6 shadow-sm">
    <div class="flex items-center justify-between">
      <div>
        <p class="text-sm text-gray-500">总收入</p>
        <p class="text-2xl font-bold mt-1">¥89,432</p>
        <p class="text-sm text-green-500 mt-2">↑ 8.2% 较上月</p>
      </div>
      <div class="w-12 h-12 bg-green-100 rounded-lg flex items-center justify-center">
        <svg class="w-6 h-6 text-green-600">💰</svg>
      </div>
    </div>
  </div>

  <!-- 更多卡片 -->
</div>
```

### 图表卡片
```html
<div class="bg-white rounded-xl p-6 shadow-sm">
  <div class="flex items-center justify-between mb-6">
    <h3 class="font-semibold">收入趋势</h3>
    <select class="text-sm border rounded-lg px-3 py-1">
      <option>最近7天</option>
      <option>最近30天</option>
      <option>最近90天</option>
    </select>
  </div>
  <div class="h-64">
    <!-- 图表区域 -->
    <canvas id="revenueChart"></canvas>
  </div>
</div>
```

## 数据表格设计

### 标准表格
```html
<div class="bg-white rounded-xl shadow-sm overflow-hidden">
  <!-- 表格头部 -->
  <div class="p-6 border-b flex items-center justify-between">
    <h3 class="font-semibold">用户列表</h3>
    <div class="flex items-center gap-4">
      <div class="relative">
        <input type="text" placeholder="搜索..."
          class="pl-10 pr-4 py-2 border rounded-lg text-sm" />
        <svg class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400">🔍</svg>
      </div>
      <button class="px-4 py-2 bg-blue-600 text-white rounded-lg text-sm">
        添加用户
      </button>
    </div>
  </div>

  <!-- 表格内容 -->
  <table class="w-full">
    <thead class="bg-gray-50 text-left text-sm text-gray-500">
      <tr>
        <th class="px-6 py-4 font-medium">
          <input type="checkbox" class="rounded" />
        </th>
        <th class="px-6 py-4 font-medium">用户</th>
        <th class="px-6 py-4 font-medium">邮箱</th>
        <th class="px-6 py-4 font-medium">状态</th>
        <th class="px-6 py-4 font-medium">注册时间</th>
        <th class="px-6 py-4 font-medium">操作</th>
      </tr>
    </thead>
    <tbody class="divide-y">
      <tr class="hover:bg-gray-50">
        <td class="px-6 py-4">
          <input type="checkbox" class="rounded" />
        </td>
        <td class="px-6 py-4">
          <div class="flex items-center gap-3">
            <img src="avatar.jpg" class="w-10 h-10 rounded-full" />
            <div>
              <div class="font-medium">张三</div>
              <div class="text-sm text-gray-500">@zhangsan</div>
            </div>
          </div>
        </td>
        <td class="px-6 py-4 text-gray-600">zhangsan@example.com</td>
        <td class="px-6 py-4">
          <span class="px-2 py-1 bg-green-100 text-green-700 rounded-full text-xs">
            活跃
          </span>
        </td>
        <td class="px-6 py-4 text-gray-600">2024-01-15</td>
        <td class="px-6 py-4">
          <div class="flex items-center gap-2">
            <button class="p-2 hover:bg-gray-100 rounded-lg">✏️</button>
            <button class="p-2 hover:bg-gray-100 rounded-lg">🗑️</button>
          </div>
        </td>
      </tr>
      <!-- 更多行 -->
    </tbody>
  </table>

  <!-- 分页 -->
  <div class="p-6 border-t flex items-center justify-between">
    <p class="text-sm text-gray-500">显示 1-10 共 100 条</p>
    <div class="flex items-center gap-2">
      <button class="px-3 py-1 border rounded-lg text-sm">上一页</button>
      <button class="px-3 py-1 bg-blue-600 text-white rounded-lg text-sm">1</button>
      <button class="px-3 py-1 border rounded-lg text-sm">2</button>
      <button class="px-3 py-1 border rounded-lg text-sm">3</button>
      <button class="px-3 py-1 border rounded-lg text-sm">下一页</button>
    </div>
  </div>
</div>
```

## 状态标签设计

```html
<!-- 状态标签 -->
<span class="px-2 py-1 rounded-full text-xs font-medium bg-green-100 text-green-700">成功</span>
<span class="px-2 py-1 rounded-full text-xs font-medium bg-yellow-100 text-yellow-700">待处理</span>
<span class="px-2 py-1 rounded-full text-xs font-medium bg-red-100 text-red-700">失败</span>
<span class="px-2 py-1 rounded-full text-xs font-medium bg-blue-100 text-blue-700">进行中</span>
<span class="px-2 py-1 rounded-full text-xs font-medium bg-gray-100 text-gray-700">已取消</span>
```

## 空状态设计

```html
<div class="flex flex-col items-center justify-center py-16">
  <div class="w-24 h-24 bg-gray-100 rounded-full flex items-center justify-center mb-6">
    <svg class="w-12 h-12 text-gray-400">📭</svg>
  </div>
  <h3 class="text-lg font-medium text-gray-900 mb-2">暂无数据</h3>
  <p class="text-gray-500 mb-6">还没有任何记录，点击下方按钮创建第一条</p>
  <button class="px-6 py-2 bg-blue-600 text-white rounded-lg">
    创建记录
  </button>
</div>
```

## 侧边栏菜单设计

```html
<nav class="p-4 space-y-1">
  <!-- 带图标的菜单项 -->
  <a href="#" class="flex items-center gap-3 px-4 py-3 rounded-lg bg-blue-50 text-blue-600">
    <svg class="w-5 h-5">📊</svg>
    <span>概览</span>
  </a>

  <!-- 带徽章的菜单项 -->
  <a href="#" class="flex items-center justify-between px-4 py-3 rounded-lg hover:bg-gray-50">
    <div class="flex items-center gap-3">
      <svg class="w-5 h-5 text-gray-500">📬</svg>
      <span>消息</span>
    </div>
    <span class="px-2 py-0.5 bg-red-500 text-white text-xs rounded-full">12</span>
  </a>

  <!-- 可折叠菜单 -->
  <div>
    <button class="w-full flex items-center justify-between px-4 py-3 rounded-lg hover:bg-gray-50">
      <div class="flex items-center gap-3">
        <svg class="w-5 h-5 text-gray-500">⚙️</svg>
        <span>设置</span>
      </div>
      <svg class="w-4 h-4 text-gray-400">▼</svg>
    </button>
    <div class="ml-8 mt-1 space-y-1">
      <a href="#" class="block px-4 py-2 text-sm text-gray-600 hover:bg-gray-50 rounded-lg">
        个人设置
      </a>
      <a href="#" class="block px-4 py-2 text-sm text-gray-600 hover:bg-gray-50 rounded-lg">
        团队设置
      </a>
    </div>
  </div>
</nav>
```

## 筛选器设计

```html
<div class="flex flex-wrap items-center gap-4 p-4 bg-white rounded-xl mb-6">
  <!-- 搜索框 -->
  <div class="relative flex-1 min-w-[200px]">
    <input type="text" placeholder="搜索..."
      class="w-full pl-10 pr-4 py-2 border rounded-lg" />
    <svg class="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400">🔍</svg>
  </div>

  <!-- 下拉筛选 -->
  <select class="px-4 py-2 border rounded-lg">
    <option>所有状态</option>
    <option>活跃</option>
    <option>待审核</option>
    <option>已禁用</option>
  </select>

  <!-- 日期选择 -->
  <input type="date" class="px-4 py-2 border rounded-lg" />

  <!-- 筛选按钮 -->
  <button class="px-4 py-2 bg-blue-600 text-white rounded-lg">
    应用筛选
  </button>
  <button class="px-4 py-2 border rounded-lg">
    重置
  </button>
</div>
```

## 仪表盘设计原则

1. **信息层次**：最重要的数据放在最显眼的位置
2. **一致性**：保持组件样式和交互的统一
3. **可扫描性**：用户能快速找到需要的信息
4. **响应式**：适配不同屏幕尺寸
5. **加载状态**：提供骨架屏和加载指示器
6. **空状态**：为无数据情况设计友好提示

---

# 落地页设计指南

## 落地页结构

### 标准结构
```
1. 导航栏（Navigation）
2. 英雄区（Hero Section）
3. 社会证明（Social Proof）
4. 功能特性（Features）
5. 工作原理（How It Works）
6. 定价方案（Pricing）
7. 用户评价（Testimonials）
8. 常见问题（FAQ）
9. 行动号召（Final CTA）
10. 页脚（Footer）
```

## 英雄区设计

### 核心要素
1. **主标题**：简洁有力，突出价值主张（6-12字）
2. **副标题**：补充说明，解释如何实现（20-30字）
3. **CTA按钮**：醒目的行动号召
4. **视觉元素**：产品截图/插画/视频

### 布局模式

#### 左文右图
```html
<section class="py-20">
  <div class="container mx-auto px-6 flex flex-col lg:flex-row items-center gap-12">
    <div class="lg:w-1/2 space-y-6">
      <h1 class="text-4xl lg:text-6xl font-bold">主标题在这里</h1>
      <p class="text-xl text-gray-600">副标题描述产品价值和用户收益</p>
      <div class="flex gap-4">
        <button class="px-8 py-3 bg-blue-600 text-white rounded-lg">立即开始</button>
        <button class="px-8 py-3 border border-gray-300 rounded-lg">了解更多</button>
      </div>
    </div>
    <div class="lg:w-1/2">
      <img src="hero-image.png" alt="产品展示" class="w-full" />
    </div>
  </div>
</section>
```

#### 居中布局
```html
<section class="py-20 text-center">
  <div class="container mx-auto px-6 max-w-4xl space-y-8">
    <h1 class="text-4xl lg:text-6xl font-bold">主标题在这里</h1>
    <p class="text-xl text-gray-600 max-w-2xl mx-auto">副标题描述</p>
    <div class="flex justify-center gap-4">
      <button class="px-8 py-3 bg-blue-600 text-white rounded-lg">立即开始</button>
    </div>
    <img src="hero-image.png" alt="产品展示" class="w-full max-w-5xl mx-auto" />
  </div>
</section>
```

## 功能特性区

### 三列布局
```html
<section class="py-20 bg-gray-50">
  <div class="container mx-auto px-6">
    <div class="text-center mb-16">
      <h2 class="text-3xl font-bold">核心功能</h2>
      <p class="text-gray-600 mt-4">简短的功能区描述</p>
    </div>
    <div class="grid md:grid-cols-3 gap-8">
      <div class="bg-white p-8 rounded-xl shadow-sm">
        <div class="w-12 h-12 bg-blue-100 rounded-lg flex items-center justify-center mb-6">
          <svg class="w-6 h-6 text-blue-600">...</svg>
        </div>
        <h3 class="text-xl font-semibold mb-3">功能标题</h3>
        <p class="text-gray-600">功能描述文字，说明这个功能如何帮助用户。</p>
      </div>
      <!-- 更多功能卡片 -->
    </div>
  </div>
</section>
```

### 交替布局
```html
<section class="py-20">
  <div class="container mx-auto px-6 space-y-24">
    <!-- 左图右文 -->
    <div class="flex flex-col lg:flex-row items-center gap-12">
      <div class="lg:w-1/2">
        <img src="feature-1.png" class="rounded-xl shadow-lg" />
      </div>
      <div class="lg:w-1/2 space-y-4">
        <h3 class="text-2xl font-bold">功能标题</h3>
        <p class="text-gray-600">详细描述这个功能的价值...</p>
      </div>
    </div>
    <!-- 右图左文 -->
    <div class="flex flex-col lg:flex-row-reverse items-center gap-12">
      <!-- 类似结构 -->
    </div>
  </div>
</section>
```

## 定价区设计

```html
<section class="py-20">
  <div class="container mx-auto px-6">
    <div class="text-center mb-16">
      <h2 class="text-3xl font-bold">选择适合你的方案</h2>
    </div>
    <div class="grid md:grid-cols-3 gap-8 max-w-5xl mx-auto">
      <!-- 基础版 -->
      <div class="border rounded-2xl p-8">
        <h3 class="text-lg font-semibold">基础版</h3>
        <div class="mt-4">
          <span class="text-4xl font-bold">¥0</span>
          <span class="text-gray-500">/月</span>
        </div>
        <ul class="mt-8 space-y-4">
          <li class="flex items-center gap-3">
            <svg class="w-5 h-5 text-green-500">✓</svg>
            <span>功能1</span>
          </li>
          <!-- 更多功能 -->
        </ul>
        <button class="w-full mt-8 py-3 border rounded-lg">开始使用</button>
      </div>

      <!-- 专业版（推荐） -->
      <div class="border-2 border-blue-600 rounded-2xl p-8 relative">
        <div class="absolute -top-4 left-1/2 -translate-x-1/2 bg-blue-600 text-white px-4 py-1 rounded-full text-sm">
          最受欢迎
        </div>
        <h3 class="text-lg font-semibold">专业版</h3>
        <div class="mt-4">
          <span class="text-4xl font-bold">¥99</span>
          <span class="text-gray-500">/月</span>
        </div>
        <!-- 功能列表 -->
        <button class="w-full mt-8 py-3 bg-blue-600 text-white rounded-lg">立即订阅</button>
      </div>

      <!-- 企业版 -->
      <!-- 类似结构 -->
    </div>
  </div>
</section>
```

## 用户评价区

```html
<section class="py-20 bg-gray-50">
  <div class="container mx-auto px-6">
    <h2 class="text-3xl font-bold text-center mb-16">用户怎么说</h2>
    <div class="grid md:grid-cols-3 gap-8">
      <div class="bg-white p-8 rounded-xl">
        <div class="flex gap-1 mb-4">
          <!-- 5颗星 -->
          <svg class="w-5 h-5 text-yellow-400 fill-current">★</svg>
          <!-- 重复5次 -->
        </div>
        <p class="text-gray-600 mb-6">"用户评价内容，描述使用产品的体验和收获..."</p>
        <div class="flex items-center gap-4">
          <img src="avatar.jpg" class="w-12 h-12 rounded-full" />
          <div>
            <div class="font-semibold">用户名</div>
            <div class="text-sm text-gray-500">职位，公司</div>
          </div>
        </div>
      </div>
      <!-- 更多评价 -->
    </div>
  </div>
</section>
```

## FAQ区域

```html
<section class="py-20">
  <div class="container mx-auto px-6 max-w-3xl">
    <h2 class="text-3xl font-bold text-center mb-16">常见问题</h2>
    <div class="space-y-4">
      <details class="border rounded-lg">
        <summary class="p-6 cursor-pointer font-semibold flex justify-between items-center">
          问题1：这是一个常见问题？
          <svg class="w-5 h-5 transition-transform">▼</svg>
        </summary>
        <div class="px-6 pb-6 text-gray-600">
          这是问题的详细回答...
        </div>
      </details>
      <!-- 更多问题 -->
    </div>
  </div>
</section>
```

## CTA设计原则

### 按钮文案
- ✅ "立即开始" "免费试用" "获取方案"
- ❌ "提交" "点击这里" "了解更多"

### 颜色对比
- CTA按钮使用高对比色
- 与页面主色形成视觉焦点

### 位置策略
- 首屏英雄区必须有CTA
- 每个主要区块后考虑添加CTA
- 页面底部添加最终CTA

## 转化优化清单

- [ ] 首屏在3秒内传达核心价值
- [ ] CTA按钮清晰可见
- [ ] 页面加载速度 < 3秒
- [ ] 移动端体验良好
- [ ] 社会证明充足（用户数、评价、合作伙伴）
- [ ] 无干扰元素（过多链接、弹窗）
- [ ] 表单字段最少化
- [ ] 信任标识（安全认证、退款保证）

---

# 移动应用设计指南

## 平台设计规范

### iOS 设计规范（Human Interface Guidelines）
```
屏幕尺寸：
  - iPhone SE: 375 x 667 pt
  - iPhone 14: 390 x 844 pt
  - iPhone 14 Pro Max: 430 x 932 pt
  - iPad: 768 x 1024 pt (竖屏)

安全区域：
  - 顶部状态栏: 47pt (刘海屏) / 20pt (非刘海)
  - 底部Home指示器: 34pt

最小触控尺寸: 44 x 44 pt

导航栏高度: 44pt
标签栏高度: 49pt (不含安全区)
```

### Android 设计规范（Material Design）
```
屏幕密度：
  - mdpi: 1x (160 dpi)
  - hdpi: 1.5x (240 dpi)
  - xhdpi: 2x (320 dpi)
  - xxhdpi: 3x (480 dpi)
  - xxxhdpi: 4x (640 dpi)

最小触控尺寸: 48 x 48 dp

App Bar高度: 56dp (手机) / 64dp (平板)
Bottom Navigation高度: 56dp
FAB尺寸: 56dp (默认) / 40dp (迷你)
```

## 移动端导航模式

### 底部标签栏
```html
<nav class="fixed bottom-0 left-0 right-0 bg-white border-t safe-area-bottom">
  <div class="flex justify-around py-2">
    <a href="#" class="flex flex-col items-center py-2 px-4 text-blue-600">
      <svg class="w-6 h-6">🏠</svg>
      <span class="text-xs mt-1">首页</span>
    </a>
    <a href="#" class="flex flex-col items-center py-2 px-4 text-gray-500">
      <svg class="w-6 h-6">🔍</svg>
      <span class="text-xs mt-1">发现</span>
    </a>
    <a href="#" class="flex flex-col items-center py-2 px-4 text-gray-500">
      <div class="relative">
        <svg class="w-6 h-6">🔔</svg>
        <span class="absolute -top-1 -right-1 w-4 h-4 bg-red-500 text-white text-xs rounded-full flex items-center justify-center">3</span>
      </div>
      <span class="text-xs mt-1">消息</span>
    </a>
    <a href="#" class="flex flex-col items-center py-2 px-4 text-gray-500">
      <svg class="w-6 h-6">👤</svg>
      <span class="text-xs mt-1">我的</span>
    </a>
  </div>
</nav>
```

### 顶部导航栏
```html
<header class="fixed top-0 left-0 right-0 bg-white border-b safe-area-top z-50">
  <div class="flex items-center justify-between h-11 px-4">
    <button class="w-10 h-10 flex items-center justify-center -ml-2">
      <svg class="w-6 h-6">←</svg>
    </button>
    <h1 class="font-semibold">页面标题</h1>
    <button class="w-10 h-10 flex items-center justify-center -mr-2">
      <svg class="w-6 h-6">⋮</svg>
    </button>
  </div>
</header>
```

## 移动端组件

### 列表项
```html
<ul class="bg-white divide-y">
  <!-- 基础列表项 -->
  <li class="flex items-center justify-between px-4 py-3 active:bg-gray-50">
    <span>设置项名称</span>
    <svg class="w-5 h-5 text-gray-400">→</svg>
  </li>

  <!-- 带图标列表项 -->
  <li class="flex items-center gap-4 px-4 py-3 active:bg-gray-50">
    <div class="w-10 h-10 bg-blue-100 rounded-lg flex items-center justify-center">
      <svg class="w-5 h-5 text-blue-600">📱</svg>
    </div>
    <div class="flex-1">
      <div class="font-medium">主标题</div>
      <div class="text-sm text-gray-500">副标题描述</div>
    </div>
    <svg class="w-5 h-5 text-gray-400">→</svg>
  </li>

  <!-- 带开关列表项 -->
  <li class="flex items-center justify-between px-4 py-3">
    <span>开关设置</span>
    <label class="relative inline-flex items-center cursor-pointer">
      <input type="checkbox" class="sr-only peer">
      <div class="w-11 h-6 bg-gray-200 rounded-full peer peer-checked:bg-blue-600 after:content-[''] after:absolute after:top-0.5 after:left-0.5 after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:after:translate-x-5"></div>
    </label>
  </li>
</ul>
```

### 卡片列表
```html
<div class="p-4 space-y-4">
  <div class="bg-white rounded-xl overflow-hidden shadow-sm">
    <img src="cover.jpg" class="w-full h-40 object-cover" />
    <div class="p-4">
      <h3 class="font-semibold">卡片标题</h3>
      <p class="text-sm text-gray-500 mt-1">卡片描述内容...</p>
      <div class="flex items-center justify-between mt-4">
        <div class="flex items-center gap-2">
          <img src="avatar.jpg" class="w-6 h-6 rounded-full" />
          <span class="text-sm text-gray-600">用户名</span>
        </div>
        <span class="text-sm text-gray-400">2小时前</span>
      </div>
    </div>
  </div>
</div>
```

### 操作表单（Action Sheet）
```html
<div class="fixed inset-0 bg-black/50 flex items-end">
  <div class="w-full bg-white rounded-t-2xl safe-area-bottom">
    <div class="p-4 text-center border-b">
      <div class="text-sm text-gray-500">选择操作</div>
    </div>
    <div class="divide-y">
      <button class="w-full py-4 text-center text-blue-600 active:bg-gray-50">
        分享
      </button>
      <button class="w-full py-4 text-center text-blue-600 active:bg-gray-50">
        保存
      </button>
      <button class="w-full py-4 text-center text-red-600 active:bg-gray-50">
        删除
      </button>
    </div>
    <div class="h-2 bg-gray-100"></div>
    <button class="w-full py-4 text-center font-medium active:bg-gray-50">
      取消
    </button>
  </div>
</div>
```

### 下拉刷新指示器
```html
<div class="flex items-center justify-center py-4">
  <svg class="w-6 h-6 animate-spin text-blue-600">⟳</svg>
  <span class="ml-2 text-sm text-gray-500">正在刷新...</span>
</div>
```

### 浮动操作按钮（FAB）
```html
<button class="fixed right-4 bottom-20 w-14 h-14 bg-blue-600 rounded-full shadow-lg flex items-center justify-center active:scale-95 transition-transform">
  <svg class="w-6 h-6 text-white">+</svg>
</button>
```

## 手势交互

### 常用手势
```
点击 (Tap): 触发主要操作
长按 (Long Press): 显示上下文菜单
滑动 (Swipe):
  - 左滑: 删除/显示操作
  - 右滑: 返回/标记
  - 下拉: 刷新
  - 上拉: 加载更多
捏合 (Pinch): 缩放
双击 (Double Tap): 快速操作（点赞/缩放）
```

### 滑动操作
```html
<div class="relative overflow-hidden">
  <!-- 滑动显示的操作按钮 -->
  <div class="absolute right-0 top-0 bottom-0 flex">
    <button class="w-20 bg-yellow-500 text-white flex items-center justify-center">
      编辑
    </button>
    <button class="w-20 bg-red-500 text-white flex items-center justify-center">
      删除
    </button>
  </div>

  <!-- 主内容（可滑动） -->
  <div class="bg-white px-4 py-3 relative z-10 transform transition-transform">
    列表项内容
  </div>
</div>
```

## 移动端表单

### 输入框
```html
<div class="px-4 space-y-4">
  <!-- 带标签输入框 -->
  <div>
    <label class="block text-sm text-gray-500 mb-1">用户名</label>
    <input type="text"
      class="w-full px-4 py-3 border rounded-xl focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20"
      placeholder="请输入用户名" />
  </div>

  <!-- 带图标输入框 -->
  <div class="relative">
    <svg class="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400">🔍</svg>
    <input type="text"
      class="w-full pl-12 pr-4 py-3 border rounded-xl"
      placeholder="搜索..." />
  </div>

  <!-- 验证码输入框 -->
  <div class="flex gap-3">
    <input type="text"
      class="flex-1 px-4 py-3 border rounded-xl text-center tracking-widest"
      maxlength="6" placeholder="验证码" />
    <button class="px-4 py-3 bg-blue-600 text-white rounded-xl whitespace-nowrap">
      获取验证码
    </button>
  </div>
</div>
```

## 安全区域适配

```css
/* iOS安全区域适配 */
.safe-area-top {
  padding-top: env(safe-area-inset-top);
}

.safe-area-bottom {
  padding-bottom: env(safe-area-inset-bottom);
}

/* 内容区域（考虑导航栏和标签栏） */
.content-area {
  padding-top: calc(44px + env(safe-area-inset-top));
  padding-bottom: calc(49px + env(safe-area-inset-bottom));
}
```

## 移动端设计原则

1. **拇指友好**：主要操作放在屏幕下半部分
2. **单手操作**：考虑单手使用场景
3. **最小触控区域**：按钮至少44x44pt
4. **即时反馈**：点击状态明显
5. **离线考虑**：设计离线状态和加载状态
6. **节省流量**：图片懒加载、渐进式加载
7. **电量友好**：减少动画、降低刷新频率

## 移动端测试清单

- [ ] 不同屏幕尺寸适配
- [ ] 横竖屏切换
- [ ] 刘海屏/挖孔屏适配
- [ ] 深色模式支持
- [ ] 字体大小调整（无障碍）
- [ ] 手势冲突检测
- [ ] 键盘弹出时的布局
- [ ] 网络状态切换

---

# 图标设计指南

## 图标尺寸规范

### 标准尺寸
```
16px - 小型图标（表格、列表项）
20px - 默认图标（按钮、输入框）
24px - 中型图标（导航、卡片）
32px - 大型图标（空状态、特性展示）
48px - 超大图标（落地页、英雄区）
64px - 巨型图标（引导页、插画）
```

### 触控目标
- 最小可点击区域：44x44px（iOS）/ 48x48px（Android）
- 图标周围保留足够的点击区域

## 推荐图标库

### 1. Heroicons（推荐）
```html
<!-- 引入方式 -->
<script src="https://unpkg.com/heroicons"></script>

<!-- 使用示例 -->
<svg class="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16" />
</svg>
```

常用图标：
- 导航：`menu`, `x`, `chevron-down`, `arrow-left`
- 操作：`plus`, `pencil`, `trash`, `download`, `upload`
- 状态：`check`, `x-circle`, `exclamation`, `information-circle`
- 用户：`user`, `users`, `user-circle`, `cog`
- 通用：`home`, `search`, `bell`, `mail`, `heart`

### 2. Lucide Icons
```html
<script src="https://unpkg.com/lucide"></script>

<!-- 特点：轻量、一致性好 -->
```

### 3. Phosphor Icons
```html
<script src="https://unpkg.com/phosphor-icons"></script>

<!-- 特点：6种粗细变体、2000+图标 -->
```

## SVG图标最佳实践

### 基本结构
```html
<svg
  xmlns="http://www.w3.org/2000/svg"
  viewBox="0 0 24 24"
  fill="none"
  stroke="currentColor"
  stroke-width="2"
  stroke-linecap="round"
  stroke-linejoin="round"
  class="w-6 h-6"
>
  <path d="..." />
</svg>
```

### 颜色控制
```html
<!-- 使用 currentColor 继承文字颜色 -->
<svg class="text-blue-500" fill="currentColor">

<!-- 或直接指定 -->
<svg class="fill-blue-500 stroke-blue-700">
```

### 尺寸控制
```html
<!-- Tailwind 方式 -->
<svg class="w-4 h-4">  <!-- 16px -->
<svg class="w-5 h-5">  <!-- 20px -->
<svg class="w-6 h-6">  <!-- 24px -->
<svg class="w-8 h-8">  <!-- 32px -->
```

## 常用图标SVG代码

### 菜单（Menu）
```html
<svg class="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16" />
</svg>
```

### 关闭（Close）
```html
<svg class="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12" />
</svg>
```

### 搜索（Search）
```html
<svg class="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
</svg>
```

### 用户（User）
```html
<svg class="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
</svg>
```

### 设置（Settings）
```html
<svg class="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
</svg>
```

### 勾选（Check）
```html
<svg class="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
  <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7" />
</svg>
```

### 加载（Loading Spinner）
```html
<svg class="w-6 h-6 animate-spin" fill="none" viewBox="0 0 24 24">
  <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
  <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
</svg>
```

## 图标设计原则

1. **一致性**：保持统一的线条粗细、圆角和视觉重量
2. **清晰性**：在小尺寸下依然清晰可辨
3. **简洁性**：去除不必要的细节
4. **可识别性**：遵循用户认知习惯
5. **可访问性**：提供适当的aria标签

## 图标无障碍

```html
<!-- 装饰性图标 -->
<svg aria-hidden="true">

<!-- 功能性图标 -->
<svg role="img" aria-label="搜索">

<!-- 按钮内图标 -->
<button aria-label="关闭菜单">
  <svg aria-hidden="true">...</svg>
</button>
```
