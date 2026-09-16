---
name: "cloudflare-deployment"
description: "Deploy and operate Cloudflare Workers + D1 + R2 + KV projects — covers Pages (Upload assets) vs Workers (API deploy) paths, project setup, API Token management, deploy scripts, JS syntax validation, c"
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\you\\AppData\\Local\\hermes\\skills\\.archive\\cloudflare-deployment\\SKILL.md"
---
# Cloudflare Deployment + Runtime Diagnostics

Consolidated umbrella for deploying single-file HTML SPAs and full Workers (Worker + D1 + R2 + KV) to Cloudflare. Covers the full lifecycle: project creation, custom domain binding, API Token management, automated deploy scripts, syntax validation, troubleshooting, AND runtime diagnostics against live D1.

> **For AI image SaaS specifics** (BYOK provider abstraction, OAuth inside Worker, credits/PayPal, watermark, anti-scraping) → see `ai-image-saas-on-cloudflare` skill. That skill references this one for deploy mechanics.

> **⚠️ RECONSTRUCTION NOTE**: This skill was accidentally deleted in a session review (July 2026) and rebuilt from a ~1500-char cache preview. The original was ~99KB. Now expanded again with patterns from inlined-bundle architecture + D1 migration pitfalls. Treat what's here as the core essentials; reach for `ai-image-saas-on-cloudflare` for AI SaaS specifics and add to this skill as new patterns surface.

## Which Path to Use

| Path | Method | Automation | Best For |
|------|--------|-----------|----------|
| **Pages — Upload assets** | Manual via Dashboard UI | User-driven (Bot Protection blocks browsers) | One-time deploy, non-technical users |
| **Workers — API deploy** | PUT via Cloudflare API | Fully automated | CI/CD, repeat deploys, programmatic control |

> ⚠️ Cloudflare Dashboard has strong Bot Protection — browser automation tools CANNOT navigate it reliably. Always use API for deploys.

## Runtime Diagnostics: Direct D1 Query via API

**This is the most underused technique and worth its own section.** When user reports "I can't log in" / "my data is missing" / "user X doesn't exist" — **query D1 directly first** before debugging frontend/password/UI issues.

### Why
- Login failures are usually one of three things: (1) account doesn't exist, (2) password mismatch, (3) frontend bug. (1) is fastest to confirm via direct DB lookup.
- Users misremember their own username constantly. "I tried Kairos2026" → actual username was `Kairos` (admin). Searching D1 reveals this in one query.
- Avoids round-trips of "try logging in again, send me the error" — which costs user time and shows lack of confidence.

### How to Query D1 Directly

```bash
ACCT="<CF_ACCOUNT_ID>"
DB="<D1_DATABASE_ID>"
TOKEN="<CF_API_TOKEN>"

# Standard query
curl -s -X POST \
  "https://api.cloudflare.com/client/v4/accounts/$ACCT/d1/database/$DB/query" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT id, username, email, is_admin, registered_at FROM members WHERE username LIKE \"%X%\" LIMIT 10"}'
```

**Response shape**: `{"result":[{"results":[{...}], "success":true, "meta":{...}}], "success":true, ...}` — note `result` is a LIST (one element per statement), with `results` inside.

### Where to Find Account/DB/Token

User's projects (e.g. `C:\Users\you\D\ImageGen\`, `C:\Users\you\D\ShortDramaForge\`) all have a `deploy.py` or `deploy_full.py` near the root. Inside it you'll find:

```python
ACCOUNT = "94b83b469095b52dae264ddacbb10d1c"        # CF Account ID
D1_DB = "ad3bb09e-e4c0-442b-a624-a3a7d751f095"     # D1 database ID
SCRIPT = "empty-fog-b746"                            # Worker script name
CF_TOKEN = base64.b64decode("Y2ZhdF81...").decode()  # Often b64-embedded
```

**Pattern**: open `deploy.py`, grep for `ACCOUNT`, `D1_DB`, `SCRIPT`, `base64.b64decode` — extract them and use directly. The token is base64-encoded inline so it survives source paste.

### Multi-Project Disambiguation

User has multiple CF projects under `D:\`. **When user names a production domain, match domain → project FIRST** (not username). The user often *thinks* they remember their username but may be wrong; the domain is unambiguous.

Concrete failure mode (recurring): user says "I can't log in to test-toplist.com with `Kairos2026`" → agent grepped `Kairos2026` across projects, found NOTHING, then jumped to the alphabetically-first project (ShortDramaForge) where an unrelated `Kairos` user happened to exist in a backup JSON. Wrong project. Real `Kairos2026` was in ImageGen.

**Step 0 (DOMAIN FIRST)**: Map the user's domain to the right project tree BEFORE doing anything else.

```bash
# 1. Map domain → project
cd /c/Users/you/D
grep -rEn "test-toplist\.com|imagegen-forge|empty-fog-b746|shortdrama-forge|shortdrama\.com" ImageGen/ ShortDramaForge/ 2>/dev/null | head -10
```

Look for: hostname strings in `worker.js` / `worker_api.js`, Worker script names (`SCRIPT_NAME = '...'` in `deploy*.py`), or `route_pattern` config.

**Step 1 (then username grep, scoped to that project)**:

```bash
grep -rn "<username>" <project_dir>/
```

If it appears in D1 backup JSON (`D1_backup_*.json`), it tells you which project the user belongs to. If it appears in `worker_api.js` or `js/store.js` (admin checks like `username === 'X'`), same thing. **Do NOT cross-match between projects** — a username in `ShortDramaForge/D1_backup_*.json` is NOT the same account as one in `ImageGen/`.

For the project catalog with domain → D1 → schema mapping, see `references/d1-diagnostic-recipes.md` → "Project Catalog".

### Diagnostic Decision Tree

When user says "I can't log in with `<username>`":

1. **Grep across projects** — which project does this username belong to?
2. **Find deploy.py for that project** — extract ACCT/DB/TOKEN
3. **Query D1** — does `members.username = '<username>'` exist?
   - **Not found** → "Account doesn't exist. Here are the existing users: [list]. Did you mean one of these?"
   - **Found** → check `is_admin`, `registered_at`, password length. If password column is short/hashed differently, that's a separate issue.
4. **If exists but login fails** → check schema for status column (`status='banned'`?), or frontend for username casing issues (SQLite LIKE is case-insensitive by default but exact `=` is case-sensitive in D1).

### Hard-Won Lesson: Token Redaction

The platform redacts any token-like substring (`cfat_...`, `Bearer `, `sk-...`, `TOKEN=...`) in `write_file`, `patch`, `execute_code`, and `terminal` command args. Once redacted, the file/env holds `***` literally — no workaround recovers the real token.

**Only reliable pattern**: read token at runtime from a file the user created with their text editor (e.g. `_secrets.json` they edit in Notepad/VSCode). See `ai-image-saas-on-cloudflare` SKILL.md for full details on this.

**For direct D1 query from agent**: the token needs to be passed to a curl/exec call. If it's already on disk in an unredacted file, you can `read_file` it as part of an `execute_code` script and use it there. If you have to type it inline in a shell command, the redaction system catches it. Workaround: have `execute_code` read the file and make the request itself, keeping the token in memory.

**Read-at-runtime pattern (always prefer this)** — never paste the token into any literal. The platform redacts `***`-style markers and token-shaped substrings (`cfat_...`, `Bearer `, `sk-...`) at write time, leaving literal `***` chars that fail Python parsing. The reliable pattern:

```python
import os, json, urllib.request
PROJ = r'C:\Users\you\D\ImageGen'
with open(os.path.join(PROJ, '.deploy_token')) as f:
    TOKEN = f.read().strip()
# Use TOKEN in the same script — never written to a file/env var.
```

This works in both one-off diagnostic scripts and persistent helper files. If you find yourself writing `TOKEN=***`, `cfat_***`, or `Bearer ***` in any source, stop and switch to this pattern.

## Deploy Mechanics (Core)

### Worker Deploy via API (Python)

```python
import subprocess, json

# 1. Build (combines static files into one Worker script)
r = subprocess.run(["python3", "deploy_cf.py"], capture_output=True, text=True, timeout=30)
if "Syntax OK" not in r.stdout:
    print("Build failed:", r.stdout[-300:])
    sys.exit(1)

# 2. Bindings (D1, KV, R2, plain_text secrets)
bindings = [
    {"type": "d1", "name": "DB", "id": D1_DB},
    {"type": "plain_text", "name": "AUTH_SECRET", "text": auth_secret},
    # {"type": "r2_bucket", "name": "ASSETS", "bucket_name": "..."},
]
meta = json.dumps({"main_module": "worker_deploy.js", "bindings": bindings})

# 3. Upload via multipart PUT
cmd = ["curl", "-s", "-X", "PUT",
    "-H", f"Authorization: Bearer {TOKEN}",
    "-F", "metadata=@_meta.json;type=application/json",
    "-F", "worker_deploy.js=@worker_deploy.js;type=application/javascript+module",
    f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/workers/scripts/{SCRIPT}"]
subprocess.run(cmd, check=True)
```

### Required Binding Types

| Type | Use | Example |
|------|-----|---------|
| `d1` | D1 database | `{"type":"d1","name":"DB","id":"<uuid>"}` |
| `plain_text` | Secret injected as env var | `{"type":"plain_text","name":"AUTH_SECRET","text":"..."}` |
| `r2_bucket` | R2 storage bucket | `{"type":"r2_bucket","name":"ASSETS","bucket_name":"..."}` |
| `kv_namespace` | KV namespace | `{"type":"kv_namespace","name":"OAUTH_STATE","namespace_id":"..."}` |

**Always upload `metadata` and the main module file in the same multipart PUT** — the deploy script must be uploaded with `filename=main_module` (matches the `main_module` field in metadata).

### Pre-Deploy Validation

Always run `node --check worker_deploy.js` (or equivalent) before upload to catch syntax errors. Common issues:
- Unbalanced template literal braces (`${...}` with stray `}`)
- Apostrophes in single-quoted strings containing `${}`
- Missing closing `}` for function bodies

### Schema Migrations

When adding columns to D1 tables (e.g. new `status`, `credits`, `commission` fields):
1. Add `ALTER TABLE x ADD COLUMN y TYPE` to schema.sql
2. Wrap in `try/catch` that ignores "duplicate column name" errors
3. Ship a one-shot migration script for existing DBs

## Inlined Static Files Architecture & Bundle Optimization

**Pattern**: Single-binary deploy where the Worker bundle = API code + `FILES` dict (base64 of every static file) + `ROUTES` dict + `decode()` helper. Every static file in the project tree gets inlined.

```
Worker bundle (worker_deploy.js)
  ├── // header comment
  ├── const FILES = { "index_html": "PCF...", "js_app_js": "Y29uc3Q...", ... }   ← base64 of every file
  ├── const ROUTES = { "/": "index_html", "/css/style.css": "css_style_css", ... }
  ├── function decode(b64) { /* atob + TextDecoder */ }
  ├── const MIME = { ... }; function getMime(path) { ... }
  └── worker_api.js body   ← API endpoints, leading comments stripped
```

Serve path in main fetch handler:
```js
if (path !== '/' && !path.startsWith('/api/') && !path.startsWith('/cdn/')) {
  if (ROUTES[path] && FILES[ROUTES[path]]) {
    return new Response(decode(FILES[ROUTES[path]]), {
      headers: { 'Content-Type': getMime(path), 'Cache-Control': 'public, max-age=3600', ...SECURITY_HEADERS }
    });
  }
}
```

**Implication**: bundle size ≈ Σ(1.33 × static file size). First-paint download IS the bundle size. 532KB → slow cold start, sluggish feel. Every file in the project root ships forever.

### Bundle Size Optimization Playbook (proven path)

Achieved **532KB → 226KB (-57%)** on imagegen-forge:

| Action | Typical Savings |
|---|---|
| Delete orphan standalone files (`.html` tools, debug JSON dumps) | ~150KB+ |
| Slim i18n: drop languages `toggleLang()` doesn't actually flip | ~50KB |
| Extract shared utilities (lightbox duplicated across views → store.js) | ~5KB |
| Drop unused feature surface (BYOK endpoints + UI sections) | ~10KB |

**Before deleting any orphan file**, verify zero references via `search_files` or `grep` — including `worker_deploy.js` route table (auto-registers all files). Orphaned `.html` tools not linked from `index.html` / `sitemap.xml` / any JS are usually safe to delete.

**For BYOK-style feature removal**: don't just delete the frontend UI. Walk the backend API handlers in `worker_api.js` too — `/api/account/apikey`, `/api/providers`, and the `useOwnKey` branches in generate/recover paths. Extract shared utilities (e.g., lightbox) BEFORE deleting the feature, otherwise you delete it in one place and break it in another.

**Always do these BEFORE reaching for architecture rewrites** (Workers Assets, KV swap, etc.):

1. **Verify zero references before deleting**:
   ```bash
   grep -r "filename" js/ css/ index.html --include='*.js' --include='*.html'
   ```
2. **Check i18n `toggleLang()` body** — if it only flips `en ↔ zh`, the other 5 languages are pure bloat. Each lang dict ≈ 4KB + per-key translation ≈ 50KB total savings typical.
3. **Check for cross-view duplication** — same modal/handler in 2+ view files = extract to store.js or top-level utility.
4. **Look at deploy_cf.py SKIP_NAMES list** — anything NOT skipped ends up in the bundle. Review that list periodically.

#### i18n slimming: how to actually do it

The "drop languages" bullet above is a one-liner; the mechanical work is 50+ lines of regex + stack-based brace parsing because `i18n.js` has three different shapes to edit:

- Top-level `I18N = { en: {...}, zh: {...}, ja: {...}, ... }` — drop whole lang dicts
- Top-level `keyName: 'string',` lines — drop keys by name
- Nested `STYLE_LABELS[id] = { en: '...', zh: '...', ja: '...', ... }` — strip individual fields, keep the ones you want

A naive regex on `^  [a-z]{2}: \{$` boundaries mis-counts braces when any object has nested braces (it never does in canonical i18n.js, but the pattern breaks the moment someone adds a template literal). The robust approach uses **stack-based brace matching that ignores braces inside backtick template literals**.

**Use `scripts/i18n_slim.py`** for this — it implements all three operations with the safe parsing. Audit first:

```bash
python scripts/i18n_slim.py audit js/i18n.js
# i18n.js: js/i18n.js
#   Total size: 66.2 KB
#   Language blocks: 7
#     de: 113 lines, 3.9 KB
#     en: 113 lines, 3.7 KB
#     es: 113 lines, 3.9 KB
#     fr: 113 lines, 4.0 KB
#     ja: 113 lines, 4.4 KB
#     ko: 113 lines, 4.0 KB
#     zh: 113 lines, 3.6 KB
#   Contains CJK chars: True

# Drop whole language dicts
python scripts/i18n_slim.py drop-langs js/i18n.js ja,ko,fr,de,es

# Strip the same languages from STYLE_LABELS nested objects (keeps en + zh by default)
python scripts/i18n_slim.py strip-labels js/i18n.js ja,ko,fr,de,es

# Remove specific top-level keys (e.g. all the BYOK-related ones)
python scripts/i18n_slim.py remove-keys js/i18n.js genModel,accountApiKeys,accountAddProvider,...

# Verify final size
python scripts/i18n_slim.py audit js/i18n.js
```

Order matters: `drop-langs` first (whole dicts), then `strip-labels` (nested fields), then `remove-keys` (specific keys). Run `audit` between steps to confirm. The script rewrites the file in-place; run your project's `deploy_cf.py` after to push the smaller bundle.

### Bulk value transforms (translate, retitle, rewrite)

Different from i18n slimming: when you need to **replace the values themselves** in a large inlined object literal (e.g., translate all 87 STYLE_LIBRARY entries from Chinese to English, rewrite 30 error strings for tone, bulk-retitle product names). The pattern: ordered list-of-tuples + block walker + assert order + verify zero residuals. See `references/bulk-content-transforms.md` for the full template and pitfall list.

### deploy_cf.py Standard Shape

```python
SKIP_NAMES = {deploy.py, deploy_cf.py, .deploy_token, .gitignore, README.md,
              schema.sql, worker_api.js, worker_deploy.js, __pycache__}
SKIP_PREFIXES = ('.', '_')      # skip .git, _apikey.py, _diag.py
SKIP_SUFFIXES = ('.pyc',)

# Walk project → base64 encode each → build FILES + ROUTES dicts
# Concatenate: header + FILES + ROUTES + decode() + MIME + getMime() + worker_api.js body
# Write worker_deploy.js
# node --check worker_deploy.js   # must pass or abort
# PUT to /accounts/{id}/workers/scripts/{name}
# Apply schema (split on ';', 1 stmt per request)
# (optional) Set up zone route — 403 OK to ignore if existing
```

Typical good output (ignore these warnings):
```
Built worker_deploy.js: 227398 bytes
✅ Syntax OK
✅ Existing D1: <uuid>
✅ Worker DEPLOYED: <name>
⚠️ Schema stmt N error: read operation timed out    ← benign, CF read replica slow
⚠️ Zone route 403                                   ← benign, existing route
```

**Full ImageGen-style `deploy_cf.py` reference implementation** (where each token comes from, multipart upload quirks, D1 schema apply, zone-route setup, env-file handling) → **`references/deploy-cf-py-implementation.md`**.

### ⛔ "I edited the file but the change isn't live" — three steps, all required

When the user reports "I changed X in admin.js but the page still shows the old version" / "我的修改没生效", the deployment pipeline has **three independent steps** and missing any one of them leaves the user looking at the old code:

1. **File actually saved** — verify with `git diff` or `cat`.
2. **Worker redeployed** — the static assets are base64-inlined into `worker_deploy.js`. Changing `js/views/admin.js` does NOT change the served file until `python deploy_cf.py` runs, builds the new bundle, and PUTs it to CF.
3. **Browser/CDN cache busted** — every `<script>` / `<link>` in `index.html` carries `?v=N`. After deploy, bump every `v=N` → `v=N+1` in `index.html` and re-deploy. Otherwise the browser (or CF edge cache) keeps serving the old asset.

**Symptom ladder**:
- Step 1 missed → file edit isn't actually saved; re-edit and verify with `grep`.
- Step 2 missed → old bundle still deployed; the user sees `404` for any new file added, or stale code for any edited file.
- Step 3 missed → deploy succeeded but browser keeps requesting `?v=N` (old). User hard-refreshes → still old (CF edge cache honors `Cache-Control: public, max-age=3600` on static responses). The fix is bumping `?v=N` in `index.html` AND re-deploying.

**Fix (all three steps)**:
```bash
# Step 2: rebuild + redeploy
cd /path/to/project && python deploy_cf.py

# Step 3: bump every ?v=N to v=(N+1) in index.html, then redeploy again
# (use patch with replace_all=true on 'v=N' → 'v=N+1' to avoid sed accumulation bugs;
#  see frontend-patching → "sed -i multi-pass accumulation" pitfall)
```

**Defensive `?cb=$(date +%s)` for verification** — even after a clean deploy + cache bump, the user might be behind a caching proxy. Append `?cb=1234567890` to the URL once to force-load fresh, then revert to the bumped `?v=N+1`.

**Verify the bundle actually contains the new code** — don't trust "deploy succeeded":
```bash
# After deploy, check the live server returns the new content
curl -s "https://yourdomain.com/js/views/admin.js?v=N+1" | grep "yourNewString"
# Empty result → bundle doesn't have the change → recheck deploy output for Syntax OK / Worker DEPLOYED
```

**User says "I did all three and it still doesn't work"** — then check:
- Did `node --check worker_deploy.js` actually pass? (deploy script aborts on syntax errors but doesn't always print clearly)
- Did the PUT actually succeed? Look for `Worker DEPLOYED` line in deploy output, not just the `Built worker_deploy.js` line.
- Did `index.html` get edited (the cache-bump is on a DIFFERENT file than the actual fix)?
- Is the user hard-refreshing? `Ctrl+Shift+R` is required, not just `F5` — service workers and CDN caches can be sticky.

## D1 Schema Migration Pitfalls

### ⛔ INSERT OR IGNORE does NOT update existing rows

**This is the #1 deploy foot-gun for config tables.** After:
```sql
INSERT OR IGNORE INTO pricing_packages (id, credits, amount_usd, ...) VALUES ('starter', 500, 20.00, ...);
```
If `id='starter'` already exists, the row is **silently skipped**. Deploy "succeeded" but the data didn't change.

**For config tables with fixed IDs, `schema.sql` alone won't migrate values.** You need one of:

```bash
# Option A: DELETE + INSERT via CF API (cleanest, but may disrupt users mid-session)
DELETE FROM pricing_packages;
INSERT INTO pricing_packages (id, credits, amount_usd, ...) VALUES (...);

# Option B: Change IDs to force new INSERT (safest for prod, but bloats table)
INSERT OR IGNORE INTO pricing_packages (id='v2_starter', ...) VALUES (...);
-- UPDATE old rows to active=0 if needed

# Option C: Surgical via wrangler d1 execute (best for one-off changes without redeploy)
wrangler d1 execute imagegen-db --command="DELETE FROM pricing_packages WHERE id IN ('old1','old2');"
wrangler d1 execute imagegen-db --command="INSERT INTO pricing_packages (...) VALUES (...);"
```

**Symptom**: deploy reports success, but `SELECT * FROM pricing_packages` shows old values. Don't trust `INSERT OR IGNORE` for config migrations.

### ⛔ D1 query API runs ONE statement per request

`POST /accounts/{ACCOUNT_ID}/d1/database/{DB_ID}/query` with `{"sql": "INSERT...; INSERT...;"}` runs the first statement and silently drops the rest. Always split on `;` client-side and send each statement separately.

```python
# Correct
for stmt in statements:  # already split on ;
    r = urllib.request.urlopen(query_url, data=json.dumps({'sql': stmt}))
```

### "Schema applied with warnings" is usually benign

Deploy script reports `⚠️ Schema stmt N: read operation timed out`. This is CF's read replica being slow — the write usually went through. **Don't assume failure.** Verify with a follow-up `SELECT` query before rolling back.

### ⛔ JS number binds to TEXT column as `"7.0"` — silent data isolation

**Symptom**: User-side rows and admin-side rows for the SAME `user_id` value never match each other in a `WHERE user_id = ?` query. The table looks fine in `SELECT *`, but each side only sees their own rows. Looks like a UI / rendering bug, is actually a data layer bug.

**Root cause**: D1's `bind()` serializes JS values to SQLite types with text-affinity coercion:
- JS number `7` → bound as REAL/INTEGER → SQLite stores in TEXT column as `"7.0"` (D1's coercion appends `.0` when an integer goes into a TEXT column under NUMERIC affinity)
- JS string `"7"` → bound as TEXT → stored as `"7"`

So if your code mixes `bind(user.id)` (number) and `bind(String(user.id))` (string) for the same column, rows end up split across two storage formats and never `=`-match each other. **SQLite's type affinity is the trap, not the data.**

**Real failure trail (ImageGen, Aug 2026)**: `messages` table had `user_id TEXT NOT NULL`. User-side POST bound `user.id` (number) → stored as `"7.0"`. Admin-side POST used `.toString()` → stored as `"7"`. User's GET `WHERE user_id = 7` matched only the user-side rows. "User can't see admin messages" — a real bug that took 3 diagnostic steps to find because the symptoms looked like frontend/render failure.

**Diagnostic query** (reveals the split immediately):

```sql
SELECT id, user_id, typeof(user_id) AS uid_type, sender, content
FROM messages ORDER BY id;
-- id=1  user_id='5.0'  type=text  sender=user   '12345'
-- id=3  user_id='6'    type=text  sender=admin  'reply'
-- ^^ trailing ".0" on user rows but not admin rows
```

The split is invisible to the API consumer — both values look like "the same id" in JSON responses. You have to inspect the actual storage with `typeof()` to see the type drift.

**Fix — two parts**:

1. **Going forward**: pick ONE binding convention and use it everywhere. For TEXT columns holding numeric IDs, prefer `bind(String(value))` so the storage format is predictable. Apply to BOTH read and write paths (a SELECT that doesn't match the INSERT format is just as broken).

2. **Migrate existing data** (idempotent, safe to re-run on every deploy):

```sql
UPDATE messages
   SET user_id = printf('%d', CAST(user_id AS REAL))
 WHERE user_id LIKE '%.0';
```

This converts `"7.0"` → `"7"`, `"5.0"` → `"5"`, etc. After migration both sides see the same row set.

**Defense in depth** — if the JS side might pass either form, normalize at the handler boundary:

```js
const userId = String(body.user_id || '').replace(/\.0$/, '');
```

Stripping `.0` defensively means a missed call site doesn't silently corrupt data; old `"7.0"` rows still match the new `"7"`-style queries.

**Pre-deploy audit** (catches this before it ships):

```bash
# Find any bind() call in worker_api.js that doesn't use String() on a number-ish value
grep -nE "\.bind\([^)]*user\.id" worker_api.js
```

Every hit on a TEXT column is a candidate for `.bind(String(user.id), ...)`.

### `CREATE INDEX IF NOT EXISTS` collisions are normal

Filter them in the deploy script:
```python
if 'already exists' not in err_msg.lower():
    print(f"⚠️ Schema stmt {i+1}: {err_msg}")
```

## Worker Runtime Limits (cheap tier, 2026-07)

- **CPU time**: 30s default. Raise via `wrangler.toml` → `[limits] cpu_ms = 300000` (5 min). After raising, plan upgrade is permanent — there's no way to roll back via API. Dashboard action only.
- **Wall-clock**: Free tier isolate lifetime ~30s, no hard limit on HTTP handlers.
- **`ctx.waitUntil()` does NOT extend isolate lifetime** — only the HTTP response lifetime. Background work wrapped in `waitUntil` still dies when the isolate is recycled. Don't rely on it for long-running background tasks.
- **API cannot raise plan** — only Dashboard action. Diagnose CPU/timeout root cause first; only suggest upgrade as last resort.

## Stuck-Pending Generation Recovery

After wall-clock kill mid-uuapi-poll (or any upstream API with task-based async), `generations` rows can be stuck: `status='pending', completed_at=NULL, error_message=''`. Recovery sweep:

```sql
SELECT id, user_id, credits_cost, upstream_task_id, last_polled_at, created_at
FROM generations
WHERE status = 'pending'
  AND created_at < ?  -- older than maxAge (e.g. 10 min)
```

For each row:
1. Re-poll upstream API with saved `upstream_task_id`
2. If success: archive result to R2, set `status='success'`, **don't refund credits**
3. If failed/404: set `status='failed', error_message=<reason>`, **refund credits** (`UPDATE users SET credits = credits + ?`)
4. If still pending: leave alone, next sweep retries

**Run via cron trigger or one-shot `wrangler d1 execute`.** Don't try to recover inline in the generate handler — it's already busy waiting and adding more work risks another kill.

**Identifying stuck rows in D1**:
```sql
-- The classic signature
SELECT COUNT(*) FROM generations
WHERE status='pending' AND completed_at IS NULL AND error_message = ''
  AND created_at < strftime('%s','now','-10 minutes');
### Workarounds (do at least #1 always)

1. **Always ship a manual admin endpoint** alongside your cron-driven sweep. Pattern:

```js
if (path === '/api/admin/recover-pending' && method === 'POST') {
  const adminSecret = request.headers.get('X-Admin-Secret') || '';
  if (!env.ADMIN_SECRET || adminSecret !== env.ADMIN_SECRET) return errResp('Forbidden', 403);
  const results = await recoverStuckGenerations(env, { limit: 50, staleMs: 60000 });
  return jsonResp({ ok: true, ...results });
}
```

Auth via `ADMIN_SECRET` binding (plain_text). One curl recovers everything the cron should've handled.

2. **External cron fallback** — for production reliability, run a free external cron (cron-job.org, GitHub Actions `schedule:` trigger, easycron) that POSTs the admin endpoint every N minutes. Bypasses CF's flaky scheduler entirely. Cloudflare's own cron then becomes the second layer of defense.

3. **Re-PUT the schedule after every Worker deploy** that changes the `scheduled()` handler — the schedule survives deploys but stale schedules don't always pick up handler changes. Cheap insurance:

```bash
curl -X PUT ".../schedules" -H "Authorization: Bearer *** \
     -H "Content-Type: application/json" -d '[{"cron":"*/5 * * * *"}]'
```

### 💡 Best fix: stop relying on CF cron at all (Opportunistic Recovery)

For recovery/cleanup/sync work that doesn't need strict timing, **don't use cron — hook into read endpoints**. Pattern: any time a user GETs their data, check for stale background work and `ctx.waitUntil(recoverOne(env, row))` it. The response goes out immediately; the background work runs after. Zero external dependency, traffic-driven, self-scaling (more users = faster recovery).

**Full pattern + code + pitfalls + scaling notes** → `references/opportunistic-recovery.md`. Origin: developed for ImageGen `imagegen-forge` 2026-07-31 after 9.5+ hours of silent cron failure on Free plan left two `generations` rows stuck pending despite a configured `*/5 * * * *` schedule and a working `scheduled()` handler.

**Why this beats #1 + #2 above**: you eliminate the flake surface entirely instead of working around it. Keep the admin endpoint + an optional external cron as a safety net for zero-traffic periods.
SELECT id, credits, amount_usd, label_en, active, sort_order
FROM pricing_packages ORDER BY sort_order;

-- Stuck generations count
SELECT COUNT(*) FROM generations WHERE status='pending'
  AND created_at < strftime('%s','now','-10 minutes');

-- User credit balance sanity
SELECT id, email, credits FROM users WHERE credits < 0 OR credits > 100000;

-- Per-provider generation counts
SELECT model_provider, model_name, status, COUNT(*) as n
FROM generations
WHERE created_at > strftime('%s','now','-7 days')
GROUP BY model_provider, model_name, status;
```

## Building Full-Stack Apps on Cloudflare Workers

For shipping a real production web app entirely on Cloudflare's edge: Workers (ES modules) for logic, D1 (SQLite) for persistence, R2 for blob storage, and a multi-file vanilla-JS SPA bundled as base64 in the Worker. No wrangler CLI needed — everything goes through CF's REST API from a single Python deploy script.

### One-Worker architecture

```
yourdomain.com
    │
    ├── GET /             → Worker serves inlined index.html
    ├── GET /css/*.css    → Worker decodes base64 FILES dict → returns text/css
    ├── GET /js/*.js      → same pattern
    └── GET /api/*        → Worker handles (auth, account, orders, generate)
                           → D1 (SQL) / R2 (blob) / outbound HTTP (PayPal, OpenAI)
```

The single Worker script `worker_deploy.js` contains:
- A generated prefix with `const FILES = { ... }` (base64) and `const ROUTES = { ... }`
- A `decode(b64)` helper that uses `TextDecoder` (UTF-8 safe for Chinese filenames)
- Your hand-written `worker_api.js` (export default { async fetch(request, env, ctx) })

The `fetch` handler first checks static routes, then falls through to the `/api/*` switch.

### deploy.py skeleton (no wrangler needed)

See `templates/deploy_cf.py` — a known-good reference. Key points:

1. Read all files under `css/`, `js/`, `index.html`. Base64-encode and emit `const FILES = { ... }` and `const ROUTES = { ... }`
2. Concatenate: header + FILES + ROUTES + decode() + MIME + getMime() + worker_api.js body (skip leading comments)
3. Validate with `node --check worker_deploy.js` — **always do this before uploading** (multipart uploads fail silently with HTTP 400 if JS is broken)
4. PUT to `https://api.cloudflare.com/client/v4/accounts/{ACCOUNT}/workers/scripts/{SCRIPT_NAME}` with multipart/form-data
5. **`filename` in the part must equal `main_module` in metadata** — mismatch causes HTTP 400 with no useful error message

### CF API token scopes — read this BEFORE you deploy

CF API tokens have **independent scope bits** for Accounts vs Zones. A token that can `PUT /accounts/{}/workers/scripts/{}` **cannot** `POST /zones/{}/workers/routes`. Symptoms: `403 Forbidden` with code 10000 on zone calls, 200 OK on worker/script calls.

| Want to set | Endpoint | Scope needed |
|-------------|----------|--------------|
| Deploy Worker code | `PUT /accounts/{acc}/workers/scripts/{name}` | Account / Workers Scripts / Edit |
| Bind worker to custom domain | `PUT /accounts/{acc}/workers/domains` (body: `{hostname, service, zone_id}`) | Account / Workers Scripts / Edit |
| Create route in zone (old API) | `POST /zones/{zid}/workers/routes` | **Zone / Workers Routes / Edit** |
| Enable workers.dev subdomain | `POST /accounts/{acc}/workers/scripts/{name}/subdomain` `{enabled: true}` | Account / Workers Scripts / Edit |

**Best**: Create a token with template "Edit Cloudflare Workers" (covers both). **Or**: Bind custom domain via Account-level endpoint `/accounts/{}/workers/domains` (idempotent — PUT with same hostname updates `service` or creates). **Never DELETE** first — if you can't recreate, you've broken your domain.

### ⛔ Zone Analytics API scope is missing from deploy tokens — capture geo in Worker instead

`POST /zones/{zid}/analytics/dashboard*` and the GraphQL Analytics API both require `com.cloudflare.api.account.zone.analytics.read` scope. **Typical deploy tokens (created via "Edit Cloudflare Workers" template) don't have it.** Symptom: 403 Forbidden on every zone analytics call, even though the token works for Worker deploy + D1 query.

If you need per-country traffic breakdowns, **don't try to expand the token scope** — capture geo data at the Worker edge instead. See **User GeoIP capture via request.cf** section below for the full pattern. This is more reliable than Zone Analytics because:
- Token scope never changes
- Per-user attribution (not just per-request)
- Survives D1 cache and CDN layer

### D1 schema migrations — `CREATE TABLE IF NOT EXISTS` is a trap

D1's `CREATE TABLE IF NOT EXISTS` does NOT alter existing tables. **You'll get "no such column" errors at runtime** that look like code bugs but are actually schema drift.

Even worse: `deploy_cf.py`'s built-in `schema.sql` apply walks `CREATE TABLE IF NOT EXISTS` statements but does **NOT** execute `ALTER TABLE ADD COLUMN` statements against existing tables in any meaningful way — they're either silently skipped or thrown as a class of error that the deploy script ignores. **Bottom line: adding a column to a production table that already has rows requires a separate one-shot migration script run via CF API directly.**

Idempotent migration pattern (`templates/migrate_d1.py`):

1. Split schema.sql into individual statements
2. For `ALTER TABLE ... ADD COLUMN` statements, expect `duplicate column` errors and treat them as success
3. D1 query API runs ONE statement per call — `multi-statement` is rejected
4. Verify with `PRAGMA table_info(<table>)` afterwards

### Custom OAuth (Google, GitHub) in Workers

State: store a random token in KV with `expirationTtl: 600` (10 min) to prevent CSRF. Check in callback handler, delete after use.

Token issuance: insert into `sessions(token, user_id, expires_at)`. Return `{token, user}` to client. Client stores in `sessionStorage` and sends `Authorization: Bearer <token>` on every API call.

User lookup order for `findOrCreateOAuthUser`: 1. Existing user by `oauth_<provider> = profile_id`. 2. Existing user by `email` (link OAuth). 3. Create new user (no signup bonus unless intentionally set).

Full OAuth flow at `references/oauth-flow.md`.

### BYOK image providers (Bring Your Own Key)

Users bring their own OpenAI-compatible API keys. Store encrypted in D1:

```sql
CREATE TABLE user_api_keys (
  provider TEXT NOT NULL,
  base_url TEXT DEFAULT '',
  model_name TEXT DEFAULT '',
  encrypted_key TEXT NOT NULL,
  UNIQUE(user_id, provider)
);
```

Encryption: AES-GCM with a Worker secret (`AUTH_SECRET`) padded to 32 bytes. Forwarding on generate: `POST {base_url}/images/generations` with body `{model, prompt, n:1, size, response_format:'url'}`. Parse `{data:[{url}]}`. On failure, refund credits.

**When image generation fails, the FIRST thing to check is D1's `generations.error_message`** — it's the only authoritative log of what the upstream provider actually returned. Full 4-step ladder at `references/image-generation-failure-diagnosis.md`.

### Rate limiting in Workers

Sliding window via `rate_log(user_id, action, created_at)` table. Cheap, simple, survives Worker restarts. Apply to every state-changing endpoint:

```js
const n = await env.DB.prepare(
  'SELECT COUNT(*) AS n FROM rate_log WHERE user_id=? AND action=? AND created_at>?'
).bind(user.id, action, now()-60).first().n;
if (n >= MAX_PER_MINUTE) return errResp('Rate limited', 429);
```

Recommended limits: generate=10/min, register=3/hour, save_apikey=20/min, create_order=10/min.

### PayPal Orders (Checkout)

For one-time purchases, use v2 Orders API:

```
POST {base}/v1/oauth2/token   → get access_token (Basic auth, grant_type=client_credentials)
POST {base}/v2/checkout/orders → create order, get approval_url
```

base is `https://api-m.sandbox.paypal.com` for sandbox, `https://api-m.paypal.com` for live. `PAYPAL_ENV` secret controls this. For `mock` mode (no PayPal creds), return `{mock: true}` so the UI can show a "PayPal not configured" state.

### Vanilla-JS SPA pattern that pairs with this Worker

Files: `index.html` (single `<div id="app">` mount point + 5 JS files), `js/i18n.js` (dictionary + `t(key)` + `setLang`), `js/store.js` (apiCall with auto Bearer header), `js/router.js` (hash-based SPA), `js/app.js` (init), `js/views/<name>.js` (each defines `views.<name> = { async render(params) {...} }`).

**The `this` binding pitfall**: `route('/x', views.foo.render)` calls the handler as `handler(params)` with `this === undefined` (strict mode). Internal calls like `this.bindEvents()` crash with `this.bindEvents is not a function`. **Fix**:

```javascript
function routeView(path, view, methodName) {
  routes[path] = (...args) => view[methodName].apply(view, args);
}
routeView('/generate', views.generate, 'render');  // `this` inside render = views.generate
```

**Language toggle re-render**: `setLang` must dispatch `hashchange` because views hardcode translations into HTML at render time. `dispatchEvent(new HashChangeEvent('hashchange'))` works regardless of load order.

Full SPA pattern: `references/cf-api-endpoints.md`, `references/byok-image-providers.md`.

### Common pitfalls checklist

- [ ] `filename` in multipart part == `main_module` in metadata, else HTTP 400
- [ ] `node --check worker_deploy.js` before PUT, else deploys with broken JS
- [ ] D1 schema migrations: split statements, tolerate `duplicate column`
- [ ] CF token scope: Account vs Zone are independent. Custom domains via `/accounts/{}/workers/domains`, NOT `/zones/{}/workers/routes`
- [ ] Never DELETE a custom domain record before you can PUT it back
- [ ] `route('/x', view.method)` loses `this` — use `routeView` wrapper
- [ ] `'}` inside `${cond ? '' : 'value'}` inside a template literal breaks parsing — close the string before the expression closes
- [ ] `try { window.dispatch(...) }` doesn't work — `window.dispatch` is not a DOM method
- [ ] Read `references/pitfalls.md` for full debugging notes

## User ↔ Admin Support Pattern (1:1 Chat, Sample Gallery, Hidden Admin Route)

When a SaaS needs a direct support channel between members and a single
admin, plus admin-managed public content (sample gallery, banners, FAQs)
— and the admin shouldn't need a normal user account — use this pattern.

- **Schema**: `messages(user_id, sender['user'|'admin'], sender_name, content, read_at, created_at)` + `sample_images(type, url, caption, sort_order, created_at)`. `user_id` always refers to the member — never the admin.
- **Backend**: Member endpoints gated by `requireAuth()`; admin endpoints gated by `requireAdmin(request, env)` helper that checks `X-Admin-Token` header against `env.ADMIN_API_TOKEN` (secret_text binding).
- **Frontend**: User-side `/messages` view with 6s polling. Hidden `/admin` route accessed via `?admin_token=XXX` URL param → stored in `localStorage.sf_admin_token` → stripped from URL via `history.replaceState`.
- **Privacy**: Member queries always include `WHERE user_id = me`. Member never sees other members' threads.
- **Read-receipt**: GET handler marks admin messages read as a side effect — no separate "mark read" endpoint needed.
- **Admin identity hiding**: Worker INSERTs `'Admin'` (generic) as `sender_name`, not the actual admin's name. UI renders `t('msgAdminLabel')` (translated label) for admin messages, not `m.sender_name` from DB.

Full code patterns + endpoint list + pitfalls → **`references/admin-support-panel.md`**.

## D1 Storage Limits — Images, Blobs, and the ~1 MB Per-Param Cap

D1 (SQLite) is great for structured data but has a **per-bound-parameter
limit of ~1 MB** on TEXT columns. Any `bind()` larger than ~1 MB throws
`SQLITE_TOOBIG` and your endpoint returns HTTP 500. This bites when storing
base64-encoded images, large JSON blobs, or fetched HTML content.

**Decision by content type**:
- User-uploaded image (display ≤300px) → resize in browser via `<canvas>`
  to 1280px max edge + JPEG q=0.85, then store data URL in D1 (95% size
  reduction: 7 MB PNG → 360 KB JPEG)
- Full-resolution image you need to preserve → push to **R2**, store only
  the R2 URL in D1
- Large JSON blob → split across rows or use **KV** (25 MB per value)
- Anything > 25 MB → not a D1 problem, use R2 + presigned URLs

**Two coupled gotchas when shipping image uploads**:
1. `<input type="url">` silently blocks form submission when the value is a
   `data:` URL — Chrome/Safari reject it as not-a-URL during constraint
   validation. Use `type="text"` instead. See pitfalls.md.
2. Cloudflare WAF blocks default `urllib` / `curl` User-Agent with error
   code 1010. Always set `User-Agent: Mozilla/5.0` in test scripts.
   See pitfalls.md.

**Full Canvas resize recipe, server-side validation, R2 migration path,
and the curl-reproduce debugging script** → **`references/d1-image-storage.md`**.

**End-to-end TOOBIG reproduction recipe with verified byte counts** (7.2 MB PNG → 360 KB JPEG, per-bind size probe, curl + jq stack trace) → `references/sqlite-toobig-repro.md`.

## Multi-language i18n Discipline

When the project supports 2+ languages (CN/EN baseline, expanding to 8+
like CN/EN/JP/KO/FR/DE/ES/RU), three pitfalls recur on every new feature:

1. **Late-added keys**: A new feature adds `msgTitle` to `en` only — the other 7 language blocks don't have it, so users see `msgTitle` literal text. **Fix**: Add new keys to ALL language blocks atomically in one operation (Python script that locates each `lang: { ... }` block via depth-walked regex). **Verify**: `grep -c "msgTitle: '" js/i18n.js` should match language count before deploy.
2. **`${t('key')}` in static HTML doesn't evaluate**: HTML carries structure (id, class, href). JS fills content via `t('key')` calls inside template literals. Never mix the two.
3. **Config table migrations**: `INSERT OR IGNORE` does NOT update existing rows — use `ON CONFLICT(id) DO UPDATE SET ...` for config tables that need value changes (pricing tiers, etc.).

Also: CF edge cache serves stale HTML after deploy (1-hour TTL on `Cache-Control: public, max-age=3600`). Always verify with `?cb=$(date +%s)` query string, AND have user hard-refresh.

Full details + audit script + diagnostic commands → **`references/i18n-discipline.md`**.

## AI Image SaaS Specifics (ImageGen / BYOK pattern)

Class-level architecture for an AI image generation SaaS deployed on Cloudflare Workers (validated by ImageGen project). Covers the components no single-purpose skill touches together:

1. **Multi-provider OpenAI-compatible API abstraction** — single function calls `/images/generations` against any base URL, with BYOK and custom providers
2. **OAuth (Google + GitHub) inside CF Worker** — state in KV, redirect → callback → token-in-hash pattern
3. **Credits + payment system** — PayPal Checkout, idempotent webhook credit grants
4. **Anti-scraping** — base64-embedded static files, CSP headers, watermark overlay, rate limit per user

### OpenAI-Compatible Provider Abstraction

```javascript
async function generateWithProvider(env, params, apiKey, providerConfig) {
  const url = (providerConfig.base_url || '').replace(/\/+$/, '') + '/images/generations';
  const body = {
    model: providerConfig.model_name || 'gpt-image-2.0',
    prompt: params.prompt || '',
    n: 1,
    size: sizeMap[params.aspect || '1:1'] || '1024x1024',
    response_format: 'url'
  };
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Authorization': 'Bearer ' + apiKey, 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  if (!resp.ok) throw new Error(`Image API ${resp.status}: ${(await resp.text()).slice(0, 300)}`);
  const data = await resp.json();
  return data.data?.[0]?.url || data.data?.[0]?.b64_json;
}
```

To add a new provider: 1. Add to `providerPresets` array in store.js. 2. Add binding in `deploy_cf.py` for the platform key. 3. Users can always add as custom provider via account page.

### Credits + Cost Model

Three-tier pricing, no signup bonus, no bonus credits on tiers. Simpler is better — bonus credits confuse users and create margin ambiguity.

**Per-image cost** (platform key, your margin):
- 2K → 1 credit
- 4K → 2 credits

**Recharge tiers** (flat $ → credits, no bonuses):

| Tier | Price | Credits | Effective $/credit |
|------|-------|---------|---------------------|
| Starter | $20 | 500 | $0.040 |
| Plus | $50 | 1,500 | $0.033 |
| Pro | $100 | 3,500 | $0.029 |

**Don't give free signup credits.** Users will register throwaway accounts to game the free credits. (User explicitly requested removing this.)

### Watermark Strategy

Platform-generated images get a visible CSS overlay watermark (in the **frontend**, not the image file). User's BYOK-generated images are **watermark-free** — this is the killer feature that justifies the credit cost differential. Use CSS overlay (Workers don't have sharp/canvas to modify images server-side).

### Static File Embedding = Automatic Anti-Scraping

All static files (`index.html`, `css/style.css`, `js/*.js`) are base64-encoded into the Worker's `FILES` dict. Browser must go through the Worker to fetch anything. Direct URL scraping returns the Worker endpoint, not a clean directory. Full build script at `templates/deploy_cf.py`.

### CSP + Security Headers

```javascript
const SECURITY_HEADERS = {
  'Content-Security-Policy': "default-src 'self'; img-src 'self' data: blob: https:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; script-src 'self' 'unsafe-inline'; connect-src 'self' https://apihub.agnes-ai.com https://api.openai.com https://dashscope.aliyuncs.com; frame-ancestors 'none'; base-uri 'self'; form-action 'self' https://accounts.google.com https://github.com",
  'X-Content-Type-Options': 'nosniff',
  'X-Frame-Options': 'DENY',
  'Referrer-Policy': 'strict-origin-when-cross-origin',
  'Permissions-Policy': 'geolocation=(), microphone=(), camera=()'
};
```

**`connect-src`** must include every external API endpoint you call. Adding a new provider = update this CSP or browser blocks the fetch.

Full patterns: `references/oauth-flow.md`, `references/byok-image-providers.md`, `references/image-generation-failure-diagnosis.md`, `references/ai-saas-anti-scraping.md`.

## User GeoIP Capture via `request.cf`

For any Worker that wants to track where users are coming from (country, region, city), capture `request.cf` at the edge instead of relying on Zone Analytics API. This pattern was added to ImageGen in Aug 2026 after a token-scope roadblock blocked the Zone Analytics route — see "Zone Analytics API scope is missing from deploy tokens" pitfall above.

### The pattern (4 layers)

**1. Worker top — read `request.cf.country` once per request:**

```js
async function handleRequest(request, env, ctx) {
  const url = new URL(request.url);
  const path = url.pathname;
  const method = request.method;

  // Country from CF edge GeoIP. request.cf is always present on Workers;
  // country may be 'XX' (unknown) or '' which we normalize to ''.
  const country = (request.cf && request.cf.country && request.cf.country !== 'XX')
    ? request.cf.country : '';

  // ... rest of handler — pass `country` into user-creation paths
}
```

**2. Schema — add the column with safe default:**

```sql
-- In schema.sql users table:
country TEXT DEFAULT '',  -- ISO 3166-1 alpha-2 ('' = unknown / not yet captured)
```

Add index for country aggregations:
```sql
CREATE INDEX IF NOT EXISTS idx_users_country ON users(country);
```

**3. Insert paths — bind `country` in every INSERT:**

```js
// Email/password register
const r = await env.DB.prepare(
  'INSERT INTO users (email, username, password_hash, display_name, credits, country, created_at, updated_at) VALUES (?, ?, ?, ?, 0, ?, ?, ?)'
).bind(email, username, ph, displayName || username, country, now(), now()).run();

// OAuth (Google + GitHub) — pass `country` as 3rd arg to findOrCreateOAuthUser
const user = await findOrCreateOAuthUser(env, profile, country);
```

**4. Backfill existing users on next OAuth login** — only fill if `country` is empty:

```sql
UPDATE users
   SET country = CASE WHEN country = '' THEN ? ELSE country END
 WHERE id = ?
```

Or in JS (works for both write paths in `findOrCreateOAuthUser`):
```js
.bind(profile.provider_id, profile.provider, profile.avatar, country, now(), user.id)
// Case-when protects existing data while filling blanks.
```

### Why `request.cf.country` works for OAuth flows

When a user does Google OAuth: `/api/auth/google` (302 to Google) → Google → `/api/auth/google/callback` (302 back). Each hop is a separate request to the Worker. **CF GeoIP is per-IP**, so the callback's `request.cf.country` matches the user's real location, not Google's edge. The OAuth `state` token does NOT need to carry the country — relying on the callback's `cf` is correct.

### Filter `country === 'XX'`

CF returns `'XX'` when it cannot determine the user's country (VPNs, certain edge nodes, internal traffic). Filter this out before writing:

```js
const country = (request.cf && request.cf.country && request.cf.country !== 'XX')
  ? request.cf.country : '';
```

Otherwise you pollute the data with useless `XX` rows that always look like "top country" in `GROUP BY country` queries.

### Use the data — 30-day country breakdown

```sql
SELECT country,
       COUNT(*) AS users,
       SUM(CASE WHEN oauth_provider != '' THEN 1 ELSE 0 END) AS oauth_users,
       SUM(credits) AS total_credits
FROM users
WHERE created_at >= strftime('%s', 'now', '-30 days') * 1000
  AND country != ''
GROUP BY country
ORDER BY users DESC;
```

This is the canonical "which channels are pulling from which countries" query. Use it 30 days after a launch to kill dead channels and double down on the ones converting best.

### Pitfalls

- **Don't read `request.cf` inside try/catch or async boundaries** without re-reading it — the object is per-request and not on `env`. Always extract at the top of `handleRequest`.
- **`request.cf` is NOT available on assets served from R2 / Workers Assets** — those bypass the Worker entirely. GeoIP capture only works for `/api/*` routes in your own Worker.
- **CF returns 'T1' (Tor), 'XX' (unknown), and country codes** — check `request.cf` shape before assuming ISO codes.

## Custom Domains