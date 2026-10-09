"""Continual Harness - R38.6.4.

Inspired by long-running-harness's Continual Harness (arXiv 2605.09998):
the agent can CRUD its own prompt / memory / skill state at runtime.
We never touch the immutable base system prompt; we only add /
update / delete "supplemental" entries that layer on top of it.

A simple version: a single JSON file per project, located at
``.kairos/harness.json``, with append-only history of edits.
The agent calls PUT/DELETE/POST via REST endpoints; the frontend
or a special chat command exposes a "self-tune" button.

Why a single file (not a full Key-Value schema with versioning):
Kairos's MemoryKB is already JSONL with FTS5. Harness is a
structured document (typed fields), so JSON fits. The version
history is preserved in ``harness_history.jsonl`` so the agent
can roll back a bad self-edit.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


HARNESS_VERSION = 1

# Default schema for a project harness state.
DEFAULT_HARNESS = {
    "version": HARNESS_VERSION,
    "supplemental_prompt": "",   # appended to the agent system_prompt
    "memory_notes": [],          # [ {key, value, tags} ] from remember()
    "skill_hints": [],            # [ {pattern, hint, source} ] discovered
    "tooling_prefs": {},          # free-form (e.g. {"test":"pytest","pkg":"uv"})
    "updated_at": 0.0,
    "history_count": 0,
}


def _load(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return dict(DEFAULT_HARNESS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # Forward-compat: any new fields keep their defaults
        for k, v in DEFAULT_HARNESS.items():
            data.setdefault(k, v)
        return data
    except Exception as exc:
        logger.warning("harness %s read failed: %s", path, exc)
        return dict(DEFAULT_HARNESS)


def _note_sort_key(note: Dict[str, Any]) -> float:
    """Recency key for a memory note: ``updated_at`` (a later edit) beats the
    original ``added_at``. Non-numeric / missing stamps sort last (0.0) rather
    than raising — a hand-edited file must not break assembly."""
    for field in ("updated_at", "added_at"):
        value = note.get(field)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def load_memory_notes(work_dir: Any, limit: int = 8) -> List[Dict[str, Any]]:
    """Read-only view of a project's harness ``memory_notes``, newest first.

    This is the *read* half of :meth:`HarnessStore.add_memory_note`. Without
    it a note recorded in the harness panel is written to
    ``<work_dir>/.kairos/harness/harness.json`` and never read back — the
    agent never sees it. :func:`kairos.memory.retrieval.assemble_coder_memory`
    calls this so harness notes reach the coder prompt.

    Unlike ``HarnessStore`` it never creates the harness directory: merely
    assembling memory must not litter ``.kairos/harness/`` into a project the
    user never enabled the harness for.

    Best-effort by contract — never raises, never swallows in silence: a
    missing file yields ``[]``; a corrupt file, a non-object top level, or a
    ``memory_notes`` of the wrong shape logs a WARNING and yields ``[]``.
    """
    path = Path(work_dir) / ".kairos" / "harness" / "harness.json"
    try:
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("harness %s unreadable (%s); ignoring", path, exc)
        return []
    if not isinstance(data, dict):
        logger.warning("harness %s: top level is %s, expected an object; ignoring",
                       path, type(data).__name__)
        return []
    notes = data.get("memory_notes")
    if notes is None:
        return []
    if not isinstance(notes, list):
        logger.warning("harness %s: memory_notes is %s, expected a list; ignoring",
                       path, type(notes).__name__)
        return []
    cleaned: List[Dict[str, Any]] = []
    for n in notes:
        if not isinstance(n, dict):
            logger.warning("harness %s: dropping non-object memory note %r", path, n)
            continue
        value = n.get("value")
        if not isinstance(value, str) or not value.strip():
            logger.warning("harness %s: dropping note %r with no usable value",
                           path, n.get("key"))
            continue
        cleaned.append(n)
    cleaned.sort(key=_note_sort_key, reverse=True)
    if isinstance(limit, int) and limit > 0:
        cleaned = cleaned[:limit]
    return cleaned


def _save(path: Path, data: Dict[str, Any]) -> None:
    data["updated_at"] = time.time()
    data["version"] = HARNESS_VERSION
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(path)


def _append_history(history_path: Path, op: str, payload: Dict[str, Any]):
    """Append-only edit log. Used for /refine-style rollback."""
    line = json.dumps(
        {"ts": time.time(), "op": op, "payload": payload},
        ensure_ascii=False,
    )
    with open(history_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


class HarnessStore:
    """Per-project store for the agent's Continual Harness state."""

    def __init__(self, work_dir: Path):
        self.root = Path(work_dir) / ".kairos" / "harness"
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "harness.json"
        self.history = self.root / "harness_history.jsonl"

    def load(self) -> Dict[str, Any]:
        return _load(self.path)

    def _commit(self, op: str, mutate_fn) -> Dict[str, Any]:
        data = self.load()
        before = json.dumps(data, sort_keys=True)
        mutate_fn(data)
        after = json.dumps(data, sort_keys=True)
        if before != after:
            data["history_count"] = data.get("history_count", 0) + 1
            _save(self.path, data)
            _append_history(self.history, op, {"before": before, "after": after})
        return data

    def set_supplemental_prompt(self, text: str) -> Dict[str, Any]:
        def m(d):
            d["supplemental_prompt"] = text
        return self._commit("set_supplemental_prompt", m)

    def add_memory_note(self, key: str, value: str,
                          tags: List[str] = None) -> Dict[str, Any]:
        def m(d):
            notes = d.setdefault("memory_notes", [])
            # Replace if key already exists
            for n in notes:
                if n.get("key") == key:
                    n["value"] = value
                    n["tags"] = tags or []
                    n["updated_at"] = time.time()
                    return
            notes.append({
                "key": key, "value": value,
                "tags": tags or [],
                "added_at": time.time(),
            })
        return self._commit("add_memory_note", m)

    def add_skill_hint(self, pattern: str, hint: str,
                          source: str = "agent") -> Dict[str, Any]:
        def m(d):
            hints = d.setdefault("skill_hints", [])
            for h in hints:
                if h.get("pattern") == pattern:
                    h["hint"] = hint
                    h["updated_at"] = time.time()
                    return
            hints.append({
                "pattern": pattern, "hint": hint,
                "source": source, "added_at": time.time(),
            })
        return self._commit("add_skill_hint", m)

    def set_tooling_pref(self, key: str, value: str) -> Dict[str, Any]:
        def m(d):
            d.setdefault("tooling_prefs", {})[key] = value
        return self._commit("set_tooling_pref", m)

    def remove(self, key: str) -> Dict[str, Any]:
        """Remove a memory_note or skill_hint by key/pattern."""
        def m(d):
            d["memory_notes"] = [n for n in d.get("memory_notes", [])
                                   if n.get("key") != key]
            d["skill_hints"] = [h for h in d.get("skill_hints", [])
                                  if h.get("pattern") != key]
        return self._commit("remove", m)

    def history_tail(self, limit: int = 20) -> List[Dict[str, Any]]:
        if not self.history.exists():
            return []
        with open(self.history, "r", encoding="utf-8") as f:
            lines = f.readlines()[-limit:]
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
        return out

    def rollback(self, history_id: str) -> bool:
        """Roll back to a specific history entry (long-running-harness semantics).
        ``history_id`` is the ``ts`` field of the target entry."""
        entries = self.history_tail(limit=1000)
        target = None
        for e in entries:
            if str(e.get("ts")) == history_id:
                target = e
                break
        if target is None:
            return False
        after = json.loads(target["payload"]["after"])
        _save(self.path, after)
        _append_history(self.history, "rollback", {"to": history_id})
        return True
