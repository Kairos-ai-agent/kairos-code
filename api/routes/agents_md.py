"""AGENTS.md API — read / write the project memory file.

R38.6 §29: the project-scoped AGENTS.md file is the single
source of truth for "memory that survives across sessions."
The backend already loads it into every agent's system_prompt
on construction (see ``kairos/agents_md.py``), so the file
just needs a way to be edited from the UI.

Endpoints
---------

GET  /api/agents-md?project_id=...
    Read the project's AGENTS.md content. Returns the raw
    markdown (or the built-in fallback content) plus metadata
    about which file the content came from (project /
    global / fallback).

PUT  /api/agents-md?project_id=...
    Overwrite the project's AGENTS.md. Returns the saved
    content + the new file size. Path-traversal is rejected.

GET  /api/agents-md/template
    Return a starter template the user can paste into a new
    AGENTS.md. The template documents the available sections
    (## Coding conventions, ## Tool usage, ## Review
    contract) and includes a brief example.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from api import deps

logger = logging.getLogger(__name__)

router = APIRouter()


def _orch():
    """Resolve the live orchestrator via the deps module.

    Mirrors the shim in projects.py / workbench.py so tests
    that monkeypatch ``api.deps.orchestrator`` are seen by
    every handler.
    """
    return deps.orchestrator

# Repo root is the parent of api/. The agents_md module lives in
# kairos/agents_md.py; this API lives at api/routes/agents_md.py.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project_root(project_id: str) -> Path:
    """Return the project's effective root dir (work_dir or
    workspace), or raise 404. Mirrors api/routes/workbench.py.
    """
    project = _orch().get_project(project_id)
    if not project:
        raise HTTPException(status_code=404,
                            detail=f"project not found: {project_id}")
    raw = getattr(project, "work_dir", None) or str(project.workspace)
    return Path(raw).resolve()


def _agents_md_path(project_root: Path) -> Path:
    return project_root / "AGENTS.md"


def _safe_read(path: Path) -> Optional[str]:
    if not path.exists() or not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("AGENTS.md: read failed %s: %s", path, exc)
        return None


def _safe_write(path: Path, content: str) -> int:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(status_code=500,
                            detail=f"write failed: {exc}")
    return len(content.encode("utf-8"))


# Default content for the template. A new project gets a copy
# of this if the user clicks "Initialize from template".
DEFAULT_TEMPLATE = """# Project Instructions (AGENTS.md)

This file is read by every agent on this project (Coder,
Reviewer, security, perf, test, docs, design, refactor)
and prepended to the agent's hard-coded system prompt. Use
it to record conventions, tooling choices, or recurring
feedback that the agent should remember across sessions.

The file is loaded by `kairos/agents_md.py` and capped at 4 KB
per scope (project + global). Sections are merged in
project → global order; duplicate section names from a
project AGENTS.md take precedence over the same name from
the global file.

## Coding conventions
- Use TypeScript strict mode for all new code.
- Prefer functional React components; no class components.
- All exported functions need JSDoc.
- No `any` types except for genuinely opaque 3rd-party
  boundaries (with a comment explaining why).

## Tool usage
- Read a file before editing it. Use `fs.list` or the file
  picker; don't guess paths.
- Prefer `fs.edit` for surgical changes; use `fs.write` only
  for full-file rewrites.
- Run `npm test` after any frontend change. The backend has
  pytest under `tests/`.

## Review contract
- All code changes go through the Coder ↔ Reviewer loop.
- A round passes when the weighted score >= 80 and no
  CRITICAL issues remain.
- Security reviews are required for any change touching auth,
  secrets, or external network egress.
"""


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AgentsMdResponse(BaseModel):
    project_id: str
    path: str  # absolute path of the file (or "(built-in fallback)")
    source: str  # "project" | "global" | "fallback" | "missing"
    content: str
    bytes: int
    modified: Optional[float] = None  # epoch seconds; None for fallback


class AgentsMdUpdateRequest(BaseModel):
    content: str


class AgentsMdUpdateResponse(BaseModel):
    path: str
    bytes: int
    saved_at: float


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/agents-md", response_model=AgentsMdResponse)
async def get_agents_md(
    project_id: str = Query(..., description="Project id"),
) -> AgentsMdResponse:
    """Read the project's AGENTS.md. Returns the built-in
    fallback if neither project nor global file exists, so the
    UI always has something to display in the editor.
    """
    import time
    root = _project_root(project_id)
    p = _agents_md_path(root)
    content = _safe_read(p)
    if content is not None:
        try:
            modified = p.stat().st_mtime
        except OSError:
            modified = None
        return AgentsMdResponse(
            project_id=project_id,
            path=str(p),
            source="project",
            content=content,
            bytes=len(content.encode("utf-8")),
            modified=modified,
        )
    # Fall back to the global file if the project hasn't written
    # one yet. Same effect, different file.
    global_path = Path.home() / ".kairos" / "AGENTS.md"
    if global_path.exists():
        g_content = _safe_read(global_path)
        if g_content is not None:
            return AgentsMdResponse(
                project_id=project_id,
                path=str(global_path),
                source="global",
                content=g_content,
                bytes=len(g_content.encode("utf-8")),
                modified=global_path.stat().st_mtime,
            )
    # No file anywhere. Return the built-in fallback so the UI
    # shows a useful editor pane rather than an empty box.
    return AgentsMdResponse(
        project_id=project_id,
        path="(built-in fallback)",
        source="fallback",
        content=DEFAULT_TEMPLATE,
        bytes=len(DEFAULT_TEMPLATE.encode("utf-8")),
        modified=None,
    )


@router.put("/agents-md", response_model=AgentsMdUpdateResponse)
async def put_agents_md(
    project_id: str = Query(..., description="Project id"),
    body: AgentsMdUpdateRequest = ...,
) -> AgentsMdUpdateResponse:
    """Overwrite the project's AGENTS.md.

    We deliberately don't validate the body here — the user is
    the source of truth and the parser is forgiving (it just
    looks for `## Heading` blocks). If the body is empty,
    the project's AGENTS.md is deleted instead.
    """
    import time
    root = _project_root(project_id)
    p = _agents_md_path(root)
    # Reject path traversal: the resolved path must be under root.
    try:
        p.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid path")
    if not body.content.strip():
        # Empty content — delete the file (revert to fallback).
        if p.exists():
            try:
                p.unlink()
            except OSError as exc:
                raise HTTPException(status_code=500,
                                    detail=f"delete failed: {exc}")
        return AgentsMdUpdateResponse(
            path="(deleted)", bytes=0, saved_at=time.time(),
        )
    written = _safe_write(p, body.content)
    return AgentsMdUpdateResponse(
        path=str(p), bytes=written, saved_at=time.time(),
    )


@router.get("/agents-md/template")
async def get_template() -> dict:
    """Return a starter template the user can paste into a
    fresh AGENTS.md. The template documents the available
    sections and includes an example for each.
    """
    return {"template": DEFAULT_TEMPLATE, "bytes":
            len(DEFAULT_TEMPLATE.encode("utf-8"))}
