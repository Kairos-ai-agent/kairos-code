"""Agent System for Kairos Code.

R38.6.4: the eager ``from kairos.agents.base import ...`` at module
top level was breaking PyInstaller onefile bundles (the eager
import ran before the loader had a chance to register the
submodule). We now lazy-import via __getattr__ so the submodule
only loads when something actually references these names.
"""
from typing import Any


def __getattr__(name: str) -> Any:
    if name in {"KairosAgent", "AgentTask", "AgentState", "AgentStatus"}:
        from kairos.agents.base import (
            KairosAgent, AgentTask, AgentState, AgentStatus,
        )
        mapping = {
            "KairosAgent": KairosAgent,
            "AgentTask": AgentTask,
            "AgentState": AgentState,
            "AgentStatus": AgentStatus,
        }
        return mapping[name]
    raise AttributeError(f"module 'kairos.agents' has no attribute {name!r}")


__all__ = ["KairosAgent", "AgentTask", "AgentState", "AgentStatus"]
