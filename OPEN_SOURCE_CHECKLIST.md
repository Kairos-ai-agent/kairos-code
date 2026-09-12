# Open-source readiness checklist

Status of the repository against "somebody can clone this and trust it".
Written after a full audit + remediation pass; every item below was verified with
a command, not by inspection alone.

## Done

### Blockers (all fixed)

| Item | Evidence |
|---|---|
| **No LICENSE file** (the repo was "all rights reserved" while `pyproject.toml` claimed a license) | [`LICENSE`](LICENSE) added — **AGPL-3.0-or-later**, chosen deliberately so a modified Kairos Code offered as a network service must publish its source; [NOTICE](NOTICE) covers the vendored third-party skills, which stay MIT (permissive, compatible with (A)GPL-3.0). Wheel metadata verified: `License-Expression: AGPL-3.0-or-later`, both files bundled under `dist-info/licenses/` |
| **Two broken gitlinks** (`vendor/_oss/anthropic-skills`, `vendor/_oss/superpowers`) — empty dirs on clone, `git submodule update --init` failed with "no .gitmodules" | vendored as ordinary files (12 + 64 files), `git ls-files -s \| grep 160000` → 0 |
| **Hardcoded personal paths** in shipped code (`E:/D_bak/...`, `C:\Users\user\...`) | `scripts/gen_languages.mjs` derives the repo root from `import.meta.url`; `build_exe_with_icon.py` uses `tempfile.gettempdir()`; `kairos/bench/real_eval.py` reads `KAIROS_BENCH_API_KEY(_FILE)`; `web/public/branding/_build_icons.py` takes argv/env; UI/copy and test text genericised. `git grep -lI "user\|D_bak"` on tracked files → only `docs/internal/` |
| **`npm run build` failed** (3 TypeScript errors) — no production UI could be built at all, which also breaks the Docker image | `NewChatButton.tsx` now types the created project as `Project`, `chatStore.ts` merge is typed as `ChatStore` → `npx tsc --noEmit` clean, build passes |
| **Deep links 404 in the packaged UI** (`/run?project=…` worked on the dev server only) | `api/app.py` mounts `SPAStaticFiles` with an `index.html` fallback for non-API, extension-less paths |
| **`loop.*` WebSocket events were swallowed** by an early `return` in the topic filter, making the session-switch/topbar-refresh block dead code (`web/src/pages/Chat.tsx`) | fall-through branch added; covered by `chatWebSocketRouting.test.tsx` |
| **41 test failures** on a clean checkout (7 groups) | 24 were undeclared optional deps → `tui` / `metrics` / `mcp` extras now exist and `dev` pulls them in; the rest were stale assertions updated to the current contract (URL probe mocks, terminal allowlist, persist cap, new-chat flow) |
| **`/test`, `/lint`, `/format` were dead features** — they shelled out as `python -m pytest` / `python -m ruff`, which the terminal tool blocks ("head 'python' is not on the command allowlist"), so every invocation failed by construction | `kairos/commands.py` now calls the allowlisted `pytest` / `ruff` heads; `ruff` joined the opt-in `BUILD_COMMANDS` set |
| **12 test-connection tests mocked the wrong HTTP layer** — the probes moved from `urllib` to `httpx`, so 26 fakes were never called and the assertions read an empty capture dict | [`tests/_httpx_bridge.py`](tests/_httpx_bridge.py) bridges httpx back onto those urllib-shaped fakes (HTTPStatusError / ConnectError translation); `tests/test_r37_backend.py` 55 passed |
| **The vitest run never exited** — the FolderPicker modal mounts `BrowsePanel` (antd scroll locker + layout effects), which kept the worker alive after the assertions passed | `BrowsePanel` is stubbed in that test file; the manual-path input is now reached through its disclosure (`folder-picker-manual-toggle`), and the module-level zustand store is reset per test |
| **`tests/test_pre_commit.py` broke with the docs move** | it now points at `docs/internal/PRE_COMMIT_HOOK.py` |
| **The test suite silently committed the repository** — `checkpoint_round` staged the workspace with a bare `git add -A`; the default workspace is the process CWD (this checkout), so `tests/unit/test_loop_run.py` committed the whole working tree round after round. It produced the mislabelled commits `kairos: round 1 approved (score 75)` and `kairos: round 12 rejected (score 20)` on top of an in-progress working tree (the same mechanism that committed a 640 MB archive earlier) | three layers: staging and the commit are scoped to the workspace subtree (`_scope` in `kairos/tools/checkpoint.py`), `KAIROS_NO_CHECKPOINTS=1` makes `checkpoint_round` a documented no-op, and `tests/conftest.py` sets it for the whole suite while the checkpoint tests opt back in on throwaway repos (`tests/test_checkpoint_scoping.py` proves the scoping). Verified: running the suite leaves `HEAD` unchanged |
| **`test_r37_ui_source.py` had 12 stale assertions** (they asserted English copy that the i18n migration replaced with keys) | rewritten against the current contract; 73 passed |
| **No test CI** (only the "Kairos reviews its own PRs" workflow) | [`.github/workflows/ci.yml`](.github/workflows/ci.yml): pytest, the zero-key demo, `tsc`, vitest, `merge_i18n --strict`, `check_i18n.mjs`. Mirror it locally with [`scripts/ci_local.sh`](scripts/ci_local.sh) |

### Facade and engineering files

`CONTRIBUTING.md` · `SECURITY.md` · `CODE_OF_CONDUCT.md` · `CHANGELOG.md` ·
`.gitattributes` (i18n dictionaries marked `linguist-generated`, binaries) ·
`.editorconfig` · `.dockerignore` · `.nvmrc` · `requirements.txt` ·
`Dockerfile` + `docker-compose.yml` · `.github/ISSUE_TEMPLATE/{bug_report,feature_request}.yml` ·
`.github/ISSUE_TEMPLATE/config.yml` · `.github/PULL_REQUEST_TEMPLATE.md` ·
`docs/README.md` · `docs/internal/README.md` ·
`scripts/ci_local.sh` · `scripts/prepare_github.py`

Packaging fixes in `pyproject.toml`: real `description`, `authors`, `keywords`,
`classifiers`, `[project.urls]`, the `tui`/`metrics`/`mcp`/`all` extras, and
`hatch_build.py` (a hatchling `custom` build hook) so a wheel install ships the
UI as well as the API **when the frontend has been built** — and still installs
cleanly when it has not. Measured, because the obvious options do not work:
`artifacts = ["web/dist"]` adds nothing to the wheel (0 entries, whether at
`[tool.hatch.build]` or on the wheel target), and a static `force-include`
hard-fails a fresh clone with `Forced include not found: .../web/dist`. With the
hook: 199 `web/dist` entries incl. `index.html` when built; a working
backend-only wheel when not.

The README was rewritten around what is actually different (enforced gate, cost
ledger, eval harness, the shareable Gate Report) with the real `kairos demo`
output, screenshots in `docs/assets/`, the stop-condition list, the API table and
a security section that points at `SECURITY.md` and `docs/SANDBOX_ISOLATION.md`.

## Before you push (maintainer actions)

1. **Point the placeholders at your account** (they are `OWNER`/`REPO` today):

   ```bash
   python scripts/prepare_github.py --owner <your-github-user> --repo <repo-name>
   python scripts/prepare_github.py --owner <your-github-user> --repo <repo-name> --apply
   ```

2. ~~Pick the license~~ — **decided: AGPL-3.0-or-later** (see `LICENSE`). The
   trade-off (protection against a hosted fork vs. permissive adoption) was made
   on purpose.
3. ~~Review `docs/internal/`~~ — **decided: it stays public.** The round reports
   and review iterations are part of the record; nothing imports them.
4. **Run the gates**: `./scripts/ci_local.sh` (or at least `pytest tests -q`,
   `npx tsc --noEmit`, `npx vitest run`, `node scripts/check_i18n.mjs`).
5. **Create the repo and push** — add it as `origin` first, because the history
   was rewritten (see below).

## Known limitations of this pass

- **The Docker image was not built** — there is no Docker on the machine this was
  prepared on, and the maintainer accepted that (`Dockerfile`/`docker-compose.yml`
  follow the real entry points: `python -m kairos serve`, `KAIROS_DATA_DIR`, the
  `web/dist` resolution, and the build hook that ships the UI). The first
  `docker compose up --build` is still the real check.
- **CI runs on `ubuntu-latest` but was only executed locally on Windows** (via
  `scripts/ci_local.sh`). Linux-only paths (Landlock, `os.fork`) are covered by
  tests that skip elsewhere; the first GitHub run is the real check.
- **Low-resource translations** (`by-BY`, `kmr-IQ`, `tk-TK`, `my-MM`, `si-LK`,
  `hy-AM`) were machine-translated and flagged for a native review.
- **`en-US` reporting "0% coverage"** in the translation tooling is expected: it
  is the source language and has no catalog.
- **History was rewritten** with `git-filter-repo` to drop a 640 MB third-party
  archive and unrelated extracted trees. All commit hashes changed; if you have
  any older clone, re-clone. A pre-rewrite bundle exists locally
  (`_kairos_code_git_backup.bundle`) — do **not** publish it.

## Deliberately not done

- **No cloud parallel sandbox** (fan out N containers, open PRs). The queue is
  local and sequential by design in 0.1.
- **No GitHub App / PR bot.** `kairos review` + the Gate Report are the
  primitives; wiring them into a bot is a separate project.
- **No benchmark claims.** The README says "more controllable process", not
  "writes better code", because there are no published scores to back the latter.
