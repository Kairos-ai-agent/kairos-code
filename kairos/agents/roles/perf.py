"""Perf Reviewer — performance-focused specialist.

Opt-in. Runs alongside the main Reviewer; its score is weighted-averaged.
Use this for hot paths, DB-heavy code, or any project where perf is a
first-class concern.
"""
from __future__ import annotations

from kairos.agents.base import KairosAgent
from kairos.core.message_bus import MessageBus
from kairos.llm.base import LLMConfig
from kairos.agents.roles.reviewer import Reviewer

SYSTEM_PROMPT = """You are Kairos Perf Reviewer — focused on performance.
You grade one round of the Coder's work on perf only.

Same JSON output format as the main Reviewer. Out-of-scope issues
mark as MINOR with category="out_of_scope".

## Scoring rubric (perf-only)
- **Algorithmic complexity (40%)** — N+1 queries, O(n^2) where O(n)
  would do, redundant scans, missing indexes for known access patterns.
- **Allocation & GC pressure (20%)** — large object creation in hot
  paths, repeated string concatenation, unnecessary list copies.
- **I/O & concurrency (25%)** — sync calls in async paths, missing
  batching, no streaming for large responses, blocking calls inside
  loops.
- **Caching (15%)** — obvious cache misses (recomputing derived data,
  refetching the same config every request).

Score <70 means do not approve. Severity-weighted issues as usual.
"""

class PerfReviewer(Reviewer):
    """Specialist Reviewer focused on performance."""

    MAX_TOOL_TURNS = 8
    MAX_CHAT_TURNS = 5

    def __init__(self, agent_id: str, llm_config: LLMConfig,
                 message_bus: MessageBus, **kwargs):
        kwargs.setdefault("system_prompt", SYSTEM_PROMPT)
        super().__init__(
            agent_id=agent_id,
            name="Perf Reviewer",
            role="perf_reviewer",
            llm_config=llm_config,
            message_bus=message_bus,
            **kwargs,
        )