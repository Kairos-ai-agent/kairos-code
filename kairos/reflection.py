"""Coder self-reflection.

When a loop ends (any outcome: approved, cost_cap, no_progress, ...),
the Coder is given one final pass over its own work. It produces a
structured reflection with three sections:

  - what_went_well   (1-3 bullets, things to keep doing)
  - what_to_improve  (1-3 bullets, things to do differently next time)
  - next_actions     (0-3 bullets, concrete follow-ups to consider)

The reflection is then stored in the project's persistent memory so
the next loop in the same project starts with that context. This
gives Kairos a minimal but real form of "learning from experience"
without needing a fine-tune or a separate vector store.

The reflection step is best-effort: if the LLM call fails, the loop
outcome is still recorded and the project keeps working.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Reflection:
    """One Coder self-reflection, stored in project memory."""
    what_went_well: List[str] = field(default_factory=list)
    what_to_improve: List[str] = field(default_factory=list)
    next_actions: List[str] = field(default_factory=list)
    # The round/range this reflection covers.
    rounds: int = 0
    outcome: str = ""  # approved / cost_cap / no_progress / stagnation / safety_cap
    final_score: float = 0.0
    # Free-form Coder text. Useful when the LLM emits more than the
    # structured sections; we keep it for UI / debugging.
    raw: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Reflection":
        return cls(
            what_went_well=list(d.get("what_went_well") or []),
            what_to_improve=list(d.get("what_to_improve") or []),
            next_actions=list(d.get("next_actions") or []),
            rounds=int(d.get("rounds", 0)),
            outcome=str(d.get("outcome", "")),
            final_score=float(d.get("final_score", 0.0)),
            raw=str(d.get("raw", "")),
        )


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_REFLECT_PROMPT = """You are the Coder agent that just finished a coding loop.
Below is your full session history. Reflect on it honestly and concisely.

Output exactly three sections, in this order, no extra commentary:

WHAT_WENT_WELL:
- <bullet 1>
- <bullet 2>
- <bullet 3>

WHAT_TO_IMPROVE:
- <bullet 1>
- <bullet 2>
- <bullet 3>

NEXT_ACTIONS:
- <bullet 1>
- <bullet 2>

Keep each bullet under 20 words. Skip empty sections by writing "-" only.

--- REQUIREMENT ---
{requirement}

--- SESSION OUTCOME ---
{outcome_summary}

--- RECENT ROUND DIGESTS ---
{round_digests}
"""


def build_reflect_prompt(
    requirement: str,
    outcome: str,
    rounds: int,
    round_digests: List[Dict[str, Any]],
) -> str:
    """Assemble the reflection prompt from a session's outcome + digests."""
    summary = f"outcome={outcome} rounds={rounds}"
    if round_digests:
        rendered = []
        for d in round_digests[-5:]:  # last 5 rounds only
            rendered.append(
                f"[round {d.get('round', '?')}] "
                f"score={d.get('score', '?')} "
                f"approve={d.get('approve', '?')} "
                f"notes={(d.get('notes') or '')[:200]}"
            )
        digests_str = "\n".join(rendered)
    else:
        digests_str = "(no round digests recorded)"
    return _REFLECT_PROMPT.format(
        requirement=requirement[:2000],
        outcome_summary=summary,
        round_digests=digests_str,
    )


# ---------------------------------------------------------------------------
# Parsing the LLM response
# ---------------------------------------------------------------------------


_SECTION_HEADERS = {
    "what_went_well": re.compile(r"^\s*WHAT_WENT_WELL\s*:?\s*$", re.IGNORECASE | re.MULTILINE),
    "what_to_improve": re.compile(r"^\s*WHAT_TO_IMPROVE\s*:?\s*$", re.IGNORECASE | re.MULTILINE),
    "next_actions": re.compile(r"^\s*NEXT_ACTIONS\s*:?\s*$", re.IGNORECASE | re.MULTILINE),
}


def _extract_bullets(block: str) -> List[str]:
    """Pull `- foo` style bullets out of a text block."""
    out: List[str] = []
    for line in block.splitlines():
        s = line.strip()
        if not s:
            continue
        if s in ("-", "—"):  # explicit "skip" placeholder
            continue
        # Strip leading bullet markers (-, *, •, –)
        for marker in ("- ", "* ", "• ", "– "):
            if s.startswith(marker):
                s = s[len(marker):].strip()
                break
        # Skip "section header" leftovers
        if s.upper().rstrip(":") in {"WHAT_WENT_WELL", "WHAT_TO_IMPROVE", "NEXT_ACTIONS"}:
            continue
        out.append(s)
    return out


def parse_reflection(text: str) -> Reflection:
    """Parse a Coder reflection response into a structured Reflection.

    The parser is forgiving: if a section is missing it stays empty,
    if headers are absent the whole text goes into ``raw``, and any
    malformed bullets are dropped rather than raising.
    """
    if not text or not text.strip():
        return Reflection(raw="")

    # Find header offsets.
    positions: Dict[str, int] = {}
    for key, pat in _SECTION_HEADERS.items():
        m = pat.search(text)
        if m:
            positions[key] = m.end()
    if not positions:
        return Reflection(raw=text)

    # Slice each section by header positions.
    sorted_keys = sorted(positions.keys(), key=lambda k: positions[k])
    sections: Dict[str, str] = {}
    for i, key in enumerate(sorted_keys):
        start = positions[key]
        end = positions[sorted_keys[i + 1]] if i + 1 < len(sorted_keys) else len(text)
        sections[key] = text[start:end]

    refl = Reflection(
        what_went_well=_extract_bullets(sections.get("what_went_well", "")),
        what_to_improve=_extract_bullets(sections.get("what_to_improve", "")),
        next_actions=_extract_bullets(sections.get("next_actions", "")),
        raw=text,
    )
    return refl


# ---------------------------------------------------------------------------
# Memory adapter
# ---------------------------------------------------------------------------


def _memory_layer():
    """Late-import to avoid a hard dep on the memory layer in unit tests."""
    try:
        from kairos.learning import auto_memory  # type: ignore
        return auto_memory
    except Exception:  # pragma: no cover
        return None


def save_reflection_to_memory(project_id: str, refl: Reflection) -> bool:
    """Persist a reflection into the project memory.

    Returns True iff a write happened. If the memory layer isn't
    available, this is a no-op (reflections are still emitted in logs).
    """
    am = _memory_layer()
    if am is None:
        logger.info("reflection: memory layer unavailable, not saved (project=%s)", project_id)
        return False
    payload = refl.to_dict()
    try:
        # The exact API depends on the project; the most common one is
        # ``auto_memory.record(project_id, kind, body)``.
        if hasattr(am, "record"):
            am.record(project_id=project_id, kind="reflection", body=payload)
            return True
        if hasattr(am, "add"):
            am.add(project_id=project_id, kind="reflection", body=payload)
            return True
    except Exception:
        logger.exception("reflection: failed to save to memory (project=%s)", project_id)
        return False
    return False


# ---------------------------------------------------------------------------
# High-level driver
# ---------------------------------------------------------------------------


async def run_reflection(
    *,
    project_id: str,
    coder_agent: Any,
    requirement: str,
    outcome: str,
    rounds: int,
    round_digests: List[Dict[str, Any]],
    final_score: float = 0.0,
) -> Reflection:
    """Run the Coder's self-reflection and persist it.

    ``coder_agent`` must expose a ``generate(prompt)`` (or async
    ``agenerate``) method that returns a string. The agent is wrapped
    in a metrics timer automatically.

    Returns the parsed Reflection (which is also saved to memory).
    Never raises — all errors are logged and an empty Reflection is
    returned so callers can always rely on a result.
    """
    from kairos.metrics import agent_invocation_timer, record_loop_round

    prompt = build_reflect_prompt(requirement, outcome, rounds, round_digests)

    raw = ""
    try:
        with agent_invocation_timer("coder", "reflect") as ctx:
            if hasattr(coder_agent, "agenerate"):
                raw = await coder_agent.agenerate(prompt)
            elif hasattr(coder_agent, "generate"):
                gen = coder_agent.generate(prompt)
                if hasattr(gen, "__await__"):
                    raw = await gen
                else:
                    raw = gen
            else:
                raise RuntimeError("coder_agent lacks generate()/agenerate()")
            ctx["status"] = "ok"
    except Exception:
        logger.exception("reflection: LLM call failed (project=%s)", project_id)
        return Reflection(outcome=outcome, rounds=rounds, final_score=final_score)

    refl = parse_reflection(raw or "")
    refl.rounds = rounds
    refl.outcome = outcome
    refl.final_score = final_score
    save_reflection_to_memory(project_id, refl)
    record_loop_round(f"reflected:{outcome}")
    return refl
