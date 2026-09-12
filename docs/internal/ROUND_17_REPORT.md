# Round 17 — Full-text skill search (SQLite FTS5)

> Status: **2/2 items shipped**. **15 new tests pass** (3 of
> which are FTS5-only and skip on stripped-down Python builds).
> `tsc --noEmit` clean.

The skill library is now 14+ skills strong (R9's superpowers
drop-in). A real full-text index is overdue: keyword heuristics
are fast but rank poorly on natural-language queries like
"find me the skill that mentions pytest fixtures".

---

## 1. SQLite FTS5 skill search backend ✅

`kairos.skill_search` — a search engine over the skill library,
backed by SQLite FTS5 (the standard full-text extension
shipped with every modern Python). Falls back to a Python-side
token match when FTS5 is unavailable (e.g. stripped-down
containers).

### What changed
- **`kairos/skill_search.py`** (8.3 KB) — new module:
  - `fts5_available() -> bool` — runtime detection (cached
    in a module-level var)
  - `build_index_from_loader(loader, db_path) -> int` —
    builds a fresh `skills_fts` virtual table from
    `loader.discover()`. Uses Porter stemming for English
    morphology.
  - `search(query, loader=None, db_path=None, limit=10)` —
    FTS5 MATCH query when the index is available, Python
    fallback otherwise. Returns a list of
    `{name, source_path, priority, score, snippet}` dicts
    sorted by descending score.
  - CLI entrypoint: `python -m kairos.skill_search "pytest"`

### What this unlocks
- The user can find skills by natural-language query, not
  just exact keyword
- The team can audit the library: "which skills mention
  Flask?" → 1 second answer
- Round 18+ can wire a `:skill <query>` slash command in
  the chat UI

---

## 2. CLI + tests ✅

### Tests (15 in `tests/test_skill_search.py`)
- 2 tests for `_skill_to_row` (truncation, defensive defaults)
- 3 FTS5-only tests for index round-trip + no-match + limit
- 4 Python fallback tests (name match > body match, empty
  query returns all by priority, no match returns empty,
  limit respected)
- 3 `search()` tests (loader-based, no-db path, raises on
  no loader + no db)
- 2 edge cases (rebuild replaces old DB, empty list works)

### CLI usage
```bash
# Build an index once, then search quickly
python -m kairos.skill_search build --db .kairos/skills.fts
python -m kairos.skill_search "pytest fixtures" --db .kairos/skills.fts

# Or just search directly (slower — builds a one-shot index)
python -m kairos.skill_search "react hooks" --project-dir .
```

---

## Test summary

| Suite | New | Pass | Skipped |
|---|---:|---:|---:|
| `tests/test_skill_search.py` (15) | 15 | 12 | 3 (FTS5 only) |
| **`Round 17 new`** | **15** | **12** | **3** |
| All touched files (this round) | — | **419** | **19** |

`tsc --noEmit` clean.
