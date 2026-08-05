"""File Read Tool - reads file contents (sandboxed)."""

from __future__ import annotations

from typing import Optional

from kairos.tools.base import BaseTool, ToolResult


class FileReadTool(BaseTool):
    """Read the contents of a file inside the project directory."""

    name = "file_read"
    description = "Read the contents of a file"

    def to_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file to read (relative to project dir)"},
                },
                "required": ["path"],
            },
        }

    async def execute(self, path: str = "", **kwargs) -> ToolResult:
        from kairos.tools.cache import get_cache, make_key
        cache = get_cache()
        ck = make_key(self.name, path=path)
        cached = cache.get(ck)
        if cached is not None:
            return cached
        try:
            file_path = self._resolve_safe(path)
        except PermissionError as e:
            result = ToolResult(success=False, output="", error=str(e))
            cache.set(ck, result)
            return result

        if not file_path.exists():
            result = ToolResult(success=False, output="", error=f"File not found: {path}")
            cache.set(ck, result)
            return result

        if file_path.is_dir():
            # Reading a directory as text raises PermissionError on POSIX
            # and "Is a directory" on Windows — neither is useful for the
            # LLM. Return an actionable hint instead.
            try:
                entries = sorted(p.name + ("/" if p.is_dir() else "")
                                  for p in file_path.iterdir())
                listing = "\n".join(entries[:200])
                result = ToolResult(
                    success=True,
                    output=f"<directory listing of {file_path}>\n{listing}",
                    metadata={"path": str(file_path), "is_dir": True,
                              "entry_count": len(entries)},
                )
            except Exception as e:
                result = ToolResult(
                    success=False, output="",
                    error=f"Path is a directory and could not be listed: {e}",
                )
            cache.set(ck, result)
            return result

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            original_length = len(content)
            truncated = False
            if original_length > 50000:
                content = content[:50000] + "\n... (truncated)"
                truncated = True
            result = ToolResult(
                success=True,
                output=content,
                metadata={
                    "path": str(file_path),
                    "original_length": original_length,
                    "truncated": truncated,
                    "max_length": 50000,
                },
            )
            cache.set(ck, result)
            return result
        except Exception as e:
            result = ToolResult(success=False, output="", error=str(e))
            cache.set(ck, result)
            return result