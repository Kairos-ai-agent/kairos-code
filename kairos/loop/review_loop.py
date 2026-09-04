"""Backward-compatible re-exports for kairos.loop.review_loop.

The 1,600+ line monolith has been split into focused submodules. New
imports should target the specific submodule (gates / cross_loop /
plan_mode / prompts / reviewers); this module keeps the original
`from kairos.loop import review_loop as rl` style alive for the
existing tests and any external code.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# R38.6.4 packaging: was 'from kairos.agents.base import AgentTask, KairosAgent' — replaced with __getattr__ lazy load
from kairos.core.message_bus import Message, MessageBus

from kairos.loop.gates import (
    APPROVAL_TIMEOUT_S,
    APPROVE_SCORE_THRESHOLD,
    COST_TIME_CAP_S,
    COST_TOKEN_CAP,
    INFRA_FAILURE_LIMIT,
    LOOP_SAFETY_CAP,
    NO_PROGRESS_LIMIT,
    PER_ROUND_TIMEOUT_S,
    STAGNATION_TOLERANCE,
    STAGNATION_WINDOW,
    issues_signature,
    loop_health_score,
)
from kairos.loop.cross_loop import (
    CODER_TEMPERATURE_DECAY_ROUNDS,
    CODER_TEMPERATURE_END,
    CODER_TEMPERATURE_START,
    MAX_HISTORY_ROUNDS,
    coder_temperature_for_round,
    detect_cross_loop_patterns,
    load_history_digest,
)
from kairos.loop.plan_mode import is_plan_dirty, sanitize_plan_text
from kairos.loop.prompts import build_next_prompt, build_reviewer_description
from kairos.loop.reviewers import (
    parse_review_verdict,
    run_reviewer_round,
    run_reviewer_round_for,
    run_reviewers_parallel,
)

logger = logging.getLogger(__name__)

_loop_health_score = loop_health_score
_issues_signature = issues_signature
_load_history_digest = load_history_digest
_detect_cross_loop_patterns = detect_cross_loop_patterns
_coder_temperature_for_round = coder_temperature_for_round
_is_plan_dirty = is_plan_dirty
_sanitize_plan_text = sanitize_plan_text
_build_next_prompt = build_next_prompt
_parse_review_verdict = parse_review_verdict
_run_reviewer_round_for = run_reviewer_round_for
_run_reviewer_round = run_reviewer_round
_run_reviewers_parallel = run_reviewers_parallel

@dataclass
class LoopSession:
    """Holds state for one ongoing loop."""
    project: Any
    message_bus: MessageBus
    coder: KairosAgent
    reviewer: KairosAgent
    persistence: Any = None

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    round: int = 0
    history: List[Dict[str, Any]] = field(default_factory=list)
    user_stopped: bool = False
    started_at: float = field(default_factory=time.time)
    last_score: int = 0
    last_approve: bool = False
    last_issues_signature: Optional[str] = None
    no_progress_count: int = 0
    plan_pending: bool = False
    plan_text: str = ""
    plan_decision: Optional[str] = None
    plan_event: Optional[asyncio.Event] = None
    original_requirement: str = ""
    plan_completed: bool = False
    infra_failure_streak: int = 0
    score_window: List[int] = field(default_factory=list)
    total_tokens_used: int = 0
    round_tokens: int = 0
    specialist_reviewers: List[Any] = field(default_factory=list)
    best_of_n: int = 1
    _round_file_snapshot: Dict[str, bytes] = field(default_factory=dict)
    ask_pending: bool = False
    ask_question: str = ""
    ask_context: str = ""
    ask_answer: str = ""
    ask_event: Optional[asyncio.Event] = None
    review_focus: List[str] = field(default_factory=list)
    # Round 11: structured plan tracking (TodoWrite-style). The Coder
    # agent emits a `write_todos` tool call; we apply the diff to this
    # Plan and surface it on the bus for the UI. See kairos.loop.plan.
    plan_todos: Any = None  # kairos.loop.plan.Plan instance (lazy)

    def answer_ask(self, answer: str):
        self.ask_answer = answer
        self.ask_pending = False
        if self.ask_event and not self.ask_event.is_set():
            self.ask_event.set()

    def request_stop(self):
        self.user_stopped = True

    def approve_plan(self, plan_text: str = ""):
        if plan_text:
            self.plan_text = plan_text
        self.plan_decision = "approve"
        self.plan_pending = False
        if self.plan_event and not self.plan_event.is_set():
            self.plan_event.set()

    def reject_plan(self):
        self.plan_decision = "reject"
        self.plan_pending = False
        if self.plan_event and not self.plan_event.is_set():
            self.plan_event.set()

# Re-export the loop runner helpers. Each underscore-prefixed name is the
# canonical location; we mirror it here so tests can keep using
# `rl._foo` style imports.
from kairos.loop.loop_runner import (
    _auto_checkpoint,
    _best_of_n_attempts,
    _build_round_summary,
    _check_gates,
    _is_trivial_requirement,
    _maybe_auto_approve_plan,
    _maybe_rollback_on_regression,
    _run_coder_round,
    _run_precheck,
    _update_progress,
    _wait_for_plan_decision,
    run_loop,
    should_auto_approve_plan,
)

_auto_checkpoint = _auto_checkpoint
_best_of_n_attempts = _best_of_n_attempts
_build_round_summary = _build_round_summary
_check_gates = _check_gates
_is_trivial_requirement = _is_trivial_requirement
_maybe_auto_approve_plan = _maybe_auto_approve_plan
_maybe_rollback_on_regression = _maybe_rollback_on_regression
_run_coder_round = _run_coder_round
_run_precheck = _run_precheck
_update_progress = _update_progress
_wait_for_plan_decision = _wait_for_plan_decision
should_auto_approve_plan = should_auto_approve_plan
run_loop = run_loop


# R38.6.4 packaging: lazy import so PyInstaller onefile
# can resolve this module (eager top-level imports trip
# the bootloader when --collect-submodules misses the
# symbol).
def __getattr__(name):
    if name in ['AgentTask', 'KairosAgent']:
        import importlib as _il, kairos.agents.base as _m
        return getattr(_m, name)
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
