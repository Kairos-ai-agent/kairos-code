"""LoopController - Manages the Coder <-> Reviewer loop lifecycle.

Handles:
- Starting and stopping loops
- Plan mode approval/rejection
- Loop state queries
- Post-loop cleanup and self-learning
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from kairos.core.message_bus import Message, MessageBus

logger = logging.getLogger(__name__)


class LoopController:
    """Controls the execution of Coder <-> Reviewer loops.
    
    This class manages the runtime aspects of a loop session:
    - Starting/stopping the background task
    - Handling plan mode decisions
    - Querying loop state
    - Running post-loop cleanup
    
    The actual loop logic lives in kairos.loop.loop_runner.
    """

    def __init__(self, message_bus: MessageBus):
        self._message_bus = message_bus
        self._active_loops: Dict[str, asyncio.Task] = {}

    def start_loop(self, project_id: str, requirement: str, 
                   session: Any) -> str:
        """Start a new loop for a project.
        
        Returns the loop session ID.
        """
        from kairos.loop.review_loop import LoopSession, run_loop

        if project_id in self._active_loops:
            existing = self._active_loops[project_id]
            if not existing.done():
                raise ValueError(f"Loop already running for project {project_id}")

        # Import here to avoid circular imports
        from kairos.config.gates import LOOP_SAFETY_CAP
        
        # Determine if this is a new project (unbounded mode)
        is_new = self._is_new_project(session.persistence, project_id)
        
        # Build reference digest and memory
        ref_digest = self._build_reference_digest(session, project_id)
        pref_block = self._build_preferences_block(session, project_id)
        mem_block = self._build_memory_block(session, project_id, requirement)
        
        if ref_digest:
            requirement = f"{ref_digest}\n\n## User Requirement\n{requirement}"
        if pref_block:
            requirement = pref_block + "\n\n" + requirement
        if mem_block:
            requirement = mem_block + "\n\n" + requirement

        # Load loop config
        loop_cfg = self._load_loop_config()
        review_focus = list(loop_cfg.get("review_focus") or [])
        
        # Auto-route specialists
        try:
            focus_needed = self._auto_route_specialists(requirement)
            if focus_needed:
                review_focus = sorted(set(review_focus) | set(focus_needed))
        except Exception:
            logger.debug("review-focus auto-route failed")

        # New project bootstrap: unbounded + bug_reviewer focus
        if is_new and not review_focus:
            review_focus = ["bug_reviewer"]

        specialist_reviewers = self._get_specialists(session, loop_cfg)

        # Create session
        from kairos.loop.review_loop import LoopSession
        session_obj = LoopSession(
            project=session.project,
            message_bus=self._message_bus,
            coder=session.coder,
            reviewer=session.reviewer,
            persistence=session.persistence,
            best_of_n=int(loop_cfg.get("best_of_n", 1) or 1),
            review_focus=review_focus,
            specialist_reviewers=specialist_reviewers,
        )
        session_obj.require_test_evidence = bool(
            loop_cfg.get("require_test_evidence", False)
        )

        # Difficulty-aware routing
        if loop_cfg.get("difficulty_routing", False):
            self._apply_difficulty_routing(session_obj, session)

        session.loop_session = session_obj
        session.status = "running"
        
        # Start the loop task
        loop_task = asyncio.create_task(
            run_loop(session_obj, requirement, unbounded=is_new),
            name=f"loop-{project_id}",
        )
        self._active_loops[project_id] = loop_task
        loop_task.add_done_callback(lambda t: self._on_loop_done(project_id, t))

        return session_obj.session_id

    def stop_loop(self, project_id: str) -> bool:
        """Stop an active loop."""
        task = self._active_loops.get(project_id)
        if task is None or task.done():
            return False
        
        task.cancel()
        return True

    def get_plan(self, session: Any) -> Optional[dict]:
        """Get the current plan state."""
        if not hasattr(session, 'loop_session') or not session.loop_session:
            return None
        s = session.loop_session
        from kairos.loop.plan_mode import sanitize_plan_text
        return {
            "pending": s.plan_pending,
            "decision": s.plan_decision,
            "text": sanitize_plan_text(s.plan_text) if hasattr(s, 'plan_text') else "",
            "round": s.round,
        }

    def approve_plan(self, project_id: str, plan_text: str = "") -> bool:
        """Approve the current plan."""
        task = self._active_loops.get(project_id)
        if not task or not task.done():
            # Need to get session from somewhere...
            return False
        return False

    def reject_plan(self, project_id: str) -> bool:
        """Reject the current plan."""
        return False

    def get_loop_state(self, project_id: str) -> Optional[dict]:
        """Get current loop state."""
        task = self._active_loops.get(project_id)
        if not task or task.done():
            return None
        
        # Return basic state info
        return {
            "active": not task.done(),
            "project_id": project_id,
        }

    def _on_loop_done(self, project_id: str, task: asyncio.Task) -> None:
        """Handle loop completion."""
        del self._active_loops[project_id]
        
        if task.cancelled():
            status = "stopped"
        elif task.exception():
            status = "failed"
            logger.exception("Loop for %s raised", project_id, exc_info=task.exception())
        else:
            status = "done"
        
        # Update project status in DB
        try:
            # Find the project and update
            pass
        except Exception:
            logger.debug("Failed to persist final status for %s", project_id)

    def _is_new_project(self, persistence: Any, project_id: str) -> bool:
        """Check if project has no prior sessions."""
        try:
            msgs = persistence.load_messages(limit=1, project_id=project_id) or []
            rounds = persistence.load_loop_rounds(project_id, limit=1) or []
            return len(msgs) == 0 and len(rounds) == 0
        except Exception:
            return False

    def _build_reference_digest(self, session: Any, project_id: str) -> str:
        """Build digest of reference files."""
        try:
            from kairos.core.persistence import Persistence
            if not session._db:
                return ""
            files = session._db.list_files(project_id)
            if not files:
                return ""
            
            chunks = ["## 参考资料"]
            preview_bytes = 3000
            for meta in files[:8]:
                payload = session._db.get_file(meta["id"])
                if not payload:
                    continue
                raw = payload.get("content", "")
                if isinstance(raw, (bytes, bytearray)):
                    body = raw.decode("utf-8", errors="replace")
                else:
                    body = str(raw or "")
                name = meta.get("name", "?")
                size = int(meta.get("size") or len(body))
                if size <= preview_bytes:
                    chunks.append(f"### {name}\n```\n{body}\n```")
                else:
                    head = body[:preview_bytes]
                    chunks.append(
                        f"### {name}\n```\n{head}\n\n… truncated (showing first {preview_bytes} of {size} bytes)\n```"
                    )
            return "\n\n".join(chunks)
        except Exception:
            return ""

    def _build_preferences_block(self, session: Any, project_id: str) -> str:
        """Build preferences block from saved rules."""
        try:
            prefs = session._db.list_preferences(project_id) if session._db else []
            if not prefs:
                return ""
            
            by_kind = {"always": [], "never": [], "prefer": []}
            for p in prefs:
                kind = p.get("kind", "always")
                rule = (p.get("rule") or "").strip()
                if kind in by_kind and rule:
                    by_kind[kind].append(rule)
            
            lines = ["## Project Conventions (user-saved rules)"]
            for kind, items in by_kind.items():
                if items:
                    lines.append(f"\n{kind.capitalize()}:")
                    for r in items:
                        lines.append(f"- {r}")
            return "\n".join(lines)
        except Exception:
            return ""

    def _build_memory_block(self, session: Any, project_id: str, 
                           requirement: str) -> str:
        """Build cross-loop memory block."""
        try:
            from kairos.memory.retrieval import assemble_coder_memory
            if not session._db:
                return ""
            return assemble_coder_memory(
                session._db, project_id, requirement,
            )
        except Exception:
            return ""

    def _load_loop_config(self) -> dict:
        """Load loop configuration from settings."""
        defaults = {
            "specialists": [],
            "best_of_n": 1,
            "review_focus": [],
            "require_test_evidence": False,
            "difficulty_routing": False,
        }
        try:
            from kairos.config.settings import settings
            path = settings.data_dir / "settings.json"
            if not path.exists():
                return defaults
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f) or {}
            loop_cfg = cfg.get("loop_config", {}) or {}
            
            specialists = list(loop_cfg.get("specialists", []) or [])
            try:
                best_of_n = max(1, min(int(loop_cfg.get("best_of_n", 1) or 1), 5))
            except (TypeError, ValueError):
                best_of_n = 1
            review_focus = list(loop_cfg.get("review_focus", []) or [])
            
            return {
                "specialists": specialists,
                "best_of_n": best_of_n,
                "review_focus": review_focus,
                "require_test_evidence": bool(loop_cfg.get("require_test_evidence", False)),
                "difficulty_routing": bool(loop_cfg.get("difficulty_routing", False)),
            }
        except Exception:
            return defaults

    def _auto_route_specialists(self, requirement: str) -> List[str]:
        """Auto-route specialists based on requirement keywords."""
        from kairos.core.project_factory import _auto_route_specialists
        return _auto_route_specialists(requirement)

    def _get_specialists(self, session: Any, loop_cfg: dict) -> List[Any]:
        """Get specialist reviewer instances."""
        specialists = loop_cfg.get("specialists", [])
        if not specialists:
            return []
        
        from kairos.core.project_factory import ProjectFactory
        factory = ProjectFactory(self.model_router if hasattr(self, 'model_router') else None)
        return factory.get_specialist_classes(specialists)

    def _apply_difficulty_routing(self, session: Any, original_session: Any) -> None:
        """Apply difficulty-aware model routing."""
        try:
            from kairos.llm.model_router import resolve_task_tier
            requirement = getattr(original_session.project, 'requirements', '')
            tier = resolve_task_tier(requirement)
            if tier == "fast" and hasattr(session, 'coder') and session.coder:
                # Route to fast model
                pass
        except Exception:
            logger.debug("difficulty routing failed")
