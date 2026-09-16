---
name: "storyboard-preview-tool"
description: "Build browser-based storyboard animation preview tools — programmatic 3D / 2D game-engine / Ken Burns style previews that simulate motion from a storyboard script without rendering real video. Trigger"
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/.archive/storyboard-preview-tool/SKILL.md"
---
# Browser Storyboard Animation Preview

Class of work: build self-contained HTML prototypes that **simulate** storyboard motion in the browser without producing real video. Three rendering tiers (3D / 2D / Ken Burns), all driven by the same shot-script format and camera vocabulary. Designed for animators/storyboard artists who want to validate camera moves and pacing before committing to expensive AI renders.

## The Three Tiers (Same Script → Three Visuals)

All three share:
- **Script format** — `[location] | [action] | [camera move] | [seconds]` OR free-form natural language (auto-parsed)
- **Camera vocabulary** — 6 moves: `wide`, `medium`, `closeup`, `over`, `dutch`, `reveal`
- **Scene vocabulary** — 4 environments: `street`, `room`, `forest`, `rooftop`
- **Timeline UX** — playhead + shot segments + per-shot click-to-seek
- **WebM export** — `MediaRecorder` on the rendered canvas

| Tier | Library | Weight | Look | When to use |
|------|---------|--------|------|-------------|
| **3D** | Three.js 0.160+ | ~2 MB | Low-poly 3D scene with real camera FOV + orbit | Concept validation, camera move testing, realistic spatial feel |
| **2D** | Pixi.js 7.4+ | ~300 KB | Parallax sprite layers, RPG-style | Quick pacing check, mobile-friendly, light previews |
| **Ken Burns** | Pure CSS + SVG | <50 KB | Pan/zoom on procedural gradient backgrounds | Documentary style, ultra-lightweight, fastest to load |

**Working references**: `<projects>\ImageGen\storyboard-3d.html`, `storyboard-2d.html`, `storyboard-kenburns.html` — three independent single-file implementations. Always offer all three, never pick for the user.

## Critical Pitfalls (Hard-Won This Session)

### Pitfall 1: Canvas init runs before CSS layout computes

**Symptom**: Canvas appears empty/black even though `state.app` or `renderer` exists and `ticker.add` is running. `toDataURL()` returns a tiny or zero-byte blob.

**Root cause**: When a script tag at the bottom of `<body>` runs, the CSS grid layout in `<main>` hasn't been computed yet. `viewport.clientWidth` returns 0. The canvas is created at 0×0.

**Fix**: Defer renderer init to the next animation frame:

```javascript
function initRenderer() {
  requestAnimationFrame(() => {
    const w = viewport.clientWidth || 1280;
    const h = viewport.clientHeight || 720;
    // create renderer with w, h here (NOT viewport.clientWidth at call time)
    // ...
    // Add ResizeObserver for any layout changes:
    if (typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(() => {
        const nw = viewport.clientWidth, nh = viewport.clientHeight;
        if (nw > 0 && nh > 0) {
          renderer.resize(nw, nh);
          camera.aspect = nw / nh;
          camera.updateProjectionMatrix();
          if (shots.length > 0) renderStoryboard();
        }
      }).observe(viewport);
    }
  });
}
```

**Auto-render after init**: if the page tries to render before init completes (e.g., the script's bottom-of-file calls `renderStoryboard()` synchronously after `initRenderer()`), the renderer isn't ready. Use a pending flag:

```javascript
// at bottom of script:
initRenderer();
applyPreset('dialogue');
state._pendingRender = true;  // initRenderer's RAF picks this up
```

See `references/canvas-init-timing.md` for the full diagnosis recipe and reproducer.

### Pitfall 2: Free-form regex without word boundaries matches too much

**Symptom**: A line like "Dutch angle: she hears footsteps" is mis-categorized as `over` (over-the-shoulder) because the OTS regex `o\.?t\.?s\.?` matches the substring "ots" in "foots**t**eps" or "fo**ot**steps".

**Fix**: Always use `\b` boundaries AND prefer full-word phrases over abbreviations:

```javascript
const CAMERA_KEYWORDS = [
  [/\bover[\s-]?the[\s-]?shoulder\b|\bovers\b|\bo\.?\s*t\.?\s*s\.?\b|\boverhead\b|\btop[\s-]?down\b|\bbird'?s?[\s-]?eye\b|\bfrom above\b/i, 'over'],
  [/\bclose[\s-]?up\b|\bcloseup\b/i, 'closeup'],
  [/\bdutch\b|\btilt(?:ed)?\b|\bdiagonal\b|\bangled\b|\bcanted\b/i, 'dutch'],
  [/\breveal\b|\bpull(?:ing|s|ed)?[\s-]?back\b|\bwider\b|\bpan(?:ning)?[\s-]?out\b/i, 'reveal'],
  [/\bwide\b|\bestablishing\b|\blong[\s-]?shot\b|\baerial\b|\bfar[\s-]?shot\b/i, 'wide'],
  [/\bmedium\b|\bmid[\s-]?shot\b/i, 'medium'],
];
```

Same `\b` boundary discipline applies to scene detection and character-count detection — don't match bare digits (`/three|3|trio/` matches "3." as shot number, not "three people").

## Free-Form Script Parser

The user's workflow is paste-natural-language-scripts. Don't force the `|` format. A line-by-line state machine works:

```javascript
function parseFreeFormScript(text) {
  const lines = text.split(/\n+/).map(l => l.trim()).filter(Boolean);
  const shots = [];
  let cursor = 0;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    // Structured (|) format takes precedence when present
    if (line.includes('|')) {
      const [loc, action, cam, durStr] = line.split('|').map(s => s.trim());
      const duration = parseFloat(durStr) || 3;
      shots.push({ id: 'shot_' + i, action: action || line, camera: (cam || 'medium').toLowerCase(),
                   duration, startT: cursor, endT: cursor + duration });
      cursor += duration;
      continue;
    }
    // Free-form: parse duration from "X seconds"
    const durMatch = line.match(/(\d+(?:\.\d+)?)\s*(?:seconds?|secs?|s)\b/i);
    const duration = durMatch ? parseFloat(durMatch[1]) : 3;
    // Parse camera from keywords (most specific first)
    let camera = 'medium';
    for (const [re, type] of CAMERA_KEYWORDS) if (re.test(line)) { camera = type; break; }
    // Strip leading number / "Shot N:" / "Scene N:"
    const action = line
      .replace(/^\s*(?:\d+\s*[\.\)\:]\s*|shot\s+\d+\s*[:\.\-]?\s*|scene\s+\d+\s*[:\.\-]?\s*)/i, '')
      .replace(/\s*[\(\[]?\s*\d+(?:\.\d+)?\s*(?:seconds?|secs?|s)\s*[\)\]]?\s*$/i, '')
      .trim();
    if (!action) continue;
    shots.push({ id: 'shot_' + i, action, camera, duration,
                 startT: cursor, endT: cursor + duration });
    cursor += duration;
  }
  // Whole-document detection: scene type (count keyword hits, pick max) + char count
  const fullText = text;
  // ... SCENE_KEYWORDS regex sweep ...
  // ... char count: 'three people'|'two people'|'they'|'both' etc.
  return { shots, scene: detectedScene, charCount: detectedChars };
}
```

## Camera Move Vocabulary → Keyframe Mapping

Each tier maps the 6 camera moves to actual keyframes:

| Camera | 3D keyframe | 2D keyframe | Ken Burns keyframe |
|--------|-------------|-------------|--------------------|
| **wide** | pos [0,4,14], fov 50, target [0,1.5,0] | zoom 0.55, no shift | scale 1.0→1.15, x 0→-3% |
| **medium** | pos [4,3,8], fov 40 | zoom 0.9, no shift | scale 1.3→1.5, x 0→5% |
| **closeup** | pos [1.5,2.2,3.5], fov 30 | zoom 1.6, focus on character | scale 2.0→2.3 |
| **over** | pos [0,8,5], fov 55 (top-down) | zoom 0.6, y -400 | scale 1.2→1.4, y -10%→-8% |
| **dutch** | pos [3,3,4], fov 35, rotZ 0.25 | zoom 1.2, tilt 0.2, x -200 | scale 1.4, tilt 0.12rad |
| **reveal** | pos [-8,3,8], fov 60 (pull-back) | zoom 0.5, no shift | scale 1.6→1.0 (zoom out) |

Use smoothstep (`t * t * (3 - 2 * t)`) for ease, not linear interpolation — otherwise shots feel mechanical.

## Single-File Architecture Constraints

These are double-click-to-run demos. No build step. Constraints:

- **CDN-only dependencies** — Three.js, Pixi.js from unpkg/jsdelivr; Google Fonts; MediaRecorder API (built-in)
- **All state in module-level `state` object** — no module bundlers, no React/Vue. Vanilla JS.
- **Inline CSS** — single `<style>` block at top
- **Same UI shell across all 3 tiers** — header (logo + duration), sidebar (tabs: Script/Shots/Info), timeline (playhead + segments), viewport (canvas or DOM). Only the rendering layer differs.

## Export to WebM (All 3 Tiers)

For canvas-based (3D, 2D):
```javascript
const stream = renderer.domElement.captureStream(30);  // 30 fps
const recorder = new MediaRecorder(stream, { mimeType: 'video/webm;codecs=vp9' });
// record, stop after total duration, save blob, trigger download
```

For DOM-based (Ken Burns): draw each frame into an offscreen `<canvas>` via SVG `<foreignObject>` capture, then `MediaRecorder` on that canvas. This works but is slower — only use Ken Burns when the user explicitly wants the lightweight tier.

## Reference Files

| File | Contents |
|------|----------|
| `references/canvas-init-timing.md` | The 0×0 canvas bug diagnosis + reproducer (Pixi/Three) |
| `references/free-form-parser.md` | Complete parser source with regex test cases + the OTS-in-footsteps bug fix |
| `references/camera-keyframe-table.md` | Full 6×3 keyframe matrix with reasoning |
| `templates/sample-scripts.md` | 5 ready-to-paste storyboard scripts covering each scene type |

## When to Use Which Tier (Decision Guide for User)

If the user is unsure which tier to try first, suggest based on intent:

- **"I want to feel like I'm directing a real short film"** → 3D (programmatic 3D scenes, real camera FOV)
- **"I want fast iteration, mobile-friendly preview"** → 2D (Pixi parallax, lightest real-time renderer)
- **"I'm doing voice-over / documentary style storyboards"** → Ken Burns (CSS-only, no library load)

But: **always deliver all three as independent files so the user can compare**, never pick one for them.