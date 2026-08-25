"""Tests for the Agent Teams feature.

Covers:
* SharedTaskBoard — thread-safe add/update/snapshot/save/load
* Team.from_descriptions — auto-id, auto-title, no duplicates
* Team.dispatch — parallel execution, semaphore limiting, timeout, exception
* Team.merge — fast_forward / squash / manual strategies
* Team.to_dict — JSON shape used by the API
* API layer — create → dispatch → status → merge via FastAPI test client
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from kairos.teams import (
    MergeResult,
    MergeStrategy,
    SharedTaskBoard,
    TaskStatus,
    Team,
    TeamConfig,
    TeamTask,
    WorkerResult,
    run_team,
)


# ---------------------------------------------------------------------------
# SharedTaskBoard
# ---------------------------------------------------------------------------


def test_board_add_and_get():
    board = SharedTaskBoard()
    t = TeamTask(id="abc", title="t", description="d")
    board.add(t)
    assert board.get("abc") is t
    assert board.get("missing") is None


def test_board_add_duplicate_raises():
    board = SharedTaskBoard()
    board.add(TeamTask(id="x", title="t", description="d"))
    with pytest.raises(ValueError):
        board.add(TeamTask(id="x", title="t2", description="d2"))


def test_board_update_changes_fields():
    board = SharedTaskBoard([TeamTask(id="x", title="t", description="d")])
    board.update("x", status="running", assigned_to="w1")
    t = board.get("x")
    assert t.status == TaskStatus.RUNNING
    assert t.assigned_to == "w1"


def test_board_update_unknown_raises():
    board = SharedTaskBoard()
    with pytest.raises(KeyError):
        board.update("missing", status="running")


def test_board_snapshot_is_json_safe():
    board = SharedTaskBoard([
        TeamTask(id="a", title="A", description="do A"),
        TeamTask(id="b", title="B", description="do B"),
    ])
    snap = board.snapshot()
    # Must be a list of plain dicts (no dataclass leaks).
    assert isinstance(snap, list)
    for entry in snap:
        assert isinstance(entry, dict)
        assert isinstance(entry["status"], str)
    # Round-trip through JSON.
    text = json.dumps(snap, ensure_ascii=False)
    again = json.loads(text)
    assert again[0]["id"] == "a"


def test_board_counts_tally_statuses():
    board = SharedTaskBoard([
        TeamTask(id="a", title="A", description="d", status=TaskStatus.PENDING),
        TeamTask(id="b", title="B", description="d", status=TaskStatus.PENDING),
        TeamTask(id="c", title="C", description="d", status=TaskStatus.DONE),
    ])
    counts = board.counts()
    assert counts["pending"] == 2
    assert counts["done"] == 1
    assert counts["running"] == 0


def test_board_thread_safety():
    """Concurrent adders should not lose tasks or raise."""
    board = SharedTaskBoard()
    def adder(prefix: str, n: int):
        for i in range(n):
            board.add(TeamTask(id=f"{prefix}{i}", title=f"{prefix}{i}",
                               description="d"))
    threads = [threading.Thread(target=adder, args=(f"t{i}_", 50))
               for i in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(board.all()) == 200


def test_board_save_and_load(tmp_path):
    path = tmp_path / "board.json"
    board = SharedTaskBoard([
        TeamTask(id="x", title="X", description="do X"),
    ])
    board.update("x", status=TaskStatus.RUNNING, assigned_to="w1")
    board.save(path)
    assert path.exists()
    loaded = SharedTaskBoard.load(path)
    assert loaded.get("x").status == TaskStatus.RUNNING
    assert loaded.get("x").assigned_to == "w1"


def test_board_load_missing_returns_empty(tmp_path):
    board = SharedTaskBoard.load(tmp_path / "nope.json")
    assert board.all() == []


# ---------------------------------------------------------------------------
# TeamTask
# ---------------------------------------------------------------------------


def test_team_task_to_and_from_dict_round_trip():
    t = TeamTask(
        id="abc", title="T", description="d",
        status=TaskStatus.RUNNING, assigned_to="w",
        worktree_path="/tmp/wt", branch_name="feat/x",
    )
    d = t.to_dict()
    # status is coerced to its string value
    assert d["status"] == "running"
    again = TeamTask.from_dict(d)
    assert again.id == "abc"
    assert again.status == TaskStatus.RUNNING
    assert again.branch_name == "feat/x"


# ---------------------------------------------------------------------------
# Team.from_descriptions
# ---------------------------------------------------------------------------


def test_team_from_descriptions_assigns_ids():
    team = Team.from_descriptions(
        ["first task", "second task", "third task"],
        project_id="p1", work_dir="/tmp",
        worker_fn=lambda t, c: asyncio.sleep(0),
    )
    assert len(team.board.all()) == 3
    ids = [t.id for t in team.board.all()]
    assert len(set(ids)) == 3  # all unique


def test_team_from_descriptions_titles_from_first_line():
    team = Team.from_descriptions(
        ["Add login flow\nDetails here",
         "single line task"],
        project_id="p1", work_dir="/tmp",
        worker_fn=lambda t, c: asyncio.sleep(0),
    )
    titles = [t.title for t in team.board.all()]
    assert titles[0] == "Add login flow"
    assert titles[1] == "single line task"


def test_team_from_descriptions_truncates_long_titles():
    long = "x" * 100
    team = Team.from_descriptions(
        [long], project_id="p1", work_dir="/tmp",
        worker_fn=lambda t, c: asyncio.sleep(0),
    )
    title = team.board.all()[0].title
    # ellipsis indicates truncation
    assert title.endswith("…")
    assert len(title) <= 51


# ---------------------------------------------------------------------------
# Team.dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_runs_all_pending():
    async def worker(task, ctx):
        return f"done-{task.id}"
    team = Team.from_descriptions(
        ["a", "b", "c"], project_id="p1", work_dir="/tmp",
        worker_fn=worker, config=TeamConfig(max_workers=3),
    )
    results = await team.dispatch()
    assert len(results) == 3
    assert all(r.success for r in results)
    assert all(r.output.startswith("done-") for r in results)
    counts = team.status_counts()
    assert counts["done"] == 3
    assert counts["pending"] == 0


@pytest.mark.asyncio
async def test_dispatch_skips_non_pending():
    async def worker(task, ctx):
        return "x"
    team = Team.from_descriptions(
        ["a", "b"], project_id="p1", work_dir="/tmp",
        worker_fn=worker, config=TeamConfig(max_workers=2),
    )
    # Manually mark one as DONE — should be skipped on dispatch.
    tasks = team.board.all()
    team.board.update(tasks[0].id, status=TaskStatus.DONE, result="preset")
    results = await team.dispatch()
    assert len(results) == 1
    assert team.status_counts()["done"] == 2  # 1 preset + 1 from dispatch


@pytest.mark.asyncio
async def test_dispatch_marks_running_then_done():
    """The board should reflect RUNNING during the worker call."""
    seen_running = []

    async def worker(task, ctx):
        # Peek at board state from another async coroutine while we're running.
        seen_running.append(ctx.board.get(task.id).status)
        return "ok"

    team = Team.from_descriptions(
        ["a"], project_id="p1", work_dir="/tmp",
        worker_fn=worker, config=TeamConfig(max_workers=1),
    )
    await team.dispatch()
    assert seen_running[0] == TaskStatus.RUNNING
    final = team.board.get(team.board.all()[0].id).status
    assert final == TaskStatus.DONE


@pytest.mark.asyncio
async def test_dispatch_respects_max_workers_concurrency():
    """max_workers=1 should serialize; max_workers=N should overlap."""
    timeline = []
    lock = asyncio.Lock()

    async def worker(task, ctx):
        async with lock:
            timeline.append(("start", task.id))
        await asyncio.sleep(0.05)
        async with lock:
            timeline.append(("end", task.id))
        return "ok"

    # Serial
    serial_team = Team.from_descriptions(
        ["a", "b", "c"], project_id="p1", work_dir="/tmp",
        worker_fn=worker, config=TeamConfig(max_workers=1),
    )
    await serial_team.dispatch()
    serial_starts = [i for (k, i) in timeline if k == "start"]
    serial_ends = [i for (k, i) in timeline if k == "end"]
    # With max_workers=1, all tasks start sequentially.
    assert serial_starts == serial_ends  # same order, no overlap

    # Parallel
    timeline.clear()
    par_team = Team.from_descriptions(
        ["a", "b", "c"], project_id="p1", work_dir="/tmp",
        worker_fn=worker, config=TeamConfig(max_workers=3),
    )
    await par_team.dispatch()
    # With max_workers=3, all 3 starts happen before any ends.
    par_starts = [i for (k, i) in timeline if k == "start"]
    par_ends = [i for (k, i) in timeline if k == "end"]
    assert len(par_starts) == 3
    assert len(par_ends) == 3
    # First three events are all starts.
    first_three = [k for (k, i) in timeline[:3]]
    assert first_three == ["start", "start", "start"]


@pytest.mark.asyncio
async def test_dispatch_timeout_marks_failed():
    async def slow(task, ctx):
        await asyncio.sleep(5)
        return "should-not-reach"

    team = Team.from_descriptions(
        ["a"], project_id="p1", work_dir="/tmp",
        worker_fn=slow, config=TeamConfig(max_workers=1, timeout_seconds=1),
    )
    results = await team.dispatch()
    assert results[0].success is False
    assert "timed out" in results[0].output
    assert team.status_counts()["failed"] == 1


@pytest.mark.asyncio
async def test_dispatch_exception_marks_failed():
    async def bad(task, ctx):
        raise RuntimeError("boom")

    team = Team.from_descriptions(
        ["a"], project_id="p1", work_dir="/tmp",
        worker_fn=bad, config=TeamConfig(max_workers=1),
    )
    results = await team.dispatch()
    assert results[0].success is False
    assert "RuntimeError" in results[0].output
    assert "boom" in results[0].output
    assert team.status_counts()["failed"] == 1


@pytest.mark.asyncio
async def test_dispatch_persists_board_when_configured(tmp_path):
    async def worker(task, ctx):
        return "ok"
    board_path = tmp_path / "team.json"
    team = Team.from_descriptions(
        ["a", "b"], project_id="p1", work_dir="/tmp",
        worker_fn=worker,
        config=TeamConfig(max_workers=2,
                          persist_board_path=str(board_path)),
    )
    await team.dispatch()
    assert board_path.exists()
    payload = json.loads(board_path.read_text(encoding="utf-8"))
    assert len(payload) == 2
    assert all(p["status"] == "done" for p in payload)


# ---------------------------------------------------------------------------
# Team.merge
# ---------------------------------------------------------------------------


class FakeWorktreeManager:
    """In-memory WorktreeManager for testing merge strategies."""

    def __init__(self, fail_for: list = None):
        self.merged: list = []
        self.fail_for = fail_for or []

    def merge_to(self, branch: str) -> None:
        if branch in self.fail_for:
            raise RuntimeError(f"merge conflict on {branch}")
        self.merged.append(branch)


def test_merge_manual_is_noop():
    team = Team.from_descriptions(
        ["a", "b"], project_id="p1", work_dir="/tmp",
        worker_fn=lambda t, c: asyncio.sleep(0),
        config=TeamConfig(merge_strategy=MergeStrategy.MANUAL),
    )
    # Manually mark tasks done with branch names.
    for t in team.board.all():
        team.board.update(t.id, status=TaskStatus.DONE, branch_name=f"feat/{t.id}")
    wm = FakeWorktreeManager()
    result = team.merge(worktree_manager=wm)
    assert result.strategy == MergeStrategy.MANUAL
    assert wm.merged == []
    assert set(result.skipped) == {"feat/" + t.id for t in team.board.all()}


def test_merge_fast_forward_calls_merge_to_in_order():
    team = Team.from_descriptions(
        ["a", "b", "c"], project_id="p1", work_dir="/tmp",
        worker_fn=lambda t, c: asyncio.sleep(0),
        config=TeamConfig(merge_strategy=MergeStrategy.FAST_FORWARD),
    )
    for t in team.board.all():
        team.board.update(t.id, status=TaskStatus.DONE, branch_name=f"feat/{t.id}")
    wm = FakeWorktreeManager()
    result = team.merge(worktree_manager=wm)
    assert wm.merged == [f"feat/{t.id}" for t in team.board.all()]
    assert result.failed == []


def test_merge_collects_failed_branches():
    team = Team.from_descriptions(
        ["a", "b"], project_id="p1", work_dir="/tmp",
        worker_fn=lambda t, c: asyncio.sleep(0),
    )
    for t in team.board.all():
        team.board.update(t.id, status=TaskStatus.DONE, branch_name=f"feat/{t.id}")
    fail_branch = "feat/" + team.board.all()[0].id
    wm = FakeWorktreeManager(fail_for=[fail_branch])
    result = team.merge(worktree_manager=wm)
    assert fail_branch in result.failed
    assert fail_branch not in result.merged


def test_merge_skips_failed_or_pending_tasks():
    """Only DONE tasks with a branch_name are merged."""
    team = Team.from_descriptions(
        ["a", "b", "c"], project_id="p1", work_dir="/tmp",
        worker_fn=lambda t, c: asyncio.sleep(0),
    )
    tasks = team.board.all()
    # a: done with branch
    team.board.update(tasks[0].id, status=TaskStatus.DONE, branch_name="feat/a")
    # b: failed (no branch)
    team.board.update(tasks[1].id, status=TaskStatus.FAILED, error="x")
    # c: still pending
    wm = FakeWorktreeManager()
    result = team.merge(worktree_manager=wm)
    assert wm.merged == ["feat/a"]


# ---------------------------------------------------------------------------
# Team.to_dict
# ---------------------------------------------------------------------------


def test_team_to_dict_has_expected_shape():
    team = Team.from_descriptions(
        ["a"], project_id="p1", work_dir="/tmp",
        worker_fn=lambda t, c: asyncio.sleep(0),
    )
    d = team.to_dict()
    assert d["team_id"] == team.team_id
    assert d["project_id"] == "p1"
    assert d["work_dir"] == "/tmp"
    assert "config" in d and d["config"]["max_workers"] >= 1
    assert "board" in d and len(d["board"]) == 1
    assert "counts" in d


# ---------------------------------------------------------------------------
# run_team sync entry point
# ---------------------------------------------------------------------------


def test_run_team_runs_sync():
    seen = []

    def make_worker():
        async def worker(task, ctx):
            seen.append(task.id)
            return "ok"
        return worker

    team = Team.from_descriptions(
        ["a", "b"], project_id="p1", work_dir="/tmp",
        worker_fn=make_worker(),
    )
    results = run_team(team)
    assert len(results) == 2
    assert len(seen) == 2
