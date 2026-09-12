"""Tests for the pre-commit hook wrapper.

The wrapper is at `docs/internal/PRE_COMMIT_HOOK.py` and is meant to be
copied into a project. We test the decision logic (no staged
files = pass; CRITICAL in output = fail) without actually running
git, by monkey-patching the subprocess calls.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Add the wrapper's directory to sys.path so we can import it.
sys.path.insert(0, str(ROOT / "docs" / "internal"))
import importlib
_hook_module = importlib.import_module("PRE_COMMIT_HOOK")
main = _hook_module.main


# ---------------------------------------------------------------------------
# _staged_files
# ---------------------------------------------------------------------------


def test_staged_files_parses_git_output():
    fake = subprocess.CompletedProcess(
        args=[], returncode=0,
        stdout="a.py\nb/c.py\n\n", stderr=""
    )
    with patch.object(subprocess, "run", return_value=fake):
        out = _hook_module._staged_files()
    assert out == ["a.py", "b/c.py"]


def test_staged_files_empty_when_git_fails():
    fake = subprocess.CompletedProcess(
        args=[], returncode=128, stdout="", stderr="fatal: not a git repo"
    )
    with patch.object(subprocess, "run", return_value=fake):
        out = _hook_module._staged_files()
    assert out == []


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def test_main_returns_zero_when_no_staged_files():
    with patch.object(_hook_module, "_staged_files", return_value=[]), \
         patch.object(sys, "argv", ["PRE_COMMIT_HOOK.py"]):
        code = main()
    assert code == 0


def test_main_runs_kairos_when_files_staged():
    with patch.object(_hook_module, "_staged_files", return_value=["x.py"]), \
         patch.object(_hook_module, "_has_kairos", return_value=True), \
         patch.object(subprocess, "run", return_value=subprocess.CompletedProcess(
             args=[], returncode=0, stdout="ok", stderr="",
         )), \
         patch.object(sys, "argv", ["PRE_COMMIT_HOOK.py", "--json"]):
        code = main()
    assert code == 0


def test_main_aborts_commit_on_critical():
    with patch.object(_hook_module, "_staged_files", return_value=["x.py"]), \
         patch.object(_hook_module, "_has_kairos", return_value=True), \
         patch.object(subprocess, "run", return_value=subprocess.CompletedProcess(
             args=[], returncode=0,
             stdout=json.dumps({"status": "completed",
                               "result": "CRITICAL: leaked secret"}),
             stderr="",
         )), \
         patch.object(sys, "argv", ["PRE_COMMIT_HOOK.py", "--json"]):
        code = main()
    assert code == 1


def test_main_passes_when_status_failed_but_no_critical_keyword():
    """A non-CRITICAL review (e.g. MAJOR only) should let the commit
    proceed. We trust the developer to look at the result themselves."""
    with patch.object(_hook_module, "_staged_files", return_value=["x.py"]), \
         patch.object(_hook_module, "_has_kairos", return_value=True), \
         patch.object(subprocess, "run", return_value=subprocess.CompletedProcess(
             args=[], returncode=0,
             stdout=json.dumps({"status": "completed",
                               "result": "MAJOR: missing tests"}),
             stderr="",
         )), \
         patch.object(sys, "argv", ["PRE_COMMIT_HOOK.py", "--json"]):
        code = main()
    assert code == 0


def test_main_does_not_block_when_kairos_missing():
    """If kairos isn't installed, the hook is a no-op."""
    with patch.object(_hook_module, "_staged_files", return_value=["x.py"]), \
         patch.object(_hook_module, "_has_kairos", return_value=False), \
         patch.object(sys, "argv", ["PRE_COMMIT_HOOK.py"]):
        code = main()
    assert code == 0


def test_main_treats_kairos_crash_as_non_blocking():
    """A kairos crash shouldn't fail the commit; the developer will
    see the error in the log."""
    with patch.object(_hook_module, "_staged_files", return_value=["x.py"]), \
         patch.object(_hook_module, "_has_kairos", return_value=True), \
         patch.object(subprocess, "run", return_value=subprocess.CompletedProcess(
             args=[], returncode=1, stdout="oops", stderr="",
         )), \
         patch.object(sys, "argv", ["PRE_COMMIT_HOOK.py"]):
        code = main()
    assert code == 0
