"""A project tree snapshot and its diff — "what did this round really touch?".

This is the neutral home of the snapshot/diff machinery the WeChat channel
(``kairos/weixin_ilink.py``) already used to decide which files a turn actually
produced: take a ``{path -> (mtime, size)}`` snapshot before the agent runs,
take another after, and the files that are new or whose ``(mtime, size)``
changed are exactly what the round wrote. It moved here so the WeChat lane and
the general chat lane (``kairos/skeleton``) share **one** implementation
instead of two that can drift; ``kairos/weixin_ilink`` now re-exports these
names, so nothing that imported them from there changed.

Design notes carried over unchanged:

* The scan is **best-effort and bounded**: an entry cap and a wall-clock cap
  mean a huge directory can never slow a reply down, and any error simply
  yields "fewer files seen", never an exception.
* Snapshot keys are ``os.path.realpath`` so the same file seen through two
  roots or a symlink collapses to one entry.
* The ignore list (dirs / prefixes / suffixes) is the single source of truth
  shared by every consumer.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: Directory names skipped entirely while snapshotting (a hit at any depth
#: prunes that whole subtree). Kept in one place on purpose:
#: * ``.git`` / ``node_modules`` / ``.venv`` / ``venv`` / ``site-packages`` /
#:   ``__pycache__``: version control and dependency / bytecode caches -- their
#:   churn is not a deliverable the user asked for;
#: * ``attachments``: the **inbound** attachment staging dir (what the user sent
#:   in); echoing it back as "this round's output" would bounce the user's own
#:   upload at them -- pure noise;
#: * ``.kairos-worktrees`` / ``runs`` / the ``.kairos-`` prefix: runtime dirs
#:   (sandbox work trees, task-run records, locks and logs) -- a work tree is
#:   scanned as its own **separate root**, never re-entered here;
#: * ``dist`` / ``build`` / ``.mypy_cache`` / ``.pytest_cache`` / ``.tox`` /
#:   ``.eggs``: build outputs and tool caches.
SNAPSHOT_IGNORE_DIRS = frozenset({
    ".git", "node_modules", ".venv", "venv", "site-packages", "__pycache__",
    "attachments", "runs", "dist", "build", ".eggs",
    ".mypy_cache", ".pytest_cache", ".tox", ".ruff_cache",
})

#: Names (dir or file) starting with one of these prefixes are skipped --
#: runtime dirs/files (``.kairos-worktrees`` and the like, plus ``.kairos_*``).
SNAPSHOT_IGNORE_PREFIXES = (".kairos-", ".kairos_")

#: File suffixes skipped (temp / intermediate files).
SNAPSHOT_IGNORE_SUFFIXES = (
    ".tmp", ".temp", ".swp", ".swo", ".pyc", ".pyo", ".log", ".lock",
)

#: Per-scan entry cap (including ignored entries): stop a huge directory from
#: dragging a single reply down.
SNAPSHOT_MAX_ENTRIES = 5000
#: Per-scan wall-clock cap (seconds); on timeout use whatever was scanned so
#: far, never slow a reply down.
SNAPSHOT_MAX_SECONDS = 0.5


def normalize_roots(value: Any) -> List[str]:
    """Normalise "the roots a resolver returned" to a de-duplicated list.

    Accepts ``None`` / a single path (``str``/``Path``) / a sequence of paths --
    so a caller may hand over just the project root, or the "project root +
    coder work tree" pair, and either shape is digestible.
    """
    if value is None:
        return []
    if isinstance(value, (str, Path)):
        single = str(value).strip()
        return [single] if single else []
    out: List[str] = []
    try:
        items = list(value)
    except TypeError:
        single = str(value).strip()
        return [single] if single else []
    for item in items:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def snapshot_ignored_dir(name: str) -> bool:
    """Whether this **directory** is skipped whole while snapshotting."""
    low = (name or "").lower()
    if low in SNAPSHOT_IGNORE_DIRS:
        return True
    return any(low.startswith(p) for p in SNAPSHOT_IGNORE_PREFIXES)


def snapshot_ignored_file(name: str) -> bool:
    """Whether this **file** is skipped while snapshotting."""
    low = (name or "").lower()
    if any(low.startswith(p) for p in SNAPSHOT_IGNORE_PREFIXES):
        return True
    return low.endswith(SNAPSHOT_IGNORE_SUFFIXES)


def snapshot_tree_files(
    roots: Any,
    *,
    max_entries: int = SNAPSHOT_MAX_ENTRIES,
    max_seconds: float = SNAPSHOT_MAX_SECONDS,
) -> Dict[str, Tuple[float, int]]:
    """Snapshot one or more roots as ``{realpath: (mtime, size)}``.

    Best-effort, capped, never raises. Any exception / missing root / limit hit
    only means "less was captured" -- the caller degrades to "no output signal
    this round", never an error. Keys are ``os.path.realpath`` so the same file
    seen through different roots/symlinks lines up and de-duplicates.
    """
    import stat as _stat

    snap: Dict[str, Tuple[float, int]] = {}
    root_list = normalize_roots(roots)
    if not root_list:
        return snap
    try:
        deadline = time.monotonic() + max(0.0, float(max_seconds))
    except (TypeError, ValueError):
        deadline = time.monotonic() + SNAPSHOT_MAX_SECONDS
    try:
        limit = max(0, int(max_entries))
    except (TypeError, ValueError):
        limit = SNAPSHOT_MAX_ENTRIES
    count = 0
    for root in root_list:
        try:
            root_path = Path(str(root))
            if not root_path.is_dir():
                continue
        except (OSError, ValueError):
            continue
        try:
            for dirpath, dirnames, filenames in os.walk(root_path):
                if count >= limit or time.monotonic() >= deadline:
                    return snap
                # Prune in place: an ignored dir never has its subtree walked.
                dirnames[:] = [d for d in dirnames
                               if not snapshot_ignored_dir(d)]
                for filename in filenames:
                    if count >= limit or time.monotonic() >= deadline:
                        return snap
                    count += 1
                    if snapshot_ignored_file(filename):
                        continue
                    full = os.path.join(dirpath, filename)
                    try:
                        st = os.stat(full)
                        if not _stat.S_ISREG(st.st_mode):
                            continue
                        key = os.path.realpath(full)
                    except OSError:
                        continue
                    snap[key] = (st.st_mtime, st.st_size)
        except Exception:  # noqa: BLE001 - a whole-root snapshot is best-effort
            continue
    return snap


def diff_touched_files(
    before: Optional[Dict[str, Tuple[float, int]]],
    after: Optional[Dict[str, Tuple[float, int]]],
) -> List[Path]:
    """Snapshot difference: files this round **added** or changed.

    A file counts as changed when its ``(mtime, size)`` differs from the before
    snapshot. Untouched older files (even ones the model names in its reply) are
    not here. Order is stable (sorted), independent of ``os.walk`` order.
    """
    before = before or {}
    after = after or {}
    touched = [key for key, sig in after.items() if before.get(key) != sig]
    touched.sort()          # stable order, independent of walk order
    return [Path(p) for p in touched]


__all__ = [
    "SNAPSHOT_IGNORE_DIRS",
    "SNAPSHOT_IGNORE_PREFIXES",
    "SNAPSHOT_IGNORE_SUFFIXES",
    "SNAPSHOT_MAX_ENTRIES",
    "SNAPSHOT_MAX_SECONDS",
    "normalize_roots",
    "snapshot_ignored_dir",
    "snapshot_ignored_file",
    "snapshot_tree_files",
    "diff_touched_files",
]
