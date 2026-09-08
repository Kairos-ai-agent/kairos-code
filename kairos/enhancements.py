"""Top-level package for Kairos agent enhancements.

This module provides backward-compatible re-exports of new capabilities:
- Hierarchical memory system
- Execution trace recording
- Adaptive gates
- Tool recommendation
- Human intervention interface
- Multi-objective optimization
- Enhanced sandboxing
- Explainability engine
- IDE protocol
"""
from __future__ import annotations

# Memory systems
from kairos.memory.hierarchical import (
    ActiveForgetting,
    HierarchicalMemory,
    MemoryEntry,
    RecallResult,
)
from kairos.memory.execution_trace import (
    DecisionPoint,
    ExecutionTrace,
    LLMCallRecord,
    ToolCallRecord,
)

# Loop enhancements
from kairos.loop.adaptive_gates import AdaptiveGates, get_adaptive_thresholds
from kairos.loop.multi_objective import (
    MultiObjectiveLoopController,
    ObjectiveScores,
    ObjectiveWeights,
    ParetoOptimizer,
)
from kairos.loop.types import (
    GateResult,
    LoopEventType,
    LoopMetadata,
    PlanDecision,
    ReflectionResult,
    ReviewVerdict,
    RoundResult,
)

# Tool enhancements
from kairos.tools.recommendation import (
    ToolRecommendationEngine,
    ToolRelevanceScore,
)

# Security enhancements
from kairos.sandbox.enhanced import EnhancedSandbox, ResourceLimit, SandboxSnapshot

# Human intervention
from kairos.human.intervention import (
    HumanInterventionManager,
    InterventionRequest,
    InterventionType,
)

# Explainability
from kairos.explainability.engine import ExplainabilityEngine, Explanation

# IDE integration
from kairos.ide.protocol import (
    IDEProtocolHandler,
    IDEMessage,
    IDEMessageType,
)

__all__ = [
    # Memory
    "HierarchicalMemory",
    "ActiveForgetting",
    "MemoryEntry",
    "RecallResult",
    "ExecutionTrace",
    "LLMCallRecord",
    "ToolCallRecord",
    "DecisionPoint",
    
    # Loop
    "AdaptiveGates",
    "get_adaptive_thresholds",
    "MultiObjectiveLoopController",
    "ParetoOptimizer",
    "ObjectiveScores",
    "ObjectiveWeights",
    "LoopEventType",
    "LoopMetadata",
    "RoundResult",
    "GateResult",
    "PlanDecision",
    "ReflectionResult",
    "ReviewVerdict",
    
    # Tools
    "ToolRecommendationEngine",
    "ToolRelevanceScore",
    
    # Security
    "EnhancedSandbox",
    "ResourceLimit",
    "SandboxSnapshot",
    
    # Human
    "HumanInterventionManager",
    "InterventionRequest",
    "InterventionType",
    
    # Explainability
    "ExplainabilityEngine",
    "Explanation",
    
    # IDE
    "IDEProtocolHandler",
    "IDEMessage",
    "IDEMessageType",
]
