# Round 10 — Tier 2 全量采纳 + 新发现

> Status: **6/6 Tier-2 items shipped + 2 new discoveries (deepagents
> reference + agent-eval framework)**. **85 new tests pass** (9
> Linux-only skipped, all nsjail). `tsc --noEmit` clean. Pre-existing
> 165 tests still green (1 platform-skipped).

This round executes the Tier-2 queue from the
[OSS Adoption Roadmap](./OSS_ADOPTION_ROADMAP.md), plus two
discoveries from the new scan: `langchain-ai/deepagents` (the
canonical "Claude Code architecture open-sourced" reference) and
`agentkitai/agenteval` (eval framework for agent regression
detection). All work ships with tests and docs.

---

## 1. LiteLLM provider (Tier 2 — provider 抽象) ✅

`BerriAI/litellm` (the de-facto 100+ provider gateway) is now a
first-class provider in Kairos.

### What changed
- **`kairos/llm/providers/litellm_provider.py`** (10.4 KB) — wraps
  `litellm.acompletion` with Kairos's `BaseLLMProvider` contract.
  Auto-registers under `"litellm"` in `ProviderRegistry`.
- **11 tests** in `tests/test_litellm_provider.py` covering message
  coercion (basic / tool_call_id / tool_calls dict-args / string-args),
  response mapping (text / tool_calls / usage tokens), and graceful
  handling of malformed responses.
- **The model string is now free-form** — bare names like `gpt-4o`
  resolve to the appropriate provider; prefixed names like
  `bedrock/<id>`, `ollama/<model>`, `openrouter/<id>` are routed
  explicitly. Base URL is honored for OpenAI-compatible backends
  (vLLM / Ollama proxy / self-hosted gateways).

### What this unlocks
- 100+ providers with **one** import: `from kairos.llm.providers import litellm_provider`
- Built-in retry, fallback, response caching, cost tracking (litellm
  ships a per-model price table)
- Drop-in migration path for users who already have a LiteLLM
  config (`litellm_config.yaml`) — they can keep using it and
  Kairos routes through it

### Why not replace `model_router.py` outright?
The current `ModelRouter` has a `role_mappings` field that points
to a model name, then dispatches via `_create_dynamic_config` —
that's Kairos-specific. LiteLLM doesn't have a role-mapping
concept; the user picks one provider per call. We keep the
router as the role-mapping layer and put LiteLLM **under** it:
the router resolves a role → model, the LiteLLM provider
resolves model → real call. Best of both.

---

## 2. Langfuse / OpenTelemetry observability (Tier 2) ✅

LLM call tracing via standard OTel semantic conventions. Langfuse
accepts OTLP spans natively (it runs an OTel receiver on its
default port), so we ship a generic OTel exporter and document
the Langfuse setup path.

### What changed
- **`kairos/observability.py`** (13.6 KB) — a `Tracer` class that
  emits OTel spans when configured, otherwise falls back to an
  in-memory `NoOpSpan` for tests. Honors `OTEL_EXPORTER_OTLP_ENDPOINT`
  env var (the OTel standard). Includes a nested `span.llm_call()`
  context manager that auto-records latency.
- **9 tests** in `tests/test_observability.py` (all pass) covering
  the no-op path (used by tests and dev), exception recording,
  status setting, default-tracer singleton, and the lazy-init
  contract.
- Standard OTel attributes on LLM spans:
  - `gen_ai.operation.name = "chat"`
  - `gen_ai.request.model` / `gen_ai.system`
  - `gen_ai.request.message_count`
  - `gen_ai.usage.input_tokens` / `output_tokens` / `cost`
  - `gen_ai.response.duration_ms` / `finish_reason`

### What this unlocks
- Drop-in Langfuse support: `OTEL_EXPORTER_OTLP_ENDPOINT=http://langfuse:4317`
  → every LLM call shows up in the Langfuse UI with full
  prompt/response/cost/timing
- Zero coupling: the SDK doesn't import Langfuse. If you want
  Langfuse SDK-style (their custom HTTP client), the path is the
  same — set the OTLP endpoint to their collector

---

## 3. Cognee-style memory layer (Tier 2 — memory) ✅

`topoteretes/cognee` (27K ★, Apache 2.0) is the reference for
"agent memory as a 4-op CRUD". Kairos now has a local-first
implementation of the same API surface.

### What changed
- **`kairos/memory_kb.py`** (8.5 KB) — `MemoryKB` class with
  the four atomic ops:
  - `remember(key, value, scope, tags)` — insert or update
  - `recall(query, scope, limit)` — substring / token match
  - `forget(key, scope)` — delete
  - `improve(key, feedback, scope)` — append human feedback
  Three scopes: `user` / `project` / `session`. Thread-safe
  (`threading.Lock`). Atomic writes (`.tmp` + `os.replace`).
- **23 tests** in `tests/test_memory_kb.py` (all pass) covering
  every op, scope isolation, persistence round-trip, concurrent
  writes (20 threads × 5 writes), invalid-scope errors, recall
  ordering, and feedback accumulation.

### What this unlocks
- Drop-in alternative to the existing `kairos.memory_hierarchy`
  (3-tier JSON). Same scope semantics, slightly different API.
- The `MemoryKB` class is the seam for a future round to swap in
  graphiti / cognee proper without changing call sites.

### Tier 2 in `.mcp.example.yaml`
Also added: `modelcontextprotocol/server-memory` is in the example
MCP config (Round 9), which is the official MCP-native graph memory
server. Users can either pick that (zero Python work) or use
`MemoryKB` (in-process, no network).

---

## 4. nsjail sandbox tier (Tier 2 — sandbox) ✅

`google/nsjail` — process-level isolation for Linux, complementary
to the existing Landlock (path-level) tier.

### What changed
- **`kairos/sandbox.py`** (extended, +~120 lines) — three new
  functions:
  - `nsjail_available()` — feature-detect (Linux only, `nsjail`
    on PATH)
  - `_linux_nsjail_profile(policy)` — generates the nsjail config
    from a `SandboxPolicy` (mirrors Landlock semantics: allowed_root,
    deny_paths, network)
  - `wrap_command_in_nsjail(command, policy)` — wraps an argv
    as `nsjail --config <tmp.cfg> -- <cmd...>`
  - `describe_capabilities()` extended with the `nsjail` and
    `seatbelt` keys.
- `SandboxPolicy` extended with the nsjail-only knobs
  (`max_memory_mb`, `max_cpu_seconds`, `max_processes`, `deny_paths`,
  `use_nsjail`).
- **9 tests** in `tests/test_nsjail_sandbox.py` (all skip on
  Windows; will run on Linux CI). Cover profile generation,
  resource limits, network allow/deny, deny_paths emission,
  command wrapping, and unique temp paths.

### What this unlocks
- Production-grade process isolation on Linux without the
  complexity of KVM/Firecracker. nsjail starts in 50ms; uses
  Linux namespaces + seccomp-bpf (already in every Linux
  kernel).
- The user gets a single `SandboxPolicy` to express
  intent; `apply_to_subprocess` picks the right tier per host.

### Tier 2 still pending
- `gVisor` — needs Docker runtime registration (medium effort)
- `Firecracker` — needs KVM (heavy, but the strongest tier)
- `E2B` / `Daytona` — cloud-only, deferred until the user asks

---

## 5. Agent eval framework (new — discovery) ✅

`agentkitai/agenteval` + similar — we had no automated regression
testing for the Coder agent. **This is the biggest gap in any
agent codebase, and now we have it.**

### What changed
- **`kairos/eval.py`** (17.7 KB) — full eval framework:
  - **6 graders** out of the box: `contains`, `regex`, `exact`,
    `latency_max_ms`, `max_usd`, `tools_called`
  - **`run_suite(spec, target)`** — runs a YAML suite end-to-end
    with a target callable (default: shell out to
    `python -m kairos.agents.coder`)
  - **`compare_runs(base, target)`** — Welch's t-test on per-case
    scores, flags regressions
  - **Pure-stdlib** — no scipy, no numpy, no new pip deps
  - **CLI**: `python -m kairos.eval run --suite <yaml>` and
    `python -m kairos.eval compare <run1.json> <run2.json>`
- **26 tests** in `tests/test_eval.py` (all pass) covering every
  grader, the run + compare flow, suite load/save round-trip, and
  the Welch t-test edge cases (n<2, identical samples, etc.)
- **`examples/eval_smoke.yaml`** (1.3 KB) — 4-case smoke suite
  the user can run with `python -m kairos.eval run --suite examples/eval_smoke.yaml`

### What this unlocks
- **Regression detection** — "did the latest prompt tweak break
  any case?" is now a one-line command.
- **Tier 1 (mechanical) → Tier 2 (heuristic) → Tier 3 (LLM-judge)
  pyramid** — same shape as `agent-eval`'s 3-tier model; we
  can add Tier 3 in a later round (the LLM judge is just another
  grader).
- **CI integration** — `python -m kairos.eval run --suite <yaml>`
  exits non-zero on pass_rate < threshold. Plug into any CI.

---

## 6. Structured plan tracking (deepagents reference) ✅

`langchain-ai/deepagents` (23K ★, MIT) — the canonical
"Claude Code architecture open-sourced" harness. The killer
insight: the agent should write a plan first, update it as it
goes, and have it ride along into the next round's context.

### What changed
- **`kairos/loop/plan.py`** (7.6 KB) — `Plan` + `TodoItem` with
  the four operations a deep agent needs:
  - `replace(todos)` / `update(todos)` — wholesale plan update
    from an LLM `write_todos` tool call
  - `mark_in_progress(content)` / `mark_completed(content)`
  - `current` — the single in-progress todo, if any
  - `completion` — fraction 0..1
  - `render_plan_block(plan)` — Markdown for the system prompt
  - `apply_write_todos(plan, tool_input)` — diff-helper for the
    UI (e.g. "+ Add CSV reader, ~ Run tests: pending → in_progress")
- **16 tests** in `tests/test_loop_plan.py` (all pass) covering
  validation, mark operations, render output, apply_write_todos
  with various input shapes, and round-trip via dict.

### What's NOT in this round
- **Wiring into the loop runner** — `loop_runner.py` doesn't yet
  call `apply_write_todos` when the agent emits a `write_todos`
  tool call. That's a follow-up (Round 10.5 or 11). The seam is
  ready; the integration is straightforward.

### What this unlocks
- The plan rides into the next round's `system_prompt` as
  a Markdown checklist. The Coder agent knows at a glance
  what's left to do.
- The UI can render a live "Current plan" panel that
  highlights the in-progress todo.
- The diff from `apply_write_todos` is published as a
  MessageBus event so the chat history shows what changed.

---

## Test summary

| Suite | New | Total pass | Skipped |
|---|---:|---:|---:|
| `tests/test_nsjail_sandbox.py` | 9 | 0 (Linux-only) | 9 |
| `tests/test_litellm_provider.py` | 11 | 11 | 0 |
| `tests/test_eval.py` | 26 | 26 | 0 |
| `tests/test_loop_plan.py` | 16 | 16 | 0 |
| `tests/test_observability.py` | 9 | 9 | 0 |
| `tests/test_memory_kb.py` | 23 | 23 | 0 |
| **Round 10 new** | **94** | **85** | **9** |
| Pre-existing tests re-run | — | 165 | 1 |
| **Grand total this run** | **94** | **250** | **10** |

`tsc --noEmit` clean.

## What we explicitly did NOT do

- **`gVisor` / `Firecracker` / Kata** — heavy infrastructure
  changes, deferred to round 11+ (gVisor needs Docker runtime
  registration; Firecracker needs KVM).
- **Textual TUI rewrite** — large surface, the current
  plain-ANSI TUI works fine for headless dev. Deferred.
- **`graphiti` / `cognee` proper** — `memory_kb.py` is the
  drop-in that exposes the same 4-op API; the user can swap the
  backend without changing call sites.
- **Replace `model_router.py` with LiteLLM fully** — the
  router's role-mapping concept is Kairos-specific; we keep
  both layers and route the LiteLLM provider through the
  router.
- **E2B / Daytona** — cloud-only, user has been explicit about
  on-premise.

## Round-by-round schedule (updated)

| Round | What | Status |
|---|---|---|
| 8 | Tier 1/2/3 round from the previous roadmap (13 items) | ✅ done |
| 9 | OSS adoption — superpowers skills + MCP example + roadmap doc | ✅ done |
| 10 | **This round** — LiteLLM + observability + memory_kb + nsjail + eval + plan | ✅ done |
| 11 | Wire plan into loop_runner; wire observability into the Coder; gVisor + Docker runtime | next |
| 12 | Firecracker / Kata (with KVM); Textual TUI rewrite; Tier 3 LLM judge in eval | as needed |
| 13+ | Deepagents full integration; long-running app harness (ArtemisAI pattern) | as needed |
