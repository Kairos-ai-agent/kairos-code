"""Memory API — surfaces the agent's notes, skills, working fixes,
and cross-project KB to the UI so the user can see what the agent has
learned and curate it.

Endpoints are deliberately thin: the Persistence layer already enforces
all invariants. The router just validates input and shapes output.
"""
from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.deps import orchestrator

router = APIRouter()


def _db():
    return getattr(orchestrator, "_db", None)


# --------------------------------------------------------- request schemas

class NoteIn(BaseModel):
    kind: str = Field(default="convention", pattern="^(convention|pitfall|architecture|fact)$")
    title: str = Field(..., min_length=1, max_length=120)
    body: str = Field(..., min_length=1, max_length=2000)
    source: str = Field(default="user", max_length=40)


class SkillIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    triggers: List[str] = Field(default_factory=list)
    body: str = Field(..., min_length=1, max_length=2000)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    source: str = Field(default="user", max_length=40)


# --------------------------------------------------------- helpers

def _project_exists(project_id: str) -> None:
    if not orchestrator.get_project(project_id):
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")


def _need_db() -> Any:
    db = _db()
    if db is None:
        raise HTTPException(status_code=503, detail="Persistence not initialized")
    return db


# --------------------------------------------------------- overview

@router.get("/{project_id}/memory")
async def get_memory_overview(project_id: str, query: Optional[str] = None) -> dict:
    """Return a small summary of everything the agent remembers about
    this project: counts of notes/skills/working-fixes, the most-used
    items, and any cross-project insights matching query."""
    _project_exists(project_id)
    db = _need_db()
    out: dict = {"project_id": project_id}
    try:
        notes = db.list_project_notes(project_id, limit=50)
        out["notes_count"] = len(notes)
        out["notes"] = notes[:20]
    except Exception:
        out["notes_count"] = 0
        out["notes"] = []
    try:
        skills = db.list_skills(project_id)
        out["skills_count"] = len(skills)
        out["skills"] = skills[:20]
    except Exception:
        out["skills_count"] = 0
        out["skills"] = []
    try:
        if query:
            insights = db.search_global_insights(query, limit=8)
        else:
            insights = []
        out["global_insights"] = insights
    except Exception:
        out["global_insights"] = []
    try:
        # Try to fetch the recent working fixes by searching the round history.
        rounds = db.load_loop_rounds(project_id, limit=10) or []
        out["recent_rounds"] = len(rounds)
    except Exception:
        out["recent_rounds"] = 0
    return out


# --------------------------------------------------------- notes

@router.get("/{project_id}/memory/notes")
async def list_notes(project_id: str, limit: int = 20) -> dict:
    _project_exists(project_id)
    db = _need_db()
    try:
        notes = db.list_project_notes(project_id, limit=max(1, min(limit, 100)))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"notes": notes}


@router.post("/{project_id}/memory/notes")
async def add_note(project_id: str, request: NoteIn) -> dict:
    _project_exists(project_id)
    db = _need_db()
    try:
        nid = db.add_project_note(
            project_id, request.kind, request.title, request.body, request.source,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": nid, "status": "added"}


@router.delete("/{project_id}/memory/notes/{note_id}")
async def delete_note(project_id: str, note_id: int) -> dict:
    _project_exists(project_id)
    db = _need_db()
    ok = db.delete_project_note(project_id, note_id)
    return {"deleted": ok}


# --------------------------------------------------------- skills

@router.get("/{project_id}/memory/skills")
async def list_skills(project_id: str) -> dict:
    _project_exists(project_id)
    db = _need_db()
    try:
        skills = db.list_skills(project_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"skills": skills}


@router.post("/{project_id}/memory/skills")
async def add_skill(project_id: str, request: SkillIn) -> dict:
    _project_exists(project_id)
    db = _need_db()
    try:
        sid = db.add_skill(
            project_id, request.name, request.triggers, request.body,
            confidence=request.confidence, source=request.source,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": sid, "status": "added"}


@router.delete("/{project_id}/memory/skills/{skill_id}")
async def delete_skill(project_id: str, skill_id: int) -> dict:
    _project_exists(project_id)
    db = _need_db()
    ok = db.delete_skill(project_id, skill_id)
    return {"deleted": ok}


# --------------------------------------------------------- working fixes

@router.get("/{project_id}/memory/fixes")
async def list_fixes(project_id: str, signature: Optional[str] = None) -> dict:
    _project_exists(project_id)
    db = _need_db()
    out: dict = {"fixes": []}
    if signature:
        try:
            fix = db.find_working_fix(project_id, signature)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        out["fixes"] = [fix] if fix else []
    return out


# --------------------------------------------------------- global KB

@router.get("/{project_id}/memory/kb")
async def search_kb(query: str = "", limit: int = 10) -> dict:
    db = _need_db()
    try:
        insights = db.search_global_insights(query, limit=max(1, min(limit, 50)))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"insights": insights}


# --------------------------------------------------------- loop-history (memory-context)

@router.get("/{project_id}/memory/history")
async def search_history(project_id: str, query: str = "", limit: int = 5) -> dict:
    _project_exists(project_id)
    db = _need_db()
    try:
        rounds = db.search_loop_rounds(project_id, query, limit=max(1, min(limit, 20)))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"rounds": rounds}
