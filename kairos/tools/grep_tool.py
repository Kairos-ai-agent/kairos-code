"""GrepTool — regex search across the project (sandboxed).

Modeled on ripgrep's most common flags. Returns matched lines with file
path + line number + content, capped to avoid flooding the LLM context.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from kairos.tools.base import BaseTool, ToolResult


class GrepTool(BaseTool):
    name = "grep"
    description = "Search for a regex pattern across files in the project"

    def to_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string",
                                "description": "Regex pattern to search for"},
                    "path": {"type": "string",
                             "description": "Directory to search in (relative to project root). Defaults to '.'."},
                    "glob": {"type": "string",
                              "description": "Optional glob filter, e.g. '*.py' or 'src/**/*.ts'."},
                    "ignore_case": {"type": "boolean", "default": False},
                    "max_results": {"type": "integer", "default": 100,
                                     "description": "Cap to keep LLM context sane."},
                },
                "required": ["pattern"],
            },
        }

    async def execute(self, pattern: str = "", path: str = ".",
                      glob: Optional[str] = None,
                      ignore_case: bool = False,
                      max_results: int = 100,
                      **kwargs) -> ToolResult:
        if not pattern:
            return ToolResult(success=False, output="", error="pattern is required")
        try:
            root = self._resolve_safe(path)
        except PermissionError as e:
            return ToolResult(success=False, output="", error=str(e))

        flags = re.IGNORECASE if ignore_case else 0
        try:
            compiled = re.compile(pattern, flags)
        except re.error as e:
            return ToolResult(success=False, output="", error=f"invalid regex: {e}")

        # Common binary / heavy dirs to skip — saves wall time and keeps
        # noise out of results.
        SKIP_DIRS = {".git", "node_modules", "__pycache__", "venv", ".venv",
                     "dist", "build", "target", ".next", ".pytest_cache"}

        matches: list[str] = []
        files_scanned = 0

        def iter_files(p: Path):
            if p.is_file():
                yield p
                return
            for child in p.rglob("*") if not glob else p.glob(glob):
                if child.is_dir():
                    continue
                if any(part in SKIP_DIRS for part in child.parts):
                    continue
                yield child

        for f in iter_files(root):
            files_scanned += 1
            try:
                with f.open("r", encoding="utf-8", errors="ignore") as fh:
                    for lineno, line in enumerate(fh, start=1):
                        if compiled.search(line):
                            rel = f.relative_to(self._allowed_root)
                            matches.append(f"{rel}:{lineno}:{line.rstrip()}")
                            if len(matches) >= max_results:
                                break
            except (OSError, UnicodeError):
                continue
            if len(matches) >= max_results:
                break

        if not matches:
            return ToolResult(success=True, output=f"(no matches in {files_scanned} files)",
                              metadata={"files_scanned": files_scanned, "matches": 0})
        out = "\n".join(matches[:max_results])
        if len(out) > 50000:
            out = out[:50000] + f"\n... (truncated, {len(matches)} total)"
        return ToolResult(
            success=True, output=out,
            metadata={"files_scanned": files_scanned, "matches": len(matches)},
        )