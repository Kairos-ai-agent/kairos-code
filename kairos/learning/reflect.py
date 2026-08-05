"""Self-learning subsystem: agent reflects on its own history.

This module is the "idle time" learning loop. After a project loop
finishes, the orchestrator fires `maybe_run_reflection` as a
fire-and-forget background task. We do four things, in order of
expense:

  1. Cheap heuristics (no LLM): re-derive the cross-loop advisory,
     promote repeated failures to preferences, flag ambiguous
     signatures. (Lives in `kairos.memory.growth.consolidate_project`.)

  2. Free in-process: scan the round history for patterns we can
     extract with simple code — categories that recurred, files that
     were edited, fixes that consistently helped. We write
     project_notes / project_skills without ever calling an LLM.

  3. One LLM call (the "self-reflection"): we hand the Coder the
     last N round digests and ask it to extract 1-3 short, reusable
     project notes and 1-3 skill playbooks. Wrapped in a 60s timeout
     and a hard token budget so a hung LLM cannot block the server.

The reflection is deliberately best-effort: failures here must NEVER
break the loop or surface as a user-facing error. They are logged
and ignored.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


REFLECTION_TIMEOUT_S = 60.0
REFLECTION_MAX_TOKENS = 1500
MIN_REFLECTION_INTERVAL_S = 30 * 60  # 30 minutes
REFLECTION_ROUND_WINDOW = 8


async def maybe_run_reflection(
    persistence: Any,
    project_id: str,
    rounds: List[dict],
) -> Dict[str, int]:
    """Run the (mostly-no-LLM) self-reflection pass."""
    result = {"notes": 0, "skills": 0, "insights": 0, "skipped": 0}
    if not persistence or not project_id or not rounds:
        result["skipped"] += 1
        return result

    try:
        last_reflection = getattr(persistence, "_last_reflection_at", {})
        if not isinstance(last_reflection, dict):
            last_reflection = {}
        last_at = last_reflection.get(project_id, 0)
        if time.time() - last_at < MIN_REFLECTION_INTERVAL_S:
            result["skipped"] += 1
            return result
        last_reflection[project_id] = time.time()
        try:
            persistence._last_reflection_at = last_reflection
        except Exception:
            pass

        try:
            _extract_patterns_to_notes(persistence, project_id, rounds)
            result["notes"] += 1
        except Exception:
            logger.debug("reflection: pattern extraction failed", exc_info=True)

        try:
            await asyncio.wait_for(
                _llm_reflect_once(persistence, project_id, rounds),
                timeout=REFLECTION_TIMEOUT_S,
            )
            result["skills"] += 1
        except asyncio.TimeoutError:
            logger.info("reflection: LLM call timed out after %.0fs", REFLECTION_TIMEOUT_S)
        except Exception:
            logger.debug("reflection: LLM call failed", exc_info=True)

    except Exception:
        logger.debug("maybe_run_reflection: outer failure", exc_info=True)

    return result


def _extract_patterns_to_notes(
    persistence: Any, project_id: str, rounds: List[dict],
) -> None:
    """Cheap pass: pull a few pattern-level notes from the round history."""
    cat_counts: Counter = Counter()
    cat_to_first_issue: Dict[str, dict] = {}
    for r in rounds:
        review = _safe_json(r.get("review_json"))
        for issue in (review or {}).get("issues") or []:
            cat = str(issue.get("category") or "").strip().lower()
            if not cat:
                continue
            cat_counts[cat] += 1
            if cat not in cat_to_first_issue:
                cat_to_first_issue[cat] = issue
    for cat, count in cat_counts.items():
        if count >= 3:
            issue = cat_to_first_issue[cat]
            title = f"Recurring {cat} pattern ({count}x)"
            body = (
                f"{cat.capitalize()} issues appeared {count} times. "
                f"Example: {(issue.get('description') or '')[:160]}"
            )
            try:
                persistence.add_project_note(
                    project_id, "pitfall", title, body, source="consolidator",
                )
            except Exception:
                pass


async def _llm_reflect_once(
    persistence: Any, project_id: str, rounds: List[dict],
) -> None:
    """One cheap LLM call to extract project_notes + project_skills."""
    provider = _get_reflection_provider()
    if provider is None:
        logger.debug("reflection: no provider configured, skipping LLM pass")
        return

    digest = _build_reflection_digest(rounds)
    prompt = _build_reflection_prompt(digest)
    try:
        response = await provider.complete(
            [{"role": "user", "content": prompt}],
            max_tokens=REFLECTION_MAX_TOKENS,
            temperature=0.2,
        )
        text = (response.content if hasattr(response, "content") else str(response)).strip()
    except Exception:
        logger.debug("reflection: provider.complete failed", exc_info=True)
        return

    notes_written, skills_written = _parse_reflection_output(
        text, persistence, project_id,
    )
    logger.info(
        "reflection: wrote %d note(s) + %d skill(s) for %s",
        notes_written, skills_written, project_id,
    )


def _get_reflection_provider() -> Any:
    """Pick the provider for self-reflection."""
    try:
        from kairos.llm.model_router import ModelRouter
        router = ModelRouter()
        for role in ("coder", "reviewer", "default"):
            try:
                provider = router.get_provider_for_role(role)
                if provider is not None:
                    return provider
            except Exception:
                continue
    except Exception:
        logger.debug("reflection: provider lookup failed", exc_info=True)
    return None


def _build_reflection_digest(rounds: List[dict]) -> str:
    parts = []
    window = rounds[-REFLECTION_ROUND_WINDOW:] if len(rounds) > REFLECTION_ROUND_WINDOW else rounds
    for r in window:
        rnd = r.get("round", "?")
        verdict = "approved" if r.get("approve") else "rejected"
        score = r.get("score") or 0
        summary = (r.get("review_summary") or "")[:200]
        coder = (r.get("coder_summary") or "")[:200]
        parts.append(
            f"R{rnd} {verdict} score={score}\n  summary: {summary}\n  coder: {coder}"
        )
    return "\n\n".join(parts)


_REFLECTION_PROMPT = """You are reviewing a completed coding loop and extracting
REUSABLE lessons. Be terse. Each lesson must be useful to a future
Coder run on this same project.

Output MUST be valid JSON in this shape:
{{
  "notes": [
    {{"kind": "convention|pitfall|architecture|fact", "title": "...", "body": "..."}}
  ],
  "skills": [
    {{"name": "...", "triggers": ["kw1","kw2"], "body": "..."}}
  ]
}}

Rules:
- 1-3 notes total. Each note <= 200 chars body.
- 1-3 skills total. Triggers are concrete keywords.
- Skip generic advice ("always test", "use git").
- Only lessons that clearly recur or are non-obvious from the code.
- If nothing worth saving, return {{"notes": [], "skills": []}}.

Loop digest:
{digest}
"""


def _build_reflection_prompt(digest: str) -> str:
    return _REFLECTION_PROMPT.format(digest=digest[:6000])


def _parse_reflection_output(
    text: str, persistence: Any, project_id: str,
) -> tuple:
    notes_written = 0
    skills_written = 0
    parsed = _extract_json(text)
    if not isinstance(parsed, dict):
        return notes_written, skills_written

    for note in parsed.get("notes") or []:
        if not isinstance(note, dict):
            continue
        title = (note.get("title") or "").strip()
        body = (note.get("body") or "").strip()
        kind = (note.get("kind") or "fact").strip().lower()
        if not title or not body:
            continue
        try:
            persistence.add_project_note(
                project_id, kind, title[:120], body[:500], source="consolidator",
            )
            notes_written += 1
        except Exception:
            logger.debug("reflection: write note failed", exc_info=True)

    for skill in parsed.get("skills") or []:
        if not isinstance(skill, dict):
            continue
        name = (skill.get("name") or "").strip()
        body = (skill.get("body") or "").strip()
        triggers = skill.get("triggers") or []
        if not name or not body:
            continue
        if isinstance(triggers, str):
            triggers = [t.strip() for t in triggers.split(",") if t.strip()]
        if not isinstance(triggers, list):
            triggers = []
        try:
            persistence.add_skill(
                project_id, name[:80], triggers, body[:500],
                confidence=0.5, source="consolidator",
            )
            skills_written += 1
        except Exception:
            logger.debug("reflection: write skill failed", exc_info=True)

    return notes_written, skills_written


def _extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        first_nl = s.find("\n")
        if first_nl > 0:
            s = s[first_nl + 1 :]
        if s.endswith("```"):
            s = s[:-3]
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass
    depth = 0
    start = -1
    for i, ch in enumerate(s):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                chunk = s[start : i + 1]
                try:
                    obj = json.loads(chunk)
                    if isinstance(obj, dict):
                        return obj
                except (json.JSONDecodeError, ValueError):
                    start = -1
                    depth = 0
    return None


def _safe_json(raw: Any) -> Optional[dict]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return None
    return None
