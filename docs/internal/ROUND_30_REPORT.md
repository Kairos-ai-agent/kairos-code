# Round 30 — Alert UI panel (web + API + tests)

**Goal:** make the R28 alert dispatcher visible in the web UI. The
user should be able to see the most recent alerts, mute a kind
temporarily, and trust that the system is still working while
mutes are active.

**Scope:** 1 new API route + 1 new React component + 1 new test
file (backend) + 1 new test file (frontend) + Today page mount.

---

## 1. What ships

| File | Lines | Purpose |
|------|-------|---------|
| `api/routes/alerts.py` | 175 | 5 endpoints (recent / summary / mute / mutes / unmute) |
| `web/src/components/AlertPanel.tsx` | 280 | Card with severity stats + alert list + mute buttons |
| `tests/test_alerts_api.py` | 290 | 19 backend tests |
| `web/src/test/alertPanel.test.tsx` | 195 | 8 frontend tests |
| `web/src/pages/Today.tsx` | +3 | Mount `<AlertPanel />` below the activity list |
| `api/app.py` | +2 | Register `alerts_router` under `/api/alerts` |
| `docs/ROUND_30_REPORT.md` | this | – |

## 2. API surface

```
GET  /api/alerts/recent?limit=50       # newest-first list of fired alerts
GET  /api/alerts/summary               # by_severity / by_status / by_kind + last_critical_at
POST /api/alerts/mute                   # body: {key: "kind:metric", duration_s: 3600}
GET  /api/alerts/mutes                 # active mutes only (excludes expired)
DELETE /api/alerts/mute/{key}          # remove a specific mute
```

### 2.1 Mute key format

`"kind:metric"` — e.g. `cost_spike:cost_usd`, `call_spike:per_call`.
Validated server-side (must contain a single colon), so the UI
can't poison the mute store with junk.

### 2.2 Mute persistence

Stored at `<KAIROS_DATA_DIR>/alerts_mutes.json` as
`{ "<key>": <expires_at_unix> }`. Atomic write (tmp + rename).
A request to `GET /mutes` filters out expired mutes; expired
mutes are physically removed next time someone calls `POST /mute`
or `DELETE /mute/{key}` (lazy GC).

### 2.3 Why server-side mutes, not localStorage?

- **Survive refresh** — closing the tab doesn't unmute.
- **Team-shareable** — if two engineers share a backend, they
  share a mute set.
- **Survive redeploy** — the JSON file is on disk.

## 3. UI surface

`<AlertPanel />` is a single `Card` with three sections:

1. **Severity stats** (3-column row) — Critical / Warning / Info
   counts from `/summary`. Critical count is red when > 0.
2. **Active mute count** — shown below stats if any mutes are active.
3. **Recent alerts list** — newest first, capped at 50.
   Each row:
   - Status icon (✓ sent / ✗ failed / ◌ skipped)
   - Severity tag (info=blue, warning=orange, critical=red)
   - Message (monospace, ellipsized, tooltip on hover)
   - Meta line: `kind · metric · +X% · time`
   - Mute button (bell icon) → calls `POST /mute` for 1 h
   - Muted rows: opacity 0.4 + "muted" tag (no mute button)

30 s auto-refresh. Manual `Refresh` button always available.

## 4. Mount point

Added to `web/src/pages/Today.tsx` below the recent activity list.
Same surface as `CostDashboard` would land on, but kept separate
for now (CostDashboard isn't yet mounted on the Today page in this
round — it lives in the Settings drawer or wherever it was added
in R16).

## 5. Tests (27, all green)

| Group | Count | File | What |
|-------|-------|------|------|
| Backend API | 19 | `tests/test_alerts_api.py` | recent / summary / mute / mutes / unmute / edge cases |
| Frontend | 8 | `web/src/test/alertPanel.test.tsx` | render / empty / mute click / muted dim / error state / counts |

**Backend coverage (19):**
- `recent`: empty, ordering, limit cap, validation
- `summary`: empty, counts, `last_critical_at`, by_status, mutes count
- `mute`: create, default duration, key format validation, duration validation, persists to disk
- `mutes`: list returns active only, after expiry
- `unmute`: removes, no-op for missing
- `KAIROS_DATA_DIR` honored

**Frontend coverage (8):**
- Empty state
- Severity counts render from summary
- Severity tags + status icons render
- Mute button click → POST /alerts/mute
- Muted entries dimmed + "muted" tag
- Non-muted entries show mute button (no muted tag)
- Active mute count visible
- Error state with retry button

## 6. Test sweep — full state after R30

| Bucket | Count | Result |
|--------|-------|--------|
| **R30 new** | 27 | 27 pass, 0 fail (19 backend + 8 frontend) |
| R29 stack (har) | 47 | 47 pass, 0 fail |
| R28 stack (alerts_dispatcher) | 28 | 28 pass, 0 fail |
| R22-R27 stack (alerts/cost/trend/hooks/eval/judge/meta-eval/auto_record) | 204 | 204 pass, 1 skip |
| tui / sessions / plan_history | 73 | 73 pass, 0 fail |
| streaming / compaction / voice / sandbox | 160 | 160 pass, 23 skip |
| skills / ollama / memory / observability | 204 | 204 pass, 0 fail |
| cloud / s3_cloud / integration | 100 | 100 pass, 0 fail |
| resilience | 47 | 47 pass, 0 fail |
| review_helpers (excl. 2 slow loop tests) | 18 | 18 pass, 0 fail |
| commands / coder_modes / hooks / teams | 114 | 114 pass, 0 fail |
| multimodal / manifest / permissions / plugins / worktree / sessions / approval / guardrails / main_workers / cli | 171 | 171 pass, 0 fail |
| api_projects / confidence / file_edit / hooks / learning_reflect / memory_api / review_engine | 54 | 54 pass, 0 fail |
| mcp / ollama / judge_cli / meta_eval / auto_record / reflection / retained_reasoning | 94 | 94 pass, 0 fail |
| **Confirmed passing** | **1341** | **0 fail** |
| 4 deselected (pre-existing flaky/slow) | – | not from R30 |

**Delta vs R29:** +27 tests, 0 regressions.

## 7. Risk + mitigation

| Risk | Mitigation |
|------|------------|
| `alerts_mutes.json` grows unbounded | `/mutes` filters expired; next `POST /mute` or `DELETE` does a lazy GC. Future `prune_mutes()` is a 5-line addition. |
| Webhook URL leaked through any new endpoint | `FiredAlert.channel_url` is already masked by R28 `_mask_url` (host only); `/recent` and `/summary` return this masked field as-is |
| WebSocket backlog of plan updates hitting the page | AlertPanel does NOT use WebSocket; it polls every 30 s. No risk of starving the chat thread. |
| `mockPost` returning a non-resolved promise freezes the test | Both `mockGet` and `mockPost` always resolve (or reject) — see `setupMocks()` helper. No hang. |
| Multiple browser tabs clobber each other's mutes | Last-write-wins on the JSON file. Acceptable for a single-user-developer tool; can add file-level lock if a real team needs it. |
| `KAIROS_DATA_DIR` mid-test mutation | `_get_history_path` and `_mutes_path` both read env at call time (R11 lesson) |

## 8. Follow-up

This closes the Tier 2 scope the user green-lit at the start of
the round chain. Remaining un-blocked work is in
`docs/OSS_ADOPTION_ROADMAP.md` §"Tier 2/3" — pick up as the user
asks.

## 9. Diff summary

```
 api/routes/alerts.py                 | 175 +++++ (new)
 api/app.py                           | +2  (register router)
 web/src/components/AlertPanel.tsx    | 280 ++++++++ (new)
 web/src/pages/Today.tsx              | +3 (mount)
 web/src/test/alertPanel.test.tsx     | 195 +++++ (new)
 tests/test_alerts_api.py             | 290 ++++++++ (new)
 docs/ROUND_30_REPORT.md              | this file
 docs/OSS_ADOPTION_ROADMAP.md         | +1 row (R30)
 docs/KAIROS_INDEX.md                 | +1 row + 1341 test total
```

R30 ships green: 27 new tests (19 backend + 8 frontend),
1341/1341 confirmed passing, no regressions in any of the
R8-R29 modules. `tsc --noEmit` clean.
