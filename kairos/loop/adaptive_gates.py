"""Adaptive Gates - Thresholds that learn from project history.

Instead of using fixed constants, these gates adjust based on:
- Project complexity (derived from requirement size, file count, etc.)
- Historical performance (average scores, typical round counts)
- Task type (architecture vs bugfix vs feature)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from kairos.config.gates import (
    APPROVE_SCORE_THRESHOLD,
    LOOP_SAFETY_CAP,
    NO_PROGRESS_LIMIT,
    STAGNATION_WINDOW,
    STAGNATION_TOLERANCE,
    INFRA_FAILURE_LIMIT,
    COST_TOKEN_CAP,
    COST_TIME_CAP_S,
)

logger = logging.getLogger(__name__)


@dataclass
class ProjectProfile:
    """Historical profile for a project."""
    project_id: str
    total_rounds: int = 0
    avg_score: float = 50.0
    score_stddev: float = 15.0
    typical_rounds_to_approve: int = 5
    common_failure_modes: Dict[str, int] = field(default_factory=dict)
    last_updated: float = 0.0


class AdaptiveGates:
    """Gates that adapt based on project history and current context."""
    
    def __init__(self, persistence: Any):
        self._persistence = persistence
        self._profiles: Dict[str, ProjectProfile] = {}
        
    def get_profile(self, project_id: str) -> ProjectProfile:
        """Get or create a project profile."""
        if project_id not in self._profiles:
            self._profiles[project_id] = self._load_profile(project_id)
        return self._profiles[project_id]
    
    def _load_profile(self, project_id: str) -> ProjectProfile:
        """Load historical profile from persistence."""
        try:
            rounds = self._persistence.load_loop_rounds(project_id, limit=100)
            if not rounds:
                return ProjectProfile(project_id=project_id)
            
            scores = [r.get("score", 50) for r in rounds if r.get("score")]
            rounds_to_approve = sum(1 for r in rounds if r.get("approve"))
            
            # Calculate statistics
            avg_score = sum(scores) / len(scores) if scores else 50.0
            variance = sum((s - avg_score) ** 2 for s in scores) / len(scores) if scores else 225.0
            stddev = variance ** 0.5
            
            # Count failure modes
            failure_modes: Dict[str, int] = {}
            for r in rounds:
                mode = r.get("_failure_mode", "none")
                failure_modes[mode] = failure_modes.get(mode, 0) + 1
            
            return ProjectProfile(
                project_id=project_id,
                total_rounds=len(rounds),
                avg_score=avg_score,
                stddev=stddev,
                typical_rounds_to_approve=max(1, rounds_to_approve),
                common_failure_modes=failure_modes,
                last_updated=self._persistence.get_last_activity(project_id),
            )
        except Exception as e:
            logger.debug("Failed to load profile for %s: %s", project_id, e)
            return ProjectProfile(project_id=project_id)
    
    def compute_dynamic_thresholds(self, project_id: str, 
                                   requirement: str = "") -> Dict[str, Any]:
        """Compute adaptive thresholds for a project.
        
        Returns thresholds adjusted based on:
        - Project complexity
        - Historical performance
        - Current task characteristics
        """
        profile = self.get_profile(project_id)
        
        # Base thresholds
        thresholds = {
            "approve_score": APPROVE_SCORE_THRESHOLD,
            "safety_cap": LOOP_SAFETY_CAP,
            "no_progress_limit": NO_PROGRESS_LIMIT,
            "stagnation_window": STAGNATION_WINDOW,
            "stagnation_tolerance": STAGNATION_TOLERANCE,
            "infra_failure_limit": INFRA_FAILURE_LIMIT,
            "token_cap": COST_TOKEN_CAP,
            "time_cap_s": COST_TIME_CAP_S,
        }
        
        # Adjust safety cap based on project history
        if profile.total_rounds > 0:
            # Projects that typically need more rounds get more headroom
            history_ratio = profile.typical_rounds_to_approve / LOOP_SAFETY_CAP
            adjusted_cap = max(LOOP_SAFETY_CAP, int(LOOP_SAFETY_CAP * (1 + history_ratio)))
            thresholds["safety_cap"] = min(adjusted_cap, LOOP_SAFETY_CAP * 4)
        
        # Adjust approve score based on historical average
        if profile.avg_score > 80:
            # High-performing project - raise bar slightly
            thresholds["approve_score"] = min(95, APPROVE_SCORE_THRESHOLD + 10)
        elif profile.avg_score < 50:
            # Low-performing project - lower bar to avoid giving up too early
            thresholds["approve_score"] = max(60, APPROVE_SCORE_THRESHOLD - 15)
        
        # Adjust for requirement complexity
        req_length = len(requirement)
        if req_length > 5000:
            # Very long requirements get more time and tokens
            thresholds["time_cap_s"] *= 2
            thresholds["token_cap"] *= 2
        
        # Adjust no-progress limit based on failure patterns
        infra_failures = profile.common_failure_modes.get("infra_fail", 0)
        if infra_failures > profile.total_rounds * 0.3:
            # Project prone to infra failures - be more tolerant
            thresholds["no_progress_limit"] *= 2
            thresholds["infra_failure_limit"] += 2
        
        # Clamp all values
        thresholds["approve_score"] = max(60, min(95, thresholds["approve_score"]))
        thresholds["safety_cap"] = max(20, min(200, thresholds["safety_cap"]))
        thresholds["no_progress_limit"] = max(3, min(10, thresholds["no_progress_limit"]))
        
        return thresholds
    
    def update_profile(self, project_id: str, round_result: Dict[str, Any]) -> None:
        """Update project profile after a round."""
        profile = self.get_profile(project_id)
        
        score = round_result.get("score", profile.avg_score)
        approved = round_result.get("approve", False)
        failure_mode = round_result.get("_failure_mode", "none")
        
        # Update rolling average
        profile.total_rounds += 1
        alpha = 0.1  # Exponential moving average
        profile.avg_score = alpha * score + (1 - alpha) * profile.avg_score
        
        # Update failure mode counts
        if failure_mode != "none":
            profile.common_failure_modes[failure_mode] = \
                profile.common_failure_modes.get(failure_mode, 0) + 1
        
        profile.last_updated = __import__('time').time()
        
        # Persist if enough rounds
        if profile.total_rounds % 10 == 0:
            self._persist_profile(profile)
    
    def should_relax_gates(self, project_id: str, current_score: int) -> bool:
        """Check if gates should be relaxed for this project."""
        profile = self.get_profile(project_id)
        
        # Relax if project is struggling (low average score)
        if profile.avg_score < 50 and current_score < 70:
            return True
        
        # Relax if project has many successful iterations
        if profile.total_rounds > 20 and profile.avg_score > 70:
            return False  # Tighten - project is doing well
        
        return False
    
    def get_relaxation_factor(self, project_id: str) -> float:
        """Get factor to relax thresholds by (1.0 = no relaxation)."""
        if self.should_relax_gates(project_id, 0):
            return 1.5
        return 1.0
    
    def _persist_profile(self, profile: ProjectProfile) -> None:
        """Persist profile to database."""
        try:
            from kairos.core.persistence import Persistence
            if isinstance(self._persistence, Persistence):
                self._persistence.store_project_profile(profile.to_dict())
        except Exception:
            pass
    
    def clear_profile(self, project_id: str) -> None:
        """Clear cached profile."""
        if project_id in self._profiles:
            del self._profiles[project_id]


# Backward compatibility function
def get_adaptive_thresholds(persistence: Any, project_id: str,
                            requirement: str = "") -> Dict[str, Any]:
    """Convenience function for backward compatibility."""
    adapter = AdaptiveGates(persistence)
    return adapter.compute_dynamic_thresholds(project_id, requirement)
