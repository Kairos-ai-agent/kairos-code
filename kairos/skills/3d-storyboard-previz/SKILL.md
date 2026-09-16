---
name: "3d-storyboard-previz"
description: "|"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\you\\AppData\\Local\\hermes\\skills\\.archive\\3d-storyboard-previz\\SKILL.md"
---
# 3D Storyboard Previz

A class-level skill for going from **v6 storyboard markdown** → **browser-playable 3D
previz** (graybox / whitebox "look-before-you-burn-GPU" tool). The output is a single
HTML file the user double-clicks; no server required once assets are vendored.

## When procedural generation isn't enough: jump to a textured source

**All procedural "white model" GLBs look identical as gray meshes.** The
realism difference comes from PBR materials + textures + normal maps, not
from geometry. If the user says the model looks "childish", "still cartoon",
or "no different from before", the answer is **not** a different procedural
GLB — it's a textured GLB (Mixamo, Meshy, or commercial). Don't waste
hours downloading more gray-mesh GLBs from Khronos / MakeHuman. See
`references/open-source-glbs.md` for the full source list and the realistic
character path.

## Pipeline (4 stages)

```
v6 markdown ──► JSON scene graph ──► Three.js runtime ──► Browser playback
   │                │                       │                    │
   │                │                       │                    └─ MediaRecorder → MP4
   │                │                       └─ Group fallback or GLB mesh
   │                └─ parsed by LLM (Anthropic/OpenAI) or direct paste
   └─ from storyboard-gen skill (upstream) or hand-written
```

## Steps

### 1. Schema (JSON scene graph)

The runtime consumes this schema (kept deliberately simple):

```json
{
  "segs": [{
    "name": "SEG01｜...｜12秒",
    "duration": 12,
    "lighting": "开 SEG 暖黄3000K → 收 SEG 蓝紫8000K",
    "props": ["推车", "书摊", "小王子"],
    "transition": "黑场" | "声音桥" | "匹配剪辑" | "视觉引导" | "直切",
    "shots": [{
      "duration": 4,
      "camera": {
        "focal": 35,                         // mm (35mm equivalent)
        "height": "胸口高度" | "高位俯俯" | "低机位" | "平视",
        "distance": 5,                       // meters from subject
        "motion": "固定机位" | "横移" | "推" | "拉" | "跟"
      },
      "actors": [{
        "name": "林深",
        "startPos": [-4, 0.8, -2],
        "endPos": [2, 0.8, -2]
      }],
      "intent": "一句话说明镜头存在的理由"
    }]
  }]
}
```

Convention: `y < 0.5` ⇒ child (auto scale 0.7); `0.5 ≤ y ≤ 1.0` ⇒ adult.
Name regex `/女|母|姐|妹|她|娘|姑|婆/` ⇒ female body proportions.

### 2. Three.js scene build

Camera focal → FOV: `fovDeg = 2 * atan(12 / focalMm) * 180/π` (35mm sensor
half-height = 12mm). Example: 35mm → 54°, 50mm → 40°, 85mm → 24°, 24mm → 73°.

Motion mapping:
- 固定机位 → camera at (0, height, distance)
- 推 → distance lerps from 2× → 1× over shot duration
- 拉 → distance lerps from 0.5× → 1×
- 跟 → camera follows actor.startPos → actor.endPos
- 横移 → camera slides ±2m perpendicular to actor

Lighting (kelvin → RGB via blackbody approximation):
- Parse `(\d+)K` from SEG.lighting string → kelvinToRGB()
- Apply to AmbientLight + DirectionalLight + HemisphereLight
- Tint scene.background + fog with same color × 0.25 / × 0.15
- Default for unrecognized: warm 3000K (hex 0xffdc82) or cool 8000K (hex 0x8282ff)

### 3. Actor construction

**Two-tier strategy** (toggle button):

| Tier | When | Pros | Cons |
|------|------|------|------|
| **Group fallback** | default | walking animation works (limbs rotatable) | low triangle count (~3k) |
| **GLB mesh** | manual toggle | high fidelity (~80k+ triangles) | no skeletal animation |

Group fallback is a multi-piece assembly: head + hair + eyes + nose + mouth + torso +
limbs (named `arm_L`, `arm_R`, `leg_L`, `leg_R` for animation access) + shoes.
GLB is loaded via built-in parser (no GLTFLoader.js dependency — see pitfalls).

#### Character library for >2 character types

If the storyboard has more than 2 distinct character archetypes (e.g. adult /
child / elderly, or different body types), generate a **character library**
instead of a single `human.glb`:

```
vendor/characters/
├── adult_male.glb
├── adult_female.glb
├── strong_male.glb
├── slim_male.glb
├── child_boy.glb
├── child_girl.glb
└── old_man.glb
```

Parameterize `make_human.py` with `--gender`, `--age`, `--body`, `--muscular`
flags, then run a batch script (see `references/character-library.md` for
the full pattern). At runtime, auto-select via `pickCharacterModel(name,
actor)`:

```js
function pickCharacterModel(name, actor) {
  if (actor?.model && characterLib[actor.model]) return actor.model;
  const y = actor?.startPos?.[1] ?? 0.8;
  if (y < 0.5) return /(女|妹|姐|她|童)/.test(name) ? 'child_girl' : 'child_boy';
  if (/(女|母|姐|妹|她|娘|姑|婆)/.test(name)) return 'adult_female';
  return 'adult_male';
}
```

Single GLB is fine for prototypes with one adult role; library is the right
move once a storyboard has child + adult + elderly characters or different
body types. **But remember**: all these procedural GLBs render as the
same gray-mesh look. For visible distinction, use a textured source —
see `references/open-source-glbs.md`.

### 4. Walking animation (Group tier only)

Use `traverse()` to find named limb meshes after building the Group:

```js
limbs = { arms: [], legs: [] };
result.group.traverse(o => {
  if (o.name === 'arm_L' || o.name === 'arm_R') limbs.arms.push(o);
  if (o.name === 'leg_L' || o.name === 'leg_R') limbs.legs.push(o);
});
```

Detect movement via `startPos !== endPos` (element-wise compare).

## `!numericIndex` is always true for index 0 (the GLB parser silent-fail)

When a GLB's primitives reference accessors by **integer index**:

```json
{
  "meshes": [{
    "primitives": [{
      "attributes": { "POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2 }
    }]
  }]
}
```

Checking with `!` (the natural "is this defined?" pattern) silently fails:

```js
// BROKEN — POSITION=0 is falsy, so the early return ALWAYS fires
if (!prim.attributes || !prim.attributes.POSITION) return null;

// FIX — use strict undefined check
if (!prim.attributes || prim.attributes.POSITION === undefined) return null;
```

**Symptom**: `parseGLB` returns successfully, `scene.children.length === 0`,
no console errors. Took 30+ minutes to debug because every layer looked
correct in isolation. The actual scene ended up empty with `null` silently
filtered out by the early return.

**Same trap for primitive indices, material indices, animation track
indices, KHR extension fields** — anywhere the value `0` is a legitimate
identifier. Always use `=== undefined` or `!== undefined` instead of `!`.

## Naive `new Float32Array(buffer.length)` then byte-loop copies bytes, not values

A common bug when porting glTF parser code: treating the returned
ArrayBuffer as if its length is in elements, not bytes:

```js
// BROKEN — copies bytes (0-255) into float slots
const positions = new Float32Array(posView.length);
for (let i = 0; i < posView.length; i++) positions[i] = posView[i];
// Result: positions = [garbage bytes as floats, not actual vertex coords]

// FIX 1 — zero-copy view, ArrayBuffer's typed-array constructor
//        interprets length in element units, not bytes
const positions = new Float32Array(posView);

// FIX 2 — explicit count (most readable)
const positions = new Float32Array(posView, 0, acc.count * typeCount(acc.type));
```

The same applies to `new Uint16Array` / `new Uint32Array` for indices.

## GLB characterLib cache holds STALE empty Group after parser fix

If you fix a bug in your GLB parser (e.g. the `!0` bug above) and the
demo is already running with cached `characterLib[model]` from before
the fix, the cache still holds the **old empty Group**. The fix is correct
but the user still sees an empty scene.

Detection: `useGLB === true` but `actor.mesh.children.length === 0` even
though `parseGLB` returns successfully.

Solutions:
- **Cache-bust on load**: append `?v=2` to GLB URLs after a parser change
- **Validate cache**: on load, check if each `characterLib[model]` has any
  mesh children; if not, refetch
- **Force reset**: `characterLib = {}` at startup, always re-fetch
- **User reload**: tell the user to hard-refresh (Ctrl+Shift+R)

The cache-bust query string is the simplest. Change
`loadGLB('./vendor/characters/x.glb')` to
`loadGLB('./vendor/characters/x.glb?v=2')` after a parser fix; bump
the version on every regeneration.

## Limb rotation magnitude is hard to see on tiny renders

A swing of `Math.sin(phase) * 0.5` (≈28°) looks correct in the math, but
on a character rendered ~30 pixels tall (long shot, far camera), the motion
is below the perceptible threshold for casual viewers. Bump to 0.8 rad (≈46°)
+ add a vertical body bounce (`Math.abs(Math.sin(phase * 2)) * 0.06`),
and the character reads as "walking" at a glance:

```js
const swing = Math.sin(walkPhase) * 0.8;  // ±46°, was 0.5
const bounce = Math.abs(Math.sin(walkPhase * 2)) * 0.06;
armL.rotation.x = swing;
armR.rotation.x = -swing;
mesh.position.y += bounce;  // body bob
```

Below ~0.6 rad, the arm/leg motion is in the noise. The bounce sells
the walk even when individual limb motion is too small to read.
GLB-only characters (no limbs refs) get the bounce-only fallback.

### 5. LLM integration

Provider auto-detection (see `references/llm-provider-detection.md`):
- Base URL contains `/anthropic` or matches `anthropic\.com` → Anthropic Messages API
  - Path: `{baseUrl}/v1/messages` (auto-append `/v1` if missing)
  - Header: `x-api-key` (NOT `Authorization: Bearer`)
  - Body: `{model, max_tokens, messages:[{role,content}]}` (system folded into user content)
- Otherwise → OpenAI Chat Completions
  - Path: `{baseUrl}/chat/completions`
  - Header: `Authorization: Bearer <key>`
  - Body: `{model, messages:[{role,system|user,content}]}`

JSON extraction is fragile; LLM may wrap output in markdown code blocks or include
explanatory text. Use `extractJSON()` (handwritten brace-matching parser, NOT a
greedy regex) to find the first balanced JSON object, then attempt `JSON.parse`.
Sanitize trailing commas before re-trying.

**CORS reality**: Browser fetch to MiniMax / OpenAI is blocked without proxy. Either:
- Run local FastAPI proxy (`proxy.py`) forwarding to the upstream, listen on localhost:8765
- Or have the user deploy their own proxy

### 6. Recording → MP4

`canvas.captureStream(30)` → `MediaRecorder` with codec `video/webm;codecs=vp9`.
Per-SEG splitting: stop recorder at SEG boundary, auto-download `previs-seg01.webm`,
restart for next SEG. Note: output is WebM, not MP4 — rename or transcode with ffmpeg
if the user needs MP4.

## Pitfalls (read these before starting)

- **` ```json ` inside template literals breaks syntax.** A system prompt like
  `"不要 ```json 包裹"` written inside a JS template literal will silently kill
  the entire script tag — the first ` ``` ` ends the string, `json` becomes an identifier,
  the next ` ``` ` opens a new string. Browser parses the whole script block as a syntax
  error and skips it silently. Workaround: describe markdown fences in Chinese
  ("三三段式"), or escape ` ``` ` as `\\` ``` `.
- **`window.GLTFLoader is not a constructor`** when GLTFLoader.js is loaded as a
  normal `<script>`. The library's `export { GLTFLoader }` is ES module syntax and
  doesn't bind to `window`. Either inline-convert to UMD (`const {...} = window.THREE;
  ...; if (window) window.GLTFLoader = GLTFLoader;`), or — better — write a minimal
  built-in parser (~80 lines, supports mesh + material + position/normal/indices).
- **`Failed to fetch` on `file://`** with large GLB (>2MB) in some browsers. Solutions:
  inline base64 (~33% overhead), or run a local HTTP server
  (`python -m http.server 8765` in the project directory, then open
  `http://localhost:8765/storyboard-previs.html`). The HTTP server is the simplest
  approach for demos that vendors 50-100MB of character libraries. If you see this
  error during testing, run the HTTP server first.
- **Camera lookAt during `跟`** (follow) motion: aim at `actors[0]` position, not at
  `(0, 1, 0)` — the actor is moving, the camera must lead/lag correctly.
- **MiniMax endpoint** is NOT OpenAI-compatible despite the `/v1` prefix — it serves
  Anthropic Messages at `/anthropic/v1/messages`. Common confusion: filling MiniMax
  config into OpenAI paths returns 404.
- **`scene.background` + fog don't reset between SEGs**: explicitly reset both in
  `buildStage()` before applying new lighting, or you'll leak the previous SEG's
  color into the next one.
- **`scene.fog` far distance**: setting to 30 with camera at distance 8 means actors
  fade as they recede — usually correct for cinematic look but verify your camera
  distances don't accidentally fall outside the fog band.
- **`THREE.MathUtils.radToDeg`**: easy to forget when doing custom math. Always
  wrap if you want degrees in the result.
- **Walking animation only works on Group tier.** GLB meshes are single merged meshes
  with no skeletal hierarchy. Don't promise walking on the GLB toggle without
  adding armature + AnimationMixer first.
- **MPFB 2.0+ cannot run headless.** The MakeHuman Plugin for Blender 2.0+ ships
  with a `blender_manifest.toml` and registers as a Blender 4.2 *extension* through
  the GUI's `Edit > Preferences > Get Extensions` flow. Running
  `bpy.ops.preferences.addon_enable(module='mpfb')` from `--background` Blender
  silently fails with `Error: The "package" does not name an extension`. Don't
  waste time trying to script MPFB headlessly — the user must enable it in
  Blender GUI first, then can use MPFB's UI to generate a character and export GLB.
- **GitHub raw downloads time out / rate-limit from China.** Trying to auto-fetch
  Mixamo, RiggedFigure, or MB-Lab character GLBs into a `vendor/` directory
  routinely fails mid-transfer. Use **jsDelivr CDN** (`cdn.jsdelivr.net/gh/...`)
  for Khronos files. For other repos: `gh-proxy.com` mirror, or `git clone --depth 1`.
  Don't promise "I'll auto-download the perfect model" — set the expectation
  that the user picks the quality bar.
- **Three.js GLTFLoader.js with `<script src>` ≠ ES module export.** The library
  uses ES module `export { GLTFLoader }` at file end, which is a no-op in plain
  `<script>` mode — you get `THREE.GLTFLoader is not a constructor`. Either
  (a) inline-convert to UMD by replacing the `import { ... } from 'three';`
  line with `const { ... } = window.THREE;` and appending
  `if (window) window.GLTFLoader = GLTFLoader;` at the end, or (b) skip
  GLTFLoader entirely and write a ~80-line built-in parser that handles
  mesh + material + position/normal/indices — usually simpler. See
  `references/glb-parser.md`.
- **MPFB addon zip format**: When packaging MPFB (or any Blender 4.2
  extension addon) as a zip for `--background` registration attempts, the
  zip's top-level directory MUST be the addon ID (e.g. `mpfb/`). Files
  directly under the zip root (`__init__.py` at root, etc.) will not be
  recognized. Verify by listing `unzip -l mpfb.zip` — first entry should
  be `mpfb/`, not `src/mpfb/` or a loose file. Even with the right zip
  format, MPFB 2.0+ still won't register headlessly (see below).
- **`scene.fog` + `scene.background` leak between SEGs.** Both are scene-level
  properties set in `buildStage()`. If you only set them when
  `parseLighting()` returns non-empty, an unrecognized lighting field
  (or a SEG with empty `lighting`) keeps the previous SEG's tinted fog/background
  for the new SEG. Always reset both at the top of `buildStage()`:
  `scene.background = new THREE.Color(0x0a0a0a); scene.fog = new THREE.Fog(0x0a0a0a, 8, 30);`
  before conditionally applying lighting. This bug is silent — the previz looks
  "almost right" but colors shift one SEG behind.
- **Headless browser ≠ user's local browser.** During dev, headless tools
  (Playwright, Browserbase, etc.) may fail to `fetch()` local `file://` GLBs
  (>2MB) due to security policies, while the user's actual Chrome/Firefox
  handles them fine. Don't conclude "the demo is broken" from headless
  failure alone. Add `window._previsDebug = { getState: () => ..., getCharLib: () => ... }`
  to inspect runtime state, but verify the user's reported issue against
  their actual browser before redesigning.
- **`window.THREE` partial init causes silent failures.** Three.js UMD is
  ~650KB; some script loaders (or headless policies) may evaluate it but
  fail to expose all sub-objects (Texture, Quaternion, Skeleton, etc.).
  Before depending on a specific class, check `typeof THREE.X === 'undefined'`
  and fail loudly — don't let the script tag fail silently and then wonder
  why nothing renders.
- **`export_draco_mesh_compression_enable=True` but the parser can't read
  Draco.** Three.js's built-in GLTFLoader needs the DRACOLoader extension for
  Draco-compressed GLBs. If you use the built-in 80-line parser (no DRACOLoader),
  export UNCOMPRESSED GLB from Blender or the GLB will fail to load silently.
  Check with `unzip -p file.glb | grep -i draco` — if there's a Draco
  extension chunk, your parser will choke on it.
- **MakeHuman base.obj units are decimeters, not meters.** The file uses
  1 unit = 1 dm. A 1.7m human = 17 units. After `bpy.ops.wm.obj_import` without
  scaling, the model appears as **17m tall**. Fix: `obj.scale = (0.1, 0.1, 0.1)`
  then `transform_apply(scale=True)`. Without this, the GLB is unusable
  (camera at typical heights is "inside" the model or way off-frame).
  See `references/open-source-glbs.md` for the full conversion pattern.
- **Blender OBJ import applies a Y-up→Z-up axis-rotation matrix.** After
  import, `obj.matrix_world` is not identity even when `obj.scale == 1`.
  This means `Box3.setFromObject` returns world-space dimensions that mix
  the rotation and scale. To compute a clean bounding box, either apply
  the matrix manually (`box.applyMatrix4(obj.matrixWorld)`), or call
  `geometry.computeBoundingBox()` on the raw geometry before adding to scene.

## When NOT to use this skill

- If the user wants to produce actual final video, not previz — route to
  `creative/short-drama-pipeline` (AIGC_agent + Agnes video)
- If the user wants a quick mockup of a single static shot — a screenshot generator
  is overkill, just render one frame
- If the user wants a real-time game engine scene (interactive) — use Three.js
  directly with OrbitControls, this skill is for playback
- If the user wants a "realistic" character model (not gray-mesh placeholder) —
  jump to Mixamo / Meshy AI; procedural / CesiumMan / MakeHuman base are all
  visually equivalent gray meshes. See `references/open-source-glbs.md`.

## References

- `references/llm-provider-detection.md` — Anthropic vs OpenAI endpoint details, header/body
  shapes, JSON extraction patterns, CORS proxy setup
- `references/blender-headless.md` — Windows install via Tsinghua mirror, PATH setup,
  Draco export gotcha, MPFB 2.0+ can't run headless, Python API for procedural mesh
- `references/glb-parser.md` — Minimal built-in parser implementation (~80 lines, no deps)
- `references/v6-schema-bridge.md` — How to map storyboard-gen v6 markdown fields to
  the JSON scene graph schema this runtime consumes
- `references/user-upload-glb.md` — Drag-and-drop fallback when `fetch()` fails on
  `file://`; pattern for users to bring their own GLB into the prev
- `references/character-library.md` — Pre-generate a library of GLB variants
  (adult_male/female, child_boy/girl, old_man, etc.) with parameterized
  make_human.py + auto-select at runtime via pickCharacterModel(name, actor)
- `references/open-source-glbs.md` — Where to find humanoid GLB sources
  (Khronos samples, MakeHuman base.obj, Mixamo, Meshy AI), and the reality
  check that all "white model" GLBs look identical as gray meshes

## Templates

- `templates/make-human-blender.py` — Headless Blender Python script template for
  procedural human mesh generation; parameterized by `--gender`, `--age`,
  `--body`, `--muscular`. Customize body proportions.
- `templates/build-character-lib.sh` — Batch regeneration script for the
  character library (7 variants × ~13MB ≈ 91MB total, ~10 min on Windows).
  Re-run after `make-human-blender.py` changes.
- `templates/test-glb-view.html` — Standalone viewer to compare GLB models
  side-by-side. Pass `?m=MODELNAME` in URL, auto-frames the camera. Use
  this when comparing candidate models or debugging parser output.

## Upstream

- `storyboard-gen` skill — produces the v6 markdown this skill consumes. When
  loading the JSON via LLM, point at that skill's template for the field semantics.