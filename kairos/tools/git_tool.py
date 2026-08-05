"""GitTool — read-only git introspection (sandboxed).

Allows: status, diff, log, show, branch, add, commit.
Blocks: push, force-push, reset --hard on main, clean -fd.

The point: Reviewer needs `git diff` to grade changes; Coder needs
`git status` to see what's untracked. We don't want either agent pushing
to a remote without explicit user action.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Optional

from kairos.tools.base import BaseTool, ToolResult

# Subcommands we permit.
ALLOWED_SUBCMDS = {
    "status", "diff", "log", "show", "branch", "add", "commit",
    "rev-parse", "ls-files", "remote", "config",
}

# Args we never allow — even on otherwise-OK subcommands.
FORBIDDEN_ARGS = {
    "--force", "-f",            # force push etc.
    "--no-verify",              # skip hooks
    "--mirror",                 # mirror clone
    "push",                     # disallow pushes entirely
    "clean",                    # git clean -fd removes untracked
    "reset",                    # destructive
}

class GitTool(BaseTool):
    name = "git"
    description = "Run a read-only-ish git subcommand (status, diff, log, add, commit, ...)"

    def to_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "subcommand": {"type": "string",
                                   "description": "Git subcommand: status|diff|log|show|branch|add|commit|rev-parse|ls-files|remote|config"},
                    "args": {"type": "string",
                             "description": "Additional arguments to pass to git. Optional."},
                },
                "required": ["subcommand"],
            },
        }

    async def execute(self, subcommand: str = "", args: str = "",
                      **kwargs) -> ToolResult:
        if not subcommand:
            return ToolResult(success=False, output="", error="subcommand is required")
        sub = subcommand.strip()
        if sub not in ALLOWED_SUBCMDS:
            return ToolResult(success=False, output="", error=f"git subcommand '{sub}' is not allowed")

        # Tokenize args to inspect per-token.
        try:
            arg_tokens = args.split() if args else []
        except Exception:
            return ToolResult(success=False, output="", error="could not parse args")

        for tok in arg_tokens:
            base = tok.lstrip("-").split("=")[0]
            if tok in FORBIDDEN_ARGS or base in FORBIDDEN_ARGS:
                return ToolResult(success=False, output="",
                                  error=f"git argument '{tok}' is forbidden")

        cmd = ["git", sub] + arg_tokens
        cwd = str(self._allowed_root)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, cwd=cwd,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return ToolResult(success=False, output="", error="git timed out after 30s")
            out = stdout.decode("utf-8", errors="replace")
            err = stderr.decode("utf-8", errors="replace")
            combined = out if not err else f"{out}\n[stderr]\n{err}"
            if len(combined) > 50000:
                combined = combined[:50000] + "\n... (truncated)"
            return ToolResult(
                success=proc.returncode == 0,
                output=combined,
                error=err if proc.returncode != 0 else None,
                metadata={"subcommand": sub, "return_code": proc.returncode},
            )
        except FileNotFoundError:
            return ToolResult(success=False, output="",
                              error="git is not installed or not on PATH")
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))