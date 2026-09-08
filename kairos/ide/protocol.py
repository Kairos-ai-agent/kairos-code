"""IDE Integration Protocol - JSON-RPC interface for IDE plugins.

Provides a standard protocol for:
- VS Code extension communication
- JetBrains plugin integration
- Real-time progress updates
- Human intervention via IDE UI
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class IDEMessageType(str, Enum):
    """Message types for IDE communication."""
    # Agent lifecycle
    AGENT_STARTED = "agent/started"
    AGENT_STOPPED = "agent/stopped"
    ROUND_START = "round/start"
    ROUND_COMPLETE = "round/complete"
    
    # Progress
    PROGRESS_UPDATE = "progress/update"
    TOKEN_USAGE = "token/usage"
    
    # Human intervention
    PLAN_PENDING = "plan/pending"
    PLAN_APPROVED = "plan/approved"
    PLAN_REJECTED = "plan/rejected"
    HUMAN_MESSAGE = "human/message"
    
    # Results
    LOOP_COMPLETED = "loop/completed"
    LOOP_FAILED = "loop/failed"
    
    # Diagnostics
    DIAGNOSTIC = "diagnostic"
    NOTE = "note"


@dataclass
class IDEMessage:
    """Message sent to/from IDE."""
    message_type: IDEMessageType
    project_id: str
    session_id: str
    payload: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    sequence_id: int = 0
    
    def to_json(self) -> str:
        return json.dumps({
            "type": self.message_type.value,
            "project_id": self.project_id,
            "session_id": self.session_id,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "sequence": self.sequence_id,
        })
    
    @classmethod
    def from_json(cls, data: str) -> "IDEMessage":
        parsed = json.loads(data)
        return cls(
            message_type=IDEMessageType(parsed["type"]),
            project_id=parsed["project_id"],
            session_id=parsed["session_id"],
            payload=parsed["payload"],
            timestamp=parsed.get("timestamp", time.time()),
            sequence_id=parsed.get("sequence", 0),
        )


class IDEProtocolHandler:
    """Handles IDE protocol messages."""
    
    def __init__(self):
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}  # project_id -> queues
        self._sequence: int = 0
        
    def register_client(self, project_id: str) -> asyncio.Queue:
        """Register an IDE client (extension/plugin) for a project."""
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(project_id, []).append(queue)
        return queue
    
    def unregister_client(self, project_id: str, queue: asyncio.Queue) -> None:
        """Unregister a client."""
        if project_id in self._subscribers:
            try:
                self._subscribers[project_id].remove(queue)
            except ValueError:
                pass
    
    async def publish(self, project_id: str, message: IDEMessage) -> None:
        """Publish a message to all registered clients."""
        message.sequence_id = self._sequence
        self._sequence += 1
        
        if project_id not in self._subscribers:
            return
        
        payload_str = message.to_json()
        for queue in self._subscribers[project_id]:
            try:
                queue.put_nowait(payload_str)
            except asyncio.QueueFull:
                logger.warning("IDE message queue full, dropping: %s", message.message_type)
    
    async def receive(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Receive a message from IDE (blocking)."""
        queues = self._subscribers.get(project_id, [])
        if not queues:
            return None
        
        # Wait for message from any queue
        while True:
            for queue in queues:
                try:
                    return queue.get_nowait()
                except asyncio.QueueEmpty:
                    continue
            await asyncio.sleep(0.1)
    
    def send_plan_pending(self, project_id: str, session_id: str, 
                          plan_text: str) -> None:
        """Notify IDE that a plan needs approval."""
        msg = IDEMessage(
            message_type=IDEMessageType.PLAN_PENDING,
            project_id=project_id,
            session_id=session_id,
            payload={"plan_text": plan_text},
        )
        asyncio.create_task(self.publish(project_id, msg))
    
    def send_progress(self, project_id: str, session_id: str,
                      round_no: int, score: int, message: str) -> None:
        """Send progress update."""
        msg = IDEMessage(
            message_type=IDEMessageType.PROGRESS_UPDATE,
            project_id=project_id,
            session_id=session_id,
            payload={
                "round": round_no,
                "score": score,
                "message": message,
            },
        )
        asyncio.create_task(self.publish(project_id, msg))
    
    def send_loop_complete(self, project_id: str, session_id: str,
                           result: Dict[str, Any]) -> None:
        """Send loop completion."""
        msg = IDEMessage(
            message_type=IDEMessageType.LOOP_COMPLETED,
            project_id=project_id,
            session_id=session_id,
            payload=result,
        )
        asyncio.create_task(self.publish(project_id, msg))


# WebSocket endpoint for browser-based IDEs
async def handle_websocket(websocket, project_id: str, handler: IDEProtocolHandler):
    """Handle WebSocket connection from browser extension."""
    queue = handler.register_client(project_id)
    try:
        async for message in websocket:
            # Parse and route IDE message
            try:
                data = json.loads(message)
                # Echo back or process based on type
                await websocket.send(json.dumps({
                    "type": "ack",
                    "received": data,
                }))
            except json.JSONDecodeError:
                pass
    finally:
        handler.unregister_client(project_id, queue)
