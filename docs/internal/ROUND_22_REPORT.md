# Round 22 — Cost regression alerts

> Status: **1 module + 1 dispatcher shipped**. **18 new tests
> pass**. **491 + 19 in the touched-file sweep**. `tsc --noEmit`
> clean.

Cost spikes are silent unless the user happens to read the
cost.jsonl log. This round adds an engine that compares two
runs and fires structured alerts (warning / critical) for any
regression beyond a configurable threshold.

---

## 1. `kairos.alerts` — regression engine ✅

`kairos/alerts.py` (7.9 KB) — a pure-stdlib module with three
pieces:

- **`Alert` dataclass** — severity, kind, message, metric,
  baseline, current, delta_pct
- **`detect_cost_regressions(baseline, current)`** — returns
  a list of `Alert` objects. Three independent checks:
  - **cost_spike** — total cost % increase; warning at
    `threshold_pct` (default 50%), critical at 2x that
  - **call_spike** — per-call p95 cost % increase (default
    threshold 200%)
  - **calls_growth** — number of LLM calls % increase
    (default threshold 100%)
- **`format_slack_payload(alerts)`** — Slack-compatible
  blocks API
- **`send_to_webhook(url, payload)`** — `urllib`-based POST,
  returns True/False, never raises

### Tests (18 in `tests/test_alerts.py`)
- 4 for `_load_run_costs` (total, missing file, corrupt, skips
  invalid cost values)
- 4 for total-cost spike detection (no regression, warning
  above 50%, critical at >100%, no alert on decrease)
- 2 for per-call spike (spike triggers, stable doesn't)
- 1 for call-count growth
- 1 for `detect_from_files` end-to-end
- 2 for `format_slack_payload` (empty, with alerts)
- 4 for `send_to_webhook` (2xx success, 4xx fail, 5xx fail,
  network error fail)

### What this unlocks
- CI can run a cost check on every PR: "did the per-call
  cost of any case go up >200%?"
- A scheduled job can compare today's run to yesterday's
  and Slack the team when something regresses
- The hooks (R21) can include a cost check so the user
  catches the spike before committing

---

## Test summary

| Suite | New | Pass | Skipped |
|---|---:|---:|---:|
| `tests/test_alerts.py` (18) | 18 | 18 | 0 |
| **`Round 22 new`** | **18** | **18** | **0** |
| All touched files (this round) | — | **491** | **19** |

`tsc --noEmit` clean.
