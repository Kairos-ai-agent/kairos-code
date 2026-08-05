"""LoopReview mode: Coder + Reviewer, plus opt-in specialist reviewers.

The default LoopReview deployment uses just two roles: a universal Coder
and a strict Reviewer. For projects where security / perf / design /
testing is a first-class concern, enable one or more specialist
Reviewers via the orchestrator config (see run_review_with_specialists).
"""
from kairos.agents.roles.coder import Coder
from kairos.agents.roles.reviewer import Reviewer
from kairos.agents.roles.security import SecurityReviewer
from kairos.agents.roles.perf import PerfReviewer
from kairos.agents.roles.design import DesignReviewer
from kairos.agents.roles.test import TestReviewer

__all__ = [
    "Coder",
    "Reviewer",
    "SecurityReviewer",
    "PerfReviewer",
    "DesignReviewer",
    "TestReviewer",
]


# Default weights when multiple reviewers run on the same round.
# Main Reviewer gets the bulk; specialists get less but matter when
# their domain flags issues.
DEFAULT_SPECIALIST_WEIGHTS = {
    "reviewer": 0.55,
    "security_reviewer": 0.20,
    "perf_reviewer": 0.10,
    "design_reviewer": 0.10,
    "test_reviewer": 0.05,
}