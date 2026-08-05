"""FindTool — locate files by glob (sandboxed).

Thin wrapper over pathlib.Path.glob/rglob. Honors the same skip-dir rules
as GrepTool so results stay focused.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from kairos.tools.base import BaseTool, ToolResult

class FindTool(BaseTool):
    name = "find"
    description = "Find files by glob pattern (e.g. '**/*.py', 'src/*.ts')"

    def to_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string",
                                "description": "Glob pattern, e.g. '**/*.py' or 'src/*.ts'"},
                    "path": {"type": "string",
                             "description": "Directory to start from. Defaults to '.'."},
                    "max_results": {"type": "integer", "default": 200},
                },
                "required": ["pattern"],
            },
        }

    SKIP_DIRS = {".git", "node_modules", "__pycache__", "venv", ".venv",
                 "dist", "build", "target", ".next", ".pytest_cache"}

    async def execute(self, pattern: str = "", path: str = ".",
                      max_results: int = 200, **kwargs) -> ToolResult:
        if not pattern:
            return ToolResult(success=False, output="", error="pattern is required")
        try:
            root = self._resolve_safe(path)
        except PermissionError as e:
            return ToolResult(success=False, output="", error=str(e))

        results: list[str] = []
        for p in root.glob(pattern):
            if not p.is_file():
                continue
            if any(part in self.SKIP_DIRS for part in p.parts):
                continue
            try:
                results.append(str(p.relative_to(self._allowed_root)))
            except ValueError:
                results.append(str(p))
            if len(results) >= max_results:
                break

        if not results:
            return ToolResult(success=True, output=f"(no files matched '{pattern}')",
                              metadata={"matches": 0})
        return ToolResult(
            success=True, output="\n".join(results),
            metadata={"matches": len(results)},
        )