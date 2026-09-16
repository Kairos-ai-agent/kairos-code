---
name: "data-pipeline-debugging"
description: "Debug empty/stale/wrong results in data pipelines."
priority: 0.5
version: "1.0.0"
imported-from: "hermes"
source-path: "hermes/skills/software-development/data-pipeline-debugging/SKILL.md"
---
# Data-Pipeline Debugging

Fix empty, stale, or wrong outputs in a first-party data/ML pipeline — when the code "runs" and each layer reports success but the numbers are missing, wrong, or won't recompute. These are dependency-drift failures, not logic bugs, so they hide behind silent `except: pass` guards and batch loops.

Apply when: `/api` returns 0 items; a model is `ready=True` but yields nothing; retrain can't start; a stored value (cash, model, account) looks wrong or outdated; source looks fixed but a packaged exe still misbehaves; a progress/status panel says "no tasks yet" while a job is running.

## Step 1 — Surface the swallowed error before touching anything

Batch loops (scan-all, batch predict) wrap each row in `try/except: continue`, so per-row failures vanish into "0 results, no error". Do NOT trust a status()/scan() call. Call the **single-item path** for one concrete input — it returns the real exception string:

```python
tm.predict('sh601318')   # -> {'code':..., 'error': "['north_hold_pct',...] not in index"}
```

That error names the missing columns immediately. `status()` only tells you `ready=True / 0 items`. If the single-item path also swallows, add a temporary bare `except` that re-raises with the traceback to a file.

## Step 2 — Verify the data source, not the logic

When a count/score/store is wrong, check whether the feed behind it is actually up *right now*, using the app's own wrapper (not just curl — the wrapper may gate a column or parse a different shape):

```python
print(ds.akshare_status())             # -> {'akshare_available': False, ...}
print(len(ds.get_northbound_series('sh601318')))   # -> 0
```

A model trained while a source was up keeps that source's columns in `meta['features']` forever; the runtime must tolerate the source going down.

## Step 3 — Pitfall: optional-source columns silently dropped by `except: pass`

A pipeline that merges columns from an *optional* source (akshare, northbound, fundamentals, a live API that can be down) inside `try: ... except: pass` loses those columns **silently** when the source is down. Downstream `df[features]` raises `KeyError: [...not in index]` for **every** row.

**Fix (rule):** before indexing `df[features]`, guarantee every feature column exists; fill missing ones with NaN. GBMs (XGBoost/LightGBM) handle NaN natively, so this degrades gracefully:

```python
for c in FEATURES:
    if c not in df.columns:
        df[c] = float('nan')
```

- Place the fill at the END of the frame builder / merge function, so every caller gets the full column set.
- Never dedupe/adjust the feature list at predict time (dropping missing columns) — that changes input width and breaks `predict_proba` on a shape mismatch.
- The guard is required even when "it worked at train time": the feature list is baked into `meta['features']` and must exist as columns (NaN is fine) whether or not the source is up, keeping train and predict consistent.

## Step 4 — Pitfall: packaged app reads a different data store than the source tree

A PyInstaller `onedir` app keeps persistent state under `_internal/<pkg>/data`, a SEPARATE store from the source tree's `<pkg>/data`. "Stale/wrong data" (wrong cash, old model, old account) in a packaged app is usually this divergence, not a logic bug.

1. Enumerate ALL stores: `search_files` for the data filename across the whole project (`paper_account*.json`, `*_meta.json`) — you will find both the source copy and the `_internal` copy.
2. Compare modification times; the older store is what the packaged app reads. Timestamps of the exe itself tell you when it was last rebuilt.
3. A source-code fix is NOT in the packaged exe until it is rebuilt — the exe bundles its own `.py` and its own data dir. If source looks fixed but the exe still misbehaves, the exe is an old build.

## Step 5 — Empty list from an event-log endpoint (chat / audit history)

A history endpoint that returns `[]` while the UI shows a friendly empty state is the
same silent failure: something swallowed an error, or the query asked for the wrong rows.
Check both, in this order.

**5a. Read the raw response before touching the client.** A client that maps any non-200
to "offline" renders an empty list, not an error banner. Call the endpoint directly and
print status + body:

```bash
curl -s -w '\nHTTP:%{http_code}\n' 'http://localhost:8000/api/projects/<id>/chat-messages?limit=3'
```

Typical backend cause of a permanent 500 on such a route: a local
`from api.deps import orchestrator as _orch` shadows a module-level `def _orch()` helper
of the same name, so `_orch(...)` calls the imported instance →
`TypeError: '<X>' object is not callable` on every request. Call the module-level helper
instead; never import a same-named object into the module that defines the helper (keep
the helper so tests can still monkeypatch it).

**5b. The newest-N window may be all machine noise.** Event tables mix human-readable
rows with high-volume deltas/telemetry (`stream.chunk` streaming fragments, `tool.*` /
`loop.*` events). `ORDER BY ts DESC LIMIT 200` can return nothing but noise while the
real records sit thousands of rows back — and raising the limit just ships more noise.
Fix at the query layer:

- Filter with an explicit topic/type keep-list (`chat_only`) in SQL, never by filtering
the full payload in the client.
- Page with a keyset cursor over `(timestamp, id)` —
  `WHERE (ts, id) < (?, ?) ORDER BY ts DESC, id DESC LIMIT n` — and return `has_more` +
the next cursor; the client loops until exhausted.
- Keep `n` bounded (a few thousand); a cursor beats a bigger limit on a table holding
tens of thousands of delta rows.

**5c. The writer may persist only one side of the conversation.** If the DB stores
assistant replies but never the user's own messages, the history is structurally
incomplete, the browser's local cache is the only copy of the user's half, and loading
history overwrites it. Persist the missing side with the same envelope (sender + topic +
`metadata.project_id`), then merge DB rows with the locally cached thread:

- dedupe by message id first;
- for the user's optimistic bubble (locally generated id) fall back to `content` +
  `|Δt| ≤ 5s` so the DB copy and the local copy collapse into one bubble;
- never dedupe on content alone — two genuinely identical messages must both survive.

**5d. Verify by counting.** Compare the DB row count for the keep-list topics with the
number of bubbles the client renders, and repeat on a second project. "It shows something
now" is not verification.

## Step 6 — Progress/status panel shows nothing while work is running

Same silent-empty class as Step 3, now in a UI panel that reports "no tasks yet" even
though a job is running. Check these causes in order.

**6a. The shape assumed in the code is not the shape in the store.** Dump one real record
before touching the renderer (curl the endpoint, or `sqlite3` one row) and print `type()` +
keys. A snapshot stored as `{"todos": [...], "updated_at": ...}` walked with
`for i, step in enumerate(plan)` yields its KEYS; an `isinstance(step, dict)` guard then
drops every entry and the endpoint returns `[]` with no error. Never infer a payload shape
from field names — assert it from one live record.

**6b. Per-row state must come from the row, not from a global flag.** Deriving status as
`running ? in_progress : done` erases each item's own status and mislabels completed rows.
Map the item's own status (`completed`/`in_progress`/`pending`) and keep the global flag
only for rows that carry no status of their own.

**6c. Rebuild from the persisted event log, not only from the in-memory object.** An
endpoint that reads a live session/state object returns `[]` after any restart — watchdog,
crash, redeploy. Replay the persisted events for the relevant topics (`plan.updated`,
`loop.coder_started`, `task.result`, `task.error`) to reconstruct the list, and prefer the
persisted snapshot when the live object is gone.

**6d. Degrade through explicit fallback levels so the list is never empty.** e.g. decomposed
plan items → one row per completed round (title = first line of that round's summary) → the
requirement text itself. Return a `source` field naming the level that produced the rows, so
tests and the UI can assert which path ran. "If a task exists at all, the panel is non-empty"
is the acceptance criterion.

Three follow-ups that decide whether the panel actually updates:

- **A progress UI needs a refresh path.** A panel that loads once on mount/project-switch can
  never show incremental completion. Subscribe to the relevant WS events for a silent refetch
  and add a bounded interval poll (fast while running, slow when idle).
- **A new status value is an API change.** Before adding one (e.g. `rejected` distinct from
  `failed`), grep every consumer for the status union / label map / icon switch and update
  all of them, or the new state renders blank.
- **An empty table may have no writer.** Grep the call sites of the persistence method
  (`save_loop_round`) before blaming the reader; an uncalled writer leaves the table
  permanently empty while every read looks correct.

## Companion pitfall — "data sink drift" (related)

The same diagnostic instinct applies when a frontend reads a store that the backend doesn't write (or writes a sibling store): the consumer reads from the unwritten location. Enumerate the two ends of any data flow — who writes the store the consumer reads? — before assuming the value is computed wrong. See the `systematic-debugging` skill's "data sink drift" section for the parallel-stores variant.

## Resolution checklist

- [ ] Single-item path surfaces the real error (no silent 0)
- [ ] Optional-source columns present (NaN-filled) in every frame
- [ ] Data source verified up/down via the app's wrapper
- [ ] If packaged: which store does the consumer read? Compare timestamps across source and `_internal`
- [ ] Rebuilt the exe if the fix must reach it
- [ ] History endpoint's raw status/body checked before blaming the UI
- [ ] Event-log query filters machine topics in SQL and pages with a keyset cursor
- [ ] Both sides of the conversation persisted; local cache merged id-first, content+time fallback
- [ ] Progress endpoint asserts the stored shape from one real record (no dict walked as a list)
- [ ] Per-row status read from the row; fallback chain guarantees non-empty and reports `source`
- [ ] Progress list rebuildable from the persisted event log after a restart
- [ ] New status value propagated to every consumer (union / label map / icon switch)
