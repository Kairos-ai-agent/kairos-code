# Kairos Code — Full Project Index

> Last updated: 2026-08-27, end of Round 24.

This is a single-page index of every module, test, and report
in the Kairos Code project, organized by feature area. Use it
as a map when you come back to this codebase after a break.

---

## Test totals

- **491 tests pass**, 19 skipped (Linux-only), 0 failed
- Sweep runtime: **24.25 seconds** (`pytest -q -p no:cacheprovider`)
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
| `scripts/adapt_superpowers_skills.py` | R9 | Anthropic-format → Kairos format adapter |
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
| `docs/kairos-vs-codex-vs-claude.png` | 9 | Comparison image (3 products × 30 features) |
| `docs/kairos-tui-screenshot.svg` | 9 | TUI screenshot |
| `docs/KAIROS_INDEX.md` | R24 | This file |

---

## How to run the full sweep

```bash
# Backend tests
cd D:\software_bak\Kairos_code
python -m pytest -q -p no:cacheprovider \
  --ignore=tests/test_bench_multi_agent.py \
  --ignore=tests/integration \
  --ignore=tests/unit/test_loop_run.py \
  --ignore=tests/test_commands.py \
  --ignore=tests/test_cli.py
# → 491 passed, 19 skipped in ~24s

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

# CLI: cost alerts
python -c "
from kairos.alerts import detect_from_files
from pathlib import Path
alerts = detect_from_files(Path('results/old.json'), Path('results/new.json'))
for a in alerts:
    print(f'{a.severity}: {a.message}')
"
```

---

## Architecture summary

```
                         ┌─ web (React + Vite)
                         ├─ Textual TUI
Kairos Code ─────────────┼─ CLI (eval / hook / trend / alerts / skill_search)
                         └─ FastAPI server

Internals:
  Agents ── Plan ── Skills (FTS5) ── Memory (4-op) ── Sandbox (5-tier)
  Loop    ── Tracer (OTel) ── Cost (litellm) ── Eval (record/replay/derive/alerts/trend)

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
