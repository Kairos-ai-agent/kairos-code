"""Worker identity tests: one repo is one worker, every time.

The requirement this encodes: a main agent must be able to dispatch task after
task without Kairos starting a new session each time. So "same repo → same
project id" is checked repeatedly, including when our own state file is
missing or corrupt — identity that depends on a file we might lose would be a
new single point of failure.
"""
from __future__ import annotations

import json
from pathlib import Path

from kairos import worker_identity as wi


def test_bind_is_stable_for_one_repo(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()

    first = wi.bind(repo)
    second = wi.bind(repo)

    assert first.project_id == second.project_id
    assert first.project_id.startswith("w")
    assert len(first.project_id) == 8


def test_two_repos_get_two_workers(tmp_path):
    left = tmp_path / "left"
    right = tmp_path / "right"
    left.mkdir()
    right.mkdir()

    assert wi.bind(left).project_id != wi.bind(right).project_id


def test_case_differences_do_not_create_a_second_worker(tmp_path):
    """Windows paths are case-insensitive; a worker must not fork on that."""
    repo = tmp_path / "Proj"
    repo.mkdir()
    first = wi.bind(repo)
    second = wi.bind(str(repo).upper())
    assert first.project_id == second.project_id


def test_identity_survives_losing_our_state_file(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    original = wi.bind(repo).project_id

    (repo / wi.KAIROS_DIR / wi.BINDING_NAME).unlink()
    assert wi.load(repo) is None

    # A fresh clone, a cleaned tree, someone's rm -rf: the id still resolves
    # because it is derived, not stored.
    assert wi.bind(repo).project_id == original


def test_corrupt_binding_recovers_instead_of_raising(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    expected = wi.bind(repo).project_id

    path = repo / wi.KAIROS_DIR / wi.BINDING_NAME
    path.write_text("{not json at all", encoding="utf-8")

    recovered = wi.bind(repo)
    assert recovered.project_id == expected
    # And it healed the file on the way through.
    assert json.loads(path.read_text(encoding="utf-8"))["project_id"] == expected


def test_our_state_never_shows_up_in_the_project_diff(tmp_path):
    """The main agent reviews a diff; worker bookkeeping must not appear in
    it. ``.kairos/.gitignore`` holding ``*`` is what keeps that true."""
    repo = tmp_path / "proj"
    repo.mkdir()
    wi.bind(repo)

    ignore = repo / wi.KAIROS_DIR / ".gitignore"
    assert ignore.exists()
    assert ignore.read_text(encoding="utf-8").strip().endswith("*")


def test_read_only_repo_keeps_the_binding_elsewhere(tmp_path, monkeypatch):
    repo = tmp_path / "readonly"
    repo.mkdir()
    elsewhere = tmp_path / "outside"

    monkeypatch.setattr(wi, "ensure_kairos_dir", lambda _repo: None)
    binding = wi.bind(repo, fallback_dir=elsewhere)

    assert binding.path
    assert not binding.path.startswith(str(repo))
    assert Path(binding.path).parent == elsewhere
    # The derived id is the same either way, so behaviour is unchanged.
    assert binding.project_id == wi.derive_project_id(repo)


def test_touch_records_dispatch_history(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    binding = wi.bind(repo)
    assert binding.dispatches == 0

    wi.touch(binding, "T001")
    wi.touch(binding, "T002")
    reloaded = wi.load(repo)

    assert reloaded is not None
    assert reloaded.dispatches == 2
    assert reloaded.first_task_id == "T001"
    assert reloaded.last_task_id == "T002"
    assert "2 次派发" in reloaded.summary()


def test_forget_detaches_without_destroying_history(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    wi.bind(repo)

    assert wi.forget(repo) is True
    assert wi.load(repo) is None
    assert wi.forget(repo) is False       # idempotent


def test_persist_false_leaves_no_trace(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    binding = wi.bind(repo, persist=False)
    assert binding.project_id
    assert wi.load(repo) is None
