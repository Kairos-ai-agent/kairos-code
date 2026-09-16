---
name: "hardware-design-review"
description: "Review embedded hardware designs by comparing technical PDFs against production files (PCB coordinates, BOMs). Diagnose firmware/algorithm issues. Use when user provides hardware design documents, PCB"
priority: 0.5
imported-from: "agents"
source-path: "C:\\Users\\you\\.agents\\skills\\software-development\\hardware-design-review\\SKILL.md"
---
# Hardware Design Review

Review embedded hardware designs by cross-referencing technical proposals against actual production files, then diagnose firmware/algorithm issues.

## When to Use

- User provides hardware design PDF + production files (PCB coordinates, BOM)
- User reports sensor performance issues (accuracy, latency, saturation)
- Need to compare "design intent" vs "actual implementation"

## Workflow

### Step 1: Extract Technical Document Content

PDFs cannot be read by `vision_analyze`. Use `pdftotext` instead:

```bash
# Check if pdftotext is available
which pdftotext

# Extract with layout preservation (critical for tables and code)
pdftotext -layout "path/to/document.pdf" -

# Save to file for large documents
pdftotext -layout "path/to/document.pdf" /tmp/extracted.txt
```

Key information to extract:
- Hardware specs (MCU, sensors, amplifiers, pin assignments)
- Circuit topology (LED drivers, amplifier gain, ADC configuration)
- Algorithm logic (sampling rate, feature extraction, classification thresholds)
- Timing parameters (debounce, window sizes, update rates)
- Claimed performance metrics

### Step 2a: Parse Gerber Files (RS-274X)

Gerber files are text-based PCB layer data in RS-274X format. Key file extensions:

| Extension | Layer | Purpose |
|-----------|-------|---------|
| .gtl / .gbl | Copper top/bottom | Trace routing |
| .gto / .gbo | Silkscreen top/bottom | Component labels |
| .gts / .gbs | Solder paste top/bottom | **Stencil (钢网)** |
| .gko | Board outline | Outline/keep-out |
| .drl | Drill file | Hole positions |
| .sai | Assembly info | Drill map with repeats |

**Format header** (always at top):
```
%FSLAX25Y25*%   ← Fixed, absolute, 2.5 format (2 integer + 5 decimal digits)
%MOIN*%         ← Units: INCH (MOM = mm)
%ADD10C,0.005906*%  ← Aperture definition: D10 = Circle, 0.005906 inch
```

**Coordinate conversion**: value / 100000 × 25.4 = mm (for INCH units)

**Essential commands**:
```bash
# Read first 30 lines of any Gerber file to get format + apertures
head -30 file.gts

# Count drill hits
grep -c 'D03\*' file.drl

# Extract all coordinates for analysis
grep -oP 'X\d+Y\d+' file.drl
```

**Panelization analysis from drill coordinates**:
```python
import re
from collections import defaultdict

def analyze_panel(drl_path):
    """Extract panel layout from drill file by clustering coordinates."""
    with open(drl_path) as f:
        content = f.read()

    coords = re.findall(r'X(\d+)Y(\d+)D\d+', content)
    xs = sorted(set(int(x) for x, y in coords))
    ys = sorted(set(int(y) for x, y in coords))

    # Cluster by gaps > 2mm (20000 units in 2.5-inch format)
    def cluster(vals, gap=20000):
        groups, cur = [], [vals[0]]
        for v in vals[1:]:
            if v - cur[-1] > gap:
                groups.append(cur); cur = [v]
            else:
                cur.append(v)
        groups.append(cur)
        return groups

    x_groups = cluster(xs)
    y_groups = cluster(ys)

    cols = len(x_groups)
    rows = len(y_groups)
    pitch_x = (x_groups[1][0] - x_groups[0][0]) / 100000 * 25.4 if cols > 1 else 0
    pitch_y = (y_groups[1][0] - y_groups[0][0]) / 100000 * 25.4 if rows > 1 else 0
    board_w = (x_groups[0][-1] - x_groups[0][0]) / 100000 * 25.4
    board_h = (y_groups[0][-1] - y_groups[0][0]) / 100000 * 25.4

    return {
        'panel': f'{cols}×{rows} = {cols*rows} up',
        'board_size': f'{board_w:.1f} × {board_h:.1f} mm',
        'pitch': f'{pitch_x:.1f} × {pitch_y:.1f} mm',
        'cols': cols, 'rows': rows
    }
```

**To identify board outline dimensions**, parse the .gko file:
```python
with open(gko_path) as f:
    gko = f.read()
coords = re.findall(r'X(\d+)Y(\d+)D\d+', gko)
xs = [int(x) for x, y in coords]
ys = [int(y) for x, y in coords]
panel_w = (max(xs) - min(xs)) / 100000 * 25.4  # mm
panel_h = (max(ys) - min(ys)) / 100000 * 25.4  # mm
```

### Step 2b: Parse PCB Coordinate Files (CSV)

Production coordinate files are typically CSV with columns:
```
Designator, Footprint, Mid X, Mid Y, Ref X, Ref Y, Pad X, Pad Y, Layer, Rotation, Comment
```

Parse to identify:
- Actual component positions (especially sensors, LEDs, photodetectors)
- Actual part numbers (may differ from design doc)
- Actual spacing between critical components

```python
# Quick coordinate extraction
import csv
with open('coordinates.csv', 'r', encoding='utf-8-sig') as f:
    reader = csv.DictReader(f)
    for row in reader:
        if any(key in row.get('Comment', '').upper() for key in ['LED', 'PD', 'IR', 'SENSOR']):
            print(f"{row['Designator']}: X={row['Mid X']}, Y={row['Mid Y']}, {row['Comment']}")
```

### Step 3: Compare Design vs Production

Create a comparison table:

| Item | Design Document | Actual Production | Impact |
|------|----------------|-------------------|--------|
| MCU | CIU32F003 | PY32F002BL15Sx | Different peripherals |
| LED spacing | 1mm | 40mm | Spatial resolution |
| Amplifier | SGM321 | ME321AM5G | Gain/bandwidth |

**Critical:** Design docs often describe intended specs, not actual production. Always verify.

### Step 4: Diagnose Algorithm Issues

Common embedded algorithm pitfalls:

#### 4.1 Baseline Drift / Slow Recovery
```c
// PROBLEM: Baseline frozen during gesture, slow alpha
#define BASELINE_ALPHA 0.01f  // Too slow
if (last_gesture == GESTURE_NONE) UpdateBaseline();  // Frozen during gesture

// FIX: Reset baseline on gesture end, or increase alpha
```
**Symptom:** Long delay (2-3s) between consecutive detections

#### 4.2 Hard Threshold Dead Zones
```c
// PROBLEM: Large dead zone between thresholds
if (ratio > 0.65) return LEFT;
if (ratio < 0.35) return RIGHT;
return NONE;  // 0.35-0.65 always rejected

// FIX: Adaptive thresholds based on signal amplitude
```
**Symptom:** Low accuracy for weak/small signals

#### 4.3 ADC Saturation
```c
// PROBLEM: High gain causes ADC clipping
// Gain 11x + close range = ADC reads 4095 (max)
// Both channels saturated → ratio = 0.5 → dead zone

// FIX: Reduce gain, add saturation detection, or use AGC
```
**Symptom:** Large/fast gestures not recognized

#### 4.4 Excessive Debounce / Confirmation
```c
// PROBLEM: Stacking delays
if (++confirm >= 3) {  // 3 confirmations
    debounce = 75;     // + 150ms debounce
}
// Total: 3×2ms + 150ms = 156ms minimum

// FIX: Reduce confirmation count, shorten debounce
```
**Symptom:** Slow response, missed gestures

#### 4.5 Window Size Mismatch
```c
// Short window (60ms) and medium window (100ms) must agree
if (gs == gm && gs != GESTURE_NONE) return gs;
return GESTURE_NONE;  // Disagreement → reject

// FIX: Allow single-window output at high confidence
```
**Symptom:** Intermittent detection, low accuracy

### Step 5: Write Optimization Report

Output as structured markdown with:
1. Design vs Production comparison table
2. Actual PCB layout diagram (ASCII art from coordinates)
3. Root cause analysis for each reported symptom
4. Prioritized optimization recommendations
5. Code changes with before/after
6. Verification test cases

### Step 6: Triangulation Geometry Check

For sensor arrays, calculate actual geometry:
```python
import math

# Example: LED-PD triangulation
led_spacing = 40  # mm
sensing_distance = 100  # mm (typical)
half_spacing = led_spacing / 2

angle = math.degrees(math.atan(half_spacing / sensing_distance))
# arctan(20/100) ≈ 11.3° per side, total ≈ 22.6°
```

If angle is sufficient (>15° total), hardware geometry is not the bottleneck.

## Output Format

Deliver as `.md` file when user requests it. Include:
- Comparison tables
- ASCII layout diagrams
- Code snippets (before/after)
- Prioritized fix list with effort estimates

### Step 7: PCB Ordering Guidance

When user wants to order PCBs, map Gerber analysis results to manufacturer form options:

**Common form fields and how to determine values from Gerber:**

| Field | How to Determine |
|-------|-----------------|
| Layers | Count copper files: .gtl+.gbl = 2层; +in1/in2 = 4层 |
| Board size | Use GKO outline dimensions (not single board if panelized) |
| Delivery method | "客户拼版" if Gerber is already panelized |
| Panel pieces | 1 if single design; count unique board shapes |
| Min trace width | From CAD design rules or apertures in Gerber |
| Min hole diameter | Smallest drill tool in .drl file |
| Surface finish | Check design spec (HASL/ENIG/OSP/Immersion Silver) |
| Solder mask color | Visual check or design spec |
| Copper weight | Design spec (1oz = 35μm typical) |

**Key pitfalls:**
- Board size on order form = **panel outer dimensions** if panelized, NOT single board
- "客户拼版" means manufacturer cuts along your panel outline; "代拼" means they re-panel
- Always verify panel orientation (horizontal vs vertical) matches manufacturer's preferred axis
- Stencil ordering is separate from PCB — order stencil (.gts/.gbs) from same manufacturer for best fit

## Pitfalls

- `vision_analyze` does NOT support PDFs — always use `pdftotext`
- Design documents may describe intended specs, not actual production — always verify against coordinate/BOM files
- Chinese technical PDFs: use `-layout` flag to preserve table structure
- PCB coordinates: verify units (mm vs mil) and origin point
- Amplifier gain: calculate from actual resistor values, not schematic labels
- ADC saturation is silent — both channels read max, ratio falls in dead zone
- Baseline algorithms need explicit "reset on gesture end" logic
