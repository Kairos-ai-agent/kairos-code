"""Loop termination thresholds and pure gate helpers."""
from __future__ import annotations

from typing import Any, Dict, List


APPROVE_SCORE_THRESHOLD = 75
NO_PROGRESS_LIMIT = 5
LOOP_SAFETY_CAP = 50
PER_ROUND_TIMEOUT_S = 600.0
APPROVAL_TIMEOUT_S = 90.0

COST_TOKEN_CAP = 500_000
COST_TIME_CAP_S = 30 * 60
INFRA_FAILURE_LIMIT = 5
STAGNATION_WINDOW = 3
STAGNATION_TOLERANCE = 2


def loop_health_score(session: Any) -> int:
    """Return a 0-100 health score for an in-progress loop."""
    score = 100
    window = list(getattr(session, "score_window", None) or [])
    if len(window) >= 2:
        delta = window[-1] - window[0]
        if delta < -5:
            score -= 30
        elif delta < 0:
            score -= 10
        elif delta == 0:
            score -= 5

    infra_streak = int(getattr(session, "infra_failure_streak", 0) or 0)
    if infra_streak >= 3:
        score -= 30
    elif infra_streak >= 1:
        score -= 10

    no_progress = int(getattr(session, "no_progress_count", 0) or 0)
    if no_progress >= 3:
        score -= 30
    elif no_progress >= 1:
        score -= 10

    return max(0, min(100, score))


def issues_signature(issues: List[Dict]) -> str:
    """Build a stable issue identity for consecutive-round detection."""
    signature = []
    for issue in issues or []:
        signature.append((
            str(issue.get("file", "")).replace("\\", "/").lower(),
            issue.get("line", 0),
            str(issue.get("severity", "")).upper(),
            str(issue.get("category", "")).lower(),
        ))
    return repr(sorted(signature)) if signature else ""