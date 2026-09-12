# Documentation

Start with the [main README](../README.md) — it covers the 60-second demo, the
gate/ledger model and the install paths.

## What is in here

| File | What it covers |
|---|---|
| [`SANDBOX_ISOLATION.md`](SANDBOX_ISOLATION.md) | What the sandbox actually contains: argv-only execution, the command allowlist, Landlock on Linux, Job Objects on Windows — and the gaps that remain. |
| [`KAIROS_INDEX.md`](KAIROS_INDEX.md) | Module-by-module map of `kairos/` — useful when you are about to change something. |
| [`OSS_ADOPTION_ROADMAP.md`](OSS_ADOPTION_ROADMAP.md) | Where the project is going: what a first-time user should see in 60 seconds, what is deliberately out of scope. |
| [`assets/`](assets) | Screenshots used by the README (Gate Report, Run view, History view). |

## Internal notes

[`internal/`](internal) holds the historical working notes: the round-by-round
development reports, the code-review iterations, the earlier LLM-connectivity
and harness-baseline reports, and the draft competitor charts. They are kept for
provenance — they document how the pipeline was built and what was rejected
along the way — but they describe intermediate states and are **not**
documentation of the current release.

## Contributing docs

Two rules:

1. **No absolute paths.** Anything that reads like `D:\some\machine\...` or
   `C:\Users\<name>\...` is a bug — use relative paths or `<repo>` placeholders.
   `scripts/prepare_github.py` exists to keep the publishing placeholders
   consistent.
2. **A doc that describes behaviour must name the file that implements it.**
   Documentation drifts; a pointer to `kairos/loop/loop_runner.py` lets the
   reader check.
