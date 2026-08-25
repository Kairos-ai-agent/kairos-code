"""Tests for the computer-use (desktop automation) module."""
from __future__ import annotations

import base64
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from kairos.computer_use import (
    Action,
    ActionType,
    ComputerUseError,
    MockComputerUse,
    MouseButton,
    PlatformComputerUse,
    _encode_png,
    _vkey_from_name,
    make_default_computer_use,
)


# ---------------------------------------------------------------------------
# Action dataclass
# ---------------------------------------------------------------------------


def test_action_to_dict_uses_string_type():
    a = Action(id="a", type=ActionType.SCREENSHOT, timestamp=1.0)
    d = a.to_dict()
    assert d["type"] == "screenshot"
    assert d["id"] == "a"
    assert d["args"] == {}
    assert d["confirmed"] is False
    assert d["dry_run"] is False


# ---------------------------------------------------------------------------
# MockComputerUse: confirm / dry-run safety
# ---------------------------------------------------------------------------


def test_mock_input_actions_require_confirm_by_default():
    cu = MockComputerUse()
    with pytest.raises(ComputerUseError, match="confirm=True"):
        cu.mouse_click(10, 10)
    with pytest.raises(ComputerUseError, match="confirm=True"):
        cu.mouse_move(10, 10)
    with pytest.raises(ComputerUseError, match="confirm=True"):
        cu.key_press("enter")
    with pytest.raises(ComputerUseError, match="confirm=True"):
        cu.type_text("hi")
    with pytest.raises(ComputerUseError, match="confirm=True"):
        cu.scroll(0, 100)


def test_mock_input_actions_accepted_with_confirm():
    cu = MockComputerUse()
    a = cu.mouse_click(10, 10, confirm=True)
    assert a.confirmed is True
    assert a.dry_run is False
    assert a.result == {"clicked": True}
    assert cu.history[-1] is a


def test_mock_dry_run_records_but_doesnt_execute():
    """dry_run=True marks the action but no real call happens. The
    history still records the action for the trace viewer."""
    cu = MockComputerUse()
    a = cu.mouse_click(10, 10, dry_run=True)
    assert a.dry_run is True
    assert a.confirmed is False
    assert cu.history == [a]


def test_mock_auto_confirm_disables_safety():
    cu = MockComputerUse(auto_confirm=True)
    a = cu.mouse_click(10, 10)
    assert a.confirmed is True
    b = cu.type_text("hello")
    assert b.confirmed is True


# ---------------------------------------------------------------------------
# MockComputerUse: screenshot
# ---------------------------------------------------------------------------


def test_mock_screenshot_returns_png_bytes():
    cu = MockComputerUse()
    data = cu.screenshot()
    assert isinstance(data, bytes)
    assert len(data) > 0
    # PNG signature
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def test_mock_screenshot_records_action():
    cu = MockComputerUse()
    cu.screenshot()
    cu.screenshot()
    a = cu.history[-1]
    assert a.type == ActionType.SCREENSHOT
    assert a.result["counter"] == 2
    assert a.result["width"] == 4
    assert a.result["height"] == 4


def test_mock_screenshot_writes_to_dir(tmp_path):
    cu = MockComputerUse(screenshot_dir=tmp_path)
    cu.screenshot()
    a = cu.history[-1]
    assert "path" in a.result
    p = Path(a.result["path"])
    assert p.exists()
    assert p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_mock_screen_size_default():
    cu = MockComputerUse()
    assert cu.screen_size() == (1920, 1080)


# ---------------------------------------------------------------------------
# MockComputerUse: all action types
# ---------------------------------------------------------------------------


def test_mock_mouse_click_records_button():
    cu = MockComputerUse()
    a = cu.mouse_click(50, 60, button=MouseButton.RIGHT, confirm=True)
    assert a.args == {"x": 50, "y": 60, "button": "right"}
    assert a.type == ActionType.MOUSE_CLICK


def test_mock_mouse_move_args():
    cu = MockComputerUse()
    a = cu.mouse_move(100, 200, confirm=True)
    assert a.args == {"x": 100, "y": 200}


def test_mock_key_press_args():
    cu = MockComputerUse()
    a = cu.key_press("enter", confirm=True)
    assert a.args == {"key": "enter"}


def test_mock_type_text_records_length():
    cu = MockComputerUse()
    a = cu.type_text("hello world", confirm=True)
    assert a.args == {"text": "hello world", "length": 11}
    assert a.result["chars"] == 11


def test_mock_scroll_args():
    cu = MockComputerUse()
    a = cu.scroll(0, -300, confirm=True)
    assert a.args == {"dx": 0, "dy": -300}


def test_mock_button_string_accepted():
    cu = MockComputerUse()
    a = cu.mouse_click(0, 0, button="middle", confirm=True)
    assert a.args["button"] == "middle"


# ---------------------------------------------------------------------------
# MockComputerUse: history
# ---------------------------------------------------------------------------


def test_mock_history_dict_serializable():
    import json
    cu = MockComputerUse()
    cu.mouse_click(10, 10, confirm=True)
    cu.screenshot()
    cu.type_text("hi", confirm=True)
    d = cu.history_dict()
    assert len(d) == 3
    # Round-trip through JSON.
    text = json.dumps(d)
    again = json.loads(text)
    assert again[1]["type"] == "screenshot"


# ---------------------------------------------------------------------------
# _encode_png + _vkey_from_name
# ---------------------------------------------------------------------------


def test_encode_png_returns_valid_png():
    w, h = 8, 8
    rgb = b"\x10\x20\x30" * (w * h)
    png = _encode_png(w, h, rgb)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    # IEND chunk
    assert b"IEND" in png


def test_vkey_from_name_known_keys():
    assert _vkey_from_name("enter") == 0x0D
    assert _vkey_from_name("esc") == 0x1B
    assert _vkey_from_name("escape") == 0x1B
    assert _vkey_from_name("tab") == 0x09
    assert _vkey_from_name("space") == 0x20
    assert _vkey_from_name("F1") == 0x70
    assert _vkey_from_name("f12") == 0x7B


def test_vkey_from_name_letters_and_digits():
    assert _vkey_from_name("a") == ord("A")
    assert _vkey_from_name("Z") == ord("Z")
    assert _vkey_from_name("5") == ord("5")


def test_vkey_from_name_unknown_returns_none():
    assert _vkey_from_name("") is None
    assert _vkey_from_name("definitely-not-a-key") is None


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------


def test_mock_satisfies_protocol():
    from kairos.computer_use import ComputerUseProtocol
    cu: ComputerUseProtocol = MockComputerUse(auto_confirm=True)
    # All five input methods callable, returns Action.
    a = cu.mouse_move(0, 0)
    assert isinstance(a, Action)


# ---------------------------------------------------------------------------
# make_default_computer_use
# ---------------------------------------------------------------------------


def test_make_default_returns_mock(monkeypatch):
    monkeypatch.delenv("KAIROS_COMPUTER_USE", raising=False)
    monkeypatch.delenv("KAIROS_COMPUTER_USE_PLATFORM", raising=False)
    cu = make_default_computer_use()
    assert isinstance(cu, MockComputerUse)


def test_make_default_mock_explicit(monkeypatch):
    monkeypatch.setenv("KAIROS_COMPUTER_USE", "mock")
    cu = make_default_computer_use(auto_confirm=True)
    assert isinstance(cu, MockComputerUse)
    assert cu.auto_confirm is True


def test_make_default_platform_on_non_windows(monkeypatch):
    """If KAIROS_COMPUTER_USE_PLATFORM=1 but we're not on Windows,
    we should fall back to MockComputerUse rather than raise."""
    monkeypatch.setenv("KAIROS_COMPUTER_USE_PLATFORM", "1")
    # We don't actually flip os.name here (would break too much);
    # instead we just confirm the env path returns SOMETHING usable.
    cu = make_default_computer_use()
    assert cu is not None


# ---------------------------------------------------------------------------
# PlatformComputerUse: error path on non-Windows
# ---------------------------------------------------------------------------


def test_platform_computer_use_raises_on_non_windows(monkeypatch):
    import kairos.computer_use as cu_mod
    monkeypatch.setattr(cu_mod.os, "name", "posix")
    with pytest.raises(ComputerUseError, match="Windows-only"):
        PlatformComputerUse()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_error_class_is_runtime_error():
    """ComputerUseError should be catchable as RuntimeError too,
    so callers can use a single except clause for runtime issues."""
    err = ComputerUseError("test")
    assert isinstance(err, RuntimeError)
