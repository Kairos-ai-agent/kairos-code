"""Cognee-style 4-op memory layer for Kairos.

A drop-in replacement / alternative for ``kairos.memory_hierarchy``
that exposes the four atomic memory operations from
``topoteretes/cognee`` (Apache 2.0, 27K stars):

    remember(key, value, scope="project", tags=())
    recall(query, scope="project", limit=10)
    forget(key, scope="project")
    improve(key, feedback, scope="project")

Storage is local-first (JSON file under ``<data_dir>/memory/kb.json``).
The four operations are atomic per call, and the file is rewritten
on every mutation with a `.tmp` + `os.replace` rename so a crash
mid-write doesn't corrupt prior state.

Compared to ``kairos.memory_hierarchy``:
  - 3 tiers (user / project / session) → 3 scopes (user / project / session)
  - File-per-tier vs single file
  - No "recall_always" / "recall_never" shortcuts (round 8 added those;
    we leave the more general "recall" + "remember" interface)

Designed so the next round can swap the backend for graphiti / cognee
without changing the public API: the ``MemoryKB`` class is the seam.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

VALID_SCOPES = ("user", "project", "session")


@dataclass
class MemoryEntry:
    """One remembered fact. ``key`` is the lookup handle within scope."""
    key: str
    value: Any
    scope: str = "project"
    tags: List[str] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    feedback: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MemoryEntry":
        # Explicit per-field handling so we don't trip over the
        # dataclass-required positional args (key, value).
        return cls(
            key=d.get("key"),
            value=d.get("value"),
            scope=d.get("scope", "project"),
            tags=list(d.get("tags") or []),
            created_at=float(d.get("created_at") or 0.0),
            updated_at=float(d.get("updated_at") or 0.0),
            feedback=list(d.get("feedback") or []),
        )


def _now() -> float:
    return time.time()


class MemoryKB:
    """Cognee-style 4-op memory layer with local JSON storage.

    Thread-safe via a single lock (the in-memory dict + file rewrite
    are both serialized). For multi-process access, mount a single
    process as the writer.
    """

    def __init__(self, storage_path: Optional[Path] = None):
        if storage_path is None:
            data_dir = Path(os.environ.get(
                "KAIROS_DATA_DIR",
                Path(__file__).resolve().parent.parent / "data",
            ))
            storage_path = data_dir / "memory" / "kb.json"
        self.path = Path(storage_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # In-memory mirror: scope -> key -> entry
        self._store: Dict[str, Dict[str, MemoryEntry]] = {
            s: {} for s in VALID_SCOPES
        }
        self._load()

    # --- persistence -----------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("memory_kb: failed to load %s: %s", self.path, exc)
            return
        for scope in VALID_SCOPES:
            for key, entry in (raw.get(scope) or {}).items():
                self._store[scope][key] = MemoryEntry.from_dict(entry)

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps(
                    {s: {k: e.to_dict() for k, e in d.items()}
                     for s, d in self._store.items()},
                    ensure_ascii=False, indent=2,
                ),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
        except OSError as exc:
            logger.warning("memory_kb: save failed: %s", exc)
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    # --- the 4 atomic ops ------------------------------------------------

    def remember(
        self, key: str, value: Any,
        scope: str = "project", tags: Iterable[str] = (),
    ) -> MemoryEntry:
        """Insert or update a fact under ``scope:key``."""
        if scope not in VALID_SCOPES:
            raise ValueError(f"invalid scope {scope!r} (must be one of {VALID_SCOPES})")
        now = _now()
        with self._lock:
            existing = self._store[scope].get(key)
            if existing is None:
                entry = MemoryEntry(
                    key=key, value=value, scope=scope,
                    tags=list(tags), created_at=now, updated_at=now,
                )
                self._store[scope][key] = entry
            else:
                existing.value = value
                existing.updated_at = now
                # Tags are merged (set union)
                merged = list(dict.fromkeys([*existing.tags, *tags]))
                existing.tags = merged
                entry = existing
            self._save()
        return entry

    def recall(
        self, query: str, scope: str = "project", limit: int = 10,
    ) -> List[MemoryEntry]:
        """Token-overlap recall within a scope, with a semantic fallback.

        Phase 1 (exact): lowercase substring / token match on key +
        stringified value — kept as-is for backward compatibility.
        Phase 2 (semantic): when exact matches are thinner than
        ``limit``, entries that missed phase 1 are re-ranked against
        the query with a lightweight TF-IDF cosine (see
        ``kairos.memory.semantic``) and appended by descending
        similarity. This rescues "same meaning, different wording"
        queries without any external dependency.

        Ordering: exact hits first (newest first), then semantic
        hits by similarity. Newest-first within the exact set
        preserves the pre-semantic behavior for existing callers.
        """
        if scope not in VALID_SCOPES:
            raise ValueError(f"invalid scope {scope!r}")
        q = query.lower().strip()
        tokens = [t for t in q.split() if t]
        exact: List[MemoryEntry] = []
        missed: List[MemoryEntry] = []
        with self._lock:
            for entry in self._store[scope].values():
                haystack = (entry.key + " " + _stringify(entry.value)).lower()
                if (q and q in haystack) or (tokens and any(t in haystack for t in tokens)):
                    exact.append(entry)
                else:
                    missed.append(entry)
        exact.sort(key=lambda e: (e.updated_at, e.key), reverse=True)
        out = exact[:max(0, limit)]
        # Semantic phase: only when exact recall left headroom.
        if q and len(out) < limit and missed:
            try:
                from kairos.memory.semantic import rank_by_similarity
                docs = [e.key + " " + _stringify(e.value) for e in missed]
                ranked = rank_by_similarity(query, docs)
                for idx, _score in ranked:
                    out.append(missed[idx])
                    if len(out) >= limit:
                        break
            except Exception:
                logger.debug("memory_kb: semantic recall failed", exc_info=True)
        return out[:max(0, limit)]

    def forget(self, key: str, scope: str = "project") -> bool:
        """Delete one fact. Returns True if it was present."""
        if scope not in VALID_SCOPES:
            raise ValueError(f"invalid scope {scope!r}")
        with self._lock:
            if key in self._store[scope]:
                del self._store[scope][key]
                self._save()
                return True
        return False

    def improve(
        self, key: str, feedback: str, scope: str = "project",
    ) -> Optional[MemoryEntry]:
        """Append a feedback string to an entry's ``feedback`` list
        and bump ``updated_at``. The entry itself is unchanged —
        human feedback is recorded for later review or for an
        LLM-driven consolidation pass."""
        if scope not in VALID_SCOPES:
            raise ValueError(f"invalid scope {scope!r}")
        with self._lock:
            entry = self._store[scope].get(key)
            if entry is None:
                return None
            entry.feedback.append(feedback)
            entry.updated_at = _now()
            self._save()
        return entry

    # --- queries ---------------------------------------------------------

    def get(self, key: str, scope: str = "project") -> Optional[MemoryEntry]:
        with self._lock:
            return self._store[scope].get(key)

    def list_keys(self, scope: str = "project") -> List[str]:
        with self._lock:
            return sorted(self._store[scope].keys())

    def all_entries(self, scope: str = "project") -> List[MemoryEntry]:
        with self._lock:
            return list(self._store[scope].values())

    # --- cross-scope (used for "global memory" queries) ----------------

    def recall_all_scopes(
        self, query: str, limit: int = 10,
    ) -> Dict[str, List[MemoryEntry]]:
        return {s: self.recall(query, scope=s, limit=limit) for s in VALID_SCOPES}


def _stringify(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    try:
        return json.dumps(v, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(v)
