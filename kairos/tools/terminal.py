"""Terminal Tool - executes shell commands in a sandboxed directory."""

from __future__ import annotations

import asyncio
import os
import re
import shlex
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from kairos.tools.base import BaseTool, ToolResult


def _split_command(command: str) -> List[str]:
    """Split a command line into argv without a shell.

    On POSIX we use ``shlex.split(..., posix=True)``. On Windows we use the
    real ``CommandLineToArgvW`` parser so backslash paths (``C:\\dir\\x``)
    and quoted arguments survive intact — ``shlex(posix=True)`` would treat
    ``\\`` as an escape and mangle them.
    """
    if os.name != "nt":
        return shlex.split(command, posix=True)
    try:
        import ctypes
        cmd_line = ctypes.windll.shell32.CommandLineToArgvW
        cmd_line.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
        cmd_line.restype = ctypes.POINTER(ctypes.c_wchar_p)
        argc = ctypes.c_int()
        argv = cmd_line(command, ctypes.byref(argc))
        if not argv or argc.value <= 0:
            return shlex.split(command, posix=True)
        try:
            return [argv[i] for i in range(argc.value)]
        finally:
            local_free = ctypes.windll.kernel32.LocalFree
            local_free.argtypes = [ctypes.c_void_p]
            local_free.restype = ctypes.c_void_p
            local_free(ctypes.cast(argv, ctypes.c_void_p))
    except Exception:  # noqa: BLE001
        return shlex.split(command, posix=True)


class TerminalTool(BaseTool):
    """Execute a command in a sandboxed project directory (argv, no shell).

    Safety model (R38.6 hardening):
      - the command is parsed with ``shlex`` and executed via
        ``create_subprocess_exec`` — there is **no shell**, so shell
        chaining (``&&`` / ``;`` / ``|``), redirection, ``$()`` and
        backticks are impossible, and the allow-listed head *is* the real
        executable.
      - the head must be on an allow-list; interpreter / leak-prone heads
        (``python``, ``node``, ``env``, ``cat``, …) are excluded by default.
      - shell-control tokens and destructive deny-patterns are rejected.
      - the portable ``kairos.sandbox`` deny-list is also applied, and the
        child PID is handed to the OS-level sandbox (Windows Job Object) so
        the process tree dies with the parent.

    The list of "safe" heads is intentionally conservative — production
    usage should extend it via subclassing, not by relaxing the defaults.
    """

    name = "terminal"
    description = "Execute a shell command inside the project directory"
    max_output = 10000

    # Commands agents are allowed to invoke by default.
    # Each entry is (head, allowed_arg_substrings) — if allowed_arg_substrings
    # is empty, no flag-level restriction is applied to that head.
    #
    # SECURITY (R38.6 hardening): interpreter / leak-prone heads are
    # deliberately NOT here. ``python -c`` / ``node -e`` / ``env`` / ``cat``
    # are arbitrary-code-exec or secret-dump primitives, so the default
    # allow-list is conservative. Production usage can extend it via
    # subclassing (mirroring the docs), but the shipped default exposes the
    # smallest surface that still covers the Coder/Reviewer workflow
    # (``pytest`` / ``npm test`` / ``go test`` / ``git status``).
    ALLOWED_COMMANDS = {
        # Build/test — interpreter-free runners documented in the role
        # prompts (pytest / npm test / go test). These run arbitrary code
        # *through their project config* by design; that is exactly what the
        # OS-level sandbox layer is meant to confine (see sandbox.py).
        "pytest": (), "pyright": (),
        "npm": (), "pnpm": (), "yarn": (),  # npx removed: `npx <pkg>` is remote code-exec
        "go": (), "cargo": (), "rustc": (),
        "make": (), "cmake": (),
        # Read-only inspection (no code exec, no env/secret dump)
        "ls": (), "dir": (), "grep": (), "rg": (), "find": (),
        "wc": (), "echo": (), "pwd": (),
        # VCS
        "git": ("status", "log", "diff", "show", "branch", "remote",
                "add", "commit", "push", "pull", "fetch", "merge",
                "checkout", "switch", "stash", "tag", "init", "clone",
                "config", "rev-parse", "ls-files", "ls-tree"),
    }

    # Tokens that only ever appear as shell control operators. Under argv
    # execution a lone ``&&`` / ``;`` / ``|`` / ``>`` token means the caller
    # tried to chain or redirect — reject it outright (no shell is used, so
    # these operators should never be a legitimate part of the command).
    SHELL_OPERATORS = {"&&", "||", ";", "|", ">", ">>", "<", "&", "<&", ">&"}

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
        # find -exec / -execdir followed by an interpreter or shell: even
        # without a shell, `find . -exec rm -rf {} +` (or `... ;`) runs an
        # arbitrary command when the '+'/';' terminator is present.
        r"\s-exec(?:dir)?\s+(rm|sh|bash|zsh|python|python3|node|nodejs|perl|ruby|php|powershell|pwsh|cmd|curl|wget|nc|ncat|socat)\b",
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
        #    ``_split_command`` is Windows-aware (CommandLineToArgvW) so
        #    backslash paths aren't mangled.
        try:
            tokens = _split_command(command)
        except ValueError:
            return "could not parse command safely"

        if not tokens:
            return "empty command"

        # 1b. Reject shell control operators. A lone ``&&`` / ``;`` / ``|``
        #     token can only come from a chaining/redirect attempt, which is
        #     impossible under argv execution and bypasses the head allowlist.
        for tok in tokens:
            if tok in self.SHELL_OPERATORS:
                return f"shell operator '{tok}' is not allowed (chaining and redirection are disabled)"

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

        # 6. Block argv tokens that resolve to an existing path OUTSIDE the
        #    allowed cwd (e.g. ``grep -r key C:\\Users\\...`` or
        #    ``grep -r x ../../etc/passwd``). The cwd lock only constrains
        #    the working directory, not absolute/``..`` paths a tool reads.
        escape = self._escapes_cwd(tokens)
        if escape:
            return f"argument '{escape}' resolves to a path outside the project directory"

        return None

    def _escapes_cwd(self, tokens: List[str]) -> Optional[str]:
        """Return the first token that resolves to an *existing* path outside
        ``self._allowed_cwd``, else ``None``. Flags are skipped; every other
        token is resolved against the cwd and checked. Tokens that don't map
        to a real existing path (regex patterns, not-yet-created files) are
        left alone, so this mainly stops reads of existing absolute / ``..``
        paths that escape the project."""
        for tok in tokens:
            if tok.startswith("-"):
                continue
            # ``shlex(..., posix=True)`` treats ``\\`` as an escape on every
            # OS, so a Windows drive path may arrive de-cased; also try the
            # backslash-normalized form so ``C:\\Windows\\...`` is still seen.
            for cand in (tok, tok.replace("\\", "/")):
                try:
                    if (os.path.isabs(cand) or cand.startswith("/")
                            or cand.startswith("\\\\")
                            or re.match(r"^[A-Za-z]:[\\/]", cand)):
                        resolved = Path(cand).resolve()
                    else:
                        resolved = (self._allowed_cwd / cand).resolve()
                except (OSError, ValueError):
                    continue
                try:
                    resolved.relative_to(self._allowed_cwd)
                except ValueError:
                    # Outside the allowed cwd; only reject if it truly exists
                    # (a dangling/absolute path arg that doesn't exist is inert).
                    if resolved.exists():
                        return tok
        return None

    def _resolve_cwd(self, cwd: Optional[str]) -> Path:
        target = Path(cwd).resolve() if cwd else self._allowed_cwd
        try:
            target.relative_to(self._allowed_cwd)
        except ValueError:
            raise PermissionError(f"cwd outside allowed path: {target}")
        return target

    async def execute(self, command: str = "", cwd: Optional[str] = None,
                      *,
                      timeout_s: Optional[float] = None,
                      env: Optional[dict] = None,
                      stdin: Optional[str] = None,
                      stream: bool = False,
                      on_stdout: Optional[Callable[[str], None]] = None,
                      on_stderr: Optional[Callable[[str], None]] = None,
                      kill_process_group: bool = True,
                      **kwargs) -> ToolResult:
        """Run a shell command with full Bash semantics.

        New parameters (all optional, all backwards compatible):

        - ``timeout_s`` — per-call override of the 60s default. Set
          to a large number (or ``None`` for the default) for slow
          operations like ``pytest -x``.
        - ``env`` — extra environment variables merged on top of
          ``os.environ``. Useful for setting ``PYTHONPATH``,
          ``HTTP_PROXY``, etc., without leaking them into the
          agent's outer process.
        - ``stdin`` — string piped to the child's stdin. Useful for
          feeding input to ``python -c`` snippets or ``bc``.
        - ``stream`` + ``on_stdout`` / ``on_stderr`` — when ``stream``
          is true, the tool invokes the callbacks on every chunk
          the child writes, instead of waiting for completion. The
          final ``ToolResult`` still carries the full output, so the
          agent can keep doing "show me the result" without
          re-running the command.
        - ``kill_process_group`` — when true (default), the entire
          process group is killed on timeout / cancel, not just
          the leader. This prevents orphaned children from holding
          a port or a file lock after the parent dies.

        Returns a :class:`ToolResult` with metadata fields:
          ``return_code``, ``cwd``, ``duration_s``, ``timed_out``,
          ``command``.
        """
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

        # Defense-in-depth: run the portable sandbox deny-list too. This
        # keeps the terminal aligned with kairos.sandbox even when the
        # allow-list here is extended by a subclass.
        policy = None
        try:
            from kairos.sandbox import SandboxPolicy, check_policy, assign_child_to_sandbox
            policy = SandboxPolicy(allowed_root=safe_cwd)
            rule = check_policy(policy, command)
            if rule:
                return ToolResult(success=False, output="",
                                  error=f"Blocked by sandbox policy: {rule}")
        except Exception:
            policy = None

        effective_timeout = float(timeout_s) if timeout_s is not None else 60.0
        full_env = dict(os.environ)
        if env:
            for k, v in env.items():
                if v is None:
                    full_env.pop(k, None)
                else:
                    full_env[str(k)] = str(v)

        start = time.perf_counter()
        timed_out = False
        try:
            # On POSIX, start_new_session=True puts the child in its
            # own process group so we can SIGTERM the whole tree on
            # timeout. On Windows we don't have process groups, so
            # this is a no-op; the child process is killed directly.
            if os.name == "nt":
                new_session_kw = {}
            else:
                new_session_kw = {"start_new_session": True}

            # SECURITY: execute via argv (no shell) so the head allow-list
            # is the *actual* executable and shell chaining/redirection is
            # impossible. The parsed argv is regenerated here (the parse in
            # ``_is_safe_command`` is only used for validation).
            argv = _split_command(command)

            popen_kwargs = {
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.PIPE,
                "stdin": asyncio.subprocess.PIPE if stdin else None,
                "cwd": str(safe_cwd),
                "env": full_env,
                **new_session_kw,
            }
            # Apply the OS-level sandbox to the spawn kwargs. On Linux this
            # attaches a ``preexec_fn`` + ``pass_fds`` so the Landlock
            # ruleset is applied in the forked CHILD (never the parent); on
            # Windows it's a no-op (Job Object is applied after spawn).
            if policy is not None:
                try:
                    from kairos.sandbox import apply_to_subprocess
                    popen_kwargs = apply_to_subprocess(policy, popen_kwargs)
                except Exception:
                    pass
                # macOS's Seatbelt integration stores a profile marker that
                # is NOT a valid subprocess kwarg; terminal doesn't wrap with
                # sandbox-exec, so drop it (macOS isolation stays best-effort).
                popen_kwargs.pop("__kairos_seatbelt_profile", None)

            try:
                process = await asyncio.create_subprocess_exec(*argv, **popen_kwargs)
            except (TypeError, ValueError):
                # Some asyncio loops / Popen builds reject preexec_fn/pass_fds;
                # retry without them so the command still runs (at reduced
                # isolation) rather than failing outright.
                for k in ("preexec_fn", "pass_fds"):
                    popen_kwargs.pop(k, None)
                process = await asyncio.create_subprocess_exec(*argv, **popen_kwargs)

            # Wire the OS-level sandbox for this child (Windows Job Object
            # KILL_ON_JOB_CLOSE; called in the child for Linux via the
            # preexec_fn above). Kept best-effort so a sandbox failure never
            # breaks the command.
            if policy is not None:
                try:
                    assign_child_to_sandbox(policy, process.pid)
                except Exception:
                    pass

            if stdin is not None:
                try:
                    process.stdin.write(stdin.encode("utf-8"))
                    await process.stdin.drain()
                except (BrokenPipeError, ConnectionResetError):
                    pass
                try:
                    process.stdin.close()
                except Exception:
                    pass

            if stream and (on_stdout or on_stderr):
                stdout_bytes, stderr_bytes = await self._stream_process(
                    process, on_stdout, on_stderr, effective_timeout,
                )
                if stdout_bytes is None:
                    timed_out = True
                    stdout_bytes, stderr_bytes = b"", b""
            else:
                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        process.communicate(), timeout=effective_timeout,
                    )
                except asyncio.TimeoutError:
                    timed_out = True
                    await self._kill_tree(process, kill_process_group)
                    stdout_bytes, stderr_bytes = b"", b""

            output = stdout_bytes.decode("utf-8", errors="replace")
            error_output = stderr_bytes.decode("utf-8", errors="replace")

            if len(output) > self.max_output:
                output = output[:self.max_output] + "\n... (truncated)"
            if len(error_output) > self.max_output:
                error_output = error_output[:self.max_output] + "\n... (truncated)"

            combined = output
            if error_output:
                combined += f"\n[stderr]\n{error_output}"

            duration = time.perf_counter() - start
            success = (process.returncode == 0) and not timed_out
            err_msg = None
            if timed_out:
                err_msg = f"Command timed out after {effective_timeout}s"
            elif process.returncode != 0:
                err_msg = error_output or f"exit code {process.returncode}"

            return ToolResult(
                success=success,
                output=combined,
                error=err_msg,
                metadata={
                    "return_code": process.returncode,
                    "cwd": str(safe_cwd),
                    "duration_s": round(duration, 3),
                    "timed_out": timed_out,
                    "command": command[:500],
                    "env_overrides": list((env or {}).keys()),
                    "had_stdin": stdin is not None,
                    "streamed": bool(stream),
                },
            )
        except Exception as e:
            return ToolResult(success=False, output="", error=str(e))

    # -- helpers ----------------------------------------------------------

    async def _stream_process(
        self,
        process: "asyncio.subprocess.Process",
        on_stdout: Optional[Callable[[str], None]],
        on_stderr: Optional[Callable[[str], None]],
        timeout_s: float,
    ) -> Tuple[Optional[bytes], Optional[bytes]]:
        """Stream child output to callbacks while collecting the final bytes.

        Returns ``(stdout_bytes, stderr_bytes)``. Either may be ``None``
        if the process was killed because of a timeout.
        """
        import asyncio as _asyncio

        out_chunks: List[bytes] = []
        err_chunks: List[bytes] = []

        async def _drain(stream, sink: List[bytes], cb) -> None:
            assert stream is not None
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    return
                sink.append(chunk)
                if cb:
                    try:
                        cb(chunk.decode("utf-8", errors="replace"))
                    except Exception:
                        pass

        try:
            await _asyncio.wait_for(
                _asyncio.gather(
                    _drain(process.stdout, out_chunks, on_stdout),
                    _drain(process.stderr, err_chunks, on_stderr),
                ),
                timeout=timeout_s,
            )
        except _asyncio.TimeoutError:
            await self._kill_tree(process, True)
            return None, None

        # Drain anything still in the pipe buffers.
        try:
            rest_out, rest_err = await process.communicate()
            if rest_out:
                out_chunks.append(rest_out)
            if rest_err:
                err_chunks.append(rest_err)
        except Exception:
            pass
        return b"".join(out_chunks), b"".join(err_chunks)

    async def _kill_tree(
        self, process: "asyncio.subprocess.Process", use_group: bool,
    ) -> None:
        """Kill the child (and its group, on POSIX) and wait for it."""
        try:
            if use_group and os.name != "nt":
                # POSIX: kill the whole process group.
                import signal
                try:
                    pgid = os.getpgid(process.pid)
                    os.killpg(pgid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
                try:
                    await asyncio.wait_for(process.wait(), timeout=2.0)
                    return
                except asyncio.TimeoutError:
                    pass
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            else:
                # Windows or no group requested.
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), timeout=2.0)
                    return
                except asyncio.TimeoutError:
                    pass
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            try:
                await asyncio.wait_for(process.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
        except Exception:
            # Swallow — best-effort cleanup.
            pass