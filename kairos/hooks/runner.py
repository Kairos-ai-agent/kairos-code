"""Hook discovery and dispatch.

Loads user hooks from data/hooks/*.py on first use, caches the runner
for the process lifetime. Each hook file is exec'd in a fresh module
namespace so multiple hooks can coexist without symbol clashes.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class HookRunner:
    """Loads hooks from a directory and dispatches events to them."""

    def __init__(self, hooks_dir: Optional[Path] = None):
        # If hooks_dir is None, use <data_dir>/hooks relative to cwd.
        # We don't take a hard dep on settings so this is testable in
        # isolation.
        self.hooks_dir = hooks_dir
        self._loaded = False
        self._pre_tool_use: List[Callable] = []
        self._post_tool_use: List[Callable] = []
        self._loop_round: List[Callable] = []
        self._loop_completed: List[Callable] = []

    def _ensure_loaded(self):
        if self._loaded:
            return
        self._loaded = True
        if self.hooks_dir is None or not self.hooks_dir.exists():
            return
        for py in sorted(self.hooks_dir.glob("*.py")):
            if py.name.startswith("_"):
                continue
            try:
                spec = importlib.util.spec_from_file_location(
                    f"kairos_hook_{py.stem}", py
                )
                mod = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = mod
                spec.loader.exec_module(mod)  # type: ignore
            except Exception:
                logger.warning("Failed to load hook %s", py, exc_info=True)
                continue
            if hasattr(mod, "pre_tool_use"):
                self._pre_tool_use.append(mod.pre_tool_use)
            if hasattr(mod, "post_tool_use"):
                self._post_tool_use.append(mod.post_tool_use)
            if hasattr(mod, "loop_round"):
                self._loop_round.append(mod.loop_round)
            if hasattr(mod, "loop_completed"):
                self._loop_completed.append(mod.loop_completed)

    def pre_tool_use(self, tool_name: str, arguments: dict,
                     agent_id: str, project_id: str) -> dict:
        """Run pre-tool hooks. Return value replaces arguments if any
        hook returns non-None; later hooks see the rewritten args."""
        self._ensure_loaded()
        for hook in self._pre_tool_use:
            try:
                replaced = hook(tool_name, arguments, agent_id, project_id)
            except Exception:
                logger.warning("pre_tool_use hook raised", exc_info=True)
                continue
            if replaced is not None and isinstance(replaced, dict):
                arguments = replaced
        return arguments

    def post_tool_use(self, tool_name: str, arguments: dict, result,
                      agent_id: str, project_id: str) -> None:
        self._ensure_loaded()
        for hook in self._post_tool_use:
            try:
                hook(tool_name, arguments, result, agent_id, project_id)
            except Exception:
                logger.warning("post_tool_use hook raised", exc_info=True)

    def loop_round(self, round_no: int, coder_summary: str,
                   review: dict, project_id: str) -> None:
        self._ensure_loaded()
        for hook in self._loop_round:
            try:
                hook(round_no, coder_summary, review, project_id)
            except Exception:
                logger.warning("loop_round hook raised", exc_info=True)

    def loop_completed(self, project_id: str, final_score: int,
                        total_rounds: int) -> None:
        self._ensure_loaded()
        for hook in self._loop_completed:
            try:
                hook(project_id, final_score, total_rounds)
            except Exception:
                logger.warning("loop_completed hook raised", exc_info=True)


_runner: Optional[HookRunner] = None


def get_runner() -> HookRunner:
    """Process-global hook runner. Lazily created with the default
    hooks directory."""
    global _runner
    if _runner is None:
        from kairos.config.settings import settings
        _runner = HookRunner(hooks_dir=settings.data_dir / "hooks")
    return _runner