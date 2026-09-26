"""Static analysis + test pre-check.

Runs BEFORE the Reviewer each round:
- ruff (Python lint) on changed files
- a type check (mypy / tsc) when the project is configured for one
- pytest on the workspace if a test command is configured
- A simple "did tests fail last time?" check for self-debug mode

Results are injected into both the Coder next-round prompt and the
Reviewer prompt so they don'"'"'t have to discover them via tool calls.
This catches:
- SyntaxError / ImportError before the LLM wastes a turn
- Failing tests with the exact error message
- Lint regressions introduced by the Coder

Best-effort: if ruff/pytest is not installed, we silently skip and let
the loop continue (the Reviewer still has the final word).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Cap output so a long pytest trace doesn'"'"'t blow up the prompt.
MAX_OUTPUT_CHARS = 3000

async def _terminate_tree(proc) -> None:
    """Kill the command *and its children*, then reap it.

    ``wait_for(proc.communicate())`` cancels the *read* on timeout but leaves the process
    running with its pipes open — and a command that spawned children keeps those too.
    The loop calls ``_run`` every round, so leaked processes (and their memory) pile up
    round after round, which is exactly how a CI shard climbed to 13 GB.
    """
    if proc is None or proc.returncode is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       capture_output=True, check=False)
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            proc.kill()
    try:
        await asyncio.wait_for(proc.wait(), timeout=10)
    except (asyncio.TimeoutError, ProcessLookupError):
        pass


async def _run(cmd: List[str], cwd: Path, timeout: int = 60) -> Dict:
    """Run a shell command asynchronously. Never raises.

    The child gets its own session (POSIX) / process group (Windows) so that killing a
    timed-out command cannot signal this process, and so its whole tree can be ended.
    """
    proc = None
    try:
        extra: Dict = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                       if os.name == "nt" else {"start_new_session": True})
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **extra,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return {
            "ok": proc.returncode == 0,
            "code": proc.returncode or 0,
            "stdout": (stdout.decode("utf-8", errors="replace") or "")[:MAX_OUTPUT_CHARS],
            "stderr": (stderr.decode("utf-8", errors="replace") or "")[:MAX_OUTPUT_CHARS],
        }
    except asyncio.TimeoutError:
        await _terminate_tree(proc)
        return {"ok": False, "code": -1, "stdout": "", "stderr": "(timeout)"}
    except FileNotFoundError:
        return {"ok": None, "code": 127, "stdout": "", "stderr": "(command not found)"}
    except Exception as e:
        await _terminate_tree(proc)
        return {"ok": False, "code": -1, "stdout": "", "stderr": str(e)}

async def pre_check_workspace(
    workspace: Path,
    changed_files: List[str],
    test_command: Optional[List[str]] = None,
) -> Dict:
    """Run ruff + tests. Returns a dict the loop can inject into prompts.

    Result shape:
        {
            "lint": {"ok": bool, "output": "..."},
            "types": {"ok": bool, "output": "..."} | None,
            "tests": {"ok": bool, "output": "..."},
            "has_failures": bool,
            "summary": "2 lint errors; 1 test failure"
        }

    `test_command` is the override list, e.g. ["pytest", "-q"]. If None,
    we'"'"'ll auto-detect: pytest for .py repos, npm test for package.json.
    """
    workspace = Path(workspace)
    if not workspace.exists():
        return {"lint": None, "tests": None, "has_failures": False,
                "summary": "(workspace not found)"}

    # ---- lint ----
    py_files = [f for f in changed_files if f.endswith(".py")]
    lint = None
    if py_files and shutil.which("ruff"):
        lint = await _run(
            ["ruff", "check", "--no-fix", "--output-format=concise"] + py_files,
            cwd=workspace, timeout=30,
        )
        lint["changed_files"] = py_files

    # ---- type check ----
    types = await _type_check(workspace, py_files)

    # ---- tests ----
    tests = None
    if test_command is None:
        test_command = _auto_detect_test_command(workspace)
    if test_command:
        tests = await _run(test_command, cwd=workspace, timeout=120)

    # ---- summarize ----
    has_failures = bool(
        (lint and not lint["ok"] and lint.get("code") not in (127,))
        or (types and not types["ok"] and types.get("code") not in (127,))
        or (tests and not tests["ok"] and tests.get("code") not in (127,))
    )
    parts = []
    if lint and lint.get("code") not in (127, None):
        parts.append(f"{'pass' if lint['ok'] else 'fail'}: ruff")
    if types and types.get("code") not in (127, None):
        parts.append(f"{'pass' if types['ok'] else 'fail'}: types")
    if tests and tests.get("code") not in (127, None):
        parts.append(f"{'pass' if tests['ok'] else 'fail'}: tests")
    summary = "; ".join(parts) if parts else "no checks ran"

    return {
        "lint": lint,
        "types": types,
        "tests": tests,
        "has_failures": has_failures,
        "summary": summary,
    }

def _has_mypy_config(workspace: Path) -> bool:
    """Is this project actually configured for mypy?

    Running an unconfigured mypy on a few files reports dozens of pre-existing
    complaints about third-party stubs and untyped defs. The Coder cannot tell
    those from the ones it just caused, so an unconfigured project gets no type
    check at all -- a check that cries wolf is worse than no check.
    """
    pyproject = workspace / "pyproject.toml"
    if pyproject.exists():
        try:
            if "[tool.mypy]" in pyproject.read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            pass
    for name in ("mypy.ini", ".mypy.ini"):
        if (workspace / name).exists():
            return True
    setup_cfg = workspace / "setup.cfg"
    if setup_cfg.exists():
        try:
            if "[mypy]" in setup_cfg.read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            pass
    return False


def _local_tsc(workspace: Path) -> Optional[List[str]]:
    """Command for a *locally installed* TypeScript compiler, or None.

    Never `npx tsc`: on a project without node_modules that downloads the
    compiler, which turns a static check into a network call that hangs behind a
    firewall. If the project has not installed TypeScript, it does not get a
    type check.
    """
    if not (workspace / "tsconfig.json").exists():
        return None
    tsc = workspace / "node_modules" / "typescript" / "bin" / "tsc"
    if not tsc.exists() or not shutil.which("node"):
        return None
    return ["node", "node_modules/typescript/bin/tsc", "--noEmit"]


async def _type_check(workspace: Path, py_files: List[str]) -> Optional[Dict]:
    """Type-check the changed files, when the project is set up for it."""
    if py_files and _has_mypy_config(workspace) and shutil.which("mypy"):
        return await _run(
            ["mypy", "--no-error-summary", "--follow-imports=silent"] + py_files,
            cwd=workspace, timeout=90,
        )
    tsc = _local_tsc(workspace)
    if tsc:
        return await _run(tsc, cwd=workspace, timeout=150)
    return None


def _auto_detect_test_command(workspace: Path) -> Optional[List[str]]:
    # Never start a test suite from inside one. The loop calls this every round, and the
    # workspace is usually this very project — so the auto-detected `pytest` would run the
    # whole suite from within `tests/unit/test_loop_run.py`, which itself drives the loop,
    # which prechecks again. Each level spawns the next: the shard that hit this climbed
    # from 1 GB to 13.5 GB while eight tests timed out at 150 s apiece.
    if os.environ.get("KAIROS_INSIDE_TESTS"):
        return None
    if (workspace / "pyproject.toml").exists() or (workspace / "pytest.ini").exists():
        return ["pytest", "-q", "--tb=short", "-x"]
    if (workspace / "package.json").exists():
        return ["npm", "test", "--silent"]
    return None

def format_precheck_for_prompt(precheck: Dict) -> str:
    """Render the precheck result as a string the LLM can read.

    Returns "" if no failures (so we don'"'"'t pollute the prompt).
    """
    if not precheck.get("has_failures"):
        return ""
    lines = ["[PRECHECK FAILURES]"]
    if precheck.get("lint") and not precheck["lint"]["ok"]:
        out = (precheck["lint"].get("stdout", "") or
               precheck["lint"].get("stderr", ""))
        lines.append(f"LINT (ruff):")
        lines.append(out[-1500:])  # last 1500 chars
    if precheck.get("types") and not precheck["types"]["ok"]:
        out = (precheck["types"].get("stdout", "") or
               precheck["types"].get("stderr", ""))
        lines.append("TYPES:")
        lines.append(out[-1500:])
    if precheck.get("tests") and not precheck["tests"]["ok"]:
        out = (precheck["tests"].get("stdout", "") or
               precheck["tests"].get("stderr", ""))
        lines.append("TESTS:")
        lines.append(out[-1500:])
    return "\n".join(lines)

# ---------------------------------------------------------------- self-debug

# Regex for the common "X not found / X not installed" class of errors
# that a Coder can fix in 1 tool call.
_KNOWN_ERROR_PATTERNS = [
    (re.compile(r"ModuleNotFoundError: No module named '([^']+)'"),
     "missing_module", "pip install {match}"),
    (re.compile(r"ImportError: cannot import name '([^']+)' from '([^']+)'"),
     "bad_import", "fix import or add missing symbol"),
    (re.compile(r"PermissionError: \[Errno 13\]"),
     "permission", "chmod or pick a writable path"),
    (re.compile(r"Address already in use|port.*already.*in use", re.IGNORECASE),
     "port_in_use", "pick a different port or stop the conflicting process"),
    (re.compile(r"IndentationError|SyntaxError:"),
     "syntax_error", "fix the syntax at the reported line"),
    (re.compile(r"command not found: (\S+)"),
     "missing_binary", "install {match} or use a different command"),
]

def extract_known_fixes(stderr: str, stdout: str = "") -> List[Dict]:
    """Scan a test/lint error log for patterns the Coder can fix in 1 step.

    Returns list of {kind, match, fix_instruction}. Empty if nothing
    matches. These are surfaced to the Coder as a hint in the next
    prompt so it doesn'"'"'t have to re-discover them.
    """
    text = (stderr or "") + "\n" + (stdout or "")
    if not text.strip():
        return []
    found = []
    for pat, kind, instruction in _KNOWN_ERROR_PATTERNS:
        for m in pat.finditer(text):
            fix = instruction.format(match=m.group(1) if m.lastindex else "x")
            found.append({
                "kind": kind,
                "match": m.group(0),
                "fix_instruction": fix,
            })
            if len(found) >= 5:
                return found
    return found

def format_self_debug_hint(fixes: List[Dict]) -> str:
    """Render the known-fix list as a Coder-readable hint."""
    if not fixes:
        return ""
    lines = ["[AUTO-DETECTED FIXABLE ERRORS — try these first]"]
    for f in fixes:
        lines.append(f"- {f['kind']}: `{f['match'][:120]}` -> {f['fix_instruction']}")
    return "\n".join(lines)
