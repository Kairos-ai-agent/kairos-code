"""FindTool — locate files by glob (sandboxed).

Thin wrapper over pathlib.Path.glob/rglob. Honors the same skip-dir rules
as GrepTool so results stay focused.

Truncation contract (mirrors file_read):

* ``max_results`` caps how many paths are *shown*.
* counting does **not** stop at the cap — the remaining matches are still
  enumerated (cheap: only a counter moves, no path string is materialised)
  so ``total_matches`` is the real number of hits instead of "how many we
  happened to return". Enumeration is bounded by an absolute ceiling so a
  runaway pattern cannot turn one capped call into an unbounded walk; past
  that ceiling the total is a lower bound (``>=N``), surfaced both in the
  marker and in ``metadata["total_is_lower_bound"]``.
* when anything is withheld the output ends with an explicit marker naming
  shown/total and how to see the rest. When nothing is withheld the output
  is byte-for-byte the match list — no marker, no noise.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

from kairos.tools.base import BaseTool, ToolResult

# Absolute ceiling on how many matching paths we enumerate while counting
# past ``max_results``. Enumeration is a pure filesystem walk (no file is
# opened), so it is cheap; the ceiling only stops a pathological tree or
# pattern from turning a capped call into an unbounded walk. Past it the
# reported total degrades to a lower bound (">=N").
_COUNT_CEILING = 20_000


class FindTool(BaseTool):
    name = "find"
    description = (
        "Find files matching a glob pattern. Skips .git, node_modules, "
        "__pycache__, and other common build directories. Glob semantics are "
        "pathlib's: '*.py' matches only files directly under `path`, while "
        "'**/*.py' — or any pattern starting with '**/' — recurses into "
        "subdirectories (same as rglob). `path` defaults to '.'. Results are "
        "capped at max_results; when the cap is hit the output ends with an "
        "explicit [truncated: ...] marker giving the true number of matches "
        "and how to see the rest, and metadata carries truncated / "
        "total_matches / returned. An uncapped run carries no marker at all."
    )

    def to_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": (
                            "Glob pattern. '*.py' is single-level under `path`; "
                            "'**/*.py' recurses into subdirectories."
                        ),
                    },
                    "path": {"type": "string",
                             "description": "Directory to start from. Defaults to '.'."},
                    "max_results": {
                        "type": "integer", "default": 200,
                        "description": (
                            "Cap on how many paths are shown. The true match "
                            "count is still reported (output marker + "
                            "metadata.total_matches), so a capped result is "
                            "never silently mistaken for the whole set."
                        ),
                    },
                },
                "required": ["pattern"],
            },
        }

    SKIP_DIRS = {".git", "node_modules", "__pycache__", "venv", ".venv",
                 "dist", "build", "target", ".next", ".pytest_cache"}

    @staticmethod
    def _iter_matches(root: Path, pattern: str) -> Iterator[Path]:
        """Yield candidate paths for ``pattern`` under ``root``.

        A leading ``**/`` (or a bare ``**``) is routed through ``rglob`` so
        recursion is explicit and does not depend on pathlib's
        version-specific handling of ``**``. Every other pattern keeps plain
        ``glob`` semantics, so ``*.py`` still matches only the root level
        (backward compatible); a Windows-style ``**\\`` prefix is handled by
        ``glob`` itself, which accepts both separators. A malformed pattern
        (e.g. ``**.py``) makes pathlib raise ``ValueError`` — the caller
        turns that into a tool error instead of a crash.
        """
        if pattern in ("**", "**/"):
            yield from root.rglob("*")
        elif pattern.startswith("**/"):
            yield from root.rglob(pattern[3:])
        else:
            yield from root.glob(pattern)

    async def execute(self, pattern: str = "", path: str = ".",
                      max_results: int = 200, **kwargs) -> ToolResult:
        if not pattern:
            return ToolResult(success=False, output="", error="pattern is required")
        try:
            root = self._resolve_safe(path)
        except PermissionError as e:
            return ToolResult(success=False, output="", error=str(e))

        # The cap arrives from a JSON tool call, so coerce it defensively.
        try:
            cap = int(max_results)
        except (TypeError, ValueError):
            cap = 200
        if cap < 1:
            cap = 1

        results: list[str] = []
        total = 0
        total_exact = True
        # At least one past the cap, so a full count can always *prove* there
        # were more than max_results matches (rather than guessing).
        ceiling = max(cap + 1, _COUNT_CEILING)

        try:
            for p in self._iter_matches(root, pattern):
                if not p.is_file():
                    continue
                if any(part in self.SKIP_DIRS for part in p.parts):
                    continue
                total += 1
                if len(results) < cap:
                    try:
                        results.append(str(p.relative_to(self._allowed_root)))
                    except ValueError:
                        # Under full access the searched root can sit outside
                        # the worktree this tool is anchored to; keep the
                        # absolute path rather than failing (same guard as
                        # GrepTool).
                        results.append(str(p))
                # Deliberately keep walking past the cap: counting only moves a
                # counter, it never materialises a path, so the real total is
                # cheap. Stop only at the absolute ceiling.
                if total >= ceiling:
                    total_exact = False
                    break
        except (ValueError, NotImplementedError) as e:
            # A malformed glob like '**.py' raises here (pathlib validates
            # lazily, during iteration).
            return ToolResult(success=False, output="",
                              error=f"invalid glob pattern: {e}")

        if total == 0:
            return ToolResult(
                success=True,
                output=f"(no files matched '{pattern}')",
                metadata={"matches": 0, "returned": 0, "total_matches": 0,
                          "total_is_lower_bound": False, "truncated": False},
            )

        truncated = len(results) < total
        output = "\n".join(results)
        if truncated:
            shown_total = str(total) if total_exact else f">={total}"
            output += (f"\n\n[truncated: showing {len(results)} of {shown_total} "
                       f"paths; narrow the path/glob or raise max_results]")

        return ToolResult(
            success=True,
            output=output,
            metadata={
                # `matches` is kept for backward compatibility and equals the
                # number of paths actually shown; `total_matches` is the true
                # number of matches (a lower bound when total_is_lower_bound).
                "matches": len(results),
                "returned": len(results),
                "total_matches": total,
                "total_is_lower_bound": not total_exact,
                "truncated": truncated,
            },
        )
