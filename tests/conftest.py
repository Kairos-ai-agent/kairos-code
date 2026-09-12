"""Stop the test suite from committing anything to a real repository.

Why this exists: `checkpoint_round` stages the workspace with `git add -A`. A
test that runs a real loop against a workspace that happens to sit inside a
repository (the default workspace is the process CWD — i.e. this repo when you
run `pytest` from the checkout) therefore commits the *whole repository*, round
after round. That is exactly how commits named
"kairos: round 1 approved (score 75)" and "kairos: round 12 rejected (score 20)"
appeared on top of a working tree mid-development.

Belt:
  * `KAIROS_NO_CHECKPOINTS=1` makes `checkpoint_round` a no-op (see
    kairos/tools/checkpoint.py). Set here for the entire session.

Braces:
  * tests/unit/test_loop_run.py now builds its LoopSession against a throwaway
    temporary workspace instead of the default (CWD).
"""
import os
import tempfile
from pathlib import Path

import pytest


def pytest_configure(config):
    """Session-wide: never let a test commit into a repository."""
    os.environ["KAIROS_NO_CHECKPOINTS"] = "1"


@pytest.fixture
def tmp_workspace(tmp_path):
    """Provide an isolated workspace directory for tool tests."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


@pytest.fixture
def throwaway_workspace():
    """A temp workspace for tests that run real loops."""
    with tempfile.TemporaryDirectory(prefix="kairos-test-ws-") as d:
        yield Path(d)
