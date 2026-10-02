"""Tests for the per-account IM store.

The point of these is isolation, so that is what they assert: the same
conversation identifier on two accounts must stay two separate bindings,
and one account's queued replies must never be visible to another
account's connector.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kairos.im_accounts import (IMAccountError, IMAccountStore,
                               validate_account_id)


def run(coro):
    """Drive one coroutine without depending on an async test plugin."""
    return asyncio.run(coro)


@pytest.fixture
def store(tmp_path: Path) -> IMAccountStore:
    s = IMAccountStore(db_path=tmp_path / "im.db")
    run(s.init())
    return s


# --------------------------------------------------------------------- accounts

def test_account_ids_are_restricted_on_purpose():
    """A loose id is how two connectors believe they are one account."""
    assert validate_account_id("  wx-main_01  ") == "wx-main_01"
    for bad in ("", "   ", "a/b", "a b", "a\\b", "../etc", "x" * 65):
        with pytest.raises(IMAccountError):
            validate_account_id(bad)


def test_upsert_and_list_round_trip(store: IMAccountStore):
    run(store.upsert_account("wx-a", name="A", secret="s-a"))
    run(store.upsert_account("wx-b", name="B", secret="s-b"))
    listed = run(store.list_accounts())
    assert sorted(a.account_id for a in listed) == ["wx-a", "wx-b"]


def test_listing_accounts_cannot_leak_a_secret(store: IMAccountStore):
    run(store.upsert_account("wx-a", name="A", secret="super-secret-value"))
    listed = run(store.list_accounts())
    dumped = repr(listed) + str([a.__dict__ for a in listed])
    assert "super-secret-value" not in dumped
    assert not hasattr(listed[0], "secret")
    # The secret is still reachable, but only by asking for it.
    assert run(store.get_secret("wx-a")) == "super-secret-value"


def test_upsert_without_a_secret_keeps_the_stored_one(store: IMAccountStore):
    """Renaming an account must not silently wipe its signing secret."""
    run(store.upsert_account("wx-a", name="A", secret="keep-me"))
    run(store.upsert_account("wx-a", name="A renamed", secret=""))
    assert run(store.get_account("wx-a")).name == "A renamed"
    assert run(store.get_secret("wx-a")) == "keep-me"


def test_accounts_can_be_disabled_without_being_deleted(store: IMAccountStore):
    run(store.upsert_account("wx-a", name="A", secret="s"))
    run(store.upsert_account("wx-a", name="A", secret="s", enabled=False))
    assert run(store.get_account("wx-a")).enabled is False


def test_deleting_an_account_takes_its_rows_with_it(store: IMAccountStore):
    run(store.upsert_account("wx-a", secret="s"))
    run(store.bind("wx-a", "chat-1", "proj-1"))
    run(store.enqueue("wx-a", "chat-1", "hello"))
    run(store.delete_account("wx-a"))
    assert run(store.get_account("wx-a")) is None
    assert run(store.list_bindings()) == []
    assert run(store.pending("wx-a")) == []


# --------------------------------------------------------------------- bindings

def test_same_chat_id_on_two_accounts_is_two_bindings(store: IMAccountStore):
    """The collision the single-key table would have hidden.

    Two accounts can legitimately see the same chat identifier (the same
    friend, or two groups whose opaque ids happen to match). Keyed by
    account + chat, they stay separate workspaces.
    """
    run(store.bind("wx-a", "shared-chat", "proj-a"))
    run(store.bind("wx-b", "shared-chat", "proj-b"))
    assert run(store.lookup("wx-a", "shared-chat")) == "proj-a"
    assert run(store.lookup("wx-b", "shared-chat")) == "proj-b"


def test_binding_one_chat_does_not_disturb_another(store: IMAccountStore):
    run(store.bind("wx-a", "chat-1", "proj-1"))
    run(store.bind("wx-a", "chat-2", "proj-2"))
    run(store.bind("wx-a", "chat-1", "proj-1b"))
    assert run(store.lookup("wx-a", "chat-1")) == "proj-1b"
    assert run(store.lookup("wx-a", "chat-2")) == "proj-2"


def test_lookup_of_an_unknown_conversation_is_none(store: IMAccountStore):
    assert run(store.lookup("wx-a", "never-seen")) is None


def test_bindings_can_be_filtered_by_account(store: IMAccountStore):
    run(store.bind("wx-a", "chat-1", "proj-1"))
    run(store.bind("wx-b", "chat-1", "proj-2"))
    only_a = run(store.list_bindings("wx-a"))
    assert [b["project_id"] for b in only_a] == ["proj-1"]
    assert len(run(store.list_bindings())) == 2


def test_unbinding_one_conversation_leaves_the_other(store: IMAccountStore):
    run(store.bind("wx-a", "chat-1", "proj-1"))
    run(store.bind("wx-a", "chat-2", "proj-2"))
    run(store.unbind("wx-a", "chat-1"))
    assert run(store.lookup("wx-a", "chat-1")) is None
    assert run(store.lookup("wx-a", "chat-2")) == "proj-2"


def test_binding_rejects_empty_parts(store: IMAccountStore):
    with pytest.raises(IMAccountError):
        run(store.bind("wx-a", "", "proj-1"))
    with pytest.raises(IMAccountError):
        run(store.bind("wx-a", "chat-1", "   "))
    with pytest.raises(IMAccountError):
        run(store.bind("bad/id", "chat-1", "proj-1"))


# ----------------------------------------------------------------------- outbox

def test_a_queue_belongs_to_one_account(store: IMAccountStore):
    """The isolation that matters most: no connector sees another's mail."""
    run(store.enqueue("wx-a", "chat-1", "for A"))
    run(store.enqueue("wx-b", "chat-1", "for B"))
    a = run(store.pending("wx-a"))
    b = run(store.pending("wx-b"))
    assert [m.text for m in a] == ["for A"]
    assert [m.text for m in b] == ["for B"]
    assert {m.account_id for m in a} == {"wx-a"}


def test_replies_carry_their_own_destination(store: IMAccountStore):
    """The reply knows which conversation to go back to."""
    run(store.enqueue("wx-a", "chat-7", "answer"))
    msg = run(store.pending("wx-a"))[0]
    assert (msg.chat_id, msg.account_id) == ("chat-7", "wx-a")


def test_delivery_is_at_least_once(store: IMAccountStore):
    """Collecting does not consume: only an ack does.

    A connector that dies between collecting and sending must find the
    reply again -- a repeated line is better than a lost one.
    """
    run(store.enqueue("wx-a", "chat-1", "once"))
    first = run(store.pending("wx-a"))
    again = run(store.pending("wx-a"))
    assert [m.id for m in first] == [m.id for m in again]
    assert run(store.ack("wx-a", [first[0].id])) == 1
    assert run(store.pending("wx-a")) == []
    # Acking twice is harmless.
    assert run(store.ack("wx-a", [first[0].id])) == 0


def test_pending_returns_oldest_first_and_respects_the_limit(
        store: IMAccountStore):
    for i in range(5):
        run(store.enqueue("wx-a", f"chat-{i}", f"message {i}"))
    got = run(store.pending("wx-a", limit=3))
    assert [m.text for m in got] == ["message 0", "message 1", "message 2"]


def test_pending_count_tracks_only_this_account(store: IMAccountStore):
    run(store.enqueue("wx-a", "chat-1", "one"))
    run(store.enqueue("wx-b", "chat-1", "two"))
    assert run(store.pending_count("wx-a")) == 1
    assert run(store.pending_count("wx-b")) == 1
    run(store.ack("wx-a", [run(store.pending("wx-a"))[0].id]))
    assert run(store.pending_count("wx-a")) == 0
    assert run(store.pending_count("wx-b")) == 1


def test_one_account_cannot_ack_another_accounts_replies(
        store: IMAccountStore):
    """An unscoped ack would silently drop mail that was never sent."""
    run(store.enqueue("wx-a", "chat-1", "for A"))
    run(store.enqueue("wx-b", "chat-1", "for B"))
    victim = run(store.pending("wx-a"))[0]
    # B tries to acknowledge A's reply by id.
    assert run(store.ack("wx-b", [victim.id])) == 0
    assert run(store.pending_count("wx-a")) == 1
    assert [m.text for m in run(store.pending("wx-a"))] == ["for A"]


def test_empty_replies_are_refused(store: IMAccountStore):
    """Sending nothing to a chat is a bug upstream, not a valid message."""
    with pytest.raises(IMAccountError):
        run(store.enqueue("wx-a", "chat-1", "   "))
    with pytest.raises(IMAccountError):
        run(store.enqueue("wx-a", "chat-1", ""))


def test_queue_needs_a_valid_account(store: IMAccountStore):
    with pytest.raises(IMAccountError):
        run(store.enqueue("bad/id", "chat-1", "hello"))
    with pytest.raises(IMAccountError):
        run(store.pending("bad/id"))
