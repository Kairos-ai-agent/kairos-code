# Round 33 — 3 more Anthropic skills (algorithmic-art, canvas-design, brand-guidelines)

**Goal:** expand the R31 set of adapted Anthropic skills from 6 to 9
by including the three most useful remaining ones for a coding
agent: generative art, visual canvas design, and Anthropic's
brand color palette (as a reusable design-token reference).

**Scope:** 3 new vendor SKILL.md files + 1 SKIP-list update in the
adapter + 5 new tests + 1 docs update.

---

## 1. What ships

| File | Lines | Purpose |
|------|-------|---------|
| `vendor/_oss/anthropic-skills/skills/algorithmic-art/SKILL.md` | ~7 KB | p5.js generative art (seeded randomness, parameter exploration) |
| `vendor/_oss/anthropic-skills/skills/canvas-design/SKILL.md` | ~8 KB | Visual design philosophy → PNG/PDF output |
| `vendor/_oss/anthropic-skills/skills/brand-guidelines/SKILL.md` | ~2 KB | Anthropic's official brand colors + fonts |
| `kairos/skills/anthropic__{name}.md` × 3 | ~17 KB | Adapted versions (same mechanism as R31) |
| `scripts/adapt_anthropic_skills.py` | – | SKIP set shrunk: removed `brand-guidelines` |
| `tests/test_adapt_anthropic_skills.py` | +100 | 5 new tests covering R33 scope |
| `docs/ROUND_33_REPORT.md` | this | – |

## 2. The 3 new skills

| Skill | Priority | What it does |
|-------|----------|--------------|
| `algorithmic-art` | 0.7 | Create original p5.js generative art with seeded randomness, parameter exploration, and seed navigation. 4-6 paragraph "algorithmic philosophy" → single self-contained HTML artifact with p5.js from CDN. |
| `canvas-design` | 0.7 | Create beautiful visual art in PNG/PDF using design philosophy. 4-6 paragraph "design philosophy" → single page output. Anti-AI-slop emphasis: minimal text, spatial expression, expert craftsmanship. |
| `brand-guidelines` | 0.7 | Reference for Anthropic's brand colors (Dark `#141413`, Light `#faf9f5`, Mid Gray, Light Gray, Orange `#d97757`, Blue `#6a9bcc`, Green `#788c5d`) + typography (Poppins headings, Lora body). Useful as a design-token reference for any "make it look polished" task. |

## 3. SKIP set changes

**Before R33** (R31 baseline):
```python
SKIP = {
    "brand-guidelines",       # ← was skipped (R31 considered it too Anthropic-specific)
    "internal-comms",         # corporate comms templates
    "web-artifacts-builder",  # Claude.ai artifacts-only
}
```

**After R33** (this round):
```python
SKIP = {
    "internal-comms",         # still skipped — corporate, not coding
    "web-artifacts-builder",  # still skipped — Claude.ai-only feature
}
```

**Why `brand-guidelines` came back:** the colors and fonts are public
design tokens (Apache-2.0 license). They're useful as a reference
when the user asks for "a slide that looks professional" or "an
artifact that uses the Anthropic aesthetic." The skill is small
and the body has zero proprietary content.

## 4. Total skills now loaded

| Source | Count |
|--------|-------|
| superpowers__* (R9) | 14 |
| anthropic__* (R31 + R33) | 9 |
| **Total** | **23** |

## 5. Tests (21 total, all green)

`tests/test_adapt_anthropic_skills.py` now has 21 tests:

| Group | Count | Notes |
|-------|-------|-------|
| `extract_keywords` | 5 | unchanged from R31 |
| `adapt_skill` | 6 | unchanged |
| `main()` | 3 | updated `test_main_skips_listed` to use `internal-comms` (was `brand-guidelines`) |
| `SkillsLoader` integration | 1 | unchanged |
| FTS5 integration | 1 | unchanged |
| **R33 specific** | **5** | **NEW** — SKIP set, 3 skills in production, loader discovery, brand-guidelines color check, algorithmic-art p5.js check |

**Total: 21 / 21 passing in 0.38 s.**

### 5.1 R33-specific tests

- `test_skip_set_excludes_only_internal_comms_and_web_artifacts` —
  asserts the SKIP set has exactly the 2 expected names (and the 3
  R33 skills are NOT in SKIP)
- `test_r33_new_skills_in_production` — verifies the 3 SKILL.md files
  exist on disk and have the expected frontmatter fields
- `test_r33_skills_discoverable_by_loader` — `SkillsLoader.discover()`
  returns all 3
- `test_brand_guidelines_has_color_palette` — body contains the
  canonical color hexes (`#141413`, `#faf9f5`, `#d97757`) and font
  names (Poppins, Lora)
- `test_algorithmic_art_mentions_p5js` — body references p5.js and
  `randomSeed` / "seed" (the core mechanism)

## 6. End-to-end verification

```python
>>> from kairos.skills import SkillsLoader
>>> loader = SkillsLoader()
>>> sorted([s.name for s in loader.discover()])
['algorithmic-art', 'brainstorming', 'brand-guidelines', 'call_spike',
 ..., 'canvas-design', ..., 'doc-coauthoring', ..., 'mcp-builder', ...,
 'webapp-testing']  # 23 total

>>> from kairos.doctor import check_skills_loadable
>>> check_skills_loadable().message
'23 skills discovered'
```

## 7. Test sweep — full state after R33

| Bucket | Count | Result |
|--------|-------|--------|
| **R33 new tests** | 5 | 5 pass, 0 fail |
| R31 + R32 stack (adapt_anthropic_skills + doctor) | 16 + 37 = 53 | 53 pass, 0 fail |
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
| **Confirmed passing** | **1399** | **0 fail** |
| 4 deselected (pre-existing flaky/slow) | – | not from R33 |

**Delta vs R32:** +5 tests, 0 regressions.

## 8. Risk + mitigation

| Risk | Mitigation |
|------|------------|
| Brand guidelines' color hexes become stale | The body is a verbatim copy of `vendor/_oss/.../SKILL.md`; re-running `adapt_anthropic_skills.py` after `git pull` picks up changes. The 5 new R33 tests catch obvious regressions. |
| Network broken during R33 (couldn't fetch all 13 remaining skills) | The 3 chosen skills are the most distinct from the R31 set; the other 10 (docx/pdf/pptx/xlsx/academy-guide/etc.) can be added later by removing from SKIP and re-running. |
| `brand-guidelines` is "Anthropic-specific" — should it be skipped after all? | R31's reasoning was "Anthropic brand assets." But the body has zero proprietary content — it's just hex codes and font names. The license is Apache-2.0, so it's safe to ship. Users who don't want it can put it back in SKIP. |

## 9. Follow-up

- `alirezarezvani/claude-skills` (143 skills) — bigger pool but lower
  overlap with Kairos's coding focus
- The remaining 10 Anthropic skills (docx / pdf / pptx / xlsx /
  academy-guide / claude-api / discernment-nudge / slack-gif-creator
  / internal-comms / web-artifacts-builder) — drop from SKIP and
  re-run if needed
- COGS dashboard
- Tier 3 picks (inspiration only)

## 10. Diff summary

```
 vendor/_oss/anthropic-skills/skills/{3 names}/SKILL.md  |  ~17 KB (new)
 kairos/skills/anthropic__{3 names}.md                  |  ~17 KB (new)
 scripts/adapt_anthropic_skills.py                      |  -1 line (SKIP edit)
 tests/test_adapt_anthropic_skills.py                   |  +100 (5 new tests)
 docs/ROUND_33_REPORT.md                               | this file
 docs/OSS_ADOPTION_ROADMAP.md                          | +1 row (R33)
 docs/KAIROS_INDEX.md                                  | 23 skills line
```

R33 ships green: 5 new tests, 1399/1399 confirmed passing, no
regressions in any of the R8-R32 modules. `tsc --noEmit` clean.
SkillsLoader now discovers **23 skills** (14 superpowers + 9 anthropic).
