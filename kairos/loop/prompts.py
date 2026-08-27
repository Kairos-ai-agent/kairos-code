"""Bounded prompt builders used by the Coder and Reviewer."""
from __future__ import annotations

from typing import Any, Dict, Iterable

_REVIEW_FOCUS_LABELS = {
    "bug_reviewer": (
        "BUGS ONLY. Look exclusively for runtime errors, exceptions, "
        "null-pointer / index-out-of-bounds risks, infinite loops, "
        "race conditions, unhandled error paths, broken control flow, "
        "and any code that obviously does not do what the requirement "
        "asks. Do NOT comment on style, naming, performance, security "
        "best-practices, architecture, test coverage, or any other "
        "subjective quality dimension. A file that runs correctly and "
        "matches the requirement is APPROVED with score 100, even if "
        "you would have written it differently. Only flag actual bugs."
    ),
    "security_reviewer": "security, authentication, authorization, secrets, and OWASP risks",
    "perf_reviewer": "performance, latency, memory, I/O, concurrency, and scalability",
    "design_reviewer": "architecture, maintainability, UX, accessibility, and visual regressions",
    "test_reviewer": "test coverage, edge cases, deterministic behavior, and regression safety",
}

def _render_self_debug_block(fixes: Iterable[Dict[str, Any]]) -> str:
    rendered = []
    for fix in list(fixes)[:5]:
        kind = str(fix.get("kind") or "known_error")
        match = str(fix.get("match") or "")[:160]
        instruction = str(fix.get("fix_instruction") or "apply the minimal fix")[:240]
        rendered.append(f"- {kind}: {match}\n  Minimal fix: {instruction}")
    if not rendered:
        return ""
    return (
        "\nSELF-DEBUG MODE (highest priority):\n"
        "Precheck found a concrete, auto-fixable failure. In this turn you MUST:\n"
        "1. Apply the smallest listed fix before any Reviewer suggestion.\n"
        "2. Avoid unrelated files and do not introduce new scope.\n"
        "3. Re-run the exact failing check and stop if it still fails.\n"
        "Detected fixes:\n" + "\n".join(rendered) + "\n"
    )

def build_next_prompt(session: Any, review: Dict[str, Any]) -> str:
    """Compose a bounded next-round Coder prompt from immutable inputs."""
    issues = review.get("issues") or []
    summary = str(review.get("summary") or "")[:1200]
    score = review.get("score", 0)
    approve = review.get("approve", False)

    issues_block = "\n".join(
        f"- [{issue.get('severity', '?')}] {issue.get('file', '?')}:"
        f"{issue.get('line', '?')} - {issue.get('description', '')}\n"
        f"  Fix: {issue.get('fix_instruction', '')}"
        for issue in issues[:30]
    ) or "(no specific issues)"

    history_hint = ""
    if session.history:
        previous = session.history[-1].get("review", {}).get("summary", "")
        if previous and previous != summary:
            history_hint = f"\nPrior-round summary: {str(previous)[:300]}\n"

    user_answer = str(review.get("_user_answer") or "").strip()
    answer_block = ""
    if user_answer:
        answer_block = (
            f"\nUSER ANSWER to Reviewer question: {user_answer[:2000]}\n"
            "Address this answer before the remaining issues.\n"
        )

    precheck_hint = str(review.get("_precheck_hint") or "").strip()
    precheck_block = ""
    if precheck_hint:
        precheck_block = (
            "\nFIX THESE PRECHECK FAILURES FIRST:\n" + precheck_hint[:3500] + "\n"
        )
    self_debug_block = _render_self_debug_block(
        review.get("_precheck_fixable") or []
    )

    # Pull the bounded memory block (notes, skills, working fixes, FTS
    # history, ask history, global insights). The orchestrator already
    # injects this on start_loop; we re-pull it here so within-loop
    # failures see fresh state (e.g. a working fix recorded last round).
    memory_block = ""
    try:
        from kairos.memory.retrieval import assemble_coder_memory
        persistence = getattr(session, "persistence", None)
        pid = getattr(getattr(session, "project", None), "id", None)
        if persistence is not None and pid:
            # Build a short failure signature so working fixes match.
            from kairos.memory.retrieval import failure_signature
            issues = review.get("issues") or []
            sig = failure_signature(issues[0]) if issues else ""
            comments = []
            for issue in issues:
                comments.append({
                    "severity": issue.get("severity") or "?",
                    "file": issue.get("file") or "?",
                    "line": issue.get("line") or "?",
                    "body": issue.get("description") or "",
                })
            memory_block = assemble_coder_memory(
                persistence,
                str(pid),
                str(getattr(session, "original_requirement", ""))[:2000],
                last_failure_signature=sig or None,
                last_reviewer_comments=comments or None,
            )
    except Exception:
        memory_block = ""

    memory_prepend = ""
    if memory_block:
        memory_prepend = "\n\n## Project Memory (notes / skills / past fixes)\n" + memory_block + "\n"

    return (
        memory_prepend
        + f"Round {session.round} of the loop. The previous round was NOT approved "
        f"(approve={approve}, score={score})."
        + answer_block
        + self_debug_block
        + "\n\n"
        + f"Reviewer summary: {summary}"
        + history_hint
        + precheck_block
        + "\n\nIssues to fix this round:\n"
        + issues_block
        + "\n\nOriginal user requirement (do not modify):\n"
        + str(session.original_requirement)
        + "\n\nFix only the failures and issues above. Do not introduce new scope. "
        "Run the relevant checks before reporting completion."
    )

def build_reviewer_description(
    session: Any,
    coder_result: str,
    round_no: int,
    precheck_hint: str = "",
) -> str:
    """Build a bounded review task for the single automatic Reviewer."""
    previous_summary = ""
    if session.history:
        previous_summary = str(
            session.history[-1].get("review", {}).get("summary", "")
        )[:500]
    focus_items = []
    for profile in getattr(session, "review_focus", None) or []:
        label = _REVIEW_FOCUS_LABELS.get(profile)
        if label and label not in focus_items:
            focus_items.append(label)
    focus_block = ""
    if focus_items:
        focus_block = (
            "\nReview focus profiles (same Reviewer, not extra agents):\n- "
            + "\n- ".join(focus_items)
            + "\n"
        )
    check_block = ""
    if precheck_hint:
        check_block = (
            "\nAutomated precheck output (a failing check must block approval):\n"
            + precheck_hint[:3500]
            + "\n"
        )
    requirement = str(getattr(session.project, "requirements", ""))[:2500]
    return (
        f"Review the Coder's work for round {round_no} of project "
        f"{session.project.id}.\n\n"
        f"Original requirement:\n{requirement}\n\n"
        f"Coder output (last 4000 chars):\n{(coder_result or '')[-4000:]}\n"
        + (f"\nPrevious review summary:\n{previous_summary}\n" if previous_summary else "")
        + focus_block
        + check_block
        + "\nInspect the actual workspace diff and run relevant tests. Return the "
        "strict JSON verdict only."
    )