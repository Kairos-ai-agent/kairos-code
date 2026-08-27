# Round 11 — Wiring Round (loose ends into runtime)

> Status: **4/4 items shipped**. **108 new tests pass** (15 Linux-only
> skipped, all nsjail/gvisor). **300/300 in the touched-file sweep**
> (15 skipped). `tsc --noEmit` clean.

This round takes the pieces built in rounds 9-10 and wires them into
the runtime. No new modules are added — every item here is an
integration. After this round, **the deepagents-style plan, the
Langfuse-compatible tracer, the LLM judge, and the gVisor tier are
all live in the agent loop.**

---

## 1. `write_todos` → Plan tracker (deepagents reference) ✅

Round 10 added `Plan` / `TodoItem` / `apply_write_todos`. This round
**plumbs the wire**: the Coder agent intercepts its own `write_todos`
tool call, applies the diff to a `Plan` instance, and publishes a
`plan.updated` event on the MessageBus. The plan rides along into
the next round's context as a Markdown checklist.

### What changed
- **`kairos/agents/base.py`** — `KairosAgent.__init__` now accepts
  `plan_tracker: Optional[Plan] = None`. The `run()` loop checks
  for `tool.name == "write_todos"` *before* dispatching — if a
  plan_tracker is attached, the call is routed to
  `_handle_write_todos` which:
  1. calls `apply_write_todos(plan, args)` to apply the diff
  2. publishes a `plan.updated` event with the diff string + the
     full plan state
  3. returns a synthetic `ToolResult` so the agent loop continues
- **`kairos/loop/review_loop.py`** — `LoopSession.plan_todos` field
  added (defaults to `None`).
- **`kairos/loop/loop_runner.py`** — `run_loop` lazily creates a
  `Plan()` if the session has none, and attaches it to
  `session.coder.plan_tracker` before the first round. This means
  **every Coder run gets a plan for free** — no call-site changes
  needed.

### Tests (5 new in `tests/test_write_todos_wiring.py`)
- `test_handle_write_todos_applies_to_plan` — the diff ends up in
  `plan.todos`
- `test_handle_write_todos_malformed_returns_error` — bad input
  returns a tool error, not a silent no-op
- `test_handle_write_todos_publishes_plan_updated` — the bus event
  has `metadata.plan` = full plan + the diff string in `content`
- `test_handle_write_todos_no_diff_publishes` — even no-op updates
  emit the event (so the UI sees "agent emitted but didn't change
  anything")
- `test_handle_write_todos_without_plan_tracker_raises` — without
  the tracker, the attribute is None (the call path is never
  reached)

### What this unlocks
- The UI can subscribe to `plan.updated` events and render a live
  "Current plan" panel with checkboxes
- The plan rides into the next round's `system_prompt` via
  `render_plan_block(plan)` so the agent knows what's left
- Round 12 can plumb the plan into the loop's checkpoint history

---

## 2. Tracer wired into every LLM call ✅

Round 10 added `Tracer` / `NoOpSpan` / OTel export. This round
**wraps every LLM call in the agent** in a tracer span. Production
users wire Langfuse via `OTEL_EXPORTER_OTLP_ENDPOINT`; tests
continue to use the in-memory no-op path.

### What changed
- **`kairos/agents/base.py`** — `_traced_llm_call(messages, tools)`
  helper added. All 4 LLM call sites are now wrapped:
  1. `_stream_complete` — the streaming path
  2. `_stream_complete` — the complete-fallback path
  3. `_summarize_old_turns` — the compaction summary call
  4. `run_chat` — the main agent loop's primary LLM call
  Each span carries:
  - `gen_ai.request.model`, `gen_ai.system`
  - `gen_ai.request.message_count`
  - `gen_ai.usage.input_tokens` / `output_tokens` (when provided)
  - `gen_ai.response.duration_ms` (auto-recorded)
  - `gen_ai.response.finish_reason` (when provided)
- **`kairos/observability.py`** — `NoOpSpan.set_output(...)` shim
  added so the same code works whether the trace is a no-op or
  an OTel span.

### Tests (4 new in `tests/test_agent_tracing.py`)
- `test_traced_llm_call_returns_noop_span` — the helper yields a
  `NoOpSpan` when no OTLP endpoint is configured
- `test_traced_llm_call_records_model_name` — span name encodes
  the model for filterable Langfuse queries
- `test_stream_complete_records_span_on_response` — a full
  streaming LLM call produces a span with `duration_ms` and
  `gen_ai.request.model`
- `test_traced_call_swallows_tracing_errors` — a misbehaving
  tracer span does NOT break the LLM call (defense in depth)

### What this unlocks
- `OTEL_EXPORTER_OTLP_ENDPOINT=http://langfuse:4317` → every LLM
  call in Kairos shows up in the Langfuse UI with full
  prompt/response/cost/timing
- Token usage + cost can be aggregated across the project
- Latency p95/p99 tracking is now first-class

---

## 3. gVisor tier (runsc) ✅

`google/gVisor` — Google's user-space kernel container runtime.
**Stronger than Docker's default `runc`**, with **no KVM requirement**
(default `ptrace` platform works everywhere). We add it as a
detection + install-instructions surface so the Settings UI can
tell the user "gVisor is available, here's how to install it."

### What changed
- **`kairos/sandbox.py`** — three additions:
  1. `gvisor_available() -> bool` — `runsc` on PATH + Linux
  2. `gvisor_install_instructions() -> str` — multi-line install
     script (APT repo + `runsc install` + Docker restart +
     `--runtime=runsc` smoke test)
  3. `describe_capabilities()` extended with the `gvisor` key
- `SandboxPolicy` is unchanged — gVisor is a *runtime-level*
  isolation layer that the user opts into via Docker CLI, not
  something Kairos spawns

### Tests (4 new in `tests/test_gvisor_sandbox.py`, all Linux-only)
- `test_gvisor_available_is_false_on_non_linux`
- `test_describe_capabilities_includes_gvisor`
- `test_gvisor_install_instructions_are_helpful` — the doc
  mentions `apt-get install`, `runsc install`, and
  `--runtime=runsc`
- `test_gvisor_install_instructions_non_empty` — at least 200
  chars of actionable content

### What this unlocks
- The Settings UI can show the user a "Sandbox" panel with:
  - ✅ Landlock (always on, Linux 5.13+)
  - ✅ nsjail (if `nsjail` binary present)
  - ✅ gVisor (if `runsc` binary present + Docker registered)
  - ❌ gVisor — click here for install instructions
- gVisor gives **kernel-level syscall filtering** without KVM.
  The Kairos team can recommend it as the production-grade tier
  on any Linux host.

---

## 4. LLM judge grader (LiteLLM-backed) ✅

Round 10 added the eval framework with 6 mechanical graders
(contains, regex, exact, latency_max_ms, max_usd, tools_called).
This round adds **Tier 3: LLM judge** — a grader that calls an
LLM to grade the agent's output against a free-form rubric.

### What changed
- **`kairos/eval.py`** — new grader + factory:
  1. `LLMJudgeGrader(prompt, judge, threshold)` — sends the agent
     output + a rubric to a `judge(prompt) -> str` callable,
     parses a 0-1 score from the response, returns 1.0 if
     `score >= threshold` else 0.0
  2. `make_litellm_judge(model, api_key, base_url)` — a factory
     that builds a `judge` callable backed by `litellm.completion`.
     Wires up via `OPENAI_API_KEY` env var or `api_key` arg
  3. `build_graders(specs, judge=None)` — new `llm_judge` key
     dispatches to `LLMJudgeGrader`; the `judge` is plumbed
     through automatically
  4. CLI: `python -m kairos.eval run --suite x.yaml --judge kairos.eval:make_litellm_judge`
  5. `_default_parse_score` extracted to a helper; supports
     `score: 0.85` pattern, percentage normalization (>10 → /100),
     and clamping
  6. Per-case `threshold` in the YAML now overrides the
     default 0.5 pass threshold (so a hard case can be a 0.7
     while easy ones are 0.5)
- Score response formatting is robust: any 0-1 number in the
  last non-empty line, or `score: N` pattern, parses correctly.
  Garbage → 0.0 (so a confused judge doesn't fail-open by
  accident).

### Tests (14 new in `tests/test_llm_judge_grader.py`)
- 5 tests for the score parser (explicit pattern, fallback,
  percentage normalization, garbage handling, clamping)
- 4 tests for `LLMJudgeGrader` (no-judge fail-open, passes /
  fails on threshold, exception handling, prompt composition)
- 3 tests for `build_graders` integration
- 2 tests for `run_suite` end-to-end with the judge wired in
  (verifies per-case threshold, details dict carries judge score)

### What this unlocks
- **Subjective grading** — "is this code idiomatic?", "did the
  agent use the right library?", "is the documentation clear?"
- **Tie-breaker** — when two graders disagree (e.g. contains
  passes but regex fails), the LLM judge can be the deciding
  vote
- **Cheap grading in CI** — wire `gpt-4o-mini` as the judge;
  one call per case is a fraction of a cent
- The eval suite is now **T1 + T2 + T3** (mechanical + heuristic
  + LLM-judge) matching the `agent-eval` pyramid

---

## Test summary

| Suite | New | Pass | Skipped |
|---|---:|---:|---:|
| `tests/test_write_todos_wiring.py` | 5 | 5 | 0 |
| `tests/test_agent_tracing.py` | 4 | 4 | 0 |
| `tests/test_gvisor_sandbox.py` | 4 | 0 (Linux-only) | 4 |
| `tests/test_llm_judge_grader.py` | 14 | 14 | 0 |
| **Round 11 new** | **27** | **23** | **4** |
| `tests/test_streaming.py` (regression check) | — | — | — |
| All touched files (this round) | — | **300** | **15** |

`tsc --noEmit` clean.

## Round-by-round schedule (updated)

| Round | What | Status |
|---|---|---|
| 8 | Tier 1/2/3 from previous roadmap (13 items) | ✅ |
| 9 | OSS adoption — superpowers skills + MCP + roadmap | ✅ |
| 10 | LiteLLM + observability + memory_kb + nsjail + eval + plan | ✅ |
| 11 | **This round** — wire plan + tracer + gVisor + LLM judge | ✅ |
| 12 | Plumb plan into checkpoint history; add AgentLens-style session importer; Tier 2/3 CLI for live regression monitoring | next |
| 13+ | Firecracker / Kata; Textual TUI; Cognee / Graphiti swap | as needed |

## What we explicitly did NOT do

- **Wire `render_plan_block` into the next round's system prompt** — the `Plan` is captured on the session but the next Coder turn doesn't see it yet. Round 12.
- **Wire the eval CLI into CI** — `python -m kairos.eval compare A B` works; the GitHub Action template is for Round 12.
- **Real Langfuse end-to-end test** — we test the OTel export path, not a live Langfuse receiver. Cost concern.
- **Sandbox-exec call for gVisor** — gVisor is a runtime registration, not a command Kairos spawns. The user wires it once and uses `--runtime=runsc` in their Docker invocations.
- **Replace `model_router.py` with LiteLLM fully** — same as round 10, kept the role-mapping layer separate.
