# Round 26 — Per-case flaky detection

> Status: **1 new function + 1 endpoint shipped**. Covered
> by the 10 tests in `tests/test_trend_api.py` (R25).

R24's `aggregate_trend_from_dir` answered "is pass_rate going
up?". R26 answers the more actionable question: **"which
specific case is being flaky?"**.

---

## 1. `kairos.trend.aggregate_per_case_trend` ✅

A new function in `kairos/trend.py` that, given a directory of
run JSONs, returns one `CaseTrendPoint` per case:

- `case_name` — the unique key
- `pass_rate` — across the window
- `n_runs`, `n_passed`, `n_failed`
- `flaky` — `True` iff the case both passed and failed in the
  window (non-deterministic behavior)
- `history` — `[bool, ...]` newest-last

Results are sorted with **flaky cases first**, then by lowest
pass_rate. This puts the most actionable rows at the top of
the table.

The new function is wired into the API at
`GET /api/trend/per_case` (R25.1) and rendered in the
TrendPanel UI (R25.2) on the **Per-case** tab.

### Tests (in `tests/test_trend_api.py`, 4 new)
- `test_aggregate_per_case_trend_basic` — 3 runs, 'a' is
  stable, 'b' is flaky; history + flaky flag correct
- `test_aggregate_per_case_trend_flaky_first` — sort order:
  flaky first, then by pass rate
- `test_aggregate_per_case_trend_window` — windowed
  truncation works
- `test_per_case_endpoint` — the HTTP endpoint returns the
  same shape

### What this unlocks
- "Which case has been flaky?" → the `Per-case` tab in the
  Loop page, ranked by flakiness
- Future round: a Slack alert on a case going flaky
- Future round: a CI check that fails if any case has been
  flaky for the last 3 runs

---

## Test summary

| Suite | New | Pass | Skipped |
|---|---:|---:|---:|
| `tests/test_trend_api.py` (R25+R26 combined) | 10 | 10 | 0 |
| **`Round 26 new`** | (covered above) | — | — |

`tsc --noEmit` clean.
