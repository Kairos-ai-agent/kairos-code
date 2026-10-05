"""The HTTP side of "dispatch without starting over".

The requirement, verbatim: a main agent (Claude Code, Codex, …) splits a project
into tasks and hands them to Kairos — and **must not create a new session for
every task**. The CLI could already do this (``kairos worker attach <repo>``)
and so could the orchestrator (``attach_project``), but over HTTP the only entry
point was ``POST /projects``, which always makes a *new* project — and with it a
session that remembers nothing about the previous task.

These tests pin the idempotence, not the implementation: same repo in, same
project id out, however many times it is called.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from kairos.llm.base import LLMConfig
from kairos.llm.model_router import ModelRouter


@pytest.fixture
def mock_router():
    router = ModelRouter()
    fake_provider = MagicMock()
    fake_provider.config = LLMConfig(
        provider="openai", model="gpt-4o", api_key="sk-test")
    router._role_mapping = {"coder": "test", "reviewer": "test"}
    router._model_configs["test"] = fake_provider.config
    router._model_configs["default"] = fake_provider.config
    router._provider_cache = {"test": fake_provider}
    return router


@pytest.fixture
def no_agents(monkeypatch):
    # Agent construction needs MCP/network wiring with a known teardown hang in
    # this environment (see test_worker_session). Identity is what is tested.
    from kairos.core.orchestrator import Orchestrator

    monkeypatch.setattr(
        Orchestrator, "_create_agents", lambda self, project: None)


@pytest.fixture
def api(tmp_path, mock_router, no_agents, monkeypatch):
    """The projects router, wired to an orchestrator that owns tmp_path only."""
    import api.deps as deps
    import api.routes.projects as projects_api
    from kairos.core.orchestrator import Orchestrator
    from kairos.core.persistence import Persistence

    orch = Orchestrator(
        model_router=mock_router,
        workspace_base=tmp_path / "workspaces",
        db=Persistence(tmp_path / "kairos.db"),
    )
    # The documented seam: routes resolve through ``api.deps.orchestrator``.
    monkeypatch.setattr(deps, "orchestrator", orch)
    return projects_api


async def test_attach_is_idempotent(tmp_path, api):
    repo = tmp_path / "main-agent-repo"
    repo.mkdir()

    first = await api.attach_project(api.AttachProjectRequest(repo=str(repo)))
    second = await api.attach_project(api.AttachProjectRequest(repo=str(repo)))
    third = await api.attach_project(
        api.AttachProjectRequest(repo=str(repo), name="a later name"))

    assert first["id"] == second["id"] == third["id"], (
        "a second dispatch started a new session instead of rejoining the first"
    )
    assert (repo / ".kairos" / "worker.json").exists(), (
        "the binding must persist next to the repo so it survives a restart"
    )


async def test_two_repos_get_two_workers(tmp_path, api):
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()

    a = await api.attach_project(api.AttachProjectRequest(repo=str(repo_a)))
    b = await api.attach_project(api.AttachProjectRequest(repo=str(repo_b)))

    assert a["id"] != b["id"]


async def test_creating_a_project_is_the_thing_that_starts_over(tmp_path, api):
    """The contrast that makes this route necessary.

    ``POST /projects`` is the documented create path and is deliberately not
    idempotent; two "dispatches" through it are two sessions. That is exactly
    the behaviour the requirement rules out, and why attach had to exist.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    orch = api._orch()

    created = [orch.create_project(f"p{i}", "", str(repo)) for i in range(2)]
    assert created[0].id != created[1].id

    attached = await api.attach_project(api.AttachProjectRequest(repo=str(repo)))
    again = await api.attach_project(api.AttachProjectRequest(repo=str(repo)))
    assert attached["id"] == again["id"]


async def test_attach_rejects_a_missing_directory(tmp_path, api):
    with pytest.raises(HTTPException) as err:
        await api.attach_project(
            api.AttachProjectRequest(repo=str(tmp_path / "does-not-exist")))
    assert err.value.status_code == 400


async def test_attach_requires_a_repo(tmp_path, api):
    with pytest.raises(HTTPException) as err:
        await api.attach_project(api.AttachProjectRequest(repo="   "))
    assert err.value.status_code == 400


def test_the_route_is_actually_registered():
    """Defined is not the same as reachable: a route below ``/{project_id}``
    style patterns, or one nobody mounted, is invisible to a client."""
    from api.routes.projects import router

    found = [
        r for r in router.routes
        if getattr(r, "path", "") == "/attach"
        and "POST" in (getattr(r, "methods", None) or set())
    ]
    assert found, "POST /attach is not registered on the projects router"
