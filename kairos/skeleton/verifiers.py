"""Verifier registry + the five built-in verifier kinds.

Registered kinds (the gap report's §P0-2 list):

* ``tests``       -- run the workspace's test command (existing behaviour).
* ``assertion``   -- run a user-supplied callable or expression over the result.
* ``rubric``      -- score a list of criteria; pass when the score clears a bar.
* ``human``       -- block on an explicit human approval.
* ``tool_oracle`` -- check the result with a separate tool/probe.

The existing Reviewer is a *different* implementation of this interface (see
``kairos/skeleton/adapters.py`` and register it under ``"reviewer"``).

Every verifier returns a :class:`~kairos.skeleton.contracts.Verdict` whose
``evidence`` is a list of ``{"criterion", "satisfied"}`` records -- the
decision is data, not a trusted model score.
"""
from __future__ import annotations

import asyncio
import inspect
import subprocess
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from kairos.skeleton.contracts import Verdict, Verifier, WorkerResult, Workspace

_NULL = object()


def _normalize_outcome(raw: Any) -> Tuple[Optional[bool], List[Dict[str, Any]], str, Optional[float]]:
    """Coerce a checker's return value into ``(passed, evidence, reason, score)``.

    Accepted shapes from a user checker / oracle:

    * ``bool``                          -> passed
    * ``(bool, evidence_list_or_str)``  -> passed + evidence/reason
    * ``(bool, reason, score)``
    * ``dict`` with keys passed/reason/score/evidence
    """
    if raw is None:
        return None, [], "checker returned None", None
    if isinstance(raw, bool):
        return raw, [], "", None
    if isinstance(raw, dict):
        ev = raw.get("evidence") or []
        if not isinstance(ev, list):
            ev = [{"criterion": "evidence", "satisfied": None, "detail": str(ev)}]
        return (
            raw.get("passed"),
            ev,
            str(raw.get("reason") or ""),
            raw.get("score"),
        )
    if isinstance(raw, (tuple, list)):
        if not raw:
            return None, [], "empty outcome", None
        passed = raw[0]
        second = raw[1] if len(raw) > 1 else None
        reason = ""
        evidence: List[Dict[str, Any]] = []
        if isinstance(second, str):
            reason = second
        elif isinstance(second, list):
            evidence = second
        score = raw[2] if len(raw) > 2 else None
        return passed, evidence, reason, score
    return None, [], f"unrecognised outcome: {raw!r}", None


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class VerifierRegistry:
    """A pluggable name -> :class:`Verifier` map."""

    def __init__(self) -> None:
        self._verifiers: Dict[str, Verifier] = {}

    def register(self, verifier_or_name: Union[Verifier, str], verifier: Optional[Verifier] = None) -> Verifier:
        """Register by ``(name, verifier)`` or by the verifier's own ``.name``."""
        if isinstance(verifier_or_name, str):
            name = verifier_or_name
            obj = verifier
            if obj is None:
                raise ValueError("register(name, verifier) needs a verifier")
        else:
            obj = verifier_or_name
            name = getattr(obj, "name", "") or obj.__class__.__name__
        if not isinstance(obj, Verifier):
            raise TypeError(f"{obj!r} is not a Verifier")
        self._verifiers[name] = obj
        return obj

    def unregister(self, name: str) -> None:
        self._verifiers.pop(name, None)

    def get(self, name: str) -> Verifier:
        try:
            return self._verifiers[name]
        except KeyError:
            raise KeyError(
                f"no verifier registered as {name!r}; have {sorted(self._verifiers)}"
            )

    def names(self) -> List[str]:
        return sorted(self._verifiers)

    def __contains__(self, name: str) -> bool:
        return name in self._verifiers

    def __getitem__(self, name: str) -> Verifier:
        return self.get(name)

    async def verify(
        self, name: str, workspace: Workspace, task, result: WorkerResult
    ) -> Verdict:
        return await self.get(name).verify(workspace, task, result)


# --------------------------------------------------------------------------
# Built-in verifiers
# --------------------------------------------------------------------------

def _detect_test_command(root) -> Optional[List[str]]:
    """Auto-detect a test command the way ``loop.precheck`` does."""
    import json
    import shutil
    from pathlib import Path

    root = Path(root)
    if (root / "pytest.ini").exists() or (root / "tests").is_dir() or (root / "pyproject.toml").exists():
        if shutil.which("pytest"):
            return ["pytest", "-q"]
        return [".venv/Scripts/python.exe", "-m", "pytest", "-q"] if (root / ".venv").is_dir() else None
    if (root / "package.json").is_file():
        try:
            pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
            if "test" in (pkg.get("scripts") or {}) and shutil.which("npm"):
                return ["npm", "test", "--silent"]
        except Exception:
            pass
    return None


class ProjectTestsVerifier(Verifier):
    """Run the workspace's test command (the pre-existing verification path).

    Registered under the name ``"tests"``. Named ``ProjectTests*`` rather than
    ``Tests*`` because pytest would otherwise try to collect the class.
    """

    name = "tests"

    def __init__(self, test_command: Optional[List[str]] = None, timeout: int = 300):
        self.test_command = list(test_command) if test_command else None
        self.timeout = timeout

    async def verify(self, workspace: Workspace, task, result: WorkerResult) -> Verdict:
        if not workspace.supports("tests"):
            return Verdict(
                passed=None, verifier=self.name,
                reason=f"workspace kind {workspace.kind!r} has no 'tests' capability",
                evidence=[{"criterion": "tests capability", "satisfied": False}],
            )
        cmd = self.test_command or _detect_test_command(workspace.root)
        if not cmd:
            return Verdict(
                passed=None, verifier=self.name, reason="no test command detected",
                evidence=[{"criterion": "test command detected", "satisfied": False}],
            )
        try:
            from kairos.platform_flags import hidden_kwargs
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=str(workspace.root),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                **hidden_kwargs(),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
            code = proc.returncode
        except asyncio.TimeoutError:
            return Verdict(
                passed=False, verifier=self.name, reason=f"tests timed out after {self.timeout}s",
                score=0.0,
                evidence=[{"criterion": "tests ran", "satisfied": False,
                           "command": " ".join(cmd), "detail": "timeout"}],
            )
        except FileNotFoundError as exc:
            return Verdict(
                passed=None, verifier=self.name, reason=f"test command not found: {exc}",
                evidence=[{"criterion": "test command present", "satisfied": False}],
            )
        out = (stdout or b"").decode("utf-8", "replace")
        err = (stderr or b"").decode("utf-8", "replace")
        passed = code == 0
        return Verdict(
            passed=passed, verifier=self.name, score=100.0 if passed else 0.0,
            reason=(f"test command exited {code}" if not passed else "tests passed"),
            evidence=[{
                "criterion": "test command exit 0",
                "satisfied": passed,
                "command": " ".join(cmd),
                "returncode": code,
                "stdout_tail": out[-1500:],
                "stderr_tail": err[-1500:],
            }],
        )


class AssertionVerifier(Verifier):
    """Run a user-supplied check.

    ``check`` may be a callable ``(workspace, task, result) -> outcome`` (sync
    or async) or a string expression evaluated with ``workspace``/``task``/
    ``result`` in scope (and a minimal builtins set). The outcome is coerced by
    :func:`_normalize_outcome`.
    """

    name = "assertion"

    def __init__(self, check: Union[Callable, str, None] = None, name: str = "assertion"):
        self.check = check
        self.name = name

    async def verify(self, workspace: Workspace, task, result: WorkerResult) -> Verdict:
        if self.check is None:
            return Verdict(
                passed=None, verifier=self.name, reason="no assertion supplied",
                evidence=[{"criterion": "assertion provided", "satisfied": False}],
            )
        try:
            if isinstance(self.check, str):
                raw = eval(  # noqa: S307 - explicit user-supplied check by design
                    self.check,
                    {"__builtins__": {"len": len, "all": all, "any": any, "str": str,
                                      "int": int, "bool": bool, "set": set}},
                    {"workspace": workspace, "task": task, "result": result},
                )
            else:
                raw = await _maybe_await(self.check(workspace, task, result))
        except Exception as exc:
            return Verdict(
                passed=False, verifier=self.name, reason=f"assertion raised: {exc}",
                evidence=[{"criterion": "assertion ran", "satisfied": False, "detail": str(exc)}],
            )
        passed, evidence, reason, score = _normalize_outcome(raw)
        return Verdict(passed=passed, verifier=self.name, reason=reason,
                       score=score, evidence=evidence)


class RubricVerifier(Verifier):
    """Score a rubric of criteria; pass when the satisfied fraction clears a bar.

    ``criteria`` is a list of ``(label, predicate)`` pairs (predicates take
    ``(workspace, task, result)``) or ``(label, bool)`` pairs for static rules.
    This is a *structured* rubric: each row's boolean is the evidence.
    """

    name = "rubric"

    def __init__(self, criteria: Optional[List[Tuple[str, Any]]] = None,
                 threshold: float = 1.0, name: str = "rubric"):
        self.criteria = list(criteria or [])
        self.threshold = threshold
        self.name = name

    async def verify(self, workspace: Workspace, task, result: WorkerResult) -> Verdict:
        if not self.criteria:
            return Verdict(
                passed=None, verifier=self.name, reason="no rubric criteria supplied",
                evidence=[{"criterion": "rubric supplied", "satisfied": False}],
            )
        evidence: List[Dict[str, Any]] = []
        satisfied = 0
        for label, pred in self.criteria:
            try:
                if callable(pred):
                    outcome = await _maybe_await(pred(workspace, task, result))
                else:
                    outcome = pred
                ok = bool(outcome)
            except Exception as exc:
                ok = False
                evidence.append({"criterion": label, "satisfied": False, "detail": str(exc)})
                continue
            satisfied += int(ok)
            evidence.append({"criterion": label, "satisfied": ok})
        total = len(self.criteria)
        score = round(100.0 * satisfied / total, 1)
        passed = (satisfied / total) >= self.threshold
        return Verdict(
            passed=passed, verifier=self.name, score=score,
            reason=f"{satisfied}/{total} criteria met (threshold {self.threshold:g})",
            evidence=evidence,
        )


class HumanVerifier(Verifier):
    """An explicit human gate: return an undecided, human-blocked verdict."""

    name = "human"

    async def verify(self, workspace: Workspace, task, result: WorkerResult) -> Verdict:
        return Verdict(
            passed=None, verifier=self.name, requires_human=True,
            reason="awaiting human approval",
            evidence=[{"criterion": "human approval", "satisfied": None}],
        )


class ToolOracleVerifier(Verifier):
    """Verify with a separate tool/probe instead of the producer's own word.

    ``oracle`` is a callable ``(workspace, task, result) -> outcome`` (sync or
    async) that usually re-reads the emitted artifact through the workspace
    (e.g. an independent citation counter).
    """

    name = "tool_oracle"

    def __init__(self, oracle: Callable, name: str = "tool_oracle"):
        self.oracle = oracle
        self.name = name

    async def verify(self, workspace: Workspace, task, result: WorkerResult) -> Verdict:
        try:
            raw = await _maybe_await(self.oracle(workspace, task, result))
        except Exception as exc:
            return Verdict(
                passed=False, verifier=self.name, reason=f"oracle raised: {exc}",
                evidence=[{"criterion": "oracle ran", "satisfied": False, "detail": str(exc)}],
            )
        passed, evidence, reason, score = _normalize_outcome(raw)
        return Verdict(passed=passed, verifier=self.name, reason=reason,
                       score=score, evidence=evidence)


def build_default_registry(*, include_assertion_placeholder: bool = True) -> VerifierRegistry:
    """Register the five built-in verifier kinds.

    ``assertion`` is registered with no check so the name exists; a caller that
    wants a real assertion re-registers ``AssertionVerifier(check=...)``.
    """
    reg = VerifierRegistry()
    reg.register(ProjectTestsVerifier())
    if include_assertion_placeholder:
        reg.register(AssertionVerifier())
    reg.register(RubricVerifier())
    reg.register(HumanVerifier())
    reg.register(ToolOracleVerifier(oracle=lambda w, t, r: (None, "no oracle supplied")))
    return reg
