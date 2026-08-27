"""Tests for kairos.memory_kb (Cognee-style 4-op memory)."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from kairos.memory_kb import MemoryEntry, MemoryKB, VALID_SCOPES


@pytest.fixture
def tmp_kb(tmp_path: Path) -> MemoryKB:
    """A fresh MemoryKB pointing at a temp file."""
    return MemoryKB(storage_path=tmp_path / "kb.json")


# ---------------------------------------------------------------------------
# remember + get + list
# ---------------------------------------------------------------------------


def test_remember_creates_entry(tmp_kb):
    e = tmp_kb.remember("favorite_color", "blue")
    assert e.key == "favorite_color"
    assert e.value == "blue"
    assert e.scope == "project"
    assert e.tags == []
    assert e.created_at > 0
    assert e.updated_at == e.created_at


def test_remember_then_get(tmp_kb):
    tmp_kb.remember("foo", 42)
    got = tmp_kb.get("foo")
    assert got is not None
    assert got.value == 42


def test_remember_invalid_scope_raises(tmp_kb):
    with pytest.raises(ValueError):
        tmp_kb.remember("k", "v", scope="bogus")


def test_remember_update_bumps_updated_at(tmp_kb):
    e1 = tmp_kb.remember("k", "v1")
    e2 = tmp_kb.remember("k", "v2")
    assert e2 is e1  # same entry
    assert e2.value == "v2"
    assert e2.updated_at >= e1.updated_at


def test_remember_merges_tags(tmp_kb):
    tmp_kb.remember("k", "v", tags=["a", "b"])
    tmp_kb.remember("k", "v", tags=["b", "c"])
    e = tmp_kb.get("k")
    assert set(e.tags) == {"a", "b", "c"}


def test_remember_persists_to_disk(tmp_kb):
    tmp_kb.remember("a", 1)
    tmp_kb.remember("b", 2, scope="user")
    assert tmp_kb.path.exists()
    raw = json.loads(tmp_kb.path.read_text(encoding="utf-8"))
    assert "project" in raw
    assert "user" in raw
    assert raw["project"]["a"]["value"] == 1
    assert raw["user"]["b"]["value"] == 2


def test_load_round_trip(tmp_path: Path):
    """A second MemoryKB pointing at the same file sees the same data."""
    p = tmp_path / "kb.json"
    k1 = MemoryKB(storage_path=p)
    k1.remember("a", 1)
    k1.remember("b", "two", scope="user")
    k2 = MemoryKB(storage_path=p)
    assert k2.get("a").value == 1
    assert k2.get("b", scope="user").value == "two"


# ---------------------------------------------------------------------------
# recall
# ---------------------------------------------------------------------------


def test_recall_substring_match(tmp_kb):
    tmp_kb.remember("favorite_color", "blue")
    tmp_kb.remember("favorite_food", "pizza")
    tmp_kb.remember("name", "alice")
    hits = tmp_kb.recall("favorite")
    keys = [h.key for h in hits]
    assert "favorite_color" in keys
    assert "favorite_food" in keys
    assert "name" not in keys


def test_recall_token_match(tmp_kb):
    """Multi-token query: any token matches the entry."""
    tmp_kb.remember("preferred_backend", "anthropic")
    tmp_kb.remember("rate_limit_strategy", "exponential backoff")
    hits = tmp_kb.recall("anthropic rate limit")
    keys = [h.key for h in hits]
    assert "preferred_backend" in keys
    assert "rate_limit_strategy" in keys


def test_recall_limit_respected(tmp_kb):
    for i in range(20):
        tmp_kb.remember(f"k{i}", f"v{i}")
    hits = tmp_kb.recall("v", limit=5)
    assert len(hits) == 5


def test_recall_newest_first(tmp_kb):
    import time
    tmp_kb.remember("shared", 1)
    time.sleep(0.05)
    tmp_kb.remember("shared", 2)
    time.sleep(0.05)
    tmp_kb.remember("shared", 3)
    hits = tmp_kb.recall("shared", limit=10)
    # Same key updated 3 times → only one entry; the latest value
    # is 3 and updated_at is the most recent.
    assert len(hits) == 1
    assert hits[0].value == 3


def test_recall_newest_first_across_distinct_keys(tmp_kb):
    """When keys differ, recall returns them newest-first."""
    import time
    tmp_kb.remember("alpha", "a")
    time.sleep(0.05)
    tmp_kb.remember("beta", "b")
    time.sleep(0.05)
    tmp_kb.remember("gamma", "g")
    # Recall matches the common tag "alpha/beta/gamma" via key prefix
    hits = tmp_kb.recall("a", limit=10)
    # "a" matches "alpha" (and "beta" since "a" is in "beta"). 3 hits
    # total. The newest (gamma) comes first.
    keys = [h.key for h in hits]
    assert keys[0] == "gamma"  # newest first
    assert set(keys) == {"alpha", "beta", "gamma"}


def test_recall_invalid_scope_raises(tmp_kb):
    with pytest.raises(ValueError):
        tmp_kb.recall("q", scope="bogus")


def test_recall_all_scopes(tmp_kb):
    tmp_kb.remember("p", 1)
    tmp_kb.remember("u", 2, scope="user")
    tmp_kb.remember("s", 3, scope="session")
    out = tmp_kb.recall_all_scopes("1")
    # The "1" matches only the project entry
    assert len(out["project"]) == 1
    assert out["project"][0].value == 1
    assert out["user"] == []
    assert out["session"] == []


# ---------------------------------------------------------------------------
# forget
# ---------------------------------------------------------------------------


def test_forget_removes_existing(tmp_kb):
    tmp_kb.remember("a", 1)
    assert tmp_kb.forget("a") is True
    assert tmp_kb.get("a") is None


def test_forget_missing_returns_false(tmp_kb):
    assert tmp_kb.forget("nope") is False


def test_forget_invalid_scope_raises(tmp_kb):
    with pytest.raises(ValueError):
        tmp_kb.forget("k", scope="bogus")


def test_forget_is_scoped(tmp_kb):
    """Forgetting from one scope doesn't touch other scopes."""
    tmp_kb.remember("k", "p", scope="project")
    tmp_kb.remember("k", "u", scope="user")
    tmp_kb.forget("k", scope="project")
    assert tmp_kb.get("k", scope="project") is None
    assert tmp_kb.get("k", scope="user").value == "u"


# ---------------------------------------------------------------------------
# improve
# ---------------------------------------------------------------------------


def test_improve_appends_feedback(tmp_kb):
    tmp_kb.remember("k", "v")
    e = tmp_kb.improve("k", "try redis instead")
    assert e is not None
    assert e.feedback == ["try redis instead"]
    e2 = tmp_kb.improve("k", "ok actually keep it")
    assert e2.feedback == ["try redis instead", "ok actually keep it"]


def test_improve_missing_key_returns_none(tmp_kb):
    assert tmp_kb.improve("nope", "fix it") is None


def test_improve_bumps_updated_at(tmp_kb):
    tmp_kb.remember("k", "v")
    # Snapshot updated_at before improve() (the in-memory entry is
    # mutated in place; re-fetching would yield the same value).
    e_before = tmp_kb.get("k")
    ts_before = e_before.updated_at
    import time
    time.sleep(0.05)
    e_after = tmp_kb.improve("k", "note")
    assert e_after is not None
    # The improve() call bumped updated_at
    assert e_after.updated_at > ts_before


# ---------------------------------------------------------------------------
# list_keys
# ---------------------------------------------------------------------------


def test_list_keys_scoped(tmp_kb):
    tmp_kb.remember("a", 1)
    tmp_kb.remember("b", 2)
    tmp_kb.remember("c", 3, scope="user")
    assert tmp_kb.list_keys() == ["a", "b"]
    assert tmp_kb.list_keys("user") == ["c"]
    assert tmp_kb.list_keys("session") == []


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


def test_concurrent_remember_safe(tmp_kb):
    """20 threads each write 5 entries; all writes land and no key is lost."""
    def worker(i: int):
        for j in range(5):
            tmp_kb.remember(f"t{i}-k{j}", j)
    threads = [threading.Thread(target=worker, args=(i,))
               for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    keys = tmp_kb.list_keys()
    # 20 threads × 5 keys = 100 distinct
    assert len(keys) == 100
    assert keys[0].startswith("t")  # sanity
