"""Tests for cross-platform OS sandboxing."""
from __future__ import annotations

import sys
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from kairos.sandbox import (
    DEFAULT_DENY_PATTERNS,
    SandboxPolicy,
    _linux_landlock_sandbox,
    apply_to_subprocess,
    assign_child_to_sandbox,
    check_policy,
    describe_capabilities,
    landlock_available,
    windows_job_object_available,
)


def test_describe_capabilities_returns_platform():
    caps = describe_capabilities()
    assert caps["platform"] == sys.platform
    assert caps["deny_list"] is True
    # OS-specific keys exist but may be False.
    assert "landlock" in caps
    assert "job_object" in caps


def test_landlock_only_on_linux():
    if sys.platform.startswith("linux"):
        # Just check the function returns a bool without raising.
        assert isinstance(landlock_available(), bool)
    else:
        assert landlock_available() is False


def test_job_object_only_on_windows():
    if sys.platform.startswith("win"):
        assert isinstance(windows_job_object_available(), bool)
    else:
        assert windows_job_object_available() is False


def test_check_policy_blocks_dangerous():
    with tempfile.TemporaryDirectory() as d:
        policy = SandboxPolicy(allowed_root=Path(d))
        assert check_policy(policy, "rm -rf /") is not None
        assert check_policy(policy, "rm -rf ~") is not None
        assert check_policy(policy, "del /f C:\\Windows") is not None
        assert check_policy(policy, "format C:") is not None


def test_check_policy_passes_safe_commands():
    with tempfile.TemporaryDirectory() as d:
        policy = SandboxPolicy(allowed_root=Path(d))
        assert check_policy(policy, "ls -la") is None
        assert check_policy(policy, "echo hello world") is None
        assert check_policy(policy, "git status") is None
        assert check_policy(policy, "python -m pytest -v") is None


def test_check_policy_extra_deny_augments_builtin():
    with tempfile.TemporaryDirectory() as d:
        policy = SandboxPolicy(
            allowed_root=Path(d),
            extra_deny=[r"\.ssh\b"],
        )
        assert check_policy(policy, "ls ~/.ssh/id_rsa") is not None
        # Built-ins still work.
        assert check_policy(policy, "rm -rf /") is not None
        # And unrelated things still pass.
        assert check_policy(policy, "echo hi") is None


def test_sandbox_policy_root_resolved():
    with tempfile.TemporaryDirectory() as d:
        rel_root = Path(d)
        policy = SandboxPolicy(allowed_root=rel_root)
        # allowed_root is stored as the user passed it; the caller
        # (TerminalTool) is responsible for resolving before
        # checking paths. SandboxPolicy itself does not mutate.
        assert policy.allowed_root == rel_root
        # But it must be a Path.
        assert isinstance(policy.allowed_root, Path)


def test_apply_to_subprocess_returns_dict():
    """apply_to_subprocess should not mutate the original kwargs
    object unexpectedly, and should always return a dict."""
    with tempfile.TemporaryDirectory() as d:
        policy = SandboxPolicy(allowed_root=Path(d))
        kwargs = {"stdout": "PIPE"}
        result = apply_to_subprocess(policy, kwargs)
        assert isinstance(result, dict)
        # The original stdout is preserved.
        assert result.get("stdout") == "PIPE"


def test_assign_child_to_sandbox_is_noop_on_unsupported_platform():
    """On a non-Windows host, calling assign_child_to_sandbox must
    not raise — it's a no-op. We don't assert success."""
    with tempfile.TemporaryDirectory() as d:
        policy = SandboxPolicy(allowed_root=Path(d))
        # Any PID works because we won't actually attach.
        assign_child_to_sandbox(policy, pid=99999)
        # No exception = pass.


def test_default_deny_patterns_is_nonempty():
    assert len(DEFAULT_DENY_PATTERNS) > 5
    # Every entry should be a non-empty string.
    for p in DEFAULT_DENY_PATTERNS:
        assert isinstance(p, str)
        assert p.strip()


def test_check_policy_handles_empty_command():
    with tempfile.TemporaryDirectory() as d:
        policy = SandboxPolicy(allowed_root=Path(d))
        assert check_policy(policy, "") is None


def test_sandbox_policy_network_default_off():
    with tempfile.TemporaryDirectory() as d:
        policy = SandboxPolicy(allowed_root=Path(d))
        assert policy.network is False
        policy_on = SandboxPolicy(allowed_root=Path(d), network=True)
        assert policy_on.network is True


# ---------------------------------------------------------------------------
# Landlock ctypes implementation
# ---------------------------------------------------------------------------


def test_landlock_sandbox_returns_none_on_non_linux():
    """On non-Linux platforms, _linux_landlock_sandbox short-circuits
    via the `landlock_available()` gate and returns None without
    touching ctypes. This test runs on every platform."""
    with tempfile.TemporaryDirectory() as d:
        policy = SandboxPolicy(allowed_root=Path(d))
        # Even on Linux, this won't apply the sandbox (no real
        # Landlock kernel in CI), but the call must not raise.
        result = _linux_landlock_sandbox(policy)
        if sys.platform != "linux":
            assert result is None
        else:
            # On real Linux: may return fd (success) or None
            # (no Landlock support, e.g. old kernel). Either is
            # acceptable — we only assert the call doesn't raise.
            assert result is None or isinstance(result, int)


def test_landlock_sandbox_without_allowed_root_returns_none():
    """A policy with no allowed_root is a no-op (return None)."""
    policy = SandboxPolicy(allowed_root=Path(""))
    assert _linux_landlock_sandbox(policy) is None


def test_landlock_sandbox_does_not_raise_on_bad_root():
    """If the allowed_root doesn't exist, the function returns None
    silently (the open() syscall fails, errno is logged, we bail)."""
    policy = SandboxPolicy(allowed_root=Path("/this/does/not/exist/at/all"))
    # Must not raise.
    result = _linux_landlock_sandbox(policy)
    assert result is None


def test_landlock_sandbox_arch_gate(monkeypatch):
    """On a platform we don't know (e.g. riscv64), the function
    returns None before touching ctypes. We simulate this by
    making `landlock_available()` return True but reporting an
    unknown arch."""
    if sys.platform != "linux":
        pytest.skip("Linux-only arch gate test")
    import platform
    import kairos.sandbox as sb

    real_machine = platform.machine

    def fake_machine():
        return "riscv64"
    monkeypatch.setattr(platform, "machine", fake_machine)
    monkeypatch.setattr(sb, "landlock_available", lambda: True)
    with tempfile.TemporaryDirectory() as d:
        policy = SandboxPolicy(allowed_root=Path(d))
        assert _linux_landlock_sandbox(policy) is None
    # Restore for subsequent tests (monkeypatch should auto-restore).
