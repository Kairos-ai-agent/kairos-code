"""Test for the TUI screenshot helper.

Boots the TUI in headless mode, drives a few interactions, and
asserts the SVG output is well-formed and contains the seeded
turns.
"""
from __future__ import annotations

import asyncio
import html
import re
from pathlib import Path

import pytest

from kairos.tui import BackendClient, TuiController, build_textual_app
from kairos.tui.__main__ import StaticBackend, _capture_screenshot


def _svg_text(out: Path) -> str:
    """Return the SVG file's text content with HTML entities decoded
    and non-breaking spaces normalized to regular spaces (so
    substring checks can use plain ASCII)."""
    raw = out.read_text(encoding="utf-8")
    fragments = re.findall(r"<text[^>]*>([^<]*)</text>", raw)
    decoded = html.unescape("".join(fragments))
    # Normalize non-breaking space → regular space for substring tests.
    return decoded.replace("\xa0", " ")


def test_screenshot_creates_svg(tmp_path: Path):
    out = tmp_path / "tui.svg"
    backend = StaticBackend()
    written = asyncio.run(_capture_screenshot(out, backend, project_id="p1",
                                              interactions=2))
    assert Path(written) == out
    assert out.is_file()
    assert out.stat().st_size > 1000  # not empty


def test_screenshot_svg_contains_seeded_turns(tmp_path: Path):
    out = tmp_path / "tui.svg"
    backend = StaticBackend()
    asyncio.run(_capture_screenshot(out, backend, project_id="p1",
                                    interactions=1))
    text = _svg_text(out)
    # Header line (built from controller.state.header())
    assert "Kairos" in text
    assert "sandbox" in text
    # Seeded user message + tool call
    assert "small benchmark" in text
    assert "file_write" in text
    # The composer placeholder is also visible
    assert "Type a message or" in text or "/command" in text


def test_screenshot_includes_interaction_turns(tmp_path: Path):
    out = tmp_path / "tui.svg"
    backend = StaticBackend()
    asyncio.run(_capture_screenshot(out, backend, project_id="p1",
                                    interactions=3))
    text = _svg_text(out)
    # each "Continue with test N" should be sent through
    for n in (1, 2, 3):
        assert f"test {n}" in text
    # And the stub backend echoes back each one
    assert text.count("echo: Continue with test") == 3


def test_screenshot_strips_ansi_escapes_in_turn_rendering(tmp_path: Path):
    """Rich / Textual log already strips ANSI; assert no raw ESC
    bytes leak into the rendered SVG."""
    out = tmp_path / "tui.svg"
    backend = StaticBackend()
    asyncio.run(_capture_screenshot(out, backend, project_id="p1",
                                    interactions=0))
    raw = out.read_bytes()
    # ESC = 0x1B
    assert b"\x1b" not in raw, "ANSI ESC byte leaked into SVG"


def test_screenshot_svg_is_valid_xml(tmp_path: Path):
    """Quick well-formedness check via stdlib XML parser."""
    import xml.etree.ElementTree as ET
    out = tmp_path / "tui.svg"
    backend = StaticBackend()
    asyncio.run(_capture_screenshot(out, backend, project_id="p1",
                                    interactions=0))
    # parse raises on malformed XML
    ET.fromstring(out.read_text(encoding="utf-8"))
