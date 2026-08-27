"""Tests for the gVisor (runsc) Linux sandbox tier.

gVisor is a heavy install (system package, not pip). These tests
cover the detection + instructions surface so the Settings UI
can show them even on hosts that don't have runsc installed.
"""
from __future__ import annotations

import sys

import pytest

from kairos.sandbox import (
    describe_capabilities,
    gvisor_available,
    gvisor_install_instructions,
)


# Skip the whole module on non-Linux — gVisor is Linux-only.
pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="gVisor tier is Linux-only",
)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_gvisor_available_is_false_on_non_linux():
    """Function is callable; on non-Linux it returns False."""
    if sys.platform.startswith("linux"):
        return
    assert gvisor_available() is False


def test_describe_capabilities_includes_gvisor():
    caps = describe_capabilities()
    assert "gvisor" in caps
    assert isinstance(caps["gvisor"], bool)


# ---------------------------------------------------------------------------
# Install instructions surface
# ---------------------------------------------------------------------------


def test_gvisor_install_instructions_are_helpful():
    text = gvisor_install_instructions()
    # The instructions must mention both the package install and the
    # Docker registration — incomplete instructions are a
    # common failure mode.
    assert "runsc" in text
    assert "apt-get install" in text or "apt install" in text
    assert "runsc install" in text
    assert "docker" in text.lower()
    # The "try it" smoke test is documented
    assert "--runtime=runsc" in text


def test_gvisor_install_instructions_non_empty():
    text = gvisor_install_instructions()
    # The instructions should be substantial — at least 200 chars
    # of actionable content.
    assert len(text) >= 200
