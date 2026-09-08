"""Execution Trace - Complete decision chain recording for debugging and audit.

Records every LLM call, tool invocation, and state change in a structured
format that supports:
- Post-hoc analysis of agent behavior
- Reproducibility (replay traces)
- Debugging stuck loops
- Audit trails for safety compliance
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ToolCallRecord:
    """Record of a single tool invocation."""
    call_id: str
    tool_name: str
    arguments: Dict[str, Any]
    start_time: float
    end_time: float
    result: Any
    error: Optional[str] = None
    cached: bool = False
    
    @property
    def duration_ms(self) -> int:
        return int((self.end_time - self.start_time) * 1000)
    
    def to_dict(self) -> dict:
        return {
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "result_preview": str(self.result)[:200] if self.result else None,
            "error": self.error,
            "cached": self.cached,
        }


@dataclass
class LLMCallRecord:
    """Record of a single LLM API call."""
    call_id: str
    model: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    temperature: float
    response_time_ms: int
    function_calls: List[Dict[str, Any]] = field(default_factory=list)
    response_text: Optional[str] = None
    error: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "call_id": self.call_id,
            "model": self.model,
            "provider": self.provider,
            "tokens": {
                "prompt": self.prompt_tokens,
                "completion": self.completion_tokens,
                "total": self.total_tokens,
            },
            "temperature": self.temperature,
            "response_time_ms": self.response_time_ms,
            "error": self.error,
        }


@dataclass
class DecisionPoint:
    """A point where the agent made a significant decision."""
    timestamp: float
    decision_type: str  # "plan", "tool_choice", "strategy_change", etc.
    rationale: str
    alternatives_considered: List[str] = field(default_factory=list)
    outcome: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "decision_type": self.decision_type,
            "rationale": self.rationale,
            "alternatives": self.alternatives_considered,
            "outcome": self.outcome,
        }


class ExecutionTrace:
    """Complete execution trace for a loop session."""
    
    def __init__(self, project_id: str, session_id: str, output_dir: Optional[Path] = None):
        self.project_id = project_id
        self.session_id = session_id
        self.start_time = time.time()
        self.trace_file = (output_dir or Path(".trace")).joinpath(
            f"{project_id}_{session_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        
        self.llm_calls: List[LLMCallRecord] = []
        self.tool_calls: List[ToolCallRecord] = []
        self.decisions: List[DecisionPoint] = []
        self.state_snapshots: List[Dict[str, Any]] = []
        self._lock = False  # Placeholder for threading safety
        
    def record_llm_call(self, call: LLMCallRecord) -> None:
        """Record an LLM API call."""
        self.llm_calls.append(call)
        
    def record_tool_call(self, call: ToolCallRecord) -> None:
        """Record a tool invocation."""
        self.tool_calls.append(call)
        
    def record_decision(self, decision: DecisionPoint) -> None:
        """Record a significant decision point."""
        self.decisions.append(decision)
        
    def snapshot_state(self, state: Dict[str, Any]) -> None:
        """Capture current agent state."""
        self.state_snapshots.append({
            "timestamp": time.time(),
            "state": state,
        })
        
    def save(self) -> Path:
        """Save trace to JSON file."""
        trace_data = {
            "project_id": self.project_id,
            "session_id": self.session_id,
            "start_time": self.start_time,
            "duration_s": time.time() - self.start_time,
            "llm_calls": [c.to_dict() for c in self.llm_calls],
            "tool_calls": [c.to_dict() for c in self.tool_calls],
            "decisions": [d.to_dict() for d in self.decisions],
            "state_snapshots": self.state_snapshots,
        }
        
        self.trace_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.trace_file, 'w', encoding='utf-8') as f:
            json.dump(trace_data, f, indent=2, ensure_ascii=False)
        
        return self.trace_file
    
    def get_stats(self) -> Dict[str, Any]:
        """Get trace statistics."""
        total_prompt_tokens = sum(c.prompt_tokens for c in self.llm_calls)
        total_completion_tokens = sum(c.completion_tokens for c in self.llm_calls)
        total_response_time = sum(c.response_time_ms for c in self.llm_calls)
        
        return {
            "duration_s": time.time() - self.start_time,
            "llm_calls": len(self.llm_calls),
            "tool_calls": len(self.tool_calls),
            "decisions": len(self.decisions),
            "total_tokens": total_prompt_tokens + total_completion_tokens,
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "avg_response_time_ms": total_response_time / max(1, len(self.llm_calls)),
            "tool_errors": sum(1 for c in self.tool_calls if c.error),
        }
    
    def find_stuck_patterns(self) -> List[Dict[str, Any]]:
        """Detect patterns that indicate the agent is stuck."""
        patterns = []
        
        # Check for repeated tool calls
        tool_counts: Dict[str, int] = {}
        for call in self.tool_calls:
            tool_counts[call.tool_name] = tool_counts.get(call.tool_name, 0) + 1
        
        for tool, count in tool_counts.items():
            if count > 10:
                patterns.append({
                    "type": "repeated_tool",
                    "tool": tool,
                    "count": count,
                    "severity": "warning" if count < 20 else "critical",
                })
        
        # Check for long gaps between progress
        for i in range(len(self.decisions)):
            if i > 0:
                gap = self.decisions[i].timestamp - self.decisions[i-1].timestamp
                if gap > 300:  # 5 minutes
                    patterns.append({
                        "type": "long_gap",
                        "gap_seconds": gap,
                        "between_decisions": [i-1, i],
                        "severity": "info",
                    })
        
        return patterns
