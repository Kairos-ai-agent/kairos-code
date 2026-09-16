---
name: "wechat-mini-game-dev"
description: "Build, debug, and ship WeChat mini-games (微信小游戏) using vanilla JS + Canvas 2D, no external engine. Triggers on requests involving 微信小游戏, 小游戏开发, wx APIs, game.json, 微信开发者工具 debugging, or \"导入小游戏项目\"."
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\software-development\\wechat-mini-game-dev\\SKILL.md"
---
# WeChat Mini-Game Development (vanilla JS path)

Use this skill when the user wants to build a 微信小游戏 (not a 小程序, not a H5 page) from scratch or port an existing Canvas game into one. It assumes vanilla JS + Canvas 2D, no Cocos/Unity/Laya — keep the toolchain to zero so the project imports cleanly into 微信开发者工具 and the bundle stays small.

## When NOT to use

- Building a 微信小程序 (page-based, uses wxml/wxss) → different path, not covered here
- Building a Unity/Cocos mini-game → those engines have their own export pipelines
- Building H5/mobile-web games that just happen to live in a browser → no wx.* APIs needed

## Project structure (known-good)

```
project_root/
├── project.config.json     # IDE config (AppID, compile settings)
├── game.json               # Game config — see pitfall #1
├── game.js                 # Entry — `require('./js/main.js')`
└── js/
    ├── main.js             # Scene manager + rAF loop
    ├── core/               # state, storage, time, event bus
    ├── data/               # Static data (items, NPCs, scenes)
    ├── render/             # canvas adapter, draw helpers, UI components
    └── scenes/             # One file per game screen
```

Vanilla JS path means: no npm dependencies, no bundler, no .wxml/.wxss. Everything renders into one Canvas via `wx.createCanvas()`.

## Critical pitfalls (READ BEFORE CODING)

### Pitfall 1 — game.json: no empty strings

**Symptom**: IDE shows `game.json: ["openDataContext"] 不能为 ''` and refuses to compile.

**Cause**: `"openDataContext": ""` and `"workers": ""` are not allowed. Empty string is rejected.

**Fix**: If you don't use open data context (好友排行 / 关系链), **delete the field entirely**. Don't set it to `""`.

```json
{
  "deviceOrientation": "portrait",
  "showStatusBar": false,
  "networkTimeout": { "request": 5000, "connectSocket": 5000, "uploadFile": 5000, "downloadFile": 5000 },
  "subpackages": [],
  "navigateToMiniProgramAppIdList": []
}
```

### Pitfall 2 — wx.getSystemInfoSync() throws async from main()

**Symptom**: Console shows red error `[jsbridge] invoke getSystemInfo fail: jsbridge not ready`. Stack trace bottom is `Object.get deviceOrientation [as deviceOrientation]` — looks like your code but isn't.

**Cause**: `wx.getSystemInfoSync()` is queued into the jsbridge which isn't ready when `main()` first runs.

**Fix**: Defer the call to the first frame of your main loop. Don't call it from `main()` or any synchronous init.

```js
let infoTried = false;
function loop() {
  if (!infoTried && typeof wx !== 'undefined' && wx.getSystemInfoSync) {
    infoTried = true;
    try {
      const info = wx.getSystemInfoSync();
      // resize canvas to actual screen
    } catch (e) { /* keep defaults */ }
  }
  // ... rest of frame
}
```

Wrap in try/catch anyway — even deferred calls can fail in some IDE states.

### Pitfall 3 — Gray-scale base library has its own bugs

**Symptom**: Console errors that look like your code but the stack trace is entirely inside `WAGame.js` (the IDE's runtime), no frames in your own files.

**Cause**: The IDE sometimes defaults to gray-scale base libraries (e.g. 3.17.1) which have known bugs.

**Fix**: Don't try to "fix" your code. Tell the user: 工具栏 → 详情 → 本地设置 → 切换到稳定版基础库 (uncheck 灰度). This eliminates most phantom errors.

### Pitfall 4 — Emoji + Chinese text in same line overlap

**Symptom**: Multiple `🪵 木材: 20` style labels stacked on one y-line render unpredictably; some get hidden behind others because emoji width varies across fonts.

**Fix**: 4-equal-column layout. Each resource centered in its `w/4` slice. Don't try to manually position with hardcoded x offsets.

```js
const items = [
  { icon: '💰', val: state.player.gold, color: '#ffd86a' },
  { icon: '🪙', val: state.player.fishCoin, color: '#a8d8e8' },
  { icon: '🐟', val: state.player.fishDry, color: '#a8d8a0' },
  { icon: '💎', val: state.player.spirit, color: '#c8a8e8' },
];
items.forEach((it, i) => {
  const cx = (w / 4) * i + w / 8;
  ctx.textAlign = 'center';
  ctx.fillText(`${it.icon} ${it.val}`, cx, y);
});
```

### Pitfall 5 — Button text ordering: emoji + 4 chars gets clipped

**Symptom**: "🏞️ 鱼塘升级" renders as "鱼塘升..." in the IDE, while "🏞️ 升级鱼塘" renders cleanly.

**Cause**: The IDE's default font width measurement for emoji+Chinese combos over-estimates when emoji comes first.

**Fix**: For icon + verb patterns, swap to verb-first: `升级鱼塘` not `鱼塘升级`. Same number of characters, no clipping.

## Development workflow (proven order)

1. **Plan files** — list all data files, scenes, shared modules first. Mini-game bundle limits matter.
2. **Core layer** — `storage.js` (wx.getStorageSync wrapper + in-memory fallback), `state.js` (default + mutator pattern), `time.js` (for activity windows), `event.js`.
3. **Data layer** — all static content as plain objects/arrays with `module.exports = { ..., getById, rollX }` helpers.
4. **Render layer** — `canvas.js` adapter, `draw.js` (gradients, noise, water, roundRect, drawX helpers), `ui.js` (Button, Panel, ProgressBar, Card components), `scenes.js` (per-environment background renderers).
5. **Scene logic** — each scene exports `createXxxScene(manager)` returning `{ name, enter?, update, draw, onTouch }`. Manager keeps a stack for back navigation.
6. **main.js** — init canvas → load state → register scenes → switch to home → start rAF loop → wire touch events.

## Testing strategy (Node.js based — works without IDE)

**This is the single biggest productivity win.** You can run unit + smoke tests on a Windows PC without launching the IDE, and catch most bugs before the user even opens 微信开发者工具.

### Mock wx environment (drop-in)

```js
// mock-wx.js — drop into test files
global.wx = {
  getStorageSync: () => null,
  setStorageSync: () => {},
  createCanvas: () => ({ width: 750, height: 1334, getContext: () => mockCtx }),
  getSystemInfoSync: () => ({ windowWidth: 750, windowHeight: 1334, pixelRatio: 1 }),
  onTouchStart: () => {}, onError: () => {},
  showModal: (o) => {}, showToast: (o) => {},
};
const mockCtx = new Proxy({}, {
  get: (t, prop) => {
    if (prop === 'createLinearGradient' || prop === 'createRadialGradient')
      return () => ({ addColorStop: () => {} });
    if (prop === 'measureText') return () => ({ width: 100 });
    return () => {};
  },
  set: () => true,
});
```

### Three-layer test pyramid

1. **self-test.js** — data integrity, helper functions (`rollFish`, `recycleValue`, etc.), state mutations
2. **deep-test.js** — edge cases (boundary times, level-up thresholds, full game loops)
3. **smoke-test.js** — instantiate every scene, call `draw()` + `onTouch()` to verify no crash
4. **e2e-test.js** — simulate a full player journey (钓→回收→升级→PK)

All four should report `0 failures` before declaring the build done. See `templates/smoke-test-stub.js` for the pattern.

## Program-drawn "photo-real" backgrounds (no image assets)

For mini-games you don't want to bundle megabytes of PNGs. Canvas 2D gradient stacking with `createLinearGradient` + multi-layer `fillRect` + sin/cos wave + radial gradient glow + noise dot scatter achieves convincing scenic backgrounds:

- 江南水乡: sunset gradient + mountain silhouette polygons + boat ellipse + radial lantern glow + drooping willow bezier strokes
- 极地: aurora sine bands (3 stacked with offsets) + iceberg polygon + floe drift via `Math.sin(t * 0.02)`
- 火山: black/red sky gradient + lava paths via `quadraticCurveTo` + smoke ellipses with sin sway
- 深海: dark sky gradient + light shaft polygons + jellyfish dome + bioluminescent particles pulsing with `Math.sin(t / 600)`

**Always pass elapsed `t` into draw functions** for ambient animation. Static screenshots look dead; animated scenes sell the "实拍" feel.

## Touch coordinate handling (cross-environment)

Two paths must both work — IDE real-device testing AND browser-based smoke tests:

```js
// Real device (IDE simulator or actual phone)
if (typeof wx !== 'undefined' && wx.onTouchStart) {
  wx.onTouchStart(e => {
    const t = e.touches[0];
    manager.onTouch({ x: t.clientX, y: t.clientY });
  });
}
// Browser / PC debug
else if (canvas.addEventListener) {
  canvas.addEventListener('click', e => {
    const rect = canvas.getBoundingClientRect();
    manager.onTouch({
      x: (e.clientX - rect.left) * (canvas.width / rect.width),
      y: (e.clientY - rect.top) * (canvas.height / rect.height),
    });
  });
}
```

## Activity window pattern (time-restricted games)

```js
function activityStatus() {
  const d = new Date();
  const w = d.getDay();           // 0=Sun, 6=Sat
  const t = d.getHours() * 60 + d.getMinutes();
  if (w === 0 || w === 6) return { open: true, label: '周末全天' };
  // both endpoints included (`>= && <=`) so :30 and :45 both open
  if (t >= 12 * 60 + 30 && t <= 12 * 60 + 45) return { open: true, label: '午间场' };
  if (t >= 19 * 60 && t <= 21 * 60) return { open: true, label: '晚间场' };
  return { open: false, label: '休息中', next: '...', remainMinutes: ... };
}
```

Display status at top + bottom of every screen, show countdown to next open when closed. Disable action buttons with `enabled = false` when closed.

## State persistence

`wx.getStorageSync` works on real device but may fail in some IDE states. Always pair with an in-memory fallback so smoke tests work:

```js
function safeSet(data) {
  try { wx.setStorageSync(KEY, data); } catch (e) {}
  if (!globalThis.__MEM) globalThis.__MEM = {};
  globalThis.__MEM[KEY] = data;  // PC debug fallback
}
```

## Delivery checklist

Before telling the user to open the IDE:

- [ ] All 4 test layers pass with 0 failures
- [ ] `game.json` has no empty strings
- [ ] `game.js` is just `require('./js/main.js')` — no logic in entry
- [ ] `wx.getSystemInfoSync` is wrapped in try/catch and deferred if used
- [ ] Every scene has both `update` and `draw` methods even if empty
- [ ] Touch handlers work in both `wx.onTouchStart` and DOM modes
- [ ] README exists with how-to-import + how-to-test instructions

## Files in this skill

- `references/wx-pitfalls.md` — extended notes on jsbridge timing, base library gray-scale issues
- `references/wx-api-quickref-legacy.md` — absorbed from older `wechat-mini-game`: commonly-used wx API quick reference at a glance
- `templates/game.json` — known-good minimal config
- `templates/project.config.json` — known-good IDE config
- `templates/mock-wx.js` — drop-in mock for Node tests
- `templates/smoke-test-stub.js` — pattern for testing all scenes without IDE
- `templates/main-skeleton-legacy.js` — absorbed from older `wechat-mini-game`: full bootstrap with stack-based scene manager
- `scripts/wx-mock-legacy.js` — absorbed variant of the wx mock (kept alongside `templates/mock-wx.js` for back-compat)

## Absorbed skills

This umbrella subsumes the previously-separate `wechat-mini-game` skill (now in `.archive/`). The older sibling covered the same class (vanilla JS + Canvas 2D WeChat mini-game workflow) with these unique additions kept here:
- `templates/main-skeleton-legacy.js` — full bootstrap with stack-based scene manager (the new `wechat-mini-game-dev` uses the lighter `templates/mock-wx.js` only)
- `references/wx-api-quickref-legacy.md` — quick reference of common wx APIs at a glance
- The full scene-manager + activity-window time-logic pattern is preserved in the SKILL.md body of the older sibling (still readable while archived).

If you find yourself loading the legacy templates, file a skill patch — the absorber wants to converge them into the canonical templates, not maintain two parallel starter kits indefinitely.