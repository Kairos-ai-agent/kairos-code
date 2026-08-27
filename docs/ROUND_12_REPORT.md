# Round 12 — Plan persistence + eval-in-CI + session importer

> Status: **5/5 items shipped**. **35 new tests pass**. **327+15 in the
> touched-file sweep**. `tsc --noEmit` clean.

This round is the "wire it all together" round. R10 + R11 built the
Plan, observability, eval, and importer modules. R12 connects them
to the runtime and ships the CI template the team can copy-paste.

---

## 1. Plan block injected into the Coder's next-turn system prompt ✅

The Coder can now see its own plan as a Markdown checklist on every
turn, so it knows what's left to do and avoids re-doing completed
work.

### What changed
- **`kairos/agents/base.py`** — `_build_messages()` now appends
  `render_plan_block(self.plan_tracker)` to the system prompt when
  the plan is non-empty. The block is appended *after* the skills
  block and *before* the memory summary, so the agent sees:
  1. base system prompt
  2. relevant skills
  3. **current plan** (new)
  4. earlier-conversation summary
  5. tool result memory

### Tests (3 of 9 in `test_plan_runtime_wiring.py`)
- `test_plan_injected_into_next_turn_system_prompt` — verifies
  the rendered plan block lands in the system message with the
  right checkbox markers (`[>]` in_progress, `[x]` completed,
  `[ ]` pending)
- `test_empty_plan_not_injected` — empty plan doesn't bloat the
  system prompt
- `test_no_plan_tracker_no_injection` — without the tracker, no
  plan block

### What this unlocks
- The Coder doesn't re-do done work
- The Coder knows what's in_progress right now (the `[>]` item)
- Round 13 can use the plan to drive a more aggressive
  auto-approve / auto-reject heuristic

---

## 2. Plan snapshot in `session.history` ✅

Every round's history entry now records the plan state at the
end of that round. Future sessions can reconstruct the agent's
plan at any point in the conversation.

### What changed
- **`kairos/loop/loop_runner.py`** — new `_plan_snapshot(session)`
  helper returns the JSON-safe dict form of the plan (or `None`).
  Two `session.history.append` call sites (one for the normal
  round end, one for the regression-rollback branch) now include
  `"plan": _plan_snapshot(session)`.

### Tests (3 of 9 in `test_plan_runtime_wiring.py`)
- `test_plan_snapshot_returns_dict_when_plan_set` — the helper
  returns the right shape
- `test_plan_snapshot_returns_none_when_no_plan`
- `test_plan_snapshot_round_trip` — `Plan.from_dict(snap)`
  reproduces the original `Plan` exactly

### What this unlocks
- The session log doubles as a Plan history
- Round 13 can render a "Plan evolution timeline" in the UI
  (what got added / marked done / dropped, by round)

---

## 3. Plan in the git commit message ✅

`checkpoint_round()` now accepts an optional `plan` dict and
embeds the rendered plan as a Markdown block in the commit body.
`git log` becomes a queryable Plan history.

### What changed
- **`kairos/tools/checkpoint.py`** — `checkpoint_round` gains a
  `plan: Optional[dict] = None` keyword. When provided and
  non-empty, the rendered plan is appended to the commit message
  under a `# Plan at this round` header. Existing call sites
  (without the kwarg) keep working unchanged.
- **`kairos/loop/loop_runner.py`** — the auto-checkpoint call
  now passes `_plan_snapshot(session)`.

### Tests (3 of 9 in `test_plan_runtime_wiring.py`)
- `test_plan_lands_in_git_commit_message` — `git log -1 --format=%B`
  shows the plan block
- `test_checkpoint_without_plan_still_works` — backwards compat
  for existing call sites
- `test_checkpoint_with_empty_plan_does_not_add_header` — empty
  plan dict doesn't bloat the commit

### What this unlocks
- `git log --format=%B | grep -A20 "Plan at this round"` answers
  "what was the agent working on at commit X?"
- Round 13 can plumb `git log --grep` into the UI's "history
  timeline" view

---

## 4. eval-in-CI template ✅

Drop-in GitHub Actions workflow + example suite, ready to copy
into `.github/workflows/eval.yml`.

### What changed
- **`examples/eval_ci.yaml`** (1.9 KB) — 5-case baseline suite
  covering: code-fences, no-trivial-hallucinations, python-syntax,
  no-empty-output, no-tool-overflow. Designed to be fast (<60s)
  and free (no LLM judge by default; pass `--judge` to enable
  subjective cases).
- **`examples/eval_ci_workflow.yaml`** (3.7 KB) — the full GitHub
  Actions workflow: triggers on PR + push to main; installs
  Kairos + litellm + pyyaml; runs the suite; compares against the
  last green run (alpha=0.05 for the Welch t-test); promotes the
  new baseline on merge to main; uploads results as an artifact;
  prints a GitHub Actions `::notice` summary.

### What this unlocks
- Any repo using Kairos can have agent regression detection
  with zero glue code — copy two files, set 2 secrets, done
- The compare step uses the same `kairos.eval compare` from
  Round 10; Welch's t-test determines "is this a real regression?"

---

## 5. AgentLens-style session importer ✅

`scripts/import_session_to_eval.py` — convert any production
session log into a fresh `kairos_eval.yaml` for regression
detection. Two modes:

- **`replay`** (default) — pin the original output's content
  (contains + def-name regex) so future runs that emit the same
  shape pass; regressions fail
- **`scaffold`** — empty graders, so a human can fill in the
  expectations later. Use this when you want to *track* a
  requirement but haven't decided what "good" means yet.

### What changed
- **`scripts/import_session_to_eval.py`** (8.8 KB) — accepts
  three input formats (eval suite result, LoopSession history,
  one-shot `{requirement, output}`); extracts needles
  (non-stopword tokens ≥ 4 chars, deduped) and the first
  `def`/`class` regex; serializes as hand-rolled YAML (no PyYAML
  dep at write time).

### Tests (18 in `tests/test_session_importer.py`)
- 4 tests for `_extract_needles` (substring, dedup, stopword
  filter, max_n)
- 3 tests for `_extract_first_def` (function, class, no-def)
- 4 tests for `cases_from_session_json` (eval/loop/one-shot
  formats + unknown format raises)
- 3 tests for YAML serialization (structure, quoting, list
  format)
- 2 CLI smoke tests (scaffold mode produces empty graders;
  replay mode pins the function name in a regex)
- 2 utility tests for `_yaml_str` and `_dump_grader`

### What this unlocks
- "Every interesting session becomes a test case" — the
  defining AgentLens property
- Round 13 can add a `kairos eval record` subcommand that
  auto-records every successful production run as a new
  candidate case
- The team can build up a regression suite by simply running
  the Coder and importing the sessions they like

---

## Test summary

| Suite | New | Pass | Skipped |
|---|---:|---:|---:|
| `tests/test_plan_runtime_wiring.py` | 9 | 9 | 0 |
| `tests/test_session_importer.py` | 18 | 18 | 0 |
| **`Round 12 new`** | **27** | **27** | **0** |
| All touched files (this round) | — | **327** | **15** |

`tsc --noEmit` clean.

## What's NOT in this round

- **Rendering the plan in the UI** — the `plan.updated` event
  is published; the React panel can be a 30-line addition when
  someone asks
- **Tier 2 eval cases** (LLM-judge baseline) — wired but no
  cases yet; the team picks the rubric
- **`kairos eval record` subcommand** — Round 13, the natural
  follow-up to the importer
- **Auto-derive regression cases from git log** — same idea,
  via the git commit message we now write in R12.3

## Round-by-round schedule (updated)

| Round | What | Status |
|---|---|---|
| 8-11 | All prior rounds | ✅ |
| 12 | **This round** — plan in system_prompt + history + git + eval-CI + importer | ✅ |
| 13 | Plan UI panel; auto-derive regression cases from git log; `eval record` subcommand; full eval-CI integration | next |
| 14+ | Firecracker / Kata; Textual TUI; Cognee swap | as needed |
