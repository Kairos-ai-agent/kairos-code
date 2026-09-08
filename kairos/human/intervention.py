"""Human Intervention Interface - Allow users to guide agents mid-execution.

Provides mechanisms for:
- Real-time chat during loop execution
- Mid-loop requirement modification
- Targeted guidance ("focus on security", "simplify this approach")
- Pausing and resuming with new instructions
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class InterventionType(str, Enum):
    """Types of human interventions."""
    CHAT = "chat"                           # Real-time message
    REQUIREMENT_CHANGE = "requirement_change"  # Modify task
    FOCUS_DIRECTIVE = "focus_directive"     # "Focus on X"
    PAUSE = "pause"                         # Pause execution
    RESUME = "resume"                       # Resume with instructions
    SKIP_ROUND = "skip_round"               # Skip current round
    RETRY_FROM = "retry_from"              # Retry from specific point
    
    @property
    def blocking(self) -> bool:
        """Whether this intervention blocks execution."""
        return self in (InterventionType.PAUSE, InterventionType.RESUME)


@dataclass
class InterventionRequest:
    """A request from a human user."""
    request_id: str
    intervention_type: InterventionType
    content: str
    timestamp: float
    project_id: str
    session_id: str
    priority: int = 0  # Higher = more important
    
    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "intervention_type": self.intervention_type.value,
            "content": self.content[:1000],  # Truncate for storage
            "timestamp": self.timestamp,
            "project_id": self.project_id,
            "session_id": self.session_id,
            "priority": self.priority,
        }


class HumanInterventionManager:
    """Manages human-in-the-loop interventions during agent execution."""
    
    def __init__(self, message_bus: Any = None):
        self._bus = message_bus
        self._active_interventions: Dict[str, List[InterventionRequest]] = {}
        self._interruption_event: Optional[asyncio.Event] = None
        
    def register_project(self, project_id: str) -> None:
        """Register a project for intervention tracking."""
        if project_id not in self._active_interventions:
            self._active_interventions[project_id] = []
    
    def post_intervention(self, project_id: str, 
                          intervention_type: InterventionType,
                          content: str,
                          priority: int = 0) -> str:
        """Post an intervention request for a project."""
        request_id = f"int_{int(time.time() * 1000)}"
        
        # Need session_id from somewhere... using placeholder
        request = InterventionRequest(
            request_id=request_id,
            intervention_type=intervention_type,
            content=content,
            timestamp=time.time(),
            project_id=project_id,
            session_id="current_session",  # Will be set by caller
            priority=priority,
        )
        
        self._active_interventions.setdefault(project_id, []).append(request)
        
        # Emit event for UI
        if self._bus:
            asyncio.create_task(self._bus.publish({
                "topic": "human.intervention_posted",
                "content": request.to_dict(),
                "sender": "human",
            }))
        
        return request_id
    
    def get_pending_interventions(self, project_id: str) -> List[InterventionRequest]:
        """Get all pending interventions for a project."""
        return self._active_interventions.get(project_id, [])
    
    def consume_next_intervention(self, project_id: str,
                                  timeout: float = 60.0) -> Optional[InterventionRequest]:
        """Block until next intervention arrives or timeout."""
        events = self._active_interventions.get(project_id, [])
        if not events:
            return None
        
        # Take highest priority intervention
        events.sort(key=lambda x: -x.priority)
        return events.pop(0)
    
    def should_pause_execution(self, project_id: str) -> bool:
        """Check if execution should pause for human input."""
        interventions = self.get_pending_interventions(project_id)
        return any(i.intervention_type == InterventionType.PAUSE 
                   for i in interventions)
    
    def add_requirement_hint(self, project_id: str, hint: str) -> None:
        """Add a focus directive to the current requirement."""
        self.post_intervention(
            project_id,
            InterventionType.FOCUS_DIRECTIVE,
            hint,
            priority=10,
        )
    
    def modify_requirement(self, project_id: str, new_requirement: str) -> None:
        """Modify the project requirement mid-loop."""
        self.post_intervention(
            project_id,
            InterventionType.REQUIREMENT_CHANGE,
            new_requirement,
            priority=100,  # Highest priority
        )
    
    def clear_interventions(self, project_id: str) -> None:
        """Clear all pending interventions."""
        self._active_interventions[project_id] = []
    
    async def wait_for_input(self, project_id: str, 
                             timeout: float = 300.0) -> Optional[str]:
        """Wait for human input with timeout."""
        start = time.time()
        
        while time.time() - start < timeout:
            interventions = self.get_pending_interventions(project_id)
            if interventions:
                # Get highest priority non-blocking intervention
                for intro in sorted(interventions, key=lambda x: -x.priority):
                    if not intro.intervention_type.blocking:
                        return intro.content
            
            await asyncio.sleep(0.1)
        
        return None  # Timeout


# Convenience functions
def post_chat_message(message_bus: Any, project_id: str, 
                      message: str, sender: str) -> None:
    """Post a chat message to the project channel."""
    if message_bus:
        asyncio.create_task(message_bus.publish({
            "topic": f"project.{project_id}.chat",
            "content": message,
            "sender": sender,
            "timestamp": time.time(),
        }))


def is_blocked_for_human(project_id: str, manager: HumanInterventionManager) -> bool:
    """Check if agent is blocked waiting for human."""
    return manager.should_pause_execution(project_id)
