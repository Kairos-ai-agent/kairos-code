"""Agent management API routes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query

from api.deps import orchestrator
from api.schemas.agent import (
    AssignTaskRequest,
    ChatRequest,
    ChatResponse,
    TaskResponse,
)
from kairos.agents.base import AgentTask

router = APIRouter()


@router.get("")
async def list_agents(project_id: str = None):
    """List all agents or agents in a project."""
    # Auto-refresh agent models from current settings
    orchestrator.refresh_all_agents()
    states = orchestrator.get_all_agent_states(project_id)
    return {"agents": states}


@router.get("/{agent_id}")
async def get_agent(agent_id: str):
    """Get agent details."""
    states = orchestrator.get_all_agent_states()
    for state in states:
        if state["agent_id"] == agent_id:
            return state
    raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")


@router.post("/chat")
async def chat_with_agent(request: ChatRequest, project_id: str = Query(..., description="Project ID (required)")):
    """Chat directly with an agent."""
    try:
        response = await orchestrator.chat_with_agent(project_id, request.agent_role, request.message)
        return ChatResponse(
            agent_id=f"{project_id}.{request.agent_role}",
            agent_name=request.agent_role,
            response=response,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/task")
async def assign_task(request: AssignTaskRequest, project_id: str = Query(..., description="Project ID (required)")):
    """Assign a task to an agent."""
    task = AgentTask(
        id=uuid.uuid4().hex[:8],
        title=request.title,
        description=request.description,
        context=request.context,
    )

    try:
        result = await orchestrator.assign_task(project_id, request.agent_role, task)
        return TaskResponse(
            task_id=task.id,
            agent_role=request.agent_role,
            result=result,
            status=task.status,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/refresh")
async def refresh_agents():
    """Refresh all agents with current model settings."""
    orchestrator.refresh_all_agents()
    return {"status": "ok", "agents": orchestrator.get_all_agent_states()}
