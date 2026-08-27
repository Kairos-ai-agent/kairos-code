"""Tests for the Firecracker (Linux + KVM) sandbox tier.

Like gVisor, Firecracker is a heavy install (system binary + KVM
kernel module). We test the detection + instructions surface
only — the actual VM is something the user opts into via a
containerd shim, not something Kairos spawns.
"""
from __future__ import annotations

import sys

import pytest

from kairos.sandbox import (
    describe_capabilities,
    firecracker_available,
    firecracker_install_instructions,
)


# Skip the whole module on non-Linux — Firecracker is Linux-only.
pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="Firecracker tier is Linux-only",
)


def test_firecracker_available_is_false_on_non_linux():
    """Function is callable; on non-Linux it returns False."""
    if sys.platform.startswith("linux"):
        return
    assert firecracker_available() is False


def test_describe_capabilities_includes_firecracker():
    caps = describe_capabilities()
    assert "firecracker" in caps
    assert isinstance(caps["firecracker"], bool)


def test_firecracker_install_instructions_are_helpful():
    text = firecracker_install_instructions()
    # Both the binary and the kernel module are mentioned
    assert "firecracker" in text
    assert "/dev/kvm" in text
    # At least one install method is documented
    assert "snap" in text or "tar.gz" in text


def test_firecracker_install_instructions_non_empty():
    text = firecracker_install_instructions()
    assert len(text) >= 200
