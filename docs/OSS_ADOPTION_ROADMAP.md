# Kairos Code — Open-Source Adoption Roadmap

> Status: **Round 36 / 2026-08-27**.
> Tier 1 quick wins are landed (14 bundled superpowers skills, body
> cap raised, example `.mcp.yaml`). Tier 2/3 are scoped but
> unscheduled. This document is the single source of truth for
> "which OSS project do we adopt, and when".

The 2026-08-26 audit of the agent-harness ecosystem found ~200 live
projects that could plausibly strengthen Kairos. After scoring
each on (1) star count, (2) activity, (3) license compatibility,
(4) integration cost, and (5) marginal value to Kairos specifically,
the list below is the **curated set worth considering** — every
entry passed at least one of the cutoffs and was confirmed active
in the last 90 days.

---

## Tier 1 — shipped or in-flight (highest ROI)

| Project | What it does | Where it lands in Kairos | Status |
|---|---|---|---|
| `obra/superpowers` (243K ★) | TDD / systematic debugging / subagent-driven development / code review / plan mode. The most battle-tested skill framework in the open-source agent ecosystem. | `kairos/skills/superpowers__*.md` (14 skills bundled). Loader now reads from `kairos/skills/` automatically; priority 0.8 beats user ad-hoc 0.5. | ✅ **shipped** (this round) |
| `anthropics/skills` (157K ★) | Official Anthropic skills: docx/pdf/pptx document creation, webapp-testing (Playwright), mcp-builder, frontend-design. | Defer to round 10. Same `kairos/skills/` mechanism. Each needs a Kairos frontmatter adapter pass. | 📋 planned |
| `alirezarezvani/claude-skills` (143 skills) | Largest community collection: engineering / data / marketing / HR / business / finance. | Defer to round 10 (lower priority — narrower overlap with Kairos's coding focus). | 📋 planned |
| `upstash/context7` (54K ★) | Live version-pinned library docs. Solves "the LLM is using deprecated API". | `.mcp.example.yaml` lists it as #1 server. | ✅ **shipped** (example yaml) |
| `microsoft/playwright-mcp` (31K ★) | Real Chromium, accessibility-tree targeting. Backbone of `webapp-testing` skill. | `.mcp.example.yaml` #3. | ✅ **shipped** (example yaml) |
| `github/github-mcp-server` (29K ★) | GitHub: issues / PRs / code search. | `.mcp.example.yaml` #2. | ✅ **shipped** (example yaml) |
| `microsoft/markitdown` (119K ★) | PDF/Office/HTML → Markdown. | `.mcp.example.yaml` #4. | ✅ **shipped** (example yaml) |

### What this round (9) actually changed

1. **`scripts/adapt_superpowers_skills.py`** (5.2 KB) — one-shot
   shallow-clone + adapt-and-copy for any Anthropic-format
   SKILL.md → Kairos `*.md` skill. Re-run after `git pull`ing
   upstream.
2. **`kairos/skills/superpowers__*.md`** (14 files, ~140 KB total) —
   adapted superpowers skill library, shipped in the box.
3. **`kairos/skills.py`** — added a third scope (bundled) to
   `SkillsLoader`; bumped `DEFAULT_MAX_BODY_BYTES` from 6 KB to
   16 KB so the longer skills aren't truncated. Project > global
   > bundled override order preserved.
4. **`.mcp.example.yaml`** (5.1 KB) — drop-in starter pack with
   8 curated servers (context7, github, playwright, markitdown,
   filesystem, fetch, sequential-thinking, memory).
5. **`docs/OSS_ADOPTION_ROADMAP.md`** (this file) — the long view.

---

## Tier 2 — strong candidates, 1-2 weeks each

### 2.1 Memory / RAG (replaces `kairos/memory_hierarchy.py`)

| Project | Stars / signal | When to pick | Integration cost |
|---|---|---|---|
| `getzep/graphiti` | Powers Zep (SOTA agent memory, paper ICLR 2026). Bi-temporal graph. | **Default recommendation.** Bi-temporal is the killer feature for "what did we decide about X in March?". | Medium — needs Neo4j (or FalkorDB). |
| `topoteretes/cognee` | 27K ★. 4-op memory (`remember`/`recall`/`forget`/`improve`). Apache 2.0. | Drop-in alternative if we don't want a separate Neo4j. | Low — pip install + REST adapter. |
| `agentmemorylabs/agent-memory` | SpacetimeDB. Temporal GraphRAG. | Best if we want "agent reads month-old conversation in 50ms". | Medium — new DB. |
| `river-ai-lab/graph-memory-mcp` | MCP-native. | **Lowest-cost upgrade** — just add to .mcp.yaml. | Zero code on Kairos side. |

**Recommended path**: Start with `river-ai-lab/graph-memory-mcp`
(round 10) for the lowest-friction win. If the user wants temporal
queries, migrate to `graphiti` (round 11+).

### 2.2 Sandboxing (beyond Landlock)

| Project | Strength | Weakness | When to pick |
|---|---|---|---|
| `google/gVisor` (18K ★) | user-space kernel; runs in containers; OCI-compatible | Linux-only; ~5-15% syscall overhead | Default for production when KVM isn't available |
| `firecracker-microvm/firecracker` (34K ★) | True hardware isolation; <5MB per VM; 125ms boot | Linux + KVM required | When the user has KVM and needs the strongest isolation |
| `google/nsjail` | process-level isolation; near-zero overhead | Linux-only; needs seccomp policy | Quick win for ad-hoc agent commands |
| `anthropic-ai/srt` (Anthropic) | agent-native npm package; works with MCP | Newer / less battle-tested | If we want to mirror Claude Code's default sandbox |

**Recommended path**: Add `nsjail` profile alongside the existing
Landlock policy (round 10). Defer gVisor/Firecracker until the
user asks for enterprise deployment.

### 2.3 Observability / cost

| Project | What it gives |
|---|---|
| `langfuse/langfuse` | LLM call tracing, eval, prompt management. Apache 2.0. |
| `helicone/helicone` | Proxy + cache + cost analytics. |
| `BerriAI/litellm` | 100+ provider gateway with fallback + cache. **Worth adopting as the unified provider router** to replace `kairos/llm/model_router.py`. |

**Recommended path**: `litellm` (round 10) — drop-in replacement
for our `model_router.py` adds provider fallback + caching +
cost tracking in one. Langfuse (round 11) for trace storage.

### 2.4 TUI

| Project | Notes |
|---|---|
| `Textualize/textual` | 30K ★. The obvious upgrade for the current plain-ANSI TUI. Replaces the screenshot-rendering paths in `kairos/tui/`. |
| `charmbracelet/bubbletea` (Go) | Reference for cross-language TUI patterns. Not directly adoptable (different language) but worth reading. |

**Recommended path**: Round 10+ — only if the user starts using
the TUI heavily.

---

## Tier 3 — inspiration only, not direct adoption

These were considered and **rejected for direct integration** but
contain patterns / architectures worth studying:

| Project | What it teaches |
|---|---|
| `HKUDS/OpenHarness` | Smallest readable harness (~5 KLOC Python). Read it before writing any new orchestration code. |
| `langchain-ai/deepagents` | Reference implementation of "batteries-included harness" — planning, filesystem, shell, sub-agents, auto-summarization. |
| `os-factory/har` | Multi-agent parallel + `.har/` machine-readable contract + per-slot worktrees + verification evidence trail. Model for the next generation of Kairos's loop runner. |
| `qjc1997/harness-runner` | Planner + Generator + multi-shift long-running pattern. The cleanest published "long-running app" implementation. |
| `ArtemisAI/Harness_Engineering` | `claude-progress.txt` handoff log + `agents.json` swarm manifest + T1/T2/T3 cost tiers. |
| `mattpocock/skills` | Recently trending. Engineering-grade TDD-style skills. Worth a deep read for the next skills drop. |
| `waveloom` (Go), `agentty` (C++26) | Reference for native-binary, no-runtime-deps TUI coding agents. |
| `volcengine/OpenViking` (26K ★) | ByteDance's filesystem-paradigm context DB. Strong alternative to the manual "load all skills into context" pattern. |
| `Tencent/TencentDB-Agent-Memory` | 4-tier progressive memory. Pattern source for a future `memory_hierarchy` upgrade. |
| `garrytan/gstack` (118K ★) | YC CEO's "23 tools for office-in-a-box". Heavy, opinionated; the `office-hours` and `plan-ceo-review` skills are great, but the org-chart role-play isn't for Kairos. |
| `ArtemisAI/Harness_Engineering`, `everything-claude-code` (140K ★) | Source for the meta-harness pattern — agents that optimize their own prompts/tools. |
| `dvalin/dvalincode` (Go) | Provider-neutral, local-first, hash-chained audit trail, per-request network-egress check. Reference for the next gen of Kairos's `output_guardrail.py`. |
| `san` (Go) | "Runs Claude Code skills/plugins/MCP unmodified". If we ever ship a Go-side server, this is the playbook. |

---

## What we explicitly did **not** adopt

- **`Minecraft / game engine harnesses`** — out of scope (Kairos is
  for code, not game content).
- **Cloud-only agent platforms** (`e2b`, `daytona`, `modal`,
  `northflank`) — the user runs Kairos on-premise per the system
  constraints. Worth a one-page `docs/CLOUD_ALTERNATIVES.md` if
  the user ever asks, but no integration.
- **Jailbreak / anti-cheat-bypass projects** — explicitly refused
  per the persistent safety policy.
- **`LangChain` itself** — Kairos has its own agent loop. The
  patterns inside `langchain-ai/deepagents` are borrow-worthy but
  the framework is too heavyweight for the use case.
- **OpenAI `openai-agents-python`** — wrong project. The real
  OpenAI harness is `openai/codex` (Rust); we already mirror that.
- **Claw Code / `claw-code-agent`** — Python-only Claude Code
  rewrite spawned by the March 2026 source leak. Chaotic and
  largely agent-maintained; details vary across sources. Not safe
  to depend on.

---

## Round-by-round integration schedule

| Round | What | Status |
|---|---|---|
| 9 | superpowers skills bundled, body cap raised, .mcp.example.yaml, this roadmap doc | ✅ done |
| 10 | `anthropics/skills` + `alirezarezvani/claude-skills` adapted; `litellm` + observability + memory_kb + nsjail + eval + plan | ✅ done |
| 11 | Wiring round — write_todos→Plan, tracer into Coder, gVisor tier, LLM judge | ✅ done |
| 12 | Plan in system_prompt + history + git; eval-in-CI template; session importer | ✅ done |
| 13 | Dataset record/replay + git-log derive; PlanPanel UI; meta-eval | ✅ done |
| 14 | Plan history UI; CI auto-derive; auto-record on success; cost tracking (litellm native) | ✅ done |
| 15 | Firecracker tier; Cognee-style 4-op memory adapter | ✅ done |
| 16 | Cost dashboard API + UI panel | ✅ done |
| 17 | Full-text skill search (SQLite FTS5) | ✅ done |
| 18 | Textual TUI (focused slice: skills + cost + log) | ✅ done |
| 19 | EvalPanel UI + Datasets API (record/replay/derive from web) | ✅ done |
| 20 | Per-case LLM judge CLI verified + example suite | ✅ done |
| 21 | Pre-commit hook runner (kairos.hook) | ✅ done |
| 22 | Cost regression alerts (engine + webhook dispatcher) | ✅ done |
| 23 | Frontend skill search UI (Ctrl+K palette) | ✅ done |
| 24 | Multi-run trend aggregator | ✅ done |
| 25 | Trend API + UI panel (Overview + Per-case) | ✅ done |
| 26 | Per-case flaky detection (R24+R25 follow-up) | ✅ done |
| 27 | Windows pre-commit compatibility check | ✅ done |
| 28 | Real Slack integration + alert history (`kairos.alerts_dispatcher`) | ✅ done |
| 29 | Long-running app harness — `.har/` contract + resume runtime (`kairos.har`) | ✅ done |
| 30 | Alert UI panel — API + React + server-side mutes (`api/routes/alerts.py`, `AlertPanel.tsx`) | ✅ done |
| 31 | Anthropic skills adapted (webapp-testing / mcp-builder / frontend-design / skill-creator / theme-factory / doc-coauthoring) | ✅ done |
| 32 | `kairos doctor` — 14-check self-diagnostic CLI (settings / data / LLM / skills / FTS5 / MCP) | ✅ done |
| 33 | 3 more Anthropic skills adapted (algorithmic-art / canvas-design / brand-guidelines) — 9 total | ✅ done |
| 34 | First community skills (alirezarezvani/claude-skills) — 3 engineering: senior-architect / tdd-guide / code-reviewer | ✅ done |
| 35 | COGS value metrics endpoint (`GET /api/cost/value`) — cost / value ratios for the dashboard | ✅ done |
| 36 | CogsPanel.tsx — R35 metrics in the web UI (Today page) | ✅ done |
| 37+ | More community skills; per-model COGS; trend over time; Tier 3 picks | as needed |

---

## Provenance & licensing

All Tier 1 projects are MIT or Apache 2.0. The superpowers skill
files we ship include a "Adapted from obra/superpowers"
provenance footer (see `scripts/adapt_superpowers_skills.py`).
The adaptation only re-formats frontmatter and adds a
`when:` block heuristic — the body content is unchanged
upstream. If we ever change the body, we must either ship the
modification under the same license OR re-implement the
technique from scratch.

## How to update this list

1. Spot a new candidate via `bradAGI/awesome-cli-coding-agents`
   or `yunwei37/awesome-harness-engineering` (both auto-curated).
2. Add it to the table above with score on the 5 axes.
3. Re-run `scripts/adapt_superpowers_skills.py` if it's a
   new skill source (after shallow-cloning into
   `vendor/_oss/<repo>/skills/`).
4. Add to `.mcp.example.yaml` if it's a server we'd recommend
   to most users.
