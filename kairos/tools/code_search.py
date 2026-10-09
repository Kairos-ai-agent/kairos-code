"""CodeSearchTool — semantic code search over the project (semble-backed).

The agent's usual way to find code is grep + read: guess an identifier, read
whole files, repeat. That is exact and cheap when the name is known and
expensive when it is not. This tool answers "where is X handled?" with the
handful of code chunks that are actually about X, so the model reads a snippet
instead of a file — on a real codebase that is a ~99% token saving over
grep + read.

Three properties the wiring depends on, so they are stated once here:

* **semble is optional at every stage.** The package is imported lazily, inside
  the call that needs it, never at import time. A machine without it still
  starts Kairos; this one tool answers with a readable error instead of an
  ImportError at agent-construction time.
* **Indexing and search are CPU-bound and can take seconds.** They run in a
  worker thread (``asyncio.to_thread``) so the agent loop and the websocket
  stay responsive while a large tree is indexed.
* **The result budget is enforced here.** Snippets are truncated per chunk and
  the whole payload is capped, so a search cannot flood the context.

Indexes are cached on disk by semble itself (``%LOCALAPPDATA%\\semble\\Cache``
on Windows) and revalidated against the file tree on every call, so a fresh
build happens once and later searches reuse it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from kairos.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)

# Matches GrepTool's cap: one tool must not be able to blow up the context on
# its own.
_MAX_OUTPUT_CHARS = 50_000
_DEFAULT_TOP_K = 8
_MAX_TOP_K = 50
_DEFAULT_SNIPPET_LINES = 40

# Full HuggingFace mirror, used only as a fallback (see build_semble_index).
_HF_MIRROR = "https://hf-mirror.com"


class SembleUnavailableError(RuntimeError):
    """Raised when the optional ``semble`` backend cannot be imported."""


def build_semble_index(root: Path) -> Any:
    """Build (or load from the on-disk cache) a semble index for ``root``.

    Blocking and CPU-bound — call it from a worker thread. Kept as a
    module-level function so tests can inject their own factory and so the
    ``semble`` import stays off ``kairos.tools``' import path.
    """
    try:
        from semble import ContentType, SembleIndex
    except ImportError as exc:  # pragma: no cover - reached only without semble
        raise SembleUnavailableError(
            "code_search requires the 'semble' package, which is not installed. "
            "Install it with `pip install semble`."
        ) from exc

    # ``from_path`` already consults the validated disk cache and reindexes
    # incrementally, but it never persists a fresh build itself (the MCP layer
    # calls this explicitly, and so do we).
    try:
        from semble.cache import save_index_to_cache
    except ImportError:  # pragma: no cover - older semble without the cache API
        save_index_to_cache = None  # type: ignore[assignment]

    def _build() -> Any:
        index = SembleIndex.from_path(
            root, content=ContentType.CODE, show_progress_bar=False,
        )
        if save_index_to_cache is not None:
            try:
                save_index_to_cache(index, str(root))
            except Exception as exc:  # noqa: BLE001 - a cache write failure is never fatal
                # The index is still returned and usable; losing the disk cache
                # only costs a re-index on the next search, so this is an
                # ignorable performance miss, not an error.
                logger.debug("code_search: save_index_to_cache failed "
                             "(root=%s): %s", root, exc)
        return index

    try:
        return _build()
    except Exception:
        # The model comes from huggingface.co, which is unreachable from
        # mainland China: the very first search would otherwise hang and then
        # fail on a machine that can otherwise run everything. Retry once
        # through hf-mirror.com (a complete mirror of the Hub) when the
        # operator has not already chosen an endpoint. Nothing is uploaded, and
        # an explicit HF_ENDPOINT always wins.
        if os.environ.get("HF_ENDPOINT"):
            raise
        os.environ["HF_ENDPOINT"] = _HF_MIRROR
        return _build()


class CodeSearchTool(BaseTool):
    """Semantic ("where is this handled?") search over the project."""

    name = "code_search"
    description = (
        "Semantic code search: describe what you are looking for in natural "
        "language or a rough phrase and get the most relevant code snippets, "
        "each with its file path and line range. Use it to locate code when "
        "you do not know the exact identifier (unlike grep, which needs the "
        "literal text). Results are snippets, not whole files, so prefer this "
        "before reading files you would only be guessing at. The first call on "
        "a project builds a local index and may take a while; later calls are "
        "fast."
    )

    def __init__(
        self,
        allowed_root: str | Path = ".",
        *,
        index: Any = None,
        index_factory: Callable[[Path], Any] | None = None,
        top_k: int = _DEFAULT_TOP_K,
    ):
        super().__init__(allowed_root)
        # ``index`` / ``index_factory`` exist for tests and for callers that
        # already hold an index; production wiring passes only ``allowed_root``
        # and lets the default factory build lazily on the first search.
        self._index = index
        self._index_factory = index_factory or build_semble_index
        self._index_root: Path | None = None
        self._build_lock = threading.Lock()
        self._default_top_k = top_k

    def to_schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Natural-language or keyword description of the "
                            "code you are looking for, e.g. 'where are agent "
                            "tools registered' or 'retry with backoff'."
                        ),
                    },
                    "top_k": {
                        "type": "integer",
                        "default": self._default_top_k,
                        "description": "Maximum number of code chunks to return (1-50).",
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "Directory to search within, relative to the "
                            "project root. Defaults to '.' (the whole project)."
                        ),
                    },
                    "max_snippet_lines": {
                        "type": "integer",
                        "default": _DEFAULT_SNIPPET_LINES,
                        "description": "Maximum lines of each code snippet to include.",
                    },
                    "filter_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional list of repo-relative file paths to "
                            "restrict the search to."
                        ),
                    },
                    "refresh": {
                        "type": "boolean",
                        "default": False,
                        "description": "Rebuild the index instead of reusing the in-memory one.",
                    },
                },
                "required": ["query"],
            },
        }

    async def execute(
        self,
        query: str = "",
        top_k: int | None = None,
        path: str = ".",
        max_snippet_lines: int | None = None,
        filter_paths: list[str] | None = None,
        refresh: bool = False,
        **kwargs,
    ) -> ToolResult:
        if not isinstance(query, str) or not query.strip():
            return ToolResult(success=False, output="", error="query is required")

        try:
            root = self._resolve_safe(path)
        except PermissionError as e:
            return ToolResult(success=False, output="", error=str(e))

        if not root.exists():
            return ToolResult(success=False, output="", error=f"path does not exist: {path}")
        if not root.is_dir():
            return ToolResult(success=False, output="", error=f"path is not a directory: {path}")

        if top_k is None:
            k = self._default_top_k
        else:
            try:
                k = int(top_k)
            except (TypeError, ValueError):
                return ToolResult(success=False, output="", error="top_k must be an integer")
        k = max(1, min(k, _MAX_TOP_K))

        if max_snippet_lines is None:
            snippet_lines = _DEFAULT_SNIPPET_LINES
        else:
            try:
                snippet_lines = max(1, int(max_snippet_lines))
            except (TypeError, ValueError):
                return ToolResult(success=False, output="",
                                  error="max_snippet_lines must be an integer")

        # Everything below touches semble (import, index build, search) and is
        # blocking — hand the whole unit of work to a worker thread.
        try:
            results, built = await asyncio.to_thread(
                self._search_sync,
                root,
                query,
                k,
                snippet_lines,
                list(filter_paths) if filter_paths else None,
                bool(refresh),
            )
        except SembleUnavailableError as e:
            return ToolResult(success=False, output="", error=str(e))
        except Exception as e:  # noqa: BLE001 - a backend failure is a tool error, not a crash
            return ToolResult(success=False, output="", error=f"code search failed: {e}")

        if not results:
            return ToolResult(
                success=True,
                output="(no matches)",
                metadata={"results": 0, "index_built": built},
            )

        out = self._format(root, results, snippet_lines)
        truncated = False
        if len(out) > _MAX_OUTPUT_CHARS:
            out = out[:_MAX_OUTPUT_CHARS] + f"\n... (truncated; {len(results)} results total)"
            truncated = True

        return ToolResult(
            success=True,
            output=out,
            metadata={"results": len(results), "index_built": built, "truncated": truncated},
        )

    # -- internals (run inside the worker thread) --------------------------

    def _get_index(self, root: Path) -> tuple[Any, bool]:
        """Return ``(index, built_this_call)`` for ``root``, building once.

        An injected ``index`` is authoritative and reused for every call; a
        built index is remembered per root, so pointing the tool at a different
        directory rebuilds instead of silently searching the first one.
        """
        with self._build_lock:
            if self._index is not None and (
                self._index_root is None or self._index_root == root
            ):
                return self._index, False
            index = self._index_factory(root)
            self._index = index
            self._index_root = root
            return index, True

    def _search_sync(
        self,
        root: Path,
        query: str,
        top_k: int,
        snippet_lines: int,
        filter_paths: list[str] | None,
        refresh: bool,
    ) -> tuple[list[Any], bool]:
        if refresh:
            with self._build_lock:
                self._index = None
                self._index_root = None
        index, built = self._get_index(root)
        results = index.search(query, top_k=top_k, filter_paths=filter_paths)
        return list(results or []), built

    def _format(self, root: Path, results: list[Any], snippet_lines: int) -> str:
        blocks: list[str] = []
        for res in results:
            chunk = getattr(res, "chunk", res)
            location = self._format_location(chunk)
            score = getattr(res, "score", None)
            header = location
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                header = f"{location}  (score {float(score):.3f})"
            body = getattr(chunk, "content", "") or ""
            lines = body.splitlines()
            if snippet_lines and len(lines) > snippet_lines:
                omitted = len(lines) - snippet_lines
                body = "\n".join(lines[:snippet_lines]) + (
                    f"\n... (+{omitted} more lines in {location})"
                )
            blocks.append(f"{header}\n{body}".rstrip())
        return "\n\n".join(blocks)

    def _format_location(self, chunk: Any) -> str:
        path = getattr(chunk, "file_path", None) or "?"
        # semble already returns paths relative to the indexed root; normalise
        # an absolute path so the agent sees a project-relative location.
        try:
            p = Path(str(path))
            if p.is_absolute():
                path = str(p.relative_to(self._allowed_root)).replace("\\", "/")
        except (ValueError, OSError):
            pass
        start = getattr(chunk, "start_line", None)
        end = getattr(chunk, "end_line", None)
        if start is None:
            return str(path)
        return f"{path}:{start}-{end if end is not None else start}"
