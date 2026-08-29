"""Tests for kairos.browser (R38.6 §32).

The BrowserManager requires Playwright + a Chromium binary,
which is heavy. We mock the Playwright objects to keep tests
fast and hermetic — the real Playwright launch is exercised
manually with a smoke run.
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from kairos.browser import (BrowserManager, DEFAULT_VIEWPORT,
                             IDLE_TIMEOUT_S, MAX_PROJECTS)


@pytest.fixture
def tmp_data_dir(tmp_path):
    return tmp_path


@pytest.fixture
def mock_pw():
    """A mock for the playwright async context."""
    pw = MagicMock()
    chromium = MagicMock()
    browser_ctx = AsyncMock()
    browser_ctx.pages = [AsyncMock()]
    chromium.launch_persistent_context = AsyncMock(
        return_value=browser_ctx,
    )
    pw.chromium = chromium
    pw.start = AsyncMock(return_value=pw)
    return pw


@pytest.mark.asyncio
async def test_manager_creates_profile_dir(tmp_data_dir, mock_pw):
    from kairos.browser import _get_playwright
    # Patch the lazy loader
    import kairos.browser as bm
    bm._playwright = lambda: mock_pw
    mgr = BrowserManager(data_dir=tmp_data_dir)
    await mgr.start()
    # No projects yet
    assert mgr._projects == {}
    profiles = tmp_data_dir / "browsers"
    assert profiles.exists()
    await mgr.stop()


@pytest.mark.asyncio
async def test_get_or_create_uses_persistent_context(tmp_data_dir, mock_pw):
    import kairos.browser as bm
    bm._playwright = lambda: mock_pw
    mgr = BrowserManager(data_dir=tmp_data_dir)
    await mgr.start()
    proj = await mgr.get_or_create("p1")
    assert proj.project_id == "p1"
    # launch_persistent_context was called with the profile dir
    expected_dir = str(tmp_data_dir / "browsers" / "p1")
    mock_pw.chromium.launch_persistent_context.assert_called_once()
    call = mock_pw.chromium.launch_persistent_context.call_args
    assert call.kwargs["user_data_dir"] == expected_dir
    assert call.kwargs["headless"] is True
    assert call.kwargs["viewport"] == DEFAULT_VIEWPORT
    assert "--no-sandbox" in call.kwargs["args"]
    # Second call returns the same context (no new launch)
    proj2 = await mgr.get_or_create("p1")
    assert proj2 is proj
    assert mock_pw.chromium.launch_persistent_context.call_count == 1
    await mgr.stop()


@pytest.mark.asyncio
async def test_close_project_tears_down_context(tmp_data_dir, mock_pw):
    import kairos.browser as bm
    bm._playwright = lambda: mock_pw
    mgr = BrowserManager(data_dir=tmp_data_dir)
    await mgr.start()
    await mgr.get_or_create("p1")
    ok = await mgr.close_project("p1")
    assert ok is True
    assert "p1" not in mgr._projects
    # Closing again returns False
    assert await mgr.close_project("p1") is False
    await mgr.stop()


@pytest.mark.asyncio
async def test_idle_eviction(tmp_data_dir, mock_pw, monkeypatch):
    import kairos.browser as bm
    bm._playwright = lambda: mock_pw
    mgr = BrowserManager(data_dir=tmp_data_dir)
    await mgr.start()
    # Create 3 projects
    await mgr.get_or_create("p1")
    await mgr.get_or_create("p2")
    await mgr.get_or_create("p3")
    # Backdate p1 + p2 so they're idle
    import time
    mgr._projects["p1"].last_used = time.time() - IDLE_TIMEOUT_S - 1
    mgr._projects["p2"].last_used = time.time() - IDLE_TIMEOUT_S - 1
    mgr._projects["p3"].last_used = time.time()
    # Trigger eviction
    await mgr.get_or_create("p4")  # also creates one
    assert "p1" not in mgr._projects, "idle p1 should be evicted"
    assert "p2" not in mgr._projects, "idle p2 should be evicted"
    assert "p3" in mgr._projects, "fresh p3 should remain"
    await mgr.stop()


@pytest.mark.asyncio
async def test_navigate_returns_page_info(tmp_data_dir, mock_pw):
    import kairos.browser as bm
    bm._playwright = lambda: mock_pw
    mgr = BrowserManager(data_dir=tmp_data_dir)
    await mgr.start()
    # Set up a fake response + page
    proj = await mgr.get_or_create("p1")
    response = MagicMock()
    response.status = 200
    response.ok = True
    proj.page.goto = AsyncMock(return_value=response)
    proj.page.title = AsyncMock(return_value="Example")
    proj.page.url = "https://example.com"
    info = await mgr.navigate("p1", "https://example.com")
    assert info["ok"] is True
    assert info["title"] == "Example"
    assert info["url"] == "https://example.com"
    proj.page.goto.assert_called_once()
    await mgr.stop()


@pytest.mark.asyncio
async def test_screenshot_returns_bytes(tmp_data_dir, mock_pw):
    import kairos.browser as bm
    bm._playwright = lambda: mock_pw
    mgr = BrowserManager(data_dir=tmp_data_dir)
    await mgr.start()
    proj = await mgr.get_or_create("p1")
    proj.page.screenshot = AsyncMock(return_value=b"PNG_DATA")
    png = await mgr.screenshot("p1")
    assert png == b"PNG_DATA"
    await mgr.stop()
