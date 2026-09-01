"""Round 17: full-text skill search via SQLite FTS5.

The existing :mod:`kairos.skills` loader matches skills by
keyword heuristics — fast but limited. As the skill library
grows (R9 shipped 14 the skill library skills and the team is
encouraged to write more), a real full-text index becomes
useful: "find me the skill that mentions pytest fixtures"
should rank the relevant skills above the irrelevant ones.

This module builds an in-memory SQLite FTS5 index from a
``SkillsLoader.discover()`` result, exposes a simple
``search(query, limit) -> list[dict]`` API, and ships a CLI
subcommand so the team can ``kairos skill search "pytest"``
from the terminal.

If SQLite is built without FTS5 (rare on modern Python
distributions but possible on stripped-down containers),
the module falls back to a Python-side substring / token
matcher that returns the same result shape.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Cached: is FTS5 available in this Python's sqlite3?
_FTS5_AVAILABLE: Optional[bool] = None


def fts5_available() -> bool:
    """Detect whether the running sqlite3 module has FTS5 compiled in."""
    global _FTS5_AVAILABLE
    if _FTS5_AVAILABLE is not None:
        return _FTS5_AVAILABLE
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        _FTS5_AVAILABLE = True
    except sqlite3.OperationalError:
        _FTS5_AVAILABLE = False
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return _FTS5_AVAILABLE


def _skill_to_row(skill) -> Tuple[str, str, str, float]:
    """Map a Skill dataclass to (name, source_path, body, priority).

    Defensive against any skill-shaped object: missing attrs
    default to "" / 0.5 instead of raising. This keeps the
    index builder robust against partial / mock skill objects
    in tests.
    """
    try:
        name = skill.name
    except AttributeError:
        name = ""
    try:
        body = skill.body or ""
    except Exception:
        body = ""
    # Truncate body for FTS (most skills are 4-16KB; FTS index
    # works fine with 4KB blobs but 16KB is wasteful — we keep
    # 4KB which is enough for keyword search).
    body_4k = body[:4096]
    try:
        src = str(skill.source_path) if skill.source_path else ""
    except Exception:
        src = ""
    try:
        prio = float(skill.priority) if skill.priority is not None else 0.5
    except (TypeError, ValueError, AttributeError):
        prio = 0.5
    return (name, src, body_4k, prio)


def _build_index(skills: List[Any], db_path: Path) -> int:
    """Build a fresh FTS5 index of ``skills`` in ``db_path``.

    Returns the number of skills indexed. If FTS5 is not
    available, returns 0 and the index is empty (the
    fallback path uses in-Python matching).
    """
    if not fts5_available():
        logger.warning("FTS5 not available; skill search falls back to "
                       "in-Python matching (slower, less accurate)")
        return 0
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE skills_fts USING fts5("
            "name, source_path, body, priority UNINDEXED, "
            "tokenize='porter')"
        )
        for s in skills:
            row = _skill_to_row(s)
            conn.execute(
                "INSERT INTO skills_fts(name, source_path, body, priority) "
                "VALUES (?, ?, ?, ?)",
                row,
            )
        conn.commit()
        return len(skills)
    finally:
        conn.close()


def _search_fts5(db_path: Path, query: str, limit: int) -> List[Dict[str, Any]]:
    """Run an FTS5 MATCH query and return ranked hits."""
    conn = sqlite3.connect(str(db_path))
    try:
        # FTS5 syntax: wrap the user query in double quotes to
        # avoid accidental operator interpretation; the user's
        # words are still tokenized.
        cur = conn.execute(
            "SELECT name, source_path, priority, "
            "  rank * -1 AS score, snippet(skills_fts, 2, '«', '»', '...', 12) "
            "FROM skills_fts WHERE skills_fts MATCH ? "
            "ORDER BY rank LIMIT ?",
            (f'"{query}"', limit),
        )
        return [
            {"name": name, "source_path": src, "priority": prio,
             "score": float(score), "snippet": snippet}
            for name, src, prio, score, snippet in cur.fetchall()
        ]
    except sqlite3.OperationalError as exc:
        # Bad FTS5 query (e.g. reserved chars) — return empty
        logger.debug("FTS5 query failed: %s", exc)
        return []
    finally:
        conn.close()


def _search_python(skills: List[Any], query: str, limit: int
                   ) -> List[Dict[str, Any]]:
    """Fallback Python-side search when FTS5 is not available."""
    q_tokens = [t.lower() for t in query.split() if t]
    if not q_tokens:
        # Empty query — return by priority
        out = sorted(skills, key=lambda s: -float(s.priority))
        return [{"name": s.name, "source_path": str(s.source_path),
                 "priority": float(s.priority), "score": 0.0,
                 "snippet": (s.body or "")[:120]}
                for s in out[:limit]]
    scored: List[Tuple[float, Any]] = []
    for s in skills:
        body = (s.body or "").lower()
        name = s.name.lower()
        score = 0.0
        for tok in q_tokens:
            if tok in name:
                score += 3.0  # name match is the strongest signal
            score += body.count(tok) * 0.5
        if score > 0:
            scored.append((score, s))
    scored.sort(key=lambda x: -x[0])
    return [{"name": s.name, "source_path": str(s.source_path),
             "priority": float(s.priority), "score": float(score),
             "snippet": (s.body or "")[:120]}
            for score, s in scored[:limit]]


def build_index_from_loader(loader, db_path: Path) -> int:
    """Build a fresh FTS5 index from a SkillsLoader instance."""
    skills = loader.discover()
    return _build_index(skills, db_path)


def search(query: str, *, loader=None, db_path: Optional[Path] = None,
          limit: int = 10) -> List[Dict[str, Any]]:
    """Search the skill library.

    If ``db_path`` is None, builds a one-shot in-memory index
    from ``loader.discover()`` (slow but correct). If a
    ``db_path`` is provided, the caller is expected to have
    pre-built the index via ``build_index_from_loader``.

    Returns a list of ``{name, source_path, priority, score,
    snippet}`` dicts, ordered by descending score.
    """
    if db_path is not None and fts5_available() and db_path.exists():
        return _search_fts5(db_path, query, limit)
    # Fall back to Python-side search
    if loader is None:
        raise ValueError(
            "Either db_path (with a pre-built index) or loader must be provided"
        )
    return _search_python(loader.discover(), query, limit)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli(argv: List[str]) -> int:
    import argparse
    p = argparse.ArgumentParser(
        prog="kairos.skill_search",
        description="Full-text search over the skill library",
    )
    p.add_argument("query", help="Search query")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--db", help="Pre-built FTS5 db path (recommended)")
    p.add_argument("--project-dir", default=".",
                   help="Project root to discover skills from")
    args = p.parse_args(argv)
    if args.db:
        db_path = Path(args.db)
        results = search(args.query, db_path=db_path, limit=args.limit)
    else:
        from kairos.skills import SkillsLoader
        loader = SkillsLoader(project_dir=Path(args.project_dir))
        results = search(args.query, loader=loader, limit=args.limit)
    for r in results:
        prio = r.get("priority", 0.0)
        score = r.get("score", 0.0)
        snippet = r.get("snippet", "")
        # Compact one-line output for terminal use
        print(f"{r['name']:50s}  score={score:6.2f}  prio={prio:.2f}")
        if snippet:
            print(f"  {snippet[:120]}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))
