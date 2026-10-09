"""Bounded MCP-config reload: edit the YAML, take effect; do not churn.

``b4ddce2`` stopped the per-request registry churn by *reusing*
``project.runtime.mcp_registry``. That reuse also removed a behaviour: editing
``.kairos/mcp.yaml`` (or ``~/.kairos/mcp.yaml``) at runtime no longer took
effect until the project runtime was closed. This file locks the fix.

The reload is bounded by a **fingerprint** of the user-editable config files
(``kairos.mcp_client.mcp_config_fingerprint``): unchanged -> reuse the registry
exactly as ``b4ddce2`` established (same ``id()``, no new children); changed ->
**close the old registry before building the new one**, so two live subprocess
sets never coexist (that coexistence is the leak this all exists to prevent).

Everything here is source-level: ``McpRegistry`` is replaced by a recording fake
and ``should_defer_start`` is forced, so npx/uvx never runs and no assertion
depends on the host's real ``~/.kairos/mcp.yaml``.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from kairos.llm.base import LLMConfig
from kairos.llm.model_router import ModelRouter
from kairos.mcp_client import mcp_config_fingerprint, mcp_config_paths


# --------------------------------------------------------------------------- fakes
class _FakeRegistry:
    """A registry stand-in that records load/start/close, in order."""

    def __init__(self, factory: "_RegistryFactory"):
        self.factory = factory
        factory.constructed += 1
        self.index = factory.constructed
        factory.alive.add(self)
        factory.log.append(("construct", self.index))
        self.load_dir = None
        self._tools = [SimpleNamespace(name=f"mcp_tool_{self.index}")]

    def load(self, project_dir=None, user_dir=None):
        self.load_dir = str(project_dir)
        self.factory.log.append(("load", self.index, self.load_dir))

    def defer_start(self):
        self.factory.log.append(("defer", self.index))

    async def start_all(self):
        self.factory.log.append(("start", self.index))

    def all_tools(self):
        return list(self._tools)

    async def close_all(self):
        self.factory.log.append(("close", self.index))
        if self.factory.close_raises:
            raise RuntimeError("close_all blown up")
        self.factory.alive.discard(self)


class _RegistryFactory:
    """Callable that stands in for the ``McpRegistry`` class."""

    def __init__(self, *, close_raises: bool = False):
        self.log: list = []
        self.constructed = 0
        self.alive: set = set()
        self.close_raises = close_raises

    def __call__(self, *args, **kwargs) -> _FakeRegistry:
        return _FakeRegistry(self)

    # -- helpers -------------------------------------------------------------
    def order_of(self, event) -> int:
        return self.log.index(event)

    def has(self, event) -> bool:
        return event in self.log

    @property
    def closes(self) -> int:
        return sum(1 for e in self.log if e[0] == "close")


def _install_fake_registry(monkeypatch, factory: _RegistryFactory) -> _RegistryFactory:
    """Point ``kairos.mcp_client`` at the fake and keep start_all deferred."""
    import kairos.mcp_client as mcp

    monkeypatch.setattr(mcp, "McpRegistry", factory)
    monkeypatch.setattr(mcp, "should_defer_start", lambda: True)
    return factory


# --------------------------------------------------------------------------- orch
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
    return orch


def _project_with_config_dir(orch, tmp_path, name="svc"):
    """A project whose ``.kairos/`` dir exists but has no config yet.

    Used by the change tests: the first attach records the 'absent' fingerprint,
    then the test writes the file, and the next attach must reload.
    """
    ws = tmp_path / name
    ws.mkdir(parents=True, exist_ok=True)
    p = orch.create_project(name, "d", work_dir=str(ws))
    cfg_dir = ws / ".kairos"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    return p, cfg_dir / "mcp.yaml"


def _config_first(orch, tmp_path, name, content):
    """A project whose config is already on disk *before* the first attach."""
    ws = tmp_path / name
    (ws / ".kairos").mkdir(parents=True, exist_ok=True)
    cfg = ws / ".kairos" / "mcp.yaml"
    cfg.write_text(content, encoding="utf-8")
    p = orch.create_project(name, "d", work_dir=str(ws))
    return p, cfg


_MCP_A = "mcp_servers:\n  alpha:\n    command: echo\n    args: [a]\n"
_MCP_B = "mcp_servers:\n  alpha:\n    command: echo\n    args: [a]\n  beta:\n    command: echo\n    args: [b-c-d]\n"


# ===================================================================== 1. fingerprint
def test_fingerprint_detects_created_changed_and_missing(tmp_path):
    """The three cases the reload keys on: missing, added, edited."""
    proj = tmp_path / "proj"
    (proj / ".kairos").mkdir(parents=True)
    user = tmp_path / "user"
    user.mkdir()
    cfg = proj / ".kairos" / "mcp.yaml"

    missing = mcp_config_fingerprint(project_dir=proj, user_dir=user)
    assert isinstance(missing, str) and missing

    cfg.write_text(_MCP_A, encoding="utf-8")
    added = mcp_config_fingerprint(project_dir=proj, user_dir=user)
    assert added != missing, "creating the file was not detected"

    # Unchanged: same content -> same fingerprint.
    assert mcp_config_fingerprint(project_dir=proj, user_dir=user) == added

    cfg.write_text(_MCP_B, encoding="utf-8")
    edited = mcp_config_fingerprint(project_dir=proj, user_dir=user)
    assert edited != added, "editing the file was not detected"


def test_fingerprint_paths_are_ordered_user_then_project(tmp_path):
    proj = tmp_path / "proj"
    user = tmp_path / "user"
    paths = mcp_config_paths(project_dir=proj, user_dir=user)
    assert paths[0] == user / "mcp.yaml"
    assert paths[-1] == proj / ".kairos" / "mcp.yaml"


def test_fingerprint_hashes_content_only_when_the_file_moved(tmp_path, monkeypatch):
    """A repeat call must be a stat, not a re-read — no per-request heavy IO."""
    import pathlib

    proj = tmp_path / "proj"
    (proj / ".kairos").mkdir(parents=True)
    user = tmp_path / "user"
    user.mkdir()
    (proj / ".kairos" / "mcp.yaml").write_text(_MCP_A, encoding="utf-8")

    reads = {"n": 0}
    real = pathlib.Path.read_bytes

    def counting(self):  # noqa: ANN001
        reads["n"] += 1
        return real(self)

    monkeypatch.setattr(pathlib.Path, "read_bytes", counting)

    first = mcp_config_fingerprint(project_dir=proj, user_dir=user)
    after_first = reads["n"]
    assert after_first >= 1
    second = mcp_config_fingerprint(project_dir=proj, user_dir=user)
    assert second == first
    assert reads["n"] == after_first, "an unchanged config was re-read (heavy IO)"


# ============================================================== 2. unchanged -> reuse
def test_unchanged_config_reuses_registry_for_many_calls(tmp_path, monkeypatch):
    """N calls, unchanged config: one registry, one _create_agents, no close."""
    factory = _install_fake_registry(monkeypatch, _RegistryFactory())
    orch = _make_orch(tmp_path)

    calls = {"create": 0}
    real = orch._create_agents

    def counted(project):
        calls["create"] += 1
        return real(project)

    monkeypatch.setattr(orch, "_create_agents", counted)

    # The config is already on disk before the first attach, and never changes.
    p, cfg_path = _config_first(orch, tmp_path, "svc", _MCP_A)

    ids = [id(p.runtime.mcp_registry)]
    for _ in range(6):
        got = orch.get_project(p.id)
        ids.append(id(got.runtime.mcp_registry))

    assert calls["create"] == 1, f"rebuilt agents {calls['create']} times"
    assert len(set(ids)) == 1, f"registry churned: {ids}"
    assert factory.constructed == 1, "a second registry (new children) was built"
    assert factory.closes == 0, "an unchanged config closed the registry"
    assert len(factory.alive) == 1


# ===================================================== 3. changed -> close-then-swap
def test_changed_config_closes_old_before_building_new(tmp_path, monkeypatch):
    """The edit is picked up, and close_all(old) runs BEFORE the new build."""
    factory = _install_fake_registry(monkeypatch, _RegistryFactory())
    orch = _make_orch(tmp_path)
    p, cfg_path = _project_with_config_dir(orch, tmp_path)
    old = p.runtime.mcp_registry
    assert factory.constructed == 1

    # The live edit a user makes while the project is open.
    cfg_path.write_text(_MCP_A, encoding="utf-8")
    orch._create_agents(p)

    assert factory.constructed == 2, "the edit did not trigger a reload"
    assert factory.closes == 1, "the old registry was not closed"
    assert factory.order_of(("close", 1)) < factory.order_of(("construct", 2)), (
        "the replacement was built before the old registry was closed")
    assert p.runtime.mcp_registry is not old
    assert factory.has(("load", 2, str(cfg_path.parent.parent))), \
        "the replacement was not loaded from the project that changed"
    # Exactly one live registry remains (the swap, not a coexistence).
    assert len(factory.alive) == 1
    assert orch._mcp_registries[p.id] is p.runtime.mcp_registry


def test_changed_config_reloads_via_get_project_hot_path(tmp_path, monkeypatch):
    """The hot path notices the change; a second call does not reload again."""
    factory = _install_fake_registry(monkeypatch, _RegistryFactory())
    orch = _make_orch(tmp_path)
    p, cfg_path = _project_with_config_dir(orch, tmp_path)

    cfg_path.write_text(_MCP_A, encoding="utf-8")
    orch.get_project(p.id)  # first call after the edit -> reload

    assert factory.constructed == 2
    assert factory.order_of(("close", 1)) < factory.order_of(("construct", 2))

    for _ in range(4):
        orch.get_project(p.id)

    assert factory.constructed == 2, "the reload ran more than once per change"
    assert factory.closes == 1
    assert len(factory.alive) == 1


# ============================ 3b. same swap, from a request (running loop)
@pytest.mark.asyncio
async def test_reload_inside_a_running_loop_closes_before_starting(
        tmp_path, monkeypatch):
    """Inside a request the swap runs as one task; close still precedes start."""
    factory = _install_fake_registry(monkeypatch, _RegistryFactory())
    orch = _make_orch(tmp_path)
    p, cfg_path = _project_with_config_dir(orch, tmp_path)

    cfg_path.write_text(_MCP_A, encoding="utf-8")
    orch.get_project(p.id)          # schedules the single close-then-swap task
    await asyncio.sleep(0.1)         # let the scheduled swap run

    assert factory.constructed == 2
    assert factory.order_of(("close", 1)) < factory.order_of(("start", 2)), factory.log
    assert factory.order_of(("close", 1)) < factory.order_of(("construct", 2))
    assert p.runtime.mcp_registry is orch._mcp_registries[p.id]
    assert len(factory.alive) == 1

    # No further change -> no further reload.
    orch.get_project(p.id)
    await asyncio.sleep(0.02)
    assert factory.constructed == 2


# ============================================ 4. reverse nail: close_all raising
def test_close_failure_keeps_old_registry_and_does_not_break_assembly(
        tmp_path, monkeypatch):
    """A failing close_all must not orphan children or blow up the attach.

    Chosen behaviour: the reload is **deferred** — keep the old registry (never
    build a second live one, which is how the leak starts), record a trace, and
    leave the wiring intact so the next attach can retry.
    """
    factory = _install_fake_registry(
        monkeypatch, _RegistryFactory(close_raises=True))
    orch = _make_orch(tmp_path)
    p, cfg_path = _project_with_config_dir(orch, tmp_path)
    old = p.runtime.mcp_registry

    cfg_path.write_text(_MCP_A, encoding="utf-8")

    # Must not raise.
    orch._create_agents(p)

    assert factory.constructed == 1, "a second live registry was built anyway"
    assert p.runtime.mcp_registry is old
    assert orch._mcp_registries[p.id] is old
    assert p.coder is not None and p.reviewer is not None, "assembly was broken"
    assert any("reload deferred" in e for e in p.runtime.attach_errors), \
        p.runtime.attach_errors
    # No change was recorded, so a later attach retries.
    assert orch._mcp_config_changed(p) is True


# ======================================================== 5. teardown drops state
@pytest.mark.asyncio
async def test_teardown_drops_the_fingerprint_too(tmp_path, monkeypatch):
    factory = _install_fake_registry(monkeypatch, _RegistryFactory())
    orch = _make_orch(tmp_path)
    p, cfg_path = _project_with_config_dir(orch, tmp_path)

    assert p.id in orch._mcp_fingerprints

    await orch._close_project_runtime_async(p)

    assert orch._mcp_registries.get(p.id) is None
    assert orch._mcp_fingerprints.get(p.id) is None
    assert p.runtime.mcp_registry is None
    assert factory.closes == 1
