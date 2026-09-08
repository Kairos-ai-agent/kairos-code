"""Loop gate configuration module.

Centralizes all threshold constants for the LoopReview engine.
Import from here instead of hardcoding values throughout the codebase.

Usage:
    from kairos.config.gates import APPROVE_SCORE_THRESHOLD, LOOP_SAFETY_CAP
"""
from __future__ import annotations

from typing import Any, Dict, List

# Import settings to get dynamic values
from kairos.config.settings import settings

# ============================================================================
# Approval & Scoring Thresholds
# ============================================================================

#: Minimum score required to approve a round
APPROVE_SCORE_THRESHOLD: int = settings.loop_gates.approve_score_threshold

#: Confidence threshold for promoting issues to preferences
PROMOTE_CONFIDENCE_THRESHOLD: float = 0.7

# ============================================================================
# Progress Detection
# ============================================================================

#: How many consecutive identical issue signatures trigger "no progress" stop
NO_PROGRESS_LIMIT: int = settings.loop_gates.no_progress_limit

#: Window size for stagnation detection (consecutive rounds to check)
STAGNATION_WINDOW: int = settings.loop_gates.stagnation_window

#: Maximum score variation within stagnation window before considered "stuck"
STAGNATION_TOLERANCE: int = settings.loop_gates.stagnation_tolerance

# ============================================================================
# Hard Limits
# ============================================================================

#: Maximum number of rounds before forced termination (defensive)
LOOP_SAFETY_CAP: int = settings.loop_gates.safety_cap

#: Maximum safety cap for heavy tasks (adaptive caps respect this ceiling)
MAX_SAFETY_CAP: int = settings.loop_gates.max_safety_cap

#: Per-round timeout in seconds
PER_ROUND_TIMEOUT_S: float = settings.loop_gates.per_round_timeout_s

#: Plan approval timeout in seconds
APPROVAL_TIMEOUT_S: float = settings.loop_gates.approval_timeout_s

# ============================================================================
# Cost Caps
# ============================================================================

#: Maximum cumulative tokens per loop
COST_TOKEN_CAP: int = settings.loop_gates.cost_token_cap

#: Maximum cumulative wall-clock time per loop (seconds)
COST_TIME_CAP_S: int = settings.loop_gates.cost_time_cap_s

#: Maximum token cap for heavy tasks (adaptive)
MAX_TOKEN_CAP: int = settings.loop_gates.max_token_cap

# ============================================================================
# Failure Detection
# ============================================================================

#: Consecutive infra failures (parse/tool-limit) before stopping
INFRA_FAILURE_LIMIT: int = settings.loop_gates.infra_failure_limit

# ============================================================================
# Adaptive Caps Configuration
# ============================================================================

#: Character count threshold for "heavy" requirements
HEAVY_REQUIREMENT_CHARS: int = settings.loop_gates.heavy_requirement_chars

#: Number of plan items that qualifies as "heavy"
HEAVY_PLAN_ITEMS: int = settings.loop_gates.heavy_plan_items

#: Keywords indicating architecture-scale work
_HEAVY_KEYWORDS = (
    "architecture", "refactor", "migrate", "rewrite",
    "redesign", "multi-file",
    "架构", "重构", "迁移", "重写", "多文件",
)

# ============================================================================
# Coder Configuration
# ============================================================================

#: Maximum tool turns per Coder task
CODER_MAX_TOOL_TURNS: int = settings.coder.max_tool_turns

#: Maximum chat turns for direct Coder conversation
CODER_MAX_CHAT_TURNS: int = settings.coder.max_chat_turns

#: Token budget for Coder memory
CODER_MAX_TOKENS: int = settings.coder.max_tokens

#: Number of recent messages to always keep in memory
CODER_KEEP_RECENT: int = settings.coder.keep_recent

#: Summarize memory every N turns
CODER_SUMMARIZE_EVERY_N: int = settings.coder.summarize_every_n

#: Starting temperature for Coder (high = creative exploration)
CODER_TEMPERATURE_START: float = settings.coder.temperature_start

#: Ending temperature for Coder (low = conservative polish)
CODER_TEMPERATURE_END: float = settings.coder.temperature_end

#: Rounds over which temperature decays from start to end
CODER_TEMPERATURE_DECAY_ROUNDS: int = settings.coder.temperature_decay_rounds

# ============================================================================
# Reviewer Configuration
# ============================================================================

#: Maximum tool turns per Reviewer task (tighter than Coder)
REVIEWER_MAX_TOOL_TURNS: int = settings.reviewer.max_tool_turns

#: Maximum chat turns for direct Reviewer conversation
REVIEWER_MAX_CHAT_TURNS: int = settings.reviewer.max_chat_turns

# ============================================================================
# Plan Mode Configuration
# ============================================================================

#: Character limit below which plans may be auto-approved
PLAN_AUTO_APPROVE_CHARS: int = settings.plan_mode.auto_approve_char_limit

#: Whether to require multi-line input to bypass auto-approve
PLAN_REQUIRE_MULTI_LINE: bool = settings.plan_mode.require_multi_line_for_auto

#: Disable all auto-approval (require manual approval for everything)
PLAN_DISABLE_AUTO_APPROVE: bool = settings.plan_mode.disable_auto_approve

# ============================================================================
# Historical Context
# ============================================================================

#: Number of previous rounds to load for cross-loop memory
MAX_HISTORY_ROUNDS: int = 5

# ============================================================================
# Helper Functions
# ============================================================================

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


def should_auto_approve_plan(requirement: str) -> bool:
    """Check if a plan should be auto-approved based on settings.

    Respects the PLAN_DISABLE_AUTO_APPROVE setting - when True,
    no plans are auto-approved regardless of size.
    """
    if PLAN_DISABLE_AUTO_APPROVE:
        return False
    
    if not requirement:
        return True
    
    text = requirement.strip()
    
    # Short requirements that are also single-line auto-approve
    if len(text) <= PLAN_AUTO_APPROVE_CHARS and (
        not PLAN_REQUIRE_MULTI_LINE or "\n\n" not in text
    ):
        return True
    
    # Check for heavy signals that should NOT auto-approve
    lowered = text.lower()
    heavy_signals = (
        "architecture", "refactor", "migrate", "rewrite",
        "redesign", "multi-file",
    )
    if any(sig in lowered for sig in heavy_signals):
        return False
    
    # Count file references as a proxy for complexity
    file_path_count = sum(
        1 for line in text.splitlines() if "`" in line and "." in line
    )
    if file_path_count >= 3:
        return False
    
    return len(text) <= 600
