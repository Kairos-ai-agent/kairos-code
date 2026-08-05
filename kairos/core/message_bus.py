"""Message Bus - pub/sub system for Agent communication."""

from __future__ import annotations

import asyncio
import collections
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class Message:
    """A message passed between agents."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    sender: str = ""
    receiver: str = ""  # empty = broadcast
    topic: str = ""
    content: Any = None
    msg_type: str = "text"  # text, task, result, error, status
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "sender": self.sender,
            "receiver": self.receiver,
            "topic": self.topic,
            "content": self.content,
            "msg_type": self.msg_type,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }

class MessageBus:
    """Publish/subscribe message bus for inter-agent communication.

    Agents subscribe to topics and receive messages published to those topics.
    Supports both broadcast and targeted messaging.
    """

    def __init__(self):
        self._subscribers: Dict[str, Dict[str, Callable]] = {}  # topic -> {agent_id: callback}
        self._agent_queues: Dict[str, asyncio.Queue] = {}  # agent_id -> queue
        # O(1) append/pop; bounded so unbounded growth can't OOM the process.
        self._history: Deque[Message] = collections.deque(maxlen=1000)
        # token -> listener; add_listener returns the token so callers can remove
        # deterministically instead of relying on object identity (which leaks
        # closures on WebSocket reconnects — see B-03).
        self._listeners: Dict[int, Callable] = {}
        self._listener_counter: int = 0
        self._listeners_lock = asyncio.Lock()

    def subscribe(self, agent_id: str, topic: str, callback: Optional[Callable] = None):
        """Subscribe an agent to a topic."""
        if topic not in self._subscribers:
            self._subscribers[topic] = {}
        self._subscribers[topic][agent_id] = callback

        if agent_id not in self._agent_queues:
            self._agent_queues[agent_id] = asyncio.Queue()

    def unsubscribe(self, agent_id: str, topic: str):
        """Unsubscribe an agent from a topic."""
        if topic in self._subscribers:
            self._subscribers[topic].pop(agent_id, None)

    def add_listener(self, listener: Callable) -> int:
        """Register a UI/persistence listener; returns a token for removal."""
        self._listener_counter += 1
        token = self._listener_counter
        self._listeners[token] = listener
        return token

    def remove_listener(self, token_or_listener):
        """Remove a listener by token (preferred) or by callable identity (legacy)."""
        if isinstance(token_or_listener, int):
            self._listeners.pop(token_or_listener, None)
            return
        # Legacy: remove by object identity (used by existing callers).
        stale = [tok for tok, fn in self._listeners.items() if fn is token_or_listener]
        for tok in stale:
            self._listeners.pop(tok, None)

    async def publish(self, message: Message):
        """Publish a message to the bus."""
        # Store in history (deque enforces maxlen, no manual slicing)
        self._history.append(message)

        # Snapshot listeners under a sync lock-style copy so a concurrent
        # remove_listener doesn't mutate mid-iteration.
        snapshot = list(self._listeners.values())

        # Fan-out listeners concurrently so one slow listener can't stall
        # the others (DB persist, multiple WS clients, etc.).
        if snapshot:
            results = await asyncio.gather(
                *(self._invoke(listener, message) for listener in snapshot),
                return_exceptions=True,
            )
            for listener, result in zip(snapshot, results):
                if isinstance(result, Exception):
                    logger.exception(
                        "Listener %r raised for topic=%s sender=%s",
                        listener, message.topic, message.sender,
                    )

        # Route to subscribers
        if message.receiver:
            # Targeted message
            queue = self._agent_queues.get(message.receiver)
            if queue:
                await queue.put(message)
        elif message.topic:
            # Topic-based broadcast
            subscribers = self._subscribers.get(message.topic, {})
            for agent_id, callback in subscribers.items():
                if agent_id != message.sender:  # Don't send back to sender
                    queue = self._agent_queues.get(agent_id)
                    if queue:
                        await queue.put(message)
                    if callback:
                        try:
                            if asyncio.iscoroutinefunction(callback):
                                await callback(message)
                            else:
                                callback(message)
                        except Exception:
                            logger.exception(
                                "Subscriber callback raised for topic=%s", message.topic
                            )

    @staticmethod
    async def _invoke(listener: Callable, message: Message) -> None:
        """Call a listener whether it's sync or async, isolating its errors."""
        try:
            if asyncio.iscoroutinefunction(listener):
                await listener(message)
            else:
                listener(message)
        except Exception:
            # Re-raise so the gather() above can route the failure to logger.exception.
            raise

    async def receive(self, agent_id: str, timeout: float = 30.0) -> Optional[Message]:
        """Receive next message for an agent (with timeout)."""
        queue = self._agent_queues.get(agent_id)
        if not queue:
            return None
        try:
            return await asyncio.wait_for(queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def get_history(self, limit: int = 50, topic: Optional[str] = None,
                     project_id: Optional[str] = None) -> List[Message]:
        """Get message history, optionally filtered by topic or project_id.

        `project_id` matches either:
          - the explicit project_id in metadata (preferred), or
          - the prefix in sender/receiver (legacy "<project_id>.<role>" ids).
        """
        def matches(m: Message) -> bool:
            if topic and m.topic != topic:
                return False
            if project_id:
                meta_pid = (m.metadata or {}).get("project_id")
                if meta_pid == project_id:
                    return True
                for field in (m.sender, m.receiver):
                    if field.startswith(project_id + "."):
                        return True
                return False
            return True

        msgs = [m for m in self._history if matches(m)]
        return msgs[-limit:]

    def get_agent_messages(self, agent_id: str, limit: int = 50) -> List[Message]:
        """Get all messages involving a specific agent."""
        msgs = [m for m in self._history if m.sender == agent_id or m.receiver == agent_id]
        return msgs[-limit:]