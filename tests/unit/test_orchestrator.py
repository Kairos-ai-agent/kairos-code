"""Tests for kairos.core.orchestrator (LoopReview mode)."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from kairos.llm.base import LLMConfig
from kairos.llm.model_router import ModelRouter

@pytest.fixture
def mock_router():
    router = ModelRouter()
    fake_provider = MagicMock()
    fake_provider.config = LLMConfig(provider="openai", model="gpt-4o", api_key="sk-test")
    router._role_mapping = {"coder": "test", "reviewer": "test"}
    router._model_configs["test"] = fake_provider.config
    router._model_configs["default"] = fake_provider.config
    router._provider_cache = {"test": fake_provider}
    return router

@pytest.mark.asyncio
async def test_create_project_creates_two_agents(mock_router, tmp_path):
    """LoopReview: a new project has exactly one Coder and one Reviewer."""
    from unittest.mock import patch
    from kairos.core.orchestrator import Orchestrator

    fake_db = MagicMock()
    fake_db.load_projects.return_value = []
    fake_db.save_project = MagicMock()
    fake_db.save_message = MagicMock()
    fake_db.delete_project = MagicMock()

    with patch("kairos.core.orchestrator.Persistence", return_value=fake_db):
        orch = Orchestrator(model_router=mock_router, workspace_base=tmp_path)
        p = orch.create_project("demo", "a demo", work_dir="")
        assert p.coder is not None
        assert p.reviewer is not None
        assert p.coder.role == "coder"
        assert p.reviewer.role == "reviewer"
        # Both registered globally.
        assert p.coder.agent_id in orch._agents
        assert p.reviewer.agent_id in orch._agents
        # Coder gets the full tool set; Reviewer is read-only.
        coder_tool_names = {t.name for t in p.coder.tools}
        reviewer_tool_names = {t.name for t in p.reviewer.tools}
        assert "file_write" in coder_tool_names
        assert "multi_edit" in coder_tool_names
        assert "grep" in coder_tool_names
        assert "git" in coder_tool_names
        assert "webfetch" in coder_tool_names
        # Reviewer must not have write tools or web access.
        assert "file_write" not in reviewer_tool_names
        assert "multi_edit" not in reviewer_tool_names
        assert "webfetch" not in reviewer_tool_names
        assert "file_read" in reviewer_tool_names

@pytest.mark.asyncio
async def test_create_team_uses_work_dir_when_set(tmp_path):
    """Regression: tools must be sandboxed to work_dir, not workspace/<id>."""
    from unittest.mock import patch
    from kairos.core.orchestrator import Orchestrator, Project

    user_workdir = tmp_path / "user_repo"
    user_workdir.mkdir()

    fake_db = MagicMock()
    fake_db.load_projects.return_value = []
    fake_db.save_project = MagicMock()

    with patch("kairos.core.orchestrator.Persistence", return_value=fake_db):
        orch = Orchestrator.__new__(Orchestrator)
        orch.message_bus = MagicMock()
        orch._agents = {}
        orch._dispatch_tasks = set()
        orch.model_router = mock_router() if False else MagicMock()
        from kairos.llm.base import LLMConfig
        cfg = LLMConfig(provider="openai", model="gpt-4o", api_key="sk-test")
        orch.model_router.get_provider_for_role = MagicMock(
            return_value=MagicMock(config=cfg)
        )

        project = Project("p1", "demo", "", workspace=tmp_path / "p1",
                           work_dir=str(user_workdir))
        orch._create_agents(project)

        roots = set()
        for t in project.coder.tools:
            root = getattr(t, "_allowed_root", None) or getattr(t, "_allowed_cwd", None)
            if root:
                roots.add(str(root))
        assert any(str(user_workdir) in r for r in roots), \
            f"expected user work_dir in tool roots, got {roots}"

# ---------------------------------------------------------------------- AgentState

def test_agent_state_includes_progress_fields():
    from kairos.agents.base import AgentState
    s = AgentState(agent_id="x", name="X", role="coder",
                   status="acting", current_turn=3, total_turns=8,
                   current_tool="file_write")
    d = s.model_dump()
    assert d["current_turn"] == 3
    assert d["total_turns"] == 8
    assert d["current_tool"] == "file_write"

def test_agent_progress_defaults_when_idle():
    from kairos.agents.base import AgentState
    s = AgentState(agent_id="x", name="X", role="coder")
    d = s.model_dump()
    assert not d["current_turn"]
    assert not d["total_turns"]
    assert not d["current_tool"]

def test_coder_and_reviewer_have_separate_turn_budgets():
    from kairos.agents.roles import Coder, Reviewer
    from kairos.config.settings import settings
    # Coder needs higher turn budget for complex tasks (default 200)
    assert Coder.MAX_TOOL_TURNS == settings.coder.max_tool_turns, \
        f"Coder MAX_TOOL_TURNS should match config ({settings.coder.max_tool_turns})"
    # Reviewer needs enough turns to read several files + run tests
    # before emitting a verdict.
    assert Reviewer.MAX_TOOL_TURNS == settings.reviewer.max_tool_turns, \
        f"Reviewer MAX_TOOL_TURNS should match config ({settings.reviewer.max_tool_turns})"

# ---------------------------------------------------------------------- Review verdict parser

@pytest.mark.parametrize("raw,expected_approve", [
    # Raw JSON
    (json.dumps({"approve": True, "score": 95, "issues": [], "summary": "lgtm"}), True),
    # Fenced JSON
    ("```json\n" + json.dumps({"approve": False, "score": 50, "issues": [
        {"category": "correctness", "severity": "MAJOR", "file": "x.py", "line": 1,
         "description": "bug", "fix_instruction": "fix it"}
    ], "summary": "fix bug"}) + "\n```", False),
    # Bare object
    ("noise " + json.dumps({"approve": True, "score": 88, "issues": [], "summary": "ok"}) + " suffix", True),
    # Unparseable → conservative reject
    ("just plain text", False),
])
def test_review_verdict_parser(raw, expected_approve):
    from kairos.loop.review_loop import _parse_review_verdict
    v = _parse_review_verdict(raw)
    assert v["approve"] is expected_approve

def test_review_verdict_critical_issue_preserved():
    """Even if approve=true is in JSON, we report CRITICAL issues faithfully
    so the orchestrator's approval gate can reject."""
    from kairos.loop.review_loop import _parse_review_verdict
    raw = json.dumps({
        "approve": True, "score": 99,
        "issues": [{"category": "security", "severity": "CRITICAL",
                    "description": "SQL injection", "fix_instruction": "use parameterized query"}],
        "summary": "looks fine but has critical bug"
    })
    v = _parse_review_verdict(raw)
    assert v["approve"] is True   # LLM said approve
    assert any(i["severity"] == "CRITICAL" for i in v["issues"])

@pytest.mark.parametrize("raw,expected_mode", [
    # Tool-limit hit should be flagged distinctly from a parse error so
    # the orchestrator can skip the no_progress counter (the Coder
    # didn't actually repeat the same problem — Reviewer just ran out
    # of turns).
    ("Tool call limit reached after 12 turns.", "tool_limit"),
    ("", "tool_limit"),
    # Real parse failures (Reviewer returned something but not JSON) get
    # a different mode and DO count toward no_progress.
    ("just plain text, no JSON", "parse_fail"),
    ("the verdict is: looks fine", "parse_fail"),
])
def test_review_verdict_failure_modes(raw, expected_mode):
    from kairos.loop.review_loop import _parse_review_verdict
    v = _parse_review_verdict(raw)
    assert v.get("_failure_mode") == expected_mode
    assert v["approve"] is False
    assert v["score"] == 0

# ---------------------------------------------------------------------- Loop no-progress detection

def test_issues_signature_ignores_fix_instruction_changes():
    """Same problem with a re-worded fix is still the same issue — must
    count as no-progress so the loop terminates instead of looping forever."""
    from kairos.loop.review_loop import _issues_signature
    a = [{"file": "x.py", "line": 10, "description": "missing error handling"}]
    b = [{"file": "x.py", "line": 10, "description": "missing error handling",
          "fix_instruction": "try/except around db call"}]
    assert _issues_signature(a) == _issues_signature(b)

def test_issues_signature_distinguishes_different_files():
    from kairos.loop.review_loop import _issues_signature
    a = [{"file": "x.py", "line": 10, "description": "bug"}]
    b = [{"file": "y.py", "line": 10, "description": "bug"}]
    assert _issues_signature(a) != _issues_signature(b)

# ---------------------------------------------------------------------- Loop start/stop

@pytest.mark.asyncio
async def test_stop_loop_marks_user_stopped():
    """stop_loop must set the session flag so the loop body exits cleanly."""
    from unittest.mock import patch
    from kairos.core.orchestrator import Orchestrator

    fake_db = MagicMock()
    fake_db.load_projects.return_value = []
    fake_db.save_project = MagicMock()

    with patch("kairos.core.orchestrator.Persistence", return_value=fake_db):
        orch = Orchestrator.__new__(Orchestrator)
        # Stub the loop session that start_loop would have created.
        from kairos.loop.review_loop import LoopSession
        project = MagicMock()
        project.id = "p1"
        project.coder = MagicMock()
        project.reviewer = MagicMock()
        project.loop_task = None
        project.loop_session = LoopSession(
            project=project, message_bus=MagicMock(),
            coder=project.coder, reviewer=project.reviewer,
        )
        orch._projects = {"p1": project}

        ok = orch.stop_loop("p1")
        assert ok is True
        assert project.loop_session.user_stopped is True