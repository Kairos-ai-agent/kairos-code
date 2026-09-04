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

# --- adaptive caps ---------------------------------------------------------

# A requirement counts as "heavy" (gets doubled caps) when it is longer
# than this, carries architecture-scale keywords, or the plan has at
# least this many todos.
HEAVY_REQUIREMENT_CHARS = 2000
HEAVY_PLAN_ITEMS = 8

_HEAVY_KEYWORDS = (
    "architecture", "refactor", "migrate", "rewrite",
    "redesign", "multi-file",
    "架构", "重构", "迁移", "重写", "多文件",
)

# Never let adaptive caps grow unbounded.
MAX_SAFETY_CAP = 200
MAX_TOKEN_CAP = 2_000_000


def dynamic_caps(requirement: str = "", plan_items: int = 0) -> Dict[str, int]:
    """Compute per-task loop caps instead of using the constants raw.

    Small/trivial tasks keep the conservative defaults; heavy tasks
    (long requirements, architecture-scale keywords, or big plans)
    get 2x headroom so a legitimately large job isn't killed by the
    same ceiling that guards a one-liner. Results are clamped so a
    pathological requirement can never produce an unbounded loop.
    """
    text = (requirement or "")
    lowered = text.lower()
    heavy = (
        len(text) > HEAVY_REQUIREMENT_CHARS
        or plan_items >= HEAVY_PLAN_ITEMS
        or any(k in lowered for k in _HEAVY_KEYWORDS)
    )
    mult = 2 if heavy else 1
    return {
        "safety_cap": min(LOOP_SAFETY_CAP * mult, MAX_SAFETY_CAP),
        "token_cap": min(COST_TOKEN_CAP * mult, MAX_TOKEN_CAP),
        "time_cap_s": min(int(COST_TIME_CAP_S * mult), 2 * 24 * 3600),
    }


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
