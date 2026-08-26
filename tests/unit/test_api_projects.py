"""Tests for project-scoped API endpoints (LoopReview mode)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

@pytest.mark.asyncio
async def test_project_messages_returns_404_for_unknown_project():
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from api.routes.projects import router as projects_router
    from api.deps import orchestrator as real_orch

    real_orch.get_project = MagicMock(return_value=None)
    real_orch.get_message_history = MagicMock(return_value=[])

    app = FastAPI()
    app.include_router(projects_router, prefix="/api/projects")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/projects/nonexistent/messages")
        assert r.status_code == 404

@pytest.mark.asyncio
async def test_project_messages_scoped_query():
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from api.routes.projects import router as projects_router
    from api.deps import orchestrator as real_orch

    real_orch.get_project = MagicMock(return_value=MagicMock(id="p1"))
    real_orch.get_message_history = MagicMock(return_value=[{"id": "m1"}])

    app = FastAPI()
    app.include_router(projects_router, prefix="/api/projects")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/projects/p1/messages")
        assert r.status_code == 200
        assert r.json() == {"messages": [{"id": "m1"}]}
        call = real_orch.get_message_history.call_args
        assert call.kwargs.get("project_id") == "p1" or call.args[1] == "p1"

@pytest.mark.asyncio
async def test_start_loop_returns_immediately_with_background_task():
    """POST /start must kick off the loop in the background, not block on LLM."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from api.routes.projects import router as projects_router
    from api.deps import orchestrator as real_orch

    real_orch.get_project = MagicMock(return_value=MagicMock(
        id="p1", loop_task=None,
    ))
    real_orch.start_loop = AsyncMock(return_value="session-xyz")

    app = FastAPI()
    app.include_router(projects_router, prefix="/api/projects")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/projects/p1/start",
                         json={"requirement": "build a thing"})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "started"
        assert body["session_id"] == "session-xyz"
        assert body["project_id"] == "p1"

@pytest.mark.asyncio
async def test_start_loop_rejects_already_running():
    """409 if a loop is already running for the project."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from api.routes.projects import router as projects_router
    from api.deps import orchestrator as real_orch

    # Simulate "loop already running" — get_project returns a project with
    # an active loop_task.
    fake_task = MagicMock()
    fake_task.done.return_value = False
    real_orch.get_project = MagicMock(return_value=MagicMock(
        id="p1", loop_task=fake_task,
    ))

    app = FastAPI()
    app.include_router(projects_router, prefix="/api/projects")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/projects/p1/start",
                         json={"requirement": "x"})
        assert r.status_code == 409

@pytest.mark.asyncio
async def test_stop_loop_returns_no_loop_when_idle():
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from api.routes.projects import router as projects_router
    from api.deps import orchestrator as real_orch

    real_orch.get_project = MagicMock(return_value=MagicMock(id="p1"))
    real_orch.stop_loop = MagicMock(return_value=False)

    app = FastAPI()
    app.include_router(projects_router, prefix="/api/projects")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/projects/p1/stop")
        assert r.status_code == 200
        assert r.json()["status"] == "no_loop_running"

@pytest.mark.asyncio
async def test_get_loop_state_returns_running_flag():
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from api.routes.projects import router as projects_router
    from api.deps import orchestrator as real_orch

    fake_session = MagicMock()
    fake_session.session_id = "sess-1"
    fake_session.round = 3
    fake_session.last_score = 75
    fake_session.last_approve = False
    fake_session.no_progress_count = 1
    fake_session.history = []
    fake_session.user_stopped = False

    real_orch.get_project = MagicMock(return_value=MagicMock(
        id="p1", loop_session=fake_session,
        loop_task=MagicMock(done=MagicMock(return_value=False)),
    ))

    app = FastAPI()
    app.include_router(projects_router, prefix="/api/projects")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/projects/p1/loop")
        assert r.status_code == 200
        body = r.json()
        assert body["running"] is True
        assert body["round"] == 3
        assert body["last_score"] == 75


# ---------------------------------------------------------------------------
# Plan mode (round 8)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_plan_404_for_unknown_project():
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from api.routes.projects import router as projects_router
    from api.deps import orchestrator as real_orch

    real_orch.get_project = MagicMock(return_value=None)

    app = FastAPI()
    app.include_router(projects_router, prefix="/api/projects")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/projects/nope/plan")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_plan_returns_pending_text():
    """GET /plan returns the Coder's draft plan when one is pending."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from api.routes.projects import router as projects_router
    from api.deps import orchestrator as real_orch

    real_orch.get_project = MagicMock(return_value=MagicMock(id="p1"))
    real_orch.get_plan = MagicMock(return_value={
        "pending": True,
        "decision": None,
        "text": "1. Create models.py\n2. Wire API routes",
        "round": 1,
    })

    app = FastAPI()
    app.include_router(projects_router, prefix="/api/projects")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/projects/p1/plan")
        assert r.status_code == 200
        body = r.json()
        assert body["pending"] is True
        assert "models.py" in body["text"]


@pytest.mark.asyncio
async def test_approve_plan_calls_orchestrator():
    """POST /plan/approve delegates to orchestrator.approve_plan."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from api.routes.projects import router as projects_router
    from api.deps import orchestrator as real_orch

    real_orch.get_project = MagicMock(return_value=MagicMock(id="p1"))
    real_orch.approve_plan = MagicMock(return_value=True)

    app = FastAPI()
    app.include_router(projects_router, prefix="/api/projects")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/projects/p1/plan/approve")
        assert r.status_code == 200
        assert r.json()["status"] == "approved"
        real_orch.approve_plan.assert_called_once_with("p1")


@pytest.mark.asyncio
async def test_reject_plan_calls_orchestrator():
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from api.routes.projects import router as projects_router
    from api.deps import orchestrator as real_orch

    real_orch.get_project = MagicMock(return_value=MagicMock(id="p1"))
    real_orch.reject_plan = MagicMock(return_value=True)

    app = FastAPI()
    app.include_router(projects_router, prefix="/api/projects")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/projects/p1/plan/reject")
        assert r.status_code == 200
        assert r.json()["status"] == "rejected"
        real_orch.reject_plan.assert_called_once_with("p1")


@pytest.mark.asyncio
async def test_approve_plan_with_no_session_returns_no_plan_pending():
    """No active loop session → orchestrator returns False → 200 with no_plan_pending."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from api.routes.projects import router as projects_router
    from api.deps import orchestrator as real_orch

    real_orch.get_project = MagicMock(return_value=MagicMock(id="p1"))
    real_orch.approve_plan = MagicMock(return_value=False)

    app = FastAPI()
    app.include_router(projects_router, prefix="/api/projects")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/projects/p1/plan/approve")
        assert r.status_code == 200
        assert r.json()["status"] == "no_plan_pending"


# ---------------------------------------------------------------------------
# Skills hot-reload (round 8)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reload_skills_404_for_unknown_project():
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from api.routes.projects import router as projects_router
    from api.deps import orchestrator as real_orch

    real_orch.get_project = MagicMock(return_value=None)

    app = FastAPI()
    app.include_router(projects_router, prefix="/api/projects")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/projects/nope/skills/reload")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_reload_skills_returns_count_and_names():
    """POST /skills/reload calls orchestrator.reload_skills and
    returns the result keyed under ``count`` and ``names``."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from api.routes.projects import router as projects_router
    from api.deps import orchestrator as real_orch

    real_orch.get_project = MagicMock(return_value=MagicMock(id="p1"))
    real_orch.reload_skills = MagicMock(return_value={
        "count": 3, "names": ["debug", "lint", "deploy"],
    })

    app = FastAPI()
    app.include_router(projects_router, prefix="/api/projects")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/projects/p1/skills/reload")
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 3
        assert set(body["names"]) == {"debug", "lint", "deploy"}
        assert body["project_id"] == "p1"


@pytest.mark.asyncio
async def test_reload_skills_handles_no_work_dir():
    """When the project has no work_dir, orchestrator returns
    count=0 and an error message — the endpoint must still 200."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from api.routes.projects import router as projects_router
    from api.deps import orchestrator as real_orch

    real_orch.get_project = MagicMock(return_value=MagicMock(id="p1"))
    real_orch.reload_skills = MagicMock(return_value={
        "count": 0, "names": [], "error": "no_work_dir",
    })

    app = FastAPI()
    app.include_router(projects_router, prefix="/api/projects")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/projects/p1/skills/reload")
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 0
        assert body["names"] == []
        assert body["error"] == "no_work_dir"


@pytest.mark.asyncio
async def test_list_skills_uses_reload_endpoint():
    """GET /skills reuses the same discovery as POST /skills/reload
    so the sidebar listing and the manual refresh are consistent."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from api.routes.projects import router as projects_router
    from api.deps import orchestrator as real_orch

    real_orch.get_project = MagicMock(return_value=MagicMock(id="p1"))
    real_orch.reload_skills = MagicMock(return_value={
        "count": 1, "names": ["only-one"],
    })

    app = FastAPI()
    app.include_router(projects_router, prefix="/api/projects")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/projects/p1/skills")
        assert r.status_code == 200
        body = r.json()
        assert body["names"] == ["only-one"]
        real_orch.reload_skills.assert_called_with("p1")