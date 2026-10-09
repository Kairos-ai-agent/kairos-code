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


# AST nodes that open a new symbol-table scope.
_SCOPE_NODES = (
    ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
    ast.ClassDef, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
)


def _scope_parent_parts(node):
    """Sub-nodes of a scope opener that resolve in the *enclosing* scope.

    Symtable attributes decorators, argument defaults/annotations and (for a
    comprehension) the first generator's ``iter`` to the outer scope, so the
    walker must visit those with the parent's table to stay in lock-step.
    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        parts = list(node.decorator_list)
        parts.append(node.args)
        if node.returns is not None:
            parts.append(node.returns)
        return parts
    if isinstance(node, ast.Lambda):
        return [node.args]
    if isinstance(node, ast.ClassDef):
        return list(node.decorator_list) + list(node.bases) + list(node.keywords)
    if isinstance(node, (ast.ListComp, ast.SetComp,
                         ast.DictComp, ast.GeneratorExp)):
        return [node.generators[0].iter]
    return []


def _scope_child_parts(node):
    """Sub-nodes of a scope opener that resolve in the *new* scope."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return list(node.body)
    if isinstance(node, ast.Lambda):
        return [node.body]
    if isinstance(node, (ast.ListComp, ast.SetComp,
                         ast.DictComp, ast.GeneratorExp)):
        gens = node.generators
        first = gens[0]
        out = [first.target] + list(first.ifs)
        for gen in gens[1:]:
            out += [gen.iter, gen.target]
            out += list(gen.ifs)
        elt = getattr(node, "elt", None)
        key = getattr(node, "key", None)
        out.append(elt if elt is not None else key)
        if isinstance(node, ast.DictComp):
            out.append(node.value)
        return [x for x in out if x is not None]
    return []


def find_undefined_names(source: str, filename: str) -> list[str]:
    """Return ``filename:line: Undefined name 'x'`` for every F821-style use.

    A name is *undefined* when a reference to it, **in the exact scope it is
    written in**, resolves to neither a local/parameter/import of that scope,
    nor a free variable of an enclosing function, nor a module-level binding,
    nor a builtin.

    ``symtable`` does the scope resolution and ``ast`` supplies both the line
    number and the scope→node mapping, so a name defined in one function is
    still reported when a *different* function references it without binding
    it. (Tracking the scope matters: an earlier version deduped undefined names
    by name alone and reported each at the file's first load line, which made
    it flag a *defined* use — ``api/routes/cost.py:68`` — instead of the two
    real misses at ``:230``/``:251``.)
    """
    tree = ast.parse(source, filename)
    module_table = symtable.symtable(source, filename, "exec")
    defined = _module_bound_names(module_table) | set(dir(builtins)) | _EXTRA_DEFINED

    def _is_undefined(table: symtable.SymbolTable, name: str) -> bool:
        try:
            sym = table.lookup(name)
        except KeyError:
            return False  # not referenced in this scope
        if not sym.is_referenced():
            return False
        # Bound here (local/param/import) or closed over (free): fine.
        if (sym.is_local() or sym.is_free() or sym.is_imported()
                or sym.is_parameter()):
            return False
        return name not in defined

    findings: set[tuple[int, str]] = set()

    def _walk(nodes, table: symtable.SymbolTable) -> None:
        # Symtable creates exactly one child table per scope opener, in source
        # order, so consuming ``get_children()`` as the walker meets each
        # opener keeps the AST node and its symbol table aligned.
        children = list(table.get_children())
        idx = 0

        def _next_child() -> symtable.SymbolTable:
            nonlocal idx
            child = children[idx]
            idx += 1
            return child

        def _scan(node) -> None:
            if isinstance(node, _SCOPE_NODES):
                child = _next_child()
                for part in _scope_parent_parts(node):
                    _scan(part)
                _walk(_scope_child_parts(node), child)
                return
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if _is_undefined(table, node.id):
                    findings.add((node.lineno, node.id))
                return
            for sub in ast.iter_child_nodes(node):
                _scan(sub)

        for node in nodes:
            _scan(node)
        assert idx == len(children), (
            f"scope/symtable desync in {table.get_name()} ({filename})")

    _walk(list(tree.body), module_table)
    return [f"{filename}:{line}: Undefined name '{name}'"
            for line, name in sorted(findings)]


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
    # Every finding must be a *genuine* use of _orch — no false positives on a
    # real 600-line module. (Each use is reported at its own line: the checker
    # tracks scopes, so it does not collapse the six references into one.)
    flagged = {f.rsplit("'", 2)[1] for f in findings}
    assert flagged == {"_orch"}, findings


# ---------------------------------------------------------------------------
# Defense 1 (tree half): no undefined name anywhere under api/ or kairos/.
#
# A per-file guard only protects the file it names; a new F821 can land in any
# other module and stay invisible until a request hits the branch. The scan is
# line-independent — an allowlist entry is keyed by ``<relpath>:<name>``, not by
# line, so editing above a symbol does not rot it.
# ---------------------------------------------------------------------------
_SCAN_ROOTS = ("api", "kairos")

# ``<relpath>:<name>`` -> reason. Empty by design: api/ and kairos/ are clean.
# An entry suppresses *every* use of that name in that file, so it must carry a
# reason — and it goes stale (fails the test) the moment the name resolves.
_UNDEFINED_NAME_ALLOWLIST: dict[str, str] = {}


def _scan_paths():
    for root in _SCAN_ROOTS:
        base = REPO_ROOT / root
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            yield path


def _iter_undefined_findings(paths):
    """Yield ``(rel, line, name, raw)`` for every F821-style use found."""
    for path in paths:
        rel = path.relative_to(REPO_ROOT).as_posix()
        source = path.read_text(encoding="utf-8")
        for raw in find_undefined_names(source, rel):
            head, _, quoted = raw.partition(": Undefined name ")
            rel_part, _, line = head.rpartition(":")
            yield rel_part, int(line), quoted.strip("'"), raw


def audit_undefined_names(paths, allowlist):
    """Return violations: un-allowlisted undefined names + rotted entries."""
    violations: list[str] = []
    used: set[str] = set()
    for rel, _line, name, raw in _iter_undefined_findings(paths):
        key = f"{rel}:{name}"
        if key in allowlist:
            used.add(key)
            continue
        violations.append(f"{raw} — import or define it")
    for key, reason in sorted(allowlist.items()):
        if key not in used:
            violations.append(
                f"stale undefined-name allowlist entry {key!r} ({reason!r}) — "
                "the name now resolves; remove the entry"
            )
    return violations


def test_no_undefined_names_across_api_and_kairos():
    violations = audit_undefined_names(_scan_paths(), _UNDEFINED_NAME_ALLOWLIST)
    assert violations == [], (
        "an F821-style undefined name exists under api/ or kairos/ — a runtime "
        "NameError waiting for a code path to reach it:\n  "
        + "\n  ".join(violations)
    )


def test_undefined_name_allowlist_entries_do_not_rot():
    """A fabricated entry for a name that *does* resolve must be reported."""
    violations = audit_undefined_names(
        [REPO_ROOT / "api" / "routes" / "cost.py"],
        {"api/routes/cost.py:zzz_never_undefined": "fabricated"})
    assert any("stale undefined-name allowlist entry" in v for v in violations), (
        f"the stale-entry check did not fire; got {violations!r}"
    )


def test_undefined_name_checker_reports_the_true_line_not_the_first_load():
    """Precision: a name imported in one function must not be reported at that
    (defined) use just because a sibling function leaves it undefined — exactly
    the ``api/routes/cost.py`` shape the old checker got wrong (it reported the
    defined use at :68 instead of the real misses at :230/:251)."""
    snippet = (
        "def summary():\n"                              # 1
        "    from kairos.cost import _get_log_path\n"    # 2  (local import)
        "    return _get_log_path()\n"                   # 3  (defined — no report)
        "\n"
        "def replay():\n"                                # 5
        "    return _get_log_path().parent\n"            # 6  (undefined — report)
    )
    findings = find_undefined_names(snippet, "snippet.py")
    assert findings == ["snippet.py:6: Undefined name '_get_log_path'"], findings


def test_undefined_name_checker_respects_nested_closures():
    """A free variable from an enclosing scope is defined; a truly missing name
    in a sibling function is still reported at its own line."""
    snippet = (
        "def outer():\n"                     # 1
        "    import os\n"                    # 2
        "    def inner():\n"                 # 3
        "        return os.getcwd()\n"       # 4  (os is free — no report)
        "    return inner()\n"               # 5
        "def other():\n"                     # 6
        "    return missing_thing\n"         # 7  (undefined — report)
    )
    findings = find_undefined_names(snippet, "snippet.py")
    assert findings == ["snippet.py:7: Undefined name 'missing_thing'"], findings


def test_the_cost_route_module_has_no_undefined_names():
    """End-to-end precision on the real module that started this: it must be
    clean (its two genuine misses were fixed, and :68 never was a bug)."""
    findings = find_undefined_names(
        (REPO_ROOT / "api" / "routes" / "cost.py").read_text(encoding="utf-8"),
        "api/routes/cost.py")
    assert findings == [], findings


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
