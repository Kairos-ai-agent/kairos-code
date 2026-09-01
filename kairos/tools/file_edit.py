"""File Edit Tool - writes or modifies files (sandboxed)."""

from __future__ import annotations

from typing import Optional

from kairos.tools.base import BaseTool, ToolResult

class FileEditTool(BaseTool):
    """Write file contents inside the project directory."""

    name = "file_write"
    description = "Write content to a file"

    def to_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file to write (relative to project dir)"},
                    "content": {"type": "string", "description": "Content to write to the file"},
                },
                "required": ["path", "content"],
            },
        }

    async def execute(self, path: str = "", content: str = "", **kwargs) -> ToolResult:
        try:
            file_path = self._resolve_safe(path)
        except PermissionError as e:
            return ToolResult(success=False, output="", error=str(e))
        # R38.6 §30: auto-checkpoint BEFORE the write. A failed
        # snapshot is logged but never blocks the write — we
        # don't want a permission bug in the checkpointer to
        # brick the agent.
        snap_err: Optional[str] = None
        if self._checkpointer is not None:
            snap_err = self._checkpointer.before_write(path)
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            meta = {"bytes_written": len(content)}
            if snap_err:
                meta["auto_checkpoint_error"] = snap_err
            return ToolResult(
                success=True,
                output=f"File written: {path} ({len(content)} bytes)",
                metadata=meta,
            )
        except PermissionError as e:
            return ToolResult(success=False, output="", error=str(e))
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))

class FileEditReplaceTool(BaseTool):
    """Replace text in a file inside the project directory."""

    name = "file_edit_replace"
    description = "Replace text in a file"

    def to_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file"},
                    "old_text": {"type": "string", "description": "Text to find and replace"},
                    "new_text": {"type": "string", "description": "Replacement text"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        }

    async def execute(self, path: str = "", old_text: str = "", new_text: str = "", **kwargs) -> ToolResult:
        try:
            file_path = self._resolve_safe(path)
        except PermissionError as e:
            return ToolResult(success=False, output="", error=str(e))

        if not file_path.exists():
            return ToolResult(success=False, output="", error=f"File not found: {path}")

        # R38.6 §30: auto-checkpoint BEFORE the read-modify-write
        # cycle so a single restore can undo the whole edit.
        snap_err: Optional[str] = None
        if self._checkpointer is not None:
            snap_err = self._checkpointer.before_write(path)
        try:
            content = file_path.read_text(encoding="utf-8")
            if old_text not in content:
                return ToolResult(success=False, output="", error="old_text not found in file")
            new_content = content.replace(old_text, new_text, 1)
            file_path.write_text(new_content, encoding="utf-8")
            meta = {"path": path}
            if snap_err:
                meta["auto_checkpoint_error"] = snap_err
            return ToolResult(success=True, output=f"Replaced text in {path}",
                               metadata=meta)
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))

class MultiEditTool(BaseTool):
    """Apply many edits across many files in a single call.

    Each edit is {path, old_text, new_text}. Edits are applied in order; on
    the first failure the whole call returns an error and the file is left
    in whatever state the partial edits produced (we don't roll back —
    that would require tracking original contents and writing them back,
    which is expensive and the Coder can retry).

    Designed to mirror the agentic CLI's MultiEdit: one round-trip, atomic-ish,
    and the Reviewer can see all changes in one tool invocation.
    """

    name = "multi_edit"
    description = "Apply multiple text replacements across multiple files in one call"

    def to_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "edits": {
                        "type": "array",
                        "description": "List of edits to apply, in order",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string",
                                         "description": "Path to the file (relative to project dir)"},
                                "old_text": {"type": "string"},
                                "new_text": {"type": "string"},
                            },
                            "required": ["path", "old_text", "new_text"],
                        },
                    },
                },
                "required": ["edits"],
            },
        }

    async def execute(self, edits: Optional[list] = None, **kwargs) -> ToolResult:
        if not edits:
            return ToolResult(success=False, output="", error="No edits provided")
        # R38.6 §30: snapshot every path that's about to be
        # modified, in one batch, BEFORE we touch anything. If
        # any single snapshot fails, we continue — a failed
        # checkpoint is logged but doesn't block the edit.
        snap_errors: dict = {}
        if self._checkpointer is not None and edits:
            for e in edits:
                p = e.get("path", "")
                if p and p not in snap_errors:
                    err = self._checkpointer.before_write(p)
                    if err:
                        snap_errors[p] = err
        applied = 0
        failed = []
        for i, e in enumerate(edits):
            try:
                path = e.get("path", "")
                old = e.get("old_text", "")
                new = e.get("new_text", "")
                fp = self._resolve_safe(path)
                if not fp.exists():
                    failed.append(f"#{i}: file not found: {path}")
                    continue
                content = fp.read_text(encoding="utf-8")
                if old not in content:
                    failed.append(f"#{i}: old_text not found in {path}")
                    continue
                fp.write_text(content.replace(old, new, 1), encoding="utf-8")
                applied += 1
            except PermissionError as e:
                failed.append(f"#{i}: permission denied ({e})")
            except Exception as e:
                failed.append(f"#{i}: {e}")
        meta = {
            "applied": applied,
            "files": len({e.get('path', '') for e in edits}),
        }
        if snap_errors:
            meta["auto_checkpoint_errors"] = snap_errors
        if failed:
            return ToolResult(
                success=False,
                output=f"Applied {applied}/{len(edits)} edits.",
                error="; ".join(failed),
                metadata=meta,
            )
        return ToolResult(
            success=True,
            output=f"Applied {applied} edits across {meta['files']} file(s).",
            metadata=meta,
        )
