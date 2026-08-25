"""Agent Teams — coordinated multi-agent execution with a shared task board.

Mirrors the Codex-Harness / Claude Code "Agent Teams" feature: a single
TeamLead agent decomposes a high-level goal into independent sub-tasks,
then dispatches them to N Worker agents that run in parallel, each in
its own git worktree for filesystem isolation. Workers report progress
back to a SharedTaskBoard; the lead (or a separate merge step) combines
the worktree branches back into the main checkout when the team
finishes.

Design notes:

* `TeamTask` is intentionally NOT a `BaseModel` — we keep it as a
  dataclass so it can be JSON-serialized for the UI task board and the
  API without pydantic. (Pydantic would force every field to be
  declared up-front; for the task board we want extensibility.)
* `SharedTaskBoard` uses a `threading.Lock` because dispatch + worker
  callbacks can run from any thread (asyncio tasks, watcher threads,
  HTTP handlers). For the rare cases where two writers race, the lock
  keeps the task list consistent.
* `Team.dispatch()` is **async** because we use `asyncio.gather` to run
  workers in parallel. The synchronous entry point is `run_team()` for
  scripts that don't have an event loop.
* Worktree isolation uses `kairos.worktree.WorktreeManager` so a worker
  crashing or producing a bad patch can't damage the main checkout.
* `Team.merge()` defaults to the "fast_forward" strategy (each worker
  branch is fast-forwarded into the main branch in order); "squash"
  collapses each branch into a single commit; "manual" leaves the
  branches in place for a human to review.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """Lifecycle of a team task.

    pending    — accepted, not yet started
    running    — a worker is actively working on it
    done       — worker finished successfully
    failed     — worker raised or produced an error result
    skipped    — superseded by another worker (e.g. a duplicate task)
    """
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class MergeStrategy(str, Enum):
    """How worker branches are recombined at the end of a team run."""
    FAST_FORWARD = "fast_forward"  # merge each branch in order
    SQUASH = "squash"              # collapse each branch into 1 commit
    MANUAL = "manual"              # leave branches; don't merge


@dataclass
class TeamTask:
    """A single unit of work handled by one worker.

    Attributes:
        id: short unique identifier (8 hex chars)
        title: human-readable label for the UI
        description: detailed prompt for the worker
        status: current TaskStatus
        assigned_to: worker_id once dispatched (empty while pending)
        worktree_path: absolute path of the worker's worktree
        branch_name: git branch the worker is operating on
        result: short text result returned by the worker (None until done)
        error: error message if status == FAILED
        created_at: unix timestamp when the task was added
        started_at: unix timestamp when the worker started
        finished_at: unix timestamp when the worker finished
    """
    id: str
    title: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    assigned_to: str = ""
    worktree_path: str = ""
    branch_name: str = ""
    result: Optional[str] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    finished_at: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TeamTask":
        d = dict(d)
        d["status"] = TaskStatus(d.get("status", "pending"))
        return cls(**d)


class SharedTaskBoard:
    """Thread-safe task board shared across the team.

    The board is the single source of truth for "what is each worker
    doing right now". Workers call `mark_running`, `mark_done`, etc. as
    they progress; the lead (or the HTTP layer) calls `snapshot()` to
    get a consistent view.
    """

    def __init__(self, tasks: Optional[List[TeamTask]] = None):
        self._tasks: Dict[str, TeamTask] = {}
        self._lock = threading.Lock()
        if tasks:
            for t in tasks:
                self._tasks[t.id] = t

    def add(self, task: TeamTask) -> None:
        with self._lock:
            if task.id in self._tasks:
                raise ValueError(f"duplicate task id: {task.id}")
            self._tasks[task.id] = task

    def get(self, task_id: str) -> Optional[TeamTask]:
        with self._lock:
            return self._tasks.get(task_id)

    def update(self, task_id: str, **fields) -> TeamTask:
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None:
                raise KeyError(task_id)
            for k, v in fields.items():
                if k == "status":
                    v = TaskStatus(v) if not isinstance(v, TaskStatus) else v
                setattr(t, k, v)
            return t

    def all(self) -> List[TeamTask]:
        with self._lock:
            return list(self._tasks.values())

    def snapshot(self) -> List[dict]:
        """Return a JSON-serializable list for the UI / API."""
        with self._lock:
            return [t.to_dict() for t in self._tasks.values()]

    def counts(self) -> Dict[str, int]:
        """Return {status_value: count} for the UI badge."""
        with self._lock:
            out: Dict[str, int] = {s.value: 0 for s in TaskStatus}
            for t in self._tasks.values():
                out[t.status.value] += 1
            return out

    def save(self, path: Path) -> None:
        """Persist the board to disk so a UI refresh / CLI restart can
        recover state."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            payload = [t.to_dict() for t in self._tasks.values()]
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                        encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "SharedTaskBoard":
        if not path.exists():
            return cls()
        payload = json.loads(path.read_text(encoding="utf-8"))
        tasks = [TeamTask.from_dict(p) for p in payload]
        return cls(tasks)


@dataclass
class TeamConfig:
    """Configuration for a team run."""
    max_workers: int = 3
    merge_strategy: MergeStrategy = MergeStrategy.FAST_FORWARD
    worktree_base: Optional[str] = None
    worker_role: str = "coder"  # which agent role to use as workers
    timeout_seconds: int = 600
    persist_board_path: Optional[str] = None


@dataclass
class WorkerResult:
    """Outcome of one worker."""
    task_id: str
    worker_id: str
    success: bool
    output: str
    duration_seconds: float
    branch_name: str = ""
    worktree_path: str = ""


# A worker is just a callable: given (task, ctx) it returns output text.
# We don't bind to a specific Agent class so tests can pass mocks, and
# production wires in `kairos.agents.roles.Coder`.
WorkerFn = Callable[[TeamTask, "TeamContext"], Awaitable[str]]


@dataclass
class TeamContext:
    """Per-run context shared between lead and workers."""
    team_id: str
    project_id: str
    work_dir: str
    config: TeamConfig
    board: SharedTaskBoard


class Team:
    """A team of workers executing tasks from a shared board.

    The Team is constructed from a list of task descriptions; calling
    `dispatch()` runs the workers in parallel (asyncio.gather) and
    collects results. Merge is a separate explicit step so callers
    can inspect results first.

    Typical usage:
        team = Team.from_descriptions(["add tests", "update docs"],
                                       project_id="p1",
                                       work_dir="/path",
                                       worker_fn=my_worker)
        results = await team.dispatch()
        team.merge()  # fast-forward the worktree branches back
    """

    def __init__(self,
                 tasks: List[TeamTask],
                 project_id: str,
                 work_dir: str,
                 worker_fn: WorkerFn,
                 config: Optional[TeamConfig] = None):
        self.team_id = uuid.uuid4().hex[:8]
        self.project_id = project_id
        self.work_dir = work_dir
        self.worker_fn = worker_fn
        self.config = config or TeamConfig()
        self.board = SharedTaskBoard(tasks)
        self._results: List[WorkerResult] = []
        self._results_lock = threading.Lock()

    @classmethod
    def from_descriptions(cls,
                          descriptions: List[str],
                          project_id: str,
                          work_dir: str,
                          worker_fn: WorkerFn,
                          config: Optional[TeamConfig] = None,
                          title_prefix: str = "task") -> "Team":
        """Build a team from a flat list of task descriptions.

        Each description becomes a TeamTask with an auto-generated id
        and a title derived from the first 50 chars of the description.
        """
        tasks = []
        for desc in descriptions:
            tid = uuid.uuid4().hex[:8]
            first_line = desc.strip().splitlines()[0] if desc.strip() else ""
            title = (first_line[:50] + "…") if len(first_line) > 50 else first_line
            tasks.append(TeamTask(
                id=tid,
                title=title or f"{title_prefix}-{tid}",
                description=desc,
            ))
        return cls(tasks, project_id, work_dir, worker_fn, config)

    def status_counts(self) -> Dict[str, int]:
        return self.board.counts()

    def _make_context(self) -> TeamContext:
        return TeamContext(
            team_id=self.team_id,
            project_id=self.project_id,
            work_dir=self.work_dir,
            config=self.config,
            board=self.board,
        )

    async def _run_one(self, task: TeamTask, semaphore: asyncio.Semaphore) -> WorkerResult:
        """Run a single worker with concurrency limiting + timeout."""
        worker_id = f"w_{task.id}"
        self.board.update(task.id,
                          status=TaskStatus.RUNNING,
                          assigned_to=worker_id,
                          started_at=time.time())
        ctx = self._make_context()
        t0 = time.time()
        try:
            async with semaphore:
                output = await asyncio.wait_for(
                    self.worker_fn(task, ctx),
                    timeout=self.config.timeout_seconds,
                )
            duration = time.time() - t0
            self.board.update(task.id,
                              status=TaskStatus.DONE,
                              result=output,
                              finished_at=time.time())
            result = WorkerResult(
                task_id=task.id, worker_id=worker_id,
                success=True, output=output, duration_seconds=duration,
                branch_name=task.branch_name, worktree_path=task.worktree_path,
            )
        except asyncio.TimeoutError:
            duration = time.time() - t0
            err = f"worker timed out after {self.config.timeout_seconds}s"
            self.board.update(task.id,
                              status=TaskStatus.FAILED, error=err,
                              finished_at=time.time())
            result = WorkerResult(
                task_id=task.id, worker_id=worker_id,
                success=False, output=err, duration_seconds=duration,
                branch_name=task.branch_name, worktree_path=task.worktree_path,
            )
        except Exception as e:  # noqa: BLE001
            duration = time.time() - t0
            err = f"{type(e).__name__}: {e}"
            logger.exception("worker %s failed", worker_id)
            self.board.update(task.id,
                              status=TaskStatus.FAILED, error=err,
                              finished_at=time.time())
            result = WorkerResult(
                task_id=task.id, worker_id=worker_id,
                success=False, output=err, duration_seconds=duration,
                branch_name=task.branch_name, worktree_path=task.worktree_path,
            )
        with self._results_lock:
            self._results.append(result)
        # Persist board state if a path was configured.
        if self.config.persist_board_path:
            try:
                self.board.save(Path(self.config.persist_board_path))
            except Exception:
                logger.debug("failed to persist task board", exc_info=True)
        return result

    async def dispatch(self) -> List[WorkerResult]:
        """Run all pending tasks in parallel (bounded by max_workers)."""
        pending = [t for t in self.board.all()
                   if t.status == TaskStatus.PENDING]
        if not pending:
            return list(self._results)
        sem = asyncio.Semaphore(max(1, self.config.max_workers))
        coros = [self._run_one(t, sem) for t in pending]
        results = await asyncio.gather(*coros, return_exceptions=False)
        return results

    def results(self) -> List[WorkerResult]:
        with self._results_lock:
            return list(self._results)

    def to_dict(self) -> dict:
        return {
            "team_id": self.team_id,
            "project_id": self.project_id,
            "work_dir": self.work_dir,
            "config": {
                "max_workers": self.config.max_workers,
                "merge_strategy": self.config.merge_strategy.value,
                "worker_role": self.config.worker_role,
                "timeout_seconds": self.config.timeout_seconds,
            },
            "board": self.board.snapshot(),
            "counts": self.status_counts(),
        }

    # ----- merge -----

    def merge(self,
              worktree_manager: Optional["WorktreeLike"] = None) -> MergeResult:
        """Combine worker branches back into the main branch.

        `worktree_manager` is the WorktreeManager that produced the
        worktrees; if omitted, we try to construct one from work_dir
        (which must be inside a git repo). For "manual" strategy, this
        is a no-op and just returns the list of branches for review.
        """
        from kairos.worktree import WorktreeManager  # local import to avoid cycle
        wm = worktree_manager or WorktreeManager(repo_path=Path(self.work_dir))
        strategy = self.config.merge_strategy
        branches = [t.branch_name for t in self.board.all()
                    if t.status == TaskStatus.DONE and t.branch_name]
        if strategy == MergeStrategy.MANUAL:
            return MergeResult(strategy=strategy, merged=[], skipped=branches)
        merged: List[str] = []
        failed: List[str] = []
        for branch in branches:
            try:
                wm.merge_to(branch)
                merged.append(branch)
            except Exception as e:  # noqa: BLE001
                logger.warning("merge of %s failed: %s", branch, e)
                failed.append(branch)
        return MergeResult(
            strategy=strategy,
            merged=merged,
            skipped=[],
            failed=failed,
        )


@dataclass
class MergeResult:
    """Outcome of a team merge step."""
    strategy: MergeStrategy
    merged: List[str]
    skipped: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy.value,
            "merged": list(self.merged),
            "skipped": list(self.skipped),
            "failed": list(self.failed),
        }


# A small Protocol for the worktree manager we depend on. Tests can
# pass a fake without dragging in real git plumbing.
from typing import Protocol  # noqa: E402


class WorktreeLike(Protocol):
    def merge_to(self, branch: str) -> None: ...


def run_team(team: Team) -> List[WorkerResult]:
    """Synchronous entry point: run dispatch() under asyncio.run()."""
    return asyncio.run(team.dispatch())
