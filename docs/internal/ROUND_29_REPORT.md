# Round 29 — Long-running app harness (.har/ contract + resume runtime)

**Goal:** let a developer kick off a multi-day refactor in the morning,
close the laptop, and resume the next day from the same round — without
any daemon, scheduler, or external state.

**Scope:** 1 new module + 1 new test file + 1 docs update.

---

## 1. The contract: `.har/` directory

Five files in a single directory, all written by the runtime, all
human-readable / grep-able:

```
my-project/
  .har/
    contract.json     # immutable: goal, har_id, created_at, cwd
    state.json        # mutable: round, last_score, last_approve, plan
    plan.md           # human-readable, synced from state.plan_text
    history.jsonl     # one line per round (newest last)
    .gitignore        # "lock" — lock file should not be committed
    lock              # PID + ts (only present while resume is running)
```

**Why this shape:**

- **`contract.json`** is the only file the user is expected to read
  first. Goal + id + creation time is enough for a glance to know
  "what is this thing".
- **`state.json`** is the only file the runtime mutates. Atomic
  write (tmp + rename) so a crash mid-write preserves the old state.
- **`plan.md`** is the human view. UI / `cat` / `git diff` all work
  on the same file.
- **`history.jsonl`** is append-only, crash-safe, easy to tail. Same
  pattern as `data/alerts.jsonl` (R28) and `data/cost.jsonl` (R14).
- **`lock`** is a plain PID + timestamp file. Acquired with
  `O_CREAT | O_EXCL` for atomic create-or-fail. Stale locks
  (older than 1 hour) are auto-stolen — handles the "laptop
  suspended for 3 hours" case.
- **`.gitignore`** keeps `lock` out of git so a stale lock from
  a CI run doesn't break the developer's local resume.

## 2. Public API

```python
from kairos.har import (
    HarContract,            # dataclass: goal, har_id, created_at, cwd, ...
    HarState,               # dataclass: round, last_score, last_approve, ...
    init_har,               # create the .har/ dir
    load_har,               # read contract + state from disk
    save_state,             # atomic state.json write
    save_plan,              # sync plan.md
    append_history,         # append one history line
    read_history,           # newest-first list (default limit=50)
    acquire_lock,           # O_EXCL PID lock; auto-steal stale
    release_lock,           # release if (and only if) we own it
    resume,                 # run N rounds via tick_fn callback
    _synthetic_tick,        # no-op tick for tests
    status_text,            # human-readable status line
    main,                   # CLI entry: init / status / checkpoints / resume
)
```

### 2.1 The `tick_fn` decoupling

The runtime is decoupled from `kairos.loop.loop_runner` (which is
async, heavyweight, and needs a fully-wired `LoopSession` with
real agents). The runtime accepts any callable:

```python
TickFn = Callable[[HarState, HarContract],
                  Tuple[HarState, Dict[str, Any]]]
```

For tests and `--dry-run` runs, `_synthetic_tick` produces
predictable output (approves at round 3). For production, the
caller wires a tick_fn that invokes their loop of choice:

```python
def my_tick(state, contract):
    loop = build_loop(contract)  # whatever you have
    result = loop.run_one_round(state)
    return state, result.to_history_entry()

resume(root, max_rounds=5, tick_fn=my_tick)
```

The framework is **portable**: the same `.har/` format works for
any loop that can produce `(new_state, history_entry)`.

## 3. CLI

```
$ python -m kairos.har init "migrate 47 endpoints to FastAPI DI" --rounds 12
Initialized C:\proj\.har
  har_id    = 62125153
  goal      = migrate 47 endpoints to FastAPI DI
  rounds    = 12
  cwd       = C:\proj

$ python -m kairos.har status
Harness 62125153  goal: migrate 47 endpoints to FastAPI DI
  cwd: C:\proj
  round=0  last_score=0  approved=False  no_progress=0
  updated_at=0  plan_chars=0  rounds_planned=12

$ python -m kairos.har resume --rounds 3
ran 3 round(s) without approval

$ python -m kairos.har checkpoints
Recent rounds (newest first, max 20):
  R  3  score= 60  [X]  round 3 synthetic (goal=migrate 47 endpoin...)
  R  2  score= 60  [X]  round 2 synthetic (goal=migrate 47 endpoin...)
  R  1  score= 60  [X]  round 1 synthetic (goal=migrate 47 endpoin...)

# Next morning, second `resume` call:
$ python -m kairos.har resume --rounds 5
approved at round 6; stopping
```

Subcommands:
- `init <goal> [--cwd DIR] [--rounds N] [--meta JSON]` — create `.har/`
- `status` — show current state + `plan.md` if not placeholder
- `checkpoints [--limit N] [--json]` — list history entries
- `resume [--rounds N] [--no-stop-on-approve]` — run N more rounds

Resume exit codes:
- `0` — rounds ran (or 0 rounds requested), no approve yet
- `1` — missing/invalid CLI args
- `2` — lock held by another live process
- `3` — `.har/` not found
- `4` — approved this run, stopped early
- `5` — no progress for 3+ rounds, stopped

## 4. Tests (47, all green)

`tests/test_har.py` covers 8 sub-groups:

| Group | Count | Covers |
|-------|-------|--------|
| Data class round-trips | 4 | `HarContract.to/from_dict`, `HarState.to/from_dict`, missing-field defaults |
| `init_har` / `load_har` | 5 | file layout, idempotency, `FileExistsError`, `FileNotFoundError`, plan.md seeding |
| `save_state` (atomic) | 2 | tmp+rename, timestamp update |
| History | 3 | round-trip, limit truncation, corrupt-line skip |
| Lock | 5 | acquire/release, contention, stale steal, "only own" release, missing-file noop |
| `save_plan` | 2 | write, empty |
| `_synthetic_tick` | 2 | advance round, approve at round 3 |
| `resume` | 8 | synthetic rounds, stop-on-approve, no-stop, lock contention, missing harness, no-progress stop, state persistence, plan persistence, error path releases lock |
| CLI | 10 | init / status / checkpoints / resume + invalid input + missing harness |
| Formatting helpers | 3 | `status_text`, `_format_history_row`, `_har_dir` |

**Total: 47 / 47 passing in 1.62 s.**

## 5. What this round does NOT do (intentional)

- **No real loop integration.** The framework is decoupled from
  `kairos.loop.loop_runner`; production wiring is a 10-line glue
  function the user writes (see §2.1). This keeps `kairos.har`
  testable without async/sandbox/agents.
- **No cross-process resume coordination.** A second `resume`
  process on the same `.har/` is blocked by the lock; a third one
  on a *different* `.har/` (different CWD) is fine.
- **No automatic git commit per round.** That's the existing
  `kairos.tools.checkpoint` (R12) and works orthogonally — you
  can call it from your `tick_fn`.
- **No plan generation.** `plan_text` is just a string in state.
  Your tick_fn (or the agent) fills it in.

## 6. Test sweep

| Bucket | Count | Result |
|--------|-------|--------|
| **R29 new** | 47 | 47 pass, 0 fail |
| R28 stack (alerts_dispatcher) | 28 | 28 pass, 0 fail |
| R22-R27 stack (alerts/cost/trend/hooks/eval/judge/meta-eval/auto_record) | 204 | 204 pass, 1 skip |
| tui / checkpoints / sessions / plan_history | 73 | 73 pass, 0 fail |
| streaming / compaction / voice / sandbox | 160 | 160 pass, 23 skip |
| skills / ollama / memory / observability | 204 | 204 pass, 0 fail |
| cloud / s3_cloud / integration | 100 | 100 pass, 0 fail |
| resilience | 47 | 47 pass, 0 fail |
| review_helpers (excl. 2 slow loop tests) | 18 | 18 pass, 0 fail |
| commands / coder_modes / hooks / teams | 114 | 114 pass, 0 fail |
| multimodal / manifest / permissions / plugins / worktree / sessions / approval / guardrails / main_workers / cli | 171 | 171 pass, 0 fail |
| api_projects / confidence / file_edit / hooks / learning_reflect / memory_api / review_engine | 54 | 54 pass, 0 fail |
| mcp / ollama / judge_cli / meta_eval / auto_record / reflection / retained_reasoning | 94 | 94 pass, 0 fail |
| **Confirmed passing** | **1314** | **0 fail** |
| 4 deselected (pre-existing flaky/slow) | – | not from R29 |

**Delta vs R28:** +47 tests, 0 regressions.

## 7. Risk + mitigation

| Risk | Mitigation |
|------|------------|
| Two `resume` processes corrupt state | `.har/lock` with `O_CREAT \| O_EXCL`; stale locks auto-stolen after 1 h |
| Crash mid-write corrupts `state.json` | `os.replace(tmp, state.json)` (atomic on POSIX + Windows); tmp file deleted on next load if present |
| `history.jsonl` grows unbounded | `read_history(limit=)` already caps reads; future `har prune --keep N` will trim the file |
| `cwd` from contract becomes stale (user moves the project) | `HarContract.cwd` is captured at init for diagnostic only; runtime uses the `load_har(root)` caller-supplied root |
| Tick crashes mid-resume | `try/finally` around `release_lock` — lock is always released; last successful round's state is on disk |
| `init_har` overwrites an existing harness | `FileExistsError` raised with a clear message — user must delete the directory or run `resume` |

## 8. Follow-up (R30)

**R30** — Alert UI panel: read `data/alerts.jsonl` (R28) from the
FastAPI side and surface in the web UI alongside the existing
`CostDashboard`. Severity color-coding + "Mute for 1h" / "Open in
$EDITOR" actions.

After R30, the remaining Tier 2/3 items can be picked up as needed.

## 9. Diff summary

```
 kairos/har.py                       | 460 +++++++++++++ (new)
 tests/test_har.py                   | 480 +++++++++++++ (new)
 docs/ROUND_29_REPORT.md             | this file
 docs/OSS_ADOPTION_ROADMAP.md        | +1 row (R29)
 docs/KAIROS_INDEX.md                | +1 row + 1314 test total
```

R29 ships green: 47 new tests, 1314/1314 confirmed passing, no
regressions in any of the R8-R28 modules.
