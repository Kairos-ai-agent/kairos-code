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


def test_chat_only_drops_stream_noise_but_keeps_the_process(db):
    """R38.6.5 + R38.8: a busy project's newest rows are almost all
    ``stream.chunk`` deltas, so ``chat_only`` must keep the conversation
    topics *and* the agent's process (tool calls and their results, the
    turn's thinking) — the deltas and the per-turn progress chatter stay
    out, otherwise the chat page renders noise and the real bubbles never
    fit in the ``limit`` window."""
    db.save_message(Message(sender="p1.coder", topic="stream.chunk",
                            content="tok", metadata={"project_id": "p1"}))
    db.save_message(Message(sender="p1.coder", topic="agent.progress",
                            content="Turn 1/2: reasoning", metadata={"project_id": "p1"}))
    db.save_message(Message(sender="p1.coder", topic="tool.call",
                            content="{}", metadata={"project_id": "p1"}))
    db.save_message(Message(sender="user", topic="user.chat",
                            content="hello", metadata={"project_id": "p1"}))
    db.save_message(Message(sender="p1.coder", topic="agent.response",
                            content="hi", metadata={"project_id": "p1"}))

    rows = db.load_messages(project_id="p1", chat_only=True, limit=50)
    assert {r["topic"] for r in rows} == {"user.chat", "agent.response", "tool.call"}


def test_chat_only_cursor_pages_backwards_without_gaps(db):
    """The (timestamp, id) keyset cursor must walk the whole history
    exactly once — no duplicates, no skipped rows."""
    for i in range(5):
        db.save_message(Message(sender="p1.coder", topic="agent.response",
                                content=f"m{i}", timestamp=float(i),
                                metadata={"project_id": "p1"}))

    page1 = db.load_messages(project_id="p1", chat_only=True, limit=2)
    assert [r["content"] for r in page1] == ["m4", "m3"]
    oldest = page1[-1]

    page2 = db.load_messages(project_id="p1", chat_only=True, limit=2,
                             before_ts=oldest["timestamp"],
                             before_id=oldest["id"])
    assert [r["content"] for r in page2] == ["m2", "m1"]

    page3 = db.load_messages(project_id="p1", chat_only=True, limit=2,
                             before_ts=page2[-1]["timestamp"],
                             before_id=page2[-1]["id"])
    assert [r["content"] for r in page3] == ["m0"]

    seen = [r["content"] for r in page1 + page2 + page3]
    assert seen == ["m4", "m3", "m2", "m1", "m0"]


def test_cursor_handles_equal_timestamps(db):
    """Rows sharing a timestamp must still be paged by id tie-breaker."""
    for i in range(3):
        db.save_message(Message(sender="p1.coder", topic="agent.response",
                                content=f"t{i}", timestamp=1.0,
                                metadata={"project_id": "p1"}))

    page1 = db.load_messages(project_id="p1", chat_only=True, limit=1)
    page2 = db.load_messages(project_id="p1", chat_only=True, limit=1,
                             before_ts=page1[-1]["timestamp"],
                             before_id=page1[-1]["id"])
    page3 = db.load_messages(project_id="p1", chat_only=True, limit=1,
                             before_ts=page2[-1]["timestamp"],
                             before_id=page2[-1]["id"])
    ids = [r["id"] for r in page1 + page2 + page3]
    assert len(set(ids)) == 3