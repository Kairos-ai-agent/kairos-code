"""Verifier registry + the five built-in verifier kinds.

Registered kinds (the gap report's §P0-2 list):

* ``tests``       -- run the workspace's test command (existing behaviour).
* ``assertion``   -- run a user-supplied callable or *safe* expression.
* ``rubric``      -- score a list of criteria; pass when the score clears a bar.
* ``human``       -- block on an explicit human approval (a gate, not a decider).
* ``tool_oracle`` -- check the result with a separate tool/probe.

The existing Reviewer is a *different* implementation of this interface (see
``kairos/skeleton/adapters.py`` and register it under ``"reviewer"``).

Every verifier returns a :class:`~kairos.skeleton.contracts.Verdict` whose
``evidence`` is a list of ``{"criterion", "satisfied"}`` records -- the
decision is data, not a trusted model score.

Honesty about readiness
-----------------------
A verifier that has nothing to check against is *not* a verifier that passed.
``assertion`` / ``rubric`` / ``tool_oracle`` are registered by name but start
unconfigured: they abstain (``passed=None``) with an explicit reason until a
check / criteria / oracle is supplied. ``Verifier.ready`` and
:meth:`VerifierRegistry.ready_names` expose which kinds can actually decide
right now, so "five kinds registered" is never mistaken for "five kinds work".
"""
from __future__ import annotations

import ast
import asyncio
import inspect
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from kairos.skeleton.contracts import Verdict, Verifier, WorkerResult, Workspace
from kairos.test_command import detect_project_test_command

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


# --------------------------------------------------------------------------
# Safe evaluation of string assertions
# --------------------------------------------------------------------------

#: AST node types a string assertion is allowed to contain. Attribute access is
#: deliberately absent: an attribute chain is the classic sandbox escape
#: (``().__class__.__base__.__subclasses__()``), and a restricted
#: ``__builtins__`` does not stop it. Comprehensions/lambdas are excluded too
#: (they can smuggle in new scopes); the evaluator stays a small comparison /
#: boolean language.
_ALLOWED_AST_NODES: Tuple[type, ...] = (
    ast.Expression,
    ast.BoolOp, ast.And, ast.Or,
    ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
    ast.UnaryOp, ast.Not, ast.USub, ast.UAdd,
    ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.In, ast.NotIn, ast.Is, ast.IsNot,
    ast.IfExp,
    ast.Name, ast.Load,
    ast.Constant,
    ast.List, ast.Tuple, ast.Set, ast.Dict,
    ast.Subscript, ast.Slice,
    ast.Call, ast.keyword,
)

#: The only callables a string assertion may call. Everything else -- and any
#: attribute method call -- is rejected at validation time.
_SAFE_FUNCTIONS: Dict[str, Callable] = {
    "len": len, "all": all, "any": any, "sorted": sorted,
    "min": min, "max": max, "sum": sum, "abs": abs,
    "str": str, "int": int, "float": float, "bool": bool,
    "list": list, "tuple": tuple, "dict": dict, "set": set,
    "repr": repr, "isinstance": isinstance,
}


def _validate_expression(tree: ast.AST, allowed_names: set) -> None:
    """Reject anything outside the small, escape-proof AST subset."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            # ANY attribute access is rejected, deliberately NOT just dunder
            # names: ``x.__class__`` is the famous escape door, but ``x.foo``
            # is the very same door. Do NOT narrow this to dunder-only to make
            # ``result.output`` parse -- reach into objects with a *callable*
            # check instead (it is unrestricted and runs as trusted code).
            raise ValueError(
                "attribute access is not allowed in a string check "
                "(use a callable check instead)"
            )
        if isinstance(node, ast.Name):
            name = node.id
            if name.startswith("__") or name.startswith("_"):
                raise ValueError(f"private/dunder name {name!r} is not allowed")
            if name not in allowed_names and name not in _SAFE_FUNCTIONS:
                raise ValueError(f"unknown name {name!r}")
        if isinstance(node, ast.Call):
            func = node.func
            if not (isinstance(func, ast.Name) and func.id in _SAFE_FUNCTIONS):
                raise ValueError("only whitelisted functions may be called")
        if not isinstance(node, _ALLOWED_AST_NODES):
            raise ValueError(
                f"{type(node).__name__} is not allowed in a string check"
            )


def _safe_eval_expression(expr: str, scope: Dict[str, Any]) -> Any:
    """Evaluate a string assertion inside a locked-down evaluator.

    The expression is parsed and validated against :data:`_ALLOWED_AST_NODES`
    *before* it runs: no attribute access, no dunder/private names, no calls
    except :data:`_SAFE_FUNCTIONS`, and no ``__builtins__``. A hostile
    expression (``().__class__.__base__``, ``__import__('os')``) is **rejected**
    -- raising ``ValueError`` -- rather than executed.
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"invalid expression: {exc}") from exc
    _validate_expression(tree, set(scope))
    namespace: Dict[str, Any] = {"__builtins__": {}}
    namespace.update(_SAFE_FUNCTIONS)
    namespace.update(scope)
    return eval(compile(tree, "<assertion>", "eval"), namespace)  # noqa: S307 - validated above


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

    def ready_names(self) -> List[str]:
        """Names whose verifier is configured enough to be consulted."""
        return sorted(n for n, v in self._verifiers.items() if getattr(v, "ready", True))

    def deciding_names(self) -> List[str]:
        """Ready names that can return a real ``passed`` (i.e. not a human gate)."""
        return sorted(
            n for n, v in self._verifiers.items()
            if getattr(v, "ready", True) and getattr(v, "decides", True)
        )

    def describe(self) -> Dict[str, Dict[str, bool]]:
        return {
            n: {"ready": bool(getattr(v, "ready", True)),
                "decides": bool(getattr(v, "decides", True))}
            for n, v in sorted(self._verifiers.items())
        }

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
    """Auto-detect a cross-platform test command.

    Delegates to :func:`kairos.test_command.detect_project_test_command` -- the
    one place the venv layout is resolved -- so this and
    ``loop.precheck._auto_detect_test_command`` cannot drift. Prefers the
    project's own ``.venv`` interpreter (``bin/python`` on POSIX,
    ``Scripts/python.exe`` on Windows) and falls back to the global ``pytest``
    console script.
    """
    return detect_project_test_command(
        root,
        pytest_args=("-q",),
        prefer_venv_python=True,
        require_pytest_on_path=True,
        marker_files=("pyproject.toml", "pytest.ini"),
        marker_dirs=("tests",),
        check_npm_on_path=True,
    )


class ProjectTestsVerifier(Verifier):
    """Run the workspace's test command (the pre-existing verification path).

    Registered under the name ``"tests"``. Named ``ProjectTests*`` rather than
    ``Tests*`` because pytest would otherwise try to collect the class.
    """

    name = "tests"

    def __init__(self, test_command: Optional[List[str]] = None, timeout: int = 300):
        self.test_command = list(test_command) if test_command else None
        self.timeout = timeout

    @property
    def ready(self) -> bool:
        # Can decide on its own (it abstains honestly when a workspace has no
        # tests, which is not the same as being unconfigured).
        return True

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

    ``check`` is **preferably a callable** ``(workspace, task, result) ->
    outcome`` (sync or async). A string is also accepted, but only for
    **trusted input** (a human-authored rule, a config file): it is evaluated
    by :func:`_safe_eval_expression`, a locked-down evaluator that rejects
    attribute access, dunder/private names, and any call beyond a small
    whitelist -- so a check that arrived from a model or an untrusted user
    cannot reach ``().__class__.__base__.__subclasses__()`` or ``__import__``.
    The expression sees ``workspace`` / ``task`` / ``result`` plus the
    convenience names ``output`` (``result.output``), ``artifacts`` and ``ok``.
    The outcome is coerced by :func:`_normalize_outcome`.
    """

    name = "assertion"

    def __init__(self, check: Union[Callable, str, None] = None, name: str = "assertion"):
        self.check = check
        self.name = name

    @property
    def ready(self) -> bool:
        return self.check is not None

    async def verify(self, workspace: Workspace, task, result: WorkerResult) -> Verdict:
        if self.check is None:
            return Verdict(
                passed=None, verifier=self.name, reason="no assertion supplied",
                evidence=[{"criterion": "assertion provided", "satisfied": False}],
            )
        if isinstance(self.check, str):
            scope = {
                "workspace": workspace, "task": task, "result": result,
                "output": result.output or "",
                "artifacts": list(result.artifacts or []),
                "ok": bool(result.ok),
            }
            try:
                raw = _safe_eval_expression(self.check, scope)
            except Exception as exc:
                return Verdict(
                    passed=False, verifier=self.name,
                    reason=f"assertion rejected: {exc}",
                    evidence=[{"criterion": "assertion expression is safe",
                               "satisfied": False, "detail": str(exc)}],
                )
        else:
            try:
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

    @property
    def ready(self) -> bool:
        return bool(self.criteria)

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
    """An explicit human gate: return an undecided, human-blocked verdict.

    This is a *gate*, not a decider -- it never returns ``passed`` True/False.
    The driver persists the run and can :func:`resume <kairos.skeleton.driver.resume_task>`
    it once the human answers.
    """

    name = "human"

    @property
    def decides(self) -> bool:
        return False

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
    (e.g. an independent citation counter). ``oracle`` may be omitted at
    construction and set later; until an oracle is supplied the verifier is
    *unconfigured* (``ready`` is False) and abstains (``passed=None``) with an
    explicit reason rather than emitting a fake verdict.
    """

    name = "tool_oracle"

    def __init__(self, oracle: Optional[Callable] = None, name: str = "tool_oracle"):
        self.oracle = oracle
        self.name = name

    @property
    def ready(self) -> bool:
        return self.oracle is not None

    async def verify(self, workspace: Workspace, task, result: WorkerResult) -> Verdict:
        if self.oracle is None:
            return Verdict(
                passed=None, verifier=self.name,
                reason="tool_oracle has no oracle configured",
                evidence=[{"criterion": "oracle configured", "satisfied": False}],
            )
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


def build_default_registry(*, include_unconfigured: bool = True) -> VerifierRegistry:
    """Register the five built-in verifier kinds, honestly.

    ``tests`` can decide on its own. ``assertion`` / ``rubric`` /
    ``tool_oracle`` need configuration (a check / criteria / oracle) and are
    registered *either* as unconfigured stubs that abstain with a clear reason
    (``include_unconfigured=True``, the default) *or* not at all
    (``include_unconfigured=False``). ``human`` is a gate, not a decider.

    Callers should read :meth:`VerifierRegistry.ready_names` /
    :meth:`VerifierRegistry.deciding_names` instead of assuming "five names
    registered" means "five kinds work".
    """
    reg = VerifierRegistry()
    reg.register(ProjectTestsVerifier())
    if include_unconfigured:
        reg.register(AssertionVerifier())
        reg.register(RubricVerifier())
    reg.register(HumanVerifier())
    if include_unconfigured:
        reg.register(ToolOracleVerifier())
    return reg
