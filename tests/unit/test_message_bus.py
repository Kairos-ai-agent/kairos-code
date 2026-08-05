"""Tests for kairos.core.message_bus."""

import asyncio

import pytest

from kairos.core.message_bus import Message, MessageBus


@pytest.mark.asyncio
async def test_publish_stores_in_history_and_respects_maxlen():
    bus = MessageBus()
    # Push more than maxlen to verify deque trims without error
    for i in range(1100):
        await bus.publish(Message(sender="test", topic="t", content=i))
    history = bus.get_history(limit=10000)
    assert len(history) == 1000  # bounded, no growth


@pytest.mark.asyncio
async def test_publish_invokes_all_listeners_even_when_one_fails():
    """A misbehaving listener must not block the others (regression: B-07)."""
    bus = MessageBus()

    seen = []

    async def good(msg):
        seen.append(("good", msg.content))

    async def bad(msg):
        raise RuntimeError("listener boom")

    bus.add_listener(good)
    bus.add_listener(bad)
    bus.add_listener(good)

    await bus.publish(Message(sender="s", topic="t", content=42))

    # Two good listeners recorded, both got the message.
    assert ("good", 42) in seen
    assert seen.count(("good", 42)) == 2


@pytest.mark.asyncio
async def test_listener_token_round_trip():
    """add_listener returns a token; remove_listener(token) drops it (B-03)."""
    bus = MessageBus()
    calls = []

    async def listener(msg):
        calls.append(msg.content)

    token = bus.add_listener(listener)
    await bus.publish(Message(sender="s", topic="t", content=1))
    assert calls == [1]

    bus.remove_listener(token)
    await bus.publish(Message(sender="s", topic="t", content=2))
    assert calls == [1]  # not invoked again after removal


@pytest.mark.asyncio
async def test_topic_broadcast_routes_to_subscribers_not_sender():
    bus = MessageBus()
    bus.subscribe("agent_a", "broadcast")
    bus.subscribe("agent_b", "broadcast")

    # Sender is agent_a, so agent_a queue must NOT receive, agent_b must.
    await bus.publish(Message(sender="agent_a", topic="broadcast", content="hi"))

    msg_b = await bus.receive("agent_b", timeout=1.0)
    msg_a = await bus.receive("agent_a", timeout=0.05)
    assert msg_b is not None and msg_b.content == "hi"
    assert msg_a is None


def test_sync_listener_is_supported():
    """A plain (non-async) function listener must also work."""
    import asyncio
    bus = MessageBus()
    seen = []
    bus.add_listener(lambda m: seen.append(m.content))
    asyncio.run(bus.publish(Message(sender="s", topic="t", content="ok")))
    assert seen == ["ok"]