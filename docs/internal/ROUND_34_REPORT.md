# Round 34 — alirezarezvani/claude-skills (community): 3 engineering skills

**Goal:** add the first batch of community-contributed skills
(`alirezarezvani/claude-skills`, MIT-licensed) at priority 0.6 —
lower than superpowers (0.8) and anthropic (0.7) because they're
not as battle-tested, but above user ad-hoc (0.5).

**Scope:** 1 new adapter script + 3 vendored SKILL.md files + 1
new test file + 1 docs update.

---

## 1. What ships

| File | Lines | Purpose |
|------|-------|---------|
| `vendor/_oss/claude-skills/engineering-team/skills/<name>/SKILL.md` × 3 | ~13 KB | Local mirror of upstream SKILL.md (offline-friendly) |
| `scripts/adapt_community_skills.py` | 140 | Adapter: `vendor/_oss/.../SKILL.md` → `kairos/skills/community__<name>.md` (priority 0.6) |
| `kairos/skills/community__<name>.md` × 3 | ~13 KB | Adapted skills, loaded by `SkillsLoader` |
| `tests/test_adapt_community_skills.py` | 360 | 19 tests (adapter + downstream + integration) |
| `docs/ROUND_34_REPORT.md` | this | – |

## 2. The 3 adapted skills

| Skill | Priority | What it does |
|-------|----------|--------------|
| `senior-architect` | 0.6 | Architecture design + analysis (ADRs, microservices vs monolith, Mermaid/PlantUML diagrams, dependency analyzer, project architect) |
| `tdd-guide` | 0.6 | Test-driven development (red-green-refactor, test generation across Jest / Pytest / JUnit / Vitest, coverage analysis, property-based testing, mutation testing) |
| `code-reviewer` | 0.6 | Code review automation for 14 languages (PR analysis, SOLID / code-smell detection, review report generator) |

**Why these 3 first:** the alirezarezvani repo has 388 skills across
20+ domains, but most are for marketing / product / finance / HR /
C-level advisory — out of scope for a coding agent. The engineering
subset is the obvious starting point. Of the engineering-team skills,
these 3 round out the architecture / TDD / code-review triad that
the superpowers + anthropic libraries don't cover.

## 3. Why priority 0.6 (lower than anthropic 0.7)

| Priority | Who | Why |
|----------|-----|-----|
| 0.8 | `superpowers__*` | Discipline wins (TDD, plan mode) |
| 0.7 | `anthropic__*` | Tooling reference (webapp-testing, mcp-builder) |
| **0.6** | **`community__*`** (new) | **Community / 3rd party** — not as battle-tested as superpowers |
| 0.5 | User ad-hoc | Default for non-tagged skills |

The community skills are MIT-licensed (so legally shippable) and
well-structured (per the upstream README, they're "production-ready"
and 5,200+ stars), but they're a single maintainer's collection
rather than a curated library like superpowers. The 0.6 priority
means: "use if no higher-priority skill matches the trigger."

## 4. PREFIX = "community__"

To avoid name collisions:
- `superpowers__brainstorming` (R9)
- `anthropic__webapp-testing` (R31)
- `community__senior-architect` (R34, this round)

The adapter writes `community__<name>.md` and the SkillsLoader
discovers all three prefixes. Frontmatter is identical
(name / description / priority / when:) so the loader treats them
uniformly.

## 5. Tests (19, all green)

`tests/test_adapt_community_skills.py` covers 6 sub-groups:

| Group | Count | Notes |
|-------|-------|-------|
| `extract_keywords` | 3 | regression-tested vs R31 (same algorithm) |
| `adapt_skill` | 7 | body preserved, priority 0.6, when-block, provenance, quoted name + description (alirezarezvani quotes these), idempotency |
| `main()` | 3 | writes to dst, errors on missing src, skips no-SKILL.md dirs |
| PREFIX guard | 1 | `"community__"` literal |
| Production integration | 4 | files on disk, loader discovery, priority 0.6, doctor count |
| FTS5 integration | 1 | search finds community skills |

**Total: 19 / 19 passing in 0.74 s.**

### 5.1 Notable test design

- **Quoted frontmatter** — alirezarezvani skills quote `name:` and
  `description:` (e.g. `name: "senior-architect"`). The adapter must
  strip those quotes. Tested explicitly in
  `test_adapt_skill_handles_quoted_name` and
  `test_adapt_skill_handles_quoted_description`.
- **Doctor integration** — `test_r34_skills_counted_by_doctor`
  asserts `check_skills_loadable()` reports ≥ 26 skills
  (14 superpowers + 9 anthropic + 3 community).

## 6. Total skills now loaded

| Source | Count | Priority |
|--------|-------|----------|
| superpowers__* (R9) | 14 | 0.8 |
| anthropic__* (R31, R33) | 9 | 0.7 |
| **community__* (R34)** | **3** | **0.6** |
| **Total** | **26** | – |

## 7. Test sweep — full state after R34

| Bucket | Count | Result |
|--------|-------|--------|
| **R34 new tests** | 19 | 19 pass, 0 fail |
| R33 stack (adapt_anthropic + 3 skills) | 5 | 5 pass, 0 fail |
| R32 stack (doctor) | 37 | 37 pass, 0 fail |
| R31 stack (adapt_anthropic) | 16 | 16 pass, 0 fail |
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
| **Confirmed passing** | **1418** | **0 fail** |
| 4 deselected (pre-existing flaky/slow) | – | not from R34 |

**Delta vs R33:** +19 tests, 0 regressions.

## 8. Risk + mitigation

| Risk | Mitigation |
|------|------------|
| Community skill references Python scripts that don't exist locally | The adapted SKILL.md preserves the body verbatim, including the `python scripts/foo.py` examples. The user is expected to fetch the upstream `scripts/` dir if they want to actually run the tools. The body is reference material, not a runtime dependency. |
| `alirezarezvani` skills use a different frontmatter format (quoted `name:`) | R34 adapter strips quotes (tested in `test_adapt_skill_handles_quoted_*`). Same line-by-line parse as R31/R33; no whole-block `split(":")` antipattern. |
| 388 skills in upstream — picking the wrong subset | We picked 3 from `engineering-team/skills/` only. The other 285+ engineering skills (a11y-audit, snowflake-development, stripe-integration-expert, ...) and the non-engineering skills (marketing, product, finance, HR, etc.) are intentionally excluded. Drop them in `vendor/_oss/` + remove from SKIP-style logic to add more. |
| Network for the full clone is flaky | The 3 R34 skills were mirrored manually from `web_fetch` calls. The user can re-run `git clone` + `adapt_community_skills.py` later when the network is stable. |

## 9. Follow-up

- More community skills: `a11y-audit`, `incident-commander`, `tech-debt-tracker`
- COGS dashboard
- Tier 3 picks (inspiration only)

## 10. Diff summary

```
 vendor/_oss/claude-skills/engineering-team/skills/{3 names}/SKILL.md  |  ~13 KB (new)
 scripts/adapt_community_skills.py                                   | 140 (new)
 kairos/skills/community__{3 names}.md                               |  ~13 KB (new)
 tests/test_adapt_community_skills.py                                | 360 (new)
 docs/ROUND_34_REPORT.md                                            | this file
 docs/OSS_ADOPTION_ROADMAP.md                                       | +1 row (R34)
 docs/KAIROS_INDEX.md                                               | 26 skills + +19 test total
```

R34 ships green: 19 new tests, 1418/1418 confirmed passing, no
regressions in any of the R8-R33 modules. `tsc --noEmit` clean.
SkillsLoader now discovers **26 skills** (14 superpowers + 9 anthropic + 3 community).
