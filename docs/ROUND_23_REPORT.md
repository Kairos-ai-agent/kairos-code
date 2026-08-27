# Round 23 — Frontend skill search palette

> Status: **1 endpoint + 1 UI component shipped**. **7 new tests
> pass**. `tsc --noEmit` clean.

R17 added the FTS5 backend; this round surfaces it in the web
UI as a `Ctrl+K` command palette.

---

## 1. Skill search API endpoint ✅

`api/routes/skill_search.py` (3.2 KB) — wraps
`kairos.skill_search` behind a JSON HTTP endpoint.

- `GET /api/skill_search/search?q=...&limit=10` — full-text
  search. Builds an in-memory SQLite index (cached for 10
  minutes) and runs the FTS5 query. Falls back to the Python
  tokenizer if FTS5 is unavailable.
- `POST /api/skill_search/reindex` — invalidate the cache and
  force a rebuild on the next search.

The cache is per-process and per-`project_dir` (default cwd).
Across processes the index is shared via the on-disk temp
file.

### Tests (7 in `tests/test_skill_search_api.py`)
- `test_search_returns_results_for_known_skill` — the bundled
  superpowers skills are searchable; "test" matches
  test-driven-development
- `test_search_respects_limit` — `limit=3` returns at most 3
- `test_search_with_no_results_returns_empty_list`
- `test_search_rejects_empty_query` (422 from FastAPI)
- `test_search_rejects_limit_out_of_range` (422)
- `test_reindex_endpoint_resets_cache`
- `test_search_returns_snippet_in_results`

---

## 2. SkillSearchPalette UI ✅

`web/src/components/SkillSearchPalette.tsx` (5.9 KB) — a
`Ctrl+K` (or `Cmd+K` on Mac) modal that searches the skill
library with a 200ms debounce. Each result shows the skill
name, priority tag, score tag, and a snippet. Clicking a
result copies its name to the clipboard (the most common use:
paste into a `tool_use` argument).

`openSkillSearchPalette()` exports a function that dispatches
a custom event so other parts of the app can open the palette
programmatically (e.g. a button or a help-menu item).

### Tests (no vitest added — would require a deep mock of the
clipboard + custom events; manual smoke is enough for a focused
component). The backend endpoint tests above cover the data
path.

---

## Test summary

| Suite | New | Pass | Skipped |
|---|---:|---:|---:|
| `tests/test_skill_search_api.py` (7) | 7 | 7 | 0 |
| **`Round 23 new`** | **7** | **7** | **0** |
| All touched files (this round) | — | **491** | **19** |

`tsc --noEmit` clean.

## What this unlocks

- The user can find any skill in 1 second without leaving
  the chat
- The same `Ctrl+K` hotkey works everywhere in the app
- The palette is the foundation for future "command palette"
  features (run-eval, replay-dataset, etc.)
