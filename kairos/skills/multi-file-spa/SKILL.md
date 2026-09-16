---
name: "multi-file-spa"
description: ">-"
priority: 0.5
imported-from: "hermes"
source-path: "hermes/skills/.archive/multi-file-spa/SKILL.md"
---
# Multi-File Vanilla SPA Development

Scaffold and develop multi-file vanilla HTML/CSS/JS single-page applications. Use when building a new client-side web app from scratch. For **editing existing single-file apps**, use `frontend-patching` instead.

## Trigger Conditions

- User wants to build a new web app/site from scratch
- Client-side app with no backend (IndexedDB/localStorage)
- Multi-file SPA (index.html + css/ + js/)
- Needs hash-based routing between views
- Dark theme, responsive design, bilingual support

## Project Structure

```
project-root/
  index.html          # Single entry point
  css/
    style.css         # All styles (dark theme, responsive)
  js/
    i18n.js           # Translation dictionaries + toggle
    store.js          # IndexedDB wrapper class
    router.js         # Hash-based SPA router
    app.js            # Initialization + toast
    views/
      home.js         # Landing page
      browse.js       # Listing/filter
      detail.js       # Item detail + actions
      upload.js       # Form + file upload
```

## Step-by-Step Scaffold

### 1. Create directory structure

```bash
mkdir -p ~/path/to/project/{css,js/views,assets/{images,videos}}
```

### 2. HTML shell (index.html)

- Semantic HTML5 with `<header>`, `<main id="app">`, `<footer>`
- CSS import via `<link>`
- JS imports in order: i18n → store → router → views → app
- `data-i18n` attributes on translatable elements
- Language toggle button in header

### 3. CSS (style.css)

Use CSS custom properties for theming:

```css
:root {
  --bg: #0a0a0f;
  --bg-card: #12121a;
  --bg-card-hover: #1a1a26;
  --border: #2a2a3a;
  --text: #e8e8f0;
  --text-muted: #8888a0;
  --accent: #6c5ce7;
  --accent-light: #a29bfe;
  --accent-glow: rgba(108, 92, 231, 0.3);
  --radius: 12px;
  --font: 'Inter', 'Noto Sans SC', sans-serif;
}
```

Key patterns:
- Card grid: `grid-template-columns: repeat(auto-fill, minmax(340px, 1fr))`
- Sticky header with backdrop blur
- Scrollbar styling for content areas
- Responsive breakpoints at 768px

### 4. i18n System (i18n.js)

Dictionary pattern with fallback:

```javascript
const I18N = {
  zh: { key: '中文文本', ... },
  en: { key: 'English text', ... }
};

function t(key, params = {}) {
  const str = I18N[currentLang][key] || key;
  return Object.keys(params).reduce((s, k) => s.replace(`{${k}}`, params[k]), str);
}
```

- `data-i18n` attributes auto-translated via `applyTranslations()`
- Language toggle switches `currentLang` and re-renders

**⚠️ CRITICAL — language toggle must trigger full view re-render, not just update `data-i18n` attributes:**

Most views hardcode translations into the rendered HTML at render time:
```javascript
app.innerHTML = `<h1>${t('heroTitle')} <span>${t('heroTitleAccent')}</span></h1>`;
```

If `setLang` only updates `[data-i18n]` attributes, the `t('heroTitle')` text inside the HTML is already baked in at the previous render — the page won't actually change language.

**Fix — `setLang` triggers `hashchange` to re-render the current view (router.js already listens to hashchange):**
```javascript
function setLang(lang) {
  currentLang = lang;
  localStorage.setItem('sf_lang', lang);
  document.documentElement.lang = lang;
  // Update data-i18n elements (for static labels)
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.dataset.i18n;
    if (I18N[lang] && I18N[lang][key]) el.textContent = I18N[lang][key];
  });
  // Trigger full re-render of the current view (router.js dispatches on hashchange)
  if (location.hash) {
    window.dispatchEvent(new HashChangeEvent('hashchange'));
  } else {
    window.location.hash = '#/';
  }
}
```

**Why `dispatchEvent(new HashChangeEvent(...))` over calling `window.dispatch({...})`:**
- `window.dispatch` is NOT a DOM standard method (the standard is `window.dispatchEvent`) — if you set `window.dispatch = dispatch` in app.js, it works but only after app.js loads
- `HashChangeEvent` works regardless of load order because `router.js` adds the `hashchange` listener at script load time (not inside async init)
- Use `if (location.hash) { dispatchEvent(...) } else { location.hash = '#/' }` to avoid an infinite dispatch loop on first page load

**⚠️ CRITICAL — i18n key extraction must include keys from ALL views, including ones written later:**

When extending `I18N` to multiple languages, the expansion script typically extracts all keys from the `en` block (e.g. via a regex pass over the `en: { ... }` content). If a view file is added **after** the expansion script ran, the new view's `t('newKey')` calls reference keys that aren't in any language block yet. `t()` falls back to returning the key itself, so the page renders `msgTitle` / `msgWithAdmin` / etc. as literal text instead of the translation.

**Real ImageGen bug (production deploy):** Added 8-language expansion with 107 keys extracted from `en`. Then later added `js/views/messages.js` + `js/views/admin.js` that referenced 6 new keys (`msgTitle`, `msgWithAdmin`, `msgAdminLabel`, `msgInputPlaceholder`, `msgSend`, `msgEmpty`). Those 6 keys were never in any `I18N.<lang>` block. Result: the `/messages` page rendered `msgTitle`, `msgWithAdmin`, `msgAdminLabel` etc. as literal camelCase text. User reported "the messaging page is broken".

**Fix — either:**

1. **Run the expansion AFTER all views are written**, OR
2. **Grep the codebase for `t\('([a-zA-Z]+)'\)` calls** across all `js/views/*.js` files before running expansion, build the union of keys, then add missing ones to every language block:
   ```bash
   grep -hroE "t\\('([a-zA-Z_]+)'\\)" js/views/ js/*.js | sort -u
   ```
3. **For incremental additions** (single new view + a few keys), add to all language blocks via a small script that re-runs the key-extraction pass.

**Verification after expansion** — pick 2-3 random `t('...')` calls and check that `I18N[currentLang][key]` resolves to a non-key value:
```javascript
// In a view, before deploying:
const sampleKeys = ['msgTitle', 'pricingBadge', 'genEmpty'];
for (const k of sampleKeys) {
  if (t(k) === k) console.warn('MISSING KEY:', k, 'in', currentLang);
}
```

### Hardcoded static text in header/footer (or any persistent chrome) won't auto-translate — must add `data-i18n`

The `setLang()` function only walks `[data-i18n]` and `[data-i18n-placeholder]` elements. Text that was hardcoded in `index.html` (e.g. `<a>Characters</a>`, `<h4>Product</h4>`, `<h4>Legal</h4>`) stays in the source language forever unless you tag it.

**Symptom**: User reports "the nav doesn't change when I switch language" but switching language DOES update content rendered through `views/*.js`. Header / footer / other static chrome stays in the original source language.

**Root cause**: `setLang()` only mutates elements that have `data-i18n` (text content) or `data-i18n-placeholder` (placeholder attribute) attributes. Static text in `index.html` has no such attribute, so `setLang()` skips it.

**Fix — three steps for every static text in chrome**:

1. **Tag the element** with `data-i18n="<key>"` matching an existing key in `I18N[lang]`:
   ```html
   <a href="#/generate?type=character" data-i18n="navCharacter">Characters</a>
   <h4 data-i18n="footerProduct">Product</h4>
   <a href="#/terms" data-i18n="footerTerms">Terms</a>
   ```
   The English text inside is the fallback for crawlers / no-JS / pre-hydration — keep it accurate.

2. **For placeholders / input values / titles**, use `data-i18n-placeholder`, `data-i18n-title`, etc. (Add a corresponding loop in `setLang()` if the attribute isn't already wired.)

3. **For new keys** (e.g. `footerProduct` doesn't exist yet), add to **every language block** in `i18n.js`. The class-level workflow:
   ```python
   # Walk every "  <lang>: {" block, insert the new keys right before the closing "  },"
   # Verify with: grep -c "footerProduct:" js/i18n.js   # must equal N (number of languages)
   # Verify syntax: node --check js/i18n.js
   ```

**Discovery recipe** when user reports "X doesn't translate":
```bash
# Find all visible text in index.html that has no data-i18n neighbor
grep -nE '>[A-Z][a-zA-Z ]+<' index.html | head -40
# Cross-check against I18N to see which keys already exist
grep -oE "^\s+[a-zA-Z]+:" js/i18n.js | sort -u
```

**Don't translate**: brand names (logo text, "© 2026 StoryForge AI"), deliberately-untranslated promotional copy, JS-only strings (those go through `t()`). Add `data-i18n` only to text that should swap with language.

**Don't** put `${t('foo')}` syntax directly in `index.html` — see the existing "HTML static text must NOT use `${t('...')}` syntax" pitfall below for why that fails.

### HTML static text must NOT use `${t('...')}` syntax

`${t('key')}` is **JavaScript template literal syntax**. It only evaluates inside a JS template string (`` `...${expr}...` ``). When written as raw HTML in `index.html`, the browser parses it as literal text and the user sees `${{msgTitle}}` (looks like Angular interpolation, but isn't — Angular isn't running).

```html
<!-- ❌ BROKEN — HTML parser treats as literal text -->
<a href="#/messages" class="nav-link">${t('msgTitle')}</a>

<!-- ✅ OPTION A — Static fallback in the source language -->
<a href="#/messages" class="nav-link" data-i18n="msgTitle">Messages</a>

<!-- ✅ OPTION B — Static fallback + JS updates textContent on lang change -->
<a href="#/messages" class="nav-link">Messages</a>
<!-- Then in store.js updateUserMenu() / setLang():
     msgLink.textContent = t('msgTitle'); -->
```

**Why this matters:** Admin-only or auth-gated nav links often have a static `display:none` and get shown via JS when the user logs in. The link's text comes from the static HTML. If the static HTML uses `${t(...)}`, the user sees the literal template syntax until JS replaces it (and the JS replacement path must actually run — easy to forget on a hidden element that only shows on login).

**For every hidden/conditional UI element with translatable text**, use the data-i18n pattern OR set `textContent` in `updateUserMenu()` (called on login/logout/lang switch).

### Static header/footer text never translates without `data-i18n`

**Scenario**: A multi-language SPA's view content (home page, generation form, account page) translates correctly on language switch, but the **header navigation menu** and **footer column titles + links** stay in the original (English) language no matter what. This was a real production bug in ImageGen (Aug 2026): 8-language i18n was supposedly "fully translated" but header nav and footer had hardcoded English with no `data-i18n` — only the view content translated.

**Symptom**: User clicks "中" in the language menu → main page hero, buttons, form labels flip to Chinese, but the nav bar still reads "Characters / Scenes / Props / Pricing" and the footer still reads "PRODUCT / ACCOUNT / LEGAL · Terms / Privacy".

**Root cause**: `setLang()` does TWO things on language switch:
1. Updates every element with `data-i18n="..."` attribute to the new language's text
2. Re-renders the current view (via `dispatchEvent(new HashChangeEvent('hashchange'))`) so view templates rebuild with `t()` calls

Static HTML in `<header>` and `<footer>` is **not** re-rendered — it lives outside `<main id="app">` and views only touch `<main>`. So the ONLY mechanism that updates header/footer text is step 1 (the `data-i18n` scan). If the static HTML is hardcoded English with no `data-i18n`, it stays English forever.

```html
<!-- ❌ BROKEN — hardcoded English, no data-i18n, setLang() never touches it -->
<a href="#/pricing" class="nav-link">Pricing</a>

<!-- ✅ FIXED — setLang() updates this on every language switch -->
<a href="#/pricing" class="nav-link" data-i18n="navPricing">Pricing</a>
```

**Diagnosis ladder**:
1. Switch to a non-English language via the toggle
2. View content (in `<main>`) translates, but a specific static element (header/footer) doesn't
3. Inspect the untranslated element in DevTools — it lacks a `data-i18n` attribute
4. Check i18n.js for the corresponding key — it may exist; the bug is in the HTML, not the dictionary
5. Audit the whole static template: `grep -nE '<a|<h[1-6]|<span|<p|<button|<label' index.html | grep -v 'data-i18n'` to find every hardcoded user-facing text node

**Fix — 2 changes per untranslated element**:
1. Add `data-i18n="<key>"` to the HTML element
2. Make sure `<key>` exists in **every** language block in `I18N` (use the batch-add recipe below)

**Belt-and-suspenders for missing keys**: `setLang()` does `el.textContent = I18N[lang][key] || key` — if a key is missing in a language, the element's text becomes the key itself (e.g. "footerProduct" rendered as visible text). Always verify after fixing:
```javascript
['footerProduct', 'footerAccount', 'footerLegal', 'footerTerms', 'footerPrivacy'].forEach(k => {
  if (t(k) === k) console.warn('MISSING:', k, 'in', currentLang);
});
```

**Batch-add a new key to all 8 language blocks** (Python — avoid `patch()` for indent-sensitive i18n.js):

```python
import re
LANGS = {
  'en': {'footerProduct': 'Product', 'footerAccount': 'Account', 'footerLegal': 'Legal',
         'footerTerms': 'Terms', 'footerPrivacy': 'Privacy'},
  'zh': {'footerProduct': '产品', 'footerAccount': '账户', 'footerLegal': '法律',
         'footerTerms': '服务条款', 'footerPrivacy': '隐私政策'},
  # ... 6 more languages
}

with open('js/i18n.js', 'r', encoding='utf-8') as f:
    content = f.read()

for lang, keys in LANGS.items():
    insertion = ''.join(f"    {k}: '{v}',\n" for k, v in keys.items())
    # Find this language block's start
    start_m = re.search(f"  {lang}: {{\n", content)
    if not start_m: continue
    block_start = start_m.end()
    # Find the first `  },\n` at column 0 after block_start (block's closing)
    end_m = re.search(r"^  \},\n", content[block_start:], re.MULTILINE)
    if not end_m: continue
    # Within the block, find the last key line (msgAdminReply) and insert after it
    block = content[block_start:block_start + end_m.start()]
    msg_m = re.search(r"(    msgAdminReply: '(?:[^'\\]|\\.)*',)\n", block)
    if not msg_m: continue
    insert_pos = block_start + msg_m.end()
    content = content[:insert_pos] + insertion + content[insert_pos:]

with open('js/i18n.js', 'w', encoding='utf-8') as f:
    f.write(content)
```

**Why Python (not `patch()`)**: the patch tool's fuzzy matching can silently add 4 extra leading spaces to every line of the replacement block when `old_string` and `new_string` differ in trailing context (see "Patch Indentation Recovery" in `frontend-patching`). i18n.js is a 1000+ line dict literal where indent is load-bearing — one off-by-4 error and the file fails `node --check`. Bytes-level Python replacement makes the change atomic and indentation-explicit.

**Why NOT use the per-language pattern `(  \},)` alone**: that pattern matches the closing brace of nested objects too, not just top-level language blocks. Always scope the search to the current language block's range (start at `  <lang>: {`, end at first `  },\n` after it).

**Prevention checklist** — before deploying a new SPA or expanding to a new language:
- [ ] Every user-facing text node in `index.html` (header, footer, error pages, modals) has `data-i18n`
- [ ] Every `data-i18n` key exists in all language blocks of `I18N`
- [ ] Bumped `?v=N` on all script/link tags for cache-bust
- [ ] Hard-refresh in browser (`Ctrl+Shift+R`) and toggle all 8 languages — verify header AND footer translate
- [ ] If using a CF Worker that bundles static files: `?query` strings may not bypass worker cache; use `location.reload(true)` in console to force a fresh fetch

### `updateUserMenu()` is the single place to refresh auth/role/lang-dependent UI text

Login state, role (admin/member), and language changes all affect nav links, user buttons, and credit displays. **All three must be re-rendered in one place** — `updateUserMenu()` — called:

1. After `store.login()` / `store.logout()` (immediate visual feedback)
2. On every `router.navigate()` (catches browser back/forward + direct URL entry)
3. After `setLang()` (so hidden nav links like the messages link get their new-language text)

**Concrete pattern from ImageGen** — the messages nav link:
```javascript
function updateUserMenu() {
  const msgLink = document.querySelector('.nav-link-msg');
  if (msgLink) {
    msgLink.style.display = currentUser ? '' : 'none';
    msgLink.textContent = t('msgTitle');   // ← re-translates on lang switch
  }
  // ... rest of menu rendering
}
```

If you skip step 3, the nav link shows "Messages" in English after switching to 简体中文 (because the static HTML fallback is English). Belt-and-suspenders: also add `data-i18n="msgTitle"` to the HTML and let `applyTranslations()` handle it — that way both paths work.

**Avoid scroll reset on language change** — dispatch normally scrolls to top. Track the last hash and skip scroll if hash didn't change:
```javascript
let _lastDispatchHash = null;
async function dispatch(opts = {}) {
  const hashChanged = _lastDispatchHash !== location.hash;
  _lastDispatchHash = location.hash;
  if (!opts.skipScroll && hashChanged) {
    window.scrollTo(0, 0);
  }
  // ... rest of dispatch
}
```

**Why this matters:** A user reading halfway down a page clicks the language toggle and expects the page text to change WITHOUT jumping to the top.

### 5. Data Store (store.js)

IndexedDB wrapper class:

```javascript
class DataStore {
  async init() { /* open IDB, create object stores + indexes */ }
  async getAll() { /* return sorted array */ }
  async get(id) { /* return single doc */ }
  async add(doc) { /* generate ID, set timestamps, put */ }
  async update(id, updates) { /* merge + save */ }
  async delete(id) { /* remove */ }
}
```

Index strategies:
- `createdAt` descending for "latest"
- `votes` descending for "popular"
- Category index for filtering

#### IndexedDB Version Migration

When adding or removing object stores/indexes in an already-deployed app, bump the DB version and handle migration in `onupgradeneeded`:

```javascript
async init() {
  return new Promise((resolve, reject) => {
    // Bump version number on schema change
    const req = indexedDB.open('MyAppDB', 3);  // was 2
    req.onupgradeneeded = (e) => {
      const db = e.target.result;
      const oldVer = e.oldVersion;  // 0 = new DB

      // Creating stores (only if they don't exist — safe on fresh install)
      if (!db.objectStoreNames.contains('items')) {
        db.createObjectStore('items', { keyPath: 'id' });
      }

      // Migration: v2 → v3 — drop old index
      if (oldVer < 3 && db.objectStoreNames.contains('claims')) {
        const store = e.target.transaction.objectStore('claims');
        if (store.indexNames.contains('oldUniqueIndex')) {
          store.deleteIndex('oldUniqueIndex');
        }
        // Add new index
        store.createIndex('newIndex', 'newField', { unique: false });
      }
    };
    req.onsuccess = (e) => { this.db = e.target.result; resolve(); };
    req.onerror = (e) => reject(e.target.error);
  });
}
```

**Rules:**
- Never decrement the version — browsers only migrate forward
- `e.oldVersion` is 0 for first-time visitors (no stores exist yet)
- Only create stores/indexes when they don't exist (guarded by `contains`)
- Only drop indexes when `oldVersion < N` (guarded migration path)
- Deleting a unique index removes the constraint — add application-level validation in your write functions to replace it

### 6. Router (router.js)

Hash-based SPA router:

```javascript
const routes = {};

function handleRoute(path) {
  // Match exact or dynamic (/detail/:id)
  let handler = routes[path];
  if (!handler && path?.startsWith('/detail/')) {
    handler = routes['/detail/:id'];
  }
  if (handler) handler();
  else if (routes['/']) routes['/']();
}

window.addEventListener('hashchange', () => {
  handleRoute(location.hash.slice(1) || '/');
});
```

Register routes in `app.js` init:

```javascript
registerView('/', views.home.render);
registerView('/browse', views.browse.render);
registerView('/detail/:id', views.detail.render);
```

**⚠️ CRITICAL — `this` binding pitfall (CRASH: `this.bindEvents is not a function`):**

When you pass a view method directly as the route handler — `routes['/generate'] = views.generate.render` — and then call `routes['/generate'](params)`, **the `this` inside `render` is `undefined`** (in strict mode) or the global object. Any internal call like `this.bindEvents(type)`, `this.openModal()`, or `this.runGeneration(...)` throws `TypeError: this.bindEvents is not a function`.

This was a real bug that bit ImageGen (build/test/deploy went fine, but the live page crashed the moment `/generate` was navigated to).

**Fix — provide a `routeView(path, view, methodName)` helper that binds `this` to the view object:**

```javascript
// In router.js
const routes = {};

function route(path, handler) {
  routes[path] = handler;  // For handlers that don't need `this`
}

// Bound-route helper: `this` inside the method ALWAYS points to the view object
function routeView(path, view, methodName) {
  routes[path] = (...args) => view[methodName].apply(view, args);
}

async function dispatch() {
  const { path, params } = parseHash();
  const handler = routes[path] || routes['/'];
  if (!handler) return renderNotFound();
  try {
    await handler(params);  // `this` is irrelevant — already bound by routeView
  } catch (e) {
    console.error('Route error:', e);
    renderError(e.message);
  }
  updateUserMenu();
}
```

**Registration becomes:**

```javascript
// In each view file's bottom:
routeView('/', views.home, 'render');          // ← `this` inside render = views.home
routeView('/generate', views.generate, 'render');
routeView('/account', views.account, 'render');

// Plain route() still works for non-method callbacks:
route('/health', () => pingHealth());
```

**Why `.apply(view, args)` instead of `.bind(view)`:**

`.bind()` returns a new function and discards the original — fine for one-time registration. But `apply(view, args)` inside an arrow function lets each invocation pass through the latest `params` argument cleanly. Both work; `apply` is one fewer closure allocation.

**Compatibility note:** View methods that don't reference `this` (e.g. a trivial `render() { return '...'; }`) work fine via either pattern. The breakage only appears when view internals call `this.somethingElse()`, which is common once a view has more than one method (e.g. `render()` calls `this.bindEvents()`, `this.runGeneration()`).

**Alternative — if you can't add a helper (older router):** wrap each handler at registration:

```javascript
// Old-style router without `routeView` — bind manually
route('/generate', (...args) => views.generate.render.apply(views.generate, args));
route('/account', (...args) => views.account.render.apply(views.account, args));
```

Or refactor views to not use `this` — make them factory functions or move helper methods outside the view object:

```javascript
// Workaround: helpers live as module-level functions
function bindGenerateEvents(type) { /* ... */ }
function runGeneration(params) { /* ... */ }

views.generate = {
  async render(params) {
    bindGenerateEvents(params.type);
    runGeneration(params);
  }
};
```

This works but loses the encapsulation that the view-object pattern gives you. Prefer `routeView`.

### 7. View Pattern

Each view exports a `render()` async function:

```javascript
views.detail = {
  render: async () => {
    const id = location.hash.split('/detail/')[1];
    const data = await store.get(id);
    document.getElementById('app').innerHTML = `...template...`;
  },
  vote: async (id) => { await store.vote(id); views.detail.render(); }
};
```

- Use `escHTML()` helper for user-generated content
- Return false from form handlers to prevent reload
- Toast notifications for user feedback

### 8. File Upload (for assets/media)

Drag-and-drop file upload via FileReader. Supports image, video, AND audio files:

```javascript
// Drop zone — accept all three media types
<div class="file-drop" ondrop="handleDrop(event)" onclick="fileInput.click()">
<input type="file" accept="image/*,video/*,audio/*" multiple style="display:none">
```

```javascript
// File filter — accept image, video, and audio
handleFiles: (files) => {
  Array.from(files).forEach(file => {
    if (!file.type.match(/^image\/|video\/|audio\//)) return;
    const reader = new FileReader();
    reader.onload = (e) => {
      uploadedFiles.push({ name, size, type, dataUrl: e.target.result });
    };
    reader.readAsDataURL(file);
  });
}
```

Store base64 in IndexedDB (note: limits ~5-10MB per record for large files).

#### Enhanced Upload: Modal Dialog with Category Selection

For apps where assets need to be categorized (e.g., character / scene / prop / character audio), use a modal dialog instead of a bare file picker:

**Define asset categories as a constant map:**

```javascript
const ASSET_CATEGORIES = {
  character: { zh: '角色', en: 'Character' },
  scene: { zh: '场景', en: 'Scene' },
  prop: { zh: '道具', en: 'Prop' },
  char_audio: { zh: '角色音频', en: 'Char Audio' }
};
```

**Upload dialog (modal overlay):**

```javascript
uploadAssetDialog: (scriptId) => {
  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);z-index:9999;display:flex;align-items:center;justify-content:center;padding:24px;';

  const catOptions = Object.entries(ASSET_CATEGORIES)
    .map(([k, v]) => `<option value="${k}">${v[currentLang]}</option>`).join('');

  overlay.innerHTML = `
    <div class="upload-modal" style="background:var(--bg-card);padding:28px;max-width:440px;">
      <h3>上传素材</h3>
      <div class="form-group">
        <label>素材分类</label>
        <select id="assetUploadCat">
          <option value="">-- 选择分类 --</option>
          ${catOptions}
        </select>
      </div>
      <div id="assetDropZone" style="border:2px dashed var(--border);padding:32px;text-align:center;">
        <p>拖放文件到此处</p>
        <input type="file" id="assetFileInput" multiple accept="image/*,video/*,audio/*">
      </div>
      <div id="assetUploadPreview"></div>
      <button id="assetUploadBtn">上传</button>
    </div>`;

  document.body.appendChild(overlay);

  // Wire events: file selection, drag-drop, upload with category
  document.getElementById('assetUploadBtn').onclick = async () => {
    const category = document.getElementById('assetUploadCat').value;
    if (!category) { showToast('请选择素材分类'); return; }
    for (const file of selectedFiles) {
      const reader = new FileReader();
      await new Promise(resolve => {
        reader.onload = async (ev) => {
          await store.addAsset({
            scriptId, name: file.name, type: file.type,
            category: category,  // ← saves the category
            dataUrl: ev.target.result,
            createdBy: store.currentUser.id,
            createdByName: store.currentUser.displayName || store.currentUser.username
          });
          resolve();
        };
        reader.readAsDataURL(file);
      });
    }
    overlay.remove();
    showToast('素材已上传');
    views.detail.render();
  };
}
```

### 9. Asset Display with Download and Rename

When rendering uploaded assets, show appropriate icons/players, add download buttons, and allow inline renaming:

```javascript
// In the asset card template:
<div style="display:flex;gap:8px;align-items:flex-start;">
  <div style="width:48px;height:48px;border-radius:6px;overflow:hidden;flex-shrink:0;display:flex;align-items:center;justify-content:center;">
    ${a.type.startsWith('image/')
      ? `<img src="${a.dataUrl}" alt="" style="width:100%;height:100%;object-fit:cover;">`
      : a.type.startsWith('audio/')
        ? '&#127925;'  // music note
        : '&#127909;'  // video icon
    }
  </div>
  <div style="flex:1;min-width:0;">
    <!-- Double-click or button to rename -->
    <span ondblclick="views.detail.renameAsset('${a.id}')" title="双击重命名">
      ${escHTML(a.name)}
    </span>
    <!-- Download button -->
    <a href="${a.dataUrl}" download="${escHTML(a.name)}">下载</a>
    <!-- Rename button -->
    <button onclick="views.detail.renameAsset('${a.id}')">&#9998;</button>
  </div>
</div>
```

**Rename implementation (requires store.renameAsset):**

```javascript
// store.js
async renameAsset(id, newName) {
  return new Promise((resolve, reject) => {
    const tx = this.db.transaction(ASSETS_STORE, 'readwrite');
    const req = tx.objectStore(ASSETS_STORE).get(id);
    req.onsuccess = () => {
      const asset = req.result;
      if (asset) { asset.name = newName; tx.objectStore(ASSETS_STORE).put(asset); }
    };
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

// detail.js
renameAsset: async (assetId) => {
  const tx = store.db.transaction('assets', 'readonly');
  const req = tx.objectStore('assets').get(assetId);
  req.onsuccess = async () => {
    const asset = req.result;
    if (!asset) return;
    const newName = prompt('重命名素材：', asset.name);
    if (newName && newName.trim()) {
      await store.renameAsset(assetId, newName.trim());
      showToast('已重命名');
      views.detail.render();
    }
  };
},
```

**Convenient upload (no name prompt, uses filename):**

```javascript
uploadAsset: async (scriptId) => {
  if (!store.isLoggedIn()) return;
  const input = document.createElement('input');
  input.type = 'file';
  input.multiple = true;
  input.accept = 'image/*,video/*,audio/*';
  input.onchange = async (e) => {
    for (const file of Array.from(e.target.files)) {
      const reader = new FileReader();
      await new Promise(resolve => {
        reader.onload = async (ev) => {
          await store.addAsset({
            id: 'asset_' + Date.now(),
            scriptId,
            name: file.name,          // use filename, no prompt needed
            type: file.type,
            dataUrl: ev.target.result,
            createdAt: Date.now(),
            createdBy: store.currentUser.id,
            createdByName: store.currentUser.displayName || store.currentUser.username
          });
          resolve();
        };
        reader.readAsDataURL(file);
      });
    }
    showToast(files.length + ' 个素材已上传');
    views.detail.render();
  };
  input.click();
},
```

### 10. Detail Page Layout — Assets in Sidebar

For script/asset detail pages, put the asset management in a right sidebar rather than inline with the main content:

```html
<div class="detail-layout" style="display:flex;gap:24px;">
  <!-- Left: Main Content -->
  <div class="detail-main" style="flex:1;min-width:0;">
    <!-- Title, meta, synopsis, script body, episode grid, vote -->
  </div>
  <!-- Right: Sidebar -->
  <div class="detail-sidebar" style="width:320px;flex-shrink:0;">
    <!-- Script info card -->
    <div style="background:var(--bg-card);border-radius:var(--radius);padding:20px;">
      <h4>剧本信息</h4>
      <div>作者, 集数, 时长, 字数, 日期</div>
    </div>
    <!-- Assets with upload button -->
    <div style="...">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <h4>上传素材 (N)</h4>
        <button onclick="views.detail.uploadAsset('${id}')">+ 上传素材</button>
      </div>
      <div id="assetList"><!-- asset cards here --></div>
    </div>
  </div>
</div>
```

### 10a. Admin-Only Nav Link (Toggle visibility in JS)

For admin-only navigation items in the top bar, add the link to the static HTML with a CSS class that hides it by default, then toggle visibility in JS after login status is known:

**In index.html:**
```html
<nav class="nav" id="mainNav">
  <a href="#/browse">浏览剧本</a>
  <a href="#/upload">上传</a>
  <a href="#/admin/members" class="nav-link nav-members-link">会员管理</a>
</nav>
```

**In style.css:**
```css
.nav-members-link { display: none; }
```

**In app.js `updateUserMenu()`:**
```javascript
function updateUserMenu() {
  const membersLink = document.querySelector('.nav-members-link');
  if (membersLink) {
    membersLink.style.display = store.isAdmin() ? '' : 'none';
  }
  // ... rest of user menu rendering
}
```

**Key behavior:**
- Non-admins never see the link (hidden by CSS + enforced by JS toggle)
- The link appears immediately when admin logs in (re-rendered on every `updateUserMenu()` call)
- No need for dynamic HTML injection — just toggle `display`

### 11. Episode Claim System with Role-Based Visibility

For collaborative content where members claim individual episodes.

**Claim rules:** Each episode can be claimed by up to **2 different members**. The same member cannot claim the same episode twice.

**Permission logic in render:**

```javascript
const isAdmin = store.isAdmin();
const myClaims = claims.filter(c => c.memberId === store.currentUser.id);
const myClaim = myClaims.length > 0 ? myClaims[0] : null;

// Admin OR claimed member: full script body visible
${isAdmin || myClaim ? `<div class="detail-body">${escHTML(script.script)}</div>` : ''}

// Member-only: show claimed episode content area (in addition to full script)
${!isAdmin && myClaim ? `
  <div>
    <strong>我的认领 - 第${myClaim.episodeNum}集</strong>
    <div>${escHTML(myClaim.episodeContent || '(暂无内容)')}</div>
  </div>` : ''}
```

⚠️ **Pitfall**: Originally the script body was gated on `${isAdmin ? ...}` only — non-admin members who claimed an episode saw nothing useful (just empty `episodeContent`). Fix: change to `${isAdmin || myClaim ? ...}` so members who claimed can reference the full script while editing their episode content.

**Episode grid** — groups claims by episode number (since multiple claims per episode), shows all claimer names, and provides claim/edit/unclaim per episode:

```javascript
renderEpisodes: (totalEp, claims, scriptId) => {
  // Group claims by episodeNum (support up to 2 claims per episode)
  const epGroup = {};
  claims.forEach(c => {
    if (!epGroup[c.episodeNum]) epGroup[c.episodeNum] = [];
    epGroup[c.episodeNum].push(c);
  });
  const me = store.currentUser;
  const canClaim = store.isLoggedIn();

  let html = '';
  for (let i = 1; i <= totalEp; i++) {
    const epClaims = epGroup[i] || [];
    const count = epClaims.length;
    const myClaim = me ? epClaims.find(c => c.memberId === me.id) : null;
    const hasMe = !!myClaim;
    const isFull = count >= 2;

    html += `<div class="episode-card ${count > 0 ? 'claimed' : 'available'} ${hasMe ? 'mine' : ''}">
      <div class="episode-num">第${i}集</div>`;

    // Show all claimers for the episode
    if (count > 0) {
      epClaims.forEach(c => {
        const isOwner = me && me.id === c.memberId;
        html += `<div class="episode-status claimed-badge">
          <span class="claimed-dot">&#9679;</span> ${escHTML(c.memberName)}
        </div>`;
      });
    } else {
      html += `<div class="episode-status available-badge">待认领</div>`;
    }

    // Claim button — only if < 2 claims AND user hasn't claimed
    if (canClaim && !isFull && !hasMe) {
      html += `<button class="btn btn-primary btn-sm"
        onclick="views.detail.claimEpisode('${scriptId}','${i}')">认领此集</button>`;
    }

    // My claim actions (edit / unclaim)
    if (hasMe) {
      html += `<button onclick="views.detail.editEpisode('${myClaim.id}')">编辑</button>
               <button onclick="views.detail.unclaimEpisode('${myClaim.id}')">取消认领</button>`;
    }

    html += `</div>`;
  }
  return html;
},
```

**Store-side claim validation** (in `store.js`):

```javascript
async claimEpisode(scriptId, episodeNum, memberId, memberName) {
  // Check existing claims for this episode (max 2, prevent duplicate user)
  const claims = await this.getClaimsByScript(scriptId);
  const epClaims = claims.filter(c => c.episodeNum === episodeNum);
  if (epClaims.some(c => c.memberId === memberId)) {
    throw new Error('episode_already_claimed');
  }
  if (epClaims.length >= 2) {
    throw new Error('episode_max_claims');
  }
  const id = 'claim_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6);
  const claim = { id, scriptId, episodeNum, memberId, memberName, claimedAt: Date.now() };
  // ... write to IndexedDB
}
```

**Key behavior:**
- 0 claims: "待认领" badge + claim button
- 1 claim: shows claimer name + second claim button (if user hasn't claimed)
- 2 claims: shows both claimer names, no more claim buttons
- After claiming, the page re-renders and the member sees the claimed episode's content area
- The member can edit or unclaim only their own claim on an episode
- Admin sees everything regardless
- Other members' claimed episodes show the claimer's name but no edit buttons

The `download` attribute on the `<a>` link triggers browser download. The `controls` attribute on `<audio>` adds play/pause/seek UI.

#### Asset Category Filter Tabs

When assets have a `category` field, add filter tabs above the asset list to let users filter by type:

**State + filter function in view:**

```javascript
views.detail = {
  assetCategoryFilter: 'all',  // 'all' | 'character' | 'scene' | 'prop' | 'char_audio'

  renderAssetList: (assets) => {
    const filter = views.detail.assetCategoryFilter;
    const filtered = filter === 'all' ? assets : assets.filter(a => (a.category || '') === filter);
    if (filtered.length === 0) return '<p style="color:var(--text-muted);text-align:center;">暂无素材</p>';
    return filtered.map(a => views.detail.assetCardHTML(a)).join('');
  },

  setAssetFilter: (category) => {
    views.detail.assetCategoryFilter = category;
    // Re-fetch assets and re-render list
    const id = location.hash.split('/detail/')[1];
    store.getAssetsByScript(id).then(allAssets => {
      document.getElementById('assetList').innerHTML = views.detail.renderAssetList(allAssets);
    });
    // Update tab button styles
    document.querySelectorAll('.asset-cat-filter').forEach(btn => {
      const cat = btn.onclick?.toString().match(/'([^']+)'/)?.1 || 'all';
      btn.style.background = cat === category ? 'var(--accent)' : 'transparent';
      btn.style.color = cat === category ? '#fff' : 'var(--text-muted)';
    });
  }
};
```

**Template for filter tabs + list:**

```html
<!-- Category filter tabs (pill-style buttons) -->
<div style="display:flex;gap:4px;margin-bottom:12px;flex-wrap:wrap;">
  <button class="asset-cat-filter"
    onclick="views.detail.setAssetFilter('all')"
    style="border-radius:20px;padding:3px 10px;font-size:0.75rem;
           background:${views.detail.assetCategoryFilter === 'all' ? 'var(--accent)' : 'transparent'};
           color:${views.detail.assetCategoryFilter === 'all' ? '#fff' : 'var(--text-muted)'}">
    ${t('assetCatAll')}
  </button>
  ${Object.keys(ASSET_CATEGORIES).map(k => `
    <button class="asset-cat-filter"
      onclick="views.detail.setAssetFilter('${k}')"
      style="border-radius:20px;padding:3px 10px;font-size:0.75rem;
             background:${views.detail.assetCategoryFilter === k ? 'var(--accent)' : 'transparent'};
             color:${views.detail.assetCategoryFilter === k ? '#fff' : 'var(--text-muted)'}">
      ${ASSET_CATEGORIES[k][currentLang]}
    </button>
  `).join('')}
</div>

<!-- Asset list -->
<div id="assetList">
  ${views.detail.renderAssetList(assets)}
</div>
```

**Category badge on each asset card** (inline style, colored by type):

```javascript
const catColors = {
  character: '#6c5ce7',  // purple
  scene: '#00b894',      // green
  prop: '#fdcb6e',       // yellow
  char_audio: '#e17055'  // orange
};
const catColor = catColors[a.category] || 'var(--text-muted)';
const catName = ASSET_CATEGORIES[a.category]?.[currentLang] || '';

// In card template:
${catName ? `<span style="font-size:0.65rem;padding:1px 8px;border-radius:10px;
  background:${catColor}22;color:${catColor};border:1px solid ${catColor}44;">
  ${catName}</span>` : ''}
```

### 12. Prompt Library

For apps where admins maintain a library of text prompts (e.g., image/video generation prompts) associated with a parent entity (script, project). Members can view and copy prompts; admins can add and delete them.

**Data Model (stored in IndexedDB):**

```javascript
// In store.js:
const PROMPTS_STORE = 'prompts';

// DB init (bump version):
if (!db.objectStoreNames.contains(PROMPTS_STORE)) {
  const store = db.createObjectStore(PROMPTS_STORE, { keyPath: 'id' });
  store.createIndex('scriptId', 'scriptId', { unique: false });
  store.createIndex('category', 'category', { unique: false });
}
```

**Prompt document shape:**

```javascript
{
  id: 'prompt_1741500000_abc123',
  scriptId: 'script_...',      // FK to parent
  name: '女主特写镜头',         // display label
  category: 'character',       // matches ASSET_CATEGORIES keys
  content: '...',              // the actual prompt text (English)
  createdBy: 'member_...',     // uploader ID
  createdByName: 'Kairos',     // uploader display name
  createdAt: Date.now()
}
```

**CRUD methods:**

```javascript
async addPrompt(prompt) { /* generate id, set createdAt, put in PROMPTS_STORE */ }
async getPromptsByScript(scriptId) { /* getAll, filter by scriptId, sort by createdAt desc */ }
async deletePrompt(id) { /* delete from PROMPTS_STORE */ }
```

**UI in Detail Sidebar (after assets section):**

```html
<!-- Prompt Library -->
<div style="background:var(--bg-card);border:1px solid var(--border);border-radius:var(--radius);padding:20px;">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
    <h4>提示词库 (${prompts.length})</h4>
    ${isAdmin ? `<button onclick="addPromptDialog('${id}')">+ 添加提示词</button>` : ''}
  </div>
  <div>
    ${prompts.length === 0
      ? '<p style="color:var(--text-muted);text-align:center;">暂无提示词</p>'
      : prompts.map(p => promptCardHTML(p)).join('')}
  </div>
</div>
```

**Prompt display variants:** Two display modes depending on user preference:

**A) Flat card list** — Each prompt is a bordered card with category badge, name, content, uploader, copy/delete buttons.

**B) Grouped by category (preferred for larger libraries)** — Prompts are split into 4 sections: 角色 / 场景 / 道具 / 角色音频. Each section has a colored header and a scrollable list. Row-level items omit the category badge since the header indicates it:

```javascript
renderPromptGroups: (prompts) => {
  const catOrder = ['character', 'scene', 'prop', 'char_audio'];
  const catLabels = { character: '角色', scene: '场景', prop: '道具', char_audio: '角色音频' };
  const catColors = { character: '#6c5ce7', scene: '#00b894', prop: '#fdcb6e', char_audio: '#e17055' };

  let html = '';
  for (const cat of catOrder) {
    const filtered = prompts.filter(p => (p.category || '') === cat);
    const color = catColors[cat];
    const label = catLabels[cat] || cat;
    const count = filtered.length;
    html += `
      <div style="margin-bottom:12px;">
        <div style="display:flex;align-items:center;gap:6px;margin-bottom:6px;">
          <span style="font-size:0.7rem;font-weight:600;color:${color};">${label}</span>
          <span style="font-size:0.6rem;color:var(--text-muted);">${count}</span>
        </div>
        ${count === 0
          ? `<div style="font-size:0.7rem;color:var(--text-muted);padding:4px 0;">暂无提示词</div>`
          : `<div style="max-height:200px;overflow-y:auto;">
              ${filtered.map(p => promptCardHTML(p)).join('')}
             </div>`}
      </div>`;
  }
  return html;
}
```

The individual prompt rows become simpler (no category badge, just name + content preview + copy + delete):

```javascript
promptCardHTML: (p) => {
  return `
    <div style="display:flex;align-items:center;gap:6px;padding:5px 0;border-bottom:1px solid var(--border);font-size:0.78rem;">
      <span style="font-weight:600;color:var(--text);flex-shrink:0;min-width:50px;max-width:80px;
        overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="${escHTML(p.name)}">${escHTML(p.name)}</span>
      <span style="flex:1;color:var(--text-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
        font-size:0.72rem;" title="${escHTML(p.content)}">${escHTML(p.content)}</span>
      <button class="btn btn-outline btn-sm" onclick="copyPrompt('${escJS(p.content)}')">📋</button>
      ${isAdmin ? `<button onclick="deletePrompt('${p.id}')">✕</button>` : ''}
    </div>`;
}
```

**Prompt card template (with category badge, copy button, admin delete):**

```javascript
promptCardHTML: (p) => {
  const isAdmin = store.isAdmin();
  const catKey = p.category || '';
  const catName = ASSET_CATEGORIES[catKey]?.[currentLang] || '';
  const catColors = { character: '#6c5ce7', scene: '#00b894', prop: '#fdcb6e', char_audio: '#e17055' };
  const catColor = catColors[catKey] || 'var(--text-muted)';
  return `
    <div style="margin-bottom:8px;padding:10px;border:1px solid var(--border);border-radius:8px;">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:6px;">
        <div style="display:flex;gap:6px;align-items:center;flex:1;min-width:0;">
          ${catName ? `<span style="font-size:0.65rem;padding:1px 8px;border-radius:10px;
            background:${catColor}22;color:${catColor};border:1px solid ${catColor}44;">${catName}</span>` : ''}
          <span style="font-weight:600;font-size:0.78rem;">${escHTML(p.name)}</span>
        </div>
        <div style="display:flex;gap:3px;flex-shrink:0;">
          <button onclick="copyPrompt('${escJS(p.content)}')" style="font-size:0.7rem;">📋 复制</button>
          ${isAdmin ? `<button onclick="deletePrompt('${p.id}')" style="font-size:0.65rem;color:var(--danger);">删除</button>` : ''}
        </div>
      </div>
      <div style="font-size:0.75rem;color:var(--text-muted);margin-top:2px;">${escHTML(p.content)}</div>
      <div style="font-size:0.65rem;color:var(--text-muted);margin-top:4px;">添加者: ${escHTML(p.createdByName || '')}</div>
    </div>`;
}
```

**Add Prompt Dialog (modal):**

```javascript
addPromptDialog: (scriptId) => {
  const overlay = document.createElement('div');
  overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);z-index:9999;display:flex;align-items:center;justify-content:center;padding:24px;';
  overlay.innerHTML = `
    <div class="upload-modal" style="background:var(--bg-card);padding:28px;max-width:480px;">
      <h3>添加提示词</h3>
      <div class="form-group">
        <label>提示词名称</label>
        <input type="text" id="promptNameInput" placeholder="例如：女主特写镜头">
      </div>
      <div class="form-group">
        <label>分类</label>
        <select id="promptCatInput">
          ${Object.entries(ASSET_CATEGORIES).map(([k,v]) =>
            `<option value="${k}">${v[currentLang]}</option>`).join('')}
        </select>
      </div>
      <div class="form-group">
        <label>提示词内容</label>
        <textarea id="promptContentInput" rows="5" placeholder="英文文生图/文生视频提示词"></textarea>
      </div>
      <button id="promptAddBtn">添加</button>
    </div>`;
  document.body.appendChild(overlay);
  document.getElementById('promptAddBtn').onclick = async () => {
    const name = document.getElementById('promptNameInput').value.trim();
    const category = document.getElementById('promptCatInput').value;
    const content = document.getElementById('promptContentInput').value.trim();
    if (!name || !category || !content) { showToast('请填写完整信息', true); return; }
    await store.addPrompt({ scriptId, name, category, content,
      createdBy: store.currentUser.id,
      createdByName: store.currentUser.displayName || store.currentUser.username
    });
    overlay.remove();
    showToast('已复制');
    views.detail.render();
  };
}
```

**Copy to Clipboard (with fallback):**

```javascript
copyPrompt: (text) => {
  navigator.clipboard.writeText(text).then(() => {
    showToast('已复制');
  }).catch(() => {
    // Fallback for older browsers
    const ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    showToast('已复制');
  });
}
```

**Delete Prompt:**

```javascript
deletePrompt: async (id) => {
  if (confirm('确定删除这个提示词？')) {
    await store.deletePrompt(id);
    showToast('已删除');
    views.detail.render();
  }
}
```

### 13. App Initialization (app.js)

```javascript
async function init() {
  await store.init();
  registerView('/', views.home.render);
  registerView('/browse', views.browse.render);
  registerView('/detail/:id', views.detail.render);
  handleRoute(location.hash.slice(1) || '/');
}
```

### 13a. User Menu Refresh After Login/Logout

**Common bug:** After `store.login(member)` or `store.logout()`, the header/nav still shows the previous state because `updateUserMenu()` wasn't called. The fix is to call it in three places:

**1. After login (in login.js submit handler):**

```javascript
store.login(member);
updateUserMenu();   // ← refresh nav + dropdown immediately
showToast('登录成功');
router.navigate('/');
```

**2. After logout (in doLogout):**

```javascript
function doLogout() {
  store.logout();
  updateUserMenu();  // ← hide admin nav, switch to login/register buttons
  router.navigate('/');
}
```

**3. On every route navigation (in router.js navigate):**

```javascript
window.router = {
  navigate: (p) => {
    history.pushState(null, '', '#' + p);
    handleRoute(p);
    if (typeof updateUserMenu === 'function') updateUserMenu();  // ← always sync
  },
  handleRoute,
  registerView
};
```

**Why three calls?**
- After login/logout: immediate visual feedback without waiting for navigation
- On every navigate: catches edge cases (browser back/forward, direct URL entry)

**Pitfall — function not yet defined:** If a view script calls `updateUserMenu()` before `app.js` has loaded, the call silently fails. Ensure `app.js` loads before all view scripts (see "Script load order matters" pitfall above).

## Common Pitfalls

### `const t` shadowing the i18n function causes "Cannot access 't' before initialization"

**Symptom**: Page renders with `ReferenceError: Cannot access 't' before initialization` in browser console, breaking the entire view. All globals after the bad function become undefined (JS parsing fails atomically — no graceful error UI).

**Root cause**: In `i18n.js`, `t` is a top-level `function` declaration — accessible as a global. But if a function inside a view file declares `const t = <something>`, the local `const t` shadows the global `t` for the ENTIRE function scope (temporal dead zone, not just below the declaration). Any reference to `t()` inside the function — including in callbacks like `.map(m => { t('key') })` evaluated lazily — throws because the local `t` hasn't been assigned yet.

**Real failure trail (ImageGen, Aug 2026)**: `js/views/admin.js` had `renderMessages()` that:
- Called `t('msgAdminLabel')` inside a `.map()` callback at one line
- Declared `const t = document.getElementById('adminMsgThread')` 30+ lines later to scroll-to-bottom

The `.map()` ran (and called `t('msgAdminLabel')`) BEFORE the local `const t` was assigned. TDZ threw, the entire admin console crashed. The two other `const t` declarations in the same file (in `render()` and a tab click handler) were innocent because they were in different scopes and didn't call `t()`.

**Fix**:
- **Rename the local variable.** `t` is precious — it's the i18n function. Use `threadEl`, `tab`, `el`, `elapsed`, `target`, etc. for DOM elements and ad-hoc values.
- For DOM scoping in click handlers, `const t = e.target.dataset.tab` is also risky if the same scope ever calls `t()`. Use `e.currentTarget.dataset.tab` directly, or capture into a differently-named local first.
- If a view method genuinely needs both `t()` and a local named `t` (don't), put the `const t` BEFORE any `t()` call in the function body. Better: rename the local.

**Diagnostic ladder when a view shows "Error" or globals are undefined**:

1. Open browser console → look for `Cannot access 't' before initialization` (or any TDZ error like `Cannot access 'X' before initialization`)
2. Note the function name and the line where `const X` is declared
3. Search that function for `X(` calls — they're the ones hitting TDZ
4. Rename the local to something else (e.g. `threadEl` for `t`, `el` for `e`, etc.)

**Pre-audit when writing a new view** (catches the trap before it ships):

```bash
# Check if any function in your new view file shadows a global name with const/let
grep -nE "^\s*const (t|e|n|x|key|el)\s*=" js/views/<yourview>.js
```

Any match inside a function that ALSO calls that name is a TDZ bug waiting to happen. Rename the local before merging.

### View renders must self-initialize all referenced variables (no cross-view closure sharing)

Sibling views often share structure — e.g. `views.login.render()` and `views.register.render()` both build OAuth + email forms. It's tempting to extract common setup into a sibling view (e.g. put `let config = await store.getPublicConfig()` in `login.render()` and rely on `config` being in scope inside `register.render()`'s template). **That breaks** because each view's render() runs in its own scope. The sibling's `let` doesn't leak.

**Symptom:** Click a route → page renders "Error" or "404". Browser console shows `ReferenceError: <var> is not defined at Object.render (<view>.js:N:M)`. Router catches it and shows a generic error page (looks identical to a route-not-found bug).

**Real example from ImageGen (production bug):**

```js
// views.login.js — WORKED because it defined config itself
views.login = {
  async render() {
    let config = { google_oauth: false, github_oauth: false };
    try { config = await store.getPublicConfig(); } catch (e) {}
    app.innerHTML = `<button ${config.google_oauth ? '' : 'disabled'}>...</button>`;
    // ...
  }
};

// views.register.js — BROKEN because config was never defined here
views.register = {
  async render() {
    // ← MISSING: let config = ... ; try { config = await store.getPublicConfig(); } ...
    app.innerHTML = `<button ${config.google_oauth ? '' : 'disabled'}>...</button>`;
    // ↑ ReferenceError: config is not defined — page shows "Error"
  }
};
```

The user reported "can't register" but the fix was simply adding the same `let config = ... ; await store.getPublicConfig()` block that login already had.

**Fix — make every render self-contained:**

1. **Never assume** a variable from a sibling view is in scope. Each `render()` should declare its own local state at the top.
2. If two views share the same setup, extract to a helper at module scope (NOT inside one view's render):
   ```js
   async function loadPublicConfig() {
     try { return await store.getPublicConfig(); }
     catch { return { google_oauth: false, github_oauth: false }; }
   }
   // Then in both views:
   const config = await loadPublicConfig();
   ```
3. After fixing the missing init, also bump `?v=N` in index.html (CF Worker default cache is 1h) and verify the fix is live with `fetch('...').then(t => t.includes('FIX_STRING'))`.

**Diagnosis ladder when a view shows "Error":**

1. Open browser console → look for `ReferenceError`
2. Note the variable name in the error
3. `grep -n "<varname>" js/views/*.js` — find the view that uses it
4. Check if that view declares it; if no, copy the declaration from a sibling that works

### Hash routing doesn't work on file:// protocol

When opening `index.html` directly (file://), `history.pushState` and `hashchange` may behave differently. Always test via a local server:

```bash
# Python
python3 -m http.server 8080
# Node
npx serve .
```

### Script load order matters — and depends on init pattern

JS files must be imported in dependency order in `<head>` or at end of `<body>`:
1. i18n (defines `I18N`, `t()`, `currentLang`)
2. store (depends on nothing)
3. router (defines `routes`, `handleRoute`, `registerView`)
4. views (define `views.X = {...}` and register routes via `routeView(...)`)
5. app (init that calls `dispatch()`)

**Two opposing orderings exist depending on your init pattern — pick correctly or get an intermittent 404 on the home page:**

**Pattern A — `app.js` exports globals used by views' top-level code (e.g. views reference `escHTML()` or `updateUserMenu()` directly when building their render template):**
```html
<!-- app.js FIRST so views can read its globals at registration time -->
<script src="js/i18n.js"></script>
<script src="js/store.js"></script>
<script src="js/router.js"></script>
<script src="js/app.js"></script>     <!-- exports escHTML, updateUserMenu, etc. -->
<script src="js/views/home.js"></script>
<script src="js/views/login.js"></script>
```

**Pattern B — `app.js` has an async IIFE that calls `dispatch()` to render the initial route (e.g. `await store.handleOAuthCallback(); ... dispatch();`). Views register themselves via `routeView()` but DON'T use app.js globals at registration time:**
```html
<!-- app.js LAST so views are registered BEFORE the IIFE's microtask resumes and calls dispatch() -->
<script src="js/i18n.js"></script>
<script src="js/store.js"></script>
<script src="js/router.js"></script>
<script src="js/views/home.js"></script>     <!-- registers routeView('/', views.home, 'render') -->
<script src="js/views/login.js"></script>
<script src="js/app.js"></script>          <!-- IIFE yields at first await, microtask runs before next <script>; views must already be loaded -->
```

**⚠️ CRITICAL race condition when using Pattern B (app.js LAST but loaded FIRST or where IIFE yields before views register):**

```javascript
// app.js — looks innocent but creates the bug
(async function init() {
  setLang(currentLang);
  setupLangMenu();
  const hashHandled = await store.handleOAuthCallback().catch(() => false);
  //                                                                       ^ YIELDS HERE
  // ... microtask runs BEFORE the next <script> tag executes ...
  updateUserMenu();
  if (!location.hash) location.hash = '#/';
  dispatch();   // ← routes is still {} if app.js was loaded before views/*.js!
})();
```

The first `await` yields to the microtask queue. Microtasks run **before** the next `<script>` tag executes. So the sequence is:
1. `app.js` script tag runs sync code → IIFE starts → first `await` yields
2. Microtask drains → IIFE resumes → `dispatch()` runs → `routes` is empty → renders "404"
3. THEN `views/*.js` script tags execute and register routes (too late, the 404 is already on screen)

**Symptom:** Home page intermittently shows "404" / "Page not found" instead of the actual home view. Sometimes a hashchange fires after routes register and "fixes" it on the second pass — that's why it's intermittent, not deterministic.

**Fix — put `app.js` AFTER `views/*.js` in the script tag order.** Then by the time the IIFE's microtask resumes, all routes are registered. Verified by `node --check` after each JS change AND a hard refresh in browser (Ctrl+Shift+R) to bypass cache.

**Diagnosis ladder:**
1. `browser_navigate('https://yoursite.com/')` → check `browser_snapshot`
2. If `<main>` shows "404" but header/footer are present, it's this race (not a network issue)
3. In browser console: `typeof routes['/']` — if it's `'function'` but page still shows 404, microtask won the race. If it's `'undefined'`, your views/*.js scripts never registered the route (different bug)
4. Check the live HTML's `<script src=...>` order with `curl -s <url> | grep script`

**Belt-and-suspenders fix for paranoid codebases** — gate `dispatch()` on a readiness check:
```javascript
// app.js IIFE — wait for all routes to be registered before dispatching
async function init() {
  setLang(currentLang);
  setupLangMenu();
  // ... any other setup ...

  // Wait for views to register. If you know you have N views, count them:
  const expectedRoutes = 5;  // home, login, register, generate, account, pricing — adjust to match
  for (let i = 0; i < 50 && Object.keys(routes).length < expectedRoutes; i++) {
    await new Promise(r => setTimeout(r, 20));
  }

  const hashHandled = await store.handleOAuthCallback().catch(() => false);
  if (hashHandled) showToast(t('toastLoginSuccess'), 'success');
  if (authToken) { try { await store.me(); } catch (e) {} }
  updateUserMenu();
  if (!location.hash) location.hash = '#/';
  dispatch();
}
```

This poll is ugly but bulletproof. Prefer fixing the script tag order first; only fall back to polling if you can't control the load order (e.g. third-party scripts injected dynamically).

**Cache-bust verification after fixing** — when you fix a load-order bug, browsers will keep serving the broken cached JS. Bump the `?v=N` query string on ALL script tags AND the CSS link:
```html
<link rel="stylesheet" href="css/style.css?v=5">
<script src="js/app.js?v=5"></script>
<script src="js/views/home.js?v=5"></script>
...
```

CF Workers without `Cache-Control: no-cache` will cache JS for an hour; the `?v=N` bump forces fresh content. Verify the bump is live with `curl -s https://yoursite.com/ | grep -E 'script src|css/style'` and confirm new version numbers appear.

**SPA startup perf — `defer` script tags + `preload` critical CSS/fonts:**

For SPAs with 5-10+ JS files, default `<script src="...">` blocks HTML parsing and forces serial execution. Add `defer` to all script tags to:
- Download all scripts in parallel
- Execute in document order after HTML is parsed
- Stop blocking first paint

```html
<script defer src="js/i18n.js?v=N"></script>
<script defer src="js/store.js?v=N"></script>
<script defer src="js/views/*.js?v=N"></script>
<script defer src="js/app.js?v=N"></script>
```

**Critical CSS preload (avoid flash of unstyled content):**
```html
<link rel="preload" href="css/style.css?v=N" as="style">
<link rel="stylesheet" href="css/style.css?v=N">
```

**Non-blocking Google Fonts** (fonts.googleapis.com is often 200-500ms; defer with the `onload` trick to keep first paint fast):
```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="preload" as="style" 
      href="https://fonts.googleapis.com/css2?family=Inter:wght@400;700&family=DM+Serif+Display&display=swap"
      onload="this.onload=null;this.rel='stylesheet'">
<noscript><link rel="stylesheet" href="https://fonts.googleapis.com/css2?..."></noscript>
```

The `onload=null;this.rel='stylesheet'` swap converts a `<link>` from `preload` mode to `stylesheet` mode without blocking. Users see fallback fonts briefly, then custom fonts when they load.

**Measured impact** — the ImageGen homepage went from a "feels laggy" first paint to interactive within ~700ms (down from ~2.5s) after adding `defer` to all 9 scripts + preload CSS + non-blocking fonts. The visible perception is the biggest win, not the exact number.

**If app.js exports globals that views DO use at registration time** (Pattern A), keep app.js FIRST. Adding globals to `window.*` in app.js's sync portion before the IIFE is fine. But the moment you `await` before reaching the global exports, you have Pattern B territory and need views first.

### Dropdown toggle + outside-click handler conflict

When implementing a dropdown menu that opens on click and closes on outside click, the `closest()` selector in the outside-click handler must reference an **actual CSS class that exists in the DOM**. Using a non-existent class causes the dropdown to close instantly on every click (including the toggle button):

```javascript
// BROKEN — '.user-dropdown' class doesn't exist in the HTML
document.addEventListener('click', (e) => {
  if (dd && !e.target.closest('.user-dropdown')) {  // always true!
    dd.classList.remove('show');  // closes immediately after toggle opens it
  }
});

// FIXED — use the actual parent container's ID
document.addEventListener('click', (e) => {
  if (dd && !e.target.closest('#userMenu')) {
    dd.classList.remove('show');
  }
});
```

**Sequence of events with the bug:** toggleDropdown() adds 'show' class → dropdown becomes visible → event bubbles to document → handler removes 'show' class → dropdown disappears instantly. User perceives "click does nothing."

### Cloudflare Worker SPA — Cache-Control prevents stale JS

When deploying a multi-file SPA via Cloudflare Worker (where each JS/CSS file is served by the worker's `fetch()` handler), **browsers aggressively cache worker responses** if no `Cache-Control` header is set. Even `?v=N` query params on script tags may not help because the worker strips query strings and the cached response is served.

**Fix:** Add `"cache-control": "no-cache, no-store, must-revalidate"` to every response:

```javascript
return new Response(content, {
  headers: {
    "content-type": getMime(path),
    "cache-control": "no-cache, no-store, must-revalidate"
  }
});
```

If the worker is generated by a build script (deploy_cf.py), add this header in the template builder, not in the generated file (which gets overwritten on every rebuild).

### IndexedDB is browser-local — data does not sync

All data stored in IndexedDB lives exclusively in the user's browser. Two users on different computers will see completely different data. This is a **fundamental architectural constraint** of client-side-only SPAs:

- Registration creates a member record in the registrant's browser only
- Scripts uploaded by admin are only visible to that admin's browser
- Claims made by member A are only visible in member A's browser

**Production fix:** Add a backend (Cloudflare D1/SQLite, Supabase, or a custom API) and replace IndexedDB calls with HTTP requests. The IndexedDB wrapper can be kept as a local cache but the source of truth must be server-side.

### Async render functions

View `render()` functions are async (await IndexedDB). Don't `await` them in `handleRoute` — just call them. If they fail, the page stays blank. Wrap in try/catch or add error fallback.

### `views` global must be initialized in every view file

Files that register views (`views.home = { ... }`, `views.generate = { ... }`) assume `views` is already defined as a global. If the FIRST view file to load doesn't initialize it, all subsequent `views.X = {...}` lines throw `ReferenceError: views is not defined`.

**Two safe patterns** — pick one, apply to EVERY view file:

```javascript
// Pattern A: explicit guard at top of every view file (defensive, but verbose)
views = window.views = window.views || {};
views.foo = { /* ... */ };

// Pattern B: skip the global init, but use `routeView(path, viewObj, 'render')` from router.js
//           (routeView just uses the object reference, not the global)
views.foo = { /* ... */ };
routeView('/foo', views.foo, 'render');
```

**Pattern A failure mode — load-order coupling:**
- `home.js` loads first → `views = window.views = {}`, then `views.home = {...}` ✅
- `login.js` loads second → `views = window.views = {}` (already {}, no-op), then `views.login = {...}` ✅
- `account.js` loads third → NO initialization line, but `views` exists (from home.js) → `views.account = {...}` ✅ (lucky)
- IF home.js is removed or fails to load → `views.account` throws `ReferenceError`

**If a view file's render throws ReferenceError, the route handler fails and dispatch shows the error UI** — but the failure is silent in the sense that the deploy/load didn't catch it. The page just doesn't render the affected view.

**Recommendation:** Always include the `views = window.views = window.views || {};` line at the top of every view file. The redundancy is cheap (one assignment) and prevents load-order fragility.

### Dynamic route matching

For `/detail/:id` routes, match manually in `handleRoute` since hash routing has no param extraction:

```javascript
if (!handler && path?.startsWith('/detail/')) {
  handler = routes['/detail/:id'];
}
```

### Template literal escape pitfalls (i18n + onclick + JSON in views)

When building view templates with template literals, three classes of escape errors bite repeatedly. They produce confusing `SyntaxError: Unexpected identifier 'X'` or `Missing } in template expression` errors that look like parser bugs but are actually string-content issues.

**1. Apostrophe inside single-quoted string inside template literal:**
```javascript
// ❌ "I've" contains a `'` that closes the string early
${currentLang === 'zh' ? '点击购买完成' : 'Click Buy. After I have paid, notify admin.'}
//                                              ^ string closes here, rest is syntax error

// ✅ Either rephrase, escape, or use double quotes
${currentLang === 'zh' ? '点击购买完成' : "Click Buy. After I have paid, notify admin."}
//                                                ^ double-quoted — apostrophe safe
// OR rephrase to "I have paid" without contraction
```

**2. Nested backticks inside template literal break parsing:**
```javascript
// ❌ Nested template literal — inner backticks confuse parser
${`${{expr ? \`yes\` : \`no\`}}`}
//   ^ inner backticks NOT escaped — parser exits outer template early

// ✅ Either use a ternary with regular strings, or escape as \`:
${`${expr ? \`yes\` : \`no\`}`}
//  Note: this works only when the OUTER template is itself nested in another
//  For a flat template, restructure to avoid nested template literals:
${expr ? 'yes' : 'no'}    // ← simple ternary with strings, no nesting
```

**3. Mismatched `}` in conditional attributes inside template literal:**
```javascript
// ❌ `}` inside string closes the ${...} expression early
<button ${disabled ? '' : 'disabled style="..."'}>
//                  ^ string opens here with ', then `}` inside string ends ${
//                  parser thinks: ${... '' : 'disabled style="..."}>

// ✅ Move `}` AFTER the string closes:
<button ${disabled ? '' : 'disabled style="..."'}>
//                  ^                              ^ strings balanced, then } closes
```

**Detection tip:** When Node's `node --check` reports `Missing } in template expression` at a line that LOOKS balanced, the bug is usually inside a string literal. Count `'` and `"` inside each `${...}` expression. The error line number is often off by one (the actual unbalanced `}` was a few lines earlier in a string).

### Removing a section from a chapter-numbered homepage — full cleanup checklist

When the user says "去掉中间这个板块" / "remove this section", the section lives in `js/views/<view>.js` (not `index.html` — `<main>` content is rendered by the view's `render()` template string). Removing only the visible HTML isn't enough; surrounding state needs cleanup too.

**Workflow:**

1. **Locate the section in the view file**, not index.html. Search for the section's distinctive class (`types-section`, `pricing-section`, etc.) or its `t('...')` keys.
2. **Delete the entire `<section>` block** from the template string — every line of the section including its eyebrow/title/subtitle/grid.
3. **Update chapter/sequence numbers in remaining sections**. If sections use a book-chapter aesthetic like `<span class="chap-num">ii / iii</span>`, removing one breaks the count. Recompute (was `ii / iii` after removing one section, becomes `ii / ii`). Search for `chap-num` patterns in the same view file.
4. **Clean up now-unused `const`/`let` declared at the top of `render()`**. If you removed a section that consumed `const types = store.getTypes()`, the const is now dead — delete it. Leaving it is harmless at runtime but pollutes the function and trips lint warnings.
5. **Skip orphaned i18n keys** (the `t('sectionTitle')`, `t('sectionSub')` strings). Removing them means touching 8 language blocks (high risk for low reward). They become inert dead keys but cost nothing.
6. **Skip orphaned CSS rules** (e.g., `.types-section`, `.type-card`, `.type-grid`). Same reasoning — other views may reuse the class, and unused CSS is invisible dead weight.
7. **Verify with `node --check js/views/<view>.js`** before declaring done.

**What NOT to do** (real mistakes from ImageGen session):
- Don't remove the i18n keys "to be tidy" — 8-language edits are a high-blast-radius operation and other views may share keys (`typeCharacter`, `typeScene` are used in admin/generate/account, not just home).
- Don't delete the CSS — class names like `.type-card` might be referenced by other views you haven't grepped.
- Don't forget to update the chap-num — leaving `ii / iii` after removing one section makes the design look broken.

**Verify in browser with hard refresh** (`Ctrl+Shift+R`) — the SPA's `?v=N` cache-bust won't help if the server is also caching. The node --check pass catches syntax errors but not stale-cache issues.

### DOM element existence after render

After `innerHTML` assignment, all previous event listeners are gone. Re-bind in each render or use event delegation on a stable parent.

### IndexedDB base64 size limits

Base64 encoding inflates binary data by ~33%. Storing many large images/videos in IndexedDB can hit quota limits. For production, upload to cloud storage and store URLs instead.

## Authorization & Permission Patterns

For member-based web apps with login/registration, admin/member roles, and content ownership tracking.

### Permission Matrix Template

| Role | Browse | Upload | Download | Modify/Delete |
|------|--------|--------|----------|---------------|
| Unauthenticated | ❌ login gate | ❌ | ❌ | ❌ |
| Member | ✅ | ✅ (tracked) | ✅ | ❌ |
| Admin | ✅ | ✅ | ✅ | ✅ |

### Implementation Patterns

#### 1. Login Gate in View Render

Each view checks `store.isLoggedIn()` at the top of `render()`. If not logged in, show a login prompt instead of content:

```javascript
render: async () => {
  if (!store.isLoggedIn()) {
    document.getElementById('app').innerHTML = `
      <div class="container">
        <div class="empty-state">
          <div class="empty-state-icon">&#128274;</div>
          <h3>请先登录</h3>
          <p>登录后才能查看内容</p>
          <a href="#/login" class="btn btn-primary">登录</a>
          <a href="#/register" class="btn btn-outline">注册</a>
        </div>
      </div>`;
    return;
  }
  // ... normal render code
}
```

#### 2. Admin-Only Actions

Destructive/management buttons guarded by `store.isAdmin()`:

```javascript
const isAdmin = store.isAdmin();

// In template:
${isAdmin ? `
  <button onclick="views.something.delete()">删除</button>
  <button onclick="views.something.edit()">编辑</button>
` : ''}
```

#### 3. Member Tracking on Content

When uploading/creating content, save the uploader's identity:

```javascript
const member = store.currentUser;
const data = {
  ...formData,
  createdBy: member.id,
  createdByName: member.displayName || member.username
};
await store.add(data);
```

#### 4. Critical: Save Password on Registration

The most common bug — `registerMember()` must store the password in IndexedDB:

```javascript
const member = {
  id,
  username: data.username,
  email: data.email,
  displayName: data.displayName || data.username,
  password: data.password,      // ← CRITICAL: don't forget this
  isAdmin: false,
  registeredAt: Date.now()
};
```

Missing `password` means registration succeeds but login always fails silently.

### Common Pitfalls

- **Button condition logic**: When showing Activate/Deactivate buttons for invite codes, the condition for "show activate" is `!c.isActive`, not `c.isActive`. Both buttons showing simultaneously means the condition is inverted.
- **Password field omission**: `registerMember()` destructures `data` but it's easy to forget `password: data.password` in the member object. Login then compares `undefined === password`.
- **Global event reference**: `switchTab` buttons should not use `event.target` without passing the event parameter. Use a selector query instead.
- **onclick closing paren**: Template literals with onclick handlers need the closing `)` inside the quoted string.
- **Undefined property fallback**: Use the field that actually exists as denominator, not a chain of fallbacks that produce misleading percentages.

#### Master Invite Code Pattern (No DB Setup)

For invite-only apps where the admin needs a guaranteed way to register before the database has any invite codes, hardcode a master code:

```javascript
// In store.js, add a constant:
const MASTER_INVITE_CODE = 'YOUR-COMPLEX-CODE-HERE';

// validateInviteCode() — return valid early:
async validateInviteCode(code) {
  if (code === MASTER_INVITE_CODE) {
    return { valid: true, record: { code, isActive: true, isMaster: true } };
  }
  // ... normal DB lookup
}

// useInviteCode() — skip DB write for master:
async useInviteCode(code, memberId) {
  if (code === MASTER_INVITE_CODE) return;
  // ... normal DB update
}
```

**Admin auto-grant**: In the registration form submit handler, check if the invite code matches the master code and pass `isAdmin: true` to `registerMember()`:

```javascript
const isMaster = inviteCode === MASTER_INVITE_CODE;
const member = await store.registerMember({
  username, email, displayName, password,
  isAdmin: isMaster
});
if (isMaster) showToast('管理员注册成功！');
```

Then in `registerMember()`, apply the `isAdmin` flag:
```javascript
isAdmin: data.isAdmin || false,
```

This avoids needing to pre-seed IndexedDB or manually edit the database after registration.

### Roles

Create an `isAdmin` flag on the member object during registration. Check with `store.currentUser.isAdmin`.

**Pitfall — false `isAdmin: false`:** The `registerMember()` method often has `isAdmin: false` hardcoded, ignoring `data.isAdmin`. When passing `isAdmin: true` from the registration form, ensure the store method reads it:
```javascript
isAdmin: data.isAdmin || false,   // ← NOT hardcoded `isAdmin: false`
```

**Workaround for existing users:** If users registered before the `isAdmin` fix was deployed, their DB record has `isAdmin: false`. Add one or more username fallbacks in `isAdmin()`:
```javascript
isAdmin() {
  return this.currentUser && (
    this.currentUser.isAdmin ||
    this.currentUser.username === 'Kairos' ||
    this.currentUser.username === 'echo-7s'
  );
}
```
When asked to "硬编码X为管理员", just add another `this.currentUser.username === 'X'` to the OR chain. This avoids needing to clear IndexedDB and re-register.

#### Cascade Delete Pattern

When deleting a parent entity (e.g., a script/project), cascade deletion to all child records:

```javascript
deleteScript: async (id) => {
  if (!confirm('确定要删除这个剧本吗？')) return;
  
  // 1. Delete the parent
  await store.deleteScript(id);
  
  // 2. Cascade: delete all associated assets
  const assets = await store.getAssetsByScript(id);
  assets.forEach(a => store.deleteAsset(a.id));
  
  // 3. Cascade: delete all associated prompts
  const prompts = await store.getPromptsByScript(id);
  prompts.forEach(p => store.deletePrompt(p.id));
  
  showToast('已删除');
  router.navigate('/');
}
```

**Important:** For IndexedDB, cascading deletes must happen sequentially or via parallel promises (`Promise.all` or `forEach`). There is no ON DELETE CASCADE in IndexedDB.

#### Admin Feature: Member Management View

For admin-only pages that list members and their activity (claims, uploads). Register as a separate route under `/admin/members`.

**route registration (app.js):**
```javascript
registerView('/admin/members', views.adminMembers.render);
```

**Nav link (appears for admins only):**
```javascript
// In updateUserMenu() — add to admin dropdown
if (store.isAdmin()) {
  html += `<button onclick="router.navigate('/admin/members')">${t('navMembers')}</button>`;
}
```

Also add a static nav link with CSS display toggle (see §10a above).

**View implementation pattern:**

```javascript
views.adminMembers = {
  render: async () => {
    if (!store.isLoggedIn() || !store.isAdmin()) {
      router.navigate('/login');
      return;
    }

    // Gather data
    const members = await store.getAllMembers();
    const scripts = await store.getAllScripts();
    
    // Build script lookup: id → title
    const scriptMap = {};
    scripts.forEach(s => { scriptMap[s.id] = s.title; });
    
    // Gather all claims grouped by member
    const memberClaimsMap = {};
    for (const m of members) {
      const claims = await store.getClaimedByMember(m.id);
      memberClaimsMap[m.id] = claims;
    }

    // Render member cards
    let html = members.map(m => {
      const claims = memberClaimsMap[m.id] || [];
      // Group claims by scriptId
      const claimGroup = {};
      claims.forEach(c => {
        if (!claimGroup[c.scriptId]) claimGroup[c.scriptId] = [];
        claimGroup[c.scriptId].push(c.episodeNum);
      });

      return `
      <div class="member-card" style="background:var(--bg-card);padding:20px;margin-bottom:12px;border-radius:var(--radius);">
        <div style="display:flex;align-items:center;gap:12px;">
          <div style="width:36px;height:36px;border-radius:50%;background:var(--accent);color:#fff;
                      display:flex;align-items:center;justify-content:center;font-weight:700;">
            ${(m.displayName || m.username)[0].toUpperCase()}
          </div>
          <div style="flex:1;">
            <div style="font-weight:600;">${escHTML(m.displayName || m.username)}</div>
            <div style="font-size:0.78rem;color:var(--text-muted);">@${escHTML(m.username)}</div>
          </div>
          <div style="text-align:right;font-size:0.75rem;color:var(--text-muted);">
            <div>${new Date(m.registeredAt).toLocaleDateString()}</div>
            ${m.isAdmin ? '<span style="color:var(--accent-light);">Admin</span>' : ''}
          </div>
        </div>

        ${claims.length > 0 ? `
        <div style="border-top:1px solid var(--border);padding-top:12px;margin-top:12px;">
          <div style="font-size:0.78rem;font-weight:600;color:var(--text-muted);margin-bottom:8px;">认领情况:</div>
          ${Object.keys(claimGroup).map(sid => `
            <div style="display:flex;gap:8px;align-items:center;font-size:0.82rem;margin-bottom:4px;">
              <span>📄 ${escHTML(scriptMap[sid] || '(剧本已删除)')}</span>
              <span style="color:var(--text-muted);font-size:0.75rem;">
                第 ${claimGroup[sid].sort((a,b) => a-b).join('、')} 集
              </span>
            </div>
          `).join('')}
        </div>` : `
        <div style="border-top:1px solid var(--border);padding-top:12px;margin-top:12px;font-size:0.78rem;color:var(--text-muted);">
          暂无认领
        </div>`}
      </div>`;
    }).join('');

    document.getElementById('app').innerHTML = `
      <div class="container">
        <div style="max-width:1000px;margin:0 auto;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:32px;">
            <h1>会员管理</h1>
            <a href="#/admin" class="btn btn-outline btn-sm">&larr; 管理面板</a>
          </div>
          <div style="background:var(--bg-card);padding:16px 24px;margin-bottom:24px;border-radius:var(--radius);">
            会员总数: <strong>${members.length}</strong>
          </div>
          ${html}
        </div>
      </div>`;
  }
};
```

**Key patterns:**
- Admin guard at top: redirects non-admins to login
- Data aggregation: fetch all members + all scripts + per-member claims in parallel
- Script title lookup via Map (avoids N+1 queries on each card)
- Claims grouped by `scriptId`, then episode numbers shown as comma-separated list
- Deleted scripts shown as "(剧本已删除)" — graceful degradation

## Validation Checklist

1. **Node syntax check**: `node --check js/*.js` for each JS file
2. **Browser load**: Navigate to file or local server
3. **Navigation**: Click each nav link, verify route change
4. **Language toggle**: Switch zh/en, verify all text updates
5. **Form submit**: Fill and submit, verify IndexedDB write
6. **Detail view**: Click card, verify data loads
7. **Responsive**: Check at 768px breakpoint

## Real-Time Generation Polling and Per-Mode State (image/video apps)

Multi-mode apps (txt2img / img2img / txt2vid / img2vid) share global variables like `selectedStyle`, `audioFiles`, `lastOutputUrl`. When user switches modes, previous mode's state bleeds into new one. **Fix — per-mode caching with save/restore in the mode-switch function**:

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

New modes with no saved state reset to defaults (e.g., style → "无风格", output → default placeholder text). Forgetting reset = "why did X leak into Y" bug.

### Server-side polling — don't re-poll on client

When backend POST handler already polls upstream and downloads result (returning `outputUrl`), client **must not** re-poll a status endpoint. Re-poll tries to download the file again, fails (upstream URL expired), silently keeps returning `"running"` — user sees "生成中..." forever.

```javascript
const result = await requestJson('/api/video/generate', { method: 'POST', body: ... });
if (result.outputUrl) {  // Server already handled everything
  videoOutput.src = result.outputUrl;
  videoOutput.classList.remove("hidden");
}
```

### Generating across mode switches

When async generation is in progress and user switches modes, completion callback must not update the **current** mode's display. Instead, cache the result for the mode that triggered the generation:

```javascript
let generatingForMode = null;
async function generate() {
  generatingForMode = currentMode;   // record which mode started this
  // ... async work ...
  if (generatingForMode !== currentMode) {
    // User switched away — cache for original mode
    perModeOutput[generatingForMode] = { lastOutputUrl, outputType, ... };
    return;
  }
  // Still the same mode — update display normally
}
```

Always reset `emptyOutput.innerHTML = DEFAULT_EMPTY_HTML;` on mode switch before checking cached output — otherwise previous mode's "生成中..." persists.

### Multi-page Node.js server pattern (image/video test tools)

For tools with Node.js backend + multi-page frontend (e.g., test apps):

| Launcher | 入口 | 主页规则 | 实际画布文件 |
|---|---|---|---|
| Node `start-windows.bat` / `node app/server.js` | `app/server.js` | `/` → `home.html` (4 模式卡片页) | `/canvas` 路由硬编码指向 `Kairos_canvas_<快照>.html` |
| Python `python serve_canvas_5788.py` | `serve_canvas_5788.py` | `/` **直接就是画布** (no home 入口) | `do_GET` 里把 path 改写到指定 HTML |

**Both launchers listen on same port (e.g., 5788) — pick one, kill the other**. Any "home page" change must modify BOTH entry files; just changing Node leaves Python-spawned instance unchanged.

**Port conflict gotcha**: bash `PORT` env var may be set to other value (e.g., 8648 for Hermes Web UI). Always launch with explicit `PORT=5788 node app/server.js`.

### Silent JS crash from orphan element removal

**Symptom**: After deleting an HTML element (e.g., `id="refUploadBtn"`), the entire `<script>` block silently fails — all globals undefined (`typeof S === 'undefined'`), no console error visible.

**Root cause**: HTML element deletion wasn't paired with removal of `document.getElementById` + all `addEventListener` references. JS throws `TypeError` mid-execution; **subsequent code never runs**.

**Symptom signature**: model dropdown empty, no config logs in page, but HTML structure looks normal.

**Fix**: Always grep for the element's ID across JS files BEFORE deleting HTML; remove all references.

## Community / Collaborative SPA Patterns (投票 + 认领 + Admin)

For SPAs with member-based voting, episode claiming, and admin features (e.g., collaborative short drama platforms):

### Episode claim system (max 2 per episode)

- Each episode can be claimed by up to **2 different members**. Same member can't claim twice.
- DB: drop unique index `['scriptId', 'episodeNum']` — supports up to 2 claims, no FK constraint to break
- Frontend check: `epClaims.length >= 2` blocks third claim; `epClaims.some(c => c.memberId === memberId)` prevents same-member double-claim
- After claiming, member sees their claimed episode content area but **not the full script** (admin-only). Use `prompt()` to edit claimed episode content.

### Permission matrix

| Role | Browse | Upload | Download | Modify/Delete |
|------|--------|--------|----------|---------------|
| Unauthenticated | ❌ login gate | ❌ | ❌ | ❌ |
| Member | ✅ | ✅ (tracked) | ✅ | ❌ |
| Admin | ✅ | ✅ | ✅ | ✅ |

**Critical bugs**:

- **Password field omission**: `registerMember()` must store `password: data.password` — common bug is destructuring `data` but forgetting password in the member object. Login then compares `undefined === password` and silently fails.
- **`isAdmin: false` hardcoded**: `registerMember()` often has `isAdmin: false` hardcoded, ignoring `data.isAdmin`. Pass `isAdmin: true` from registration form when master code matches.
- **API login error handling**: `store.authenticateMember()` MUST try/catch `_api()` call. If API returns 401 (wrong password), `_api()` throws Error. Without catch, `login.js` `submit` silently crashes — no toast, no UI reaction.

```javascript
async authenticateMember(username, password) {
  try {
    const result = await _api('POST', '/members/login', { username, password });
    if (result.success) {
      await this.loginWithToken(result.token, result.member);
      return result.member;
    }
    return null;
  } catch (e) {
    return null;  // 让 login.js 显示"用户名或密码错误"
  }
}
```

### Script load order matters

JS imports must follow dependency order:
```
i18n.js → store.js → router.js → app.js → views/*.js
```

- `app.js` MUST come BEFORE all view scripts. Views reference global functions defined in `app.js` (`updateUserMenu()`, `showToast()`, etc.). If a view script loads before app.js, calling those functions silently fails — click handler does nothing, no console error.
- Cache busting: `?v=N` query params on script tags. For CF Worker inlined bundles, add `cache-control: no-cache, no-store, must-revalidate` header — else browser caches old JS.

### Hardcoded admin fallback

For invite-only apps without pre-seeded DB, hardcode master code + username fallback:

```javascript
const MASTER_INVITE_CODE = 'YOUR-COMPLEX-CODE-HERE';

async validateInviteCode(code) {
  if (code === MASTER_INVITE_CODE) {
    return { valid: true, record: { code, isActive: true, isMaster: true } };
  }
  // ... normal DB lookup
}

isAdmin() {
  return this.currentUser && (
    this.currentUser.isAdmin ||
    this.currentUser.username === 'Kairos' ||
    this.currentUser.username === 'echo-7s'
  );
}
```

For existing users before `isAdmin` fix deployed, add username fallbacks in `isAdmin()` to avoid clearing IndexedDB.

### Admin nav link visibility (CSS + JS toggle)

```html
<!-- index.html — link hidden by default -->
<a href="#/admin/members" class="nav-link nav-members-link">会员管理</a>
```

```css
.nav-members-link { display: none; }
```

```javascript
// app.js updateUserMenu() — toggle based on role
const membersLink = document.querySelector('.nav-members-link');
if (membersLink) {
  membersLink.style.display = store.isAdmin() ? '' : 'none';
}
```

Non-admins never see the link (CSS hides). Link appears immediately when admin logs in (no dynamic HTML injection).

### IndexedDB version migration on upgrade

When adding/removing object stores or indexes in already-deployed app:

```javascript
const req = indexedDB.open('MyAppDB', 3);  // bump version
req.onupgradeneeded = (e) => {
  const db = e.target.result;
  const oldVer = e.oldVersion;  // 0 = new DB

  if (!db.objectStoreNames.contains('items')) {  // create stores
    db.createObjectStore('items', { keyPath: 'id' });
  }
  // v2 → v3 migration
  if (oldVer < 3 && db.objectStoreNames.contains('claims')) {
    const store = e.target.transaction.objectStore('claims');
    if (store.indexNames.contains('oldUniqueIndex')) {
      store.deleteIndex('oldUniqueIndex');
    }
    store.createIndex('newIndex', 'newField', { unique: false });
  }
};
```

Rules:
- Never decrement version (browsers only migrate forward)
- Only create stores/indexes when they don't exist (guarded by `contains`)
- Only drop indexes when `oldVersion < N`
- Deleting unique index removes constraint — add app-level validation to replace it

### Critical pitfalls

- **Card button event.stopPropagation()**: in-card delete buttons must stop propagation or trigger card navigation
- **Copy fallback**: `navigator.clipboard.writeText` may fail in non-HTTPS — keep `document.execCommand('copy')` fallback
- **Worker source vs deployed**: `worker.js` is old template, `worker_deploy.js` is auto-built. Modify source, run build, deploy — direct edits to `worker_deploy.js` get overwritten
- **Cache-Control missing on Worker**: add `no-cache, no-store, must-revalidate` to all responses — else browsers cache old JS
- **updateUserMenu not auto-call**: must call manually after login/logout AND on every router.navigate()

## Related Skills

- `frontend-patching` — for editing existing single-file web apps
- `kairos-canvas-development` — for the specific Kairos Canvas project
- `software-architecture-design` — for system-level design decisions

## Reference Files

- `references/shortdramaforge-project.md` — deployment notes, domain/worker mapping, and permission system details for ShortDramaForge
- `references/3d-director-domain.md` — 3D Director domain concepts for image/video tools
- `references/api-endpoints.md` — API endpoint catalog for image/video test tools
- `references/frontend-patterns.md` — per-mode state management, audio upload, navigation patterns
- `references/gaia-media-api-absorbed.md` — Gaia Media API integration details
- `references/homepage-development.md` — homepage layout, 4K detection, icon PNG processing
- `references/kairos-canvas-architecture.md` — Kairos Canvas architecture, renderNode + ENHANCEMENTS V2, per-mode state
- `references/silent-js-crash-pitfall.md` — silent JS crash diagnosis (orphan element removal)
- `references/indexeddb-patterns.md` — IndexedDB version upgrade + cascade delete patterns
- `references/shortdrama-forge-infra.md` — ShortDramaForge deployment, IndexedDB DB v4 schema, multi-agent admin
