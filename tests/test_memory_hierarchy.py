"""Tests for the three-tier memory hierarchy."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kairos.memory_hierarchy import (
    DEFAULT_USER_PATH,
    MemoryHierarchy,
    MemoryRecord,
    _project_path,
    _user_path,
)


@pytest.fixture
def tmp_user_dir(tmp_path: Path, monkeypatch):
    """Redirect ~/.kairos/memory to a tmp dir so we don't pollute HOME."""
    user_file = tmp_path / "user.json"
    proj_dir = tmp_path / "projects"
    proj_dir.mkdir()
    # Default values: use the tmp paths explicitly
    return tmp_path


def test_default_user_path_is_under_kairos():
    """The default user memory lives under ``~/.kairos/memory``."""
    assert "kairos/memory" in DEFAULT_USER_PATH


def test_user_path_expanduser():
    p = _user_path("~/my-memory.json")
    assert "~" not in str(p)
    assert str(p).endswith("my-memory.json")


def test_project_path_includes_project_id():
    p = _project_path("", "demo-proj")
    assert "demo-proj" in str(p)
    assert str(p).endswith(".json")


# ---------------------------------------------------------------------------
# session tier
# ---------------------------------------------------------------------------


def test_session_add_returns_record():
    h = MemoryHierarchy()
    rec = h.session_add("current task: benchmark", category="context",
                        project_id="p1", session_id="s1")
    assert isinstance(rec, MemoryRecord)
    assert rec.tier == "session"
    assert rec.content == "current task: benchmark"
    assert rec.project_id == "p1"
    assert rec.session_id == "s1"


def test_session_add_is_in_memory_only():
    h = MemoryHierarchy()
    h.session_add("ephemeral note")
    # No disk activity — session tier is in-memory only.
    assert h.stats()["session_records"] == 1


def test_session_clear_removes_all_session_records():
    h = MemoryHierarchy()
    h.session_add("note 1")
    h.session_add("note 2")
    h.session_add("note 3")
    assert h.stats()["session_records"] == 3
    removed = h.session_clear()
    assert removed == 3
    assert h.stats()["session_records"] == 0


# ---------------------------------------------------------------------------
# project tier
# ---------------------------------------------------------------------------


def test_project_add_persists_to_disk(tmp_path: Path):
    h = MemoryHierarchy(user_path=str(tmp_path / "user.json"),
                          project_path=str(tmp_path / "projects" / "{id}.json"))
    h.project_add("p1", "uses Black formatter", category="always")
    # The file should now exist on disk
    on_disk = h.project_list("p1")
    assert len(on_disk) == 1
    assert on_disk[0].content == "uses Black formatter"
    assert on_disk[0].tier == "project"


def test_project_add_isolated_per_project(tmp_path: Path):
    h = MemoryHierarchy(user_path=str(tmp_path / "user.json"),
                          project_path=str(tmp_path / "projects" / "{id}.json"))
    h.project_add("p1", "note for p1")
    h.project_add("p2", "note for p2")
    assert len(h.project_list("p1")) == 1
    assert len(h.project_list("p2")) == 1
    assert h.project_list("p1")[0].content == "note for p1"
    assert h.project_list("p2")[0].content == "note for p2"


def test_project_list_empty_for_unknown_project(tmp_path: Path):
    h = MemoryHierarchy(user_path=str(tmp_path / "user.json"),
                          project_path=str(tmp_path / "projects" / "{id}.json"))
    assert h.project_list("unknown") == []


def test_project_add_survives_reload(tmp_path: Path):
    """Records added in one MemoryHierarchy instance are visible
    in a second instance reading the same file."""
    h1 = MemoryHierarchy(user_path=str(tmp_path / "user.json"),
                          project_path=str(tmp_path / "projects" / "{id}.json"))
    h1.project_add("p1", "persistent fact")
    h2 = MemoryHierarchy(user_path=str(tmp_path / "user.json"),
                          project_path=str(tmp_path / "projects" / "{id}.json"))
    assert len(h2.project_list("p1")) == 1


# ---------------------------------------------------------------------------
# user tier
# ---------------------------------------------------------------------------


def test_user_add_persists_to_disk(tmp_path: Path):
    h = MemoryHierarchy(user_path=str(tmp_path / "user.json"),
                          project_path=str(tmp_path / "projects" / "{id}.json"))
    h.user_add("user prefers dark mode", category="always")
    h.user_add("user's name is Alice", category="context")
    records = h.user_list()
    assert len(records) == 2
    contents = {r.content for r in records}
    assert "user prefers dark mode" in contents


def test_user_add_with_metadata(tmp_path: Path):
    h = MemoryHierarchy(user_path=str(tmp_path / "user.json"),
                          project_path=str(tmp_path / "projects" / "{id}.json"))
    rec = h.user_add("test", metadata={"source": "settings-ui"})
    assert rec.metadata == {"source": "settings-ui"}


# ---------------------------------------------------------------------------
# recall (union across tiers)
# ---------------------------------------------------------------------------


def test_recall_unions_all_tiers(tmp_path: Path):
    h = MemoryHierarchy(user_path=str(tmp_path / "user.json"),
                          project_path=str(tmp_path / "projects" / "{id}.json"))
    h.user_add("user always X", category="always")
    h.project_add("p1", "project always Y", category="always")
    h.session_add("session always Z", category="always",
                  project_id="p1", session_id="s1")
    out = h.recall(project_id="p1", session_id="s1", category="always")
    contents = [r.content for r in out]
    assert "user always X" in contents
    assert "project always Y" in contents
    assert "session always Z" in contents


def test_recall_always_shortcut(tmp_path: Path):
    h = MemoryHierarchy(user_path=str(tmp_path / "user.json"),
                          project_path=str(tmp_path / "projects" / "{id}.json"))
    h.user_add("user-rule", category="always")
    h.user_add("user-context", category="context")
    always = h.recall_always(project_id="p1")
    assert always == ["user-rule"]


def test_recall_never_shortcut(tmp_path: Path):
    h = MemoryHierarchy(user_path=str(tmp_path / "user.json"),
                          project_path=str(tmp_path / "projects" / "{id}.json"))
    h.user_add("never rm -rf", category="never")
    assert h.recall_never(project_id="p1") == ["never rm -rf"]


def test_recall_filters_by_category(tmp_path: Path):
    h = MemoryHierarchy(user_path=str(tmp_path / "user.json"),
                          project_path=str(tmp_path / "projects" / "{id}.json"))
    h.user_add("a", category="always")
    h.user_add("b", category="context")
    h.user_add("c", category="always")
    out = h.recall(category="always")
    contents = [r.content for r in out]
    assert "a" in contents
    assert "c" in contents
    assert "b" not in contents


def test_recall_filters_by_project_id(tmp_path: Path):
    h = MemoryHierarchy(user_path=str(tmp_path / "user.json"),
                          project_path=str(tmp_path / "projects" / "{id}.json"))
    h.session_add("for p1", project_id="p1", session_id="s1")
    h.session_add("for p2", project_id="p2", session_id="s1")
    out = h.recall(project_id="p1")
    contents = [r.content for r in out]
    assert "for p1" in contents
    assert "for p2" not in contents


def test_recall_session_filter_matches_only_same_session(tmp_path: Path):
    h = MemoryHierarchy(user_path=str(tmp_path / "user.json"),
                          project_path=str(tmp_path / "projects" / "{id}.json"))
    h.session_add("for s1", project_id="p1", session_id="s1")
    h.session_add("for s2", project_id="p1", session_id="s2")
    out = h.recall(project_id="p1", session_id="s1")
    assert [r.content for r in out] == ["for s1"]


# ---------------------------------------------------------------------------
# MemoryRecord round-trip
# ---------------------------------------------------------------------------


def test_memory_record_roundtrip():
    r = MemoryRecord(
        id="abc123", tier="user", category="always",
        content="hello", project_id="p1", session_id="s1",
        metadata={"x": 1},
    )
    r2 = MemoryRecord.from_dict(r.to_dict())
    assert r.id == r2.id
    assert r.tier == r2.tier
    assert r.category == r2.category
    assert r.content == r2.content
    assert r.project_id == r2.project_id
    assert r.session_id == r2.session_id
    assert r.metadata == r2.metadata


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def test_stats_returns_counts(tmp_path: Path):
    h = MemoryHierarchy(user_path=str(tmp_path / "user.json"),
                          project_path=str(tmp_path / "projects" / "{id}.json"))
    h.session_add("a")
    h.session_add("b")
    h.user_add("u1")
    h.user_add("u2")
    h.user_add("u3")
    s = h.stats()
    assert s["session_records"] == 2
    assert s["user_records"] == 3


def test_stats_survives_corrupt_file(tmp_path: Path):
    """A corrupt user memory file shouldn't crash stats()."""
    user_file = tmp_path / "user.json"
    user_file.write_text("{not valid json", encoding="utf-8")
    h = MemoryHierarchy(user_path=str(user_file),
                          project_path=str(tmp_path / "projects" / "{id}.json"))
    s = h.stats()
    # 0 records (corrupt file → empty list)
    assert s["user_records"] == 0
