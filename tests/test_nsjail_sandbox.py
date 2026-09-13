"""Tests for the nsjail Linux sandbox tier."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from kairos.sandbox import (
    SandboxPolicy,
    describe_capabilities,
    nsjail_available,
    nsjail_config_path,
    _linux_nsjail_profile,
    wrap_command_in_nsjail,
)


# Skip the whole module on non-Linux — nsjail is Linux-only.
pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="nsjail tier is Linux-only",
)


# ---------------------------------------------------------------------------
# Feature detection
# ---------------------------------------------------------------------------


def test_nsjail_available_is_false_on_non_linux():
    """Function is callable; on Windows it returns False regardless."""
    if sys.platform.startswith("linux"):
        return  # only meaningful on non-Linux
    assert nsjail_available() is False


def test_describe_capabilities_includes_nsjail():
    caps = describe_capabilities()
    assert "nsjail" in caps
    assert isinstance(caps["nsjail"], bool)
    # Linux exposes nsjail key
    if sys.platform.startswith("linux"):
        # The flag itself may be False if nsjail isn't installed; we
        # just check the key is present and the right type.
        assert caps["nsjail"] in (True, False)


# ---------------------------------------------------------------------------
# Profile generation
# ---------------------------------------------------------------------------


def test_nsjail_profile_basic():
    """Profile contains the required nsjail fields and respects the
    policy's resource limits."""
    policy = SandboxPolicy(
        allowed_root=Path("/tmp"),
        max_memory_mb=256,
        max_cpu_seconds=5,
        max_processes=32,
    )
    profile = _linux_nsjail_profile(policy)
    # Top-level structural sanity
    assert 'name: "kairos-agent"' in profile
    assert "mode: ONCE" in profile
    # rlimit_as is the memory limit in MiB
    assert "rlimit_as: 256" in profile
    assert "rlimit_cpu: 5" in profile
    assert "rlimit_nproc: 32" in profile
    # read-only mount of allowed_root
    assert 'mount_rdonly: "/tmp"' in profile
    # tmpfs for /tmp with size cap
    assert "tmpfs: /tmp:size=16m" in profile


def test_nsjail_profile_no_network_by_default():
    policy = SandboxPolicy(allowed_root=Path("/srv/proj"), network=False)
    profile = _linux_nsjail_profile(policy)
    # Default should be deny-network — no resolv.conf / ssl mount
    assert "# network: blocked" in profile
    assert "/etc/resolv.conf" not in profile
    assert "/etc/ssl/certs" not in profile


def test_nsjail_profile_with_network_allowed():
    policy = SandboxPolicy(allowed_root=Path("/srv/proj"), network=True)
    profile = _linux_nsjail_profile(policy)
    assert "/etc/resolv.conf" in profile
    assert "/etc/ssl/certs" in profile


def test_nsjail_profile_emits_deny_paths():
    policy = SandboxPolicy(
        allowed_root=Path("/srv/proj"),
        deny_paths=["/root/.ssh", "/etc/shadow"],
    )
    profile = _linux_nsjail_profile(policy)
    assert 'deny_src: "/root/.ssh"' in profile
    assert 'deny_src: "/etc/shadow"' in profile


def test_nsjail_profile_handles_missing_allowed_root():
    """An unset allowed_root leaves the mount commented out.

    Note that ``Path()`` is *not* an unset root: it means ``.`` and is always
    truthy, so the generator legitimately emits a real mount for it. The earlier
    version of this test asserted the opposite, and because this whole module is
    skipped on non-Linux it only ever failed for Linux contributors.
    """
    policy = SandboxPolicy(allowed_root="")  # unset
    profile = _linux_nsjail_profile(policy)
    # The placeholder comment should be there instead of a real mount
    assert "mount_rdonly" in profile
    assert "# mount_rdonly: (none)" in profile
    assert 'mount_rdonly: "' not in profile


# ---------------------------------------------------------------------------
# Command wrapping
# ---------------------------------------------------------------------------


def test_wrap_command_in_nsjail_returns_correct_argv():
    """wrap_command_in_nsjail(['echo', 'hi']) → ['nsjail', '--config',
    '<tmp>', '--', 'echo', 'hi']."""
    policy = SandboxPolicy(allowed_root=Path("/tmp"))
    argv = wrap_command_in_nsjail(["echo", "hello"], policy)
    assert argv[0] == "nsjail"
    assert argv[1] == "--config"
    cfg_path = argv[2]
    assert Path(cfg_path).exists()  # cfg was written
    # the user's command comes after the '--' delimiter
    assert "--" in argv
    sep_idx = argv.index("--")
    assert argv[sep_idx + 1:] == ["echo", "hello"]
    # Cleanup
    os.unlink(cfg_path)


def test_nsjail_config_path_creates_unique_paths():
    """Two calls return different temp paths."""
    p1 = nsjail_config_path(SandboxPolicy(allowed_root=Path("/tmp")))
    p2 = nsjail_config_path(SandboxPolicy(allowed_root=Path("/tmp")))
    try:
        assert p1 != p2
    finally:
        # mkstemp creates the file; close the fd (mkstemp doesn't but
        # mkstemp returns fd; nsjail_config_path calls os.close(fd) so
        # the file is closed and just sitting on disk).
        for p in (p1, p2):
            if Path(p).exists():
                os.unlink(p)
