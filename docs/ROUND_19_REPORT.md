# Round 19 — Eval Panel UI + Datasets API

> Status: **2/2 items shipped**. **6 new tests pass**.
> **451 + 19 in the touched-file sweep**. `tsc --noEmit` clean.

The user can now do the full record / replay / derive loop
without touching the CLI. Three new API endpoints + a new
web panel.

---

## 1. Eval Datasets API ✅

Four new endpoints under `/api/cost/`:

- `GET /api/cost/datasets?directory=...` — list JSONL datasets
  in the given directory (default `data/datasets/`). Returns
  `[{name, path, size_bytes, count, mtime}, ...]`.
- `POST /api/cost/datasets/record` — append cases from a run
  JSON to a dataset. Honors `only_passed`.
- `POST /api/cost/datasets/replay` — replay a dataset against
  the default target.
- `POST /api/cost/datasets/derive` — scan a git repo for
  kairos commits and write a suite.

### What changed
- **`api/routes/cost.py`** — 4 new endpoints added to the
  existing `cost` router.

### Tests (6 in `tests/test_eval_api.py`)
- `test_list_datasets_returns_count_and_size` — 3 JSONL files
  in a temp dir → 3 entries with correct counts/sizes
- `test_list_datasets_empty_directory` — no files → `[]`
- `test_list_datasets_missing_directory` — path doesn't exist
  → `[]` (not 404)
- `test_record_dataset_endpoint` — run with 1 pass + 1 fail,
  `only_passed=true` → 1 case recorded
- `test_record_dataset_endpoint_all_cases` — `only_passed=false`
  → 2 cases recorded
- `test_derive_from_git_endpoint` — git commit with a plan
  block → 1 case derived

### What this unlocks
- The web UI can show the user "you have 3 datasets" + record
  / replay / derive buttons — no shell access required
- External scripts (CI, dashboards) can hit the same
  endpoints

---

## 2. EvalPanel UI ✅

A 4-tab card (Datasets, Record, Replay, Derive) wired into
the Loop page.

### What changed
- **`web/src/components/EvalPanel.tsx`** (9.7 KB) — new
  component. Uses `Tabs` to switch between:
  - **Datasets**: list with count + size + mtime, with a
    refresh button and a directory filter
  - **Record**: form (run_path, dataset_path, only_passed)
    + success alert
  - **Replay**: form (dataset_path, out_path) + result
    alert with pass_rate and cost
  - **Derive**: form (repo_path, out_path, limit) + result
    alert with case count
- **`web/src/pages/Loop.tsx`** — imports and renders the
  panel below `CostDashboard`.

### What this unlocks
- The team can manage the regression suite entirely from the
  web — no shell access
- Datasets grow automatically (every successful CI run is
  one Record click away)

---

## Test summary

| Suite | New | Pass | Skipped |
|---|---:|---:|---:|
| `tests/test_eval_api.py` (6) | 6 | 6 | 0 |
| **`Round 19 new`** | **6** | **6** | **0** |
| All touched files (this round) | — | **451** | **19** |

`tsc --noEmit` clean.
