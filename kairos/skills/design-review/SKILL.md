---
name: "design-review"
description: "Generate complete .md review reports for multi-version design documents (hardware/software/architecture). Use when user asks to \"评审\" (review) a design document (V0.X, V1.X), upload a new version of a"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\you\\AppData\\Local\\hermes\\skills\\.archive\\design-review\\SKILL.md"
---
# Design Review Skill

## When to use this skill

Trigger when the user asks for a structured review of a technical design document and wants the output saved as a standalone `.md` file. Specifically:

- User uploads a design document version (`V0.X`, `V1.X`, etc.) and says "评审", "评估", "看看", "review"
- User mentions "出报告", "出评审意见", "设计稿评审", "出 .md"
- Multiple versions of the same design exist and need cross-version consistency check
- The user is iterating on a design and wants per-version review reports for archival

**Don't use for**: code reviews (different skill needed), informal feedback, single-line evaluations.

## Pre-review setup (CRITICAL — read this first)

Before writing any review:

1. **`search_files` the project directory** to enumerate ALL existing document versions. Missing a version is the #1 pitfall — the user will catch it (e.g., "设计稿没有评估吗"). Each project has a version chain (V0.1 → V0.2 → ...) and you must verify each link.
2. **Read all upstream review opinions** for the version chain (`XXX_V0.1_评审意见.md`, `XXX_V0.2_评审意见.md`, ...). They tell you what was previously identified as a problem and what the current version must address.
3. **Identify companion documents** — design drafts come in pairs/groups:
   - System design draft (系统设计稿) + Schematic design draft (原理图稿)
   - Architecture doc + Implementation doc
   - V0.X 系列 + V1.0X 系列 (D6~D10 阶段)
4. **Read the target document fully**, including tables, code blocks, and BOM sections. Truncated reads miss data inconsistencies.

If you discover an existing document version you didn't know about (the user already produced V0.7 when you thought it was "planned"), review it immediately rather than deferring.

## Review process

### Step 1: Classify the version's nature

- **增量修正版 (incremental fix)**: addresses specific issues from previous review (N1, N2, ...). Focus on whether each item is resolved AND whether new issues emerged.
- **综合改进版 (comprehensive improvement)**: addresses an external evaluation (e.g., "8.8/10 评估"). Focus on coverage breadth and self-consistency.
- **文档定稿版 (document finalization)**: addresses documentation-level issues (formatting, consistency). Often small, focused changes.
- **初版 (first version)**: full review against requirements baseline.

### Step 2: Build the response matrix

For each previously identified issue, mark:
- ✅ Resolved (with chapter reference)
- ⚠ Partially resolved (what's missing)
- ❌ Not resolved (still problematic)
- ➕ New issue introduced

Track new issues separately. They are the value-add of the current review.

### Step 3: Cross-check consistency

For documents that come in families (system + schematic + PCB):
- 引脚分配 (pin assignments) must match across all docs
- BOM numbers must be internally consistent
- Code snippets must match hardware pin labels
- DTC tables, fault codes, error names must match across system + schematic

Document every inconsistency as a new issue with severity rating.

### Step 4: Score and recommend

Rate 0~5 stars or X/10 across 4-6 dimensions:
- 文档结构 (document structure)
- 响应度 (response to prior review)
- 一致性 (cross-document consistency)
- 工程可落地性 (engineering implementability)
- 自洽性 (internal self-consistency)
- 综合 (overall)

End with a clear recommendation: 通过 / 有条件通过 / 不通过 + next-version plan.

## Standard review report template

The review output is a single `.md` file saved **alongside the source document** with the naming convention `<source>_评审意见.md`. Full 10-section template is in `references/template.md`. Structure:

```
# <Document Name> V?.? — 评审意见

## 0. 总体评价
[3-5 line summary + verdict]

## 1. <Prior review> 响应度核对
[Table: Issue | Prior state | Current response | Status]

## 2. <Current version> 引入的新问题
[Grouped by severity: 🔴 严重 / 🟡 重要 / 🟢 一般]
[For each: location, problem, impact, fix]

## 3. 设计稿优点
[5-10 strengths]

## 4. <Cross-document> 一致性核对
[Table for each comparison dimension]

## 5. 80 天里程碑评估 (or project milestone)
[Table: Stage | Days | Assessment | Feasibility]

## 6. 评审行动清单
[6.1: must-resolve at review meeting (date: meeting day)]
[6.2: D-stage actions]

## 7. 综合评分
[Table per dimension with star ratings]

## 8. 评审签字
[8.1: per-item checkbox table]
[8.2: signature row with 4-5 roles]

## 9. 评审结论
[通过 / 有条件通过 / 不通过 + special note]

## 10. 附注
[10.1: change verification checklist]
[10.2: new issues for next-stage handling]
[10.3: cross-version evolution table]
[10.4: full version evolution summary]
[10.5: archival path]
```

## Pitfalls (lessons learned)

- **漏评已存在文档**: Never mark a document as "计划" (planned) without first `search_files`-checking it doesn't already exist. This is the #1 source of user complaints in iterative review projects. (User explicitly flagged this with "设计稿没有评估吗" when I missed V0.7.)
- **评审报告不完整**: Missing signature block, score table, or action items → user complains report is useless for signing off.
- **评审意见不一致**: Saying "issue X resolved" in current review while issue X is still open in cross-document consistency check → user loses trust in review accuracy.
- **不引用前序评审**: Skipping the "响应度核对" table → user can't see whether prior issues were addressed.
- **过度承诺**: Rating a version "通过" while listing 5 new issues → user pushes back ("评分太低").
- **评分不连贯**: Each version's score should be derivable from prior version + new issues. Big jumps signal inconsistent judgment.
- **归档命名错误**: Using `V1.0_评审意见.md` instead of `V1.0a_评审意见.md` → file can't be paired with source.
- **遗漏签字栏**: Reports without signature block can't be signed off, defeating the purpose.
- **跨文档一致性漏核对**: Missing the 引脚冲突/故障码/版本号 mismatch between system + schematic docs → user catches it in PCB review.
- **过长输出**: Reports over 20KB for small increments → user pushes for conciseness. Aim for proportional depth: small fixes = 5-8KB report, big reviews = 15-20KB.

## User preferences (embedded from this project)

User 华士林 preferences for review reports in this project:
- Output is always **complete .md file**, not just terminal summary
- File saved **alongside source document** (e.g., `XXX_V?.?_评审意见.md`)
- Must contain: 评分表 (score table) + 问题清单 (issue list) + 签字栏 (signature block) + 下步行动 (next-step actions)
- Bilingual: Chinese sections with English technical terms
- Tone: 精炼直接 (concise and direct), no verbose explanation
- When user says "还是不对" / "评分太低" / "还不够" → immediately iterate to next-level fix, no defense
- When user says "每次评审要输出完整 .md 报告" → apply this skill every review session

## Skill composition

For hardware design reviews (typical pattern):
- Combine with **terminal** `ls` to verify file presence
- Use **read_file** with `offset` + `limit` for large multi-thousand-line documents
- Cross-reference BOM numbers across multiple document versions
- Pay special attention to **pin assignment conflicts** in mixed-version review chains

## Related skills

- `hermes-agent-skill-authoring` — for authoring new SKILL.md files
- `memory-management` — for capturing one-off user facts
- `software-architecture-design` — for the content being reviewed
- `api-design` / `database-schema-design` — for reviewing those doc types

## Domain-specific checklists (apply per project type)

The base workflow above is universal. The checklists below are domain-specific overlays — apply the relevant one when the design doc covers that domain.

### Engineering / Hardware / Embedded (from engineering-design-review)

Severity classification (use consistently):

- 🔴 **严重 (Critical)** — Blocks next stage, must fix before PCB/layout/production
- 🟡 **重要 (Important)** — Should fix in current or next stage, can defer if tracked
- 🟢 **一般 (Minor)** — Documentation-level, fix in later iteration

Composite score dimensions for design docs (use half-star increments):

1. 文档结构 (Document structure)
2. 上游评审响应度 (Upstream review response rate) — only for non-initial versions
3. 与说明书一致性 (Spec consistency)
4. 硬件/软件设计完整度 (Design completeness)
5. 工程可落地性 (Engineering implementability)
6. 风险识别 (Risk identification)
7. 里程碑可行性 (Milestone feasibility)
8. **总评 (Overall)**

Score calibration:
- Initial version (V0.1): 3.0 ~ 3.5 / 5 typical
- Each successful incremental fix: +0.25 ~ +0.5
- Comprehensive evaluation response: +0.5 ~ +1.0
- First "通过" version: 4.0 / 5 milestone

### Hardware / Embedded-specific review checks (from hardware-design-review)

When reviewing MCU-based hardware docs, check these categories in addition to the universal workflow:

**Pin Allocation**
- 同一引脚多个功能冲突 (most common): same pin listed under different functional groups in §3 GPIO table
- GPIO 复用 vs 重映射: confirm whether MCU supports function remap (STM32 AFIO, HC32F460 Fun_Grp1/Fun_Grp2)
- EXTI vs PWM vs UART vs ADC: each pin should have ONE primary function
- JTAG/SWD 与功能 IO 冲突: PA13/PA14 on STM32 are SWD default; must remap for other use

**Power Topology**
- 24V/12V → 5V → 3.3V sequence: each rail's max current vs source
- 常电 vs 钥匙控制: 钥匙 OFF 后哪些 rail 仍保留？MCU 休眠时哪个供电方案？
- 防反接 (PMOS): drain-source orientation, gate bias, Vgs rating vs Vin
- 多个输出并联到同一节点: e.g., U10 OUT and Q7 both → J6-12 (causes short if not intentional)
- 上电时序: DCDC soft-start, LDO sequencing, inrush current

**Communication Interfaces**
- TX/RX 接线交叉: MCU TX → 编程器 RX (must be labeled correctly on both ends — common error: top of schematic vs bottom of pin table)
- CAN/485 终端电阻: 120Ω at both ends only, not in middle
- CAN 共模电感: 90Ω@100MHz typical (ACM2012)
- 波特率/帧格式: must be confirmed with peer device, not assumed

**BOM Consistency**
- 总和 vs 单项加总: 70.5 vs (主芯片 22 + 智能开关 14.5 + ...) must match
- 同型号不同单价: same chip, different price in different places
- 数量与原理图: connector count vs schematic pin count
- 选焊 vs 量产: production DNP (Do Not Populate) marked clearly

**DTC / Fault Code (automotive / industrial)**
- DTC 编号唯一: each fault has unique code (no two faults share 0x91)
- 严重度分级: 1=提示 / 2=警告 / 3=严重 / 4=紧急
- 触发条件: temperature > X, voltage < Y, sensor stuck, etc.
- 故障码 vs 代码一致: 代码中 `Record_DTC(0x91)` 必须对应 §6 表中的 0x91

**EMC/ESD**
- ESD 阵列 (USBLC6-2SC6): 在每个外露接口
- TVS 选型: 5.1V for 3.3V signals, 12V for 12V rails, SMBJ33CA for 24V
- 共模电感位置: between connector and transceiver IC

**Thermal**
- 功率器件: IRF4905 / Q1 / 智能开关 散热片设计
- 功耗核算: 峰值 vs 典型值 (worst-case vs typical)
- 结温 Tj < 125℃: 工业级标准

### Software / System architecture (from technical-design-review)

For software/system design docs (not hardware), the two-pass review structure is:

**Pass A — 响应度核对 (Response rate to previous review)**

For each issue in previous 评审意见, locate where design doc claims to fix it, verify the fix actually addresses the issue (not just renamed it). Tabulate: 编号 / 上版本问题 / 本版本响应 / 结果 (✅ 通过 / ⚠ 部分 / ❌ 失败).

**Pass B — 新引入问题扫描 (New issues introduced)**

After all previous issues are addressed, scan for NEW problems. Common patterns:

- **Pin / peripheral conflicts** when moving pins around (e.g. TIM1 channel conflicts with USART remap)
- **BOM spec inconsistencies** when updating component specs (datasheet vs BOM mismatch)
- **Power topology inconsistencies** when changing power path (PMOS current rating, 常电 vs 切电)
- **Software/hardware timing mismatches** (power-on sequence §2.2 vs code §5.4)
- **Cross-reference inconsistencies** between sections (规格 in §2.2 vs BOM in §7)
- **Lost capability** when refactoring (renaming/removing a peripheral feature)
- **Newly-required features not implemented** (e.g. NTC added but no threshold strategy)
- **Components referenced in text but missing from BOM** (CAN 共模电感标了但 BOM 无)

**Consistency check vs upstream spec**: If 需求说明书 / 功能说明书 exists, verify every requirement is covered by the design. One table: 需求项 / 说明书 / 设计稿 / 一致性. Look for items in spec missing from design. Look for items in design not justified by spec.

## Cross-version pitfalls (lessons from real reviews)

These apply across all domain types — hardware, software, system.

1. **Avoid absolute technical assertions**. Do NOT write "X 不支持 Y" unless 100% verified from datasheet. Use "需对照 [具体文档章节] 确认".
   - Lesson: HC32F460 通过 Fun_Grp1/Grp2 可重映射 USART 到大部分 GPIO. 即使有把握, also建议加"需查 Fun_Grp 表确认".

2. **When fixing old bugs, scan for new ones**. Don't just do "response rate check" and stop. Fixing one bug by moving pins can lose a feature the original pin provided.

3. **Multi-section consistency**. Specs, timings, pin assignments that appear in multiple places must agree. When they don't, pick one as 权威 and have others reference it.

4. **BOM specs must be datasheet-traceable**. Every component's voltage/current spec in BOM must match its datasheet. Add a "datasheet link / spec verification" column or note.

5. **Lab scheduling reality**. EMC 测试实验室排队 2~3 周 + 测试 + 整改 2~3 轮, "10 天 EMC 整改" 是不现实的. Adjust ship date accordingly.

6. **Never sign for the user**. The 签字栏 must be empty (☐ 占位) for actual signers to fill. Do not check ☑ or write names.

7. **Multi-version continuity**. Each 评审意见 should reference the previous one. Build a version chain: V0.1 → V0.2 → V0.3 → V0.4. The "附注" section should include a version evolution table.

8. **Watch for 引脚冲突 (pin conflicts) across multi-version docs**. When reviewing iteration V(N), check if pin allocations from earlier versions still hold. A pin allocated to function A in V(N-1) might be reallocated to function B in V(N) without removing the A assignment. Build a pin → functions map, look for any pin with > 1 function.

9. **Hardware schematic-level issues need separate review from system design**. A document hierarchy often has multiple layers — system 设计稿 (architecture, software) + schematic稿 (电路图, BOM). These need DIFFERENT evaluation dimensions:
   - 设计稿: 架构、状态机、软件架构、决策表、风险表
   - 原理图稿: 电路图正确性、引脚分配、BOM 完整性、计算正确性、PCB Layout 准备度

10. **"追加式合并" failure (merge review trap)**. When the doc claims to be "合并版 V1.0 = V0.6 + V0.7 + V0.8 + V1.0a + V1.0b + V1.0c":
    - ✅ **Overwrite** (good): later version's content replaces earlier version's content in the merged document
    - ❌ **Append** (bad): earlier version kept AS-IS, later version added as "appendix"
    - Review verdict: ☐ 不通过 — must produce V1.0b that OVERWRITES the old content with the new content

11. **"TX/RX label cross-contradiction"**. Schematic top shows: `PA14 (USART2_TX) ──┬── J4-3`. Pin table bottom shows: `3 TX │ PA15 (RX direction)`. Both look correct, but together they contradict. PCB designer following the top will wire PA14 to J4-3; following the bottom will wire PA15 to J4-3.

12. **Software-hardware DTC mismatch**. `if (fault) Record_DTC(0x91);` in §11 code. `0x91 = "灯光过温急停"` in §6.2 table. These need to refer to the same fault.

13. **User correction pattern: "严格意义上不准确"**. When user says "X is inaccurate in the strict sense":
    - Acknowledge: "承认 N1 严格意义上不准确"
    - Demote: 🔴 → 🟡 (from must-fix to verify-first)
    - Add verification: "查 HC32F460 用户手册 Fun_Grp 表确认 PB6/PB7 对 USART0 的支持"
    - Provide alternatives: A/B/C
    - Re-classify: not "error" but "needs verification before commitment"

14. **Distinguish "incremental fix version" vs "comprehensive evaluation response version"**. A version sequence can have different natures — V0.5 reviewed as "通过" doesn't mean V0.6 is "next review iteration". V0.6 might be a response to a SEPARATE comprehensive evaluation (e.g., "8.8/10 综合评估"). Document the version's nature explicitly:
    - V0.1: 初版 (initial, based on spec)
    - V0.2 ~ V0.5: 增量修正版 (incremental fix responding to specific review items)
    - V0.6: 综合改进版 (responding to external comprehensive evaluation with score like 8.8/10)

15. **Don't confuse "reviewing the design document" with "summarizing the user's prior review of it"**. When user asks "review this V0.6", evaluate the V0.6 document itself against the spec and engineering best practices. Don't just rehash or summarize what the user's previous review said — they want a FRESH evaluation. Previous reviews are useful INPUT (to track response), not OUTPUT.

16. **Don't preserve raw calculation errors + correction process in published documents**. Documents sometimes keep "wrong → correct" inline (e.g., "R_FB1=100K, R_FB2=49.9K → VOUT=2.40V 不对! 应该是 5V: 修正..."). Recommend cleanup in review — published documents should show only the final correct values.

## Support files in this skill

- `references/template.md` — full 10-section report template, ready to adapt per project
- `references/checklist.md` — 12-phase pre-submit sanity check to catch every observed pitfall
- `references/version-tracking.md` — rules for tracking pin conflicts, BOM totals, and score trajectories across multi-version chains