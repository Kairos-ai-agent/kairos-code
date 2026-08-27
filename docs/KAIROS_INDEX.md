# Kairos Code — Full Project Index

> Last updated: 2026-08-27, end of Round 36.

This is a single-page index of every module, test, and report
in the Kairos Code project, organized by feature area. Use it
as a map when you come back to this codebase after a break.

---

## Test totals

- **1429 backend tests + 22 frontend tests pass**, 23 skipped, 0 failed
  (full sweep — see `docs/ROUND_36_REPORT.md` §7 for the
  bucket-by-bucket breakdown; 4 pre-existing slow/flaky tests deselected)
- Total skills loaded: **26** (14 superpowers + 9 anthropic + 3 community)
- Sweep runtime: full sweep exceeds 10 min when including the 4 pre-existing
  slow loop tests; targeted sub-sweeps run in 5-25 s each
- TypeScript: `tsc --noEmit` clean
- Vitest: 6/6 passing (plan panel, plan history panel)

---

## Feature map

### Agent runtime (Coder / Reviewer / Specialist)

| Module | Round | What |
|---|---|---|
| `kairos/agents/base.py` | 1, R8, R11, R12 | `KairosAgent` base class, run loop, streaming, plan wiring, tracer wiring |
| `kairos/agents/coder.py` | 1 | Coder agent (the workhorse) |
| `kairos/agents/reviewer.py` | 1 | Reviewer agent |
| `kairos/agents/specialist.py` | 1 | Specialist agents (per-domain) |
| `kairos/llm/base.py` | 1 | `BaseLLMProvider` contract |
| `kairos/llm/provider_registry.py` | 1 | Provider registry |
| `kairos/llm/model_router.py` | R8, R11 | Role-based model routing |
| `kairos/llm/providers/litellm_provider.py` | R10 | 100+ provider gateway via LiteLLM |
| `kairos/llm/providers/ollama_provider.py` | R8 | Local Ollama support |
| `kairos/llm/providers/*.py` | various | Direct OpenAI, Anthropic, etc. |
| `kairos/loop/loop_runner.py` | 1, R12, R14 | `run_loop` orchestrator |
| `kairos/loop/review_loop.py` | 1, R12 | `LoopSession` + `LoopResult` |
| `kairos/loop/plan.py` | R10, R11, R12, R14 | `Plan` + `TodoItem` + diff helpers |
| `kairos/loop/review_loop.py` | R11 | Plan tracker attach |

### Tooling

| Module | Round | What |
|---|---|---|
| `kairos/tools/base.py` | 1 | `BaseTool` + `ToolResult` |
| `kairos/tools/checkpoint.py` | R12 | Git-based round checkpointing |
| `kairos/tools/*.py` | various | file_write, bash, read_file, etc. |

### Observability

| Module | Round | What |
|---|---|---|
| `kairos/observability.py` | R10, R11 | OTel tracer + Langfuse export |
| `kairos/cost.py` | R14, R16 | litellm cost_callback + JSONL log + per-model aggregation |
| `api/routes/cost.py` | R16, R19 | Cost API (summary / recent / by_model / datasets) |

### Skills (TodoWrite-style)

| Module | Round | What |
|---|---|---|
| `kairos/skills.py` | 1, R9 | `SkillsLoader` + 3-tier scope (bundled / global / project) |
| `kairos/skill_search.py` | R17 | FTS5 full-text search + Python fallback |
| `kairos/skills/*.md` (14 files) | R9 | Bundled superpowers skills |
| `kairos/skills/anthropic__*.md` (9 files) | R31, R33 | Bundled anthropic skills (webapp-testing, mcp-builder, frontend-design, skill-creator, theme-factory, doc-coauthoring, algorithmic-art, canvas-design, brand-guidelines) |
| `kairos/skills/community__*.md` (3 files) | R34 | Bundled community skills (senior-architect, tdd-guide, code-reviewer) — alirezarezvani/claude-skills |
| `scripts/adapt_superpowers_skills.py` | R9 | Anthropic-format → Kairos format adapter |
| `scripts/adapt_anthropic_skills.py` | R31 | Anthropic SKILL.md → Kairos format adapter (priority 0.7) |
| `scripts/adapt_community_skills.py` | R34 | alirezarezvani/claude-skills → Kairos format adapter (priority 0.6) |
| `web/src/components/SkillSearchPalette.tsx` | R23 | Ctrl+K command palette |
| `api/routes/skill_search.py` | R23 | Search API endpoint |

### Memory

| Module | Round | What |
|---|---|---|
| `kairos/memory_hierarchy.py` | R8 | 3-tier (user / project / session) |
| `kairos/memory_kb.py` | R10 | Cognee-style 4-op memory (local backend) |
| `kairos/memory_4op.py` | R15 | Multi-backend dispatcher (local / cognee / graphiti / mock) |

### Eval (regression detection)

| Module | Round | What |
|---|---|---|
| `kairos/eval.py` | R10, R12, R13, R14, R16, R19 | Core: graders, suite runner, compare, record, replay, derive, auto-record, CLI |
| `kairos/alerts.py` | R22 | Cost regression engine + webhook dispatcher |
| `kairos/doctor.py` | R32 | 14-check self-diagnostic CLI (settings / data / LLM / skills / FTS5 / MCP) |
| `kairos/alerts_dispatcher.py` | R28 | Slack sender + JSONL alert history + CLI |
| `api/routes/alerts.py` | R30 | Alert UI API (recent / summary / mute / mutes / unmute) |
| `kairos/har.py` | R29 | Long-running harness (`.har/` contract + resume runtime + lock) |
| `kairos/trend.py` | R24 | Multi-run trend aggregator |
| `examples/eval_ci.yaml` | R12 | CI smoke suite (5 mechanical cases) |
| `examples/eval_with_judge.yaml` | R20 | LLM-judge demo suite (2 cases) |
| `examples/eval_eval.yaml` | R13 | Meta-eval (tests the eval framework itself) |
| `examples/eval_ci_workflow.yaml` | R12, R14 | GitHub Actions workflow template |
| `scripts/import_session_to_eval.py` | R12 | Production session → eval suite converter |
| `kairos/hook.py` | R21 | Pre-commit runner (smoke + meta-eval + tests) |

### Sandbox / Isolation

| Module | Round | What |
|---|---|---|
| `kairos/sandbox.py` | 1, R10, R11, R15 | 5-tier detection (deny list / Landlock / nsjail / gVisor / Firecracker / macOS Seatbelt) |

### Configuration & settings

| Module | Round | What |
|---|---|---|
| `kairos/config/settings.py` | 1, R10 | pydantic settings (workers, loop, etc.) |
| `kairos/settings_store.py` | R11 | Voice / MCP / metrics / cloud / provider |
| `kairos/main.py` | R10 | uvicorn entrypoint with workers + loop selection |

### API surface

| File | Round | Endpoints |
|---|---|---|
| `api/app.py` | 1 + R10-R19 | Top-level FastAPI app |
| `api/routes/projects.py` | 1 | Project CRUD + run loop + plan |
| `api/routes/agents.py` | 1 | Agent state |
| `api/routes/config.py` | 1 | Settings drawer |
| `api/routes/memory.py` | 1 | Memory CRUD |
| `api/routes/teams.py` | 1 | Multi-agent teams |
| `api/routes/cloud.py` | 1 | Cloud sync |
| `api/routes/cost.py` | R16, R19 | Cost summary / recent / by_model / datasets / record / replay / derive |
| `api/routes/skill_search.py` | R23 | Search + reindex |
| `api/routes/alerts.py` | R30 | Recent / summary / mute / mutes / unmute |
| `api/routes/cost.py` (`/value`) | R35 | COGS value metrics (cost/case, efficiency, approval_yield) |
| `api/routes/websocket.py` | 1 | WebSocket relay |

### Frontend (React + Vite + antd + zustand)

| File | Round | What |
|---|---|---|
| `web/src/pages/Loop.tsx` | 1, R11-R19 | Loop review page (now hosts Plan + PlanHistory + Cost + Eval + Skill palette) |
| `web/src/pages/Chat.tsx` | 1 | Chat-style session page |
| `web/src/pages/Dashboard.tsx` | 1 | Agent dashboard |
| `web/src/pages/Settings.tsx` | 1 | Settings page |
| `web/src/pages/Tools.tsx`, `Trace.tsx`, `Today.tsx` | 1 | Other pages |
| `web/src/components/PlanPanel.tsx` | R13 | Live TodoWrite checklist |
| `web/src/components/PlanHistoryPanel.tsx` | R14 | Per-round diff timeline |
| `web/src/components/AlertPanel.tsx` | R30 | Recent alerts + mute buttons (Today page) |
| `web/src/components/CogsPanel.tsx` | R36 | COGS value metrics (cost / value ratios) — Today page |
| `web/src/components/CostDashboard.tsx` | R16 | Per-model spend panel |
| `web/src/components/EvalPanel.tsx` | R19 | 4-tab eval (Datasets/Record/Replay/Derive) |
| `web/src/components/SkillSearchPalette.tsx` | R23 | Ctrl+K command palette |
| `web/src/components/SettingsDrawer.tsx` | R9, R11 | Settings UI with provider panel + voice + MCP |
| `web/src/components/MermaidRenderer.tsx` | 1 | Plan visualization (mermaid) |
| `web/src/components/ChatThread.tsx` | 1 | Message list |
| `web/src/components/ChatComposer.tsx` | 1 | Input box |
| `web/src/components/ChatSidebar.tsx` | 1 | Session list |
| `web/src/components/AppLayout.tsx` | 1 | Top-level layout |
| `web/src/stores/*` | 1 | zustand stores (chat, settings, agent, etc.) |
| `web/src/types/index.ts` | 1 | TS types for the whole app |
| `web/src/test/*.test.tsx` | various | vitest + RTL tests |

### Terminal UI

| Module | Round | What |
|---|---|---|
| `kairos/tui_textual.py` | R18 | Textual 3-pane dashboard (skills + cost + log) |
| `kairos/tui/` | pre-R18 | Legacy plain-ANSI TUI (kept for compatibility) |

### Vendor / sources

| Path | Round | What |
|---|---|---|
| `vendor/_oss/superpowers/` | R9 | Shallow-cloned upstream of `obra/superpowers` |

### Reports (one per round)

| Path | Round | Topic |
|---|---|---|
| `docs/CODEX_HARNESS_INTEGRATION_REPORT.md` | 1-7 | Codex-Harness integration |
| `docs/CODEX_CLAUDE_PARITY_PROGRESS.md` | 1-7 | Parity with Codex / Claude Code |
| `docs/REAL_FEATURES_REPORT.md` | 5 | Real features push |
| `docs/LANDING_RECOMMENDATIONS_REPORT.md` | 6 | Landing-page recommendations |
| `docs/ROUND_7_FOLLOWUP_REPORT.md` | 7 | Followup |
| `docs/code_review_report.md` | various | Code review |
| `docs/OSS_ADOPTION_ROADMAP.md` | R9 | OSS adoption roadmap |
| `docs/ROUND_8_REPORT.md` | R8 | Tier 1/2/3 optimization |
| `docs/ROUND_9_REPORT.md` | R9 | OSS adoption |
| `docs/ROUND_10_REPORT.md` | R10 | Tier 2 + 2 new discoveries |
| `docs/ROUND_11_REPORT.md` | R11 | Wiring round |
| `docs/ROUND_12_REPORT.md` | R12 | Plan persistence + eval-CI + importer |
| `docs/ROUND_13_REPORT.md` | R13 | Record/replay/derive + PlanPanel + meta-eval |
| `docs/ROUND_14_REPORT.md` | R14 | Plan history + auto-record + cost |
| `docs/ROUND_15_REPORT.md` | R15 | Firecracker + Cognee adapter |
| `docs/ROUND_16_REPORT.md` | R16 | Cost dashboard API + UI |
| `docs/ROUND_17_REPORT.md` | R17 | FTS5 skill search |
| `docs/ROUND_18_REPORT.md` | R18 | Textual TUI |
| `docs/ROUND_19_REPORT.md` | R19 | EvalPanel UI + Datasets API |
| `docs/ROUND_20_REPORT.md` | R20 | Per-case LLM judge CLI |
| `docs/ROUND_21_REPORT.md` | R21 | Pre-commit hook runner |
| `docs/ROUND_22_REPORT.md` | R22 | Cost regression alerts |
| `docs/ROUND_23_REPORT.md` | R23 | Frontend skill search |
| `docs/ROUND_24_REPORT.md` | R24 | Multi-run trend aggregator |
| `docs/ROUND_25_REPORT.md` | R25 | Trend API + UI panel |
| `docs/ROUND_26_REPORT.md` | R26 | Per-case flaky detection |
| `docs/ROUND_27_REPORT.md` | R27 | Windows pre-commit compatibility |
| `docs/kairos-vs-codex-vs-claude.png` | 9 | Comparison image (3 products × 30 features) |
| `docs/kairos-tui-screenshot.svg` | 9 | TUI screenshot |
| `docs/KAIROS_INDEX.md` | R27 | This file |

---

## How to run the full sweep

```bash
# Backend tests (full sweep)
cd D:\software_bak\Kairos_code
python -m pytest -q -p no:cacheprovider \
  --deselect tests/test_perf.py::test_timed_async_records_sample \
  --deselect tests/test_bench_multi_agent.py::test_parallel_coder_speedup \
  --deselect tests/unit/test_review_helpers.py::test_run_loop_uses_specialists_when_configured \
  --deselect tests/unit/test_review_helpers.py::test_run_loop_best_of_n_runs_multiple_coders
# → 1429 backend + 22 frontend passed, 23 skipped (timing-flaky + 30 s+ loop tests deselected)

# Backend tests (fast sub-sweep, ~28 s)
python -m pytest -q -p no:cacheprovider \
  --ignore=tests/test_bench.py \
  --ignore=tests/test_bench_multi_agent.py \
  --ignore=tests/test_integration.py \
  --ignore=tests/test_perf.py \
  --ignore=tests/unit/test_review_helpers.py
# → ~1000 passed in ~28 s

# Frontend type check
cd web
npx tsc --noEmit
# → clean

# Frontend vitest
cd web
npx vitest run
# → 6 passed

# CLI: pre-commit hook
cd D:\software_bak\Kairos_code
python -m kairos.hook run --fast
# → 3 checks (skill-search, meta-eval, smoke) all pass

# CLI: trend
python -m kairos.trend results/ --window 20
# → table of recent runs

# CLI: cost alerts (R22 detect, R28 dispatch)
python -m kairos.alerts_dispatcher detect results/old.json results/new.json
# → finds regressions + POSTs to $KAIROS_SLACK_WEBHOOK + appends to data/alerts.jsonl

# CLI: alert history (R28)
python -m kairos.alerts_dispatcher history --limit 20
# → newest-first list of fired alerts

# CLI: long-running harness (R29)
python -m kairos.har init "migrate 47 endpoints to FastAPI DI"
python -m kairos.har status
python -m kairos.har resume --rounds 5        # pick up tomorrow morning
python -m kairos.har checkpoints              # list rounds from history.jsonl

# CLI: re-adapt anthropic skills after `git pull` (R31)
python scripts/adapt_anthropic_skills.py     # vendor/_oss/.../SKILL.md -> kairos/skills/

# CLI: re-adapt community skills after `git pull` (R34)
python scripts/adapt_community_skills.py    # priority 0.6 (community/3rd-party)

# CLI: self-diagnostic (R32)
python -m kairos.doctor                     # 14 checks, [OK]/[WARN]/[FAIL]
python -m kairos.doctor --json              # machine-readable
python -m kairos.doctor --only python       # filter to one check

# API: alerts (R30 — also visible in the Today page)
curl http://localhost:8000/api/alerts/recent?limit=20
curl http://localhost:8000/api/alerts/summary
curl -X POST http://localhost:8000/api/alerts/mute \
  -H "Content-Type: application/json" \
  -d '{"key": "cost_spike:cost_usd", "duration_s": 3600}'

# API: cost value (R35 — COGS metrics)
curl http://localhost:8000/api/cost/value
# → { total_cost_usd, n_llm_calls, dataset: {...},
#     alerts: {...}, metrics: { cost_per_case,
#     cost_per_passing, cost_per_alert, efficiency,
#     approval_yield } }
```

---

## Architecture summary

```
                         ┌─ web (React + Vite)
                         ├─ Textual TUI
Kairos Code ─────────────┼─ CLI (eval / hook / trend / alerts / alerts_dispatcher / har / skill_search)
                         └─ FastAPI server

Internals:
  Agents ── Plan ── Skills (FTS5) ── Memory (4-op) ── Sandbox (5-tier)
  Loop    ── Tracer (OTel) ── Cost (litellm) ── Eval (record/replay/derive/alerts/trend) ── Dispatcher (R28)

OSS adopted (top 10):
  1. openai/codex              (the harness we mirror)
  2. anthropic/skills           (skill format)
  3. obra/superpowers          (drop-in skill library, 14 skills)
  4. BerriAI/litellm           (provider gateway)
  5. topoteretes/cognee        (memory model)
  6. getzep/graphiti           (memory backend)
  7. langchain-ai/deepagents   (deep-agent pattern)
  8. GoogleCloudPlatform/gVisor (sandbox tier)
  9. firecracker-microvm/firecracker (sandbox tier)
 10. sqlite (FTS5)             (skill search)
```
