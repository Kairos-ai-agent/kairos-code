---
name: "hw-design-pdf"
description: "Umbrella for hardware design document generation, PCB design review, fabrication workflows, and formal multi-version design review reports. Covers: HTML→Edge→PDF pipeline, SVG block diagrams, fpdf2 Chinese PDF generation, hardware design document creation, design revision from evaluation feedback, Gerber/PCB coordinate parsing, PCB fabrication export (KiCad), PDF annotation, and complete .md review reports (评审意见) with score tables / signature blocks / action items for hardware/software/architecture design docs. Consolidated umbrella for: hw-design-pdf, hardware-design-document, hardware-design-review, hardware-design-revision, kicad-pcb-automation, iot-product-design, design-review."
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/hardware/hw-design-pdf/SKILL.md"
---
# Hardware Design PDF Generation (Windows + Edge)

## Workflow (7 steps)

1. **Read all inputs** — design doc, evaluation reports, any attached files.
   Identify every issue by severity (CRITICAL / HIGH / MEDIUM / LOW). Log to
   todo as items.

2. **Apply fixes** — address CRITICAL and HIGH issues in order. For hardware:
   signal-chain magnitude mismatches, missing IC configuration pins,
   thermal/power budget violations, ESD, EMC, and test-coverage gaps.

3. **Create a single standalone HTML file** — NEVER include change logs,
   version comparisons, revision history, or "what changed from vX" sections.
   The deliverable is a complete, final document only.

4. **Draw the block diagram as inline SVG** (NOT schemdraw — its SVG output
   contains NaN coordinates and bad stroke-dasharray values that break most
   PDF converters). Hand-write the SVG with `<rect>`, `<text>`, `<line>`,
   `<polygon>` elements. Keep the viewBox around 780×400 for A4 fit. Label
   functional blocks clearly; do NOT attempt component-level circuit schematics
   (those belong in an EDA tool, not HTML SVG).

5. **Use tables for all structured data** — specs, BOM, pinout, test matrix,
   parameter appendix. Use `<table>` with alternating row colors (`#f2f6fc`).

6. **Convert to PDF via Edge headless** (only method that works reliably on
   this Windows system with Chinese fonts):
   ```
   taskkill /F /IM msedge.exe  2>/dev/null
   sleep 1
   rm -f "out.pdf"
   "/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" \
     --headless \
     --print-to-pdf="C:/path/to/out.pdf" \
     --no-margins \
     "file:///C:/path/to/report.html"
   ```
   If the file is locked (previous Edge process), use a temp filename then
   `mv` to the final name. Clean up the `.html` temp file afterward.

7. **Clean temp files** — remove the `.html` source, any test SVGs, Python
   scripts.

## Document type 2: Development Process Report (开发过程报告)

For retrospective version-history documents covering multi-iteration engineering projects (v1.1 → v3.0 style). Different from a design spec — this is a chronological narrative.

### Structure (14-chapter template)

| # | Chapter | Content |
|---|---------|---------|
| 1 | Cover | centered title, subtitle "从概念到可量产", company, date, project cycle |
| 2 | TOC | auto-generated via `<ol>` |
| 3 | Background & overview | project goal, design constraints, sensing principle |
| 4 | Version timeline | 8+n versions in a CSS vertical timeline with colored bullets |
| 5–12 | Per-version deep-dive | one chapter per version: context → changes → evaluation findings |
| 13 | Metrics comparison | side-by-side table of ALL versions for key technical specs |
| 14 | Cost evolution | table tracking BOM cost per version + ROI analysis |
| 15 | Test matrix evolution | table showing which tests were added/removed per version |
| 16 | Summary | core achievements, decision postmortem, methodology lessons, future roadmap |

### CSS vertical timeline (for version chronology)

```css
.timeline { position:relative; margin:4mm 0 4mm 20px; padding-left:30px; }
.timeline::before {
  content:''; position:absolute; left:8px; top:0; bottom:0;
  width:2px; background:#1a3a5c;
}
.tl-item { position:relative; margin-bottom:4mm; }
.tl-item::before {
  content:''; position:absolute; left:-26px; top:6px;
  width:12px; height:12px; border-radius:50%;
  background:#1a3a5c; border:2px solid #fff;
}
.tl-date { font-size:9pt; color:#888; }
```

Use `.tl-item.v3::before { background:#c0392b; }` to mark the final/current version.

### Multi-version comparison table

```html
<tr><th>指标</th><th>v2.3</th><th>v2.4</th><th>v2.5</th><th>v3.0</th></tr>
<tr><td>前置放大</td><td>❌ 无</td><td>MCP6001 471x</td><td>MCP6V51 101x</td><td>MCP6V51 101x + 限幅</td></tr>
```

Use colored tags for status: `<span class="tag tag-red">` for blocking issues, `tag-yellow` for warnings, `tag-green` for resolved.

### Decision postmortem table

```html
<tr><th>决策</th><th>版本</th><th>背景</th><th>结果</th></tr>
<tr><td>CD4541 替代 NE555</td><td>v2.3</td><td>高阻定时的湿漏问题</td><td>✅ 正确</td></tr>
```

### Cover-page differences (vs design spec)

- Add project cycle span: "项目周期：YYYY-MM-DD — YYYY-MM-DD（N 天密集迭代）"
- Tagline: "从概念到可量产 — 完整版本迭代与技术演进记录"
- NO block diagram on cover (use timeline instead)

## Page layout rules

- `@page` size: A4, margins 14mm left/right, 14mm top, 16mm bottom
- Cover page: `page-break-after: always`, vertically centred title + tags +
  company + date. NO header/footer on cover (`@page cover { ... }`).
- Schematic page: title + SVG + one-line signal-flow description.
- Content pages: `<h2>` (blue underline) → `<h3>` → `<p>` / table.
- Tables: `<th>` with `#1a3a6b` background, alternating `#f2f6fc`.
- Code blocks: `<div class="code">` with `#f0f4fa` bg and `3px #1a3a6b` left border.
- Footer: centred page number.

## Block diagram SVG style

- viewBox: ~780×400 (fits A4 at 100% width)
- Signal blocks: `#e8f0fe` fill, `#3a6bb5` stroke, 1.5px
- Power/Output blocks: `#fef3e2` fill, `#d48806` stroke
- New/changed blocks (v2.5+ style): `#d4edda` fill, `#28a745` stroke
- Arrows: `<marker>` with `#3a6bb5` fill
- Font: `'Microsoft YaHei', sans-serif`, 6.5-8pt
- Protection summary bar at bottom: `#f0f4fa` fill, single row
- Improvement annotation bar: `#e8f0fe` fill below main blocks

## Must-fix checklist (from prior evaluation history)

- [ ] **Signal chain**: Thermopile output (µV-mV) must be amplified before
      comparator (MCP6V51, Gain≈100). R_bias must be 10K (NOT 100K) to avoid
      36% signal attenuation with TS4148's ~180K source impedance.
- [ ] **CD4541 config**: MODE(P10)=GND, AR(P5)=GND, RS(P5-标)=VDD(Q̅).
      Document ALL 16 pins in a full pin table.
- [ ] **7805 thermal**: Use DPAK(TO-252). 0.35W max (heater on 12V direct).
      1cm² copper + 5×5 via array. θJA≈35-40°C/W, Tj<100°C.
- [ ] **LED drive**: CD4541 Q → R_LED 1K → LED → GND. Running=Q=LOW→ON
      (current sink). NEVER write "Q=HIGH=亮" — it's the opposite.
- [ ] **No change log**: User explicitly rejects version-comparison sections.
      Zero revision history, zero "what changed" callouts in the final output.

## Document Evaluation & Annotation (Mark Up Existing PDFs)

When asked to **evaluate / review / assess** an existing design document and output a corrected version:

### Pre-requisite: Determine abstraction level

CRITICAL — determine what type of document you're evaluating BEFORE diving into detail:

| Document type | What to evaluate | What NOT to evaluate |
|---|---|---|
| 系统框图 (System Block Diagram) | Module partitioning, signal flow, bus topology, comm interfaces, power domains, safety redundancy | Missing CAN transceivers, missing TVS diodes, missing gate driver ICs — those are detailed schematic level |
| 详细原理图 (Detailed Schematic) | Component selection, signal chain gain/offset, protection circuits, decoupling, thermal, pin connections | Everything above IS fair game |
| PCB 布局 (PCB Layout) | Placement, routing, stackup, clearance, thermal, EMI | Architecture decisions are already locked |

Pitfall: If you evaluate a 系统框图 as a detailed schematic, the user will correct you ("这个是系统框图"). The 框图 shows what talks to what, not which pin connects to which resistor.

### Workflow: Annotate corrections on the original PDF

When the user expects corrections ON the original document (not a separate report):

1. Do NOT create a separate HTML/PDF — the user will reject it as "乱七八糟" (messy)
2. Use PyMuPDF (fitz) to annotate directly on the original PDF:
   - `page.add_rect_annot(Rect(...))` for boxes (set_border with dashes for dashed lines)
   - `page.add_line_annot(Point(...), Point(...))` for connector lines
   - `page.insert_text(Point(...), text, fontname="Helvetica-Bold", ...)` for labels
   - `page.new_shape().draw_rect(...).finish(fill=color, fill_opacity=alpha)` for semi-transparent fills
3. Font: "Helvetica-Bold" works with `get_text_length()` (NOT "helvb"); "courier" works too
4. Coordinate estimation: `page.rect` gives dimensions (e.g. 1191x842 for A4 landscape). Estimate layout coordinates by visual inspection
5. Color convention: Orange dashed = structural changes; Red solid = hardware must-fix; Purple dashed = safety; Gold fill = area modified
6. Add a legend at page bottom explaining color meanings
7. Save: `doc.save("output.pdf", garbage=4, deflate=True)`

### Pitfall: Don't create parallel files when user expects inline annotation

The user's explicit preference: apply corrections directly to the original document. Creating separate HTML reports or new PDFs from scratch when the task is "assess this and fix it" will be rejected. Exception: when the task is "write an evaluation report" or "generate a new version of the design from scratch", then HTML→Edge→PDF is the correct path.

## Formal Design Review Reports (评审意见)

When the user asks to **review/evaluate** a multi-version design document (V0.X, V1.X, etc.) and produce a structured .md review report (评审意见) saved alongside the source — distinct from annotating existing PDFs above — use this workflow.

**Output**: A single `.md` file named `<source>_评审意见.md` next to the source. Contains score table, issue list grouped by severity (🔴/🟡/🟢), signature block, action items. Works for hardware / software / architecture design docs (not domain-specific).

### Pre-review setup (critical)

Before writing any review:

1. **`search_files` the project directory** to enumerate ALL existing document versions. Missing a version is the #1 pitfall. Each project has a version chain (V0.1 → V0.2 → ...) and you must verify each link.
2. **Read all upstream review opinions** for the version chain (`XXX_V0.1_评审意见.md`, etc.). They tell you what was previously identified as a problem and what the current version must address.
3. **Identify companion documents** — design drafts come in pairs/groups: 系统设计稿 + 原理图稿, V0.X 系列 + V1.0X 系列.
4. **Read the target document fully**, including tables, code blocks, and BOM sections. Truncated reads miss data inconsistencies.

### Review process

**Step 1**: Classify the version's nature — 增量修正版 (incremental fix), 综合改进版 (comprehensive improvement), 文档定稿版 (finalization), or 初版 (first version).

**Step 2**: Build the response matrix — for each previously identified issue, mark ✅ Resolved / ⚠ Partially / ❌ Not resolved / ➕ New issue introduced.

**Step 3**: Cross-check consistency — for documents in families (system + schematic + PCB): pin assignments must match, BOM numbers must be internally consistent, code snippets must match hardware pin labels, DTC tables / fault codes / error names must match across system + schematic.

**Step 4**: Score and recommend — rate 0~5 stars across 4-6 dimensions (文档结构 / 响应度 / 一致性 / 工程可落地性 / 自洽性 / 综合). End with clear recommendation: 通过 / 有条件通过 / 不通过 + next-version plan.

### Standard 10-section report template

```
# <Document Name> V?.? — 评审意见

## 0. 总体评价 (3-5 line summary + verdict)
## 1. <Prior review> 响应度核对 (response matrix table)
## 2. <Current version> 引入的新问题 (grouped by severity 🔴/🟡/🟢)
## 3. 设计稿优点 (5-10 strengths)
## 4. <Cross-document> 一致性核对
## 5. 80 天里程碑评估 (or project milestone)
## 6. 评审行动清单 (must-resolve at review meeting + D-stage actions)
## 7. 综合评分 (per-dimension star table)
## 8. 评审签字 (per-item checkbox + 4-5 role signature row)
## 9. 评审结论 (通过 / 有条件通过 / 不通过 + special note)
## 10. 附注 (change verification checklist + cross-version evolution table)
```

Full template lives at `templates/review-report-template.md` and `references/template.md`. Domain-specific checklists (engineering/hardware, software/system) at `references/checklist.md`.

### Engineering / Hardware checklists

When the design doc covers hardware, also check:

- **Pin Allocation**: same pin multiple functions (e.g., PA13/PA14 on STM32 are SWD default); EXTI/PWM/UART/ADC primary function conflicts; JTAG/SWD conflicts
- **Power Topology**: 24V/12V→5V→3.3V sequence; 常电 vs 钥匙控制; 防反接 (PMOS) drain-source; 上电时序; inrush current
- **Communication Interfaces**: TX/RX 接线交叉; CAN/485 终端电阻 (120Ω at both ends); CAN 共模电感 90Ω@100MHz; 波特率/帧格式
- **BOM Consistency**: 总和 vs 单项加总; 同型号不同单价; 数量与原理图; 选焊 vs 量产 DNP
- **DTC / Fault Code**: DTC 编号唯一; 严重度分级 (1=提示 / 2=警告 / 3=严重 / 4=紧急); 触发条件; 故障码 vs 代码一致
- **EMC/ESD**: ESD 阵列在每个外露接口; TVS 选型 (5.1V for 3.3V signals, 12V for 12V rails); 共模电感位置
- **Thermal**: 功率器件散热; 功耗核算; 结温 Tj < 125℃

### Software / System checklists

Two-pass review structure: Pass A (response rate to previous review) + Pass B (new issues introduced). Common patterns: pin/peripheral conflicts when moving pins, BOM spec inconsistencies when updating component specs, power topology inconsistencies, software/hardware timing mismatches, cross-reference inconsistencies, lost capability when refactoring, newly-required features not implemented, components referenced but missing from BOM.

### 16 cross-version pitfalls (lessons from real reviews)

1. **Avoid absolute technical assertions** — don't write "X 不支持 Y" unless 100% verified from datasheet
2. **When fixing old bugs, scan for new ones** — fixing one bug by moving pins can lose a feature
3. **Multi-section consistency** — specs in multiple places must agree
4. **BOM specs must be datasheet-traceable** — every component's voltage/current spec must match its datasheet
5. **Lab scheduling reality** — EMC 测试排队 2-3 周 + 测试 + 整改 2-3 轮，"10 天 EMC 整改" 不现实
6. **Never sign for the user** — the 签字栏 must be empty (☐ 占位) for actual signers to fill
7. **Multi-version continuity** — each 评审意见 should reference the previous one
8. **Watch for 引脚冲突 across multi-version docs** — pin allocated to A in V(N-1) might be reallocated to B in V(N) without removing A
9. **Hardware schematic-level vs system design** — different evaluation dimensions for different document layers
10. **"追加式合并" failure** — when the doc claims "合并版 V1.0 = V0.6 + ... + V1.0c", later version should OVERWRITE not APPEND
11. **"TX/RX label cross-contradiction"** — schematic top shows one direction, pin table bottom shows the other
12. **Software-hardware DTC mismatch** — code references 0x91 but DTC table defines 0x91 as a different fault
13. **User correction pattern: "严格意义上不准确"** — acknowledge, demote severity (🔴 → 🟡), add verification step, provide alternatives
14. **Distinguish "incremental fix version" vs "comprehensive evaluation response version"** — V(N+1) may be responding to a separate comprehensive evaluation, not iterating V(N)
15. **Don't confuse "reviewing the design document" with "summarizing the user's prior review"** — fresh evaluation vs rehashing
16. **Don't preserve raw calculation errors + correction process in published documents** — show only final correct values

### Reference

- `references/template.md` — full 10-section report template
- `references/checklist.md` — 12-phase pre-submit sanity check
- `references/version-tracking.md` — rules for tracking pin conflicts, BOM totals, score trajectories
- `references/mcu-pin-conflict-check.md` — detailed MCU pin conflict checklist (STM32 / HC32F460)
- `references/embedded-pin-conflict-checklist.md` — embedded pin conflict patterns
- `references/schematic-merge-pitfalls.md` — schematic merge failure modes
- `references/new-issue-scan-checklist.md` — new-issue scan patterns for hardware/system
- `references/review-report-template.md` — alternative report template
- `templates/review-report-template.md` — copy-paste starter template
- `templates/design-review-report.md` — design review report template

## Pitfalls

- **schemdraw SVG has NaN + bad dasharray**: Its SVG output always contains
  `<polygon points="nan,nan ...">` and `stroke-dasharray:-;` which crash
  fpdf2, weasyprint, and Edge. Do NOT use schemdraw output directly.
- **fpdf2 lacks proper SVG/HTML rendering**: It fails on SVG with dasharray
  issues and has poor Chinese-unicode support (missing combining chars like
  U+0305). Edge headless is the only reliable path.
- **File-lock race on Edge**: If a previous Edge process holds the output PDF,
  the `--print-to-pdf` silently fails. Always `taskkill /F /IM msedge.exe`
  then `sleep 1` before each conversion. Use a temp filename as a fallback.
- **Vos × gain**: Cheap op-amps (MCP6001, Vos=±4mV) × high gain (471×) →
  ±1.9V DC offset, saturating the output. Always use zero-drift op-amps
  (MCP6V51, Vos≈2µV) when gain > 50×.
- **Power LEDs from CD4541 Q**: Q is LOW during MR=HIGH (running). A
  LED→R→Q(LOW) circuit sinks current and lights up. Document the polarity
  correctly — it is NOT intuitive.

## Reference files

| File | Covers |
|------|--------|
| `references/thermopile-range-hood-controller.md` | Full CD4541 timing analysis (on/off delay calculation, level-sensitive MR, retriggerable state machine, low-impedance anti-drift, MR filter), signal chain, pin table, power budget, protection stack, LED semantics, PDF gen command |
| `references/kicad-mcp-setup-windows.md` | KiCad MCP server setup on Windows |
| `references/pymupdf-pdf-annotation.md` | PyMuPDF inline annotation on existing PDFs |

## Document type 3: Analysis / Evaluation Report (fpdf2)

For text-heavy analysis reports where the primary deliverable is structured analysis rather than visual design (timing analysis, failure analysis, design review). Use **fpdf2** instead of the HTML→Edge→PDF pipeline — simpler, faster, no Edge file-lock issues, better for CJK text-heavy documents.

### When to use fpdf2 vs HTML→Edge→PDF

| Criterion | Use fpdf2 | Use HTML→Edge→PDF |
|-----------|-----------|-------------------|
| Content type | Text-heavy analysis, tables, simple diagrams | Visual block diagrams, design specs, BOM-heavy |
| Pages | ≤ 12 pages | Any length |
| Diagrams | Simple text-based flow, timing charts with colored cells | Complex SVG block diagrams, multi-layer schematics |
| CJK fonts | msyh.ttc via add_font() | Microsoft YaHei via CSS |
| Output quality | Good for structured reports | Better for design specs |

### fpdf2 Report Template Patterns

**Font setup (Windows + msyh.ttc):**
```python
from fpdf import FPDF
pdf = FPDF()
pdf.add_font('yh', '', 'C:/Windows/Fonts/msyh.ttc')
pdf.set_auto_page_break(auto=True, margin=18)
```

**Table with multi-line cells + alternating row colors:**
```python
pdf.table(
    headers=['Col1', 'Col2', 'Col3'],
    rows=[['data', 'data', 'data']],
    col_widths=[40, 80, 60]
)
```

**Section formatting:**
```python
def sect(self, title):
    self.set_font('yh', '', 13)
    self.set_text_color(30, 60, 120)
    self.cell(0, 9, title, new_x='LMARGIN', new_y='NEXT')
    self.set_draw_color(30, 60, 120)
    self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
    self.ln(4)
```

**Pitfalls when using fpdf2 for Chinese reports:**
- Combining accents (U+0305 overline) missing in msyh.ttc
- `\n` embedded in cell text → must use multi_cell()
- Deprecated `uni=True` in add_font() — omit in fpdf2 v2.8+
- Deprecated `ln=True` / `ln=1` — use `new_x='LMARGIN', new_y='NEXT'`
- Table height estimation — always pre-measure with `split_only=True`

---

## IoT Sensor Product Design (from `iot-product-design`)

IoT/传感器产品方案设计工作流。核心设计原则：

### 1. 相对测量 > 绝对阈值（关键教训）
传感器自动化产品设计中，**差分/速率测量远优于绝对阈值**：
- ❌ 绝对阈值：温度 ≥ 42°C → 冬季/夏季差异巨大
- ✅ 相对测量：30秒温升 ≥ 8°C AND 当前 ≥ 35°C → 自适应

### 2. 动态基准跟踪
上电校准采样30次取平均 → 运行中每60秒指数平滑更新基准 → 烹饪/工作时停止更新。

### 3. 双条件AND触发
触发 = 速率条件 AND 绝对保底条件。

### 标准方案文档结构
方案概述 → 传感器选型 → 安装结构设计 → 硬件电路设计 → 主控程序设计 → 模拟测试报告 → BOM与成本 → 风险与注意事项。

### 高压绝缘设计
传感器安装在**大功率家电外部**时（抽油烟机、集成灶），整机需通过Hi-Pot耐压测试。核心方案：PET麦拉片垫在安装面消除电位差。参数速查：PET≥0.125mm(~25kV), Kapton≥0.05mm(~14kV), 硅胶≥1mm(~10kV), FR4≥1.6mm(~40kV)。关键避坑：金属螺丝固定=短路；麦拉片边缘爬电必须每边宽5-10mm；出线口加橡胶护线套。

### 低成本传感器选型
- 精确测温 → MLX90614（I2C，¥8-12）
- 仅检测"有/无高温热源" → 热电堆TS4148（模拟输出，¥1-2）
- PIR热释电不适合厨房（检测运动中热源）
- 热电堆检测辐射强度（300°C灶面 vs 37°C人体差2个数量级）

### 文档输出
HTML深色主题（GitHub-style）或 HTML→Edge→PDF。用户偏好：新方案不与旧方案对比；多个方案分别出独立文档；方案简化只保留用户要求的功能。
---

## Absorbed Skills

### Hardware Design Document (from `hardware-design-document`)
Generate professional hardware engineering design documents: block diagrams as inline SVG (NOT component-level schematics), signal chain analysis, BOM, test matrices, protection/thermal analysis. 8-page template: cover → block diagram → overview → circuit details → power/BOM → installation/test → highlights/appendix. Deliver as final version only — NO change logs, NO version comparisons. HTML→Edge→PDF pipeline.

### Hardware Design Review (from `hardware-design-review`)
Cross-reference design PDFs against production files (PCB coordinates, BOMs, Gerber files). Gerber parsing: RS-274X format, panelization analysis from drill coordinates, board outline extraction. PCB coordinate CSV parsing for component position verification. Algorithm diagnosis: baseline drift, hard threshold dead zones, ADC saturation, excessive debounce, window size mismatch. Analog circuit evaluation: open-drain interactions, 555 timer lock, relay brown-out, temperature drift, thermopile compensation, optical window contamination. Optocoupler selection guide. Structured evaluation report format: merits → risks → recommendations.

### Hardware Design Revision (from `hardware-design-revision`)
Revise hardware designs based on evaluation feedback. Issue classification: P0 (logic/functional), P1 (datasheet mismatch/wrong calc), P2 (missing coverage), P3 (missing docs). Analog circuit fixes: non-retriggerable monostable, CD4541 level-sensitive MR, hysteresis notation errors, power spec contradictions. Clean final document: NO changelog, NO "changes from vX", self-contained. System block diagram review: module partitioning, signal flow, bus architecture, power domains, safety redundancy. PDF annotation with PyMuPDF: exact word coordinates, annotation styles, color convention, legend.

### KiCad PCB Automation (from `kicad-pcb-automation`)
Programmatic PCB creation via pcbnew Python API: NewBoard, FOOTPRINT, PAD helpers, board outline, mounting holes, silkscreen. kicad-cli commands: DRC, Gerber export, drill file, pick-and-place, 3D render, BOM export. Common pad coordinates reference table. Design evaluation report generation: PDF extraction, design analysis (MCUs, power, signal chain, comms, motor drive, safety), HTML report with severity tags, Edge→PDF conversion.
