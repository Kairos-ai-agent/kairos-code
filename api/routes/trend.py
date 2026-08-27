"""API endpoint for the multi-run trend aggregator (R25).

Wraps :mod:`kairos.trend` behind a JSON HTTP endpoint so the
UI can show pass_rate / cost trends without spawning a
subprocess.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/trend")
async def trend(
    directory: str = Query("results",
                            description="Directory of run-*.json files"),
    window: int = Query(20, ge=1, le=200,
                       description="Take the last N runs"),
    pattern: str = Query("run-*.json",
                         description="Glob pattern"),
) -> Dict[str, Any]:
    """Return the trend report (pass_rate + cost over time)."""
    from kairos.trend import aggregate_trend_from_dir
    try:
        report = aggregate_trend_from_dir(
            Path(directory), window=window, pattern=pattern,
        )
    except Exception as exc:
        logger.warning("trend aggregation failed: %s", exc)
        raise HTTPException(status_code=500,
                            detail=f"trend failed: {exc}")
    return report.to_dict()


@router.get("/per_case")
async def per_case_trend(
    directory: str = Query("results"),
    window: int = Query(20, ge=1, le=200),
) -> Dict[str, Any]:
    """For each case name, return its pass history across the last
    ``window`` runs. Useful for "which case has been flaky?"."""
    from kairos.trend import aggregate_per_case_trend
    try:
        report = aggregate_per_case_trend(Path(directory), window=window)
    except Exception as exc:
        logger.warning("per-case trend failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"per-case failed: {exc}")
    return report
