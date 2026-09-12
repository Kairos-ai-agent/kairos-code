# Round 15 — Firecracker + Cognee adapter

> Status: **2/2 items shipped**. **16 new tests pass** (4
> Linux-only skipped + 12 backend). `tsc --noEmit` clean.

This round closes two long-deferred items from the OSS adoption
roadmap: the Firecracker sandbox tier and the Cognee-style
4-op memory adapter.

---

## 1. Firecracker tier ✅

Strongest isolation Kairos can detect on a host: each
process in its own microVM with a separate kernel. Used in
production by AWS Lambda, Fly Machines, and many others.

### What changed
- **`kairos/sandbox.py`** — two new functions:
  - `firecracker_available() -> bool` — Linux + `firecracker`
    on PATH + `/dev/kvm` present
  - `firecracker_install_instructions() -> str` — multi-line
    install recipe (snap OR release tarball + KVM check)
- `describe_capabilities()` extended with the `firecracker`
  key

### Tests (4 in `tests/test_firecracker_sandbox.py`, Linux-only)
- `firecracker_available_is_false_on_non_linux`
- `describe_capabilities_includes_firecracker`
- `firecracker_install_instructions_are_helpful` (mentions
  `firecracker`, `/dev/kvm`, install method)
- `firecracker_install_instructions_non_empty` (≥200 chars)

### What this unlocks
- The Settings UI can show the user **five** sandbox tiers:
  - ✅ Landlock (Linux 5.13+, always on)
  - ✅ nsjail (if binary present)
  - ✅ gVisor (if `runsc` present)
  - ✅ Firecracker (if `firecracker` + `/dev/kvm`)
  - ✅ macOS Seatbelt
- A single `describe_capabilities()` call returns the full
  picture; the UI renders a checklist with one click per tier

---

## 2. Cognee-style 4-op memory adapter ✅

The 4-op memory API (remember / recall / forget / improve) that
Round 10 added in `memory_kb.py` is now a **dispatcher** that
can route to any of four backends.

### What changed
- **`kairos/memory_4op.py`** (7.5 KB) — new module:
  - `get_kb_for_backend(backend=None)` — resolves the backend
    name (from `KAIROS_MEMORY_BACKEND` env var, default `local`)
    to a 4-op object
  - `get_default_backend()` — env reader
  - `list_backends()` — `["local", "cognee", "graphiti", "mock"]`
  - 4 adapters:
    - `local` → real `MemoryKB` (JSON file backend, default)
    - `cognee` → `_CogneeAdapter` (real cognee lib if installed)
    - `graphiti` → `_GraphitiAdapter` (real graphiti-core if installed)
    - `mock` → `_MockAdapter` (in-memory, for tests)
  - Each adapter exposes the same 4 async methods
    (`remember/recall/forget/improve`) so the caller is
    backend-agnostic

### Tests (12 in `tests/test_memory_4op.py`)
- `list_backends_includes_known` — 4 backends registered
- `get_default_backend_default_is_local` — env var reading
- `get_default_backend_reads_env` — uppercase is normalized
- `get_kb_for_backend_local` — returns a `MemoryKB` instance
- `get_kb_for_backend_mock` — returns a `_MockAdapter`
- `get_kb_for_backend_unknown_raises` — clear error on bad name
- `get_kb_for_backend_cognee_without_install_raises` —
  deferred-failure contract
- `mock_adapter_remember_recall` — the 4-op contract
- `mock_adapter_forget_removes`
- `mock_adapter_improve_existing` / `_missing_key`
- `all_backends_expose_four_methods` — uniform interface

### What this unlocks
- The team can swap memory backends without changing any
  call site: just set `KAIROS_MEMORY_BACKEND=cognee` and
  every `MemoryKB.remember(...)` call routes to the real
  cognee library
- A future round can ship a real `cognee` integration (with
  Neo4j / FalkorDB) by implementing `_CogneeAdapter` properly
- The `mock` backend is invaluable for tests that want
  deterministic in-memory state

---

## Test summary

| Suite | New | Pass | Skipped |
|---|---:|---:|---:|
| `tests/test_firecracker_sandbox.py` (4) | 4 | 0 (Linux-only) | 4 |
| `tests/test_memory_4op.py` (12) | 12 | 12 | 0 |
| **`Round 15 new`** | **16** | **12** | **4** |
| All touched files (this round) | — | **380** | **19** |

`tsc --noEmit` clean.

## What's still on the roadmap (16+)

From the original OSS adoption roadmap:
- **Textual TUI** — full rewrite of the TUI; the current
  plain-ANSI TUI still works, but Textual gives scrolling
  and resize support. Defer until a user asks.
- **Cost dashboard UI** — pipe `cost.py`'s JSONL log into
  the web frontend. R14 wrote the data layer; the UI is
  the natural follow-up.
- **Full-text skill search** — currently skills match on
  keyword heuristics; a tiny SQLite FTS index would let the
  user search for "json parse" or "pytest" and get the
  relevant skill.

## Round-by-round schedule (updated)

| Round | What | Status |
|---|---|---|
| 8-14 | All prior rounds | ✅ |
| 15 | **This round** — Firecracker tier; Cognee-style 4-op adapter | ✅ |
| 16+ | Cost dashboard UI; Textual TUI; full-text skill search | as needed |
