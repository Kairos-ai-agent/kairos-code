"""Sandboxed candidate execution.

The benchmark runner writes a candidate solution to a temp file,
imports it, and runs a small set of assert tests. Everything is
done in a subprocess so an infinite loop in the candidate cannot
hang the test runner.

Security: we run the candidate under the current Python interpreter
with ``-I`` (isolated mode) and disable writing outside the temp
dir via ``PYTHONDONTWRITEBYTECODE=1`` and a temp ``HOME``/``TMPDIR``.
This is NOT a hard sandbox (use kairos.sandbox for that) but it's
good enough for benchmark eval and won't trash the user's files.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Tuple

from .problems import BenchmarkProblem


# Template that the runner writes to a temp file. It defines the
# candidate code, then runs each assert. We use a try/except so a
# single bad test doesn't kill the whole run.
_RUNNER_TEMPLATE = textwrap.dedent('''
    import sys
    import json
    candidate = {candidate!r}
    entry_point = {entry!r}
    tests = {tests!r}

    # Pre-define the candidate module.
    ns = {{}}
    try:
        exec(candidate, ns)
    except Exception as e:
        print("COMPILE_ERROR:", repr(e))
        sys.exit(0)

    # Pull the entry point out of the namespace.
    fn = ns.get(entry_point)
    if fn is None:
        print("MISSING_ENTRY:", entry_point)
        sys.exit(0)

    passed = 0
    failed = 0
    failures = []
    for t in tests:
        try:
            exec(t, ns)
            passed += 1
        except Exception as e:
            failed += 1
            failures.append(f"{{t}}: {{e!r}}")
    print(json.dumps({{
        "passed": passed,
        "failed": failed,
        "failures": failures[:5],
    }}))
''').strip()


def run_candidate(
    problem: BenchmarkProblem,
    candidate: str,
    *,
    timeout_s: float | None = None,
) -> Tuple[bool, str]:
    """Run *candidate* against *problem* in an isolated subprocess.

    Returns ``(passed, message)``. ``message`` is either a JSON
    report (for the assert path) or a short error string.
    """
    timeout = timeout_s if timeout_s is not None else problem.timeout_s

    # Strip Markdown fencing if the agent emitted ```python ... ```.
    code = _strip_code_fence(candidate)

    # Determine test set: HumanEvalProblem and MBPPProblem carry .tests.
    tests = getattr(problem, "tests", None) or []

    script = _RUNNER_TEMPLATE.format(
        candidate=code, entry=problem.entry_point, tests=list(tests),
    )

    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    # Isolate the temp dir; do not let the candidate scribble HOME.
    with tempfile.TemporaryDirectory(prefix="kairos-bench-") as tmp:
        env["HOME"] = tmp
        env["TMPDIR"] = tmp
        env["TEMP"] = tmp
        env["TMP"] = tmp
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-c", script],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return False, f"timeout after {timeout}s"
        except Exception as exc:  # noqa: BLE001
            return False, f"runner error: {exc}"

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if out.startswith("COMPILE_ERROR:"):
        return False, out
    if out.startswith("MISSING_ENTRY:"):
        return False, out
    if not out:
        return False, f"no output (stderr={err[:300]})"
    # The runner emits a JSON line. Parse it.
    try:
        import json
        report = json.loads(out.splitlines()[-1])
    except Exception:
        return False, f"unparseable output: {out[:300]}"
    passed_count = int(report.get("passed", 0))
    failed_count = int(report.get("failed", 0))
    failures = list(report.get("failures") or [])
    if failed_count == 0 and passed_count > 0:
        return True, f"all {passed_count} tests passed"
    return False, " | ".join(failures) if failures else f"0/{passed_count + failed_count} tests passed"


_FENCE_RE = None


def _strip_code_fence(text: str) -> str:
    """Remove ```python ... ``` markdown fences if present.

    Tolerant of leading/trailing whitespace and missing language tag.
    """
    s = text.strip()
    if not s.startswith("```"):
        return s
    # Find first newline (the language tag line).
    first_nl = s.find("\n")
    if first_nl < 0:
        return s
    body = s[first_nl + 1:]
    # Trim closing fence.
    if body.rstrip().endswith("```"):
        body = body.rstrip()[:-3].rstrip()
    return body
