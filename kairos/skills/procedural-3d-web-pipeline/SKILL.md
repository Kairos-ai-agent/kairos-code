---
name: "procedural-3d-web-pipeline"
description: "Generate 3D meshes headlessly via Blender Python and display them in a Three.js browser viewer that works under file:// without a server. Class-level skill for any project that needs procedurally-buil"
priority: 0.5
version: "1.0.0"
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\procedural-3d-web-pipeline\\SKILL.md"
---
## When to Use

Use this skill when the task is to:
- Generate 3D meshes procedurally (organic bodies, props, environments) **without** ZBrush/Blender GUI sculpting
- Ship a 3D viewer that runs locally from `file://` (no Node server, no Vite, no bundler)
- Avoid pulling in Three.js addons / GLTFLoader (which depend on ES modules + importmap)
- Walk a "human" character through a 3D scene with realistic limb motion **without** rigging a skeleton

Do NOT use when:
- The user has a real `.glb` or `.fbx` file ready (use the original; don't regenerate)
- The viewer can run under HTTP (just use Three.js stock + GLTFLoader, no need for the mini parser)
- Motion must be skeletal animation with proper IK (use a rigged model)

## Architecture

```
blender (headless, --background)
  └─ generate_glb.py         ← procedural mesh + subdivision + Draco? + export GLB
       │
       ▼
vendor/human.glb            ← single GLB, optionally Draco-compressed
       │
       ▼
browser (file://, plain <script src="three.min.js">)
  ├─ glb_parser.js          ← minimal ~120 line GLB parser (no GLTFLoader dep)
  ├─ storyboard-previs.html ← Three.js scene, dual mode (Group + GLB), walk anim
  └─ vendor/three.min.js    ← UMD build (NOT importmap+module — see pitfalls)
```

## Quick Reference

| Topic | File |
|-------|------|
| Blender headless mesh generation | `references/blender-headless-mesh.md` |
| Three.js UMD setup under file:// | `references/threejs-umd-viewer.md` |
| Mini GLB parser (no GLTFLoader) | `references/mini-glb-parser.md` |
| Walk animation without skeleton | `references/walk-animation-no-skeleton.md` |
| v6 storyboard → JSON schema | `references/v6-to-camera-json.md` |
| Blender script template | `scripts/blender_make_organic_mesh.py` |
| Three.js mini GLB parser template | `scripts/threejs_mini_glb_parser.js` |

## Core Workflow

```text
1. Plan meshes     → identify primitives (icosphere, cylinder, box) + muscle bumps
2. Generate GLB    → blender --background --python generate_glb.py -- --output x.glb
3. Ship viewer     → UMD Three.js + mini GLB parser + drag-drop fallback
4. Layer behavior  → walk anim via limb rotation (no skeleton), camera track per shot
```

## Core Rules

### 1. Pick the right primitive for organic forms
- **`bpy.ops.mesh.primitive_ico_sphere_add`** for head/cheek/muscle bumps (uniform triangle distribution)
- **`bpy.ops.mesh.primitive_cylinder_add(vertices=20)`** for limbs/torso (round, no UV-pole singularity)
- **`bpy.ops.mesh.primitive_cube_add`** for props with hard edges (boxes, books, shelves)
- **`bpy.ops.mesh.primitive_uv_sphere_add`** ONLY when you need poles (e.g. eyes)
- **`bpy.ops.mesh.primitive_cylinder_add`** with `vertices=8` (default) gives visible octagonal edges — always pass `vertices=20+`

### 2. Always run Subdivision Surface before merging
- Add `mod = obj.modifiers.new(name='Subsurf', type='SUBSURF'); mod.levels = 2`
- Apply **after** positioning/scaling, **before** joining into single mesh
- Each level multiplies triangle count by ~4x — `levels=3` on a 20-vert cylinder = ~3200 tris
- For 80K-150K total triangle target, mix `levels=2` (props) with `levels=3` (organic)

### 3. Merge + transform workflow
1. Apply all modifiers: `bpy.ops.object.modifier_apply(modifier=mod.name)`
2. Select all parts: `for p in parts: p.select_set(True)`
3. Join: `bpy.context.view_layer.objects.active = parts[0]; bpy.ops.object.join()`
4. Apply scale/rotation: `bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)`
5. Center origin to bottom: `bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='BOUNDS'); bpy.context.scene.cursor.location = (0,0,0); bpy.ops.object.origin_set(type='ORIGIN_CURSOR')`
6. Recompute normals: enter `EDIT` → select all → `bpy.ops.mesh.normals_make_consistent(inside=False)`

### 4. GLB export gotchas
- **Blender 4.2 param name**: `export_draco_mesh_compression_enable=True` (NOT `export_draco_mesh`)
- **Compression level 10**: cuts 13MB → 1.9MB but **GLTFLoader needs DRACOLoader extension** to decode
- **Use uncompressed** if shipping the mini parser (see `references/mini-glb-parser.md`)
- **Set `use_selection=True`** when only the human is selected, otherwise full scene exports
- Disable `export_cameras=False, export_lights=False` to keep GLB lean

### 5. Three.js UMD > ESM for file://
- Use `<script src="vendor/three.min.js"></script>` (NOT importmap + `<script type="module">`)
- `importmap` is unsupported by some browsers on `file://` — entire module silently fails to load
- UMD exposes `window.THREE` which `<script>` blocks can read synchronously
- For GLTFLoader-equivalent: ship a **mini GLB parser** (no GLTFLoader.js dep, no importmap)
- Add modules to `vendor/` to make the project **fully offline portable**

### 6. Walk animation without skeleton
- Skip armature/IK entirely — animate via `rotation.x` on individual limb meshes
- Naming convention: tag arm meshes `.name = 'arm_L'`, `'arm_R'`, `'leg_L'`, `'leg_R'`
- After `buildStage`, traverse: `result.group.traverse(o => { if (o.name === 'arm_L') limbs.arms.push(o); })`
- Phase: `walkPhase = playState.totalTime * 4.0` (rad/sec)
- Arm swing: `Math.sin(walkPhase) * 0.8` (~46° — large enough to see at 5m distance)
- Body bob: `Math.abs(Math.sin(walkPhase * 2)) * 0.06` (mimics weight transfer)

### 7. Dual mode pattern: procedural Group + GLB slot
- Default to **procedural Group** so demo works without external assets
- Keep a **`glbTemplate` slot** that's only filled when they upload a GLB
- Provide both a **toggle button** (high-quality mode) AND a **drag-drop fallback** (works on file:// where fetch fails)
- Don't auto-fetch GLB on load — `fetch()` of local files can fail in headless browsers

### 8. Camera fov from focal length
- `fov = 2 * atan(12 / focalMm)` (35mm film height ≈ 24mm → half = 12)
- 35mm → 73°, 50mm → 40°, 85mm → 24°, 24mm (wide) → 91°
- Animate via `camera.fov = ...; camera.updateProjectionMatrix()`

## Common Traps

- **`<script src>` fetch failure in file://** — Some browsers (especially headless Chromium) refuse `fetch('./vendor/x.glb')` on `file://` even when `<script src>` works. Workaround: drag-and-drop or `<input type=file>` upload, then `FileReader.readAsArrayBuffer()`. See `references/threejs-umd-viewer.md` § file:// limitations.

- **GLTFLoader export `export { X }` doesn't work in plain `<script>`** — The official `GLTFLoader.js` ends with `export { GLTFLoader };`. In a UMD `<script>` block, nothing happens. Either rewrite as `window.GLTFLoader = GLTFLoader` (and convert the `import { … } from 'three'` to `const { … } = window.THREE`), OR use the mini parser (see `references/mini-glb-parser.md`).

- **Draco-compressed GLB won't load without DRACOLoader** — Three.js GLTFLoader does not include Draco by default. Either skip Draco (keep ~13MB file) or vendor `DRACOLoader.js` + `draco_decoder.wasm`. The mini parser does not handle Draco — keep files uncompressed when using it.

- **`primitive_uv_sphere_add` `phi_start`/`phi_length` removed in Blender 4.2** — Use icosphere for partial coverage, or accept full sphere.

- **`bpy.ops.object.join()` requires all parts in same collection with active object set** — Set `bpy.context.view_layer.objects.active = parts[0]` before joining. Without it, join silently keeps parts separate.

- **WebGL canvas reads `clientWidth/clientHeight` which are 0 when canvas is in `<aside>` flex with no min-height** — Add `min-height: 400px` to canvas-wrap CSS or the canvas stays 0x0.

- **Custom Material color overrides don't propagate from cloned GLB scenes** — When you `glbTemplate.clone(true)` and then traverse to set material color, the materials are shared. Clone each material first: `obj.material = obj.material.clone(); obj.material.color = new THREE.Color(actor.color)`.

## Verification Checklist

| After | Do |
|-------|-----|
| Blender export | Confirm `triangles: X, vertices: Y` matches expectation (rough sanity) |
| File on disk | `lsfile -lh vendor/human.glb` — should be 1-15MB depending on compression |
| Syntax | `node --check scripts/threejs_mini_glb_parser.js` |
| Loaded in browser | `console.log(window._previsDebug.getGlbTemplate())` should return Object3D, not null |
| Walk animation | `console.log(actors[0].limbs.arms[0].rotation.x)` should oscillate |
| Camera fov | Watch the fov transition when switching shots — should match the focal length in JSON |

## Self-Modification

This skill DOES NOT modify its own SKILL.md or auxiliary files automatically. Add new patterns explicitly via `skill_manage(action='patch')`.

## External Endpoints

This skill makes NO network requests by itself. It documents how to optionally use:
- Three.js CDN for development (production: vendor to `local)
- Blender binary (required for the headless mesh generation half)
- Khronos glTF-Sample-Assets (for testing — most are technical demos, not real humans)