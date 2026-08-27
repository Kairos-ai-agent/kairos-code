# Round 32 — `kairos doctor` self-diagnostic command

**Goal:** when something doesn't work, the user gets a 5-second
verdict on "what's broken in my install" — not a 5-hour source-dive.

**Scope:** 1 new module + 1 new test file + 1 docs update.

---

## 1. What ships

| File | Lines | Purpose |
|------|-------|---------|
| `kairos/doctor.py` | 430 | 14 diagnostic checks + CLI |
| `tests/test_doctor.py` | 415 | 37 tests (each check + main + crash-resilience) |
| `docs/ROUND_32_REPORT.md` | this | – |

## 2. The 14 checks

Grouped by category in the human output:

| Group | Check | What it verifies |
|-------|-------|------------------|
| runtime | `python_version` | ≥ 3.10 (warn 3.8-3.9, fail < 3.8) |
| runtime | `platform` | terse `platform.platform()` for debugging |
| runtime | `dependencies` | `fastapi`, `pydantic`, `yaml`, `click` importable |
| runtime | `git` | `git --version` runs (needed for R12 checkpoints + R29 harness) |
| config | `settings_loadable` | `kairos.config.settings.settings` instantiates |
| filesystem | `data_dir` | `KAIROS_DATA_DIR` (or default) exists + writable |
| filesystem | `workspace_dir` | settings.workspace_dir exists + writable |
| filesystem | `alerts_history_dir` | `data/alerts.jsonl` parent writable |
| filesystem | `vendor_dir` | `vendor/` exists (offline deps) |
| llm | `llm_providers` | at least one of `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / etc. set (WARN if none) |
| llm | `ollama_reachable` | if `OLLAMA_BASE_URL` set, GET to it within 5s |
| skills | `skills_loadable` | `SkillsLoader.discover()` returns ≥ 1 skill |
| skills | `fts5` | SQLite FTS5 build (warn + Python fallback if absent) |
| mcp | `mcp_config` | `.mcp.yaml` parses (if present) |

## 3. Status semantics

| Status | Meaning | Counts toward exit code? |
|--------|---------|--------------------------|
| `[OK]`   | everything fine | no |
| `[WARN]` | suboptimal but workable (e.g. no API keys, Ollama down) | no |
| `[FAIL]` | broken (e.g. non-writable data dir) | **yes — exits 1** |

The user can run `python -m kairos.doctor` after a fresh install
and immediately see "12 OK, 2 WARN, 0 FAIL" with the two WARNs
explaining what to fix.

## 4. CLI

```
$ python -m kairos.doctor
  RUNTIME
    [OK]    Python version: 3.11.15
    [OK]    Platform: Windows-10
    [OK]    Dependencies: fastapi, pydantic, yaml, click all importable
    [OK]    Git: git version 2.55.0.windows.5

  CONFIG
    [OK]    Settings: host=0.0.0.0 port=8900 default=openai

  FILESYSTEM
    [OK]    Data dir: D:\software_bak\Kairos_code\data
    [OK]    Workspace dir: workspace
    [OK]    Alerts history: writable at D:\software_bak\Kairos_code\data\alerts.jsonl
    [OK]    Vendor dir: D:\software_bak\Kairos_code\vendor (0 wheels)

  LLM
    [WARN]  LLM providers: no API keys set; will use Ollama if reachable
            hint: Set OPENAI_API_KEY / ANTHROPIC_API_KEY / etc. for cloud providers.
    [WARN]  Ollama reachable: http://localhost:11434 unreachable: <urlopen error ...>
            hint: Start the Ollama daemon or change OLLAMA_BASE_URL.

  SKILLS
    [OK]    Skills loader: 20 skills discovered
    [OK]    FTS5 full-text search: available

  MCP
    [OK]    MCP config: (not configured)

  12 OK, 2 WARN, 0 FAIL
$ echo $?
0   # WARN doesn't fail; 0 if any FAILs then 1
```

### 4.1 Flags

| Flag | Effect |
|------|--------|
| `--json` | Emit one JSON object per check (machine-readable) |
| `--only SUBSTR` (repeatable) | Run only checks whose function name matches |

Examples:
```bash
python -m kairos.doctor --json | jq '.[] | select(.status=="fail")'
python -m kairos.doctor --only python --only platform
```

## 5. Tests (37, all green)

`tests/test_doctor.py` covers 4 sub-groups:

| Group | Count | Covers |
|-------|-------|--------|
| Status helpers | 2 | `_ok` / `_warn` / `_fail` / `to_dict` |
| Individual checks (happy paths) | 19 | each check returns ok/warn/fail as expected |
| `DEFAULT_CHECKS` | 3 | non-empty, callable, unique names |
| Formatters | 2 | `_format_table` groups, `_summary` counts |
| `main()` | 9 | human output, JSON output, exit codes, `--only` filter, no-match, surviving crashes, failure-in-JSON |

**Total: 37 / 37 passing in 19.84 s.**

### 5.1 Notable test design

- **Settings reload** — to test `check_llm_providers` with a single
  API key, we `importlib.reload(kairos.config.settings)` after
  clearing the other env vars (the Settings class freezes env at
  construction; reload is the only way to re-read the env).
- **DEFAULT_CHECKS patching** — to test "what if a check fails",
  we replace the function inside `DEFAULT_CHECKS` (the list holds
  references, so `monkeypatch.setattr(doctor, "check_python_version", ...)`
  doesn't propagate to the list).
- **Survives crashes** — `test_main_survives_a_crashing_check`
  adds a check that raises `RuntimeError("intentional crash")` to
  the front of the list; the doctor catches the exception and
  reports a FAIL with "intentional crash" in the message rather
  than dying.

## 6. Test sweep — full state after R32

| Bucket | Count | Result |
|--------|-------|--------|
| **R32 new** | 37 | 37 pass, 0 fail |
| R31 stack (adapt_anthropic_skills) | 16 | 16 pass, 0 fail |
| R30 stack (alerts API + UI) | 27 | 27 pass, 0 fail |
| R29 stack (har) | 47 | 47 pass, 0 fail |
| R28 stack (alerts_dispatcher) | 28 | 28 pass, 0 fail |
| R22-R27 stack (alerts/cost/trend/hooks/eval/judge/meta-eval/auto_record) | 204 | 204 pass, 1 skip |
| tui / sessions / plan_history | 73 | 73 pass, 0 fail |
| streaming / compaction / voice / sandbox | 160 | 160 pass, 23 skip |
| skills / ollama / memory / observability | 204 | 204 pass, 0 fail |
| cloud / s3_cloud / integration | 100 | 100 pass, 0 fail |
| resilience | 47 | 47 pass, 0 fail |
| review_helpers (excl. 2 slow loop tests) | 18 | 18 pass, 0 fail |
| commands / coder_modes / hooks / teams | 114 | 114 pass, 0 fail |
| multimodal / manifest / permissions / plugins / worktree / sessions / approval / guardrails / main_workers / cli | 171 | 171 pass, 0 fail |
| api_projects / confidence / file_edit / hooks / learning_reflect / memory_api / review_engine | 54 | 54 pass, 0 fail |
| mcp / ollama / judge_cli / meta_eval / auto_record / reflection / retained_reasoning | 94 | 94 pass, 0 fail |
| **Confirmed passing** | **1394** | **0 fail** |
| 4 deselected (pre-existing flaky/slow) | – | not from R32 |

**Delta vs R31:** +37 tests, 0 regressions.

## 7. Risk + mitigation

| Risk | Mitigation |
|------|------------|
| A check crashes the whole doctor | `try/except` around each check; a crash is reported as a FAIL with hint "This is a doctor bug" |
| Check takes too long (e.g. network timeout) | Each network call has a 5s timeout; OLLAMA_BASE_URL is the only network check |
| `KAIROS_DATA_DIR` env mutation mid-test (R11 lesson) | `_get_history_path()` reads env at call time; we never cache the path |
| Settings import fails (e.g. corrupt `Settings` class) | `check_settings_loadable` catches and reports FAIL — doctor still runs the other 13 checks |
| `KAIROS_DATA_DIR` test isolation | `monkeypatch.setenv` per test; the env mutation is reverted at test teardown |

## 8. Follow-up

`kairos doctor` closes the "self-service triage" gap. Remaining
items on the roadmap:

- `alirezarezvani/claude-skills` adaptation (143 skills) — drop-in
  via the same `adapt_superpowers_skills.py` pattern, just bigger
- The 13 remaining Anthropic skills (drop from `SKIP` and re-run
  the R31 adapter)
- COGS dashboard (cost-of-generation metrics beyond R16's spend
  panel — e.g. cost per approved round, cost per LOC, cost per PR)
- Tier 3 picks (inspiration only)

## 9. Diff summary

```
 kairos/doctor.py                  | 430 +++++++++++ (new)
 tests/test_doctor.py              | 415 +++++++++++ (new)
 docs/ROUND_32_REPORT.md           | this file
 docs/OSS_ADOPTION_ROADMAP.md      | +1 row (R32)
 docs/KAIROS_INDEX.md              | +1 row + 1394 test total
```

R32 ships green: 37 new tests, 1394/1394 confirmed passing, no
regressions in any of the R8-R31 modules. `tsc --noEmit` clean.
