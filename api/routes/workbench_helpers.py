"""Helpers extracted from api.routes.workbench (route registration unchanged)."""
from __future__ import annotations
import difflib
import hashlib
import json
import logging
import mimetypes
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from api import deps
from kairos.core.message_bus import Message


def _orch():
    """Resolve the live orchestrator via the deps module.

    Mirrors the shim in projects.py so tests that monkeypatch
    ``api.deps.orchestrator`` are seen by every handler.
    """
    return deps.orchestrator


def _safe_join(root: Path, rel: str) -> Path:
    """Join *rel* to *root* and ensure the result stays inside
    *root* (no path traversal)."""
    if not rel:
        return root
    # Normalize separators and reject absolute paths.
    rel = rel.replace("\\", "/").lstrip("/")
    if rel.startswith("..") or "/../" in ("/" + rel):
        raise HTTPException(status_code=400, detail="path traversal not allowed")
    target = (root / rel).resolve()
    # Defense in depth: check the resolved path is still under root.
    try:
        target.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="path traversal not allowed")
    return target


def _is_binary(path: Path) -> bool:
    """Return True if *path* looks like a binary file (either by
    extension or by sampling the first 8KB for null bytes)."""
    mt, _ = mimetypes.guess_type(str(path))
    if mt and not mt.startswith("text/") and mt != "application/json":
        # Known image / binary MIME type.
        if not mt.startswith("application/"):
            return True
    try:
        with open(path, "rb") as f:
            chunk = f.read(8192)
        return b"\x00" in chunk
    except OSError:
        return True


def _checkpoint_dir(root: Path) -> Path:
    d = root / ".kairos" / "workbench"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _hash_file(path: Path) -> str:
    """Quick file hash for change detection. Reads up to 1MB."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            h.update(f.read(1 * 1024 * 1024))
    except OSError:
        return ""
    return h.hexdigest()[:16]


def _meta_of(ev: dict) -> dict:
    """Metadata of a message row (JSON string in the DB, dict in memory)."""
    md = ev.get("metadata")
    if isinstance(md, str):
        try:
            md = json.loads(md)
        except Exception:
            return {}
    return md if isinstance(md, dict) else {}


def _first_line(text: Any, limit: int = 70) -> str:
    """One tidy line of prose — used for round titles."""
    if text is None:
        return ""
    if not isinstance(text, str):
        try:
            text = json.dumps(text, ensure_ascii=False)
        except Exception:
            text = str(text)
    if not text or text in ("null", "None", "{}", "[]"):
        return ""
    raw = text.strip()
    if not raw:
        return ""
    line = raw.splitlines()[0].strip()
    line = re.sub(r'^[#*\->\s]+', '', line)
    line = re.sub(r'\s+', ' ', line)
    return line[:limit]


def _todos_of(plan: Any) -> List[dict]:
    """Normalise a plan snapshot into a list of todo dicts.

    Accepts a ``Plan`` object, the ``{"todos": [...]}`` dict stored in
    ``session.history`` / ``plan.updated``, or a bare list. Anything
    else yields ``[]``.
    """
    if plan is None:
        return []
    if hasattr(plan, "to_dict"):
        try:
            plan = plan.to_dict()
        except Exception:
            return []
    if isinstance(plan, dict):
        plan = plan.get("todos") or plan.get("items") or plan.get("steps") or []
    if not isinstance(plan, (list, tuple)):
        return []
    return [t for t in plan if isinstance(t, dict)]


def _todo_title(todo: dict, idx: int) -> str:
    for key in ("content", "title", "task", "description", "activeForm"):
        val = todo.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return f"Step {idx + 1}"


def _todo_detail(todo: dict) -> Optional[str]:
    for key in ("activeForm", "description", "detail"):
        val = todo.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _verdict_from_content(content: Any) -> Optional[dict]:
    """Parse a Reviewer verdict out of a ``task.result`` payload.

    Two shapes are in the wild and both have to work:

    * the R38.7 simple one — ``{"has_bugs": bool, "bugs": [...], "summary": str}``
      — which the current Reviewer emits. This function only looked for
      ``approve``, so as soon as the Reviewer was simplified every round became
      "round finished without a verdict": a run whose gate said *passed* showed
      three failed tasks in the tracker;
    * the legacy rubric one — ``{"approve": bool, "score": int, ...}``, kept
      because old sessions and replays still carry it.

    Stored content is capped at 2000 chars, so long payloads are cut mid-string
    and ``json.loads`` fails; the regexes recover what the tracker needs from the
    leading fields.
    """
    text = content if isinstance(content, str) else json.dumps(content or {}, ensure_ascii=False)
    data: Any = None
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except Exception:
            data = None

    # Route the simple shape through the loop's own translation, so the tracker
    # cannot disagree with the gate about whether a round passed.
    from kairos.loop.reviewers import _normalize_bug_verdict

    simple = _normalize_bug_verdict(data)
    if simple is None:
        m_simple = re.search(r'"has_bugs"\s*:\s*(true|false)', text, re.I)
        if m_simple:
            simple = _normalize_bug_verdict({
                "has_bugs": m_simple.group(1).lower() == "true",
                "bugs": [{}] * len(re.findall(r'"description"\s*:', text, re.I)),
            })
    if simple is not None:
        return simple

    if isinstance(data, dict) and "approve" in data:
        return data
    m = re.search(r'"approve"\s*:\s*(true|false)', text, re.I)
    if not m:
        return None
    out: Dict[str, Any] = {"approve": m.group(1).lower() == "true"}
    m_score = re.search(r'"score"\s*:\s*(-?\d+)', text)
    if m_score:
        out["score"] = int(m_score.group(1))
    m_sum = re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.){0,200})', text)
    if m_sum:
        out["summary"] = m_sum.group(1)
    return out


def _with_note(detail: Optional[str], note: str) -> str:
    """Keep the item's own context and append why it stopped."""
    return f"{detail} · {note}" if detail else note
