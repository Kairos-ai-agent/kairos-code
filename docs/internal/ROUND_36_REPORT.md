# Round 36 — CogsPanel (R35 metrics in the web UI)

**Goal:** close the COGS story end-to-end. R35 added the metrics
endpoint; R36 puts them on the Today page so the user can see
"how much value am I getting per dollar" without running a CLI.

**Scope:** 1 new React component + 1 mount + 1 new test file + 1
docs update. No backend changes (reuses the R35 endpoint).

---

## 1. What ships

| File | Lines | Purpose |
|------|-------|---------|
| `web/src/components/CogsPanel.tsx` | 240 | Cost / Value (COGS) panel with 3 totals + 5 derived metrics |
| `web/src/pages/Today.tsx` | +3 | Mount `<CogsPanel />` below the AlertPanel |
| `web/src/test/cogsPanel.test.tsx` | 195 | 8 vitest tests covering rendering + edge cases |
| `docs/ROUND_36_REPORT.md` | this | – |

## 2. The panel

The CogsPanel renders a single `Card` with two rows:

**Top row — 3 totals (always present):**
- Total spend (USD, precision 4)
- Eval cases (count)
- Alerts fired (with red "N crit" tag if critical > 0)

**Bottom row — 5 derived metrics (None → "—"):**
- `$ / case` (cost per case)
- `$ / passing` (cost per passing case, green)
- `$ / alert` (cost per alert)
- `Efficiency` (passing / total, color-coded: green ≥ 0.8, orange ≥ 0.5, red < 0.5)
- `Approval yield` (1 − critical/total, color-coded: green ≥ 0.8, orange < 0.8)

Plus a monospace subtitle at the bottom:
```
42 LLM calls · 8 pass / 2 fail · 1 critical / 5 total
```

30 s auto-refresh + manual `Refresh` button + `Retry` on error.

## 3. Design choices

- **3 + 5 split** — the 3 totals are the "what" (raw counts); the
  5 metrics are the "so what" (ratios). Putting them in two rows
  makes the difference obvious at a glance.
- **Color coding only on ratios** — the raw totals are neutral;
  the ratios get a "good / OK / bad" hint. The thresholds are
  the same as the R35 backend (efficiency ≥ 0.8 = green).
- **`—` for None** — the backend's `None` renders as an em-dash.
  Distinct from `0.0` (which is a real $0 answer) so the user can
  tell "no data" from "free".
- **Critical badge** — instead of just changing the number color,
  the alerts total also gets a "N crit" tag so the user sees both
  the count and the severity at once.
- **Tooltip on each metric** — every Statistic has a `Tooltip`
  showing the formula, so the user can hover and remember what
  "approval_yield" means.

## 4. Where it's mounted

The panel is mounted on the Today page (`/today`), below the
existing AlertPanel (R30). When the user navigates to the Today
view, they see:

1. The 3 number stats (projects / sessions / avg score) — R10
2. Recent activity list — R10
3. AlertPanel (recent alerts + mute buttons) — R30
4. **CogsPanel (cost / value ratios) — R36** ← new

This puts the cost story immediately below the alert story:
"alerts fired → here's what they cost → here's the value delivered".

## 5. Tests (8, all green)

`web/src/test/cogsPanel.test.tsx` covers 5 sub-groups:

| Group | Count | What |
|-------|-------|------|
| Rendering — totals | 1 | All 3 totals render at expected positions |
| Rendering — derived | 1 | All 5 derived metrics at expected positions |
| Composition | 1 | Subtitle shows "n LLM calls · X pass / Y fail · N critical / M total" |
| Critical badge | 2 | Shown when critical > 0; not shown when critical = 0 |
| Edge cases | 3 | All-null → 5 em-dashes; color coding at 0.9 and 0.3; error state with retry |

**Total: 8 / 8 vitest passing in 0.86 s.**

### 5.1 Notable test design

- **DOM-level inspection** — antd `Statistic` splits the value
  into prefix + value spans, so `screen.getByText("1.2340")`
  doesn't find the text. The test uses
  `document.querySelectorAll('.ant-statistic-content-value')` to
  read each Statistic's numeric value directly, which is more
  robust than text matching.
- **Unmount + remount to re-fetch** — the 30 s auto-refresh
  doesn't fire in vitest, and the component's `useEffect` only
  re-runs when deps change. To test the "color codes change with
  efficiency" path, the test unmounts the first instance and
  mounts a fresh one with a different mock response.

## 6. tsc + sweep status

- `tsc --noEmit` — clean
- vitest — 8 / 8 for the new CogsPanel test
- (Full vitest suite still hangs on `npx vitest run` without args;
  the per-file runs all work, which is what we use for the
  per-round check)

## 7. Test sweep — full state after R36

| Bucket | Count | Result |
|--------|-------|--------|
| **R36 new tests** | 8 (frontend) | 8 pass, 0 fail |
| R35 stack (cost / value) | 11 | 11 pass, 0 fail |
| R34 stack (adapt_community_skills) | 19 | 19 pass, 0 fail |
| R33 stack (adapt_anthropic + 3 skills) | 5 | 5 pass, 0 fail |
| R32 stack (doctor) | 37 | 37 pass, 0 fail |
| R31 stack (adapt_anthropic) | 16 | 16 pass, 0 fail |
| R30 stack (alerts API + UI) | 27 | 27 pass, 0 fail |
| R29 stack (har) | 47 | 47 pass, 0 fail |
| R28 stack (alerts_dispatcher) | 28 | 28 pass, 0 fail |
| R22-R27 stack | 204 | 204 pass, 1 skip |
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
| **Confirmed passing** | **1429 + 8 frontend** | **0 fail** |

**Delta vs R35:** +8 frontend tests, 0 regressions.

## 8. Risk + mitigation

| Risk | Mitigation |
|------|------------|
| COGS numbers could be misleading with sparse data | The backend returns `None` for zero denominators; the UI renders `—` so "N/A" is visually distinct from "0%" |
| Adding a 9th component to Today makes the page busy | The CogsPanel goes below the existing AlertPanel; both are inside a single column layout. If the user wants a "compact" mode later, we can add a toggle. |
| Test brittleness around antd's internal DOM | Tests use `document.querySelectorAll('.ant-statistic-content-value')` to read values directly instead of `getByText` (which breaks when antd splits a value across spans). |

## 9. Follow-up

The COGS story is now complete end-to-end. Remaining roadmap items:

- **More community skills** (drop in more from alirezarezvani's 388)
- **Per-model COGS** — break down `cost_per_passing` by the model
  that produced it
- **Trend over time** — track `cost_per_passing` per eval run, so
  the user can see "efficiency is improving / degrading"
- **Tier 3 picks** (inspiration only — no direct adoption)

## 10. Diff summary

```
 web/src/components/CogsPanel.tsx   | 240 ++++++++ (new)
 web/src/pages/Today.tsx            | +3 (mount)
 web/src/test/cogsPanel.test.tsx    | 195 ++++++ (new)
 docs/ROUND_36_REPORT.md           | this file
 docs/OSS_ADOPTION_ROADMAP.md      | +1 row (R36)
 docs/KAIROS_INDEX.md              | 26 skills + +8 frontend test total
```

R36 ships green: 8 new frontend tests, all 1429 backend tests
still pass, no regressions in any of the R8-R35 modules. `tsc
--noEmit` clean. The COGS story is now visible in the web UI.
