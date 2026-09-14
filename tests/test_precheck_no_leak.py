"""The loop's precheck must not leak processes, nor run the suite inside itself.

`pre_check_workspace` runs a command every round. `asyncio.wait_for(proc.communicate())`
cancels the *read* on timeout but leaves the process — and any children it spawned —
running with their pipes open, so leaked processes (and their memory) accumulate round
after round. And on a CI runner the auto-detected command was `pytest` in the project
itself, so the suite ran inside the suite: shard 5 climbed from 1 GB to 13.5 GB while
eight tests in tests/unit/test_loop_run.py timed out at 150 s apiece.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from kairos.loop.precheck import _auto_detect_test_command, _run


def _alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True)
        return str(pid).encode() in (out.stdout or b"")
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _hard_kill(pid: int) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, check=False)
        else:
            os.kill(pid, 9)
    except OSError:
        pass


def test_inside_a_test_session_the_suite_is_never_started_again(monkeypatch, tmp_path):
    """The recursion guard: the root cause of the 13 GB shard."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")

    monkeypatch.delenv("KAIROS_INSIDE_TESTS", raising=False)
    assert _auto_detect_test_command(tmp_path) == ["pytest", "-q", "--tb=short", "-x"]

    monkeypatch.setenv("KAIROS_INSIDE_TESTS", "1")
    assert _auto_detect_test_command(tmp_path) is None


@pytest.mark.asyncio
async def test_a_timed_out_command_is_killed_with_its_children(tmp_path):
    """`wait_for` cancels the read, not the process — so the tree must be ended explicitly."""
    marker = tmp_path / "child.pid"
    code = (
        "import pathlib, subprocess, sys, time;"
        " p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']);"
        f" pathlib.Path(r'{marker}').write_text(str(p.pid));"
        " time.sleep(120)"
    )
    result = await _run([sys.executable, "-c", code], cwd=tmp_path, timeout=3)
    assert result["ok"] is False and "(timeout)" in result["stderr"], result

    child = int(marker.read_text(encoding="utf-8").strip())
    try:
        for _ in range(20):
            if not _alive(child):
                break
            time.sleep(0.25)
        assert not _alive(child), "the child of a timed-out precheck was left running"
    finally:
        if _alive(child):
            _hard_kill(child)


@pytest.mark.asyncio
async def test_a_failing_command_is_still_reported(tmp_path):
    """The kill path must not swallow the normal result shapes."""
    result = await _run([sys.executable, "-c", "print('hi')"], cwd=tmp_path, timeout=30)
    assert result["ok"] is True and "hi" in result["stdout"]

    missing = await _run([sys.executable, "-c", "import sys; sys.exit(3)"],
                         cwd=tmp_path, timeout=30)
    assert missing["ok"] is False and missing["code"] == 3

    gone = await _run(["definitely-not-a-command-xyz"], cwd=tmp_path, timeout=10)
    assert gone["code"] == 127
