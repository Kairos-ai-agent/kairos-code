"""The smoke test must not leave the server running.

scripts/smoke_binary.py launches a frozen app. A frozen app is a PyInstaller
bootloader that immediately forks the real server, so terminating only the pid we
started leaves an orphan that keeps the port — and, on Windows, a lock on the
executable, which is how a later build failed with ``[WinError 5]`` and how several
invisible servers accumulated on a developer machine. terminate_tree() kills the tree.

Everything here stays in bytes: this host's `tasklist` prints localized text, and
decoding it as UTF-8 kills the reader thread (subprocess then hands back stdout=None).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from smoke_binary import terminate_tree  # noqa: E402


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


def test_terminate_tree_kills_a_child_the_started_process_forked():
    """The bootloader pattern: a process that immediately forks a long-lived child."""
    kwargs = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
              if os.name == "nt" else {"start_new_session": True})
    parent = subprocess.Popen(
        [sys.executable, "-c",
         "import subprocess, sys, time;"
         " p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']);"
         " print(p.pid, flush=True);"
         " time.sleep(120)"],
        stdout=subprocess.PIPE, **kwargs,
    )
    try:
        grandchild = int((parent.stdout.readline() or b"").strip() or b"0")
    except ValueError:  # pragma: no cover - defensive
        _hard_kill(parent.pid)
        raise
    assert grandchild, "could not read the forked child's pid"

    try:
        assert _alive(parent.pid), "the started process should be running"
        assert _alive(grandchild), "the forked child should be running"

        terminate_tree(parent)

        for _ in range(20):
            if not _alive(grandchild):
                break
            time.sleep(0.25)
        assert not _alive(parent.pid), "the started process survived"
        assert not _alive(grandchild), (
            "the forked server survived terminate_tree — that is the orphan bug"
        )
    finally:
        for pid in (grandchild, parent.pid):
            if _alive(pid):
                _hard_kill(pid)
