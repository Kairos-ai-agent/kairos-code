"""R38.12 — starting a project's servers is bounded, and one failure stays one.

Why sequential-with-a-budget rather than parallel, measured on Windows with the
five bundled servers (see McpRegistry.start_all's docstring):

    all five at once     1/5 answered
    staggered by 0.5s    2/5
    two at a time        2/4
    one after another    5/5

So the protection against a hanging server is a ceiling on the whole phase.
These tests pin the half that matters: a failure is contained to its own server,
and a server that hangs cannot make the ones behind it wait forever.
"""

import asyncio
import time
from pathlib import Path

import pytest

import kairos.mcp_client as mc
from kairos.mcp_client import McpRegistry, McpServerConfig

DELAY = 0.4


class FakeClient:
    """A client that takes `delay` seconds to start, and may fail instead.

    It honours `budget_s` the way a real client does — the request times out when
    the budget is spent — because that is the behavior under test.
    """

    def __init__(self, config, delay: float = DELAY, fail: bool = False,
                 tools_fail: bool = False, budget_s: float = None):
        self.config = config
        self.delay = delay
        self.budget_s = budget_s
        self.fail = fail
        self.tools_fail = tools_fail
        self.closed = False
        self.server_info = None

    async def start(self):
        wait = self.delay
        if self.budget_s is not None:
            wait = min(wait, self.budget_s)
        await asyncio.sleep(wait)
        if self.budget_s is not None and self.delay > self.budget_s:
            raise RuntimeError(
                f"MCP request 'initialize' timed out after {self.budget_s}s")
        if self.fail:
            raise RuntimeError(f"{self.config.name} refuses to start")

    async def list_tools(self):
        if self.tools_fail:
            raise RuntimeError(f"{self.config.name} tools/list broke")
        return [{"name": f"{self.config.name}_tool", "description": "d",
                 "inputSchema": {"type": "object", "properties": {}}}]

    async def close(self):
        self.closed = True


def _registry(monkeypatch, specs: dict, delay: float = DELAY) -> McpRegistry:
    registry = McpRegistry()
    registry.include_bundled = False
    registry._configs = {
        name: McpServerConfig(name=name, command="kairos-fake",
                              enabled=kw.get("enabled", True))
        for name, kw in specs.items()
    }
    made: dict = {}

    def fake_client_for(cfg, budget_s=None):
        made[cfg.name] = {"budget": budget_s}
        return FakeClient(cfg, delay=delay, budget_s=budget_s,
                          fail=specs[cfg.name].get("fail", False),
                          tools_fail=specs[cfg.name].get("tools_fail", False))

    monkeypatch.setattr(mc, "client_for", fake_client_for)
    registry.fake_made = made  # type: ignore[attr-defined]
    return registry


def test_a_broken_server_does_not_stop_the_others(monkeypatch):
    registry = _registry(monkeypatch, {
        "good1": {}, "broken": {"fail": True}, "good2": {},
    })

    asyncio.run(registry.start_all())

    assert set(registry.startup_errors) == {"broken"}
    assert "refuses to start" in registry.startup_errors["broken"]
    names = {t.name for t in registry.all_tools()}
    assert names == {"mcp_good1__good1_tool", "mcp_good2__good2_tool"}, names


def test_a_server_whose_tool_list_fails_is_closed(monkeypatch):
    registry = _registry(monkeypatch, {"chatty": {"tools_fail": True}, "ok": {}})

    asyncio.run(registry.start_all())

    assert "chatty" in registry.startup_errors
    assert "tools/list" in registry.startup_errors["chatty"]
    names = {t.name for t in registry.all_tools()}
    assert names == {"mcp_ok__ok_tool"}, names


def test_a_disabled_server_is_never_started(monkeypatch):
    registry = _registry(monkeypatch, {"on": {}, "off": {"enabled": False}})

    asyncio.run(registry.start_all())

    assert set(registry.fake_made) == {"on"}, registry.fake_made
    assert {t.name for t in registry.all_tools()} == {"mcp_on__on_tool"}


def test_each_server_is_given_only_the_time_left_in_the_budget(monkeypatch):
    """One server's slice comes out of the total, not out of thin air."""
    monkeypatch.setattr(mc, "START_BUDGET_S", 5.0)
    registry = _registry(monkeypatch, {f"s{i}": {} for i in range(3)},
                         delay=0.3)

    asyncio.run(registry.start_all())

    budgets = [registry.fake_made[f"s{i}"]["budget"] for i in range(3)]
    assert all(b is not None for b in budgets), budgets
    assert budgets == sorted(budgets, reverse=True), budgets
    assert budgets[-1] < 5.0, "the last server should get less than the whole budget"
    assert registry.startup_errors == {}


def test_the_whole_start_phase_is_bounded(monkeypatch):
    """Four hanging servers must not cost four full timeouts."""
    monkeypatch.setattr(mc, "START_BUDGET_S", 1.5)
    registry = _registry(monkeypatch, {f"s{i}": {} for i in range(4)},
                         delay=5.0)

    started = time.monotonic()
    asyncio.run(registry.start_all())
    elapsed = time.monotonic() - started

    # The first server spends the budget; the rest are skipped, not waited for.
    assert elapsed < 3.0, f"{elapsed:.2f}s — the budget did not bound the phase"
    assert len(registry.startup_errors) == 4
    skipped = [e for e in registry.startup_errors.values() if "budget" in e]
    assert len(skipped) == 3, registry.startup_errors
    assert registry.all_tools() == []
