"""Document retrieval (RAG) over file content -- FTS5 first, honest about the rest.

Why this exists
---------------
``core/persistence.py``'s ``project_files`` table stores whole document bodies
inline ("the full content is stored inline so the Coder can see it") but used
to say, in the same comment, "We don't index `content` (FTS5 could be added
later)". So the only way a document reached a prompt was:
``build_reference_digest`` inlining the first 8 files (newest first), and the
skeleton worker dumping *every* body of a document-set workspace into one
prompt until it hit a size cap. Both are "read it all, in upload order" -- they
have no notion of what the current task actually asks for. On a workspace of
any size that is exactly the failure the evaluation report named: the whole
workspace gets crammed in, in arbitrary order, until the ceiling cuts it off.

This module is the retrieval layer under both of those. It builds a SQLite
FTS5 index over document content (``ensure_index`` / ``index_document``), can
rebuild it from scratch (``rebuild``), and answers ``search`` with ranked
hits that carry the source file, a matched fragment, and a score.

Honest boundaries (read before trusting a hit)
----------------------------------------------
* Retrieval is **keyword-based by default**. The FTS5 index uses the ``trigram``
  tokenizer when this SQLite build has it (it matches CJK substrings and
  English substrings alike); otherwise it degrades to ``unicode61``; and if the
  SQLite has no FTS5 at all it degrades to a plain inverted/scan table. Which
  one is in play is always reported on the result (``index``) -- never guessed.
* The **vector path is off unless a genuinely neural embedder is configured**
  (``kairos.memory.embedding.from_settings`` returning ``is_neural=True``). With
  nothing configured it stays off and says so in the result's ``vector_enabled``
  / ``note`` fields. There is no neural model bundled here and this module never
  loads, downloads, or calls one.
* An empty result is never silent: ``status`` is one of ``empty-query`` /
  ``empty-index`` / ``no-match`` / ``ok`` and ``note`` states why.

No new dependency, no network, no model.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Primary index table (FTS5). Columns: file_id/project_id are stored but not
#: tokenized; ``name`` and ``content`` are searchable.
FTS_TABLE = "project_files_fts"

#: Fallback table when this SQLite has no usable FTS5: a plain table the search
#: scans. Same column shape so callers do not branch on which one exists.
PLAIN_TABLE = "project_files_index"

#: Fragment markers used by FTS5 ``snippet()`` and by the scan fallback.
_HL_OPEN = "["
_HL_CLOSE = "]"
_ELLIPSIS = "…"

#: Cap on the number of query terms OR-ed into one MATCH expression, so a long
#: pasted query cannot build a pathological FTS query.
_MAX_MATCH_TERMS = 32

_LATIN_RE = re.compile(r"[a-z0-9_+#.\-]{2,}")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")

#: Cached availability probes (per process; the SQLite build does not change).
_FTS5_OK: Optional[bool] = None
_TRIGRAM_OK: Optional[bool] = None


def _probe_fts5() -> bool:
    global _FTS5_OK
    if _FTS5_OK is None:
        try:
            conn = sqlite3.connect(":memory:")
            try:
                conn.execute("CREATE VIRTUAL TABLE _probe USING fts5(x)")
                _FTS5_OK = True
            finally:
                conn.close()
        except Exception:
            _FTS5_OK = False
    return bool(_FTS5_OK)


def fts5_available() -> bool:
    """True when this SQLite build can create an FTS5 table (probed, not assumed)."""
    return _probe_fts5()


def trigram_available() -> bool:
    """True when FTS5 accepts the ``trigram`` tokenizer (CJK-substring capable)."""
    global _TRIGRAM_OK
    if _TRIGRAM_OK is None:
        _TRIGRAM_OK = False
        if _probe_fts5():
            try:
                conn = sqlite3.connect(":memory:")
                try:
                    conn.execute(
                        "CREATE VIRTUAL TABLE _probe USING fts5(x, tokenize='trigram')"
                    )
                    _TRIGRAM_OK = True
                finally:
                    conn.close()
            except Exception:
                _TRIGRAM_OK = False
    return bool(_TRIGRAM_OK)


# ---------------------------------------------------------------------------
# query building
# ---------------------------------------------------------------------------


def _latin_words(text: str) -> List[str]:
    return _LATIN_RE.findall((text or "").lower())


def _cjk_runs(text: str) -> List[str]:
    return _CJK_RUN_RE.findall(text or "")


def _cjk_run_terms(query: str) -> Tuple[List[str], List[str]]:
    """``(trigram_terms, fallback_terms)`` for the CJK runs of ``query``.

    A run of length >= 3 contributes both its overlapping 3-char windows (for
    the trigram index) and its 2-char bigrams (for the substring fallback, so a
    2-character term like 延迟/成本/部署 is not silently dropped).
    """
    trigrams: List[str] = []
    fallback: List[str] = []
    for run in _cjk_runs(query):
        if len(run) >= 3:
            trigrams.extend(run[i:i + 3] for i in range(len(run) - 2))
        fallback.extend(run[i:i + 2] for i in range(len(run) - 1))
        if len(run) == 1:
            fallback.append(run)
    return trigrams, fallback


def build_match_query(query: str) -> Tuple[str, List[str]]:
    """Turn free text into ``(fts5_match_expression, fallback_terms)``.

    Trigram FTS5 matches substrings of length >= 3, so:

    * a Latin word of length >= 3 is one term;
    * a CJK run of length >= 3 becomes its overlapping 3-char windows;
    * shorter terms (2-char CJK bigrams, 1-char CJK runs, 2-char Latin words)
      cannot be expressed as a trigram -- they are returned as
      ``fallback_terms`` so the caller can find them with a substring
      (``LIKE``) scan instead of silently dropping them.

    Returns ``("", [...])`` for an empty / usable-nothing query.
    """
    terms: List[str] = []
    seen = set()
    for w in _latin_words(query):
        if len(w) >= 3 and w not in seen:
            seen.add(w)
            terms.append(w)
    trigrams, cjk_fallback = _cjk_run_terms(query)
    for t in trigrams:
        if t not in seen:
            seen.add(t)
            terms.append(t)
    fallback = list(cjk_fallback)
    for w in _latin_words(query):
        if len(w) == 2:
            fallback.append(w)
    terms = terms[:_MAX_MATCH_TERMS]
    expr = " OR ".join('"%s"' % t.replace('"', "") for t in terms)
    return expr, _dedupe(fallback)


def _dedupe(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for it in items:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out


# ---------------------------------------------------------------------------
# index management (works on any sqlite connection: a project DB or :memory:)
# ---------------------------------------------------------------------------


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def ensure_index(conn: sqlite3.Connection) -> str:
    """Create the file-content index if absent; return its kind.

    Kind is ``"fts5-trigram"``, ``"fts5-unicode61"`` or ``"scan"`` (the plain
    fallback). Idempotent and safe on a database that already has one.
    """
    if _table_exists(conn, FTS_TABLE):
        return "fts5-trigram" if trigram_available() else "fts5-unicode61"
    if _table_exists(conn, PLAIN_TABLE):
        return "scan"
    if _probe_fts5():
        tokenizer = "trigram" if trigram_available() else "unicode61"
        try:
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5("
                "file_id UNINDEXED, project_id UNINDEXED, name, content, "
                f"tokenize = '{tokenizer}')"
            )
            return "fts5-trigram" if tokenizer == "trigram" else "fts5-unicode61"
        except sqlite3.OperationalError:
            logger.warning(
                "doc_search: FTS5 create failed despite a successful probe; "
                "falling back to the scan table", exc_info=True,
            )
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {PLAIN_TABLE} ("
        "file_id TEXT PRIMARY KEY, project_id TEXT, name TEXT, content TEXT)"
    )
    return "scan"


def index_document(
    conn: sqlite3.Connection,
    file_id: str,
    content: str,
    *,
    project_id: str = "",
    name: str = "",
) -> None:
    """Insert or replace one document in the index (incremental write path)."""
    kind = ensure_index(conn)
    if kind.startswith("fts5"):
        conn.execute(f"DELETE FROM {FTS_TABLE} WHERE file_id = ?", (file_id,))
        conn.execute(
            f"INSERT INTO {FTS_TABLE} (file_id, project_id, name, content) "
            "VALUES (?, ?, ?, ?)",
            (file_id, project_id or "", name or "", content or ""),
        )
    else:
        conn.execute(
            f"INSERT OR REPLACE INTO {PLAIN_TABLE} "
            "(file_id, project_id, name, content) VALUES (?, ?, ?, ?)",
            (file_id, project_id or "", name or "", content or ""),
        )


def drop_document(conn: sqlite3.Connection, file_id: str) -> None:
    """Remove one document from the index (best effort; missing table is fine)."""
    for table in (FTS_TABLE, PLAIN_TABLE):
        if _table_exists(conn, table):
            try:
                conn.execute(f"DELETE FROM {table} WHERE file_id = ?", (file_id,))
            except sqlite3.OperationalError:
                pass


def drop_project(conn: sqlite3.Connection, project_id: str) -> None:
    """Remove every indexed document of one project."""
    for table in (FTS_TABLE, PLAIN_TABLE):
        if _table_exists(conn, table):
            try:
                conn.execute(f"DELETE FROM {table} WHERE project_id = ?", (project_id,))
            except sqlite3.OperationalError:
                pass


def count_indexed(conn: sqlite3.Connection, project_id: Optional[str] = None) -> int:
    """Number of indexed documents (optionally scoped to one project)."""
    for table in (FTS_TABLE, PLAIN_TABLE):
        if _table_exists(conn, table):
            if project_id:
                row = conn.execute(
                    f"SELECT count(*) FROM {table} WHERE project_id = ?", (project_id,)
                ).fetchone()
            else:
                row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
            return int(row[0] if row else 0)
    return 0


def rebuild(conn: sqlite3.Connection, rows: Sequence[Dict[str, Any]]) -> int:
    """Drop and re-create the index from ``rows`` (id/file_id, name, content, project_id).

    Returns the number of documents indexed. Used by the ``rebuild_file_index``
    entry points; the incremental :func:`index_document` path covers uploads.
    """
    schema = ensure_index(conn)
    for table in (FTS_TABLE, PLAIN_TABLE):
        if _table_exists(conn, table):
            conn.execute(f"DELETE FROM {table}")
    del schema
    for row in rows:
        index_document(
            conn,
            row.get("file_id") or row.get("id") or "",
            row.get("content") or "",
            project_id=row.get("project_id") or "",
            name=row.get("name") or "",
        )
    return len(rows)


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def _snippet_around(content: str, term: str, width: int = 200) -> str:
    """A window of ``content`` around the first occurrence of ``term``."""
    idx = content.lower().find(term.lower())
    if idx < 0:
        head = content[:width]
        return head + _ELLIPSIS if len(content) > width else head
    start = max(0, idx - width // 3)
    end = min(len(content), idx + len(term) + (width * 2) // 3)
    window = content[start:end]
    rel = idx - start
    marked = window[:rel] + _HL_OPEN + window[rel:rel + len(term)] + _HL_CLOSE \
        + window[rel + len(term):]
    if start > 0:
        marked = _ELLIPSIS + marked
    if end < len(content):
        marked = marked + _ELLIPSIS
    return marked


def _fts_search(
    conn: sqlite3.Connection,
    match_expr: str,
    limit: int,
    project_id: Optional[str],
) -> List[Dict[str, Any]]:
    if not match_expr:
        return []
    where = f"{FTS_TABLE} MATCH ?"
    params: List[Any] = [match_expr]
    if project_id:
        where += " AND project_id = ?"
        params.append(project_id)
    sql = (
        "SELECT file_id, project_id, name, "
        "snippet(" + FTS_TABLE + ", 3, ?, ?, ?, 12) AS snip, "
        "bm25(" + FTS_TABLE + ") AS raw "
        f"FROM {FTS_TABLE} WHERE {where} ORDER BY bm25({FTS_TABLE}) LIMIT ?"
    )
    params = [_HL_OPEN, _HL_CLOSE, _ELLIPSIS] + params + [int(limit)]
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        logger.debug("doc_search: FTS query failed for %r", match_expr, exc_info=True)
        return []
    hits = []
    for file_id, pid, name, snip, raw in rows:
        hits.append({
            "file_id": file_id,
            "project_id": pid or "",
            "file": name or file_id,
            "snippet": snip or "",
            "score": round(float(-(raw or 0.0)), 4),
            "mode": "fts5",
        })
    return hits


def _scan_search(
    conn: sqlite3.Connection,
    terms: Sequence[str],
    limit: int,
    project_id: Optional[str],
) -> List[Dict[str, Any]]:
    """Substring scan over the content column (short-term fallback).

    Works whether the index is the FTS5 table (whose ``content`` column is
    readable) or the plain fallback table.
    """
    terms = [t for t in _dedupe(terms) if t]
    table = None
    for candidate in (FTS_TABLE, PLAIN_TABLE):
        if _table_exists(conn, candidate):
            table = candidate
            break
    if not terms or table is None:
        return []
    where = "1=1"
    params: List[Any] = []
    if project_id:
        where += " AND project_id = ?"
        params.append(project_id)
    rows = conn.execute(
        f"SELECT file_id, project_id, name, content FROM {table} WHERE {where}",
        params,
    ).fetchall()
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for file_id, pid, name, content in rows:
        content = content or ""
        best = 0
        marked = None
        for term in terms:
            count = content.lower().count(term.lower())
            if count > best:
                best = count
                marked = _snippet_around(content, term)
        if best:
            scored.append((float(best), {
                "file_id": file_id,
                "project_id": pid or "",
                "file": name or file_id,
                "snippet": marked or "",
                "score": float(best),
                "mode": "scan",
            }))
    scored.sort(key=lambda item: (-item[0], item[1]["file"]))
    return [h for _, h in scored[:limit]]


def vector_backend_status() -> Dict[str, Any]:
    """Whether the **neural** (vector) retrieval path is active, and why.

    Reuses the pluggable embedding seam (``kairos.memory.embedding``). The
    deterministic offline fallback is NOT a neural embedding, so it leaves the
    vector path **off** and the reason string says exactly that.
    """
    from kairos.memory.semantic import embedding_backend

    backend = embedding_backend()
    enabled = bool(backend.get("is_neural"))
    if enabled:
        reason = ""
    else:
        reason = (
            f"vector retrieval not enabled: the configured embedding provider "
            f"{backend.get('name')!r} reports is_neural=False (the deterministic "
            f"offline fallback is not a neural embedding); keyword (FTS5) "
            f"retrieval only"
        )
    return {"enabled": enabled, "backend": backend, "reason": reason}


def _vector_rerank(
    query: str, hits: List[Dict[str, Any]], k: int
) -> Tuple[List[Dict[str, Any]], bool]:
    """Re-order ``hits`` by neural-embedding cosine, only if one is configured.

    Returns ``(hits, used_vector)``. When no neural provider is configured the
    input order is returned untouched and ``used_vector`` is ``False`` -- the
    caller announces that instead of pretending a vector search happened.
    """
    if len(hits) < 2:
        return hits, False
    try:
        status = vector_backend_status()
    except Exception:
        logger.debug("doc_search: vector backend resolution failed", exc_info=True)
        return hits, False
    if not status["enabled"]:
        return hits, False
    try:
        from kairos.memory.semantic import rank_by_embedding

        docs = [f"{h.get('file', '')}\n{h.get('snippet', '')}" for h in hits]
        ranked = rank_by_embedding(query, docs)
    except Exception:
        logger.warning(
            "doc_search: neural re-rank failed; keeping keyword order", exc_info=True
        )
        return hits, False
    if not ranked:
        return hits, False
    order = [i for i, _ in ranked]
    seen = set(order)
    order += [i for i in range(len(hits)) if i not in seen]
    return [hits[i] for i in order][:k] if k else [hits[i] for i in order], True


def search(
    conn: sqlite3.Connection,
    query: str,
    k: int = 5,
    *,
    project_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Search the file-content index; always returns an explained result.

    Result shape::

        {"query", "status", "hits": [ {file_id, project_id, file, snippet,
         score, mode} ], "count", "index", "vector_rerank", "vector_backend",
         "vector_enabled", "note"}

    ``status``: ``empty-query`` | ``empty-index`` | ``no-match`` | ``ok``.
    ``note`` is a human-readable reason -- never empty when ``hits`` is empty or
    the vector path is off.
    """
    k = max(1, int(k))
    result: Dict[str, Any] = {
        "query": query,
        "status": "ok",
        "hits": [],
        "count": 0,
        "index": "unknown",
        "vector_rerank": False,
        "vector_enabled": False,
        "vector_backend": None,
        "note": "",
    }
    q = (query or "").strip()
    kind = ensure_index(conn)
    result["index"] = kind
    if not q:
        result["status"] = "empty-query"
        result["note"] = "query is empty -- nothing was searched"
        return result

    total = count_indexed(conn, project_id)
    if total == 0:
        result["status"] = "empty-index"
        result["note"] = (
            "the file-content index has no documents for this scope yet "
            "(upload files, or run the rebuild entry point)"
        )
        return result

    notes: List[str] = []
    hits: List[Dict[str, Any]] = []
    if kind.startswith("fts5"):
        match_expr, fallback_terms = build_match_query(q)
        hits = _fts_search(conn, match_expr, k * 4, project_id) if match_expr else []
        # Short (1-2 char) terms cannot be a trigram; find them with a
        # substring scan and append any file the FTS query missed, so a term
        # like 延迟/成本/部署 is not silently dropped.
        if fallback_terms:
            scan_hits = _scan_search(conn, fallback_terms, k * 4, project_id)
            seen = {h["file_id"] for h in hits}
            extra = [h for h in scan_hits if h["file_id"] not in seen]
            if extra:
                hits = hits + extra
                notes.append(
                    f"added {len(extra)} document(s) found by a substring scan "
                    "for short (1-2 character) query terms"
                )
    else:
        # No FTS5: substring scan over the plain table using every token we can.
        _, fallback_terms = build_match_query(q)
        tokens = _dedupe([w for w in _latin_words(q)] + _cjk_runs(q) + fallback_terms)
        hits = _scan_search(conn, tokens or [q], k * 4, project_id)
        notes.append("this SQLite has no usable FTS5; used a substring scan")

    if not hits:
        result["status"] = "no-match"
        result["note"] = (
            f"no indexed document matched {q!r} "
            f"(searched {total} document(s) with the {kind} index)"
        )
        # Still report the vector state so a caller knows the whole story.
        try:
            status = vector_backend_status()
            result["vector_backend"] = status["backend"]
            result["vector_enabled"] = status["enabled"]
            if not status["enabled"]:
                notes.append(status["reason"])
        except Exception:
            pass
        result["note"] = " | ".join([result["note"]] + notes)
        return result

    hits, used_vector = _vector_rerank(q, hits, k)
    result["vector_rerank"] = used_vector
    try:
        status = vector_backend_status()
        result["vector_backend"] = status["backend"]
        result["vector_enabled"] = status["enabled"]
        if used_vector:
            notes.append(
                f"re-ranked by neural embedding {status['backend'].get('name')!r}"
            )
        elif not status["enabled"]:
            notes.append(status["reason"])
    except Exception:
        pass

    result["hits"] = hits[:k]
    result["count"] = len(result["hits"])
    result["note"] = " | ".join(notes)
    return result


# ---------------------------------------------------------------------------
# convenience: search a batch of in-memory documents (workspaces, tests)
# ---------------------------------------------------------------------------


def search_documents(
    query: str,
    documents: Sequence[Dict[str, Any]],
    *,
    k: int = 5,
    project_id: str = "",
) -> Dict[str, Any]:
    """Index ``documents`` in a throwaway DB and search them.

    ``documents`` items are ``{"file"|"name": str, "content"|"body": str,
    "id"?: str}``. Used by the workspace prompt-context path, which reads
    documents from disk rather than from ``project_files``.
    """
    conn = sqlite3.connect(":memory:")
    try:
        for i, doc in enumerate(documents):
            index_document(
                conn,
                doc.get("id") or doc.get("file_id") or doc.get("file")
                or doc.get("name") or f"doc-{i}",
                doc.get("content", doc.get("body", "")) or "",
                project_id=project_id,
                name=doc.get("file") or doc.get("name") or "",
            )
        return search(conn, query, k=k, project_id=project_id or None)
    finally:
        conn.close()


def render_relevant_excerpts(
    query: str,
    documents: Sequence[Dict[str, Any]],
    *,
    cap: int,
    k: int = 6,
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Render task-relevant excerpts of a large document set, bounded by ``cap``.

    Returns ``(text, meta)``. ``text`` is ``None`` when there is nothing
    relevant to show (or no query), so the caller can keep its existing
    whole-workspace rendering -- this function *augments*, it never silently
    blanks a context.

    Every excerpt names its source file; when the ``cap`` cuts the list short,
    a ``[TRUNCATED]`` note names the inputs that were left out.
    """
    q = (query or "").strip()
    meta: Dict[str, Any] = {"hits": 0, "index": "unknown", "vector_rerank": False,
                            "frame": "excerpts"}
    if not q or not documents:
        return None, meta
    result = search_documents(q, documents, k=k)
    meta["index"] = result["index"]
    meta["vector_rerank"] = result["vector_rerank"]
    meta["status"] = result["status"]
    meta["note"] = result["note"]
    hits = result.get("hits") or []
    meta["hits"] = len(hits)
    if not hits:
        return None, meta

    header = (
        "RELEVANT EXCERPTS (the workspace is too large to inline in full: "
        f"showing the {len(hits)} input(s) that match the task, ranked by "
        f"relevance; hit by the {result['index']} index)"
    )
    blocks: List[str] = []
    used = 0
    omitted: List[str] = []
    for hit in hits:
        name = hit.get("file") or hit.get("file_id") or "?"
        snippet = (hit.get("snippet") or "").strip()
        block = f"### {name}\n{snippet}"
        if used + len(block) > cap:
            if not blocks:
                # Nothing rendered yet: take as much of this first excerpt as fits.
                room = max(0, cap - len(header) - len(f"### {name}\n") - 1)
                if room > 0:
                    blocks.append(f"### {name}\n{snippet[:room]}{_ELLIPSIS}")
                    used += cap
                omitted.append(name)
            else:
                omitted.append(name)
            continue
        blocks.append(block)
        used += len(block)
    text = header + "\n\n" + "\n\n".join(blocks)
    if omitted:
        text += (
            f"\n\n[TRUNCATED] relevant-excerpt list hit the {cap}-char cap; "
            "these inputs are not shown here: " + ", ".join(_dedupe(omitted))
        )
    return text, meta


__all__ = [
    "FTS_TABLE",
    "PLAIN_TABLE",
    "fts5_available",
    "trigram_available",
    "build_match_query",
    "ensure_index",
    "index_document",
    "drop_document",
    "drop_project",
    "count_indexed",
    "rebuild",
    "search",
    "search_documents",
    "render_relevant_excerpts",
    "vector_backend_status",
]
