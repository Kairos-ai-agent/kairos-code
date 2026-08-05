"""Self-learning subsystem.

The reflection module runs as a fire-and-forget background task after
each loop finishes. See `reflect.py` for the main entry point.
"""
from kairos.learning.reflect import (
    maybe_run_reflection,
    REFLECTION_TIMEOUT_S,
    REFLECTION_MAX_TOKENS,
    MIN_REFLECTION_INTERVAL_S,
)

__all__ = [
    "maybe_run_reflection",
    "REFLECTION_TIMEOUT_S",
    "REFLECTION_MAX_TOKENS",
    "MIN_REFLECTION_INTERVAL_S",
]
