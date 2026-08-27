# Round 24 — Multi-run trend aggregator

> Status: **1 module shipped**. **15 new tests pass**.
> **491 + 19 in the touched-file sweep**. `tsc --noEmit` clean.

R10's `eval compare` answered "did run A differ from run B?".
This round answers the bigger question: "how is the agent
doing over the last N runs?"

---

## 1. `kairos.trend` — multi-run trend aggregator ✅

`kairos/trend.py` (7.6 KB) — a pure-stdlib module that reads
a directory of run JSONs and emits a `TrendReport` summarizing
the time series.

### What it computes
- `n_total` — number of runs in the window
- `n_passed` — runs with `pass_rate >= 0.5`
- `avg_pass_rate` — mean pass rate across the window
- `avg_cost_usd` — mean cost across the window
- `cost_first_to_last` — % delta from first to last
- `pass_rate_first_to_last` — pp delta from first to last
- Per-run `RunPoint`: timestamp, suite, run_id, cases, passed,
  pass_rate, cost_usd, tokens, avg_duration_ms, p95_duration_ms

### CLI
```bash
python -m kairos.trend results/ --window 20 --out trend.json
```

Sample output:
```
Suite: smoke
Runs: 12  (avg pass_rate=68%)
Cost: avg=$0.0432  trend=+12.3%
Pass rate trend: +8.2 pp

timestamp             suite                    pass    cost       p95     path
-------------------------------------------------- ... 
2026-08-20T08:30:12Z  eval-ci-baseline        60%   $0.0321  220ms  results/run-1.json
2026-08-22T11:15:48Z  eval-ci-baseline        75%   $0.0431  245ms  results/run-2.json
...
```

### Tests (15 in `tests/test_trend.py`)
- 4 for `_p95` (empty, single, n<20 returns max, n≥20 quantile)
- 4 for `_load_run` (extracts fields, missing file, corrupt,
  no-cases)
- 4 for `aggregate_trend` (explicit files, window truncates,
  empty, n_passed via real files)
- 2 for `aggregate_trend_from_dir` (discovers runs, missing
  directory)
- 1 for `TrendReport.to_dict` round-trip

### What this unlocks
- "How is the agent doing this week?" → one command
- "Which case has been flaky for 3 runs?" → future
  per-case trend view
- Cost trend alerts in CI ("cost grew 30% in a week")

---

## Test summary

| Suite | New | Pass | Skipped |
|---|---:|---:|---:|
| `tests/test_trend.py` (15) | 15 | 15 | 0 |
| **`Round 24 new`** | **15** | **15** | **0** |
| All touched files (this round) | — | **491** | **19** |

`tsc --noEmit` clean.

## Round-by-round schedule (updated)

| Round | What | Status |
|---|---|---|
| 8-21 | All prior rounds | ✅ |
| 22 | Cost regression alerts (engine + webhook dispatcher) | ✅ |
| 23 | Frontend skill search UI (Ctrl+K palette) | ✅ |
| 24 | Multi-run trend aggregator (time series) | ✅ |
| 25+ | Per-case trend; trend UI; alert webhooks in CI | as needed |
