"""Per-round tool result cache.

Many tools re-read the same file / re-run the same grep / re-list the
same directory. Without caching, a Coder that calls file_read 5 times
on the same path makes 5 disk I/O calls and the Reviewer sees 5 copies
of the same content in tool result history.

This module is a tiny in-memory cache keyed by (tool_name, frozenset(args)).
Each LoopSession gets one cache; we wipe it at the start of each round
so a stale file content doesn'"'"'t leak between rounds.

Usage from tools:
    from kairos.tools.cache import get_cache
    cache = get_cache()
    key = ("file_read", path)
    if key in cache:
        return cache[key]
    result = ... actual read ...
    cache[key] = result
    return result

We pass the cache by attaching it to the agent'"'"'s tool instances via
`_wire_cache(tools, cache)` which sets `tool._cache = cache` on each.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

class ToolCache:
    """Thread-safe per-round tool result cache.

    Stores (key -> ToolResult) pairs. Keys are (tool_name, frozen_args)
    where frozen_args is a frozenset of (k, v) for the kwargs.

    Per-round lifetime: tools call cache.clear_round() at the end of
    each round. The orchestrator calls this from run_loop.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._data: Dict[Tuple[str, frozenset], Any] = {}
        # Per-round stats so we can report cache effectiveness.
        self.hits = 0
        self.misses = 0

    def clear(self):
        with self._lock:
            self._data.clear()
            self.hits = 0
            self.misses = 0

    def get(self, key: Tuple[str, frozenset]) -> Optional[Any]:
        with self._lock:
            if key in self._data:
                self.hits += 1
                return self._data[key]
            self.misses += 1
            return None

    def set(self, key: Tuple[str, frozenset], value: Any) -> None:
        with self._lock:
            self._data[key] = value

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    def stats(self) -> Dict[str, int]:
        return {"hits": self.hits, "misses": self.misses,
                "size": len(self._data), "hit_rate": round(self.hit_rate, 3)}

def make_key(tool_name: str, **kwargs) -> Tuple[str, frozenset]:
    """Build a hashable cache key from tool name + kwargs.

    Drops `_cache` and any other private keys the tool itself may add.
    """
    cleaned = {k: v for k, v in kwargs.items()
               if not k.startswith("_") and k not in ("self", "cls")}
    return (tool_name, frozenset(cleaned.items()))

# Module-level "current cache" pointer. Tools that opt in look this up
# and cache against it. Reset every round by run_loop.
_current: Optional[ToolCache] = None
_default: Optional[ToolCache] = None
_current_lock = threading.Lock()

def _resolve() -> ToolCache:
    """Return the active cache, lazily creating a stable singleton.

    We deliberately keep one persistent ToolCache instance alive at
    module scope so that `set_cache(None)` followed by `get_cache()`
    returns the SAME instance both times — otherwise tools that
    capture a reference would silently lose cache hits.
    """
    global _current, _default
    if _current is not None:
        return _current
    if _default is None:
        _default = ToolCache()
    return _default

def get_cache() -> ToolCache:
    """Get the active cache. Always non-None (module-level default)."""
    return _resolve()

def set_cache(cache: Optional[ToolCache]) -> None:
    """Install a new active cache. Pass None to reset to the default singleton."""
    global _current, _default
    with _current_lock:
        _current = cache
        if cache is None:
            # Reset the lazy default to a fresh singleton so the next
            # get_cache() call returns a clean, stable instance.
            _default = ToolCache()

def clear_round() -> None:
    """Wipe the active cache (call at the end of each round)."""
    cache = _resolve()
    cache.clear()