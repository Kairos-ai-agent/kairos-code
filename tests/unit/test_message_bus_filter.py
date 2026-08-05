"""Tests for the project_id filter on MessageBus.get_history (D-01)."""

import pytest

from kairos.core.message_bus import Message, MessageBus

@pytest.mark.asyncio
async def test_get_history_filters_by_project_id_metadata():
    bus = MessageBus()
    await bus.publish(Message(sender="p1.x", topic="t", content=1,
                              metadata={"project_id": "p1"}))
    await bus.publish(Message(sender="p2.x", topic="t", content=2,
                              metadata={"project_id": "p2"}))
    await bus.publish(Message(sender="p1.y", topic="t", content=3,
                              metadata={"project_id": "p1"}))

    p1 = bus.get_history(limit=100, project_id="p1")
    p2 = bus.get_history(limit=100, project_id="p2")
    assert {m.content for m in p1} == {1, 3}
    assert {m.content for m in p2} == {2}

@pytest.mark.asyncio
async def test_get_history_falls_back_to_sender_prefix():
    bus = MessageBus()
    await bus.publish(Message(sender="legacy-id.team_leader", topic="t", content="x"))
    msgs = bus.get_history(limit=100, project_id="legacy-id")
    assert len(msgs) == 1
    assert msgs[0].content == "x"

@pytest.mark.asyncio
async def test_topic_and_project_can_combine():
    bus = MessageBus()
    await bus.publish(Message(sender="p1.x", topic="chat", content="c",
                              metadata={"project_id": "p1"}))
    await bus.publish(Message(sender="p1.x", topic="tool.call", content="tc",
                              metadata={"project_id": "p1"}))

    only_chat = bus.get_history(limit=100, project_id="p1", topic="chat")
    assert len(only_chat) == 1
    assert only_chat[0].content == "c"