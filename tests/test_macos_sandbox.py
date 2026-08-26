"""Tests for the macOS Seatbelt sandbox stub."""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from unittest.mock import patch

import pytest


# All macOS-specific tests skip on non-Darwin.
DARWIN_ONLY = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="macOS Seatbelt is a Darwin-only feature",
)


# ---------------------------------------------------------------------------
# Fake SandboxPolicy for tests
# ---------------------------------------------------------------------------


class FakePolicy:
    """A test double matching the real SandboxPolicy shape."""
    def __init__(self, allowed_root=None, network=True):
        self.allowed_root = allowed_root
        self.network = network


def test_macos_seatbelt_profile_allows_network_when_enabled(monkeypatch):
    from kairos.sandbox import _macos_seatbelt_profile
    monkeypatch.setattr("kairos.sandbox.sys.platform", "darwin")
    p = _macos_seatbelt_profile(FakePolicy(network=True))
    assert "(allow network*)" in p


def test_macos_seatbelt_profile_denies_network_when_disabled(monkeypatch):
    from kairos.sandbox import _macos_seatbelt_profile
    monkeypatch.setattr("kairos.sandbox.sys.platform", "darwin")
    p = _macos_seatbelt_profile(FakePolicy(network=False))
    assert "(allow network" not in p


# ---------------------------------------------------------------------------
# Profile generation (platform-independent — we always test the
# profile string content)
# ---------------------------------------------------------------------------


def test_macos_seatbelt_profile_denies_by_default(monkeypatch):
    """The profile starts with `(deny default)` so the rest of
    the rules are explicit allow-list."""
    from kairos.sandbox import _macos_seatbelt_profile
    monkeypatch.setattr("kairos.sandbox.sys.platform", "darwin")
    p = _macos_seatbelt_profile(FakePolicy())
    assert "(deny default)" in p
    assert "(version 1)" in p


def test_macos_seatbelt_profile_allows_process_basics(monkeypatch):
    """process-exec, process-fork, sysctl-read are always allowed
    (without them the subprocess can't even start)."""
    from kairos.sandbox import _macos_seatbelt_profile
    monkeypatch.setattr("kairos.sandbox.sys.platform", "darwin")
    p = _macos_seatbelt_profile(FakePolicy())
    assert "(allow process-exec)" in p
    assert "(allow process-fork)" in p
    assert "(allow sysctl-read)" in p


def test_macos_seatbelt_profile_allows_network_when_enabled(monkeypatch):
    from kairos.sandbox import _macos_seatbelt_profile
    monkeypatch.setattr("kairos.sandbox.sys.platform", "darwin")
    p = _macos_seatbelt_profile(FakePolicy(network=True))
    assert "(allow network*)" in p


def test_macos_seatbelt_profile_denies_network_when_disabled(monkeypatch):
    from kairos.sandbox import _macos_seatbelt_profile
    monkeypatch.setattr("kairos.sandbox.sys.platform", "darwin")
    p = _macos_seatbelt_profile(FakePolicy(network=False))
    assert "(allow network" not in p


def test_macos_seatbelt_profile_includes_cwd(monkeypatch):
    from kairos.sandbox import _macos_seatbelt_profile
    monkeypatch.setattr("kairos.sandbox.sys.platform", "darwin")
    # str() to avoid Windows Path backslash conversion
    p = _macos_seatbelt_profile(FakePolicy(allowed_root="/home/user/proj"))
    assert '/home/user/proj' in p
    assert "(allow file-read* (subpath" in p


def test_macos_seatbelt_profile_falls_back_to_permissive_without_cwd(monkeypatch):
    from kairos.sandbox import _macos_seatbelt_profile
    monkeypatch.setattr("kairos.sandbox.sys.platform", "darwin")
    p = _macos_seatbelt_profile(FakePolicy(allowed_root=None))
    # No cwd → permissive file rule
    assert "(allow file*)" in p
    # No subpath rules
    assert "subpath" not in p


def test_macos_seatbelt_profile_empty_on_non_darwin():
    from kairos.sandbox import _macos_seatbelt_profile
    # On Linux/Windows the function should refuse to build a profile
    p = _macos_seatbelt_profile(FakePolicy())
    assert p == ""


# ---------------------------------------------------------------------------
# Command wrapping
# ---------------------------------------------------------------------------


def test_wrap_command_in_sandbox_exec_with_profile(monkeypatch):
    from kairos.sandbox import wrap_command_in_sandbox_exec
    monkeypatch.setattr("kairos.sandbox.sys.platform", "darwin")
    out = wrap_command_in_sandbox_exec("ls -la", "(version 1)\n(deny default)")
    assert out.startswith("sandbox-exec -p '")
    assert "ls -la" in out
    # The profile is inline-quoted
    assert "(deny default)" in out


def test_wrap_command_in_sandbox_exec_escapes_single_quotes(monkeypatch):
    """A profile with embedded single quotes must round-trip
    safely through the shell quoting."""
    from kairos.sandbox import wrap_command_in_sandbox_exec
    monkeypatch.setattr("kairos.sandbox.sys.platform", "darwin")
    out = wrap_command_in_sandbox_exec(
        "ls -la",
        "(allow file-read* (subpath \"/work's dir\"))",
    )
    # The inner single quote is escaped with the standard
    # close-quote / escaped-quote / open-quote dance.
    assert "'\\''" in out


def test_wrap_command_in_sandbox_exec_no_profile_passthrough(monkeypatch):
    """When profile is empty, the command passes through unchanged."""
    from kairos.sandbox import wrap_command_in_sandbox_exec
    out = wrap_command_in_sandbox_exec("ls -la", "")
    assert out == "ls -la"


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_macos_seatbelt_available_false_on_non_darwin():
    from kairos.sandbox import macos_seatbelt_available
    assert macos_seatbelt_available() is False


@DARWIN_ONLY
def test_macos_seatbelt_available_true_when_binary_on_path():
    from kairos.sandbox import macos_seatbelt_available
    assert macos_seatbelt_available() is True


def test_macos_seatbelt_available_false_when_binary_missing(monkeypatch):
    """Even on Darwin, returns False if sandbox-exec isn't on PATH."""
    from kairos.sandbox import macos_seatbelt_available
    monkeypatch.setattr("kairos.sandbox.sys.platform", "darwin")
    with patch("kairos.sandbox.shutil.which", return_value=None):
        assert macos_seatbelt_available() is False


# ---------------------------------------------------------------------------
# Integration: prepare_popen_kwargs adds the profile on Darwin
# ---------------------------------------------------------------------------


def test_apply_to_subprocess_adds_seatbelt_profile_on_darwin(monkeypatch):
    """When the host is Darwin, apply_to_subprocess attaches the
    Seatbelt profile under __kairos_seatbelt_profile (consumed by
    the terminal tool when it spawns)."""
    from kairos.sandbox import SandboxPolicy, apply_to_subprocess
    monkeypatch.setattr("kairos.sandbox.sys.platform", "darwin")
    policy = SandboxPolicy(allowed_root="/work", network=False)
    kwargs = apply_to_subprocess(policy, {"shell": "/bin/sh -c 'ls'"})
    assert "__kairos_seatbelt_profile" in kwargs
    profile = kwargs["__kairos_seatbelt_profile"]
    assert "(deny default)" in profile
    assert "(allow network" not in profile  # network denied
    assert "/work" in profile


def test_apply_to_subprocess_skips_seatbelt_on_linux(monkeypatch):
    from pathlib import Path as _P
    from kairos.sandbox import SandboxPolicy, apply_to_subprocess
    # On non-darwin the Seatbelt slot is absent
    kwargs = apply_to_subprocess(SandboxPolicy(allowed_root=_P("/work")),
                                  {"shell": "ls"})
    if sys.platform != "darwin":
        assert "__kairos_seatbelt_profile" not in kwargs
