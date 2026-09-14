"""R38.8 — the agent's process must survive a refresh.

``load_messages(chat_only=True)`` keeps only ``Persistence.CHAT_TOPICS``. Tool
calls, their results, the agent's thinking and task errors all rendered live
over the WebSocket, but they were missing from that whitelist — so the moment a
user refreshed or reopened a session the whole process vanished and the agent
looked like it had done nothing at all.
"""

import pytest

from kairos.core.message_bus import Message
from kairos.core.persistence import Persistence

#: What the thread must keep.
PROCESS_TOPICS = ('agent.thinking', 'tool.call', 'tool.result', 'task.error')

#: What the thread must still keep out — streaming deltas and per-turn chatter
#: would otherwise flood the newest-N window (the reason the whitelist exists).
NOISE_TOPICS = ('stream.chunk', 'agent.progress', 'loop.coder_started')


@pytest.fixture
def db(tmp_path):
    """Fresh Persistence rooted in tmp_path so we never touch the real DB."""
    return Persistence(tmp_path / 'test.db')


def _add(store: Persistence, topic: str, content: str = 'x') -> None:
    store.save_message(Message(sender='agent', receiver='user', topic=topic,
                               content=content,
                               metadata={'project_id': 'p1'}))


def test_process_topics_are_in_the_chat_whitelist():
    for topic in PROCESS_TOPICS:
        assert topic in Persistence.CHAT_TOPICS, topic


@pytest.mark.parametrize('topic', PROCESS_TOPICS)
def test_process_messages_survive_a_refresh(db, topic):
    _add(db, 'user.chat', 'please do the thing')
    _add(db, topic, f'{topic} payload')
    _add(db, 'agent.response', 'done')

    topics = [r['topic'] for r in db.load_messages(project_id='p1',
                                                   chat_only=True)]
    assert topic in topics, f'{topic} vanished on refresh'
    # And the conversation itself is still there.
    assert 'user.chat' in topics
    assert 'agent.response' in topics


def test_noise_topics_stay_out_of_the_thread(db):
    for topic in NOISE_TOPICS:
        _add(db, topic, 'noise')
    _add(db, 'user.chat', 'hi')

    rows = db.load_messages(project_id='p1', chat_only=True)
    assert [r['topic'] for r in rows] == ['user.chat']
