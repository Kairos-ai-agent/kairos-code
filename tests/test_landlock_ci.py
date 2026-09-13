"""Linux Landlock CI test.

``kairos.sandbox._linux_landlock_sandbox`` builds a Landlock ruleset through
``ctypes`` and returns its file descriptor; the *caller* keeps that fd open in the
child (``pass_fds``) and runs ``_landlock_restrict_self(fd)`` there as a
``preexec_fn``, so only the subprocess is confined. Restricting the parent is
deliberately not done — that used to be a bug.

The tests that need a real kernel only run on Linux x86_64 / aarch64 and skip
elsewhere, which is also why a stale call signature survived here for so long:
this module never executes on the maintainer's Windows machine.

Plan for each real test:

  1. build the ruleset from a ``SandboxPolicy``,
  2. spawn a child with the fd passed through and ``restrict_self`` as preexec,
  3. assert on what the child could and could not do.
"""
from __future__ import annotations

import inspect
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

LINUX_X86_64 = (sys.platform == "linux") and (platform.machine() in ("x86_64", "AMD64", "aarch64"))


# ---------------------------------------------------------------------------
# Pure-Python guards: the function exists, and has the shape callers rely on
# ---------------------------------------------------------------------------


def test_landlock_sandbox_function_exists():
    """The implementation must be importable on every platform,
    even where it returns None (no Landlock)."""
    from kairos.sandbox import _linux_landlock_sandbox
    assert callable(_linux_landlock_sandbox)


def test_landlock_sandbox_signature():
    """It takes one ``SandboxPolicy`` and returns a ruleset fd or None.

    This assertion used to be `"allowed_cwd" in params or len(params) >= 1`, which
    passed no matter what the signature was — and by the time it mattered the
    implementation had moved to ``SandboxPolicy`` while the tests still called it
    with ``allowed_cwd=``/``writable_paths=`` (a TypeError on Linux only).
    """
    from kairos.sandbox import _linux_landlock_sandbox

    assert list(inspect.signature(_linux_landlock_sandbox).parameters) == ["policy"]


# ---------------------------------------------------------------------------
# Real Landlock tests — Linux only
# ---------------------------------------------------------------------------


def _spawn_confined(policy, body: str) -> subprocess.CompletedProcess:
    """Run ``body`` in a child confined by Landlock, or skip if unavailable."""
    from kairos.sandbox import _landlock_restrict_self, _linux_landlock_sandbox

    fd = _linux_landlock_sandbox(policy)
    if fd is None:
        pytest.skip("Landlock is not available on this kernel")
    try:
        return subprocess.run(  # noqa: S603 - fixed argv, test-only
            [sys.executable, "-c", body],
            capture_output=True,
            text=True,
            timeout=30,
            pass_fds=(fd,),
            preexec_fn=lambda: _landlock_restrict_self(fd),
        )
    finally:
        os.close(fd)


WRITE_BODY = """
import sys
from pathlib import Path

target = Path({target!r}) / "landlock-probe.txt"
try:
    target.write_text("written")
except (PermissionError, OSError) as exc:
    print(f"BLOCKED: {{type(exc).__name__}}")
    sys.exit(0)
print("WROTE")
sys.exit(2)
"""


@pytest.mark.skipif(
    not LINUX_X86_64,
    reason="Landlock only available on Linux x86_64 / aarch64",
)
def test_landlock_blocks_write_outside_allowed_root():
    """A write outside the allowed root must be denied by the sandbox."""
    from kairos.sandbox import SandboxPolicy

    scratch = Path(tempfile.mkdtemp(prefix="landlock-scratch-"))
    forbidden = Path(tempfile.mkdtemp(prefix="landlock-forbidden-"))
    try:
        policy = SandboxPolicy(allowed_root=scratch)
        result = _spawn_confined(policy, WRITE_BODY.format(target=str(forbidden)))
        assert "BLOCKED" in result.stdout, (
            f"the sandbox let the child write outside its root: "
            f"rc={result.returncode} out={result.stdout!r} err={result.stderr!r}"
        )
        assert "PermissionError" in result.stdout or "OSError" in result.stdout
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
        shutil.rmtree(forbidden, ignore_errors=True)


@pytest.mark.skipif(
    not LINUX_X86_64,
    reason="Landlock only available on Linux x86_64 / aarch64",
)
def test_landlock_allows_write_inside_allowed_root():
    """The mirror image: writing inside the allowed root must still work.

    Without this, an over-restrictive ruleset would look just like a working one.
    """
    from kairos.sandbox import SandboxPolicy

    scratch = Path(tempfile.mkdtemp(prefix="landlock-allow-"))
    try:
        policy = SandboxPolicy(allowed_root=scratch)
        result = _spawn_confined(policy, WRITE_BODY.format(target=str(scratch)))
        assert "WROTE" in result.stdout, (
            f"the sandbox blocked a legitimate write inside its root: "
            f"rc={result.returncode} out={result.stdout!r} err={result.stderr!r}"
        )
        assert (scratch / "landlock-probe.txt").exists()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


@pytest.mark.skipif(
    not LINUX_X86_64,
    reason="Landlock only available on Linux x86_64 / aarch64",
)
def test_building_a_ruleset_does_not_confine_the_parent():
    """Building the ruleset must not sandbox this process.

    The implementation restricts the *child* through ``preexec_fn``; calling
    ``restrict_self`` in the parent was a bug, and it is invisible to every other
    test because it would only break the process that builds the ruleset.
    """
    from kairos.sandbox import _linux_landlock_sandbox

    scratch = Path(tempfile.mkdtemp(prefix="landlock-parent-"))
    outside = Path(tempfile.mkdtemp(prefix="landlock-parent-outside-"))
    try:
        from kairos.sandbox import SandboxPolicy

        fd = _linux_landlock_sandbox(SandboxPolicy(allowed_root=scratch))
        if fd is None:
            pytest.skip("Landlock is not available on this kernel")
        try:
            # This is the parent process; it must still be unrestricted.
            (outside / "still-writable.txt").write_text("ok")
        finally:
            os.close(fd)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
        shutil.rmtree(outside, ignore_errors=True)
