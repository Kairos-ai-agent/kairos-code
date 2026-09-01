"""Daemon mode (R38.6.4) - long-running agent supervisor."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class DaemonSupervisor:
    HEARTBEAT_INTERVAL_S = 5.0

    def __init__(self, long_running_registry=None, message_bus=None,
                 work_dir=None):
        self._registry = long_running_registry
        self._bus = message_bus
        self._work_dir = Path(work_dir) if work_dir else Path.cwd()
        self._task = None
        self._stop = asyncio.Event()
        self.started_at = time.time()
        self.daemon_id = uuid.uuid4().hex[:8]
        self._session_heartbeats = {}

    def attach(self, registry, bus):
        self._registry = registry
        self._bus = bus

    async def start(self):
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(),
                                          name="daemon-supervisor")
        logger.info("Daemon supervisor started id=%s pid=%d",
                    self.daemon_id, os.getpid())
        try:
            (self._work_dir / ".kairos").mkdir(exist_ok=True)
            (self._work_dir / ".kairos" / "daemon.json").write_text(
                json.dumps({"id": self.daemon_id, "pid": os.getpid(),
                            "started_at": self.started_at}),
                encoding="utf-8")
        except Exception:
            pass

    async def stop(self):
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _loop(self):
        while not self._stop.is_set():
            try:
                await self._heartbeat()
            except Exception as exc:
                logger.debug("daemon heartbeat error: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(),
                                        timeout=self.HEARTBEAT_INTERVAL_S)
            except asyncio.TimeoutError:
                pass

    async def _heartbeat(self):
        now = time.time()
        if self._registry is not None:
            for h, rec in self._registry._subagents.items():
                self._session_heartbeats[h] = rec.started_at
            for jid, rec in self._registry._autonomous.items():
                self._session_heartbeats[jid] = rec["started_at"]
        if self._bus is not None:
            try:
                from kairos.core.message_bus import Message
                await self._bus.publish(Message(
                    sender="daemon", topic="daemon.heartbeat",
                    content="", msg_type="system",
                    metadata={
                        "daemon_id": self.daemon_id,
                        "uptime_s": now - self.started_at,
                        "active_subagents": (
                            len(self._registry._subagents)
                            if self._registry else 0),
                        "active_autonomous": (
                            len(self._registry._autonomous)
                            if self._registry else 0),
                    }))
            except Exception:
                pass

    def status_dict(self):
        return {
            "daemon_id": self.daemon_id,
            "pid": os.getpid(),
            "started_at": self.started_at,
            "uptime_s": time.time() - self.started_at,
            "active_subagents": (
                len(self._registry._subagents)
                if self._registry else 0),
            "active_autonomous": (
                len(self._registry._autonomous)
                if self._registry else 0),
            "session_heartbeats": dict(self._session_heartbeats),
        }

    def attach_session(self, session_id):
        if self._registry is None:
            return {"found": False, "error": "no registry"}
        if session_id in self._registry._subagents:
            return {"found": True, "kind": "subagent",
                    "data": self._registry.status_of(session_id)}
        if session_id in self._registry._autonomous:
            return {"found": True, "kind": "autonomous",
                    "data": self._registry.get_autonomous(session_id)}
        return {"found": False, "session_id": session_id}


_SUPERVISOR = None


def get_supervisor():
    return _SUPERVISOR


def set_supervisor(s):
    global _SUPERVISOR
    _SUPERVISOR = s
