"""Tests for kairos.har (Round 29: .har/ contract + resume runtime)."""
from __future__ import annotations

import io
import json
import os
import time
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

from kairos.har import (
    DEFAULT_HAR_DIRNAME,
    HarContract,
    HarState,
    STALE_LOCK_S,
    _format_history_row,
    _har_dir,
    _synthetic_tick,
    acquire_lock,
    append_history,
    init_har,
    load_har,
    main,
    read_history,
    release_lock,
    resume,
    save_plan,
    save_state,
    status_text,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_contract(goal: str = "test goal", **kwargs) -> HarContract:
    return HarContract(goal=goal, cwd=str(kwargs.pop("cwd", "C:\\test")),
                       rounds_planned=kwargs.pop("rounds", 5), **kwargs)


# ---------------------------------------------------------------------------
# Data class round-trips
# ---------------------------------------------------------------------------


def test_contract_to_from_dict_round_trip():
    c = HarContract(goal="x", cwd="/y", rounds_planned=3, meta={"k": 1})
    d = c.to_dict()
    c2 = HarContract.from_dict(d)
    assert c2.goal == "x"
    assert c2.cwd == "/y"
    assert c2.rounds_planned == 3
    assert c2.meta == {"k": 1}


def test_contract_from_dict_missing_fields_use_defaults():
    c = HarContract.from_dict({})
    assert c.goal == ""
    assert c.rounds_planned == 10
    assert c.meta == {}


def test_state_to_from_dict_round_trip():
    s = HarState(round=3, last_score=80, last_approve=True,
                 last_summary="lgtm", last_signature="abc",
                 no_progress_count=2, plan_text="do x", updated_at=1.0)
    d = s.to_dict()
    s2 = HarState.from_dict(d)
    assert s2.round == 3
    assert s2.last_score == 80
    assert s2.last_approve is True
    assert s2.last_summary == "lgtm"
    assert s2.last_signature == "abc"
    assert s2.no_progress_count == 2
    assert s2.plan_text == "do x"
    assert s2.updated_at == 1.0


def test_state_from_dict_handles_missing_keys():
    s = HarState.from_dict({})
    assert s.round == 0
    assert s.last_approve is False
    assert s.plan_text == ""


# ---------------------------------------------------------------------------
# init_har
# ---------------------------------------------------------------------------


def test_init_har_creates_all_files(tmp_path: Path):
    h = init_har(tmp_path, _make_contract(goal="migrate endpoints"))
    assert h == tmp_path / DEFAULT_HAR_DIRNAME
    assert (h / "contract.json").exists()
    assert (h / "state.json").exists()
    assert (h / "plan.md").exists()
    assert (h / "history.jsonl").exists()
    assert (h / ".gitignore").exists()
    # The contract was written verbatim
    c = json.loads((h / "contract.json").read_text(encoding="utf-8"))
    assert c["goal"] == "migrate endpoints"
    # State is initialized at round 0
    s = json.loads((h / "state.json").read_text(encoding="utf-8"))
    assert s["round"] == 0
    # history.jsonl is empty (0 bytes)
    assert (h / "history.jsonl").stat().st_size == 0
    # .gitignore ignores the lock file
    assert "lock" in (h / ".gitignore").read_text(encoding="utf-8")


def test_init_har_fails_if_exists(tmp_path: Path):
    init_har(tmp_path, _make_contract())
    with pytest.raises(FileExistsError):
        init_har(tmp_path, _make_contract(goal="other"))


def test_init_har_plan_md_includes_goal(tmp_path: Path):
    h = init_har(tmp_path, _make_contract(goal="refactor the auth layer"))
    plan = (h / "plan.md").read_text(encoding="utf-8")
    assert "refactor the auth layer" in plan


# ---------------------------------------------------------------------------
# load_har
# ---------------------------------------------------------------------------


def test_load_har_round_trip(tmp_path: Path):
    h = init_har(tmp_path, _make_contract(goal="g", rounds=7))
    c, s, path = load_har(tmp_path)
    assert c.goal == "g"
    assert c.rounds_planned == 7
    assert s.round == 0
    assert path == h


def test_load_har_missing_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_har(tmp_path)


# ---------------------------------------------------------------------------
# save_state — atomic write
# ---------------------------------------------------------------------------


def test_save_state_atomic(tmp_path: Path, monkeypatch):
    """R10 lesson: tmp + rename so a crash mid-write doesn't corrupt state.json."""
    h = init_har(tmp_path, _make_contract())
    s = HarState(round=5, last_score=85, last_approve=True,
                 last_summary="ok")
    save_state(h, s)
    # No leftover .tmp file
    assert not (h / "state.json.tmp").exists()
    # The new state is on disk
    s2 = HarState.from_dict(json.loads(
        (h / "state.json").read_text(encoding="utf-8")))
    assert s2.round == 5
    assert s2.last_score == 85
    assert s2.last_approve is True


def test_save_state_updates_timestamp(tmp_path: Path):
    h = init_har(tmp_path, _make_contract())
    s = HarState()
    assert s.updated_at == 0.0
    save_state(h, s)
    assert s.updated_at > 0.0


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


def test_history_round_trip(tmp_path: Path):
    h = init_har(tmp_path, _make_contract())
    append_history(h, {"round": 1, "ts": 1.0, "score": 60,
                       "approved": False, "summary": "a"})
    append_history(h, {"round": 2, "ts": 2.0, "score": 80,
                       "approved": True, "summary": "b"})
    out = read_history(h, limit=10)
    assert len(out) == 2
    # Newest first
    assert out[0]["round"] == 2
    assert out[1]["round"] == 1


def test_history_limit_truncates(tmp_path: Path):
    h = init_har(tmp_path, _make_contract())
    for i in range(5):
        append_history(h, {"round": i, "ts": float(i), "score": 50,
                           "approved": False, "summary": f"r{i}"})
    out = read_history(h, limit=3)
    assert len(out) == 3
    assert out[0]["round"] == 4
    assert out[2]["round"] == 2


def test_history_corrupt_lines_skipped(tmp_path: Path):
    h = init_har(tmp_path, _make_contract())
    append_history(h, {"round": 1, "ts": 1.0, "score": 60,
                       "approved": False, "summary": "ok"})
    (h / "history.jsonl").write_text(
        (h / "history.jsonl").read_text(encoding="utf-8")
        + "{not json\n"
        + json.dumps({"round": 2, "ts": 2.0, "score": 80,
                      "approved": True, "summary": "ok2"})
        + "\n",
        encoding="utf-8",
    )
    out = read_history(h, limit=10)
    assert len(out) == 2
    assert out[0]["round"] == 2


# ---------------------------------------------------------------------------
# Lock
# ---------------------------------------------------------------------------


def test_lock_acquire_release(tmp_path: Path):
    h = init_har(tmp_path, _make_contract())
    assert acquire_lock(h) == os.getpid()
    assert (h / "lock").exists()
    release_lock(h)
    assert not (h / "lock").exists()


def test_lock_contention(tmp_path: Path):
    h = init_har(tmp_path, _make_contract())
    # Acquire from "this" process
    assert acquire_lock(h) == os.getpid()
    # A second attempt from the same process must fail (FileExistsError
    # is the underlying mechanism; the public API returns None).
    assert acquire_lock(h) is None


def test_lock_stale_is_stolen(tmp_path: Path, monkeypatch):
    h = init_har(tmp_path, _make_contract())
    # Plant a stale lock file
    stale_ts = time.time() - STALE_LOCK_S - 60
    (h / "lock").write_text(f"99999\n{stale_ts}\n", encoding="utf-8")
    # Acquire must succeed by stealing
    assert acquire_lock(h) == os.getpid()
    assert (h / "lock").exists()
    release_lock(h)


def test_lock_release_only_own(tmp_path: Path):
    h = init_har(tmp_path, _make_contract())
    # Plant someone else's lock
    (h / "lock").write_text(f"99999\n{time.time()}\n", encoding="utf-8")
    # release_lock must NOT remove a lock we don't own
    release_lock(h)
    assert (h / "lock").exists()


def test_lock_release_missing_file_noop(tmp_path: Path):
    h = init_har(tmp_path, _make_contract())
    # No lock present
    release_lock(h)  # should not raise
    assert not (h / "lock").exists()


# ---------------------------------------------------------------------------
# save_plan
# ---------------------------------------------------------------------------


def test_save_plan_writes_md(tmp_path: Path):
    h = init_har(tmp_path, _make_contract())
    save_plan(h, "# New plan\ndo x, y, z")
    assert (h / "plan.md").read_text(encoding="utf-8") == "# New plan\ndo x, y, z"


def test_save_plan_empty(tmp_path: Path):
    h = init_har(tmp_path, _make_contract())
    save_plan(h, "")
    assert "(empty plan)" in (h / "plan.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# _synthetic_tick
# ---------------------------------------------------------------------------


def test_synthetic_tick_advances_round():
    s = HarState()
    c = _make_contract()
    s2, entry = _synthetic_tick(s, c)
    assert s2.round == 1
    assert entry["round"] == 1
    assert entry["approved"] is False  # round 1 < 3
    assert entry["score"] == 60


def test_synthetic_tick_approves_at_round_3():
    s = HarState(round=2)
    c = _make_contract()
    s2, entry = _synthetic_tick(s, c)
    assert s2.round == 3
    assert entry["approved"] is True
    assert entry["score"] == 80


# ---------------------------------------------------------------------------
# resume
# ---------------------------------------------------------------------------


def test_resume_runs_synthetic_rounds(tmp_path: Path):
    init_har(tmp_path, _make_contract(goal="x"))
    rc, msg = resume(tmp_path, max_rounds=2)
    assert rc == 0
    # 2 rounds were appended to history
    c, s, h = load_har(tmp_path)
    entries = read_history(h, limit=10)
    assert len(entries) == 2
    assert s.round == 2


def test_resume_stops_on_approve(tmp_path: Path):
    init_har(tmp_path, _make_contract())
    # Synthetic tick approves at round 3 — so resume with max_rounds=10
    # should stop after round 3.
    rc, msg = resume(tmp_path, max_rounds=10)
    assert rc == 4  # approved, stopping
    assert "approved" in msg
    c, s, h = load_har(tmp_path)
    assert s.round == 3
    assert s.last_approve is True
    entries = read_history(h, limit=20)
    assert len(entries) == 3


def test_resume_no_stop_on_approve(tmp_path: Path):
    init_har(tmp_path, _make_contract())
    rc, msg = resume(tmp_path, max_rounds=5, stop_on_approve=False)
    assert rc == 0  # didn't stop early
    c, s, h = load_har(tmp_path)
    assert s.round == 5


def test_resume_lock_contention(tmp_path: Path):
    init_har(tmp_path, _make_contract())
    h = _har_dir(tmp_path)
    # Manually plant a lock owned by a different PID
    (h / "lock").write_text(f"99999\n{time.time()}\n", encoding="utf-8")
    rc, msg = resume(tmp_path, max_rounds=1)
    assert rc == 2
    assert "lock" in msg.lower()


def test_resume_missing_harness(tmp_path: Path):
    rc, msg = resume(tmp_path, max_rounds=1)
    assert rc == 3
    assert "no" in msg.lower()


def test_resume_stops_on_no_progress(tmp_path: Path):
    init_har(tmp_path, _make_contract())

    # A custom tick that always increments no_progress_count but never approves
    def stuck_tick(state: HarState, contract: HarContract):
        new = HarState(**{**state.to_dict(),
                          "round": state.round + 1,
                          "last_score": 50,
                          "last_approve": False,
                          "no_progress_count": state.no_progress_count + 1,
                          "last_summary": "stuck"})
        entry = {"round": new.round, "ts": time.time(),
                 "score": 50, "approved": False, "summary": "stuck"}
        return new, entry

    rc, msg = resume(tmp_path, max_rounds=10, tick_fn=stuck_tick)
    assert rc == 5  # no progress, stopping
    assert "no progress" in msg
    c, s, h = load_har(tmp_path)
    # Should have stopped at 3 rounds
    assert s.round == 3


def test_resume_saves_state_after_each_round(tmp_path: Path):
    init_har(tmp_path, _make_contract())
    resume(tmp_path, max_rounds=2)
    # state.json must reflect the latest round
    s = HarState.from_dict(json.loads(
        (_har_dir(tmp_path) / "state.json").read_text(encoding="utf-8")))
    assert s.round == 2
    assert s.last_score == 60  # synthetic: round 1 and 2 are unapproved


def test_resume_persists_plan_change(tmp_path: Path):
    init_har(tmp_path, _make_contract())

    def plan_tick(state: HarState, contract: HarContract):
        new = HarState(**{**state.to_dict(),
                          "round": state.round + 1,
                          "last_summary": "with plan",
                          "plan_text": "do x, y, z"})
        return new, {"round": new.round, "ts": time.time(),
                     "score": 70, "approved": False, "summary": "ok"}

    resume(tmp_path, max_rounds=1, tick_fn=plan_tick, stop_on_approve=False)
    plan = (_har_dir(tmp_path) / "plan.md").read_text(encoding="utf-8")
    assert "do x, y, z" in plan


def test_resume_releases_lock_on_error(tmp_path: Path):
    init_har(tmp_path, _make_contract())

    def boom_tick(state, contract):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        resume(tmp_path, max_rounds=1, tick_fn=boom_tick)
    # Lock should be released even though the tick raised
    assert not (_har_dir(tmp_path) / "lock").exists()


# ---------------------------------------------------------------------------
# status_text
# ---------------------------------------------------------------------------


def test_status_text_includes_key_fields():
    c = _make_contract(goal="refactor auth", rounds=12)
    s = HarState(round=2, last_score=70, last_approve=False,
                 no_progress_count=1, plan_text="x" * 100)
    out = status_text(c, s)
    assert "refactor auth" in out
    assert "round=2" in out
    assert "last_score=70" in out
    assert "rounds_planned=12" in out
    assert "plan_chars=100" in out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli(argv, monkeypatch=None):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main(argv)
    return rc, buf.getvalue()


def test_cli_init_creates_har(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc, out = _cli(["init", "refactor the whole thing", "--rounds", "8"])
    assert rc == 0
    assert "Initialized" in out
    assert (tmp_path / DEFAULT_HAR_DIRNAME).exists()


def test_cli_init_with_meta(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc, out = _cli(["init", "x", "--meta", '{"priority": "high"}'])
    assert rc == 0
    c = json.loads((tmp_path / DEFAULT_HAR_DIRNAME / "contract.json")
                   .read_text(encoding="utf-8"))
    assert c["meta"] == {"priority": "high"}


def test_cli_init_invalid_meta_json(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc, _ = _cli(["init", "x", "--meta", "{not valid"])
    assert rc == 1


def test_cli_init_fails_if_har_exists(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _cli(["init", "x"])
    rc, _ = _cli(["init", "y"])
    assert rc == 1


def test_cli_status_prints_state(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _cli(["init", "x", "--rounds", "3"])
    rc, out = _cli(["status"])
    assert rc == 0
    assert "round=0" in out
    assert "rounds_planned=3" in out


def test_cli_status_missing_har(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc, _ = _cli(["status"])
    assert rc == 1


def test_cli_checkpoints_empty(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _cli(["init", "x"])
    rc, out = _cli(["checkpoints"])
    assert rc == 0
    assert "No rounds" in out


def test_cli_checkpoints_with_entries(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _cli(["init", "x"])
    resume(tmp_path, max_rounds=2)
    rc, out = _cli(["checkpoints"])
    assert rc == 0
    assert "R  2" in out
    assert "R  1" in out


def test_cli_checkpoints_json(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _cli(["init", "x"])
    resume(tmp_path, max_rounds=1)
    rc, out = _cli(["checkpoints", "--json"])
    assert rc == 0
    data = json.loads(out)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["round"] == 1


def test_cli_resume_runs_rounds(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _cli(["init", "x"])
    rc, out = _cli(["resume", "--rounds", "2"])
    assert rc == 0
    c, s, h = load_har(tmp_path)
    assert s.round == 2


def test_cli_resume_missing_har(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rc, _ = _cli(["resume", "--rounds", "1"])
    assert rc == 1


# ---------------------------------------------------------------------------
# _format_history_row
# ---------------------------------------------------------------------------


def test_format_history_row_approved_shows_ok():
    row = _format_history_row({"round": 5, "score": 90,
                               "approved": True, "summary": "lgtm"})
    assert "R  5" in row
    assert "score= 90" in row
    assert "[OK]" in row
    assert "lgtm" in row


def test_format_history_row_rejected_shows_X():
    row = _format_history_row({"round": 1, "score": 30,
                               "approved": False, "summary": "issues"})
    assert "[X]" in row
    assert "issues" in row


# ---------------------------------------------------------------------------
# _har_dir
# ---------------------------------------------------------------------------


def test_har_dir_uses_default_name():
    assert _har_dir(Path("C:\\foo")) == Path("C:\\foo") / ".har"
    assert _har_dir(Path("/tmp/bar")) == Path("/tmp/bar") / ".har"
