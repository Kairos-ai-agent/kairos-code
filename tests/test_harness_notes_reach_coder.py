"""Alignment guard: a harness memory_note must reach the coder prompt.

The defect this freezes: the Continual Harness panel wrote ``memory_notes`` to
``<project>/.kairos/harness/harness.json`` (via ``HarnessStore.add_memory_note``
/ the ``POST /harness/memory-note`` route) while the prompt assembler
``kairos.memory.retrieval.assemble_coder_memory`` never read that file — the
note was write-only, so the agent never saw it. The fix adds the read half
(``kairos.continual_harness.load_memory_notes``) and wires it into the existing
note-assembly channel. This file proves it behaviourally and structurally:

* (a) round-trip — write through the store / the real API route, then read the
  note back out of ``assemble_coder_memory``;
* (b) bounded — 20 notes inject at most ``MAX_HARNESS_NOTES``, newest first;
* (c) robust — a missing / corrupt / wrong-shaped file is skipped (warning, not
  a crash) and other memory survives;
* (d) isolated — another project's harness notes never leak into this project;
* (e) once — content already in the project notes is not injected a second time;
* (f) source guard — the assembler still references the harness note source.
"""
from __future__ import annotations

import ast
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from kairos.continual_harness import HarnessStore, load_memory_notes
from kairos.core.persistence import Persistence
from kairos.memory.retrieval import MAX_HARNESS_NOTES, assemble_coder_memory

REPO_ROOT = Path(__file__).resolve().parents[1]

HARNESS_REL = Path(".kairos") / "harness" / "harness.json"


def _project(pid: str, work: Path) -> SimpleNamespace:
    return SimpleNamespace(
        id=pid, name=pid, description="", workspace=str(work),
        work_dir=str(work), requirements="", status="active", created_at=0.0,
    )


@pytest.fixture
def env(tmp_path):
    """Two projects with distinct work dirs + a shared persistence DB."""
    work_a = tmp_path / "projA"
    work_a.mkdir()
    work_b = tmp_path / "projB"
    work_b.mkdir()
    db = Persistence(tmp_path / "kairos.db")
    db.save_project(_project("A", work_a))
    db.save_project(_project("B", work_b))
    return SimpleNamespace(db=db, a=work_a, b=work_b)


# ---------------------------------------------------------------------------
# (a) round-trip: store write -> coder prompt
# ---------------------------------------------------------------------------
def test_harness_note_reaches_the_coder_prompt(env):
    HarnessStore(env.a).add_memory_note(
        "test_command", "always run `pytest -q` before committing",
        tags=["testing"])

    block = assemble_coder_memory(env.db, "A", "please add a test")

    assert "always run `pytest -q` before committing" in block, block
    # The section is labelled so the reader knows where it came from.
    assert "Harness Notes" in block
    assert "(harness" in block


async def test_api_harness_note_then_coder_can_see_it(env, monkeypatch):
    """End-to-end through the real route: the API's write path and the
    agent's read path must be the same file."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    import api.deps as deps
    from api.routes.p2_features import router as p2_router

    proj = SimpleNamespace(id="A", work_dir=str(env.a), workspace=str(env.a))
    fake_orch = MagicMock()
    fake_orch.get_project = lambda pid: proj if pid == "A" else None
    monkeypatch.setattr(deps, "orchestrator", fake_orch)

    app = FastAPI()
    app.include_router(p2_router)
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://t") as c:
        r = await c.post(
            "/api/borrowed/A/harness/memory-note",
            json={"key": "style", "value": "API-MARKER format with black",
                  "tags": ["style"]})
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True

    block = assemble_coder_memory(env.db, "A", "anything")
    assert "API-MARKER format with black" in block, block


# ---------------------------------------------------------------------------
# (b) bounded: 20 notes -> <= MAX_HARNESS_NOTES, newest first
# ---------------------------------------------------------------------------
def test_harness_notes_are_capped_and_keep_the_newest(env):
    store = HarnessStore(env.a)
    for i in range(20):
        store.add_memory_note(f"k{i:02d}", f"value-{i:02d}")

    # The store stamps near-identical wall-clock times; re-stamp deterministically
    # so "newest" is unambiguous (k19 newest .. k00 oldest).
    path = env.a / HARNESS_REL
    data = json.loads(path.read_text(encoding="utf-8"))
    for i, note in enumerate(data["memory_notes"]):
        note["added_at"] = 1000.0 + i
        note.pop("updated_at", None)
    path.write_text(json.dumps(data), encoding="utf-8")

    block = assemble_coder_memory(env.db, "A", "x")
    injected = [i for i in range(20) if f"value-{i:02d}" in block]
    assert len(injected) == MAX_HARNESS_NOTES, injected
    # Exactly the newest MAX_HARNESS_NOTES (indices 12..19).
    assert injected == list(range(20 - MAX_HARNESS_NOTES, 20)), injected


# ---------------------------------------------------------------------------
# (c) robust: missing / corrupt / wrong-shaped -> skip, warn, keep the rest
# ---------------------------------------------------------------------------
def test_missing_harness_file_is_not_fatal_and_creates_nothing(env):
    assert load_memory_notes(env.a) == []
    # Reading must not litter a harness dir the project never opted into.
    assert not (env.a / ".kairos" / "harness").exists()

    block = assemble_coder_memory(env.db, "A", "x")
    assert block == ""  # nothing yet, but no exception


def test_corrupt_harness_json_is_skipped_and_warned(env, caplog):
    hdir = env.a / ".kairos" / "harness"
    hdir.mkdir(parents=True)
    (hdir / "harness.json").write_text("{not valid json", encoding="utf-8")
    # Other memory must survive the broken file.
    env.db.add_project_note("A", "convention", "snake_case", "use snake_case")

    with caplog.at_level(logging.WARNING, logger="kairos.continual_harness"):
        block = assemble_coder_memory(env.db, "A", "anything")

    assert "use snake_case" in block          # other memory intact
    assert "Harness Notes" not in block       # broken source dropped whole
    assert any(r.levelno >= logging.WARNING for r in caplog.records), (
        "a corrupt harness file must be logged at WARNING, not swallowed")


def test_wrong_shaped_memory_notes_is_skipped_and_warned(env, caplog):
    hdir = env.a / ".kairos" / "harness"
    hdir.mkdir(parents=True)
    # memory_notes is not a list -> whole section dropped, with a warning.
    (hdir / "harness.json").write_text(
        json.dumps({"memory_notes": "oops"}), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="kairos.continual_harness"):
        assert load_memory_notes(env.a) == []
    assert any("expected a list" in r.getMessage() for r in caplog.records), \
        [r.getMessage() for r in caplog.records]

    # A stray non-object entry is dropped but the valid note survives.
    (hdir / "harness.json").write_text(
        json.dumps({"memory_notes": ["not-a-dict",
                                     {"key": "ok", "value": "KEPT-NOTE"}]}),
        encoding="utf-8")
    got = load_memory_notes(env.a)
    assert [n["value"] for n in got] == ["KEPT-NOTE"], got


# ---------------------------------------------------------------------------
# (d) isolated: another project's harness notes must not leak in
# ---------------------------------------------------------------------------
def test_another_projects_harness_note_does_not_leak(env):
    HarnessStore(env.b).add_memory_note("secret", "B-ONLY-MARKER do not leak")

    block_a = assemble_coder_memory(env.db, "A", "x")
    assert "B-ONLY-MARKER" not in block_a, block_a

    block_b = assemble_coder_memory(env.db, "B", "x")
    assert "B-ONLY-MARKER" in block_b, block_b


# ---------------------------------------------------------------------------
# (e) once: content already in project notes is not repeated
# ---------------------------------------------------------------------------
def test_same_text_in_project_note_and_harness_is_injected_once(env):
    body = "always run pytest -q before committing"
    env.db.add_project_note("A", "convention", "test cmd", body)
    store = HarnessStore(env.a)
    store.add_memory_note("test_command", body)          # duplicate of the note
    store.add_memory_note("other", "UNIQUE-HARNESS-ONLY")  # distinct, must show

    block = assemble_coder_memory(env.db, "A", "x")

    assert block.count(body) == 1, block          # stated once, not twice
    assert "UNIQUE-HARNESS-ONLY" in block         # the distinct one survives


# ---------------------------------------------------------------------------
# (f) source guard: the assembler still references the harness note source
# ---------------------------------------------------------------------------
def _calls_named(func: ast.AST, name: str) -> bool:
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            fn = node.func
            callee = (fn.attr if isinstance(fn, ast.Attribute)
                      else fn.id if isinstance(fn, ast.Name) else "")
            if callee == name:
                return True
    return False


def test_assembler_references_the_harness_note_source():
    src = (REPO_ROOT / "kairos" / "memory" / "retrieval.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef)
               and n.name == "assemble_coder_memory"), None)
    assert fn is not None, "assemble_coder_memory not found in retrieval.py"
    # The wiring, not merely an import: the read half must be *called* inside
    # the assembler, or a future cleanup could delete the line and re-break it.
    assert _calls_named(fn, "load_memory_notes"), (
        "assemble_coder_memory no longer calls load_memory_notes — harness "
        "notes would again be write-only")
