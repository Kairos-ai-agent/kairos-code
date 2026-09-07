# Kairos Code

**LoopReview development harness — one Coder agent and one Reviewer agent, locked in an auto-review loop until the Reviewer approves.**

Kairos Code runs a single universal Coder agent (full tools: file read/write/edit, grep, find, git, terminal allowlist, web fetch/search, subagent fork) against a single strict Reviewer agent (read-only + tests). The Reviewer grades each round on a 0-100 weighted rubric and the orchestrator runs them in a loop until approval, max rounds, or one of the early-stop gates fires.

## LoopReview Model

Two roles, one loop:

| Role | Job | Default Model |
|---|---|---|
| Coder | Reads requirement, edits files, runs tests, iterates | OpenAI `gpt-4o` |
| Reviewer | Reads diff, runs tests, returns strict JSON verdict `{approve, score, issues[], summary}` | Anthropic `claude-sonnet-4` |

The loop is a single background `asyncio.Task`. UI sees everything via the MessageBus → WebSocket pipeline.

## Stop Conditions

The loop exits on whichever fires first:

1. **Approve gate** — Reviewer returns `approve=true`, `score ≥ 75`, no CRITICAL issue
2. **Cost cap (tokens)** — cumulative prompt+completion tokens exceed `COST_TOKEN_CAP` (default 500k)
3. **Cost cap (wall-clock)** — `time.time() - session.started_at ≥ COST_TIME_CAP_S` (default 30 min); doubled for heavy tasks via `dynamic_caps`
4. **Infra-failure streak** — Reviewer returns 5 consecutive parse/tool-limit failures (no_progress never trips on infra failures, so this catches the stuck-broker case)
5. **Score stagnation** — last 3 scores within ±2 of each other AND below approval threshold
6. **No-progress** — same issue signature repeats for 5 rounds
7. **Safety cap** — hard limit at 50 rounds (defensive, should rarely fire now)
8. **User stop** — user clicks stop in the UI

Each gate publishes `loop.stopped` with a `reason` field so the UI can show why the loop ended.

## Features

- **LoopReview engine** — Coder ↔ Reviewer auto-loop with gate-based termination
- **Plan-mode** — first round is prose-only (no tools), user approves before execution starts
- **Cross-loop memory** — past rounds persist in SQLite; next loop on the same project gets a digest of what was tried
- **Git checkpoint** — every approved round is auto-committed; user can roll back to any round from the UI
- **Best-of-N** — run N parallel Coder subagents per round, pick the highest-scoring diff (opt-in via YAML)
- **Specialist reviewers** — security / perf / design / test focused Reviewers (opt-in via YAML), scores are weighted-averaged
- **Ask-human gate** — Reviewer can emit `loop.ask_human` to pause and request clarification
- **Multi-LLM** — OpenAI, Anthropic, DeepSeek, Ollama, DashScope, ZhipuAI, Gemini, OpenRouter, custom providers
- **Per-role model routing** — bind any model profile to the Coder or Reviewer role via Settings
- **Hooks** — `data/hooks/*.py` can intercept `pre_tool_use`, `post_tool_use`, `loop_round`, `loop_completed`
- **WebSocket** — real-time message bus + agent state heartbeat
- **Inline review comments** — Reviewer issues export as `::code-comment` JSON for editor plugins

## Quick Start

### Backend

```bash
cd D:\software_bak\Kairos_code
pip install -e .
copy .env.example .env
# Edit .env with your API keys
python -m kairos.main
```

Server runs at http://localhost:8900. API docs at http://localhost:8900/docs.

### Frontend

```bash
cd D:\software_bak\Kairos_code\web
npm install
npm run dev
```

Frontend runs at http://localhost:3000.

### Silent start (background)

```bat
start_silent.bat   :: starts backend, frontend, watchdog; opens browser
stop_silent.bat    :: kills everything cleanly
```

## Architecture

```
Kairos Code
├── kairos/
│   ├── agents/
│   │   ├── base.py            # KairosAgent — tool-calling loop with memory + streaming
│   │   └── roles/
│   │       ├── coder.py       # Universal Coder — full tools, plan-and-execute
│   │       ├── reviewer.py    # Strict Reviewer — read-only + tests, JSON verdict
│   │       ├── security.py    # Opt-in: security-focused Reviewer (OWASP)
│   │       ├── perf.py        # Opt-in: perf-focused Reviewer
│   │       ├── design.py      # Opt-in: design-focused Reviewer
│   │       └── test.py        # Opt-in: test-coverage-focused Reviewer
│   ├── core/
│   │   ├── orchestrator.py    # Project lifecycle + loop launch
│   │   ├── message_bus.py     # Async pub/sub
│   │   └── persistence.py     # SQLite (projects, messages, loop_rounds, comments, files)
│   ├── llm/
│   │   ├── base.py            # LLMConfig / LLMResponse / BaseLLMProvider
│   │   ├── model_router.py    # Per-role model routing + provider discovery
│   │   └── providers/         # 8 providers + custom
│   ├── tools/                 # file_read, file_write, file_edit_replace, multi_edit,
│   │                          # grep, find, git, terminal (allowlist), webfetch,
│   │                          # websearch, subagent, checkpoint
│   ├── loop/
│   │   └── loop_runner.py    # run_loop + gates + plan-mode gate (review_loop.py re-exports)
│   ├── review/
│   │   ├── engine.py          # Standalone file/project review (not loop-coupled)
│   │   ├── comments.py        # Reviewer verdict -> ::code-comment JSON
│   │   └── mermaid.py         # Plan text -> mermaid flowchart + file tree
│   └── hooks/                 # Hook runner (loads data/hooks/*.py)
├── api/                       # FastAPI REST + WebSocket
├── web/                       # React + TypeScript
│   ├── Dashboard              # Project overview
│   ├── Projects               # Create / configure projects
│   ├── Loop                   # Live loop view: score chart, diff viewer, ask-human modal
│   └── Settings               # Model + role routing + best-of-N + specialist toggles
└── tests/                     # Pytest, asyncio_mode = "auto"
```

## Agent Roles

Two roles, period. The README used to claim 8 (PM/Architect/QA/DevOps/etc.); those were aspirational and never implemented. If you need a second opinion, enable the specialist reviewers (`security` / `perf` / `design` / `test`) — they run alongside the main Reviewer and add weighted scores.

| Role | Description | Default Model |
|---|---|---|
| Coder | Reads requirement, plans, edits files, runs tests | OpenAI `gpt-4o` (temperature 0.7) |
| Reviewer | Reads diff, runs tests, returns JSON verdict | Anthropic `claude-sonnet-4` (temperature 0.3) |
| Security Reviewer | OWASP Top 10, secret leaks, auth checks | (opt-in, same model as Reviewer) |
| Perf Reviewer | N+1 queries, hot loops, memory growth | (opt-in) |
| Design Reviewer | Abstractions, fit with existing code | (opt-in) |
| Test Reviewer | Coverage gaps, edge cases, flaky tests | (opt-in) |

## API

| Endpoint | Method | Description |
|---|---|---|
| `/api/projects` | GET / POST | List / create projects |
| `/api/projects/:id` | GET / DELETE | Get / delete a project |
| `/api/projects/:id/start` | POST | Start the loop (async; status returns immediately) |
| `/api/projects/:id/stop` | POST | User-initiated stop |
| `/api/projects/:id/plan` | GET | Current plan (pending/decision/text/mermaid) |
| `/api/projects/:id/plan/approve` | POST | Approve the Coder plan |
| `/api/projects/:id/plan/reject` | POST | Reject the plan |
| `/api/projects/:id/loop` | GET | Current loop state: round, score, issues, history |
| `/api/projects/:id/stats` | GET | Score history, token usage, cost estimate, gate firings |
| `/api/projects/:id/diff` | GET | Per-round git diff (from/to round) |
| `/api/projects/:id/checkpoint` | GET / POST | List rounds / checkout a round |
| `/api/projects/:id/ask` | POST | Answer a pending Reviewer question |
| `/api/projects/:id/comments` | GET | Inline `::code-comment` JSON for editor plugins |
| `/api/projects/:id/files` | GET / POST / DELETE | Reference file upload |
| `/api/projects/:id/messages` | GET | Project-scoped message history |
| `/api/agents` | GET | List agent states |
| `/api/agents/chat` | POST | Chat directly with the Coder |
| `/api/agents/task` | POST | Assign a one-off task to the Coder |
| `/api/review/project` | POST | One-shot review of a project (no loop) |
| `/api/review/file` | POST | One-shot review of a single file |
| `/api/config/models` | GET / POST | Model profiles + role mapping |
| `/ws/collaboration` | WS | Real-time updates |

## Security

Kairos is a local-first tool; keep it on your own machine.

- **Loopback bind by default.** `KAIROS_HOST` defaults to `127.0.0.1`. To
  expose the API to a LAN/network, set `KAIROS_HOST` explicitly — and make
  sure `KAIROS_API_TOKEN` is also set (the app logs a warning if you bind a
  non-loopback host without a token).
- **Optional API token.** Set `KAIROS_API_TOKEN` and every `/api` route
  (except `/api/health`, `/docs`, `/openapi.json`, `/redoc`) requires it via
  `Authorization: Bearer <token>`, `X-API-Token`, or `?token=`. The `/ws`
  socket is exempt so the bundled UI still connects. When no token is set the
  API is open on loopback — don't run that on a non-loopback host.
- **Terminal tool runs argv, not a shell.** Commands are parsed with `shlex`
  and executed via `create_subprocess_exec`, so shell chaining/redirection is
  impossible; the allow-listed head is the real executable. Interpreter and
  leak-prone heads (`python`, `node`, `env`, `cat`, `head`, `tail`, `which`,
  `where`) are excluded by default. The `kairos.sandbox` deny-list is applied,
  and the child PID is attached to the platform sandbox (Windows Job Object)
  for process-tree cleanup.
- **Web fetch / config endpoints block SSRF.** `webfetch` refuses loopback,
  private, link-local, and cloud-metadata hosts (`169.254.169.254`) and does
  not follow redirects. The provider-config endpoints additionally block
  link-local/metadata targets (loopback + private LAN stay allowed so a local
  LLM server still works).
- **Filesystem browsing is rooted.** `/api/fs/*` only descends into
  `KAIROS_FS_ALLOW_ROOTS` (default: workspace_dir + home). Set it to `*` for
  the historical whole-drive browsing, or to a comma-separated path list.
- **No plaintext key round-trip.** `GET /api/config/settings` only returns
  masked keys; the full keys stay on disk in `data/settings.json`.

## License

MIT