"""Multi-Objective Optimization - Balance quality, speed, and safety.

Instead of single-objective optimization (maximize Reviewer score),
this system optimizes across multiple dimensions:
- Correctness: Functional correctness (primary)
- Design: Code quality, architecture  
- Security: Vulnerability assessment
- Performance: Runtime efficiency
- Cost: Token/time consumption
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ObjectiveWeights:
    """Relative importance of each objective."""
    correctness: float = 0.40
    design: float = 0.25
    security: float = 0.15
    performance: float = 0.10
    cost_efficiency: float = 0.10
    
    def normalize(self) -> None:
        """Ensure weights sum to 1.0."""
        total = sum(vars(self).values())
        if total > 0:
            for key in vars(self):
                setattr(self, key, getattr(self, key) / total)


@dataclass
class ObjectiveScores:
    """Scores for each objective on a solution."""
    correctness: float = 0.0
    design: float = 0.0
    security: float = 0.0
    performance: float = 0.0
    cost_efficiency: float = 1.0  # Lower cost = higher score
    weighted_total: float = 0.0
    
    def compute_weighted(self, weights: ObjectiveWeights) -> float:
        """Compute weighted sum."""
        self.weighted_total = (
            self.correctness * weights.correctness +
            self.design * weights.design +
            self.security * weights.security +
            self.performance * weights.performance +
            self.cost_efficiency * weights.cost_efficiency
        )
        return self.weighted_total


class ParetoOptimizer:
    """Multi-objective optimization using Pareto dominance."""
    
    def __init__(self, weights: Optional[ObjectiveWeights] = None):
        self.weights = weights or ObjectiveWeights()
        self._solutions: List[Tuple[ObjectiveScores, Any]] = []
        
    def add_solution(self, scores: ObjectiveScores, solution: Any) -> None:
        """Add a candidate solution."""
        scores.compute_weighted(self.weights)
        self._solutions.append((scores, solution))
    
    def get_pareto_front(self) -> List[Tuple[ObjectiveScores, Any]]:
        """Return non-dominated solutions (Pareto front)."""
        if not self._solutions:
            return []
        
        front = []
        for i, (scores_i, sol_i) in enumerate(self._solutions):
            is_dominated = False
            for j, (scores_j, sol_j) in enumerate(self._solutions):
                if i == j:
                    continue
                # Check if j dominates i
                if all(getattr(scores_j, dim) >= getattr(scores_i, dim) 
                       for dim in ["correctness", "design", "security", "performance"]):
                    if any(getattr(scores_j, dim) > getattr(scores_i, dim)
                           for dim in ["correctness", "design", "security", "performance"]):
                        is_dominated = True
                        break
            if not is_dominated:
                front.append((scores_i, sol_i))
        
        return front
    
    def get_best_by_weighted(self) -> Optional[Tuple[ObjectiveScores, Any]]:
        """Get the solution with highest weighted score."""
        if not self._solutions:
            return None
        return max(self._solutions, key=lambda x: x[0].weighted_total)
    
    def clear(self) -> None:
        """Clear all solutions."""
        self._solutions.clear()


def evaluate_objectives(review: Dict[str, Any], 
                        token_count: int = 0,
                        time_seconds: float = 0.0) -> ObjectiveScores:
    """Extract multi-dimensional scores from a Reviewer verdict."""
    scores = ObjectiveScores()
    
    # Correctness: primary score from reviewer
    scores.correctness = review.get("score", 0) / 100.0
    
    # Design: based on design-related issues
    design_issues = [
        i for i in review.get("issues", [])
        if i.get("category") in ("design", "architecture", "code_smell")
    ]
    scores.design = max(0, 1 - len(design_issues) * 0.1)
    
    # Security: based on security-related issues
    security_issues = [
        i for i in review.get("issues", [])
        if i.get("category") in ("security", "vulnerability")
    ]
    critical_security = [
        i for i in security_issues
        if i.get("severity") == "CRITICAL"
    ]
    if critical_security:
        scores.security = max(0, 0.5 - len(critical_security) * 0.2)
    elif security_issues:
        scores.security = max(0, 0.8 - len(security_issues) * 0.1)
    else:
        scores.security = 1.0
    
    # Performance: estimate based on complexity hints
    complexity = len(review.get("issues", []))
    scores.performance = max(0.5, 1.0 - complexity * 0.02)
    
    # Cost efficiency: lower tokens/time = higher score
    token_score = max(0, 1 - (token_count / 200000))
    time_score = max(0, 1 - (time_seconds / 1800))  # 30 min
    scores.cost_efficiency = (token_score + time_score) / 2
    
    return scores


class MultiObjectiveLoopController:
    """Enhanced loop controller with multi-objective optimization."""
    
    def __init__(self, optimizer: Optional[ParetoOptimizer] = None,
                 weights: Optional[ObjectiveWeights] = None):
        self.optimizer = optimizer or ParetoOptimizer(weights)
        self._round_history: List[Dict[str, Any]] = []
        
    def evaluate_round(self, round_result: Dict[str, Any],
                       token_count: int = 0,
                       elapsed_time: float = 0.0) -> ObjectiveScores:
        """Evaluate a single round across all objectives."""
        review = round_result.get("review", {})
        scores = evaluate_objectives(review, token_count, elapsed_time)
        self._round_history.append({
            "round": round_result.get("round", 0),
            "scores": scores,
            "approved": review.get("approve", False),
        })
        return scores
    
    def should_continue_loop(self, current_scores: ObjectiveScores) -> bool:
        """Decide whether to continue the loop based on objectives."""
        # If any critical objective is very low, stop
        if current_scores.security < 0.3:
            logger.warning("Security score too low, stopping loop")
            return False
        
        # If correctness is approaching threshold, consider stopping
        if current_scores.correctness >= 0.75:
            if (current_scores.design < 0.5 or 
                current_scores.security < 0.7):
                return True  # Continue to improve other dimensions
            return False  # Good enough
        
        return True
    
    def get_improvement_suggestion(self, scores: ObjectiveScores) -> str:
        """Generate suggestion for improvement based on weakest objective."""
        dims = {
            "correctness": scores.correctness,
            "design": scores.design,
            "security": scores.security,
            "performance": scores.performance,
        }
        weakest = min(dims, key=dims.get)
        
        suggestions = {
            "correctness": "Focus on fixing functional issues and test failures.",
            "design": "Improve code structure, naming, and architectural patterns.",
            "security": "Address security vulnerabilities, input validation, and access controls.",
            "performance": "Optimize algorithmic complexity and resource usage.",
        }
        return suggestions.get(weakest, "Review all aspects of the implementation.")
    
    def generate_report(self) -> Dict[str, Any]:
        """Generate a multi-objective report."""
        if not self._round_history:
            return {"status": "no_data"}
        
        latest = self._round_history[-1]["scores"]
        return {
            "final_scores": {
                k: round(v, 2) for k, v in {
                    "correctness": latest.correctness,
                    "design": latest.design,
                    "security": latest.security,
                    "performance": latest.performance,
                    "cost_efficiency": latest.cost_efficiency,
                }.items()
            },
            "weighted_total": round(latest.weighted_total, 2),
            "rounds_completed": len(self._round_history),
            "approved": self._round_history[-1].get("approved", False),
        }
