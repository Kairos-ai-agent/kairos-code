"""The computer_use tool: what the bundled skill promised all along.

The backend under test is the real `MockComputerUse` from
`kairos/computer_use.py` -- it records actions and touches nothing, which is
exactly what a test needs. The real `SendInput` backend is never constructed
here: a test that moves the developer's mouse is a test that fights the person
running it.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kairos.sentinel import is_egress
from kairos.tools.computer_tool import ComputerTool


@pytest.fixture(autouse=True)
def _mock_backend(monkeypatch):
    monkeypatch.setenv("KAIROS_COMPUTER_USE", "mock")


@pytest.fixture
def tool(tmp_path):
    return ComputerTool(out_dir=tmp_path)


def run(tool, **kwargs):
    return asyncio.run(tool.execute(**kwargs))


# --------------------------------------------------------------------------
# capture
# --------------------------------------------------------------------------

def test_capture_writes_a_png_and_names_the_file(tool, tmp_path):
    res = run(tool, action="capture")
    assert res.success, res.error
    path = Path(res.metadata["path"])
    assert path.exists()
    assert path.read_bytes().startswith(b"\x89PNG"), "must be a real PNG"
    assert str(path) in res.output
    assert str(tmp_path) in str(path), "screenshots stay out of the workspace"


def test_capture_says_it_ran_against_the_mock(tool):
    """A mock that silently reports 'clicked' is worse than no tool at all."""
    res = run(tool, action="capture")
    assert res.metadata["backend"] == "MockComputerUse"
    assert res.metadata["mock"] is True
    assert "MOCK" in res.output


def test_a_real_backend_is_reported_as_real(tmp_path):
    """The name is what the model is told to trust, so it must be accurate."""

    class PlatformComputerUse:  # noqa: N801 - mirrors the real class name
        def screenshot(self):
            return b"\x89PNG\r\n\x1a\n" + b"\x00" * 8

        def screen_size(self):
            return (1920, 1080)

        def history_dict(self):
            return []

    tool = ComputerTool(backend=PlatformComputerUse(), out_dir=tmp_path)
    res = run(tool, action="capture")
    assert res.success
    assert res.metadata["mock"] is False
    assert res.metadata["backend"] == "PlatformComputerUse"
    assert "MOCK" not in res.output


def test_screen_size(tool):
    res = run(tool, action="screen_size")
    assert res.success
    assert res.metadata["size"] == [1920, 1080]


# --------------------------------------------------------------------------
# actions
# --------------------------------------------------------------------------

def test_click_and_type_reach_the_backend(tool):
    assert run(tool, action="click", x=10, y=20).success
    assert run(tool, action="type", text="hello").success
    hist = run(tool, action="history")
    assert hist.success
    assert hist.metadata["count"] >= 2
    assert "click" in hist.output


def test_mouse_button_is_validated(tool):
    res = run(tool, action="click", x=1, y=2, button="elbow")
    assert not res.success
    assert "button must be one of" in res.error


def test_typed_text_is_never_echoed_back(tool):
    res = run(tool, action="type", text="hunter2-not-in-the-transcript")
    assert res.success
    assert "hunter2" not in res.output
    assert res.metadata["chars"] == len("hunter2-not-in-the-transcript")


def test_dry_run_is_marked_in_the_result(tool):
    res = run(tool, action="click", x=3, y=4, dry_run=True)
    assert res.success
    assert res.metadata["dry_run"] is True
    assert "dry run" in res.output


def test_key_and_scroll(tool):
    assert run(tool, action="key", key="enter").success
    assert run(tool, action="scroll", dx=0, dy=-3).success


def test_history_is_honest_when_empty(tool):
    res = run(tool, action="history")
    assert res.success
    assert "no desktop actions" in res.output


# --------------------------------------------------------------------------
# refusals
# --------------------------------------------------------------------------

def test_unknown_action_lists_the_real_ones(tool):
    res = run(tool, action="levitate")
    assert not res.success
    assert "unknown action" in res.error
    assert "capture" in res.error


def test_click_without_coordinates(tool):
    res = run(tool, action="click")
    assert not res.success
    assert "x and y" in res.error


def test_type_without_text_and_key_without_key(tool):
    assert "text is required" in run(tool, action="type").error
    assert "key is required" in run(tool, action="key").error


def test_scroll_without_a_direction(tool):
    res = run(tool, action="scroll")
    assert not res.success
    assert "dx and/or dy" in res.error


# --------------------------------------------------------------------------
# the gate contract
# --------------------------------------------------------------------------

def test_capturing_the_screen_is_not_egress_but_acting_on_it_is():
    for action in ("capture", "history", "screen_size"):
        egress, why = is_egress("computer_use", {"action": action})
        assert not egress, action + " must stay allowed: " + why
    for action in ("click", "move", "type", "key", "scroll"):
        egress, why = is_egress("computer_use", {"action": action})
        assert egress, action + " can act on the desktop and must be refused"


def test_the_screen_is_a_taint_source_and_says_so():
    from kairos.taint import TaintTracker, classify

    assert classify("computer_use") == "screen"
    tracker = TaintTracker()
    tracker.mark_tool("computer_use")
    assert tracker.tainted
    assert "screen" in tracker.describe()


def test_the_tool_is_named_exactly_as_the_skill_promises():
    """The skill says `computer_use`; a rename here breaks the model silently."""
    assert ComputerTool.name == "computer_use"
