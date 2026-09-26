"""The browser tool: the caller `kairos/browser.py` never had.

Everything here runs against a fake manager. Playwright is not a test
dependency, and a real Chromium in CI is a 100MB flake. What is under test is
the contract: which actions exist, what they return, that URLs go through the
SSRF guard, and the two rules `kairos.sentinel` depends on -- a read is not
egress, an acting action is.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from kairos.sentinel import is_egress
from kairos.tools.browser_tool import BrowserTool

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class FakeManager:
    """Just enough of BrowserManager, with every call recorded."""

    def __init__(self, shot_path: Path):
        self.calls: list = []
        self.shot_path = shot_path

    async def navigate(self, pid, url):
        self.calls.append(("navigate", pid, url))
        return {"url": url, "title": "Example", "status": 200, "ok": True}

    async def save_screenshot(self, pid, full_page=False):
        self.calls.append(("screenshot", pid, full_page))
        self.shot_path.write_bytes(PNG)
        return self.shot_path

    async def current(self, pid):
        self.calls.append(("current", pid))
        return {"url": "http://x/", "title": "Example",
                "viewport": {"width": 1280, "height": 720}, "console_count": 2}

    async def get_console(self, pid, limit=50):
        self.calls.append(("console", pid))
        return [{"type": "error", "text": "boom"}]

    async def evaluate(self, pid, expression):
        self.calls.append(("evaluate", pid, expression))
        return "Hello page" if "innerText" in expression else 42

    async def click(self, pid, x, y):
        self.calls.append(("click", pid, x, y))

    async def type_text(self, pid, text):
        self.calls.append(("type", pid, text))

    async def press_key(self, pid, key):
        self.calls.append(("key", pid, key))

    async def back(self, pid):
        self.calls.append(("back", pid))

    async def forward(self, pid):
        self.calls.append(("forward", pid))

    async def reload(self, pid):
        self.calls.append(("reload", pid))

    async def set_viewport(self, pid, width, height):
        self.calls.append(("viewport", pid, width, height))

    async def close_project(self, pid):
        self.calls.append(("close", pid))
        return True


def make_tool(tmp_path, project_id: str = "proj1"):
    mgr = FakeManager(tmp_path / "shot.png")
    return BrowserTool(project_id=project_id, manager=mgr,
                       allowed_root=tmp_path), mgr


def run(tool, **kwargs):
    return asyncio.run(tool.execute(**kwargs))


# --------------------------------------------------------------------------
# the happy paths
# --------------------------------------------------------------------------

def test_a_bare_url_navigates(tmp_path):
    tool, mgr = make_tool(tmp_path)
    res = run(tool, url="http://example.com/")
    assert res.success, res.error
    assert "example.com" in res.output
    assert "Example" in res.output
    assert mgr.calls[0][0] == "navigate"


def test_navigate_reports_the_http_status(tmp_path):
    tool, _ = make_tool(tmp_path)
    res = run(tool, action="navigate", url="http://example.com/")
    assert res.success
    assert "200" in res.output
    assert res.metadata["status"] == 200


def test_screenshot_returns_a_path_that_exists(tmp_path):
    tool, _ = make_tool(tmp_path)
    res = run(tool, action="screenshot")
    assert res.success, res.error
    path = Path(res.metadata["path"])
    assert path.exists(), "the tool must leave a file behind for the user"
    assert res.metadata["bytes"] == len(PNG)
    assert "screenshot saved" in res.output


def test_screenshot_passes_full_page_through(tmp_path):
    tool, mgr = make_tool(tmp_path)
    res = run(tool, action="screenshot", full_page=True)
    assert res.success
    assert ("screenshot", "proj1", True) in mgr.calls


def test_content_reads_the_page_as_text(tmp_path):
    """The action that makes this tool usable by a model that cannot see."""
    tool, _ = make_tool(tmp_path)
    res = run(tool, action="content")
    assert res.success
    assert res.output == "Hello page"


def test_current_reports_url_title_and_viewport(tmp_path):
    tool, _ = make_tool(tmp_path)
    res = run(tool, action="current")
    assert res.success
    assert "http://x/" in res.output
    assert "1280" in res.output


def test_console_lists_messages(tmp_path):
    tool, _ = make_tool(tmp_path)
    res = run(tool, action="console")
    assert res.success
    assert "error" in res.output and "boom" in res.output


def test_evaluate_serialises_the_result(tmp_path):
    tool, _ = make_tool(tmp_path)
    res = run(tool, action="evaluate", expression="1+1")
    assert res.success
    assert res.output.strip() == "42"


def test_click_type_key_and_viewport_reach_the_manager(tmp_path):
    tool, mgr = make_tool(tmp_path)
    assert run(tool, action="click", x=5, y=6).success
    assert run(tool, action="type", text="hello").success
    assert run(tool, action="key", key="Enter").success
    assert run(tool, action="viewport", width=800, height=600).success
    assert ("click", "proj1", 5, 6) in mgr.calls
    assert ("type", "proj1", "hello") in mgr.calls
    assert ("key", "proj1", "Enter") in mgr.calls
    assert ("viewport", "proj1", 800, 600) in mgr.calls


def test_close_says_when_there_was_nothing_open(tmp_path):
    class Nothing(FakeManager):
        async def close_project(self, pid):
            return False

    tool = BrowserTool(project_id="p", manager=Nothing(tmp_path / "s.png"),
                       allowed_root=tmp_path)
    res = run(tool, action="close")
    assert res.success
    assert "no browser open" in res.output


# --------------------------------------------------------------------------
# refusals
# --------------------------------------------------------------------------

def test_the_cloud_metadata_address_is_refused(tmp_path):
    tool, mgr = make_tool(tmp_path)
    res = run(tool, action="navigate", url="http://169.254.169.254/latest/meta-data/")
    assert not res.success
    assert "link-local" in (res.error or "").lower()
    assert mgr.calls == [], "a refused URL must never reach the browser"


def test_loopback_and_lan_are_allowed(tmp_path):
    """Verifying the user's own dev server is the point of having this tool."""
    tool, _ = make_tool(tmp_path)
    for url in ("http://localhost:3000/", "http://127.0.0.1:8000/health"):
        res = run(tool, action="navigate", url=url)
        assert res.success, url + " -> " + str(res.error)


def test_unknown_action_lists_the_real_ones(tmp_path):
    tool, _ = make_tool(tmp_path)
    res = run(tool, action="frobnicate")
    assert not res.success
    assert "unknown action" in res.error
    assert "screenshot" in res.error


def test_missing_action_is_an_error_not_a_silent_success(tmp_path):
    tool, _ = make_tool(tmp_path)
    res = run(tool)
    assert not res.success
    assert "action is required" in res.error


def test_click_without_coordinates_asks_for_them(tmp_path):
    tool, _ = make_tool(tmp_path)
    res = run(tool, action="click")
    assert not res.success
    assert "x and y" in res.error


def test_navigate_without_a_url_is_refused(tmp_path):
    tool, _ = make_tool(tmp_path)
    res = run(tool, action="navigate")
    assert not res.success
    assert "url is required" in res.error


def test_a_tool_without_a_project_id_says_so(tmp_path):
    """The browser is per project; guessing one would browse someone else's."""
    tool = BrowserTool(allowed_root=tmp_path, manager=FakeManager(tmp_path / "s.png"))
    res = run(tool, action="screenshot")
    assert not res.success
    assert "project id" in res.error


def test_typed_text_is_never_echoed_back(tmp_path):
    tool, mgr = make_tool(tmp_path)
    res = run(tool, action="type", text="correct-horse-battery-staple")
    assert res.success
    assert "correct-horse" not in res.output
    assert res.metadata["chars"] == len("correct-horse-battery-staple")
    # ...but it did reach the browser.
    assert ("type", "proj1", "correct-horse-battery-staple") in mgr.calls


# --------------------------------------------------------------------------
# the gate contract
# --------------------------------------------------------------------------

def test_reading_the_browser_is_not_egress_but_acting_on_it_is():
    for action in ("screenshot", "content", "console", "current"):
        egress, why = is_egress("browser", {"action": action})
        assert not egress, action + " must stay allowed in a tainted run: " + why
    for action in ("navigate", "click", "type", "key", "evaluate"):
        egress, why = is_egress("browser", {"action": action})
        assert egress, action + " can carry data out and must be refused"


def test_the_browser_is_a_taint_source():
    """Reading a page is reading content this run cannot vouch for."""
    from kairos.taint import classify
    assert classify("browser") == "network"
