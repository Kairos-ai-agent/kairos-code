"""Durable tasks: a run that outlives the app process.

`kairos/har.py` could already write a `.har/` contract, resume it, and keep a
history — and nothing in the app ever called it. These tests cover the two new
pieces: `har.resume_async` (the same state machine with an awaited tick, so the
tick can drive the app's event loop) and `kairos/durable.py`, which binds a
project's durable task to that project's real loop.

The headline property is the last test: a *second* orchestrator, seeing only
what is on disk, resumes the task where the first one left it.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from kairos import durable, har


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "ws"


def _contract(goal: str = "migrate all 47 endpoints") -> har.HarContract:
    return har.HarContract(goal=goal, har_id="abc12345", created_at=1.0,
                           cwd=".", rounds_planned=5)


class FakeSession:
    def __init__(self, round_no: int, approved: bool, score: int) -> None:
        self.session_id = "sess-" + str(round_no)
        self.history = [{
            "round": round_no,
            "review": {"verdict": "approve" if approved else "request_changes",
                       "score": score, "approved": approved},
            "summary": "round " + str(round_no) + " did some work",
        }]
        self.plan_text = "- step one\n"


class FakeProject:
    def __init__(self, root: Path) -> None:
        self.id = "p1"
        self.work_dir = str(root)
        self.workspace = root
        self.loop_task = None
        self.loop_session = None


class FakeOrchestrator:
    """Enough of Orchestrator: `start_loop` finishes one fake round at once."""

    def __init__(self, project: FakeProject, *, approve_after: int = 99,
                 raise_value_error: bool = False) -> None:
        self.project = project
        self.approve_after = approve_after
        self.raise_value_error = raise_value_error
        self.rounds_started = 0

    def get_project(self, project_id):
        return self.project if project_id == self.project.id else None

    async def start_loop(self, project_id, requirement):
        if self.raise_value_error:
            raise ValueError("Loop already running for project " + project_id)
        self.rounds_started += 1
        n = self.rounds_started
        self.project.loop_session = FakeSession(n, n >= self.approve_after,
                                               60 + n)

        async def _finish():
            return None

        self.project.loop_task = asyncio.ensure_future(_finish())
        return "sess-" + str(n)


# ---------------------------------------------------------------------------
# har.resume_async
# ---------------------------------------------------------------------------

class RecordingTick:
    def __init__(self, approve_at: int = 0) -> None:
        self.rounds: list = []
        self.approve_at = approve_at

    async def __call__(self, state, contract):
        n = state.round + 1
        self.rounds.append(n)
        approved = bool(self.approve_at) and n >= self.approve_at
        new_state = har.HarState(**{**state.to_dict(), "round": n,
                                    "last_score": 50 + n,
                                    "last_approve": approved,
                                    "updated_at": 1.0 * n})
        return new_state, {"round": n, "ts": n, "score": 50 + n,
                           "approved": approved, "summary": "tick " + str(n)}


def test_resume_async_runs_ticks_and_saves_state(root):
    har.init_har(root, _contract())
    tick = RecordingTick()
    rc, msg = asyncio.run(har.resume_async(root, max_rounds=3, tick_fn=tick))

    assert rc == 0, msg
    assert tick.rounds == [1, 2, 3]
    contract, state, h = har.load_har(root)
    assert state.round == 3
    assert state.last_score == 53
    assert (h / "state.json").exists()
    assert len(har.read_history(h, limit=10)) == 3
    # The lock is released even though ticks ran.
    assert not (h / "lock").exists()


def test_resume_async_stops_on_approval(root):
    har.init_har(root, _contract())
    tick = RecordingTick(approve_at=2)
    rc, msg = asyncio.run(har.resume_async(root, max_rounds=5, tick_fn=tick))

    assert rc == 4, msg
    assert "approved" in msg
    assert tick.rounds == [1, 2], "it must stop at the approval, not finish the budget"


def test_resume_async_refuses_when_another_holder_owns_the_lock(root):
    har.init_har(root, _contract())
    h = root / ".har"
    assert har.acquire_lock(h) is not None
    try:
        rc, msg = asyncio.run(har.resume_async(root, max_rounds=1,
                                               tick_fn=RecordingTick()))
        assert rc == 2
        assert "lock" in msg
    finally:
        har.release_lock(h)


def test_resume_async_reports_a_missing_contract(root):
    rc, msg = asyncio.run(har.resume_async(root, max_rounds=1,
                                           tick_fn=RecordingTick()))
    assert rc == 3
    assert ".har" in msg


def test_a_tick_that_raises_releases_the_lock(root):
    har.init_har(root, _contract())

    def boom(state, contract):
        raise RuntimeError("provider died")

    with pytest.raises(RuntimeError):
        asyncio.run(har.resume_async(root, max_rounds=1, tick_fn=boom))
    assert not (root / ".har" / "lock").exists(), "a failed tick must not wedge the task"


# ---------------------------------------------------------------------------
# the durable service
# ---------------------------------------------------------------------------

def test_start_creates_the_contract(root):
    root.mkdir(parents=True, exist_ok=True)
    res = durable.start(FakeProject(root), "refactor the parser")
    assert res["ok"], res
    assert (root / ".har" / "contract.json").exists()
    assert res["round"] == 0
    assert res["goal"] == "refactor the parser"


def test_start_refuses_a_second_task(root):
    root.mkdir(parents=True, exist_ok=True)
    project = FakeProject(root)
    assert durable.start(project, "first")["ok"]
    second = durable.start(project, "second")
    assert not second["ok"]
    assert "already exists" in second["error"]
    # ...and the first goal is intact.
    assert durable.status(project)["goal"] == "first"


def test_start_requires_a_goal(root):
    root.mkdir(parents=True, exist_ok=True)
    assert not durable.start(FakeProject(root), "   ")["ok"]


def test_status_is_none_without_a_task(root):
    root.mkdir(parents=True, exist_ok=True)
    assert durable.status(FakeProject(root)) is None


def test_resume_advances_the_task_by_one_tick(root):
    root.mkdir(parents=True, exist_ok=True)
    project = FakeProject(root)
    durable.start(project, "migrate the endpoints")
    orch = FakeOrchestrator(project, approve_after=99)

    res = asyncio.run(durable.resume(orch, "p1", ticks=1))
    assert res["ok"], res
    assert res["code"] == 0, res["message"]
    assert res["status"]["round"] == 1
    assert res["status"]["last_score"] == 61
    assert res["status"]["last_approve"] is False
    assert "round 1 did some work" in res["status"]["last_summary"]


def test_resume_stops_when_the_loop_approves(root):
    root.mkdir(parents=True, exist_ok=True)
    project = FakeProject(root)
    durable.start(project, "migrate the endpoints")
    orch = FakeOrchestrator(project, approve_after=2)

    res = asyncio.run(durable.resume(orch, "p1", ticks=4))
    assert res["code"] == 4, res["message"]
    assert orch.rounds_started == 2, "it must stop as soon as the loop approves"
    assert res["status"]["last_approve"] is True
    assert res["status"]["round"] == 2
    assert len(res["status"]["history"]) == 2


def test_resume_refuses_when_the_loop_is_already_running(root):
    root.mkdir(parents=True, exist_ok=True)
    project = FakeProject(root)
    durable.start(project, "migrate the endpoints")
    orch = FakeOrchestrator(project, raise_value_error=True)

    res = asyncio.run(durable.resume(orch, "p1", ticks=1))
    assert not res["ok"]
    assert "already running" in res["message"]
    # Nothing was recorded as progress.
    assert res["status"]["round"] == 0
    assert res["status"]["history"] == []


def test_resume_without_a_task_says_so(root):
    root.mkdir(parents=True, exist_ok=True)
    orch = FakeOrchestrator(FakeProject(root))
    res = asyncio.run(durable.resume(orch, "p1", ticks=1))
    assert not res["ok"]
    assert "no durable task" in res["error"]


def test_resume_of_an_unknown_project_says_so(root):
    orch = FakeOrchestrator(FakeProject(root))
    res = asyncio.run(durable.resume(orch, "nope", ticks=1))
    assert not res["ok"]
    assert "unknown project" in res["error"]


# ---------------------------------------------------------------------------
# the headline: it survives the process
# ---------------------------------------------------------------------------

def test_a_second_orchestrator_resumes_where_the_first_stopped(root):
    """Close the app between ticks; the next one picks up from the disk."""
    root.mkdir(parents=True, exist_ok=True)
    project = FakeProject(root)
    durable.start(project, "migrate the endpoints")

    first = FakeOrchestrator(project, approve_after=99)
    asyncio.run(durable.resume(first, "p1", ticks=1))
    assert json.loads((root / ".har" / "state.json").read_text())["round"] == 1

    # A new process: nothing in memory, only .har/ on disk.
    revived = FakeProject(root)
    # The fake counts its own rounds from zero, so approving on its first one
    # puts the *durable* round at 2 — the round that continues from disk.
    second = FakeOrchestrator(revived, approve_after=1)
    res = asyncio.run(durable.resume(second, "p1", ticks=3))

    assert res["code"] == 4, res["message"]
    assert second.rounds_started == 1
    assert res["status"]["round"] == 2, "it continued from the saved round"
    history = res["status"]["history"]
    assert [h["round"] for h in history] == [2, 1], "newest first"
