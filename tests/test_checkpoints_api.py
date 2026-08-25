"""Tests for the checkpoint HTTP API.

The endpoints live in `api/routes/checkpoints.py`. The actual git
operations go through `kairos.tools.checkpoint`, which we trust
to handle its own behavior — here we just verify the HTTP layer
returns the right codes and the right shape.

We avoid spinning up a real FastAPI server; we exercise the
route functions directly with a fake orchestrator.
"""
from __future__ import annotations

import sys
import tempfile
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

# These tests don't need a real DB. The route module imports
# `api.deps.orchestrator` at top level, so we patch it before
# the import.
import api.deps as _api_deps
_api_deps.orchestrator = MagicMock()
from api.routes.checkpoints import (  # noqa: E402
    CreateCheckpointRequest,
    RestoreRequest,
    list_project_checkpoints,
    restore_checkpoint,
    diff_checkpoint,
    create_checkpoint,
)
from kairos.tools import checkpoint as ckpt


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_project(project_id: str, work_dir: Path) -> MagicMock:
    p = MagicMock()
    p.work_dir = str(work_dir)
    p.workspace = work_dir
    return p


def _git_init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=path, check=True,
    )
    (path / "README.md").write_text("hi", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "initial"], cwd=path, check=True,
    )


@pytest.fixture
def git_project(tmp_path):
    proj_dir = tmp_path / "proj"
    _git_init(proj_dir)
    proj = _make_project("p1", proj_dir)
    _api_deps.orchestrator._projects = {"p1": proj}
    yield proj_dir


# ---------------------------------------------------------------------------
# list_project_checkpoints
# ---------------------------------------------------------------------------


def test_list_returns_empty_when_no_checkpoints(git_project):
    resp = list_project_checkpoints("p1")
    assert resp["count"] == 0
    assert resp["checkpoints"] == []


def test_list_returns_persisted_checkpoints(git_project):
    # Create a tracked, uncommitted change so checkpoint_round
    # actually has something to commit.
    (git_project / "change.txt").write_text("delta")
    ckpt.checkpoint_round(
        git_project, round_no=1, score=95,
        summary="looks good", approved=True,
    )
    resp = list_project_checkpoints("p1")
    assert resp["count"] == 1
    item = resp["checkpoints"][0]
    assert item["round"] == 1
    assert item["score"] == 95
    assert item["approved"] is True


def test_list_404_when_project_unknown(git_project):
    with pytest.raises(Exception) as exc:
        list_project_checkpoints("missing-project")
    assert "404" in str(exc.value)


# ---------------------------------------------------------------------------
# create_checkpoint
# ---------------------------------------------------------------------------


def test_create_checkpoint_makes_new_commit(git_project):
    (git_project / "new.txt").write_text("hello")
    req = CreateCheckpointRequest(label="added new.txt", round=5, score=80)
    resp = create_checkpoint("p1", req)
    assert resp["label"] == "added new.txt"
    assert len(resp["sha"]) >= 7
    # And the project now has 2 commits (initial + this one).
    out = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=git_project, capture_output=True, text=True, check=True,
    ).stdout
    assert len(out.strip().splitlines()) == 2


def test_create_checkpoint_rejects_empty_label_and_summary(git_project):
    req = CreateCheckpointRequest()  # both empty
    with pytest.raises(Exception) as exc:
        create_checkpoint("p1", req)
    assert "400" in str(exc.value)


def test_create_checkpoint_no_changes_returns_400(git_project):
    # No uncommitted changes → checkpoint_round returns None → 400.
    req = CreateCheckpointRequest(label="nothing changed")
    with pytest.raises(Exception) as exc:
        create_checkpoint("p1", req)
    assert "400" in str(exc.value)


# ---------------------------------------------------------------------------
# restore_checkpoint
# ---------------------------------------------------------------------------


def test_restore_rolls_back_tracked_files(git_project):
    # `git read-tree -u --reset` only restores TRACKED files; untracked
    # files are intentionally left alone. Test the documented
    # behavior.
    (git_project / "v1.txt").write_text("v1")
    subprocess.run(["git", "add", "."], cwd=git_project, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "v1"], cwd=git_project, check=True,
    )
    sha_v1 = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=git_project, capture_output=True, text=True, check=True,
    ).stdout.strip()
    # Make a TRACKED-file change (modify README.md).
    (git_project / "README.md").write_text("modified by v2")
    assert "modified by v2" in (git_project / "README.md").read_text()

    resp = restore_checkpoint("p1", RestoreRequest(sha=sha_v1))
    assert resp["ok"] is True
    # README.md is back to "hi" (the v1 content).
    assert (git_project / "README.md").read_text() == "hi"


def test_restore_empty_sha_400(git_project):
    with pytest.raises(Exception) as exc:
        restore_checkpoint("p1", RestoreRequest(sha=""))
    assert "400" in str(exc.value)


# ---------------------------------------------------------------------------
# diff_checkpoint
# ---------------------------------------------------------------------------


def test_diff_returns_unified_output(git_project):
    # Get initial commit SHA.
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=git_project, capture_output=True, text=True, check=True,
    ).stdout.strip()
    # Modify a TRACKED file, then diff. (Untracked files don't
    # appear in `git diff`; the test should match real git
    # semantics.)
    (git_project / "README.md").write_text("updated content\n")
    resp = diff_checkpoint("p1", sha)
    assert "README.md" in resp["diff"]
    assert "-hi" in resp["diff"]   # old line
    assert "+updated content" in resp["diff"]  # new line


def test_diff_unknown_sha_returns_500(git_project):
    with pytest.raises(Exception) as exc:
        diff_checkpoint("p1", "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")
    # git diff returns 128 for unknown rev → route returns 500.
    assert "500" in str(exc.value)
