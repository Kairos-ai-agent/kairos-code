"""Guard: every literal API path the frontend calls must exist on the backend.

Why this exists
---------------
`api/app.py` mounts ``checkpoints_router`` at ``prefix="/api/projects"`` while the
router itself declared its routes as ``"/projects/{project_id}/checkpoints"``.
FastAPI *concatenates* the two, so the effective path became
``/api/projects/projects/{project_id}/checkpoints`` and the Loop page's calls
(``/projects/{id}/checkpoint``, ``/projects/{id}/diff``) 404'd. The list call
sits behind ``.catch(() => null)``, so the panel stayed permanently blank with
no console error: neither ``tsc`` nor ``npm run build`` can see a server route
that does not exist. Only a test that cross-checks the frontend's string
literals against the app's real route table catches it.

What it checks
--------------
  * Parse ``web/src/**/*.{ts,tsx}`` for ``api.<method>(`...`)`` and
    ``api.<method>('...')`` calls whose argument is a *static* literal
    (``${x}`` inside a template becomes a path parameter placeholder).
  * Resolve axios' ``baseURL`` (``/api``, see ``web/src/api/client.ts``) and
    match each against ``app.openapi()["paths"]`` — the authoritative table of
    the app's *effective* routes — with every parameter segment normalised to a
    single-segment wildcard, and the HTTP method compared.
  * Anything it cannot parse statically (string concatenation such as
    ``api.get('/tasks/' + id)``, or a dynamic base such as
    ``api.post(`${apiBase}/click`)``) is *skipped*, counted and printed — never
    silently ignored.
  * A small, explicit ``KNOWN_MISSING`` whitelist records paths that are
    knowingly absent from the backend, each with a reason.
  * Assertion: the set of missing paths equals the whitelist exactly. A new
    frontend call with no backend route turns the guard red and names the path.

Red → green proof
-----------------
Before the checkpoint-prefix fix this test failed and named the three Loop
paths (list / diff / rollback). It passes once the routes line up.
"""
from __future__ import annotations

import glob
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# axios instance in web/src/api/client.ts uses baseURL: '/api'.
API_PREFIX = "/api"
WEB_SRC = ROOT / "web" / "src"

# Paths the frontend calls that the backend genuinely does not serve. Keyed by
# (METHOD, normalised-client-path). Every entry must carry a reason; the test
# prints the whole list so these are visible, not hidden.
KNOWN_MISSING: dict[tuple[str, str], str] = {
    ("GET", "/api/projects/{param}/stats"):
        "pre-existing: the Loop stats card calls a route the backend never "
        "implemented; the 404 is swallowed by .catch(() => null). Out of scope "
        "for the checkpoint-parity fix.",
    ("GET", "/api/projects/{param}/plan/visualization"):
        "pre-existing: the plan-viz card calls an unimplemented route; also "
        "swallowed by .catch(() => null). Out of scope.",
    ("POST", "/api/projects/{param}/requirements"):
        "pre-existing: Project page posts requirements to an unimplemented "
        "route. Out of scope.",
    ("POST", "/api/borrowed/{param}/plans/{param}/{param}"):
        "dynamic enum: the final segment is a runtime `${action}` "
        "(approve|reject) that resolves to the literal backend routes "
        "/api/borrowed/{project_id}/plans/{plan_id}/approve|reject.",
    ("POST", "/api/extensions/mcp/{param}"):
        "dynamic enum: the final segment is a runtime `${action}` "
        "(install|uninstall|probe) that resolves to literal backend routes.",
}

# api.<method>(`...` | '...' | "...") — the method and the opening quote must be
# adjacent modulo whitespace/newlines (some call sites write ``api\n  .get(...)``).
_CALL_RE = re.compile(
    r"api\s*\.\s*(get|post|put|delete|patch)\(\s*([`'\"])(.*?)\2", re.DOTALL
)
# ${ ... } inside a template literal → one path parameter.
_TEMPLATE_PARAM_RE = re.compile(r"\$\{[^}]*\}")


def _route_to_regex(route: str) -> str:
    """Turn a route path into a regex string; every ``{...}`` is a segment.

    ``{param:path}`` (Starlette's catch-all) matches one-or-more segments.
    """
    out = []
    for seg in route.split("/"):
        if seg.startswith("{") and seg.endswith("}"):
            out.append(".+" if ":path" in seg[1:-1] else "[^/]+")
        else:
            out.append(re.escape(seg))
    return "^" + "/".join(out) + "$"


def _server_routes() -> set[tuple[str, str]]:
    """(METHOD, regex) for every effective route in the app, from OpenAPI."""
    from api.app import app

    spec = app.openapi()
    routes: set[tuple[str, str]] = set()
    for path, ops in spec["paths"].items():
        rx = _route_to_regex(path)
        for method in ops:
            routes.add((method.upper(), rx))
    return routes


def _scan_frontend() -> tuple[list[tuple[str, str, str, int]], list[tuple[str, str, str]]]:
    """Return (checked, skipped).

    checked: (method, normalised-path, file, line) for statically-parseable calls.
    skipped: (method, raw-literal, reason) for calls that cannot be resolved.
    """
    files = sorted(
        glob.glob(str(WEB_SRC / "**" / "*.ts"), recursive=True)
        + glob.glob(str(WEB_SRC / "**" / "*.tsx"), recursive=True)
    )
    checked: list[tuple[str, str, str, int]] = []
    skipped: list[tuple[str, str, str]] = []
    for f in files:
        text = Path(f).read_text(encoding="utf-8")
        for m in _CALL_RE.finditer(text):
            method = m.group(1).upper()
            raw = m.group(3)
            line = text[: m.start()].count("\n") + 1
            rel = os.path.relpath(f, ROOT)
            after = text[m.end(): m.end() + 3].lstrip()
            if after.startswith("+"):
                skipped.append((method, raw + " + ...", f"{rel}:{line} (concatenation)"))
                continue
            if not raw.startswith("/"):
                skipped.append((method, raw, f"{rel}:{line} (not a rooted path)"))
                continue
            client = _TEMPLATE_PARAM_RE.sub("{param}", raw).split("?")[0]
            checked.append((method, API_PREFIX + client, rel, line))
    return checked, skipped


def _missing() -> list[tuple[str, str, str, int]]:
    server = _server_routes()
    checked, skipped = _scan_frontend()
    # Visible, not silent: report what could not be checked.
    print(
        f"\n[route-parity] checked {len(checked)} literal API call(s); "
        f"skipped {len(skipped)} non-static call(s):"
    )
    for method, raw, where in skipped:
        print(f"  skip {method} {raw!r}  {where}")

    missing = [
        (method, path, rel, line)
        for (method, path, rel, line) in checked
        if not any(
            method == sm and re.match(srx, path) for sm, srx in server
        )
    ]
    return missing


def test_frontend_literal_api_paths_exist_on_backend():
    """Every statically-parsed frontend API path must be a real backend route."""
    missing = _missing()
    unexpected = [
        (method, path, rel, line)
        for (method, path, rel, line) in missing
        if (method, path) not in KNOWN_MISSING
    ]

    # Whitelist entries that are no longer missing mean the whitelist is stale —
    # surface that too, so a fixed route gets removed from the list.
    missing_set = {(method, path) for (method, path, _, _) in missing}
    stale = [key for key in KNOWN_MISSING if key not in missing_set]

    lines = []
    if unexpected:
        lines.append("Frontend calls paths the backend does not serve:")
        for method, path, rel, line in unexpected:
            lines.append(f"  {method:6} {path:55} {rel}:{line}")
    if stale:
        lines.append("KNOWN_MISSING whitelist entries that now resolve (remove them):")
        for method, path in stale:
            lines.append(f"  {method:6} {path}")

    assert not lines, "\n".join(lines)
