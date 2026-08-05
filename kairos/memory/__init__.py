"""Memory subsystem: notes, skills, working fixes, FTS5 search, growth."""
from kairos.memory.retrieval import (
    assemble_coder_memory,
    failure_signature,
)
from kairos.memory.growth import (
    auto_promote_failure_to_preference,
    maybe_record_working_fix,
    maybe_promote_skill,
)

__all__ = [
    "assemble_coder_memory",
    "failure_signature",
    "auto_promote_failure_to_preference",
    "maybe_record_working_fix",
    "maybe_promote_skill",
]
