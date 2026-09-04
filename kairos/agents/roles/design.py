"""Design Reviewer — architecture & abstraction focused.

Opt-in. Runs alongside the main Reviewer. Use this when the project
has a complex domain model or where consistency with existing code
matters.
"""
from __future__ import annotations

from kairos.core.message_bus import MessageBus
from kairos.llm.base import LLMConfig
from kairos.agents.roles.reviewer import Reviewer  # restored direct import (R38.6.4 __getattr__ shim removed — class base expression needs name in globals)

SYSTEM_PROMPT = """You are Kairos Design Reviewer — focused on architecture
and abstraction quality. You grade one round of the Coder's work on
design only.

Same JSON output format as the main Reviewer. Out-of-scope issues
mark as MINOR with category="out_of_scope".

## Scoring rubric (design-only)
- **Fit with existing codebase (35%)** — does the new code match the
  patterns already used? (naming, module structure, error handling style)
- **Right-sized abstractions (30%)** — no premature generalization,
  no copy-paste of nearly-identical code, no leaky abstractions.
- **Cohesion & coupling (20%)** — modules don't import each other in
  circles, public surface is small, internal helpers are actually
  internal.
- **Naming & discoverability (15%)** — names say what things are,
  not how they're implemented. New code is findable via grep.

Score <70 means do not approve. Severity-weighted issues as usual.
"""

class DesignReviewer(Reviewer):
    """Specialist Reviewer focused on design & architecture."""

    MAX_TOOL_TURNS = 8
    MAX_CHAT_TURNS = 5

    def __init__(self, agent_id: str, llm_config: LLMConfig,
                 message_bus: MessageBus, **kwargs):
        kwargs.setdefault("system_prompt", SYSTEM_PROMPT)
        super().__init__(
            agent_id=agent_id,
            name="Design Reviewer",
            role="design_reviewer",
            llm_config=llm_config,
            message_bus=message_bus,
            **kwargs,
        )


# R38.6.4 packaging fallback removed: Reviewer is now imported directly above.
