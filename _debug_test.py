"""Debug the actual test fixture flow."""
import sys
from pathlib import Path
ROOT = Path("D:/software_bak/Kairos_code").resolve()
sys.path.insert(0, str(ROOT))
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
import pytest


def _build_orch_with_project(pid: str = "p1"):
    from kairos.core.orchestrator import Orchestrator, Project
    from kairos.llm.model_router import ModelRouter
    from kairos.llm.base import LLMConfig

    router = ModelRouter()
    fake_provider = MagicMock()
    fake_provider.config = LLMConfig(provider="openai", model="gpt-4o", api_key="sk-test")
    router._role_mapping = {"coder": "t", "reviewer": "t"}
    router._model_configs["t"] = fake_provider.config
    router._model_configs["default"] = fake_provider.config
    router._provider_cache = {"t": fake_provider}

    fake = MagicMock()
    fake.load_projects.return_value = []
    with patch("kairos.core.orchestrator.Persistence", return_value=fake):
        orch = Orchestrator(model_router=router)
    p = Project(pid, "demo", "", Path(f"./workspace/{pid}"), "")
    p.coder = MagicMock()
    p.reviewer = MagicMock()
    session = MagicMock()
    session.ask_pending = False
    session.round = 1
    p.loop_session = session
    orch._projects[pid] = p
    orch.get_ask = lambda pid: {
        "pending": bool(orch._projects[pid].loop_session.ask_pending),
        "question": "", "context": "", "round": 1
    }
    orch.answer_ask = lambda pid, ans: True
    return orch, p


@pytest.fixture
def app_client(monkeypatch):
    from api.app import app
    from api import deps
    orch, project = _build_orch_with_project()
    print(f"  [fixture] deps.orchestrator id: {id(deps.orchestrator)}")
    print(f"  [fixture] orch           id: {id(orch)}")
    print(f"  [fixture] same?           : {orch is deps.orchestrator}")
    monkeypatch.setattr(deps, "orchestrator", orch)
    return TestClient(app), orch, project


def test_debug(app_client):
    client, orch, project = app_client
    from api import deps
    print(f"  [test] deps.orchestrator id: {id(deps.orchestrator)}")
    print(f"  [test] orch           id: {id(orch)}")
    print(f"  [test] same?           : {orch is deps.orchestrator}")
    print(f"  [test] projects keys: {list(orch._projects.keys())}")
    r = client.get("/api/projects/p1/ask")
    print(f"  [test] GET /ask status: {r.status_code}, body: {r.text[:200]}")
