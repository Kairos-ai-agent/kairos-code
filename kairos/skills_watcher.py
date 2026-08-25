"""Skill hot-reload.

Watches the skills directories and re-reads files when they
change on disk. Mirrors Claude Code 2.1's hot-reload behavior.

The implementation is intentionally simple: we poll the mtimes
of all known ``*.md`` skill files on a fixed interval. We don't
use the `watchdog` library because:

  1. We don't want a hard dependency.
  2. Polling at 1Hz is plenty fast for "the user just edited a
     skill" workflows; the editor save + the next agent turn
     are typically seconds apart, not microseconds.

The watcher exposes a ``start()`` / ``stop()`` lifecycle and a
``reload_callback`` that the agent loop (or the API) can wire
to a real SkillsLoader re-discover.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Awaitable, Callable, List, Optional, Set

logger = logging.getLogger(__name__)


ReloadCallback = Callable[[Set[Path]], Awaitable[None]]


class SkillsWatcher:
    """Poll-based watcher for the skills directory.

    The watcher maintains a `last_seen` mtime map and calls
    `reload_callback` whenever a file's mtime changes. Created and
    tracked files are remembered so deletions are also surfaced.

    Usage:
        watcher = SkillsWatcher([Path("~/.kairos/skills")])
        watcher.set_callback(my_reload_handler)
        await watcher.start()     # spawns a background task
        ...
        await watcher.stop()
    """

    def __init__(
        self,
        skill_dirs: List[Path],
        interval_s: float = 1.0,
    ):
        self.skill_dirs: List[Path] = [Path(d) for d in skill_dirs]
        self.interval_s: float = max(0.1, interval_s)
        self._mtimes: dict[Path, float] = {}
        self._task: Optional[asyncio.Task] = None
        self._stopped = asyncio.Event()
        self._callback: Optional[ReloadCallback] = None

    def set_callback(self, callback: ReloadCallback) -> None:
        self._callback = callback

    # -- lifecycle --------------------------------------------------------

    async def start(self) -> None:
        """Spawn the background polling task."""
        if self._task is not None:
            return
        # Seed mtimes so the first scan doesn't fire spurious
        # "everything is new" callbacks.
        self._mtimes = self._scan_mtimes()
        self._stopped.clear()
        self._task = asyncio.create_task(self._run(), name="skills-watcher")

    async def stop(self) -> None:
        """Signal the task to exit and await it."""
        if self._task is None:
            return
        self._stopped.set()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def stop_sync(self) -> None:
        """Sync-friendly variant: signal the task to exit but don't
        await it. The actual cleanup happens on the next event loop
        tick; this is safe to call from non-async code (e.g.
        orchestrator shutdown, tests)."""
        if self._task is None:
            return
        self._stopped.set()
        # We don't await the task — the caller is responsible for
        # letting the loop run one more tick if they need a clean
        # shutdown. For tests this is fine because the loop will
        # clean up the task on the next iteration.
        try:
            self._task.cancel()
        except RuntimeError:
            pass
        self._task = None

    # -- internals --------------------------------------------------------

    def _scan_mtimes(self) -> dict[Path, float]:
        out: dict[Path, float] = {}
        for d in self.skill_dirs:
            if not d.exists() or not d.is_dir():
                continue
            for md in d.rglob("*.md"):
                try:
                    out[md.resolve()] = md.stat().st_mtime
                except OSError:
                    # File might have been deleted between rglob and
                    # stat; skip silently.
                    continue
        return out

    async def _run(self) -> None:
        """Poll forever (or until stop) and fire the callback on
        change."""
        while not self._stopped.is_set():
            await asyncio.sleep(self.interval_s)
            if self._stopped.is_set():
                break
            new_mtimes = self._scan_mtimes()
            changed = self._diff(self._mtimes, new_mtimes)
            if changed:
                self._mtimes = new_mtimes
                if self._callback is not None:
                    try:
                        await self._callback(changed)
                    except Exception as exc:
                        logger.warning(
                            "skills watcher: callback raised: %s", exc
                        )

    @staticmethod
    def _diff(
        old: dict[Path, float],
        new: dict[Path, float],
    ) -> Set[Path]:
        """Return the set of files that appeared, disappeared, or
        whose mtime changed between two scans."""
        changed: Set[Path] = set()
        old_keys = set(old.keys())
        new_keys = set(new.keys())
        # Added or removed.
        for k in new_keys - old_keys:
            changed.add(k)
        for k in old_keys - new_keys:
            changed.add(k)
        # Modified.
        for k in old_keys & new_keys:
            if old[k] != new[k]:
                changed.add(k)
        return changed
