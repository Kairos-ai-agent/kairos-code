"""Alignment guard: the memory the UI writes must be the memory the agent reads.

The defect this freezes: the "remember" UI wrote to ``<data_dir>/memory_kb.json``
while the agent read ``<project_dir>/.kairos/memory_kb.json`` — two different
files, so a memory the user saved was never recallable. The fix routes every
construction through :func:`kairos.memory_kb.resolve_storage_path`; this file
proves it structurally (no hand-written store paths) and behaviourally (a
round-trip from the API to the agent side).

Four checks:

* (a) source scan — no ``MemoryKB(...)`` in production code builds its path from
  a string literal; the path must come from the resolver.
* (b) round-trip — write through the real "remember" API, then recall through
  the agent's own store path.
* (c) compatibility — a canonical store that does not exist yet still answers
  from a pre-fix legacy file, read-only.
* (d) the canonical path is per project and matches the documented default when
  there is no project (no third/fourth location appears).
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_SCAN_ROOTS = ("api", "kairos")
# The resolver module itself defines the canonical/legacy path literals.
_WHITELIST_FILES = {"kairos/memory_kb.py"}


# ---------------------------------------------------------------------------
# (a) source scan: no hand-written store path in a MemoryKB(...) construction
# ---------------------------------------------------------------------------
def _func_name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def iter_memorykb_calls(source: str, filename: str):
    """Yield ``(lineno, offending_literal_or_None)`` per ``MemoryKB(...)`` call."""
    tree = ast.parse(source, filename)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _func_name(node) != "MemoryKB":
            continue
        args = list(node.args) + [kw.value for kw in node.keywords]
        literal = None
        for arg in args:
            lit = next((s for s in ast.walk(arg)
                        if isinstance(s, ast.Constant)
                        and isinstance(s.value, str)), None)
            if lit is not None:
                literal = lit.value
                break
        yield node.lineno, literal


def scan_memorykb_constructions(source: str, filename: str) -> list[str]:
    """Violations: a ``MemoryKB(...)`` whose path is a hand-written literal."""
    violations: list[str] = []
    for lineno, literal in iter_memorykb_calls(source, filename):
        if literal is None:
            continue
        violations.append(
            f"{filename}:{lineno}: MemoryKB(...) is constructed with the "
            f"hand-written path literal {literal!r}; resolve the path with "
            f"kairos.memory_kb.resolve_storage_path() / project_storage_path() "
            f"instead so the write lands where the agent reads"
        )
    return violations


def _collect_production():
    """(violations, total_calls) over production sources."""
    violations: list[str] = []
    total = 0
    files = []
    for root in _SCAN_ROOTS:
        files += [p for p in sorted((REPO_ROOT / root).rglob("*.py"))
                  if "__pycache__" not in p.parts]
    for path in files:
        rel = Path(path).relative_to(REPO_ROOT).as_posix()
        source = Path(path).read_text(encoding="utf-8")
        for _lineno, _lit in iter_memorykb_calls(source, rel):
            total += 1
        if rel in _WHITELIST_FILES:
            continue
        violations += scan_memorykb_constructions(source, rel)
    return violations, total


def test_no_hand_written_path_in_production_memorykb_constructions():
    violations, total = _collect_production()
    assert violations == [], (
        "a MemoryKB store path is hand-written in production code — this is "
        "exactly how the write path and the read path drifted apart:\n  "
        + "\n  ".join(violations)
    )
    # Non-vacuous: the scan must actually see the construction sites.
    assert total >= 4, f"the scan found only {total} MemoryKB(...) call sites"


def test_scan_flags_a_hand_written_path():
    """Red proof: the guard names the file/line of a literal store path."""
    txt = ("from kairos.memory_kb import MemoryKB\n"
           "kb = MemoryKB(storage_path=somewhere / 'memory_kb.json')\n")
    calls = list(iter_memorykb_calls(txt, "probe.py"))
    assert calls == [(2, "memory_kb.json")], calls
    violations = scan_memorykb_constructions(txt, "probe.py")
    assert violations and "probe.py:2" in violations[0], violations


def test_scan_accepts_a_resolved_path(tmp_path):
    """Positive control: a resolver-built path is not a literal."""
    txt = ("from kairos.memory_kb import MemoryKB, resolve_storage_path\n"
           "kb = MemoryKB(storage_path=resolve_storage_path(project_dir=d))\n")
    assert scan_memorykb_constructions(txt, "probe.py") == []


# ---------------------------------------------------------------------------
# (b) round-trip: API "remember" -> agent-side recall
# ---------------------------------------------------------------------------
async def test_api_remember_then_the_agent_can_recall(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    import api.deps as deps
    from api.routes.borrowed import router as borrowed_router
    from kairos.memory_kb import MemoryKB, project_storage_path

    work = tmp_path / "proj"
    work.mkdir()
    proj = SimpleNamespace(id="p1", work_dir=str(work), workspace=str(work))

    fake_orch = MagicMock()
    fake_orch.get_project = lambda pid: proj if pid == "p1" else None
    monkeypatch.setattr(deps, "orchestrator", fake_orch)

    app = FastAPI()
    app.include_router(borrowed_router)
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://t") as c:
        r = await c.post("/api/borrowed/memory/remember", json={
            "key": "deploy-target", "value": "prod-eu-1",
            "scope": "project", "project_id": "p1"})
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True

    # The write must have landed in the file the agent reads.
    canonical = project_storage_path(work)
    assert canonical.exists(), (
        "the API 'remember' did not write the per-project store the agent reads; "
        f"expected {canonical}"
    )

    # Agent side: exactly how kairos/agents/base.py builds its KB.
    agent_kb = MemoryKB(storage_path=canonical)
    hits = agent_kb.recall("deploy target", scope="project")
    assert any(h.key == "deploy-target" for h in hits), (
        "the agent cannot recall a memory written through the API; "
        f"hits were {[h.key for h in hits]}"
    )


# ---------------------------------------------------------------------------
# (c) compatibility: canonical absent, legacy present -> still readable
# ---------------------------------------------------------------------------
def test_legacy_store_is_read_when_canonical_absent(tmp_path, monkeypatch):
    from kairos.memory_kb import MemoryKB, project_storage_path

    data = tmp_path / "data"
    monkeypatch.setenv("KAIROS_DATA_DIR", str(data))
    legacy = data / "memory_kb.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({"project": {"old-key": {
        "key": "old-key", "value": "legacy-value", "scope": "project",
        "tags": [], "created_at": 1.0, "updated_at": 2.0, "feedback": [],
    }}}), encoding="utf-8")
    legacy_before = legacy.read_bytes()

    work = tmp_path / "proj"
    work.mkdir()
    canonical = project_storage_path(work)
    assert not canonical.exists()

    kb = MemoryKB(storage_path=canonical)          # canonical absent
    hits = kb.recall("legacy", scope="project")
    assert any(h.key == "old-key" for h in hits), [h.key for h in hits]

    # Non-destructive: the legacy file is neither removed nor rewritten, and
    # nothing was migrated into existence.
    assert legacy.read_bytes() == legacy_before
    assert not canonical.exists()


# ---------------------------------------------------------------------------
# (d) the canonical path is per project; no fourth location appears
# ---------------------------------------------------------------------------
def test_canonical_is_project_scoped_and_matches_the_documented_default(
        tmp_path, monkeypatch):
    from kairos.memory_kb import (
        default_storage_path, legacy_storage_paths, project_storage_path,
        resolve_storage_path,
    )

    work = tmp_path / "proj"
    canonical = project_storage_path(work)
    # Documented, repo-consistent per-project location.
    assert canonical == Path(work) / ".kairos" / "memory_kb.json"
    # The resolver prefers it whenever a project dir exists.
    assert resolve_storage_path(project_dir=work) == canonical

    data = tmp_path / "data"
    monkeypatch.setenv("KAIROS_DATA_DIR", str(data))
    # No project -> the documented default, which is one of the legacy read
    # locations (so a legacy file is exactly the default file — one location,
    # not a third).
    assert resolve_storage_path() == default_storage_path()
    assert default_storage_path() == data / "memory" / "kb.json"
    assert set(legacy_storage_paths()) == {
        data / "memory_kb.json", data / "memory" / "kb.json",
    }
    # The project store is never confused with the default store.
    assert canonical != default_storage_path()
