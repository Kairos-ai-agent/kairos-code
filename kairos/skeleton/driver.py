"""Minimal skeleton driver: run a worker, verify it, package the outcome.

This is intentionally *not* a new main loop. ``run_task`` is a two-step
``worker -> verifier`` with optional bounded retry; it leaves the existing
``kairos.loop.loop_runner.run_loop`` untouched. It exists so a non-code task can
be exercised end to end without the code loop.

A :class:`SkeletonRun` is inspectable, **persistable** (JSON under a run
directory) and **observable** (the driver publishes its nodes to the existing
MessageBus when one is supplied). A run whose verdict is ``requires_human`` is
not a dead end: :func:`resume_task` folds a human decision back in and either
marks the run passed or re-runs the worker.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from kairos.skeleton.contracts import (
    Task,
    Verdict,
    Verifier,
    Worker,
    WorkerResult,
    Workspace,
)

logger = logging.getLogger(__name__)

#: Topic prefix the driver publishes run nodes under, when a bus is supplied.
RUN_TOPIC = "skeleton.run"


@dataclass
class SkeletonRun:
    """Everything one ``run_task`` produced.

    ``outcome`` summarizes the verdict in one word:

    * ``"passed"``      -- the verifier returned ``passed=True``.
    * ``"failed"``      -- the verifier returned ``passed=False`` (retried up
      to ``max_attempts``, then given up on).
    * ``"needs_human"`` -- the verdict blocks on a human (``requires_human``).
    * ``"undecided"``   -- the verifier abstained (``passed=None``); no
      verifier could decide. The driver does **not** retry an abstention.
    """

    task: Task
    result: WorkerResult
    verdict: Verdict
    attempts: int = 1
    run_id: str = ""
    history: list = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.run_id:
            self.run_id = uuid.uuid4().hex[:8]

    @property
    def passed(self) -> bool:
        return self.verdict.passed is True

    @property
    def failed(self) -> bool:
        return self.verdict.passed is False

    @property
    def blocked_on_human(self) -> bool:
        return self.verdict.requires_human

    @property
    def undecided(self) -> bool:
        """The verifier abstained: no decision, and not a human gate either."""
        return self.verdict.passed is None and not self.verdict.requires_human

    @property
    def outcome(self) -> str:
        if self.verdict.requires_human:
            return "needs_human"
        if self.verdict.passed is True:
            return "passed"
        if self.verdict.passed is False:
            return "failed"
        return "undecided"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "outcome": self.outcome,
            "attempts": self.attempts,
            "task": self.task.to_dict(),
            "result": self.result.to_dict(),
            "verdict": self.verdict.to_dict(),
            "history": list(self.history),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SkeletonRun":
        data = data or {}
        return cls(
            task=Task.from_dict(data.get("task")),
            result=WorkerResult.from_dict(data.get("result")),
            verdict=Verdict.from_dict(data.get("verdict")),
            attempts=int(data.get("attempts", 1)),
            run_id=data.get("run_id", ""),
            history=list(data.get("history") or []),
        )

    # -- persistence ------------------------------------------------------
    def save(self, run_dir) -> Path:
        """Write this run as JSON under ``run_dir``; return the file path."""
        run_dir = Path(run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / f"skeleton-run-{self.run_id}.json"
        path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    @classmethod
    def load(cls, path) -> "SkeletonRun":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


async def _publish(bus, topic: str, run: SkeletonRun) -> None:
    """Best-effort publish of a run node to the message bus (never fatal)."""
    if bus is None:
        return
    try:
        from kairos.core.message_bus import Message
        await bus.publish(Message(
            sender="skeleton", topic=topic, msg_type="status",
            content=run.to_dict(),
            metadata={"run_id": run.run_id, "outcome": run.outcome,
                      "attempts": run.attempts},
        ))
    except Exception as exc:  # observation must never break the work
        logger.warning("skeleton run publish to %s failed: %s", topic, exc)


def _record(run: SkeletonRun) -> None:
    run.history.append({"attempt": run.attempts, "outcome": run.outcome,
                        "verifier": run.verdict.verifier})


async def run_once(
    worker: Worker,
    workspace: Workspace,
    task: Task,
    verifier: Verifier,
    *,
    bus=None,
    run_dir=None,
) -> SkeletonRun:
    """One worker pass followed by one verification (no retry)."""
    return await run_task(
        worker, workspace, task, verifier, max_attempts=1, bus=bus, run_dir=run_dir,
    )


async def run_task(
    worker: Worker,
    workspace: Workspace,
    task: Task,
    verifier: Verifier,
    *,
    max_attempts: int = 1,
    revise: Optional[Callable[[Task, Verdict], Optional[Task]]] = None,
    bus=None,
    run_dir=None,
) -> SkeletonRun:
    """Run ``worker`` against ``workspace`` and verify, retrying **only on a
    hard failure** up to ``max_attempts``.

    ``revise(task, verdict) -> Task`` (optional) folds the verdict back into the
    next attempt's task.

    Retry policy (the fix for the old ``passed is True or requires_human``
    guard): the loop retries only when the verdict is an explicit
    ``passed=False``. A ``passed=None`` abstention -- "no verifier could decide"
    -- and a ``requires_human`` gate stop the loop immediately; spinning on an
    abstention just re-runs the same work for the same undecided answer.
    """
    max_attempts = max(1, int(max_attempts))
    run: Optional[SkeletonRun] = None
    current = task
    for attempt in range(1, max_attempts + 1):
        result = await worker.run(workspace, current)
        verdict = await verifier.verify(workspace, current, result)
        run = SkeletonRun(task=current, result=result, verdict=verdict, attempts=attempt)
        _record(run)
        await _publish(bus, f"{RUN_TOPIC}.verified", run)
        if verdict.passed is not False:
            # True (passed) or None (abstained / needs human): stop.
            break
        if revise is not None:
            revised = revise(current, verdict)
            if revised is not None:
                current = revised
    assert run is not None
    if run_dir is not None:
        run.save(run_dir)
    await _publish(bus, f"{RUN_TOPIC}.completed", run)
    return run


def _fold_feedback(task: Task, feedback: str) -> Task:
    """Return a copy of ``task`` with the human's feedback appended."""
    return Task(
        instruction=f"{task.instruction}\n\nHUMAN FEEDBACK: {feedback}",
        output_name=task.output_name,
        inputs=list(task.inputs) if task.inputs is not None else None,
        title=task.title,
        meta=dict(task.meta),
    )


async def resume_task(
    prior: SkeletonRun,
    *,
    approved: bool,
    reason: str = "",
    worker: Optional[Worker] = None,
    workspace: Optional[Workspace] = None,
    verifier: Optional[Verifier] = None,
    max_attempts: int = 1,
    revise: Optional[Callable[[Task, Verdict], Optional[Task]]] = None,
    bus=None,
    run_dir=None,
) -> SkeletonRun:
    """Continue a human-gated run after a human answers the gate.

    * ``approved=True`` -- the gate is satisfied; the run is marked passed with
      a ``human`` verdict and persisted/published.
    * ``approved=False`` and a ``worker``/``workspace``/``verifier`` are given --
      the task is retried with the human's ``reason`` folded into the
      instruction (the worker really runs again).
    * ``approved=False`` with no worker -- the run is marked failed.

    The returned run reuses ``prior.run_id``, so a resumed run overwrites the
    same persisted record rather than spawning a new one.
    """
    if approved:
        verdict = Verdict(
            passed=True, verifier="human",
            reason=reason or "human approved",
            evidence=[{"criterion": "human approval", "satisfied": True}],
        )
        run = SkeletonRun(task=prior.task, result=prior.result, verdict=verdict,
                          attempts=prior.attempts, run_id=prior.run_id,
                          history=list(prior.history))
        _record(run)
        if run_dir is not None:
            run.save(run_dir)
        await _publish(bus, f"{RUN_TOPIC}.resumed", run)
        return run

    if worker is not None and workspace is not None and verifier is not None:
        revised = _fold_feedback(prior.task, reason.strip() or "the human rejected the deliverable")
        return await run_task(worker, workspace, revised, verifier,
                              max_attempts=max_attempts, revise=revise,
                              bus=bus, run_dir=run_dir)

    verdict = Verdict(
        passed=False, verifier="human",
        reason=reason or "human rejected",
        evidence=[{"criterion": "human approval", "satisfied": False}],
    )
    run = SkeletonRun(task=prior.task, result=prior.result, verdict=verdict,
                      attempts=prior.attempts, run_id=prior.run_id,
                      history=list(prior.history))
    _record(run)
    if run_dir is not None:
        run.save(run_dir)
    await _publish(bus, f"{RUN_TOPIC}.resumed", run)
    return run
