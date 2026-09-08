"""Typed message structures for loop events.

Provides strong typing for the MessageBus events used in the LoopReview engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class LoopEventType(str, Enum):
    """Types of events emitted during loop execution."""
    # Loop lifecycle
    STARTED = "loop.started"
    STOPPED = "loop.stopped"
    ERROR = "loop.error"
    
    # Round events
    CODER_STARTED = "loop.coder_started"
    REVIEWER_STARTED = "loop.reviewer_started"
    ROUND_COMPLETED = "loop.round_completed"
    
    # Plan mode events
    PLAN_STARTED = "loop.plan_started"
    PLAN_APPROVED = "loop.approved"
    PLAN_REJECTED = "loop.rejected"
    PLAN_TIMEOUT = "loop.plan_timeout"
    PLAN_AUTO_APPROVED = "loop.plan_auto_approved"
    
    # Gate events
    APPROVED = "loop.completed"
    COST_CAP = "loop.cost_cap"
    TIME_CAP = "loop.time_cap"
    INFRA_STREAK = "loop.infra_streak"
    NO_PROGRESS = "loop.no_progress"
    STAGNATION = "loop.stagnation"
    SAFETY_CAP = "loop.safety_cap"
    
    # Best-of-N events
    BEST_OF_N_PICK = "loop.best_of_n_pick"
    
    # Memory events
    HISTORY_LOADED = "loop.history_loaded"
    REFLECTION_RECORDED = "reflection.recorded"
    
    # Precheck events
    PRECHECK_FAILED = "loop.precheck_failed"
    PRECHECK_FIXABLE = "loop.precheck_fixable"
    
    # Regression events
    REGRESSION_ROLLBACK = "loop.regression_rollback"
    
    # Hook events
    SESSION_START = "hook.session_start"
    SESSION_END = "hook.session_end"


@dataclass
class LoopMetadata:
    """Common metadata for loop events."""
    project_id: str
    session_id: str
    round: int = 0
    timestamp: float = field(default_factory=lambda: __import__('time').time())


@dataclass
class RoundResult:
    """Result of a single loop round."""
    round_no: int
    score: int
    approved: bool
    issues: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    reviewer_text: str = ""
    coder_text: str = ""
    gate_triggered: Optional[str] = None


@dataclass
class GateResult:
    """Result of gate evaluation."""
    gate_name: str
    reason: str
    round_no: int
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PlanDecision:
    """Plan approval/rejection decision."""
    decision: str  # "approve", "reject", or "pending"
    text: str = ""
    round_no: int = 0


@dataclass 
class ReflectionResult:
    """Result of post-loop reflection."""
    project_id: str
    session_id: str
    outcome: str
    rounds: int
    final_score: float
    notes: str = ""
    timestamp: datetime = field(default_factory=datetime.now)


# Type aliases for common patterns
LoopEvent = Dict[str, Any]  # Message bus event format
ProjectState = Dict[str, Any]  # Project status dict
ReviewVerdict = Dict[str, Any]  # Reviewer verdict dict
