# Round 35 — COGS (Cost of Goods Sold) value metrics endpoint

**Goal:** the R16 cost dashboard shows *how much* you spent. R35
adds *what you got for it* — the "value" side. Now you can see:

  - `$X` per eval case
  - `$X` per passing case
  - `$X` per alert fired
  - efficiency (passing / total)
  - approval yield (1 − critical_alert_ratio)

**Scope:** 1 new endpoint in the existing `api/routes/cost.py` +
1 new test file + 1 docs update. No new module, no new UI panel
— the R16 `CostDashboard.tsx` can be extended to call this later.

---

## 1. What ships

| File | Lines | Purpose |
|------|-------|---------|
| `api/routes/cost.py` | +120 | New `GET /api/cost/value` endpoint + helper |
| `tests/test_cost_value.py` | 240 | 11 tests covering the endpoint |
| `docs/ROUND_35_REPORT.md` | this | – |

## 2. The endpoint

```
GET /api/cost/value
```

Response shape:

```json
{
  "total_cost_usd": 1.00,
  "n_llm_calls": 2,
  "dataset": {
    "total_cases": 4,
    "passed": 3,
    "failed": 1
  },
  "alerts": {
    "total": 3,
    "critical": 1
  },
  "metrics": {
    "cost_per_case": 0.25,
    "cost_per_passing": 0.333333,
    "cost_per_alert": 0.333333,
    "efficiency": 0.75,
    "approval_yield": 0.666667
  }
}
```

## 3. The 5 derived metrics

| Metric | Formula | Why it matters |
|--------|---------|----------------|
| `cost_per_case` | `total_cost / total_eval_cases` | How much you spent per eval case (R13 records) |
| `cost_per_passing` | `total_cost / passing_cases` | Cost of a "successful" unit of work |
| `cost_per_alert` | `total_cost / alert_count` | Cost efficiency vs. the alert system (R22/R28) |
| `efficiency` | `passing / total` | Pass rate (0-1); 1.0 = all work passed first try |
| `approval_yield` | `1 - critical_alerts / total_alerts` | Fraction of work that didn't need human intervention |

## 4. Edge cases

- **Zero denominators return `None`** (not 0 or NaN) for:
  - `cost_per_case` when no eval cases exist
  - `cost_per_passing` when no cases passed
  - `cost_per_alert` when no alerts were ever fired
  - `efficiency` when no cases exist
  - `approval_yield` when no alerts exist
- **Zero numerator with non-zero denominator returns 0.0**
  (the implementation is `round(num/den, 6) if den > 0 else None`).
  This is a real answer ("$0 spent per case") — the UI should
  distinguish None (N/A — no data) from 0.0 ($0 per case).
- **Corrupt JSONL lines are silently skipped** in cost / alerts /
  datasets (the data sources can be edited by hand or written
  concurrently).

## 5. Why no new module — fits in the cost router

The R16 `api/routes/cost.py` already reads from `data/cost.jsonl`.
R35 reads from the same file plus `data/datasets/*.jsonl` (R13/R19)
and `data/alerts.jsonl` (R28). Three existing data sources, one
new endpoint, no new dependencies.

## 6. Tests (11, all green)

`tests/test_cost_value.py` covers 5 sub-groups:

| Group | Count | What |
|-------|-------|------|
| Empty state | 1 | All `None` metrics when no data exists |
| Single-source | 2 | Cost-only (case metrics are None); dataset-only (cost metrics are 0.0) |
| Full state | 1 | All 5 ratios computed correctly with realistic inputs |
| Robustness | 4 | Skips corrupt cost/alert/dataset lines; approves-yield None when no alerts; all-failures → efficiency = 0.0 |
| Integration | 3 | Honors `KAIROS_DATA_DIR`; aggregates across multiple dataset files; pre-clears the cost log path cache (R11 lesson) |

**Total: 11 / 11 passing in 5.02 s.**

### 6.1 Notable test design

- **`_LOG_PATH` cache reset** — `kairos.cost._LOG_PATH` is
  module-level cached. The fixture clears it
  (`cost_mod._LOG_PATH = None`) so each test gets a fresh
  path resolution from `KAIROS_DATA_DIR`. (R11 lesson.)
- **None vs 0.0 distinction** — tests assert *which* case applies
  (no data → None; data with zero numerator → 0.0).
- **Multi-dataset aggregation** — `test_value_aggregates_across_multiple_datasets`
  writes 2 dataset files and confirms cases sum correctly.

## 7. Test sweep — full state after R35

| Bucket | Count | Result |
|--------|-------|--------|
| **R35 new tests** | 11 | 11 pass, 0 fail |
| R34 stack (adapt_community_skills) | 19 | 19 pass, 0 fail |
| R33 stack (adapt_anthropic + 3 skills) | 5 | 5 pass, 0 fail |
| R32 stack (doctor) | 37 | 37 pass, 0 fail |
| R31 stack (adapt_anthropic) | 16 | 16 pass, 0 fail |
| R30 stack (alerts API + UI) | 27 | 27 pass, 0 fail |
| R29 stack (har) | 47 | 47 pass, 0 fail |
| R28 stack (alerts_dispatcher) | 28 | 28 pass, 0 fail |
| R22-R27 stack | 204 | 204 pass, 1 skip |
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
| **Confirmed passing** | **1429** | **0 fail** |
| 4 deselected (pre-existing flaky/slow) | – | not from R35 |

**Delta vs R34:** +11 tests, 0 regressions.

## 8. End-to-end smoke test

```bash
$ curl -s http://localhost:8000/api/cost/value | jq
{
  "total_cost_usd": 0.0,
  "n_llm_calls": 0,
  "dataset": { "total_cases": 0, "passed": 0, "failed": 0 },
  "alerts": { "total": 4, "critical": 1 },
  "metrics": {
    "cost_per_case": null,
    "cost_per_passing": null,
    "cost_per_alert": 0.0,
    "efficiency": null,
    "approval_yield": 0.75
  }
}
```

(The dev env has 4 alerts from R28-30 testing, 1 critical → `approval_yield = 1 - 1/4 = 0.75`.)

## 9. Risk + mitigation

| Risk | Mitigation |
|------|------------|
| `_LOG_PATH` cached at import time so test 1 leaks into test 2 | Fixture clears `cost_mod._LOG_PATH = None` (R11 lesson) |
| Cost log can grow large → slow parse | Cap implicit by file size; future: tail last 10K lines (R16 already does `limit=10_000`) |
| 3 separate disk reads (cost + alerts + datasets) on every call | All 3 are JSONL tail-reads; the endpoint is cheap (~10ms). If it becomes hot, can cache with a short TTL. |
| User confuses `None` (N/A) with `0.0` (real $0) in the UI | Documented in the response shape; the UI should render "N/A" for None and "$0" for 0.0 |
| `total_cost_usd` doesn't include the in-memory ring buffer | The `/summary` endpoint already merges in-memory + disk. R35 uses disk-only so the metric is reproducible across server restarts. The UI can show `/summary` for the live view and `/value` for the analytics view. |

## 10. Follow-up

- **Extend `CostDashboard.tsx`** to show the 5 COGS metrics
- **Add a trend over time** — track `cost_per_passing` per eval
  run, so the user can see "efficiency is improving / degrading"
- **Per-model COGS** — break down `cost_per_passing` by the
  model that produced it (R8's litellm provider already has the
  data)
- **Tier 3 picks** (inspiration only)

## 11. Diff summary

```
 api/routes/cost.py             | +120 (new endpoint + helper)
 tests/test_cost_value.py       | 240 (new)
 docs/ROUND_35_REPORT.md       | this file
 docs/OSS_ADOPTION_ROADMAP.md  | +1 row (R35)
 docs/KAIROS_INDEX.md          | 26 skills + +11 test total
```

R35 ships green: 11 new tests, 1429/1429 confirmed passing, no
regressions in any of the R8-R34 modules. `tsc --noEmit` clean.
