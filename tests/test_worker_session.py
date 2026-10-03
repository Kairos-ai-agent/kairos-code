"""The experiment the requirement asks for: three dispatches, one session.

Scenario: a main agent (Claude Code, Codex, …) hands Kairos three tasks in a
row for the same repository. What must be true is that the worker does not
start over each time — task 3 must inherit what task 1 learned, and it must
still do so after the process has been restarted.

"Same session" here means same ``project_id``, because every durable memory
channel is keyed by it: project notes, preferences, skills, checkpoints,
artifacts, loop rounds, and the ``memory_kb.json`` written under the project
directory. A note written during dispatch 1 and readable during dispatch 4 is
therefore real evidence, not an id comparison.

Agent construction is stubbed out: it needs MCP/network wiring that has a
known teardown hang in this environment, and identity is what is under test.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

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


def _orchestrator(tmp_path, db_path, router):
    from kairos.core.orchestrator import Orchestrator
    from kairos.core.persistence import Persistence

    return Orchestrator(
        model_router=router,
        workspace_base=tmp_path / "workspaces",
        db=Persistence(db_path),
    )


@pytest.fixture
def no_agents(monkeypatch):
    from kairos.core.orchestrator import Orchestrator

    monkeypatch.setattr(
        Orchestrator, "_create_agents", lambda self, project: None)


def test_three_dispatches_land_on_one_worker(tmp_path, mock_router, no_agents):
    from kairos import worker_identity

    repo = tmp_path / "main-agent-repo"
    repo.mkdir()
    db_path = tmp_path / "kairos.db"

    orch = _orchestrator(tmp_path, db_path, mock_router)
    first = orch.attach_project(str(repo))
    second = orch.attach_project(str(repo))
    third = orch.attach_project(str(repo))

    assert first.id == second.id == third.id
    assert len(orch._projects) == 1, "three dispatches created more than one project"

    binding = worker_identity.load(repo)
    assert binding is not None
    assert binding.project_id == first.id
    assert binding.repo.endswith("main-agent-repo")


def test_memory_carried_between_dispatches_and_across_a_restart(
        tmp_path, mock_router, no_agents):
    repo = tmp_path / "main-agent-repo"
    repo.mkdir()
    db_path = tmp_path / "kairos.db"

    # --- dispatch 1: the worker learns something about this project ---
    orch = _orchestrator(tmp_path, db_path, mock_router)
    project = orch.attach_project(str(repo))
    orch._db.add_project_note(
        project.id, kind="convention", title="接口测试",
        body="本项目所有新接口都必须配一个 pytest 用例", source="worker")

    # --- dispatches 2 and 3: same session, so the note is already there ---
    assert orch.attach_project(str(repo)).id == project.id
    assert orch.attach_project(str(repo)).id == project.id

    # --- a restart: new process, same database ---
    restarted = _orchestrator(tmp_path, db_path, mock_router)
    resumed = restarted.attach_project(str(repo))

    assert resumed.id == project.id
    notes = restarted._db.list_project_notes(resumed.id)
    assert any("所有新接口都必须配一个 pytest 用例" in note["body"]
               for note in notes), "the worker forgot what dispatch 1 taught it"


def test_each_repo_gets_its_own_worker(tmp_path, mock_router, no_agents):
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()

    orch = _orchestrator(tmp_path, tmp_path / "kairos.db", mock_router)
    a = orch.attach_project(str(repo_a))
    b = orch.attach_project(str(repo_b))

    assert a.id != b.id
    assert len(orch._projects) == 2
    assert a.work_dir == str(repo_a.resolve())
    assert b.work_dir == str(repo_b.resolve())


def test_create_project_still_makes_a_fresh_project_each_time(
        tmp_path, mock_router, no_agents):
    """The human-facing path must not change: asking for a new project still
    gives a new project. Only ``attach_project`` is idempotent."""
    orch = _orchestrator(tmp_path, tmp_path / "kairos.db", mock_router)

    assert orch.create_project("a", "one").id != orch.create_project("b", "two").id
    assert len(orch._projects) == 2


def test_attach_project_records_dispatches(tmp_path, mock_router, no_agents):
    from kairos import worker_identity

    repo = tmp_path / "main-agent-repo"
    repo.mkdir()
    orch = _orchestrator(tmp_path, tmp_path / "kairos.db", mock_router)
    project = orch.attach_project(str(repo))

    binding = worker_identity.bind(repo)          # reload from disk
    worker_identity.touch(binding, "T001")
    worker_identity.touch(binding, "T002")

    again = worker_identity.load(repo)
    assert again is not None
    assert again.project_id == project.id
    assert again.dispatches == 2
    assert again.first_task_id == "T001"
    assert again.last_task_id == "T002"
