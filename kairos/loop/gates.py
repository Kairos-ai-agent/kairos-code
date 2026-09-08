"""Loop termination thresholds and pure gate helpers.

This module is deprecated. Import from `kairos.config.gates` instead.
Kept for backward compatibility with existing imports.
"""
from __future__ import annotations

# Re-export all constants from the new centralized location
from kairos.config.gates import (  # noqa: F401
    APPROVE_SCORE_THRESHOLD,
    NO_PROGRESS_LIMIT,
    LOOP_SAFETY_CAP,
    PER_ROUND_TIMEOUT_S,
    APPROVAL_TIMEOUT_S,
    COST_TOKEN_CAP,
    COST_TIME_CAP_S,
    INFRA_FAILURE_LIMIT,
    STAGNATION_WINDOW,
    STAGNATION_TOLERANCE,
    HEAVY_REQUIREMENT_CHARS,
    HEAVY_PLAN_ITEMS,
    MAX_SAFETY_CAP,
    MAX_TOKEN_CAP,
    dynamic_caps,
    loop_health_score,
    issues_signature,
    should_auto_approve_plan,
)
