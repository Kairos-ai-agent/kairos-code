---
name: "frontend-patching"
description: ">-"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\software-development\\frontend-patching\\SKILL.md"
---
# Frontend Patching

Surgical, targeted edits to existing web frontends. The core principle: **change only what was asked for, and only how it was asked for.**

## 多语言 / i18n（整站本地化）

做“把整个界面变成可切换的多语言”（几十种语言、上千条文案）时，先读
`references/i18n-multilang-rollout.md`（目录结构、机翻流水线、逐页残留扫描、回归基线对比）。
常驻规则：

- 词典分三层：作者维护的源片段 + 每语言一份 catalog 覆盖层 + 生成的 locale 模块；**回退链写在合并脚本里**
  （catalog → 兼容语系/兜底语言 → humanize），所以只翻一半的词典不会把原始键显给用户。
- **每种语言一个 lazy chunk**：组件库 locale、日期库 locale、词典全部动态 `import()`，只把 1–2 份兜底词典
  静态引入——几十份词典静态 import 会撑爆主 bundle。
- 语言表从组件库自带的 locale 列表生成（语言名用 `Intl.DisplayNames`，日期库代码要单独映射，目录里混的非语言文件要排除）；
  同一种语言只保留一个主 locale，并把这条做成测试不变量。
- 写完扫描器后必须补它的盲区，否则“完全切换”是假象：动态键（数据表里存键字符串）、函数实参文案
  （`msgApi.error('…')`）、JSX 内嵌片段（`{n} chars · {m} bytes`）、单个小写词（`crit`/`muted` 是文案）、
  后端直出的枚举值（severity/status）。
- RTL：方向来自组件库的 provider prop（不是 `<html dir>`），间距用逻辑属性；验收用元素坐标实测，不靠肉眼。
- 覆盖率硬门在翻译未完成期挂环境变量（`--strict` / `I18N_STRICT=1`），结构性守卫（全语言完整键集）常开；
  剔除语言后记得清理孤儿 catalog/locale 文件。

## Golden Rules

### 1. NEVER overwrite an entire file with write_file

`write_file` replaces the **entire** file. If you only need to change a few elements, use `patch` instead. Overwriting loses:
- Formatting the user chose
- Sections you haven't read yet (partial reads!)
- Previous working state with no easy way to revert

**Always read the full file first** before editing, then use `patch` for targeted changes.

### 2. Understand the JS→HTML→CSS contract before editing

Single-file web apps typically have three files with tight ID/class contracts:
- `home.html` — element IDs and class names
- `home.js` — `getElementById()` / `querySelector()` references
- `home.css` — style rules matching those IDs/classes

Before changing HTML element IDs or structure, search the JS and CSS files for references to those IDs/classes. Mismatches cause silent failures.

### 3. CSS class-based visibility pattern

When JS uses `classList.toggle("hidden", ...)` to show/hide elements:

```css
/* Default: visible */
.element { display: block; }
/* Hidden via class: JS adds/removes .hidden */
.element.hidden { display: none; }
```

For modal/lightbox elements, the full pattern is:

**CSS:**
```css
.modal { display: none; }                              /* hidden by default */
.modal.hidden { display: none !important; }             /* .hidden also hides */
.modal:not(.hidden) { display: flex; }                  /* no .hidden means visible */
```

**HTML:**
```html
<div class="modal hidden" id="assetModal">...</div>     <!-- starts hidden -->
```

**JS (existing, don't change):**
```javascript
modal.classList.remove("hidden");  // shows
modal.classList.add("hidden");    // hides
```

### 4. Mode buttons pattern

Mode buttons use `data-mode` attributes and a `MODE_CONFIG` object:

```html
<button class="mode-btn active" data-mode="txt2img">...</button>
```

```javascript
const MODE_CONFIG = {
  txt2img: { label: "文生图", needsRef: false, ... },
  img2img: { label: "图生图", needsRef: true, ... },
  ...
};
```

To add/remove a mode: update BOTH the HTML `<button>` AND the JS `MODE_CONFIG` object.

## Common Pitfalls

### CSS custom properties referenced but never defined — silent invisibility

**Scenario**: The user opens an admin panel (or a message thread, or a sample gallery) and says "看不清楚" (can't see clearly) or "ui 烂掉了" (ui is broken). DevTools → Elements shows the elements are present, in the right place, with non-zero size. The CSS rules apply. Yet backgrounds, borders, and active-tab highlights are invisible.

**Symptom** — concretely what the user sees:
- The "active" tab label has the same dim text color as inactive tabs — no visible highlight even though the active class is applied
- Card borders are missing or invisible (no border showing despite `border: 1px solid var(--border)`)
- Selected list items have no visual selection indicator
- Unread badges blend into the background
- No console errors, no 404s, no failed loads — pure styling failure

**Root cause**: The CSS uses `var(--accent)`, `var(--panel)`, `var(--panel-2)`, `var(--muted)`, `var(--border)` in many rules, but `:root` never defines them. The browser resolves undefined custom properties to the empty string, which means:
- `background: var(--panel)` → `background: ;` → no background (inherits from parent or transparent)
- `border: 1px solid var(--border)` → `border: 1px solid ;` → no border color → invisible border
- `color: var(--muted)` → `color: ;` → inherits from parent (often white on dark)
- `box-shadow: 0 4px 20px var(--shadow)` → `box-shadow: 0 4px 20px ;` → invalid value → ignored entirely

This is the worst kind of CSS bug because **every invalid `var()` reference is silently dropped — no warning, no console message, no parse error**. The selector still matches, the cascade still runs, the layout is computed. Just nothing renders.

**Reproducible diagnostic**:
```bash
# Count references vs definitions of every custom property
grep -oE "var\(--[a-zA-Z0-9_-]+\)" css/style.css | sort | uniq -c | sort -rn
```
Then for the top-used ones, verify each is defined in `:root`:
```bash
grep -nE "^\s*--(accent|panel|muted|border)" css/style.css
```
Zero hits = the var is referenced but never defined. That's your bug.

You can also detect it at runtime in DevTools:
```javascript
getComputedStyle(document.documentElement).getPropertyValue('--accent')  // '' if undefined
```

**Real failure trail from ImageGen admin panel (Aug 2026)**:
- CSS had `--bg-X`, `--line`, `--line-strong`, `--text`, `--text-2`, `--text-3`, `--gold-X`, `--danger`, `--success` defined (15 vars)
- But the admin / msg / sample sections referenced `--panel`, `--panel-2`, `--border`, `--muted`, `--accent` (5 different names) — **38 references total, 0 definitions**
- Symptom: admin page rendered with all-invisible borders, no active-tab highlight (gold on transparent), unread badges as faint gold-on-gold dots
- User feedback: "admin的界面UI优化一下，看不清楚"

**Fix — two options, pick by context**:

**Option A: Add the missing definitions as semantic aliases.** Best when the design system was renamed mid-development and the references are still valid intent. Add to `:root`:
```css
:root {
  /* ... existing tokens ... */
  --panel: var(--bg-2);          /* surface */
  --panel-2: var(--bg-3);        /* surface-elevated */
  --border: var(--line-strong);  /* hairline */
  --muted: var(--text-3);        /* low-contrast text */
  --accent: var(--gold-200);     /* brand highlight */
}
```
This is **additive only** — doesn't break any code that uses the new names, and immediately restores all 38 broken references.

**Option B: Rewrite the references to use the new names.** Best when you want a single canonical vocabulary. Use `sed -i 's/var(--panel)/var(--bg-2)/g'` carefully (audit the substitutions, run `node --check` on the resulting CSS, then deploy). More churn, but eliminates the dual-naming confusion.

**Prevention — when refactoring a design system**:
1. **Never delete an old var name without grepping every reference first.** Run `grep -rn 'var(--oldName)' src/` before removing any `:root` declaration. If references exist, either add back the alias (Option A above) or migrate the references first.
2. **Add an ESLint-style lint for CSS.** `stylelint` with `custom-property-no-unknown` (or hand-rolled) flags undefined `var()` refs at build time. Catches this class of bug before deploy.
3. **Add a one-line sanity check** to your deploy script:
   ```bash
   # Before deploy: every var() must be defined in :root
   python3 -c "
   import re
   css = open('css/style.css').read()
   refs = set(re.findall(r'var\(--([a-zA-Z0-9_-]+)\)', css))
   defs = set(re.findall(r'^\s*--([a-zA-Z0-9_-]+)\s*:', css, re.MULTILINE))
   missing = refs - defs
   if missing:
       print('UNDEFINED CSS VARS:', missing); raise SystemExit(1)
   "
   ```
4. **When renaming a token** (e.g. `--accent` → `--brand`), do BOTH: add `--brand` to `:root` AND keep `--accent: var(--brand)` as an alias for one release, so old references don't silently break.

**Lesson to encode**: CSS custom properties are type-checked by the runtime only insofar as `var()` references must syntactically parse — semantically, an undefined var is just empty string. There is no "undefined variable" warning. Treat every `var(--foo)` reference as a contract with `:root`: if you remove the declaration, every reference becomes a silent bug.

### `const t` (or any single-letter) shadows the i18n function `t()` — Temporal Dead Zone crash

**Symptom**: An admin / settings / messages view renders fine on first load, but clicking into a sub-list (e.g. selecting a conversation, switching tabs) throws `ReferenceError: Cannot access 't' before initialization`. No `t()` call exists in the surrounding code, yet the error names `t`. Stack trace points to a `.map()` callback that ran inside the same function.

**Root cause**: A local `const`/`let` named `t` (or `e`, `i`, `n`, `r` — any one-letter binding) creates a **temporal dead zone for the entire function scope**, even if the declaration appears AFTER a closure that references the outer name. The classic case:

```js
function renderMessages(messages, userId) {
  const html = messages.map(m => {
    // ...
    const name = isUser ? m.sender_name : t('msgAdminLabel');   // ← CRASH: outer t() in TDZ
    return `<div>${name}</div>`;
  }).join('');
  thread.innerHTML = html;
  // ...later, after .map() returned...
  const t = document.getElementById('adminMsgThread');           // ← this `const t` shadows the i18n t() for the WHOLE function
  if (t) t.scrollTop = t.scrollHeight;
}
```

The closure inside `.map()` is evaluated lazily, but it captures the function scope's `t` binding. From the closure's perspective, that binding is "in the temporal dead zone" until the `const t = ...` line actually executes. So the very first time `.map()` runs (which happens BEFORE the `const t` line), the lookup `t('msgAdminLabel')` hits the TDZ.

The same trap applies to any single-letter global the codebase relies on as a function — `t` (i18n), `e` (event sometimes), `i` (iterator, occasionally an index helper). Multi-letter names are safer because the collision is rarer.

**Fix — rename the local, not the global**:
```js
// Pick any name that doesn't collide with a function-style global
const threadEl = document.getElementById('adminMsgThread');
if (threadEl) threadEl.scrollTop = threadEl.scrollHeight;
```

**Diagnose the same shape in other views** before declaring the bug fixed:
```bash
grep -nE "(const|let|var) (t|e|i|n|r)\b" js/views/*.js js/*.js
```
For every match, check whether the same enclosing function calls `t('...')` (or `e(...)`, etc.) via a closure that runs before the declaration line. Each one is a ticking bomb — fixing the surface bug leaves the rest.

**Prevention**:
- For file-scoped globals in the same `<script>` (e.g. `function t`), prefer attaching to `window` (`window.t = t`) so call sites use the explicit `window.t(...)` form. Local `const t` can no longer shadow it.
- For project convention, never use single-letter local `const` for DOM elements. Use `el` / `node` / `btn` / `threadEl` / `target`.
- When adding a new single-letter global helper, audit existing view files for the same letter before committing.

### antd 5.x: static `message.xxx()` triggers warning — wrap app in `<App>` + use `App.useApp()`

**Symptom**: every static `message.success(...)`, `message.error(...)`, `message.warning(...)` call triggers a console warning:

```
Warning: [antd: message] Static function can not consume context like
dynamic theme. Please use `App` component instead.
    at ManualVideo.tsx:75
```

No visible breakage — the toast still appears, the action still runs — but the console fills with one warning per call, and the warning never goes away.

**Real failure trail from AIGC_agent (2026-08-09)**: User opened the manual video page and saw **only** two console messages — the favicon 404 and this antd warning. Because the console "looked broken" (warnings every click), the user assumed the actual generate flow was also broken. Real bug was a missing `save_video()` call elsewhere; the console noise just made it harder to see.

**Root cause**: antd 5.x added a `<App>` component that provides theme/locale context to message/notification/modal. Static API functions (`message.xxx(...)`) are imported directly from the antd package and **cannot** read this context — they always emit the warning. The recommended pattern is to use the hook-based instance returned by `App.useApp()`.

**Two-part fix**:

1. **main.tsx** — wrap the app tree in `<App>` inside the existing `<ConfigProvider>`:

   ```tsx
   import { App as AntApp, ConfigProvider, theme } from 'antd';

   <ConfigProvider locale={zhCN} theme={{...}}>
     <AntApp message={{ maxCount: 3 }}>     {/* ← NEW */}
       <BrowserRouter>
         <App />
       </BrowserRouter>
     </AntApp>
   </ConfigProvider>
   ```

2. **Each page** — replace `import { message }` with `App as AntApp`, then use the hook:

   ```tsx
   import { App as AntApp } from 'antd';

   const ManualVideoPage: React.FC = () => {
     const { message } = AntApp.useApp();   // ← hook-based instance
     // ... use message.success/error/warning as before
   };
   ```

   The hook returns an instance that reads context from the nearest `<App>` ancestor, so no warnings, and theme/locale tokens actually apply.

**Multi-page rollout**: when multiple page files use static `message.xxx`, fix all of them in the same patch — leaving one page on the static API re-introduces the warning for every action on that page. Audit with:

```bash
grep -rn "from 'antd'" web/src/pages/ | grep -E "(^|, )message(,| )"   # legacy static import
grep -rn "App as AntApp" web/src/pages/                                 # new pattern
```

**Verification**:
- Hard-refresh the browser (`Ctrl+Shift+R`)
- Trigger a toast (e.g. upload an image → `message.success('参考图已上传')`)
- Console: zero `[antd: message]` warnings. The toast still renders.

**Related antd 5.x traps**:
- `notification.xxx()` and `Modal.xxx()` static calls have the same warning — same fix (use the hook instance)
- `App.useApp()` also returns `{ notification, modal }` — destructure all three if your page uses any of them

## Multi-language at scale — N locales without bloating the bundle

Applies whenever "add language X" turns into "support every language the UI kit ships".

- **Never statically import N dictionaries.** Put each locale behind a dynamic `import()` (its own chunk) and keep only the source language + one fallback resident. 70 statically imported catalogs are megabytes in the main bundle for text that is 99% unused per session.
- **Three layers**: authored source (`parts/<area>.json`, one file per screen area), per-language overlay (`catalog/<lang>.json`), generated locale modules (`locales/<lang>.ts`) written by a single merge script. Adding a language is then "translate one file + re-run merge" — no code change, and the merge script is the one place that enforces key parity.
- **Fallback chain, never a raw key**: `catalog[lang] → language-family fallback (zh-* → zh, others → en) → humanized last segment`. A half-translated catalog must still render readable words, not `loop.plan.approved`.
- **Derive endonyms, don't hand-write them**: `new Intl.DisplayNames([locale], {type:'language'}).of(lang)`. Hand-typing 70 native language names is how you ship a typo in a language you cannot read.
- **Past ~15 entries the language picker needs search** (`showSearch` + `optionFilterProp="label"`), otherwise picking a language is scrolling.
- **RTL is two changes, not one**: `<html dir="rtl">` does not flip component libraries — Ant Design reads `<ConfigProvider direction>`. And physical spacing (`marginLeft/Right`, `paddingLeft/Right`, `borderLeft/Right`, `textAlign:'left'|'right'`) must become logical properties (`marginInlineStart`, …) or the layout stays LTR. Audit by grepping the physical names, and only rewrite object keys — bare `left:`/`right:` also appear in API payloads and positioning math.
- **The "no hardcoded strings" gate must cover call arguments**, not just JSX: `msgApi.error('failed to load template')`, `setXxxError('enter a path')` never appear as JSX text, so a JSX-only checker reports the screen as fully translated while English leaks through toasts and inline errors.
- **Verify by switching, per page**: in the target language assert no source-language sentences remain (proper nouns, user data and terms like `API key` are expected); in the source language assert zero non-source characters outside user data. For RTL, assert `document.documentElement.dir` **and** that a known nav element's `x` actually moved to the mirrored side — the attribute alone proves nothing.
- **Machine translation is a pipeline, not a one-shot**: batch it, write after every batch (resumable — a crash costs nothing), pin a glossary for product/protocol terms, and validate each batch (same key set, no empties, `{placeholders}` byte-identical to the source — a lost `{n}` is a shipped bug). Force a non-reasoning model: a thinking model can return an empty `content` and silently translate nothing.
- **Ship coverage as a number** (`--coverage`: done/total per language) with a strict gate that fails below 100% for languages you claim to support. Report partial languages as partial instead of declaring them done.

Full recipe (commands, runtime wiring, gate scripts): `references/multi-language-at-scale.md`.

## i18n key missing — `t('foo')` silently renders the key as literal text

**Scenario**: You add a new view or feature that uses `t('msgTitle')`, `t('msgWithAdmin')`, etc. The `t()` function is `(I18N[lang] && I18N[lang][key]) || key` — if the key is missing from every language block, it returns the key unchanged. The page renders `msgTitle` as visible text instead of "Messages" / "留言".

**Symptom**: User sees literal camelCase keys (`msgTitle`, `msgEmpty`, `msgWithAdmin`) where translated UI text should be. Easy to miss because there is no console error — `t()` just falls through to the key.

**Root cause**: When expanding i18n or adding a new view, the new keys are referenced in views/*.js but never added to the language dictionaries. Especially common when:
- Adding a new feature (messaging, sample gallery, admin UI) with `t('msgTitle')` etc.
- Expanding i18n.js from 2 langs to N langs by extracting keys from the existing en block — any keys added AFTER the original en block existed won't be propagated
- New views added after the i18n expansion script ran

**Fix — after adding new UI text, batch-register missing keys for ALL language blocks:**

1. Extract all `t('xxx')` keys used in the new view:
   ```bash
   grep -oE "t\('[a-zA-Z]+'\)" js/views/messages.js | sort -u
   ```

2. For each lang block in i18n.js, append missing keys. **For 8+ language blocks, do NOT use a single broad regex** — the closing `},` of the lang block collides with the closing of nested objects (e.g. `STYLE_LABELS` items). Instead, find each `  <lang>: {` start, locate the FIRST `  },\n` after it (the top-level block close), and insert the new keys inside that range right after the last existing key (e.g. `msgAdminReply:`):

   ```python
   import re

   with open('js/i18n.js', 'r', encoding='utf-8') as f:
       content = f.read()

   for lang, keys in NEW_KEYS.items():
       insertion = ''.join(f"    {k}: '{v}',\n" for k, v in keys.items())
       # Find the lang block boundaries
       start_match = re.search(f"  {lang}: {{\\n", content)
       if not start_match: continue
       block_start = start_match.end()
       end_match = re.search(r"^  \},\n", content[block_start:], re.MULTILINE)
       if not end_match: continue
       block_end = block_start + end_match.start()
       # Insert right after the last key in the block (msgAdminReply is a safe anchor)
       msg_match = re.search(r"(    msgAdminReply: '(?:[^'\\]|\\.)*',)\\n",
                             content[block_start:block_end])
       if not msg_match: continue
       insert_pos = block_start + msg_match.end()
       content = content[:insert_pos] + insertion + content[insert_pos:]

   with open('js/i18n.js', 'w', encoding='utf-8') as f:
       f.write(content)
   ```

3. Verify with grep count — every key should appear N times (one per lang):
   ```bash
   grep -c "msgTitle:" js/i18n.js   # expect N (number of languages)
   ```

4. Watch for **unescaped apostrophes** in non-English strings (e.g. French "l'instant"). Write them as `'l\\'instant'` in Python source so the JS file has the correct escape.

**Prevention**: Whenever you add a new view or feature, the workflow is:
- Add the view
- Add the keys to I18N.en first
- Then propagate to all other langs (Python regex or hand-write)
- Test in browser, not just `node --check` — `t()` returning the key is a UI-level bug, not a syntax bug

### Static header/footer text in `index.html` never translates — needs `data-i18n`

**Scenario**: Patching an existing multi-language SPA. View content (in `<main id="app">`) translates correctly when the user toggles the language, but the **header navigation menu** and **footer column titles + links** stay in English. User reports "the menu doesn't update with language" or "footer is still in English after switching to 中文".

**Symptom**: Click "中" → main page hero, buttons, form labels flip to the new language, but the static nav bar (e.g. "Characters / Scenes / Props / Pricing") and footer (e.g. "PRODUCT / ACCOUNT / LEGAL") stay English.

**Root cause**: `setLang()` only updates elements with a `data-i18n` attribute. Static HTML in `<header>` and `<footer>` lives outside `<main>` and is never re-rendered (views only touch `<main>`). So the ONLY mechanism that updates header/footer text is the `data-i18n` scan in `setLang`. If the static HTML is hardcoded English with no `data-i18n`, it stays English.

```html
<!-- ❌ BROKEN -->
<a href="#/pricing" class="nav-link">Pricing</a>

<!-- ✅ FIXED -->
<a href="#/pricing" class="nav-link" data-i18n="navPricing">Pricing</a>
```

**Fix**:
1. Add `data-i18n="<key>"` to every static text element in `<header>` and `<footer>` (and any other static templates like modals or error pages in `index.html`)
2. Ensure each `<key>` exists in every language block of `I18N` (use the batch-add recipe in the pitfall above, or the more detailed one in `multi-file-spa` → "Static header/footer text never translates without `data-i18n`")
3. Bump `?v=N` on all script tags for cache-bust
4. Hard-refresh in browser (`Ctrl+Shift+R`) and verify the header AND footer translate

**Audit command** to find every hardcoded static text node in one pass:
```bash
grep -nE '<a|<h[1-6]|<span|<p|<button|<label' index.html | grep -v 'data-i18n'
```

**For the full diagnosis ladder, batch-add recipe for 8+ languages, and prevention checklist, see `multi-file-spa` → "Static header/footer text never translates without `data-i18n`" pitfall.**

## `const t = ...` shadows the global i18n function `t()` → TDZ crash

**Symptom**: `ReferenceError: Cannot access 't' before initialization` thrown from inside a view's render function when calling `t('someKey')`. The error message is unhelpful — it just says `t`, not "you shadowed the i18n function". Often happens inside `.map()` / `.forEach()` / `.filter()` callbacks.

**Root cause**: In i18n.js, the translation function is a top-level function declaration: `function t(key, params) {...}`. Function declarations are hoisted, so the global `t` IS in scope. **But** when a view file uses `const t = someValue;` or `let t = ...` anywhere inside a function scope, that declaration creates a **temporal dead zone (TDZ)** for the *entire scope* — the `const t` line acts as a poison pill for the variable name `t` in that scope, regardless of position.

The crash happens when JS tries to call `t('foo')` BEFORE the `const t = ...` line executes. In `.map()` / `.filter()` / `.forEach()` callbacks, this is easy to hit because the callback runs before the surrounding function's later statements:

```javascript
// admin.js renderMessages() — REAL failure trail (ImageGen 2026-08)
async renderMessages(messages, userId) {
  const html = `
    ${messages.map(m => {
      const isUser = m.sender === 'user';
      const name = isUser ? (m.sender_name || 'User') : t('msgAdminLabel');
      //              ↑ t() called HERE in the .map() callback
      // ... (40 lines of HTML building) ...
    }).join('')}
  `;
  thread.innerHTML = html;
  // ...
  const t = document.getElementById('adminMsgThread');
  // ↑ const t on line 212 — AFTER the t() call on line 173 — shadows i18n t() for the whole function
  if (t) t.scrollTop = t.scrollHeight;
}
```

The `.map()` callback runs before the function reaches line 212. When the callback calls `t('msgAdminLabel')`, the enclosing scope's `t` is still in TDZ because the `const t = ...` declaration has not been evaluated yet. The browser throws `Cannot access 't' before initialization` from inside the callback — far away from the actual culprit line.

**Common variable names that collide with the i18n `t`**:
- `const t = btn.dataset.tab;` (tab key in click handler)
- `const t = url.searchParams.get('admin_token');` (URL query param)
- `const t = document.getElementById('...');` (DOM element)
- `let t = 0;` (timer / counter)

**Fix — use descriptive variable names that don't collide with the i18n function:**

```javascript
// Bad — shadows i18n t
const t = document.getElementById('adminMsgThread');
if (t) t.scrollTop = t.scrollHeight;

// Good
const threadEl = document.getElementById('adminMsgThread');
if (threadEl) threadEl.scrollTop = threadEl.scrollHeight;

// Bad — shadows i18n t
const t = btn.dataset.tab;
if (t === 'samples') await this.loadSamples();

// Good
const tabKey = btn.dataset.tab;
if (tabKey === 'samples') await this.loadSamples();

// Bad — shadows i18n t
const t = url.searchParams.get('admin_token');
if (t) setAdminToken(t);

// Good
const tokenFromUrl = url.searchParams.get('admin_token');
if (tokenFromUrl) setAdminToken(tokenFromUrl);
```

**Audit before deploy** — run this on every view file to catch shadowing before it ships:
```bash
grep -nE "^\s*(const|let)\s+t\s*=" js/views/*.js
```
Any match (other than the i18n.js definition itself, which is `function t(...)`) is a TDZ time bomb waiting to detonate when a developer uses `t` in a closure that runs before the declaration line.

**Why this is sneaky**: The error doesn't say "you shadowed the i18n function" or "rename your variable". The browser just says `Cannot access 't' before initialization` with a stack frame inside your `.map()` callback. Without knowing to look for `const t = ...` in the enclosing scope, you'll chase the wrong line for hours. The fix is also non-obvious because the line that crashes is far away from the line that causes the shadow.

**Convention recommendation** for any i18n-using project: add an ESLint rule `no-shadow` with `builtinGlobals: false` AND a custom rule banning `const t` / `let t` in any view file, OR adopt a project-wide convention: never use single-letter variable names in view files.

## HTML `${t('foo')}` — template literal syntax in raw HTML is literal text

**Scenario**: You write `<a href="#/messages">${t('msgTitle')}</a>` directly in index.html, intending the JS `t()` function to fill in the text.

**Symptom**: The link displays the literal string `${{msgTitle}}` (looks like Angular interpolation syntax). No console error. The `${...}` only gets evaluated inside JavaScript template literals (backticks) or string concatenation, not in static HTML.

**Root cause**: HTML is parsed before any JS runs. By the time `t()` exists in the JS scope, the HTML text has already been parsed as plain text. The browser doesn't have a syntax for in-HTML expression interpolation.

**Fix — two-part pattern:**

1. **Static fallback text** in the HTML so the UI is never broken before JS loads (and is indexable by crawlers):
   ```html
   <a href="#/messages" class="nav-link-msg" style="display:none">Messages</a>
   ```

2. **Re-render on state changes** (login/logout, language switch) via `updateUserMenu()` or equivalent:
   ```javascript
   function updateUserMenu() {
     const msgLink = document.querySelector('.nav-link-msg');
     if (msgLink) {
       msgLink.style.display = currentUser ? '' : 'none';
       msgLink.textContent = t('msgTitle');   // ← re-evaluate t()
     }
   }
   ```

**Verification**: Hard refresh the page, click the link — text should show translated version, not `{{...}}`. Switch language — text should update.

## CSS scroll/hover perf killers — the `.workspace-canvas` blur trap

**Scenario**: User reports "the page scrolls slow" or "hover feels janky". DevTools shows 30+ ms paint frames on every scroll event.

**Common CSS culprits (ranked by impact on scroll perf):**

| # | Effect | Where it hurts | Cost |
|---|---|---|---|
| 1 | `backdrop-filter: blur(N)` on a large container that wraps scrollable content | Every scroll frame re-blurs the entire content area | **Worst** — 30-60ms per frame on mid-range hardware |
| 2 | `backdrop-filter: blur(N) saturate(150%)` on sticky header | Every scroll blurs again | High |
| 3 | `transition: all .2s` on a grid item | Hover anywhere triggers compositor for every grid item | High |
| 4 | `transform: translateY(-Npx)` on `:hover` of a grid item | Compositor layer per item; transform re-runs on every hover | Medium |
| 5 | `box-shadow: 0 4px 14px rgba(...)` on `.active` state | Re-paint per active item | Medium |
| 6 | Decorative `::before { box-shadow: 0 0 6px ... }` per active chip | Re-paint per chip | Low-medium |
| 7 | Large grid (100+ items) all rendered at once, even off-screen | DOM + paint cost | High (depends on item complexity) |

**Diagnosis path:**
1. DevTools → Performance tab → record a scroll session
2. Look for "Recalculate Style" and "Paint" entries ≥ 5ms
3. Inspect the element under the long paint to find the heavy effect
4. Toggle `backdrop-filter` first — usually the biggest win

**Fixes (each is independent):**

```css
/* 1. Remove blur from large containers — increase opacity to compensate */
.workspace-canvas {
  background: rgba(20,20,29,0.92);  /* was rgba(...,0.78) + backdrop-filter: blur(20px) */
  /* backdrop-filter: blur(20px) saturate(140%); */  ← REMOVED
}

/* 2. Reduce sticky header blur */
.header {
  background: rgba(6,6,8,0.85);  /* was 0.7 */
  backdrop-filter: blur(12px) saturate(120%);  /* was blur(20px) saturate(150%) */
}

/* 3. Replace `transition: all` with specific properties */
.option-chip {
  transition: background-color .15s ease, border-color .15s ease, color .15s ease;
  /* transition: all .2s; ← REMOVED */
  contain: layout style;  /* bonus: isolate this element's layout/paint */
}

/* 4. Drop transform from hover */
.option-chip:hover { border-color: var(--gold-300); color: var(--text); }
/* .option-chip:hover { ...; transform: translateY(-1px); } ← REMOVED */

/* 5. Drop box-shadow from active state when many items have it */
.option-chip.active {
  background: var(--gold-grad-soft);
  border-color: var(--gold-200);
  color: var(--gold-100);
  /* box-shadow: 0 4px 14px rgba(...); ← REMOVED for grids */
}

/* 6. Drop decorative ::before glows */
.option-chip.active::before { content: none; }  /* or just delete the rule */

/* 7. Skip off-screen rendering for tall grids */
.option-grid-tall {
  max-height: 220px;
  overflow-y: auto;
  content-visibility: auto;             /* skip off-screen paint */
  contain-intrinsic-size: auto 220px;   /* reserve height so layout doesn't shift */
}
```

**What to keep:**
- Hover effects that don't trigger compositor (border-color, color, background-color)
- Small, focused box-shadow on cards (not on grid items)
- `contain: layout style` for grid items (cheaper than nothing, helps browsers skip work)
- `content-visibility: auto` for any tall section that's likely off-screen initially

**What to never put on a large scrollable container:**
- `backdrop-filter: blur()` of any value
- Heavy `filter:` chains
- `mask-image` (unless it's a static gradient)

**Verify after fix:** DevTools Performance tab — long paints should drop to < 5ms; hover on chip should not trigger paint of other chips; scroll FPS should be 60.

### `sed -i` multi-pass accumulation — replaces cascade into broken paths

**Scenario**: You're bumping cache-bust query params across `index.html` (e.g. `?v=11` → `?v=12` → `?v=13`) before a deploy. You use `sed -i 's/v=11/v=12/g' index.html`. Then you run it again with `sed -i 's/v=12/v=13/g'`. After the second pass, line 75 of index.html reads:

```html
<script defer src="js/i18n.js?v=11.js?v=12"></script>
```

Wait — that's wrong. Should be `js/i18n.js?v=13`. Where did the duplicate come from?

**Symptom**: After one or more `sed -i` runs, `<script>` / `<link>` `src` / `href` attributes have **stacked version numbers** like:

```html
<script src="js/views/js/views/home.js?v=11.js?v=12"></script>
<link href="css/style.css?v=12.css?v=13.css?v=14">
```

Two failure modes cause this:

1. **Replacement string contains the pattern's tail** — running `sed -i 's/=11/=12/g'` on a file with `=11` then `sed -i 's/=12/=13/g'` on the result, where some lines originally had multiple matches, leaves things in sync — but if you instead wrote `sed -i 's/=11/=12.js?v=12/g'` (intending to add the suffix), running it twice appends the suffix twice.
2. **Accidentally using `&` (matched text) in the replacement** — `sed -i 's/js/i18n.js?v=11/js/&.js?v=12/g'` appends `.js?v=12` to whatever matched. Re-running appends again.

**Real failure trail from ImageGen (Aug 2026):**

```bash
# Intended: bump all v=N to v=N+1
sed -i 's/css\/style.css?v=12/css\/style.css?v=13/g; s/js\/[a-z]*\.js?v=11/js\/&.js?v=12/g' index.html
```

The second substitution uses `&` (sed's matched-text placeholder) without realizing it. After 3 bumps running the same pattern, index.html had:

```html
<script defer src="js/js/store.js?v=12.js?v=12"></script>
<script defer src="js/views/js/views/home.js?v=11.js?v=12"></script>
<script defer src="js/views/js/views/login.js?v=11.js?v=12"></script>
```

Browser fails to load the script files (404), page renders broken.

**Fix — use `patch` or `write_file` for HTML attribute edits, not `sed`:**

```bash
# Wrong — sed accumulation
sed -i 's/v=11/v=12/g' index.html
sed -i 's/v=12/v=13/g' index.html

# Right — single patch with the exact target line
patch(path="index.html",
      old_string='<link rel="stylesheet" href="css/style.css?v=11">',
      new_string='<link rel="stylesheet" href="css/style.css?v=13">')
```

Or rewrite the script block section via `write_file` if many tags need bumping at once. Both tools are idempotent — re-running doesn't compound.

**Diagnostic when something looks wrong after `sed`:**

```bash
grep -nE "v=[0-9]+\.[a-z]|js/.*js/|css/.*css/" index.html
# Look for stacked path components or stacked version strings
```

If you find them, **don't try to fix with another sed pass** — write the affected `<script>` / `<link>` tags fresh via patch or `write_file`.

**Prevention:**

- For HTML attribute edits (paths, version params, hrefs), **prefer `patch` over `sed`** — patch is context-aware, idempotent, and refuses to match ambiguous patterns.
- If you must use sed for bulk version bumps, use a regex that **only matches the exact target** and appends unique suffix:
  ```bash
  # Right way with sed — match the full token, not a substring
  sed -i -E 's/(\?|&)v=11([^0-9])/\1v=12\2/g' index.html
  # Only matches v=11 followed by non-digit, so subsequent bumps are safe
  ```
- Verify with `grep -c "v=12" index.html` after each bump — count should equal the number of v=11 occurrences you started with, not 2× or 3×.
- For HTML/JS edits that affect cache-busting, prefer bumping once and deploying once — multiple sed passes in sequence are a foot-gun.

### patch tool escape trap — literal `\\n` / `\\"` corruption

**Scenario**: You patch a JS file that contains `\\n` (escape sequences inside JavaScript string literals, 0x5C+0x6E) or `\\"` (0x5C+0x22). The patch tool's fuzzy matching can corrupt these into double-escaped `\\\\n` (0x5C+0x5C+0x6E) or produce literal `\\n` characters instead of real newlines.

**Symptom**: patch reports `diff` success, but the file now has literal backslash-n characters where real newlines should be, or double-escaped sequences. JS engine refuses to parse.

**Diagnosis**: Use `repr()` to inspect the broken region:
```bash
python3 -c "
with open('file.html', 'rb') as f:
    data = f.read()
idx = data.find(b'brokenFunction')
print(repr(data[idx:idx+200]))
"""
```

Look for:
- `\\\\n` = 0x5C 0x5C 0x6E (double backslash-n) where `\\n` is expected
- `\\\\"` = 0x5C 0x5C 0x22 (double backslash-quote) where `\\"` is expected
- `\\n` as literal text (0x5C 0x6E) where real newlines (0x0A) should be

**Fix**: Switch to Python bytes-level replacement via `terminal`:
```python
# Replace via python3 in terminal
with open('file.html', 'rb') as f:
    data = f.read()
old = b'exactBytestringWithCorrectEscaping'
new = b'replacementWithRealNewlines'
data = data.replace(old, new, 1)
with open('file.html', 'wb') as f:
    f.write(data)
```

The Python bytes literal makes escape semantics explicit — there's no fuzzy matching to corrupt the result. Always verify with `node --check` afterward.

### JS syntax verification after every script edit

After any modification to `<script>` content in HTML files, validate syntax immediately:

```bash
# Extract script content and check
node -e "
const fs=require('fs');
const c=fs.readFileSync('file.html','utf-8');
const start=c.indexOf('<script>')+8;
const end=c.lastIndexOf('</script>');
const script=c.substring(start,end).replace(/\r/g,'');
fs.writeFileSync('C:/Users/leohu/_check.js',script,'utf-8');
" && node --check "C:\\Users\\leohu\\_check.js"
```

⚠️ **Windows**: `/tmp/` doesn't exist — write to `C:/Users/leohu/` instead.

**Binary search for error location** when `node --check` doesn't give a clear line:
```javascript
const lines = script.split('\n');
let lo = 0, hi = lines.length;
while (lo < hi) {
  const mid = Math.floor((lo + hi) / 2);
  try { new Function(lines.slice(0, mid).join('\n')); lo = mid + 1; }
  catch(e) { hi = mid; }
}
console.log('Error near line:', lo);
```

**Brace/paren balance check** before any complex edit:
```bash
node -e "
const fs=require('fs');
const c=fs.readFileSync('file.html','utf-8');
const s=c.substring(c.indexOf('<script>')+8,c.lastIndexOf('</script>'));
console.log('(:', s.split('(').length-1, ') :', s.split(')').length-1);
console.log('{:  ', s.split('{').length-1, '}  :', s.split('}').length-1);
"
```

If `(` ≠ `)` or `{` ≠ `}`, do NOT deploy — the file has a syntax error. Find it with the binary search above.

**JS string concatenation delete trap**: When removing the last segment from a JS string concatenation chain:
```javascript
// ❌ Before (removing "No text generation" line):
"... Texture overlay\\n"+
"- No text generation...";      // ← deleting this line
var r=await fetch(...)

// ❌ After — broken:
"... Texture overlay\\n"+       // ← dangling + ! 
var r=await fetch(...)           // SyntaxError!

// ✅ After — fixed:
"... Texture overlay\\n";        // ← + changed to ;
var r=await fetch(...)
```

Always use `node --check` after any line-level removal in JS string chains.

- **Don't remove features the user didn't ask to remove.** If they say "hide X in mode Y", just hide it (toggle class/style), don't delete the code.
- **Don't restructure or reformat existing code.** The user's formatting preferences matter — compact CSS on one line or expanded on multiple lines, keep it as-is.
- **Verify file backup exists before making changes.** If no git or backup copy exists, create one: `cp file.html file.html.bak`.
- **After editing, restart the dev server.** Static file servers cache old file content. Add a cache-busting query param (`?t=N`) when navigating in the browser.

### Parent `overflow` clips absolute-positioned children

When a parent element has `overflow: auto` or `overflow: hidden`, absolutely-positioned children that extend beyond the parent's bounds **get clipped** — they're invisible even though they're positioned correctly.

**❌ Broken:**
```css
.nbd { flex: 1; overflow: auto; }
.sboard-panel { position: absolute; left: 100%; top: 0; width: 260px; }
/* → panel exists in DOM but is invisible, clipped by nbd's overflow */
```

**✅ Fix — set overflow to visible:**
```css
.node[data-type='sboard'] .nbd { overflow: visible; }
```

Or move the absolute element outside the overflowing container in the DOM hierarchy.

### Right-side panel pattern (node + floating panel)

For attaching a floating panel to the right edge of a node box, without affecting the node's own width:

```css
.node { position: absolute; }
.node .panel {
  position: absolute;
  left: 100%;
  top: 0;
  width: 260px;
  border: 1px solid rgba(0,0,0,0.1);
  border-radius: 8px;
  display: none;
  background: #fff;
  box-shadow: 0 4px 20px rgba(0,0,0,0.12);
  z-index: 20;
}
.node .panel.has-prompt { display: flex; }
```

Key points:
- `position: absolute` anchors to the nearest positioned ancestor (`.node`).
- `left: 100%` places it flush at the node's right edge.
- The parent `.node` needs `position: absolute` (or `relative`).
- `display: none` by default, toggled via `.has-prompt` class.
- Panel is out-of-flow — doesn't affect node width or storyboard column layout.

### CSS Grid + overflow: hidden = collapsed grid rows

When a CSS Grid item has `overflow: hidden`, the browser treats it as a **scroll container** and sets its minimum content size to **zero** for grid track sizing. The grid row collapses to ~14px regardless of content height.

**❌ Broken:**
```css
.grid-item { overflow: hidden; border-radius: 8px; }
.grid-item img { width: 100%; aspect-ratio: 16/9; }
/* → grid rows are 14px tall, images invisible */
```

**✅ Fix — apply border-radius to the img/video instead:**
```css
.grid-item { overflow: visible; border-radius: 8px; }
.grid-item img, .grid-item video { border-radius: 7px; }
```

Or if you need `overflow: hidden` for animation/hover effects, set an explicit `min-height` on the grid item, or avoid making grid items scroll containers.

### Per-mode state leaks when switching tabs

Multi-mode apps (txt2img / img2img / txt2vid / img2vid) often share global variables like `selectedStyle`, `audioFiles`, `lastOutputUrl`. When the user switches modes, the previous mode's state bleeds into the new one.

**Fix — per-mode caching with save/restore in the mode-switch function:**

```javascript
// 1. Storage
let perModeStyle = {};
let perModeAudio = {};
let perModeOutput = {};

// 2. Save on switch (in applyMode)
if (prevMode && prevMode !== mode) {
  perModeStyle[prevMode] = selectedStyle;
  perModeAudio[prevMode] = [...audioFiles];
  perModeOutput[prevMode] = { lastOutputUrl, outputType, imageSrc, videoSrc, emptyHidden };
}

// 3. Restore after switch
const savedStyle = perModeStyle[mode];
if (savedStyle) { selectStyle(savedStyle); }
else { resetToDefault(); }
```

Remember to reset new modes to defaults when they have no saved state — otherwise the previous mode's value persists (the "why did X leak into Y" bug).

### Server-side polling vs client re-polling

When the backend POST handler already polls upstream and downloads the result (returning `outputUrl`), the client **must not** re-poll a status endpoint. The re-poll will try to download the file again, fail (upstream URL expired), and silently keep returning `"running"` — the user sees "生成中..." forever.

**Pattern: check if POST response already has the output:**
```javascript
const result = await requestJson('/api/video/generate', { method: 'POST', body: ... });
// If result.outputUrl exists, the server already handled everything
if (result.outputUrl) {
  videoOutput.src = result.outputUrl;
  videoOutput.classList.remove("hidden");
}
```

### Generating across mode switches

When an async generation (image/video) is in progress and the user switches to a different mode, the completion callback must not update the **current** mode's display. Instead, cache the result for the mode that triggered the generation.

**Pattern — track `generatingForMode`:**

```javascript
let generatingForMode = null;

async function generate() {
  generatingForMode = currentMode;   // ← record which mode started this
  
  // ... async work (API call, polling) ...
  
  if (generatingForMode !== currentMode) {
    // User switched away — cache for original mode
    perModeOutput[generatingForMode] = { lastOutputUrl, outputType, ... };
    return;
  }
  // Still the same mode — update display normally
}
```

### Resetting empty output state on mode switch

When switching modes, always reset `emptyOutput.innerHTML` to the default placeholder HTML before checking for cached output. Otherwise the previous mode's "生成中..." or "生成失败" message persists in the DOM.

### Converting PNG icons to transparent background

When icon PNGs have a solid near-white background (RGB mode, no alpha) and need transparency:

```python
from PIL import Image
img = Image.open('icon.png').convert('RGBA')
pixels = img.load()
for x, y in ...:
    if all(c >= 248 for c in pixels[x, y][:3]):
        pixels[x, y] = (*pixels[x, y][:3], 0)
img.save('icon.png')
```

### Model compatibility with reference images

Some video models (grok-imagine-video) always require a reference image. A guard that checks `selectedRefImages.length === 0` will also block text-to-video modes that shouldn't need refs.

**Fix — make the guard aware of the mode:**
```javascript
const needsRef = MODE_CONFIG[currentMode].needsRef;
if (isGrokVideo && needsRef && selectedRefImages.length === 0) {
  addLog("Model requires at least one reference image");
  return;
}
```

## Modal/Lightbox setup checklist

When adding modal/lightbox elements that JS controls with `.hidden` class:

1. Add `hidden` class to the modal and backdrop in HTML
2. CSS: `.modal.hidden { display: none !important; }`
3. CSS: `.modal:not(.hidden) { display: flex; }` (or block)
4. CSS: `.modal-backdrop:not(.hidden) { display: block; }`
5. Verify JS uses `classList.{add|remove|toggle}("hidden")` — do NOT change the JS

## Two-file sync pattern

When the same HTML/JS code exists in two files (e.g. `canvas.html` and `Kairos_canvas.html`), edits must be applied to **both**. The simplest approach:

1. Edit the first file with `patch()` as normal
2. Find the same anchor text in the second file using `search_files` or `grep`
3. Apply the **identical** `patch()` call to the second file

For `\uXXXX`-escaped content (see `references/unicode-escape-search.md`), find the anchor via Python search and apply in a temp script to avoid shell escaping issues.

## Batch / Multi-Change Workflow

When implementing **multiple independent changes** to the same large file (e.g. 7+ changes in one session), follow this systematic pipeline:

### 1. Pre-audit: verify current state with grep
```bash
grep -c "targetFunction\|targetVar\|targetCSS" file.html
```
Zero-count means the feature doesn't exist yet; non-zero means it does — adjust your patch strategy accordingly.

### 2. Make each change as an independent `patch()` call
Each change should be self-contained with enough surrounding context to be unique. Group related changes (e.g. all CSS changes) but keep semantically separate changes in separate calls so one failure doesn't block others.

### 3. Verify each change immediately after applying
```bash
grep -c "newFunctionName\|newCSSClass\|newEventName" file.html
```
If the count is wrong (0 when expecting 1, or 1+ for a removal), the patch silently failed or matched the wrong anchor.

### 4. Syntax / balance check after ALL patches
```bash
python3 -c "
import re
with open('file.html') as f:
    html = f.read()
m = re.search(r'<script>(.*?)</script>', html, re.DOTALL)
if m:
    js = m.group(1)
    ob = js.count('{'); cb = js.count('}')
    op = js.count('('); cp = js.count(')')
    print(f'Braces: {{ {ob} }} {cb}  Balanced: {ob==cb}')
    print(f'Parens: ( {op} ) {cp}  Balanced: {op==cp}')
"
```
Only proceed if all pairs balance. Unbalanced braces mean the file WILL fail to parse.

### 5. Function-existence verification via browser
After the file is loaded in the browser, verify new functions registered correctly:
```javascript
typeof newFunction === 'function'  // should be true
```
This catches cases where a syntax error in one function silently broke the entire `<script>` block (JS parsing fails atomically — one error kills all subsequent code).

### 6. Final checklist
Run a grep-based presence check against EVERY expected change:
```bash
for pattern in "change1-marker" "change2-marker" "change3-marker"; do
  count=$(grep -c "$pattern" file.html)
  echo "$pattern: $count"
done
```

### Python-heredoc JS injection — entire script dies, no clear error message

**Scenario**: You're generating a large block of JavaScript (200-500+ lines) and writing it into a single-file HTML via Python's `execute_code` tool (e.g. assembling a string and writing it via `with open(path,'w') as f: f.write(lines)`). The script is injected into an inline `<script>` block.

**Symptom**: Browser page loads but ALL globals are undefined (`typeof S === 'undefined'`, `typeof renderNode === 'undefined'`, etc.). The right-click contextmenu handler never fires. The browser_console tool shows `{message: "", source: "exception"}` — empty message, no line number, no stack. Debugging stalls.

**Root cause**: A syntax error in the generated JS — but the error has NO useful message because of how the browser reports parse failures in inline scripts. Most likely causes:

1. **Unquoted object keys with hyphens**: `{human-m: "男", human-f: "女"}` — `human-m` is not a valid identifier, JS parser dies. Fix: `{"human-m": "男", "human-f": "女"}`.
2. **Quote escaping inside double-quoted JS string**: `outEl.innerHTML="📥 <a href=\""+url+"\">"` — the `\"` inside `"..."` terminates the JS string. Fix: use single quotes for the outer JS string, `'<a href="'+url+'">'`.
3. **CRLF line endings getting embedded**: if your Python string has literal `\r\n` inside a regex `/.../`, the regex becomes invalid. Use `/\r?\n/` (escape the `?` correctly) or work in bytes mode.

**Diagnosis** — the diagnosis that works:

```bash
# 1. Extract the EXACT inline script that will run, not a "similar" one
python3 -c "
import re
with open('file.html','r',encoding='utf-8') as f: html=f.read()
m = re.search(r'<script>(.*?)</script>', html, re.DOTALL)
with open('D:/inline.js','w',encoding='utf-8') as f: f.write(m.group(1))
"
# 2. Run node --check on the extracted file
node --check D:/inline.js
```

`node --check` will give you the EXACT line number and a clear error like `SyntaxError: Unexpected token '-'`. The browser's empty error message is a known limitation of inline `<script>` parsing failures.

**Why this is different from "JS syntax verification after every script edit"**: that pitfall is about using `node --check` AFTER `patch()` makes a small surgical edit. This is about using `node --check` AFTER Python's `execute_code` writes 200+ lines of generated JS in one shot. The error rate is much higher in the second case because you're constructing JS from concatenated Python strings, not editing existing JS.

**Critical**: when the HTML has MULTIPLE `<script>` blocks (e.g. one inline main script + one external `<script src="three-r149.min.js">`), make sure you extract the INLINE one (`<script>(content)</script>`), not the external one's URL or any other block. A wrong-target check passes while the real script is broken.

**Prevention — generate JS in chunks**:
- Don't write 500 lines of generated JS in one Python heredoc
- Write the function in a temp `.js` file first, `node --check` it, then concatenate
- Or: use `exec()` in a Python subprocess to validate the JS string before writing

```python
# Validate generated JS in Python before writing
import subprocess
js_snippet = "function foo() { return {human-m: '男'}; }"  # would fail
r = subprocess.run(['node','--check','-e',js_snippet], capture_output=True, text=True)
if r.returncode != 0:
    print("BAD JS:", r.stderr)
    # fix it before writing
```

**The "ALL globals undefined" shortcut**: when `typeof X === 'undefined'` for every function in a script, the cause is virtually always an early syntax error killing the whole script. Don't chase undefined references — go straight to `node --check` on the script content.

### Patch Indentation Recovery

**Symptom**: After a `patch()` with multi-line context, the resulting code has shifted indentation — one block is indented 6 spaces when the surrounding code uses 2 spaces, OR 4 spaces when the rest of the file uses 4 spaces. In the worst case, lines that should be at indent level 0 end up at indent level 4, breaking nested code structurally (closing braces, regex patterns, ternary expressions all shift).

**Cause**: `patch()` uses fuzzy matching. Three distinct failure modes:

**Mode 1 — fuzzy match lifts wrong whitespace:**
When the context lines in `old_string` have different indentation than what the matcher expects, the replacement text inherits the wrong indentation level.

**Mode 2 — trailing newline + context mismatch prepending 4 spaces (NEW, bit me hard):**
When `old_string` and `new_string` differ in their trailing context (one ends with `\n`, the other with `\n  ` or extra blank line), the patch tool systematically prepends 4 spaces to every line in the replacement block. The most common trigger:

- `old_string` ends with `,\n}` (closing brace at column 0)
- `new_string` ends with `,\n  }\n` (intentionally adding 4-space indent for a new entry)

The tool's matcher interprets the closing brace as "indented inside the block I just inserted" and re-indents every preceding line in the inserted block by +4 spaces. The result: every line of the inserted content gains 4 spaces of indent. If the inserted content was already at the wrong indent level, this compounds.

**Mode 3 — adding a single new key to an i18n object (recurring trap):**
The most common +4 trigger in practice: adding one new key like `sampleLoading` to `I18N.en` where the surrounding keys are at 4-space indent. If you write the new line at 8 spaces by accident (e.g. copying a context line that was visually indented, or mixing tabs/spaces), the patch tool re-indents every line of `new_string` by +4. `node --check` then fails with `SyntaxError: Unexpected token ':'` or similar.

The reverse case (new key at 0 spaces, context at 4 spaces) has the same effect — the tool snaps to the wrong indent level.

**Prevention for Mode 3 (atomic i18n key addition):**

1. **Match leading whitespace exactly.** Every line of `new_string` must have identical leading whitespace to the corresponding line of `old_string`. The new key you add inherits the indent of the line it sits next to.
   ```javascript
   // ❌ Wrong — new_string line has different indent
   old_string: "    sampleEmpty: 'No samples yet',"
   new_string: "    sampleEmpty: 'No samples yet',\n        sampleLoading: 'Loading…',"
   //                                              ↑ 8 spaces, will trigger +4 shift

   // ✅ Right — same 4-space indent throughout
   old_string: "    sampleEmpty: 'No samples yet',"
   new_string: "    sampleEmpty: 'No samples yet',\n    sampleLoading: 'Loading…',"
   ```

2. **For multi-language i18n batches (CN/JP/KO/FR/DE/ES/RU),** `patch()` is the wrong tool — see "Multi-language i18n Discipline" → "Atomic key addition" above for the python heredoc recipe using `re.search` + `block_end` offsets that bypasses the indent bug entirely.

3. **Always `node --check` the patched file immediately.** Symptom of a Mode 3 fire: `SyntaxError: Unexpected token ':'` at the indent-shifted line. Fix: Python bytes-level scrub via terminal (`re.sub(r'^( {4})+', lambda m: m.group(0)[4:], content, flags=re.M)`) or restore from the indent-correct version manually.

**Lesson to encode**: any time you're adding a single line and the surrounding context is `n` spaces, write the new line at exactly `n` spaces. Don't copy from elsewhere in the file where the indent might differ. Or: bypass `patch()` entirely for single-line additions in i18n objects — Python regex with explicit start/end offsets is safer.

**Real failure trail from ImageGen (Aug 2026):**
```javascript
// Before patch — at correct 0/2/4 indent levels
const MASTER_TEMPLATES = {
  character: {
    sheet: `A professional character ...`
  },

  storyboard: {                // ← want to delete this whole block
    panels4: `...`,
    panels6: `...`,
    _default: `...`
  }
};

// What I wrote:
old_string = "_default: `professional prop ...`\n  },\n\n  storyboard: {"  // ends mid-property name
new_string = "_default: `professional prop ...`\n  }\n};"               // shorter, no storyboard
```

The diff showed success. The actual file had every line of `_default` through `};` re-indented by 4 spaces, plus an extra indentation of the closing braces. `node --check` failed with `SyntaxError: Unexpected token '.'`. Required a Python-bytes recovery to scrub the bad indent from the file.

**Fix — two-pass cleanup:**
1. Apply the functional patch first (it works, just ugly)
2. Apply a **second clean-up patch** that fixes just the indentation:

```javascript
// Before (broken indentation after patch):
//       }else throw new Error(...);
//     }catch(err){...}
//
// After clean-up patch (match the indentation of surrounding code):
patch(path="file.html",
  old_string="      }else throw new Error(d.error&&d.error.message",
  new_string="    }else throw new Error(d.error&&d.error.message")
```

**Recovery when patch corrupted multiple lines / the whole file:**
Switch to Python bytes-level replacement via terminal — the patch tool's fuzzy logic is the problem, so bypass it:
```python
with open('file.js', 'r', encoding='utf-8') as f:
    content = f.read()

# Scrub the corruption: collapse 8-space indent back to 4-space, etc.
broken = """    _default: `...`;
      }
    };"""
fixed = """    _default: `...`;
  }
};"""
content = content.replace(broken, fixed)

with open('file.js', 'w', encoding='utf-8') as f:
    f.write(content)
```

Then run `node --check file.js` to confirm the syntax is valid before continuing.

**Prevention:**
- Always `node --check` the patched file IMMEDIATELY after every multi-line patch, especially when `old_string` ends mid-block (in the middle of a `},` chain) and `new_string` closes the block
- For multi-line replacements, ensure the leading whitespace of the FIRST line matches what's in the file exactly. Use `read_file` to get the precise content rather than guessing indentation
- Prefer patching complete syntactic units (entire property + comma + newline + closing brace) rather than mid-block. If you must patch mid-block, include at least 2 lines of context above and below to anchor the match
- If the file ends up corrupted anyway, drop to Python bytes-level replacement and don't try to fix it with more patches

### `patch()` old_string too broad — landmark-based deletion eats adjacent content

**Symptom**: Patch reports success and the diff shows the intended change, but a separate, unintended section of the file has also vanished. Often discovered when unrelated UI breaks ("where did `.sbprompt-footer` go?").

**Cause**: When you write `old_string = "everything between landmark A and landmark B"`, the matcher finds A and B in the file and deletes EVERYTHING between them — including intervening content you didn't intend to touch. The diff still looks correct because both A and B are visible, but the middle was collateral damage.

**Real example**: deleting a 4-line CSS block accidentally removed a 2-line `.sbprompt-footer` rule that sat between the deleted block and the next landmark, because `old_string` ran from "start of block to delete" all the way to "start of next block".

**Fix — minimum boundary**:

```javascript
// old_string should end EXACTLY at what you want deleted — no trailing context.
// If landmark B is your "next unrelated block", put the END of old_string BEFORE landmark B, not at B.
// Better: include ONE LINE of the next block as context, never the entire next block.
```

**Recovery**: Re-add the deleted block as a follow-up patch. If you don't have a backup, grep your own diff output — the patch tool returns the diff that includes the now-deleted content, so you can copy it back.

**Prevention**:
- Match the LITERAL bytes you want deleted, with minimum surrounding context (1-3 lines above and below).
- For deletion patches, the END of `old_string` should be the literal end of the content you're removing — not the start of the next block.
- If using landmarks, include only ONE line of the next block as context, never the entire next block.

## ENHANCEMENTS Wrapper Pattern — additive UI for single-file SPAs

**Pattern**: Some single-file SPAs (especially canvas/graph apps with many node types) are structured so the base `renderNode()` function is wrapped by an "ENHANCEMENTS" IIFE that runs AFTER the base render. The wrapper calls `origRender(nd)` then injects extra UI per node type.

```javascript
// File structure (Kairos Canvas pattern):
var origRender = renderNode;
renderNode = function(nd) {
  origRender(nd);                          // base render — never modify this
  var el = document.getElementById(nd.id);
  if (!el) return;
  var nbd = el.querySelector(".nbd");
  // Per-type additions: overwrite nbd.innerHTML or append elements
  if (nd.type === "character") {
    nbd.innerHTML = '<div class="char-fields">...new fields + refs + buttons...</div>';
    // Bind events for the new elements
  }
};
```

**When to use this pattern**:
- Adding new fields, refs, or controls to an existing node type WITHOUT changing the base behavior
- Adding a new feature (e.g., "mask port", "face ref upload") that overlays on the existing render
- When the base render is too tangled to safely extend inline (giant string concatenation)

**Pitfalls**:
1. **Overwriting nbd.innerHTML WIPES the base render** — anything the base renderNode set inside `nbd` is lost. You lose the base textarea, footer, port attributes, etc. Only do this when you fully replace the node body.
2. **To PRESERVE base content + add to it**, append/prepend to nbd or insert siblings, don't replace innerHTML.
3. **The wrapper's element refs go stale after re-render** — bind events INSIDE the wrapper function (it runs on every render) rather than once at startup.
4. **Node body's 4 default ports (pt/pb/pl/pr) are rendered by the BASE renderNode** — if you overwrite nbd.innerHTML, you lose the ports unless you re-add them. Don't overwrite if you need ports.
5. **Re-rendering via `origRender(nd)` after state change** is the pattern for showing new UI. Call it from event handlers after mutating nd.meta.

**Reference**: `references/kairos-canvas-architecture.md` — full anatomy of one such codebase (Kairos Canvas / GaiaNetworkTester). Also covers: node type registry, renderNode + ENHANCEMENTS wrapper, floating panels, CSS conventions, S config object, server/launch, common gotchas (browser tools timing out on localhost, CRLF line endings, etc.).

## Common Pitfalls (continued)

### antd 5.x: static `message.xxx()` triggers warning — wrap app in `<App>` + use `App.useApp()`

**Symptom**: every static `message.success(...)`, `message.error(...)`, `message.warning(...)` call triggers a console warning:

```
Warning: [antd: message] Static function can not consume context like
dynamic theme. Please use `App` component instead.
    at ManualVideo.tsx:75
```

No visible breakage — the toast still appears, the action still runs — but the console fills with one warning per call, and the warning never goes away.

**Real failure trail from AIGC_agent (2026-08-09)**: User opened the manual video page and saw **only** two console messages — the favicon 404 and this antd warning. Because the console "looked broken" (warnings every click), the user assumed the actual generate flow was also broken. Real bug was a missing `save_video()` call elsewhere; the console noise just made it harder to see.

**Root cause**: antd 5.x added a `<App>` component that provides theme/locale context to message/notification/modal. Static API functions (`message.xxx(...)`) are imported directly from the antd package and **cannot** read this context — they always emit the warning. The recommended pattern is to use the hook-based instance returned by `App.useApp()`.

**Two-part fix**:

1. **main.tsx** — wrap the app tree in `<App>` inside the existing `<ConfigProvider>`:

   ```tsx
   import { App as AntApp, ConfigProvider, theme } from 'antd';

   <ConfigProvider locale={zhCN} theme={{...}}>
     <AntApp message={{ maxCount: 3 }}>     {/* ← NEW */}
       <BrowserRouter>
         <App />
       </BrowserRouter>
     </AntApp>
   </ConfigProvider>
   ```

2. **Each page** — replace `import { message }` with `App as AntApp`, then use the hook:

   ```tsx
   import { App as AntApp } from 'antd';

   const ManualVideoPage: React.FC = () => {
     const { message } = AntApp.useApp();   // ← hook-based instance
     // ... use message.success/error/warning as before
   };
   ```

   The hook returns an instance that reads context from the nearest `<App>` ancestor, so no warnings, and theme/locale tokens actually apply.

**Multi-page rollout**: when multiple page files use static `message.xxx`, fix all of them in the same patch — leaving one page on the static API re-introduces the warning for every action on that page. Audit with:

```bash
grep -rn "from 'antd'" web/src/pages/ | grep -E "(^|, )message(,| )"   # legacy static import
grep -rn "App as AntApp" web/src/pages/                                 # new pattern
```

**Verification**:
- Hard-refresh the browser (`Ctrl+Shift+R`)
- Trigger a toast (e.g. upload an image → `message.success('参考图已上传')`)
- Console: zero `[antd: message]` warnings. The toast still renders.

**Related antd 5.x traps**:
- `notification.xxx()` and `Modal.xxx()` static calls have the same warning — same fix (use the hook instance)
- `App.useApp()` also returns `{ notification, modal }` — destructure all three if your page uses any of them

### Template-literal backtick escaping in JS article data

**Scenario**: An HTML file has a `<script>` block where article content is stored as template literals (backticks). Inside one such template literal, the content itself contains backticks (`` ` ``) used as markdown formatting for code samples.

**Symptom**: The browser shows a blank page with `Uncaught SyntaxError: Unexpected identifier` in the console. The JavaScript engine can't parse the first `<script>` block at all — none of the data variables (`A_EXPANDED`, etc.) are defined.

**Root cause**: Inside a template literal (`` `...` ``), a raw backtick character **closes the template literal**. It's not escaped. The text after the backtick is parsed as JavaScript code, not as string content. Example:

```javascript
// ❌ Broken — backtick inside template literal:
b: `例如：`a cat sitting on a windowsill, cinematic lighting``

// ✅ Fixed — escape backticks with backslash:
b: `例如：\`a cat sitting on a windowsill, cinematic lighting\``
```

**Diagnosis**: Check the first `<script>` block for syntax validity:
```bash
node -e "
const fs=require('fs');
const c=fs.readFileSync('file.html','utf-8');
const start=c.indexOf('<script>')+8;
const end=c.indexOf('</script>',start);
const s=c.substring(start,end);
try { new Function(s); console.log('OK'); }
catch(e) { console.log('ERROR:', e.message); }
"
```

If `Unexpected identifier` is followed by a word that looks like it came from article content (e.g., `a` from `` `a cat...` ``), the template literal was broken by an unescaped backtick.

**Binary search for the broken backtick**: Same technique as the JS error binary search — locate which template literal contains the unescaped backtick by checking the line number from the error message.

**Prevention**: When writing template literal content that contains backtick characters, always escape them as `\`` (backslash + backtick). Common culprits:
- Code/prompt examples wrapped in backticks: ``\`prompt example\```
- Markdown-style inline code
- English text that happens to contain backtick-like characters

### Status stuck at initial UI text ("loading..." / "初始化..." never advances) = inline `<script>` syntax failure

**Symptom**: User opens the page and only the title + chrome render. A status text in the corner or a placeholder overlay shows `加载引擎...` / `初始化...` / `Loading...` forever. The actual app content (canvas, scene, controls, event handlers) never appears. **DevTools console may show nothing at all** — or only unrelated warnings that the user already filtered out.

**Common misdiagnosis**: Developer assumes async loading is slow (CDN script, font, asset). They poll. They wait. They add debug `console.log` calls and try/catch wrappers. Nothing helps.

**Real root cause**: An inline `<script>` block has a syntax error. The browser refuses to execute the rest of the script — no partial execution, no recovery. Event handlers never get attached, scene objects never initialize. **The HTML body still renders** (status text, title, buttons) because that's HTML, not JS — the user sees "the page loads but nothing works."

**Why no console error is visible**:
- Some browsers DO log the syntax error in DevTools — but it's a confusing "Unexpected identifier 'X'" pointing to a token that's actually far away from the bug
- Headless browsers / playwright snapshots often don't capture console errors
- Users may not have DevTools open
- The error gets buried under favicon 404s and other harmless warnings

**The 30-second diagnostic that ALWAYS works** — extract the inline script and run `node --check`:

```bash
# 1. Extract the inline script to a temp file
python3 -c \"
import re
with open('app.html', 'r', encoding='utf-8') as f:
    html = f.read()
# Adjust the regex if the script tag has attributes
m = re.search(r'<script>(.*?)</script>', html, re.DOTALL)
if m:
    with open('C:/Users/leohu/_check.js', 'w', encoding='utf-8') as f:
        f.write(m.group(1))
    print('Extracted, line count:', m.group(1).count(chr(10)))
else:
    print('NO <script> block found')
\"
# 2. Run Node syntax check (Windows path — Linux: /tmp/_check.js)
node --check C:/Users/leohu/_check.js
```

`node --check` gives the EXACT line number and a clear error. The browser's confusing error message becomes unambiguous. Always run this after ANY edit to inline `<script>` content — add it to your edit-then-verify ritual.

**Top 5 causes (in frequency order)**:

1. **Backticks inside a template literal describing markdown** — `` `不要 \`\`\`json 或 \`\`\` 包裹` ``. The inner backticks terminate the outer template literal at the first one. See "Documentation strings in template literals" note in the template-literal pitfall above.
2. **Missing closing brace / paren from earlier failed `patch()`** — file is syntactically invalid past that point.
3. **Optional chaining in unsupported contexts** — older parsers may reject `x?.['key']` even though modern Node accepts it. Use explicit intermediate variables: `const m = obj.match(...); const v = m ? m[1] : 0;`
4. **Hyphenated object keys without quotes** — `{ human-m: 'M' }` is invalid; must be `{ 'human-m': 'M' }`.
5. **Stray comma / semicolon** from a JS string concatenation delete — see "JS string concatenation delete trap" above.

**Real failure trail (storyboard-previs, Aug 2026)**: A SYSTEM_PROMPT template literal contained `不要 \`\`\`json 或 \`\`\` 包裹` describing markdown code fences. The inner backticks closed the template at the first inner one. The rest of the file was parsed as broken JS. The browser refused to execute anything. User saw "初始化..." stuck forever. Took ~10 minutes to diagnose via `node --check` on the extracted inline script.

**Lesson to encode**: When the UI is stuck at initial state and async loading seems slow, **first** verify the inline `<script>` syntax with `node --check` BEFORE investigating the async chain. Saves 5-15 minutes per occurrence.

### Vendor (local) libraries > CDN for `file://` single-file apps

**Symptom**: Your `index.html` uses `<script type="importmap">` + `<script type="module" src="...">` to load a library (Three.js, D3, etc.) from a CDN like `unpkg.com` or `jsdelivr.net`. Works on `http://localhost`, but on `file://` (double-click to open) the script either:
- Loads but `import * as THREE from 'three'` returns undefined
- Never loads at all (no console error, status stuck at "loading")
- Loads from cache but the import resolution fails silently

**Real failure trail (storyboard-previs, Aug 2026)**: Tried `importmap` → `unpkg.com/three.module.js` → ES module import. Worked in browser dev server, silently failed when user opened via `file://`. Switching to local UMD via `<script src="./vendor/three.min.js"></script>` + `const THREE = window.THREE;` worked everywhere instantly.

**Root cause**: ES modules under `file://` are restricted by browser security policy (the script tag and import resolution require CORS-equivalent headers that `file://` URLs don't carry reliably). Even with importmap, the underlying module fetch may fail.

**Fix — bundle the library as a local UMD file**:

```bash
# One-time download
mkdir -p vendor/
curl -fsSL -o vendor/three.min.js https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.min.js
```

```html
<!-- Replace this: -->
<script type="importmap">{ "imports": { "three": "https://unpkg.com/three@0.160.0/build/three.module.js" } }</script>
<script type="module">import * as THREE from 'three';</script>

<!-- With this: -->
<script src="./vendor/three.min.js"></script>
<script>const THREE = window.THREE;</script>
```

**Why UMD over ESM for single-file apps**:
- Plain `<script src>` works under both `file://` and `http://`
- No importmap needed
- No CORS / MIME type issues
- Vendor file is downloaded once, then fully offline

**Trade-off**: UMD bundle is slightly larger than ESM (e.g. Three.js UMD 655KB vs ESM 1.3MB but ESM includes more dependencies that may also fail to load). For single-file apps, the convenience wins.

**When to use ESM anyway**: When the app is served via HTTPS from a real origin, ESM + importmap + CDN works fine and gives you smaller bundles + tree-shaking. Only switch to vendor UMD when you need `file://` support.

### API style auto-detection (OpenAI Chat Completions vs Anthropic Messages)

Many LLM providers expose OpenAI-compatible endpoints (`/v1/chat/completions`), but a growing number (Anthropic-direct, MiniMax, Microsoft Foundry, Azure Anthropic-style) use the **Anthropic Messages API** format (`/anthropic/v1/messages` or `/v1/messages`). They have DIFFERENT request body shapes, DIFFERENT auth headers, and DIFFERENT response parsing. Hardcoding for one breaks the other.

**Auto-detection pattern** that handles both:

```javascript
const isAnthropic = /\/anthropic/i.test(baseUrl) || /anthropic\.com/i.test(baseUrl);

// URL construction
const url = isAnthropic ? `${baseUrl}/messages` : `${baseUrl}/chat/completions`;

// Auth headers
const headers = { 'Content-Type': 'application/json' };
if (isAnthropic) {
  headers['x-api-key'] = apiKey;
  headers['anthropic-version'] = '2023-06-01';
} else {
  headers['Authorization'] = `Bearer ${apiKey}`;
}

// Request body
let body;
if (isAnthropic) {
  // Anthropic Messages API has no 'system' role in messages — concatenate system prompt
  // into the first user message
  body = JSON.stringify({
    model, max_tokens: 4096,
    messages: [{ role: 'user', content: `${systemPrompt}\n\n${userText}` }]
  });
} else {
  body = JSON.stringify({
    model,
    messages: [
      { role: 'system', content: systemPrompt },
      { role: 'user', content: userText }
    ]
  });
}

// Response extraction
const data = await resp.json();
let content;
if (isAnthropic) {
  content = data.content?.map(c => c.text || '').join('') || '';
} else {
  content = data.choices?.[0]?.message?.content || '';
}
```

**When auto-detection is NOT enough** (per-provider quirks):
- Kimi `kimi.com/coding` requires `User-Agent: claude-code/0.1.0` — rejected with 403 otherwise
- Some OpenAI-compatible providers reject `response_format: { type: 'json_object' }` — omit and rely on prompt engineering
- Anthropic rejects `fine-grained-tool-streaming` and `context-1m` betas — strip these headers for non-Anthropic providers
- Status codes vary (some providers return 400 for `max_tokens: undefined`, others accept any value)

For these, expose per-provider config in the UI: model name + base URL + extra headers.

### CORS proxy for browser-to-cloud LLM (mini FastAPI template)

Cloud LLM APIs (MiniMax, OpenAI, Anthropic, Azure) typically don't expose CORS headers for browser requests. The browser blocks the fetch with `TypeError: Failed to fetch` even when the request would succeed from a server. Curl works, browser doesn't.

**Mini FastAPI proxy template** (10 lines of code, copies headers through):

```python
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
import httpx

app = FastAPI()
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

UPSTREAM = "https://target.example.com/anthropic/v1/messages"

@app.api_route("/anthropic/v1/messages", methods=["POST", "OPTIONS"])
async def proxy(request: Request):
    if request.method == "OPTIONS":
        return Response(status_code=204)
    body = await request.body()
    fwd = {k: v for k, v in request.headers.items() if k.lower() != "host"}
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(UPSTREAM, content=body, headers=fwd)
    return Response(
        content=r.content, status_code=r.status_code,
        media_type=r.headers.get("content-type", "application/json"),
        headers={"Access-Control-Allow-Origin": "*"},
    )

if __name__ == "__main__":
    import uvicorn
    print(f"Proxy: localhost:8765 -> {UPSTREAM}")
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")
```

**Frontend** changes `base_url` to `http://localhost:8765/anthropic`. Browser requests localhost (no CORS check) → proxy forwards to cloud with full auth headers intact.

**Verify the proxy with curl before pointing the browser at it**:
```bash
# Should return 401 (real endpoint) or 200 (real key), never "Failed to fetch"
curl -s -m 8 http://localhost:8765/anthropic/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: test" \
  -H "anthropic-version: 2023-06-01" \
  -d '{"model":"test","max_tokens":5,"messages":[{"role":"user","content":"hi"}]}'
```

**Why a proxy, not a CORS extension or browser flag**:
- Browser CORS flags (`--disable-web-security`) break other things and only work in dev
- RequestBin-type services leak your API key and add latency
- A local proxy is 10 lines, runs in your existing Python env, and keeps keys off third-party servers

### Robust JSON extraction from LLM output

LLM outputs JSON in various shapes; blindly `JSON.parse()` fails on most of them. Common corruption modes (in frequency order):

1. Wrapped in markdown code blocks (```json ... ```)
2. Preceded/followed by explanatory text ("Here is the JSON:\n...")
3. Truncated mid-object (max_tokens hit)
4. Contains unescaped quotes in string values (e.g. `said "hi"`)
5. Trailing commas (LLM trained on JS as well as JSON)
6. Double object — LLM writes JSON, then writes a second corrected one after

**Brace-matching extractor** that handles #1, #2, #3, #6 (the structural ones):

```javascript
function extractJSON(text) {
  // Strip markdown fences first
  let s = text.replace(/^```(?:json)?\s*\n?/gm, '').replace(/```\s*$/gm, '');
  const start = s.indexOf('{');
  if (start === -1) return null;
  // Walk forward, respecting string boundaries (so { inside "..." doesn't break)
  let d = 0, inStr = false, esc = false;
  for (let i = start; i < s.length; i++) {
    const c = s[i];
    if (esc) { esc = false; continue; }
    if (c === '\\') { esc = true; continue; }
    if (inStr) { if (c === '"') inStr = false; continue; }
    if (c === '"') inStr = true;
    else if (c === '{') d++;
    else if (c === '}') { if (--d === 0) return s.slice(start, i + 1); }
  }
  return null;  // never found balanced close
}

// Two-pass parse: raw, then sanitize trailing commas
const json = extractJSON(content);
if (!json) throw new Error('No JSON object found in LLM output');
try { return JSON.parse(json); }
catch (e) {
  const cleaned = json.replace(/,(\s*[}\]])/g, '$1');
  try { return JSON.parse(cleaned); }
  catch (e2) {
    // Show position context so user can locate the corruption
    const posMatch = e.message.match(/position (\d+)/);
    const pos = posMatch ? parseInt(posMatch[1]) : 0;
    throw new Error(`${e.message}\nContext: ...${json.slice(Math.max(0, pos - 60), pos + 60)}...`);
  }
}
```

**Coverage**: 7/8 common corruption modes. The only unfixable one is unescaped quotes inside string values (case #4) — that's the LLM's job to fix, not yours.

**Test cases** that prove each corruption mode is handled — paste these into a unit test:
```javascript
const cases = [
  ['normal', '{"a":1}'],
  ['markdown wrapped', '```json\n{"a":1}\n```'],
  ['text prefix/suffix', 'Here is the JSON:\n{"a":1}\nDone!'],
  ['nested objects', '{"segs":[{"shots":[{"duration":4}]}]}'],
  ['braces inside strings', '{"text":"with {curly} braces","a":1}'],
  ['unterminated', '{"a":1'],  // should throw with helpful message
  ['trailing comma', '{"a":1,"b":2,}'],  // sanitized on second try
  ['double object', '{"first":1} {"second":2}'],  // first wins
];
```

**Strengthen the prompt** to reduce corruption in the first place:
```
输出格式（严格遵守）：
- 只输出 JSON 对象本身，**不要任何** markdown 代码块（不要三反引号 + json + 三反引号 这种包裹）
- **不要** 在 JSON 前后加任何解释、注释、前缀、后缀
- 字段值含中文/英文/数字允许，**不要** 在字段值里写未转义的换行（用 \\n 转义）
- 字符串里的引号必须转义为 \"
- 输出尾部不要加句号、感叹号等标点
```

**Documentation strings in template literals — describe the syntax, don't render it.** When documenting the LLM instructions, prompt templates, or any string that mentions markdown code fences (` ```json ``` `), escape them or describe them in prose. Writing `` `不要 \`\`\`json 或 \`\`\` 包裹` `` inside another template literal closes the outer string at the first inner backtick and the rest of the file is parsed as broken JS — the entire `<script>` block dies silently. Use plain text descriptions ("不要 三反引号 + json + 三反引号 这种包裹") or escape with `\``. See the "Status stuck at initial UI text" pitfall below for the user-visible failure pattern.

### `patch()` partial-line consumption — old_string too short eats adjacent text

**Symptom**: Patch reports success (`"diff"` shows changes), but the file has a syntax error. The broken line appears truncated — e.g. `if(savedMode!==` where the original was `if(savedMode!=="story"){`.

**Cause**: The `patch` tool uses fuzzy matching. If your `old_string` ends mid-line (e.g. `if(savedMode!==` ), the matcher searches for `if(savedMode!==` in the file. It finds `if(savedMode!=="story"){` and treats `"story"){` as **consumed by the match** (the matcher reads past the anchor to confirm uniqueness). Your `new_string` only contains `if(savedMode!==`, so `"story"){` is dropped from the output.

**Prevention**: Always include complete lines (or enough trailing context to anchor the match without ambiguity) in `old_string`. When replacing a block, end your `old_string` with text that closes syntactically — e.g. `if(savedMode!=="story"){` not `if(savedMode!==`.

**Fix when already broken**: Add a micro-patch that restores the missing suffix. If the broken snippet appears multiple times (common after the same mistake in both files of a two-file sync), include a unique context line in `old_string` to disambiguate.

### Unescaped double quotes in HTML attribute values silently truncate the attribute

**Symptom:** You add a "copy prompt" button whose `data-prompt="..."` attribute contains user content with double quotes inside (e.g. `Character name "亚莉克丝"`). In Python you read the file and see the full attribute value (2,500+ chars). In the browser, `getAttribute('data-prompt')` returns only 650 chars — truncated mid-word. Click the button, paste, get a half-prompt.

**Why this happens:** HTML attribute values use `"` as the delimiter. A literal `"` inside the value terminates the attribute prematurely. Browsers are lenient about whitespace and odd chars in attribute values, but they are NOT lenient about unescaped `"` — it just ends the attribute. The browser then tries to parse everything after as more attributes, and the next `>` closes the tag, so the value gets cut off at the first inner `"`.

**Real failure trail:**

```html
<!-- You wrote this: -->
<button data-prompt="...Character name "亚莉克丝" displayed prominently...">📋</button>

<!-- The browser sees this (effectively): -->
<button data-prompt="...Character name "                 <!-- attribute ends here -->
亚莉克丝                       displayed prominently...        <!-- orphan text -->
">📋</button>
```

**Diagnosis — always measure `getAttribute` length, not source length:**

```javascript
const btn = document.querySelector('button.copy-btn');
console.log('source length:', document.documentElement.outerHTML.match(/data-prompt="[^"]*"/)?.[0].length);
console.log('browser reads:', btn.getAttribute('data-prompt').length);
// These two should match. If browser reads less, the attribute was truncated.
```

**Fix — never store user content in HTML attributes. Use hidden DOM elements:**

```html
<!-- The prompt goes in a hidden element, NOT in an attribute: -->
<pre class="prompt-source" hidden>{prompt_with_quotes}</pre>

<button class="copy-btn" onclick="copyPrompt(this, event)">📋 复制</button>

<pre class="prompt-block">{prompt}</pre>  <!-- visible copy for reading -->
```

```javascript
function copyPrompt(btn, e) {
  e.preventDefault();
  e.stopPropagation();
  // Read from the hidden element, NOT from btn.dataset or btn.getAttribute
  const card = btn.closest('.asset-card');
  const source = card.querySelector('.prompt-source');
  const text = source.textContent;   // full content, no truncation
  navigator.clipboard.writeText(text).then(...);
}
```

The `<pre>` element's text content is not subject to HTML attribute parsing — `Character name "亚莉克丝"` inside `<pre>...</pre>` is read by the browser exactly as written, quotes included.

**Why this matters for "copy to clipboard" features:** any time you're building a "copy this long text" button where the source text is generated dynamically (prompt templates, code snippets, AI responses), do NOT put the text in `data-...` attributes. Use a hidden DOM node (`<pre hidden>`, `<template>`, or even a `data-*` attribute on a *meta tag* read via `JSON.parse` rather than `getAttribute`). Test with a payload that includes `"`, `'`, `<`, `>`, `\n` — all common content chars that survive in `<pre>` but break attribute parsing.

**Scenario**: A template like `<button onclick="handler('${escHTML(content)}')">` reads fine at a glance, but the copy button (or any onclick handler) **silently does nothing** when clicked.

**Symptom**: No console error is visible on initial page load. The error (`Uncaught SyntaxError: Unexpected identifier`) only fires **when the button is clicked**. Use `browser_console()` after clicking to see it.

**Root cause**: `escHTML()` escapes `'` → `&#39;` for HTML text safety, but the HTML parser **resolves `&#39;` back to `'`** before handing the attribute value to the JavaScript engine. The result is a broken JS string literal:

```
// HTML entity resolved by parser:
handler('It's a test')  // ← SyntaxError: ' terminated string at the apostrophe
```

**Fix**: Use a JS-safe escape function (`escJS`) instead of `escHTML` for any content embedded in onclick JS string literals. See `references/escHTML-vs-escJS-onclick.md` for the full trace, the `escJS()` implementation, and the alternative data-attribute approach.

**Quick reference** — use `escJS` where you'd use `escHTML` in onclick handlers:

```javascript
// ❌ Broken
onclick="views.detail.copyPrompt('${escHTML(p.content)}')"
// ✅ Fixed
onclick="views.detail.copyPrompt('${escJS(p.content)}')"
```

### `\\\\uXXXX` unicode escape anchor not found

**Symptom**: `patch()` returns "Could not find a match" for a string you can see in the file — typically Chinese or other non-ASCII text in JS string literals.

**Cause**: The JS file stores the text as `\u573A\u666F` (unicode escapes) rather than the actual characters `场景`. The `patch` tool searches raw file bytes and can't match the Chinese glyphs against backslash-u sequences.

**Fix**: See `references/unicode-escape-search.md` for the complete technique.

## User Preferences (absorbed from targeted-file-editing)

- **CSS style**: Compact single-line rules (`selector { prop: val; prop: val; }`) preferred over expanded multi-line.
- **Changes**: Minimal and focused — only touch what the user asked about. Do not reformat or restructure surrounding code.
- **Form layout**: Flat single-page forms preferred over tabbed multi-step forms. All fields (including long textareas) should be on one page, not hidden behind tabs. E.g., when adding a script textarea, place it directly below the synopsis, not on a separate "Script" tab.
- **Don't modify existing functional node types' display/styling**: When adding a new feature, do NOT touch the visual/styling of existing functional node types unless explicitly asked. Extend via the ENHANCEMENTS wrapper (see below) or add NEW node types. The user will reject retroactive styling changes to working nodes ("剧本节点不做修改，回滚回来").
- **Buttons must visually sink to the bottom of flexible-height nodes**: If a node body has variable content above the buttons, apply flex column to the body container and `margin-top:auto` to the button row. Don't leave buttons inline-floating.
- **User "I rolled it back" means: work with what's there, not what you remember**: When the user says "我自己回滚了画布" (I rolled back the canvas myself) or similar, they reverted the file to a known-good baseline and want your next change to apply to that version, NOT the version that has your prior work in it. Don't re-add features you think should be there — start from the current state and apply only what's asked. Re-read the file to confirm what's actually present before patching.
- **Main view = the Product, not the Tool** (severe, repeat-offender): The user will repeatedly correct you if you make a tool (calendar, config form, settings panel) the main view of a tool-app. The main view should be the thing the app *produces* — stats grid, generated output, content the user came here for. Tools (calendar grid, settings forms, file management) go in modals, drawers, or behind a clear button. Promote a tool to main view only when explicitly told to. Verify before making a tool the page's primary content. See `ui-ux-pro-max` "Tool vs. Product Hierarchy" for full rationale.
- **Verify rendered UI before declaring it works**: When you write or overhaul UI, don't trust user reports that it "looks messy" — open the page via Playwright (the `playwright` module ships under `C:\Users\leohu\AppData\Local\hermes\node\node_modules\playwright`), screenshot it, and inspect what actually rendered. A `vision_analyze` of the screenshot catches layout/wrap/overflow/truncation bugs that textual descriptions miss. For server-returned JSON, also parse it with Python first to check the structure before debugging the JS layer that consumes it.
- **Tool-app main page = the Product, not the Tool**: When refactoring a "工具型 SPA" (attendance, scheduling, inventory, ERP), the main page is **stats + primary data table + result files**. Configuration / tools / uploads / sub-forms live in modals or a sticky side panel. Never make a calendar / schedule grid / settings form the main view — the user will reject it. See `references/business-output-dashboard-pattern.md` for the canonical dashboard layout (1 main card + 4 alert cards + sticky sidebar + settings dropdown) and patch-tool pitfalls when rewriting nested HTML blocks.
- **Ship code first, narrate after**: When the user asks for a non-trivial edit/feature, START THE WORK immediately (backup → patch → verify → run). Do NOT write a multi-step "I'll do A then B then C" plan and stop before touching the file — the user reads that as "you did nothing." A user reply like "你这是啥也没改" / "加好了吗?" / "等下你做了吗?" means the prior turn described intent without applying it. The fix is to do the work in the same turn, or split across turns with each turn producing real file output. If you must explain a multi-step plan first, do it in ≤3 lines and then immediately patch in the same response.

## Server Restart Note

If the project uses a static file server (Node.js, Python http.server), changes to HTML/JS/CSS take effect on the next HTTP request — **no server restart needed for static files**. Only restart the server if you changed the server code itself (e.g., server.js routing). However, if the server has in-memory caching, a restart may be required.

---

## Absorbed Skills

### Browser LLM Integration (from `browser-llm-integration`)
Add OpenAI-compatible LLM API calls to vanilla HTML/JS browser apps. Key patterns: URL path construction (handle /v1, /chat/completions variants), max_tokens conditional inclusion + retry on 400, paste event interception for canvas apps, settings panel pattern (separate localStorage key), flexbox node layout (textarea + footer), response node overwrite pattern (canvas UIs), error display in node UI, file upload in LLM nodes (.txt/.md/.docx/.pdf with mammoth/pdf.js CDN loaders), markdown response rendering (placeholder protection: extract tables/code blocks FIRST, format text, restore placeholders LAST), table parser handling multiple markdown formats, model name label, status classes (.busy/.done/.err), "OpenAI-Compatible ≠ Standard Format" critical pitfall, Flask backend proxy pattern for API key security, async media generation pattern (upload→submit→poll→download), startup ordering (load config before rendering), IIFE rendering trap (call rn() directly, don't duplicate DOM construction), `.ndb overflow:hidden` clipping, OpenAI-compatible provider variation (request format, image handling, polling paths, response nesting, status values — always read actual API docs), **API style auto-detection (OpenAI Chat Completions vs Anthropic Messages — see "API style auto-detection" pitfall below for full recipe with x-api-key headers and anthropic-version)**, **CORS proxy for browser-to-cloud LLM (see "CORS proxy for browser-to-cloud LLM" pitfall below for the FastAPI 10-line template — solves "Failed to fetch" when calling MiniMax / Anthropic / etc. directly from browser)**, **robust JSON parsing from LLM output (see "Robust JSON extraction from LLM output" pitfall below — brace-matching extractor that handles markdown fences, text prefix/suffix, nested objects, and trailing commas)**, **vendor local libraries instead of CDN for file:// apps (see "Vendor libraries > CDN for file:// single-file apps" pitfall below — importmap + ESM silently fails on file://, switch to plain `<script src="./vendor/lib.min.js">` for universal support)**.

### AI Media Generation UI (from `ai-media-gen-ui`)
Build frontend UIs and backend proxies for AI image/video generation tools. Key patterns: 4-mode pattern (txt2img/img2img/txt2vid/img2vid), client-side async polling architecture (server split: POST returns jobId, GET /status polls), canvas node async pitfall (POST returns jobId but no outputUrl → gaiaImgUrl helper returns null → error), connection chain depth (prefer iterative BFS over nested find+some), per-node style in canvas workflows (save to nd.meta.style, trace through connection chain), GaiaVideoFactory multipart form for reference images (全能参考模式 requires input_reference[] not JSON images[]), toolbar icon/text alignment (fixed width + text-align:center for emoji icons), model selection persistence (localStorage), model-aware size computation (min pixel requirements per model), video reference image modes (全能参考 vs 首尾帧), audio upload + @mention pattern, user preferences (clean light design #f0f2f5, no dark themes, simple rounded corners).

---

## End of Absorbed Skills

### Targeted File Editing
Content absorbed from `targeted-file-editing` — the core principle (never use write_file, always use patch), CSS Grid overflow-hidden collapse diagnostic, JS/CSS class name mismatch checks, parent container display:none debugging, and user preference conventions are now integrated throughout this skill.
