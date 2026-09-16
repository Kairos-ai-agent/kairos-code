---
name: "change-verification"
description: "Prove fixes with evidence; triage pre-existing failures."
priority: 0.5
version: "1.0.0"
imported-from: "hermes"
source-path: "C:\\Users\\leohu\\AppData\\Local\\hermes\\skills\\software-development\\change-verification\\SKILL.md"
---
# Change Verification & Baseline Triage

The deliverable of a fix is evidence, not a claim. Apply whenever you are about to tell the
user "fixed" / "done" — especially after multi-file edits in a repo with a test suite.

## Step 1 — Name the observable

Before verifying, write down what the user will actually see change: an endpoint returning
N rows, a list keeping its order after a click, a page rendering K bubbles. That observable
is what you verify. "The code looks right" and "the unit test passes" are not it.

## Step 2 — Verify end-to-end, with numbers

- Call the real endpoint and read the raw status: `curl -s -w '\nHTTP:%{http_code}\n' '<url>'`.
  A client that swallows non-200s renders a friendly empty state — the raw status is the truth.
- Reconcile counts across layers: DB row count == API rows == rendered DOM nodes. Print all
  three. "It shows something now" is not verification.
- Check a second case (another project/record) so you know it is not a coincidence.
- For UI changes see `frontend-patching/references/live-browser-verification.md` — hard
  reload, finding the real render container, ASCII-safe DOM reads.

## Step 3 — Baseline triage: is the failure mine?

Never claim a suite is clean when it is not, and never silently fix failures you did not
cause. When a full run shows failures, stash ONLY the files you touched and re-run ONLY the
failing test file:

```bash
git stash push -- path/to/Changed.tsx path/to/Other.tsx
npx vitest run src/test/theFailingFile.test.tsx   # or: python -m pytest tests/test_x.py -q
git stash pop
```

- Same failures on the stashed tree → pre-existing; report them separately with that evidence.
- Failures only with your changes → regression; fix before reporting.
- After `git stash pop`, prove the restore landed (`grep -c '<marker>' <file>` and
  `git stash list` empty). A silent pop failure leaves you editing the wrong tree.
- State the conclusion explicitly ("15 failed at baseline, the same 15 after") instead of
  implying a green suite.

## Step 4 — Report shape

1. Root cause, layer by layer — the user wants the mechanism, not the symptom.
2. What changed, per file.
3. Evidence: commands run, before/after numbers, test counts split baseline vs new.
4. What you could NOT verify (data already lost, paths needing user credentials) — say it plainly.
5. Offer the leftover decisions (pre-existing failures to fix, temp files to delete) instead of
   deciding for the user.

## Pitfalls

- Claiming "fixed" from `tsc`/unit tests alone when the user can see the UI.
- Reporting a suite as passing without a baseline run when failures exist.
- Hiding pre-existing failures to look clean — the user would rather see the split.
- Fixing pre-existing failures without asking; it widens the diff and the risk.
- Forgetting to verify `git stash pop`, then editing a stashed-away tree.
- Re-verifying against a process still running pre-patch code — restart the server and poll
  until it answers before claiming the fix verified.
- Claiming "works offline / needs no API key" without actually blocking the network. Prove it by
  blocking **DNS (`socket.getaddrinfo`) and `socket.create_connection` for non-local hosts**.
  Never block `socket.socket.connect`: asyncio's proactor event loop builds its self-pipe with
  `socketpair()` (which connects internally), so the loop fails to initialise and the whole file
  fails for the wrong reason.
- A suite that fails wholesale the moment you add a global fixture (identical infra-looking
  tracebacks, even on tests unrelated to the change): suspect the fixture first, then the code.
  A fixture that breaks the event loop or the interpreter's own plumbing is not a failing fix.
- **A suite that mutates state instead of failing.** If running the tests dirties the repo
  (new commits, modified files, written rows, touched config), the pass/fail output tells you
  nothing — probe the state around each candidate: `BEFORE=$(git rev-parse HEAD)` → run ONE
  file → compare, then bisect to the culprit file. Expect the cause to be a real product path
  running under test whose default workspace is the process CWD (i.e. your checkout). Fix the
  test (explicit temp workspace) AND add an env-level switch that disables the side effect,
  set for the whole suite, with the few tests that deliberately exercise it opting back in.
  Before adding the switch, grep the test tree for assertions about the behaviour you are
  gating: a session-wide fuse silently turns them into no-ops and they fail looking like your
  regression. Exempt those files locally (`monkeypatch.delenv(...)`) and add one test that
  asserts the fuse itself holds.
- **Flaky under load is a margin problem, not a code bug.** A test that passes in isolation
  and fails in the full run (speedup/parallelism/timeout claims) needs a wider tolerance with a
  comment saying it is a wiring check rather than a benchmark — do not "fix" product code for
  it and do not delete the test. Also stop running a heavy build next to a timing-sensitive
  suite; measure one at a time before concluding anything.
- **Triage a large failure set by cause, not test by test.** 40 failures are usually 4–6 root
  causes: undeclared optional dependency, assertion that predates a refactor, mock patched at
  the wrong layer, environment-dependent check reading the real user config. Group them, fix
  group by group, and put the group sizes in the report (`24 deps + 17 stale assertions`).
- **Mock patched at the wrong layer.** When an implementation swaps its HTTP/SDK client, the
  hand-rolled fakes keep patching the old one and silently capture nothing (assertions then
  read an empty dict). Before rewriting N fakes, add ONE adapter that translates the new
  client's calls into the old fakes' shape (request object in, status/body/exception out) and
  swap only the patch/restore lines. Grep the test tree for the symbol before widening a helper
  signature — mocks reimplement the old shape and fail on a shard you did not run.
- **A probe that returned nothing has proven nothing.** Before calling something broken,
  prove the probe itself worked: a status code, non-empty output, a positive control.
  `HTTP 000`, an empty grep, a log blob you cannot read — those mean *unknown*, not *fails*.
  Reporting one of them as a defect is how a false bug report reaches the user; say what you
  could not observe instead.
- **Trust the answer only if you know who answered.** A smoke test or probe on a fixed port
  can be served by a stale process (or the user's own running instance) and will report *its*
  version and state as if they were the artifact's — that is how a correct build got accused
  of a hardcoded version number. Confirm the port is free (or the answering PID is yours)
  before believing the response, and prefer a fresh unused port per run.
- **A tool's own success message is not evidence.** "restored", "placeholder back",
  "uploaded", "published" — re-read the artifact and compare against the pre-state: file
  contents/length, the asset list, the count you expected. Generated success text is a claim
  like any other, and a hardcoded line inside a script will happily lie about what it did
  (extends the `git stash pop` check above to every restore you perform).
- **Verify shipped artifacts *as shipped*.** Download the published binary/package from the
  release page and run it there; a locally built one can differ from the pipeline's — a dev
  environment carrying extra dependencies froze a 53 MB executable where the clean-environment
  CI build is 26 MB. Reconcile sizes and reported versions against the previous release, and
  build in a clean environment when you must produce a pipeline-equivalent artifact by hand.
- **`exit code 143` + "the runner has received a shutdown signal" is infrastructure, not a
  failing test** — re-run once, and say so plainly. But when the same job fails again on
  healthy infrastructure, stop calling it flaky: that is a real defect. The reason it is
  always the *longest* shard/job is that it is the most likely to be mid-flight when a runner
  is recycled — so a repeat is a signal about your code, not about the platform.
- **When two sources disagree about state, believe neither.** A `view` that says completed
  while a `list` says queued, a `cancel` refusing with "already completed" while a `rerun`
  refuses with "already running" — contradictory answers during a provider incident mean the
  API itself is unreliable right now. Get a fresh observation, or trigger a new run, instead
  of acting on either answer.
- **A capability can be live-only — verify the path the user will actually take.** Asked "does
  it already do X?", a hit in the runtime handler (an event dispatcher, a websocket topic
  switch) is only half the answer: the reload path usually re-reads state through a *different*
  filter — a query whitelist, a projection, a saved view — so a feature can render live and
  vanish on refresh because the reload query excludes its rows. Check both, then answer in
  three parts: live behaviour, behaviour after reload, and where the complete record actually
  lives (a detail/trace view). The reverse also happens: an event handled purely for
  navigation housekeeping and never rendered — "handled" is not "shown".
