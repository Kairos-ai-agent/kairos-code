"""Autonomous worker - R38.6.4.

Consumes jobs from the LongRunningRegistry and runs them
with the configured budget. The worker is a background asyncio.Task
started during lifespan, polling the registry for queued jobs and
executing them. Each job runs in a fresh Coder run against
the project, with a turn/token/time budget and an optional quality
gate (shell command to run for "done" verification).

long-running-harness equivalent: ``long-running-harness --autonomous --autonomous-gate
"npm test" --autonomous-max-turns 20``.

Why process-local (not daemon): the long-running registry is
in-memory so the worker has to be in the same process. Scaling
to multi-worker needs the daemon (out of scope here). For
single-process dev this gives us the autonomous loop in 200 lines.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class AutonomousWorker:
    """Background worker that runs ``/autonomous`` jobs end-to-end."""

    def __init__(self, orchestrator=None, long_running_registry=None):
        self._orch = orchestrator
        self._registry = long_running_registry
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._running_jobs: Dict[str, asyncio.Task] = {}

    def attach(self, orchestrator, registry):
        self._orch = orchestrator
        self._registry = registry

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(),
                                          name="autonomous-worker")
        logger.info("Autonomous worker started (R38.6 \u00a734)")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        for t in self._running_jobs.values():
            t.cancel()
        self._running_jobs.clear()
        logger.info("Autonomous worker stopped")

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                if self._registry is not None:
                    for jid, rec in list(self._registry._autonomous.items()):
                        if rec.get("status") != "running":
                            continue
                        if jid in self._running_jobs:
                            continue
                        task = asyncio.create_task(
                            self._run_one(jid, rec),
                            name=f"autonomous-{jid}",
                        )
                        self._running_jobs[jid] = task
            except Exception as exc:
                logger.debug("autonomous worker loop error: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass

    async def _run_one(self, job_id: str, rec: Dict[str, Any]) -> None:
        project_id = rec.get("project_id", "")
        requirement = None
        try:
            requirement = await self._fetch_requirement(job_id)
        except Exception:
            requirement = None
        if not requirement:
            self._registry.update_autonomous(
                job_id, status="failed",
                error="could not fetch requirement from bus")
            return
        max_turns = rec.get("max_turns", 20)
        time_budget_s = rec.get("time_budget_s", 1800)
        gate = rec.get("gate", "")
        started = time.time()
        try:
            result = await asyncio.wait_for(
                self._run_agent(project_id, requirement, max_turns,
                                  job_id, time_budget_s),
                timeout=time_budget_s,
            )
        except asyncio.TimeoutError:
            result = "timed out"
            status = "timeout"
        except Exception as exc:
            result = f"{type(exc).__name__}: {exc}"
            status = "failed"
        else:
            status = "completed"
        gate_passed = True
        if gate and status == "completed":
            gate_passed = await self._run_gate(project_id, gate)
        self._registry.update_autonomous(
            job_id, status=status, gate_passed=gate_passed,
            result_summary=str(result)[:500],
            finished_at=time.time(),
            wall_seconds=time.time() - started,
        )
        self._running_jobs.pop(job_id, None)

    async def _fetch_requirement(self, job_id: str) -> Optional[str]:
        if self._orch is None or self._orch.message_bus is None:
            return None
        try:
            history = await self._orch.message_bus.recent(limit=200)
        except Exception:
            return None
        for msg in history:
            if (msg.get("topic") == "autonomous.submitted"
                    and msg.get("metadata", {}).get("job_id") == job_id):
                return msg.get("content", "")
        return None

    async def _run_agent(self, project_id, requirement, max_turns,
                          job_id, time_budget_s):
        if self._orch is None:
            return "orchestrator not attached"
        project = self._orch.get_project(project_id)
        if project is None or project.coder is None:
            return "project / coder not available"
        from kairos.agents.base import AgentTask
        task = AgentTask(
            id="auto-" + job_id,
            title="Autonomous task",
            description=requirement, instruction=requirement,
            context={"project_id": project_id, "autonomous": True,
                      "job_id": job_id},
        )
        try:
            result = await project.coder.run(task)
        except Exception as exc:
            return f"coder.run failed: {exc}"
        turns = getattr(project.coder, "current_turn", 0)
        self._registry.update_autonomous(job_id, turns_used=int(turns))
        return getattr(result, "text", str(result))

    async def _run_gate(self, project_id: str, gate: str) -> bool:
        if self._orch is None:
            return False
        project = self._orch.get_project(project_id)
        if project is None:
            return False
        work_dir = getattr(project, "work_dir", None) or "."
        try:
            proc = await asyncio.create_subprocess_shell(
                gate, cwd=work_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                await asyncio.wait_for(proc.wait(), timeout=300)
            except asyncio.TimeoutError:
                proc.kill()
                return False
            return proc.returncode == 0
        except Exception as exc:
            logger.warning("gate %r failed: %s", gate, exc)
            return False


_WORKER: Optional[AutonomousWorker] = None


def get_worker() -> AutonomousWorker:
    global _WORKER
    if _WORKER is None:
        _WORKER = AutonomousWorker()
    return _WORKER


def set_worker(w: AutonomousWorker) -> None:
    global _WORKER
    _WORKER = w
