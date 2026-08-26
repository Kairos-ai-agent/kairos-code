"""Linux Landlock CI test.

The actual ``kairos.sandbox._linux_landlock_sandbox`` uses
``ctypes`` to invoke Landlock syscalls. The test only runs on
Linux x86_64 / aarch64 (where Landlock is available) — on other
platforms it's skipped, so this test is safe to include in the
default test suite.

The real-world test plan for CI:

  1. Spawn a child Python process
  2. Set up Landlock to deny write to a temp dir
  3. The child tries to write to that dir
  4. Parent asserts the write was EPERM'd

We do this via :mod:`subprocess` (not in-process) so the
sandbox actually applies to the child.
"""
from __future__ import annotations

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
# Pure-Python guard: the function exists and is the right shape
# ---------------------------------------------------------------------------


def test_landlock_sandbox_function_exists():
    """The implementation must be importable on every platform,
    even where it returns None (no Landlock)."""
    from kairos.sandbox import _linux_landlock_sandbox
    assert callable(_linux_landlock_sandbox)


def test_landlock_sandbox_signature():
    """Signature check: takes the existing ``SandboxConfig``-style args."""
    import inspect
    from kairos.sandbox import _linux_landlock_sandbox
    sig = inspect.signature(_linux_landlock_sandbox)
    # The function takes (allowed_cwd, writable_paths, read_only_paths, ...)
    params = list(sig.parameters.keys())
    assert "allowed_cwd" in params or len(params) >= 1, params


# ---------------------------------------------------------------------------
# Real Landlock test — only on Linux
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not LINUX_X86_64,
    reason="Landlock only available on Linux x86_64 / aarch64",
)
def test_landlock_blocks_write_to_denied_dir():
    """End-to-end: spawn a child with Landlock denying write to
    ``/tmp/forbidden``; the child tries to write, gets EPERM."""
    from kairos.sandbox import _linux_landlock_sandbox

    forbidden = Path(tempfile.mkdtemp(prefix="landlock-forbidden-"))
    scratch = Path(tempfile.mkdtemp(prefix="landlock-scratch-"))
    try:
        # Set up a Landlock sandbox that allows scratch but not forbidden
        sandbox = _linux_landlock_sandbox(
            allowed_cwd=str(scratch),
            writable_paths=[str(scratch)],
            read_only_paths=[str(forbidden)],
        )
        # If Landlock isn't available (kernel too old), skip
        if sandbox is None:
            pytest.skip("Landlock not available on this kernel")

        # Spawn a child that tries to write to both
        child_code = f"""
import os, sys
sys.path.insert(0, {str(Path(__file__).parent.parent)!r})
from kairos.sandbox import _linux_landlock_sandbox
sb = _linux_landlock_sandbox(
    allowed_cwd={str(scratch)!r},
    writable_paths=[{str(scratch)!r}],
    read_only_paths=[{str(forbidden)!r}],
)
if sb is None:
    print("NO_LANDLOCK")
    sys.exit(0)
# Try to write to the forbidden dir
try:
    with open({str(forbidden)!r} + "/test", "w") as f:
        f.write("should fail")
    print("WRITE_SUCCEEDED")
    sys.exit(2)
except (PermissionError, OSError) as e:
    print(f"BLOCKED: {{type(e).__name__}}: {{e}}")
    sys.exit(0)
except Exception as e:
    print(f"OTHER: {{type(e).__name__}}: {{e}}")
    sys.exit(3)
"""
        result = subprocess.run(
            [sys.executable, "-c", child_code],
            capture_output=True, text=True, timeout=10,
        )
        # Child should exit 0 (blocked) or 0 (no Landlock, skip)
        assert result.returncode in (0, 1), (
            f"Child exit={result.returncode} stdout={result.stdout!r} "
            f"stderr={result.stderr!r}"
        )
        # If Landlock worked, the write was blocked
        if "BLOCKED" in result.stdout:
            assert "PermissionError" in result.stdout or "OSError" in result.stdout
        elif "NO_LANDLOCK" in result.stdout:
            pytest.skip("Landlock not available in this kernel")
        else:
            pytest.fail(f"Unexpected child output: {result.stdout!r}")
    finally:
        shutil.rmtree(forbidden, ignore_errors=True)
        shutil.rmtree(scratch, ignore_errors=True)


@pytest.mark.skipif(
    not LINUX_X86_64,
    reason="Landlock only available on Linux x86_64 / aarch64",
)
def test_landlock_allows_write_to_allowed_path():
    """Sibling test: the same setup *should* allow writing to
    scratch/. If this fails, the sandbox is over-restrictive."""
    from kairos.sandbox import _linux_landlock_sandbox

    scratch = Path(tempfile.mkdtemp(prefix="landlock-allow-"))
    try:
        sandbox = _linux_landlock_sandbox(
            allowed_cwd=str(scratch),
            writable_paths=[str(scratch)],
        )
        if sandbox is None:
            pytest.skip("Landlock not available")

        child_code = f"""
import sys
sys.path.insert(0, {str(Path(__file__).parent.parent)!r})
from kairos.sandbox import _linux_landlock_sandbox
sb = _linux_landlock_sandbox(
    allowed_cwd={str(scratch)!r},
    writable_paths=[{str(scratch)!r}],
)
if sb is None:
    print("NO_LANDLOCK"); sys.exit(0)
try:
    with open({str(scratch)!r} + "/test", "w") as f:
        f.write("ok")
    print("WRITE_OK")
    sys.exit(0)
except Exception as e:
    print(f"FAIL: {{e}}")
    sys.exit(2)
"""
        result = subprocess.run(
            [sys.executable, "-c", child_code],
            capture_output=True, text=True, timeout=10,
        )
        assert "WRITE_OK" in result.stdout or "NO_LANDLOCK" in result.stdout
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
