"""Debug: directly call the orchestrator methods and see what happens."""
import sys
from pathlib import Path
ROOT = Path("D:/software_bak/Kairos_code").resolve()
sys.path.insert(0, str(ROOT))
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

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

fake_db = MagicMock()
fake_db.load_projects.return_value = []
with patch("kairos.core.orchestrator.Persistence", return_value=fake_db):
    orch = Orchestrator(model_router=router)
p = Project("p1", "demo", "", Path("./workspace/p1"), "")
p.coder = MagicMock()
p.reviewer = MagicMock()
session = MagicMock()
session.ask_question = ""
session.ask_context = ""
session.ask_pending = False
session.ask_answer = MagicMock()
session.round = 1
p.loop_session = session
orch._projects["p1"] = p


def get_ask(project_id):
    proj = orch._projects.get(project_id)
    if not proj:
        return None
    s = proj.loop_session
    if not s:
        return None
    return {"pending": bool(getattr(s, "ask_pending", False)),
            "question": getattr(s, "ask_question", ""),
            "context": getattr(s, "ask_context", ""),
            "round": getattr(s, "round", 0)}


def answer_ask(project_id, answer):
    proj = orch._projects.get(project_id)
    if not proj or not proj.loop_session:
        return False
    proj.loop_session.ask_answer(answer)
    proj.loop_session.ask_pending = False
    return True


orch.get_ask = get_ask
orch.answer_ask = answer_ask

# Direct method call (no HTTP)
print("Direct get_ask:", orch.get_ask("p1"))
print("Direct answer_ask:", orch.answer_ask("p1", "Postgres"))
print("After answer:", orch.get_ask("p1"))

# Now via HTTP
from api import deps
deps.orchestrator = orch
from api.app import app
client = TestClient(app)
print("HTTP GET:", client.get("/api/projects/p1/ask").json())
print("HTTP POST:", client.post("/api/projects/p1/ask/answer",
                                 json={"answer": "x"}).json())
print("HTTP GET after:", client.get("/api/projects/p1/ask").json())
