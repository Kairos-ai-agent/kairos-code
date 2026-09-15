"""Update API — "is there a newer Kairos?" and "install it for me".

The check side is read-only (a cached GitHub Releases lookup). The apply side
only runs when the user clicked in the UI, and it refuses to touch an install it
cannot verify — see :mod:`kairos.updater` for the gating rules.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query

from kairos import updater

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/status")
async def status() -> dict:
    """What this install is, and whether it could replace itself."""
    ok, reason = updater.can_self_update()
    return {
        "version": updater.current_version(),
        "installKind": updater.install_kind(),
        "canSelfUpdate": ok,
        "reason": reason,
        "enabled": updater.update_check_enabled(),
        "platform": updater.platform_key(),
    }


@router.get("/check")
async def check(force: bool = Query(False)) -> dict:
    """Look for a newer release. Cached for 12 h; ``force=true`` bypasses it."""
    return updater.check_for_update(force=force)


@router.post("/apply")
async def apply() -> dict:
    """Download, verify and stage the update. The app restarts to finish it."""
    result = updater.apply_update()
    if not result.get("ok"):
        logger.info("update not applied: %s", result.get("reason"))
    return result
