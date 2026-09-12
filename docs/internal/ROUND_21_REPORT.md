# Round 21 — Pre-commit hook runner

> Status: **1 module shipped**. **18 new tests pass**.
> `tsc --noEmit` clean.

A drop-in pre-commit script that runs the regression suite
**before** the user commits. Fails fast on local regressions
so the team doesn't have to wait for CI to discover them.

---

## 1. `kairos.hook` pre-commit runner ✅

`kairos/hook.py` (7.2 KB) — a thin wrapper that glues
`kairos.eval` + `kairos.skill_search` into a single
pre-commit flow.

### Default check set (fast → slow)
1. **skill-search** — verify FTS5 backend is wired
2. **meta-eval** — run the eval framework against itself
3. **smoke** — run `examples/eval_ci.yaml` (the
   mechanical regression suite)
4. **tests** — run the pytest smoke subset
   (skipped with `--fast`)

### CLI
```bash
# All checks
python -m kairos.hook run

# Fast (skip pytest) — typical for save-state-triggered hooks
python -m kairos.hook run --fast

# Verbose (show details for every check, not just failures)
python -m kairos.hook run --verbose

# List the default check set
python -m kairos.hook list
```

### Pre-commit integration
Drop this in `.git/hooks/pre-commit`:
```python
#!/usr/bin/env python
import sys
from kairos.hook import run_default
sys.exit(run_default(fast=True))
```

### Tests (18 in `tests/test_hook.py`)
- 3 for `_run_one` (passes / fails / catches exception)
- 4 for individual checks (skill-search, meta-eval,
  eval-smoke, tests is callable)
- 2 for the default check sets (`DEFAULT_CHECKS`,
  `FAST_CHECKS`)
- 3 for `run_default` (fast skips tests, short-circuits on
  first failure, returns 0 when all pass)
- 4 for the CLI (`list`, `run` no-args, `run --fast`,
  `run --verbose`, no-args shows help)
- 1 for `tests` (verifies it's a callable 0-arg function)

### What this unlocks
- Local regression detection before the user commits
- Fast feedback loop (the smoke suite is <2s)
- Configurable: `--fast` for save-state hooks, full for
  pre-push / pre-merge

---

## Test summary

| Suite | New | Pass | Skipped |
|---|---:|---:|---:|
| `tests/test_hook.py` (18) | 18 | 18 | 0 |
| **`Round 21 new`** | **18** | **18** | **0** |
| All touched files (this round) | — | **451** | **19** |

`tsc --noEmit` clean.

## Round-by-round schedule (updated)

| Round | What | Status |
|---|---|---|
| 8-18 | All prior rounds | ✅ |
| 19 | EvalPanel UI + Datasets API | ✅ |
| 20 | Per-case LLM judge CLI verified | ✅ |
| 21 | Pre-commit hook runner | ✅ |
| 22+ | Whatever's next | as needed |
