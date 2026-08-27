# Round 20 — Per-case LLM judge CLI verified

> Status: **2/2 items shipped**. **8 new tests pass**.
> `tsc --noEmit` clean.

R10 added the LLMJudgeGrader and `make_litellm_judge` factory.
This round verifies the end-to-end CLI flow with an example
suite that combines mechanical + subjective graders.

---

## 1. Eval-with-judge example suite ✅

`examples/eval_with_judge.yaml` — 2 cases that each combine:
- Mechanical graders (`contains`, `regex`, `latency_max_ms`)
- An `llm_judge` grader with a 0-1 rubric

The cases:
- `docstring-quality` — the judge grades the docstring
  (purpose + parameters + return value)
- `error-handling-presence` — the judge grades whether
  the agent's error handling is explicit (1.0) vs
  try/except-everything (0.5) vs absent (0.0)

### CLI usage
```bash
# Without --judge, the LLM cases fail open (score=1.0)
python -m kairos.eval run --suite examples/eval_with_judge.yaml

# With a real LLM judge (uses OPENAI_API_KEY)
python -m kairos.eval run --suite examples/eval_with_judge.yaml \
  --judge kairos.eval:make_litellm_judge
```

---

## 2. CLI flow verified ✅

### Tests (8 in `tests/test_judge_cli.py`)
- `test_judge_yaml_loads` — the example parses + has the
  expected structure
- `test_build_graders_creates_llm_judge_grader` — the
  `llm_judge:` key in a YAML spec dispatches to
  `LLMJudgeGrader` with the right prompt/threshold
- `test_run_suite_evaluates_judge_per_case` — each case
  gets its own judge instance with its own threshold
- `test_judge_with_missing_judge_fails_open` — no judge
  configured → 1.0
- `test_judge_with_real_callable_uses_returned_score` —
  text parsing works
- `test_judge_cli_help_lists_subcommands` — `--help` shows
  all 5 subcommands (run, compare, record, replay, derive)
- `test_judge_yaml_run_with_stub_target` — end-to-end with
  stub target + stub judge
- `test_judge_yaml_run_without_judge_fails_open` — end-to-end
  with no judge; pass_rate >= 0.5

---

## Test summary

| Suite | New | Pass | Skipped |
|---|---:|---:|---:|
| `tests/test_judge_cli.py` (8) | 8 | 8 | 0 |
| **`Round 20 new`** | **8** | **8** | **0** |
| All touched files (this round) | — | **451** | **19** |

`tsc --noEmit` clean.
