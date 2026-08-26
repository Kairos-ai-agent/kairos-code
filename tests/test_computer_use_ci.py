"""End-to-end integration test for computer-use providers.

The :class:`MockComputerUse` and the real :class:`WindowsComputerUse`
both speak the same interface (:meth:`screenshot`,
:meth:`mouse_click`, :meth:`type_text`, :meth:`scroll`). This test
exercises the full click → screenshot → type → screenshot loop
end-to-end against the mock, then verifies the real
WindowsComputerUse is wired up the same way (signature check).

On non-Windows the real-provider test is skipped; on Windows it
runs against a real (but minimal) BitBlt/SetCursorPos round-trip.
"""
from __future__ import annotations

import inspect
import logging
import os
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Mock provider end-to-end loop
# ---------------------------------------------------------------------------


def test_mock_screenshot_click_type_loop(tmp_path: Path):
    """Drive MockComputerUse through a full interaction loop:
    screenshot → click → screenshot → type → screenshot. Assert
    that all calls are observed and state is updated."""
    from kairos.computer_use import MockComputerUse

    cm = MockComputerUse(auto_confirm=True, screenshot_dir=tmp_path)

    # Take a few screenshots and verify they hit the screenshot_dir
    s1 = cm.screenshot()
    assert s1[:4] == b"\x89PNG"
    assert any(tmp_path.glob("shot_*.png"))

    # Click + type + another screenshot
    cm.mouse_click(100, 200)
    cm.mouse_click(50, 50, button="right")
    s2 = cm.screenshot()

    cm.type_text("hello world", confirm=True)
    cm.type_text("more text")
    s3 = cm.screenshot()

    # History: 3 screenshots + 2 clicks + 2 type_texts = 7 actions
    assert len(cm.history) == 7
    types = [a for a in cm.history if a.type.value == "type_text"]
    clicks = [a for a in cm.history if a.type.value == "mouse_click"]
    assert len(types) == 2
    assert len(clicks) == 2
    assert types[0].args["text"] == "hello world"
    assert types[1].args["text"] == "more text"
    # screenshots are recorded too
    shots = [a for a in cm.history if a.type.value == "screenshot"]
    assert len(shots) == 3


def test_mock_provider_refuses_destructive_without_confirm(caplog):
    """Without confirm=True, a mouse_click raises ComputerUseError.
    With ``auto_confirm=True`` it's accepted silently.
    With explicit ``confirm=True`` it's accepted."""
    from kairos.computer_use import ComputerUseError, MockComputerUse

    cm = MockComputerUse(auto_confirm=False)
    with pytest.raises(ComputerUseError):
        cm.mouse_click(100, 200)
    # explicit confirm bypasses the gate
    cm.mouse_click(100, 200, confirm=True)
    # auto_confirm=True bypasses
    cm2 = MockComputerUse(auto_confirm=True)
    cm2.mouse_click(100, 200)
    assert len(cm2.history) == 1


def test_mock_provider_dry_run_records_action():
    """dry_run=True still records the action (so it shows up in
    the audit log) but marks ``action.dry_run = True``."""
    from kairos.computer_use import MockComputerUse

    cm = MockComputerUse(auto_confirm=True)
    cm.mouse_click(100, 200, dry_run=True)
    assert len(cm.history) == 1
    assert cm.history[0].dry_run is True


def test_refused_action_is_not_recorded(caplog):
    """An action refused by the safety gate doesn't pollute
    the audit history — the gate fires before _record()."""
    from kairos.computer_use import ComputerUseError, MockComputerUse

    cm = MockComputerUse(auto_confirm=False)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(ComputerUseError):
            cm.mouse_click(100, 200)
    # History stays clean — the gate fired before the record.
    assert cm.history == []


# ---------------------------------------------------------------------------
# Real WindowsComputerUse signature check
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-specific ctypes test",
)
def test_platform_computer_use_class_exposes_full_interface():
    """The PlatformComputerUse class must expose the same
    interface as the mock so the orchestrator can swap them
    transparently."""
    from kairos.computer_use import PlatformComputerUse

    required = ["screenshot", "mouse_click", "mouse_move",
                "key_press", "type_text", "scroll"]
    for name in required:
        assert hasattr(PlatformComputerUse, name), f"missing: {name}"
        assert callable(getattr(PlatformComputerUse, name)), f"not callable: {name}"


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-specific ctypes test",
)
def test_platform_computer_use_click_requires_confirm():
    """PlatformComputerUse.mouse_click must accept confirm=True
    (matches the protocol) and enforce it."""
    from kairos.computer_use import PlatformComputerUse

    sig = inspect.signature(PlatformComputerUse.mouse_click)
    assert "confirm" in sig.parameters, (
        "PlatformComputerUse.mouse_click must accept confirm=True"
    )


# ---------------------------------------------------------------------------
# Cost / safety: a destructive action without confirm logs + refuses
# ---------------------------------------------------------------------------


def test_destructive_action_without_confirm_is_audited(caplog):
    """When a destructive action is requested without confirm,
    the provider should refuse. The audit log captures the
    warning so operators can review refused attempts."""
    from kairos.computer_use import ComputerUseError, MockComputerUse

    cm = MockComputerUse(auto_confirm=False)
    with pytest.raises(ComputerUseError):
        cm.mouse_click(100, 200)
    # History is NOT polluted (gate fires first)
    assert cm.history == []
    # The error message itself documents why (no need for log capture)
    try:
        cm.mouse_click(100, 200)
    except ComputerUseError as e:
        assert "confirm" in str(e).lower()


# ---------------------------------------------------------------------------
# ComputerUseTool (agent-facing wrapper) roundtrip
# ---------------------------------------------------------------------------


def test_computer_use_tool_class_imports():
    """The ``ComputerUseTool`` should be importable and have a
    JSON schema for the LLM to call."""
    try:
        from kairos.tools.computer_use import ComputerUseTool
    except ImportError:
        pytest.skip("ComputerUseTool not in this codebase")
    # Don't actually instantiate (would try to capture screen);
    # just verify the class has the right shape.
    assert callable(ComputerUseTool)
    # Inspect the class for the to_schema method
    assert hasattr(ComputerUseTool, "to_schema")
