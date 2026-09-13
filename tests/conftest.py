"""Stop the test suite from touching a real repository or a real data directory.

Why this exists: `checkpoint_round` stages the workspace with `git add -A`. A
test that runs a real loop against a workspace that happens to sit inside a
repository (the default workspace is the process CWD — i.e. this repo when you
run `pytest` from the checkout) therefore commits the *whole repository*, round
after round. That is exactly how commits named
"kairos: round 1 approved (score 75)" and "kairos: round 12 rejected (score 20)"
appeared on top of a working tree mid-development.

Same accident, one layer down: the data directory defaults to `<repo>/data`, and
parts of the suite go through the real persistence layer — tests/unit/
test_memory_api.py creates projects named `mem-api-test-<hex>`. Running pytest
filled the developer's own kairos.db with those, and they then showed up in the
sidebar of the screenshots used in README.md.

Belt:
  * `KAIROS_NO_CHECKPOINTS=1` makes `checkpoint_round` a no-op (see
    kairos/tools/checkpoint.py). Set here for the entire session.
  * `KAIROS_DATA_DIR` points at a throwaway directory. `kairos.config.settings`
    reads it when the module is imported and this hook runs before collection, so
    the whole session is covered; a test that wants its own directory can still
    set it again.

Braces:
  * tests/unit/test_loop_run.py now builds its LoopSession against a throwaway
    temporary workspace instead of the default (CWD).
  * tests/test_test_isolation.py asserts the data directory the app resolves is
    the throwaway one, and not <repo>/data.
"""
import os
import tempfile
from pathlib import Path

import pytest


def pytest_configure(config):
    """Session-wide: never let a test commit into a repository or write to the
    developer's data directory."""
    os.environ["KAIROS_NO_CHECKPOINTS"] = "1"
    os.environ["KAIROS_DATA_DIR"] = tempfile.mkdtemp(prefix="kairos-test-data-")


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
