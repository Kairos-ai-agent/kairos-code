"""A frozen windowed MCP server must not die writing its own stdout.

Real traceback (windowed EXE, parent spawned it with stdout=PIPE):

    kairos_code_launcher.py:205 _serve_bundled_mcp
    kairos/mcp_filesystem_server.py:415 _serve_async
    mcp/server/stdio.py:209 stdio_server -> stdout_writer -> flush
    OSError: [Errno 22] Invalid argument  ->  ExceptionGroup

PyInstaller leaves sys.stdout unusable in a windowed build even though fd 1 is a
good pipe; the MCP SDK flushes sys.stdout for every message, so the server died
with an opaque traceback. _reattach_std_streams reopens the real descriptors.
"""
from __future__ import annotations

import sys

from kairos_code_launcher import _reattach_std_streams


class _BrokenStream:
    """Stands in for the bootloader's dead shim: flush() raises EINVAL."""

    def flush(self):
        raise OSError(22, "Invalid argument")

    def fileno(self):
        return -1


def test_a_broken_stdout_is_replaced_with_the_real_descriptor(monkeypatch):
    broken = _BrokenStream()
    monkeypatch.setattr(sys, "stdout", broken)
    assert _reattach_std_streams() is True
    assert sys.stdout is not broken
    # It must actually be writable, which is the whole point.
    sys.stdout.write("")
    sys.stdout.flush()


def test_a_working_stdout_is_left_alone(monkeypatch):
    real = sys.stdout
    assert _reattach_std_streams() is True
    assert sys.stdout is real


def test_no_stdout_at_all_reports_failure(monkeypatch):
    # Register the harness streams with monkeypatch so the helper's own
    # setattr(None) cannot leave pytest without a stderr for the rest of the run.
    for name in ("stdin", "stdout", "stderr"):
        monkeypatch.setattr(sys, name, getattr(sys, name), raising=False)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr("os.fdopen",
                        lambda *a, **k: (_ for _ in ()).throw(OSError(9, "bad fd")))
    assert _reattach_std_streams() is False
