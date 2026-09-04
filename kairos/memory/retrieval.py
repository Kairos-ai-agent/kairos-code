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

Every section is bounded in tokens. The whole block is returned as one
string ready to be prepended to the Coder requirement.

Design note: this module does NOT mutate the DB — it only reads. The
growth side (auto-promotion, write-back) lives in `kairos.memory.growth`.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterable, List, Optional

logger = logging.getLogger(__name__)


# Per-section token budgets. Conservative so we never blow the prompt.
MAX_NOTES_TOKENS = 600
MAX_SKILLS_TOKENS = 800
MAX_FIXES_TOKENS = 600
MAX_COMMENTS_TOKENS = 500
MAX_ASK_TOKENS = 400
MAX_HISTORY_TOKENS = 1200
MAX_GLOBAL_TOKENS = 400
MAX_TOTAL_TOKENS = 4500


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
        lines.append(f"- [{kind}] {title} ({src}): {body}")
    return _truncate_to_tokens("\n".join(lines), MAX_NOTES_TOKENS)


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
        lines.append(f"- [{sig}] (used {success}x): {body}")
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
        lines.append(f"- [{cat} x{uses}] {body}")
    return _truncate_to_tokens("\n".join(lines), MAX_GLOBAL_TOKENS)


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
    try:
        notes = persistence.list_project_notes(project_id, limit=12)
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
    return _truncate_to_tokens(full, MAX_TOTAL_TOKENS)


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
