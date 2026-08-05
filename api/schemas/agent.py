"""Agent-related API schemas."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


# LoopReview roles. Anything else is rejected at the API boundary.
RoleName = Literal["coder", "reviewer"]


class CreateProjectRequest(BaseModel):
    name: str = Field(..., max_length=200)
    description: str = Field("", max_length=5000)
    work_dir: str = ""


class StartProjectRequest(BaseModel):
    requirement: str = Field(..., max_length=50000)


class ChatRequest(BaseModel):
    message: str = Field(..., max_length=10000)
    agent_role: str = Field(..., max_length=50)


class ChatResponse(BaseModel):
    agent_id: str
    agent_name: str
    response: str


class AssignTaskRequest(BaseModel):
    agent_role: str = Field(..., max_length=50)
    title: str = Field(..., max_length=200)
    description: str = Field("", max_length=10000)
    context: Dict[str, Any] = {}


class TaskResponse(BaseModel):
    task_id: str
    agent_role: str
    result: str
    status: str


class RoleModelAssignRequest(BaseModel):
    role: RoleName
    model_name: str = Field(..., max_length=100)