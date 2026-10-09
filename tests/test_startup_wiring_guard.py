"""Static guards for ``api/app.py`` — undefined names and startup wiring.

Two classes of bug this file exists to prevent, both real and both invisible at
runtime:

1. **An undefined name in the startup blocks.** ``api/app.py`` used to call
   ``_orch()`` while the module neither defined nor imported that name. The
   ``NameError`` was swallowed by the surrounding ``except``, so the approval
   channel, the long-running registry and the daemon supervisor never came up —
   and with no approval channel the gate's ASK verdict fell through to allowing
   the action (``kairos/sentinel.py``). It is the same shape as the earlier
   browser / voice / sentinel omissions: wiring that silently does nothing.

2. **A startup subsystem that is never registered.** The lifespan assembles
   subsystems each in its own ``try``; ``api.app._STARTUP_SUBSYSTEMS`` is the
   explicit list of what must prove it is alive (asserted in
   ``tests/test_startup_registry.py``). This file's second guard reads the
   lifespan source and refuses any bootstrap call that is neither mapped to a
   registered subsystem nor in an explicit, reason-carrying whitelist — so
   *forgetting to register a new subsystem fails a test* instead of shipping.

Everything here is standard library (``ast`` + ``symtable``); the guard does
not depend on ruff/flake8 being installed in CI.
"""

from __future__ import annotations

import ast
import builtins
import subprocess
import symtable
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = REPO_ROOT / "api" / "app.py"

# Names the interpreter always defines (dunder globals + the implicit
# ``__class__`` cell a method that uses ``super()``/``__class__`` creates).
_EXTRA_DEFINED = {
    "__name__", "__file__", "__doc__", "__package__", "__spec__",
    "__loader__", "__builtins__", "__debug__", "__class__",
}


# ---------------------------------------------------------------------------
# Defense 1: undefined-name (F821) check, stdlib only.
# ---------------------------------------------------------------------------
def _module_bound_names(module_table: symtable.SymbolTable) -> set[str]:
    """Names bound at module level: imports, def/class, assignments."""
    bound: set[str] = set()
    for sym in module_table.get_symbols():
        if (sym.is_assigned() or sym.is_imported()
                or sym.is_namespace() or sym.is_parameter()):
            bound.add(sym.get_name())
    return bound


def _iter_scopes(table: symtable.SymbolTable):
    yield table
    for child in table.get_children():
        yield from _iter_scopes(child)


def find_undefined_names(source: str, filename: str) -> list[str]:
    """Return ``filename:line: Undefined name 'x'`` for every F821-style use.

    A name is *undefined* when it is referenced from some scope yet resolves to
    neither a local/parameter/import in that scope, nor a free variable of an
    enclosing function, nor a module-level binding, nor a builtin. ``symtable``
    does the scope resolution (so comprehensions, closures and nested ``def``s
    are handled correctly); ``ast`` supplies the line number.
    """
    tree = ast.parse(source, filename)
    module_table = symtable.symtable(source, filename, "exec")
    defined = _module_bound_names(module_table) | set(dir(builtins)) | _EXTRA_DEFINED

    undefined: set[str] = set()
    for table in _iter_scopes(module_table):
        for sym in table.get_symbols():
            if not sym.is_referenced():
                continue
            # Bound *in this scope* (local/param/import), or resolved by an
            # enclosing function scope (free) — not undefined.
            if sym.is_local() or sym.is_free() or sym.is_imported() or sym.is_parameter():
                continue
            if sym.get_name() not in defined:
                undefined.add(sym.get_name())

    first_line: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            cur = first_line.get(node.id)
            if cur is None or node.lineno < cur:
                first_line[node.id] = node.lineno

    findings = [
        (first_line.get(name, 0), name) for name in undefined
    ]
    findings.sort()
    return [f"{filename}:{line}: Undefined name '{name}'"
            for line, name in findings]


def test_app_module_has_no_undefined_names():
    """The whole point: ``import api.app`` must not hide a name it never binds."""
    findings = find_undefined_names(
        APP_PATH.read_text(encoding="utf-8"), "api/app.py")
    assert findings == [], (
        "api/app.py references names it neither binds nor imports — during "
        "startup such a NameError is swallowed by the surrounding except and "
        "the subsystem silently never starts:\n  " + "\n  ".join(findings)
    )


def test_undefined_name_checker_catches_the_historical_orch_error():
    """Hermetic red proof: the exact ``_orch`` shape must be flagged.

    This runs everywhere (no git history needed): it is the same source shape
    that shipped, reduced to the two lines that mattered.
    """
    snippet = (
        "from contextlib import asynccontextmanager\n"
        "log = None\n"
        "\n"
        "@asynccontextmanager\n"
        "async def lifespan(app):\n"
        "    try:\n"
        "        from kairos import approvals\n"
        "        approvals.set_channel(\n"
        "            approvals.ApprovalChannel(message_bus=_orch().message_bus))\n"
        "    except Exception as exc:\n"
        "        log.warning('approval channel unavailable: %s', exc)\n"
        "    yield\n"
    )
    findings = find_undefined_names(snippet, "snippet.py")
    assert any("'_orch'" in f for f in findings), (
        "the checker failed to flag the _orch NameError it exists to catch; "
        f"findings were {findings!r}"
    )


def _git_show(rev_path: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "show", rev_path],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def test_the_pre_fix_app_module_reported_the_orch_nameerror():
    """Red proof on the *real* pre-fix file, when git history has it.

    ``57221b4^:api/app.py`` is the tree just before ``_orch`` was defined. In a
    shallow CI checkout this revision is absent and the test skips — the
    hermetic test above still runs. Locally (full history) it must report
    ``_orch``.
    """
    old_source = _git_show("57221b4^:api/app.py")
    if old_source is None:
        pytest.skip("git history for 57221b4^ unavailable (shallow checkout)")
    findings = find_undefined_names(old_source, "api/app.py (57221b4^)")
    assert any("'_orch'" in f for f in findings), (
        "the guard must flag _orch in the pre-fix module; "
        f"findings were {findings!r}"
    )
    # And it must be the *only* undefined name — no false positives on a real
    # 600-line module.
    orch = [f for f in findings if "'_orch'" in f]
    assert len(orch) == 1, findings


# ---------------------------------------------------------------------------
# Defense 3 (source half): every startup call is registered or whitelisted.
# ---------------------------------------------------------------------------
_BOOTSTRAP_VERBS = {
    "start", "start_all", "start_account",
    "set_channel", "set_registry", "set_worker", "set_supervisor",
    "set_manager", "set_default_manager", "set_store", "set_dependencies",
    "set_approval_bridge",
    "init", "attach",
}
_BOOTSTRAP_FUNCS = {
    "set_channel", "set_registry", "set_worker", "set_supervisor",
    "set_manager", "set_default_manager", "set_store", "set_dependencies",
    "set_approval_bridge",
}

# token (as produced by _call_token) -> subsystem name in _STARTUP_SUBSYSTEMS.
# Adding a subsystem = adding its wiring calls here *and* a registry entry;
# tests/test_startup_registry.py fails if either half is missing.
_TOKEN_TO_SUBSYSTEM = {
    "approvals.set_channel": "approvals",
    "set_registry": "long_running_registry",
    "w.attach": "autonomous_worker",
    "w.start": "autonomous_worker",
    "set_worker": "autonomous_worker",
    "s.attach": "daemon_supervisor",
    "s.start": "daemon_supervisor",
    "set_supervisor": "daemon_supervisor",
    "_browser_manager.start": "browser_manager",
    "browser_routes.set_manager": "browser_manager",
    "set_default_manager": "browser_manager",
    "_feishu_store.init": "feishu",
    "_feishu_forwarder.attach": "feishu",
    "_feishu_forwarder.start": "feishu",
    "feishu_routes.set_dependencies": "feishu",
    "_wecom_bindings.init": "wecom",
    "_wecom_forwarder.attach": "wecom",
    "_wecom_forwarder.start": "wecom",
    "wecom_routes.set_dependencies": "wecom",
    "_im_store.init": "im_store",
    "im_routes.set_store": "im_store",
    "_weixin_store.init": "weixin_ilink",
    "_weixin_channel.start_account": "weixin_ilink",
    "_weixin_approval_bridge.attach": "weixin_ilink",
    "weixin_routes.set_dependencies": "weixin_ilink",
    "weixin_routes.set_approval_bridge": "weixin_ilink",
}

# Startup calls that are deliberately *not* a registered liveness invariant.
# Each needs a reason; a stale entry (token no longer present) is an error so
# the whitelist cannot rot.
_WHITELISTED_STARTUP_CALLS = {
    "reg.start_all": (
        "MCP servers are user-configured; they are warmed on a background "
        "task and may legitimately be absent, so liveness is not a fixed "
        "startup invariant"
    ),
}


def _call_token(node: ast.Call) -> str | None:
    """Reduce a bootstrap call to a stable token, or None if not one."""
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in _BOOTSTRAP_VERBS:
        base = func.value
        while isinstance(base, ast.Attribute):
            base = base.value
        if isinstance(base, ast.Name):
            return f"{base.id}.{func.attr}"
        return f"<expr>.{func.attr}"
    if isinstance(func, ast.Name) and func.id in _BOOTSTRAP_FUNCS:
        return func.id
    return None


def _lifespan_function(tree: ast.Module) -> ast.AST:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == "lifespan":
            return node
    raise AssertionError("api/app.py has no 'lifespan' function")


def _startup_calls(source: str) -> dict[str, int]:
    """token -> first line, for bootstrap calls in the lifespan assembly half."""
    tree = ast.parse(source)
    fn = _lifespan_function(tree)
    yield_line = None
    for stmt in fn.body:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Name) \
                and stmt.value.id == "yield":
            yield_line = stmt.lineno
            break
    calls: dict[str, int] = {}
    for node in ast.walk(fn):
        if yield_line is not None and node.lineno >= yield_line:
            continue  # shutdown half: not startup wiring
        if not isinstance(node, ast.Call):
            continue
        token = _call_token(node)
        if token is not None and token not in calls:
            calls[token] = node.lineno
    return calls


def audit_startup_calls(source: str, registered_names: set[str]) -> list[str]:
    """Return violations: unregistered startup calls, orphaned/missing wiring."""
    violations: list[str] = []
    present = _startup_calls(source)
    for token, line in sorted(present.items(), key=lambda kv: kv[1]):
        if token in _WHITELISTED_STARTUP_CALLS:
            continue
        subsystem = _TOKEN_TO_SUBSYSTEM.get(token)
        if subsystem is None:
            violations.append(
                f"api/app.py:{line}: unregistered startup call '{token}' — "
                "map it to a _STARTUP_SUBSYSTEMS entry (and register a probe) "
                "or whitelist it with a reason in tests/test_startup_wiring_guard.py"
            )
        elif subsystem not in registered_names:
            violations.append(
                f"api/app.py:{line}: startup call '{token}' maps to subsystem "
                f"'{subsystem}', which is not in _STARTUP_SUBSYSTEMS"
            )
    # Stale whitelist: an entry whose call has disappeared is dead weight.
    for token, reason in _WHITELISTED_STARTUP_CALLS.items():
        if token not in present:
            violations.append(
                f"stale whitelist entry '{token}' ({reason!r}) no longer "
                "appears in the lifespan — remove it"
            )
    # Orphaned registry entry: a subsystem with no startup call mapped to it
    # can never actually be assembled.
    mapped = set(_TOKEN_TO_SUBSYSTEM.values())
    for name in sorted(registered_names):
        if name not in mapped:
            violations.append(
                f"registry subsystem '{name}' has no startup call mapped to "
                "it — either it is wired under a call this guard does not "
                "recognise, or the entry is dead"
            )
    return violations


def _registered_names() -> set[str]:
    from api.app import _STARTUP_SUBSYSTEMS
    return {name for name, _probe in _STARTUP_SUBSYSTEMS}


def test_every_startup_call_is_registered_or_whitelisted():
    violations = audit_startup_calls(
        APP_PATH.read_text(encoding="utf-8"), _registered_names())
    assert violations == [], (
        "a lifespan startup call is neither mapped to a registered subsystem "
        "nor whitelisted with a reason:\n  " + "\n  ".join(violations)
    )


def test_source_guard_catches_an_unregistered_start_call():
    """Red proof: inject a rogue ``_rogue.start()`` and the guard must name it."""
    source = APP_PATH.read_text(encoding="utf-8")
    injected = source.replace(
        "    yield\n",
        "    _rogue_subsystem.start()\n    yield\n",
        1,
    )
    assert injected != source, "could not inject the rogue call"
    violations = audit_startup_calls(injected, _registered_names())
    assert any("_rogue_subsystem.start" in v for v in violations), (
        f"the guard failed to catch an unregistered start() call; got {violations!r}"
    )


def test_source_guard_catches_a_dropped_registry_entry():
    """Red proof: dropping a registry name orphans its wiring → guard red."""
    source = APP_PATH.read_text(encoding="utf-8")
    names = _registered_names() - {"browser_manager"}
    violations = audit_startup_calls(source, names)
    assert any("browser_manager" in v for v in violations), (
        f"the guard failed to notice a dropped registry entry; got {violations!r}"
    )


# ---------------------------------------------------------------------------
# Defense 2 (source half): no ``except Exception`` in the lifespan may be bare.
# ---------------------------------------------------------------------------
def _catches_exception(handler: ast.ExceptHandler) -> bool:
    """True when this handler would swallow a normal (non-cancellation) error."""
    if handler.type is None:
        return True  # bare ``except:``
    names: list[str] = []
    for node in ast.walk(handler.type):
        if isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.Attribute):
            names.append(node.attr)
    # ``except asyncio.CancelledError`` is an intentional cancellation, not a
    # swallowed failure; anything naming Exception (or a tuple including it) is.
    return any(n == "Exception" for n in names) or handler.type is None


def _handler_records_failure(handler: ast.ExceptHandler) -> bool:
    for node in ast.walk(handler):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "_record_startup_failure":
                return True
            if isinstance(func, ast.Attribute) \
                    and func.attr == "_record_startup_failure":
                return True
    return False


def test_every_lifespan_except_exception_records_a_failure():
    """A silent ``except Exception: pass`` in startup is the original bug.

    Every handler in ``lifespan`` that catches ``Exception`` must call
    ``_record_startup_failure`` at least once. ``except asyncio.CancelledError``
    is exempt — it is an expected cancellation, not a swallowed failure.
    """
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    fn = _lifespan_function(tree)
    offenders: list[str] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.ExceptHandler) and _catches_exception(node):
            if not _handler_records_failure(node):
                offenders.append(
                    f"api/app.py:{node.lineno}: except Exception neither "
                    "records a startup failure nor is annotated as ignorable"
                )
    assert offenders == [], (
        "a startup/shutdown exception is swallowed without a trace:\n  "
        + "\n  ".join(offenders)
    )
