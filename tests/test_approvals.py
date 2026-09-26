"""The approval channel: the question the gate could not ask.

`kairos/sentinel.py` says it outright — "ASK mean deny once an approval channel
exists". These tests pin the channel's contract on its own: a question becomes a
record, the record is visible while the call waits, the answer resolves it, and
an unanswered question resolves to *no* rather than to a parked run.

Every test runs its whole scenario inside **one** event loop. A future created on
one loop and awaited on another never resolves, which is a fine way to write a
test that fails for the wrong reason.
"""
from __future__ import annotations

import asyncio

from kairos.approvals import (DEFAULT_TIMEOUT_S, ApprovalChannel,
                              get_channel, set_channel)


class FakeBus:
    def __init__(self) -> None:
        self.events: list = []

    async def publish(self, msg) -> None:
        self.events.append(msg)

    def topics(self) -> list:
        return [m.topic for m in self.events]


def _ask_and_answer(ch, *, answer=True, remember=False,
                    tool="terminal", resource="git push",
                    reason="publishing the branch", project_id="p1"):
    """Ask, answer, and hand back both sides — in one loop."""
    async def main():
        task = asyncio.ensure_future(ch.request(
            tool=tool, resource=resource, reason=reason, project_id=project_id))
        await asyncio.sleep(0)
        pending = ch.pending()
        assert pending, "the request should be visible while it waits"
        entry = pending[0]
        resolved = ch.resolve(entry["id"], answer, remember=remember)
        return entry, resolved, await task

    return asyncio.run(main())


# ---------------------------------------------------------------------------
# answering
# ---------------------------------------------------------------------------

def test_an_approved_request_allows():
    ch = ApprovalChannel()
    entry, resolved, out = _ask_and_answer(ch, answer=True)

    assert resolved is True
    assert out["allow"] is True
    assert out["status"] == "allowed"
    assert entry["status"] == "pending", "what the UI saw while it waited"
    assert ch.pending() == [], "an answered question is not pending"
    assert ch.history()[-1]["status"] == "allowed"


def test_remember_travels_with_the_answer():
    ch = ApprovalChannel()
    _entry, _resolved, out = _ask_and_answer(ch, answer=True, remember=True)
    assert out["allow"] is True and out["remember"] is True


def test_a_denied_request_refuses():
    ch = ApprovalChannel()
    _entry, resolved, out = _ask_and_answer(ch, answer=False)
    assert resolved is True
    assert out["allow"] is False
    assert out["status"] == "denied"


def test_an_unanswered_request_times_out_to_no():
    """Nobody looking at the screen must not mean a parked run."""
    ch = ApprovalChannel(timeout_s=0.05)
    out = asyncio.run(ch.request(tool="browser", resource="http://x/"))
    assert out["status"] == "timeout"
    assert out["allow"] is False
    assert ch.pending() == []
    assert ch.history()[-1]["resource"] == "http://x/"


def test_answering_an_unknown_or_stale_request_is_false():
    ch = ApprovalChannel()

    async def main():
        assert ch.resolve("nope", True) is False
        task = asyncio.ensure_future(ch.request(tool="terminal", resource="x"))
        await asyncio.sleep(0)
        rid = ch.pending()[0]["id"]
        assert ch.resolve(rid, True) is True
        await task
        return ch.resolve(rid, True)

    assert asyncio.run(main()) is False, "one question, one answer"


def test_cancel_all_denies_everything_in_flight():
    ch = ApprovalChannel()

    async def main():
        tasks = [
            asyncio.ensure_future(ch.request(tool="terminal",
                                             resource="cmd" + str(i)))
            for i in range(3)
        ]
        await asyncio.sleep(0)
        assert len(ch.pending()) == 3, "all three questions are open"
        cancelled = ch.cancel_all("shutdown")
        results = await asyncio.gather(*tasks)
        return cancelled, results

    cancelled, results = asyncio.run(main())
    assert cancelled == 3
    assert all(r["allow"] is False for r in results)
    assert ch.pending() == []


# ---------------------------------------------------------------------------
# observability
# ---------------------------------------------------------------------------

def test_the_question_and_the_answer_are_published():
    bus = FakeBus()
    ch = ApprovalChannel(message_bus=bus)
    _ask_and_answer(ch, answer=True)

    assert bus.topics() == ["approval.requested", "approval.resolved"]
    first = bus.events[0].metadata
    assert first["tool"] == "terminal"
    assert first["resource"] == "git push"
    assert first["project_id"] == "p1"
    assert first["status"] == "pending"
    assert bus.events[1].metadata["status"] == "allowed"


def test_a_broken_bus_does_not_lose_the_question():
    class Broken:
        async def publish(self, msg):
            raise RuntimeError("bus is down")

    ch = ApprovalChannel(message_bus=Broken(), timeout_s=0.05)
    out = asyncio.run(ch.request(tool="terminal", resource="x"))
    assert out["status"] == "timeout"      # the timeout still applies
    assert ch.stats()["timeout"] == 1


def test_stats_count_every_outcome():
    ch = ApprovalChannel(timeout_s=0.05)
    _ask_and_answer(ch, answer=True, tool="t1", resource="a")
    _ask_and_answer(ch, answer=False, tool="t2", resource="b")
    asyncio.run(ch.request(tool="t3", resource="c"))     # times out

    stats = ch.stats()
    assert stats["pending"] == 0
    assert stats["allowed"] == 1 and stats["denied"] == 1 and stats["timeout"] == 1


def test_the_module_singleton_is_swappable():
    original = get_channel()
    try:
        mine = ApprovalChannel()
        set_channel(mine)
        assert get_channel() is mine
    finally:
        set_channel(original)


def test_default_timeout_is_long_enough_to_notice():
    assert DEFAULT_TIMEOUT_S >= 30
