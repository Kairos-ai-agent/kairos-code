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