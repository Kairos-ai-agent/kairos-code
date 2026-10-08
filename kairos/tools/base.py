"""Base Tool class for agent tools.

Every tool also carries a *capability declaration* and reaches the world
through one capability gate. ``capabilities`` names the side effects a tool
has (see :mod:`kairos.capabilities`); ``__init_subclass__`` wraps each
subclass's ``execute`` so a call is judged on capability + target before it
runs. A tool that declares nothing and is not registered is refused -- the
gate defaults to deny.
"""

from __future__ import annotations

import functools
import inspect
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, FrozenSet, Optional

from pydantic import BaseModel

from kairos.access_control import is_full_access


def resolve_within_root(path: str, root: Path) -> Path:
    """Resolve ``path`` inside ``root`` or raise ``PermissionError``.

    The single path policy shared by every file tool and by the capability
    gate: a relative path is taken under the root, an absolute path as-is,
    both fully resolved (so ``..`` segments and symlinks cannot escape), and
    the result must sit under the root. Under full access the confinement is
    lifted -- the user opted into running on their own machine.
    """
    root_name = root.name
    if path.startswith(root_name + "/") or path.startswith(root_name + "\\"):
        path = path[len(root_name) + 1:]
    target = Path(path)
    if not target.is_absolute():
        target = (root / path).resolve()
    else:
        target = target.resolve()
    try:
        target.relative_to(root)
    except ValueError:
        if is_full_access():
            return target
        raise PermissionError(f"Path outside project directory: {target}")
    return target


class ToolResult(BaseModel):
    """Result of a tool execution."""

    success: bool
    output: str
    error: Optional[str] = None
    metadata: dict = {}

class BaseTool(ABC):
    """Abstract base class for all agent tools."""

    name: str = "base_tool"
    description: str = "Base tool"
    # Tools can opt into per-round result caching by reading
    # self._cache. Wired up by the orchestrator at agent
    # construction time (see kairos.tools.cache.get_cache).
    _cache = None

    #: Side effects this tool has, as a set of
    #: :class:`~kairos.capabilities.Capability`. ``None`` means "not declared
    #: on the class"; the gate then falls back to the built-in registration
    #: table (which already lists every tool that shipped before the gate
    #: existed). A tool that is neither declared nor registered is refused.
    capabilities: Optional[FrozenSet["Capability"]] = None

    def __init_subclass__(cls, **kwargs):
        """Route every subclass's ``execute`` through the capability gate.

        Wrapping here -- rather than at each call site -- is what makes the
        gate a single choke point: a new tool cannot reach the world without
        passing it, whichever code path invokes the tool.
        """
        super().__init_subclass__(**kwargs)
        # A class-level declaration is also registered under the tool's name,
        # so the sentinel (which only ever sees a name) can honour it too.
        try:
            from kairos.capabilities import register_tool_capabilities
            declared = getattr(cls, "capabilities", None)
            name = getattr(cls, "name", None)
            if declared is not None and name and name != "base_tool":
                register_tool_capabilities(name, declared)
        except Exception:  # noqa: BLE001 - never break class creation
            pass
        func = cls.__dict__.get("execute")
        if callable(func) and not getattr(func, "__capability_wrapped__", False):
            cls.execute = _wrap_execute(func)

    def __init__(self, allowed_root: str | Path = "."):
        self._allowed_root = Path(allowed_root).resolve()
        # R38.6 §30: optional auto-checkpointer. Tools that write
        # files call ``self._checkpointer.before_write(path)``
        # right before the write. The checkpointer is wired by
        # the orchestrator at agent construction time. Tools
        # that don't write files leave it ``None``.
        self._checkpointer = None

    def _resolve_safe(self, path: str) -> Path:
        """Resolve a path safely within the allowed root directory.

        Thin wrapper over :func:`resolve_within_root` -- the one path policy
        shared with the capability gate, so the two can never disagree.

        Strips redundant workspace prefix (e.g. 'workspace/<id>/app.py' -> 'app.py').
        """
        return resolve_within_root(path, self._allowed_root)

    def _invalidate_cache(self, path, resolved_path=None) -> None:
        """Invalidate the per-round tool-result cache for a written path.

        Call from a write tool after a successful write so a read-after-write
        in the same round returns fresh content instead of stale cached bytes.
        Best-effort; a cache failure never affects the write result.
        """
        try:
            from kairos.tools.cache import invalidate_path as _invalidate
            seen = set()
            for p in (path, resolved_path):
                if not p:
                    continue
                key = str(p)
                if key in seen:
                    continue
                seen.add(key)
                _invalidate(key)
        except Exception:  # noqa: BLE001
            pass

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        """Execute the tool with given arguments."""
        ...

    def to_schema(self) -> dict:
        """Return JSON schema for the tool (for LLM function calling).
        
        Subclasses should override this to provide detailed parameter
        descriptions that help the LLM understand when and how to use
        the tool correctly.
        """
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        }
    
    @property
    def detailed_description(self) -> str:
        """Extended description with usage examples and caveats.
        
        Override in subclasses to provide richer context for the LLM.
        """
        return self.description


# ---------------------------------------------------------------------------
# The capability gate, applied at the single place a tool becomes an action
# ---------------------------------------------------------------------------


def _capability_precheck(tool: Any, sig: Any, args: tuple,
                         kwargs: dict) -> Optional[ToolResult]:
    """Judge one call against the capability gate.

    Returns a refusing ``ToolResult`` when the call may not run, or ``None``
    to let it proceed. The gate fails closed: a tool that neither declares
    nor registers its capabilities is refused, and an error inside the gate
    is itself a refusal (with the trace left in the audit trail).
    """
    from kairos.capabilities import CapabilityVerdict, assess, audit

    bound: dict = {}
    if sig is not None:
        try:
            bound = {k: v for k, v in
                     sig.bind(tool, *args, **kwargs).arguments.items()
                     if k != "self"}
        except TypeError:
            bound = dict(kwargs)
    else:
        bound = dict(kwargs)

    root = getattr(tool, "_allowed_root", None)
    if root is None:
        root = getattr(tool, "_allowed_cwd", None)

    try:
        verdict = assess(getattr(tool, "name", "") or "", bound,
                         obj=tool, root=root)
    except Exception as exc:  # noqa: BLE001 - a broken gate must not open
        verdict = CapabilityVerdict(
            tool=getattr(tool, "name", "") or "", capabilities=frozenset(),
            target="", allowed=False,
            reason=f"capability gate error, refusing (fail-closed): {exc}",
            rule="gate-error",
            error_text=("Refused by the capability gate: the gate raised "
                        f"({exc}); the call is refused (fail-closed)."),
        )
    try:
        audit(verdict)
    except Exception:  # noqa: BLE001 - auditing never changes a ruling
        pass
    if verdict.allowed:
        return None
    return ToolResult(success=False, output="", error=verdict.message(),
                      metadata={"capability": verdict.to_dict()})


def _wrap_execute(func: Callable) -> Callable:
    """Wrap a tool's ``execute`` so the capability gate runs first."""
    if getattr(func, "__capability_wrapped__", False):
        return func
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):  # pragma: no cover - exotic callables
        sig = None

    if inspect.iscoroutinefunction(func):
        @functools.wraps(func)
        async def _gated(self, *args, **kwargs):
            blocked = _capability_precheck(self, sig, args, kwargs)
            if blocked is not None:
                return blocked
            return await func(self, *args, **kwargs)
    else:
        @functools.wraps(func)
        def _gated(self, *args, **kwargs):
            blocked = _capability_precheck(self, sig, args, kwargs)
            if blocked is not None:
                return blocked
            return func(self, *args, **kwargs)

    _gated.__capability_wrapped__ = True
    return _gated
