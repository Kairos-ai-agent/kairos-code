---
name: "cloudflare-worker-fullstack"
description: "|"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\.archive\\cloudflare-worker-fullstack\\SKILL.md"
---
# Cloudflare Worker Full-Stack App

This is the playbook for shipping a production web app entirely on Cloudflare's
edge: Workers (TypeScript/ES module) for logic, D1 (SQLite) for persistence,
R2 for blob storage, and a multi-file vanilla-JS SPA bundled as base64 in the
Worker. No framework runtime, no wrangler CLI required — everything goes
through CF's REST API from a single Python deploy script.

## When to use

- Shipping a real web app (auth, billing, persisted state) that needs to be
  reachable on a real domain, fast, with zero server ops.
- You don't have wrangler installed and don't want to npm-install 200MB of
  tooling just to deploy a static frontend + small API.
- The product is small enough to fit in one Worker (~10MB compressed) and
  reads/writes via D1/R2 only.
- You need real OAuth (Google / GitHub / etc.) plus an end-user API-key flow,
  PayPal orders, and image generation.

## Architecture (one Worker, one D1)

```
yourdomain.com
    │
    ├── GET /           → Worker serves inlined index.html
    ├── GET /css/*.css  → Worker decodes base64 FILES dict → returns text/css
    ├── GET /js/*.js    → same pattern
    └── GET /api/*      → Worker handles (auth, account, orders, generate)
                          → D1 (SQL) / R2 (blob) / outbound HTTP (PayPal, OpenAI)

[deploy.py]  ── base64 ─→  worker_deploy.js (generated)
                ── PUT  ─→   CF API: accounts/{acc}/workers/scripts/{name}
                ── bind ─→   D1, R2, plain_text secrets (GOOGLE_CLIENT_ID, etc.)
                ── PUT  ─→   accounts/{acc}/workers/domains   ← custom domain
```

The single Worker script `worker_deploy.js` contains:
- A generated prefix with `const FILES = { ... }` (base64) and `const ROUTES = { ... }`.
- A `decode(b64)` helper that uses `TextDecoder` (UTF-8 safe for Chinese filenames).
- Your hand-written `worker_api.js` (export default { async fetch(request, env, ctx) }).

The `fetch` handler first checks static routes, then falls through to the
`/api/*` switch. If neither matches, returns 404.

## The deploy.py skeleton (no wrangler needed)

See `templates/deploy_cf.py` — a known-good reference. Key points:

1. Read all files under `css/`, `js/`, `index.html`. Base64-encode and emit
   `const FILES = { "css_style_css": "<b64>", ... }`. The key is `path/to/file`
   with `/` → `_` and `.` → `_` (e.g. `js/views/home.js` → `js_views_home_js`).
2. Emit `const ROUTES = { "/css/style.css": "css_style_css", ... }`.
3. Concatenate the static prefix with `worker_api.js` (skip leading comments
   so you don't double-define `export default`).
4. Validate with `subprocess.run(['node', '--check', out_path])`. **Always do
   this before uploading** — multipart uploads fail silently with HTTP 400 if
   the JS is broken.
5. PUT to `https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/workers/scripts/{SCRIPT_NAME}`
   with multipart/form-data. **`filename` in the part must equal `main_module`
   in metadata** — mismatch causes HTTP 400 with no useful error message.
6. Bindings array contains: d1, plain_text (secrets), r2_bucket. Each
   binding name becomes `env.NAME` in the worker.

### Multipart body shape (the part that always trips people)

```
--BOUNDARY
Content-Disposition: form-data; name="metadata"
Content-Type: application/json

{"main_module": "worker_deploy.js", "bindings": [...]}
--BOUNDARY
Content-Disposition: form-data; name="worker_deploy.js"; filename="worker_deploy.js"
Content-Type: application/javascript+module

<worker_js bytes>
--BOUNDARY--
```

`urllib` builds this in ~30 lines. Use a random boundary, build the bytes
yourself, and send as `data=body.encode("utf-8")` with `Content-Type: multipart/form-data; boundary=...`.

## CF API token scopes — read this BEFORE you deploy

CF API tokens have **independent scope bits** for Accounts vs Zones. A token
that can `PUT /accounts/{}/workers/scripts/{}` **cannot** `POST /zones/{}/workers/routes`.
Symptoms: `403 Forbidden` with code 10000 "Authentication error" on zone calls,
200 OK on worker/script calls.

**Two unrelated APIs control the same thing:**

| Want to set | Endpoint | Scope needed |
|-------------|----------|--------------|
| Deploy Worker code | `PUT /accounts/{acc}/workers/scripts/{name}` | Account / Workers Scripts / Edit |
| Bind worker to a custom domain | `PUT /accounts/{acc}/workers/domains` (body: `{hostname, service, zone_id}`) | Account / Workers Scripts / Edit |
| Create a route in a zone (old API) | `POST /zones/{zid}/workers/routes` | **Zone / Workers Routes / Edit** |
| Enable workers.dev subdomain | `POST /accounts/{acc}/workers/scripts/{name}/subdomain` `{enabled: true}` | Account / Workers Scripts / Edit |

If your token only has Account scope, you'll be stuck: workers deploy fine,
workers.dev works, but you cannot touch zone-level routes. Workarounds:

1. **Best**: Create a token with template "Edit Cloudflare Workers" (covers both).
2. **Or**: Bind the custom domain via the Account-level endpoint
   `PUT /accounts/{acc}/workers/domains` (this is what `templates/deploy_cf.py`
   uses). It's idempotent — PUT with same hostname updates `service` field, or
   creates if missing. **Do NOT `DELETE` first** — if you can't recreate
   afterwards (zone routes 403), you've broken your domain with no recovery.
3. As a last resort, ask the user to flip the route manually in Dashboard.

## D1 schema migrations — `CREATE TABLE IF NOT EXISTS` is a trap

D1's `CREATE TABLE IF NOT EXISTS` does NOT alter existing tables. If you
deploy v2 of your schema with `users ADD COLUMN oauth_google TEXT`, the column
will NOT be added if `users` already exists. **You'll get "no such column"
errors at runtime** that look like code bugs but are actually schema drift.

**Idempotent migration pattern** (used in `templates/migrate_d1.py`):

1. Split schema.sql into individual statements.
2. For `ALTER TABLE ... ADD COLUMN` statements, expect `duplicate column`
   errors and treat them as success. D1 query API runs ONE statement per call —
   `multi-statement` is rejected, so you must loop.
3. Verify with `PRAGMA table_info(<table>)` afterwards.

```python
for sql in migrations:
    r = d1_query(sql)
    if not r.get('success'):
        msg = r.get('errors', [{}])[0].get('message', '')
        if 'duplicate column' not in msg.lower():
            print(f'FAIL: {msg}')
```

## Custom OAuth (Google, GitHub) in Workers — the minimal flow

The classic `authorization code` flow, all in the Worker:

```
GET  /api/auth/<provider>          → 302 to provider's /authorize URL
GET  /api/auth/<provider>/callback → exchange code, fetch profile, create user,
                                       issue session token, redirect back
POST /api/auth/logout              → delete session row
GET  /api/auth/me                  → return current user (Bearer token)
```

Key details:

- **State**: Store a random token in KV with `expirationTtl: 600` (10 min) to
  prevent CSRF. Check it in the callback handler, delete after use. If you
  have no KV binding, accept any state of length > 10 (less secure).
- **Secrets in Worker**: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
  `GOOGLE_REDIRECT_URI` (default `https://yourdomain.com/api/auth/<provider>/callback`).
  Pass as `plain_text` bindings.
- **Token issuance**: Insert into `sessions(token, user_id, expires_at)`.
  Return `{token, user}` to client. Client stores in `sessionStorage` and
  sends `Authorization: Bearer <token>` on every API call.
- **User lookup order** for findOrCreateOAuthUser:
  1. Existing user by `oauth_<provider> = profile_id`.
  2. Existing user by `email` — link OAuth to the email-matched account.
  3. Create new user (no signup bonus unless you intentionally set credits > 0).

## Custom image generation providers (BYOK)

Users bring their own OpenAI-compatible API keys. Store them encrypted in D1:

```sql
CREATE TABLE user_api_keys (
  provider TEXT NOT NULL,
  base_url TEXT DEFAULT '',
  model_name TEXT DEFAULT '',
  encrypted_key TEXT NOT NULL,
  UNIQUE(user_id, provider)
);
```

Encryption: AES-GCM with a Worker secret (`AUTH_SECRET`) padded to 32 bytes:

```js
const key = await crypto.subtle.importKey(
  'raw', new TextEncoder().encode(secret.padEnd(32,'0').slice(0,32)),
  { name: 'AES-GCM' }, false, ['encrypt']
);
const iv = crypto.getRandomValues(new Uint8Array(12));
const ct = await crypto.subtle.encrypt({ name:'AES-GCM', iv }, key,
                                       new TextEncoder().encode(plaintext));
// store: base64(iv + ct)
```

Forwarding on generate: `POST {base_url}/images/generations` with body
`{model, prompt, n:1, size, response_format:'url'}`. Parse `{data:[{url}]}`
(or `data:[{b64_json}]`). On failure, refund the credits.

**When image generation fails, the FIRST thing to check is D1's
`generations.error_message`** — it's the only authoritative log of what
the upstream provider actually returned. See
`references/image-generation-failure-diagnosis.md` for the full
4-step ladder.

## Rate limiting in Workers

Sliding window via a `rate_log(user_id, action, created_at)` table. On each
mutating call:

```js
const n = await env.DB.prepare(
  'SELECT COUNT(*) AS n FROM rate_log WHERE user_id=? AND action=? AND created_at>?'
).bind(user.id, action, now()-60).first().n;
if (n >= MAX_PER_MINUTE) return errResp('Rate limited', 429);
await env.DB.prepare('INSERT INTO rate_log ...').bind(...).run();
// periodically clean entries older than 5 min
```

This is cheap, simple, and survives Worker restarts. No KV counter needed.

## PayPal Orders (Checkout)

For one-time purchases, use the v2 Orders API:

```
POST {base}/v1/oauth2/token   → get access_token (Basic auth, grant_type=client_credentials)
POST {base}/v2/checkout/orders → create order, get approval_url
```

base is `https://api-m.sandbox.paypal.com` for sandbox,
`https://api-m.paypal.com` for live. `PAYPAL_ENV` secret controls this.

For `mock` mode (no PayPal creds), return `{mock: true}` from
`createPaypalOrder` so the UI can show a "PayPal not configured" state.

## Vanilla-JS SPA pattern that pairs with this Worker

Files:
- `index.html` — single `<div id="app">` mount point, references 5 JS files.
- `js/i18n.js` — `I18N = { en: {...}, zh: {...} }`, `t(key)` returns
  `I18N[currentLang][key] || key`, `setLang(lang)` re-renders via hashchange.
- `js/store.js` — `apiCall(path, opts)` with auto Bearer header + JSON.
- `js/router.js` — `routes = {}`, `route(path, handler)`, `dispatch()` reads
  `location.hash` and runs the handler.
- `js/app.js` — wires `langToggle.click → toggleLang`, calls `dispatch()` at boot.
- `js/views/<name>.js` — each defines `views.<name> = { async render(params) {...} }`
  and registers via `routeView(path, view, methodName)`.

**The `this` binding pitfall**: if you write `route('/x', views.foo.render)`,
the router will call `handler(params)` with `this === undefined` (strict mode),
so any `this.bindEvents(...)` call inside the view crashes with
"is not a function". Use:

```js
function routeView(path, view, methodName) {
  routes[path] = (...args) => view[methodName].apply(view, args);
}
```

…and register as `routeView('/x', views.foo, 'render')`. Critical for views
that call internal methods via `this`.

**Language toggle re-render**: `setLang` must trigger a full re-render because
views call `t('key')` at render time, baking strings into HTML. Dispatching
a `hashchange` event is the cleanest trigger — router.js already listens to
it. Track `_lastDispatchHash` so re-rendering the same route (language toggle)
doesn't scroll to top.

## Common pitfalls (one-line checklist)

- [ ] `filename` in multipart part == `main_module` in metadata, else HTTP 400.
- [ ] `node --check worker_deploy.js` before PUT, else deploys with broken JS.
- [ ] D1 schema migrations: split statements, tolerate `duplicate column`.
- [ ] CF token scope: Account vs Zone are independent. Custom domains go
      through Account scope (`/accounts/{}/workers/domains`), routes need
      Zone scope (`/zones/{}/workers/routes`).
- [ ] Never DELETE a custom domain record before you can PUT it back. If the
      token can't recreate it, you've orphaned your domain. PUT is idempotent
      and can update OR create — prefer it.
- [ ] `route('/x', view.method)` loses `this` — use `routeView` wrapper.
- [ ] `'}` inside `${cond ? '' : 'value'}` inside a template literal breaks
      parsing — close the string before the expression closes.
- [ ] `try { window.dispatch(...) }` doesn't work if `window.dispatch` was
      never assigned (it's not a DOM method by default). Prefer triggering
      `hashchange` via `dispatchEvent`.
- [ ] Read the `references/` directory for full transcripts of these pitfalls.

## Files in this skill

- `templates/deploy_cf.py` — known-good deploy script (multipart upload + bindings).
- `templates/migrate_d1.py` — idempotent schema migration pattern.
- `templates/worker_api_template.js` — fetch handler skeleton with auth + D1.
- `references/cf-api-endpoints.md` — exhaustive endpoint catalog with token
  scope notes.
- `references/oauth-flow.md` — full Google/GitHub OAuth implementation.
- `references/byok-image-providers.md` — Agnes / OpenAI / Qwen / custom URLs.
- `references/image-generation-failure-diagnosis.md` — 4-step ladder to
  diagnose "image won't generate" via D1 `generations.error_message`,
  OpenAI-compatible provider liveness checks, and the `SF_<KEY>` re-deploy
  pattern.
- `references/pitfalls.md` — debugging notes from past sessions.
- `references/pricing-package-changes.md` — end-to-end workflow for changing
  credit pricing (per-action cost in `PLATFORM_CREDIT_COST` + purchase tiers
  in `pricing_packages`), covering the 3 sources of truth and the
  DELETE+INSERT D1 mutation pattern (because seed uses `INSERT OR IGNORE`).

## See also

- For pure SPA design system (CSS variables, Hero patterns), use the
  `frontend-design` umbrella — this skill assumes you have a CSS file ready.
- For D1 query patterns beyond migrations, see the schema in `templates/`.