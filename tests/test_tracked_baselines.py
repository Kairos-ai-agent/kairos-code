"""Every file under tests/data/ has to be in the repository.

A baseline that exists only on the machine that wrote it is worse than no
baseline: the test passes locally and fails on CI. That is exactly what happened
to `tests/data/foreign_tool_vocabulary_baseline.txt` — the directory matched the
`data/` line in `.gitignore`, so the file was never committed, and the first
thing CI did with it was assert that it exists.

The check is deliberately about *git*, not about the filesystem: the file being
present is the thing that lies.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

DATA_DIR = Path(__file__).resolve().parent / "data"
REPO_ROOT = DATA_DIR.parent.parent


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(REPO_ROOT),
                          capture_output=True, text=True)


def test_this_is_a_git_checkout():
    if _git("rev-parse", "--is-inside-work-tree").returncode != 0:
        pytest.skip("not a git checkout (an sdist, for example)")


def test_every_baseline_file_is_tracked():
    files = sorted(p for p in DATA_DIR.rglob("*") if p.is_file())
    if not files:
        pytest.skip("no baseline files yet")

    untracked = []
    for path in files:
        rel = path.relative_to(REPO_ROOT).as_posix()
        if _git("ls-files", "--error-unmatch", rel).returncode != 0:
            untracked.append(rel)

    assert not untracked, (
        "these files are read by tests but are not in git, so CI will not have "
        "them: " + ", ".join(untracked)
    )


def test_no_baseline_file_is_ignored():
    """Belt and braces: an ignore rule would hide the problem from `git add`."""
    files = sorted(p for p in DATA_DIR.rglob("*") if p.is_file())
    if not files:
        pytest.skip("no baseline files yet")

    ignored = []
    for path in files:
        rel = path.relative_to(REPO_ROOT).as_posix()
        if _git("check-ignore", "-q", rel).returncode == 0:
            ignored.append(rel)

    assert not ignored, (
        "a .gitignore rule still covers these test baselines: " + ", ".join(ignored)
    )
