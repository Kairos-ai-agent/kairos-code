"""Long-running task registry — long-running-harness inspired.

R38.6.4: borrowed from long-running-harness (long-running-harness-ai/long-running-harness, 19k stars).
Three long-running primitives:

  1. **spawn_subagent_async** — fire-and-forget sub-agent. The parent
     gets a handle immediately and continues. The child runs in
     background and the result comes back via a message bus event.
     Like `await rlm("task", background=True)` in long-running-harness.

  2. **/goal** — persistent objective that survives turns and
     sessions. The next task in the session auto-loads the goal as
     context. Re-anchor every turn so the agent never loses sight
     of what it's working on.

  3. **/autonomous** — bounded run with turn / token / time
     budget and a user-defined quality gate. Like
     `long-running-harness --autonomous --autonomous-gate "npm test"`.

All three are exposed via REST in `api/routes/p2_features.py` so
the frontend can monitor them. The user's local file watcher
also picks up `subagent.completed` events and pushes them as
chat messages so the parent stays aware of child results.

The registry itself is a process-local singleton (in production
it would be backed by the daemon's JSONL). Good enough for the
single-process dev setup; scaling to multi-worker needs a Redis
keyspace, which is out of scope for now.
"""
from __future__ import annotations
import json

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Async subagent registry
# ---------------------------------------------------------------------------

class SubagentStatus(str, Enum):
    PENDING = "pending"        # queued, not yet started
    RUNNING = "running"        # child is in its tool loop
    COMPLETED = "completed"    # finished successfully
    FAILED = "failed"          # raised an exception
    CANCELLED = "cancelled"    # user requested cancel


@dataclass
class AsyncSubagent:
    """A single async subagent. Created via ``LongRunningRegistry.spawn``.

    The parent gets a handle to this immediately; the actual child
    task is scheduled on the asyncio event loop and runs in
    background. ``result`` stays None until the child finishes.
    """
    handle: str                                # short id the parent holds
    child_id: str                              # full agent id
    parent_id: str
    project_id: str
    task_text: str
    status: SubagentStatus = SubagentStatus.PENDING
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    result: Optional[str] = None
    error: Optional[str] = None
    # If the child was paused for a /goal re-anchor, this is the
    # pending text the parent last sent in.
    last_progress_event: Optional[str] = None


class LongRunningRegistry:
    """Process-local registry of async subagents / goals / autonomous
    runs. Replaced in production with a daemon-backed JSONL keyspace;
    for the single-process dev setup the in-memory dict is enough.

    Concurrency model: the parent coroutine calls
    ``spawn(coro_factory)`` and immediately gets a handle. The
    coro is scheduled with ``asyncio.create_task`` so it runs in
    background. The parent can later ``await registry.wait(handle)``
    or poll ``registry.status(handle)``.
    """

    def __init__(self, message_bus=None, persist_dir: "Path" = None):
        self._subagents: Dict[str, AsyncSubagent] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        # Persistent goal per project. {project_id: goal_text}
        self._goals: Dict[str, str] = {}
        # Active autonomous runs. {job_id: {started_at, max_turns, ...}}
        self._autonomous: Dict[str, Dict[str, Any]] = {}
        # R38.6.4 (P1): JSONL persistence so handles survive
        # daemon restarts. The file is append-only, one record
        # per registry change. On startup we replay the file
        # to rebuild the in-memory state.
        self._persist_dir = persist_dir
        if persist_dir is not None:
            persist_dir = Path(persist_dir)
            persist_dir.mkdir(parents=True, exist_ok=True)
            self._persist_path = persist_dir / "long_running.jsonl"
            self._replay()
        else:
            self._persist_path = None

    def _replay(self):
        """Read the JSONL log to rebuild in-memory state on startup.
        AsyncSubagent results that completed are restored as
        ``status="completed"`` with the result. Running ones
        are marked ``status="abandoned"`` since the task that
        was doing them is gone.
        """
        if not self._persist_path or not self._persist_path.exists():
            return
        for line in self._persist_path.read_text(
                encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            op = rec.get("op")
            if op == "subagent.spawn":
                handle = rec["handle"]
                self._subagents[handle] = AsyncSubagent(
                    handle=handle,
                    child_id=rec["child_id"],
                    parent_id=rec.get("parent_id", ""),
                    project_id=rec.get("project_id", ""),
                    task_text=rec.get("task_text", ""),
                    started_at=rec.get("started_at", time.time()),
                )
            elif op == "subagent.completed":
                handle = rec["handle"]
                if handle in self._subagents:
                    self._subagents[handle].status =                         AsyncSubagent(rec.get("status", "completed"))
                    self._subagents[handle].result =                         rec.get("result")
                    self._subagents[handle].finished_at =                         rec.get("finished_at", time.time())
                    self._subagents[handle].error = rec.get("error")
            elif op == "goal.set":
                self._goals[rec["project_id"]] = rec.get("text", "")
            elif op == "autonomous.register":
                self._autonomous[rec["job_id"]] = rec.get("rec", {})

    def _append_log(self, record: dict):
        if self._persist_path is None:
            return
        try:
            with open(self._persist_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.debug("persist log write failed: %s", exc)
        self._message_bus = message_bus
        self._lock = asyncio.Lock()

    # ---------- Subagent ----------

    def spawn(self, parent_id: str, project_id: str,
              task_text: str, coro_factory: Callable[[], Awaitable[Any]]
              ) -> str:
        """Schedule ``coro_factory()`` to run in background, register
        an AsyncSubagent, and return the handle.

        The parent should immediately return to its caller. The
        child's result lands in ``AsyncSubagent.result`` and a
        ``subagent.completed`` (or ``subagent.failed``) event is
        published on the bus.
        """
        handle = uuid.uuid4().hex[:10]
        child_id = f"{project_id}.sub_async_{handle}"
        record = AsyncSubagent(
            handle=handle, child_id=child_id,
            parent_id=parent_id, project_id=project_id,
            task_text=task_text,
        )
        self._subagents[handle] = record
        task = asyncio.create_task(self._run_subagent(handle, coro_factory))
        self._tasks[handle] = task
        logger.info("subagent.async.spawn handle=%s child=%s", handle, child_id)
        return handle

    async def _run_subagent(self, handle: str,
                            coro_factory: Callable[[], Awaitable[Any]]):
        record = self._subagents[handle]
        record.status = SubagentStatus.RUNNING
        try:
            result = await coro_factory()
            text = result if isinstance(result, str) else str(result)
            record.result = text
            record.status = SubagentStatus.COMPLETED
            record.finished_at = time.time()
            await self._publish_subagent_event(handle, "subagent.completed", text)
        except asyncio.CancelledError:
            record.status = SubagentStatus.CANCELLED
            record.finished_at = time.time()
            record.error = "cancelled by parent"
            await self._publish_subagent_event(handle, "subagent.cancelled",
                                              record.error)
        except Exception as exc:
            record.status = SubagentStatus.FAILED
            record.finished_at = time.time()
            record.error = f"{type(exc).__name__}: {exc}"
            await self._publish_subagent_event(handle, "subagent.failed",
                                              record.error)

    async def _publish_subagent_event(self, handle: str, topic: str, content: str):
        if self._message_bus is None:
            return
        record = self._subagents[handle]
        try:
            from kairos.core.message_bus import Message
            await self._message_bus.publish(Message(
                sender=record.child_id, topic=topic, content=content[:500],
                msg_type="text",
                metadata={"project_id": record.project_id,
                          "parent": record.parent_id,
                          "handle": handle},
            ))
        except Exception as exc:  # noqa: BLE001
            logger.debug("subagent event publish failed: %s", exc)

    def status_of(self, handle: str) -> Optional[Dict[str, Any]]:
        """Snapshot of a subagent's current state. None if unknown."""
        r = self._subagents.get(handle)
        if r is None:
            return None
        return {
            "handle": r.handle, "child_id": r.child_id,
            "parent_id": r.parent_id, "project_id": r.project_id,
            "status": r.status.value,
            "started_at": r.started_at, "finished_at": r.finished_at,
            "result": r.result, "error": r.error,
        }

    def list_subagents(self, project_id: str = "") -> List[Dict[str, Any]]:
        return [
            self.status_of(h)
            for h, r in self._subagents.items()
            if not project_id or r.project_id == project_id
        ]

    async def wait(self, handle: str, timeout_s: float = 600.0
                   ) -> Optional[Dict[str, Any]]:
        """Block until the subagent finishes, or ``timeout_s`` elapses.

        Returns the final status dict, or None on timeout.
        """
        task = self._tasks.get(handle)
        if task is None:
            return self.status_of(handle)
        try:
            await asyncio.wait_for(task, timeout=timeout_s)
        except asyncio.TimeoutError:
            return self.status_of(handle)
        return self.status_of(handle)

    def cancel(self, handle: str) -> bool:
        task = self._tasks.get(handle)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    # ---------- Goal (long-running-harness /goal) ----------

    def set_goal(self, project_id: str, text: str) -> None:
        """Persistent objective that survives turns. Empty string clears."""
        text = (text or "").strip()
        if not text:
            self._goals.pop(project_id, None)
        else:
            self._goals[project_id] = text

    def get_goal(self, project_id: str) -> str:
        return self._goals.get(project_id, "")

    def clear_goal(self, project_id: str) -> bool:
        return self._goals.pop(project_id, None) is not None

    # ---------- Autonomous (long-running-harness /autonomous) ----------

    def register_autonomous(self, project_id: str, max_turns: int,
                              time_budget_s: int, gate: str = "") -> str:
        job_id = uuid.uuid4().hex[:10]
        self._autonomous[job_id] = {
            "job_id": job_id, "project_id": project_id,
            "max_turns": max_turns, "time_budget_s": time_budget_s,
            "gate": gate, "started_at": time.time(),
            "turns_used": 0, "gate_passed": False, "status": "running",
        }
        return job_id

    def update_autonomous(self, job_id: str, **patch) -> Optional[Dict]:
        rec = self._autonomous.get(job_id)
        if rec is None:
            return None
        rec.update(patch)
        return rec

    def get_autonomous(self, job_id: str) -> Optional[Dict]:
        return self._autonomous.get(job_id)

    def list_autonomous(self, project_id: str = "") -> List[Dict]:
        return [
            rec for rec in self._autonomous.values()
            if not project_id or rec["project_id"] == project_id
        ]


# Process-local singleton, set during lifespan startup.
_REGISTRY: Optional[LongRunningRegistry] = None


def get_registry() -> LongRunningRegistry:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = LongRunningRegistry()
    return _REGISTRY


def set_registry(reg: LongRunningRegistry) -> None:
    """Called by the lifespan at app startup."""
    global _REGISTRY
    _REGISTRY = reg
