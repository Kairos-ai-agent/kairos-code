"""``python_run`` — run a short Python snippet in a child interpreter (P0-5).

The one capability the other new tools deliberately lack: **process
execution**. It runs a snippet the model wrote in a separate interpreter so a
quick calculation, a data transformation or a small experiment does not need
the shell and does not touch the app's own process state.

Every safety property is explicit:

* **No console flash.** The child is spawned through
  :func:`kairos.platform_flags.hidden_kwargs`, which OR-s ``CREATE_NO_WINDOW``
  (``0x08000000``) into ``creationflags`` on Windows and leaves
  ``stdin``/``stdout``/``stderr`` untouched (a child's pipes are orthogonal to
  the console flag — see that module's note).
* **A hard timeout.** A snippet that never returns is killed, and the result
  says so.
* **Bounded output.** stdout and stderr are each capped, with an explicit
  marker naming the cap and how many characters were dropped.
* **Working directory *default*, not a boundary.** An explicit ``cwd`` is
  resolved through the same :meth:`BaseTool._resolve_safe` every file tool
  uses, so the directory Kairos *starts the child in* cannot be named outside
  the tool's root; the default is the root itself. This chooses where the
  snippet begins -- it is **not** an access control: the snippet itself can
  read, write and ``chdir`` anywhere the Kairos process can (see the note on
  :class:`PythonRunTool`).
* **Approval.** Declaring :data:`Capability.EXEC_PROCESS` makes the sentinel's
  ladder ask for it (``kairos.sentinel`` consults
  :func:`kairos.capabilities.runtime_capabilities`), unlike a read-only tool.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import time
from typing import Any, List, Optional, Tuple

from kairos.capabilities import Capability
from kairos.tools.base import BaseTool, ToolResult

#: Per-stream character budget.
MAX_OUTPUT = 20_000
DEFAULT_TIMEOUT = 10.0
MAX_TIMEOUT = 60.0
MIN_TIMEOUT = 0.5


def _python_executable() -> str:
    """A real interpreter to run the snippet with.

    Under PyInstaller ``sys.executable`` is the *app*, so spawning it would
    relaunch Kairos instead of running Python. In that case fall back to a
    ``python``/``python3`` on ``PATH``; if there is none, the caller reports
    that honestly.
    """
    if not getattr(sys, "frozen", False):
        return sys.executable or ""
    for name in ("python", "python3", "py"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def _cap(text: str) -> Tuple[str, bool, int]:
    if len(text) <= MAX_OUTPUT:
        return text, False, 0
    omitted = len(text) - MAX_OUTPUT
    return (text[:MAX_OUTPUT] +
            f"\n... [truncated: stream capped at {MAX_OUTPUT} characters; "
            f"{omitted} characters omitted]", True, omitted)


class PythonRunTool(BaseTool):
    """Run a short Python snippet in a separate Python interpreter process.

    Use it for a self-contained computation the model cannot do reliably by
    hand — arithmetic, parsing, a quick data transformation — without opening a
    shell. The snippet runs with a hard timeout and its standard output and
    error are returned, capped. It has no access to the conversation; give it
    everything it needs in the code itself.

    What "separate interpreter" means — and does **not** mean:

    * It really is a *separate process*: a fresh interpreter launched as
      ``python -I <script>`` with a fixed working directory, so the snippet
      cannot corrupt Kairos's own in-process state and ``-I`` fixes
      ``sys.path`` / ignores ``PYTHON*`` env vars and the user site dir.
    * It is **not a security sandbox.** ``-I`` restricts nothing but import
      search; the snippet keeps the *same* filesystem, subprocess and network
      reach as the Kairos process. The working directory is a *default
      location*, not an access-control boundary — the snippet can ``open()`` or
      ``chdir()`` anywhere Kairos itself can reach.
    * The one defence is the gate: declaring :data:`Capability.EXEC_PROCESS`
      routes every call through the sentinel's approval ladder
      (``kairos.sentinel``), so running a snippet is an explicit, reviewed
      "execute a process" decision. Treat the code as fully privileged.

    Example usage:
        - Calculate: {"code": "print(1 + 1)"}
        - Read a file: {"code": "print(open('data.csv').read().splitlines()[:3])"}
        - Bounded: {"code": "import time; time.sleep(5)", "timeout_s": 2}
    """

    name = "python_run"
    description = (
        "Run a short Python snippet in a separate interpreter process and "
        "return its stdout/stderr. Bounded by a timeout (default 10s, max 60s) "
        "and capped output. This is NOT a security sandbox: the snippet is run "
        "as `python -I` with a fixed working directory, and it keeps the full "
        "filesystem, subprocess and network access of the Kairos process "
        "(the working directory is a default location, not an access "
        "boundary). The safeguard is that it declares the EXEC_PROCESS "
        "capability, so every call requires approval. Use it for "
        "self-contained computation; it cannot see the chat."
    )

    #: Running a child process. This is what makes the sentinel ask for
    #: approval (unlike the read-only tools).
    capabilities = frozenset({Capability.EXEC_PROCESS})

    def to_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": ("The Python source to run. Must be "
                                        "self-contained; print() to return a "
                                        "value."),
                    },
                    "timeout_s": {
                        "type": "number",
                        "description": (f"Kill the snippet after this many "
                                        f"seconds (default {DEFAULT_TIMEOUT:g}, "
                                        f"max {MAX_TIMEOUT:g})."),
                    },
                    "cwd": {
                        "type": "string",
                        "description": ("Working directory, relative to the "
                                        "project directory. Defaults to the "
                                        "project root; must stay inside it."),
                    },
                },
                "required": ["code"],
            },
        }

    def _resolve_workdir(self, cwd: Optional[str]) -> Tuple[Optional[str], str]:
        """Fence ``cwd`` inside the root. Returns ``(path, error)``."""
        if not cwd:
            root = self._allowed_root
            if not root.is_dir():
                return None, f"the project root does not exist: {root}"
            return str(root), ""
        try:
            resolved = self._resolve_safe(cwd)
        except PermissionError as exc:
            return None, str(exc)
        if not resolved.is_dir():
            return None, f"cwd is not a directory inside the project: {cwd}"
        return str(resolved), ""

    @staticmethod
    def _coerce_timeout(value: Any) -> float:
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            return DEFAULT_TIMEOUT
        if seconds <= 0:
            return DEFAULT_TIMEOUT
        return max(MIN_TIMEOUT, min(MAX_TIMEOUT, seconds))

    async def execute(self, code: str = "", timeout_s: Any = None,
                      cwd: Optional[str] = None, **kwargs) -> ToolResult:
        if not isinstance(code, str) or not code.strip():
            return ToolResult(success=False, output="",
                              error="python_run needs non-empty `code`")

        interpreter = _python_executable()
        if not interpreter:
            return ToolResult(
                success=False, output="",
                error=("no Python interpreter is available to run the snippet "
                       "(this build is frozen and no `python` is on PATH)"))

        workdir, problem = self._resolve_workdir(cwd)
        if workdir is None:
            return ToolResult(success=False, output="", error=problem)

        timeout = self._coerce_timeout(timeout_s)

        # Write the snippet to a temp file rather than passing it as argv, so
        # the code never shows up in the process list, and run it as a script.
        handle = None
        script = ""
        try:
            fd, script = tempfile.mkstemp(prefix="kairos_snippet_", suffix=".py")
            handle = os.fdopen(fd, "w", encoding="utf-8")
            handle.write(code)
            handle.close()
            handle = None
        except OSError as exc:
            if handle is not None:
                handle.close()
            return ToolResult(success=False, output="",
                              error=f"could not stage the snippet: {exc}")

        started = time.monotonic()
        timed_out = False
        returncode: Optional[int] = None
        out_b = b""
        err_b = b""
        spawn_error = ""
        try:
            popen_kwargs = {
                "cwd": workdir,
                "stdin": asyncio.subprocess.DEVNULL,
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.PIPE,
            }
            from kairos.platform_flags import hidden_kwargs
            process = await asyncio.create_subprocess_exec(
                interpreter, "-I", script, **hidden_kwargs(popen_kwargs))
            try:
                out_b, err_b = await asyncio.wait_for(
                    process.communicate(), timeout=timeout)
                returncode = process.returncode
            except TimeoutError:
                timed_out = True
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                try:
                    out_b, err_b = await asyncio.wait_for(
                        process.communicate(), timeout=5.0)
                except Exception:  # noqa: BLE001 - best-effort drain
                    out_b, err_b = b"", b""
        except Exception as exc:  # noqa: BLE001 - one readable line, no stack
            spawn_error = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                if script:
                    os.unlink(script)
            except OSError:
                pass

        duration = time.monotonic() - started
        stdout = out_b.decode("utf-8", errors="replace")
        stderr = err_b.decode("utf-8", errors="replace")
        shown_out, out_capped, out_omitted = _cap(stdout)
        shown_err, err_capped, err_omitted = _cap(stderr)

        sections: List[str] = []
        if shown_out.strip():
            sections.append(shown_out.rstrip("\n"))
        if shown_err.strip():
            sections.append("[stderr]\n" + shown_err.rstrip("\n"))
        body = "\n".join(sections) if sections else "(no output)"

        meta: dict = {
            "cwd": workdir,
            "timeout_s": timeout,
            "duration_s": round(duration, 3),
            "timed_out": timed_out,
            "returncode": returncode,
            "stdout_truncated": out_capped,
            "stderr_truncated": err_capped,
        }

        if spawn_error:
            meta["error"] = spawn_error
            return ToolResult(success=False, output="",
                              error=f"could not start the interpreter: "
                                    f"{spawn_error}", metadata=meta)

        if timed_out:
            prefix = (f"[timed out after {timeout:g}s; the interpreter was "
                      f"killed]\n")
            return ToolResult(success=False, output=prefix + body,
                              error=(f"python_run timed out after {timeout:g}s; "
                                     f"the interpreter was killed"),
                              metadata=meta)

        if returncode != 0:
            return ToolResult(
                success=False, output=body,
                error=f"the snippet exited with code {returncode}",
                metadata=meta)

        # Surface truncation even on the success path, so a partial result is
        # never mistaken for a complete one.
        if out_capped or err_capped:
            note = (f"\n[note: output was truncated "
                    f"({out_omitted} stdout / {err_omitted} stderr chars "
                    f"omitted); print less or narrow the snippet]")
            body += note
        return ToolResult(success=True, output=body, metadata=meta)
