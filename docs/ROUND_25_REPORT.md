# Round 25 — Trend API + UI panel

> Status: **1 API + 1 UI panel shipped**. **10 new tests pass**.
> **502 + 20 in the touched-file sweep**. `tsc --noEmit` clean.

R24's `kairos.trend` (CLI-only) is now exposed as a JSON HTTP
endpoint and rendered as a 2-tab web panel.

---

## 1. Trend API ✅

`api/routes/trend.py` (1.9 KB) — two endpoints under
`/api/trend/`:

- `GET /api/trend?directory=results&window=20` — multi-run
  trend (pass_rate, cost, p95 over time)
- `GET /api/trend/per_case?directory=results&window=20` —
  per-case trend (which cases are flaky)

### Tests (10 in `tests/test_trend_api.py`)
- 4 for the trend endpoint (empty dir, returns window, clamp
  to 200, custom pattern)
- 4 for `aggregate_per_case_trend` (basic, flaky-first sort,
  windowed, missing dir)
- 2 for the per-case endpoint (basic + clamp)

### What this unlocks
- The UI can show a live trend without a CLI
- Per-case trend answers "which case has been flaky?" — a
  common ops question that previously required grep

---

## 2. TrendPanel UI ✅

`web/src/components/TrendPanel.tsx` (7.4 KB) — a 2-tab card:

- **Overview** — total runs, avg pass rate, avg cost, cost
  trend, plus a per-run table with pass / cost / p95 / tokens
- **Per-case** — flagged flaky cases (alert at the top) +
  full table with `✓/✗` history sparkline

### What this unlocks
- The user can answer "how is the agent doing?" in 1 second
  without leaving the web
- The per-case view is the **ops console** — pinpoint
  flakiness in seconds

---

## Test summary

| Suite | New | Pass | Skipped |
|---|---:|---:|---:|
| `tests/test_trend_api.py` (10) | 10 | 10 | 0 |
| **`Round 25 new`** | **10** | **10** | **0** |
| All touched files (this round) | — | **502** | **20** |

`tsc --noEmit` clean.
