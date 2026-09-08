"""MemoryManager - Manages cross-loop memory and self-learning.

Handles:
- Building memory blocks for agent prompts
- Indexing loop rounds for retrieval
- Storing and retrieving working fixes
- Promoting repeated issues to preferences
- Recording global insights
- Post-loop consolidation and reflection
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from kairos.core.message_bus import Message, MessageBus

logger = logging.getLogger(__name__)


class MemoryManager:
    """Manages project memory across loop sessions.
    
    This manager handles the "learning" aspect of Kairos - persisting
    knowledge from past loops so future sessions benefit from experience.
    """

    def __init__(self, db: Any, message_bus: Optional[MessageBus] = None):
        self._db = db
        self._message_bus = message_bus or MessageBus()

    def build_memory_block(self, project_id: str, requirement: str,
                           last_failure_signature: Optional[str] = None,
                           last_reviewer_comments: Optional[List[dict]] = None) -> str:
        """Build a prompt-friendly memory block for the Coder.
        
        Combines notes, skills, working fixes, FTS-ranked history,
        reviewer comments, ask history, and global KB insights.
        """
        try:
            from kairos.memory.retrieval import assemble_coder_memory
            return assemble_coder_memory(
                self._db, project_id, requirement,
                last_failure_signature=last_failure_signature,
                last_reviewer_comments=last_reviewer_comments,
            )
        except Exception:
            logger.debug("assemble_coder_memory failed", exc_info=True)
            return ""

    def index_loop_round(self, project_id: str, session_id: str, round_no: int,
                         coder_summary: str = "", review_summary: str = "",
                         issues_text: str = "") -> None:
        """Index a loop round into the FTS search index."""
        try:
            from kairos.core.persistence import Persistence
            if isinstance(self._db, Persistence):
                self._db.index_loop_round(
                    project_id, session_id, round_no,
                    coder_summary=coder_summary,
                    review_summary=review_summary,
                    issues_text=issues_text,
                )
        except Exception:
            logger.debug("index_loop_round failed", exc_info=True)

    def save_review_comments(self, project_id: str, round_no: int,
                             comments: List[dict]) -> None:
        """Save Reviewer comments for a round."""
        try:
            from kairos.review.comments import verdict_to_comments
            # Comments are already in the right format
            pass
        except Exception:
            logger.debug("save_review_comments failed", exc_info=True)

    def maybe_record_working_fix(self, prior_review: Optional[dict],
                                 current_review: dict,
                                 coder_result: str) -> bool:
        """Record a working fix pattern on fail->pass transition."""
        try:
            from kairos.memory.growth import maybe_record_working_fix
            if not prior_review or not current_review.get("approve"):
                return False
            # Only record on transitions from reject to approve
            if prior_review.get("approve"):
                return False
            # Check if there's actually a meaningful change
            prior_issues = set(
                (i.get("file"), i.get("description", "")[:50])
                for i in prior_review.get("issues") or []
            )
            curr_issues = set(
                (i.get("file"), i.get("description", "")[:50])
                for i in current_review.get("issues") or []
            )
            # If all prior issues are gone, we have a working fix
            if prior_issues and not prior_issues.intersection(curr_issues):
                maybe_record_working_fix(
                    self._db, 
                    getattr(self, '_current_project_id', ''),
                    prior_review, 
                    current_review, 
                    coder_result,
                )
                return True
        except Exception:
            logger.debug("maybe_record_working_fix failed", exc_info=True)
        return False

    def auto_promote_failure_to_preference(self, issue: dict,
                                           consecutive_count: int = 3,
                                           threshold: int = 3,
                                           confidence: float = 0.5) -> None:
        """Promote repeated issues to project preferences."""
        try:
            from kairos.memory.growth import auto_promote_failure_to_preference
            auto_promote_failure_to_preference(
                self._db,
                getattr(self, '_current_project_id', ''),
                issue,
                consecutive_count=consecutive_count,
                threshold=threshold,
                confidence=confidence,
            )
        except Exception:
            logger.debug("auto_promote failed", exc_info=True)

    def record_global_insights(self, project_id: str, review: dict) -> None:
        """Promote CRITICAL/MAJOR issues into global KB."""
        try:
            from kairos.memory.growth import record_global_insights_from_review
            record_global_insights_from_review(self._db, project_id, review)
        except Exception:
            logger.debug("global insights failed", exc_info=True)

    def consolidate_project(self, project_id: str, 
                            rounds: List[dict]) -> Dict[str, Any]:
        """Run post-loop consolidation.
        
        Cheap, no-LLM pass that:
        - Re-derives advisory notes
        - Promotes repeated failures to preferences
        - Flags ambiguous signatures
        
        Returns summary with promoted preference count.
        """
        try:
            from kairos.memory.growth import consolidate_project
            return consolidate_project(self._db, project_id, rounds)
        except Exception:
            logger.debug("consolidate_project failed", exc_info=True)
            return {"promoted_preferences": 0}

    def schedule_reflection(self, project_id: str, rounds: List[dict]) -> None:
        """Schedule LLM-driven self-reflection in background.
        
        Only runs when:
        - At least 5 rounds happened
        - At least one round was approved
        - Previous reflection is >30 min old
        """
        approved = [r for r in rounds if r.get("approve")]
        if len(rounds) < 5 or not approved:
            return
        
        try:
            import asyncio
            from kairos.learning.reflect import maybe_run_reflection
            
            async def _run():
                await maybe_run_reflection(self._db, project_id, rounds)
            
            asyncio.create_task(_run())
        except Exception:
            logger.debug("reflection scheduling failed", exc_info=True)

    def load_history_digest(self, project_id: str) -> str:
        """Load cross-loop history digest for a project."""
        try:
            from kairos.loop.cross_loop import load_history_digest
            return load_history_digest(self._db, project_id)
        except Exception:
            return ""

    def get_working_fixes(self, project_id: str, limit: int = 5) -> List[dict]:
        """Get recent working fixes for a project."""
        try:
            rows = self._db.conn.execute(
                "SELECT issue, fix, severity, created_at "
                "FROM working_fixes WHERE project_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
            return [
                {
                    "issue": row[0],
                    "fix": row[1],
                    "severity": row[2],
                    "created_at": row[3],
                }
                for row in rows
            ]
        except Exception:
            return []

    def add_preference(self, project_id: str, kind: str, rule: str) -> int:
        """Add a project preference rule."""
        try:
            return self._db.add_preference(project_id, kind, rule)
        except Exception:
            return 0

    def list_preferences(self, project_id: str) -> List[dict]:
        """List all preferences for a project."""
        try:
            return self._db.list_preferences(project_id)
        except Exception:
            return []


# ============================================================================
# Convenience functions for backward compatibility
# ============================================================================

def build_memory_block(db: Any, project_id: str, requirement: str,
                       **kwargs) -> str:
    """Backward-compatible wrapper for MemoryManager.build_memory_block."""
    mgr = MemoryManager(db)
    return mgr.build_memory_block(project_id, requirement, **kwargs)
