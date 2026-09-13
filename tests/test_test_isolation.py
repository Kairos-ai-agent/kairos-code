"""The suite must not write into the developer's real data directory.

tests/unit/test_memory_api.py goes through the real persistence layer and creates
projects called `mem-api-test-<hex>`. With the data directory defaulting to
`<repo>/data`, those landed in the developer's own kairos.db — and then appeared
in the sidebar of the README screenshots. tests/conftest.py now points
KAIROS_DATA_DIR at a throwaway directory for the session; these tests assert that
stayed true, and they check the directory the *app* resolves rather than only the
environment variable.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_DATA_DIR = (REPO_ROOT / "data").resolve()


def test_session_data_dir_is_a_throwaway_directory():
    raw = os.environ.get("KAIROS_DATA_DIR")
    assert raw, "tests/conftest.py must set KAIROS_DATA_DIR for the whole session"
    assert "kairos-test-data-" in raw, raw
    assert Path(raw).resolve() != REPO_DATA_DIR


def test_the_application_resolves_that_same_directory():
    """Assert it on the code path the app uses, not only on the env var.

    `kairos.config.settings` reads KAIROS_DATA_DIR when it is imported, so a
    conftest hook that runs after that import — or a test that deletes the
    variable — would leave the real directory in play while the env var still
    looked right.
    """
    from kairos.config.settings import Settings

    resolved = Path(Settings().data_dir).resolve()
    assert resolved == Path(os.environ["KAIROS_DATA_DIR"]).resolve()
    assert resolved != REPO_DATA_DIR, (
        "the suite is pointed at the repository's own data directory — tests that "
        "create projects would pollute it"
    )
