"""API endpoint for full-text skill search (R17 + R23).

Wraps :mod:`kairos.skill_search` behind a JSON HTTP endpoint
so the web UI can call it without spawning Python subprocesses.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)
router = APIRouter()


# A short-lived in-memory index cache (10 min). Different
# processes share the on-disk DB; this just avoids re-reading
# the skill files on every API call.
_INDEX_PATH: Optional[Path] = None
_INDEX_BUILD_TS: float = 0.0
_INDEX_TTL_S = 600.0


def _get_or_build_index(project_dir: Optional[Path] = None):
    """Return a fresh (or cached) skill search index.

    The cache is per-process and per-project-dir. After 10
    minutes the cache expires and the next call rebuilds.
    """
    global _INDEX_PATH, _INDEX_BUILD_TS
    now = time.time()
    if (
        _INDEX_PATH is not None
        and (now - _INDEX_BUILD_TS) < _INDEX_TTL_S
    ):
        return _INDEX_PATH
    try:
        from kairos.skill_search import build_index_from_loader
        from kairos.skills import SkillsLoader
        # Build to a temp file
        import tempfile
        fd, path = tempfile.mkstemp(prefix="kairos-skills-", suffix=".sqlite")
        import os
        os.close(fd)
        loader = SkillsLoader(project_dir=project_dir)
        build_index_from_loader(loader, Path(path))
        _INDEX_PATH = Path(path)
        _INDEX_BUILD_TS = now
        return _INDEX_PATH
    except Exception as exc:
        logger.debug("skill index build failed: %s", exc)
        return None


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(10, ge=1, le=50),
) -> Dict[str, Any]:
    """Full-text search over the skill library.

    Returns the top-``limit`` matches with name, source path,
    priority, score, and a short snippet. Uses SQLite FTS5
    when available, falls back to a Python-side token match
    otherwise (slower but always works).
    """
    from kairos.skill_search import search as _do_search
    db_path = _get_or_build_index()
    try:
        if db_path is not None:
            results = _do_search(q, db_path=db_path, limit=limit)
        else:
            # Fallback: use the loader directly (re-discovers
            # every call; not great but works)
            from kairos.skill_search import _search_python
            from kairos.skills import SkillsLoader
            loader = SkillsLoader()
            results = _search_python(loader.discover(), q, limit=limit)
    except Exception as exc:
        logger.warning("skill search failed: %s", exc)
        raise HTTPException(status_code=500,
                            detail=f"skill search failed: {exc}")
    return {"query": q, "count": len(results), "results": results}


@router.post("/reindex")
async def reindex() -> Dict[str, Any]:
    """Force a re-index of the skill library. Next search will
    pick up the new index."""
    global _INDEX_PATH, _INDEX_BUILD_TS
    _INDEX_PATH = None
    _INDEX_BUILD_TS = 0.0
    return {"status": "reindex requested"}
