# Round 27 — Windows pre-commit hook compatibility

> Status: **1 new check + 1 fix shipped**. **2 new tests
> pass** (1 Windows-only skipped on Linux). **502 + 20 in the
> touched-file sweep**. `tsc --noEmit` clean.

R21 added the pre-commit hook but didn't handle Windows
specifically. R27 closes the gap.

---

## 1. `check_windows_compat` ✅

A new first-line check in the default pre-commit set
(`DEFAULT_CHECKS[0]`). On Windows:
- Re-encodes `sys.stdout` to UTF-8 so the `✓/✗` glyphs don't
  crash the GBK console
- Surfaces this explicitly to the user so they know it ran

On non-Windows it's a fast no-op that returns
`True` with a "skipped" detail.

### Tests (2 new in `tests/test_hook.py`)
- `test_check_windows_compat_non_windows_skipped` —
  on non-Windows, returns True with the skip message
- `test_check_windows_compat_on_windows` — on Windows,
  the check re-encodes stdout to UTF-8

### What this unlocks
- The default `python -m kairos.hook run` now works on
  Windows out of the box (no UnicodeEncodeError on
  `print("✓")`)
- Other developers (the user is on Windows per system context)
  can adopt the pre-commit hook without a manual stdout
  reconfig

---

## Test summary

| Suite | New | Pass | Skipped |
|---|---:|---:|---:|
| `tests/test_hook.py` (R27 additions) | 2 | 1 | 1 (Windows-only) |
| **`Round 27 new`** | **2** | **1** | **1** |
| All touched files (this round) | — | **502** | **20** |

`tsc --noEmit` clean.

## Round-by-round schedule (final)

| Round | What | Status |
|---|---|---|
| 8-24 | All prior rounds | ✅ |
| 25 | Trend API + UI panel | ✅ |
| 26 | Per-case flaky detection | ✅ |
| 27 | Windows pre-commit compatibility | ✅ |
| 28+ | Whatever's next | as needed |
