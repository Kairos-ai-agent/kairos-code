"""Reviewer — bug checker for the Coder's round.

R38.7: this used to be a four-dimension grader (correctness 40 / design 25 /
quality 20 / security 15, weighted score, mandatory test evidence, optional
ask-human, confidence calibration). The user's feedback: "reviewer agent
太复杂，改成简单的只检查代码是否存在bug即可". So the Reviewer now does one
thing — look for bugs — and reports a plain list.

Verdict shape the model must return:
    {"has_bugs": bool, "bugs": [{file, line, description, fix}], "summary": str}

The loop still needs an approve/score pair for its gates, so
``kairos.loop.reviewers.parse_review_verdict`` maps the simple shape onto the
internal verdict (no bugs → approve, score 100; each bug → reject, score
100-20n). Legacy rubric JSON is still accepted so old sessions/replays work.
"""

from __future__ import annotations

# R38.6.4 packaging: KairosAgent was moved to lazy __getattr__; with the
# syntax fixes in kairos/agents/base.py the direct import is safe again,
# and the direct import is required because class Reviewer(KairosAgent)
# below is evaluated at module load time (module-level __getattr__ cannot
# help a class-statement base expression).
from kairos.agents.base import KairosAgent
from kairos.core.message_bus import MessageBus
from kairos.llm.base import LLMConfig

SYSTEM_PROMPT = """You are the Kairos Reviewer. You have exactly ONE job: decide
whether the Coder's code has bugs.

## What counts as a bug
Anything that makes the code behave wrongly:
- a crash / unhandled exception on a normal path
- a wrong result: bad condition, off-by-one, wrong operator, wrong variable
- the code does not do what the requirement asked (missing case, wrong order)
- data loss, writing to the wrong place, a file/handle left open
- index / key / None access that can blow up on realistic input
- an infinite loop, or a branch that can never run but should
- shared state corrupted by concurrent access

## What is NOT a bug — do not report it
Style, naming, formatting, comments, architecture, abstractions, "could be
cleaner", performance unless it actually hangs, security best practices,
missing tests, missing docs, extra features. If the code runs correctly and
does what the requirement asked, that is a PASS — say so and stop.

## How to work
1. Read the requirement and the files this round changed (file_read, grep,
   git diff). You may run the project's tests if a quick run settles a doubt.
2. Stop as soon as you know whether there are bugs. Do not keep exploring,
   and do not "be thorough" about anything that is not a bug.

## Output — your FINAL message must be ONLY this JSON, nothing else
No markdown fences, no prose before or after:

{
  "has_bugs": true,
  "bugs": [
    {
      "file": "src/app.py",
      "line": 42,
      "description": "What is wrong, and when it breaks.",
      "fix": "The concrete change that fixes it."
    }
  ],
  "summary": "One line: how many bugs, or that there are none."
}

Rules:
- `has_bugs: false` and `bugs: []` when you found nothing. That is a normal,
  common answer — do not invent a bug to look useful.
- One entry per distinct bug. No severity levels, no categories, no score, no
  confidence number, no test-evidence report, no questions to the user.
- Not sure whether something is a bug? Re-read that code; if it is still
  unclear, run it. If it stays unclear, leave it out.
"""

class Reviewer(KairosAgent):
    """Read-mostly agent that grades Coder output each round."""

    # Tightened from 20 to 12: long turn budgets encourage the Reviewer
    # to explore instead of converge, and the orchestrator now has a
    # dedicated infra_failure_streak gate to catch "Reviewer ran out
    # of turns" cleanly. 12 still covers a typical read + diff + test
    # pass on a real codebase.
    MAX_TOOL_TURNS = 20
    MAX_CHAT_TURNS = 5

    def __init__(self, agent_id: str, llm_config: LLMConfig, message_bus: MessageBus,
                 **kwargs):
        kwargs.setdefault("system_prompt", SYSTEM_PROMPT)
        super().__init__(
            agent_id=agent_id,
            name="Reviewer",
            role="reviewer",
            llm_config=llm_config,
            message_bus=message_bus,
            **kwargs,
        )


# R38.6.4 packaging fallback removed: with the direct import above
# (kairos.agents.base.KairosAgent), this module no longer needs the
# lazy __getattr__ shim.
