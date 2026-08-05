"""Tests for kairos.core.persistence (D-01: project_id isolation)."""

import json

import pytest

from kairos.core.message_bus import Message
from kairos.core.persistence import Persistence

@pytest.fixture
def db(tmp_path):
    """Fresh Persistence rooted in tmp_path so we never touch real DB."""
    return Persistence(tmp_path / "test.db")

def test_save_and_load_message_includes_project_id_from_metadata(db):
    msg = Message(sender="p1.team_leader", topic="task", content="hi",
                  metadata={"project_id": "p1"})
    db.save_message(msg)

    rows = db.load_messages(project_id="p1")
    assert len(rows) == 1
    assert rows[0]["project_id"] == "p1"

def test_project_filter_excludes_other_projects(db):
    db.save_message(Message(sender="p1.team_leader", topic="t",
                            content="a", metadata={"project_id": "p1"}))
    db.save_message(Message(sender="p2.team_leader", topic="t",
                            content="b", metadata={"project_id": "p2"}))

    p1_msgs = db.load_messages(project_id="p1")
    assert len(p1_msgs) == 1
    assert p1_msgs[0]["content"] == "a"

def test_legacy_sender_prefix_is_parsed_for_project_id(db):
    """Older callers embed project_id as a sender prefix — keep that path
    working so existing data remains queryable."""
    msg = Message(sender="abc123.frontend_dev", topic="t", content="legacy")
    db.save_message(msg)

    rows = db.load_messages(project_id="abc123")
    assert len(rows) == 1
    assert rows[0]["content"] == "legacy"

def test_no_filter_returns_all(db):
    for pid in ("p1", "p2", "p3"):
        db.save_message(Message(sender=f"{pid}.x", topic="t", content=pid,
                                metadata={"project_id": pid}))
    rows = db.load_messages(limit=100)
    assert len(rows) == 3

def test_migration_adds_project_id_column_on_old_db(tmp_path):
    """Simulate an existing pre-migration database and verify _migrate
    adds the column + index without losing existing rows."""
    import sqlite3
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE messages (
                id TEXT PRIMARY KEY,
                sender TEXT, receiver TEXT, topic TEXT, content TEXT,
                msg_type TEXT, timestamp REAL, metadata TEXT
            );
        """)
        conn.execute(
            "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("m1", "p1.x", "", "t", "old", "text", 1.0, "{}"),
        )

    # Now boot Persistence — should migrate and preserve the row.
    p = Persistence(db_path)
    rows = p.load_messages()
    assert len(rows) == 1
    assert rows[0]["id"] == "m1"
    # The legacy row has no project_id, so the scoped query must skip it.
    legacy_only = p.load_messages(project_id="p1")
    assert len(legacy_only) == 0
    # And new writes go to the column properly.
    p.save_message(Message(sender="p1.x", topic="t", content="new",
                           metadata={"project_id": "p1"}))
    after_new = p.load_messages(project_id="p1")
    assert len(after_new) == 1
    assert after_new[0]["content"] == "new"
    # Unfiltered load returns both (legacy + new).
    assert len(p.load_messages()) == 2