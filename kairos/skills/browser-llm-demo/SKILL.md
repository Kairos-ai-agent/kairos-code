---
name: "browser-llm-demo"
description: "|"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\you\\AppData\\Local\\hermes\\skills\\.archive\\browser-llm-demo\\SKILL.md"
---
# Browser-based LLM Demo

## When to use this skill

Trigger phrases: "build me a demo that calls [LLM]", "single HTML that uses the API", "browser can't
reach the LLM", "Failed to fetch on LLM call", "stuck on initializing", "wait until loaded forever".

Don't trigger for: a normal web app with a backend, an LLM evaluation harness, server-side tooling.

## The 4-step pattern (proven on storyboard-previs 2026-08)

### Step 1: Detect API style from URL

```javascript
const isAnthropic = /\/anthropic/i.test(baseUrl) || /anthropic\.com/i.test(baseUrl);

const url = isAnthropic
  ? `${baseUrl.replace(/\/+$/, '')}/v1/messages`
  : `${baseUrl.replace(/\/+$/, '')}/v1/chat/completions`;

const headers = { 'Content-Type':': 'application/json' };
let body;
if (isAnthropic) {
  headers['x-api-key'] = cfg.api_key;
  headers['anthropic-version'] = '2023-06-01';
  body = JSON.stringify({ model, max_tokens: 4096, messages: [{ role: 'user', content: prompt }] });
} else {
  headers['Authorization'] = `Bearer ${cfg.api_key}`;
  body = JSON.stringify({ model, messages: [{ role: 'user', content: prompt }] });
}
```

Response parsing:
- Anthropic: `data.content?.map(c => c.text || '').join('')`
- OpenAI:    `data.choices?.[0]?.message?.content`

### Step 2: Local CORS proxy for cloud LLMs

Most cloud LLM APIs don't expose `Access-Control-Allow-Origin` for browsers. Fix: 10-line FastAPI
proxy on `localhost:8765` that forwards `/v1/...` → upstream with permissive CORS.

See `scripts/local_proxy.py` for the template.

User experience: demo base_url is `http://localhost:8765`, user starts proxy with
`python proxy.py` then opens the HTML. Curl test against the real cloud URL still works for
verification; browser path goes through localhost.

### Step 3: Local `vendor/` instead of CDN

`importmap` + ES module + `https://unpkg.com/...` fails silently in 3 common scenarios:
- Headless browsers (browserbase etc.) — ES module on file:// may not execute
- China networks — unpkg.com frequently times out
- Offline machines

Reliable pattern:
```
project/
├── index.html
├── vendor/
│   └── three.min.js        # downloaded once via curl
└── proxy.py
```
Reference: `<script src="./vendor/three.min.js"></script>` — UMD, no module system needed.

### Step 4: Progress checkpoints at every step

Status `<div>` updated by every step. When "stuck on initializing" hits, you know exactly which step failed:

```javascript
const _step = (msg) => {
  const el = document.getElementById('status');
  if (el) el.textContent = msg;
};
_step('loading engine…');
const THREE = window.THREE;
_step('engine ready');
_step('init scene');
init();
_step('loading sample');
loadSample();
_step('▶ playing');
```

Wrap the whole thing in try/catch that paints the status red with the error message. Don't let
silent failures leave a user staring at "initializing…" for 30 seconds.

## Pitfalls (load-bearing — re-read before every new demo)

### ❌ Backticks in template literals silently break scripts

WRONG:
```javascript
const SYSTEM_PROMPT = `...don't use \`\`\`json blocks...`;
//                                                          ^^^^^^^^
// Browser parses first \` as end of template literal, "json" as identifier → SyntaxError
// ENTIRE <script> block is skipped — no console error, just frozen UI
```

RIGHT (describe in prose):
```javascript
const SYSTEM_PROMPT = `...don't use triple-backtick + json + triple-backtick blocks...`;
```

RIGHT (escape):
```javascript
const SYSTEM_PROMPT = `...don't use \`\`\`json\`\`\` blocks...`;
```

**Symptom**: status stays on HTML default value forever. Adding `console.log` inside the script
shows nothing logged. Browser DevTools → Console shows nothing (it's a parse error, not runtime).

**Detection before shipping**: extract the inline `<script>` content to a `.js` file, run
`node -c file.js`. Node is strict; browser is forgiving but execution stops silently.

### ❌ Headless browser screenshots ≠ real-browser validation

browserbase (and most cloud browser tools) have inconsistent ES module / importmap / file:// CORS
handling. A demo that "looks broken" in headless may work fine in Chrome on the user's laptop,
and vice versa. Don't conclude a bug from headless alone — always also `node -c` for syntax,
and ask user to test in their actual browser.

### ❌ importmap + ES module on file:// is fragile

Just use plain `<script src="./vendor/lib.umd.min.js">` and read `window.Lib`. Saves hours.

### ❌ "等待加载" as initial status reads as a bug

User feedback (2026-08-14): a page that opens to "等待加载" / "Waiting for input" looks broken
even when it isn't. Either:
- Auto-trigger something on load (load sample + auto-play), OR
- Show actionable instructions ("点「示例」体验 / 「LLM 解析」处理你的分镜")

### ❌ Don't conflate different LLM API styles by URL

Mistakes observed (2026-08):
- `/v1/chat/completions` for MiniMax → 404 (MiniMax only does Anthropic-compatible)
- `/anthropic/v1/chat/completions` → 404 (mixed style — Anthropic path with OpenAI resource)
- Only `/anthropic/v1/messages` works for MiniMax
- Hermes's `~/.hermes/.env` shows `MINIMAX_CN_BASE_URL=https://api.minimaxi.com/v1` — the SDK
  internally rewrites this to `/anthropic/v1/messages`. Don't copy the bare URL into a browser
  config; copy the SDK path convention.

## Verification checklist (run before declaring done)

- [ ] `node -c` on extracted inline script content → syntax OK
- [ ] Test "test connection" button reports HTTP status (not `Failed to fetch`)
- [ ] Error messages show: URL, HTTP status, response body snippet, detected API style
- [ ] LLM call surfaces a clear error if JSON parse fails (position + context window)
- [ ] No `unpkg.com` / `cdn.jsdelivr.net` references in shipped HTML
- [ ] Auto-loads sample on page open (or shows clear instructions)
- [ ] Progress status updates visible at each step
- [ ] CORS proxy starts cleanly: `curl http://localhost:8765/` returns the proxy's status JSON

## File layout that works

```
project/
├── index.html              # main app (single file)
├── proxy.py                # CORS proxy on 8765
└── vendor/
    └── three.min.js        # ~655KB, downloaded once via curl
```

## Related skills

- `agentic-coding` — for the surrounding dev workflow / acceptance criteria
- `debug-pro` — for when "stuck on initializing" turns into a deeper investigation
- `using-superpowers` — meta, but read first