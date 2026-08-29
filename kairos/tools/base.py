"""Base Tool class for agent tools."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

class ToolResult(BaseModel):
    """Result of a tool execution."""

    success: bool
    output: str
    error: Optional[str] = None
    metadata: dict = {}

class BaseTool(ABC):
    """Abstract base class for all agent tools."""

    name: str = "base_tool"
    description: str = "Base tool"
    # Tools can opt into per-round result caching by reading
    # self._cache. Wired up by the orchestrator at agent
    # construction time (see kairos.tools.cache.get_cache).
    _cache = None

    def __init__(self, allowed_root: str | Path = "."):
        self._allowed_root = Path(allowed_root).resolve()
        # R38.6 §30: optional auto-checkpointer. Tools that write
        # files call ``self._checkpointer.before_write(path)``
        # right before the write. The checkpointer is wired by
        # the orchestrator at agent construction time. Tools
        # that don't write files leave it ``None``.
        self._checkpointer = None

    def _resolve_safe(self, path: str) -> Path:
        """Resolve a path safely within the allowed root directory.

        Strips redundant workspace prefix (e.g. 'workspace/<id>/app.py' -> 'app.py').
        """
        root_name = self._allowed_root.name
        if path.startswith(root_name + "/") or path.startswith(root_name + "\\"):
            path = path[len(root_name) + 1:]
        target = Path(path)
        if not target.is_absolute():
            target = (self._allowed_root / path).resolve()
        else:
            target = target.resolve()
        try:
            target.relative_to(self._allowed_root)
        except ValueError:
            raise PermissionError(f"Path outside project directory: {target}")
        return target

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        """Execute the tool with given arguments."""
        ...

    def to_schema(self) -> dict:
        """Return JSON schema for the tool (for LLM function calling)."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        }
