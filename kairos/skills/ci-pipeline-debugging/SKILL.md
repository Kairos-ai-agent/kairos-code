---
name: "ci-pipeline-debugging"
description: "Use when CI hangs, dies without logs, or fails only on CI."
priority: 0.5
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\ci-pipeline-debugging\\SKILL.md"
---
# Debugging CI pipelines

For the case where CI disagrees with local: a job that never finishes, dies with no log, or fails on one platform only. The running example is a GitHub Actions matrix of test shards, but the rules are about CI in general.

## Order of work

1. **Make every step self-terminating before trying to debug it.** A killed or cancelled job uploads no logs at all (GitHub: `log not found`), so a hang produces *nothing* to read. Bound each step from the inside — `timeout 2400 bash scripts/ci_shard.sh <n> <total>` — and keep the job-level limit as a backstop. Never end a round with "the shard was cancelled and I don't know why": that is a design bug fixable in one commit.
2. **Read a completed job's log while the rest of the run is still going.** `gh run view --log-failed` refuses until the whole run finishes, but the raw API serves any finished job:
   ```bash
   gh api repos/{owner}/{repo}/actions/jobs/{job_id}/logs --allow-escape-sequences | sed 's/\x1b\[[0-9;]*m//g'
   ```
   `job_id` follows from `gh run view <run> --json jobs --jq '.jobs[]|select(.name=="…")|.databaseId'`; an empty log means that job has not finished. **Never `gh run cancel` to "get the log early"** — cancelled jobs' logs are gone for good.
3. **Split by unit and bound each unit**, printing one line per unit (`ok/FAIL <unit> (Ns)`). Two reasons: a module that blocks while being *imported or collected* is invisible to test-level timeouts (pytest's `--timeout` only wraps tests), and one hanging unit then names itself instead of leaving a unit-sized hole. A ready-to-copy script is in `references/bounded-shard-runner.md`. **Make the per-unit timeout name the unit's *test*, not just dump threads.** pytest-timeout's default method (`thread`) prints stacks for every thread *except* the one that is stuck, so a per-test timeout yields a page of asyncio/`waitpid` helper stacks and no test name — the culprit stays invisible run after run. Pass `--timeout-method=signal` on POSIX: the timeout then raises inside the test's own thread and the log carries the test's name and traceback. Gate it per platform (`case "$(uname -s)" in Linux*) ;; *) drop it ;; esac`) — Windows has no SIGALRM.
4. **Instrument before the death.** Print free memory and the top few processes by RSS before each unit. Anything you only print in a final summary is lost when the thing is killed — and "somewhere in these 23 files" costs several rounds.
5. **Read the exit code as a signal, not a diagnosis.**

   | code | means |
   |---|---|
   | 137 | SIGKILL — OOM **or** someone killed it; check free memory before blaming memory |
   | 124 | your own `timeout` fired |
   | 143 | SIGTERM — cancellation, or a graceful stop gone wrong |
   | 5 (pytest) | no tests collected, e.g. `importorskip` missing an optional dependency — a skip, not a failure |
   | 143 + `The runner has received a shutdown signal` in the log | the **runner** was reclaimed, not your code — re-run once; a *repeat on healthy infrastructure is a real defect*, and the job that fails is typically the longest one simply because it is the most likely to be mid-flight |
   | job never runs, `failed to be acquired (N attempts)` | no runner was available (frequently a provider incident) — wait, or trigger a fresh run (below) |

6. **Do not accept the first plausible cause.** "The suite is slow on a 2-vCPU runner" is a hypothesis; measure per-unit durations before designing around it. The case that produced this skill died of a self-inflicted SIGKILL while the working theory was memory — and the disconfirming measurement (free memory still ~5.7 GB at death) had been sitting in the logs since the first run.
7. **Compare like with like.** CI-vs-local differences usually live in skip-guarded code: a module skipped on the dev platform never executes the branch that fails elsewhere. When you cannot run that platform locally, drive the branch directly — patch what the module actually reads, and assert both branches so the other one is covered where you *can* run.
8. **Verify against the artifact, not the pipeline's self-report.** Green steps mean the steps passed; what a user receives is the artifact. Install it, run it, or pull it the way a stranger would, and check the bytes (contents, metadata, entry points) rather than the job summary.

## When the queue, not your code, is the problem

Runs created during a provider incident can stay `queued` indefinitely *after* the incident
ends, while a freshly triggered run starts within seconds. The stale entries report
contradictory state — `gh run view` says completed, `gh run list` says queued, `cancel` refuses
with "already completed" and `rerun` with "already running". None of those answers is
information. Do **not** delete the tag, the release, or the workflow to force it: trigger a new
run with an explicit ref, which re-evaluates the workflow from scratch and leaves every
artifact in place.

```bash
gh workflow run release.yml --ref v1.2.3   # a tag ref ⇒ the workflow's tag branch runs (so it publishes)
gh workflow run ci.yml --ref master        # a clean replacement for a stuck CI run
```

This relies on the publish step being **idempotent** (`gh release view` succeeds →
`gh release upload --clobber`, else `gh release create`) — design release jobs that way, then
re-triggering is always safe and a partially published release self-heals later.

Read the provider status at the **component** level: the summary banner said "All Systems
Operational" while `githubstatus.com/api/v2/components.json` showed `Actions:
degraded_performance` and `/api/v2/incidents/unresolved.json` still carried a critical
incident. The summary lags; the components do not.

## Pitfalls that cost the most time

- **A job can kill itself and look exactly like an OOM.** `os.killpg(os.getpgid(child), SIGKILL)` kills the child's tree only if the child has a process group of its own; when the parent never asked for one (`start_new_session=True`), `getpgid(child)` is *the parent's own* group, so a child timing out SIGKILLs the test runner, the wrapper script and the shell — `Killed`, exit 137, no traceback. Fix both ends: create children with `start_new_session=True` on POSIX, and make the kill refuse to signal a group it belongs to (`child_pgid == os.getpgid(0)` → kill the child directly).
- **Any step that can hang needs its own bound**, because a killed job keeps no logs. This is the single biggest time sink in CI debugging.
- **Platform-only failures hide behind skip guards.** A file guarded by `skipif(not linux)` never runs on the author's machine, so it can call an API that no longer exists and still look green locally — its own shape/signature guards drift with it. Assume such files are stale until a CI run proves otherwise.
- **Patching global interpreter state breaks the test framework itself.** Patching `os.name` makes `pathlib` build `PosixPath` on Windows and pytest INTERNALERRORs while merely *formatting* a failure; patch the module's namespace (`setattr(module, "os", fake_os)`, built from a copy of the real module) instead. Likewise `signal.SIGKILL` does not exist on Windows, so a POSIX branch referencing it dies of an `AttributeError` that a broad `except Exception` swallows — the test then "passes" while asserting nothing.
- **CI scripts must not call a venv's console scripts.** They embed the interpreter path from creation time and, once the checkout moves, exit 1 with no output. Use `python -m pytest`, not `pytest`.
- **Per-unit runs surface what whole-suite runs hide** — "no tests collected", a module that imports something enormous, one file that spikes memory. Treat every unfamiliar exit code as a finding rather than noise.
- **A run whose failure is "cancelled" is worth re-designing, not re-running.** The information you need was never uploaded; change the pipeline so the evidence survives (bounds + per-unit lines), then run it once.
