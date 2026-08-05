"""Plan-mode output validation and cleanup."""
from __future__ import annotations

import re

_PLAN_NOISE_PATTERNS = [
    re.compile(r"<\|[a-zA-Z0-9_\- ]+\|>"),
    re.compile(r"<][a-zA-Z0-9_\-]+[>\[]"),
    re.compile(r"<\|?tool_call\|?>", re.IGNORECASE),
    re.compile(r"<\|?/?tool_call\|?>", re.IGNORECASE),
    re.compile(r"<\|?function_call\|?>", re.IGNORECASE),
    re.compile(r"<\|?/?function_call\|?>", re.IGNORECASE),
    re.compile(
        r'\{[\s\S]*?"name"\s*:\s*"[a-zA-Z_]+"[\s\S]*?'
        r'"arguments"\s*:\s*\{[\s\S]*?\}[\s\S]*?\}'
    ),
]

def is_plan_dirty(text: str) -> bool:
    """Return whether plan output contains control or tool-call residue."""
    if not text:
        return True
    if any(pattern.search(text) for pattern in _PLAN_NOISE_PATTERNS):
        return True
    stripped = text.strip()
    return stripped.startswith("{") and stripped.endswith("}")

def sanitize_plan_text(text: str) -> str:
    """Remove known provider control tokens without discarding useful prose."""
    if not text:
        return text
    cleaned = text
    for pattern in _PLAN_NOISE_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned or text.strip()