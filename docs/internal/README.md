# Internal working notes

**These files are not documentation of the current release.** They are kept as a
record of how the pipeline was built, what was tried, and what was rejected.

| Group | What it is |
|---|---|
| `ROUND_*_REPORT.md` | The development log of each work round (the project was built in numbered rounds, each ending with a report and a test run). |
| `code_review_report_v*.md` | Successive audits, including the ones that found real bugs (a missing round-persistence writer, a dead event branch, cost scoping). |
| `LLM_CONNECTIVITY_REPORT.md`, `HARNESS_BASELINE.md`, `CODEX_*.md` | Provider-connectivity and cross-harness baselines measured during development. |
| `IMPLEMENTATION_PLAN.md`, `REAL_FEATURES_REPORT.md`, `LANDING_RECOMMENDATIONS_REPORT.md` | Planning and product-positioning drafts. |
| `comparisons/` | Draft versions of competitor charts. Superseded; kept only so the iterations are traceable. |
| `system_prompt.md` | A generic agent prompt used while experimenting, unrelated to the product. |
| `PRE_COMMIT_HOOK.py` | An early hook experiment (the supported mechanism is `data/hooks/*.py`, documented in the main README). |

If you are reading the repository to understand or use Kairos Code, read
[`../README.md`](../README.md) first, then [`../docs/README.md`](../README.md).

These notes are **published on purpose**: they show how the pipeline was built,
what was measured, and what was rejected along the way. Nothing outside this
directory imports or references them, so they can be dropped without breaking
anything — but they are part of the record, not clutter.
