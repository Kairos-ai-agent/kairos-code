# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project aims at
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) once it reaches 1.0.

## [0.1.2] - 2026-09-13

### Fixed

- **A hook that timed out could kill the process that ran it — on POSIX.** The
  cleanup path used `os.killpg(os.getpgid(child), SIGKILL)`, but hook commands were
  started without `start_new_session`, so the child shared *this* process's group
  and the kill took down the running process (and, in CI, the whole job) with it:
  exit 137 and no traceback. Hook commands now get their own session, and
  `_force_kill` refuses to signal a group it belongs to. Windows was unaffected
  (`taskkill`, no process groups), which is why every local run was green.
- **The Task tracker called passed rounds failed.** It parsed Reviewer verdicts by
  looking for `approve`, which only the pre-R38.7 rubric shape carries. The
  simplified Reviewer answers `{"has_bugs": ...}`, so every round of a run whose
  gate said *passed* was listed as "failed — round finished without a verdict".
  The simple shape now goes through the loop's own translation
  (`kairos.loop.reviewers._normalize_bug_verdict`), so the tracker cannot disagree
  with the gate, and payloads truncated at the 2000-character storage cap are
  still understood.
- **The test suite wrote into the developer's real data directory.** The data
  directory defaults to `<repo>/data`, and `tests/unit/test_memory_api.py` creates
  projects called `mem-api-test-<hex>` through the real persistence layer: running
  pytest filled `data/kairos.db` with them, and they then showed up in the sidebar
  of the README screenshots. `tests/conftest.py` points `KAIROS_DATA_DIR` at a
  throwaway directory for the whole session, and `tests/test_test_isolation.py`
  asserts the directory the app resolves is that one.
- **The History table wrapped run ids mid-string.** The Run column had no width,
  so on a narrow table an eight-character id broke across three lines. It has an
  explicit width and ellipsis.

### Changed

- **README screenshots are regenerated** from a clean, key-free demo run: no
  test-fixture projects in the sidebar, no hand-annotated example project, no
  Chinese in an English README, and the Task tracker agrees with the gate.
- **The licence is now the canonical AGPL-3.0 text**, so GitHub identifies it; the
  project's own copyright notice moved to the README's licence section.
- **CI runs each shard file by file**, one bounded pytest process per file, and
  prints free memory and the largest processes around each one. A module that
  blocks during import is invisible to pytest's `--timeout`, and a job killed by
  its own limit keeps no logs — both of which made a shard-sized hole impossible
  to diagnose.

## [0.1.1] - 2026-09-13

The first release that could be published end to end. Cutting 0.1.0 is what
surfaced these, and one of them could only ever appear on a tagged run.

### Fixed

- **The container image never reached ghcr.io.** `docker push` rejected the
  reference outright (`repository name ... must be lowercase`) because the account
  is `Kairos-ai-agent`; the image path is lowercased now. The image itself built
  and passed its smoke test in 0.1.0 — only the push failed.
- **Publishing could dead-end on an existing Release.** `gh release create` now
  updates an existing release instead of erroring, so re-cutting a tag to pick up
  a workflow fix works.
- **The Docker job can no longer gate a release.** It is out of the release job's
  `needs`, so a registry problem cannot stop a release from being published; the
  job still runs and still reports red.

### Changed

- **The Python suite runs in six CI shards with a 90-minute ceiling**, printing
  `--durations=0`. A job killed by its own timeout keeps no logs at all, which is
  why diagnosing this took several attempts.
- **Platform-sensitive tests now work on Linux.** `tests/test_landlock_ci.py`
  called a sandbox API that no longer exists — a `TypeError` for every Linux
  contributor, invisible where the module is skipped — and its signature guard
  accepted any signature at all. It now exercises the documented fd + `preexec_fn`
  mechanism, checks that a write inside the allowed root still succeeds, and
  asserts that building a ruleset does not confine the calling process.
- **README and CHANGELOG name the artifacts that exist** (the wheel attached to
  the release, and a container image built by the release workflow) instead of a
  PyPI name that is not published yet.

## [0.1.0] - 2026-09-13

The first public cut of the pipeline — a Coder/Reviewer loop with an enforced
score gate, cost accounting, an eval harness (record/replay/derive), model routing
across direct providers and LiteLLM, sandbox levels, an MCP client, hooks, agent
teams, skills, and the Web UI + CLI + TUI surfaces.

Three ways to run it, all built and smoke-tested by the release workflow before
they are published: a standalone binary per platform, a wheel that carries the Web
UI, and a container image.

### Added

- **Installable in three ways.** Standalone executables for Linux, macOS and
  Windows (no Python, no Node — unzip and run; it opens the UI in your browser);
  `pip install kairos_code-<version>-py3-none-any.whl` (the wheel attached to this
  release) for a real `kairos` command with the built Web UI inside; and a
  container image built and pushed to `ghcr.io/kairos-ai-agent/kairos-code` by the
  release workflow.
  Every artifact is verified before it is published: the wheel is installed into
  an empty virtualenv and must serve the UI, and each binary is started headless
  and must answer `/api/health` and return the bundled SPA.
- `kairos --version`.
- Optional extras for the integrations that are imported lazily: `voice`,
  `memory`, `browser`, `cloud`, `llm`, `telemetry`, `daemon` (the base install
  works without any of them).
- **Gate Report** — the receipt for a loop: rounds, scores, verdicts, issues,
  checkpoints, learned fixes and cost. Rendered as one self-contained HTML file
  (EN/中文 toggle built in), as Markdown for a PR description, or as JSON for CI.
  Available as `kairos gate report --project <id|name>` and
  `GET /api/projects/{id}/gate-report`.
- **`kairos demo`** — the whole review gate in about six seconds, with **no API
  key and no network**: the real loop runs against a scripted model on a tiny
  generated repo, and finishes by running that repo's own tests.
- **Three-view UI**: Run (did it pass / what did it cost / better than last
  time), History (every run with its verdict and spend) and Settings; the rest
  lives under a collapsed *Advanced* group. `?project=<id>` deep links.
- 63-language UI with on-demand locale chunks, RTL support and machine-translated
  dictionaries.
- A `clean install` CI job: an empty virtualenv with core dependencies only must
  be able to run `kairos --version`, the zero-key demo, and a real server start.

### Fixed

- **A clean install could not start the server.** `python-multipart` (required to
  register any form/upload route) and `aiosqlite` were imported but never
  declared, so a fresh `pip install` produced a package that died while importing
  the app — every published binary and every wheel. Nothing local could see it:
  a developer virtualenv has both packages anyway. Found by the first release dry
  run, on all three platforms.
- **The wheel shipped no Web UI.** `python -m build` builds the wheel from the
  sdist, and `web/dist` is generated (and gitignored) so it is not in an sdist;
  the wheel is now built from the tree, and both the build job and CI assert that
  `web/dist/index.html` and the console script are actually inside it.
- **The Docker image could not build**: the image never copied `hatch_build.py`,
  the custom wheel hook declared in `pyproject.toml`.
- Two tests asserted things the implementation never promised, and only failed on
  Linux — where contributors actually run them: one patched `sys.platform` while
  `_resolve_loop()` reads `os.name`, the other passed `Path()` as a "missing"
  allowed root (`Path()` is `.` and is always truthy).
- **Round history was never persisted.** The `loop_rounds` table, its loaders,
  the UI endpoints and the FTS mirror all existed, but no production code path
  wrote the row — so history vanished on restart and the Gate Report had no
  source. The loop now records each round.
- **Cost was reported as the whole ledger.** The cost ledger has no project
  column, so a run's report showed global spend. Cost is now scoped to the run's
  time window (and flagged when it has to fall back).
- A broad `.gitignore` rule (`settings.json`, unanchored) silently kept an i18n
  source fragment — 298 keys — out of the repository, so every fresh clone failed
  the i18n gate while the maintainer's working tree stayed green.
- `vendor/_oss/{anthropic-skills,superpowers}` were committed as gitlinks, so a
  clone got two empty directories instead of the 76 vendored files.
- `kairos/__init__.py` lazily imported `cli` in a way that recursed
  (`from kairos import cli` raised `RecursionError`).
- The in-process cost buffer and the JSONL ledger double-counted every call.
- `kairos exec`/agent runs refused any provider without an `api_key`, which made
  local or scripted providers unusable.
- Packaged deep links (`/run?project=…`, `/history`) returned 404: the API served
  the SPA without a history fallback.
- `loop.*` WebSocket events were dropped by a topic whitelist, which silently
  disabled the "switch session and refresh" behaviour.
- Checkpoints could commit the parent repository when a workspace lived inside it
  (once sweeping a 640 MB archive into history); commits are now scoped to the
  workspace and refuse to run without one.

### Changed

- The Reviewer reports **bugs only** (`{"has_bugs", "bugs", "summary"}`); the
  internal approve/score gate is unchanged.
- `kairos <subcommand>` no longer silently starts the server when the subcommand
  is unknown to the launcher's command list.
- The Python test job runs as three balanced shards (measured by runtime, not
  test count) with a per-test timeout, because the full suite needs 40+ minutes
  on a 2-vCPU runner and would otherwise be killed mid-suite.
