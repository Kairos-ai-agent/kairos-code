"""Minimal skeleton driver: run a worker, verify it, package the outcome.

This is intentionally *not* a new main loop. ``run_task`` is a two-step
``worker -> verifier`` with optional bounded retry; it leaves the existing
``kairos.loop.loop_runner.run_loop`` untouched and unwired. It exists so a
non-code task can be exercised end to end without the code loop.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from kairos.skeleton.contracts import Task, Verdict, Verifier, Worker, WorkerResult, Workspace


@dataclass
class SkeletonRun:
    """Everything one ``run_task`` produced."""

    task: Task
    result: WorkerResult
    verdict: Verdict
    attempts: int = 1

    @property
    def passed(self) -> bool:
        return self.verdict.passed is True

    @property
    def blocked_on_human(self) -> bool:
        return self.verdict.requires_human

    def to_dict(self):
        return {
            "task": self.task.id,
            "attempts": self.attempts,
            "result": self.result.to_dict(),
            "verdict": self.verdict.to_dict(),
        }


async def run_once(
    worker: Worker, workspace: Workspace, task: Task, verifier: Verifier
) -> SkeletonRun:
    """One worker pass followed by one verification."""
    result = await worker.run(workspace, task)
    verdict = await verifier.verify(workspace, task, result)
    return SkeletonRun(task=task, result=result, verdict=verdict, attempts=1)


async def run_task(
    worker: Worker,
    workspace: Workspace,
    task: Task,
    verifier: Verifier,
    *,
    max_attempts: int = 1,
    revise: Optional[Callable[[Task, Verdict], Optional[Task]]] = None,
) -> SkeletonRun:
    """Run ``worker`` against ``workspace`` and verify, retrying up to
    ``max_attempts`` when the verdict fails.

    ``revise(task, verdict) -> Task`` (optional) folds the verdict back into
    the next attempt's task. A pending-human verdict stops the loop (there is
    nothing to retry automatically). With ``max_attempts=1`` this is exactly
    :func:`run_once`.
    """
    max_attempts = max(1, int(max_attempts))
    run: Optional[SkeletonRun] = None
    current = task
    for attempt in range(1, max_attempts + 1):
        result = await worker.run(workspace, current)
        verdict = await verifier.verify(workspace, current, result)
        run = SkeletonRun(task=current, result=result, verdict=verdict, attempts=attempt)
        if verdict.passed is True or verdict.requires_human:
            break
        if revise is not None:
            revised = revise(current, verdict)
            if revised is not None:
                current = revised
    assert run is not None
    return run
