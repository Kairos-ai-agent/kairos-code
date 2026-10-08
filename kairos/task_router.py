"""Route a task to the code loop or to the domain-neutral skeleton.

Why this exists
---------------
``kairos/skeleton`` lifted the code loop's three hidden assumptions into
``Workspace`` / ``Worker`` / ``Verifier`` so non-code tasks ("read three
documents, write a comparison report") run on a shape that fits them. But the
**product surface** never picked it: every request at
``POST /api/projects/{id}/start`` went straight into the Coder <-> Reviewer
loop, so a document task was still forced into "edit a repo + run its tests".
This module is the missing decision point -- for one request it answers *which*
of the two paths the task should take, and *why*, as inspectable data.

Priority (safety first)
-----------------------
1. **Explicit signal wins outright.** A caller that declares the task kind
   (``kind: docs|repo``, ``workspace_kind: docs``, or ``meta.kind``) is routed
   exactly as declared. A caller who knows routes itself.
2. **A conservative heuristic next.** Only used when nothing was declared. It
   sends a task to the skeleton *only* when the workspace positively looks like
   a document set: documents present **and** no git repo **and** no engineering
   markers **and** no source files. Every code-ish signal pulls the decision
   back to the loop.
3. **Otherwise the original loop.** An ambiguous or unscannable workspace is
   *undecided*, and by default undecided means ``route="loop"`` -- byte for
   byte today's behaviour. An operator may change *only* this undecided
   default with ``KAIROS_ROUTE_DEFAULT=skeleton`` (see below); explicit and
   heuristic signals always outrank it.

The heuristic is deliberately one-sided: a false "docs" reading would move a
code task off the loop, which is the failure worth designing against, so a
workspace with a ``.git``, a ``package.json``/``pyproject.toml``/``tests/``,
or any source file is a ``repo`` no matter how many markdown files it also
holds. When nothing decides, the answer is the loop -- never a guess.

Configuring the default lane
----------------------------
``KAIROS_ROUTE_DEFAULT`` selects what an *undecided* task does. Only the exact
value ``"skeleton"`` does anything: it sends a task the router could not place
(no explicit kind, no workspace evidence) to the skeleton instead of the loop.
Absent, empty, ``"loop"``, or any unrecognised value leaves the historical
loop default in place. It is read at call time (never frozen at import), so it
can be flipped per process/request without a restart.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: The two routes a task can take.
ROUTE_LOOP = "loop"
ROUTE_SKELETON = "skeleton"

#: Environment variable / settings key naming the route an *undecided* task
#: takes. Only the exact value ``"skeleton"`` changes anything; every other
#: value (including a typo) keeps the historical loop default unchanged.
ROUTE_DEFAULT_ENV = "KAIROS_ROUTE_DEFAULT"

#: The two workspace kinds the skeleton understands (``RepoWorkspace`` /
#: ``DocSetWorkspace``).
WS_REPO = "repo"
WS_DOCS = "docs"

#: Caller spellings that mean "document / non-code task" -> skeleton.
_DOCS_ALIASES = frozenset({
    "docs", "doc", "document", "documents", "docset", "doc_set", "doc-set",
    "noncode", "non_code", "non-code", "report", "research", "text", "texts",
    "writing", "writeup", "write_up",
})

#: Caller spellings that mean "code / repo task" -> loop.
_REPO_ALIASES = frozenset({
    "repo", "repository", "code", "coding", "bugfix", "bug_fix", "bug-fix",
    "bug", "refactor", "feature", "patch", "programming",
})

#: Extensions that read as *documents* (inputs a report is written from).
_DOC_EXTS = frozenset({
    ".md", ".markdown", ".mdx", ".rst", ".txt", ".text", ".adoc", ".org",
    ".csv", ".tsv", ".pdf", ".docx", ".doc", ".rtf", ".tex", ".epub",
})

#: Extensions that read as *source* -- their presence alone means "repo".
_CODE_EXTS = frozenset({
    ".py", ".pyi", ".pyx", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx",
    ".go", ".rs", ".java", ".kt", ".kts", ".c", ".h", ".cc", ".cpp", ".cxx",
    ".hpp", ".hh", ".cs", ".rb", ".php", ".swift", ".scala", ".sh", ".bash",
    ".zsh", ".ps1", ".bat", ".sql", ".lua", ".pl", ".r", ".m", ".mm", ".vue",
    ".svelte", ".dart", ".ex", ".exs", ".clj", ".cljs", ".hs", ".elm", ".zig",
})

#: Top-level files that mark a software project, whatever else is present.
_ENGINEERING_MARKERS = (
    ".git", "package.json", "pyproject.toml", "setup.py", "setup.cfg",
    "requirements.txt", "Pipfile", "poetry.lock", "uv.lock", "Cargo.toml",
    "go.mod", "go.sum", "pom.xml", "build.gradle", "build.gradle.kts",
    "settings.gradle", "Makefile", "CMakeLists.txt", "Gemfile",
    "composer.json", "mix.exs", "pubspec.yaml", "tsconfig.json", "Dockerfile",
    "docker-compose.yml", "docker-compose.yaml", "justfile", "Taskfile.yml",
)

#: Top-level directories that mark a software project.
_ENGINEERING_DIRS = ("tests", "test", "__tests__", "spec")

#: Directories a workspace scan never descends into (generated / vendored).
_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox",
    "dist", "build", ".idea", ".vscode", ".kairos", "vendor", "target",
    ".next", ".nuxt", "site-packages",
})


@dataclass
class RouteDecision:
    """The chosen path for one task, plus the evidence behind it.

    ``route`` is ``"skeleton"`` or ``"loop"``; ``workspace_kind`` is the
    workspace the skeleton should build (``"docs"`` / ``"repo"``) -- meaningful
    even when ``route`` is ``"loop"``, because a caller may still want to log
    what the workspace looked like. ``source`` is ``"explicit"``, ``"heuristic"``
    or ``"default"`` so a log line can say which tier decided.
    """

    route: str
    workspace_kind: str
    reason: str
    signals: Dict[str, Any] = field(default_factory=dict)
    source: str = ""

    @property
    def uses_skeleton(self) -> bool:
        return self.route == ROUTE_SKELETON

    def to_dict(self) -> Dict[str, Any]:
        return {
            "route": self.route,
            "workspace_kind": self.workspace_kind,
            "source": self.source,
            "reason": self.reason,
            "signals": dict(self.signals),
        }


def _normalize_kind(value: Any) -> Optional[str]:
    """Map a caller's kind spelling to ``"docs"`` / ``"repo"`` / ``None``."""
    if not isinstance(value, str):
        return None
    token = value.strip().lower()
    if not token:
        return None
    if token in _DOCS_ALIASES:
        return WS_DOCS
    if token in _REPO_ALIASES:
        return WS_REPO
    return None


def _explicit_kind(
    explicit_kind: Any, metadata: Optional[Dict[str, Any]]
) -> Optional[str]:
    """The first *recognised* explicit signal, or ``None``.

    An explicit value that we cannot recognise is treated as "no signal" and
    the decision falls through to the heuristic / default -- a caller's typo
    must not silently change the route.
    """
    for value in (explicit_kind,):
        kind = _normalize_kind(value)
        if kind is not None:
            return kind
    if isinstance(metadata, dict):
        for key in ("kind", "workspace_kind", "task_kind", "mode"):
            kind = _normalize_kind(metadata.get(key))
            if kind is not None:
                return kind
    return None


def scan_workspace(workspace: Any, *, max_files: int = 2000) -> Dict[str, Any]:
    """Inspect a workspace directory for the signals the heuristic uses.

    Bounded (``max_files``) and never raises: an unreadable or missing
    directory yields ``scanned=False`` and a decision that falls through to the
    default loop.
    """
    signals: Dict[str, Any] = {
        "scanned": False,
        "has_git": False,
        "engineering_markers": [],
        "code_files": 0,
        "doc_files": 0,
        "total_files": 0,
    }
    if not workspace:
        return signals
    try:
        root = Path(str(workspace)).expanduser()
    except Exception:
        return signals
    try:
        if not root.is_dir():
            return signals
    except OSError:
        return signals

    signals["scanned"] = True
    try:
        if (root / ".git").exists():
            signals["has_git"] = True
    except OSError:
        pass

    markers: List[str] = []
    for name in _ENGINEERING_MARKERS:
        if name == ".git":
            continue
        try:
            if (root / name).exists():
                markers.append(name)
        except OSError:
            pass
    for d in _ENGINEERING_DIRS:
        try:
            if (root / d).is_dir():
                markers.append(d + "/")
        except OSError:
            pass
    signals["engineering_markers"] = sorted(set(markers))

    code = doc = total = 0
    stop = False
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fn in filenames:
                total += 1
                ext = os.path.splitext(fn)[1].lower()
                if ext in _CODE_EXTS:
                    code += 1
                elif ext in _DOC_EXTS:
                    doc += 1
                if total >= max_files:
                    stop = True
                    break
            if stop:
                break
    except OSError:
        pass
    signals["code_files"] = code
    signals["doc_files"] = doc
    signals["total_files"] = total
    return signals


def _heuristic_kind(signals: Dict[str, Any]) -> Optional[str]:
    """``"repo"`` if anything says code, ``"docs"`` only if it clearly is docs.

    Order matters: code signals are checked first and win, so a workspace that
    is *both* (a repo with a ``docs/`` folder) stays a repo.
    """
    if not signals.get("scanned"):
        return None
    if (
        signals.get("has_git")
        or signals.get("engineering_markers")
        or int(signals.get("code_files", 0) or 0) > 0
    ):
        return WS_REPO
    if int(signals.get("doc_files", 0) or 0) > 0:
        return WS_DOCS
    return None


def default_route() -> str:
    """The route an *undecided* task takes (see ``KAIROS_ROUTE_DEFAULT``).

    Read at call time -- never frozen at import -- so an operator or a test can
    flip it without a restart. Returns :data:`ROUTE_SKELETON` **only** for the
    exact value ``"skeleton"`` (trimmed, case-insensitive). Everything else --
    unset, empty, ``"loop"``, or an unrecognised value such as a typo --
    returns :data:`ROUTE_LOOP`, i.e. the behaviour the router has always had.
    """
    value = (os.environ.get(ROUTE_DEFAULT_ENV) or "").strip().lower()
    return ROUTE_SKELETON if value == ROUTE_SKELETON else ROUTE_LOOP


def route_task(
    *,
    explicit_kind: Any = None,
    workspace: Any = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> RouteDecision:
    """Decide ``loop`` vs ``skeleton`` for one task (see the module docstring).

    ``explicit_kind`` / ``metadata`` carry the caller's declaration;
    ``workspace`` is the directory the heuristic may inspect. Nothing here has
    side effects -- it is a pure read used by the API, the CLI and the tests.
    """
    kind = _explicit_kind(explicit_kind, metadata)
    if kind is not None:
        return RouteDecision(
            route=ROUTE_SKELETON if kind == WS_DOCS else ROUTE_LOOP,
            workspace_kind=kind,
            reason=f"explicit kind={kind!r} declared by the caller",
            signals={"explicit": True},
            source="explicit",
        )

    signals = scan_workspace(workspace)
    ws_kind = _heuristic_kind(signals)
    if ws_kind == WS_DOCS:
        return RouteDecision(
            route=ROUTE_SKELETON,
            workspace_kind=WS_DOCS,
            reason=(
                "heuristic: a document set (documents present, no git, no "
                "engineering markers, no source files)"
            ),
            signals=signals,
            source="heuristic",
        )
    if ws_kind == WS_REPO:
        return RouteDecision(
            route=ROUTE_LOOP,
            workspace_kind=WS_REPO,
            reason="heuristic: the workspace looks like a repo",
            signals=signals,
            source="heuristic",
        )
    if default_route() == ROUTE_SKELETON:
        return RouteDecision(
            route=ROUTE_SKELETON,
            workspace_kind=WS_REPO,
            reason=(
                "no explicit signal and the workspace is ambiguous -> "
                "KAIROS_ROUTE_DEFAULT=skeleton routes the undecided default to "
                "the skeleton (explicit and heuristic signals still win)"
            ),
            signals=signals,
            source="default",
        )
    return RouteDecision(
        route=ROUTE_LOOP,
        workspace_kind=WS_REPO,
        reason=(
            "no explicit signal and the workspace is ambiguous -> default "
            "loop (unchanged behaviour)"
        ),
        signals=signals,
        source="default",
    )


__all__ = [
    "RouteDecision",
    "route_task",
    "default_route",
    "scan_workspace",
    "ROUTE_LOOP",
    "ROUTE_SKELETON",
    "ROUTE_DEFAULT_ENV",
    "WS_REPO",
    "WS_DOCS",
]
