---
name: "hardware-failure-analysis"
description: "Umbrella for hardware failure analysis, 8D report generation, and quality problem-solving. Covers: root cause analysis (Hi-Pot/ESD/surge/overvoltage), physics-based failure diagnosis, 8D report creation (D1-D8), PDF generation with Chinese fonts, fpdf2 patterns, PET insulation design, partial discharge analysis, engineering development process reports, and generic failure analysis methodology for hardware/electrical/software problems. Consolidated umbrella for: hardware-failure-analysis, 8d-report-generation, electronics-8d-report, failure-analysis, quality-8d-report, engineering-development-report."
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/hardware/hardware-failure-analysis/SKILL.md"
---
# Hardware Failure Analysis & 8D Report Generation

Use this skill when the user presents a hardware failure scenario involving:
- Hi-Pot (耐压) / ESD / surge testing failures
- Sensor modules mounted on metal cavities/enclosures
- MOSFET gate oxide breakdown
- Requests for 8D (eight-discipline) reports
- **Optical / IR / ToF / ultrasonic / capacitive-proximity modules** showing distance-dependent behavior (close OK / far NG) — see `references/optical-sensor-failure-modes.md` BEFORE defaulting to "component variability" root cause

## General Workflow

1. **Understand the structure** — Ask for: installation diagram, material stackup (cavity → insulation → module → PCB), testing conditions (voltage, current limit, power state).
2. **Distinguish root cause categories**:
   - Direct breakdown vs capacitive coupling vs partial discharge (PD)
   - PET/insulator breakdown vs edge creepage vs air-gap discharge
3. **Use physics to eliminate possibilities**:
   - Calculate PET dielectric strength (~200kV/mm) to rule out through-breakdown
   - Calculate parasitic capacitance: C = ε₀·εr·A/d
   - Calculate displacement current: I = C × dV/dt
   - Compare to MOSFET gate oxide rating (±20V typ)
4. **PET insulation design rules** (see references/hi-pot-pet-design.md):
   - PET must extend BEYOND the metal shield, >=3mm per side
   - Recommended thickness >=0.18mm
   - Failure mechanism is usually edge PD, not capacitive coupling or through-breakdown
5. **Generate 8D report**:
   - D1: Cross-functional team
   - D2: Problem description with test data
   - D3: Interim containment — do NOT describe shipment suspension unless still ongoing; user prefers concrete measures (incoming inspection, additional testing)
   - D4: Root cause (with physics evidence + 5-Why table + evidence photo)
   - D5: Corrective actions
   - D6: Verification (sample size, pass rate)
   - D7: Preventive actions (DFMEA, spec update, lessons learned)
   - D8: Team recognition (may be removed per customer preference — user typically omits it)
6. **Include 5-Why analysis in D4** — format as a table with columns: Why, Question, Answer. Five levels minimum.
7. **Customize content per customer context**:
   - D8 (team recognition) is optional — many customers don't want it; user prefers it removed
   - D3 (interim containment) — remove shipment suspension if the issue is already resolved in production; list specific actions with责任人/完成期限
   - Language: Chinese for Chinese customers, English for international
   - Signature block (编制/审核/批准) for formal reports
   - User's specific product specs: UART is **5V / 9600bps**, NOT 3.3V
   - Date year: use 2026 (not 2025) — report number format: 8D-SSDX5-2026-001
   - Verification 6.3 (outgoing inspection): 每批次抽20pcs, Ac=0, Re=1 — do NOT use AQL sampling rates
   - Report version: V1.1
   - **D1 team table**: columns are exactly **序号 / 姓名 / 职位**. 职位 is free text the user types (质量工程师 / PE / SMT 工艺 …) — never a fixed dropdown or a preset role list, and **no 联系方式 / phone column**. Collect the team in ONE place: the user rejected the same team input appearing on both the input page and the report page of the tool.
8. **Deliver in PDF** — use fpdf2 (via Hermes venv) to generate professional PDF output:
   - `from fpdf import FPDF`
   - Title page with report number, product, company info
   - Tables for structured data (use multi_cell with dry_run for Chinese text wrapping)
   - Section headers with color
   - See `references/pdf-generation-cn.md` for the table helper pattern

## Key Physics for Hi-Pot on Metal-Enclosed Sensors

| Common Mistake | Correct Understanding |
|---------------|---------------------|
| Capacitive coupling current causes damage | Partial discharge at PET edge causes HF noise injection |
| PET thickness insufficient | PET size (overhang beyond shield) is more critical |
| Openings/holes in module couple HV | Openings face away from cavity, not the coupling path |
| Larger+thicker PET must improve coupling | Counter-intuitive: A/d ratio increased 17% in actual fix — capacitance went UP but issue was resolved. Proof the mechanism is creepage, not capacitance |

## Calculation That Proves PD Theory

When the user's fix (55×32×0.188mm PET) PASSED but capacitance math says it should be WORSE:

- Old: A=1200mm², d=0.15mm → C∝8000
- New: A=1760mm², d=0.188mm → C∝9362 (+17%)

If the failure were capacitive coupling, the fix would worsen it. The fix WORKED, so the mechanism must be something else (creepage / partial discharge). Use this logic when verifying root cause hypotheses.

## 8D Report Template Structure

See references/8d-template.md for the full template. Key sections:
- Report header (number, product, date, company)
- D1-D8 with evidence tables
- PDF generation script pattern (fpdf2)

## References  
- `references/hi-pot-pet-design.md` — PET insulation physics, design rules, calculations  
- `references/8d-template.md` — 8D report structure and content guide  
- `references/optical-sensor-failure-modes.md` — IR / optical module failure patterns: 1/d² attenuation, component variability (§2), customer application shift (§3), and **calibration jig drift (§7 — confirmed SGC1601B 2026-09-02)**
- `references/agnes-llm-8d-tool-notes.md` — **接 Agnes 视觉模型做缺陷图片分析时的实测参数**：可用端点、2.5-flash 图片 72–265s（超时必须 ≥300s）、免费额度第 2 发即 429、必绕注册表代理、JSON 解析不能用 find('{')、`report_info` 必喂的 7 个键、PyInstaller datas 坑
- `references/pdf-generation-cn.md` — fpdf2 Chinese PDF generation: font registration, table auto-wrap, cover page
- `references/pdf-image-embed.md` — Photo embedding in PDF: EXIF orientation, sizing, placement rules

## Templates
- `templates/8d-pdf-saifeng-style/gen_pdf.py` — Reusable fpdf2 generator with brand VI injection (赛光智能 `#0070C0` reference template, verified working). Copy the directory and customize `REPORT_DIR` / `LOGO_PATH` / `info` / `sections`.

## Scripts
- `scripts/extract_brand_vi.py` — Extract logo + brand colors from a customer `.docx` spec file. Usage: `python extract_brand_vi.py "spec.docx" [out_dir]`. Outputs `brand_colors.json` and image candidates.

## Pitfalls
- Don't assume larger/thicker PET always helps — calculate A/d ratio to check if capacitance increases
- Don't blame PET thickness when the real issue is edge coverage
- Don't attribute failure to capacitive coupling without calculating the actual current
- **Don't default to "component variability" root cause for optical sensor "close OK / far NG" failures** — calibration jig drift (mechanical arm + grey card) is a more common root cause at device makers. ASK the user which distribution pattern (time-correlated vs batch-correlated) before writing the 8D report. See `references/optical-sensor-failure-modes.md` §7.
- **Never declare a capability unusable from a single failure** — one timeout or error is not evidence about the model or the API. A request that hangs is more often queued behind a rate limit, sent with `max_tokens` too small, pointed at a dead or overridden endpoint, routed through a system proxy, or waiting on a timeout shorter than the real latency. Rule those out and re-run with adequate parameters before concluding; report the measured numbers (latency, token counts), not a verdict.
- User will correct your analysis if wrong — re-examine evidence rather than defending original position

---

## Absorbed Skills

### 8D Report Generation (from `8d-report-generation`)
Core 8D workflow: gather problem data → analyze root cause (physical mechanism + coupling path + design gap) → generate structured 8D report → PDF output. Common customer requests: remove D8 team recognition, remove D3 shipment suspension, add 5-Why table, add signature block. Physical failure patterns: creepage/partial discharge (PET insulation), capacitive coupling (metal cavity), ESD through ventilation openings. Image embedding in PDF: EXIF orientation correction with PIL, placement rules (only under exact section heading), fpdf2 insertion pattern.

### Electronics 8D Report (from `electronics-8d-report`)
Key physics: direct vs coupled failure, partial discharge at PET edge, capacitive coupling calculation, PET dielectric strength ~200kV/mm. Production-grade fpdf2 table helper with dry_run auto-wrap and page break handling. Python escape-drift pitfall: `\\'` in single-quoted strings closes the string silently — use double-quoted strings or write_file. CSS node styling requires both header gradient AND border-color rules.

### Generic Failure Analysis (from `failure-analysis`)
Structured methodology: gather facts → read spec → build physical model → evaluate multiple failure mechanisms (direct breakdown, capacitive coupling, partial discharge, creepage/flashover, dielectric breakdown, ESD, thermal runaway, inductive kickback) → contrastive testing → edge effect principle. Key insight: root cause is the condition that when corrected prevents recurrence, not just the physical failure mechanism. PDF patterns: multi-cell wrapping, table helpers, section styling, auto page breaks.

### Quality 8D Report (from `quality-8d-report`)
Hi-Pot failure analysis techniques: isolate coupling path (direct vs installed), consider partial discharge at edges, creepage distance > thickness, PET edge geometry critical. Counter-intuitive: larger+thicker PET may have higher capacitance but fixes the problem via creepage distance improvement.

### Engineering Development Report (from `engineering-development-report`)
For documenting multi-iteration engineering projects (v1.1 → v3.0). Covers version timeline, per-version chapters, technical evolution tables, cost evolution, test matrix evolution, decision postmortem (honest assessment of right/wrong decisions). Uses HTML→Edge headless→PDF pipeline. CSS: vertical timeline, callout boxes, severity tags.
