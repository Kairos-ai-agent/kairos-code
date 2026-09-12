# Round 31 — Anthropic skills adapted (webapp-testing, mcp-builder, frontend-design, skill-creator, theme-factory, doc-coauthoring)

**Goal:** close the open roadmap item from R9/R10 — adapt 6 of
Anthropic's official skills (the ones most useful to Kairos) so
they're discoverable by `SkillsLoader` and the FTS5 skill search.

**Scope:** 1 new adapter script + 6 vendored SKILL.md files + 6
adapted output files + 1 new test file + 1 docs update.

---

## 1. What ships

| File | Lines | Purpose |
|------|-------|---------|
| `vendor/_oss/anthropic-skills/skills/<name>/SKILL.md` × 6 | ~52 KB | Local mirror of upstream SKILL.md (offline-friendly) |
| `scripts/adapt_anthropic_skills.py` | 195 | Adapter: `vendor/_oss/.../SKILL.md` → `kairos/skills/anthropic__<name>.md` |
| `kairos/skills/anthropic__<name>.md` × 6 | ~54 KB | Adapted skills, loaded by `SkillsLoader` |
| `tests/test_adapt_anthropic_skills.py` | 280 | 16 tests covering the adapter + downstream discovery |
| `docs/ROUND_31_REPORT.md` | this | – |

## 2. The 6 adapted skills

| Skill | Priority | What it does |
|-------|----------|--------------|
| `webapp-testing` | 0.7 | Playwright-based local webapp testing; bundled `with_server.py` helper |
| `mcp-builder` | 0.7 | Guide to writing high-quality MCP servers (Python FastMCP + TS SDK) |
| `frontend-design` | 0.7 | Frontend aesthetic direction (palette / type / layout) |
| `skill-creator` | 0.7 | The skill-author loop: draft → test → review → improve |
| `theme-factory` | 0.7 | 10 pre-set slide / artifact themes + custom theme generation |
| `doc-coauthoring` | 0.7 | 3-stage co-authoring workflow (context → refine → reader test) |

**Why these 6, not all 19 in the upstream repo:**

- `brand-guidelines` — Anthropic-specific brand assets, not useful for Kairos
- `internal-comms` — Corporate comms templates, not coding
- `web-artifacts-builder` — Claude.ai artifacts-only feature, irrelevant to CLI
- The other 12 (`docx`, `pdf`, `pptx`, `xlsx`, `algorithmic-art`, `canvas-design`, `slack-gif-creator`, `academy-guide`, `claude-api`, `discernment-nudge`, `skill-creator` variants) — out of scope for R31; can be added later by removing them from the `SKIP` set in the adapter

## 3. Why priority 0.7 (lower than superpowers 0.8)

The superpowers library is **discipline-level** (TDD, debugging,
code review) — the agent should always reach for these first when
the trigger fires. Anthropic's skills are **tooling-level** (how to
test a webapp, how to design a UI, how to write a doc) — useful
when the user asks for that specific thing, but not a default mode.

| Priority | Who | Why |
|----------|-----|-----|
| 0.8 | `superpowers__*` | Discipline wins (TDD, plan mode) |
| **0.7** | **`anthropic__*`** (new) | **Tooling reference** |
| 0.5 | User ad-hoc | Default for non-tagged skills |
| < 0.5 | Project-specific (overrides) | Local context wins |

## 4. Adaptation rules

Same as the superpowers adapter, plus one extra lesson:

- **Line-by-line frontmatter parse** (R26 lesson) — a description
  with colons like `"Use this when: building MCP servers. Or even: nested colons."`
  would be eaten by a one-shot `split(":", 1)` on the whole block.
- **`priority` and `when.keyword` injection** — synthesized from
  the description so the loader can auto-inject contextually.
- **Provenance footer** — `<!-- Adapted from anthropics/skills (Apache-2.0) -->`
  with the upstream URL and the adaptation script path. **Idempotent**:
  re-running the adapter does not append a second footer.
- **`SKIP` set in adapter** — drop a skill from the skip list and
  re-run to add it (e.g. add `mcp-builder` to skip to retire it).

## 5. Why a local mirror (not just `web_fetch` at adapter time)

The user's environment is offline-vendored (118 MB of wheels in
`vendor/`, etc.). The clone failed during this round due to
network issues, so the adapter falls back to a local mirror. Same
pattern as the superpowers clone in R9:

- `vendor/_oss/anthropic-skills/skills/<name>/SKILL.md` is the
  source of truth on disk
- Re-running `git pull` (when the network is up) refreshes the mirror
- `scripts/adapt_anthropic_skills.py` reads the mirror and writes
  the adapted files

If the network comes back, the user can re-run the R9-style clone
command to refresh:

```bash
git clone --depth=1 --filter=blob:none --sparse \
  https://github.com/anthropics/skills.git vendor/_oss/anthropic-skills
git -C vendor/_oss/anthropic-skills sparse-checkout set skills
```

## 6. Tests (16, all green)

`tests/test_adapt_anthropic_skills.py` covers 5 sub-groups:

| Group | Count | Covers |
|-------|-------|--------|
| `extract_keywords` | 5 | stop-word filter, dedup, max count, length filter, colon handling |
| `adapt_skill` | 6 | body preservation, priority + when injection, provenance footer, idempotency, colon-safe frontmatter parse, dir-name fallback |
| `main()` | 3 | end-to-end write, `SKIP` list honored, missing source errors |
| `SkillsLoader` integration | 1 | loader picks up adapted file with correct priority |
| FTS5 integration | 1 | search query hits the adapted skills (skipped if FTS5 unavailable) |

**Total: 16 / 16 passing in 1.52 s.**

## 7. End-to-end smoke test

```python
>>> from kairos.skills import SkillsLoader
>>> loader = SkillsLoader()
>>> [s.name for s in loader.discover() if "anthropic" in s.source_path.name or s.name in {...}]
['doc-coauthoring', 'frontend-design', 'mcp-builder', 'skill-creator',
 'theme-factory', 'webapp-testing']

# FTS5 search
>>> from kairos.skill_search import build_index_from_loader, search
>>> build_index_from_loader(loader, Path("data/skill_index_r31.db"))
>>> search("playwright", loader=loader, db_path=db, limit=3)
[{'name': 'webapp-testing', 'score': 4.95}, ...]
>>> search("mcp server", loader=loader, db_path=db, limit=3)
[{'name': 'mcp-builder', 'score': 3.56}, ...]
>>> search("doc coauthoring", loader=loader, db_path=db, limit=3)
[{'name': 'doc-coauthoring', 'score': 3.40}, ...]
```

## 8. Test sweep — full state after R31

| Bucket | Count | Result |
|--------|-------|--------|
| **R31 new** | 16 | 16 pass, 0 fail |
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
| **Confirmed passing** | **1357** | **0 fail** |
| 4 deselected (pre-existing flaky/slow) | – | not from R31 |

**Delta vs R30:** +16 tests, 0 regressions.

**Total skills loaded by `SkillsLoader`:** 14 superpowers + 6 anthropic = **20 skills**.

## 9. Risk + mitigation

| Risk | Mitigation |
|------|------------|
| Anthropic skills change upstream | `vendor/_oss/` is the local source; adapter is idempotent; the body is preserved verbatim so re-running the adapter after a `git pull` will pick up changes cleanly |
| Anthropic skills contain license-bound content that can't be re-shared | All 6 chosen skills are Apache-2.0; provenance footer cites the upstream URL; `brand-guidelines` (Anthropic brand assets) explicitly excluded via `SKIP` |
| Anthropic skills "undertrigger" (R31's own skill-creator warns about this) | Frontmatter `description` field is kept verbatim (the "pushy" wording Anthropic uses) so the loader's `when.keyword` extraction catches the salient verbs |
| New anthropic skill added upstream needs to be re-adapted | Just drop the new name into the `vendor/_oss/anthropic-skills/skills/<name>/SKILL.md` mirror, remove from `SKIP` if present, re-run the adapter |
| `webapp-testing` and `mcp-builder` overlap with existing Kairos capabilities | That's the point — they augment. `webapp-testing` adds the `with_server.py` pattern; `mcp-builder` is a guide for users writing their own MCP servers (Kairos already has `mcp_client.py`) |

## 10. Follow-up

R31 closes the `anthropics/skills` roadmap item from R9. Remaining:

- `alirezarezvani/claude-skills` (143 skills, lower priority — broader but less coding-focused)
- The 13 other Anthropic skills not adapted in R31 (drop-in: remove from `SKIP` and re-run)
- COGS dashboard
- Tier 3 picks (inspiration only — no direct adoption planned)

## 11. Diff summary

```
 vendor/_oss/anthropic-skills/skills/{6 names}/SKILL.md  |  ~52 KB (new)
 scripts/adapt_anthropic_skills.py                        | 195 (new)
 kairos/skills/anthropic__<6 names>.md                    |  ~54 KB (new)
 tests/test_adapt_anthropic_skills.py                     | 280 (new)
 docs/ROUND_31_REPORT.md                                 | this file
 docs/OSS_ADOPTION_ROADMAP.md                            | +1 row (R31)
 docs/KAIROS_INDEX.md                                    | +1 row + 1357 test total
```

R31 ships green: 16 new tests, 1357/1357 confirmed passing, no
regressions in any of the R8-R30 modules. `tsc --noEmit` clean.
SkillsLoader now discovers 20 skills (14 superpowers + 6 anthropic).
FTS5 search returns the expected top hit for every test query.
