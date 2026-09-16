---
name: "threejs-frontend-tools"
description: "|"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\.archive\\threejs-frontend-tools\\SKILL.md"
---
# Three.js Frontend Tools

When building an interactive 3D visualization tool in a single HTML file (or thin
vanilla HTML/JS project), use the patterns below. They were derived from building a
storyboard preview tool that loads custom GLB models and accepts LLM-parsed JSON
input.

## Quick-start recipe

1. **Vendor Three.js locally as UMD** (`three.min.js`) — never rely on a CDN for a
   tool users will run from `file://` or behind a firewall. See
   `references/threejs-umd-loading.md`.
2. **Build a `makeHumanMesh(color, name, startY)` function** that returns a Group
   of ~32 primitives (head, hair, eyes, torso via LatheGeometry, legs/arms via
   CylinderGeometry, etc.). Tag limbs with `.name = 'arm_L'` etc. so animation
   can find them. See `references/character-composition.md`.
3. **Camera focal length → Three.js FOV**: `fov = 2 * atan(12 / focalMm)` (35mm
   film height convention). See `references/threejs-gotchas.md`.
4. **Color temperature → RGB**: Tanner Helland's approximation. Drive scene
   ambient/fog from cinematographer shorthand like "3000K streetlamp" or
   "8000K blue dusk". See `references/threejs-gotchas.md`.
5. **LLM integration**: auto-detect OpenAI Chat Completions vs Anthropic Messages
   from the base_url, fall back to a local FastAPI/uvicorn proxy when the cloud
   API refuses browser CORS. See `references/llm-provider-adapter.md`.
6. **Walk cycle**: drive limb rotation from `walkPhase = totalTime * 4.0`,
   amplitude ±0.8 rad for arms, ±0.55 rad for legs, plus a vertical bounce for
   the body group. See `references/character-composition.md`.

## Critical pitfalls (read before writing code)

- **Template literal backticks.** Never write `` ``` `` (three backticks) inside a
  JavaScript template literal — it terminates the string and breaks the entire
  script block silently. The page just sits at its initial HTML state with no
  error visible. See `references/threejs-gotchas.md`.
- **ES modules over file://.** `import` and `importmap` work in some browsers via
  `file://` and silently fail in others. UMD `<script>` tags are the safe
  default for portable single-file tools. See `references/threejs-umd-loading.md`.
- **GLTFLoader as ESM.** `examples/jsm/loaders/GLTFLoader.js` uses
  `import { ... } from 'three'`. Convert to `const { ... } = window.THREE` for
  UMD compatibility. Same applies to GLTFExporter, OrbitControls, etc.
  See `references/threejs-umd-loading.md`.
- **JSON robustness for LLM output.** LLM returns may include markdown fences,
  prose before/after the JSON, or unescaped newlines. Parse the first balanced
  `{...}` rather than `\{[\s\S]*\}` (which stops on the first unbalanced close
  brace). See `references/llm-provider-adapter.md`.
- **Walk amplitude.** ±0.5 rad (~28°) swing is too subtle at distance. Use ±0.8
  rad (~46°) for arms so the asymmetry is detectable in a static screenshot.

## See also

- `references/threejs-umd-loading.md` — UMD vs ESM, file:// compatibility,
  GLTFLoader adaptation
- `references/character-composition.md` — humanoid Group construction + walk
  animation + GLB overlay
- `references/llm-provider-adapter.md` — OpenAI/Anthropic auto-detection + CORS
  proxy + robust JSON extraction
- `references/threejs-gotchas.md` — template literal backticks, FOV conversion,
  Kelvin→RGB, position-lerp + additive bounce
- `templates/storyboard-previs-skeleton.html` — minimal skeleton you can copy
  and modify