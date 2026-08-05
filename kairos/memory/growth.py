"""Growth subsystem: write-side of the memory layer.

Where `retrieval.py` reads from the memory tables, this module writes
to them. Every helper here is a small focused action that the loop
runner / orchestrator can fire at well-defined points:

  - after each round:        `maybe_record_working_fix`
  - when patterns repeat:    `auto_promote_failure_to_preference`
  - when a skill helps:      `maybe_promote_skill`
  - when loops finish:       `consolidate_project` (the heavy-lifter)

Helpers are designed to be cheap and best-effort: failures here never
break the loop, they just mean a learning opportunity is missed.
"""
from __future__ import annotations

import json
import logging
from typing import Any, List, Optional

from kairos.memory.retrieval import failure_signature

logger = logging.getLogger(__name__)


def auto_promote_failure_to_preference(
    persistence: Any,
    project_id: str,
    issue: dict,
    consecutive_count: int,
    *,
    threshold: int = 3,
    confidence: Optional[float] = None,
    min_confidence: float = 0.5,
) -> Optional[int]:
    """If the same issue category / signature has fired N times in a
    row, write a `never` preference so the Coder is told not to do
    it again on the next round.

    Threshold defaults to 3 — same number the cross-loop pattern
    detector uses. Returns the new preference id, or None if the
    threshold wasn't met / the preference already exists.
    """
    if not persistence or not issue or consecutive_count < threshold:
        return None
    if confidence is not None and confidence < min_confidence:
        return None
    category = str(issue.get("category") or "general").strip().lower()
    description = str(issue.get("description") or "").strip()
    if not description:
        return None
    # Phrase it as a "never" rule the Coder can read.
    rule = f"Avoid {category} issues like: {description[:140]}"
    try:
        existing = persistence.list_preferences(project_id)
        for p in existing:
            if (p.get("kind") == "never"
                    and rule.lower()[:80] in (p.get("rule") or "").lower()):
                return None  # de-duplicate
        return persistence.add_preference(project_id, "never", rule)
    except Exception:
        logger.debug("auto-promote failed", exc_info=True)
        return None


def maybe_record_working_fix(
    persistence: Any,
    project_id: str,
    prior_review: Optional[dict],
    current_review: Optional[dict],
    coder_result: str,
) -> Optional[int]:
    """If the round went from FAIL to PASS, persist the failing
    signature + a short excerpt of what the Coder did as a working
    fix. The next round with the same signature will get the fix
    pre-injected.
    """
    if not persistence or not prior_review or not current_review:
        return None
    if current_review.get("approve") and not prior_review.get("approve"):
        # Confidence gate: only record a fix when the verdict that
        # accepted it is reasonably confident. Prevents low-confidence
        # approvals from polluting the working-fixes playbook.
        try:
            conf = float(current_review.get("_confidence") or 0.5)
        except (TypeError, ValueError):
            conf = 0.5
        if conf < 0.6:
            return None
        prior_issues = prior_review.get("issues") or []
        if not prior_issues:
            return None
        # Use the first high-severity prior issue as the signature.
        sig_issue = next(
            (i for i in prior_issues
             if str(i.get("severity") or "").upper() in {"CRITICAL", "MAJOR"}),
            prior_issues[0],
        )
        sig = failure_signature(sig_issue)
        if not sig:
            return None
        # Extract the actionable line(s) of the fix from the Coder
        # result: anything after "Fix:" / "Applied:" / final 200 chars.
        fix_excerpt = (coder_result or "")[-400:].strip()
        if not fix_excerpt:
            return None
        try:
            return persistence.add_working_fix(
                project_id, sig, fix_excerpt,
                issue_category=sig_issue.get("category") or "general",
            )
        except Exception:
            logger.debug("record working fix failed", exc_info=True)
            return None
    return None


def maybe_promote_skill(
    persistence: Any,
    skill_id: int,
    review: Optional[dict],
) -> None:
    """Call after a round: if the Reviewer approved, climb the skill's
    confidence; otherwise lower it. Both paths bump use_count so the
    skill library reflects actual consumption."""
    if not persistence or not skill_id or not review:
        return
    try:
        persistence.record_skill_outcome(skill_id, success=bool(review.get("approve")))
    except Exception:
        logger.debug("skill outcome record failed", exc_info=True)


def record_global_insights_from_review(
    persistence: Any,
    project_id: str,
    review: dict,
    *,
    min_use_threshold: int = 1,
) -> List[int]:
    """Pull a few short, broadly-useful lessons out of a Reviewer
    verdict and push them into the cross-project knowledge base.

    Heuristic: only CRITICAL/MAJOR issues with <=120 char descriptions
    get promoted — these tend to be the universal "don't do X"
    lessons that apply across projects. Returns the insight ids.
    """
    if not persistence or not review:
        return []
    ids: List[int] = []
    try:
        for issue in review.get("issues") or []:
            sev = str(issue.get("severity") or "").upper()
            if sev not in {"CRITICAL", "MAJOR"}:
                continue
            desc = (issue.get("description") or "").strip()
            if not desc or len(desc) > 200:
                continue
            category = issue.get("category") or "general"
            ids.append(persistence.add_global_insight(
                category=category,
                body=desc[:280],
                source_project_id=project_id or "",
                min_use_threshold=min_use_threshold,
            ))
    except Exception:
        logger.debug("global-insight extraction failed", exc_info=True)
    return ids


def consolidate_project(
    persistence: Any,
    project_id: str,
    rounds: List[dict],
) -> dict:
    """Best-effort offline consolidation of a project's loop history.

    Called by the post-loop self-learning task. We don't call an LLM
    here — this is the cheap pass that runs every loop end. It:

      1. Re-derives the cross-loop advisory (existing logic, but
         centralized here so the orchestrator doesn't have to know
         about both cross_loop and memory modules).
      2. Detects repeated fix signatures (same signature, 2+ different
         "fix_body" entries) and flags them for human review.
      3. Promotes high-severity recurring issues into `never`
         preferences so the next loop session avoids them outright.

    Returns a small dict so callers can log / display the consolidation
    summary without re-deriving anything.
    """
    summary = {
        "promoted_preferences": 0,
        "ambiguous_signatures": 0,
        "advisory": "",
    }
    if not persistence or not project_id or not rounds:
        return summary
    try:
        from kairos.loop.cross_loop import detect_cross_loop_patterns
        summary["advisory"] = detect_cross_loop_patterns(rounds)
    except Exception:
        logger.debug("consolidate: advisory failed", exc_info=True)

    # 1. Auto-promote repeated categories to a never preference.
    try:
        from collections import Counter
        cat_counter = Counter()
        cat_sample_issue = {}
        for r in rounds:
            review = {}
            raw = r.get("review_json")
            if isinstance(raw, str) and raw.strip():
                try:
                    review = json.loads(raw)
                except (TypeError, ValueError):
                    review = {}
            elif isinstance(raw, dict):
                review = raw
            for issue in (review or {}).get("issues") or []:
                cat = str(issue.get("category") or "general").strip().lower()
                if cat:
                    cat_counter[cat] += 1
                    if cat not in cat_sample_issue:
                        cat_sample_issue[cat] = issue
        for cat, count in cat_counter.items():
            if count >= 3:
                issue = cat_sample_issue[cat]
                pid = auto_promote_failure_to_preference(
                    persistence, project_id, issue, count, threshold=3,
                )
                if pid is not None:
                    summary["promoted_preferences"] += 1
    except Exception:
        logger.debug("consolidate: promotion failed", exc_info=True)

    # 2. Flag ambiguous signatures (heuristic only — full review is the
    #    consolidator LLM call, future work).
    try:
        sig_to_fixes = {}
        for r in rounds:
            review = {}
            raw = r.get("review_json")
            if isinstance(raw, str) and raw.strip():
                try:
                    review = json.loads(raw)
                except (TypeError, ValueError):
                    review = {}
            for issue in (review or {}).get("issues") or []:
                sig = failure_signature(issue)
                if sig:
                    sig_to_fixes.setdefault(sig, set()).add(
                        (issue.get("category") or "general")
                    )
        for sig, cats in sig_to_fixes.items():
            if len(cats) > 1:
                summary["ambiguous_signatures"] += 1
    except Exception:
        logger.debug("consolidate: ambiguity scan failed", exc_info=True)

    return summary
