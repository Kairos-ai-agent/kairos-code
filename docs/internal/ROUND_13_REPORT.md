# Round 13 — Dataset + Derive + PlanPanel + Meta-eval

> Status: **4/4 items shipped**. **19 new tests pass** (14 backend
> + 5 frontend vitest). **344 + 15 in the touched-file sweep**.
> `tsc --noEmit` clean. Meta-eval runs at 5/9 (56%) — the four
> failing cases are the *expected* negatives, not a framework bug.

This round closes the loop on regression detection: every
production session can now become a test case, every kairos
commit can be derived back into a suite, and the eval framework
itself is tested by the same eval framework.

---

## 1. `eval record` + `eval replay` ✅

The eval framework grows persistent state. Two new subcommands:

- **`eval record --run <run.json> --dataset <ds.jsonl>`** —
  appends every passing case (or all, with `--all`) from a
  finished run to a JSONL dataset file. Each line is a
  self-contained JSON object with the case name, score, output,
  details, and recorded timestamp.
- **`eval replay --dataset <ds.jsonl>`** — re-runs every recorded
  case against the *current* agent and returns a new run. A
  regression in the agent that used to pass now fails.

The dataset format is deliberately simple (one JSON per line,
newline-delimited) so it can be `grep`ed, `diff`-ed, and
version-controlled alongside the code. Datasets are append-only:
each record is timestamped so you can see when the agent was
last known-good on a given case.

### What changed
- **`kairos/eval.py`** — new functions:
  - `record_run(run, dataset_path, only_passed=True) -> int`
  - `load_dataset(path) -> list[dict]`
  - `dataset_to_suite_spec(dataset, name="replay") -> dict`
  - `replay_dataset(dataset_path, target, judge) -> SuiteResult`
  - CLI: `record` and `replay` subparsers

### Tests (14 in `tests/test_eval_record.py`)
- `test_record_run_appends_passed_only` — only passing cases
  are appended by default
- `test_record_run_with_all_flag_keeps_everything` — `--all`
  keeps both passes and fails
- `test_record_run_is_append_only` — two record calls → two
  chunks
- `test_record_run_creates_parent_dirs` — auto-creates the
  parent directory
- `test_load_dataset_skips_corrupt_lines` — bad JSONL lines
  are warned + skipped, not fatal
- `test_dataset_to_suite_spec_emits_contains_grader` —
  recorded cases round-trip back into runnable specs
- `test_dataset_to_suite_spec_truncates_output_to_200` — long
  outputs are bounded so the replay graders stay fast
- `test_replay_dataset_runs_all_recorded_cases` — end-to-end
  replay invokes the target once per recorded case
- 6 git-log tests (next item)

### What this unlocks
- "Production regression suite" — every interesting session
  becomes a case, the suite grows over time
- Compare historical runs against the same dataset to catch
  drift
- Can be wired into CI to fail on dataset regression

---

## 2. `eval derive` (auto-extract from git log) ✅

Closes the loop with R12.3 (plan-in-commit-message). The same
commit history that records what the agent did is now the source
of truth for "what should the agent do?"

### What changed
- **`kairos/eval.py`** — new functions:
  - `derive_from_git_log(repo_path, limit=50) -> list[dict]` —
    parses `git log` for `kairos: round N` commits, extracts
    the `# Plan at this round` block, and synthesizes a case
    per commit. The case's `contains` grader pins every todo
    content from the plan, so a future run that drops or
    renames a todo is flagged.
  - `derive_and_write_suite(repo_path, out_path, name, limit)`
    — top-level helper that writes a runnable YAML suite
  - `write_yaml_simple(...)` — private YAML serializer (no
    PyYAML dep) so the suite file is self-contained
  - CLI: `derive` subparser

### Tests (6 in `tests/test_eval_record.py`)
- `test_derive_from_kairos_commits` — 1 kairos commit →
  1 case with 3 needles (the plan's todos)
- `test_derive_skips_non_kairos_commits` — "Initial commit" is
  ignored
- `test_derive_skips_commits_without_plan_block` — older
  kairos commits without the plan header don't synthesize a
  case (avoids false positives on legacy history)
- `test_derive_and_write_suite_writes_yaml` — the YAML
  contains the case name + grader contents
- `test_derive_and_write_suite_no_commits_writes_empty` —
  repos with no kairos commits still produce a valid (empty)
  YAML

### What this unlocks
- "Build the regression suite from history" — `git log` is
  the source of truth, no manual curation needed
- Round 14 can wire this into the CI workflow so every push
  to main re-derives the suite

---

## 3. `PlanPanel` UI (live TodoWrite checklist) ✅

The user can finally see the agent's plan in the UI. The panel
listens to `plan.updated` events from the WebSocket (R11.1) and
renders the live checklist with completion progress.

### What changed
- **`web/src/components/PlanPanel.tsx`** (6.3 KB) — new
  component. Subscribes to the messages array, finds the most
  recent `plan.updated` event, and renders:
  - [x] **completed** items (green, strike-through)
  - [>] **in_progress** items (blue, animated, with the
    activeForm "Doing X…" displayed)
  - [ ] **pending** items (gray)
  - completion ratio (3/5) + progress bar at the top
  - **compact** mode: collapses to a single `<Tag>` showing
    "Plan: 3/5 — Doing X…" — useful when screen real estate
    is at a premium
  - empty state: "No plan yet — the Coder will emit one when
    it starts."
- **`web/src/pages/Loop.tsx`** — imported and rendered below
  the existing Plan visualization card.
- `tsc --noEmit` clean.

### Tests (5 vitest in `web/src/test/planPanel.test.tsx`)
- Renders empty state when no plan is present
- Renders all todos with status markers
- Uses the most recent plan when multiple events exist
- Ignores non-plan messages (the `topic === 'plan.updated'`
  filter)
- Compact mode collapses to a tag with completion count

### What this unlocks
- The user can see "the agent is working on X right now" at a
  glance, not just chat scrolling
- The Coder's self-discipline (TodoWrite) becomes visible — the
  user can see when the agent is making progress vs. going in
  circles
- Round 14 can add a "click to expand" interaction that opens
  the plan history (R12.2)

---

## 4. Self-test the eval framework (meta-eval) ✅

The eval framework is now tested *by itself*. `examples/eval_eval.yaml`
is a 9-case suite that exercises every grader with both positive
and negative cases. If a grader silently changes behavior (e.g.
regex always returns 0.0, or contains stops matching), the meta-eval
catches it before it hides real regressions.

### What changed
- **`examples/eval_eval.yaml`** (1.6 KB) — 9 cases:
  - 2 contains (positive + negative)
  - 2 regex (positive + negative)
  - 2 exact (positive + negative)
  - 1 latency (always passes on the stub)
  - 2 tools_called (positive + missing)
- **`tests/test_meta_eval.py`** (3 tests) — runs the suite
  against a stub target and asserts each grader's documented
  behavior. Also covers the target-exception path.

### Meta-eval results (against stub target)
```
Meta-eval: 56% pass rate (5/9)
  ✓ contains-grader-positive
  ✗ contains-grader-negative      (correctly fails)
  ✓ regex-grader-positive
  ✗ regex-grader-negative         (correctly fails)
  ✓ exact-grader-positive
  ✗ exact-grader-negative         (correctly fails)
  ✓ latency-grader-fast
  ✓ tools-called-grader-positive
  ✗ tools-called-grader-missing  (correctly fails)
```

The 5 passing cases are the "expected to pass" ones (positive
needle / pattern / exact match / latency within budget / called
the right tool). The 4 failing cases are the "expected to fail"
ones — they assert that the grader correctly *rejects* mismatches.

This is the "套套娃" (Russian doll) round: we test the framework
that tests the agent. When the framework is broken, the agent's
regressions become invisible. When the framework works, the
regressions become visible. Catching framework breakage is the
cheapest insurance we can buy.

### What this unlocks
- A broken `regex` grader no longer silently passes every case
- A `contains` grader that always returns 1.0 trips the meta-eval
- CI catches framework regressions before they reach users

---

## Test summary

| Suite | New | Pass | Skipped |
|---|---:|---:|---:|
| `tests/test_eval_record.py` (record + replay + derive) | 14 | 14 | 0 |
| `tests/test_meta_eval.py` (3 tests) | 3 | 3 | 0 |
| `web/src/test/planPanel.test.tsx` (vitest) | 5 | 5 | 0 |
| **`Round 13 new`** | **22** | **22** | **0** |
| All touched files (this round) | — | **344** | **15** |

`tsc --noEmit` clean. Vitest pass.

## What we explicitly did NOT do

- **`kairos eval record` auto-schedule** — record every
  successful run automatically. The user wants to opt in
  per-run, not have it happen invisibly.
- **Frontend history view for plans** — the data is there
  (R12.2 snapshots), the UI shows the *current* plan; the
  timeline view is Round 14.
- **Tying derive to CI** — `eval derive` works but isn't yet
  in `examples/eval_ci_workflow.yaml`. Round 14.
- **Auto-derive on every push to main** — would generate
  fresh regression cases on every merge, but the workflow
  is heavy. Round 14.

## Round-by-round schedule (updated)

| Round | What | Status |
|---|---|---|
| 8-12 | All prior rounds | ✅ |
| 13 | **This round** — record/replay/derive, PlanPanel, meta-eval | ✅ |
| 14 | Plan history UI; CI auto-derive; auto-record on success; cost dashboard (litellm native) | next |
| 15+ | Firecracker / Kata; Textual TUI; Cognee swap | as needed |
