"""Tool Recommendation System - Intelligent tool selection based on task context.

Reduces LLM decision noise by:
- Analyzing task description for relevant tools
- Learning from historical successful patterns
- Filtering irrelevant tools to reduce context window
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class ToolRelevanceScore:
    """Relevance score for a tool given a task."""
    tool_name: str
    score: float  # 0.0 to 1.0
    reason: str
    category: str = "general"


class ToolRecommendationEngine:
    """Recommends relevant tools based on task context."""
    
    # Keyword-to-tool mappings (expandable)
    KEYWORD_RULES = {
        # File operations
        "read": ["file_read"],
        "write": ["file_write", "multi_edit"],
        "create": ["file_write"],
        "edit": ["file_edit_replace", "multi_edit"],
        "modify": ["file_edit_replace", "multi_edit"],
        "delete": ["terminal"],
        
        # Search and analysis
        "search": ["grep", "find"],
        "find": ["find"],
        "locate": ["find"],
        "grep": ["grep"],
        "regex": ["grep"],
        "pattern": ["grep"],
        
        # Git operations
        "commit": ["git"],
        "push": ["git"],
        "diff": ["git"],
        "status": ["git"],
        "branch": ["git"],
        "log": ["git"],
        "revert": ["git"],
        "undo": ["git"],
        
        # Terminal/execution
        "run": ["terminal"],
        "execute": ["terminal"],
        "install": ["terminal"],
        "test": ["terminal"],
        "build": ["terminal"],
        "compile": ["terminal"],
        "npm": ["terminal"],
        "pip": ["terminal"],
        "make": ["terminal"],
        "python": ["terminal"],
        "node": ["terminal"],
        
        # Web research
        "research": ["webfetch", "websearch"],
        "documentation": ["webfetch", "websearch"],
        "api": ["webfetch", "websearch"],
        "error": ["websearch"],
        
        # Subagents
        "parallel": ["spawn_subagent"],
        "separate": ["spawn_subagent"],
        "independent": ["spawn_subagent"],
    }
    
    # Tool categories for filtering
    TOOL_CATEGORIES = {
        "file_read": "io",
        "file_write": "io",
        "file_edit_replace": "io",
        "multi_edit": "io",
        "grep": "search",
        "find": "search",
        "git": "version_control",
        "terminal": "execution",
        "webfetch": "web",
        "websearch": "web",
        "spawn_subagent": "coordination",
    }
    
    def __init__(self, all_tools: Optional[List[str]] = None):
        self._all_tools = set(all_tools or [])
        self._usage_history: Dict[str, Dict[str, int]] = {}  # project_id -> {tool: count}
        
    def recommend_tools(self, task_description: str, 
                        project_id: str,
                        max_tools: int = 8) -> List[ToolRelevanceScore]:
        """Recommend relevant tools for a task.
        
        Args:
            task_description: Natural language description of the task
            project_id: Project identifier for personalized recommendations
            max_tools: Maximum number of tools to return
            
        Returns:
            Ranked list of (tool_name, score, reason) tuples
        """
        if not task_description:
            return []
        
        task_lower = task_description.lower()
        scores: Dict[str, ToolRelevanceScore] = {}
        
        # Keyword matching
        for keyword, tools in self.KEYWORD_RULES.items():
            if keyword in task_lower:
                for tool in tools:
                    if tool in self._all_tools:
                        current = scores.get(tool)
                        if current is None or current.score < 0.5:
                            scores[tool] = ToolRelevanceScore(
                                tool_name=tool,
                                score=0.5,
                                reason=f"keyword match: '{keyword}'",
                                category=self.TOOL_CATEGORIES.get(tool, "general"),
                            )
        
        # Context-aware boosting from usage history
        if project_id in self._usage_history:
            recent_usage = self._usage_history[project_id]
            for tool, count in recent_usage.items():
                if tool in scores:
                    # Boost tools used successfully recently
                    boost = min(count * 0.05, 0.3)
                    scores[tool].score += boost
                    scores[tool].reason += f" (history: +{boost:.2f})"
        
        # Convert to sorted list
        result = sorted(
            scores.values(),
            key=lambda x: -x.score,
        )[:max_tools]
        
        return result
    
    def record_tool_usage(self, project_id: str, tool_name: str, 
                          success: bool) -> None:
        """Record tool usage for learning."""
        if project_id not in self._usage_history:
            self._usage_history[project_id] = {}
        
        if tool_name not in self._usage_history[project_id]:
            self._usage_history[project_id][tool_name] = 0
        
        # Only count successes
        if success:
            self._usage_history[project_id][tool_name] += 1
    
    def filter_irrelevant_tools(self, available_tools: List[str],
                                 task_description: str) -> List[str]:
        """Filter out clearly irrelevant tools to reduce context."""
        if len(available_tools) <= 5:
            return available_tools
        
        recommendations = self.recommend_tools(task_description, "", max_tools=6)
        recommended_names = {r.tool_name for r in recommendations}
        
        # Always keep essential tools
        essentials = {"file_read", "file_write"}
        return [t for t in available_tools 
                if t in recommended_names or t in essentials]
    
    def get_tool_groups(self, task_description: str) -> Dict[str, List[str]]:
        """Group tools by category for organized presentation."""
        recommendations = self.recommend_tools(task_description, "", max_tools=20)
        
        groups: Dict[str, List[str]] = {}
        for rec in recommendations:
            cat = rec.category
            if cat not in groups:
                groups[cat] = []
            groups[cat].append(rec.tool_name)
        
        return groups
    
    def update_from_history(self, project_id: str, 
                            usage_data: Dict[str, int]) -> None:
        """Update usage history from a completed session."""
        if project_id not in self._usage_history:
            self._usage_history[project_id] = {}
        
        for tool, count in usage_data.items():
            self._usage_history[project_id][tool] = \
                self._usage_history[project_id].get(tool, 0) + count


# Backward compatibility
def get_recommended_tools(task: str, available: List[str]) -> List[str]:
    """Simple function for backward compatibility."""
    engine = ToolRecommendationEngine(set(available))
    recs = engine.recommend_tools(task, "default_project")
    return [r.tool_name for r in recs]
