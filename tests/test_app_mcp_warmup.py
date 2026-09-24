"""The app must serve first and warm the MCP servers on a task, never before.

A double-click used to show "127.0.0.1 refused to connect": the servers a user
had configured were awaited at import time, so the port opened 102 seconds late
while the browser had already given up. These pin the replacement -- startup
schedules the warm-up and returns, and the warm-up actually starts the servers.

The second test exists because the first version of this fix was a no-op: it
called ``asyncio.create_task`` without ``import asyncio`` in ``api/app.py``, so
scheduling raised ``NameError`` and a silent ``log.debug`` hid it. A regression
test for the *effect* is what catches a fix that never ran.
"""

import time

from fastapi.testclient import TestClient

import kairos.mcp_client as mc
from kairos.mcp_client import McpRegistry, McpServerConfig


class _Fake:
    """Enough of a client to be started and listed."""

    def __init__(self, cfg, budget_s=None):
        self.config = cfg
        self.budget_s = budget_s
        self.closed = False
        self.server_info = None

    async def start(self):
        return None

    async def list_tools(self):
        return [{"name": "ping", "description": "d",
                 "inputSchema": {"type": "object", "properties": {}}}]

    async def close(self):
        self.closed = True


def _deferred_registry(monkeypatch, names=("warm-me",)) -> McpRegistry:
    reg = McpRegistry()
    reg.include_bundled = False
    reg._configs = {n: McpServerConfig(name=n, command="kairos-fake", enabled=True)
                    for n in names}
    monkeypatch.setattr(mc, "client_for", lambda cfg, budget_s=None: _Fake(cfg, budget_s))
    reg.defer_start()
    return reg


def test_startup_schedules_the_warm_up_and_still_serves(monkeypatch):
    reg = _deferred_registry(monkeypatch)

    from api.app import app

    with TestClient(app) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        assert getattr(app.state, "mcp_warm_task", None) is not None, \
            "the warm-up was never scheduled (asyncio missing? silent except?)"

    assert reg._deferred is False, "the warm-up never entered start_all"


def test_the_warm_up_actually_starts_the_servers(monkeypatch):
    reg = _deferred_registry(monkeypatch, names=("warm-a", "warm-b"))

    from api.app import app

    with TestClient(app) as client:
        client.get("/api/health")
        for _ in range(50):
            if len(reg.all_tools()) >= 2:
                break
            time.sleep(0.1)

    assert sorted(reg._clients) == ["warm-a", "warm-b"], reg._clients
    assert sorted(t.name for t in reg.all_tools()) == [
        "mcp_warm-a__ping", "mcp_warm-b__ping"]


def test_shutdown_closes_the_project_runtimes(monkeypatch):
    """What the app starts, the app stops.

    Nothing closed the project runtimes on shutdown, so every MCP child
    outlived the app. A bundled one is a second copy of the executable:
    fifty of them were still alive, holding the binary, when it came time
    to replace it. The close -> close_all wiring itself is covered by
    test_close_closes_mcp_registry; this pins that shutdown calls it.
    """
    from api.deps import orchestrator

    called = {"n": 0}
    original = orchestrator.close

    async def spy():
        called["n"] += 1
        await original()

    monkeypatch.setattr(orchestrator, "close", spy)

    from api.app import app

    with TestClient(app) as client:
        assert client.get("/api/health").json()["status"] == "ok"

    assert called["n"] == 1, "shutdown never closed the project runtimes"
