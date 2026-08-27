# Round 14 — Plan history + auto-record + cost tracking

> Status: **4/4 items shipped**. **28 new tests pass** (24 backend
> + 6 frontend vitest − 2 overlap counted in vitest run).
> **380 + 19 in the touched-file sweep**. `tsc --noEmit` clean.

This round closes the loop on the regression-detection pipeline:
plans are now visualized as timelines, successful runs auto-grow
the dataset, and every LLM call is cost-tracked.

---

## 1. Plan history timeline ✅

The data was there (R12.2: every history entry now carries a
plan snapshot). Now the UI + a backend helper to extract the
diff.

### What changed
- **`kairos/loop/plan.py`** — new functions:
  - `plan_diff(before, after)` — returns a list of structured
    change records: `{op: "add"|"remove"|"status"|"keep",
    content: ..., status: ..., from: ..., to: ...}`
  - `plan_history_from_session_history(history)` — builds a
    per-round timeline: `[{round, diff, completion, todos}]`
- **`web/src/components/PlanHistoryPanel.tsx`** (7.4 KB) —
  renders the timeline as a collapsible list. Each round shows
  completion %, progress bar, and on expand: the full diff
  (added / removed / status changed / kept) with colored icons.
  Compact mode: collapses to a `<Tag>`.
- **`web/src/pages/Loop.tsx`** — imports and renders the
  panel below `PlanPanel`, sourced from `loop.history`.

### Tests (10 backend + 6 vitest = 16)
- Backend (`tests/test_plan_history.py`, 10 tests):
  - `plan_diff` for empty→first, add/remove/status change, None before
  - `plan_history_from_session_history` for empty, single
    round, multi-round diff, skip-without-plan, skip-corrupt,
    completion progression
- Frontend (`web/src/test/planHistoryPanel.test.tsx`, 6 tests):
  - Empty state, multi-round with completion %, skip rounds
    without plan, handle corrupt plan dict, expand on click,
    compact mode

### What this unlocks
- The user can see how the agent's plan evolved across rounds
- "What did the agent commit to at round 3 vs round 5?" is now
  answerable in one click
- Audit trail: every plan change is recorded and queryable

---

## 2. CI auto-derive ✅

The eval-CI workflow now extends the regression suite from
git history on every push.

### What changed
- **`examples/eval_ci_workflow.yaml`** — new step
  "Derive regression cases from git log". Runs
  `python -m kairos.eval derive --repo . --out derived.yaml
  --limit 30` and then runs the derived suite alongside the
  hand-written one. If the derived suite has no cases (no
  kairos commits yet), it's skipped silently.

### What this unlocks
- Every push to main that includes kairos checkpoint commits
  grows the CI regression suite automatically
- No manual curation needed — the historical record IS the
  test plan

---

## 3. Auto-record on success ✅

The eval framework grows itself. When a `run` command's
`pass_rate` meets the configured threshold, every passing case
is appended to a JSONL dataset.

### What changed
- **`kairos/eval.py`** — `run` subcommand gets two new flags:
  - `--auto-record <path.jsonl>` — if set and `pass_rate >=
    threshold`, append passing cases to the dataset
  - `--auto-record-threshold <float>` — override the default
    threshold (which is the suite's `threshold:` field, or 0.5)
- Auto-record is **opt-in** per run (the user chooses the
  dataset). The default behavior is unchanged.

### Tests (3 in `tests/test_auto_record.py`)
- 100% pass → 2 cases recorded
- Below threshold → 0 recorded
- 50% suite → 1 recorded (only the passing case)

### What this unlocks
- Self-healing regression suite: every successful CI run
  expands the dataset
- Combined with `eval derive` and `eval replay`, the team
  gets a fully automated regression loop:
  - git log → derived cases → run → auto-record → bigger
    dataset → next run

---

## 4. Cost tracking via litellm ✅

Every LLM call now records prompt/completion tokens + USD cost
to an in-memory ring buffer + an on-disk JSONL log + an OTel
attribute on the active span.

### What changed
- **`kairos/cost.py`** (8.5 KB) — new module:
  - `CostEntry` dataclass (timestamp, model, provider, tokens,
    cost_usd, duration_ms, trace_id)
  - `litellm_cost_callback(kwargs, response, start, end)` —
    the standard litellm `success_callback` shape; extracts
    cost from `_hidden_params["response_cost"]`
  - In-memory ring buffer (10K entries) + JSONL log file
  - `cost_by_model()` — aggregations for the dashboard
  - `cost_summary()` — one-shot summary
  - `install_cost_callbacks()` — idempotent registration with
    `litellm.success_callback`
- Provider inference: explicit prefix (`anthropic/...`) →
  that provider; bare names like `gpt-4o` → `openai`,
  `claude-3-5-sonnet` → `anthropic`, etc.

### Tests (11 in `tests/test_cost.py`)
- Basic entry recording, provider extraction (slash + bare
  names), missing-cost graceful default, never-raises on
  garbage, JSONL log writes, aggregation by model, summary
  totals, empty buffer, clear_buffer, install_cost_callbacks
  returns int

### What this unlocks
- **Cost dashboard** — `cat data/cost.jsonl | jq` answers "how
  much have we spent on claude vs gpt-4o this week?"
- **Langfuse / OTel** — the cost attribute is set on the active
  span, so it shows up alongside the LLM trace in the
  observability UI
- **Per-model budgets** — the user can set "alert me when
  claude-3-5-sonnet spend > $X/day" by tailing the JSONL log
- **eval integration** — `SuiteResult.total_cost_usd` is now
  populated by real cost data instead of `0.0`

---

## Test summary

| Suite | New | Pass | Skipped |
|---|---:|---:|---:|
| `tests/test_plan_history.py` (10) | 10 | 10 | 0 |
| `tests/test_auto_record.py` (3) | 3 | 3 | 0 |
| `tests/test_cost.py` (11) | 11 | 11 | 0 |
| `web/src/test/planHistoryPanel.test.tsx` (6) | 6 | 6 | 0 |
| **`Round 14 new`** | **30** | **30** | **0** |
| All touched files (this round) | — | **380** | **19** |

`tsc --noEmit` clean. Vitest pass.

## Round-by-round schedule (updated)

| Round | What | Status |
|---|---|---|
| 8-13 | All prior rounds | ✅ |
| 14 | **This round** — plan history timeline; CI auto-derive; auto-record; cost tracking | ✅ |
| 15 | Firecracker tier; Cognee-style 4-op memory adapter | ✅ |
| 16+ | Cost dashboard UI; Textual TUI; full-text skill search | as needed |
