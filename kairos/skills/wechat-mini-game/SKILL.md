---
name: "wechat-mini-game"
description: "Build any WeChat mini-game (微信小游戏) project. Covers standard project layout (project.config.json + game.json + game.js + js/ modules), Canvas 2D rendering via wx.createCanvas, touch input via wx.onTouc"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\.archive\\wechat-mini-game\\SKILL.md"
---
# WeChat Mini-Game Development

## When to use

Triggered by any task involving 微信小游戏 (WeChat mini-game): building a casual game from a spec, porting a concept to WeChat, scaffolding a new project, or debugging rendering/touch/loop issues.

This skill assumes **plain JS + Canvas 2D**, the default for casual mini-games. If the task requires a full engine (Cocos/Laya/Unity), different constraints apply.

## Project layout (canonical)

```
project_root/
├── project.config.json     # WeChat DevTools config (appid, compileType, settings)
├── game.json               # Runtime config (orientation, network timeouts)
├── game.js                 # Entry — only does `require('./js/main.js')`
├── README.md               # How to open in DevTools, what's where
└── js/
    ├── main.js             # Boot canvas, register scenes, run loop
    ├── core/               # State, storage, time, random, event bus
    ├── data/               # Static data tables (items, scenes, characters)
    ├── render/             # Canvas helpers (gradients, primitives, UI)
    └── scenes/             # Game screens as state machines
```

No `app.json`, no HTML, no CSS — WeChat mini-games render exclusively to a canvas returned by `wx.createCanvas()`.

## Configuration files

### project.config.json — test-mode minimal

```json
{
  "compileType": "game",
  "appid": "touristappid",
  "projectname": "<name>",
  "setting": {
    "urlCheck": false,
    "es6": true,
    "enhance": true,
    "nodeModules": false
  },
  "miniprogramRoot": "./"
}
```

`touristappid` is the built-in test AppID — no registration needed, works in DevTools for local debugging.

### game.json

```json
{
  "deviceOrientation": "portrait",
  "showStatusBar": false,
  "networkTimeout": {
    "request": 5000, "connectSocket": 5000,
    "uploadFile": 5000, "downloadFile": 5000
  },
  "subpackages": [], "workers": "",
  "openDataContext": "", "navigateToMiniProgramAppIdList": []
}
```

## Entry point pattern

```js
// game.js — one line
require('./js/main.js');
```

The `main.js` should:
1. Get the canvas via `wx.createCanvas()` ONCE at boot
2. Sync the canvas size from `wx.getSystemInfoSync()` accounting for `pixelRatio`
3. Load saved state (sync via `wx.getStorageSync`)
4. Register scenes on a stack-based scene manager
5. Hook `wx.onTouchStart` → scene manager
6. Run a `requestAnimationFrame` loop calling `update(dt) → clear → draw(ctx, w, h, t)`

## Canvas + touch gotchas

**Canvas size**: WeChat returns CSS-pixel sizes from `getSystemInfoSync()`. For sharp rendering, set `canvas.width = info.windowWidth * info.pixelRatio` and scale via `ctx.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0)` each frame. Drawing coordinates stay in CSS pixels.

**Touch coords**: `wx.onTouchStart(e => ...)` provides `e.touches[0].clientX/clientY` in CSS pixels relative to viewport. They map 1:1 to drawing coordinates IF you only set the CSS size on the canvas — but if you also bump the buffer size for high-DPI, you need to either draw in CSS pixels with the transform above, or divide client coords by `pixelRatio`.

**Single ctx state**: Don't rely on persistent fillStyle/font/etc. Reset at the start of each frame, or wrap complex state changes in `save()/restore()`.

## Storage pattern

```js
const SAVE_KEY = 'MY_GAME_SAVE_v1';
function load() { try { return wx.getStorageSync(SAVE_KEY); } catch (e) { return null; } }
function save(d) { try { wx.setStorageSync(SAVE_KEY, d); } catch (e) {} }
```

Storage is **synchronous** and small (<10MB practical limit). For larger data, use `wx.getFileSystemManager()`.

Always have a memory fallback so the game works in `node` for self-tests.

## Self-test pattern (critical — run before opening DevTools)

WeChat DevTools is slow to launch and its error messages are misleading. Run unit + integration tests in plain `node` FIRST. Mock `wx` + canvas context:

```js
// In test file, BEFORE any requires of game modules:
global.wx = {
  getStorageSync: () => null,
  setStorageSync: () => {},
  createCanvas: () => ({ width: 750, height: 1334, getContext: () => mockCtx }),
  getSystemInfoSync: () => ({ windowWidth: 750, windowHeight: 1334, pixelRatio: 1 }),
  onTouchStart: () => {},
  onError: e => console.error(e),
  showModal: o => console.log('[modal]', o.title),
  showToast: o => console.log('[toast]', o.title),
};
const mockCtx = new Proxy({}, {
  get: (target, prop) => {
    if (prop === 'createLinearGradient' || prop === 'createRadialGradient') {
      return () => ({ addColorStop: () => {} });
    }
    if (prop === 'measureText') return () => ({ width: 100 });
    return () => {};
  },
  set: () => true,
});
```

A complete version is in `scripts/wx-mock.js`.

For time-based logic (activity windows, daily resets), mock `Date`:

```js
const realDate = Date;
global.Date = class extends realDate {
  constructor(...args) {
    if (args.length === 0) super('2026-08-12T12:30:00');  // force a time
    else super(...args);
  }
  static now() { return realDate.now(); }
};
// ... run tests
global.Date = realDate;  // restore
```

Always test **boundary times** explicitly: 12:29, 12:30, 12:45, 12:46, 18:59, 19:00, 21:00, 21:01.

**Recommended test file structure** (matches what was used in `slg_fishing` and proven effective):

- `self-test.js` — module loads, data integrity, core functions, scene factories (~30 cases)
- `deep-test.js` — edge cases, time windows, full fishing flow, mock-Date tests (~25 cases)
- `smoke-test.js` — every scene's `draw(ctx, w, h, t)` runs without throwing
- `e2e-test.js` — simulate a complete player session end-to-end

Run with `node self-test.js` etc. before claiming the project is ready.

## Pitfalls (each has bitten real projects)

1. **Module path errors in subdirectories**: `scenes/foo.js` requiring `'./time'` fails because `time` is in `../core/`. Always use the full relative path. The node self-test catches this in 1 second; DevTools takes 30 seconds and gives a misleading "module not found" buried in a stack trace.

2. **Logic errors from wrong formula reuse**: e.g., using a "bait boost" multiplier (designed for fish rarity weighting) inside a "hook success rate" calculation makes hook success way too low. Each named formula should match its semantic intent. Add a comment explaining what each multiplication factor means.

3. **State changes without saving**: Every `update(d => ...)` mutation must call `save()` afterward. Missing this means the user loses progress on next launch. Make state updates go through methods on a single State class so it's hard to forget.

4. **Game-over from low FPS**: If "lose" depends on `dt` accumulation, low FPS accelerates the loss. Always `dt = Math.min(0.1, dt)` in the main loop.

5. **Touch hit testing in wrong coordinate space**: Buttons drawn at `y=200` in canvas coords are hit by touch if `e.touches[0].clientY ≈ 200`. But if you set `canvas.width *= pixelRatio` without scaling context transforms, your draw coords and touch coords disagree by `pixelRatio` — every button is misaligned by ~3 pixels on a 3x device.

6. **`wx.createCanvas` returns screen canvas**: Don't `getElementById` or `document.createElement`. Don't re-create the canvas. One per game.

7. **Resource paths in `require`**: Only JS files can be `require`'d. Images use `wx.createImage()`, sounds use `wx.createInnerAudioContext()`. Bundle small assets as base64 in JS, larger assets as files referenced by path.

8. **Async storage writes can silently fail**: `setStorageSync` may throw in low-storage conditions. Don't trust writes — verify by reading back if the data is critical (e.g., paid upgrades, leaderboard scores).

## Scene manager pattern (stack-based)

```js
class SceneManager {
  constructor() {
    this.scenes = {}; this.current = null;
    this.currentName = null; this.stack = [];
  }
  register(name, scene) { this.scenes[name] = scene; }
  switch(name, params) {
    if (this.current) this.stack.push(this.currentName);
    this.current = this.scenes[name];
    this.currentName = name;
    if (this.current.enter) this.current.enter(params || {});
  }
  back() {
    if (!this.stack.length) return;
    const prev = this.stack.pop();
    this.current = this.scenes[prev];
    this.currentName = prev;
    if (this.current.enter) this.current.enter({});
  }
}
```

Each scene is `createXScene(manager)` returning `{ enter?, update(dt, t), draw(ctx, w, h, t), onTouch(touch, manager) }`. The `manager` argument is how scenes navigate (e.g., `manager.switch('shop')` from main menu).

## Activity-window pattern

A common mini-game requirement is "only allow play during X-Y on weekdays, all day weekends". Implement as a single function returning `{ open, label, nextWindow? }`:

```js
function activityStatus() {
  const d = new Date();
  const w = d.getDay();  // 0=Sun, 6=Sat
  const t = d.getHours() * 60 + d.getMinutes();
  if (w === 0 || w === 6) return { open: true, label: 'weekend' };
  const windows = [
    { start: 12*60+30, end: 12*60+45, label: 'lunch' },
    { start: 19*60, end: 21*60, label: 'evening' },
  ];
  for (const win of windows) {
    if (t >= win.start && t <= win.end) return { open: true, label: win.label };
  }
  // compute nextWindow from `t`...
  return { open: false, label: 'closed', nextWindow: 'lunch', remainMinutes: ... };
}
```

UI shows status + countdown. Buttons disable + showToast when retrying outside window. Test every boundary.

## Pointers

- `templates/project.config.json` — copy and rename
- `templates/game.json` — copy as-is
- `templates/game.js` — copy as-is
- `templates/main-skeleton.js` — full bootstrap with scene manager
- `scripts/wx-mock.js` — drop into any test file before `require()` of game modules
- `references/wx-api-quickref.md` — commonly used APIs at a glance