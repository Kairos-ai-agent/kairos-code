"""WebSocket routes for real-time communication."""

from __future__ import annotations

import asyncio
import logging
from typing import Dict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api.deps import orchestrator

router = APIRouter()
log = logging.getLogger(__name__)

class WSClient:
    """Per-client WebSocket state."""

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.last_seen_msg_ts: float = 0.0
        self.alive = True

_clients: Dict[str, WSClient] = {}

# Topics that should trigger an immediate `agent_update` push to every
# client. These are emitted whenever an agent's observable state changes
# (tool use, task progress, chat, error).
AGENT_STATE_TRIGGERS = {
    "tool.call", "tool.result", "task.result", "task.error",
    "task.timeout", "agent.chat", "agent.thinking", "agent.response",
    "dispatch.warning", "project.plan", "project.status_changed",
}

# How often to push `agent_update` as a heartbeat, even when no trigger
# fires. Keeps badges moving and catches missed events from any source.
AGENT_UPDATE_INTERVAL_S = 2.0

@router.websocket("/collaboration")
async def collaboration_ws(websocket: WebSocket):
    """WebSocket endpoint for real-time collaboration updates."""
    await websocket.accept()
    client_id = f"client_{id(websocket)}"
    client = WSClient(websocket)
    _clients[client_id] = client

    # Register MessageBus listener for this client; capture the token so
    # we can deterministically remove it on disconnect (avoids the closure-
    # identity leak that grew _listeners unbounded across reconnects).
    async def listener(msg):
        if not client.alive:
            return
        try:
            await websocket.send_json({
                "type": "activity",
                "message": msg.to_dict(),
            })
        except Exception:
            client.alive = False
            return
        # Forward agent state changes immediately — don't wait for the
        # heartbeat. Otherwise the badge freezes mid-tool and the UI
        # looks stuck.
        if msg.topic in AGENT_STATE_TRIGGERS:
            try:
                orchestrator.refresh_all_agents()
                await websocket.send_json({
                    "type": "agent_update",
                    "agents": orchestrator.get_all_agent_states(),
                })
            except Exception:
                client.alive = False

    listener_token = orchestrator.message_bus.add_listener(listener)

    try:
        # Send initial state
        orchestrator.refresh_all_agents()
        msgs = orchestrator.message_bus.get_history(limit=100)
        if msgs:
            client.last_seen_msg_ts = msgs[-1].timestamp
        await websocket.send_json({
            "type": "init",
            "agents": orchestrator.get_all_agent_states(),
            "messages": [m.to_dict() for m in msgs],
        })

        # Heartbeat + periodic agent_update loop. Replaces the old
        # "only push on client message" behavior, which meant the UI
        # only updated when the user typed something.
        while client.alive:
            try:
                # wait for either a client frame or our push interval
                await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=AGENT_UPDATE_INTERVAL_S,
                )
                # Client said something — push fresh state immediately.
                if client.alive:
                    orchestrator.refresh_all_agents()
                    await websocket.send_json({
                        "type": "agent_update",
                        "agents": orchestrator.get_all_agent_states(),
                    })
            except asyncio.TimeoutError:
                # No client traffic — push a heartbeat agent_update so
                # the UI keeps moving even when no triggers fired.
                if client.alive:
                    try:
                        orchestrator.refresh_all_agents()
                        await websocket.send_json({
                            "type": "agent_update",
                            "agents": orchestrator.get_all_agent_states(),
                        })
                    except Exception:
                        break
                # Also send a ping so the client knows we're alive.
                if client.alive:
                    try:
                        await websocket.send_json({"type": "ping"})
                    except Exception:
                        break
            except WebSocketDisconnect:
                break

    except WebSocketDisconnect:
        pass
    finally:
        client.alive = False
        # Drop the listener by token so reconnects don't accumulate closures.
        orchestrator.message_bus.remove_listener(listener_token)
        _clients.pop(client_id, None)

async def broadcast(message: dict):
    """Broadcast to all alive clients."""
    dead = []
    for cid, client in _clients.items():
        if not client.alive:
            dead.append(cid)
            continue
        try:
            await client.ws.send_json(message)
        except Exception:
            client.alive = False
            dead.append(cid)
    for cid in dead:
        _clients.pop(cid, None)
