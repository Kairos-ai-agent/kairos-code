"""All available agent roles for the Kairos LoopReview harness.

The default top-of-funnel setup is 1 Coder + 1 Reviewer. Specialists
are opt-in (per-project, from settings.json) and run alongside the
main Reviewer when active — their scores are weighted-averaged.

Total roster: 8 roles.
  - Coder                    — the executor
  - Reviewer                 — the strict gatekeeper (always on)
  - SecurityReviewer         — OWASP / auth / secrets
  - PerfReviewer             — latency / memory / concurrency
  - DesignReviewer           — architecture / UX / accessibility
  - TestReviewer             — coverage / edge cases / regressions
  - DocsReviewer             — docstrings / README / examples
  - RefactorReviewer         — duplication / dead code / complexity
"""
from __future__ import annotations

from kairos.agents.roles.coder import Coder
from kairos.agents.roles.reviewer import Reviewer
from kairos.agents.roles.security import SecurityReviewer
from kairos.agents.roles.perf import PerfReviewer
from kairos.agents.roles.design import DesignReviewer
from kairos.agents.roles.test import TestReviewer
from kairos.agents.roles.docs import DocsReviewer
from kairos.agents.roles.refactor import RefactorReviewer

__all__ = [
    "Coder",
    "Reviewer",
    "SecurityReviewer",
    "PerfReviewer",
    "DesignReviewer",
    "TestReviewer",
    "DocsReviewer",
    "RefactorReviewer",
]

# Default weights when multiple reviewers run on the same round.
# Main Reviewer gets the bulk; specialists get less but matter when
# they spot a domain-specific bug. Sum does not need to be 1.0 — the
# runner normalizes by total weight.
DEFAULT_SPECIALIST_WEIGHTS = {
    "reviewer": 0.55,
    "security_reviewer": 0.20,
    "perf_reviewer": 0.10,
    "design_reviewer": 0.10,
    "test_reviewer": 0.05,
    "docs_reviewer": 0.05,
    "refactor_reviewer": 0.05,
}