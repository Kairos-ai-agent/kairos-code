"""Explainability - Make agent decisions transparent and understandable.

Generates human-readable explanations for:
- Why a tool was chosen
- Why a plan was approved/rejected  
- Why the loop is continuing/stopping
- What each decision means
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Explanation:
    """A single explanation entry."""
    explanation_id: str
    timestamp: float
    category: str  # "tool_choice", "decision", "gate", "memory", etc.
    subject: str
    rationale: str
    context: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.5
    
    def to_dict(self) -> dict:
        return {
            "explanation_id": self.explanation_id,
            "timestamp": self.timestamp,
            "category": self.category,
            "subject": self.subject,
            "rationale": self.rationale,
            "context": self.context,
            "confidence": self.confidence,
        }


class ExplainabilityEngine:
    """Generates explanations for agent behavior."""
    
    def __init__(self):
        self._explanations: List[Explanation] = []
        
    def explain_tool_choice(self, tool_name: str, arguments: Dict[str, Any],
                            alternatives: List[str], task_description: str) -> Explanation:
        """Explain why a specific tool was chosen."""
        rationale = self._generate_tool_rationale(tool_name, alternatives, task_description)
        
        exp = Explanation(
            explanation_id=f"exp_tool_{int(time.time() * 1000)}",
            timestamp=time.time(),
            category="tool_choice",
            subject=tool_name,
            rationale=rationale,
            context={
                "arguments": arguments,
                "alternatives_considered": alternatives,
            },
            confidence=0.7,
        )
        self._explanations.append(exp)
        return exp
    
    def explain_gate_decision(self, gate_name: str, triggered: bool,
                              current_state: Dict[str, Any]) -> Explanation:
        """Explain why a gate was triggered (or not)."""
        if triggered:
            rationale = f"Gate '{gate_name}' triggered because:"
            for key, value in current_state.items():
                rationale += f"\n- {key}: {value}"
        else:
            rationale = f"Gate '{gate_name}' not triggered - all conditions satisfied."
        
        exp = Explanation(
            explanation_id=f"exp_gate_{int(time.time() * 1000)}",
            timestamp=time.time(),
            category="gate",
            subject=gate_name,
            rationale=rationale,
            context=current_state,
            confidence=1.0,
        )
        self._explanations.append(exp)
        return exp
    
    def explain_memory_recall(self, query: str, results: List[str],
                              memory_type: str = "semantic") -> Explanation:
        """Explain what memory was recalled and why."""
        rationale = f"Recalled {len(results)} items from {memory_type} memory for query:\n{query}"
        if results:
            rationale += f"\n\nTop matches:\n" + "\n".join(f"- {r[:100]}..." for r in results[:3])
        
        exp = Explanation(
            explanation_id=f"exp_mem_{int(time.time() * 1000)}",
            timestamp=time.time(),
            category="memory",
            subject=memory_type,
            rationale=rationale,
            context={"query": query, "result_count": len(results)},
            confidence=0.6,
        )
        self._explanations.append(exp)
        return exp
    
    def explain_loop_progress(self, round_no: int, score: int, 
                               score_history: List[int]) -> Explanation:
        """Explain current loop progress."""
        trend = "improving" if len(score_history) >= 2 and score > score_history[-2] else \
                "declining" if len(score_history) >= 2 and score < score_history[-2] else "stable"
        
        rationale = (f"Round {round_no}: Score {score}/100 ({trend}). "
                    f"History: {score_history[-5:] if len(score_history) >= 5 else score_history}")
        
        exp = Explanation(
            explanation_id=f"exp_prog_{int(time.time() * 1000)}",
            timestamp=time.time(),
            category="progress",
            subject="loop",
            rationale=rationale,
            context={"round": round_no, "score": score, "history": score_history},
            confidence=1.0,
        )
        self._explanations.append(exp)
        return exp
    
    def _generate_tool_rationale(self, tool_name: str, alternatives: List[str],
                                  task: str) -> str:
        """Generate rationale for tool selection."""
        # Simple heuristic-based rationale
        tool_purposes = {
            "file_read": "Reading file contents to understand current state",
            "file_write": "Creating or overwriting a file with new content",
            "file_edit_replace": "Making targeted changes to existing code",
            "multi_edit": "Applying coordinated changes across multiple files",
            "grep": "Searching for patterns across the codebase",
            "find": "Locating files matching a glob pattern",
            "git": "Interacting with version control (diff, log, status)",
            "terminal": "Executing commands to run tests, build, install deps",
            "webfetch": "Fetching documentation or API references",
            "websearch": "Searching for solutions to known problems",
            "spawn_subagent": "Delegating independent sub-tasks",
        }
        
        purpose = tool_purposes.get(tool_name, f"Using tool: {tool_name}")
        
        return (
            f"I chose '{tool_name}' because: {purpose}\n\n"
            f"Task context: {task[:200]}...\n\n"
            f"Alternatives considered: {', '.join(alternatives[:3]) or 'none'}"
        )
    
    def get_context_for_prompt(self, max_explanations: int = 5) -> str:
        """Get recent explanations formatted for the LLM prompt."""
        if not self._explanations:
            return ""
        
        recent = sorted(self._explanations, key=lambda e: -e.timestamp)[:max_explanations]
        lines = ["## Recent Decisions"]
        for exp in recent:
            lines.append(f"\n### {exp.category}: {exp.subject}")
            lines.append(f"{exp.rationale}")
        
        return "\n".join(lines)
    
    def clear(self) -> None:
        """Clear all explanations."""
        self._explanations.clear()
    
    def export_debug_json(self, output_path: Optional[str] = None) -> str:
        """Export all explanations to JSON for debugging."""
        data = {
            "explanation_count": len(self._explanations),
            "explanations": [e.to_dict() for e in self._explanations],
        }
        
        json_str = __import__('json').dumps(data, indent=2, ensure_ascii=False)
        
        if output_path:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(json_str)
        
        return json_str
