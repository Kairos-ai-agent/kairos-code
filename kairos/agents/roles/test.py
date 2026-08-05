"""Test Reviewer — test coverage & quality focused.

Opt-in. Runs alongside the main Reviewer. Use this when you want
every Coder change to come with comprehensive tests.
"""
from __future__ import annotations

from kairos.core.message_bus import MessageBus
from kairos.llm.base import LLMConfig
from kairos.agents.roles.reviewer import Reviewer


SYSTEM_PROMPT = """You are Kairos Test Reviewer — focused on test coverage
and test quality. You grade one round of the Coder's work on tests
only.

Same JSON output format as the main Reviewer. Out-of-scope issues
mark as MINOR with category="out_of_scope".

## Scoring rubric (test-only)
- **Coverage of new/changed code (40%)** — every new branch, every
  error path, every external call should have a test.
- **Edge cases (25%)** — empty inputs, None, very large values,
  concurrent access, network failures.
- **Test quality (20%)** — assertions are specific (not `assertTrue(x)`),
  tests are independent (no shared mutable state), fixtures are minimal.
- **Test maintainability (15%)** — names describe behavior, tests are
  fast, no flaky timing dependencies.

If the Coder didn't add tests for new code, that's a MAJOR issue with
fix_instruction="add unit tests covering [list of behaviors]".

Score <70 means do not approve. CRITICAL if a critical path has zero
tests.
"""


class TestReviewer(Reviewer):
    """Specialist Reviewer focused on test coverage & quality."""

    MAX_TOOL_TURNS = 8
    MAX_CHAT_TURNS = 5

    def __init__(self, agent_id: str, llm_config: LLMConfig,
                 message_bus: MessageBus, **kwargs):
        kwargs.setdefault("system_prompt", SYSTEM_PROMPT)
        super().__init__(
            agent_id=agent_id,
            name="Test Reviewer",
            role="test_reviewer",
            llm_config=llm_config,
            message_bus=message_bus,
            **kwargs,
        )