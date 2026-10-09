"""Structural regression guard: MCP registries must not multiply per request.

Background (the leak this file freezes). ``get_project`` is on the request
hot path, and it used to re-run ``_create_agents`` whenever a project's
``coder`` was still ``None`` — including on the cache-miss rehydrate path.
Each ``_create_agents`` built a brand-new ``McpRegistry`` that spawns a full
set of MCP subprocesses (~100MB each), then **overwrote**
``project.runtime.mcp_registry`` without closing the previous one, so the old
children were orphaned and kept accumulating (the observed instance spawned a
new ``--mcp-serve`` child every few seconds and never reaped the old ones).

Two invariants are locked here, both source-level (no exe, no real
subprocesses — MCP start is neutralised):

1. ``_attach_mcp`` reuses the registry a project already owns (by
   ``runtime.mcp_registry`` *and* a process-level map keyed by project id), so
   rebuilding a toolset — or re-hydrating a project whose memory-cache entry
   vanished — never constructs a second registry.
2. ``get_project``'s lazy retry keys on the **result**, not on whether
   ``_create_agents`` raised: a "success" that leaves ``coder`` unset stays in
   ``_attach_failures``, so the retry is once-per-process, not once-per-request.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kairos.llm.base import LLMConfig
from kairos.llm.model_router import ModelRouter


# --------------------------------------------------------------------------- helpers
def _install_hermetic_mcp(monkeypatch) -> None:
    """Make MCP attach cheap and subprocess-free for the whole test.

    ``load`` is a no-op (no servers configured) so ``start_all`` has nothing
    to spawn; ``should_defer_start`` is forced so the sync path defers rather
    than blocking on a real server.
    """
    import kairos.mcp_client as mcp

    monkeypatch.setattr(mcp.McpRegistry, "load", lambda self, **kw: None)

    async def _noop_start_all(self) -> None:
        return None

    monkeypatch.setattr(mcp.McpRegistry, "start_all", _noop_start_all)
    monkeypatch.setattr(mcp, "should_defer_start", lambda: True)


def _make_router() -> ModelRouter:
    router = ModelRouter()
    cfg = LLMConfig(provider="openai", model="gpt-4o", api_key="sk-test")
    fake = MagicMock()
    fake.config = cfg
    router._role_mapping = {"coder": "test", "reviewer": "test"}
    router._model_configs["test"] = cfg
    router._model_configs["default"] = cfg
    router._provider_cache = {"test": fake}
    return router


def _make_orch(tmp_path):
    from kairos.core.orchestrator import Orchestrator

    fake_db = MagicMock()
    fake_db.load_projects.return_value = []
    fake_db.save_project = MagicMock()
    with patch("kairos.core.orchestrator.Persistence", return_value=fake_db):
        orch = Orchestrator(model_router=_make_router(), workspace_base=tmp_path)
    return orch, fake_db


def _count_construction(monkeypatch) -> dict:
    """Count every ``McpRegistry()`` construction (i.e. every new child set)."""
    import kairos.mcp_client as mcp

    seen = {"n": 0}
    real = mcp.McpRegistry

    def counting(*args, **kwargs):
        seen["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(mcp, "McpRegistry", counting)
    return seen


# --------------------------------------------------------------------------- 1. guard
def test_get_project_attaches_once_and_keeps_one_registry(tmp_path, monkeypatch):
    """N calls on a coder-less project: one ``_create_agents``, one registry."""
    _install_hermetic_mcp(monkeypatch)
    orch, _ = _make_orch(tmp_path)
    p = orch.create_project("demo", "d", work_dir="")
    # The registry the project already owns (built during create_project).
    owned = p.runtime.mcp_registry
    assert owned is not None

    p.coder = None
    orch._attach_failures.clear()

    calls = {"create": 0, "attach": 0}
    real_create, real_attach = orch._create_agents, orch._attach_mcp

    def c_create(project):
        calls["create"] += 1
        return real_create(project)

    def c_attach(project, work_dir):
        calls["attach"] += 1
        return real_attach(project, work_dir)

    monkeypatch.setattr(orch, "_create_agents", c_create)
    monkeypatch.setattr(orch, "_attach_mcp", c_attach)

    ids = []
    for _ in range(6):
        got = orch.get_project(p.id)
        ids.append(id(got.runtime.mcp_registry))

    assert calls["create"] == 1, calls
    assert calls["attach"] == 1, calls
    assert len(set(ids)) == 1, f"registry churned across calls: {ids}"
    assert ids[0] == id(owned), "a new registry replaced the one we already had"
    assert p.coder is not None


def test_incomplete_attach_stays_marked_failed(tmp_path, monkeypatch):
    """Reverse pin: a returning-but-incomplete attach must not re-run per call.

    ``_create_agents`` is patched to do nothing (coder stays ``None``); the
    old code discarded the failure marker on a non-raising return and so
    re-entered on every request — the leak. The guard must keep the id in
    ``_attach_failures`` and stop calling it.
    """
    _install_hermetic_mcp(monkeypatch)
    orch, _ = _make_orch(tmp_path)
    p = orch.create_project("demo2", "d", work_dir="")
    p.coder = None
    orch._attach_failures.clear()

    calls = {"n": 0}

    def incomplete(project):
        calls["n"] += 1  # deliberately does NOT set project.coder

    monkeypatch.setattr(orch, "_create_agents", incomplete)

    for _ in range(6):
        orch.get_project(p.id)

    assert calls["n"] == 1, "the retry guard re-ran every request"
    assert p.id in orch._attach_failures, "id must stay marked so it is not retried"
    assert p.coder is None


def test_successful_attach_allows_a_later_reattach(tmp_path, monkeypatch):
    """Positive case: a genuine success clears the marker, so retry is not sealed."""
    _install_hermetic_mcp(monkeypatch)
    orch, _ = _make_orch(tmp_path)
    p = orch.create_project("demo3", "d", work_dir="")
    p.coder = None
    orch._attach_failures.clear()

    # Real attach succeeds -> coder wired, marker cleared.
    orch.get_project(p.id)
    assert p.coder is not None
    assert p.id not in orch._attach_failures

    # A later reset (transient failure / manual re-arm) is allowed to retry.
    p.coder = None
    calls = {"n": 0}
    real = orch._create_agents

    def c(project):
        calls["n"] += 1
        return real(project)

    monkeypatch.setattr(orch, "_create_agents", c)
    orch.get_project(p.id)

    assert calls["n"] == 1, "a successful project could not re-attach later"
    assert p.coder is not None


# --------------------------------------------------------------------------- 2. no second registry
def test_attach_mcp_reuses_existing_registry_without_constructing_a_new_one(
        tmp_path, monkeypatch):
    """Reuse is genuine, not 'we happened not to call anything'.

    The project's existing registry must be *consulted* (its ``all_tools()``
    result returned) and no second ``McpRegistry`` may be constructed.
    """
    _install_hermetic_mcp(monkeypatch)
    orch, _ = _make_orch(tmp_path)
    p = orch.create_project("demo4", "d", work_dir="")

    sentinel = object()

    class SpyRegistry:
        def __init__(self):
            self.all_tools_calls = 0

        def all_tools(self):
            self.all_tools_calls += 1
            return [sentinel]

    spy = SpyRegistry()
    p.runtime.mcp_registry = spy
    orch._mcp_registries[p.id] = spy

    constructed = _count_construction(monkeypatch)

    tools = orch._attach_mcp(p, str(p.work_dir))

    assert tools == [sentinel], "the existing registry was not consulted"
    assert spy.all_tools_calls == 1, "all_tools() was not called on the existing registry"
    assert constructed["n"] == 0, "a second McpRegistry (new children) was built"


def test_rebuilding_toolset_does_not_spawn_a_second_registry(tmp_path, monkeypatch):
    """``_create_agents`` run repeatedly spawns exactly one registry."""
    _install_hermetic_mcp(monkeypatch)
    orch, _ = _make_orch(tmp_path)
    constructed = _count_construction(monkeypatch)

    p = orch.create_project("demo5", "d", work_dir="")
    ids = [id(p.runtime.mcp_registry)]
    for _ in range(4):
        orch._create_agents(p)
        ids.append(id(p.runtime.mcp_registry))

    assert constructed["n"] == 1, f"registry constructed {constructed['n']} times"
    assert len(set(ids)) == 1, f"registry churned: {ids}"


def test_rehydrate_reuses_registry_by_project_id(tmp_path, monkeypatch):
    """A cache-miss rehydrate (fresh Project object) finds its registry by id."""
    _install_hermetic_mcp(monkeypatch)
    orch, fake_db = _make_orch(tmp_path)
    constructed = _count_construction(monkeypatch)

    ws = tmp_path / "ws"
    ws.mkdir()
    row = {"id": "ghost01", "name": "ghost", "description": "",
           "workspace": str(ws), "work_dir": str(ws), "requirements": "",
           "status": "active", "created_at": 0}
    fake_db.load_projects.return_value = [row]

    ids = []
    for _ in range(5):
        orch._projects.pop("ghost01", None)  # force a fresh Project each call
        got = orch.get_project("ghost01")
        ids.append(id(got.runtime.mcp_registry))

    assert constructed["n"] == 1, (
        f"rehydrate spawned {constructed['n']} registries — subprocess growth")
    assert len(set(ids)) == 1, f"registry churned across rehydrates: {ids}"


# --------------------------------------------------------------------------- 3. close path
@pytest.mark.asyncio
async def test_close_calls_close_all_and_releases_the_id(tmp_path, monkeypatch):
    """The teardown path really closes the registry (so a swap would close the old)."""
    _install_hermetic_mcp(monkeypatch)
    orch, _ = _make_orch(tmp_path)
    p = orch.create_project("demo7", "d", work_dir="")

    closed = {"n": 0}

    class Spy:
        async def close_all(self):
            closed["n"] += 1

    spy = Spy()
    p.runtime.mcp_registry = spy
    orch._mcp_registries[p.id] = spy

    await orch._close_project_runtime_async(p)

    assert closed["n"] == 1, "close_all() was not invoked on the registry"
    assert orch._mcp_registries.get(p.id) is None, "id handle leaked after close"
    assert p.runtime.mcp_registry is None
