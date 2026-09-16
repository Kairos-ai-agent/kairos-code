---
name: "iot-product-design"
description: ">"
priority: 0.5
imported-from: "agents"
source-path: "agents/skills/software-development/iot-product-design/SKILL.md"
---
# IoT/传感器产品方案设计

## 核心设计原则

### 1. 相对测量 > 绝对阈值（关键教训）

传感器自动化产品设计中，**差分/速率测量远优于绝对阈值**：

```
❌ 绝对阈值：温度 ≥ 42°C → 启动
   → 冬季环境18°C需升温24°C，小火可能达不到
   → 夏季环境38°C只需升4°C，阳光照射就误触发

✅ 相对测量：30秒温升 ≥ 8°C 且 当前 ≥ 35°C → 启动
   → 冬季小火快速升温也能触发
   → 夏季环境缓慢变化不触发
```

**设计检验清单**：
- [ ] 阈值是否依赖环境基准？（是 → 必须改为相对测量）
- [ ] 季节变化是否影响检测？（是 → 必须动态跟踪基准）
- [ ] 缓慢环境变化是否误触发？（是 → 必须用速率而非绝对值）
- [ ] 是否有绝对保底防止传感器故障？（推荐保留一个安全底线）

### 2. 动态基准跟踪

环境基准不能固定，必须动态跟踪：

```
上电校准：采样30次取平均 → T_ambient_0
运行中（待机状态）：
  每60秒更新：T_ambient = T_ambient × 0.95 + T_current × 0.05
  → 缓慢跟踪环境变化
  → 烹饪/工作时停止更新（防止基准被拉偏）
```

### 3. 双条件AND触发

```
触发 = 速率条件 AND 绝对保底条件
  速率条件：30秒内变化量 ≥ 阈值（区分"快速事件"和"缓慢漂移"）
  保底条件：当前值 ≥ 安全线（防止传感器故障误触发）
```

## 标准方案文档结构

完整的产品方案文档应包含以下章节：

```
一、方案概述与设计目标
  1.1 背景
  1.2 可行性分析（绝对 vs 相对方案对比表）
  1.3 设计目标（指标表）
  1.4 工作原理

二、传感器选型
  2.1 推荐型号 + 备选型号（对比表）
  2.2 阻值-温度对照表
  2.3 选型对比
  2.4 供应商推荐

三、安装结构设计
  3.1 机型A安装方案（示意图 + 安装要点表）
  3.2 机型B安装方案
  3.3 两种机型对比

四、硬件电路设计
  4.1 传感器采样电路（原理图 + 参数计算）
  4.2 主控板集成方案
  4.3 独立模块方案（不修改原机）

五、主控程序设计
  5.1 软件架构（流程图）
  5.2 核心代码（完整C语言实现）
  5.3 状态机流程图
  5.4 与原有程序的集成方式

六、模拟测试报告
  6.1 测试环境
  6.2 测试项目与结果（每项含方法/数据/结论）
  6.3 测试总结表

七、BOM物料清单与成本估算

八、风险与注意事项
  8.1 技术风险
  8.2 安全合规
  8.3 后续优化方向
```

## HTML文档输出规范

输出为单HTML文件，使用深色主题：

```css
/* 必要样式 */
body { background: #0d1117; color: #c9d1d9; }
h1 { color: #58a6ff; border-bottom: 2px solid #30363d; }
h2 { color: #79c0ff; border-left: 4px solid #58a6ff; padding-left: 12px; }
table { border-collapse: collapse; }
th { background: #161b22; color: #58a6ff; }
.code-block { background: #161b22; font-family: monospace; }
.schematic { background: #0d1117; color: #7ee787; font-family: monospace; }
.highlight.green { border-left-color: #3fb950; }
.tag-ok { background: #1a3a2a; color: #3fb950; }
.tag-warn { background: #3a2a1a; color: #f0883e; }
```

## 可行性分析模板

在方案设计初期，必须先做可行性分析，用表格对比不同方案：

```html
<table>
<tr><th>场景</th><th>方案A问题</th><th>方案B优势</th></tr>
<!-- 列出所有极端场景 -->
<!-- 冬季/夏季/春秋 -->
<!-- 正常/异常/边界 -->
</table>
```

## Pitfalls

1. **绝对阈值不自适应**：固定阈值在不同季节/地域表现差异巨大，必须用相对测量
2. **环境基准漂移**：不跟踪基准会导致厨房逐渐变热后误触发或漏触发
3. **瞬间干扰**：必须用"持续确认"（如15秒）防止打火枪等瞬间干扰
4. **传感器老化**：NTC年漂移<0.5%，但长期需考虑校准机制
5. **安装位置关键**：传感器距热源太远响应慢，太近易受油烟污染
6. **MCU资源**：30秒历史缓冲仅需120字节RAM（float×30），8位MCU可承受
7. **Windows .bat文件**：如方案包含安装脚本，必须ASCII-only（用户环境痛点）
8. **HTML→PDF转换**：Windows上Python PDF库（weasyprint/xhtml2pdf）常缺GTK/Cairo原生依赖。最可靠方案是Chrome/Edge headless：
   ```
   chrome.exe --headless --disable-gpu --print-to-pdf="输出路径.pdf" --no-margins --print-to-pdf-no-header "file:///源文件.html"
   ```
   Edge同理（`msedge.exe`）。无需安装额外Python包。
   - `--no-margins`：去掉默认页边距
   - `--print-to-pdf-no-header`：**必须加**，否则PDF每页顶部有文件URL、底部有页码，用户会要求去除
   - 生成前确保目标PDF未被其他程序打开（否则报"另一个程序正在使用此文件"），先关浏览器再生成
9. **PDF输出规范**：用户要求PDF白底黑字、无外部链接、无页眉页脚地址、无导航锚点。HTML转PDF前需：替换深色主题为印刷友好配色、去除TOC anchor links、去除HTML注释、去除viewport meta
10. **fpdf2生成中文PDF（Windows备选方案）**：当Chrome headless不可用时，用fpdf2+SimHei字体生成PDF：
    - 字体注册：`pdf.add_font('H', '', r'C:\Windows\Fonts\simhei.ttf')` — 所有中文文本必须用此字体
    - **不能用Courier/core字体渲染中文**（报latin-1编码错误），schematic/code块也必须用SimHei
    - fpdf2弃用参数：`ln=True` → `new_x="LMARGIN", new_y="NEXT"`
    - Unicode转义字符串中不能混入裸中文字符（如`\u8距`），必须全部用`\uXXXX`或全部用裸中文
11. **用户文档偏好**：
    - 用户要求新方案时，**不要与旧方案做对比**（"去除NTC方案对比"）
    - 多个方案应**分别出独立文档**，不做交叉比较（"B和C方案各做一份，不要做比较"）
    - 方案简化时只保留用户要求的功能（"不做测温调档，只做自动开机与关机"）
12. **低成本传感器选型原则**：
    - 精确测温 → MLX90614（I2C，¥8-12，需MCU）
    - 仅检测"有/无高温热源" → 热电堆TS4148（模拟输出，¥1-2，纯模拟电路即可）
    - **PIR热释电不适合厨房**：检测运动中的热源，人走动必然误触
    - 热电堆检测辐射强度（灶面300°C vs 人体37°C差2个数量级），物理原理上区分人与灶台
