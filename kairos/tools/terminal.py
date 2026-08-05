"""Terminal Tool - executes shell commands in a sandboxed directory."""

from __future__ import annotations

import asyncio
import re
import shlex
from pathlib import Path
from typing import Optional

from kairos.tools.base import BaseTool, ToolResult


class TerminalTool(BaseTool):
    """Execute a shell command in a sandboxed project directory.

    Safety model: command + each arg is split with shlex, the head is
    checked against an allowlist of common dev/build commands, and the
    full argv is scanned for destructive patterns. This is more robust
    than regex-on-raw-string because `rm -rf  /` (two spaces) and
    `echo "rm -rf /" | bash` both get normalized away.

    The list of "safe" heads is intentionally conservative — production
    usage should extend it via subclassing, not by relaxing the defaults.
    """

    name = "terminal"
    description = "Execute a shell command inside the project directory"
    max_output = 10000

    # Commands agents are allowed to invoke by default.
    # Each entry is (head, allowed_arg_substrings) — if allowed_arg_substrings
    # is empty, no flag-level restriction is applied to that head.
    ALLOWED_COMMANDS = {
        # Build/test
        "python": (), "python3": (), "pytest": (), "pyright": (),
        "node": (), "npm": (), "pnpm": (), "yarn": (), "npx": (),
        "go": (), "cargo": (), "rustc": (), "make": (), "cmake": (),
        # Read-only inspection
        "ls": (), "dir": (), "cat": (), "head": (), "tail": (),
        "grep": (), "rg": (), "find": (), "wc": (), "echo": (),
        "pwd": (), "env": (), "which": (), "where": (),
        # VCS
        "git": ("status", "log", "diff", "show", "branch", "remote",
                "add", "commit", "push", "pull", "fetch", "merge",
                "checkout", "switch", "stash", "tag", "init", "clone",
                "config", "rev-parse", "ls-files", "ls-tree"),
    }

    # Patterns that are NEVER allowed, even if the head looks innocent.
    # These match anywhere in the command string (post-shlex reassembly too).
    DENY_PATTERNS = [
        # Destructive rm / dd / mkfs / chmod
        r"\brm\s+-[a-z]*[rf][a-z]*\s+[/~]",
        r"\brm\s+-[a-z]*[rf][a-z]*\s+\*",
        r"\bdd\s+if=",
        r"\bmkfs\b",
        r"\bchmod\s+-R\s+777",
        r"\bchown\s+-R\b",
        # Recursive delete / format on Windows
        r"\bdel\s+/[fFsS].*C:\\",
        r"\bformat\s+[C-Z]:",
        r"\brd\s+/s\s+/q\s+[C-Z]:",
        # Privilege escalation / system mods
        r"\bsudo\b",
        r"\breg\s+delete\b",
        r"\bsc\s+delete\b",
        r"\bbcdedit\b",
        r"\bdiskpart\b",
        r"\bshutdown\b",
        r"\breboot\b",
        # Fork bomb
        r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:",
        # Remote-download-into-shell
        r"\|\s*(bash|sh|powershell|cmd|py|python)\b",
        r"\bcurl\s+.*\|\s*(bash|sh)",
        r"\bwget\s+.*\|\s*(bash|sh)",
        # Disk-level wipes
        r"\bshred\b",
        r"\b:\s*>\s*/dev/(sd|nvme|hd)",
        # Network exfiltration / shells
        r"\bnc\s+-e\b",
        r"\bbash\s+-i\b.*>/dev/tcp/",
    ]

    # Command heads that are never allowed, regardless of deny-pattern outcome.
    ALWAYS_DENY_HEADS = {"rm", "del", "rd", "format", "mkfs", "dd", "shred",
                          "powershell", "cmd", "reg", "sc", "bcdedit",
                          "diskpart", "shutdown", "reboot", "sudo", "su"}

    def __init__(self, allowed_cwd: str | Path = "."):
        self._allowed_cwd = Path(allowed_cwd).resolve()
        self._allowed_cwd.mkdir(parents=True, exist_ok=True)

    def _is_safe_command(self, command: str) -> Optional[str]:
        """Return the rule that blocked the command, or None if allowed."""
        # 1. Parse with shlex so spacing tricks ("rm  -rf /") collapse.
        try:
            tokens = shlex.split(command, posix=(__import__("os").name != "nt"))
        except ValueError:
            return "could not parse command safely"

        if not tokens:
            return "empty command"

        head = tokens[0]
        head_lower = head.lower()

        # 2. Disallow known-dangerous heads outright.
        if head_lower in self.ALWAYS_DENY_HEADS:
            return f"head '{head_lower}' is on the deny list"

        # 3. Head must be on the allowlist.
        if head_lower not in self.ALLOWED_COMMANDS:
            return f"head '{head_lower}' is not on the command allowlist"

        # 4. Per-head argument restrictions (e.g. git push is fine, git push --force is not).
        allowed_args = self.ALLOWED_COMMANDS[head_lower]
        if allowed_args:
            # Only the first non-flag positional (subcommand) is constrained;
            # flags pass through.
            subcmds = [t for t in tokens[1:] if not t.startswith("-")]
            if subcmds and subcmds[0] not in allowed_args:
                return f"subcommand '{subcmds[0]}' not allowed for '{head_lower}'"

        # 5. Final deny-pattern scan over the original string (catches
        # things like `find / -name x -exec rm -rf {} \;`).
        normalized = re.sub(r"\s+", " ", command).strip()
        for pat in self.DENY_PATTERNS:
            if re.search(pat, normalized, re.IGNORECASE):
                return f"blocked by deny pattern: {pat}"

        return None

    def _resolve_cwd(self, cwd: Optional[str]) -> Path:
        target = Path(cwd).resolve() if cwd else self._allowed_cwd
        try:
            target.relative_to(self._allowed_cwd)
        except ValueError:
            raise PermissionError(f"cwd outside allowed path: {target}")
        return target

    async def execute(self, command: str = "", cwd: Optional[str] = None, **kwargs) -> ToolResult:
        if not command:
            return ToolResult(success=False, output="", error="No command provided")

        # Safety check
        blocked = self._is_safe_command(command)
        if blocked:
            return ToolResult(success=False, output="", error=f"Blocked by safety: {blocked}")

        # Lock cwd
        try:
            safe_cwd = self._resolve_cwd(cwd)
        except PermissionError as e:
            return ToolResult(success=False, output="", error=str(e))

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(safe_cwd),
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return ToolResult(success=False, output="", error="Command timed out after 60s")

            output = stdout.decode("utf-8", errors="replace")
            error_output = stderr.decode("utf-8", errors="replace")

            if len(output) > self.max_output:
                output = output[:self.max_output] + "\n... (truncated)"
            if len(error_output) > self.max_output:
                error_output = error_output[:self.max_output] + "\n... (truncated)"

            combined = output
            if error_output:
                combined += f"\n[stderr]\n{error_output}"

            return ToolResult(
                success=process.returncode == 0,
                output=combined,
                error=error_output if process.returncode != 0 else None,
                metadata={"return_code": process.returncode, "cwd": str(safe_cwd)},
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))