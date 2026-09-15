"""R38.6 §34 — borrowed features from top AI agents.

A single module that wires together all the P0/P1 features
from the "what to borrow" roadmap. Each sub-module is
self-contained so we can build and test them in isolation.

This is a FACADE module — the actual heavy lifting lives in
the existing `kairos.approval`, `kairos.hooks`, `kairos.skills`,
`kairos.sandbox`, `kairos.compaction`, `kairos.memory_kb`
modules. We re-export them with a unified API + add the
missing pieces (new providers, IM platforms, FTS5 query,
Plan Mode, Session Fork).

Provides:
  - Approval flow (the cloud task-style) — already in kairos.approval
  - Hook system (Claude-style) — already in kairos.hooks
  - Skills runtime (Claude-style) — already in kairos.skills
  - Sandbox (the agent-gateway-style) — already in kairos.sandbox
  - Context compaction (Claude-style) — already in kairos.compaction
  - Memory FTS5 (self-improving style) — already in kairos.memory_kb
  - Plan Mode (Gemini-style) — kairos.plan_mode (new in §34)
  - Session Fork (the multi-model CLI-style) — kairos.session_fork (new)
  - More LLM providers (the multi-model CLI-style) — kairos.providers_more (new)
  - Multi-IM platforms (the agent-gateway-style) — kairos.im_platforms (new)
"""
from __future__ import annotations

# Re-exports for convenience — the actual implementations
# already exist in the project; we just want one import hub
# so the API routes can pull from a stable surface.

# Approval (the cloud task-style)
from kairos.approval import (  # noqa: F401
    ApprovalMode, decide, decide_with_mode_name,
    READ_ONLY_TOOLS, DEFAULT_MODE,
)

# Hooks (Claude-style)
from kairos.hooks import (  # noqa: F401
    HookEvent, HookContext, HookDecision, HookSpec, HookResult,
    HookRegistry, HookRunner, get_default_registry, get_runner,
    load_project_hooks, reset_default_registry,
)

# Skills runtime (Claude-style)
from kairos.skills import (  # noqa: F401
    Skill, SkillsLoader,
)

# Sandbox (the agent-gateway-style)
from kairos.sandbox import (  # noqa: F401
    SandboxPolicy, check_policy, apply_to_subprocess,
)

# Compaction (Claude-style)
from kairos.compaction import (  # noqa: F401
    CompactedDigest, maybe_compact, build_digest, compaction_stats,
)

# Memory (self-improving style — 4-op API)
from kairos.memory_kb import (  # noqa: F401
    MemoryKB, MemoryEntry,
)

# Plan Mode (Gemini-style)
from kairos.plan_mode import (  # noqa: F401
    Plan, PlanStep, PlanStore, build_plan_from_llm, mark_step,
)


# Convenience wrappers around SkillsLoader for the API layer
def load_skill(name: str):
    """Load a single skill by name from the project + global
    + bundled directories. Returns the Skill object or None."""
    from kairos.config.settings import settings as ksettings
    from kairos.skills import SkillsLoader
    # NOTE: no ``bundled_dir`` here. The loader's default is <package>/skills/,
    # which is where the bundled set actually ships (the wheel carries 52 files).
    # These wrappers used to point it at <data_dir>/bundled_skills — a path
    # nothing ever populates — so every bundled skill was invisible to them.
    sl = SkillsLoader(
        project_dir=ksettings.data_dir / "skills",
        global_dir=ksettings.data_dir / "global_skills",
    )
    # Use what discover() *returns* — the loader has no ``.skills`` attribute,
    # so the old ``sl.skills`` lookup found nothing even with correct dirs.
    for s in sl.discover():
        if s.name == name:
            return s
    return None


def list_skills():
    """List all available skills."""
    from kairos.config.settings import settings as ksettings
    from kairos.skills import SkillsLoader
    # See load_skill: bundled_dir stays at its default (<package>/skills/).
    sl = SkillsLoader(
        project_dir=ksettings.data_dir / "skills",
        global_dir=ksettings.data_dir / "global_skills",
    )
    return list(sl.discover())


__all__ = [
    # Approval
    "ApprovalMode", "decide", "decide_with_mode_name",
    "READ_ONLY_TOOLS", "DEFAULT_MODE",
    # Hooks
    "HookEvent", "HookContext", "HookDecision", "HookSpec", "HookResult",
    "HookRegistry", "HookRunner", "get_default_registry", "get_runner",
    "load_project_hooks",
    # Skills
    "Skill", "SkillsLoader", "load_skill", "list_skills",
    # Sandbox
    "SandboxPolicy", "check_policy", "apply_to_subprocess",
    # Compaction
    "CompactedDigest", "maybe_compact", "build_digest", "compaction_stats",
    # Memory
    "MemoryKB", "MemoryEntry", "load_skill", "list_skills",
    # Plan Mode
    "Plan", "PlanStep", "PlanStore", "build_plan_from_llm", "mark_step",
]
