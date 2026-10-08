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

Priority (code-ish first, general otherwise)
--------------------------------------------
1. **Explicit signal wins outright.** A caller that declares the task kind
   (``kind: docs|repo``, ``workspace_kind: docs``, or ``meta.kind``) is routed
   exactly as declared. A caller who knows routes itself.
2. **A long-task signal next.** A caller that flags a multi-step / plan-mode
   task (``long_task=``, or ``meta.require_plan`` / ``meta.plan_id``) is routed
   to the loop -- a long-horizon task is exactly what the Coder <-> Reviewer
   loop is for.
3. **Conservative coding intent next.** A *fixed, auditable* vocabulary of
   coding words in the task text (see :data:`CODING_INTENT_TERMS`) pulls the
   decision to the loop even when the workspace is ambiguous. This is a
   deliberately crude heuristic and it *will* misfire on prose that merely
   mentions a code word (e.g. "写一份 bug 管理规范"); the misfire is one-sided
   on purpose. A false "coding" reading costs one real agent turn on the loop,
   never the work; the reverse -- a code task answered as prose on the general
   lane -- is the failure worth avoiding.
4. **The workspace heuristic next.** Only used when nothing above decided. A
   workspace that positively looks like a document set (documents present
   **and** no git repo **and** no engineering markers **and** no source files)
   goes to the skeleton; anything code-ish (``.git``, a
   ``package.json``/``pyproject.toml``/``tests/``, or any source file) is a
   ``repo`` and goes to the loop no matter how many markdown files it also
   holds.
5. **Otherwise the general lane.** An ambiguous or unscannable workspace with
   no code signal is *undecided*, and the default lane is now the **skeleton**
   ("平时 chat 走通用"). ``KAIROS_ROUTE_DEFAULT=loop`` restores the historical
   loop default one process at a time (see below); every signal above still
   outranks it.

Configuring the default lane
----------------------------
``KAIROS_ROUTE_DEFAULT`` selects what an *undecided* task does. The default is
now ``"skeleton"``: a task the router could not place (no explicit kind, no
long-task flag, no coding intent, no workspace evidence) runs on the
domain-neutral general lane. Only the exact value ``"loop"`` restores the
historical behaviour (an undecided task goes to the Coder <-> Reviewer loop);
absent, empty, ``"skeleton"``, or any unrecognised value keeps the new
skeleton default. It is read at call time (never frozen at import), so it can
be flipped per process/request without a restart. Explicit, long-task,
coding-intent and heuristic signals always outrank it.
"""
from __future__ import annotations

import logging
import os
import re
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

#: Fixed, auditable coding-intent vocabulary. Each entry is a *word* whose
#: presence in a task text is read as "this is probably a coding task -> loop".
#: The list is deliberately small and lives in code so a reviewer can read
#: every trigger in one place; add words *here*, never in ad-hoc regexes
#: elsewhere. ASCII terms match on a leading word boundary (so "code" hits
#: "codebase" but not "decode"); CJK terms match as substrings, because Chinese
#: has no word spaces.
#:
#: This is a *conservative heuristic, not a classifier* -- it WILL misfire on
#: prose that merely mentions a code word ("帮我写一份 bug 管理规范" reads as
#: coding). The misfire is deliberate and one-sided: a false "coding" reading
#: costs one real agent turn on the loop, whereas the opposite mistake (a code
#: task answered as prose) loses the work. Widen or trim it here when a real
#: misfire is observed.
CODING_INTENT_TERMS = (
    # -- Chinese (substring match) --
    "修复", "修", "改", "修改", "实现", "重构", "调试", "排查", "测试",
    "报错", "异常", "错误", "函数", "变量", "文件", "代码", "脚本",
    "提交", "编译", "构建", "部署", "接口", "模块", "依赖", "补丁",
    "合并", "分支", "单元测试", "回滚", "配置", "改动",
    # -- English (leading word-boundary match) --
    "fix", "bug", "refactor", "implement", "debug", "test", "function",
    "method", "commit", "merge", "compile", "build", "deploy", "patch",
    "endpoint", "api", "code", "script", "module", "repo", "repository",
    "dependency", "rollback", "config", "diff", "syntax",
)

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


def _compile_coding_intent():
    """Precompile the coding-intent terms once (ASCII -> word-boundary regex).

    Returns a tuple of ``(term, pattern_or_None)``; ``pattern`` is ``None`` for
    CJK terms (matched by substring) and a regex for ASCII ones.
    """
    compiled = []
    for term in CODING_INTENT_TERMS:
        if term.isascii():
            compiled.append(
                (term, re.compile(r"(?<![a-z0-9_])" + re.escape(term)))
            )
        else:
            compiled.append((term, None))
    return tuple(compiled)


_CODING_INTENT_PATTERNS = _compile_coding_intent()


def detect_coding_intent(text: Any) -> Optional[str]:
    """The first coding-intent word in ``text``, or ``None``.

    Fixed, auditable vocabulary (:data:`CODING_INTENT_TERMS`). ASCII terms match
    on a leading word boundary; CJK terms match as substrings. A crude
    heuristic *by design* -- a hit means "probably code", never "certainly
    code". Never raises: a non-string ``text`` yields ``None``.
    """
    if not isinstance(text, str) or not text:
        return None
    lowered = text.lower()
    for term, pattern in _CODING_INTENT_PATTERNS:
        if pattern is None:
            if term in lowered:
                return term
        elif pattern.search(lowered):
            return term
    return None


def _long_task_requested(long_task: Any, metadata: Optional[Dict[str, Any]]) -> bool:
    """True when the caller flagged a multi-step / plan-mode (long) task.

    The repo has no standalone "is this long" classifier; the closest existing
    marker is Plan Mode. ``ChatRequest.require_plan`` / ``plan_id`` are exactly
    "run the multi-step / long-horizon flow" switches (see
    ``kairos/plan_mode.py``), so that is what we reuse -- the explicit
    ``long_task`` argument, or ``meta.long_task`` / ``meta.long_running`` /
    ``meta.require_plan`` / a non-empty ``meta.plan_id``.
    """
    if long_task:
        return True
    if isinstance(metadata, dict):
        for key in ("long_task", "long_running", "require_plan"):
            if metadata.get(key):
                return True
        if str(metadata.get("plan_id") or "").strip():
            return True
    return False


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
    """The lane an *undecided* task takes (see ``KAIROS_ROUTE_DEFAULT``).

    Read at call time -- never frozen at import -- so an operator or a test can
    flip it without a restart. The default is now :data:`ROUTE_SKELETON`: an
    undecided task (no explicit kind, no long-task flag, no coding intent, no
    workspace evidence) goes to the domain-neutral general lane -- "平时 chat
    走通用". Only the exact value ``"loop"`` (trimmed, case-insensitive)
    restores the historical behaviour; everything else -- unset, empty,
    ``"skeleton"``, or a typo -- yields the new skeleton default. Explicit,
    long-task, coding-intent and heuristic signals always outrank this.
    """
    value = (os.environ.get(ROUTE_DEFAULT_ENV) or "").strip().lower()
    return ROUTE_LOOP if value == ROUTE_LOOP else ROUTE_SKELETON


def route_task(
    *,
    explicit_kind: Any = None,
    workspace: Any = None,
    metadata: Optional[Dict[str, Any]] = None,
    requirement: Any = None,
    long_task: Any = False,
) -> RouteDecision:
    """Decide ``loop`` vs ``skeleton`` for one task (see the module docstring).

    ``explicit_kind`` / ``metadata`` carry the caller's declaration;
    ``requirement`` is the task text the coding-intent heuristic reads (the
    user's own message on the chat path); ``long_task`` flags a multi-step /
    plan-mode task. ``workspace`` is the directory the heuristic may inspect.
    Nothing here has side effects -- it is a pure read used by the API, the CLI
    and the tests.
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

    # A long task (multi-step / plan-mode) is the loop's job; check it before
    # the one-off signals so the reason names the strongest declaration.
    if _long_task_requested(long_task, metadata):
        return RouteDecision(
            route=ROUTE_LOOP,
            workspace_kind=WS_REPO,
            reason=(
                "long task: the caller flagged a multi-step / plan-mode task "
                "-> loop"
            ),
            signals={"long_task": True},
            source="long_task",
        )

    # Conservative coding intent in the task text. Deliberately rated above the
    # workspace heuristic: a coding word is a stronger statement of intent than
    # "the workspace happens to hold documents".
    term = detect_coding_intent(requirement)
    if term is not None:
        signals = scan_workspace(workspace)
        signals = dict(signals)
        signals["coding_intent"] = term
        return RouteDecision(
            route=ROUTE_LOOP,
            workspace_kind=WS_REPO,
            reason=(
                f"coding intent: the message contains {term!r} (conservative "
                "coding-intent vocabulary) -> loop"
            ),
            signals=signals,
            source="coding_intent",
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
    if default_route() == ROUTE_LOOP:
        return RouteDecision(
            route=ROUTE_LOOP,
            workspace_kind=WS_REPO,
            reason=(
                "no explicit signal, no coding intent, no long-task flag and "
                "the workspace is ambiguous -> KAIROS_ROUTE_DEFAULT=loop routes "
                "the undecided default to the loop (historical behaviour)"
            ),
            signals=signals,
            source="default",
        )
    return RouteDecision(
        route=ROUTE_SKELETON,
        workspace_kind=WS_REPO,
        reason=(
            "no explicit signal, no coding intent, no long-task flag and the "
            "workspace is ambiguous -> the undecided default is the general "
            "lane (skeleton)"
        ),
        signals=signals,
        source="default",
    )


__all__ = [
    "RouteDecision",
    "route_task",
    "default_route",
    "detect_coding_intent",
    "CODING_INTENT_TERMS",
    "scan_workspace",
    "ROUTE_LOOP",
    "ROUTE_SKELETON",
    "ROUTE_DEFAULT_ENV",
    "WS_REPO",
    "WS_DOCS",
]
