"""FastAPI Application for Kairos Code."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from kairos import __version__
from kairos.config.settings import settings
from kairos.metrics import install_middleware, install_metrics_endpoint
from api.deps import orchestrator
from api.routes.agents import router as agents_router
from api.routes.projects import router as projects_router
from api.routes.review import router as review_router
from api.routes.config import router as config_router
from api.routes.websocket import router as ws_router
from api.routes.memory import router as memory_router
from api.routes.teams import router as teams_router
from api.routes.checkpoints import router as checkpoints_router
from api.routes.traces import router as traces_router
from api.routes.cloud import router as cloud_router
from api.routes.cost import router as cost_router
from api.routes.skill_search import router as skill_search_router

log = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    yield
    # Shutdown: close all LLM provider clients
    log.info("Shutting down: closing LLM provider clients...")
    for agent in orchestrator._agents.values():
        try:
            await agent._llm.close()
        except Exception:
            pass
    log.info("Shutdown complete.")

# Create FastAPI app
app = FastAPI(
    title="Kairos Code",
    description="Multi-Agent Collaboration Platform for Software Development",
    version=__version__,
    lifespan=lifespan,
)

# CORS: read allowed origins from KAIROS_CORS_ORIGINS (comma-separated).
# Avoid wildcard when credentials are enabled — browsers reject that combo
# and we don't actually need either "*" in the default dev setup.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
)

# Prometheus metrics: request count + latency histogram, exposed at /metrics
install_middleware(app)
install_metrics_endpoint(app)

# Include routers
app.include_router(agents_router, prefix="/api/agents", tags=["agents"])
app.include_router(projects_router, prefix="/api/projects", tags=["projects"])
app.include_router(review_router, prefix="/api/review", tags=["review"])
app.include_router(config_router, prefix="/api/config", tags=["config"])
app.include_router(memory_router, prefix="/api/projects", tags=["memory"])
app.include_router(ws_router, prefix="/ws", tags=["websocket"])
app.include_router(teams_router, prefix="/api/projects", tags=["teams"])
app.include_router(checkpoints_router, prefix="/api/projects", tags=["checkpoints"])
app.include_router(traces_router, prefix="/api/projects", tags=["traces"])
app.include_router(cloud_router, prefix="/api/projects", tags=["cloud"])
app.include_router(cost_router, prefix="/api/cost", tags=["cost"])
app.include_router(skill_search_router, prefix="/api/skill_search", tags=["skill_search"])

# Global message stream — mounted at /api/messages (not under /projects
# because FastAPI's path-param matching can shadow literal /messages
# routes). Used by the Collaboration page for the project-agnostic feed.
@app.get("/api/messages")
async def global_messages(limit: int = 100):
    return {"messages": orchestrator.get_message_history(limit=limit)}

@app.get("/")
async def root():
    """Root endpoint - redirects to frontend or shows API info."""
    return JSONResponse({
        "name": "Kairos Code",
        "version": __version__,
        "status": "running",
        "endpoints": {
            "health": "/api/health",
            "dashboard": "/api/dashboard",
            "messages": "/api/messages",
            "docs": "/docs",
            "frontend": "http://localhost:3000",
        },
    })

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": __version__}

@app.get("/api/dashboard")
async def dashboard():
    """Get dashboard overview data."""
    projects = orchestrator.list_projects()
    agent_states = orchestrator.get_all_agent_states()
    messages = orchestrator.get_message_history(limit=20)
    return {
        "project_count": len(projects),
        "agent_count": len(agent_states),
        "agents": agent_states,
        "recent_messages": messages,
    }
