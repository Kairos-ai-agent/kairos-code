# Round 16 — Cost dashboard (API + UI)

> Status: **2/2 items shipped**. **7 new tests pass**.
> **419 + 19 in the touched-file sweep** (R14-16 all green).
> `tsc --noEmit` clean.

This round surfaces the cost data captured by R14's
`litellm.success_callback` as a real dashboard.

---

## 1. Cost dashboard API ✅

Three new endpoints under `/api/cost/`:

- `GET /api/cost/summary` — totals + per-model breakdown.
  Merges the in-memory ring buffer (process-local) with
  the on-disk JSONL log (process-spanning) so a fresh
  server restart shows full history.
- `GET /api/cost/recent?limit=50` — most recent entries
  (newest first), in-memory only.
- `GET /api/cost/by_model` — same merge logic as summary,
  but returns just the per-model map (for cheaper polling).

### What changed
- **`api/routes/cost.py`** (3.6 KB) — new module with the
  three handlers. Uses the `Query` validator for `limit` to
  clamp at 1..10_000.
- **`api/app.py`** — registers the new router under
  `/api/cost`.

### Tests (7 in `tests/test_cost_api.py`)
- `test_summary_returns_total_and_per_model` — disk log
  totals + per-model dict
- `test_summary_in_memory_only_when_buffer_has_entries`
- `test_recent_returns_entries_newest_first` — newest first
  ordering
- `test_recent_respects_limit`
- `test_by_model_returns_aggregates` — disk + in-memory
  merge
- `test_summary_clamps_limit_to_10k` — FastAPI 422 on bad
  input
- `test_summary_handles_missing_log_file` — graceful 0
  response when log doesn't exist yet

### What this unlocks
- The web frontend can poll `/api/cost/summary` every 30s
  to render a live cost panel
- An external Grafana / Datadog can scrape the JSONL log
  directly
- CI can fail the build when per-call cost exceeds a budget

---

## 2. CostDashboard UI panel ✅

Three-card dashboard: total spend, per-model breakdown,
recent calls. Auto-refreshes every 30s.

### What changed
- **`web/src/components/CostDashboard.tsx`** (6.8 KB) — new
  component. Uses `Statistic` for the headline numbers,
  `Table` for the per-model breakdown, and a virtual scroller
  for the recent-calls log. The 30s auto-refresh uses
  `setInterval` and cleans up on unmount.
- **`web/src/pages/Loop.tsx`** — imports and renders the
  panel below `PlanHistoryPanel`.

### What this unlocks
- The user can see per-model spend in real time
- A 30-day cost report is a one-line UI change away
- A cost-per-rubric grader can fail CI when a single eval
  run exceeds a budget

---

## Test summary

| Suite | New | Pass | Skipped |
|---|---:|---:|---:|
| `tests/test_cost_api.py` (7) | 7 | 7 | 0 |
| **`Round 16 new`** | **7** | **7** | **0** |
| All touched files (this round) | — | **419** | **19** |

`tsc --noEmit` clean.
