"""Inline code comment writer.

Converts Reviewer verdicts into `::code-comment` directives that editor
plugins (VS Code, Cursor) can render as red/yellow/green squiggles with
hover-text. Format mirrors Codex desktop's inline comment system:

    ::code-comment{title="..." body="..." file="..." start=N end=N priority=0..3}

We emit JSON (one comment per issue). The frontend can render them
directly, or a small CLI can append them to the file as comments for
non-IDE consumption.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


# Severity -> Codex priority mapping (0 = blocker, 3 = nit).
_SEVERITY_PRIORITY = {
    "CRITICAL": 0,
    "MAJOR": 1,
    "MINOR": 2,
    "SUGGESTION": 3,
}


def verdict_to_comments(
    review: Dict[str, Any],
    project_id: str = "",
    round_no: int = 0,
) -> List[Dict[str, Any]]:
    """Convert a parsed Reviewer verdict into ::code-comment dicts.

    Each issue becomes one comment. Returns an empty list if `review`
    has no `issues` field.
    """
    issues = review.get("issues") or []
    comments = []
    for it in issues:
        severity = it.get("severity", "MINOR")
        priority = _SEVERITY_PRIORITY.get(severity, 2)
        file = it.get("file", "")
        line = int(it.get("line", 0) or 0)
        title = f"[{severity}] {it.get('category', 'issue')}"
        body_parts = [it.get("description", "").strip()]
        fix = it.get("fix_instruction", "").strip()
        if fix:
            body_parts.append(f"\nFix: {fix}")
        source = it.get("_source_reviewer", "reviewer")
        if source and source != "reviewer":
            body_parts.append(f"\n(flagged by {source})")
        comments.append({
            "title": title,
            "body": "\n".join(p for p in body_parts if p),
            "file": file,
            "start": line,
            "end": line,  # single-line comments; expand if line ranges come later
            "priority": priority,
            "metadata": {
                "project_id": project_id,
                "round": round_no,
                "category": it.get("category", ""),
                "severity": severity,
            },
        })
    return comments


def comments_to_jsonl(comments: List[Dict[str, Any]]) -> str:
    """Serialize comments as JSON Lines, one per line.

    Suitable for piping into editor plugins or `code-comment apply`.
    """
    return "\n".join(json.dumps(c, ensure_ascii=False) for c in comments)


def comments_to_directive_lines(comments: List[Dict[str, Any]]) -> str:
    """Render comments as Codex-style ::code-comment{...} directives.

    One per line. Useful for embedding directly in source files as
    inline review notes that downstream tools can grep for.
    """
    lines = []
    for c in comments:
        meta = json.dumps({
            "title": c["title"],
            "body": c["body"],
            "file": c["file"],
            "start": c["start"],
            "end": c["end"],
            "priority": c["priority"],
        }, ensure_ascii=False)
        lines.append(f"::code-comment{{{meta}}}")
    return "\n".join(lines)