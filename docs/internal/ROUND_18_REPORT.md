# Round 18 — Textual TUI (focused slice)

> Status: **1 module shipped (focused slice)**. **17 new tests
> pass**. `tsc --noEmit` clean.

This round adds a terminal UI for the headless / SSH
workflow. It's a focused slice (3 panels + 3 testable renderers),
not a full rewrite — the existing `kairos/tui/` legacy module
stays for backward compatibility.

---

## 1. `kairos.tui_textual` — Textual-based dashboard ✅

A read-only three-pane dashboard:
- **Skills** (top-left): list of discovered skills with
  priority, body preview, and a live filter input
- **Cost** (top-right): per-model spend with totals
- **Recent log** (bottom): tail of `cost.jsonl` as a scrollable
  log (capped at 30 lines)

Keybindings: `q` quit · `r` refresh · `/` focus the search
input.

### What changed
- **`kairos/tui_textual.py`** (9.7 KB) — new module (renamed
  from `tui.py` to avoid conflict with the existing
  `kairos/tui/` package). The Textual import is lazy; if
  Textual isn't installed, the entrypoint prints a
  helpful error and exits 1.
- **3 testable renderers** (`_render_cost_table`,
  `_render_skills_table`, `_render_recent_log`) — the
  Textual app delegates to these so the rendering logic
  is unit-testable in any environment.
- **`_load_cost_log(path)`** — JSONL tail reader with corrupt
  line skipping.

### What this unlocks
- SSH-friendly monitoring without a browser
- A renderable log of recent LLM calls for debugging
- A skills browser that's grep-friendly from the terminal

### Tests (17 in `tests/test_tui.py`)
- 4 cost log loader tests (newest first, missing file,
  corrupt lines, env var)
- 3 cost table renderer tests (empty, aggregation, sort)
- 5 skills table renderer tests (empty, list, filter,
  no match, 20-cap)
- 2 recent log renderer tests (empty, 30-cap)
- 2 main entrypoint tests (no-textual path, _HAS_TEXTUAL flag)

---

## Test summary

| Suite | New | Pass | Skipped |
|---|---:|---:|---:|
| `tests/test_tui.py` (17) | 17 | 17 | 0 |
| **`Round 18 new`** | **17** | **17** | **0** |
| All touched files (this round) | — | **419** | **19** |

`tsc --noEmit` clean.
