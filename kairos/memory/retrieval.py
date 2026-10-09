"""Memory retrieval — assembles the prompt-side memory block.

This module is the single entry point for "what does the Coder already
know about this project and this kind of work". It stitches together:

  1. Project notes — distilled conventions / pitfalls / architecture
  2. Skills         — playbook entries whose triggers match the requirement
  3. Working fixes  — concrete {from_state -> fix} patterns from past rounds
  4. Reviewer comments digest — recent high-severity notes the Reviewer left
  5. Ask history digest     — questions the Reviewer asked before, answered
  6. FTS5-relevant past rounds (replaces naive "last 5")
  7. Global KB insights — cross-project lessons matched on keywords
  8. Cross-loop advisory — repeated patterns the heuristics already detect
  9. Harness notes — the agent's own memory_notes from the Continual Harness
     panel (`<project>/.kairos/harness/harness.json`), so a note recorded
     there actually reaches the prompt instead of being write-only.

Every section is bounded in tokens. The whole block is returned as one
string ready to be prepended to the Coder requirement.

Design note: this module does NOT mutate the DB — it only reads. The
growth side (auto-promotion, write-back) lives in `kairos.memory.growth`.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Iterable, List, Optional

from kairos.continual_harness import load_memory_notes

logger = logging.getLogger(__name__)


# Per-section token budgets. Conservative so we never blow the prompt.
MAX_NOTES_TOKENS = 600
MAX_SKILLS_TOKENS = 800
MAX_FIXES_TOKENS = 600
MAX_COMMENTS_TOKENS = 500
MAX_ASK_TOKENS = 400
MAX_HISTORY_TOKENS = 1200
MAX_GLOBAL_TOKENS = 400
# Harness notes are a peer of project notes (limit=12 below), so the bound is
# the same order of magnitude. Newest-first: the most recent self-edit is the
# most relevant, and 8 keeps even a heavily-used harness from dominating.
MAX_HARNESS_NOTES = 8
MAX_HARNESS_TOKENS = 500
MAX_TOTAL_TOKENS = 4500

# Memory records what was true *then*, it is not a description of now: a note
# written three months ago is not evidence about today's code. Every line
# therefore carries its age and the block says so once, because a model that
# cannot tell a fresh note from a stale one asserts the stale one with exactly
# the same confidence.
MEMORY_DRIFT_WARNING = (
    "> Memory below records what was observed **at the time**, not live state. "
    "Mind the dates and check the current code before relying on an entry."
)


def _age_phrase(ts: Any) -> str:
    """Human age for a unix timestamp: "today" / "4 days ago" / "3 months ago".

    Returns "" for a missing or unusable stamp — including the 0.0 that means
    "never recorded" — so callers can append the result blindly instead of
    inventing an age nobody knows.
    """
    if not ts:  # 0.0 / None / "" mean "no stamp", not "1970"
        return ""
    try:
        age = time.time() - float(ts)
    except (TypeError, ValueError):
        return ""
    if age < 60:
        return "just now"
    if age < 86400:
        return "today"
    days = int(age // 86400)
    if days == 1:
        return "yesterday"
    if days < 60:
        return f"{days} days ago"
    months = days // 30
    if months < 24:
        return f"{months} months ago"
    return f"{months // 12} years ago"


def _age_suffix(*stamps: Any) -> str:
    """``", 4 days ago"``, or "" when none of the stamps is usable.

    Comma form, not parenthesised: callers already sit inside their own
    ``(...)``/``[...]`` and nesting them read as ``(user (4 days ago))``.
    """
    for stamp in stamps:
        phrase = _age_phrase(stamp)
        if phrase:
            return f", {phrase}"
    return ""


def _approx_tokens(text: str) -> int:
    """Rough token estimate (~4 chars per token). Same heuristic the
    agent uses elsewhere, kept consistent so the budgets above are
    predictable across the codebase."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _truncate_to_tokens(text: str, budget: int) -> str:
    if _approx_tokens(text) <= budget:
        return text
    # Truncate to ~budget*4 chars, leave room for the marker.
    cut = max(0, budget * 4 - 30)
    return text[:cut] + "\n... (truncated)"


def _render_notes(notes: List[dict]) -> str:
    if not notes:
        return ""
    lines = ["## Project Notes (auto-remembered)"]
    for n in notes:
        kind = n.get("kind") or "note"
        title = n.get("title") or "(untitled)"
        body = (n.get("body") or "").strip()
        if not body:
            continue
        src = n.get("source") or "user"
        age = _age_suffix(n.get("updated_at"), n.get("created_at"))
        lines.append(f"- [{kind}] {title} ({src}{age}): {body}")
    return _truncate_to_tokens("\n".join(lines), MAX_NOTES_TOKENS)


def _norm_for_dedup(text: Any) -> str:
    """Whitespace/case-folded key for "is this the same note twice?".

    Used to keep a lesson that lives in both the project-notes table and the
    harness from being injected twice in one prompt.
    """
    return " ".join(str(text or "").split()).strip().lower()


def _render_harness_notes(notes: List[dict], exclude: Optional[set] = None) -> str:
    """Render the agent's own Continual-Harness ``memory_notes``.

    ``exclude`` holds normalised texts already injected elsewhere (the project
    notes rendered just before this), so the same content is not stated twice
    in one prompt. A header names the source so the model can tell a
    self-recorded harness note from a user/auto project note.
    """
    if not notes:
        return ""
    exclude = exclude or set()
    lines = ["## Harness Notes (self-recorded via the Continual Harness panel)"]
    seen: set = set()
    for n in notes:
        key = (n.get("key") or "").strip() or "(untitled)"
        value = (n.get("value") or "").strip()
        if not value:
            continue
        norm = _norm_for_dedup(value)
        if norm in exclude or norm in seen:
            continue
        seen.add(norm)
        tags = n.get("tags")
        tag_s = ""
        if isinstance(tags, list) and tags:
            tag_s = " [" + ", ".join(str(t) for t in tags[:5]) + "]"
        age = _age_suffix(n.get("updated_at"), n.get("added_at"))
        lines.append(f"- {key}{tag_s} (harness{age}): {value}")
    if len(lines) == 1:  # header only — every note was dropped
        return ""
    return _truncate_to_tokens("\n".join(lines), MAX_HARNESS_TOKENS)


def _render_skills(skills: List[dict]) -> str:
    if not skills:
        return ""
    lines = ["## Skills / Playbooks (matched by requirement keywords)"]
    for s in skills:
        name = s.get("name") or "?"
        body = (s.get("body") or "").strip()
        conf = float(s.get("confidence") or 0.5)
        if not body:
            continue
        lines.append(f"### {name} (confidence {conf:.2f})\n{body}")
    return _truncate_to_tokens("\n\n".join(lines), MAX_SKILLS_TOKENS)


def _render_working_fixes(fixes: Iterable[dict]) -> str:
    items = [f for f in fixes if f]
    if not items:
        return ""
    lines = ["## Known Working Fixes (matched by current failure signature)"]
    for fix in items:
        sig = fix.get("from_signature") or "?"
        body = (fix.get("fix_body") or "").strip()
        success = fix.get("success_count") or 0
        if not body:
            continue
        age = _age_suffix(fix.get("updated_at"), fix.get("created_at"))
        lines.append(f"- [{sig}] (used {success}x{age}): {body}")
    return _truncate_to_tokens("\n".join(lines), MAX_FIXES_TOKENS)


def _render_reviewer_comments(comments: List[dict]) -> str:
    """comments is the parsed JSON list from `review_comments` table."""
    if not comments:
        return ""
    # Only keep high-severity items, sorted descending.
    high = [c for c in comments if str(c.get("severity") or "").upper() in {"CRITICAL", "MAJOR"}]
    high.sort(key=lambda c: 0 if str(c.get("severity") or "").upper() == "CRITICAL" else 1)
    if not high:
        return ""
    lines = ["## Recent Reviewer Comments (high severity)"]
    for c in high[:8]:
        sev = c.get("severity", "?")
        path = c.get("file") or c.get("path") or "?"
        line = c.get("line") or "?"
        body = (c.get("body") or c.get("text") or c.get("description") or "").strip()
        if not body:
            continue
        lines.append(f"- [{sev}] {path}:{line} - {body[:180]}")
    return _truncate_to_tokens("\n".join(lines), MAX_COMMENTS_TOKENS)


def _render_ask_history(asks: List[dict]) -> str:
    if not asks:
        return ""
    lines = ["## Previously Asked Reviewer Questions (so we don't repeat them)"]
    for a in asks[:6]:
        q = (a.get("question") or "").strip()
        ans = (a.get("answer") or "").strip()
        if not q:
            continue
        if ans:
            lines.append(f"- Q: {q[:140]}\n  A: {ans[:200]}")
        else:
            lines.append(f"- Q (unanswered): {q[:200]}")
    return _truncate_to_tokens("\n".join(lines), MAX_ASK_TOKENS)


def _render_relevant_history(rows: List[dict]) -> str:
    """rows from FTS5 search; each has round / coder_summary / review_summary."""
    if not rows:
        return ""
    lines = ["## Relevant Past Rounds (FTS-ranked)"]
    for r in rows[:5]:
        rnd = r.get("round") or "?"
        verdict = ""
        score = r.get("score")
        if isinstance(score, (int, float)):
            verdict = f"score {score:.1f}"
        cs = (r.get("coder_summary") or "").strip()[:140]
        rs = (r.get("review_summary") or "").strip()[:140]
        lines.append(f"- R{rnd} {verdict}: coder={cs} | reviewer={rs}")
    return _truncate_to_tokens("\n".join(lines), MAX_HISTORY_TOKENS)


def _render_global_insights(insights: List[dict]) -> str:
    if not insights:
        return ""
    lines = ["## Global Knowledge (cross-project lessons)"]
    for ins in insights[:5]:
        body = (ins.get("body") or "").strip()
        cat = ins.get("category") or "general"
        uses = ins.get("use_count") or 0
        if not body:
            continue
        age = _age_suffix(ins.get("created_at"), ins.get("updated_at"))
        lines.append(f"- [{cat} x{uses}{age}] {body}")
    return _truncate_to_tokens("\n".join(lines), MAX_GLOBAL_TOKENS)


def _resolve_project_dir(persistence: Any, project_id: str) -> Optional[str]:
    """Best-effort location of a project's on-disk directory.

    Mirrors ``api.routes.p2_features._project_work_dir`` — ``work_dir`` first,
    then ``workspace`` — because that is the directory the harness store writes
    ``.kairos/harness/harness.json`` under, and the read must use the *same*
    directory as the write or the note stays invisible. Returns None when the
    project cannot be resolved, so the harness section is skipped rather than
    guessed at (never silently reads some other project's notes).
    """
    if not project_id:
        return None
    try:
        rows = persistence.load_projects()
    except Exception:
        logger.debug("memory: project lookup for harness failed", exc_info=True)
        return None
    for row in rows or []:
        if str(row.get("id")) != str(project_id):
            continue
        directory = row.get("work_dir") or row.get("workspace")
        return str(directory) if directory else None
    return None


def assemble_coder_memory(
    persistence: Any,
    project_id: str,
    requirement: str,
    *,
    last_failure_signature: Optional[str] = None,
    last_reviewer_comments: Optional[List[dict]] = None,
) -> str:
    """Build a single string containing every "memory" section the
    Coder should see at loop start. Returns "" when persistence is
    None (so callers can `if mem_block: ...` without sentinel checks).

    Sections are independent; a failure in any one is logged and the
    section silently dropped so a corrupt memory table never breaks
    the loop.

    The total is bounded by MAX_TOTAL_TOKENS so we never overflow the
    prompt even if the user has hundreds of saved skills.
    """
    if persistence is None:
        return ""

    sections: List[str] = []

    # 1. Project notes (most-used first)
    project_note_norms: set = set()
    try:
        notes = persistence.list_project_notes(project_id, limit=12)
        for n in notes:
            for field in ("title", "body"):
                norm = _norm_for_dedup(n.get(field))
                if norm:
                    project_note_norms.add(norm)
        rendered = _render_notes(notes)
        if rendered:
            sections.append(rendered)
            # Bump use counts so the most-referenced notes bubble up.
            for n in notes:
                try:
                    persistence.bump_project_note_use(n["id"])
                except Exception:
                    pass
    except Exception:
        logger.debug("memory: notes retrieval failed", exc_info=True)

    # 1b. Harness notes — the agent's own Continual-Harness memory_notes.
    # Same "notes" family as (1): read from the project's harness file the
    # harness API writes, bounded the same way, and deduped against the
    # project notes just rendered so one lesson is not stated twice.
    try:
        project_dir = _resolve_project_dir(persistence, project_id)
        if project_dir:
            harness_notes = load_memory_notes(project_dir, limit=MAX_HARNESS_NOTES)
            rendered = _render_harness_notes(harness_notes, exclude=project_note_norms)
            if rendered:
                sections.append(rendered)
    except Exception:
        logger.debug("memory: harness-notes retrieval failed", exc_info=True)

    # 2. Skills — match on requirement text + last failure text
    try:
        skill_query = requirement or ""
        if last_failure_signature:
            skill_query = (skill_query + "\n" + last_failure_signature)[:2000]
        skills = persistence.find_skills_for(project_id, skill_query, limit=5)
        rendered = _render_skills(skills)
        if rendered:
            sections.append(rendered)
    except Exception:
        logger.debug("memory: skills retrieval failed", exc_info=True)

    # 3. Working fixes — exact signature match on the current failure
    try:
        if last_failure_signature:
            fix = persistence.find_working_fix(project_id, last_failure_signature)
            rendered = _render_working_fixes([fix] if fix else [])
            if rendered:
                sections.append(rendered)
    except Exception:
        logger.debug("memory: working-fixes retrieval failed", exc_info=True)

    # 4. Reviewer comments — passed in directly from the prior review,
    # or looked up from the DB so the next-round prompt still has them.
    try:
        comments = last_reviewer_comments
        if comments is None:
            # Fall back to the most recent round's comments.
            try:
                comments = persistence.load_review_comments(project_id, round_no=0)
            except Exception:
                comments = []
        rendered = _render_reviewer_comments(comments or [])
        if rendered:
            sections.append(rendered)
    except Exception:
        logger.debug("memory: reviewer-comments retrieval failed", exc_info=True)

    # 5. Ask history — questions the Reviewer asked, with user answers
    try:
        asks = persistence.load_ask_history(project_id, limit=6)
        rendered = _render_ask_history(asks)
        if rendered:
            sections.append(rendered)
    except Exception:
        logger.debug("memory: ask-history retrieval failed", exc_info=True)

    # 6. FTS5-relevant history
    try:
        query = (requirement or "").strip()
        # Mix in a couple of keywords from the most recent issues so
        # we get failure-relevant history even when the requirement is
        # terse (e.g. "fix it").
        history = persistence.search_loop_rounds(project_id, query, limit=5)
        # Semantic re-rank: FTS rank is term-frequency-driven, so a
        # round that shares more raw words can outrank one that is
        # actually closer in meaning. Re-order the FTS hits by
        # TF-IDF cosine against the full requirement.
        if history and query:
            try:
                from kairos.memory.semantic import rank_by_similarity
                docs = [
                    "{} {} {}".format(
                        r.get("coder_summary") or "",
                        r.get("review_summary") or "",
                        r.get("issues_text") or "",
                    )
                    for r in history
                ]
                order = rank_by_similarity(query, docs)
                history = [history[i] for i, _ in order]
            except Exception:
                logger.debug("memory: semantic rerank failed", exc_info=True)
        rendered = _render_relevant_history(history)
        if rendered:
            sections.append(rendered)
    except Exception:
        logger.debug("memory: fts history retrieval failed", exc_info=True)

    # 7. Global insights — cross-project lessons
    try:
        insights = persistence.search_global_insights(requirement or "", limit=5)
        rendered = _render_global_insights(insights)
        if rendered:
            sections.append(rendered)
            for ins in insights:
                try:
                    persistence.bump_global_insight_use(ins["id"])
                except Exception:
                    pass
    except Exception:
        logger.debug("memory: global-kb retrieval failed", exc_info=True)

    # Concatenate and truncate to the overall budget.
    full = "\n\n".join(s for s in sections if s)
    if not full:
        return ""
    # Said once at the top rather than repeated in every section.
    return _truncate_to_tokens(
        f"{MEMORY_DRIFT_WARNING}\n\n{full}", MAX_TOTAL_TOKENS)


def failure_signature(issue: dict) -> str:
    """Build the canonical "from_state" signature for an issue.

    Used both to look up working fixes and to record new ones. The
    signature is intentionally rough — file + category + 3 content
    keywords — so similar failures across rounds collapse to the same
    signature without needing an embedding model.
    """
    if not issue:
        return ""
    path = str(issue.get("file") or issue.get("path") or "").strip()
    category = str(issue.get("category") or "general").strip().lower()
    description = str(issue.get("description") or "").strip()
    # Take the first 3 alpha words >3 chars from the description.
    words = []
    seen = set()
    for w in description.split():
        clean = "".join(c for c in w if c.isalpha()).lower()
        if len(clean) >= 4 and clean not in seen:
            seen.add(clean)
            words.append(clean)
        if len(words) >= 3:
            break
    sig = f"{path}:{category}:{'+'.join(words)}"
    return sig[:500]
