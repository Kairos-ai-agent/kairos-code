"""Browser panel HTTP API (R38.6 §32).

Routes:
  POST   /api/browser/{project_id}/navigate    {url, wait_until?}
  GET    /api/browser/{project_id}/screenshot  → image/png
  GET    /api/browser/{project_id}/current     → {url, title, viewport, ...}
  POST   /api/browser/{project_id}/click       {x, y}
  POST   /api/browser/{project_id}/type        {text}
  POST   /api/browser/{project_id}/press       {key}
  POST   /api/browser/{project_id}/back
  POST   /api/browser/{project_id}/forward
  POST   /api/browser/{project_id}/reload
  POST   /api/browser/{project_id}/viewport    {width, height}
  GET    /api/browser/{project_id}/console     → [{type, text, ts}]
  POST   /api/browser/{project_id}/evaluate    {expression} → any
  POST   /api/browser/{project_id}/close

The browser is shared across users of the same project (one
context per project_id, persistent on disk). This is the same
"shared per project" pattern as the workbench checkpoints.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Response

from kairos.browser import BrowserManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/browser", tags=["browser"])

# Singleton — wired by api/app.py at startup. We use a module-
# level Optional so the router can be imported before the app
# wires the manager (avoids a circular import).
_manager: Optional[BrowserManager] = None


def set_manager(mgr: BrowserManager) -> None:
    """Called once at app startup with the process-wide manager."""
    global _manager
    _manager = mgr


def _mgr() -> BrowserManager:
    if _manager is None:
        raise HTTPException(
            status_code=503,
            detail="browser manager not initialized",
        )
    return _manager


def _project_id_for(project_id: str) -> str:
    """Validate project id is sane (alphanumeric + dash/underscore).
    Defends against path traversal if we ever add filesystem access."""
    if not project_id or len(project_id) > 64:
        raise HTTPException(status_code=400, detail="bad project_id")
    if not all(c.isalnum() or c in "-_" for c in project_id):
        raise HTTPException(status_code=400,
                            detail="project_id must be alphanumeric")
    return project_id


@router.post("/{project_id}/navigate")
async def navigate(project_id: str, body: Dict[str, Any]):
    _project_id_for(project_id)
    url = body.get("url", "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    if not url.startswith(("http://", "https://")):
        # Default to https for bare domains
        url = "https://" + url
    mgr = _mgr()
    try:
        return await mgr.navigate(project_id, url)
    except Exception as exc:  # noqa: BLE001
        logger.exception("browser navigate failed")
        raise HTTPException(status_code=500,
                            detail=f"navigate failed: {exc}")


@router.get("/{project_id}/screenshot")
async def screenshot(project_id: str, full_page: bool = False):
    _project_id_for(project_id)
    mgr = _mgr()
    try:
        png = await mgr.screenshot(project_id, full_page=full_page)
    except Exception as exc:  # noqa: BLE001
        logger.exception("screenshot failed")
        raise HTTPException(status_code=500,
                            detail=f"screenshot failed: {exc}")
    return Response(content=png, media_type="image/png")


@router.get("/{project_id}/current")
async def current(project_id: str):
    _project_id_for(project_id)
    return await _mgr().current(project_id)


@router.post("/{project_id}/click")
async def click(project_id: str, body: Dict[str, Any]):
    _project_id_for(project_id)
    try:
        x = int(body.get("x", 0))
        y = int(body.get("y", 0))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="x and y must be ints")
    await _mgr().click(project_id, x, y)
    return {"ok": True, "x": x, "y": y}


@router.post("/{project_id}/type")
async def type_text(project_id: str, body: Dict[str, Any]):
    _project_id_for(project_id)
    text = body.get("text", "")
    if not isinstance(text, str):
        raise HTTPException(status_code=400, detail="text must be string")
    await _mgr().type_text(project_id, text)
    return {"ok": True, "length": len(text)}


@router.post("/{project_id}/press")
async def press_key(project_id: str, body: Dict[str, Any]):
    _project_id_for(project_id)
    key = body.get("key", "")
    if not key or not isinstance(key, str):
        raise HTTPException(status_code=400, detail="key is required")
    await _mgr().press_key(project_id, key)
    return {"ok": True, "key": key}


@router.post("/{project_id}/back")
async def back(project_id: str):
    _project_id_for(project_id)
    await _mgr().back(project_id)
    return {"ok": True}


@router.post("/{project_id}/forward")
async def forward(project_id: str):
    _project_id_for(project_id)
    await _mgr().forward(project_id)
    return {"ok": True}


@router.post("/{project_id}/reload")
async def reload(project_id: str):
    _project_id_for(project_id)
    await _mgr().reload(project_id)
    return {"ok": True}


@router.post("/{project_id}/viewport")
async def set_viewport(project_id: str, body: Dict[str, Any]):
    _project_id_for(project_id)
    try:
        w = int(body.get("width", 1280))
        h = int(body.get("height", 800))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400,
                            detail="width/height must be ints")
    if not (320 <= w <= 3840) or not (240 <= h <= 2160):
        raise HTTPException(status_code=400,
                            detail="viewport out of range")
    await _mgr().set_viewport(project_id, w, h)
    return {"ok": True, "width": w, "height": h}


@router.get("/{project_id}/console")
async def get_console(project_id: str, limit: int = 50):
    _project_id_for(project_id)
    msgs = await _mgr().get_console(project_id, limit=limit)
    return {"messages": msgs}


@router.post("/{project_id}/evaluate")
async def evaluate(project_id: str, body: Dict[str, Any]):
    _project_id_for(project_id)
    expr = body.get("expression", "")
    if not expr or not isinstance(expr, str):
        raise HTTPException(status_code=400, detail="expression required")
    try:
        result = await _mgr().evaluate(project_id, expr)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500,
                            detail=f"evaluate failed: {exc}")
    # Truncate huge results to avoid blowing up the response
    if isinstance(result, str) and len(result) > 100_000:
        result = result[:100_000] + "...[truncated]"
    return {"result": result}


@router.post("/{project_id}/close")
async def close(project_id: str):
    _project_id_for(project_id)
    ok = await _mgr().close_project(project_id)
    return {"ok": ok}
